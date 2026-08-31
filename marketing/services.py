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
from cases.constants import MarketingLabel
from cases.models import Case, Client

from .models import ClientLabel

# The twelve labelable keys — NOT all nineteen chart fields. See
# ``cases.constants.MarketingLabel``'s own docstring for why only these
# twelve (of the chart's nineteen fields) describe a business role a case's
# client could actually hold.
FIELD_LABELS = {k: v for k, v in MarketingLabel.CHOICES}
LABEL_KEYS = tuple(k for k, _ in MarketingLabel.CHOICES)


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
    """
    qs = Client.objects.all()
    query = (query or "").strip()
    if query:
        qs = qs.filter(name__icontains=query)
    return list(qs.order_by("name")[:limit])


def get_or_create_client(name: str, user) -> Client:
    """The client named ``name`` — existing (case-insensitive match), or freshly
    registered exactly the way ``cases/views.py::client_add`` already does:
    a sequential code from ``cases.services.next_client_code()`` and
    ``created_by`` stamped to ``user``.

    Matches on ``name__iexact`` first, the same check
    ``cases.forms.ClientForm.clean_name`` performs, since ``Client.name``'s
    database-level uniqueness is case-sensitive (sqlite has no
    case-insensitive collation here) and would otherwise let "Foolad Sanat"
    and "foolad sanat" become two rows. If two concurrent requests both miss
    that check and race to create the same name, the loser's ``IntegrityError``
    (raised by ``Client.name``'s unique constraint) is caught and resolved by
    re-reading the winner's row — the same race ``client_add`` already
    tolerates implicitly via ``next_client_code()``'s own atomic counter,
    just made explicit here since this path (unlike ``client_add``'s own
    form-backed one) can be called concurrently from independent JSON
    requests without a form re-render in between to catch it.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A name is required.")
    existing = Client.objects.filter(name__iexact=name).first()
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
        existing = Client.objects.filter(name__iexact=name).first()
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

        [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "sub"},
         {"case_id": 55, "doc_no": "TE-2603-012-KA", "label": "owner"}]

    ``label`` is ``case.marketing_label`` or, when that field was left
    blank, ``"owner"`` (the blank-defaults-to-owner rule).
    """
    cases = Case.objects.filter(client=client).only("id", "doc_no", "marketing_label")
    return [
        {"case_id": c.pk, "doc_no": c.doc_no, "label": _effective_label(c)}
        for c in cases
    ]


def label_counts(user, scope) -> dict:
    """``{label key: how many companies carry it}`` for all twelve keys.

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
         "cases": [{"case_id": 41, "doc_no": "IN-2601-007-KA", "label": "owner"}]}

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
