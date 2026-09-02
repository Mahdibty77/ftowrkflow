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
``create_connection``/``remove_connection`` below). A ``Connection`` never
changes which labels a client itself DIRECTLY holds — the two sources above
still decide that, unchanged — it only decides WHO is displayed under a
label's chart card (``connections_of_client``'s new ``connected`` field), and
it can add an entry to that report for a label the client never actually
held itself, purely because the client connected some OTHER company under
it (see ``connections_of_client``'s docstring for the worked example).

TWO MORE THINGS HANG OFF A COMPANY, added this round, and both follow the
same rules as everything above rather than inventing their own:
``marketing/models.py::CompanyContact`` (the people to call there — created,
listed and removed by ``add_contact``/``list_contacts``/``remove_contact``,
scoped by the same ``(user, scope)`` pair) and
``marketing/models.py::ClientEvent`` (the company's own immutable timeline —
written by ``log_client_event``/``_try_log`` from every mutating service in
this file, and read back by ``client_timeline``, which MERGES those rows with
case history derived live from ``cases.models.Case``; see that function for
why the case half is derived at read time instead of mirrored into rows).

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
in this app already does (``companies_for_label``, ``connections_of_client``,
``client_timeline``). ``case_access`` is decided by the caller from the
request, never re-derived here, for the same reason ``scope`` is not.

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

from .access import CaseAccess, case_open_url, scope_case_rows
from .models import (
    ClientEvent, ClientEventAction, ClientLabel, CompanyContact, Connection,
    ContactGender, ContactRole,
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


def _scoped(qs, user, scope):
    """``qs`` narrowed to ``user``'s own rows when ``scope == "own"``."""
    if scope == "own":
        return qs.filter(created_by=user)
    return qs


def _effective_label(case: Case) -> str:
    """A case's business-role label, applying the blank-defaults-to-owner rule."""
    return case.marketing_label or MarketingLabel.OWNER


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
                     comment: str = "") -> ClientEvent:
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
        comment=comment or "",
        actor_name=actor_name,
        actor_role_label=actor_role_label,
    )


def _try_log(client: Client, actor, action: str, subject: str = "",
             comment: str = "") -> None:
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
            log_client_event(client, actor, action, subject=subject, comment=comment)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# The client directory itself
# --------------------------------------------------------------------------- #
def search_clients(query: str = "", limit: int = 25):
    """Clients whose name contains ``query`` (case-insensitive), by name.

    GLOBAL and UNSCOPED — every Marketing viewer (Expert, Supervisor, or the
    view-only GM) searches the SAME shared ``cases.Client`` directory
    Commercial uses; hiding a real registered company's mere existence from
    a colleague would contradict the shared-directory design. Returns
    ``Client`` model instances, not dicts — callers serialize.

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
def toggle_manual_label(client: Client, label: str, user, add: bool) -> None:
    """Add or remove THIS USER's own manual tag on ``client``.

    ``add=True`` gets-or-creates ``ClientLabel(client, label, created_by=user)``
    — idempotent, safe to call when the tag already exists.

    ``add=False`` deletes ONLY the row owned by ``user``: an Expert can never
    remove a Supervisor's or another Expert's manual tag this way, matching
    the ownership boundary ``scope`` enforces everywhere else. A no-op (not
    an error) when no such row exists.

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
    needed, and both come free with the write that already happens.
    """
    if add:
        _obj, created = ClientLabel.objects.get_or_create(
            client=client, label=label, created_by=user,
        )
        if created:
            _try_log(client, user, ClientEventAction.LABEL_ADDED,
                     subject=FIELD_LABELS.get(label, label))
    else:
        removed, _detail = ClientLabel.objects.filter(
            client=client, label=label, created_by=user,
        ).delete()
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
def _connection_comment(anchor_role: str, target_role: str, case=None) -> str:
    """The one-line "which roles, and under which case" note frozen onto a
    connection's timeline row.

    A ``Connection`` is four facts (two clients, two roles) plus an optional
    case, but a ``ClientEvent`` has exactly one ``subject`` slot, and that
    slot is spent on the OTHER company's name — the thing a reader scanning a
    timeline is actually looking for. The two roles and the case number are
    the remaining context, so they go in ``comment``, rendered here once
    rather than at each of the two call sites.

    Frozen text, not identifiers, for the same reason every other field on a
    ``ClientEvent`` is frozen (see ``marketing/models.py::ClientEvent``): the
    display names come from ``FIELD_LABELS`` at write time, so renaming a
    role's Persian display text later can never rewrite what this row says
    happened. ``FIELD_LABELS.get(role, role)`` — never ``FIELD_LABELS[role]``
    — so a row can still be written for a role key this module no longer
    recognises instead of the logging attempt raising inside the real
    operation it is only meant to be recording.
    """
    anchor_fa = FIELD_LABELS.get(anchor_role, anchor_role)
    target_fa = FIELD_LABELS.get(target_role, target_role)
    line = f"{anchor_fa} → {target_fa}"
    doc_no = (getattr(case, "doc_no", "") or "").strip() if case is not None else ""
    return f"{line} · {doc_no}" if doc_no else line


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
                 comment=_connection_comment(anchor_role, target_role, case))


def remove_connection(anchor_client: Client, anchor_role: str, target_client: Client, target_role: str, case, user) -> None:
    """Delete the directed edge (anchor_client, anchor_role) ->
    (target_client, target_role) scoped to ``case`` — but ONLY a row THIS
    USER themselves created.

    The exact same "cannot remove what you don't own" rule
    ``toggle_manual_label(..., add=False)`` already enforces for
    ``ClientLabel``, applied here identically: an Expert can never remove a
    connection a Supervisor or another Expert attached, matching the
    ownership boundary ``scope`` enforces everywhere else in this module. A
    no-op (not an error) when no such row exists at all, or when it exists
    but belongs to someone else — the caller cannot distinguish "gone" from
    "not yours" from this function's return value alone, by design, same as
    ``toggle_manual_label``.

    Writes a ``ClientEvent`` (see ``_try_log``) only when a row was actually
    deleted, on the ANCHOR company — the same two rules ``create_connection``
    above logs by, for the same reasons: a no-op is not a business event, and
    a directed edge belongs to the timeline of the company it was drawn from.
    ``delete()``'s own row count is the signal, and it comes free with the
    write that already happens.
    """
    removed, _detail = Connection.objects.filter(
        anchor_client=anchor_client, anchor_role=anchor_role,
        target_client=target_client, target_role=target_role,
        case=case, created_by=user,
    ).delete()
    if removed:
        _try_log(anchor_client, user, ClientEventAction.CONNECTION_REMOVED,
                 subject=target_client.name,
                 comment=_connection_comment(anchor_role, target_role, case))


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


def labels_for_clients(client_ids, user, scope) -> dict:
    """``{client_id: [{"label","label_fa","source","removable"}, ...]}`` for
    every id in ``client_ids`` — the same manual+case-derived merge
    ``connections_of_client`` does for one client, batched across many in a
    constant number of queries. Used by ``client_search`` so any caller
    listing companies can show label chips without a per-row round trip.
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
                "removable": label in own_manual.get(cid, set()),
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
                        case_access: CaseAccess = NO_CASE_ACCESS) -> list:
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
            "removable": client.pk in own_manual_client_ids,
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
                          case_access: CaseAccess = NO_CASE_ACCESS) -> dict:
    """The label/case report Inquiry shows when a COMPANY (not "us") is the focus.

    The mirror image of ``companies_for_label``: instead of "one label -> its
    companies", this is "one client -> its labels", using the identical
    merge rule and the identical per-entry field meanings for the labels
    ``client`` DIRECTLY holds (``source``, ``also_manual``, ``removable``,
    ``case_numbers`` — see ``companies_for_label``'s docstring for what each
    means). On top of that, every entry now also carries who is actually
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
      exists purely because of the Connection row.

    ``connected`` — a ``[{"id", "name"}, ...]`` list of the companies to
    actually display under this label's chart card, for EVERY entry:

    * Gather every ``Connection`` row anchored at ``client`` in one of the
      roles ``client`` genuinely, directly holds (i.e. ``anchor_role`` must
      be a label from the DIRECTLY-HELD half above — a connection anchored
      on a role ``client`` does not actually hold is never surfaced),
      whose ``target_role`` equals THIS entry's own label, and whose
      ``case`` is NULL (general — always visible) or equals the ``case``
      argument (case-scoped — visible only when Inquiry is running in that
      case's own context). Scoped by ``user``/``scope`` exactly like manual
      ``ClientLabel`` rows are (see ``_scoped``) — an Expert only sees
      connections THEY built until a Supervisor/GM looks at the union,
      consistent with every other manually-created fact in this module.
    * If any such rows exist, ``connected`` is their DISTINCT target
      clients (deduplicated by id, since more than one connection can
      legitimately point at the same target — e.g. one general and one
      case-scoped row for the same pair).
    * If none exist, ``connected`` falls back to ``client``'s own identity —
      ``[{"id": client.pk, "name": client.name}]`` — which is exactly
      TODAY's behaviour for a label with no explicit connection behind it: a
      company that simply also holds that role itself, with nobody else
      specifically connected through it. This fallback only ever applies to
      a DIRECTLY-HELD entry — a connection-only entry (``source is None``)
      never falls back to ``client``'s own identity, since ``client`` never
      held that role to begin with; its ``connected`` list is always the
      real connected target(s) (and such an entry only exists at all because
      at least one such target exists).

    ``cases`` is ``cases_for_client(client)`` NARROWED BY ``case_access`` — the
    same list the caller would otherwise have to filter itself, kept under its
    own key so the "connected to Us via case NNNN" display doesn't have to
    re-derive it from ``labels``. It is filtered HERE, not by the view, so that
    nothing this function returns — neither key — can carry a document number
    the viewer may not read.

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

    conn_qs = Connection.objects.filter(
        anchor_client=client, anchor_role__in=directly_held_labels,
    )
    if case is not None:
        conn_qs = conn_qs.filter(Q(case__isnull=True) | Q(case=case))
    else:
        conn_qs = conn_qs.filter(case__isnull=True)
    conn_qs = _scoped(conn_qs, user, scope).select_related("target_client").order_by("target_client__name")

    # {target_role: {target_client_id: target_client_name}} — an inner dict
    # (not a set of tuples) so the SAME target client reached via more than
    # one Connection row (e.g. a general row and a case-scoped one for the
    # identical pair, deliberately allowed to coexist — see ``Connection``'s
    # own docstring) collapses into a single entry, per the "DISTINCT target
    # clients" rule above. Insertion order follows the query's own
    # ``order_by("target_client__name")``, and a plain dict preserves
    # insertion order, so the final list comes out name-sorted for free.
    connected_by_role: dict = {}
    for row in conn_qs:
        connected_by_role.setdefault(row.target_role, {})[row.target_client_id] = row.target_client.name

    labels_out = []
    for label in LABEL_KEYS:
        is_case = label in case_labels
        is_manual = label in manual_labels
        directly_held = is_case or is_manual
        connected_targets = [
            {"id": cid, "name": name}
            for cid, name in connected_by_role.get(label, {}).items()
        ]
        if not directly_held and not connected_targets:
            continue
        if directly_held:
            entry = {
                "label": label,
                "label_fa": FIELD_LABELS[label],
                "source": "case" if is_case else "manual",
                "also_manual": is_case and is_manual,
                "removable": label in own_manual_labels,
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

    return {"labels": labels_out, "cases": case_rows}


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
    """
    if scope == "own":
        return qs.filter(actor=user)
    return qs


def _timeline_entry(kind: str, action: str, *, subject: str = "",
                    comment: str = "", actor_name: str = "",
                    actor_role_label: str = "", created_at=None,
                    case_id=None, doc_no: str = "", open_url: str = "") -> dict:
    """One row of ``client_timeline``, in the ONE shape every source produces.

    Every key is present on every entry, whichever source it came from — a
    case-derived row carries an empty ``comment`` and a native row carries
    ``case_id=None``/``doc_no=""``/``open_url=""`` — so the template walks a
    single uniform list and never branches on where a row came from. ``kind``
    is there for the one thing a template legitimately WANTS to differ on
    (linking a case-derived row through to its case), not for reconstructing
    the rest of the shape.

    ``open_url`` is where a case-derived row's link actually goes, built by
    ``marketing/access.py::case_open_url`` — the SAME function
    ``marketing/views.py::_visible_case_rows`` uses for the Cases tab of the
    same page, so the two tabs cannot send the same viewer to two different
    places for the same case. It is never a bare ``cases:case_detail`` URL: a
    dual-seat viewer has to pass through their own Commercial seat or the case
    page bounces them. Empty on every non-case row.
    """
    return {
        "kind": kind,
        "action": action,
        "action_label": ClientEventAction.ALL_LABELS.get(action, action),
        "subject": subject or "",
        "comment": comment or "",
        "actor_name": actor_name or "",
        "actor_role_label": actor_role_label or "",
        "created_at": created_at,
        "case_id": case_id,
        "doc_no": doc_no or "",
        "open_url": open_url or "",
    }


def _registration_entry(client: Client) -> list:
    """The "who first added this company" row, DERIVED from ``cases.Client``.

    Returns a one-item list, or an empty one when the row must not be
    synthesised. Kept as a list rather than an ``Optional`` so the caller can
    concatenate it exactly like the other two halves of the timeline.

    WHY DERIVED, AND NOT A STORED ROW. The owner asked for the timeline to say
    who added a company, when, and in what role. A real ``CLIENT_REGISTERED``
    ``ClientEvent`` is written by exactly one code path —
    ``get_or_create_client``, the chart's own Attach/"+ Add company" flow — so
    every company that predates this app, and every company Commercial creates
    through ``cases/views.py::client_add``, would otherwise have a timeline that
    never mentions its own registration. ``cases.models.Client`` has carried
    ``created_by`` and ``created_at`` all along, which is the fact itself, so
    this reads it at the moment the timeline is rendered — precisely the way
    ``client_timeline`` already derives the whole CASE half at read time, and
    for the same two reasons: no backfill to run for history that already
    happened, and no second copy to keep in step. The cases app is untouched.

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


def client_timeline(client: Client, user, scope, *,
                    case_access: CaseAccess = NO_CASE_ACCESS) -> list:
    """One company's whole history, newest first — all three sources merged.

    THREE SOURCES, ONE LIST, in the shape this module already uses for labels
    (see the module docstring's "TWO SOURCES OF A LABEL" section), applied to
    history instead of tags:

    * NATIVE — ``marketing/models.py::ClientEvent`` rows, everything MARKETING
      did to this company in its own directory (registered it, tagged it,
      connected it, added or removed a contact). Scoped by ``(user, scope)`` —
      see ``_scoped_events`` for why history has to honour the same boundary
      the facts behind it do.
    * CASE-DERIVED — the company's cases, read live from ``cases.models.Case``
      and synthesised into ``ClientEventAction.CASE_CREATED`` entries using
      each case's OWN ``created_at`` and ``doc_no``. SCOPED BY ``case_access``,
      not by ``(user, scope)`` — see the next paragraph.
    * REGISTRATION — who first added the company at all, derived from
      ``cases.models.Client``'s own ``created_by``/``created_at`` when no
      stored ``CLIENT_REGISTERED`` row already says it. See
      ``_registration_entry`` for why it is derived rather than backfilled, and
      for why it can never double-report.

    WHY THE CASE HALF IS SCOPED, AND BY WHAT. It used to be a plain
    ``Case.objects.filter(client=client)`` with no filtering at all, on the
    reasoning that case-derived facts are shared truth. Half of that is right
    and half of it leaked: a case's EXISTENCE is shared truth, but this half of
    the timeline does not merely say a case exists — it prints the case's
    document number, the commercial expert's name frozen onto it, and a link
    straight into it. That is case IDENTITY, governed by
    ``marketing/access.py::case_access_for`` like every other case list in this
    app, and without it the Timeline tab of the company detail page listed
    exactly the document numbers the Cases tab of the SAME page had just
    correctly refused to show. ``case_access`` is that decision, taken by the
    caller from the request and applied here through the same
    ``scope_case_rows`` the Cases tab uses; ``NO_CASE_ACCESS`` (no cases at
    all) is the fail-closed default for a caller that passes nothing.

    WHY THE CASE HALF IS DERIVED AT READ TIME rather than the cases app
    writing ``ClientEvent`` rows as it goes. Teaching ``cases/services.py`` to
    write into a Marketing table would put Marketing's history model on the
    critical path of case creation — a change to another app's business logic,
    which this round was explicitly told not to make. Deriving here costs one
    extra query on one detail page and, unlike a mirror table, can never drift
    out of sync with the cases it describes: there is no backfill to run for
    cases created before this existed, and no second copy to keep correct when
    a case is renumbered. The trade-off is accepted deliberately: this half of
    the timeline is a VIEW of case data, not a record Marketing owns.

    ``actor_name`` on a case-derived entry is the case's own
    ``commercial_expert_display`` — the name the cases app FROZE onto the case
    when it was created (see ``cases/services.py::freeze_commercial_expert``),
    never a live re-derivation from ``case.created_by``, so this half of the
    timeline keeps the same discipline the native half does. There is no
    frozen role label to go with it, so ``actor_role_label`` is honestly empty
    rather than guessed at.

    Returns a list of ``_timeline_entry`` dicts, newest first. Ties (two rows
    with the identical timestamp) keep native rows ahead of case-derived ones:
    Python's sort is stable and native rows are appended first.
    """
    entries = [
        _timeline_entry(
            "event", ev.action,
            subject=ev.subject, comment=ev.comment,
            actor_name=ev.actor_name, actor_role_label=ev.actor_role_label,
            created_at=ev.created_at,
        )
        for ev in _scoped_events(
            ClientEvent.objects.filter(client=client), user, scope,
        )
    ]
    entries += _registration_entry(client)
    # Rows first, filter, THEN synthesise entries — ``scope_case_rows`` keys off
    # ``case_id`` and returns the rows otherwise untouched, so everything the
    # entry needs rides along and a case this viewer may not open never reaches
    # ``_timeline_entry`` at all. Its ``doc_no`` is therefore never built into a
    # string that could leak through some other key.
    case_rows = [
        {
            "case_id": c.pk, "doc_no": c.doc_no, "created_at": c.created_at,
            "expert": c.commercial_expert_display,
        }
        for c in Case.objects.filter(client=client).only(
            "id", "doc_no", "created_at", "commercial_expert_display",
        )
    ]
    entries += [
        _timeline_entry(
            "case", ClientEventAction.CASE_CREATED,
            subject=row["doc_no"],
            actor_name=row["expert"],
            created_at=row["created_at"],
            case_id=row["case_id"], doc_no=row["doc_no"],
            open_url=case_open_url(case_access, row["case_id"]),
        )
        for row in scope_case_rows(case_rows, case_access)
    ]
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return entries


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
        "phone_prefix": contact.phone_prefix,
        "phone": contact.phone,
        "phone_ext": contact.phone_ext,
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

    Ordered by ``CompanyContact.Meta.ordering`` (last name, then first), and
    ``select_related`` on the role so a list of contacts costs two queries,
    not one per row.
    """
    contacts = _scoped(
        CompanyContact.objects.filter(client=client), user, scope,
    ).select_related("role")
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
                gender: str, role=None, phone_prefix: str = "",
                phone: str = "", phone_ext: str = "",
                email: str = "") -> CompanyContact:
    """Add one person at ``client``, stamped ``created_by=user``.

    Keyword-only past ``client``/``user`` on purpose: this takes eight
    optional-looking strings, and a positional call site would be unreadable
    and one silent swap away from filing a phone number as an extension.

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
    imports, backfills — that the model itself deliberately permits.

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
    contact = CompanyContact.objects.create(
        client=client,
        first_name=first_name,
        last_name=last_name,
        role=_resolve_contact_role(role),
        gender=gender,
        phone_prefix=(phone_prefix or "").strip(),
        phone=(phone or "").strip(),
        phone_ext=(phone_ext or "").strip(),
        email=(email or "").strip(),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
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
