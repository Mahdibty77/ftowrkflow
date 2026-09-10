"""Screens for the people directory (administrators only).

Four groups of views live here. They are a map of WHAT is here, not of where it
sits: the file has been appended to over time, so several views are nowhere near
the group they belong to (``person_toggle_status`` sits well below the shift and
heartbeat views, and ``person_reset_password`` sits inside the seats span). Go
by name, not by line number.

* THE DIRECTORY — ``person_list`` / ``person_create`` / ``person_detail`` /
  ``person_edit`` / ``person_toggle_status``. Plain CRUD over ``Person``,
  rendered from the card spec in ``people.spec`` via ``people.forms``.
* THE SHIFT PAGE — ``person_shift`` and ``person_shift_month`` draw one
  employee's Jalali year and month of worked hours. They compute nothing
  themselves; every figure comes from ``people.shift_hours``. NOT
  administrator-only, unlike the rest of this file — gated by
  ``admin_or_self_required`` (defined just below ``admin_required``), which
  lets an administrator open anyone's copy exactly as before AND lets a
  person open their own. The "Daily hours" edit card is still
  administrator-only within that shared page — see the template's own guard
  and ``person_shift``'s own docstring.
* THE HEARTBEAT — ``shift_presence_ping`` is the endpoint the signed-in
  person's open tab POSTs to every few seconds, and ``shift_ended`` is the
  goodbye screen the middleware sends them to when the shift closes. Neither is
  administrator-only, so neither carries the admin gate the rest of this file
  uses, but they are not gated alike: the ping is ``@login_required`` (it credits
  presence for whoever is signed in), while ``shift_ended`` is deliberately
  unauthenticated. ``WorkShiftMiddleware`` calls ``logout()`` and only then
  redirects there, so the request that arrives is always anonymous — requiring a
  login would bounce the user past the very screen the redirect exists to show.
  Leaving it open costs nothing: it reads no session and no database, only a
  display name off the query string, which it clips before rendering.
* SEATS — ``person_seats``, the POST-only actions around it (``seat_assign``,
  ``seat_release``, ``seat_claim``, ``role_release``, ``role_translate``,
  ``role_return``) and ``person_reset_password`` are the administrator's console
  for the seat model. They validate and redirect; every state change is made by
  ``people.seats``, which is also where that model is explained. The seat's own
  lifecycle screens — ``seat_close`` and ``seat_delegate`` — live in
  ``accounts.views`` instead, because they act on the seat account rather than
  on the person holding it.

``activate_role`` is the odd one out and belongs to no group: it is what the
sidebar role switcher posts to when a person with several ``PersonRole`` rows
changes which one they are working as. See ``people.role_nav``.
"""
import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import spec
from .constants import PersonStatus
from .forms import PersonForm, PersonSearchForm
from .models import Person, PersonAccount, PersonRole
from .seats import (
    SeatError, assign_seat, available_seats, ensure_person_login, primary_login,
    reconcile_person_accounts, release_seat, roles_of, seats_of,
    sync_person_users_active,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 25


def _is_admin(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_admin)


admin_required = user_passes_test(_is_admin, login_url="accounts:login")


def admin_or_impersonating_admin_required(view):
    """``admin_required``, but judged on whoever is really at the keyboard.

    During an impersonation ``request.user`` is deliberately the employee being
    stood in for, so a plain ``admin_required`` refuses the administrator who
    started it. That is correct for anything that WRITES — an edit made in that
    state would be recorded against the employee, not against the administrator
    who actually made it — but it broke the one page that has to stay reachable:
    the people list is where the "Log in as" control lives, so being locked out
    of it mid-impersonation meant an administrator could never switch from one
    person to another. That is the 403 the owner hit after pressing Back.

    Nobody new is admitted. The test is the same ``_is_admin``, applied to the
    administrator named in the session and re-read from the database on every
    request, so an account that has since been deactivated or demoted cannot go
    on using a session it opened while it still could. An ordinary request —
    no impersonation in progress — still stands or falls on ``request.user``
    exactly as before.

    Deliberately applied to the read-only list ONLY. Every mutating people view
    keeps ``admin_required``: an administrator who wants to change a person
    should return to their own account first, so the audit trail names them.
    """

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        from accounts.views import _impersonation_actor

        actor = _impersonation_actor(request)
        if actor is None or not actor.is_active or not _is_admin(actor):
            return redirect_to_login(request.get_full_path(), reverse("accounts:login"))
        return view(request, *args, **kwargs)

    return _wrapped


def admin_or_self_required(view):
    """Admin viewing anyone's shift calendar, OR a person viewing their OWN.

    Built for ``person_shift`` / ``person_shift_month`` — the two views the
    owner asked to open up so a person can see their own worked/planned hours
    without becoming administrator-only. Every OTHER shift-adjacent view (the
    edit POST inside ``person_shift`` itself is guarded separately, right
    where it branches — see that view's own comment) stays exactly as
    strict as ``admin_required`` already was; this decorator exists for
    these two GET-first pages alone, not as a general-purpose replacement for
    ``admin_required`` elsewhere in this file.

    THE TEST, IN ORDER: an administrator (``_is_admin``, re-read off the
    database on every request, same as ``admin_required`` itself) is let
    through unconditionally — an admin looking at their OWN record is still
    an admin looking at a shift page, nothing about that case is special.
    Failing that, the requester is let through only when the URL's own
    ``pk`` names THEIR OWN linked ``Person`` (``work_shift.person_for_user``
    — the same lookup ``my_tasks`` and the work-shift middleware already use
    to answer "who is this login"), so a person can open their own two pages
    and no one else's. Anyone else — no admin, no matching person — gets the
    same login-redirect ``admin_required`` would have given them; this is
    strictly a WIDENING of who gets through, never a narrowing.

    A GENERIC HELPER, NOT A ONE-OFF: reads ``pk`` out of the view's own
    keyword arguments (Django hands named URL converters through as kwargs)
    rather than assuming a fixed argument position, so it applies unchanged
    to both ``person_shift(request, pk)`` and
    ``person_shift_month(request, pk, year, month)``.
    """

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if _is_admin(request.user):
            return view(request, *args, **kwargs)
        from .work_shift import person_for_user

        person = person_for_user(request.user)
        if person is not None and person.pk == kwargs.get("pk"):
            return view(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path(), reverse("accounts:login"))

    return _wrapped


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
@admin_or_impersonating_admin_required
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
                _("%(name)s was registered. Detail code: %(code)s — username: %(username)s")
                % {
                    "name": person.display_name,
                    "code": person.detail_code,
                    "username": person.username,
                },
            )
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
            messages.success(
                request,
                _("%(name)s's information was saved.") % {"name": person.display_name},
            )
            return redirect("people:person_profile", pk=person.pk)
    else:
        form = PersonForm(instance=person)
    return render(request, "people/person_form.html", {
        "form": form, "mode": "edit", "person": person,
        "job_titles": spec.JOB_TITLES,
    })


def _reminder_day_counts_for_person(person):
    """This person's own currently-open reminders, one query, bucketed by the
    LOCAL Jalali calendar day each is due on.

    ``marketing.models.Reminder`` HAS NO "OPEN" FLAG TO FILTER ON — this
    round's own close-by-report flow means a reminder that has been dealt
    with is a report row now, not a ``Reminder`` row at all (see that
    model's own docstring and ``marketing/reminders.py::close_with_report``).
    So "open" here is simply "the row still exists": every ``Reminder`` this
    person's login owns, no ``state`` filter needed or possible.

    ``owner`` IS A ``User``, NOT A ``Person`` (see ``Reminder.owner``) — the
    login that actually created the row, not the personnel record it belongs
    to on this page. ``PersonAccount`` is the one link between the two (a
    person has AT MOST ONE login ``User``, per that model's own docstring),
    so this reads it the same direction ``people.seats.primary_login``
    already does, rather than inventing a second notion of "this person's
    account".

    ONE QUERY FOR THE WHOLE PAGE, NOT ONE PER CARD — a person's own reminder
    list is small (it is a private to-do list, not a company-wide table), so
    fetching every ``due_at`` this login owns and bucketing it here in Python
    costs one round trip regardless of how many year/month/day cards the two
    templates go on to render, instead of a query per card that would grow
    with the calendar. ``person_shift``/``person_shift_month`` each call this
    exactly once and then look their own cards up in the ``Counter`` it
    returns — see ``_reminder_count_for_year``/``_reminder_count_for_month``
    just below, and the per-day lookup inline in ``person_shift_month``.
    """
    from collections import Counter

    from django.utils import timezone as dj_timezone

    from cases.jalali import gregorian_to_jalali
    from marketing.models import Reminder

    from .models import PersonAccount
    from .work_shift import _tz

    counts = Counter()
    user_ids = list(
        PersonAccount.objects.filter(person=person).values_list("user_id", flat=True)
    )
    if not user_ids:
        # No login at all (a personnel record with no seat) means no
        # ``Reminder`` row could ever name this person as ``owner`` — every
        # card's count is genuinely zero, not merely unknown.
        return counts
    tz = _tz()
    due_ats = Reminder.objects.filter(owner_id__in=user_ids).values_list("due_at", flat=True)
    for due_at in due_ats:
        local = dj_timezone.localtime(due_at, tz)
        jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
        counts[f"{jy:04d}-{jm:02d}-{jd:02d}"] += 1
    return counts


def _reminder_count_for_year(day_counts, jy: int) -> int:
    """Sum of ``day_counts`` (see ``_reminder_day_counts_for_person``) for
    every day of one Jalali year — a prefix sum over its own keys, never a
    second query."""
    prefix = f"{jy:04d}-"
    return sum(v for k, v in day_counts.items() if k.startswith(prefix))


def _reminder_count_for_month(day_counts, jy: int, jm: int) -> int:
    """The same prefix sum as ``_reminder_count_for_year``, narrowed to one
    Jalali month."""
    prefix = f"{jy:04d}-{jm:02d}-"
    return sum(v for k, v in day_counts.items() if k.startswith(prefix))


def _my_tasks_period_url(jy: int, jm: int | None = None, jd: int | None = None) -> str:
    """The "My Tasks" Reminders-tab URL, pre-filtered to one Jalali period
    (a year, a month, or a single day — whichever of ``jm``/``jd`` is given).

    REUSES THE SAME DATE-RANGE FILTER THAT TAB ALREADY DRAWS — not a second
    filtering mechanism. ``marketing/templates/marketing/_my_tasks_reminders.html``
    already carries a From/To pair of ``data-jalali-datetime`` inputs wired
    through ``static/js/ui.js``'s own ``data-filter-table`` pass
    (``data-filter-colname="Due"``, ``gte``/``lte``) — the exact mechanism
    ``cases/templates/cases/archive.html`` already drives from its own
    ``?ffrom=``/``?fto=`` query parameters (see ``cases/views.py``'s
    ``f_active``). ``marketing/views.py::my_tasks`` now reads this page's
    ``?from=``/``?to=`` the same way and hands them straight back onto those
    two inputs' own ``value=`` attribute — ``ui.js`` runs its filter pass on
    page load regardless of whether a value arrived by typing or by markup,
    so no new JavaScript and no server-side row filtering was written for
    this: the browser filters exactly as it would if the viewer had typed
    these two dates in by hand. ``?tab=reminders`` selects the tab through
    the identical ``?tab=`` mechanism ``marketing/static/marketing/js/
    directory.js`` already reads (shared with the company page's own tab
    strip).

    VALUES ARE PLAIN JALALI STRINGS, never converted to Gregorian — the
    filter compares the Due column's own printed text (also Jalali,
    ``r.due_at|jalali:"Y.m.d H:i"``) against these two, so handing it
    anything else would silently compare two different calendars. A whole
    day/month/year is expressed as its FIRST moment through its LAST
    (00:00 through 23:59); the client-side comparison only ever reads the
    date part of each side (see ``ui.js``'s own ``dateKey``), so the exact
    minute chosen here matters only for what a reader would see if they
    opened the date picker themselves.
    """
    from urllib.parse import urlencode

    from . import shift_hours as sh

    if jd is not None:
        frm = f"{jy:04d}-{jm:02d}-{jd:02d} 00:00"
        to = f"{jy:04d}-{jm:02d}-{jd:02d} 23:59"
    elif jm is not None:
        length = sh.jalali_month_length(jy, jm)
        frm = f"{jy:04d}-{jm:02d}-01 00:00"
        to = f"{jy:04d}-{jm:02d}-{length:02d} 23:59"
    else:
        length = sh.jalali_month_length(jy, 12)
        frm = f"{jy:04d}-01-01 00:00"
        to = f"{jy:04d}-12-{length:02d} 23:59"
    qs = urlencode({"tab": "reminders", "from": frm, "to": to})
    return f"{reverse('marketing:my_tasks')}?{qs}"


@login_required
@admin_or_self_required
def person_shift(request, pk):
    """Edit daily work-shift hours + monthly/yearly hour reports.

    OPENED UP BEYOND ADMINISTRATORS BY ``admin_or_self_required`` (see that
    decorator's own docstring) — a person can now reach their OWN copy of
    this page, but with the "Daily hours" edit card hidden entirely, not
    merely disabled: see ``is_admin`` in the returned context and the
    template's own guard around ``.ppl-shift-card``. ``viewer_is_self``
    (whether the URL's ``pk`` names the SIGNED-IN login's own linked
    ``Person`` — computed once here, not re-derived per card) is what the
    reminder-count badges below use to decide whether their own click can
    safely land on "My Tasks": that page always shows ``request.user``'s OWN
    reminders, so a badge is only ever made a link when THIS is that same
    person; an administrator looking at somebody else's calendar sees the
    right OTHER person's count (see ``_reminder_day_counts_for_person``) but
    the badge stays a plain, unlinked count for them, never a link that
    would silently open the admin's own tasks instead — see the template.
    """
    from datetime import datetime as dt
    from datetime import time as dtime

    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh
    from .work_shift import person_for_user, shift_window

    person = get_object_or_404(Person, pk=pk)
    # Who is actually looking, and at whose record — see this view's own
    # docstring and ``admin_or_self_required``. Computed once, up front,
    # because both the template guard around "Daily hours" and every
    # reminder-count badge below need the same answer.
    is_admin = _is_admin(request.user)
    viewer_person = person_for_user(request.user)
    viewer_is_self = bool(viewer_person and viewer_person.pk == person.pk)

    if request.method == "POST" and not is_admin:
        # The "Daily hours" card that posts here is hidden from a self-viewer
        # entirely (see the template), but hiding a button is a display
        # choice, not a permission check — this is the actual gate: a
        # signed-in person who reaches this URL by hand (or replays an old
        # form) still cannot change their OWN shift definition. Only an
        # administrator may ever submit this form; a person let in by
        # ``admin_or_self_required`` for the read-only view below gets
        # bounced straight back to that read-only page instead.
        return redirect("people:person_shift", pk=person.pk)

    start, end = shift_window(person)
    sh.prune_empty_past_snapshots(person)
    sh.freeze_past_months(person)
    current = sh.ensure_month_snapshot(person)
    sh.refresh_worked(person, current)

    hour_choices = [f"{i:02d}" for i in range(24)]
    minute_choices = [f"{i:02d}" for i in range(60)]

    now = sh.now_local()
    jy, jm, _jd = gregorian_to_jalali(now.year, now.month, now.day)
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

    # Open-reminder badges — TASK 3 of this round. One query for the whole
    # page (see ``_reminder_day_counts_for_person``'s own docstring for why),
    # then a prefix sum per card. ``reminder_url`` is only ever set when
    # ``viewer_is_self`` — an admin looking at someone ELSE's calendar still
    # gets that other person's own correct count (never the admin's own),
    # but as a plain unlinked badge, because "My Tasks" has no way yet to
    # open anyone's list but the signed-in login's own; see this view's own
    # docstring and the template.
    reminder_day_counts = _reminder_day_counts_for_person(person)
    for c in year_cards:
        c["reminder_count"] = _reminder_count_for_year(reminder_day_counts, c["jalali_year"])
        c["reminder_url"] = (
            _my_tasks_period_url(c["jalali_year"]) if viewer_is_self else None
        )
    for c in month_cards:
        c["reminder_count"] = _reminder_count_for_month(
            reminder_day_counts, view_year, c["month"])
        c["reminder_url"] = (
            _my_tasks_period_url(view_year, c["month"]) if viewer_is_self else None
        )

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
            "tracking_label": f"{td} {sh.month_name(tm)} {ty}",
            "hours_per_day": round(sh.shift_minutes(s, e) / 60, 2),
            "is_admin": is_admin,
            "viewer_is_self": viewer_is_self,
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
            messages.error(request, _("Please enter valid times (HH:MM / MM:SS)."))
            return redirect("people:person_shift", pk=person.pk)
        if new_start == new_end:
            messages.error(request, _("Start and end times must be different."))
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
            _(
                "Work shift for %(name)s saved (%(start)s–%(end)s, floating time "
                "%(float)s, reconnect time %(reconnect)s). Only the current month's "
                "planned hours were updated."
            )
            % {
                "name": person.display_name,
                "start": new_start.strftime("%H:%M"),
                "end": new_end.strftime("%H:%M"),
                "float": sh.format_float_mmss(new_float),
                "reconnect": sh.format_float_mmss(new_grace),
            },
        )
        return redirect("people:person_shift", pk=person.pk)

    return render(
        request,
        "people/person_shift.html",
        _shift_ctx(start, end, float_now, grace_now),
    )


@login_required
@admin_or_self_required
def person_shift_month(request, pk, year, month):
    """Day cards for one Jalali month.

    OPENED UP THE SAME WAY ``person_shift`` WAS — see
    ``admin_or_self_required``'s own docstring. This page carries no edit
    form of its own (it is drill-down only), so unlike its parent there is
    no POST branch to additionally guard: read access is the entire
    permission surface here.
    """
    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh
    from .work_shift import person_for_user

    person = get_object_or_404(Person, pk=pk)
    month = int(month)
    year = int(year)
    if month < 1 or month > 12:
        messages.error(request, _("Invalid month."))
        return redirect("people:person_shift", pk=person.pk)

    # Same "who is looking, at whose record" pair ``person_shift`` computes —
    # see that view's own docstring. This page has no admin-only card to
    # hide, so ``is_admin`` is not needed here, only ``viewer_is_self`` for
    # the reminder-count badges' own link-or-not decision (see the template
    # and ``_my_tasks_period_url``).
    viewer_person = person_for_user(request.user)
    viewer_is_self = bool(viewer_person and viewer_person.pk == person.pk)

    sh.freeze_past_months(person)
    days = sh.month_day_details(person, year, month)
    now = sh.now_local()
    cy, cm, _cd = gregorian_to_jalali(now.year, now.month, now.day)
    cards = sh.year_month_cards(person, year)
    month_card = next((c for c in cards if c["month"] == month), None)

    # Open-reminder badges, one per day card — see
    # ``_reminder_day_counts_for_person``'s own docstring for why this is one
    # query for the whole month rather than one per day.
    reminder_day_counts = _reminder_day_counts_for_person(person)
    for d in days:
        key = f"{year:04d}-{month:02d}-{d['jalali_day']:02d}"
        d["reminder_count"] = reminder_day_counts.get(key, 0)
        d["reminder_url"] = (
            _my_tasks_period_url(year, month, d["jalali_day"]) if viewer_is_self else None
        )

    return render(request, "people/person_shift_month.html", {
        "person": person,
        "jalali_year": year,
        "jalali_month": month,
        "month_name": sh.month_name(month),
        "days": days,
        "month_card": month_card,
        "is_current_month": (year, month) == (cy, cm),
        "viewer_is_self": viewer_is_self,
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
    st = shift_status(request.user)
    if not st.get("allowed"):
        return JsonResponse({
            "ok": True,
            "day_minutes": 0,
            "allowed": False,
            "minutes_left": 0,
            "seconds_left": 0,
            "shift_ended": True,
            "name": st.get("name") or "",
        })
    minutes = record_presence_ping(person)
    return JsonResponse({
        "ok": True,
        "day_minutes": minutes,
        "allowed": True,
        "minutes_left": st.get("minutes_left"),
        "seconds_left": _seconds_left(st),
    })


def shift_ended(request):
    """Standalone goodbye screen shown for 40s after the work shift ends."""
    from django.shortcuts import render

    name = (request.GET.get("n") or "").strip() or "colleague"
    # Keep it short — avoid dumping arbitrary query text into the page.
    if len(name) > 80:
        name = name[:80]
    return render(request, "people/shift_ended.html", {
        "shift_end_name": name,
    })


def _seconds_left(st: dict) -> int | None:
    """Remaining seconds until shift end. Never derive from minutes*60."""
    if not st.get("allowed") or st.get("exempt"):
        return None
    if st.get("seconds_left") is not None:
        return max(0, int(st["seconds_left"]))
    end_dt = st.get("effective_end_dt")
    if end_dt is not None:
        from .work_shift import now_local
        return max(0, int((end_dt - now_local()).total_seconds()))
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
        messages.success(
            request,
            _("%(name)s marked «departed».") % {"name": person.display_name},
        )
    else:
        person.status = PersonStatus.ACTIVE
        messages.success(
            request,
            _("%(name)s marked «active» again.") % {"name": person.display_name},
        )
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
    # "One slash but not two" reads as a safe relative path and is not: browsers
    # treat "/\host" as "//host" and leave the site, which turns this link into a
    # way of landing a signed-in colleague on somebody else's login page. Django's
    # helper re-tests the URL with the backslashes swapped, and the accounts app
    # already relies on it for exactly this reason.
    if nxt and url_has_allowed_host_and_scheme(
        nxt,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
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
            # str(...): Unit.LABELS / Role.LABELS now hold gettext_lazy proxies
            # (accounts.constants.Unit/Role.CHOICES) — the join() below needs
            # real str instances, same reason accounts.models.Profile's own
            # unit_label/role_label properties resolve eagerly.
            parts = [x for x in [str(Unit.LABELS.get(p.unit, "")), str(Role.LABELS.get(p.role, ""))] if x]
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
        messages.error(request, _("This person has no login username yet."))
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
    messages.success(
        request,
        _("Password reset for “%(name)s”.") % {"name": person.display_name},
    )
    return redirect("people:person_list")


@login_required
@admin_required
@require_POST
def seat_assign(request, pk):
    """Give this person one or more free seats / roles."""
    person = get_object_or_404(Person, pk=pk)
    wanted = request.POST.getlist("seats")
    if not wanted:
        messages.warning(request, _("No seat was selected."))
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
            _("%(n)s seat(s) assigned to %(name)s: %(seats)s")
            % {"n": len(done), "name": person.display_name, "seats": ", ".join(done)},
        )
    for reason in failed:
        messages.error(request, reason)
    if missing > 0:
        messages.warning(
            request,
            _("%(n)s seat(s) were taken by someone else and were skipped.") % {"n": missing},
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
            _("Login released from %(name)s; account renamed to «%(code)s».")
            % {"name": person.display_name, "code": freed},
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
                _("Cannot close «%(title)s» while %(n)s open task(s) remain — Delegate first.")
                % {"title": title, "n": tasks},
            )
            return redirect("accounts:seat_delegate", pk=role.source_user_id)
        freed = close_seat(role, actor=request.user)
    except SeatError as exc:
        messages.error(request, str(exc))
    else:
        if freed:
            messages.success(
                request,
                _("Role «%(title)s» closed; seat freed as «%(code)s».")
                % {"title": title, "code": freed},
            )
        else:
            messages.success(
                request,
                _("Role «%(title)s» closed for %(name)s.")
                % {"title": title, "name": person.display_name},
            )
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
        _("Role «%(title)s» translated to %(name)s (substitute).")
        % {"title": title, "name": to_person.display_name},
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
        _("Role «%(title)s» returned to %(name)s.") % {"title": title, "name": owner.display_name},
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
        messages.error(request, _("No seat selected."))
        return redirect("people:person_seats", pk=person.pk)
    seat_user = get_object_or_404(User.objects.select_related("profile", "person_link"), pk=int(seat_id))
    link = getattr(seat_user, "person_link", None)
    try:
        if link is None:
            assign_seat(person, seat_user, actor=request.user)
            messages.success(
                request,
                _("Seat «%(code)s» assigned.")
                % {"code": seat_user.profile.seat_code or seat_user.username},
            )
        else:
            if link.person_id == person.pk:
                messages.warning(request, _("That seat already belongs to this person."))
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
                _("Role «%(title)s» translated onto %(name)s.")
                % {"title": title, "name": person.display_name},
            )
    except SeatError as exc:
        messages.error(request, str(exc))
    return redirect("people:person_seats", pk=person.pk)
