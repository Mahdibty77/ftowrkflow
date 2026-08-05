"""Screens for the people directory (administrators only)."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import spec
from .constants import PersonStatus
from .forms import PersonForm, PersonSearchForm
from .models import Person, PersonAccount, PersonRole
from .seats import (
    SeatError, assign_seat, available_seats, ensure_person_login, primary_login,
    reconcile_person_accounts, release_role, release_seat, roles_of, seats_of,
    sync_person_users_active,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 25


def _is_admin(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_admin)


admin_required = user_passes_test(_is_admin, login_url="accounts:login")


def _with_seats(queryset):
    """Prefetch seats and roles for the people list chips."""
    return queryset.prefetch_related(
        Prefetch(
            "accounts",
            queryset=PersonAccount.objects.select_related("user", "user__profile")
                                          .order_by("assigned_at", "pk"),
        ),
        Prefetch(
            "roles",
            queryset=PersonRole.objects.select_related(
                "source_user", "source_user__profile",
            ).order_by("created_at", "pk"),
        ),
    )


def _search(queryset, term: str):
    """Narrow by detail code, name, last name, the Latin spellings or username.

    Deliberately NOT by national ID. That is the most sensitive thing stored
    here, and a search box that matches on it turns this screen into a way to
    confirm, one guess at a time, whether a given ID belongs to somebody in the
    company.
    """
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(
        Q(detail_code__icontains=term)
        | Q(first_name__icontains=term) | Q(last_name__icontains=term)
        | Q(first_name_en__icontains=term) | Q(last_name_en__icontains=term)
        | Q(username__icontains=term)
    )


@login_required
@admin_required
def person_list(request):
    """The people list, and the same list again as a fragment.

    Typing in the filter box re-fetches this view and swaps the results in,
    which is why the fragment exists: re-sending the sidebar, the top bar and
    the stylesheet on every keystroke would be most of the bytes for none of
    the change. Both paths run exactly the same query — there is one list, and
    it cannot disagree with itself.
    """
    form = PersonSearchForm(request.GET or None)
    people = _with_seats(Person.objects.all())

    q = status = seats = ""
    if form.is_valid():
        q = form.cleaned_data.get("q", "")
        status = form.cleaned_data.get("status", "")
        seats = form.cleaned_data.get("seats", "")

    people = _search(people, q)
    if status:
        people = people.filter(status=status)
    if seats == "yes":
        people = people.filter(accounts__isnull=False).distinct()
    elif seats == "no":
        people = people.filter(accounts__isnull=True)

    paginator = Paginator(people, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    # What the pager and the "back to the list I was on" hidden field carry.
    # "fragment" is dropped along with "page": leaving it on would make every
    # pager link return the bare results fragment as if it were a whole page.
    base_query = request.GET.copy()
    base_query.pop("page", None)
    base_query.pop("fragment", None)

    context = {
        "form": form,
        "page_obj": page_obj,
        "total": paginator.count,
        "base_query": base_query.urlencode(),
        "filtered": bool(q or status or seats),
        "active_count": Person.objects.filter(status=PersonStatus.ACTIVE).count(),
        "seatless_count": Person.objects.filter(
            status=PersonStatus.ACTIVE, accounts__isnull=True).count(),
        "free_seat_count": available_seats().count(),
        "reveal_credential": request.session.pop("_reveal_credential", None),
    }
    if request.GET.get("fragment") == "1":
        return render(request, "people/_person_rows.html", context)
    return render(request, "people/person_list.html", context)


@login_required
@admin_required
def person_create(request):
    if request.method == "POST":
        form = PersonForm(request.POST)
        if form.is_valid():
            person = form.save(actor=request.user, post=request.POST)
            messages.success(
                request,
                f"{person.display_name} ثبت شد. کد تفصیلی: {person.detail_code} — "
                f"نام کاربری: {person.username}")
            return redirect("people:person_detail", pk=person.pk)
    else:
        form = PersonForm()
    return render(request, "people/person_form.html", {
        "form": form, "mode": "create", "person": None,
        "job_titles": spec.JOB_TITLES,
    })


@login_required
@admin_required
def person_detail(request, pk):
    """Details hub: Record summary + Profile / Work shift cards."""
    person = get_object_or_404(_with_seats(Person.objects.all()), pk=pk)
    from .work_shift import shift_window
    start, end = shift_window(person)
    return render(request, "people/person_hub.html", {
        "person": person,
        "shift_label": f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}",
    })


@login_required
@admin_required
def person_edit(request, pk):
    person = get_object_or_404(_with_seats(Person.objects.all()), pk=pk)
    if request.method == "POST":
        form = PersonForm(request.POST, instance=person)
        if form.is_valid():
            form.save(actor=request.user, post=request.POST)
            messages.success(request, f"اطلاعات {person.display_name} ذخیره شد.")
            return redirect("people:person_profile", pk=person.pk)
    else:
        form = PersonForm(instance=person)
    return render(request, "people/person_form.html", {
        "form": form, "mode": "edit", "person": person,
        "job_titles": spec.JOB_TITLES,
    })


@login_required
@admin_required
def person_shift(request, pk):
    """Edit daily work-shift hours + monthly/yearly hour reports."""
    from datetime import datetime as dt
    from datetime import time as dtime

    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh
    from .work_shift import shift_window

    person = get_object_or_404(Person, pk=pk)
    start, end = shift_window(person)
    sh.prune_empty_past_snapshots(person)
    sh.freeze_past_months(person)
    current = sh.ensure_month_snapshot(person)
    sh.refresh_worked(person, current)

    hour_choices = [f"{i:02d}" for i in range(24)]
    minute_choices = [f"{i:02d}" for i in range(60)]

    now = sh.now_local()
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    try:
        view_year = int(request.GET.get("year") or jy)
    except (TypeError, ValueError):
        view_year = jy
    if view_year > jy:
        view_year = jy

    month_cards = sh.year_month_cards(person, view_year)
    year = sh.summarize_year_cards(month_cards, view_year)
    year_cards = sh.year_cards_for_person(
        person, jy, current_summary=(year if view_year == jy else None),
    )
    for c in year_cards:
        c["is_selected"] = c["jalali_year"] == view_year

    tracking_start = sh.get_tracking_start()
    ty, tm, td = gregorian_to_jalali(
        tracking_start.year, tracking_start.month, tracking_start.day,
    )

    def _shift_ctx(s: dtime, e: dtime, float_secs: int, grace_secs: int, *, pending=False):
        mm, ss = divmod(max(0, int(float_secs)), 60)
        gm, gs = divmod(max(0, int(grace_secs)), 60)
        return {
            "person": person,
            "work_start": s.strftime("%H:%M"),
            "work_end": e.strftime("%H:%M"),
            "start_h": f"{s.hour:02d}",
            "start_m": f"{s.minute:02d}",
            "end_h": f"{e.hour:02d}",
            "end_m": f"{e.minute:02d}",
            "float_mmss": sh.format_float_mmss(float_secs),
            "float_m": f"{mm:02d}",
            "float_s": f"{ss:02d}",
            "grace_mmss": sh.format_float_mmss(grace_secs),
            "grace_m": f"{gm:02d}",
            "grace_s": f"{gs:02d}",
            "float_minute_choices": [f"{i:02d}" for i in range(60)],
            "hour_choices": hour_choices,
            "minute_choices": minute_choices,
            "pending_confirm": pending,
            "current": current,
            "month_cards": month_cards,
            "year": year,
            "year_cards": year_cards,
            "report_year": view_year,
            "tracking_label": f"{td} {sh.month_name_en(tm)} {ty}",
            "hours_per_day": round(sh.shift_minutes(s, e) / 60, 2),
        }

    def _parse_posted_time(prefix: str):
        raw = (request.POST.get(prefix) or "").strip()
        if raw:
            return dt.strptime(raw, "%H:%M").time()
        hh = (request.POST.get(f"{prefix}_h") or "").strip()
        mm = (request.POST.get(f"{prefix}_m") or "").strip()
        return dt.strptime(f"{hh}:{mm}", "%H:%M").time()

    def _parse_mmss(prefix: str, default_m: str = "0", default_s: str = "0") -> int:
        raw = (request.POST.get(prefix) or "").strip()
        if raw and ":" in raw:
            a, b = raw.split(":", 1)
            return max(0, int(a) * 60 + int(b))
        # float_time / reconnect_time hidden, or float_m/float_s / grace_m/grace_s
        if prefix == "float_time":
            mm = int((request.POST.get("float_m") or default_m).strip())
            ss = int((request.POST.get("float_s") or default_s).strip())
        else:
            mm = int((request.POST.get("grace_m") or default_m).strip())
            ss = int((request.POST.get("grace_s") or default_s).strip())
        return max(0, mm * 60 + ss)

    float_now = sh.float_seconds_for(person)
    grace_now = sh.reconnect_grace_seconds_for(person)

    if request.method == "POST":
        step = (request.POST.get("confirm_step") or "").strip()
        try:
            new_start = _parse_posted_time("work_start")
            new_end = _parse_posted_time("work_end")
            new_float = _parse_mmss("float_time", "15", "0")
            new_grace = _parse_mmss("reconnect_time", "10", "0")
        except ValueError:
            messages.error(request, "Please enter valid times (HH:MM / MM:SS).")
            return redirect("people:person_shift", pk=person.pk)
        if new_start == new_end:
            messages.error(request, "Start and end times must be different.")
            return redirect("people:person_shift", pk=person.pk)

        if step != "2":
            return render(
                request,
                "people/person_shift.html",
                _shift_ctx(new_start, new_end, new_float, new_grace, pending=True),
            )

        sh.apply_shift_change(
            person,
            new_start,
            new_end,
            float_seconds=new_float,
            reconnect_grace_seconds=new_grace,
        )
        messages.success(
            request,
            f"Work shift for {person.display_name} saved "
            f"({new_start.strftime('%H:%M')}–{new_end.strftime('%H:%M')}, "
            f"floating time {sh.format_float_mmss(new_float)}, "
            f"reconnect time {sh.format_float_mmss(new_grace)}). "
            f"Only the current month's planned hours were updated.",
        )
        return redirect("people:person_shift", pk=person.pk)

    return render(
        request,
        "people/person_shift.html",
        _shift_ctx(start, end, float_now, grace_now),
    )


@login_required
@admin_required
def person_shift_month(request, pk, year, month):
    """Day cards for one Jalali month."""
    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh

    person = get_object_or_404(Person, pk=pk)
    month = int(month)
    year = int(year)
    if month < 1 or month > 12:
        messages.error(request, "Invalid month.")
        return redirect("people:person_shift", pk=person.pk)

    sh.freeze_past_months(person)
    days = sh.month_day_details(person, year, month)
    now = sh.now_local()
    cy, cm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    cards = sh.year_month_cards(person, year)
    month_card = next((c for c in cards if c["month"] == month), None)

    return render(request, "people/person_shift_month.html", {
        "person": person,
        "jalali_year": year,
        "jalali_month": month,
        "month_name": sh.month_name_en(month),
        "days": days,
        "month_card": month_card,
        "is_current_month": (year, month) == (cy, cm),
    })

@login_required
@require_POST
def shift_presence_ping(request):
    """Heartbeat: credit ~1 minute of presence for the signed-in person."""
    from django.http import JsonResponse

    from .shift_hours import record_presence_ping
    from .work_shift import person_for_user, shift_exempt, shift_status

    if request.session.get("impersonator_id"):
        return JsonResponse({"ok": True, "skipped": "impersonating"})
    if shift_exempt(request.user):
        return JsonResponse({"ok": True, "exempt": True})
    person = person_for_user(request.user)
    if person is None:
        return JsonResponse({"ok": False, "reason": "no_person"}, status=400)
    minutes = record_presence_ping(person)
    st = shift_status(request.user)
    return JsonResponse({
        "ok": True,
        "day_minutes": minutes,
        "allowed": st["allowed"],
        "minutes_left": st.get("minutes_left"),
        "seconds_left": _seconds_left(st),
    })


def _seconds_left(st: dict) -> int | None:
    if not st.get("allowed") or st.get("exempt"):
        return None
    if st.get("seconds_left") is not None:
        return max(0, int(st["seconds_left"]))
    if st.get("minutes_left") is not None:
        return max(0, int(st["minutes_left"]) * 60)
    end = st.get("effective_end") or st.get("end")
    if end is None:
        return None
    from datetime import datetime, timedelta

    from .work_shift import now_local
    when = now_local()
    end_dt = datetime.combine(when.date(), end, tzinfo=when.tzinfo)
    start = st.get("start")
    if start and start > end and when.time() >= start:
        end_dt += timedelta(days=1)
    return max(0, int((end_dt - when).total_seconds()))


@login_required
@admin_required
@require_POST
def person_toggle_status(request, pk):
    """Mark somebody departed, or bring them back. Never deletes anything.

    Leaving sets the leaving date to today if one has not been recorded, and
    keeps whatever date was entered if one has — an administrator who typed the
    real last day should not have it overwritten by the day they got round to
    pressing the button. Coming back clears it, because a date of leaving on
    somebody who is here is a contradiction rather than history.

    Linked seat Users are deactivated / reactivated with the person (primary
    login only when active). Case history and seat rows are never deleted.
    """
    person = get_object_or_404(Person, pk=pk)
    if person.is_active:
        person.status = PersonStatus.DEPARTED
        messages.success(request, f"{person.display_name} «خارج‌شده» علامت خورد.")
    else:
        person.status = PersonStatus.ACTIVE
        messages.success(request, f"{person.display_name} دوباره «شاغل» شد.")
    person.save(update_fields=["status", "updated_at"])
    sync_person_users_active(person)
    return redirect(_back_to_list(request))


#: The only things "go back to where I was" is allowed to carry.
BACK_KEYS = ("q", "status", "seats", "page")


def _back_to_list(request):
    """The list, with the filters the administrator was looking at still on.

    Rebuilt from four known keys rather than echoing whatever arrived. Sending
    a user back to a URL taken from their own request is how an open redirect
    is written by accident, and re-encoding only these four means the result is
    a URL this application composed itself.
    """
    from urllib.parse import parse_qs, urlencode

    incoming = parse_qs((request.POST.get("back", "") or "").lstrip("?"),
                        keep_blank_values=False)
    safe = {k: v[0] for k, v in incoming.items() if k in BACK_KEYS and v}
    url = reverse("people:person_list")
    return f"{url}?{urlencode(safe)}" if safe else url


@login_required
def activate_role(request, role_id):
    """Switch the signed-in person's active organisational role."""
    if request.method not in ("GET", "POST"):
        return redirect("core:home")
    profile = getattr(request.user, "profile", None)
    if profile is None or profile.is_admin or profile.is_general_manager:
        return redirect("core:home")
    link = getattr(request.user, "person_link", None)
    if link is None:
        return redirect("core:home")
    role = get_object_or_404(PersonRole, pk=role_id, person=link.person)
    from .role_nav import safe_activate_role
    safe_activate_role(request.user, role)
    request.session["active_role_id"] = role.pk
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("cases:inbox")


@login_required
@admin_required
def person_seats(request, pk):
    """Roles this person holds, and seats that can be given to them."""
    from people.constants import PersonStatus
    from people.role_nav import peek_inbox_count

    person = get_object_or_404(Person, pk=pk)
    try:
        reconcile_person_accounts(person)
        ensure_person_login(person, actor=request.user)
    except SeatError:
        pass
    held = list(seats_of(person))
    login_user = primary_login(person)
    roles = list(roles_of(person))
    role_rows = []
    from people.models import SeatTenure
    for role in roles:
        tasks = 0
        try:
            from people.seats import open_task_count
            tasks = open_task_count(role)
        except Exception:
            login_for_count = login_user or role.source_user
            tasks = peek_inbox_count(login_for_count, role) if login_for_count else 0
        is_sub = False
        if role.source_user_id:
            is_sub = SeatTenure.objects.filter(
                source_user=role.source_user,
                kind=SeatTenure.KIND_SUBSTITUTE,
                ended_at__isnull=True,
            ).exists()
        role_rows.append({
            "role": role,
            "tasks": tasks,
            "is_sub": is_sub,
            "seat_pk": role.source_user_id,
        })

    people_choices = list(
        Person.objects.filter(status=PersonStatus.ACTIVE)
        .exclude(pk=person.pk)
        .order_by("first_name_en", "last_name_en", "detail_code")
    )

    from accounts.constants import Role, SupplyKind, Unit
    from people.seats import is_blank_org_seat

    seat_users = list(
        User.objects.filter(profile__seat_code__isnull=False)
        .exclude(profile__seat_code="")
        .exclude(profile__is_admin=True)
        .select_related("profile", "person_link__person")
        .order_by(
            "profile__unit", "profile__role", "profile__supply_kind",
            "profile__seat_code",
        )
    )
    avail_groups = {}
    for u in seat_users:
        p = u.profile
        if is_blank_org_seat(p):
            continue
        if p.is_general_manager:
            title = "General Manager"
        else:
            parts = [x for x in [Unit.LABELS.get(p.unit, ""), Role.LABELS.get(p.role, "")] if x]
            if p.supply_kind:
                sk = SupplyKind.LABELS.get(p.supply_kind, p.supply_kind)
                if sk:
                    parts.append(f"({sk})")
            title = " ".join(parts) if parts else p.title_line or "Seat"
        holder = getattr(getattr(u, "person_link", None), "person", None)
        if holder is not None and holder.pk == person.pk:
            continue  # already ours
        bucket = avail_groups.setdefault(title, {"title": title, "seats": []})
        bucket["seats"].append({
            "user": u,
            "index": p.seat_code or "—",
            "holder": holder,
            "active": u.is_active,
            "vacant": holder is None,
        })
    free_groups = list(avail_groups.values())
    free = list(available_seats())
    reveal_credential = request.session.pop("_reveal_credential", None)
    return render(request, "people/person_seats.html", {
        "person": person,
        "held": held,
        "login_user": login_user,
        "roles": roles,
        "role_rows": role_rows,
        "people_choices": people_choices,
        "free": free,
        "free_groups": free_groups,
        "free_count": sum(len(g["seats"]) for g in free_groups),
        "reveal_credential": reveal_credential,
    })


@login_required
@admin_required
@require_POST
def person_reset_password(request, pk):
    """Reset the person's single login password (shown once on the Seats page)."""
    from accounts.forms import generate_temp_password
    from accounts.views import _kill_sessions_for

    person = get_object_or_404(Person, pk=pk)
    try:
        login_user = ensure_person_login(person, actor=request.user)
    except SeatError as exc:
        messages.error(request, str(exc))
        return redirect("people:person_seats", pk=person.pk)
    if login_user is None:
        messages.error(request, "This person has no login username yet.")
        return redirect("people:person_seats", pk=person.pk)

    generated = generate_temp_password()
    login_user.set_password(generated)
    login_user.save(update_fields=["password"])
    profile = login_user.profile
    profile.must_change_password = True
    profile.save(update_fields=["must_change_password"])
    _kill_sessions_for(login_user)

    request.session["_reveal_credential"] = {
        "username": login_user.username,
        "password": generated,
        "label": f"Password reset for {person.display_name}",
    }
    messages.success(request, f"Password reset for “{person.display_name}”.")
    return redirect("people:person_list")


@login_required
@admin_required
@require_POST
def seat_assign(request, pk):
    """Give this person one or more free seats / roles."""
    person = get_object_or_404(Person, pk=pk)
    wanted = request.POST.getlist("seats")
    if not wanted:
        messages.warning(request, "No seat was selected.")
        return redirect("people:person_seats", pk=person.pk)

    chosen = list(available_seats().filter(pk__in=wanted))
    done, failed = [], []
    for user in chosen:
        label = (getattr(user.profile, "seat_code", None) or user.username)
        try:
            assign_seat(person, user, actor=request.user)
        except SeatError as exc:
            failed.append(str(exc))
        else:
            done.append(label)

    missing = len(wanted) - len(chosen)
    if done:
        messages.success(
            request,
            f"{len(done)} seat(s) assigned to {person.display_name}: " + ", ".join(done),
        )
    for reason in failed:
        messages.error(request, reason)
    if missing > 0:
        messages.warning(
            request,
            f"{missing} seat(s) were taken by someone else and were skipped.",
        )
    return redirect("people:person_seats", pk=person.pk)


@login_required
@admin_required
@require_POST
def seat_release(request, pk, seat_id):
    """Release the person's login seat entirely (all roles)."""
    person = get_object_or_404(Person, pk=pk)
    link = get_object_or_404(
        PersonAccount.objects.select_related("user"), pk=seat_id, person=person)
    try:
        freed = release_seat(link)
    except SeatError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Login released from {person.display_name}; account renamed to «{freed}».",
        )
    return redirect("people:person_seats", pk=person.pk)


@login_required
@admin_required
@require_POST
def role_release(request, pk, role_id):
    """Close one organisational role (only when inbox/tasks are empty)."""
    from .seats import close_seat, open_task_count

    person = get_object_or_404(Person, pk=pk)
    role = get_object_or_404(PersonRole, pk=role_id, person=person)
    title = role.title_line
    try:
        tasks = open_task_count(role)
        if tasks > 0 and role.source_user_id:
            messages.warning(
                request,
                f"Cannot close «{title}» while {tasks} open task(s) remain — Delegate first.",
            )
            return redirect("accounts:seat_delegate", pk=role.source_user_id)
        freed = close_seat(role, actor=request.user)
    except SeatError as exc:
        messages.error(request, str(exc))
    else:
        if freed:
            messages.success(
                request,
                f"Role «{title}» closed; seat freed as «{freed}».",
            )
        else:
            messages.success(request, f"Role «{title}» closed for {person.display_name}.")
    return redirect("people:person_seats", pk=person.pk)


@login_required
@admin_required
@require_POST
def role_translate(request, pk, role_id):
    """Temporarily hand a role to another person (substitute)."""
    from .seats import translate_role

    person = get_object_or_404(Person, pk=pk)
    role = get_object_or_404(PersonRole, pk=role_id, person=person)
    to_pk = (request.POST.get("to_person") or "").strip()
    to_person = get_object_or_404(Person, pk=to_pk) if to_pk.isdigit() else None
    title = role.title_line
    try:
        if to_person is None:
            raise SeatError("Choose a person to receive this role.")
        translate_role(role, to_person, actor=request.user)
    except SeatError as exc:
        messages.error(request, str(exc))
        return redirect("people:person_seats", pk=person.pk)
    messages.success(
        request,
        f"Role «{title}» translated to {to_person.display_name} (substitute).",
    )
    return redirect("people:person_seats", pk=to_person.pk)


@login_required
@admin_required
@require_POST
def role_return(request, pk, role_id):
    """Return a substitute-held role to the origin owner."""
    from .seats import return_role

    person = get_object_or_404(Person, pk=pk)
    role = get_object_or_404(PersonRole, pk=role_id, person=person)
    title = role.title_line
    try:
        returned = return_role(role, actor=request.user)
        owner = returned.person
    except SeatError as exc:
        messages.error(request, str(exc))
        return redirect("people:person_seats", pk=person.pk)
    messages.success(
        request,
        f"Role «{title}» returned to {owner.display_name}.",
    )
    return redirect("people:person_seats", pk=owner.pk)


@login_required
@admin_required
@require_POST
def seat_claim(request, pk):
    """Claim a seat (vacant or held) onto this person via translate/assign."""
    from .seats import assign_seat, translate_role

    person = get_object_or_404(Person, pk=pk)
    seat_id = (request.POST.get("seat") or "").strip()
    if not seat_id.isdigit():
        messages.error(request, "No seat selected.")
        return redirect("people:person_seats", pk=person.pk)
    seat_user = get_object_or_404(User.objects.select_related("profile", "person_link"), pk=int(seat_id))
    link = getattr(seat_user, "person_link", None)
    try:
        if link is None:
            assign_seat(person, seat_user, actor=request.user)
            messages.success(
                request,
                f"Seat «{seat_user.profile.seat_code or seat_user.username}» assigned.",
            )
        else:
            if link.person_id == person.pk:
                messages.warning(request, "That seat already belongs to this person.")
                return redirect("people:person_seats", pk=person.pk)
            role = PersonRole.objects.filter(
                person=link.person, source_user=seat_user,
            ).first()
            if role is None:
                raise SeatError("That seat has no role to translate.")
            title = role.title_line
            translate_role(role, person, actor=request.user)
            messages.success(
                request,
                f"Role «{title}» translated onto {person.display_name}.",
            )
    except SeatError as exc:
        messages.error(request, str(exc))
    return redirect("people:person_seats", pk=person.pk)
