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

Case data itself (which clients have which cases, and each case's effective
label) is SHARED TRUTH, not Marketing-owned data, so every case-derived read
in this module is deliberately UNSCOPED: every viewer — Expert, Supervisor,
or the view-only GM — sees the same case-derived facts. Manual ``ClientLabel``
rows are the one thing that stays scoped, the same way ``Entity`` used to be:
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

from .models import ClientLabel

# The fourteen labelable keys — NOT all nineteen chart fields. Twelve of
# them (``MarketingLabel.CHOICES``) also describe a business role a case's
# client could actually hold — see ``cases.constants.MarketingLabel``'s own
# docstring for why only those twelve. The other two, rival and supplier
# (``MarketingLabel.MANUAL_ONLY_CHOICES``), can NEVER come from a case —
# Case.marketing_label's own choices deliberately stop at the twelve, so a
# company only ever picks up these two labels via a manual ``ClientLabel``
# row. Deriving from the SAME combined list ``ClientLabel.label``'s own
# ``choices`` uses (see marketing/models.py) keeps this module's notion of
# "every labelable key" from drifting apart from what the model actually
# accepts — every function below that walks ``LABEL_KEYS`` generically
# (``label_counts``, ``companies_for_label``, ``connections_of_client``,
# ``labels_for_clients``, ``is_label``) therefore already works correctly
# for "rival"/"supplier" too, with no special-casing: the case side of the
# merge simply never matches them (no ``Case`` row can ever carry that
# ``marketing_label``), so they end up genuinely manual-tag-only in
# practice, which is exactly the desired behaviour.
_ALL_LABEL_CHOICES = MarketingLabel.CHOICES + MarketingLabel.MANUAL_ONLY_CHOICES
FIELD_LABELS = {k: v for k, v in _ALL_LABEL_CHOICES}
LABEL_KEYS = tuple(k for k, _ in _ALL_LABEL_CHOICES)


def is_label(key: str) -> bool:
    return key in FIELD_LABELS


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
    """
    if add:
        ClientLabel.objects.get_or_create(client=client, label=label, created_by=user)
    else:
        ClientLabel.objects.filter(client=client, label=label, created_by=user).delete()


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
    """``{label key: how many companies carry it}`` for all fourteen keys.

    Two queries total (one grouped read over ``Case``, one over
    ``ClientLabel``), regardless of client count — this is what
    ``marketing/views.py::home()`` uses for the chart's badge numbers,
    replacing an earlier version that called ``companies_for_label`` once per
    key (twelve calls, each several queries — an N+1 pattern on a page every
    viewer loads).
    """
    counts: dict = {k: set() for k in LABEL_KEYS}
    for client_id, marketing_label in Case.objects.values_list("client_id", "marketing_label"):
        counts[marketing_label or MarketingLabel.OWNER].add(client_id)
    for client_id, label in _scoped(ClientLabel.objects.all(), user, scope).values_list("client_id", "label"):
        if label in counts:
            counts[label].add(client_id)
    return {k: len(v) for k, v in counts.items()}


def labels_for_clients(client_ids, user, scope) -> dict:
    """``{client_id: [{"label","label_fa","source","removable"}, ...]}`` for
    every id in ``client_ids`` — the same manual+case-derived merge
    ``connections_of_client`` does for one client, batched across many in a
    constant number of queries. Used by ``client_search`` so the Companies
    tab's "All" view can show label chips without a per-row round trip.
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
    first — or, when ``case_id`` is given, exactly that one case. This is the
    admin/GM-only "us" card search behind
    ``marketing/views.py::all_cases_search``; ordinary Marketing viewers never
    reach it (see ``access.Access.is_gm_or_admin``).

    UNSCOPED, like ``us_connections`` above and for the identical reason:
    case data is shared truth owned by Commercial/Technical/Supply, not
    Marketing, so this reads every case regardless of who created it — there
    is no ``(user, scope)`` pair to narrow by here, unlike the manual-tag
    functions further up this file.

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
def companies_for_label(label: str, user, scope) -> list:
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
      more authoritative one.
    * ``case_numbers`` — the ``doc_no`` of every case that gave this client
      the label. Present (and non-empty) only when ``source == "case"``.
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
    case_rows = Case.objects.filter(
        _case_label_query(label)
    ).only("id", "doc_no", "client_id")
    case_numbers_by_client: dict = {}
    for c in case_rows:
        case_numbers_by_client.setdefault(c.client_id, []).append(c.doc_no)
    case_client_ids = set(case_numbers_by_client.keys())

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
        if is_case:
            entry["case_numbers"] = case_numbers_by_client[client.pk]
        results.append(entry)
    return results


def connections_of_client(client: Client, user, scope) -> dict:
    """The label/case report Inquiry shows when a COMPANY (not "us") is the focus.

    The mirror image of ``companies_for_label``: instead of "one label -> its
    companies", this is "one client -> its labels", using the identical
    merge rule and the identical per-entry field meanings (``source``,
    ``also_manual``, ``removable``, ``case_numbers`` — see
    ``companies_for_label``'s docstring for what each means).

    Returns, e.g.::

        {"labels": [
            {"label": "owner", "label_fa": "کارفرمای اصلی — OWNER / CLIENT",
             "source": "case", "also_manual": False, "removable": False,
             "case_numbers": ["IN-2601-007-KA"]},
            {"label": "sub", "label_fa": "پیمانکار جزء — SUBCONTRACTOR",
             "source": "manual", "also_manual": False, "removable": True},
         ],
         "cases": [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "owner",
                    "status": "WITH_TECHNICAL", "status_fa": "بدون نتیجه"}]}

    ``labels`` lists only labels that actually apply to ``client``, in
    ``LABEL_KEYS`` order. ``cases`` is exactly ``cases_for_client(client)`` —
    the same list, kept under its own key so the "connected to Us via case
    NNNN" display doesn't have to re-derive it from ``labels``.
    """
    case_rows = cases_for_client(client)
    case_labels: dict = {}
    for row in case_rows:
        case_labels.setdefault(row["label"], []).append(row["doc_no"])

    manual_labels = set(
        _scoped(ClientLabel.objects.filter(client=client), user, scope)
        .values_list("label", flat=True)
    )
    own_manual_labels = set(
        ClientLabel.objects.filter(client=client, created_by=user).values_list("label", flat=True)
    )

    labels_out = []
    for label in LABEL_KEYS:
        is_case = label in case_labels
        is_manual = label in manual_labels
        if not (is_case or is_manual):
            continue
        entry = {
            "label": label,
            "label_fa": FIELD_LABELS[label],
            "source": "case" if is_case else "manual",
            "also_manual": is_case and is_manual,
            "removable": label in own_manual_labels,
        }
        if is_case:
            entry["case_numbers"] = case_labels[label]
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
