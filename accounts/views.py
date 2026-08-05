"""Account management views (admin-only, plus a self profile page)."""
import json
import logging
import os
import re
import time

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .constants import Unit
from .forms import (
    AdminPlatformForm,
    AdminUnitStampsForm,
    ForcePasswordChangeForm,
    SelfPasswordForm,
    SelfProfileForm,
    UserCreateForm,
    UserEditForm,
    _validate_avatar_upload,
    generate_temp_password,
)
from .models import ImpersonationLog, PlatformConfig, Profile

logger = logging.getLogger(__name__)


def _kill_sessions_for(user) -> None:
    """Log a user out of every device immediately (used on cut-off and reset).

    Django doesn't do this on its own: deactivating a user or changing their
    password does not, by itself, end a session they already have open —
    without this, "cut off" access still leaves an already-signed-in browser
    signed in until that session naturally expires.
    """
    target_id = str(user.pk)
    for s in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            data = s.get_decoded()
        except Exception:
            # A single malformed/corrupt session row must not abort cutting
            # off access for this user — skip it and keep going.
            continue
        if str(data.get("_auth_user_id")) == target_id:
            s.delete()


def _is_admin(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_admin)


admin_required = user_passes_test(_is_admin, login_url="accounts:login")


def _can_impersonate(user) -> bool:
    """Platform Administrator OR General Manager — the two roles the user
    named together as "Super Admin / General Manager". Deliberately NOT the
    same test as admin_required: this must not widen access to the rest of
    the Users page (create/edit/reset password/cut off) for a General
    Manager, only to impersonation itself. Django's built-in
    User.is_superuser is deliberately NOT used here even though it was
    suggested — in this app is_superuser only ever gets set for the very
    first account (via createsuperuser); every admin created afterward
    through the normal "Create user" screen has Profile.is_admin=True but
    is_superuser=False (see accounts/signals.py), so gating on is_superuser
    would silently lock out most real admin accounts rather than restrict
    anything meaningfully.
    """
    profile = getattr(user, "profile", None)
    return bool(profile and (profile.is_admin or profile.is_general_manager))


impersonation_access_required = user_passes_test(_can_impersonate, login_url="accounts:login")


@login_required
@admin_required
def admin_console(request):
    """Person-centric management landing page for administrators."""
    from collections import Counter

    from cases.models import Case, Client
    from people.models import Person, PersonRole

    people = list(
        Person.objects.prefetch_related("roles", "accounts")
        .order_by("first_name_en", "last_name_en", "detail_code")
    )
    roles = list(PersonRole.objects.select_related("person").all())

    role_counts = Counter()
    for role in roles:
        role_counts[role.title_line] += 1

    person_rows = []
    active_people = 0
    with_roles = 0
    without_roles = 0
    for person in people:
        person_roles = list(person.roles.all())
        if person.is_active:
            active_people += 1
        if person_roles:
            with_roles += 1
        else:
            without_roles += 1
        person_rows.append({
            "pk": person.pk,
            "name": person.display_name,
            "detail_code": person.detail_code,
            "username": person.username or "",
            "active": person.is_active,
            "role_count": len(person_roles),
            "roles": [
                {
                    "title": r.title_line,
                    "seat": r.seat_code or "",
                    "code": r.internal_code or "",
                }
                for r in person_roles
            ],
        })

    role_summary = [
        {"title": title, "count": count}
        for title, count in sorted(role_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    context = {
        "person_count": len(people),
        "active_people": active_people,
        "with_roles": with_roles,
        "without_roles": without_roles,
        "role_assignment_count": len(roles),
        "case_count": Case.objects.count(),
        "client_count": Client.objects.count(),
        "person_rows": person_rows,
        "role_summary": role_summary,
    }
    return render(request, "accounts/admin_console.html", context)


@login_required
@impersonation_access_required
def user_list(request):
    """Seats catalogue — grouped by Unit & role."""
    from people.constants import PersonStatus
    from people.models import Person
    from people.seats import is_blank_org_seat, purge_unassigned_seats

    actor_profile = getattr(request.user, "profile", None)
    can_manage_users = bool(actor_profile and actor_profile.is_admin)
    if can_manage_users:
        try:
            purge_unassigned_seats()
        except Exception:
            logger.exception("purge_unassigned_seats failed")

    users = list(
        User.objects.select_related(
            "profile", "person_link__person",
        ).order_by(
            "profile__unit", "profile__role", "profile__supply_kind",
            "profile__is_general_manager", "profile__is_admin",
            "profile__seat_code", "pk",
        )
    )
    reveal_credential = (
        request.session.pop("_reveal_credential", None) if can_manage_users else None
    )

    groups = {}
    for u in users:
        profile = u.profile
        if is_blank_org_seat(profile) and (u.username or "").lower() != "admin":
            continue
        if profile.is_admin:
            title = "Administrator"
            kind = "admin"
        elif profile.is_general_manager:
            title = "General Manager"
            kind = "gm"
        else:
            kind = "role"
            parts = [p for p in [profile.unit_label, profile.role_label] if p]
            if profile.supply_kind:
                from accounts.constants import SupplyKind
                sk = SupplyKind.LABELS.get(profile.supply_kind, profile.supply_kind)
                if sk:
                    parts.append(f"({sk})")
            title = " ".join(parts) if parts else (profile.title_line or "Seat")
        bucket = groups.setdefault(title, {
            "title": title,
            "seats": [],
            "key": title,
            "kind": kind,
        })
        person = getattr(getattr(u, "person_link", None), "person", None)

        is_sub = False
        open_tasks = 0
        role_id = None
        if person is not None and kind == "role":
            from people.models import PersonRole, SeatTenure
            from people.seats import open_task_count
            role = PersonRole.objects.filter(person=person, source_user=u).first()
            if role is not None:
                role_id = role.pk
                try:
                    open_tasks = open_task_count(role)
                except Exception:
                    open_tasks = 0
                is_sub = SeatTenure.objects.filter(
                    source_user=u, kind=SeatTenure.KIND_SUBSTITUTE, ended_at__isnull=True,
                ).exists()

        bucket["seats"].append({
            "user": u,
            "index": profile.seat_code or "—",
            "name": person.display_name if person else "—",
            "username": (person.username if person else u.username) or "—",
            "detail_code": person.detail_code if person else "—",
            "active": u.is_active,
            "pending": bool(profile.must_change_password),
            "can_edit": can_manage_users and u.last_login is None,
            "held": person is not None,
            "holder": person.display_name if person else "",
            "can_assign": can_manage_users and kind == "role" and person is None,
            "can_manage": can_manage_users and kind == "role",
            "is_sub": is_sub,
            "open_tasks": open_tasks,
            "role_id": role_id,
        })

    role_groups = list(groups.values())
    # Flat list for virtual scroll: group headers + seat rows.
    seat_rows_json = []
    for g in role_groups:
        seat_rows_json.append({
            "t": "g",
            "title": g["title"],
            "count": len(g["seats"]),
            "kind": g.get("kind") or "role",
        })
        for s in g["seats"]:
            seat_rows_json.append({
                "t": "r",
                "pk": s["user"].pk,
                "index": s["index"],
                "name": s["name"],
                "username": s["username"],
                "detail_code": s["detail_code"],
                "active": s["active"],
                "pending": s["pending"],
                "can_edit": s["can_edit"],
                "held": s["held"],
                "holder": s["holder"],
                "can_assign": s["can_assign"],
                "can_manage": s["can_manage"],
                "is_sub": s["is_sub"],
                "open_tasks": s["open_tasks"],
                "role_id": s["role_id"],
            })

    people_choices = []
    if can_manage_users:
        people_choices = [
            {"pk": p.pk, "label": f"{p.display_name} · {p.detail_code}"}
            for p in Person.objects.filter(status=PersonStatus.ACTIVE)
            .order_by("first_name_en", "last_name_en", "detail_code")
        ]

    row_flash = request.session.pop("_seat_row_flash", None)
    if row_flash and seat_rows_json:
        out = []
        target = str(row_flash.get("pk") or "")
        for item in seat_rows_json:
            out.append(item)
            if item.get("t") == "r" and str(item.get("pk") or "") == target:
                out.append({
                    "t": "m",
                    "level": row_flash.get("level") or "ok",
                    "text": row_flash.get("text") or "",
                })
        seat_rows_json = out

    return render(request, "accounts/user_list.html", {
        "users": users,
        "role_groups": role_groups,
        "seat_rows_json": seat_rows_json,
        "people_choices": people_choices,
        "people_choices_json": people_choices,
        "reveal_credential": reveal_credential,
        "can_manage_users": can_manage_users,
        "row_flash_pk": (row_flash or {}).get("pk"),
    })


@login_required
@admin_required
@require_POST
def seat_assign(request, pk):
    """Assign a vacant catalogue seat to a person (first person↔seat link)."""
    from people.models import Person
    from people.seats import SeatError, assign_seat

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    profile = seat_user.profile
    if profile.is_admin or profile.is_general_manager:
        messages.error(request, "Administrator and General Manager seats are not assigned this way.")
        return redirect("accounts:user_list")

    link = getattr(seat_user, "person_link", None)
    if link is not None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "warn",
            "text": "This seat is occupied — use Translate instead of Assign.",
        }
        return redirect("accounts:user_list")

    to_pk = (request.POST.get("person") or "").strip()
    if not to_pk.isdigit():
        messages.error(request, "Choose a person to assign this seat to.")
        return redirect("accounts:user_list")
    person = get_object_or_404(Person, pk=int(to_pk))
    if not person.is_active:
        messages.error(request, "Cannot assign a seat to a departed person.")
        return redirect("accounts:user_list")

    try:
        assign_seat(person, seat_user, actor=request.user)
        text = (
            f"Seat «{profile.seat_code or seat_user.username}» "
            f"assigned to {person.display_name}."
        )
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "ok", "text": text,
        }
    except SeatError as exc:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": str(exc),
        }
    return redirect("accounts:user_list")


@login_required
@admin_required
@require_POST
def seat_translate(request, pk):
    """Temporarily hand an occupied seat to another person (substitute)."""
    from people.models import Person, PersonRole
    from people.seats import SeatError, translate_role

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    link = getattr(seat_user, "person_link", None)
    if link is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err",
            "text": "Vacant seats use Assign, not Translate.",
        }
        return redirect("accounts:user_list")

    to_pk = (request.POST.get("person") or "").strip()
    if not to_pk.isdigit():
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err",
            "text": "Choose a person to receive this seat.",
        }
        return redirect("accounts:user_list")
    person = get_object_or_404(Person, pk=int(to_pk))
    role = PersonRole.objects.filter(person=link.person, source_user=seat_user).first()
    if role is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err",
            "text": "That seat has no role to translate.",
        }
        return redirect("accounts:user_list")
    try:
        holder_name = link.person.display_name
        title = role.title_line
        translate_role(role, person, actor=request.user)
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "ok",
            "text": (
                f"Seat «{title}» translated from {holder_name} "
                f"to {person.display_name} (substitute)."
            ),
        }
    except SeatError as exc:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": str(exc),
        }
    return redirect("accounts:user_list")


@login_required
@admin_required
@require_POST
def seat_return(request, pk):
    """Return a substitute-held seat to the origin owner."""
    from people.models import PersonRole
    from people.seats import SeatError, return_role

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    link = getattr(seat_user, "person_link", None)
    if link is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": "This seat is vacant.",
        }
        return redirect("accounts:user_list")
    role = PersonRole.objects.filter(person=link.person, source_user=seat_user).first()
    if role is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": "No role on this seat.",
        }
        return redirect("accounts:user_list")
    try:
        holder = link.person.display_name
        return_role(role, actor=request.user)
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "ok",
            "text": f"Seat returned from substitute {holder} to the owner.",
        }
    except SeatError as exc:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": str(exc),
        }
    return redirect("accounts:user_list")


@login_required
@admin_required
@require_POST
def seat_close(request, pk):
    """Close/free a seat when open tasks are zero."""
    from people.models import PersonRole
    from people.seats import SeatError, close_seat, open_task_count

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    link = getattr(seat_user, "person_link", None)
    if link is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "warn", "text": "This seat is already vacant.",
        }
        return redirect("accounts:user_list")
    role = PersonRole.objects.filter(person=link.person, source_user=seat_user).first()
    if role is None:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": "No role on this seat.",
        }
        return redirect("accounts:user_list")
    try:
        tasks = open_task_count(role)
        if tasks > 0:
            request.session["_seat_row_flash"] = {
                "pk": seat_user.pk, "level": "warn",
                "text": (
                    f"Cannot Close while {tasks} open task(s) remain — "
                    "Delegate them first."
                ),
            }
            return redirect("accounts:seat_delegate", pk=seat_user.pk)
        title = role.title_line
        close_seat(role, actor=request.user)
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "ok",
            "text": f"Seat «{title}» closed and returned to the catalogue.",
        }
    except SeatError as exc:
        request.session["_seat_row_flash"] = {
            "pk": seat_user.pk, "level": "err", "text": str(exc),
        }
    return redirect("accounts:user_list")


@login_required
@admin_required
def seat_history(request, pk):
    """Timeline of assign / translate / return / delegate / close for one seat."""
    from people.seat_history import build_seat_timeline

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    profile = seat_user.profile
    person = getattr(getattr(seat_user, "person_link", None), "person", None)
    timeline = build_seat_timeline(seat_user)
    return render(request, "accounts/seat_history.html", {
        "seat_user": seat_user,
        "profile": profile,
        "person": person,
        "timeline": timeline,
        "seat_index": profile.seat_code or "—",
        "seat_title": profile.title_line or seat_user.username,
    })


@login_required
@admin_required
def seat_delegate(request, pk):
    """Delegate open tasks from this seat to another same-role seat."""
    from people.models import Person, PersonRole
    from people.seats import (
        SeatError, delegate_tasks, open_cases_for_seat, open_task_count, delegate_recipients,
    )

    seat_user = get_object_or_404(
        User.objects.select_related("profile", "person_link__person"),
        pk=pk,
    )
    link = getattr(seat_user, "person_link", None)
    if link is None:
        messages.error(request, "This seat is vacant — nothing to delegate.")
        return redirect("accounts:user_list")
    role = PersonRole.objects.filter(person=link.person, source_user=seat_user).first()
    if role is None:
        messages.error(request, "No role on this seat.")
        return redirect("accounts:user_list")

    if request.method == "POST":
        to_pk = (request.POST.get("to_person") or "").strip()
        case_ids = request.POST.getlist("cases")
        try:
            if not to_pk.isdigit():
                raise SeatError("Choose a person with the same role seat.")
            to_person = get_object_or_404(Person, pk=int(to_pk))
            n = delegate_tasks(role, to_person, case_ids, actor=request.user)
            messages.success(
                request,
                f"Delegated {n} open task(s) to {to_person.display_name}.",
            )
            remaining = open_task_count(role)
            if remaining == 0 and request.POST.get("then_close") == "1":
                from people.seats import close_seat
                close_seat(role, actor=request.user)
                messages.success(request, "Seat closed after delegating open tasks.")
            return redirect("accounts:user_list")
        except SeatError as exc:
            messages.error(request, str(exc))

    cases = list(
        open_cases_for_seat(role).select_related("client").order_by("-updated_at", "-created_at")[:300]
    )
    recipients = delegate_recipients(role, exclude_person_id=link.person_id)

    def _offer_label(c):
        return (c.offer_stage_label or c.offer_type_label or "—") or "—"

    f_docs = sorted({(c.doc_no or "").strip() for c in cases if (c.doc_no or "").strip()})
    f_clients = sorted({c.client.name for c in cases if c.client and c.client.name})
    f_statuses = sorted({(c.status_label or "").strip() for c in cases if (c.status_label or "").strip()})
    f_offers = sorted({_offer_label(c) for c in cases})

    return render(request, "accounts/seat_delegate.html", {
        "seat_user": seat_user,
        "role": role,
        "person": link.person,
        "open_cases": cases,
        "recipients": recipients,
        "open_tasks": len(cases),
        "then_close": request.GET.get("then_close") == "1",
        "f_docs": f_docs,
        "f_clients": f_clients,
        "f_statuses": f_statuses,
        "f_offers": f_offers,
    })


def _jalali_dt(value) -> str:
    if not value:
        return "—"
    try:
        from django.utils import timezone
        from people.models import format_jalali
        local = timezone.localtime(value)
        d = format_jalali(local.date()).replace("/", ".")
        return f"{d} {local.strftime('%H:%M')}" if d else "—"
    except Exception:
        return "—"


@sensitive_post_parameters()
@login_required
@admin_required
def user_create(request):
    from people.models import Person

    locked_person = None
    person_pk = (request.GET.get("person") or request.POST.get("locked_person") or "").strip()
    if person_pk.isdigit():
        locked_person = Person.objects.filter(pk=int(person_pk)).first()

    if request.method == "POST":
        form = UserCreateForm(
            request.POST, request.FILES, locked_person=locked_person,
        )
        if form.is_valid():
            user = form.save()
            code = getattr(form, "seat_code", None) or user.profile.seat_code
            person = form.cleaned_data.get("person") or locked_person
            if person is not None and hasattr(user, "person_link"):
                messages.success(
                    request,
                    f"Seat “{code}” created and linked to {person.display_name}.",
                )
                return redirect("people:person_seats", pk=person.pk)
            request.session["_reveal_credential"] = {
                "username": user.username,
                "password": form.generated_password,
                "label": f"Seat created: {code}",
            }
            messages.success(
                request,
                f"Seat “{code}” created (inactive until assigned in People).",
            )
            return redirect("accounts:user_list")
    else:
        form = UserCreateForm(locked_person=locked_person)
    return render(request, "accounts/user_form.html", {
        "form": form,
        "mode": "create",
        "locked_person": locked_person,
    })


@login_required
@admin_required
def user_edit(request, pk):
    user = get_object_or_404(
        User.objects.select_related("profile", "person_link", "person_link__person"),
        pk=pk,
    )
    if user.last_login is not None:
        messages.error(
            request,
            "This seat can no longer be edited — someone has already signed in with it.",
        )
        return redirect("accounts:user_list")
    profile = user.profile
    person = getattr(getattr(user, "person_link", None), "person", None)
    if request.method == "POST":
        form = UserEditForm(request.POST, request.FILES, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Seat updated.")
            return redirect("accounts:user_list")
    else:
        gender_map = {
            "آقا": "MALE", "خانم": "FEMALE",
            "MALE": "MALE", "FEMALE": "FEMALE",
        }
        if person is not None:
            gender_init = gender_map.get((person.gender or "").strip(), "") or profile.gender
        else:
            gender_init = profile.gender
        form = UserEditForm(
            user=user,
            initial={
                "seat_code": profile.seat_code or "",
                "first_name": (person.first_name_en if person else ""),
                "last_name": (person.last_name_en if person else ""),
                "email": user.email,
                "is_admin": profile.is_admin,
                "is_general_manager": profile.is_general_manager,
                "unit": profile.unit,
                "role": profile.role,
                "supply_kind": profile.supply_kind,
                "org_number": (person.detail_code if person else ""),
                "gender": gender_init if person else "",
            },
        )
    return render(
        request, "accounts/user_form.html",
        {
            "form": form,
            "mode": "edit",
            "edited_user": user,
            "held_person": person,
        },
    )


@sensitive_post_parameters()
@login_required
@admin_required
@require_POST
def user_reset_password(request, pk):
    """Generate a fresh one-time password for a user — the only way a
    password is ever set for someone other than themself. Shows the new
    password to the admin exactly once (via the same reveal mechanism as
    user_create) and immediately signs the user out everywhere, since their
    old password is no longer valid credentials for anyone to be using.
    """
    target = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, "Use your profile page to change your own password.")
        return redirect("accounts:user_list")

    generated = generate_temp_password()
    target.set_password(generated)
    target.save(update_fields=["password"])
    profile = target.profile
    profile.must_change_password = True
    profile.save(update_fields=["must_change_password"])
    _kill_sessions_for(target)

    request.session["_reveal_credential"] = {
        "username": target.username,
        "password": generated,
        "label": f"Password reset for {target.get_full_name() or target.username}",
    }
    messages.success(
        request, f"Password reset for “{target.get_full_name() or target.username}”.")
    return redirect("accounts:user_list")


@sensitive_post_parameters()
@login_required
def force_password_change(request):
    """Landing page for an account with must_change_password set — reached
    right after signing in with a temporary password (a freshly created
    account, or one an admin just reset). Nothing else on the platform is
    reachable until this is done — enforced platform-wide by
    accounts.middleware.MustChangePasswordMiddleware, mirroring the same
    always-on-gate pattern already used for the license check. This view is
    the one way through that gate.
    """
    profile = getattr(request.user, "profile", None)
    if profile is None or not profile.must_change_password:
        return redirect("core:home")

    if request.method == "POST":
        form = ForcePasswordChangeForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Your password has been set. Welcome in.")
            return redirect("core:home")
    else:
        form = ForcePasswordChangeForm(user=request.user)
    return render(request, "accounts/force_password_change.html", {"form": form})


# ---------------------------------------------------------------------------
# Impersonation ("log in as user")
# ---------------------------------------------------------------------------
# Built entirely on Django's own, stable, documented auth primitives — no
# third-party package — since this is the single most security-sensitive
# piece of this change and the one most worth being easy to read end to end.
# The technique (attach `.backend` to the target user, then call auth_login)
# is exactly what Django's own documentation describes for signing a user in
# without checking a password: https://docs.djangoproject.com/en/5.2/topics/auth/default/#auth-web-requests
# ("if you have a custom auth backend... you can also simply set the
# backend attribute").
# ---------------------------------------------------------------------------
_AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


@login_required
@impersonation_access_required
@require_POST
def impersonate_start(request, pk):
    """Admin 'log in as' a user — full read/write capability exactly as that
    user would have it, for operational support and troubleshooting.

    Restricted to Platform Administrator (Profile.is_admin) OR General
    Manager (Profile.is_general_manager) accounts ONLY — the user named both
    of these together, twice, as "Super Admin / General Manager (مدیر کل)",
    so both are treated as authorized. A departmental manager (Technical
    Manager, Commercial Manager, or any other unit Manager/Supervisor/
    Expert) can NEVER reach this view, under any circumstances: they fail
    both @impersonation_access_required above and the explicit re-check
    below, and they have no UI path to it either — the "Log in as" action
    only renders on the Users page for someone who already passes this same
    check (see user_list). Extending eligibility to General Manager
    accounts deliberately did NOT widen @admin_required itself (which still
    gates user creation, editing, password resets, and backups to Platform
    Administrators only, unchanged) — only impersonation specifically.

    While impersonating, request.user genuinely IS the target user (not the
    admin with extra powers layered on) — every action taken is attributed to
    that user precisely as if they had signed in and done it themselves,
    which is also exactly what makes ordinary case/form history correct with
    no changes needed anywhere else. What this view adds is the separate,
    permanent record of who did the switching and when (ImpersonationLog):
    cross-referencing an action's timestamp against that log answers "was
    this really them, or an admin standing in for them" whenever that
    question matters.
    """
    actor_profile = getattr(request.user, "profile", None)
    if actor_profile is None or not (actor_profile.is_admin or actor_profile.is_general_manager):
        # Belt-and-suspenders: @impersonation_access_required already blocks
        # this request from reaching here for anyone who is neither a
        # Platform Administrator nor a General Manager, including every
        # departmental manager. This explicit re-check exists so that fact
        # is not implicit.
        messages.error(request, "Only a Platform Administrator or General Manager may impersonate a user.")
        return redirect("accounts:user_list")

    if request.session.get("impersonator_id"):
        messages.error(request, "You are already viewing the platform as another user. Return to your own account first.")
        return redirect("accounts:user_list")

    target = get_object_or_404(User.objects.select_related("profile"), pk=pk)

    if target.pk == request.user.pk:
        messages.error(request, "You are already signed in as yourself.")
        return redirect("accounts:user_list")
    if not target.is_active:
        messages.error(request, "This account is closed and cannot be impersonated.")
        return redirect("accounts:user_list")
    target_profile = getattr(target, "profile", None)
    if target_profile is not None and (target_profile.is_admin or target_profile.is_general_manager):
        # Also blocks impersonating yourself-as-a-second-privileged-account and
        # any chained-privilege scenario: a Platform Administrator or General
        # Manager identity can never be entered via impersonation, only by
        # signing in with its own credentials — this now matters for General
        # Manager accounts too, since they can initiate impersonation.
        messages.error(request, "Administrator and General Manager accounts cannot be impersonated.")
        return redirect("accounts:user_list")

    original_admin_id = request.user.pk
    original_admin_username = request.user.username

    log_entry = ImpersonationLog.objects.create(
        admin=request.user, admin_username=original_admin_username,
        target=target, target_username=target.username,
    )

    target.backend = _AUTH_BACKEND
    # Prevent shift login stamp for the impersonated person (session markers
    # are written only after auth_login rotates the session key).
    request._ft_skip_shift_stamp = True
    auth_login(request, target)
    # auth_login() deliberately rotates the session key (it prevents session
    # fixation), which would wipe anything set before this call — so the
    # impersonation markers are written to the session only after, never
    # before.
    request.session["impersonator_id"] = original_admin_id
    request.session["impersonator_username"] = original_admin_username
    request.session["impersonation_log_id"] = log_entry.pk

    messages.info(
        request,
        f"You are now viewing the platform as {target.get_full_name() or target.username}.")
    return redirect("core:home")


@login_required
def impersonate_stop(request):
    """Return to the real admin account.

    Deliberately reachable regardless of what the currently-impersonated
    user could normally access (no @admin_required here) — it depends only
    on the session marker impersonate_start set, never on the permissions of
    whoever request.user currently resolves to.
    """
    admin_id = request.session.get("impersonator_id")
    if not admin_id:
        return redirect("core:home")

    admin_user = User.objects.filter(pk=admin_id).first()
    log_id = request.session.get("impersonation_log_id")

    # Skip shift stamps for both the target logout side-effects and admin login.
    request._ft_skip_shift_stamp = True

    # Clear the markers unconditionally, so a since-deleted admin account can
    # never leave someone stuck impersonating with no way back.
    request.session.pop("impersonator_id", None)
    request.session.pop("impersonator_username", None)
    request.session.pop("impersonation_log_id", None)

    if log_id:
        ImpersonationLog.objects.filter(pk=log_id, ended_at__isnull=True).update(
            ended_at=timezone.now())

    if admin_user is None or not admin_user.is_active:
        messages.error(
            request,
            "Could not return to the administrator account automatically. Please sign in again.")
        return redirect("accounts:login")

    admin_user.backend = _AUTH_BACKEND
    auth_login(request, admin_user)
    messages.info(request, "You're back in your own account.")
    return redirect("accounts:user_list")


@sensitive_post_parameters()
@login_required
def my_profile(request):
    profile = request.user.profile
    form = SelfProfileForm(instance=profile)
    if request.method == "POST":
        if "save_avatar" in request.POST:
            raw = request.FILES.get("avatar")
            if not raw:
                messages.error(request, "Choose a photo to upload.")
                return redirect("accounts:my_profile")
            try:
                raw = _validate_avatar_upload(raw)
            except ValidationError as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
                return redirect("accounts:my_profile")
            try:
                raw.seek(0)
            except Exception:
                pass
            # Copy bytes into a ContentFile so the write cannot depend on a
            # half-consumed upload stream (same pattern as signature saves).
            import uuid
            from django.core.files.base import ContentFile

            data = raw.read()
            if not data:
                messages.error(request, "The selected photo was empty. Try another file.")
                return redirect("accounts:my_profile")
            orig = (getattr(raw, "name", "") or "").lower()
            if orig.endswith(".png"):
                ext = ".png"
            elif orig.endswith(".webp"):
                ext = ".webp"
            elif orig.endswith(".gif"):
                ext = ".gif"
            else:
                ext = ".jpg"
            base = f"{uuid.uuid4().hex}{ext}"
            profile = Profile.objects.get(pk=profile.pk)
            if profile.avatar:
                try:
                    profile.avatar.delete(save=False)
                except Exception:
                    pass
            profile.avatar.save(base, ContentFile(data), save=True)
            messages.success(request, "Profile photo saved.")
            return redirect("accounts:my_profile")
        elif "clear_avatar" in request.POST:
            if profile.avatar:
                profile.avatar.delete(save=False)
                profile.avatar = None
                profile.save(update_fields=["avatar"])
            messages.success(request, "Profile photo removed.")
            return redirect("accounts:my_profile")
        else:
            form = SelfProfileForm(request.POST, request.FILES, instance=profile)
            if form.is_valid():
                form.save()
                messages.success(request, "Your profile was updated.")
                return redirect("accounts:my_profile")

    # Only expose image URLs when the file actually exists on disk — otherwise
    # the browser shows a broken <img> whose alt text looks like the word
    # "signature" inside the preview frame.
    def _media_url(field):
        if not field or not getattr(field, "name", None):
            return ""
        try:
            if field.storage.exists(field.name):
                return field.url
        except Exception:
            return ""
        return ""

    profile.refresh_from_db()
    return render(
        request, "accounts/my_profile.html",
        {
            "form": form,
            "profile": profile,
            "signature_url": _media_url(profile.signature),
            "avatar_url": _media_url(profile.avatar),
        },
    )


def _parse_mmss_post(request, hidden_name: str, m_name: str, s_name: str, default_m: int, default_s: int = 0) -> int:
    raw = (request.POST.get(hidden_name) or "").strip()
    if raw and ":" in raw:
        a, b = raw.split(":", 1)
        return max(0, int(a) * 60 + int(b))
    mm = int((request.POST.get(m_name) or str(default_m)).strip())
    ss = int((request.POST.get(s_name) or str(default_s)).strip())
    return max(0, mm * 60 + ss)


def _parse_hm_post(request, prefix: str):
    from datetime import datetime as dt

    raw = (request.POST.get(prefix) or "").strip()
    if raw:
        return dt.strptime(raw, "%H:%M").time()
    hh = (request.POST.get(f"{prefix}_h") or "").strip()
    mm = (request.POST.get(f"{prefix}_m") or "").strip()
    return dt.strptime(f"{hh}:{mm}", "%H:%M").time()


def _apply_global_daily_hours(start, end, float_seconds: int, grace_seconds: int) -> int:
    """Persist platform defaults and push them to every Person. Returns count."""
    from people import shift_hours as sh
    from people.models import Person

    cfg = PlatformConfig.load()
    cfg.default_work_start = start
    cfg.default_work_end = end
    cfg.default_float_seconds = max(0, int(float_seconds))
    cfg.default_reconnect_grace_seconds = max(0, int(grace_seconds))
    cfg.save(update_fields=[
        "default_work_start",
        "default_work_end",
        "default_float_seconds",
        "default_reconnect_grace_seconds",
        "updated_at",
    ])
    n = 0
    for person in Person.objects.all().iterator():
        sh.apply_shift_change(
            person,
            start,
            end,
            float_seconds=float_seconds,
            reconnect_grace_seconds=grace_seconds,
        )
        n += 1
    return n


@sensitive_post_parameters()
@login_required
def settings_page(request):
    """Settings hub: Appearance, password; admin also stamps + platform."""
    from people import shift_hours as sh

    profile = request.user.profile
    is_admin = bool(profile.is_admin)
    pw_form = SelfPasswordForm(user=request.user)
    platform_form = None
    stamps_form = None
    cfg = PlatformConfig.load() if is_admin else None
    if is_admin:
        platform_form = AdminPlatformForm(instance=cfg)
        stamps_form = AdminUnitStampsForm(instance=cfg)

    settings_url = reverse("accounts:settings")

    if request.method == "POST":
        if "change_password" in request.POST:
            pw_form = SelfPasswordForm(request.POST, user=request.user)
            if pw_form.is_valid():
                pw_form.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "Your password was changed.")
                return redirect(settings_url)
        elif "save_platform" in request.POST and is_admin:
            platform_form = AdminPlatformForm(request.POST, instance=PlatformConfig.load())
            if platform_form.is_valid():
                platform_form.save()
                messages.success(request, "Platform settings saved.")
                return redirect(settings_url)
        elif "save_daily_hours" in request.POST and is_admin:
            try:
                new_start = _parse_hm_post(request, "work_start")
                new_end = _parse_hm_post(request, "work_end")
                new_float = _parse_mmss_post(request, "float_time", "float_m", "float_s", 15, 0)
                new_grace = _parse_mmss_post(request, "reconnect_time", "grace_m", "grace_s", 10, 0)
            except ValueError:
                messages.error(request, "Please enter valid times (HH:MM / MM:SS).")
                return redirect(settings_url)
            if new_start == new_end:
                messages.error(request, "Start and end times must be different.")
                return redirect(settings_url)
            n = _apply_global_daily_hours(new_start, new_end, new_float, new_grace)
            messages.success(
                request,
                f"Daily hours updated for all people ({n}): "
                f"{new_start.strftime('%H:%M')}–{new_end.strftime('%H:%M')}, "
                f"floating {sh.format_float_mmss(new_float)}, "
                f"reconnect {sh.format_float_mmss(new_grace)}.",
            )
            return redirect(settings_url)
        elif "save_unit_stamps" in request.POST and is_admin:
            stamps_form = AdminUnitStampsForm(
                request.POST, request.FILES, instance=PlatformConfig.load(),
            )
            if stamps_form.is_valid():
                stamps_form.save()
                messages.success(request, "Unit stamps updated.")
                return redirect(settings_url)

    def _media_url(field):
        if not field or not getattr(field, "name", None):
            return ""
        try:
            if field.storage.exists(field.name):
                return field.url
        except Exception:
            return ""
        return ""

    stamp_urls = {}
    daily = None
    hour_choices = [f"{i:02d}" for i in range(24)]
    minute_choices = [f"{i:02d}" for i in range(60)]
    if cfg is not None:
        cfg.refresh_from_db()
        stamp_urls = {
            "commercial": _media_url(cfg.stamp_commercial),
            "technical": _media_url(cfg.stamp_technical),
            "supply": _media_url(cfg.stamp_supply),
        }
        fs = int(cfg.default_float_seconds or 900)
        gs = int(cfg.default_reconnect_grace_seconds or 600)
        fm, fsec = divmod(fs, 60)
        gm, gsec = divmod(gs, 60)
        ws = cfg.default_work_start
        we = cfg.default_work_end
        daily = {
            "start_h": f"{ws.hour:02d}",
            "start_m": f"{ws.minute:02d}",
            "end_h": f"{we.hour:02d}",
            "end_m": f"{we.minute:02d}",
            "float_m": f"{fm:02d}",
            "float_s": f"{fsec:02d}",
            "grace_m": f"{gm:02d}",
            "grace_s": f"{gsec:02d}",
            "hours_per_day": round(sh.shift_minutes(ws, we) / 60, 2),
        }

    return render(request, "accounts/settings.html", {
        "profile": profile,
        "is_admin": is_admin,
        "pw_form": pw_form,
        "platform_form": platform_form,
        "stamps_form": stamps_form,
        "stamp_urls": stamp_urls,
        "daily": daily,
        "hour_choices": hour_choices,
        "minute_choices": minute_choices,
        "float_minute_choices": [f"{i:02d}" for i in range(60)],
    })


# ---------------------------------------------------------------------------
# Login brute-force throttle
# ---------------------------------------------------------------------------
# Failed sign-ins are counted in the shared (database) cache, so a lockout is
# enforced across every gunicorn worker. Two limits guard both a single account
# being hammered and one host spraying many usernames.
LOGIN_MAX_USER_ATTEMPTS = 8      # per (IP, username) window
LOGIN_MAX_IP_ATTEMPTS = 40       # per IP window (username spraying)
LOGIN_WINDOW_SECONDS = 15 * 60


def _client_ip(request) -> str:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _login_keys(request):
    ip = _client_ip(request)
    username = (request.POST.get("username", "") or "").strip().lower()
    return f"login_fail:{ip}:{username}", f"login_fail_ip:{ip}"


def _login_is_locked(request) -> bool:
    user_key, ip_key = _login_keys(request)
    try:
        return (cache.get(user_key, 0) >= LOGIN_MAX_USER_ATTEMPTS
                or cache.get(ip_key, 0) >= LOGIN_MAX_IP_ATTEMPTS)
    except Exception:
        return False


def _login_register_failure(request) -> None:
    user_key, ip_key = _login_keys(request)
    for key in (user_key, ip_key):
        try:
            cache.set(key, cache.get(key, 0) + 1, LOGIN_WINDOW_SECONDS)
        except Exception:
            pass


def _login_clear_failures(request) -> None:
    user_key, _ip_key = _login_keys(request)
    try:
        cache.delete(user_key)
    except Exception:
        pass


@sensitive_post_parameters()
@require_POST
def login_check(request):
    """AJAX sign-in: validate credentials, create the session, never rely on a
    classic username/password form POST (browsers would offer to save it)."""
    if _login_is_locked(request):
        return JsonResponse({
            "ok": False,
            "locked": True,
            "message": "Too many attempts. Please wait a few minutes and try again.",
        })
    username = (request.POST.get("username", "") or "").strip()
    password = request.POST.get("password", "") or ""

    # Tell a closed account apart from a plain wrong password — but do this
    # WITHOUT checking whether the submitted password was actually correct.
    # The previous version only showed this message when check_password()
    # passed, which let anyone confirm a guessed password was right even
    # though the account is disabled and they still can't sign in with it.
    existing = User.objects.filter(username__iexact=username).first()
    if existing is not None and not existing.is_active:
        _login_register_failure(request)
        return JsonResponse({
            "ok": False,
            "disabled": True,
            "message": "This account has been closed by the administrator.",
        })

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        _login_register_failure(request)
        if _login_is_locked(request):
            return JsonResponse({
                "ok": False,
                "locked": True,
                "message": "Too many attempts. Please wait a few minutes and try again.",
            })
        return JsonResponse({
            "ok": False,
            "message": "Invalid username or password.",
        })

    # Work-shift gate (admin / GM exempt). Checked after credentials so the
    # message can include the person's name without confirming a wrong password.
    try:
        from people.work_shift import outside_shift_login_message, shift_status
        st = shift_status(user)
        if not st["allowed"]:
            return JsonResponse({
                "ok": False,
                "shift": True,
                "message": outside_shift_login_message(user),
            })
    except Exception:
        pass

    auth_login(request, user)
    _login_clear_failures(request)

    prof = getattr(user, "profile", None)
    if prof:
        display_name = prof.display_first_name
    else:
        display_name = (user.first_name or "").strip() or user.username
    config = PlatformConfig.load()

    from django.conf import settings
    from django.utils.http import url_has_allowed_host_and_scheme
    next_url = (request.POST.get("next") or "").strip()
    force_change = bool(prof and prof.must_change_password)
    if force_change:
        # A temporary/admin-issued password must be replaced before anything
        # else — even a deep link the user was originally headed to.
        from django.urls import reverse
        redirect_to = reverse("accounts:force_password_change")
    elif next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        redirect_to = next_url
    else:
        redirect_to = settings.LOGIN_REDIRECT_URL
        try:
            from django.urls import reverse
            if redirect_to and ":" in redirect_to and not redirect_to.startswith("/"):
                redirect_to = reverse(redirect_to)
        except Exception:
            redirect_to = "/"

    return JsonResponse({
        "ok": True,
        "display_name": display_name,
        "welcome_message": config.login_welcome_message,
        "redirect": redirect_to,
    })

class CaptureLoginView(LoginView):
    """Standard (non-AJAX) login fallback — the real login page uses
    login_check above instead, but this stays reachable directly.

    Adds a shared-cache brute-force throttle: after too many failures the login
    is blocked for a cool-down window without even checking the credentials.
    """

    template_name = "accounts/login.html"

    @method_decorator(sensitive_post_parameters())
    def post(self, request, *args, **kwargs):
        if _login_is_locked(request):
            form = self.get_form()
            return self.render_to_response(
                self.get_context_data(form=form, locked=True))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        _login_register_failure(self.request)
        if _login_is_locked(self.request):
            return self.render_to_response(
                self.get_context_data(form=form, locked=True))
        username = (self.request.POST.get("username", "") or "").strip()
        existing = User.objects.filter(username__iexact=username).first()
        login_error = "Invalid username or password."
        # As in login_check: tell the user their account is closed WITHOUT
        # requiring their submitted password to have been correct first —
        # that check used to let a wrong-password attempt still confirm
        # whether a guessed password was right, for a disabled account.
        if existing is not None and not existing.is_active:
            login_error = "This account has been closed by the administrator."
        return self.render_to_response(
            self.get_context_data(form=form, login_error=login_error))

    def form_valid(self, form):
        user = form.get_user()
        try:
            from people.work_shift import outside_shift_login_message, shift_status
            st = shift_status(user)
            if not st["allowed"]:
                return self.render_to_response(
                    self.get_context_data(
                        form=form,
                        login_error=outside_shift_login_message(user),
                    )
                )
        except Exception:
            pass
        response = super().form_valid(form)
        _login_clear_failures(self.request)
        return response

    def get_success_url(self):
        user = self.request.user
        profile = getattr(user, "profile", None)
        if profile is not None and profile.must_change_password:
            from django.urls import reverse
            return reverse("accounts:force_password_change")
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("locked", False)
        ctx.setdefault("login_error", "")
        if ctx.get("locked") and not ctx.get("login_error"):
            ctx["login_error"] = (
                "Too many attempts. Please wait a few minutes and try again."
            )
        config = PlatformConfig.load()
        ctx["welcome_message"] = config.login_welcome_message
        return ctx


@login_required
@admin_required
def user_toggle_active(request, pk):
    """Cut off or restore a user's access (an inactive user cannot sign in)."""
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        if user == request.user:
            messages.error(request, "You cannot disable your own account.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            if user.is_active:
                messages.success(request, f"Access restored for {user.get_full_name() or user.username}.")
            else:
                # Deactivating alone doesn't end a session already open in
                # someone's browser — without this, "cut off" only blocks the
                # *next* sign-in attempt, not access already in progress.
                _kill_sessions_for(user)
                messages.success(request, f"Access cut off for {user.get_full_name() or user.username}.")
    return redirect("accounts:user_list")


# Permanent deletion was removed in the 2026-07 security pass. CaseForm.signed_by
# and similar fields use SET_NULL, so hard-deleting a user silently unsigns
# every document they ever approved — indistinguishable, on the document,
# from it never having been signed at all. "Cut off" (user_toggle_active,
# above) is the supported way to end someone's access today: it blocks sign-in
# immediately, ends any open session immediately, and is fully reversible,
# without touching a single historical record. A fuller offboarding flow
# (reassigning their open work to a successor, marking them departed) belongs
# with the Personnel module and is intentionally not built here — see the
# changes summary document for why.


# ===========================================================================
# Backups (admin-only): list / download / upload / queue backup / queue restore
# ---------------------------------------------------------------------------
# Heavy lifting (pg_dump / psql / extracting volumes) is done by the separate
# `backup` service (postgres:16). The web app only manages files in the shared
# /backups folder and queues actions via a small control file the service polls.
# ===========================================================================
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
# Relative paths under BACKUP_DIR, e.g. db/ftdb_….tar.gz — no ".." / absolute.
_BACKUP_REL_RE = re.compile(
    r"^(?:"
    r"db/(?:ftdb_|ftbackup_)[A-Za-z0-9_\-]+\.tar\.gz|"
    r"media/(?:ftmedia_|ftbackup_)[A-Za-z0-9_\-]+\.tar\.gz|"
    r"code_db/ftcode_db\.tar\.gz|"
    r"(?:ftdb_|ftmedia_|ftbackup_)[A-Za-z0-9_\-]+\.tar\.gz|"
    r"ftcode_db\.tar\.gz"
    r")$"
)
# Note: ftdb_upload_* is covered by the ftdb_ prefix.
_BACKUP_KIND_ORDER = {"code_db": 0, "db": 1, "media": 2, "daily": 3}
_BACKUP_KIND_LABELS = {
    "db": "Database (cases, people, seats…)",
    "media": "Media (avatars, stamps, signatures)",
    "code_db": "Code tables (SQLite)",
    "daily": "Database + media (legacy)",
}
# Soft ceiling so a mistaken huge upload cannot fill the backup volume.
BACKUP_UPLOAD_MAX_BYTES = int(os.environ.get("BACKUP_UPLOAD_MAX_BYTES", str(4 * 1024 * 1024 * 1024)))


def _control_dir() -> str:
    return os.path.join(BACKUP_DIR, ".control")


def _backup_kind(rel: str) -> str:
    base = os.path.basename(rel)
    folder = rel.split("/", 1)[0] if "/" in rel else ""
    if base == "ftcode_db.tar.gz" or folder == "code_db":
        return "code_db"
    if folder == "media" or base.startswith("ftmedia_"):
        return "media"
    if folder == "db" or base.startswith("ftdb_"):
        return "db"
    # Older combined archives (database.sql + media).
    return "daily"


def _safe_backup_path(rel: str) -> str | None:
    """Return absolute path if rel is an allowed backup file under BACKUP_DIR."""
    if not rel or not _BACKUP_REL_RE.match(rel.replace("\\", "/")):
        return None
    rel = rel.replace("\\", "/")
    path = os.path.normpath(os.path.join(BACKUP_DIR, rel))
    root = os.path.normpath(BACKUP_DIR)
    if path != root and not path.startswith(root + os.sep):
        return None
    if not os.path.isfile(path):
        return None
    return path


def _validate_backup_archive(path: str) -> str | None:
    """Return an error message if ``path`` is not a usable FT backup, else None."""
    import tarfile
    try:
        with tarfile.open(path, "r:gz") as tf:
            names = {m.name.split("/")[-1] for m in tf.getmembers() if m.isfile()}
            # Also keep full member paths for nested layouts.
            full = {m.name for m in tf.getmembers() if m.isfile()}
    except Exception as exc:
        return "Not a valid .tar.gz archive (%s)." % exc
    markers = ("database.sql", "media.tar.gz", "code_db.tar.gz", "MANIFEST.txt")
    if any(m in names or any(p.endswith("/" + m) or p == m for p in full) for m in markers):
        return None
    if any(n.endswith(".sqlite3") for n in names):
        return None
    return (
        "This file does not look like an FT backup "
        "(expected database.sql, media.tar.gz, or code_db contents)."
    )


def _list_backups() -> list:
    items = []
    scan_specs = (
        ("db", ("ftdb_", "ftbackup_")),
        ("media", ("ftmedia_", "ftbackup_")),
        ("code_db", ("ftcode_db.tar.gz",)),
        ("", ("ftdb_", "ftmedia_", "ftbackup_", "ftcode_db.tar.gz")),
    )
    seen = set()
    for folder, prefixes in scan_specs:
        directory = os.path.join(BACKUP_DIR, folder) if folder else BACKUP_DIR
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if folder == "code_db":
                if name != "ftcode_db.tar.gz":
                    continue
            elif not any(
                name == p if p.endswith(".tar.gz") else name.startswith(p)
                for p in prefixes
            ):
                continue
            if not name.endswith(".tar.gz"):
                continue
            rel = f"{folder}/{name}" if folder else name
            rel = rel.replace("\\", "/")
            if rel in seen or not _BACKUP_REL_RE.match(rel):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            seen.add(rel)
            kind = _backup_kind(rel)
            items.append({
                "name": rel,
                "kind": kind,
                "kind_label": _BACKUP_KIND_LABELS.get(kind, kind),
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                "_raw": st.st_mtime,
            })
    items.sort(key=lambda x: (_BACKUP_KIND_ORDER.get(x["kind"], 9), -x["_raw"]))
    return items


def _read_status():
    try:
        with open(os.path.join(_control_dir(), "status.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_status(state: str, action: str = "", file: str = "", message: str = "") -> None:
    """Write a status.json the backup service / console can show immediately."""
    directory = _control_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        payload = {
            "state": state,
            "action": action,
            "file": file,
            "message": message,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = os.path.join(directory, "status.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(directory, "status.json"))
    except OSError:
        pass


def _queue_request(action: str, file: str = "") -> bool:
    directory = _control_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        # Compact JSON (no spaces) so the backup service's simple sed parser
        # matches "action":"backup" — spaced dumps were read as Unknown request
        # and never created a file.
        payload = json.dumps(
            {"action": action, "file": file or ""},
            separators=(",", ":"),
        )
        tmp = os.path.join(directory, "request.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, os.path.join(directory, "request.json"))
        return True
    except OSError:
        return False


@login_required
@admin_required
def backup_console(request):
    """Admin tab: create/upload/restore full backups."""
    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "backup_now":
            # Queue immediate database + media backups (code SQLite stays on schedule).
            if _queue_request("backup"):
                _write_status(
                    "running", "backup", "",
                    "Manual backup started — database + media…",
                )
                messages.success(
                    request,
                    "Backup started on this server: database (cases, people, seats…) "
                    "into backups/db/ and media into backups/media/. Wait a few seconds "
                    "and refresh — new files appear in Available backups below.",
                )
            else:
                messages.error(
                    request,
                    "Could not start the backup (backups folder not writable). "
                    "Check that the backup service is running and the backups "
                    "folder is mounted.",
                )

        elif action == "restore":
            name = (request.POST.get("name", "") or "").replace("\\", "/")
            if not _safe_backup_path(name):
                messages.error(request, "Invalid backup file selected.")
            elif _queue_request("restore", name):
                _write_status("running", "restore", name, "Restore in progress…")
                kind = _backup_kind(name)
                if kind == "code_db":
                    messages.success(
                        request,
                        "Code-tables restore queued. This replaces the SQLite code databases only "
                        "(pipe/fitting/…). Refresh in a few seconds to see the result.",
                    )
                elif kind == "media":
                    messages.success(
                        request,
                        "Media restore queued. This replaces uploaded files only "
                        "(avatars, stamps, signatures). Refresh in a few seconds to see the result.",
                    )
                elif kind == "db":
                    messages.success(
                        request,
                        "Database restore queued. This replaces PostgreSQL data "
                        "(cases, people, seats…). Media and code tables are left unchanged. "
                        "You may need to sign in again. Refresh in a few seconds to see the result.",
                    )
                else:
                    messages.success(
                        request,
                        "Restore queued. It replaces the current database and uploaded files "
                        "(code tables are left unchanged unless this is an older full backup). "
                        "You may need to sign in again. Refresh in a few seconds to see the result.",
                    )
            else:
                messages.error(request, "Could not queue the restore (backups folder not writable).")

        elif action == "upload":
            upload = request.FILES.get("backup_file")
            if upload is None:
                messages.error(request, "Please choose a .tar.gz backup file to upload.")
            elif not upload.name.endswith(".tar.gz"):
                messages.error(request, "The file must be a .tar.gz backup archive.")
            elif getattr(upload, "size", 0) and int(upload.size) > BACKUP_UPLOAD_MAX_BYTES:
                messages.error(
                    request,
                    "Backup file is too large (max %s GB)."
                    % (BACKUP_UPLOAD_MAX_BYTES // (1024 ** 3)),
                )
            else:
                # Uploads land under db/ so they appear in the list and can be restored.
                dest_rel = "db/ftdb_upload_%s.tar.gz" % time.strftime("%Y-%m-%d_%H%M%S")
                dest = os.path.join(BACKUP_DIR, dest_rel.replace("/", os.sep))
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    written = 0
                    with open(dest, "wb") as out:
                        for chunk in upload.chunks():
                            written += len(chunk)
                            if written > BACKUP_UPLOAD_MAX_BYTES:
                                raise ValueError("too large")
                            out.write(chunk)
                    bad = _validate_backup_archive(dest)
                    if bad:
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                        messages.error(request, bad)
                    else:
                        messages.success(
                            request,
                            "Uploaded as %s. You can restore it from the list below." % dest_rel,
                        )
                except ValueError:
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    messages.error(
                        request,
                        "Backup file is too large (max %s GB)."
                        % (BACKUP_UPLOAD_MAX_BYTES // (1024 ** 3)),
                    )
                except OSError:
                    messages.error(request, "Failed to save the uploaded file.")

        return redirect("accounts:backup_console")

    try:
        status = _read_status()
    except Exception:
        status = None
    # After a successful DB restore, drop pooled connections so the next
    # requests see the restored data (CONN_MAX_AGE otherwise keeps stale sockets).
    if (
        isinstance(status, dict)
        and status.get("state") == "success"
        and status.get("action") == "restore"
    ):
        restored = (status.get("file") or "").replace("\\", "/")
        if _backup_kind(restored) in ("db", "daily"):
            try:
                from django.db import connections
                connections.close_all()
            except Exception:
                pass

    try:
        backups = _list_backups()
    except Exception:
        backups = []
        messages.error(request, "Could not read the backups folder.")

    context = {
        "backups": backups,
        "status": status,
        "backup_dir": BACKUP_DIR,
    }
    return render(request, "accounts/backup_console.html", context)


@login_required
@admin_required
def backup_download(request, name):
    """Stream a backup archive to the admin for off-site safekeeping."""
    rel = (name or "").replace("\\", "/")
    path = _safe_backup_path(rel)
    if not path:
        raise Http404("not found")
    try:
        fh = open(path, "rb")
    except OSError:
        raise Http404("not found")
    return FileResponse(
        fh,
        as_attachment=True,
        filename=os.path.basename(path),
    )
