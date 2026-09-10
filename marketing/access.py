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
    at all. ``all_cases`` — True for the admin/GM tier AND for a person holding
    a Commercial MANAGER seat (see :func:`case_access_for`). ``own_user_ids`` —
    the User primary keys that "their own commercial cases" is measured against
    (see :func:`case_access_for`); empty when ``all_cases`` is True, because
    there is then nothing to measure. ``seat_role_id`` — the Commercial
    ``PersonRole`` that has to be made active before a case detail page will
    open, or None when the viewer can open one from where they already sit.

    ``substitute_user_ids`` — the subset of ``own_user_ids`` whose Commercial
    seat is currently being worked as a SUBSTITUTE (an open
    ``people.models.SeatTenure`` of kind SUBSTITUTE on that seat User). Cases
    created on one of those seats are shown ONLY while they are still running:
    a terminal one (``cases.constants.CaseStatus.ENDED``) is withheld, which is
    verbatim what ``cases/services.py::archive_scope`` does for the same seat
    (``if ctx.is_substitute: qs = qs.exclude(status__in=CaseStatus.ENDED)``).
    A tuple rather than a single boolean because one person can hold SEVERAL
    Commercial seats and ``archive_scope`` only ever reasons about the one seat
    it is active on: a boolean would either hide a non-substituted seat's
    finished cases or show a substituted seat's, and both would be a different
    answer from the archive's. Empty for every other viewer — the admin/GM tier
    and a Commercial MANAGER never reach it (see :func:`case_access_for`), so
    nothing about it can narrow a seat the archive does not narrow.

    ``show_money`` — may this viewer be shown PI money figures over those cases.
    MONEY FOLLOWS THE CASES: it is decided from the same two facts the case
    rows themselves are decided from (the admin/GM tier, or holding a real
    Commercial seat), so on this side of the platform it answers True for
    exactly the viewers ``can_open`` answers True for and False for everyone
    else — see :func:`_case_money_visible` for why that is the defensible rule
    and what it deliberately changes. It stays its own field rather than being
    folded into ``can_open`` because it is a different question about the same
    rows ("may you see these cases" is not "may you see what they are worth"),
    and the company page gates a different piece of markup on each.
    """

    can_open: bool
    all_cases: bool
    own_user_ids: tuple = ()
    substitute_user_ids: tuple = ()
    seat_role_id: int | None = None
    show_money: bool = False


def _commercial_seat_roles(roles):
    """The person's real Commercial ``PersonRole`` rows, in seat order.

    "Real" excludes the admin and General Manager rows, which carry no unit of
    their own and are answered a whole branch earlier in
    :func:`case_access_for`. Factored out because both questions this module
    asks of a Commercial seat — which seat USER owns its cases, and whether any
    of those seats is a MANAGER — have to walk the identical list, and two
    copies of that filter would be two chances to disagree about which rows
    count.
    """
    out = []
    for role in roles:
        if role.is_admin or role.is_general_manager:
            continue
        if (getattr(role, "unit", "") or "").strip() != Unit.COMMERCIAL:
            continue
        out.append(role)
    return out


def _own_commercial_seat_users(request, roles) -> tuple:
    """User pks whose ``Case.created_by`` counts as THIS viewer's own.

    MIRRORS, rather than calls, the EXPERT branch of
    ``cases/services.py::archive_scope``. That function is the one definition of
    what a Commercial viewer owns — for an expert, ``qs.filter(created_by=
    seat_user)`` with the seat user resolved through
    ``people.role_nav.work_context`` — and this reproduces exactly that filter.
    It cannot be reused directly for two independent reasons:

    * it returns None outright for a Marketing unit ("a unit outside the TO/PI
      workflow has no case history to scope"), which is precisely the seat a
      dual-seat person is sitting in when they look at the chart; and
    * it keys every branch on the ACTIVE seat, so even if it did answer, it
      would answer for the Marketing seat — which has created no cases — rather
      than for the Commercial one whose cases the owner is asking about.

    So the ownership TEST is copied and the seat it is applied to is widened
    from "the active seat" to "every Commercial seat this person holds".

    THIS FUNCTION ANSWERS ONLY THE EXPERT HALF OF ``archive_scope``'s table,
    and that is the whole of its job. It used to be the whole of the RULE too,
    on a docstring that claimed "the Commercial MANAGER's 'Only my cases'
    toggle is not carried over ... only the admin and the General Manager ever
    see everyone's cases". That inference was wrong, and the owner corrected it:
    ``archive_scope``'s own table reads "commercial manager (off) -> the entire
    archive (incl. experts' cases)", with the toggle merely NARROWING a manager
    who asks for it. A Commercial MANAGER therefore gets the whole archive here
    too — decided by :func:`_holds_commercial_manager_seat` in
    :func:`case_access_for`, which never consults this function's result for
    that answer. Nothing about the widening reaches a seat this function was
    already returning nothing for: a Marketing-only seat still holds no
    Commercial ``PersonRole`` at all, so it is still the empty tuple, and
    ``case_access_for`` still refuses it every case row.
    """
    user = getattr(request, "user", None)
    login_pk = getattr(user, "pk", None)
    ids = set()
    for role in _commercial_seat_roles(roles):
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


def _substituted_seat_users(request, user_ids) -> tuple:
    """Which of ``user_ids`` are seats currently held by a SUBSTITUTE.

    MIRRORS THE ONE CLAUSE OF ``cases/services.py::archive_scope`` THAT THIS
    MODULE OTHERWISE MISSED. That function's Commercial-expert branch is two
    statements, not one::

        elif profile_unit == Unit.COMMERCIAL:
            qs = qs.filter(created_by=seat_user)
            # Substitutes do not see fully closed / terminal archive rows.
            if ctx.is_substitute:
                qs = qs.exclude(status__in=CaseStatus.ENDED)

    :func:`_own_commercial_seat_users` reproduces the first statement; this
    reproduces the second. Without it the marketing side listed a substitute's
    finished cases while the archive, one click away, did not — two screens in
    one platform contradicting each other about the same viewer, which is the
    exact failure :func:`case_access_for`'s "exactly what the CASE ARCHIVE would
    show them, and nothing more" promise exists to prevent.

    ``ctx.is_substitute`` IS A FACT ABOUT THE SEAT, NOT ABOUT THE SESSION —
    ``people.role_nav._work_context_uncached`` sets it from
    ``open_substitute_tenure(seat_user)``, i.e. an open SUBSTITUTE
    ``people.models.SeatTenure`` on the seat User itself — which is the only
    reason the same question can be asked from here at all. A Marketing viewer
    is by definition sitting in their MARKETING seat (see this half of the
    module's section header), so ``work_context`` would report the substitution
    state of the wrong seat entirely; the tenure is therefore looked up per
    COMMERCIAL seat user, exactly the seats whose cases ``own_user_ids`` admits.

    ``open_substitute_tenure`` is CALLED, not copied — unlike ``archive_scope``
    itself, which cannot be reached from a Marketing seat at all (it returns
    ``None`` outright for this unit), that helper is seat-keyed, request-cached
    and perfectly happy to answer about a seat nobody is sitting in. Passing
    ``request`` shares the per-request tenure memo the sidebar accordion has
    usually already filled.

    FAILS CLOSED, like every other seat-layer read in this module: if the seat
    layer cannot answer, every contributing seat is treated as substituted, so
    the viewer gets the NARROWER list (running cases only) rather than silently
    being handed terminal rows the archive might be withholding.
    """
    ids = tuple(user_ids or ())
    if not ids:
        return ()
    try:
        from people.role_nav import open_substitute_tenure
        from django.contrib.auth.models import User
        users = {u.pk: u for u in User.objects.filter(pk__in=ids)}
        return tuple(
            pk for pk in ids
            if open_substitute_tenure(users.get(pk), request=request) is not None
        )
    except Exception:
        return ids


def _holds_commercial_manager_seat(request, roles) -> bool:
    """Does this person hold a Commercial MANAGER seat — active or not?

    THE FLAG THAT DECIDES IT IS ``role.role == accounts.constants.Role.MANAGER``
    on a ``people.models.PersonRole`` whose ``unit`` is
    ``Unit.COMMERCIAL`` — the same pair ``cases/services.py::archive_scope``
    tests (``profile_unit == Unit.COMMERCIAL and profile_role == Role.MANAGER``)
    for the branch of its table that reads "commercial manager (off) -> the
    entire archive (incl. experts' cases)". It is NOT ``PersonRole.is_admin``
    and NOT ``is_general_manager``: those two are their own seat kind, carry no
    unit, and are already answered before this function is ever reached.

    Read from the WHOLE seat list rather than the active seat, for exactly the
    reason this half of the module exists (see the section header above): the
    person asking is by definition sitting in their MARKETING seat while looking
    at the chart, so the active seat would answer "not a manager" for precisely
    the person the rule is about — which is the bug the owner hit.

    The same three sources :func:`_own_commercial_seat_users` reads, in the same
    order and for the same reasons, so the two answers cannot be drawn from
    different pictures of the same person:

    * the person's ``PersonRole`` rows;
    * the login ``Profile`` alone, when there are no ``PersonRole`` rows at all
      (most logins — the profile then describes the only seat there is); and
    * the ACTIVE seat via ``work_context``, which is how a translated /
      substituted Commercial seat presents itself, since such a seat is worked
      through ``ctx.role`` rather than through a row of this person's own.

    Nothing here can widen a non-Commercial viewer: every branch requires the
    unit to be Commercial before the role is even looked at, so a Marketing
    Supervisor (``Role.SUPERVISOR`` under ``Unit.MARKETING``) and a Marketing
    seat holding ``Role.MANAGER`` under ``Unit.MARKETING`` both answer False.
    """
    for role in _commercial_seat_roles(roles):
        if (getattr(role, "role", "") or "").strip() == Role.MANAGER:
            return True
    if not roles:
        profile = getattr(getattr(request, "user", None), "profile", None)
        if ((getattr(profile, "unit", "") or "").strip() == Unit.COMMERCIAL
                and (getattr(profile, "role", "") or "").strip() == Role.MANAGER):
            return True
    try:
        from people.role_nav import work_context
        ctx = work_context(request)
        role = ctx.role
        if (role is not None and not role.is_admin and not role.is_general_manager
                and (role.unit or "").strip() == Unit.COMMERCIAL
                and (role.role or "").strip() == Role.MANAGER):
            return True
    except Exception:
        # Same swallow, same reason, as everywhere else the seat layer is read
        # in this module: it is an enhancement over the login profile, never a
        # precondition for it, and a malformed seat row must not take a page
        # down. Failing here fails CLOSED — the viewer keeps the narrower
        # created_by scoping rather than silently gaining the whole archive.
        pass
    return False


def _commercial_seat_role(roles):
    """The Commercial ``PersonRole`` a case link should switch into, or None.

    MANAGER FIRST, then whatever comes next. The seat this returns is the one
    ``case_open_url`` sends the viewer through before the case detail page
    opens, and that page runs its OWN check afterwards
    (``cases/services.py::user_can_view_case``), which asks the ACTIVE seat's
    unit/role. A person who holds both a Commercial manager seat and a
    Commercial expert seat is being listed the manager's whole archive here, so
    landing them on the expert seat would offer rows that then refuse to open —
    the link has to arrive under the authority the list was built with.
    """
    commercial = _commercial_seat_roles(roles)
    for role in commercial:
        if (getattr(role, "role", "") or "").strip() == Role.MANAGER:
            return role
    return commercial[0] if commercial else None


def _case_money_visible(access: Access, own_user_ids) -> bool:
    """MONEY FOLLOWS THE CASES. A DECISION TAKEN HERE, and here is the argument.

    THE RULE: if this viewer may see a case at all on the Marketing side, they
    may see that case's money. So this is decided from EXACTLY the two facts
    :func:`case_access_for` decides the case rows themselves from — the
    admin/GM tier (``access.is_gm_or_admin``), or holding at least one real
    Commercial seat (``own_user_ids``, from
    :func:`_own_commercial_seat_users`) — and from nothing else.

    THIS IS A DELIBERATE CHANGE, NOT A RESTORED COPY. This function used to
    reproduce ``cases/services.py::archive_scope``'s own one-line expression
    verbatim::

        show_money = bool(
            profile.is_admin or profile.is_general_manager
            or profile.unit == Unit.COMMERCIAL
        )

    read — as over there — from the LOGIN PROFILE. Its docstring argued at
    length that the consequence (a Marketing login sees no money) was "the
    existing rule, not a decision taken here". That was true of the expression
    and false of the outcome: the archive reads ``profile.unit`` while sitting
    in the seat that unit describes, and this app reads it while the viewer is
    sitting in their MARKETING seat, so the same words mean a different thing
    here. The result was that the company detail page's PI total rendered for
    nobody who actually uses that page — the owner asked for the figure twice
    and never saw it — because a Marketing seat's login profile says MARKETING
    even when the same person holds the Commercial seat whose cases the page is
    listing. Answering "the archive said so" to that is quoting a rule that was
    never asked this question. So the rule for this app is decided here, and
    this docstring says so plainly.

    WHY THIS IS THE DEFENSIBLE ANSWER RATHER THAN SIMPLY DELETING THE GATE.
    The seat that puts a case row on this page is the same seat
    :func:`case_open_url` switches the viewer into one click later — and on the
    other side of that switch the case archive shows them that case's money
    already, under ``archive_scope``'s own rule, because they are then sitting
    in the Commercial seat it reads. Withholding the total here hides a number
    the very next click reveals; showing it to someone with no such seat would
    be a real widening. Tying money to case visibility is what keeps the two
    screens saying the same thing about the same viewer, which is the promise
    ``case_access_for`` is built on.

    THE CONSEQUENCES, CHECKED RATHER THAN ASSUMED:

    * A MARKETING-ONLY SEAT STILL SEES NOTHING. It holds no Commercial
      ``PersonRole``, so ``own_user_ids`` is the empty tuple, so
      :func:`case_access_for` already refuses it every case row
      (``can_open=False``) — and this function answers False for the same
      reason, off the same value. Nothing is exposed to them that was not
      before: not the figure, and not the rows it would have been computed
      over.
    * A DUAL-SEAT PERSON'S TOTAL COVERS EXACTLY THE CASES THEY ARE ALREADY
      SHOWN, because the caller computes it over exactly the scoped rows (see
      ``marketing/views.py::_pi_money_total``) and this function does not touch
      which rows those are.
    * ONE POPULATION IS NARROWED, AND VISIBLY NOTHING CHANGES FOR THEM: a login
      whose PROFILE unit is Commercial but who holds no Commercial seat and is
      not admin/GM (their ``PersonRole`` rows are all Marketing) used to answer
      True here. They also get no case rows at all, and
      ``_pi_money_total`` returns "" for an empty row set before it looks at
      this flag, so the figure was already blank for them and still is.

    Takes the values rather than the request so it cannot be computed from a
    different picture of the person than the branches beside it — see
    :func:`case_access_for`, which passes what it has already resolved.
    """
    return bool(access.is_gm_or_admin or own_user_ids)


def case_access_for(request, access: Access | None = None) -> CaseAccess:
    """May this viewer see case detail, and if so which cases? The owner's rule:

        A Marketing expert who ALSO holds a Commercial seat may move freely
        between the chart and case details in both directions — but wherever
        the marketing side lists CASES, that person sees exactly what the CASE
        ARCHIVE would show them on that Commercial seat, and nothing more; the
        platform admin and the General Manager see all cases.

    "Exactly what the case archive would show them" is not a paraphrase — it is
    the deliberate design of this function, and ``cases/services.py::
    archive_scope`` is the authority it defers to. That function's own docstring
    table is the rule, quoted here in the two lines that reach a Commercial seat
    looking at the marketing side:

        commercial manager (off)    -> the entire archive (incl. experts' cases)
        commercial expert           -> only cases they personally created

    THAT SECOND LINE HAS A RIDER IN ``archive_scope``'S BODY that its own table
    does not spell out, and this function reproduces it too: when the Commercial
    seat is being worked as a SUBSTITUTE, terminal cases are excluded on top of
    the ``created_by`` filter ("Substitutes do not see fully closed / terminal
    archive rows"). It was the one clause of that branch the mirror here did not
    carry, so a substitute saw finished cases on the marketing side that the
    archive hid — see :func:`_substituted_seat_users`, which is where the
    exclusion is decided, and ``CaseAccess.substitute_user_ids``, which carries
    it to :func:`scope_case_rows`. It touches nothing else: a non-substituted
    seat contributes no id, and neither the manager line above nor the admin/GM
    line ever reaches it.

    Resolved to four answers, and this is the ONLY place they are decided:

        admin / General Manager    -> can_open, all_cases
        Commercial MANAGER seat    -> can_open, all_cases
        other Commercial seat      -> can_open, only cases created on that seat
        Marketing seat only        -> no case rows at all

    THE SECOND LINE IS THIS ROUND'S CORRECTION. It used to be absent, and a
    person holding both a Marketing seat and a Commercial MANAGER seat was given
    the EXPERT's ``created_by`` scoping — so the chart and the company detail
    page showed them the two cases they had personally created while the case
    archive, one click away, correctly showed them every case in the unit. Two
    screens in one platform contradicting each other about the same viewer is
    the bug; ``archive_scope`` is the tie-breaker, and its table says the
    manager sees everything. The archive's "Only my cases" toggle is what
    NARROWS a manager who asks for it — a control on that page, not a second
    definition of what a manager may see — so it has no counterpart here, and
    the manager's default (toggle off) is the answer this function gives.

    THE FOURTH LINE IS NOT A GAP. A viewer with no Commercial seat has created no
    cases, so "their own commercial cases" is the empty set, and the honest
    rendering of an empty set is an empty list — not everyone's cases. It is
    also exactly what the placeholder this function replaces
    (``marketing/views.py::_visible_case_rows``) already did on the company
    detail page; what changes is that the chart's own panels and the all-cases
    search now obey the same rule instead of showing every case in the system to
    every Marketing viewer. NOTHING IN THIS ROUND'S WIDENING TOUCHES IT: a
    Marketing-only seat holds no Commercial ``PersonRole``, so it is neither a
    manager (:func:`_holds_commercial_manager_seat`) nor an owner of any seat
    user (:func:`_own_commercial_seat_users`), and it still gets nothing.

    WHICH USER "OWN" IS MEASURED AGAINST is the subtle half, and it is not the
    login. One person's Marketing and Commercial seats are two different ``User``
    rows (``people.seats.assign_seat`` hardens every seat after the first into
    its own account), and a case's ``created_by`` is the COMMERCIAL seat's User.
    Read from the login — or from ``work_context``'s active ``seat_user``, which
    on the chart is the MARKETING seat — the answer would be "no cases at all"
    for exactly the person this rule exists for. See
    :func:`_own_commercial_seat_users`.

    MONEY IS ANSWERED ALONGSIDE, FROM THE SAME FACTS. ``show_money`` is not a
    fifth line of its own table: it is decided by :func:`_case_money_visible`
    from ``access.is_gm_or_admin`` and from the very ``own_user_ids`` the rows
    are scoped with, so every viewer this function grants case rows to may also
    be shown what those rows are worth, and no other viewer can be. Read that
    function for why the rule changed and what it does — and does not — expose.

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
        # ``own_user_ids`` is not resolved on this branch at all (there is
        # nothing to measure ownership against — see :class:`CaseAccess`), so
        # the money question is asked with the empty tuple; the admin/GM half
        # of :func:`_case_money_visible` is what answers it.
        return CaseAccess(can_open=True, all_cases=True,
                          show_money=_case_money_visible(access, ()))
    roles = _person_roles(request)
    own = _own_commercial_seat_users(request, roles)
    if not own:
        return CaseAccess(can_open=False, all_cases=False)
    # Computed from the SAME ``own`` the two branches below scope their rows
    # with, and after the guard that has already refused everyone it is empty
    # for — that is the whole of "money follows the cases", and why it is
    # resolved here rather than at the top of the function where it used to sit
    # (before the guard, off the login profile, for every caller alike).
    show_money = _case_money_visible(access, own)
    seat_role = _commercial_seat_role(roles)
    seat_role_id = getattr(seat_role, "pk", None)
    # ``own`` is still computed and still checked FIRST, manager or not, and it
    # is what keeps this widening from reaching anybody new: only a person with
    # at least one real Commercial seat gets past the guard above, and
    # ``_holds_commercial_manager_seat`` can only ever answer True for someone
    # who already has one. A manager always contributes a seat user of their
    # own, so the guard can never swallow them.
    if _holds_commercial_manager_seat(request, roles):
        # ``own_user_ids`` is left empty deliberately: with ``all_cases`` True
        # there is nothing left to measure ownership against (see
        # :class:`CaseAccess`), and ``scope_case_rows`` returns every row
        # untouched before it would ever read the tuple.
        return CaseAccess(can_open=True, all_cases=True,
                          seat_role_id=seat_role_id, show_money=show_money)
    # The second half of ``archive_scope``'s Commercial-expert branch, and the
    # ONLY branch that gets it — exactly as over there, where the manager line
    # and the admin/GM line above it both return before it is reached. See
    # :func:`_substituted_seat_users`.
    return CaseAccess(
        can_open=True, all_cases=False, own_user_ids=own,
        substitute_user_ids=_substituted_seat_users(request, own),
        seat_role_id=seat_role_id, show_money=show_money,
    )


def scope_case_rows(rows, case_access: CaseAccess) -> list:
    """``rows`` (dicts carrying ``case_id``) narrowed to what this viewer may see.

    One query, whatever the row count: the ids on the rows are asked for in a
    single ``created_by__in`` lookup rather than each row being re-fetched. The
    rows themselves are returned untouched and in their original order — this
    only decides which of them survive, so every caller keeps whatever extra
    keys its own producer put on them.

    THE ``exclude`` BELOW IS THE SUBSTITUTE RULE, and it is the same one query:
    a seat listed in ``substitute_user_ids`` is being worked by a stand-in, and
    ``cases/services.py::archive_scope`` withholds that seat's terminal rows
    from them (see :func:`_substituted_seat_users`). Both conditions sit in ONE
    ``exclude`` call deliberately — that means "created on a substituted seat
    AND finished", so a running case on a substituted seat and a finished case
    on an ordinary one both survive. The tuple is empty for every viewer the
    rule does not name, and an empty ``__in`` would exclude nothing anyway; the
    guard is there so the SQL stays exactly what it was for them.
    """
    if not case_access.can_open:
        return []
    rows = list(rows)
    if case_access.all_cases:
        return rows
    ids = [row.get("case_id") for row in rows if row.get("case_id") is not None]
    if not ids or not case_access.own_user_ids:
        return []
    from cases.constants import CaseStatus
    from cases.models import Case
    qs = Case.objects.filter(
        pk__in=ids, created_by_id__in=case_access.own_user_ids,
    )
    if case_access.substitute_user_ids:
        qs = qs.exclude(
            created_by_id__in=case_access.substitute_user_ids,
            status__in=CaseStatus.ENDED,
        )
    allowed = set(qs.values_list("pk", flat=True))
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
