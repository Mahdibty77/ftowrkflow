"""Reminders: one person's private notes-to-self about a company, and the
"this one is due" check that puts a notification at the top of their screen.

A MODULE OF ITS OWN RATHER THAN MORE OF ``marketing/services.py``, for one
concrete reason that still holds: everything in that file answers "what may
THIS VIEWER see of MARKETING'S SHARED DATA", takes a ``scope`` argument and
runs it through ``services._scoped``, and this module owns something none of
those functions does — a cache key and the duty to invalidate it — which is
easier to keep correct in one small file than scattered through a
two-thousand-line one.

THIS ROUND REVERSES PART OF AN EARLIER DECISION HERE, AND SAYS SO PLAINLY
RATHER THAN QUIETLY REWRITING THE PARAGRAPH THAT USED TO STAND HERE. That
paragraph used to argue, at length, that a reminder "has no scope" and that
filing an owner-only query next to a scope-aware one in this same module was
exactly the mistake to avoid — "how a ``scope`` argument eventually gets
threaded through one of them by analogy". The owner has now explicitly asked
for precisely that: Reminders should work exactly like Reports already do —
an ordinary Marketing Expert sees only their own, while a Marketing
Supervisor (and, for consistency with every other elevated-access surface in
this app, the platform admin and the General Manager) sees EVERYONE's. See
``marketing/models.py::Reminder`` for the fuller history of the rule this
replaces and for why LISTING is what changed while CREATING and ACTING ON a
reminder did not.

SO, PRECISELY, WHAT CHANGED AND WHAT DID NOT:

* :func:`list_for_user` and :func:`_due_rows` now take a ``scope`` argument,
  built through a private ``_scoped`` helper that is ``marketing/services.py
  ::_scoped`` in miniature — the SAME two-value shape ("own" filters to this
  person's rows, "all" does not), applied to ``owner`` here instead of
  ``created_by`` over there, because ``owner`` is this model's equivalent of
  it. A caller that does not pass ``scope`` still gets exactly the old,
  strictly-private answer — the default is ``"own"``, not the fail-open
  opposite — so nothing that already calls these two functions changes
  behaviour by standing still.
* :func:`_own` ALSO takes a ``scope`` argument now, for the same shape, but
  its real callers — :func:`get_own` and :func:`close_with_report` — do NOT
  pass one, and must not: ACTING ON a reminder (which, per the mandatory
  close-only-via-a-report cycle, means exactly one thing now — closing it out
  with a report) stays exactly as owner-only as it always was. Widening who
  may SEE a person's reminders is not the same decision as widening who may
  CHANGE them, and the owner asked only for the former. See :func:`_own`'s
  own docstring — including the note there about ``reschedule``/``mark_done``,
  the reschedule-or-mark-done-with-no-report pair that used to also call
  through here and has since been removed for being a live bypass of that
  cycle.
* :func:`due_notification` (and the ``_due_rows`` call inside it) DELIBERATELY
  keeps calling with ``scope="own"``, EXPLICITLY, even though the function it
  calls can now do more. The top-of-page banner tells a person about THEIR
  OWN due reminders wherever they are in the site — widening it to a
  Supervisor's or the GM's due reminders would turn one person's private
  "you have something to deal with" nudge into a firehose of the whole unit's
  open items on every page they load, which is not what the owner asked for
  and is not what a banner is for. See that function's own comment for the
  same point made where the call actually happens.
* Nothing here writes a ``ClientEvent``, still, for the reason given further
  down this docstring — that has not changed and this round does not touch
  it.

THE OWNER'S CONSTRAINT IS THE DESIGN, and it was stated literally: this must not
make the site slow or heavy. So:

* NO BACKGROUND JOB, NO SCHEDULER, NO POLLING. Nothing anywhere writes "this
  became due"; being due is ``due_at <= now``, evaluated in the one query below
  at the moment the notification is rendered. There is no clock in this
  application that has to be running for a reminder to fire.
* ONE INDEXED QUERY, FOR ONE USER. :func:`_due_rows` filters on
  ``(owner, state, due_at)`` — exactly the composite index
  ``Reminder.Meta.indexes`` declares, equalities first and the range last — so
  it is an index range scan over one person's own open rows and never a table
  scan. The related company name and case number ride along on the same query
  through the ``client__name`` / ``case__doc_no`` joins rather than costing a
  query per row.
* AND MOST PAGES DO NOT EVEN RUN IT. The result is cached per user for
  :data:`DUE_TTL` seconds and invalidated whenever that user changes one of
  their own reminders, so the notification is normally a cache read. The cache
  is the one the site already has — ``django.core.cache``, the same import
  ``accounts/views.py``'s login throttle and ``cases/pdf_export.py``'s document
  cache use, which is the database table ``entrypoint.sh`` creates with
  ``createcachetable`` (shared across gunicorn workers) or Redis when
  ``REDIS_URL`` is set. No new mechanism, no new configuration.

  BEING HONEST ABOUT WHAT THAT BUYS, because the answer differs per backend and
  it is not a free win. Measured on this codebase's own SQLite/database-cache
  configuration: the due query is ONE statement; a cache HIT is also one
  statement, but a keyed single-row read on ``ft_cache`` instead of a
  three-table join, so the steady state is genuinely cheaper; a cache MISS costs
  seven, because ``DatabaseCache.set`` counts, probes and writes the row. Under
  Redis — which ``settings.py`` switches to whenever ``REDIS_URL`` is set, and
  which is what a busy install runs — the hit costs no query at all and the miss
  costs one. Stated honestly, because the numbers do not say what an earlier
  version of this paragraph claimed: on the DATABASE backend the cache is a
  net LOSS in statement count - equal on a hit, +6 on a miss, and with a 60s
  TTL that is one miss per active user per minute. It buys two things that do
  not show up in that count (the read is a single keyed row rather than a
  three-table join, and the payload is built once rather than per render, which
  is what lets the case-visibility check below be affordable at all), and on
  Redis - which settings.py switches to whenever REDIS_URL is set, and which is
  what a busy install runs - it is a clear saving: no query on a hit, one on a
  miss.

  THE BIGGER SAVING IS UPSTREAM OF BOTH, in
  ``marketing/context_processors.py``: for every login that cannot hold a
  reminder — most of the platform — this module is never entered at all, and no
  cache read happens either.

EVERY WRITE INVALIDATES, AND THAT IS WHY EVERY WRITE LIVES HERE. A reminder
created or closed out with a report changes what the banner should say, and a
banner that keeps insisting on a reminder the person just closed is exactly
the "it stays until you act on it" promise turned into a bug. Views call
these functions and never touch ``Reminder.objects`` themselves, so there is no
path that writes without clearing the key.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import CompanyReport, Reminder, ReminderState

# How long a user's due-notification payload may be served from the cache.
#
# A MINUTE, because that is the largest delay that is still invisible in the
# working rhythm this feature exists for (set a reminder for 10:30, be reminded
# at 10:30) while collapsing the per-page query for a person clicking around the
# site. It is only ever a delay on a reminder BECOMING due — every change the
# person makes themselves clears the key immediately (see :func:`invalidate`),
# so nothing they do is ever reflected late.
DUE_TTL = 60

_KEY_PREFIX = "ft:mkt:rem:due:"


def _key(user) -> str:
    """The per-user cache key. Keyed on the login's primary key alone.

    Not on a seat, and not on the active role: a reminder belongs to the LOGIN
    that created it (``Reminder.owner``), so a person who switches seats is
    still the same owner and must see the same notification. Prefixed like every
    other key this codebase puts in the shared cache (``ft:pdf:`` in
    ``cases/pdf_export.py``, the login throttle's own keys) so one glance at a
    key says which feature owns it.
    """
    return "%s%s" % (_KEY_PREFIX, getattr(user, "pk", "") or "")


def invalidate(user) -> None:
    """Forget this user's cached due-notification. Never raises.

    Called after every write in this module. A cache backend that is down or
    misconfigured must not be the reason setting a reminder fails — the worst
    outcome of a failed delete is a banner up to :data:`DUE_TTL` seconds stale,
    which is the same contained failure ``cases/pdf_export.py`` accepts around
    its own cache.
    """
    try:
        from django.core.cache import cache

        cache.delete(_key(user))
    except Exception:
        pass


def _scoped(qs, user, scope):
    """``qs`` narrowed to ``user``'s own rows when ``scope == "own"``.

    ``marketing/services.py::_scoped`` in miniature, over ``owner`` instead of
    ``created_by`` — the same two-value shape every scoped read in this app
    already uses, applied here now that Reminders follow it too (see the
    module docstring's "THIS ROUND REVERSES PART OF AN EARLIER DECISION"
    section for why that is new). "own" is also the fail-closed direction: an
    unrecognised ``scope`` value behaves as "own", never as "all", so a typo
    or a caller that forgot to pass a real one gets too little rather than
    somebody else's private notes.
    """
    if scope == "own":
        return qs.filter(owner=user)
    return qs


def _due_rows(user, scope: str = "own") -> list:
    """THE ONE QUERY. This user's open reminders that are due, newest first.

    ``scope`` EXISTS FOR SHAPE-CONSISTENCY WITH :func:`list_for_user` AND
    :func:`_own`, NOT BECAUSE THIS FUNCTION'S ONE REAL CALLER USES ANYTHING
    BUT ``"own"``. :func:`due_notification` — the top-of-page banner — passes
    ``scope="own"`` explicitly and always will; see that function's own
    comment for why widening the BANNER to a Supervisor's or the GM's due
    reminders was considered and deliberately rejected this round even though
    the LIST pages were widened. Keeping the parameter here rather than
    hard-coding ``owner=user`` below is what lets a future caller that
    genuinely needs "everyone's due rows" (there is none today) reuse this
    query instead of writing a second one that could drift from it.

    ``owner`` and ``state`` are equalities and ``due_at`` is the range, in that
    order, which is the column order of ``marketing_reminder_due_idx`` — so
    a ``scope="own"`` call (every real call, today) walks one person's open
    rows in ``due_at`` order and stops, rather than reading the table.
    ``values()`` rather than model instances because the result is cached and
    rendered, never saved: it keeps the payload to plain types the cache can
    store and pulls the company name and the case's document
    number through the same single query instead of a lazy FK per row.
    ``client_id``/``client__name`` come back as plain ``None`` for a bare
    reminder that names no company at all (see
    ``marketing/models.py::Reminder.client``) — ``values()`` never raises on a
    NULL FK, it is just a row whose join found nothing, so no special case is
    needed here; :func:`_payload` below is what turns that ``None`` into the
    empty string the banner actually renders.

    DELIBERATELY NOT LIMITED. A ``[:n]`` slice would make the "and N others"
    count in the notification silently saturate, and a count that quietly
    understates how much is waiting is worse than reading a few more rows once a
    minute. The set is bounded by construction anyway: it is ONE person's OWN
    reminders that are both open and already due, i.e. the ones they are about
    to work through.

    Ordered newest-due first because the notification shows the most recently
    due reminder (the owner's wording), which is the opposite of
    ``Reminder.Meta.ordering``'s soonest-first list order — so it is stated here
    rather than inherited. ``-pk`` breaks a tie between two reminders set for
    the very same minute, so the banner is stable across renders.
    """
    qs = _scoped(
        Reminder.objects.filter(state=ReminderState.OPEN, due_at__lte=timezone.now()),
        user, scope,
    )
    return list(
        qs.order_by("-due_at", "-pk")
          .values("pk", "due_at", "note", "client_id", "client__name",
                  "case_id", "case__doc_no")
    )


def _payload(rows, visible_case_ids=None) -> dict:
    """``_due_rows`` output as what the banner renders — or ``{}`` if nothing is
    due.

    ``{}`` and not ``None``: this value is what gets cached, and ``None`` is
    what a cache MISS returns, so an empty result stored as ``None`` would be
    indistinguishable from "not cached yet" and would re-run the query on every
    page for exactly the people who have nothing due. A falsy dict caches the
    "nothing to show" answer as an answer.

    ``others`` is how many MORE are due or overdue beyond the one being shown —
    the owner's "plus a count of any others that are also due or overdue" —
    counted off the same rows, so the headline and the count can never come from
    two different reads.

    ``client_name`` IS ``""`` FOR A BARE REMINDER, NOT ``None`` AND NOT A
    TEMPLATE ERROR. ``top["client__name"] or ""`` already covers it — a
    reminder with no ``client`` produces ``client__name is None`` off the
    join, and ``or ""`` turns that into the same empty string a template
    already renders as "nothing here" for any other blank field. The banner
    template (``core/templates/base.html``) prints this value unconditionally
    once the notice exists at all, and an empty string prints as nothing —
    exactly the "no company shown" degrade the bare-reminder case needs,
    with no template change required for this field specifically. (The
    ``·`` SEPARATOR beside it is guarded in the template itself, so a blank
    name does not leave a stray bullet floating in the sentence.)
    """
    if not rows:
        return {}
    top = rows[0]
    # THE DOCUMENT NUMBER IS STILL CASE IDENTITY HERE. An earlier version of
    # this function argued that a reminder has only one reader - the person who
    # set it, who was shown that number by the scoped picker - so the check
    # ``services._report_row`` runs could be skipped. That was wrong for a
    # reason the argument missed: a seat can LOSE case access after the
    # reminder is set (the Commercial PersonRole is removed), and the banner
    # then went on printing the number on every page while the Cases tab on
    # that same page showed the same person nothing. Two screens contradicting
    # each other about one viewer is the exact failure ``case_access_for``
    # exists to prevent. The cost objection does not survive either: this runs
    # only when the CACHED payload is built, not per render.
    #
    # ``visible_case_ids is None`` means the caller could not resolve access
    # (no request to ask), so the number is withheld - fail closed, the same
    # direction every other guard in this app fails.
    doc_no = top["case__doc_no"] or ""
    case_id = top.get("case_id")
    if case_id is not None and (visible_case_ids is None or case_id not in visible_case_ids):
        doc_no = ""
    return {
        "id": top["pk"],
        "client_id": top["client_id"],
        "client_name": top["client__name"] or "",
        "case_doc_no": doc_no,
        "note": top["note"] or "",
        "due_at": top["due_at"],
        "others": len(rows) - 1,
    }


def _visible_ids_for(request, rows) -> set | None:
    """Which of ``rows``' attached cases this request may actually be shown.

    ``None`` when it cannot be decided - no request, or the access layer itself
    failed - and :func:`_payload` treats that as "withhold the number". Kept
    beside the payload rather than inside it so the cost is paid once, on a
    cache MISS, next to the one query that produced ``rows``.
    """
    if request is None:
        return None
    ids = [r.get("case_id") for r in rows if r.get("case_id") is not None]
    if not ids:
        return set()
    try:
        from .access import case_access_for
        from .services import _visible_case_ids
        return _visible_case_ids(ids, case_access_for(request))
    except Exception:
        return None


def due_notification(user, request=None) -> dict:
    """What the top-of-page notification should say for ``user``, or ``{}``.

    Cache first, query on a miss, cache the answer either way — see the module
    docstring for why the cache is here at all and what it is worth on each
    backend. Every cache access is contained: a backend that cannot answer just
    means the query runs, which is the correct, slower behaviour rather than a
    broken page. This is called from a context processor, so it must never be
    the reason a page 500s.
    """
    key = _key(user)

    def _build():
        # scope="own", EXPLICITLY, and always — see _due_rows' own docstring
        # and the module docstring's "THIS ROUND REVERSES PART OF AN EARLIER
        # DECISION" section. The list pages widened for a Supervisor/GM/admin
        # this round; this banner deliberately did not, because it tells a
        # person about THEIR OWN due reminders wherever they are in the site,
        # and widening it would turn that into a firehose of the whole unit's
        # open items on every page load — not what the owner asked for.
        rows = _due_rows(user, scope="own")
        return _payload(rows, _visible_ids_for(request, rows))

    try:
        from django.core.cache import cache
    except Exception:
        return _build()
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:
        cached = None
    payload = _build()
    try:
        cache.set(key, payload, DUE_TTL)
    except Exception:
        pass
    return payload


def list_for_user(user, scope: str = "own", *, client=None, case=None) -> list:
    """Reminders this viewer may see under ``scope`` — soonest first, open and
    dealt-with alike — optionally narrowed to one company, or to one case.

    ``scope`` NOW EXISTS, AND THE DEFAULT IS STILL "OWN" — see the module
    docstring's "THIS ROUND REVERSES PART OF AN EARLIER DECISION" section for
    the full story. ``"own"`` filters to ``owner=user`` (this model's
    equivalent of ``created_by``); ``"all"`` is unfiltered, the same "the
    union of everyone's" answer ``marketing/services.py::list_reports``
    already gives a Marketing Supervisor, the General Manager and the
    platform admin for reports. A caller that omits ``scope`` gets exactly
    the old, strictly-private answer, so ``marketing/views.py::reminder_list``
    and the new company-page Reminders tab are the only two places that ever
    pass ``"all"`` — both resolve it from ``marketing/access.py::access_for``,
    never derive it themselves, for the same reason every other scoped
    function in this app takes the decision as an argument rather than
    re-deriving it.

    ``client`` (keyword-only, optional) NARROWS TO ONE COMPANY — new for the
    company detail page's own Reminders tab, which wants "this viewer's
    reminders ON THIS COMPANY", not their whole cross-company list. ``None``
    (the default) is the pre-existing behaviour of the standalone "My
    reminders" page: every company at once.

    ``case`` (keyword-only, optional) NARROWS TO ONE CASE — added for
    ``cases/views.py::case_detail``'s own Reminders tab, which wants "the
    reminders set on THIS case", not the whole company's. It stacks with
    ``client`` rather than replacing it (both are plain ``.filter()`` clauses
    on the same queryset), though in practice a caller passing ``case`` also
    passes that case's own ``client`` for the same reason
    ``services.list_reports`` does. ``None`` (the default) leaves every
    existing caller's behaviour unchanged.

    Model instances rather than the ``values()`` dicts :func:`_due_rows`
    returns, because the list page renders each row's company link and case
    number from the related objects and is not cached — ``select_related``
    (now including ``owner``, so a scope="all" list can print WHOSE reminder
    each row is without a query per row) keeps that to one query for the
    whole page. ``Reminder.Meta.ordering`` is soonest-first, which is what a
    to-do list wants, so it is inherited here rather than restated.
    """
    qs = Reminder.objects.select_related("client", "case", "owner")
    if client is not None:
        qs = qs.filter(client=client)
    if case is not None:
        qs = qs.filter(case=case)
    return list(_scoped(qs, user, scope))


def create(owner, client=None, *, note: str, due_at, case=None) -> Reminder:
    """Set one reminder. The only place a ``Reminder`` row is created.

    Keyword-only past ``owner``/``client`` for ``services.add_report``'s reason:
    the rest are optional-looking values of different kinds and a positional
    call site would be one swap away from filing the case as the note.

    ``client`` DEFAULTS TO ``None``, WIDENED ALONGSIDE
    ``marketing/models.py::Reminder.client`` — see that field's own comment
    for why. A reminder about no particular company at all — a bare personal
    note — is now a supported answer, not an error; the company-page
    ``reminder_add`` view still always passes a real ``Client`` (the screen
    it is reached from names one in its own URL), so ``None`` only ever
    arrives from a caller that genuinely has no company in scope, which today
    means the platform-wide "My Tasks" screen a later stage builds.

    WHICH CASE MAY BE ATTACHED IS NOT CHECKED HERE, deliberately and exactly as
    ``services.add_report`` does not check it either: this function has no
    request and no viewer, so it cannot know. The VIEW resolves the submitted id
    against the same scoped rows the company page's Cases tab lists and passes
    ``None`` for anything else — see ``marketing/views.py::reminder_add``.

    NO ``ClientEvent`` IS WRITTEN, and that is a decision rather than an
    omission. ``services.add_contact`` and ``services.add_report`` both log to
    the company timeline because what they record is a fact about the COMPANY
    that the unit shares. A reminder is a private note to self: a row on a
    timeline a Supervisor, the General Manager and the platform admin all read
    would publish the existence, the timing and the company of something this
    model goes out of its way to keep private, and it would say nothing about
    the company that anyone else could act on. The report the person writes when
    the reminder fires is the shared record, and that one does log. (A bare,
    company-less reminder has no company timeline to write to in any case.)
    """
    reminder = Reminder.objects.create(
        owner=owner,
        client=client,
        case=case,
        note=(note or "").strip(),
        due_at=due_at,
    )
    invalidate(owner)
    return reminder


def _own(user, reminder_id, scope: str = "own"):
    """This reminder, narrowed by ``scope`` — ``user``'s own by default, or
    None.

    THE OWNERSHIP CHECK, IN ONE PLACE. Every real caller below goes through
    it, so none of them can be written as "fetch by id, then remember to
    compare the owner". A reminder belonging to somebody else and a reminder
    that does not exist are the same answer — None — so a response can never
    be used to discover that somebody else's reminder exists, which is
    ``views.contact_remove``'s own rule applied to a stricter kind of
    privacy.

    ``scope`` EXISTS FOR THE SAME SHAPE-CONSISTENCY REASON ``_due_rows`` HAS
    IT, NOT BECAUSE ITS REAL CALLERS USE ANYTHING BUT THE DEFAULT. :func:`get_own`
    and :func:`close_with_report` both call this with NO ``scope`` argument,
    deliberately, and must keep doing so: ACTING ON a reminder — the only way
    left to, which is closing it out with a report — stays exactly as
    owner-only as it always was, even now that VIEWING one is scope-aware for
    a Supervisor/GM/admin — see the module docstring's "THIS ROUND REVERSES
    PART OF AN EARLIER DECISION" section, which names this split explicitly.
    Widening this default would let a Supervisor close out someone else's
    private note on their behalf, which the owner never asked for and which
    is a materially bigger change than "let them see it".

    THIS USED TO ALSO BACK ``reschedule``/``mark_done`` — giving a reminder a
    new time, or marking it dealt with, with NO report required either way.
    Both functions are gone: they were the service-layer half of
    ``marketing/views.py::reminder_retime``/``reminder_done``, which let a
    reminder close (or silently reopen with a fresh due date) without ever
    passing through ``close_with_report`` — a live bypass of the mandatory
    close-only-via-a-report cycle this whole round was built around, retired
    for exactly that reason. See the comment left in ``marketing/views.py``
    where those two views used to be defined for the fuller story.
    """
    return _scoped(Reminder.objects.filter(pk=reminder_id), user, scope).first()


def get_own(user, reminder_id):
    """One of ``user``'s own reminders, by id — or ``None``.

    A THIN PUBLIC WRAPPER OVER :func:`_own`, added for "My Tasks"'s own
    "Submit report" step (``marketing/views.py::my_tasks_report``), which has
    to READ the reminder it is about to close — its client, its case, its
    note, its own due date — before ``close_with_report`` deletes the row.
    That is a plain lookup, not a mutation, but it must be exactly as
    owner-scoped as :func:`close_with_report` already is: showing a person a
    report-writing screen pre-titled with
    somebody ELSE's private note would leak the existence, timing and company
    of that note before the write it is guarding even happens. Calling
    :func:`_own` directly here rather than writing a second, matching
    ``Reminder.objects.filter(pk=..., owner=user).first()`` is what keeps
    this from being a second copy of that rule that could quietly drift from
    it — see :func:`_own`'s own docstring for why a reminder belonging to
    somebody else and one that does not exist are the identical answer,
    ``None``, on purpose.
    """
    return _own(user, reminder_id)


def count_open_for_client(user, client) -> int:
    """How many of ``user``'s OWN reminders on ``client`` are still OPEN.

    ALWAYS OWNER-SCOPED, WITH NO ``scope`` PARAMETER AT ALL — deliberately
    unlike :func:`list_for_user` above. This backs the company detail page's
    "Company" card, which the owner asked for as "the count of THIS VIEWER'S
    OWN open reminders set on this company" — a per-viewer number by its own
    stated design, kept that way regardless of whatever the Reminders TAB on
    that same page ends up showing a Supervisor (everyone's rows, per this
    round's widening of :func:`list_for_user`). Giving this its own function
    rather than a ``len([r for r in list_for_user(...) if r.state == OPEN])``
    one-liner at the call site is what keeps that distinction impossible to
    blur by accident later: this counts one thing, always the same way, no
    matter what the tab beside it is scoped to.
    """
    return Reminder.objects.filter(
        owner=user, client=client, state=ReminderState.OPEN,
    ).count()


# ``reschedule``/``mark_done`` USED TO LIVE HERE — "give one of user's own
# reminders a new time, and reopen it" / "mark one of user's own reminders
# dealt with", both routed through ``_own`` with no ``scope`` argument. They
# were the service-layer half of ``marketing/views.py
# ::reminder_retime``/``reminder_done``, the pre-"My Tasks" reschedule/
# mark-done pair that closed (or reopened) a reminder with NO report ever
# required. Retiring those two views without also retiring these left a
# live, callable path that quietly bypassed the mandatory
# close-only-via-a-report cycle this whole round was built around — grepping
# the repo after removing the views turned up no OTHER caller of either
# function, so both are removed with them rather than kept "just in case".
# ``close_with_report`` below is now the only way a reminder is ever closed,
# and it is the only place ``ReminderState.OPEN`` is ever left for something
# other than itself — see that constant's own docstring on
# ``marketing/models.py::ReminderState`` for why ``DONE`` remains a real,
# readable state even so: rows already marked dealt-with the old way, before
# this fix, still exist and must go on rendering correctly as "Dealt with"
# everywhere ``Reminder.is_done`` is read, even though nothing can produce a
# new one from here on.


def close_with_report(reminder_id, user, *, text: str = "", options=()) -> CompanyReport:
    """Close out one of ``user``'s own reminders by writing the report that
    accounts for it, and delete the reminder — the "write report, freeze this
    reminder's own dates onto it, then remove the reminder" half of the loop
    the owner described (see ``marketing/models.py::Reminder``'s own
    docstring for the full rhythm: a case gets a reminder, the person is
    reminded, they write a report, they set the next reminder, until the case
    ends). Returns the created :class:`~marketing.models.CompanyReport`.

    OWNER-SCOPED, LIKE EVERY OTHER WRITE IN THIS MODULE — routed through
    :func:`_own` with NO ``scope`` argument, so only the reminder's OWN owner
    may close it out (see :func:`_own`'s own docstring, and the module
    docstring's "ACTING ON a reminder ... stays exactly as owner-only as it
    always was" point): a Supervisor who can now SEE a colleague's reminder
    must not also be able
    to close it out on their behalf — that is a materially bigger grant than
    "may see", and the owner never asked for it. A reminder that does not
    exist, or exists but belongs to somebody else, raises
    ``Reminder.DoesNotExist`` either way — the two are indistinguishable on
    purpose (``_own``'s own rule), so a caller cannot use the failure to
    discover that somebody else's private reminder exists.

    ``client``/``case`` COME FROM THE REMINDER BEING CLOSED, NEVER FROM THE
    CALLER — a report that closes a reminder is always about whatever that
    reminder was about, not something a caller gets to override by passing a
    different one. ``reminder.client`` may itself be ``None`` (a bare,
    company-less reminder — see ``marketing/models.py::Reminder.client``),
    which closes into an equally bare report; that is exactly why
    ``marketing/models.py::CompanyReport.client`` and
    ``marketing/services.py::add_report``'s own signature were widened
    alongside it — see both of their comments for the reasoning this
    function leans on.

    THE THREE FROZEN FIELDS ARE WHY THIS FUNCTION EXISTS AS ONE PLACE RATHER
    THAN BEING LEFT FOR A VIEW TO ASSEMBLE FROM TWO SEPARATE CALLS.
    ``CompanyReport.reminder_set_at`` / ``reminder_due_at`` / ``reminder_note``
    are frozen copies of the closed reminder's own ``created_at`` / ``due_at``
    / ``note`` because the reminder itself is about to be deleted — see that
    field's own docstring on ``CompanyReport`` for why a live FK cannot do
    this job. They are read off ``reminder`` and written onto the just-created
    ``report`` inside the SAME transaction that goes on to delete
    ``reminder``, which is what guarantees the freeze can never observe a
    reminder that something else has already changed or removed out from
    under it.

    ONE TRANSACTION, START TO FINISH: ``services.add_report`` writes the
    report (its own ``transaction.atomic()`` becomes a savepoint nested
    inside this one, not a second top-level transaction), the three frozen
    fields are set and saved on that same report, and the reminder row is
    then deleted — all of it commits together or none of it does, so a
    report can never be left pointing at a reminder that also still exists,
    and a reminder can never be removed without the report that was supposed
    to replace it having actually been written.

    THE "THEN OPEN A FORM FOR THE NEXT REMINDER, SAME COMPANY/CASE" HALF OF
    THE CYCLE IS DELIBERATELY NOT HERE. That is a VIEW-level redirect
    decision — a later stage's "My Tasks - Reminders" screen reads the
    ``client``/``case`` off the ``CompanyReport`` this function returns and
    decides what to do next; this function's own job ends the instant the
    reminder it was closing is gone. Keeping that decision out of the service
    layer is the same split every other write in this module already makes
    between "what changed in the database" and "where the browser goes next".

    ``text``/``options`` ARE THE FRESHLY-WRITTEN REPORT'S OWN CONTENT — passed
    straight through to ``services.add_report`` unvalidated, for that
    function's own stated reason (the "at least one of options/text" rule is
    a FORM-layer rule, enforced where a violation can be reported against the
    field the writer actually left blank): this is the SERVICE layer, and a
    future form's ``clean()`` is where that rule will actually be enforced,
    exactly as it already is for a standalone report.
    """
    from . import services

    reminder = _own(user, reminder_id)
    if reminder is None:
        raise Reminder.DoesNotExist(
            "No open reminder %r belongs to this user." % (reminder_id,))
    with transaction.atomic():
        report = services.add_report(
            reminder.client, user,
            text=text, options=options, case=reminder.case,
        )
        # THE FREEZE. Read off ``reminder`` — still a live row at this point,
        # not yet deleted — and written onto the report that will outlive it.
        # See the docstring's "THE THREE FROZEN FIELDS" section for why this
        # cannot instead be a live FK from the report to the reminder.
        report.reminder_set_at = reminder.created_at
        report.reminder_due_at = reminder.due_at
        report.reminder_note = reminder.note
        report.save(update_fields=[
            "reminder_set_at", "reminder_due_at", "reminder_note",
        ])
        # THE REMINDER IS DELETED, NOT MARKED DONE. This is what makes the
        # report the only surviving record of the reminder's own timing —
        # exactly the premise the three frozen fields above exist to answer
        # for, and exactly what the owner described: the reminder is gone the
        # instant the report that closes it is saved, not kept around in a
        # DONE state the way :func:`mark_done` leaves an ordinary "no next
        # step" reminder.
        reminder.delete()
    invalidate(user)
    return report


def validate_due_at_shift(user, due_at) -> bool:
    """Does ``due_at``'s TIME OF DAY fall inside ``user``'s own work-shift
    window? ``True``/``False``, never raises — the caller decides what a
    ``False`` means to it (a form's ``clean()`` turns it into a
    ``ValidationError`` on the field; a plain call site can just branch on
    it).

    ENFORCEMENT INFRASTRUCTURE ONLY, THIS ROUND — nothing in this module or
    ``marketing/forms.py`` calls this yet. Both the existing company-page
    ``ReminderForm`` and a later stage's company-less "My Tasks" reminder
    form are meant to wire this into their own ``clean()`` so a reminder
    cannot be set for a time its owner is never at their desk to be reminded
    at, but doing that wiring — and deciding how the rejection reads on
    screen — is that later stage's job. This function only has to exist,
    behave correctly on its own, and be trivially unit-testable in isolation,
    which is exactly what it is.

    WHY WORK SHIFT AND NOT SOMETHING RECOMPUTED HERE: ``people/work_shift.py``
    already answers "what is this person's own daily window" once, correctly,
    for the whole platform (login gating, the end-of-shift banner, approved
    overtime) — see that module's own docstring. Reusing
    ``person_for_user``/``shift_window`` rather than re-deriving the same
    answer a second time is what keeps a reminder's own notion of "inside the
    shift" from silently drifting from every other screen's.

    ONLY THE TIME OF DAY IS CHECKED, NOT THE DATE. ``shift_window`` returns a
    ``(start, end)`` pair of plain ``datetime.time`` values with no date
    attached — a person's shift is the same 08:00-17:00 (or whatever their
    own ``Person.work_start``/``work_end`` says) on every working day this
    function is asked about, so comparing ``due_at.time()`` against that pair
    is the whole check. Which CALENDAR DAYS count as a working day at all
    (weekends, holidays) is a different question this function does not
    answer and was not asked to.

    AN OVERNIGHT WINDOW (e.g. 22:00-06:00) IS HANDLED BY REUSING
    ``people/work_shift.py::_in_window`` DIRECTLY, NOT BY RE-DERIVING THE SAME
    COMPARISON A SECOND TIME. That helper already carries the "``start > end``
    means the window wraps past midnight, so a time counts as inside it when
    it is at or after ``start`` OR before ``end``" logic for login gating; a
    second, hand-written copy of that comparison here could silently drift
    from it (a boundary fixed on one side and not the other, say) the next
    time either one is touched. The leading underscore on ``_in_window``
    marks it private to the ``people`` APP, not to that one module — the same
    convention ``marketing/services.py`` already leans on for
    ``cases.services._actor_snapshot`` — and reading a pure, side-effect-free
    comparison across that line is safe for the identical reason.

    ``due_at`` IS EXPECTED TO BE A ``datetime`` (naive or aware; only
    ``.time()`` is read, and Django's own form field already returns one in
    the project's local time — see ``marketing/forms.py::_clean_jalali_datetime``
    — so no timezone conversion happens here). ``None`` is treated as
    invalid (``False``) rather than raising, since "no time was even picked"
    is exactly the kind of input a form's ``clean()`` hands this function on
    a still-invalid submission.
    """
    if due_at is None or getattr(due_at, "time", None) is None:
        return False
    from people.work_shift import _in_window, person_for_user, shift_window

    person = person_for_user(user)
    start, end = shift_window(person)
    due_time = due_at.time().replace(microsecond=0)
    return _in_window(due_time, start, end)


def validate_due_at_future(due_at) -> bool:
    """Is ``due_at`` still in the future? ``True``/``False``, never raises —
    the same "caller decides what a False means to it" contract
    :func:`validate_due_at_shift` already documents, reused here for the
    companion rule the owner asked for THIS round: a reminder is a thing to
    be reminded of LATER, and a due time that has already passed the moment
    it is submitted is not a reminder at all, it is a typo.

    THIS IS A GENUINE, DELIBERATE NARROWING OF WHAT ``marketing/forms.py::
    _clean_jalali_datetime`` USED TO ARGUE, AT LENGTH, FOR ITS OWN REASONS —
    see that function's own docstring, which still explains the history: a
    past due time was allowed there because the old "Set new time"
    re-time control on the pre-"My Tasks" reminders list needed to be able to
    walk an overdue reminder's time BACKWARDS to "now" as the fastest way to
    bring it back to the top of the list. That control
    (``marketing/views.py::reminder_retime``) no longer exists — it was
    retired as a live bypass of the mandatory close-only-via-a-report cycle
    (see the comment left in ``views.py`` where it used to be defined) — so
    the one legitimate reason a reminder's OWN due time ever needed to name
    the past is gone, and every remaining caller of this check is a brand
    new reminder being CREATED, never one being walked backward. Enforcing
    "future only" here closes that gap rather than reopening the retired
    control's own loophole by a different, unguarded door.

    COMPARED AGAINST ``timezone.now()`` AT THE INSTANT THIS IS CALLED, not
    against some earlier snapshot — a submission that was valid when the page
    was rendered but has since ticked past into the past (a very slow typist,
    or a stale tab resubmitted) is correctly refused, the same "compare live,
    never a cached moment" discipline ``_task_rows``' own ``is_due`` already
    documents for the identical reason.

    ``due_at`` IS EXPECTED TO BE A ``datetime`` (naive or aware — Django's own
    form field already hands this an aware one, in the project's local time,
    via ``marketing/forms.py::_clean_jalali_datetime``, but a naive value is
    made aware in the project's own timezone rather than compared against
    ``now()`` in the wrong frame, so a caller that ever passes a naive value
    still gets a correct answer instead of a silently wrong one). ``None`` is
    treated as invalid (``False``) — "no time was even picked" is exactly the
    kind of input a form's ``clean()`` hands this function on a still-invalid
    submission, ``validate_due_at_shift``'s own reasoning for the same guard.
    """
    if due_at is None:
        return False
    if timezone.is_naive(due_at):
        due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
    return due_at > timezone.now()


def validate_due_at_working_day(due_at) -> bool:
    """Does ``due_at``'s CALENDAR DAY fall on a day this company actually
    works? ``True``/``False``, never raises — the third of the three rules
    the owner asked for this round, alongside :func:`validate_due_at_future`
    above (a reminder must not be set for the past) and
    :func:`validate_due_at_shift` (a reminder must fall inside the owner's
    own daily HOURS). This one is the missing DAY half of that same
    question: ``validate_due_at_shift`` is explicit that it checks the TIME
    OF DAY only and says plainly that "which calendar days count as a
    working day at all (weekends, holidays) is a different question this
    function does not answer" — this is that other function, answering
    exactly that question, for a reminder's due DATE rather than its time.

    REUSES ``people/shift_hours.py::_is_working_day`` VERBATIM, RATHER THAN
    RE-DERIVING WEEKEND/HOLIDAY LOGIC A SECOND TIME — the exact function the
    monthly shift-planning table (``plan_month``) and the login-day gate
    (``note_shift_login``/``record_presence_ping``) both already lean on for
    the identical question, "is the shift actually expected to be worked on
    this Gregorian date". It already folds BOTH halves of "not a working
    day" into one boolean — Thursday/Friday weekends
    (``people.iran_holidays.WEEKEND_WEEKDAYS``) AND Iran's official calendar
    (``people.iran_holidays.is_official_holiday``, itself already layered on
    top of the weekend check there, per that module's own docstring: "a
    holiday that falls on a weekend is not double-counted as an extra day
    off") — so calling it once here is not a partial answer that still needs
    a second, separate holiday check bolted on beside it; it already IS both
    checks, done the one place this codebase already does them.

    IMPORTED BY ITS OWN UNDERSCORE NAME, DELIBERATELY, RATHER THAN GIVEN A
    NEW PUBLIC ALIAS — the identical judgement ``validate_due_at_shift``
    itself already makes for ``people/work_shift.py::_in_window`` a few lines
    up in this same module (see that function's own comment): the leading
    underscore marks a name private to the ``people`` APP, not to the one
    module it is defined in, and reading a pure, side-effect-free predicate
    across that line — no state, no writes, just a date in and a bool out —
    is the same safe crossing ``marketing/services.py`` already documents for
    ``cases.services._actor_snapshot``. Renaming it here would create a
    second name for the one answer to "is this a working day", which is
    exactly the kind of duplicate-source-of-truth this codebase goes out of
    its way to avoid — see this module's own repeated "ONE ... NEVER A
    SECOND" refrain elsewhere in this file.

    ONLY THE DATE PART IS READ — ``due_at.date()``, converted to this
    project's LOCAL calendar day first (``timezone.localtime``) exactly as
    ``_task_rows``' own ``day_key`` already is, so a reminder set for a few
    minutes after local midnight is judged against the LOCAL day it visibly
    falls on, never the UTC day the raw stored instant happens to carry.
    ``due_at`` is expected to be an aware ``datetime`` (see
    :func:`validate_due_at_future`'s identical note on why a naive value is
    made aware rather than mishandled); ``None`` is treated as invalid
    (``False``), the same guard every sibling validator in this module uses.
    """
    if due_at is None or getattr(due_at, "date", None) is None:
        return False
    if timezone.is_naive(due_at):
        due_at = timezone.make_aware(due_at, timezone.get_current_timezone())
    from people.shift_hours import _is_working_day

    return _is_working_day(timezone.localtime(due_at).date())
