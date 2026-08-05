"""Helpers for multi-role navigation (one login, several PersonRole rows)."""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass

from django.db import IntegrityError, transaction

logger = logging.getLogger(__name__)

# Seat User for the active role in this request (secondary seats / Translate).
# ``cases.services.log`` reads this so Substitute tagging uses the seat being
# worked, not only the login User.
_bound_seat_user: ContextVar = ContextVar("ft_bound_seat_user", default=None)


def bind_work_seat(seat_user) -> None:
    """Remember the active seat for timeline actor snapshots in this request."""
    _bound_seat_user.set(seat_user)


def get_bound_work_seat():
    """Seat User bound by ``work_context`` / ``bind_work_seat``, or None."""
    return _bound_seat_user.get()


@dataclass
class WorkContext:
    """Resolved active role + the seat User that owns inbox/archive data."""

    login_user: object
    seat_user: object
    role: object | None
    is_substitute: bool = False
    origin_person: object | None = None
    panel_title: str = ""


def profile_matches_role(profile, role) -> bool:
    if bool(profile.is_admin) != bool(role.is_admin):
        return False
    if role.is_admin or role.is_general_manager:
        return bool(profile.is_general_manager) if role.is_general_manager else bool(profile.is_admin)
    return (
        (profile.unit or "") == (role.unit or "")
        and (profile.role or "") == (role.role or "")
        and (profile.supply_kind or "") == (role.supply_kind or "")
    )


def person_has_gm_role(person) -> bool:
    if person is None:
        return False
    try:
        return person.roles.filter(is_general_manager=True).exists()
    except Exception:
        return False


def user_has_gm_access(user) -> bool:
    """True when login profile is GM or the person holds a GM PersonRole seat."""
    profile = getattr(user, "profile", None)
    if profile is not None and profile.is_general_manager:
        return True
    link = getattr(user, "person_link", None)
    if link is None:
        return False
    return person_has_gm_role(link.person)


def open_substitute_tenure(source_user):
    """Open SUBSTITUTE tenure for a seat user, if any."""
    if source_user is None:
        return None
    from people.models import SeatTenure
    return (
        SeatTenure.objects.filter(
            source_user=source_user,
            kind=SeatTenure.KIND_SUBSTITUTE,
            ended_at__isnull=True,
        )
        .select_related("person", "origin_person")
        .first()
    )


def resolve_active_role(request, user):
    """PersonRole currently selected in session, or best match from profile.

    General Manager roles never become the accordion active role — they are
    shown via the dedicated General Management sidebar block instead.
    """
    profile = getattr(user, "profile", None)
    if profile is None or profile.is_admin:
        return None
    link = getattr(user, "person_link", None)
    if link is None:
        return None
    from people.seats import roles_of

    roles = [r for r in roles_of(link.person) if not r.is_general_manager]
    if not roles:
        return None
    active_id = request.session.get("active_role_id")
    active = None
    if active_id:
        try:
            active = next((r for r in roles if r.pk == int(active_id)), None)
        except (TypeError, ValueError):
            active = None
    if active is None:
        active = next((r for r in roles if profile_matches_role(profile, r)), None)
    if active is None:
        active = roles[0]
    request.session["active_role_id"] = active.pk
    return active


def work_context(request, user=None) -> WorkContext:
    """Login user + effective seat user for inbox/archive under the active role."""
    user = user or getattr(request, "user", None)
    role = resolve_active_role(request, user) if user is not None else None
    seat_user = user
    is_sub = False
    origin = None
    panel = ""
    if role is not None:
        if role.source_user_id:
            seat_user = role.source_user
        tenure = open_substitute_tenure(seat_user)
        if tenure is not None:
            is_sub = True
            origin = tenure.origin_person
            origin_name = ""
            if origin is not None:
                origin_name = (origin.display_name or "").strip()
            if not origin_name and tenure.origin_person_id:
                origin_name = "Owner"
            title = role.title_line or "Role"
            # e.g. "Ali (Commercial · Expert)"
            panel = f"{origin_name} ({title})" if origin_name else title
        else:
            panel = role.title_line or ""
    # Bind so case timeline logging can tag Substitute against the active seat.
    bind_work_seat(seat_user or user)
    return WorkContext(
        login_user=user,
        seat_user=seat_user or user,
        role=role,
        is_substitute=is_sub,
        origin_person=origin,
        panel_title=panel,
    )


def peek_inbox_count(user, role) -> int:
    """Inbox count as if ``role`` were active — without writing the profile."""
    try:
        from cases.services import inbox_count
        seat = role.source_user if getattr(role, "source_user_id", None) else user
        return int(inbox_count(user, role=role, work_user=seat) or 0)
    except Exception:
        return 0


def role_can_create_case(role, *, is_substitute: bool = False) -> bool:
    from accounts.constants import Role, Unit
    if is_substitute:
        return False
    if role.is_admin or role.is_general_manager:
        return False
    return role.unit == Unit.COMMERCIAL and role.role in {Role.MANAGER, Role.EXPERT}


def role_nav_icon(role) -> str:
    """Offline icon class for a PersonRole in the sidebar accordion."""
    if getattr(role, "is_general_manager", False):
        return "fa-user-tie"
    if getattr(role, "is_admin", False):
        return "fa-user-shield"
    unit = role.unit or ""
    r = role.role or ""
    if unit == "TECHNICAL":
        return "fa-screwdriver-wrench"
    if unit == "SUPPLY":
        return "fa-boxes-packing"
    if unit == "COMMERCIAL":
        if r == "MANAGER":
            return "fa-briefcase"
        if r == "SUPERVISOR":
            return "fa-clipboard-check"
        return "fa-tags"
    return "fa-id-badge"


def build_nav_roles(request, user):
    """List of non-GM role dicts for the sidebar accordion, or empty.

    GM seats are never listed here — they use the dedicated General Management
    block driven by ``user_has_gm_access``.
    """
    profile = getattr(user, "profile", None)
    if profile is None or profile.is_admin:
        return []
    link = getattr(user, "person_link", None)
    if link is None:
        return []
    from people.seats import apply_role_to_profile, roles_of

    person = link.person
    roles = [r for r in roles_of(person) if not r.is_general_manager]
    if not roles:
        return []

    active = resolve_active_role(request, user)
    if active is None:
        return []

    if not profile_matches_role(profile, active):
        try:
            apply_role_to_profile(user, active)
        except IntegrityError:
            logger.exception(
                "apply_role_to_profile IntegrityError user=%s role=%s",
                getattr(user, "pk", None), getattr(active, "pk", None),
            )

    out = []
    for role in roles:
        seat = role.source_user if role.source_user_id else user
        tenure = open_substitute_tenure(seat)
        is_sub = tenure is not None
        title = role.title_line
        if is_sub and tenure.origin_person_id:
            origin_name = (tenure.origin_person.display_name or "").strip() or "Owner"
            title = f"{origin_name} ({role.title_line})"
        out.append({
            "id": role.pk,
            "title": title,
            "icon": role_nav_icon(role),
            "unit": role.unit or "",
            "role": role.role or "",
            "is_active": role.pk == active.pk,
            "inbox_count": peek_inbox_count(user, role),
            "can_create_case": role_can_create_case(role, is_substitute=is_sub),
            "is_substitute": is_sub,
            "show_dashboard": role.role in {"MANAGER", "SUPERVISOR"} and not is_sub,
            "show_pricing": role.unit == "SUPPLY" and role.role == "MANAGER" and not is_sub,
            "show_coding": role.unit == "TECHNICAL" and role.role == "MANAGER" and not is_sub,
            "show_clients": role.unit == "COMMERCIAL" and role.role == "MANAGER" and not is_sub,
        })
    return out


def safe_activate_role(user, role) -> bool:
    """Apply role for session; never leave an aborted DB transaction."""
    from people.seats import apply_role_to_profile
    try:
        with transaction.atomic():
            apply_role_to_profile(user, role)
        return True
    except IntegrityError:
        logger.exception(
            "safe_activate_role IntegrityError user=%s role=%s",
            getattr(user, "pk", None), getattr(role, "pk", None),
        )
        return False
