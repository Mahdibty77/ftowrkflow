"""Helpers for multi-role navigation (one login, several PersonRole rows).

One human signs in once. If they hold more than one seat, each extra seat
contributes a ``PersonRole`` row and the sidebar offers a switcher between them.
This module answers the two questions that follow from that, and nothing else:

WHICH ROLE IS ACTIVE? ``resolve_active_role`` takes the row the session last
switched to (``active_role_id``); failing that, the row whose unit and role
match the login's ``Profile``, which always carries the *currently active*
one; failing that, the person's first role. ``work_context`` wraps that into a
``WorkContext``: the login user, the role, and — the part that matters —
``seat_user``, the seat whose inbox and archive are actually being worked. On a
secondary seat or a Translate substitution the login user and the seat user are
different objects, and using one where the other was meant is how this area
goes wrong. ``build_nav_roles`` renders the switcher; ``safe_activate_role``
performs the switch.

WHO IS ACTING? ``bind_work_seat`` parks the active seat in a ``ContextVar`` for
the duration of the request so ``cases.services.log`` can attribute a timeline
entry to the seat being worked rather than only to the login. A ``ContextVar``
rather than a module global because it is per-task; the resolved-role memo next
to it is parked on the *request* for the same isolation reason, spelled out at
its definition.

The seat model itself — seat vs person vs role — is explained in the
``people.models`` module docstring under "SEAT, PERSON, ROLE".
"""
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

# Attribute name under which ``resolve_active_role`` parks its answer on the
# request. Deliberately request-scoped rather than module-level state: a request
# object belongs to exactly one visitor and is discarded when the response is
# sent, so no seat can ever leak from one person's page render into another's.
_ACTIVE_ROLE_MEMO_ATTR = "_ft_active_role_memo"


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

    Rendering one page asks this question three times — the sidebar accordion,
    the work context, and the view itself — and each ask used to re-read the
    person's whole seat list. The answer is memoised on the ``request`` object,
    which Django creates and throws away per request, so nothing can survive
    into the next one. It is additionally keyed on the user's primary key: a
    call made for a different user than the one already resolved (impersonation
    swapping ``request.user`` mid-request) misses the memo and recomputes rather
    than being answered with somebody else's seat.
    """
    key = getattr(user, "pk", None)
    memo = getattr(request, _ACTIVE_ROLE_MEMO_ATTR, None)
    if isinstance(memo, tuple) and len(memo) == 2 and memo[0] == key:
        return memo[1]
    active = _resolve_active_role_uncached(request, user)
    try:
        setattr(request, _ACTIVE_ROLE_MEMO_ATTR, (key, active))
    except Exception:
        # The memo is an optimisation only; if the request object refuses the
        # attribute we simply resolve again, exactly as before.
        pass
    return active


def _resolve_active_role_uncached(request, user):
    """Do the real resolution work for :func:`resolve_active_role`."""
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
    # Only write when the session does not already hold this exact value. The
    # unconditional write marked the session dirty on every single render, which
    # forced a session row to be re-saved for nothing. Types are compared too, so
    # a leftover string pk left by an older release is still normalised to the
    # int the unconditional write used to store — the session ends up holding
    # exactly what it held before, just written far less often.
    stored = request.session.get("active_role_id")
    if type(stored) is not type(active.pk) or stored != active.pk:
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
