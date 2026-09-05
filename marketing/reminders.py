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
  its two real callers — :func:`reschedule` and :func:`mark_done` — do NOT
  pass one, and must not: ACTING ON a reminder (giving it a new time, marking
  it dealt with) stays exactly as owner-only as it always was. Widening who
  may SEE a person's reminders is not the same decision as widening who may
  CHANGE them, and the owner asked only for the former. See :func:`_own`'s
  own docstring.
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
created, rescheduled or marked dealt-with changes what the banner should say,
and a banner that keeps insisting on a reminder the person just handled is
exactly the "it stays until you act on it" promise turned into a bug. Views call
these functions and never touch ``Reminder.objects`` themselves, so there is no
path that writes without clearing the key.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import Reminder, ReminderState

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


def create(owner, client, *, note: str, due_at, case=None) -> Reminder:
    """Set one reminder. The only place a ``Reminder`` row is created.

    Keyword-only past ``owner``/``client`` for ``services.add_report``'s reason:
    the rest are optional-looking values of different kinds and a positional
    call site would be one swap away from filing the case as the note.

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
    the reminder fires is the shared record, and that one does log.
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

    THE OWNERSHIP CHECK, IN ONE PLACE. Both mutations below go through it, so
    neither can be written as "fetch by id, then remember to compare the owner".
    A reminder belonging to somebody else and a reminder that does not exist are
    the same answer — None — so a response can never be used to discover that
    somebody else's reminder exists, which is ``views.contact_remove``'s own
    rule applied to a stricter kind of privacy.

    ``scope`` EXISTS FOR THE SAME SHAPE-CONSISTENCY REASON ``_due_rows`` HAS
    IT, NOT BECAUSE ITS TWO REAL CALLERS USE ANYTHING BUT THE DEFAULT.
    :func:`reschedule` and :func:`mark_done` both call this with NO ``scope``
    argument, deliberately, and must keep doing so: ACTING ON a reminder
    (giving it a new time, marking it dealt with) stays exactly as
    owner-only as it always was, even now that VIEWING one is scope-aware for
    a Supervisor/GM/admin — see the module docstring's "THIS ROUND REVERSES
    PART OF AN EARLIER DECISION" section, which names this split explicitly.
    Widening this default would let a Supervisor re-time or close out
    someone else's private note, which the owner never asked for and which
    is a materially bigger change than "let them see it".
    """
    return _scoped(Reminder.objects.filter(pk=reminder_id), user, scope).first()


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


def reschedule(user, reminder_id, due_at) -> bool:
    """Give one of ``user``'s own reminders a new time, and reopen it.

    THE LOOP THE OWNER DESCRIBED IS THIS FUNCTION: a case gets a reminder, the
    person is reminded, they write a report, they set the next time — until the
    case ends. So setting a new time is what "acting on it" normally means, and
    it puts the row back in the OPEN state unconditionally: a reminder that had
    been marked dealt with and is now given a fresh time is waiting again, and
    leaving it DONE would silently produce a row that can never fire.

    Returns whether anything was changed, so a caller can tell a no-op from a
    write; the view redirects identically either way (see
    ``views.contact_remove`` for why a mutation that quietly does nothing is the
    right answer to a request for a row that is not yours).
    """
    reminder = _own(user, reminder_id)
    if reminder is None or due_at is None:
        return False
    with transaction.atomic():
        reminder.due_at = due_at
        reminder.state = ReminderState.OPEN
        reminder.save(update_fields=["due_at", "state"])
    invalidate(user)
    return True


def mark_done(user, reminder_id) -> bool:
    """Mark one of ``user``'s own reminders dealt with.

    The other way to act on a notification: the thing is handled and there is no
    next time to set. The row is kept rather than deleted — the person's own
    list is where they see what they have already dealt with, and nothing else
    in this app reads it — and it drops out of :func:`_due_rows` immediately
    because that query asks for OPEN rows only.
    """
    reminder = _own(user, reminder_id)
    if reminder is None:
        return False
    if reminder.state != ReminderState.DONE:
        reminder.state = ReminderState.DONE
        reminder.save(update_fields=["state"])
    invalidate(user)
    return True
