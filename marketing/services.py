"""Client/label operations: search, register, tag, and connection reports.

Every function here is a thin, transaction-safe wrapper around
``cases.models.Client`` (the ONE shared company directory — see
``marketing/models.py``'s module docstring for why there is no
Marketing-only copy of it), ``marketing/models.py::ClientLabel`` (Marketing's
manual tags on a client) and ``cases.models.Case`` (read-only here — this
module never creates or edits a case, it only reads ``Case.marketing_label``
to derive the OTHER half of a client's labels). Nothing here knows about
HTTP; request handling stays in ``marketing/views.py``.

TWO SOURCES OF A LABEL, MERGED AT READ TIME. A client can carry a business
role tag for two independent reasons:

* MANUAL — a ``ClientLabel`` row a Marketing user added by hand.
* CASE-DERIVED — the client has at least one ``cases.Case`` whose
  ``marketing_label`` equals that label, OR (the blank-defaults-to-owner
  rule from ``cases.constants.MarketingLabel``'s own docstring) a case whose
  ``marketing_label`` is blank, which counts as OWNER.

Every function below that reports labels/companies merges both sources and
says, per item, which one is displayed (``source``) and whether the other
one also applies (``also_manual``) — see ``companies_for_label`` and
``connections_of_client`` for the exact shape, documented once there and
reused identically in both directions (one label -> its companies, one
client -> its labels).

A THIRD mechanism sits alongside those two, added this round:
``marketing/models.py::Connection``, a directed (client, role) -> (client,
role) edge created through the chart's own Attach flow (see
``create_connection``/``remove_connection`` below). The ``Connection`` row
itself never changes which labels a client DIRECTLY holds — the two sources
above still decide that, unchanged — it only decides WHO is displayed under a
label's chart card (``connections_of_client``'s new ``connected`` field), and
it can add an entry to that report for a label the client never actually
held itself, purely because the client connected some OTHER company under
it (see ``connections_of_client``'s docstring for the worked example).
CONFIRMING one, however, CAN cause a DIRECTLY-HELD label to appear where
there was none before: ``create_connection`` separately writes a MANUAL
``ClientLabel`` for the target when the target does not already effectively
hold the role it was just connected under (see that function's own docstring
and ``_client_holds_label``) — this is what makes the target show up when a
role card is browsed on its own, independent of the anchor it was attached
through, since ``companies_for_label`` has never looked at ``Connection`` rows
directly. That write goes through the ordinary manual-label mechanism
(``toggle_manual_label``), not through any new merge rule, so every other
read in this module already understands it for free.

THREE MORE THINGS HANG OFF A COMPANY, and all three follow the same rules as
everything above rather than inventing their own:
``marketing/models.py::CompanyContact`` (the people to call there — created,
listed and removed by ``add_contact``/``list_contacts``/``remove_contact``,
scoped by the same ``(user, scope)`` pair); ``marketing/models.py::
CompanyReport`` (what a marketing person wrote about the company — created and
listed by ``add_report``/``list_reports``, scoped by that same pair through
that same ``_scoped`` helper, deliberately NOT by a second rule of its own);
and ``marketing/models.py::ClientEvent`` (the company's own immutable timeline
— written by ``log_client_event``/``_try_log`` from every mutating service in
this file, and read back by ``client_timeline``).

THE TIMELINE NO LONGER SAYS ANYTHING ABOUT CASES. ``client_timeline`` used to
merge a live-derived "case NNNN was opened" entry per case into those stored
rows; the owner removed it ("it is not needed to record that someone opened a
case"), so the timeline is now Marketing's own history alone. A company's cases
are listed by the company detail page's own Cases tab, which has always been a
separate code path with its own scoping.

IT DOES STILL CARRY CASE NUMBERS THAT WERE FROZEN INTO IT, though, and that is
a different thing from deriving rows: ``add_report`` freezes the attached
case's document number as a ``REPORT_ADDED`` row's ``subject``, and
``_connection_comment`` freezes one into a ``CONNECTION_ADDED`` /
``CONNECTION_REMOVED`` row's ``comment``. Those rows are scoped by the ACTOR
(``_scoped_events``) — Marketing's rule, which says nothing about cases — so
``client_timeline`` takes a ``case_access`` and puts every such number through
``_redact_case_numbers`` before returning. Same rule as everywhere else in this
file, applied at read time because a frozen audit row cannot be un-written; the
row itself is never dropped, only the case goes unnamed.

Case EXISTENCE (which clients have which cases, and each case's effective
label) is SHARED TRUTH, not Marketing-owned data, so that half of every
case-derived read in this module is deliberately UNSCOPED: every viewer —
Expert, Supervisor, or the view-only GM — sees the same set of labels on a
company and the same "this company has cases" fact.

CASE IDENTITY IS NOT. A case's DOCUMENT NUMBER, its id, and the name frozen
onto it are case CONTENT, not the shared fact that a relationship exists, and
they are governed by ``marketing/access.py::case_access_for`` — the owner's
rule that a Marketing viewer sees only the cases that are theirs on the
commercial side, and the admin/GM see them all. Every function below that
emits a document number therefore takes a ``case_access`` argument and filters
through ``marketing/access.py::scope_case_rows``, exactly like every case LIST
in this app already does (``companies_for_label``, ``connections_of_client``).
``case_access`` is decided by the caller from the request, never re-derived
here, for the same reason ``scope`` is not.

A CONSEQUENCE, AND IT IS THE CORRECT ONE: two viewers looking at the same
company can legitimately see the same label backed by different numbers of
document numbers. That is not an inconsistency to "fix" — it is exactly how
every other case list in this app already behaves, and the alternative is
printing a colleague's document number next to a label to prove the label is
real.

Manual ``ClientLabel``
rows are the one thing that stays scoped by ``(user, scope)``, the same way
``Entity`` used to be:
most functions below take ``(user, scope)`` — ``scope`` is ``"own"`` or
``"all"``, decided by ``marketing.access.access_for`` from the caller's seat
and never re-derived here (this module stays request-agnostic on purpose).
"own" filters manual rows down to the ones ``user`` themselves added; "all"
is unfiltered. Nothing here trusts a caller to have already applied the
filter itself — that would make the scope a UI nicety instead of the actual
security boundary it has to be.

TERMINAL CASES STILL COUNT. A case that ended up BURNED, CANCELLED, or
otherwise closed still proved a real business relationship existed with its
client, so nothing here excludes a case by status (see
``cases.constants.CaseStatus.TERMINAL`` if you are looking for that list —
it is deliberately not consulted anywhere in this module).
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Q

from cases import services as case_services
from cases.constants import CaseStatus, MarketingLabel
from cases.models import Case, Client
from core.persian_text import normalize_persian

from .access import CaseAccess, scope_case_rows
from .models import (
    ClientEvent, ClientEventAction, ClientLabel, CompanyContact, CompanyReport,
    Connection, ContactGender, ContactPhone, ContactRole, ReportOption,
)

# The twenty labelable keys — NOT all twenty-one chart fields ("us" is the
# one field no label can ever express; it is backed by case data alone, see
# ``us_connections``). Fourteen of them (``MarketingLabel.CHOICES``) also
# describe a business role a case's client could actually hold — see
# ``cases.constants.MarketingLabel``'s own docstring for why only those
# fourteen. The other six (``MarketingLabel.MANUAL_ONLY_CHOICES``: rival,
# supplier, project, phase, tpi and laboratory) can NEVER come from a case —
# Case.marketing_label's own choices deliberately stop at the fourteen, so a
# company only ever picks up those six labels via a manual ``ClientLabel``
# row. Deriving from the SAME combined list ``ClientLabel.label``'s own
# ``choices`` uses (see marketing/models.py) keeps this module's notion of
# "every labelable key" from drifting apart from what the model actually
# accepts — every function below that walks ``LABEL_KEYS`` generically
# (``label_counts``, ``companies_for_label``, ``connections_of_client``,
# ``labels_for_clients``, ``is_label``) therefore already works correctly
# for those six too, with no special-casing: the case side of the merge
# simply never matches them (no ``Case`` row can ever carry that
# ``marketing_label``), so they end up genuinely manual-tag-only in
# practice, which is exactly the desired behaviour.
_ALL_LABEL_CHOICES = MarketingLabel.CHOICES + MarketingLabel.MANUAL_ONLY_CHOICES
FIELD_LABELS = {k: v for k, v in _ALL_LABEL_CHOICES}
LABEL_KEYS = tuple(k for k, _ in _ALL_LABEL_CHOICES)


def is_label(key: str) -> bool:
    return key in FIELD_LABELS


# The fail-closed default for every ``case_access`` argument below: a viewer
# who may open no case at all, so ``scope_case_rows`` yields nothing and no
# document number is emitted.
#
# It exists for exactly the reason ``search_companies``' ``user=None,
# scope="own"`` defaults do, and is documented once here rather than at each of
# the four call sites: this module stays request-agnostic, so it cannot resolve
# a viewer's case access itself, and a caller who forgets to pass one must get
# TOO LITTLE — a label with no document numbers beside it — never somebody
# else's case identity. Every real caller passes
# ``marketing/access.py::case_access_for(request, access)``.
NO_CASE_ACCESS = CaseAccess(can_open=False, all_cases=False)


def _visible_case_ids(case_ids, case_access: CaseAccess) -> set:
    """Which of these case primary keys ``case_access`` actually admits.

    A thin adapter, not a second rule: ``marketing/access.py::scope_case_rows``
    is the one place that decides, and it works on ROWS carrying ``case_id``,
    so a caller holding bare ids wraps them, asks it, and unwraps the answer.
    Written once here because two callers now need the SET rather than the
    filtered rows — a report keeps its place in the list whether or not its case
    may be named (see ``_report_row``), so "which survive" is the wrong shape
    for them.

    One query, whatever the count, because ``scope_case_rows`` is one query.
    """
    ids = {i for i in case_ids if i is not None}
    if not ids:
        return set()
    return {
        row["case_id"]
        for row in scope_case_rows([{"case_id": i} for i in ids], case_access)
    }


def _hidden_case_doc_nos(candidates, case_access: CaseAccess) -> set:
    """Of these frozen strings, the ones that ARE case document numbers this
    viewer may not be shown.

    THE READ-TIME HALF OF THE DOCUMENT-NUMBER RULE, and it exists because
    ``ClientEvent`` freezes its nouns as TEXT at write time, by design (see
    ``marketing/models.py::ClientEvent``): a timeline row that recorded a case
    number recorded the STRING, not a foreign key, and a row already written
    cannot be un-written. The module docstring's rule — a document number is
    case identity and is governed by ``case_access_for`` — therefore has to be
    applied where the row is READ, which is here, and not by refusing to freeze
    it, which would break the audit record for the people who may see it.

    Works by asking the case table which of these strings is a real ``doc_no``
    and then putting exactly those through the SAME
    ``marketing/access.py::scope_case_rows`` every case LIST in this app goes
    through — no second rule, and no per-action parsing: a string is treated as
    case identity if and only if the case table says it is one.

    A STRING THAT MATCHES NO CASE IS LEFT ALONE, and that is not an oversight.
    Most of the strings handed to this function are company names, role labels
    and contact names, which is precisely why it cannot blank what it does not
    recognise. The residue — a number frozen for a case that has since been
    deleted — names nothing this viewer (or anyone) could open, so leaving it is
    the honest reading of an audit row rather than a hole in the rule.

    Two queries total for a whole timeline: one over ``Case.doc_no``, one inside
    ``scope_case_rows``.
    """
    wanted = {c for c in candidates if c}
    if not wanted:
        return set()
    rows = [
        {"case_id": pk, "doc_no": doc_no}
        for pk, doc_no in Case.objects.filter(
            doc_no__in=wanted).values_list("pk", "doc_no")
    ]
    if not rows:
        return set()
    visible = {row["doc_no"] for row in scope_case_rows(rows, case_access)}
    return {row["doc_no"] for row in rows} - visible


def _scoped(qs, user, scope):
    """``qs`` narrowed to ``user``'s own rows when ``scope == "own"``."""
    if scope == "own":
        return qs.filter(created_by=user)
    return qs


def effective_marketing_label(value: str) -> str:
    """The blank-defaults-to-owner rule, applied to a RAW ``marketing_label``
    string rather than a ``Case`` instance.

    Split out of ``_effective_label`` below purely so a caller that is
    holding two raw strings — not two ``Case`` rows — can apply the exact
    same normalization without duplicating its one line of logic inline.
    The concrete caller this exists for is ``cases/views.py``'s two case-edit
    paths (the contacts-only edit and the fresh-draft full edit): both need
    to compare the marketing role BEFORE an edit against the role AFTER it,
    to decide whether ``log_case_role_change`` below should fire at all — a
    blank-to-"owner" or "owner"-to-blank pair is not a real change (see that
    function's own docstring), and getting that comparison right means
    reusing this exact rule, not re-deriving it as ``value or "owner"`` at
    the call site where a future edit to the real rule (should OWNER ever
    stop being the default) could silently drift out of step.
    PUBLIC (no leading underscore), unlike most of this module's small
    helpers, specifically so it is safe and intended for that cross-app
    import — see ``marketing/models.py::ClientEventAction``'s
    ``CASE_ROLE_CHANGED`` docstring for why the cases app needs it at all.
    """
    return value or MarketingLabel.OWNER


def _effective_label(case: Case) -> str:
    """A case's business-role label, applying the blank-defaults-to-owner rule."""
    return effective_marketing_label(case.marketing_label)


# The "cases connected to Us" side panel shows a case's outcome, not its raw
# workflow status — the owner asked for exactly three buckets, named exactly
# this way, and nothing finer: a dead case (fell through or was cancelled)
# reads "Cancelled", a case Commercial has actually finished (final-approved
# or fully shut) reads "Approved", and every other status — still moving
# through Draft/Technical/Supply/Commercial, including the various
# unsuppliable-pending states — reads "بدون نتیجه" ("no result yet"). Terminal
# states that are neither a clean win nor an explicit cancel (e.g.
# UNSUP_CLOSED) deliberately fall into the third bucket rather than being
# guessed into "Cancelled" — the owner named exactly BURNED/CANCELLED for
# that bucket and nothing else.
_STATUS_CANCELLED = frozenset({CaseStatus.BURNED, CaseStatus.CANCELLED})
_STATUS_APPROVED = frozenset({CaseStatus.FINAL_APPROVED, CaseStatus.FINAL_CLOSED})


def _status_fa(status: str) -> str:
    """A case's raw ``status`` bucketed into the three outcomes above."""
    if status in _STATUS_CANCELLED:
        return "کنسل"
    if status in _STATUS_APPROVED:
        return "تایید شده"
    return "بدون نتیجه"


# --------------------------------------------------------------------------- #
# The company timeline — writing to it
# --------------------------------------------------------------------------- #
def log_client_event(client: Client, actor, action: str, subject: str = "",
                     comment: str = "", subject_role: str = "") -> ClientEvent:
    """Write one immutable ``ClientEvent`` row — this app's ``cases.services.log``.

    A deliberate mirror of ``cases/services.py::log``, down to freezing the
    actor's display name and unit/role label at write time. It does not copy
    that logic: it calls ``cases.services._actor_snapshot`` DIRECTLY, the
    exact same function ``cases.services.log`` itself calls. Reusing it
    rather than reimplementing it is the whole point — that helper already
    knows about linked ``people.Person`` rows, vacant seat usernames that
    must never be frozen onto history, and the ``Substitute · ...`` prefix a
    Translate seat earns. A second copy here would drift from it the first
    time any of those rules changed, and a timeline whose two halves
    disagree about who someone was is worse than no timeline.

    The leading underscore on ``_actor_snapshot`` marks it private to the
    cases APP, not to the cases MODULE, and reading it is safe: it is a pure
    function of ``user``, it writes nothing, and ``cases/services.py`` is
    already imported here (as ``case_services``) for
    ``next_client_code()``. ``cases/services.py`` itself is not modified.

    ``subject`` is free text naming what the action was about (a label's
    Persian display name, another company's name, a person's name) and is
    frozen for the same reason the actor fields are — see
    ``marketing/models.py::ClientEvent``.

    ``subject_role`` is the optional SECOND frozen noun: the role ``subject``
    was connected AS, for the two CONNECTION actions. It is passed as already
    resolved display text (``FIELD_LABELS.get(...)``, done by the caller) rather
    than as a role key, because freezing is the whole point — see that model's
    docstring. Blank for every other action, and blank on every row written
    before the column existed.

    Raises whatever the database raises: this is the plain writer, for
    callers that WANT a failure to surface. The mutating services in this
    module deliberately do not use it directly — see ``_try_log`` below.
    """
    actor_name, actor_role_label = case_services._actor_snapshot(actor)
    return ClientEvent.objects.create(
        client=client,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        subject=subject or "",
        subject_role=subject_role or "",
        comment=comment or "",
        actor_name=actor_name,
        actor_role_label=actor_role_label,
    )


def _try_log(client: Client, actor, action: str, subject: str = "",
             comment: str = "", subject_role: str = "") -> None:
    """``log_client_event`` that can never break the operation it is recording.

    HISTORY IS SECONDARY TO THE FACT IT RECORDS. If tagging a company
    succeeds but writing the timeline row fails, the correct outcome is a
    tagged company with a gap in its history — not a 500 that leaves the
    user believing the tag was rejected when the tag itself is committed.
    Every mutating service below therefore logs through here, never through
    ``log_client_event`` directly.

    The inner ``transaction.atomic()`` is a SAVEPOINT, and it is the reason
    this swallow is actually safe. A bare ``except`` around a failed database
    write inside a caller's own atomic block would leave that transaction
    marked for rollback, so the very next query in the real operation would
    raise ``TransactionManagementError`` — the swallow would have converted a
    logging failure into a failure of exactly the thing it was trying not to
    break. Rolling back to a savepoint instead confines the damage to this
    row and leaves the surrounding transaction usable.
    """
    try:
        with transaction.atomic():
            log_client_event(client, actor, action, subject=subject,
                             comment=comment, subject_role=subject_role)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# A case's own business-role change — logged on the CASE'S CLIENT's timeline
# --------------------------------------------------------------------------- #
def log_case_role_change(case: Case, actor, old_label: str, new_label: str) -> None:
    """Record, on ``case.client``'s OWN COMPANY TIMELINE, that this case's
    business role (``cases.models.Case.marketing_label`` — the ONE thing that
    decides which (client, role) pair the case anchors to on the chart; see
    ``_effective_label``) changed from ``old_label`` to ``new_label``.

    THE ONLY CALLERS ARE ``cases/views.py``'s TWO EDIT-SAVE PATHS, and both
    are expected to call this ONLY after they have already established, via
    ``effective_marketing_label``, that the role genuinely changed — this
    function does not re-check that itself and will happily write a
    (technically true but useless) "changed from OWNER to OWNER" row if a
    caller hands it two equal labels. That is a deliberate division of
    labour, not an oversight: the "did it really change" question needs the
    BEFORE value, which only the view (holding the in-memory ``Case`` before
    it overwrites the field) ever has; by the time anything here could look,
    the change has already happened.

    ``old_label``/``new_label`` are EFFECTIVE labels (already normalized
    through ``effective_marketing_label`` by the caller — e.g. ``"owner"``,
    never ``""``), not raw ``Case.marketing_label`` values, so
    ``FIELD_LABELS.get(...)`` below always resolves to real display text
    instead of silently falling back to an empty-string key.

    WHERE EACH FACT GOES ON THE ``ClientEvent`` ROW, and why: ``subject``
    carries the case's OWN document number, frozen at write time, through the
    identical mechanism ``add_report`` already established for
    ``REPORT_ADDED`` — see that function's own comment. Freezing it there,
    rather than inventing a new frozen slot, is what lets it ride the SAME
    read-time redaction ``client_timeline`` already applies to every
    ``ClientEvent`` row (``_redact_case_numbers``, which inspects ``subject``
    generically, by action-agnostic design): a viewer ``case_access_for``
    would refuse this case to reads this row with an empty ``subject``,
    exactly as they already would for a redacted report. ``subject_role``
    carries the frozen "OLD → NEW" text as one string (not split across
    ``subject_role``/``comment`` the way ``CONNECTION_ADDED`` splits its two
    nouns) — there is no natural "primary noun" here the way another
    company's name is for a connection, so one combined, already-formatted
    slot the template prints verbatim is simpler than two that would have to
    be stitched back together at render time. ``comment`` is left blank: it
    would otherwise duplicate ``subject_role``'s text in the timeline's own
    separate ".tl-comment" note, which is written once, in
    ``company_detail.html``, for every action rather than per action kind.

    Routed through ``_try_log`` like every other write in this module, for
    the same reason: a case whose ROLE FIELD saved correctly must never come
    back as a 500 because its timeline row failed to write.
    """
    old_fa = FIELD_LABELS.get(old_label, old_label)
    new_fa = FIELD_LABELS.get(new_label, new_label)
    doc_no = (getattr(case, "doc_no", "") or "").strip()
    _try_log(
        case.client, actor, ClientEventAction.CASE_ROLE_CHANGED,
        subject=doc_no,
        subject_role=f"{old_fa} → {new_fa}",
    )


# --------------------------------------------------------------------------- #
# The client directory itself
# --------------------------------------------------------------------------- #
def search_clients(query: str = "", limit: int | None = 25):
    """Clients whose name contains ``query`` (case-insensitive), by name.

    GLOBAL and UNSCOPED — every Marketing viewer (Expert, Supervisor, or the
    view-only GM) searches the SAME shared ``cases.Client`` directory
    Commercial uses; hiding a real registered company's mere existence from
    a colleague would contradict the shared-directory design. Returns
    ``Client`` model instances, not dicts — callers serialize.

    ``limit=None`` means UNCAPPED — both branches below slice with
    ``[:limit]``, and a plain list/queryset both treat a ``None`` stop as "no
    bound", so this is the same code path, not a special case. ``client_search``
    (the "+ Add company" panel's own endpoint) and ``companies_for_label``'s
    own name-match call both rely on this to show the FULL directory rather
    than a truncated head of it — see each caller's own comment.

    Persian/Arabic letter variants (e.g. Arabic ke/ye vs. Persian keheh/ye)
    must compare as equal, so a plain ``icontains`` (which is byte/codepoint
    exact) is not enough on its own — see ``core.persian_text``. The table is
    small (hundreds of rows, confirmed against production), so the simplest
    correct fix at this scale is a Python-side filter over every row rather
    than chaining SQL REPLACE() calls; this does not scale to a huge table,
    but the signature is unchanged, so a future move to DB-side
    normalization is a pure internal swap.
    """
    query = (query or "").strip()
    if not query:
        return list(Client.objects.all().order_by("name")[:limit])
    needle = normalize_persian(query).lower()
    matches = [
        c for c in Client.objects.all()
        if needle in normalize_persian(c.name).lower()
    ]
    matches.sort(key=lambda c: c.name)
    return matches[:limit]


def _find_by_normalized_name(needle: str):
    """The first ``Client`` whose normalized name equals ``needle`` (already
    itself a ``normalize_persian(...).lower()`` result), or ``None``.

    Same Python-side scan-and-compare ``search_clients`` above uses — the
    table is small (hundreds of rows), so one pass here is cheap and avoids
    N+1 queries; not reinvented, just reused for equality instead of
    substring containment.
    """
    return next(
        (c for c in Client.objects.all() if normalize_persian(c.name).lower() == needle),
        None,
    )


def get_or_create_client(name: str, user) -> Client:
    """The client named ``name`` — an existing NEAR-DUPLICATE if one already
    exists, or freshly registered exactly the way ``cases/views.py::client_add``
    already does: a sequential code from ``cases.services.next_client_code()``
    and ``created_by`` stamped to ``user``.

    Matches by NORMALIZED name first, using ``normalize_persian`` (see
    ``core.persian_text``) the same scan-and-compare way ``search_clients``
    above already does over the whole table: this folds Persian/Arabic
    letter variants AND strips all whitespace, so two names that differ only
    by which keyboard typed a shared letter, or by spacing (extra/irregular
    spaces, or a space missing entirely between two words), are treated as
    the SAME company. This subsumes a plain case-insensitive exact match
    (``cases.forms.ClientForm.clean_name``'s check, and the reason
    ``Client.name``'s database-level uniqueness — case-sensitive, since
    sqlite has no case-insensitive collation here — could otherwise let
    "Foolad Sanat" and "foolad sanat" become two rows), so that check is not
    duplicated separately. The whole point of "get or create" is that this
    is transparent to the caller: an existing near-duplicate genuinely IS
    the client they meant, so this returns it rather than creating a second
    row or raising. Only when no existing client's normalized name matches
    is a new row created. One pass over the (small, hundreds-of-rows) Client
    table — see ``_find_by_normalized_name`` — no per-row query, so no N+1.

    If two concurrent requests both miss that check and race to create the
    same (or near-duplicate) name, the loser's ``IntegrityError`` (raised by
    ``Client.name``'s unique constraint) is caught and resolved by
    re-reading the winner's row via the same normalized comparison — the
    same race ``client_add`` already tolerates implicitly via
    ``next_client_code()``'s own atomic counter, just made explicit here
    since this path (unlike ``client_add``'s own form-backed one) can be
    called concurrently from independent JSON requests without a form
    re-render in between to catch it.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A name is required.")
    needle = normalize_persian(name).lower()
    existing = _find_by_normalized_name(needle)
    if existing is not None:
        return existing
    actor = user if getattr(user, "is_authenticated", False) else None
    try:
        with transaction.atomic():
            client = Client.objects.create(
                name=name, code=case_services.next_client_code(), created_by=actor,
            )
        # Logged ONLY on this branch — the one that genuinely registers a new
        # company. Both of the "found an existing near-duplicate" returns
        # (the fast path above and the race-loser path below) are reads, not
        # registrations: writing a CLIENT_REGISTERED row there would claim a
        # company was created every time somebody merely typed its name into
        # the Attach flow, which is exactly the noise a timeline dies of.
        _try_log(client, user, ClientEventAction.CLIENT_REGISTERED, subject=client.name)
        return client
    except IntegrityError:
        existing = _find_by_normalized_name(needle)
        if existing is not None:
            return existing
        raise


# --------------------------------------------------------------------------- #
# Manual tags
# --------------------------------------------------------------------------- #
def toggle_manual_label(client: Client, label: str, user, add: bool, *,
                        elevated: bool = False) -> None:
    """Add or remove a manual tag on ``client`` — THIS USER's own, unless
    ``elevated``.

    ``add=True`` gets-or-creates ``ClientLabel(client, label, created_by=user)``
    — idempotent, safe to call when the tag already exists. ``elevated`` has no
    bearing on this branch: creating a tag was never ownership-gated to begin
    with, so there is nothing here for it to bypass.

    ``add=False`` deletes ONLY the row owned by ``user`` — UNLESS ``elevated``
    IS TRUE, in which case the ``created_by`` filter is dropped entirely and
    ANY row for ``(client, label)`` goes, whoever created it. This is the
    owner's explicit instruction for the Marketing Supervisor ("دستش بازه
    کاملا" — completely unrestricted): "an Expert can never remove a
    Supervisor's or another Expert's manual tag this way" remains exactly
    true for an ordinary Expert (``elevated=False``, the default — every
    existing caller that never passes it keeps behaving precisely as before),
    while a genuinely elevated caller is the one population the owner named as
    exempt from that boundary. ``elevated`` is never derived here — this
    module stays request-agnostic, same as ``scope`` — the caller passes
    ``marketing/access.py::Access.can_manage_config``, the same flag already
    established for "Supervisor and admin, not an ordinary editor" (see that
    field's own docstring). A no-op (not an error) when no matching row
    exists at all, elevated or not.

    Never touches case-derived labels — those have no ``ClientLabel`` row to
    begin with; see ``companies_for_label`` / ``connections_of_client`` for
    how the two sources are merged for display.

    Writes a ``ClientEvent`` (see ``_try_log``) on BOTH directions, but only
    when a row actually changed hands: this function is documented above as
    idempotent in both directions, and an idempotent no-op is not an event.
    Re-adding a tag the user already holds, or "removing" one they never had,
    must leave the timeline untouched — otherwise a double-clicked button
    reads back as two separate business decisions. ``get_or_create``'s
    ``created`` flag and ``delete()``'s row count are exactly the signals
    needed, and both come free with the write that already happens. On an
    elevated removal of SOMEONE ELSE's row, the ``LABEL_REMOVED`` event is
    still logged with ``actor=user`` (the Supervisor who acted) — the
    timeline records who DID the removal, not who originally added the tag,
    exactly like every other action this module logs.
    """
    if add:
        _obj, created = ClientLabel.objects.get_or_create(
            client=client, label=label, created_by=user,
        )
        if created:
            _try_log(client, user, ClientEventAction.LABEL_ADDED,
                     subject=FIELD_LABELS.get(label, label))
    else:
        qs = ClientLabel.objects.filter(client=client, label=label)
        if not elevated:
            qs = qs.filter(created_by=user)
        removed, _detail = qs.delete()
        if removed:
            _try_log(client, user, ClientEventAction.LABEL_REMOVED,
                     subject=FIELD_LABELS.get(label, label))


# --------------------------------------------------------------------------- #
# Connections — directed, role-to-role edges between two clients
# --------------------------------------------------------------------------- #
# See ``marketing/models.py``'s module docstring (the "UPDATE — the link
# concept is back, deliberately" section) and ``Connection``'s own docstring
# for the full reasoning behind this shape. The short version: this is what
# the chart's Attach flow creates when a user attaches one card's client to
# ANOTHER company under a different card, and it is deliberately narrower
# than the old, removed ``EntityLink`` — scoped to one (client, role) pair on
# each end, never a bare arbitrary link between two names.
def _connection_role_label(role: str) -> str:
    """One role key as the frozen display text a timeline row stores.

    ``FIELD_LABELS.get(role, role)`` — never ``FIELD_LABELS[role]`` — so a row
    can still be written for a role key this module no longer recognises,
    instead of the logging attempt raising inside the real operation it is only
    meant to be recording. Frozen at write time for the reason every other value
    on a ``ClientEvent`` is (see ``marketing/models.py::ClientEvent``): renaming
    a role's Persian display text later must never rewrite what an old row says
    happened.
    """
    return FIELD_LABELS.get(role, role)


# The separator ``_connection_comment`` joins a comment's nouns with, and the
# one ``_redact_case_numbers`` splits them back apart on at read time. ONE
# constant rather than two literals: the reader of a timeline row and the writer
# of it have to agree about where one frozen noun ends and the next begins, and
# a comment written with " · " but split on "·" would keep a document number
# glued to the role beside it and hand it to a viewer who may not see the case.
_COMMENT_NOUN_SEP = " · "


def _connection_comment(anchor_role: str, case=None) -> str:
    """The "from which of THIS company's roles, and under which case" note
    frozen onto a connection's timeline row.

    A ``Connection`` is four facts (two clients, two roles) plus an optional
    case, and a ``ClientEvent`` now has two frozen noun slots for them: the
    OTHER company's name goes in ``subject`` (the thing a reader scanning a
    timeline is actually looking for) and the role that company was connected
    AS goes in ``subject_role``, so the template can print it as its own tag.
    What is left is the ANCHOR side — which of this company's own roles the edge
    was drawn from — plus the case number, and those are the remaining context,
    so they stay in ``comment``, rendered here once rather than at each of the
    two call sites.

    THE TARGET ROLE USED TO BE IN HERE TOO, as the second half of an
    "anchor → target" string, and the owner asked for it to come out: a
    connection's timeline entry has to read "Connected this company to X as
    ROLE" with the role as a chip, not as a fragment buried in a note under the
    sentence. It moved to its own frozen column rather than being reconstructed
    at render time — see ``ClientEvent.subject_role``. Rows written before that
    change keep their original arrow text in this field and still render exactly
    as they always did; nothing rewrites them.

    THE DOCUMENT NUMBER IS STILL WRITTEN, and deliberately so even though a
    document number is case identity: this is an audit row, and the people who
    may see that case must be able to read which case a connection was drawn
    under. Who may READ it is decided when the timeline is rendered, by
    ``_redact_case_numbers`` — see that function for why the gate has to be at
    read time. The joiner is ``_COMMENT_NOUN_SEP``, which is the same constant
    that reader splits on.
    """
    anchor_fa = _connection_role_label(anchor_role)
    doc_no = (getattr(case, "doc_no", "") or "").strip() if case is not None else ""
    return f"{anchor_fa}{_COMMENT_NOUN_SEP}{doc_no}" if doc_no else anchor_fa


def _client_holds_label(client: Client, label: str) -> bool:
    """Whether ``client`` ALREADY, effectively holds ``label`` right now —
    the merge-time question ``companies_for_label``/``connections_of_client``
    answer per label/per client, asked here as a single unscoped yes/no fact
    rather than a display row.

    UNSCOPED ON PURPOSE, unlike the manual half of every OTHER read in this
    module. Everywhere else, "does a manual ``ClientLabel`` count" depends on
    ``(user, scope)`` because that half of the merge is about WHO MAY SEE the
    row (see the module docstring's "Manual ``ClientLabel`` rows are the one
    thing that stays scoped" paragraph). This function asks a different
    question — not "should THIS viewer be shown this label" but "is there
    already ANY row, from ANY source, making this a true fact about the
    client" — because it exists purely to gate a WRITE (see
    ``create_connection`` below), and a write must not duplicate a fact that
    is already real just because the user confirming a connection is not the
    same Marketing user who tagged it, or is not in the same case's access
    list. A case-derived hold is unscoped everywhere already (case data is
    shared truth); a manual hold is checked here across every ``created_by``,
    not just ``user``'s own, for the identical reason.

    Two cheap existence queries, short-circuited: the case check runs first
    since case data is looked up here the same way ``_case_label_query``
    already expresses it, and most calls need at most one of the two.
    """
    if Case.objects.filter(_case_label_query(label), client=client).exists():
        return True
    return ClientLabel.objects.filter(client=client, label=label).exists()


def create_connection(anchor_client: Client, anchor_role: str, target_client: Client, target_role: str, user, case=None) -> None:
    """Get-or-create the directed edge (anchor_client, anchor_role) ->
    (target_client, target_role), optionally scoped to ``case``.

    Idempotent, like ``toggle_manual_label(..., add=True)``: calling this
    again for the exact same five-tuple (see ``Connection.Meta.constraints``)
    is a no-op, not a duplicate row or an error — the chart's Attach flow can
    be re-opened and re-submitted without special-casing "already attached".

    Both roles are validated with ``is_label()`` — the same helper
    ``marketing/views.py::_label_or_400`` already uses at the HTTP layer for
    a plain manual tag — and an invalid one raises ``ValueError``, matching
    this file's existing error-handling convention (see
    ``get_or_create_client``'s "A name is required." raise for the other
    example of it). Validating here, not just at the view, keeps this
    function safe to call from anywhere else in the codebase later without
    silently accepting a role key that does not actually exist.

    ``created_by`` is stamped on first creation only (it is deliberately not
    part of the uniqueness constraint, and re-attaching the same edge as a
    different user does not steal ownership of the existing row) — see
    ``Connection``'s own docstring for why ownership is tracked but not part
    of "is this the same fact".

    CONFIRMING A CONNECTION ALSO REGISTERS THE TARGET'S ROLE, and this is the
    piece that makes "connect a company under role X, then browse role X's
    own card" actually show it. A ``Connection`` alone never changed which
    labels ``target_client`` DIRECTLY holds (see the module docstring's "A
    THIRD mechanism" paragraph) — it only decided who is displayed under the
    ANCHOR's card. But ``companies_for_label`` — what backs browsing a role
    card directly, independent of any anchor — has only ever unioned
    ``ClientLabel`` rows and case-derived rows; it has never looked at
    ``Connection`` at all, and widening it to do so is a much bigger, riskier
    change than the one asked for here. So instead, the moment a NEW
    connection genuinely GIVES the target a role it did not already hold,
    that fact is made real the same way a human ticking the target's own
    manual-tag checkbox would make it real: a ``ClientLabel`` row, added
    through the exact same mechanism (``toggle_manual_label``) that checkbox
    already calls, so it needs no new merge logic anywhere and shows up on
    every existing manual-label read path for free.
    ``_client_holds_label`` is the gate — see its own docstring for why it is
    unscoped — and it is checked only when a connection was genuinely NEW
    (``created``), never on a re-submit of an already-existing edge: this is
    "did this action just grant a role", not "keep granting it every time the
    Attach flow is reopened". A target that already effectively holds the
    role (via a case, or via any user's prior manual tag) is left exactly as
    it was — nothing here ever touches ``ClientLabel`` rows that already
    exist, and there is no "undo" on the other side of this (see
    ``remove_connection``'s docstring for why removing the edge later does
    not revoke this).
    ``toggle_manual_label`` stamps ``created_by=user`` — the person who
    confirmed the connection — and, since it is the one and only place a
    ``ClientLabel`` is ever created, it ALSO writes the ``LABEL_ADDED``
    ``ClientEvent`` on ``target_client``'s own timeline itself; nothing here
    logs a second time for the same fact, matching this whole file's rule of
    one row per genuine change (see ``toggle_manual_label``'s own docstring).
    """
    if not is_label(anchor_role):
        raise ValueError(f"Unknown label: {anchor_role!r}.")
    if not is_label(target_role):
        raise ValueError(f"Unknown label: {target_role!r}.")
    _obj, created = Connection.objects.get_or_create(
        anchor_client=anchor_client, anchor_role=anchor_role,
        target_client=target_client, target_role=target_role, case=case,
        defaults={"created_by": user},
    )
    if created:
        # Logged on the ANCHOR company only — the one whose card the Attach
        # flow was opened on, and whose timeline a reader would go looking
        # for this in. A Connection is directed (see ``Connection``'s own
        # docstring); writing a second row on the target would assert a
        # mirror fact this model deliberately does not create.
        #
        # Only on ``created``, for the same reason ``toggle_manual_label``
        # only logs on a real change: this function is documented as
        # idempotent, and re-submitting the Attach flow is not a new
        # business event.
        _try_log(anchor_client, user, ClientEventAction.CONNECTION_ADDED,
                 subject=target_client.name,
                 subject_role=_connection_role_label(target_role),
                 comment=_connection_comment(anchor_role, case))
        # The TARGET's own role registration — see this function's docstring
        # ("CONFIRMING A CONNECTION ALSO REGISTERS THE TARGET'S ROLE") for the
        # full reasoning. Gated on ``_client_holds_label`` so a target that
        # already carries the role (case-derived or manually, by anyone) is
        # left untouched — this only ever ADDS a fact that was not already
        # true, never re-asserts one that was.
        if not _client_holds_label(target_client, target_role):
            toggle_manual_label(target_client, target_role, user, add=True)


def remove_connection(anchor_client: Client, anchor_role: str, target_client: Client, target_role: str, case, user, *,
                      elevated: bool = False) -> None:
    """Delete the directed edge (anchor_client, anchor_role) ->
    (target_client, target_role) scoped to ``case`` — a row THIS USER
    themselves created, unless ``elevated``.

    The exact same "cannot remove what you don't own" rule
    ``toggle_manual_label(..., add=False)`` already enforces for
    ``ClientLabel``, applied here identically, INCLUDING ITS ``elevated``
    ESCAPE HATCH: an Expert can never remove a connection a Supervisor or
    another Expert attached (``elevated=False``, the default — unchanged for
    every existing caller), but a caller passing ``elevated=True`` — the
    Marketing Supervisor or the platform admin, via
    ``marketing/access.py::Access.can_manage_config`` — deletes the row
    regardless of who created it, per the owner's explicit instruction that a
    Supervisor's access to fixing connections is unrestricted. See
    ``toggle_manual_label``'s own docstring for the fuller argument; it is not
    repeated twice. A no-op (not an error) when no such row exists at all, or
    — for a non-elevated caller — when it exists but belongs to someone else;
    the caller cannot distinguish "gone" from "not yours" from this
    function's return value alone, by design, same as ``toggle_manual_label``.

    Writes a ``ClientEvent`` (see ``_try_log``) only when a row was actually
    deleted, on the ANCHOR company — the same two rules ``create_connection``
    above logs by, for the same reasons: a no-op is not a business event, and
    a directed edge belongs to the timeline of the company it was drawn from.
    ``delete()``'s own row count is the signal, and it comes free with the
    write that already happens. ``actor=user`` is the person who removed it —
    the Supervisor, on an elevated removal of someone else's edge — not
    whoever originally attached it, exactly as ``toggle_manual_label``
    documents for its own elevated branch.

    DELIBERATELY NOT SYMMETRIC WITH ``create_connection``'s TARGET-ROLE
    REGISTRATION. Confirming a connection can, per that function's docstring,
    give ``target_client`` a manual ``ClientLabel`` it did not already carry.
    Deleting the edge afterward does NOT remove that label — the owner
    described the grant as one-directional ("a role, once given, stays"), and
    the label is by then an independent, freestanding fact: the target may
    since have picked up the same role through a case, or the connection may
    simply not have been the only reason it deserved the tag in the first
    place. This function only ever deletes the ``Connection`` row itself;
    un-tagging a company's manual label remains exclusively the job of the
    existing manual ``label_toggle`` UI (``toggle_manual_label(...,
    add=False)``), which is the one place that already knows how to check
    ownership of a ``ClientLabel`` row before removing it — and, since this
    round, the one place that already knows how an elevated caller bypasses
    that check, identically to this function.
    """
    qs = Connection.objects.filter(
        anchor_client=anchor_client, anchor_role=anchor_role,
        target_client=target_client, target_role=target_role, case=case,
    )
    if not elevated:
        qs = qs.filter(created_by=user)
    removed, _detail = qs.delete()
    if removed:
        _try_log(anchor_client, user, ClientEventAction.CONNECTION_REMOVED,
                 subject=target_client.name,
                 subject_role=_connection_role_label(target_role),
                 comment=_connection_comment(anchor_role, case))


def contributor_count(client: Client) -> int:
    """How many DISTINCT people have ever added a manual role tag or a
    connection that involves ``client`` — the company detail page's own
    "Company" card wants this, unscoped, next to the plain Name/Code facts.

    AN UNSCOPED, COMPANY-WIDE FACT, NOT A VISIBILITY QUESTION — and that is a
    deliberate contrast with ``labels_for_clients``/``connections_of_client``
    right above it. Those two decide which MANUAL rows a given viewer may be
    SHOWN, because ``(user, scope)`` is a real security boundary for the list
    itself (see the module docstring's "Manual ``ClientLabel`` rows are the
    one thing that stays scoped" paragraph). This is not a list at all — it
    is one integer describing the company's own history, the same kind of
    fact ``case_status_counts`` already reports unscoped ("the company's
    whole history"), and every viewer who can open the company page at all is
    told the same number, exactly the way every viewer sees the same
    case-derived facts.

    TWO TABLES, ONE UNION OF ``created_by`` IDS:

    * :class:`ClientLabel` rows ON THIS CLIENT (``client=client``) — someone
      manually tagged this company with a role.
    * :class:`Connection` rows this client appears in on EITHER end
      (``anchor_client=client`` OR ``target_client=client``). The owner's own
      wording is "added ... a role/connection TO this company", naming the
      fact rather than the direction the edge happens to have been drawn in —
      a connection this company is the TARGET of (someone opened a DIFFERENT
      company's card and attached it here) is exactly as real a contribution
      to this company's own record as one drawn from its own card as the
      anchor, so both sides count. (Contrast ``_client_holds_label``, which
      also reasons about "does this client hold X" unscoped by owner, for the
      same kind of company-wide-fact reason.)

    ``created_by`` is nullable on both models (``SET_NULL`` when the user who
    added the row is later deleted) — those rows contribute nothing to a
    DISTINCT PEOPLE count (``None`` is not a person), so ``created_by__isnull``
    rows are dropped before the two id sets are unioned in Python. Three
    cheap ``values_list`` queries total, none of them touching a model
    instance, regardless of how much history this company has.
    """
    label_users = set(
        ClientLabel.objects
        .filter(client=client, created_by__isnull=False)
        .values_list("created_by_id", flat=True)
    )
    connection_users = set(
        Connection.objects
        .filter(Q(anchor_client=client) | Q(target_client=client),
                created_by__isnull=False)
        .values_list("created_by_id", flat=True)
    )
    return len(label_users | connection_users)


# --------------------------------------------------------------------------- #
# Case-derived facts (unscoped — shared truth)
# --------------------------------------------------------------------------- #
def cases_for_client(client: Client) -> list:
    """Every case ``client`` has, any status, with each case's effective label.

    UNSCOPED: case data belongs to Commercial/Technical/Supply, not to
    Marketing, so every viewer sees the same list. Terminal cases (burned,
    cancelled, ...) are included on purpose — see the module docstring.

    Returns, e.g.::

        [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "sub",
          "status": "FINAL_CLOSED", "status_fa": "تایید شده"},
         {"case_id": 55, "doc_no": "TE-2603-012-KA", "label": "owner",
          "status": "WITH_TECHNICAL", "status_fa": "بدون نتیجه"}]

    ``label`` is ``case.marketing_label`` or, when that field was left
    blank, ``"owner"`` (the blank-defaults-to-owner rule). ``status`` is the
    raw ``cases.constants.CaseStatus`` value; ``status_fa`` is that status
    bucketed into the three outcomes the "cases connected to Us" panel shows
    (see ``_status_fa``) — sent pre-bucketed so the panel never has to know
    the full status list itself.
    """
    cases = Case.objects.filter(client=client).only("id", "doc_no", "marketing_label", "status")
    return [
        {
            "case_id": c.pk, "doc_no": c.doc_no, "label": _effective_label(c),
            "status": c.status, "status_fa": _status_fa(c.status),
        }
        for c in cases
    ]


def all_cases() -> list:
    """Every case in the system, any client, any status — the platform-wide
    counterpart to :func:`cases_for_client` immediately above, added for a
    picker that has no single company in scope at all. See
    ``marketing/views.py::_visible_case_rows_all``, its only caller: the new
    "My Tasks" surface a later stage builds reaches this page with no client
    in its own URL, so it cannot narrow to one company's cases the way the
    existing company-page pickers do — it has to offer every case the viewer
    is allowed to see, platform-wide, and let picking one imply which company
    it belongs to.

    THE SAME PER-ROW SHAPE AS ``cases_for_client``, PLUS TWO EXTRA KEYS.
    ``case_id``/``doc_no``/``label``/``status``/``status_fa`` are computed
    identically — the very same ``_effective_label``/``_status_fa`` calls —
    so a reader that already knows how to render a ``cases_for_client`` row
    needs to learn nothing new to render one of these. ``client_id`` and
    ``client_name`` ride along on top of that shape, not in place of it: a
    row with no client in scope at all is useless to a reader unless the row
    itself says which company it belongs to, and ``cases_for_client``'s own
    callers never needed that key because the client was already the thing
    they had picked before asking for its cases.

    UNSCOPED, EXACTLY LIKE ``cases_for_client`` — case data belongs to
    Commercial/Technical/Supply, not to Marketing, so this returns the
    identical list to every viewer regardless of who is asking. Narrowing it
    to what ONE particular viewer may actually see is
    ``marketing/access.py::scope_case_rows``'s job, applied by the caller the
    same way it already is over ``cases_for_client``'s own output — this
    function is not a second, competing access rule, it is the same
    unscoped-fact-then-scope-it-at-the-call-site split applied to every case
    at once instead of one client's.
    """
    cases = Case.objects.select_related("client").only(
        "id", "doc_no", "marketing_label", "status", "client__name")
    return [
        {
            "case_id": c.pk, "doc_no": c.doc_no, "label": _effective_label(c),
            "status": c.status, "status_fa": _status_fa(c.status),
            "client_id": c.client_id, "client_name": c.client.name,
        }
        for c in cases
    ]


def label_counts(user, scope) -> dict:
    """``{label key: how many companies carry it}`` for all twenty keys.

    Two queries total (one grouped read over ``Case``, one over
    ``ClientLabel``), regardless of client count — this is what
    ``marketing/views.py::home()`` uses for the chart's badge numbers,
    replacing an earlier version that called ``companies_for_label`` once per
    key (twelve calls, each several queries — an N+1 pattern on a page every
    viewer loads).
    """
    counts: dict = {k: set() for k in LABEL_KEYS}
    for client_id, marketing_label in Case.objects.values_list("client_id", "marketing_label"):
        # The membership guard is defensive, and matches the manual loop just
        # below it. ``Case.marketing_label``'s own ``choices`` cannot currently
        # produce a key outside ``LABEL_KEYS``, but ``choices`` is not a
        # database constraint: a fixture, an import or a value left behind by a
        # renamed label would sail straight past it, and an unguarded
        # ``counts[...]`` would then raise ``KeyError`` and take down the
        # Marketing home page — the one page every viewer of this section
        # loads — over a single unrecognised row. Skipping it costs one company
        # from one badge; the alternative costs the whole chart.
        key = marketing_label or MarketingLabel.OWNER
        if key in counts:
            counts[key].add(client_id)
    for client_id, label in _scoped(ClientLabel.objects.all(), user, scope).values_list("client_id", "label"):
        if label in counts:
            counts[label].add(client_id)
    return {k: len(v) for k, v in counts.items()}


def labels_for_clients(client_ids, user, scope, *, elevated: bool = False) -> dict:
    """``{client_id: [{"label","label_fa","source","removable"}, ...]}`` for
    every id in ``client_ids`` — the same manual+case-derived merge
    ``connections_of_client`` does for one client, batched across many in a
    constant number of queries. Used by ``client_search`` so any caller
    listing companies can show label chips without a per-row round trip.

    ``removable`` is the same ``companies_for_label``/``connections_of_client``
    field, including the same ``elevated`` widening: True for THIS user's own
    manual row by default, or for ANY manual row on that client/label when
    ``elevated`` is the Marketing Supervisor or the platform admin (see
    ``marketing/access.py::Access.can_manage_config``) — see
    ``companies_for_label``'s docstring for the full reasoning.
    """
    client_ids = list(client_ids)
    if not client_ids:
        return {}

    case_labels: dict = {}
    for cid, marketing_label in Case.objects.filter(client_id__in=client_ids).values_list("client_id", "marketing_label"):
        case_labels.setdefault(cid, set()).add(marketing_label or MarketingLabel.OWNER)

    manual_labels: dict = {}
    for cid, label in _scoped(ClientLabel.objects.filter(client_id__in=client_ids), user, scope).values_list("client_id", "label"):
        manual_labels.setdefault(cid, set()).add(label)

    own_manual: dict = {}
    for cid, label in ClientLabel.objects.filter(client_id__in=client_ids, created_by=user).values_list("client_id", "label"):
        own_manual.setdefault(cid, set()).add(label)

    # See ``companies_for_label``'s identical ``removable_client_ids`` — queried
    # fresh, unscoped by ``created_by``, only when ``elevated`` is actually True
    # (an extra query no non-elevated caller — the overwhelming majority — pays
    # for).
    removable_map = own_manual
    if elevated:
        removable_map = {}
        for cid, label in ClientLabel.objects.filter(client_id__in=client_ids).values_list("client_id", "label"):
            removable_map.setdefault(cid, set()).add(label)

    out = {}
    for cid in client_ids:
        keys = case_labels.get(cid, set()) | manual_labels.get(cid, set())
        rows = []
        for label in LABEL_KEYS:
            if label not in keys:
                continue
            is_case = label in case_labels.get(cid, set())
            rows.append({
                "label": label,
                "label_fa": FIELD_LABELS[label],
                "source": "case" if is_case else "manual",
                "removable": label in removable_map.get(cid, set()),
            })
        out[cid] = rows
    return out


def us_connections(user) -> list:
    """Every client connected to "our own position" — i.e. every client with
    at least one case, any status.

    UNSCOPED, like every case-derived read (``user`` is accepted only so the
    call signature matches the rest of this module's ``(..., user)``
    functions and so a future scoping rule has somewhere to attach; case
    data itself is shared truth and is never filtered by owner). This is
    what the "us" chart card's modal list and Inquiry-on-Us both read.

    Returns, e.g.::

        [{"id": 3, "name": "Foolad Sanat Co.", "code": "014",
          "cases": [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "sub"}]}]

    Two queries total regardless of client count (one over ``Case``, one
    over ``Client``) — no N+1.
    """
    case_rows = Case.objects.only("id", "doc_no", "marketing_label", "client_id")
    by_client: dict = {}
    for c in case_rows:
        by_client.setdefault(c.client_id, []).append(
            {"case_id": c.pk, "doc_no": c.doc_no, "label": _effective_label(c)}
        )
    if not by_client:
        return []
    clients = Client.objects.filter(pk__in=by_client.keys()).order_by("name")
    return [
        {"id": cl.pk, "name": cl.name, "code": cl.code, "cases": by_client[cl.pk]}
        for cl in clients
    ]


def _case_search_row(case: Case) -> dict:
    """The one dict shape ``search_all_cases`` returns, for one case."""
    label = _effective_label(case)
    return {
        "case_id": case.pk,
        "doc_no": case.doc_no,
        "client_id": case.client_id,
        "client_name": case.client.name,
        "client_code": case.client.code,
        "label": label,
        "label_fa": FIELD_LABELS[label],
    }


def search_all_cases(query: str = "", limit: int = 500, case_id=None) -> list:
    """Every case matching ``query`` (by doc no. OR client name), newest
    first — or, when ``case_id`` is given, exactly that one case. This is what
    ``marketing/views.py::all_cases_search`` searches over, for BOTH of its
    callers: the "us" card's admin/GM-only all-cases browse, and the chart's
    "case mode" widget, which an ordinary Marketing Expert/Supervisor uses to
    point the chart at one specific case. That second caller is a deliberate
    widening made this round — see that view's own docstring, which records why
    an Expert/Supervisor reaching this search is core Marketing work rather than
    an admin/GM capability.

    UNSCOPED AT THIS LAYER, and that is NOT the last word on who sees what. It
    returns every matching case regardless of who created it, because there is
    no ``(user, scope)`` pair that means anything for case data (see the module
    docstring). WHICH of these rows a viewer may actually see is the separate
    ``marketing/access.py::case_access_for`` decision, applied by the VIEW
    through ``scope_case_rows`` — every row here carries ``case_id`` precisely
    so it can be. Do not call this function and hand its result straight to a
    template or a JSON response without that filter: these rows carry document
    numbers, which are case identity and not everyone's to read.

    ``case_id`` takes priority over ``query`` and short-circuits to an exact
    lookup — at most one result, an empty list if that case doesn't exist.
    Otherwise an empty ``query`` returns the newest ``limit`` cases
    unfiltered; a non-empty one narrows to cases whose ``doc_no`` OR client
    name contains it, using the identical Persian/Arabic letter-variant
    normalization ``search_clients`` above already uses (same
    ``normalize_persian`` helper, same Python-side substring approach, not
    reinvented here) so a query typed with Arabic-style ke/ye still matches a
    client name stored with the Persian keheh/ye, and vice versa.

    Returns dicts, not model instances, e.g.::

        [{"case_id": 41, "doc_no": "IN-2601-007-KA", "client_id": 3,
          "client_name": "Foolad Sanat Co.", "client_code": "014",
          "label": "sub", "label_fa": "پیمانکار جزء — SUBCONTRACTOR"}]

    ``label``/``label_fa`` use the same ``_effective_label`` /
    ``FIELD_LABELS`` this file already uses elsewhere (e.g.
    ``connections_of_client``), not case.marketing_label read directly, so
    the blank-defaults-to-owner rule applies here too.
    """
    if case_id is not None:
        case = Case.objects.select_related("client").filter(pk=case_id).first()
        return [] if case is None else [_case_search_row(case)]

    cases_qs = Case.objects.select_related("client").order_by("-created_at")
    query = (query or "").strip()
    if not query:
        return [_case_search_row(c) for c in cases_qs[:limit]]

    needle = normalize_persian(query).lower()
    matches = [
        c for c in cases_qs
        if needle in normalize_persian(c.doc_no).lower()
        or needle in normalize_persian(c.client.name).lower()
    ]
    return [_case_search_row(c) for c in matches[:limit]]


# --------------------------------------------------------------------------- #
# Merged reports (manual + case-derived together)
# --------------------------------------------------------------------------- #
def companies_for_label(label: str, user, scope, *,
                        case_access: CaseAccess = NO_CASE_ACCESS,
                        elevated: bool = False) -> list:
    """The companies a chart card shows when a label is clicked.

    Union of:

    * MANUAL — clients with a ``ClientLabel(label=label)`` row, SCOPED to
      ``user``/``scope`` (an Expert only sees their own tags; a Supervisor
      or the GM sees everyone's).
    * CASE-DERIVED — clients with >=1 case whose effective label (see
      ``cases_for_client``) equals ``label`` — UNSCOPED, every viewer sees
      this, because it is case data, not Marketing-owned data.

    A client that qualifies via BOTH sources appears ONCE. Shape, e.g.::

        [{"id": 3, "name": "Foolad Sanat Co.", "code": "014",
          "source": "case", "also_manual": True, "removable": True,
          "case_numbers": ["IN-2601-007-KA"]},
         {"id": 9, "name": "Pars Alloy", "code": "021",
          "source": "manual", "also_manual": False, "removable": True},
         {"id": 12, "name": "Delta Eng.", "code": "033",
          "source": "case", "also_manual": False, "removable": False,
          "case_numbers": ["TE-2603-012-KA", "TE-2603-018-KA"]}]

    Field meanings:

    * ``source`` — which source is DISPLAYED for this row; ``"case"`` wins
      over ``"manual"`` when both apply, since a case-derived fact is the
      more authoritative one. UNSCOPED by case access, on purpose: "this
      company holds this role because of a case" is the shared truth half (see
      the module docstring), and hiding it would make the company vanish from
      a card it genuinely belongs on.
    * ``case_numbers`` — the ``doc_no`` of every case that gave this client
      the label AND THAT THIS VIEWER MAY SEE, filtered through
      ``case_access``. Present (and non-empty) only when ``source == "case"``
      and at least one of those cases survives that filter; a row can
      therefore legitimately be ``source == "case"`` with no ``case_numbers``
      key at all, which is the honest rendering of "backed by a case you may
      not read". THE COUNT IS PER VIEWER, DELIBERATELY — the chart's per-card
      badge (``chart_interact.js``, which reads ``case_numbers.length``) can
      show a different number to two people looking at the same company, and
      that is correct and consistent with every other case list in this app.
      It is not a bug to "fix" by unscoping this: a document number is case
      identity, and the alternative is proving a label is real by printing a
      colleague's case number next to it.
    * ``also_manual`` — True when a manual ``ClientLabel`` row (within
      ``scope``) ALSO exists for this client/label, in addition to the
      case-derived fact that made ``source == "case"`` win. Purely
      informational (e.g. a small "+ manually tagged too" hint).
    * ``removable`` — True iff THIS USER (not just "within scope") owns a
      manual ``ClientLabel`` row for this client/label — i.e.
      ``toggle_manual_label(client, label, user, add=False)`` would actually
      delete something. This is independent of ``source``: a client shown
      with ``source == "case"`` can still be ``removable`` when this same
      user separately hand-tagged it too, which is exactly how the frontend
      lets someone un-tag their own manual copy without touching the
      case-derived truth underneath it.

      WHEN ``elevated`` IS TRUE — the Marketing Supervisor or the platform
      admin, via ``marketing/access.py::Access.can_manage_config`` — this
      widens from "owns" to "ANY manual row exists for this client/label,
      whoever created it", mirroring exactly what
      ``toggle_manual_label(client, label, user, add=False, elevated=True)``
      would actually delete (see that function's own docstring for the
      owner's instruction this implements). Still independent of ``source``,
      and still False for a client that is ``source == "case"`` with no
      manual row behind it at all — an elevated viewer gets to remove any
      EXISTING manual tag, not a tag that was never there to begin with.
      ``elevated`` never widens WHICH companies are listed (the union above),
      only whether a listed one's manual half may be removed.
    """
    # TWO DIFFERENT QUESTIONS OVER THE SAME QUERY, and they get two different
    # answers on purpose. WHICH COMPANIES hold this label is read from every
    # matching case (``case_client_ids``) — shared truth, unscoped. WHICH
    # DOCUMENT NUMBERS may be printed beside them is read from only the rows
    # ``case_access`` admits. Rows carry ``case_id`` so ``scope_case_rows`` can
    # do that in its one extra query, and it returns them otherwise untouched,
    # so ``client_id``/``doc_no`` survive the filter.
    case_rows = [
        {"case_id": c.pk, "doc_no": c.doc_no, "client_id": c.client_id}
        for c in Case.objects.filter(
            _case_label_query(label)
        ).only("id", "doc_no", "client_id")
    ]
    case_client_ids = {row["client_id"] for row in case_rows}
    case_numbers_by_client: dict = {}
    for row in scope_case_rows(case_rows, case_access):
        case_numbers_by_client.setdefault(row["client_id"], []).append(row["doc_no"])

    manual_client_ids = set(
        _scoped(ClientLabel.objects.filter(label=label), user, scope)
        .values_list("client_id", flat=True)
    )
    own_manual_client_ids = set(
        ClientLabel.objects.filter(label=label, created_by=user).values_list("client_id", flat=True)
    )
    # See the docstring's ``removable`` bullet — an elevated caller (Marketing
    # Supervisor or platform admin) may remove ANY manual row for this label,
    # not just their own, so the set that decides ``removable`` widens to
    # every client carrying one, regardless of ``created_by``. Queried fresh
    # rather than reusing ``manual_client_ids``: that set is already unscoped
    # whenever ``scope == "all"`` (which every real elevated caller has — see
    # ``access.access_for``), but this function's own contract must not rely
    # on that invariant holding at every call site to stay correct.
    removable_client_ids = (
        set(ClientLabel.objects.filter(label=label).values_list("client_id", flat=True))
        if elevated else own_manual_client_ids
    )

    all_client_ids = manual_client_ids | case_client_ids
    if not all_client_ids:
        return []

    clients = Client.objects.filter(pk__in=all_client_ids).order_by("name")
    results = []
    for client in clients:
        is_case = client.pk in case_client_ids
        is_manual = client.pk in manual_client_ids
        entry = {
            "id": client.pk,
            "name": client.name,
            "code": client.code,
            "source": "case" if is_case else "manual",
            "also_manual": is_case and is_manual,
            "removable": client.pk in removable_client_ids,
        }
        # ``.get`` and a truth test, not ``[client.pk]``: a company can be in
        # ``case_client_ids`` (it really does hold this label through a case)
        # while every one of those cases is outside this viewer's case access,
        # in which case the key is simply absent — see the docstring.
        visible_numbers = case_numbers_by_client.get(client.pk)
        if is_case and visible_numbers:
            entry["case_numbers"] = visible_numbers
        results.append(entry)
    return results


def connections_of_client(client: Client, user, scope, case=None, *,
                          case_access: CaseAccess = NO_CASE_ACCESS,
                          elevated: bool = False) -> dict:
    """The label/case report Inquiry shows when a COMPANY (not "us") is the focus.

    The mirror image of ``companies_for_label``: instead of "one label -> its
    companies", this is "one client -> its labels", using the identical
    merge rule and the identical per-entry field meanings for the labels
    ``client`` DIRECTLY holds (``source``, ``also_manual``, ``removable``,
    ``case_numbers`` — see ``companies_for_label``'s docstring for what each
    means, ``removable`` included — the same ``elevated`` widening applies
    here too: True for every directly-held entry with ANY manual row behind
    it, not just this user's own, when ``elevated`` is the Marketing
    Supervisor or the platform admin). On top of that, every entry now also
    carries who is actually
    CONNECTED under that label — see ``connected`` below, the whole point of
    this round's change and the piece the next phase builds its display on.

    ``case`` is optional and controls which ``Connection`` rows are visible:
    ``None`` shows only general (case-less) connections; a specific ``Case``
    ADDS that case's own case-scoped connections on top of the general ones
    — see ``Connection``'s own docstring for the full visibility rule. Every
    existing caller keeps working unchanged by simply not passing it.

    Returns, e.g. — the worked example being the owner's own: ``client`` is
    "Water & Sewage of East Azerbaijan Province", which holds "owner" (via a
    case) and has, through the chart's Attach flow, connected "Ofogh Novin
    Homa" as its Supervision Consultant::

        {"labels": [
            {"label": "owner", "label_fa": "کارفرمای اصلی — OWNER / CLIENT",
             "source": "case", "also_manual": False, "removable": False,
             "case_numbers": ["IN-2601-007-KA"],
             "connected": [{"id": 3, "name": "Water & Sewage of East Azerbaijan Province"}]},
            {"label": "supervision", "label_fa": "مشاور نظارت — SUPERVISION",
             "source": None, "also_manual": False, "removable": False,
             "connected": [{"id": 9, "name": "Ofogh Novin Homa"}]},
         ],
         "cases": [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "owner",
                    "status": "WITH_TECHNICAL", "status_fa": "بدون نتیجه"}]}

    ``labels`` still lists every label that applies to ``client`` in
    ``LABEL_KEYS`` order, but now in TWO different senses:

    * Labels ``client`` DIRECTLY holds (case-derived and/or manual, exactly
      as before — this half of the merge is UNCHANGED) get the usual
      ``source``/``also_manual``/``removable``/``case_numbers`` fields, plus
      ``connected``.
    * Labels that apply ONLY because a ``Connection`` targets them — i.e.
      ``client`` connected some OTHER company under that role through the
      chart's Attach flow, without ever holding the role itself — appear as
      NEW entries with ``source: None``, ``also_manual: False``,
      ``removable: False``, and no ``case_numbers`` key at all (there is no
      case-derived or manual fact behind these entries directly — only a
      connection). The owner's "supervision" example above is exactly this
      case: Water & Sewage never held Supervision itself, so this entry
      exists purely because of the Connection row. NOTE the direction this
      makes visible: ``connections_of_client("Ofogh Novin Homa")`` — i.e.
      running the SAME Inquiry from the OTHER name on that Connection row —
      must show a "supervision" entry of its OWN (Ofogh Novin Homa directly
      holds it, thanks to ``create_connection``'s target-role registration —
      see the module docstring's "A THIRD mechanism" paragraph) whose
      ``connected`` list contains "Water & Sewage of East Azerbaijan
      Province" right back. That is the REVERSE half of ``connected``,
      documented in full just below — the connection is one real-world fact
      and must be visible activating from either end, not only from the
      anchor Water & Sewage was drawn from.

    ``connected`` — a ``[{"id", "name"}, ...]`` list of the companies to
    actually display under this label's chart card, for EVERY entry:

    * For a DIRECTLY-HELD entry (the REVERSE half below is the fix this round
      makes — querying used to only ever surface a connection from the
      ANCHOR side), gather every ``Connection`` row that ties ``client`` to
      THIS entry's own label from EITHER end, since a ``Connection`` is one
      real-world fact and querying from either name it names must surface it:

      - FORWARD — ``client`` is the row's ``anchor_client``, in one of the
        roles ``client`` genuinely, directly holds (i.e. ``anchor_role`` must
        be a label from the DIRECTLY-HELD half above — a connection anchored
        on a role ``client`` does not actually hold is never surfaced), and
        ``target_role`` equals THIS entry's own label. This half is
        unchanged from before this round.
      - REVERSE — ``client`` is the row's ``target_client`` instead, under
        ``target_role`` equal to THIS entry's own label (gated the same way
        as the forward half: only a label ``client`` directly holds may be
        the label a reverse row is filed under, matching the "only ever
        applies to a DIRECTLY-HELD entry" rule two bullets down). The
        ANCHOR on the other end of such a row — some OTHER company that
        connected ``client`` to itself through the Attach flow — is exactly
        as "connected to client under this field" as a forward target is: it
        is the SAME kind of fact, read from ``client``'s side instead of the
        anchor's. Concretely, if activating A under "design" and attaching B
        as its Subcontractor-P wrote ``Connection(anchor_client=A,
        anchor_role="design", target_client=B, target_role="sub_p")``, then
        activating B under "sub_p" must show A here — B, as a sub_p
        contractor, IS connected to A under design, and that is one fact
        whichever end it is queried from.
      Both halves are scoped identically: ``case`` NULL (general — always
      visible) or equal to the ``case`` argument (case-scoped — visible only
      when Inquiry is running in that case's own context), and ``user``/
      ``scope`` exactly like manual ``ClientLabel`` rows (see ``_scoped``) —
      an Expert only sees connections THEY built, from either end, until a
      Supervisor/GM looks at the union, consistent with every other
      manually-created fact in this module. Removing a row surfaced from the
      reverse direction is still removing the SAME ``Connection`` row, with
      the SAME ``created_by`` — ``remove_connection`` filters on the row's
      real, unchanged ``(anchor_client, anchor_role, target_client,
      target_role)`` tuple regardless of which end a caller found it from, so
      ownership and effect are identical either way; nothing about which
      side surfaced a row changes who may remove it or what removing it does.
      A connection-only entry (``source is None``, two bullets down) stays
      FORWARD-ONLY and unaffected by any of this — see that bullet for why.
    * If any such rows exist (either half), ``connected`` is their DISTINCT
      OTHER clients (deduplicated by id, since more than one connection can
      legitimately point at the same other client — e.g. one general and one
      case-scoped row for the same pair, or a forward row and a reverse row
      that both happen to name the same company).
    * If none exist, ``connected`` falls back to ``client``'s own identity —
      ``[{"id": client.pk, "name": client.name}]`` — which is exactly
      TODAY's behaviour for a label with no explicit connection behind it: a
      company that simply also holds that role itself, with nobody else
      specifically connected through it. This fallback only ever applies to
      a DIRECTLY-HELD entry — a connection-only entry (``source is None``)
      never falls back to ``client``'s own identity, since ``client`` never
      held that role to begin with; its ``connected`` list is always the
      real connected target(s) (and such an entry only exists at all because
      at least one such target exists). A connection-only entry is also,
      necessarily, FORWARD-ONLY: it exists at all only because ``client`` is
      someone's anchor (see the bullet above and the module docstring's "A
      THIRD mechanism" paragraph) — there is no reverse half to add here
      since a reverse row would require ``client`` to directly hold this
      very label, which is precisely the condition that makes an entry
      DIRECTLY-HELD instead of connection-only.

    ``cases`` is ``cases_for_client(client)`` NARROWED BY ``case_access`` — the
    same list the caller would otherwise have to filter itself, kept under its
    own key so the "connected to Us via case NNNN" display doesn't have to
    re-derive it from ``labels``. It is filtered HERE, not by the view, so that
    nothing this function returns — neither key — can carry a document number
    the viewer may not read.

    AND, WHEN ``case`` IS GIVEN, NARROWED A SECOND TIME DOWN TO THAT ONE CASE
    — this is the "cases connected to Us" panel's OWN scoping rule, distinct
    from ``case_access`` and applied on top of it, never instead of it. The
    panel exists to answer "what is THIS Inquiry (running against THIS case)
    connected to", and before this narrowing it answered a different
    question — EVERY case this client has, permission allowing, regardless of
    which one (if any) case mode is actually running against; a client with
    several cases in different roles would show all of them even while the
    chart was anchored on exactly one. Scoped-by-``case`` and scoped-by-
    ``case_access`` compose in the only direction that is safe: this can only
    ever REMOVE rows ``case_access`` already let through, never add one back
    — a case this viewer may not see stays invisible whether or not it
    happens to be the active one. Every existing caller keeps working
    unchanged by simply not passing ``case``, exactly as the rest of this
    function's ``case`` handling already promises.

    ``case_access`` scopes DOCUMENT NUMBERS ONLY, never which labels appear.
    Which roles ``client`` holds is shared truth and stays unscoped (see the
    module docstring); ``case_numbers`` on an entry is the visible subset, and
    the key is absent when that subset is empty even though ``source`` is still
    ``"case"`` — identical to ``companies_for_label``'s rule, and see that
    docstring for why a per-viewer badge count is the correct outcome rather
    than an inconsistency.
    """
    all_case_rows = cases_for_client(client)
    case_rows = scope_case_rows(all_case_rows, case_access)
    # Label MEMBERSHIP from every case (shared truth); document NUMBERS from
    # only the visible ones. Two dicts rather than one, because conflating them
    # is exactly the leak this split exists to close.
    case_labels: set = {row["label"] for row in all_case_rows}
    case_numbers_by_label: dict = {}
    for row in case_rows:
        case_numbers_by_label.setdefault(row["label"], []).append(row["doc_no"])

    manual_labels = set(
        _scoped(ClientLabel.objects.filter(client=client), user, scope)
        .values_list("label", flat=True)
    )
    own_manual_labels = set(
        ClientLabel.objects.filter(client=client, created_by=user).values_list("label", flat=True)
    )
    # See ``companies_for_label``'s identical ``removable_client_ids`` — the
    # same elevated widening, queried fresh for the same reason (not reused
    # from ``manual_labels`` even though that set is already unscoped whenever
    # ``scope == "all"``, which every real elevated caller has).
    removable_labels = (
        set(ClientLabel.objects.filter(client=client).values_list("label", flat=True))
        if elevated else own_manual_labels
    )

    # Only a role client genuinely, directly holds (case-derived and/or
    # manual) may ever be the ANCHOR of a connection that gets surfaced here
    # — see the docstring's "connected" bullet list above. This is computed
    # before the Connection query below precisely so that query can filter
    # on it directly (``anchor_role__in=...``), rather than fetching every
    # connection anchored at ``client`` and discarding some after the fact.
    directly_held_labels = {
        label for label in LABEL_KEYS
        if label in case_labels or label in manual_labels
    }

    # Both directions get the identical case/scope filter — a helper so that
    # is enforced by construction rather than by two hand-kept-in-sync copies
    # of the same two ``.filter()`` calls.
    def _case_scoped(qs):
        if case is not None:
            return qs.filter(Q(case__isnull=True) | Q(case=case))
        return qs.filter(case__isnull=True)

    # FORWARD — ``client`` is the anchor. Unchanged from before this round:
    # see the docstring's "connected" bullet list above.
    fwd_qs = _scoped(
        _case_scoped(Connection.objects.filter(
            anchor_client=client, anchor_role__in=directly_held_labels,
        )),
        user, scope,
    ).select_related("target_client")

    # REVERSE — ``client`` is the TARGET instead, under a label it directly
    # holds. THE FIX: see the docstring's "REVERSE" bullet for why this half
    # was missing and what it means for a connection to be one real-world
    # fact visible from either name it names. Scoped by the exact same
    # ``_case_scoped``/``_scoped`` calls as the forward query above — a
    # reverse-surfaced row must never be less scoped than a forward one, since
    # it is the same kind of fact wearing the other end of the same row.
    rev_qs = _scoped(
        _case_scoped(Connection.objects.filter(
            target_client=client, target_role__in=directly_held_labels,
        )),
        user, scope,
    ).select_related("anchor_client")

    # {label: {other_client_id: other_client_name}} — an inner dict (not a
    # set of tuples) so the SAME other client reached via more than one
    # Connection row (a forward row and a reverse row both naming it, or two
    # rows in the same direction — e.g. one general and one case-scoped row
    # for the identical pair, deliberately allowed to coexist — see
    # ``Connection``'s own docstring) collapses into a single entry, per the
    # "DISTINCT" rule above. The forward row's key is its own ``target_role``;
    # the reverse row's key is ALSO its own ``target_role`` — that is the
    # label ``client`` (the target here) directly holds and the one this
    # entry is filed under. Built from two separately-ordered queries, so
    # (unlike the old single-query version) insertion order is no longer
    # trusted to already be name-sorted — the list below sorts explicitly.
    connected_by_role: dict = {}
    for row in fwd_qs:
        connected_by_role.setdefault(row.target_role, {})[row.target_client_id] = row.target_client.name
    for row in rev_qs:
        connected_by_role.setdefault(row.target_role, {})[row.anchor_client_id] = row.anchor_client.name

    labels_out = []
    for label in LABEL_KEYS:
        is_case = label in case_labels
        is_manual = label in manual_labels
        directly_held = is_case or is_manual
        # Sorted explicitly by name — see the merge comment above for why
        # insertion order alone (fine when a single ``order_by`` query fed
        # this dict) is no longer enough now that a forward and a reverse
        # query both feed it.
        connected_targets = [
            {"id": cid, "name": name}
            for cid, name in sorted(connected_by_role.get(label, {}).items(), key=lambda pair: pair[1])
        ]
        if not directly_held and not connected_targets:
            continue
        if directly_held:
            entry = {
                "label": label,
                "label_fa": FIELD_LABELS[label],
                "source": "case" if is_case else "manual",
                "also_manual": is_case and is_manual,
                "removable": label in removable_labels,
            }
            # See ``companies_for_label``'s identical guard: a label can be
            # case-derived while every case behind it is outside this viewer's
            # case access, and the honest answer is then no key at all.
            visible_numbers = case_numbers_by_label.get(label)
            if is_case and visible_numbers:
                entry["case_numbers"] = visible_numbers
            entry["connected"] = connected_targets or [{"id": client.pk, "name": client.name}]
        else:
            # A connection-only entry: client never held this role itself,
            # only connected some other company under it — see the
            # docstring's "connection-only" paragraph. No case-derived or
            # manual fact backs this row, hence no source/case_numbers.
            entry = {
                "label": label,
                "label_fa": FIELD_LABELS[label],
                "source": None,
                "also_manual": False,
                "removable": False,
                "connected": connected_targets,
            }
        labels_out.append(entry)

    # The panel's OWN narrowing, on top of (never instead of) ``case_access``
    # — see the docstring's "AND, WHEN ``case`` IS GIVEN" paragraph. Applied
    # here, at the very end, against ``case_rows`` (already permission-
    # filtered above) rather than against ``all_case_rows``, so this step can
    # only ever REMOVE a row ``case_access`` already let through — it can
    # never add back a case the viewer may not see, whether or not that case
    # happens to be the active one. ``case_numbers_by_label`` above is
    # deliberately built from the wider ``case_rows``, not this narrowed
    # view: document numbers on a label are "every visible case", while this
    # is purely about which row(s) the "cases connected to Us" panel lists.
    cases_out = case_rows
    if case is not None:
        cases_out = [row for row in case_rows if row["case_id"] == case.pk]

    return {"labels": labels_out, "cases": cases_out}


def _case_label_query(label: str):
    """The ``Case`` filter for "this case's effective label is ``label``".

    Blank ``marketing_label`` counts as OWNER (see the module docstring's
    blank-defaults-to-owner rule), so matching the OWNER label has to catch
    both the explicit choice and the blank default; every other label only
    ever matches its own exact, explicitly-chosen value.
    """
    if label == MarketingLabel.OWNER:
        return Q(marketing_label=MarketingLabel.OWNER) | Q(marketing_label="")
    return Q(marketing_label=label)


# --------------------------------------------------------------------------- #
# The company timeline — reading it
# --------------------------------------------------------------------------- #
def _scoped_events(qs, user, scope):
    """``qs`` of ``ClientEvent`` narrowed to ``user``'s own rows for "own".

    A SEPARATE helper from ``_scoped`` above, and deliberately not a
    generalisation of it: ``_scoped`` filters on ``created_by``, which is what
    every OWNED row in this app is stamped with, while a ``ClientEvent``
    records who ACTED (``actor``) — there is no ``created_by`` on it to filter,
    so ``_scoped`` would raise ``FieldError`` here rather than quietly do the
    wrong thing.

    WHY EVENTS ARE SCOPED AT ALL. A timeline row is not new information; it is
    a record OF the manual facts ``_scoped`` already governs. An Expert who
    cannot see another Expert's ``ClientLabel`` row must not be able to read
    "LABEL_ADDED · سرمایه‌گذار" off the timeline and learn it anyway — that
    would make the scope a UI nicety instead of the boundary the module
    docstring insists it is. Filtering on the actor reproduces the same
    boundary exactly: the rows an Expert can see are the rows recording their
    own actions, and a Supervisor/GM/admin ("all") sees the union of
    everyone's, precisely as they see the union of everyone's tags.

    ``CASE_ROLE_CHANGED`` IS EXEMPTED FROM THE "own" FILTER, AND THIS IS A
    DELIBERATE CARVE-OUT, NOT AN OVERSIGHT — worth spelling out because every
    other action this filter has ever governed shares one property that this
    one structurally does not. For LABEL_ADDED, CONNECTION_ADDED, CONTACT_ADDED
    and the rest, ``actor`` is ALWAYS a MARKETING user — the person who clicked
    the tag, drew the connection, added the contact — so "rows whose actor is
    me" and "rows recording MY OWN actions" are the exact same set, and the
    boundary above is real: it is genuinely reproducing who did what.
    ``CASE_ROLE_CHANGED``'s actor is ALWAYS a COMMERCIAL user instead —
    ``cases/views.py``'s two case-edit paths are its only callers (see
    ``log_case_role_change``), and Commercial and Marketing are different
    units by construction (``people.seats`` hardens every seat after the first
    into its own account). So for THIS action alone, ``actor=user`` is not
    "did I do this" narrowed to true — it is a comparison that can never be
    true for any Marketing viewer, own or otherwise, and applying it under
    "own" would silently hide the row from EVERY ordinary Marketing Expert
    while a Supervisor/GM/admin (scope "all", unfiltered) reads it freely. That
    is not what "own" has ever meant anywhere else in this module: nowhere
    else does it depend on which HUMAN happens to be reading rather than on
    who wrote the row being read.
    The owner's own instruction for this feature was that "the company's own
    timeline must record" a role change, with no mention of narrowing that to
    whichever scope the viewer happens to hold — and ``company_detail`` already
    gates entry to the page itself on ``access.can_view`` before ``scope`` is
    ever consulted (see ``marketing/views.py::company_detail``), so every
    reader reaching this list already has genuine, legitimate view access to
    THIS company. Withholding one specific row from an Expert that a
    Supervisor sees on the identical page would not be protecting anyone's
    manual work the way the actor filter protects a colleague's tag — it would
    only be an accident of this filter being reused for a fact it was never
    designed to gate. So a ``CASE_ROLE_CHANGED`` row is included unconditionally,
    in BOTH scopes, the same way the derived registration row already is (see
    ``_registration_entry``, unscoped for the identical reason: some facts on
    this timeline are not one Marketing user's private action to own).
    """
    if scope == "own":
        return qs.filter(Q(actor=user) | Q(action=ClientEventAction.CASE_ROLE_CHANGED))
    return qs


def _timeline_entry(kind: str, action: str, *, subject: str = "",
                    subject_role: str = "", comment: str = "",
                    actor_name: str = "", actor_role_label: str = "",
                    created_at=None) -> dict:
    """One row of ``client_timeline``, in the ONE shape every source produces.

    Every key is present on every entry, whichever source it came from — a
    derived registration row simply carries empty strings where a stored row has
    a comment or a role — so the template walks a single uniform list and never
    branches on where a row came from. ``kind`` records which source produced it
    (``"event"`` for a stored ``ClientEvent``, ``"client"`` for the derived
    registration row), for a caller that legitimately wants to tell them apart.

    THIS SHAPE USED TO CARRY ``case_id`` / ``doc_no`` / ``open_url`` TOO. All
    three existed for exactly one thing — the derived "Case NNNN was opened for
    this company" row, which linked through to the case — and the owner removed
    that row from the timeline outright. With nothing left to link to, the three
    keys were three empty values on every entry and one more shape for a reader
    to hold in their head, so they went with it. The Cases tab of the company
    detail page is where a company's cases are listed and linked
    (``marketing/views.py::_visible_case_rows``, which builds its own
    ``open_url`` through ``access.case_open_url`` and is untouched by any of
    this).

    ``actor_role_label`` is still carried even though the timeline no longer
    RENDERS it (the owner asked for the actor's name alone). It is a frozen
    audit value that ``ClientEvent`` keeps writing, and passing it through costs
    nothing while leaving it available to anything that later wants it — the
    decision not to print it belongs to the template, not to this shape.
    """
    return {
        "kind": kind,
        "action": action,
        "action_label": ClientEventAction.LABELS.get(action, action),
        "subject": subject or "",
        "subject_role": subject_role or "",
        "comment": comment or "",
        "actor_name": actor_name or "",
        "actor_role_label": actor_role_label or "",
        "created_at": created_at,
    }


def _registration_entry(client: Client) -> list:
    """The "who first added this company" row, DERIVED from ``cases.Client``.

    Returns a one-item list, or an empty one when the row must not be
    synthesised. Kept as a list rather than an ``Optional`` so the caller can
    concatenate it exactly like the other two halves of the timeline.

    WHY DERIVED, AND NOT A STORED ROW. The owner asked for the timeline to say
    who added a company and when. A real ``CLIENT_REGISTERED``
    ``ClientEvent`` is written by exactly one code path —
    ``get_or_create_client``, the chart's own Attach/"+ Add company" flow — so
    every company that predates this app, and every company Commercial creates
    through ``cases/views.py::client_add``, would otherwise have a timeline that
    never mentions its own registration. ``cases.models.Client`` has carried
    ``created_by`` and ``created_at`` all along, which is the fact itself, so
    this reads it at the moment the timeline is rendered, for two reasons: no
    backfill to run for history that already happened, and no second copy to
    keep in step. The cases app is untouched.

    THIS IS NOW THE TIMELINE'S ONLY DERIVED ROW. It used to sit beside a whole
    derived CASE half; the owner removed that half (see ``client_timeline``),
    and this one stays because it reports a MARKETING fact — who put this
    company in the directory — that simply happens to be stored on a cases-app
    table, not because deriving rows is a habit of this module.

    NEVER DOUBLE-REPORTS. A company registered through the chart already has a
    real stored row saying so, and synthesising a second one from the very
    ``Client`` fields that same call stamped would print the registration
    twice, a few milliseconds apart. So the existence check comes first, and it
    is deliberately UNSCOPED (``ClientEvent.objects.filter(...)``, not
    ``_scoped_events``): whether a stored row EXISTS is a property of the
    company, not of who is looking, and asking the scoped queryset would answer
    "no row" for an Expert who did not personally register it and hand them a
    duplicate that a Supervisor does not see.

    THE ACTOR IS RESOLVED LIVE, not frozen, and this is the one place in this
    module's history where that is unavoidable rather than a slip: there is no
    frozen snapshot to read — the fact being reported is two columns on
    ``cases.Client``, written by an app that has no ``actor_name`` discipline of
    its own. ``cases.services._actor_snapshot`` is the same helper
    ``log_client_event`` freezes WITH, so the name and role label at least read
    identically to every stored row beside them. A company whose creator has
    since been deleted (``created_by`` is ``SET_NULL``) yields empty strings,
    and the template already prints "—" for that.
    """
    created_at = getattr(client, "created_at", None)
    if created_at is None:
        # Only reachable for an unsaved instance; sorting the merged list
        # would raise on a None timestamp, so there is nothing to report.
        return []
    if ClientEvent.objects.filter(
        client=client, action=ClientEventAction.CLIENT_REGISTERED,
    ).exists():
        return []
    actor_name, actor_role_label = case_services._actor_snapshot(client.created_by)
    return [
        _timeline_entry(
            "client", ClientEventAction.CLIENT_REGISTERED,
            subject=client.name,
            actor_name=actor_name, actor_role_label=actor_role_label,
            created_at=created_at,
        )
    ]


def _redact_case_numbers(entries, case_access: CaseAccess) -> list:
    """``entries`` with every case document number this viewer may not see
    taken out of them — the row itself always kept.

    THE PROBLEM THIS SOLVES. Two ``ClientEvent`` actions freeze a case's
    document number into their text at write time: ``REPORT_ADDED`` puts it in
    ``subject`` (``add_report``) and ``CONNECTION_ADDED`` /
    ``CONNECTION_REMOVED`` put it in ``comment`` (``_connection_comment``).
    ``client_timeline`` is scoped by the ACTOR (``_scoped_events``), which is
    the right rule for Marketing's own history and says nothing whatever about
    cases — so a viewer with ``scope="all"`` (a Marketing Supervisor, the GM,
    the admin) reads every one of those rows, including rows naming cases that
    ``marketing/access.py::case_access_for`` refuses them. The module docstring
    is unambiguous that a document number is case identity and is that
    function's to give out, and the Cases tab has always honoured it; these two
    rows did not.

    WHY THE FIX IS HERE AND NOT AT THE WRITE. The number is frozen by design —
    a ``ClientEvent`` is an immutable record, and a viewer who MAY see the case
    is entitled to read which case a report or a connection was filed under.
    Refusing to write it would take that away from everyone to protect it from
    someone, and would rewrite nothing already stored. So the row is written as
    it always was and the gate is applied when it is read, per viewer, which is
    the only place a per-viewer answer exists.

    THE ROW IS NEVER DROPPED. The report, the connection and their actor are
    Marketing's own history, already correctly scoped by ``_scoped_events``;
    only the case attached to them is withheld. A blanked ``subject`` makes the
    template read "Wrote a report on this company" instead of "... about case
    NNNN", and a comment loses the number while keeping the role beside it —
    the case is simply not named.

    ONE MECHANISM FOR BOTH ACTIONS, and no per-action parsing: every frozen
    noun on every entry (the whole ``subject``, and each
    ``_COMMENT_NOUN_SEP``-separated part of ``comment``) is offered to
    ``_hidden_case_doc_nos``, which answers using the case table and
    ``scope_case_rows``. A string is redacted if and only if it really is the
    document number of a case this viewer is refused; company names, role
    labels and contact names match no case and pass through untouched. That is
    what keeps the report path and the connection path from ever disagreeing
    about the same number — there is one gate, not two.

    Entries are returned in the same order, and an entry with nothing to redact
    is returned as the very same dict object.
    """
    entries = list(entries)
    candidates = set()
    for entry in entries:
        if entry.get("subject"):
            candidates.add(entry["subject"])
        for part in (entry.get("comment") or "").split(_COMMENT_NOUN_SEP):
            part = part.strip()
            if part:
                candidates.add(part)
    hidden = _hidden_case_doc_nos(candidates, case_access)
    if not hidden:
        return entries
    out = []
    for entry in entries:
        subject = "" if entry.get("subject") in hidden else entry.get("subject", "")
        comment = entry.get("comment") or ""
        if comment:
            comment = _COMMENT_NOUN_SEP.join(
                part for part in comment.split(_COMMENT_NOUN_SEP)
                if part.strip() not in hidden
            )
        if subject != entry.get("subject", "") or comment != (entry.get("comment") or ""):
            entry = dict(entry, subject=subject, comment=comment)
        out.append(entry)
    return out


def client_timeline(client: Client, user, scope, *,
                    case_access: CaseAccess = NO_CASE_ACCESS) -> list:
    """One company's history, newest first — what MARKETING did to it.

    TWO SOURCES, ONE LIST:

    * NATIVE — ``marketing/models.py::ClientEvent`` rows, everything MARKETING
      did to this company in its own directory (registered it, tagged it,
      connected it, added or removed a contact, wrote a report on it). Scoped by
      ``(user, scope)`` — see ``_scoped_events`` for why history has to honour
      the same boundary the facts behind it do.
    * REGISTRATION — who first added the company at all, derived from
      ``cases.models.Client``'s own ``created_by``/``created_at`` when no
      stored ``CLIENT_REGISTERED`` row already says it. See
      ``_registration_entry`` for why it is derived rather than backfilled, and
      for why it can never double-report.

    THERE WAS A THIRD SOURCE, AND THE OWNER REMOVED IT. This function used to
    also synthesise one "Case NNNN was opened for this company" entry per case,
    live from ``cases.models.Case``, scoped by a ``case_access`` argument this
    signature no longer takes. The owner's instruction was plain — "it is not
    needed to record that someone opened a case" — so the case half is gone
    entirely, and with it everything that existed only to serve it: the
    ``CASE_CREATED`` action, its icon, its template branch, and the
    ``case_id``/``doc_no``/``open_url`` keys on ``_timeline_entry``.

    ``case_access`` IS BACK, FOR A COMPLETELY DIFFERENT REASON, and it is worth
    being precise about the difference because the argument was removed once
    already. It used to decide which cases got a SYNTHESISED row of their own;
    that half is still gone and is not coming back. What it does now is
    RETRACTIVE: two stored actions froze a case's document number into their own
    text when they were written (``REPORT_ADDED``'s ``subject``,
    ``CONNECTION_ADDED``/``CONNECTION_REMOVED``'s ``comment``), and
    ``_scoped_events`` scopes this list by the ACTOR alone — which is the
    correct rule for Marketing's history and no rule at all about cases. A
    Supervisor, the GM or the admin (``scope="all"``) therefore read every such
    row, document numbers included, for cases ``case_access_for`` refuses them —
    exactly what the module docstring says must never happen. ``_redact_case_
    numbers`` takes those numbers back out, per viewer, at read time; see it for
    why the gate cannot live at the write. NO ROW IS EVER DROPPED by it: the
    history is still whatever ``scope`` says it is, only the case is unnamed.

    Defaults to ``NO_CASE_ACCESS`` like every other ``case_access`` argument in
    this module, and fails the same way — a caller who forgets it gets a
    timeline with no document numbers on it, never somebody else's case
    identity. The CASES TAB is still a different code path
    (``marketing/views.py::_visible_case_rows``) with its own scoping,
    untouched.

    Returns a list of ``_timeline_entry`` dicts, newest first. Ties (two rows
    with the identical timestamp) keep stored rows ahead of the derived
    registration one: Python's sort is stable and stored rows are appended
    first.
    """
    entries = [
        _timeline_entry(
            "event", ev.action,
            subject=ev.subject, subject_role=ev.subject_role,
            comment=ev.comment,
            actor_name=ev.actor_name, actor_role_label=ev.actor_role_label,
            created_at=ev.created_at,
        )
        for ev in _scoped_events(
            ClientEvent.objects.filter(client=client), user, scope,
        )
    ]
    entries += _registration_entry(client)
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return _redact_case_numbers(entries, case_access)


# --------------------------------------------------------------------------- #
# Contacts — the people at a company
# --------------------------------------------------------------------------- #
# SCOPING NOTE, once, for all three functions below. The owner's rule is "a
# Marketing user sees ONLY the contacts they themselves added; a Marketing
# Supervisor, the GM and the platform admin see every contact." That reads as
# a different rule from ``ClientLabel``'s, but it is not: ``marketing/access.py``
# already resolves exactly those two populations into exactly two scope
# values — an ordinary Marketing seat gets ``scope="own"``, while a Supervisor,
# the GM and the admin all get ``scope="all"`` — and ``_scoped`` filters on
# ``created_by``, which is precisely "who added this contact". So the existing
# helper expresses this rule correctly AS-IS, and contacts deliberately do NOT
# get a helper of their own; a second one would be the same two lines under a
# different name, free to drift. (``_scoped_events`` above is a genuine
# exception, and for a structural reason: ``ClientEvent`` has no
# ``created_by`` field at all.)
def _contact_row(contact: CompanyContact, user) -> dict:
    """One contact as the flat dict the contact list/table renders.

    Dicts, not model instances, like every other report in this module — the
    caller serialises straight to JSON without touching the ORM again, and the
    ``role``/``gender`` display texts are resolved here once instead of by a
    template doing a query per row.

    ``phones`` IS A LIST OF DICTS NOW, ONE PER :class:`ContactPhone` ROW,
    WHERE THIS USED TO BE THREE FLAT STRINGS. See
    ``marketing/models.py::CompanyContact``'s "THE PHONE NUMBER(S) LIVE ON
    ContactPhone, NOT HERE" section for why the number(s) moved off this
    model onto a child table. ``contact.phones.all()`` is expected to already
    be prefetched by the caller (:func:`list_contacts` does exactly that) so
    building this list costs no extra query per contact.
    """
    return {
        "id": contact.pk,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "full_name": contact.full_name,
        "role_id": contact.role_id,
        "role": contact.role.name if contact.role_id else "",
        "gender": contact.gender,
        "gender_label": ContactGender.LABELS.get(contact.gender, contact.gender),
        "phones": [
            {
                "phone_prefix": p.phone_prefix,
                "phone": p.phone,
                "phone_ext": p.phone_ext,
            }
            for p in contact.phones.all()
        ],
        "email": contact.email,
        "created_by_id": contact.created_by_id,
        "created_at": contact.created_at,
        # True iff THIS user owns the row — i.e. ``remove_contact`` would
        # actually delete something for them at ``scope="own"``. Mirrors
        # ``companies_for_label``'s ``removable`` field exactly, including the
        # fact that it is about ownership and not about what is displayed.
        "removable": contact.created_by_id == getattr(user, "pk", None),
    }


def list_contacts(client: Client, user, scope) -> list:
    """Every contact at ``client`` this viewer may see — see the scoping note.

    Ordered by ``CompanyContact.Meta.ordering`` (last name, then first).
    ``select_related`` on the role and ``prefetch_related`` on the new
    ``phones`` child rows so a list of contacts costs a fixed small number of
    queries — three, now that phones live in their own table — never one per
    row. ``ContactPhone.Meta.ordering`` (creation order) is what
    ``prefetch_related`` uses to order each contact's ``phones.all()``, so the
    rows come back in the same "as entered" order :func:`add_contact` wrote
    them in.
    """
    contacts = _scoped(
        CompanyContact.objects.filter(client=client), user, scope,
    ).select_related("role").prefetch_related("phones")
    return [_contact_row(c, user) for c in contacts]


def _resolve_contact_role(role):
    """``role`` as a ``ContactRole`` instance (or ``None``).

    Accepts an instance, a primary key, or ``None``/blank, because both kinds
    of caller are legitimate: a form hands over a resolved instance, while a
    JSON endpoint has only the id the select box posted. Resolving in one
    place keeps that convenience from being re-implemented per view. An id
    that matches no row resolves to ``None`` (the contact simply has no title
    on file) rather than raising — the title is optional on the model, so a
    stale id in a posted form must not cost the user the whole contact.
    """
    if role is None or role == "":
        return None
    if isinstance(role, ContactRole):
        return role
    return ContactRole.objects.filter(pk=role).first()


def add_contact(client: Client, user, *, first_name: str, last_name: str,
                gender: str, role=None, phones=(),
                email: str = "") -> CompanyContact:
    """Add one person at ``client``, stamped ``created_by=user``.

    Keyword-only past ``client``/``user`` on purpose: this takes several
    optional-looking values, and a positional call site would be unreadable
    and one silent swap away from filing a phone number as an extension.

    ``phones`` — AN ITERABLE OF PHONE ROWS, NOT THREE STRINGS ANY MORE. Each
    row is anything subscriptable by ``"prefix"``/``"number"``/``"ext"`` (a
    dict, as ``marketing/forms.py::ContactForm.clean``'s ``"phones"`` entry
    already is) — one :class:`ContactPhone` is created per row, in the order
    given, so the order a person typed their numbers in survives onto
    ``ContactPhone.Meta.ordering`` (creation order). See
    ``marketing/models.py::CompanyContact``'s "THE PHONE NUMBER(S) LIVE ON
    ContactPhone, NOT HERE" section for why a contact's numbers moved off this
    model's own columns onto a child table in the first place. An empty
    ``phones`` is completely legitimate — a contact reachable only by email
    has nothing here — exactly as an empty ``phone`` string always was on the
    old single field.

    VALIDATES ONLY WHAT THE DATABASE CANNOT. ``first_name``/``last_name`` must
    be non-blank and ``gender`` must be one of
    ``marketing/models.py::ContactGender.CHOICES``; both raise ``ValueError``,
    the same way ``get_or_create_client`` ("A name is required.") and
    ``create_connection`` (an unknown role key) already do in this module.
    Django's model layer would NOT catch either on a direct ``create()`` —
    ``CharField`` accepts an empty string and ``choices`` is only enforced by
    full-model validation — so a service that can be called from anywhere has
    to check them itself.

    DELIBERATELY NOT VALIDATED HERE: the "at least one of phone/email" rule.
    It is a FORM-layer rule (see ``marketing/models.py::CompanyContact``'s own
    docstring for the full reasoning), so it is enforced where a violation can
    be reported against the specific field the user left blank. Raising it
    here as well would only add a second, worse-worded copy of the same error
    for the exact same input, and would block legitimate direct writes —
    imports, backfills — that the model itself deliberately permits. The same
    reasoning now covers ``phones`` being empty: a caller that is not a form
    (an import, a shell fix-up) must still be able to write a contact with no
    phone rows at all.

    WRAPPED IN ``transaction.atomic()`` — new here, and necessary now in a way
    it was not before: writing used to be one ``INSERT`` (the whole contact,
    phone included, in one row), and is now potentially several (the contact,
    then zero or more ``ContactPhone`` rows). Without the wrapper, a failure
    partway through the phone rows (a database hiccup, not anything this
    function itself would raise) could leave a saved contact with only some
    of the numbers the user typed — worse than not saving it at all, because
    it would look complete and quietly not be.

    Writes a ``ClientEvent`` (see ``_try_log``) with the person's own name as
    ``subject``. Unlike ``toggle_manual_label``/``create_connection`` there is
    no idempotence to check first: adding a contact always creates a row (two
    people at one company really can share a name), so every call is a real
    event.
    """
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if not first_name or not last_name:
        raise ValueError("A first name and a last name are required.")
    gender = (gender or "").strip()
    if gender not in ContactGender.LABELS:
        raise ValueError(f"Unknown gender: {gender!r}.")
    with transaction.atomic():
        contact = CompanyContact.objects.create(
            client=client,
            first_name=first_name,
            last_name=last_name,
            role=_resolve_contact_role(role),
            gender=gender,
            email=(email or "").strip(),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        ContactPhone.objects.bulk_create([
            ContactPhone(
                contact=contact,
                phone_prefix=(row.get("prefix") or "").strip(),
                phone=(row.get("number") or "").strip(),
                phone_ext=(row.get("ext") or "").strip(),
            )
            for row in phones
        ])
    _try_log(client, user, ClientEventAction.CONTACT_ADDED,
             subject=contact.full_name)
    return contact


def remove_contact(client: Client, contact_id, user, scope) -> bool:
    """Delete contact ``contact_id`` at ``client``, within this viewer's scope.

    Returns True when a row was actually deleted, False when there was nothing
    to delete OR the row exists but is out of this viewer's scope — the caller
    deliberately cannot tell those two apart, the same way
    ``toggle_manual_label``/``remove_connection`` deliberately do not.

    SCOPE-AWARE, unlike ``toggle_manual_label`` and ``remove_connection``
    (which delete strictly ``created_by=user`` rows and let a Supervisor
    remove nothing but their own). The difference is intentional and follows
    what the two things ARE. A manual TAG is one Expert's own opinion about a
    company — a Supervisor sees the union of those opinions but does not get
    to unpick somebody else's. A CONTACT is a shared fact about the company
    ("this is who answers the phone there"), the Supervisor is the person the
    owner named as seeing all of them, and a departed Expert's stale contacts
    would otherwise be permanently unremovable. So removal follows visibility
    here: an ordinary Marketing seat ("own") still deletes only its own rows —
    identical to the other two functions — while a Supervisor/GM/admin ("all")
    can delete any contact on the company. Read access decides write access;
    nothing outside a viewer's scope is ever touched.

    Writes a ``ClientEvent`` (see ``_try_log``) only on a real deletion, with
    the person's name captured BEFORE the delete — the row it names is gone
    afterwards, which is exactly why ``ClientEvent.subject`` is frozen text
    and not a foreign key (see ``marketing/models.py::ClientEvent``).
    """
    contact = _scoped(
        CompanyContact.objects.filter(client=client, pk=contact_id), user, scope,
    ).first()
    if contact is None:
        return False
    name = contact.full_name
    contact.delete()
    _try_log(client, user, ClientEventAction.CONTACT_REMOVED, subject=name)
    return True


# --------------------------------------------------------------------------- #
# Reports — what a marketing person wrote about a company
# --------------------------------------------------------------------------- #
# SCOPING NOTE, once, for both functions below, and it is the SAME note the
# contacts section above already carries — deliberately, because it is the same
# rule and not a lookalike. The owner's wording for reports is "each marketing
# user sees only the reports THEY wrote; the manager and the admin see all of
# them", which is word for word the shape of the contacts rule, and
# ``marketing/access.py::access_for`` already resolves exactly those two
# populations into exactly two scope values (an ordinary Marketing seat ->
# "own"; a Supervisor, the GM and the platform admin -> "all"). ``_scoped``
# filters on ``created_by``, which is precisely "who wrote this report". So
# reports reuse that one helper and get no rule of their own; a second one
# would be the same two lines under a different name, free to drift.
def _report_row(report: CompanyReport, user, visible_case_ids=frozenset()) -> dict:
    """One report as the flat dict the company page renders.

    Dicts, not model instances, like every other report in this module (see
    ``_contact_row``, which this mirrors field for field where the two overlap)
    — the caller renders straight from these without touching the ORM again.

    ``author`` is the FROZEN ``author_name``, through
    ``CompanyReport.author_display_name`` so a row somehow written outside
    ``add_report`` still shows something; never a live lookup on
    ``created_by``, for the reason that model's docstring gives.

    ``options`` is the option NAMES, resolved here once. ``case_doc_no`` is the
    attached case's document number or an empty string — read live from the FK
    rather than frozen, deliberately and unlike everything on ``ClientEvent``:
    a report is a live working record that points at a live case, not an
    immutable audit row, so it should follow a renumbered case rather than keep
    claiming the old number. An attached case that was later deleted leaves the
    FK NULL (``SET_NULL``) and this empty.

    AND IT IS EMPTY AGAIN WHENEVER THIS VIEWER MAY NOT SEE THE CASE. This
    function used to read ``report.case.doc_no`` off the FK with no filter at
    all, which made the reports panel the one place in the section that printed
    a document number to a viewer ``marketing/access.py::case_access_for``
    refuses — the module docstring's rule, and the one the Cases tab on the very
    same page already obeys. ``visible_case_ids`` is that decision, made once
    per list by ``list_reports`` through the SAME ``scope_case_rows`` every case
    listing here goes through (see ``_visible_case_ids``), and it defaults to
    the empty set so a caller who does not supply it gets no numbers rather than
    everyone's.

    WHICH WITHHOLDS THE CASE, NEVER THE REPORT. The report itself is Marketing's
    own working data, governed by ``_scoped``/``scope``, and if it is in this
    list the viewer is entitled to it — its text, its options, its author. Only
    the case attached to it goes unnamed, so ``case_id`` is dropped alongside
    ``case_doc_no`` (an id is case identity too — see the module docstring) and
    ``has_case`` is carried instead, purely so the template can say "on a case"
    without saying WHICH rather than mislabel the row "on the company".

    ``client_id``/``client_name`` ARE READ LIVE OFF THE FK, NOT FROZEN — added
    for ``list_reports_for_user`` below, whose whole reason for existing is a
    list that SPANS every company at once (My Tasks' own Reports tab), so a
    row needs to say which company it is on the way ``case_doc_no`` says which
    case. Every existing caller of this function already narrows to one known
    ``Client`` before it ever reaches here (``list_reports``'s own ``client``
    argument), so these two keys are simply unused by their templates rather
    than a behaviour change for them. Live, not frozen, for
    ``case_doc_no``'s own reason: a report is a live working record that
    points at a live company, not an immutable audit row, so it should follow
    a rename rather than keep claiming the old one. ``client_id`` can be
    ``None`` — a report that closed out a BARE reminder never named a company
    at all (see ``marketing/models.py::CompanyReport``'s own docstring) — and
    ``client_name`` is then "" rather than raising on a ``None`` FK.

    ``reminder_set_at``/``reminder_due_at``/``reminder_note`` ARE THE THREE
    FROZEN SNAPSHOTS COPIED STRAIGHT OFF THE ROW, no re-shaping — see
    ``CompanyReport``'s own docstring for why these three can only ever be a
    frozen copy and never a live lookup. All three are blank/``None`` for a
    standalone report (one never born from ``close_with_report``), and a
    template tells the two kinds of row apart by testing
    ``reminder_set_at`` for exactly that reason: it is the one of the three
    that is never a legitimately-blank FREE-TEXT field the way ``reminder_note``
    can be (an owner who set a reminder with no note still has a real
    ``reminder_set_at``), so it is the one flag that can never lie about
    whether this report closed a reminder or not.
    """
    case_visible = report.case_id is not None and report.case_id in visible_case_ids
    return {
        "id": report.pk,
        "text": report.text,
        "options": [opt.name for opt in report.options.all()],
        "case_id": report.case_id if case_visible else None,
        "case_doc_no": report.case.doc_no if case_visible else "",
        # Whether a case is attached AT ALL — true even when it may not be
        # named. Not the same question as ``case_id``, which is now the
        # narrower "attached, and you may see it".
        "has_case": report.case_id is not None,
        "author": report.author_display_name,
        "created_by_id": report.created_by_id,
        "created_at": report.created_at,
        # See the docstring's "client_id/client_name" section — live off the
        # FK, empty/blank for a report that named no company at all.
        "client_id": report.client_id,
        "client_name": report.client.name if report.client_id and report.client else "",
        # See the docstring's "reminder_set_at/..." section — frozen copies,
        # populated only by ``marketing/reminders.py::close_with_report``.
        "reminder_set_at": report.reminder_set_at,
        "reminder_due_at": report.reminder_due_at,
        "reminder_note": report.reminder_note,
        # True iff THIS user wrote the row — the same ``removable``-style
        # ownership flag ``_contact_row`` carries, kept under an honest name
        # because reports are not removable at all: nothing in this module
        # deletes one, and a written report is a record, not a draft.
        "is_own": report.created_by_id == getattr(user, "pk", None),
    }


def list_reports(client: Client, user, scope, *, case=None,
                 case_access: CaseAccess = NO_CASE_ACCESS) -> list:
    """Every report on ``client`` this viewer may see — see the scoping note.

    Newest first (``CompanyReport.Meta.ordering``). ``select_related`` on the
    case and ``prefetch_related`` on the options so a list of reports costs a
    constant number of queries rather than two per row.

    TWO INDEPENDENT DECISIONS, AND THEY ARE NOT THE SAME ONE. ``(user, scope)``
    decides WHICH REPORTS appear — Marketing's own visibility rule, the scoping
    note above. ``case_access`` decides only whether the case attached to a
    report that already appears may be NAMED, which is
    ``marketing/access.py::case_access_for``'s question and not this module's
    (see the module docstring on case identity). Neither can stand in for the
    other: a Supervisor sees every report on the company AND may be refused
    every case behind them.

    ``case`` (keyword-only, optional) NARROWS TO ONE CASE'S OWN REPORTS —
    added for ``cases/views.py::case_detail``'s own Reports tab, which wants
    "every report attached to THIS case", not the whole company's. ``None``
    (the default) is every existing caller's behaviour, unchanged: the
    company detail page still lists the whole client's reports. THIS IS THE
    ONE PLACE THE CASE PAGE'S REPORTS TAB ADDS ANY FILTER OF ITS OWN — it is a
    plain ``case=case`` clause on the SAME query and SAME ``_scoped``/
    ``_report_row`` machinery every other caller goes through, not a second
    reports system. See that view's own docstring for why it calls this with
    ``scope="all"`` rather than deriving one from whatever Marketing seat the
    viewer might or might not separately hold: the case page's own viewing
    rule is "anyone with access to this case sees its reports", which is a
    CASE-ACCESS question, already settled by ``user_can_view_case`` before
    this is ever called, and not the author-privacy question ``scope``
    answers everywhere else in this module.

    Resolved here, once for the whole list, rather than per row: one
    ``scope_case_rows`` call over the attached case ids (``_visible_case_ids``)
    instead of one per report. Defaults to ``NO_CASE_ACCESS`` like every other
    ``case_access`` argument in this module, and fails the same way — a caller
    who forgets it gets reports with no document numbers on them.
    """
    qs = _scoped(CompanyReport.objects.filter(client=client), user, scope)
    if case is not None:
        qs = qs.filter(case=case)
    # ``select_related("client")`` ADDED ALONGSIDE ``_report_row``'s NEW
    # ``client_name`` KEY — every row here already carries the SAME known
    # ``client`` (the function's own argument), so this join costs nothing new
    # in practice; it exists so ``_report_row``'s ``report.client.name`` read
    # never fires a per-row query on the rare caller that does not already
    # have the instance warm some other way.
    reports = list(qs.select_related("case", "client").prefetch_related("options"))
    visible = _visible_case_ids([r.case_id for r in reports], case_access)
    return [_report_row(r, user, visible) for r in reports]


def list_reports_for_user(user, case_access: CaseAccess = NO_CASE_ACCESS,
                          scope: str = "own") -> list:
    """Every ``CompanyReport`` this exact ``user`` wrote — ACROSS EVERY COMPANY
    AT ONCE, unlike ``list_reports`` above, which is always one company's own
    list.

    BUILT FOR "MY TASKS"' OWN REPORTS TAB, the company-INDEPENDENT screen every
    person with a linked ``people.Person`` record reaches from the sidebar's
    own "Personal" group — see ``marketing/views.py``'s "MY TASKS" section for
    the full story of that page. There is no single ``Client`` in that page's
    URL for ``list_reports`` to be called once per company against, and the
    owner's own wording for THAT tab is "every report THIS viewer authored" —
    literally ``created_by=user``.

    ``scope`` NOW EXISTS, AND THE DEFAULT IS STILL "OWN" — the same reversal
    ``marketing/reminders.py::list_for_user`` already went through, applied
    here for the identical reason: a LATER round built an admin-wide variant
    of My Tasks (``marketing/views.py::my_tasks``'s own ``scope`` parameter,
    reached only through ``marketing:my_tasks_all`` and gated on the exact
    same admin check that already guards the People section's own admin
    pages — see that view's docstring), and "every report ever written, by
    anyone" is precisely what that screen has to be able to ask for. This
    still takes no MARKETING-seat concept at all (an ordinary Marketing
    seat's own "own"/"all" split, resolved from ``access_for``, is a
    different question this function has never answered and still does not)
    — ``scope`` here is resolved by the CALLER from the admin check on THIS
    page alone, never from a Marketing seat, so a Marketing Supervisor's own
    visit to the ordinary "My Tasks" page is completely unaffected: it always
    passes ``scope="own"`` (the default), and the surprise widening this
    docstring used to warn against — a Supervisor's "My Tasks" quietly
    showing a colleague's report on a page titled "mine" — still cannot
    happen through this function. ``"own"`` filters to
    ``created_by=user`` through the SAME ``_scoped`` helper every other
    reports list in this module already goes through; ``"all"`` is
    unfiltered, the union of everyone's, read live off the table with no
    further narrowing.

    REUSES ``_report_row`` UNCHANGED, the same shaping every other reports
    list in this app goes through, so a report a viewer can see HERE and the
    identical row they can see on that report's own company page (or its
    case's own page) can never disagree about what it says — only which LIST
    it happens to appear in differs. ``case_access`` is resolved by the
    caller exactly as every other caller in this module resolves it — see
    ``marketing/views.py::_my_tasks_case_access`` for why My Tasks' own
    version of that decision is not simply ``case_access_for(request,
    access_for(request))`` the way every Marketing-gated page's is.

    Newest first (``CompanyReport.Meta.ordering``), the identical order
    ``list_reports`` already returns. ``select_related`` on the case AND the
    client (unlike ``list_reports``, which already has a known, single client
    and only needs the case) — this list spans every company at once, so
    ``_report_row``'s new ``client_name`` read would otherwise cost one query
    per row.
    """
    qs = _scoped(
        CompanyReport.objects.select_related("case", "client")
        .prefetch_related("options"),
        user, scope,
    )
    reports = list(qs)
    visible = _visible_case_ids([r.case_id for r in reports], case_access)
    return [_report_row(r, user, visible) for r in reports]


def add_report(client: Client | None, user, *, text: str = "", options=(),
               case=None) -> CompanyReport:
    """Record one report on ``client``, written by ``user``.

    ``client`` MAY NOW BE ``None`` — widened alongside
    ``marketing/models.py::CompanyReport.client`` (see that field's own
    comment) so ``marketing/reminders.py::close_with_report`` can write the
    report that closes a BARE reminder, one that names no company and no case
    at all. Every ordinary caller (``marketing/views.py::report_create``)
    still always passes a real ``Client`` — the company page a report is
    written from cannot exist without one — so ``None`` only ever arrives
    through the reminder-closing path.

    Keyword-only past ``client``/``user`` for ``add_contact``'s reason: the
    remaining arguments are three optional-looking things of three different
    kinds, and a positional call site would be one silent swap away from filing
    the case as the text.

    THE AUTHOR'S DISPLAY NAME IS FROZEN HERE, through the same
    ``cases.services._actor_snapshot`` ``log_client_event`` uses — not a second
    copy of the same logic. That helper already knows about linked
    ``people.Person`` rows, vacant seat usernames that must never be frozen onto
    a record, and the ``Substitute · ...`` prefix a Translate seat earns; see
    ``log_client_event``'s own docstring for why reusing it is the point. Only
    the NAME is kept — the role label the helper also returns is dropped,
    because the owner asked for names without role labels and there is no screen
    that wants one on a report.

    DELIBERATELY NOT VALIDATED HERE: the "at least one of options / text" rule.
    It is a FORM-layer rule (see ``marketing/models.py::CompanyReport`` and
    ``marketing/forms.py::ReportForm``), enforced where a violation can be
    reported against the fields the person actually left blank — exactly the
    split ``add_contact`` already documents for ``CompanyContact``'s own
    "at least one of phone/email" rule, and for exactly those reasons, including
    not blocking a legitimate direct write (an import, a backfill) that the
    model itself permits.

    ``options`` may be any iterable of ``ReportOption`` instances or primary
    keys; it is assigned after the row exists because a many-to-many cannot be
    set on an unsaved instance. Both are wrapped in one ``transaction.atomic``
    so a report can never be committed without the options that give it its
    meaning.

    Writes a ``ClientEvent`` (see ``_try_log``) exactly the way ``add_contact``
    does. There is no idempotence to check first: writing a report always
    creates a row (the same person really can report twice on one company on one
    day), so every call is a real event.
    """
    author_name, _role_label = case_services._actor_snapshot(user)
    with transaction.atomic():
        report = CompanyReport.objects.create(
            client=client,
            case=case,
            text=(text or "").strip(),
            created_by=user if getattr(user, "is_authenticated", False) else None,
            author_name=author_name,
        )
        chosen = list(options or ())
        if chosen:
            report.options.set(chosen)
    # ``subject`` is the attached case's document number, frozen at write time
    # like every other ``ClientEvent`` value, or empty when the writer skipped
    # the case step — which the owner made an explicit, supported choice rather
    # than an omission. The report's own TEXT is deliberately not copied onto
    # the timeline row: the timeline records that a report was written, and the
    # report itself is where it is read.
    #
    # ``client is not None`` GUARDS THIS RATHER THAN LEAVING IT TO
    # ``_try_log``'S SWALLOW. ``ClientEvent.client`` is still a required FK —
    # there is no such thing as a company-less timeline entry, because there
    # is no company to have one — so a report born from closing a BARE
    # reminder (no client at all) has no timeline to write to at all, not a
    # timeline write that happens to fail. ``_try_log`` WOULD swallow the
    # resulting IntegrityError and leave the report itself intact either way,
    # but skipping the attempt outright says plainly "this report has no
    # company" instead of spending a savepoint and a rollback to arrive at
    # the same place every single time.
    if client is not None:
        _try_log(client, user, ClientEventAction.REPORT_ADDED,
                 subject=(getattr(case, "doc_no", "") or "").strip())
    return report


def _resolve_report_options(values):
    """``values`` (instances and/or primary keys) as a list of ``ReportOption``.

    The same convenience ``_resolve_contact_role`` provides for a single role,
    for the same reason: a form hands over resolved instances while a plain POST
    has only the ids its checkboxes submitted, and resolving in one place keeps
    that from being re-implemented per view. Unknown ids are dropped rather than
    raised on — an option retired between the page render and the submit must
    not cost the writer their whole report — and the result is de-duplicated,
    since a many-to-many cannot hold the same option twice anyway.
    """
    resolved, ids = [], []
    for value in values or ():
        if isinstance(value, ReportOption):
            resolved.append(value)
        elif value not in (None, ""):
            ids.append(value)
    if ids:
        resolved.extend(ReportOption.objects.filter(pk__in=ids))
    seen, out = set(), []
    for opt in resolved:
        if opt.pk not in seen:
            seen.add(opt.pk)
            out.append(opt)
    return out


# --------------------------------------------------------------------------- #
# Per-company case outcome counts
# --------------------------------------------------------------------------- #
# The three-way bucketing itself is defined ONCE, by ``_status_fa`` and the
# two frozensets above it — this map only says which English key each of that
# helper's three answers belongs under. It is built by CALLING the helper with
# one representative status per bucket rather than by repeating its Persian
# strings as literals, so renaming a bucket's display text (or moving a status
# between buckets) can never leave this map silently pointing at a string
# ``_status_fa`` no longer returns.
_STATUS_BUCKET_KEY = {
    _status_fa(CaseStatus.FINAL_APPROVED): "approved",
    _status_fa(CaseStatus.CANCELLED): "cancelled",
    _status_fa(CaseStatus.DRAFT): "pending",
}


def case_status_counts(client: Client, user) -> dict:
    """``{"approved": n, "cancelled": n, "pending": n, "total": n}`` for one company.

    The company detail page's headline numbers, bucketed by the EXACT same
    three-way rule the "cases connected to Us" panel already shows per case —
    ``_status_fa`` and nothing else, via ``_STATUS_BUCKET_KEY`` above — so a
    company whose panel lists two "تایید شده" cases can never show a different
    number of approved ones in its summary.

    UNSCOPED, like every case-derived read in this module. ``user`` is accepted
    only so the signature matches the rest of this module's ``(..., user)``
    functions and so a future scoping rule has somewhere to attach — see
    ``us_connections``, which takes it for exactly the same reason.

    One query, values-only. Terminal cases still count, per the module
    docstring: ``total`` is every case this company has ever had, and
    ``approved + cancelled + pending == total`` always.
    """
    counts = {"approved": 0, "cancelled": 0, "pending": 0, "total": 0}
    for status in Case.objects.filter(client=client).values_list("status", flat=True):
        counts[_STATUS_BUCKET_KEY[_status_fa(status)]] += 1
        counts["total"] += 1
    return counts


# --------------------------------------------------------------------------- #
# The company directory listing
# --------------------------------------------------------------------------- #
def search_companies(query: str = "", label: str = "", limit: int = 200, *,
                     user=None, scope: str = "own") -> list:
    """The companies directory page's list: name search, optional label filter.

    ``marketing/views.py::directory`` IS ITS CALLER — twice per page load, once
    unfiltered to build the two dropdowns' option lists and once with whatever
    filters arrived. (It briefly had no caller at all, in the window between the
    Marketing workspace's "Companies" tab being removed and the company
    directory section that replaced it being built. That gap is closed.) There
    is deliberately ONE definition of what the directory lists, shared with the
    chart, rather than the new section re-deriving it and drifting.

    Composed entirely out of this module's existing pieces, deliberately
    defining nothing new:

    * NAME MATCHING is ``search_clients`` — the same ``normalize_persian``
      scan-and-compare, so a name typed with Arabic-style ke/ye still matches
      one stored with the Persian keheh/ye here exactly as it does everywhere
      else. It is called with ``limit=None`` (no cap) whenever a label filter
      is also in play, because capping the name match FIRST and filtering
      after would silently drop companies that do match both; the cap is
      applied once, at the end, to the finished list.
    * "HOLDS THIS LABEL" is ``companies_for_label`` — its manual+case-derived
      merge, its scoping, its blank-defaults-to-owner rule. This function only
      reads the ids out of that result; there is no second definition of what
      holding a label means, and there must never be one. It deliberately takes
      no ``case_access`` of its own and lets that call fall through to
      ``NO_CASE_ACCESS``: the only key read back is ``id``, which is the
      unscoped shared-truth half, and this function never emits a document
      number. Nothing in the directory listing can leak one, so there is
      nothing here for a case-access argument to protect.
    * THE LABEL CHIPS on each row are ``labels_for_clients`` — the same batched
      merge ``client_search`` already returns, so a row here and a row there
      can never disagree about which chips a company carries.

    ``user``/``scope`` are keyword-only and default to the FAIL-CLOSED pair
    (nobody, "own"): every real caller passes the pair from
    ``marketing/access.py::access_for``, exactly like every other scoped
    function in this module. The defaults exist so the documented positional
    signature stays callable, and they are deliberately not ("all") — a caller
    who forgets to pass the scope must get too little, never somebody else's
    manual tags.

    An unknown ``label`` raises ``ValueError``, matching ``create_connection``.
    A blank one (the default) means "no label filter" and is not an error.

    Returns, e.g.::

        [{"id": 3, "name": "Foolad Sanat Co.", "code": "014",
          "labels": [{"label": "owner", "label_fa": "کارفرمای اصلی — OWNER / CLIENT",
                      "source": "case", "removable": False}]}]

    ``labels`` is whatever ``labels_for_clients`` returns for that company (an
    empty list when it carries none within this scope) — see that function for
    each key's meaning.
    """
    label = (label or "").strip()
    if label and not is_label(label):
        raise ValueError(f"Unknown label: {label!r}.")

    # No cap on the name match while a label filter still has to be applied —
    # see the docstring. ``search_clients`` slices by ``[:limit]``, and both a
    # list and a queryset treat a ``None`` bound as "no bound", so this is the
    # same code path, not a special case inside it.
    matches = search_clients(query, limit=None if label else limit)
    if label:
        allowed_ids = {row["id"] for row in companies_for_label(label, user, scope)}
        matches = [c for c in matches if c.pk in allowed_ids]
    matches = matches[:limit]

    labels_by_client = labels_for_clients([c.pk for c in matches], user, scope)
    return [
        {
            "id": c.pk,
            "name": c.name,
            "code": c.code,
            "labels": labels_by_client.get(c.pk, []),
        }
        for c in matches
    ]
