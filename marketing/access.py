"""Who may open the Marketing directory, and how much of it they get.

Three questions, answered together by :func:`access_for` because none of them
can be decided alone — a Marketing Expert and the platform's General Manager
can both "view", but mean completely different things by it:

    Unit.MARKETING + Supervisor        -> can_view, can_edit, scope "all"
    Unit.MARKETING + anything else     -> can_view, can_edit, scope "own"
    profile.is_general_manager         -> can_view, NOT can_edit, scope "all"
    platform admin                     -> can_view, NOT can_edit, scope "all"
    everyone else                      -> refused outright

"own" and "all" are not "some rows" and "more rows" of the same list — an
Expert's own registrations ARE their directory; a Supervisor or the GM sees
the union of every Expert's. See ``marketing/services.py`` for where that
split actually filters a query.

``viewer_context`` is the one place a viewer's (unit, role, is_general_manager,
is_admin) is resolved from the active seat. It used to be two separate
functions in ``marketing/views.py`` — a unit-only resolver for the page gate,
and nothing at all for role/GM — which is exactly the drift this module
exists to close.
"""
from __future__ import annotations

from dataclasses import dataclass

from accounts.constants import Role, Unit


@dataclass(frozen=True)
class Access:
    can_view: bool
    can_edit: bool
    scope: str  # "own" | "all"
    # The "read everything, no edits" tier — True for both the General
    # Manager and the platform admin. Beyond the plain can_view/scope grant
    # above, this flag ALSO unlocks chart-wide capabilities ordinary
    # Marketing Expert/Supervisor viewers never get: an all-cases search
    # behind the renamed "us" card, and a click-through from the Companies
    # tab straight into a filtered case archive (both built in other phases
    # of this same round). It exists as its own field, separate from
    # can_edit/scope, so those other-phase features can gate on exactly this
    # tier without re-deriving "GM or admin" from scratch.
    is_gm_or_admin: bool = False


def viewer_context(request):
    """(unit, role, is_general_manager, is_admin) for the seat this person is
    using now.

    Unit and role are resolved exactly the way ``cases.views.case_detail``
    resolves a seat's unit: active-seat-first via
    ``people.role_nav.work_context``, falling back to the login's own
    ``Profile`` when the seat layer has nothing to say (no ``PersonRole`` rows
    at all, which is most logins, or the seat layer failing outright). One
    human may hold several seats, and on a secondary seat the login's own
    profile still describes the PRIMARY one — reading the profile alone would
    miss a Commercial expert who also holds a Marketing seat and has switched
    to it.

    ``is_general_manager`` is read from the profile alone, never from the
    active-seat role: ``people.role_nav.resolve_active_role`` deliberately
    never lets a GM ``PersonRole`` become the session's active role (the
    dedicated General Management sidebar block covers it instead), so asking
    the seat for it would always answer False even for an actual GM.

    ``is_admin`` is different: unlike GM, an administrator CAN be an active
    seat role, so it is read from BOTH sources — this mirrors, field for
    field, how ``cases/views.py::case_detail`` computes its own
    ``is_admin_view``: ``role.is_admin or (profile and profile.is_admin)``
    when a seat role is active, or ``profile and profile.is_admin`` alone
    when it is not. Restricting this to profile-only, the way ``is_gm`` is
    restricted, would miss an admin who is currently on an active seat role
    that itself carries ``is_admin``.
    """
    profile = getattr(request.user, "profile", None)
    fallback_unit = (getattr(profile, "unit", "") or "").strip()
    fallback_role = (getattr(profile, "role", "") or "").strip()
    is_gm = bool(profile and profile.is_general_manager)
    is_admin = bool(profile and profile.is_admin)
    try:
        from people.role_nav import work_context
        role = work_context(request).role
    except Exception:
        # The seat layer is an enhancement over the profile, never a
        # precondition for it: if it cannot answer, the profile still can, and
        # a page must not 500 because a seat row is malformed.
        return fallback_unit, fallback_role, is_gm, is_admin
    if role is None:
        return fallback_unit, fallback_role, is_gm, is_admin
    unit = (getattr(role, "unit", "") or "").strip() or fallback_unit
    seat_role = (getattr(role, "role", "") or "").strip() or fallback_role
    is_admin = is_admin or bool(getattr(role, "is_admin", False))
    return unit, seat_role, is_gm, is_admin


def access_for(request) -> Access:
    """The view/edit/scope decision for this request. See the module docstring.

    ``is_gm`` and ``is_admin`` are checked FIRST, together, and unconditionally
    force view-only — a person can hold a General Manager seat, or carry the
    platform admin flag, alongside an ordinary Marketing seat (an admin can
    attach a GM seat, or grant the admin flag, to an existing login without
    clearing that login's other seats), and in that case ``viewer_context``
    still reports the active Marketing unit/role. Checking the Marketing
    branch first would let that one combination edit as if they were an
    ordinary Expert or Supervisor, silently reopening the write access neither
    the GM nor the admin is supposed to have. Both restrictions are a property
    of the ACCOUNT, not of which seat happens to be active — checked first,
    unconditionally view-only, for the exact same reason.
    """
    unit, role, is_gm, is_admin = viewer_context(request)
    if is_gm or is_admin:
        return Access(can_view=True, can_edit=False, scope="all", is_gm_or_admin=True)
    if unit == Unit.MARKETING:
        scope = "all" if role == Role.SUPERVISOR else "own"
        return Access(can_view=True, can_edit=True, scope=scope)
    return Access(can_view=False, can_edit=False, scope="own")
