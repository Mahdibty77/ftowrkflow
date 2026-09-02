"""Who may open the Marketing directory, and how much of it they get.

Three questions, answered together by :func:`access_for` because none of them
can be decided alone — a Marketing Expert and the platform's General Manager
can both "view", but mean completely different things by it:

    Unit.MARKETING + Supervisor        -> can_view, can_edit, scope "all",
                                          can_manage_config
    Unit.MARKETING + anything else     -> can_view, can_edit, scope "own"
    profile.is_general_manager         -> can_view, NOT can_edit, scope "all"
    platform admin                     -> can_view, NOT can_edit, scope "all",
                                          can_manage_config
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

CASES ARE A SECOND, SEPARATE QUESTION, answered by :func:`case_access_for`
further down. ``access_for`` decides who may open the Marketing SECTION and how
much of MARKETING'S OWN data (manual tags, contacts, connections) they get;
which CASES a Marketing viewer may see and click through to is not the same
decision and does not follow from ``scope`` — see that function's docstring for
the owner's rule and for why the two are kept apart.
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
    # behind the renamed "us" card, and a click-through straight into a
    # filtered case archive. It exists as its own field, separate from
    # can_edit/scope, so those other-phase features can gate on exactly this
    # tier without re-deriving "GM or admin" from scratch.
    is_gm_or_admin: bool = False
    # May this viewer ADMINISTER the unit's shared CONFIGURATION vocabularies —
    # today exactly one, ``marketing/models.py::ContactRole``, the managed list
    # of job titles a company contact can hold.
    #
    # A SEPARATE CAPABILITY FROM ``can_edit``, DELIBERATELY, and the distinction
    # is the whole reason this field exists rather than the platform admin being
    # folded into ``can_edit``. ``can_edit`` governs the unit's own WORKING DATA
    # — manual labels, connections, contacts — and the admin and the General
    # Manager are view-only over all of it, unconditionally (see
    # :func:`access_for`). A configuration vocabulary is not working data: it is
    # a settings list the working data merely points at, nobody's private
    # opinion about a company, and the owner named exactly two populations who
    # may extend it — a Marketing Supervisor and the platform admin. Expressing
    # that as its own narrowly-named flag lets the admin administer the list
    # without gaining a single write on anything ``can_edit`` protects.
    #
    # THE GENERAL MANAGER IS DELIBERATELY EXCLUDED even though they share the
    # admin's ``is_gm_or_admin`` tier: the owner's rule names the Supervisor and
    # the admin, and the GM is a reader of this section, not an administrator of
    # it. This is the one place the two halves of that tier are not the same
    # answer, which is precisely why it is not derived from ``is_gm_or_admin``.
    can_manage_config: bool = False


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

    ``can_manage_config`` is the ONE answer that splits that first branch in
    two: the platform admin gets it, the General Manager does not, and a
    Marketing Supervisor gets it from the second branch. See the field's own
    comment on :class:`Access` for why administering a configuration vocabulary
    is not the same grant as ``can_edit`` and must not be expressed by widening
    it. Note the admin keeps ``can_edit=False`` here — nothing about this flag
    reopens a single write on labels, connections or contacts.
    """
    unit, role, is_gm, is_admin = viewer_context(request)
    if is_gm or is_admin:
        return Access(can_view=True, can_edit=False, scope="all",
                      is_gm_or_admin=True, can_manage_config=is_admin)
    if unit == Unit.MARKETING:
        scope = "all" if role == Role.SUPERVISOR else "own"
        return Access(can_view=True, can_edit=True, scope=scope,
                      can_manage_config=(scope == "all"))
    return Access(can_view=False, can_edit=False, scope="own")


# --------------------------------------------------------------------------- #
# The seat list, and the two questions that need it
# --------------------------------------------------------------------------- #
# Everything above this line reads the ACTIVE seat. Everything below deliberately
# reads the person's WHOLE seat list instead, because both questions it answers
# are about a seat the viewer is NOT sitting in at that moment:
#
#   * "may this person reach the Marketing section at all?" is asked from the
#     cases app, where a dual-seat person is by definition on their Commercial
#     seat; and
#   * "which cases are theirs?" is asked from the Marketing section, where the
#     same person is by definition on their Marketing seat.
#
# Asking the active seat would answer "no" and "none" respectively — the exact
# two failures this round exists to fix.
def _person_roles(request) -> list:
    """Every ``PersonRole`` the signed-in person holds, or ``[]``.

    Goes through ``people.role_nav.roles_for_person`` rather than
    ``people.seats.roles_of`` directly so the per-request memo that function
    keeps is shared with the sidebar accordion, which has already read the very
    same list by the time any page calls this.

    Swallows its own errors for the same reason ``viewer_context`` does: the
    seat layer is an enhancement over the login profile, never a precondition
    for it, and a malformed seat row must not take a page down.
    """
    user = getattr(request, "user", None)
    link = getattr(user, "person_link", None)
    if link is None:
        return []
    try:
        from people.role_nav import roles_for_person
        return list(roles_for_person(link.person, request=request))
    except Exception:
        return []


def marketing_seat_role(request):
    """The person's Marketing ``PersonRole``, or None — active seat or not.

    This is the "holds a Marketing seat" test the cases app needs for its two
    "go to the marketing chart" buttons. It must NOT be answered from the active
    seat (``viewer_context``), because the whole point is that the person is
    sitting in their Commercial seat at that moment; a Marketing seat they hold
    but have not switched to still counts.

    Admin / General Manager are deliberately NOT covered here — they hold no
    Marketing ``PersonRole`` and do not need one; :func:`can_reach_marketing`
    below adds them via ``access_for``.
    """
    for role in _person_roles(request):
        if role.is_admin or role.is_general_manager:
            continue
        if (getattr(role, "unit", "") or "").strip() == Unit.MARKETING:
            return role
    return None


def can_reach_marketing(request) -> bool:
    """May this viewer open the Marketing section — now, or after a seat switch?

    True for exactly two populations, and the union is what the cases app gates
    its "Marketing chart" / "View in marketing chart" buttons on:

    * anyone ``access_for`` already lets in from where they are sitting (the
      admin/GM tier, and a viewer already on their Marketing seat); plus
    * a person who HOLDS a Marketing seat without currently sitting in it —
      a Marketing expert who also holds a Commercial seat, working a case.

    Showing the button is not the grant: ``marketing/views.py::home`` re-runs
    ``access_for`` on arrival for everybody, so a widened button cannot become a
    way in for someone this function would answer False for.
    """
    if access_for(request).can_view:
        return True
    return marketing_seat_role(request) is not None


@dataclass(frozen=True)
class CaseAccess:
    """Which CASES a Marketing viewer may see listed, and open.

    ``can_open`` — may this viewer see case rows / follow one to its detail page
    at all. ``all_cases`` — True only for the admin/GM tier. ``own_user_ids`` —
    the User primary keys that "their own commercial cases" is measured against
    (see :func:`case_access_for`); empty when ``all_cases`` is True, because
    there is then nothing to measure. ``seat_role_id`` — the Commercial
    ``PersonRole`` that has to be made active before a case detail page will
    open, or None when the viewer can open one from where they already sit.
    """

    can_open: bool
    all_cases: bool
    own_user_ids: tuple = ()
    seat_role_id: int | None = None


def _own_commercial_seat_users(request, roles) -> tuple:
    """User pks whose ``Case.created_by`` counts as THIS viewer's own.

    MIRRORS, rather than calls, ``cases/services.py::archive_scope``. That
    function is the one definition of what a Commercial viewer owns —
    ``qs.filter(created_by=seat_user)``, the seat user resolved through
    ``people.role_nav.work_context`` — and this reproduces exactly that filter.
    It cannot be reused directly for two independent reasons:

    * it returns None outright for a Marketing unit ("a unit outside the TO/PI
      workflow has no case history to scope"), which is precisely the seat a
      dual-seat person is sitting in when they look at the chart; and
    * it keys every branch on the ACTIVE seat, so even if it did answer, it
      would answer for the Marketing seat — which has created no cases — rather
      than for the Commercial one whose cases the owner is asking about.

    So the ownership TEST is copied and the seat it is applied to is widened
    from "the active seat" to "every Commercial seat this person holds". The
    Commercial MANAGER's "Only my cases" toggle is not carried over: that is a
    control on the archive page, not a second definition of ownership, and the
    owner's rule here says only the admin and the General Manager ever see
    everyone's cases.
    """
    user = getattr(request, "user", None)
    login_pk = getattr(user, "pk", None)
    ids = set()
    for role in roles:
        if role.is_admin or role.is_general_manager:
            continue
        if (getattr(role, "unit", "") or "").strip() != Unit.COMMERCIAL:
            continue
        # A role's own seat User is what ``created_by`` was stamped with; a role
        # with no source seat can only be the login's own.
        ids.add(getattr(role, "source_user_id", None) or login_pk)
    if not roles:
        # No PersonRole rows at all — most logins. The login profile then
        # describes the only seat there is, so it is its own seat user (this is
        # the same fallback ``viewer_context`` makes for unit/role).
        profile = getattr(user, "profile", None)
        if (getattr(profile, "unit", "") or "").strip() == Unit.COMMERCIAL:
            ids.add(login_pk)
    # A translated / substituted Commercial seat is worked through
    # ``work_context``'s ``seat_user`` and not through ``role.source_user``, so
    # ask it too when the seat being worked right now IS the Commercial one.
    try:
        from people.role_nav import work_context
        ctx = work_context(request)
        if ctx.role is not None and (ctx.role.unit or "").strip() == Unit.COMMERCIAL:
            ids.add(getattr(ctx.seat_user, "pk", None))
    except Exception:
        pass
    ids.discard(None)
    return tuple(sorted(ids))


def case_access_for(request, access: Access | None = None) -> CaseAccess:
    """May this viewer see case detail, and if so which cases? The owner's rule:

        A Marketing expert who ALSO holds a Commercial seat may move freely
        between the chart and case details in both directions — but wherever
        the marketing side lists CASES, that person sees only the cases that
        are theirs on the commercial side, never everyone's; the platform admin
        and the General Manager see all cases.

    Resolved to three answers, and this is the ONLY place they are decided:

        admin / General Manager   -> can_open, all_cases
        holds a Commercial seat   -> can_open, only cases created on that seat
        Marketing seat only       -> no case rows at all

    THE THIRD LINE IS NOT A GAP. A viewer with no Commercial seat has created no
    cases, so "their own commercial cases" is the empty set, and the honest
    rendering of an empty set is an empty list — not everyone's cases. It is
    also exactly what the placeholder this function replaces
    (``marketing/views.py::_visible_case_rows``) already did on the company
    detail page; what changes is that the chart's own panels and the all-cases
    search now obey the same rule instead of showing every case in the system to
    every Marketing viewer.

    WHICH USER "OWN" IS MEASURED AGAINST is the subtle half, and it is not the
    login. One person's Marketing and Commercial seats are two different ``User``
    rows (``people.seats.assign_seat`` hardens every seat after the first into
    its own account), and a case's ``created_by`` is the COMMERCIAL seat's User.
    Read from the login — or from ``work_context``'s active ``seat_user``, which
    on the chart is the MARKETING seat — the answer would be "no cases at all"
    for exactly the person this rule exists for. See
    :func:`_own_commercial_seat_users`.

    ``access`` may be passed in by a caller that already computed it (every view
    in this app has); it is only read for ``is_gm_or_admin`` / ``can_view``.

    Scoping only decides WHICH cases are listed. Whether the case detail page
    then opens is still ``cases/services.py::user_can_view_case``'s own call,
    made independently on that page — see ``case_open_url`` for why a
    dual-seat viewer's link goes through a seat switch rather than straight at
    the case.
    """
    access = access or access_for(request)
    if not access.can_view:
        return CaseAccess(can_open=False, all_cases=False)
    if access.is_gm_or_admin:
        return CaseAccess(can_open=True, all_cases=True)
    roles = _person_roles(request)
    own = _own_commercial_seat_users(request, roles)
    if not own:
        return CaseAccess(can_open=False, all_cases=False)
    seat_role = next(
        (r for r in roles
         if not r.is_admin and not r.is_general_manager
         and (getattr(r, "unit", "") or "").strip() == Unit.COMMERCIAL),
        None,
    )
    return CaseAccess(
        can_open=True, all_cases=False, own_user_ids=own,
        seat_role_id=getattr(seat_role, "pk", None),
    )


def scope_case_rows(rows, case_access: CaseAccess) -> list:
    """``rows`` (dicts carrying ``case_id``) narrowed to what this viewer may see.

    One query, whatever the row count: the ids on the rows are asked for in a
    single ``created_by__in`` lookup rather than each row being re-fetched. The
    rows themselves are returned untouched and in their original order — this
    only decides which of them survive, so every caller keeps whatever extra
    keys its own producer put on them.
    """
    if not case_access.can_open:
        return []
    rows = list(rows)
    if case_access.all_cases:
        return rows
    ids = [row.get("case_id") for row in rows if row.get("case_id") is not None]
    if not ids or not case_access.own_user_ids:
        return []
    from cases.models import Case
    allowed = set(
        Case.objects.filter(
            pk__in=ids, created_by_id__in=case_access.own_user_ids,
        ).values_list("pk", flat=True)
    )
    return [row for row in rows if row.get("case_id") in allowed]


def case_open_url(case_access: CaseAccess, case_id) -> str:
    """Where a case row should actually point, for THIS viewer.

    For the admin/GM tier that is the case detail page itself. For a dual-seat
    Marketing/Commercial person it is NOT: they are sitting in their Marketing
    seat, and ``cases/services.py::user_can_view_case`` resolves "did you
    participate in this case" against the seat being worked — which on the chart
    is the Marketing seat, which created nothing. Sent straight at the case they
    would be bounced to their inbox with "You do not have access to this case",
    which is precisely the permission wall a case row must never lead to.

    So their link goes through ``people:activate_role`` with ``?next=`` — the
    app's own way of reaching a destination that belongs to another seat, the
    same one the sidebar accordion uses for every entry under an inactive role
    (see ``core/templates/base.html``). The seat switch is the thing that makes
    the case openable, and it is safe by construction: ``activate_role`` only
    accepts a ``PersonRole`` belonging to the signed-in person, and the case page
    still runs its own check afterwards.
    """
    from django.urls import reverse
    from django.utils.http import urlencode

    target = reverse("cases:case_detail", args=[case_id])
    if not case_access.seat_role_id:
        return target
    return "%s?%s" % (
        reverse("people:activate_role", args=[case_access.seat_role_id]),
        urlencode({"next": target}),
    )
