"""The Marketing unit's own screens.

TWO screens now, and they are deliberately separate pages rather than two tabs
of one:

* THE MARKETING WORKSPACE (``home``) — the project role chart
  (``marketing/templates/marketing/_role_chart.html`` +
  ``marketing/static/marketing/js/chart_interact.js``), and nothing else on it.
  There is no tab strip any more. Two different second tabs lived here across
  earlier sessions — an entity-directory grid the owner replaced with the
  click-on-chart design the chart now uses, and after it a flat "Companies"
  directory — and both are gone. A one-entry tab strip is not navigation, it is
  decoration, so when the second tab went the strip went with it and that view
  no longer has a ``tab`` concept at all.
* THE COMPANY DIRECTORY (``directory`` -> ``company_detail`` ->
  ``contact_add`` / ``contact_remove``, and ``report_add`` -> ``report_create``)
  — the section that replaced that removed "Companies" tab,
  built as its own set of URLs with a row-per-company list and a detail page
  per company. It reads the SAME shared ``cases.Client`` directory and the same
  ``ClientLabel`` rows the chart reads, through the same
  ``marketing/services.py`` functions, so the two screens can never disagree
  about which companies exist or which labels they carry. It is modelled, on
  purpose, on the two case screens a reader of this platform already knows: the
  list is the case archive (same ``data-combo`` filter controls, same
  ``table.data`` in a ``.vscroll`` list, same "N of M" count), and the detail
  page is the case detail page (same ``.card``/``.card-head`` panels, same tab
  strip, same ``.timeline`` markup and the same ``|jalali`` stamp on every
  row).
* REMINDERS (``reminder_add``, ``reminder_list``, ``reminder_retime``,
  ``reminder_done``) — a person's own private notes-to-self about a company,
  set from the company detail page and listed on a screen of their own. They
  hang off the same directory and follow the same screen conventions, but they
  are the one thing in this app that NOBODY else can see, including a
  Supervisor, the General Manager and the platform admin — see the section
  above those views, ``marketing/models.py::Reminder``, and
  ``marketing/reminders.py``, which owns the due check and its cache.

WHO MAY OPEN IT, AND HOW MUCH THEY GET. See ``marketing/access.py`` for the
actual decision (``access_for``) — this is the plain-language version of it:

* Marketing Expert — sees and edits only the manual tags THEY THEMSELVES
  added, per client. This is their own list, not a filtered view of a
  bigger one.
* Marketing Supervisor — sees and edits the union of every Marketing
  Expert's manual tags. A supervisor who cannot see the work of their own
  unit is not supervising it.
* The platform's actual General Manager (``profile.is_general_manager``) —
  sees that same union, but VIEW ONLY: every mutating endpoint below refuses
  them independently of whatever the page hides, because a GM who can browse
  is not the same grant as a GM who can edit someone else's directory.
* The platform admin — now gets that SAME view-only, union-of-everyone scope
  as the General Manager (``access.Access.is_gm_or_admin`` covers both).
  Reachable via a new button in the cases archive page (built elsewhere, a
  separate parallel phase), not via a hidden nav link in this app's own
  sidebar. That distinction still matters: a hidden nav link would not be a
  permission on its own, so the grant is still enforced here, on the URL, and
  still answers 403 to a direct GET from anyone this decision does not cover.
* Commercial / Technical / Supply, at every rank — no.

Case-derived facts (which clients have which cases, and each case's
effective label) are never gated by ``scope`` at all — see
``marketing/services.py``'s module docstring for why: that is shared truth
owned by Commercial/Technical/Supply, not Marketing-owned data, so every
viewer who can open this page at all sees the same case-derived facts.

Anyone refused the page gets the same short page with a 403 on it, inside
their own navigation, rather than a redirect — a redirect would have to choose
a destination for a seat kind this app knows nothing about, and "you may not
open this" is the honest answer to give.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from cases.models import Case, Client

from . import reminders, rolechart, services
from .access import (
    access_for, can_reach_marketing, case_access_for, case_open_url,
    marketing_seat_role, scope_case_rows,
)
from .forms import ContactForm, ReminderForm, ReminderTimeForm, ReportForm
from .models import ClientEventAction, ContactRole, ReminderState, ReportOption


def _int_or_none(raw):
    """``raw`` as an int, or None — never an exception.

    The same soft parse this file already applies to every optional id that
    arrives from a request (``home``'s ``?case=``, ``connection_toggle``'s
    ``case_id``, ``all_cases_search``'s ``case_id``): an absent, blank or
    unparseable value means "nothing was chosen", not a 400. Written once here
    for the report flow rather than inlined a fourth time.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _case_open_prefix(case_access) -> str:
    """The chart's client-side half of ``access.case_open_url``.

    The panel builds its own case URLs in JavaScript from a numeric id it only
    has client-side, so it cannot call ``case_open_url`` per row. It gets the
    part that does not depend on the id instead: an empty string when the viewer
    may open a case straight (the admin/GM tier), or the seat-switch prefix that
    the id's own URL is appended to, URL-encoded, by the JS. Same destination,
    same reasoning — see ``access.case_open_url`` for why the switch is there.
    """
    if not case_access.can_open or not case_access.seat_role_id:
        return ""
    return "%s?next=" % reverse(
        "people:activate_role", args=[case_access.seat_role_id])


@login_required
def home(request):
    """The Marketing workspace.

    Every field on the chart is its own clickable card, and every one of the
    twenty-one now has a real data source — there are no inert cards left. The
    twenty labelable fields (``services.LABEL_KEYS``) are counted by
    ``services.label_counts``: each badge is how many companies currently carry
    that label, manual or case-derived, scoped to this viewer. The
    twenty-first, "us", is counted by ``services.us_connections`` instead
    (every client with a case, unscoped).

    There used to be a pre-seeded ``{"project": 0, "phase": 0, "laboratory": 0,
    "tpi": 0}`` here, from the round when those four cards had nothing behind
    them. All four became manual-only tags (see
    ``cases/constants.py::MarketingLabel.MANUAL_ONLY_CHOICES``), so
    ``label_counts`` returns a real, possibly nonzero count for each and the
    seed had been overwritten unconditionally ever since. ``rolechart.build``
    treats a missing key as zero anyway, so a card with no companies still
    draws exactly as it did.
    """
    access = access_for(request)
    if not access.can_view:
        # A person who HOLDS a Marketing seat but is sitting in another one
        # (the dual Marketing/Commercial seat this round is about, arriving from
        # the "Marketing chart" button on a case page) is not refused — they are
        # sent through the app's own seat switch and straight back here, which is
        # exactly what the sidebar accordion already does for every entry under
        # an inactive Marketing role. This is NOT a widening of ``access_for``:
        # the switch only ever activates a PersonRole this same person already
        # holds, and this view then re-runs ``access_for`` against the new
        # active seat, so anyone it refuses is still refused. It also cannot
        # loop — after the switch the active seat IS the Marketing one, so
        # ``can_view`` is True and this branch is not reached again.
        seat = marketing_seat_role(request)
        if seat is not None:
            return redirect("%s?%s" % (
                reverse("people:activate_role", args=[seat.pk]),
                urlencode({"next": request.get_full_path()}),
            ))
        return render(request, "marketing/denied.html", status=403)

    counts = dict(services.label_counts(request.user, access.scope))
    counts["us"] = len(services.us_connections(request.user))

    case_access = case_access_for(request, access)
    context = {
        "chart": rolechart.build(counts),
        "can_edit": access.can_edit,
        "is_admin_tier": access.is_gm_or_admin,
        # Whether the chart's "cases connected to Us" panel may make its rows
        # clickable at all. Every row it is given is already one this viewer may
        # open (the endpoints behind the panel are scoped by the same
        # ``case_access``), so this is a straight yes/no rather than a per-row
        # test — see chart_interact.js's renderUsCasesPanelContent.
        "can_open_cases": case_access.can_open,
        "case_open_prefix": _case_open_prefix(case_access),
    }
    # An optional "jump straight into this case's marketing connections" deep
    # link (?case=123), the entry point a new button on the cases archive
    # page (built in a later phase) will use. Only the param is wired through
    # here — the frontend consumes it and gracefully handles a case id that
    # doesn't exist or doesn't parse, so no lookup happens on this end.
    deep_link_case_id = None
    raw_case = request.GET.get("case")
    if raw_case is not None:
        try:
            deep_link_case_id = int(raw_case)
        except (TypeError, ValueError):
            deep_link_case_id = None
    if deep_link_case_id is not None:
        context["deep_link_case_id"] = deep_link_case_id
    return render(request, "marketing/home.html", context)


# --------------------------------------------------------------------------- #
# The client directory: search, register, tag, and the "what is connected"
# reports a selection produces. Every route below is JSON-only, gated by the
# same ``access_for`` decision as the page itself, and re-checked independently
# of whatever the calling template shows or hides — see the module docstring.
# --------------------------------------------------------------------------- #
def _client_json(client: Client) -> dict:
    return {"id": client.pk, "name": client.name, "code": client.code}


def _label_or_400(request, key_param="label"):
    label = (request.GET.get(key_param) or request.POST.get(key_param) or "").strip()
    if not services.is_label(label):
        return None, JsonResponse({"ok": False, "error": "Unknown label."}, status=400)
    return label, None


@login_required
def client_search(request):
    """GET ?q= -> the shared client directory. Read-only, so every viewer who
    can open the page at all (including the view-only GM) may search.

    Each result carries its own ``labels`` (manual + case-derived, same shape
    ``client_connections`` returns) so a caller listing companies can show
    label chips without a second round trip per row — batched in a constant
    number of queries via ``services.labels_for_clients``, not one query per
    result. The chart's own "+ Add company" panel
    (chart_interact.js's ``CFG.clientSearchUrl``) is the live caller; the
    ``labels`` field is also what the separate company-directory section
    reads.
    """
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    query = request.GET.get("q") or ""
    clients = services.search_clients(query)
    labels_map = services.labels_for_clients([c.pk for c in clients], request.user, access.scope)
    return JsonResponse({"ok": True, "clients": [
        dict(_client_json(c), labels=labels_map.get(c.pk, [])) for c in clients
    ]})


@login_required
@require_POST
def client_create(request):
    """POST name= -> register a new client. Called by the chart's own
    "+ Add company" panel (chart_interact.js's ``buildCreateRow``, via
    ``CFG.clientCreateUrl``) when a search finds no match. Gated on
    ``can_edit`` alone, same as every other mutating endpoint here."""
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "A name is required."}, status=400)
    client = services.get_or_create_client(name, request.user)
    return JsonResponse({"ok": True, "client": _client_json(client)})


@login_required
def label_companies(request):
    """GET ?label= -> the companies a chart card shows when clicked.

    WHICH COMPANIES are listed is unscoped by case access, deliberately —
    holding a role is shared truth, and narrowing it would make the list
    disagree with the badge on the card. The ``case_numbers`` each row carries
    IS scoped, by the same rule ``us_connections`` states in one line: document
    numbers are case data and are not everyone's to read. See
    ``services.companies_for_label``.
    """
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    label, err = _label_or_400(request)
    if err is not None:
        return err
    companies = services.companies_for_label(
        label, request.user, access.scope,
        case_access=case_access_for(request, access))
    return JsonResponse({"ok": True, "label": label, "companies": companies})


@login_required
@require_POST
def label_toggle(request):
    """POST client_id=, label=, add=1|0 -> add/remove THIS USER's own manual tag."""
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    label, err = _label_or_400(request)
    if err is not None:
        return err
    client_id = request.POST.get("client_id")
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=400)
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=404)
    add = (request.POST.get("add") or "1").strip() not in ("0", "false", "False", "")
    services.toggle_manual_label(client, label, request.user, add)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def connection_toggle(request):
    """POST anchor_client_id=, anchor_role=, target_client_id=, target_role=,
    add=1|0, and optionally case_id= -> create/remove the directed
    (anchor_client, anchor_role) -> (target_client, target_role) ``Connection``
    the chart's Attach wizard stages when a viewer connects one company to
    ANOTHER company under a different card, rather than tagging the anchor
    itself — see ``services.create_connection``/``remove_connection``. Same
    shape and gating as ``label_toggle`` above, just for the two-client edge
    instead of a single client's own tag.

    General (case-less) by default — ``case_id`` is optional and fails SOFT
    exactly like ``client_connections``' own ``case_id`` read: an absent,
    unparseable, or unknown id just means "no case", not a 400/404. Passing
    one is how the chart's "case mode" (chart_interact.js's ``currentCase`` /
    ``withCaseId``) scopes a connection staged while that mode is active to
    the one case it was staged for, so it only becomes visible again when
    Inquiry is later run in that SAME case's own context — see
    ``Connection``'s own docstring and ``connections_of_client``'s ``case``
    param for the full visibility rule. Every caller that never sends
    ``case_id`` (every write outside case mode) keeps behaving exactly as
    before: a general, case-less edge.
    """
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    anchor_role, err = _label_or_400(request, "anchor_role")
    if err is not None:
        return err
    target_role, err = _label_or_400(request, "target_role")
    if err is not None:
        return err
    try:
        anchor_client_id = int(request.POST.get("anchor_client_id"))
        target_client_id = int(request.POST.get("target_client_id"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=400)
    anchor_client = Client.objects.filter(pk=anchor_client_id).first()
    target_client = Client.objects.filter(pk=target_client_id).first()
    if anchor_client is None or target_client is None:
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=404)
    case_id = request.POST.get("case_id")
    try:
        case_id = int(case_id) if case_id is not None else None
    except (TypeError, ValueError):
        case_id = None
    case = Case.objects.filter(pk=case_id).first() if case_id is not None else None
    add = (request.POST.get("add") or "1").strip() not in ("0", "false", "False", "")
    if add:
        services.create_connection(anchor_client, anchor_role, target_client, target_role, request.user, case=case)
    else:
        services.remove_connection(anchor_client, anchor_role, target_client, target_role, case, request.user)
    return JsonResponse({"ok": True})


@login_required
def client_connections(request):
    """GET ?client_id=&case_id= -> the label/case report Inquiry shows for a company.

    ``case_id`` is optional and fails SOFT, the same way ``all_cases_search``'s
    own ``case_id`` param already does: an absent, unparseable, or unknown id
    just means "no case context", not a 400/404 — Inquiry runs perfectly well
    with no case in view, and this is what puts case-scoped ``Connection``
    rows in front of ``connections_of_client`` (see its own docstring) rather
    than a hard requirement.
    """
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    client_id = request.GET.get("client_id")
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=400)
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=404)
    case_id = request.GET.get("case_id")
    try:
        case_id = int(case_id) if case_id is not None else None
    except (TypeError, ValueError):
        case_id = None
    case = Case.objects.filter(pk=case_id).first() if case_id is not None else None
    # THE CHART'S "cases connected to Us" PANEL IS FED FROM HERE, so this is one
    # of the places the owner's case rule has to bite: a Marketing expert who
    # also holds a Commercial seat sees their own commercial cases and nobody
    # else's; the admin/GM tier sees them all.
    #
    # BOTH KEYS BELOW ARE COVERED BY THAT ONE ARGUMENT, and they have to be.
    # This endpoint used to scope only ``cases``, by wrapping it here — which
    # left a response that answered ``"cases": []`` and, three lines further
    # down its own body, ``"case_numbers": ["CONF-B-222", "CONF-A-111"]`` for
    # the very cases it had just refused. Those numbers reach the UI:
    # chart_interact.js reads ``l.case_numbers`` into its per-card badge counts.
    # So the filter now goes INTO the service, which applies it to every
    # document number it emits under either key — see
    # ``services.connections_of_client``. WHICH LABELS appear is still
    # untouched: labels are Marketing's own data, scoped by ``access.scope``,
    # and a different question from which CASES this viewer may open.
    report = services.connections_of_client(
        client, request.user, access.scope, case=case,
        case_access=case_access_for(request, access))
    return JsonResponse({
        "ok": True,
        "client": _client_json(client),
        "labels": report["labels"],
        "cases": report["cases"],
    })


# THERE IS NO ``us_connections`` VIEW ANY MORE. It answered "every client with
# at least one case", was routed as ``marketing:us_connections``, and was handed
# to the chart as ``CFG.usConnectionsUrl`` — which no line of
# ``chart_interact.js`` (or any other client) ever read. The "us" card's own
# panel is fed by ``client_connections`` and ``all_cases_search`` instead. The
# route, the view and the config key went together; ``services.us_connections``
# itself stays, because ``home`` above still counts it for the "us" badge.


@login_required
def client_case_counts(request):
    """GET ?client_id= -> ``{"approved": n, "cancelled": n, "pending": n, "total": n}``.

    The three status counts the chart's "cases connected to Us" panel shows at
    its top for the company an Inquiry is currently about — approved,
    cancelled, and "no result" (``pending``). The bucketing itself is not
    decided here: it is ``services.case_status_counts``, which buckets by the
    exact same ``_status_fa`` rule that already labels every individual case
    row in that same panel, so the summary and the rows underneath it can
    never disagree.

    Gated on ``access.can_view``, like every other read endpoint in this file.

    THE NUMBERS DESCRIBE THE ROWS UNDERNEATH THEM, not the company's whole
    history. That is a change from this endpoint's first version, and it is the
    same correction ``_counts_over`` already made on the company detail page for
    the same reason: now that the panel's case rows are scoped to what this
    viewer may see (see ``client_connections``), a head that still counted every
    case the company ever had would sit directly above four rows and say
    fifty-seven. ``services.case_status_counts`` is documented as unscoped and
    stays that way — it is simply not the number this panel wants — so the
    buckets are counted here over exactly the visible rows, through the same
    ``_counts_over`` the detail page uses, which reads the bucket off each row
    rather than re-deriving it.
    """
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    client_id = request.GET.get("client_id")
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=400)
    client = Client.objects.filter(pk=client_id).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Unknown client."}, status=404)
    rows = scope_case_rows(
        services.cases_for_client(client), case_access_for(request, access))
    return JsonResponse({"ok": True, "counts": _counts_over(rows)})


@login_required
def all_cases_search(request):
    """GET ?q=&case_id= -> every case matching, by doc no. or client name.

    Two independent callers share this endpoint now, gated DIFFERENTLY on the
    frontend even though the server-side check below is the same
    ``access.can_view`` every other read endpoint in this file already uses:

    * The renamed "us" card's own flat all-cases BROWSE — still rendered
      admin/GM-only in chart_interact.js (``CFG.isAdminTier``), unchanged.
      Browsing every case in the system from an unrelated card is the
      broader, admin/GM-scoped capability the original admin/GM-only gate
      here was actually written for.
    * The chart's "case mode" search widget (chart_interact.js's
      ``rcCaseModeWidget`` / ``enterCaseMode``) — an ordinary Marketing
      Expert/Supervisor searching for ONE SPECIFIC case, by its own doc
      number or client name, so THEY can point the chart at it and finish
      that case's own relationship map. That is core Marketing work, not an
      admin/GM-only capability: an Expert/Supervisor already has ``can_edit``
      on every write this endpoint's result feeds into (Attach, connection
      staging, Inquiry), and this endpoint itself is read-only (a plain case
      lookup/search, no different in kind from ``client_search`` above,
      which every viewer already reaches). Refusing them here would make
      case mode unusable for the very seats who would use it day to day,
      for no matching security gain — the case DATA it returns (doc_no,
      client name/id, effective label) is exactly what every other
      case-derived read in this module already shows every viewer
      unscoped (see the module docstring's "Case-derived facts" note).
      So the gate below is deliberately widened from ``is_gm_or_admin`` to
      plain ``can_view``, matching this file's other read endpoints — this is
      a genuine, intentional widening of who may call this URL, not an
      oversight; only the "us" card's own BROWSE UI stays admin/GM-only, via
      its own frontend flag, unaffected by this change.

    WHO MAY CALL IT AND WHAT IT ANSWERS ARE NOW TWO DIFFERENT QUESTIONS. The
    widening above stays exactly as it is — an Expert/Supervisor still reaches
    this URL, so case mode still works from an ordinary Marketing seat — but the
    RESULTS are scoped by ``case_access_for`` like every other case list in this
    app. That closes the one thing the paragraphs above did not consider: a
    Marketing expert who ALSO holds a Commercial seat was being handed their
    colleagues' commercial cases here, by name and document number, which is
    precisely what the owner's rule forbids. The admin/GM tier still gets every
    case, which is what its own "us"-card browse was written for.

    A Marketing seat with no Commercial seat now gets an empty list here rather
    than the whole archive. That is the same answer this viewer gets everywhere
    else cases are listed (see ``access.case_access_for``), and it is a real
    narrowing of what case mode can find for them — deliberately so: pointing
    the chart at somebody else's case meant reading somebody else's case.
    """
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    query = request.GET.get("q") or ""
    case_id = request.GET.get("case_id")
    try:
        case_id = int(case_id) if case_id is not None else None
    except (TypeError, ValueError):
        case_id = None
    cases = services.search_all_cases(query=query, case_id=case_id)
    return JsonResponse({
        "ok": True,
        "cases": scope_case_rows(cases, case_access_for(request, access)),
    })


# --------------------------------------------------------------------------- #
# The company directory: a list of every company, and a page per company
# --------------------------------------------------------------------------- #
# This is the section that replaced the removed "Companies" tab (see the module
# docstring). Six plain HTML views, no JSON: the list, one company's detail
# page, the form that adds a contact to it, the POST that removes one, and the
# two steps of the "write a report on this company" flow. Every one of them goes
# through ``access_for`` exactly like the JSON endpoints above, and each
# mutating path (adding a contact, removing a contact, adding a contact role,
# recording a report) is gated on ``can_edit`` independently of whatever its
# template chose to draw — a hidden button is not a permission.


def _label_text_to_key() -> dict:
    """``{label display text: label key}`` for all twenty labelable roles.

    The directory's role filter is a ``data-combo`` <select> whose OPTION VALUE
    is the label's own display text, not its key, and this is the map that
    reads such a value back. That looks like the long way round until you look
    at what the archive does, which is what this page is modelled on: its
    Client filter posts ``"<name> (<code>)"`` — the exact text the Client
    column prints — and the server filters on that same text. The reason is
    that ONE value has to satisfy TWO filters at once. ``static/js/ui.js``
    hides table rows by comparing the control's value against the rendered cell
    text (instant, no round trip), and the server narrows the same list for a
    reader with no JavaScript. A key like ``"sub"`` would filter correctly on
    the server and, on the browser side, match every row whose label cell
    happens to contain those three letters — "SUBCONTRACTOR — PC" among them.
    Posting the display text keeps both ends comparing the identical string, so
    they cannot disagree about which rows belong.

    Built from ``services.FIELD_LABELS`` — the same single source of truth
    every other label display in this app reads — so a renamed role can never
    leave this map pointing at text no option prints any more.
    """
    return {text: key for key, text in services.FIELD_LABELS.items()}


@login_required
def directory(request):
    """The company directory: every company Marketing knows, filterable.

    MODELLED ON THE CASE ARCHIVE (``cases/views.py::archive`` +
    ``cases/templates/cases/archive.html``), which is what the owner asked for,
    and reusing its controls rather than imitating them: the two filters are
    ordinary ``<select data-combo>`` boxes that ``static/js/ui.js`` upgrades
    into the platform's searchable combo, they carry the same
    ``data-filter-for``/``data-filter-colname`` attributes ui.js's instant
    row-hiding pass reads, the count in the page head is the archive's own
    ``data-filter-count`` span, and the rows are a ``table.data`` inside a
    ``.vscroll .vscroll-list``.

    ONE DELIBERATE DEPARTURE FROM THE ARCHIVE: no scroll-slice loading. The
    archive windows its rows (``services.ARCHIVE_WINDOW`` +
    ``static/js/archive_stream.js``) because it can hold thousands of cases and
    a reader looks at twenty. The client table is a different size and
    ``marketing/services.py`` says so in three separate places — "the table is
    small (hundreds of rows, confirmed against production)" — which is why
    several functions there scan it in Python at all. At that size the whole
    directory is one modest page of HTML, and rendering it complete buys
    something the windowed version cannot have: the browser-side filters see
    EVERY row, so typing in either combo narrows the real list instantly with
    no fetch and no possibility of the two ends disagreeing about rows that
    were never sent. ``services.search_companies`` is therefore called with
    ``limit=None`` (uncapped) rather than its default 200 — a cap would
    silently truncate a directory that is allowed to grow past it.

    BOTH FILTERS WORK TWICE OVER, and that is not redundancy. With JavaScript
    on, ui.js hides non-matching rows as the reader types. With JavaScript off,
    the surrounding GET form submits to this same view and the ``fname`` /
    ``flabel`` parameters below narrow the list server-side through
    ``services.search_companies`` — the one definition of what the directory
    lists, shared with the chart. The two ends agree because they compare the
    same text; see ``_label_text_to_key``.

    Every read is scoped through ``access_for``: the manual half of a company's
    labels is an Expert's own work until a Supervisor or the GM looks at the
    union (see ``marketing/services.py``'s module docstring).
    """
    access = access_for(request)
    if not access.can_view:
        return render(request, "marketing/denied.html", status=403)

    # What arrived, echoed back verbatim so the re-rendered form can re-select
    # the option the reader picked — the archive's own ``f_active`` idiom.
    f_name = (request.GET.get("fname") or "").strip()
    f_label_text = (request.GET.get("flabel") or "").strip()
    # An unrecognised role text narrows nothing rather than raising: a filter
    # value can only reach this view from an option this page itself printed or
    # from a hand-edited URL, and the honest answer to the second is the
    # unfiltered list, not a 500. (``search_companies`` raises ValueError on an
    # unknown KEY, which is why the text is resolved before it is passed.)
    label_key = _label_text_to_key().get(f_label_text, "")

    # The full directory, unfiltered — this is what the two dropdowns offer, so
    # their option lists describe the whole directory rather than only the rows
    # the current filters leave. (The archive cascades its option lists instead;
    # it can afford to, because it re-derives them from an in-memory list it
    # already holds for other reasons. Here the equivalent would mean running
    # the whole manual+case-derived merge twice more per page load for a list
    # this small, and a role that vanishes from its own dropdown the moment you
    # pick a company is a worse trade than one that occasionally leaves the
    # table empty.)
    all_rows = services.search_companies(
        limit=None, user=request.user, scope=access.scope)
    rows = (
        all_rows if not (f_name or label_key)
        else services.search_companies(
            query=f_name, label=label_key, limit=None,
            user=request.user, scope=access.scope)
    )

    # Option lists. Names come from the directory itself; roles are every role
    # actually carried by a company in it, in ``services.LABEL_KEYS`` order (the
    # chart's own order), so the dropdown never offers a role that would return
    # an empty table.
    f_names = sorted({row["name"] for row in all_rows if row["name"]})
    carried = {lab["label"] for row in all_rows for lab in row["labels"]}
    f_labels = [services.FIELD_LABELS[key] for key in services.LABEL_KEYS if key in carried]

    return render(request, "marketing/directory.html", {
        "rows": rows,
        "total_count": len(all_rows),
        "filtered_count": len(rows),
        "f_names": f_names,
        "f_labels": f_labels,
        "f_active": {"name": f_name, "label": f_label_text},
        "can_edit": access.can_edit,
    })


# Which case rows a viewer sees on a company's detail page.
#
# THE RULE ITSELF NO LONGER LIVES HERE. It used to — a placeholder written when
# the shared helper did not exist yet, which narrowed to ``created_by ==
# request.user`` and therefore quietly answered "no cases" for the one person it
# was written for: a dual-seat Marketing/Commercial viewer's cases are stamped
# with their COMMERCIAL SEAT's User, not with the login they are signed in as.
# ``marketing/access.py::case_access_for`` is the real rule now (read its
# docstring for the owner's wording and for how the seat is resolved), and every
# case list in this app — this page, the chart's panel, the all-cases search —
# goes through that one decision. What is left here is presentation.
def _visible_case_rows(client, request, case_access) -> list:
    """``services.cases_for_client(client)`` narrowed to what this viewer may see.

    Each surviving row also picks up two display fields:

    * ``label_fa`` — the Persian display text for the role that row's ``label``
      key names. ``cases_for_client`` returns the KEY (it is consumed by the
      chart's JavaScript, which does its own lookup), and a table printing
      "owner" where every other screen in this app prints "کارفرمای اصلی —
      OWNER / CLIENT" would be the only place the role reads differently.
      Resolved through ``services.FIELD_LABELS`` — the same map the rest of this
      file uses — with ``.get`` falling back to the raw key so a row can never
      fail to render.
    * ``open_url`` — where this row's click actually goes, from
      ``access.case_open_url``. Computed per row rather than hard-coded in the
      template because a dual-seat viewer has to pass through their own
      Commercial seat to reach a case at all; see that function.
    """
    rows = scope_case_rows(services.cases_for_client(client), case_access)
    return [
        dict(row,
             label_fa=services.FIELD_LABELS.get(row["label"], row["label"]),
             open_url=case_open_url(case_access, row["case_id"]))
        for row in rows
    ]


def _counts_over(case_rows) -> dict:
    """``{"approved","cancelled","pending","total"}`` over exactly ``case_rows``.

    The company detail page's headline numbers have to describe the rows the
    reader can actually see underneath them (see ``_visible_case_rows``), and
    ``services.case_status_counts`` is documented as UNSCOPED — it counts every
    case the company has ever had, which is the right number for "how big is
    this company's history" and the wrong one for "how many of the cases in
    this table were approved".

    So the buckets are counted here, over the visible rows — but the RULE that
    decides which bucket a case falls in is not re-implemented: each row already
    carries the bucket's own display text in ``status_fa`` (put there by
    ``services.cases_for_client``), and ``services._STATUS_BUCKET_KEY`` is the
    map from that text to the key. Reading that one private name is deliberate
    and is the same judgement ``marketing/services.py`` itself documents when it
    calls ``cases.services._actor_snapshot``: the underscore marks it private to
    the app, not to the module, and it is a pure lookup table with no side
    effects. Copying the three-way bucketing here instead would create the
    second definition ``services.py`` went out of its way to avoid, and the two
    would disagree the first time a status moved between buckets.
    """
    counts = {"approved": 0, "cancelled": 0, "pending": 0, "total": 0}
    for row in case_rows:
        counts[services._STATUS_BUCKET_KEY[row["status_fa"]]] += 1
        counts["total"] += 1
    return counts


# One icon per timeline action, so a reader scanning a company's history can
# tell a tag from a contact from a case without reading a word — the same job
# the coloured dot does on the case timeline, done per action kind because this
# timeline mixes far more kinds of event than a case's does.
#
# Every name below is one the OFFLINE icon layer actually defines
# (``static/css/icons-offline.css``); that file is a fixed set of inline SVGs,
# not a CDN webfont, so an icon it does not define renders as a blank box.
# Removals deliberately do not reuse their own "added" icon — a timeline where
# LABEL_ADDED and LABEL_REMOVED look identical is a timeline you have to read
# twice.
#
# ``CASE_CREATED`` used to have an entry here (``fa-folder-plus``). The owner
# removed the derived "a case was opened for this company" row from the timeline
# outright, so the action no longer exists — see
# ``services.client_timeline`` — and its icon went with it rather than being
# left behind pointing at a key nothing can produce.
_TIMELINE_ICONS = {
    ClientEventAction.CLIENT_REGISTERED: "fa-building",
    ClientEventAction.LABEL_ADDED: "fa-tag",
    ClientEventAction.LABEL_REMOVED: "fa-eraser",
    ClientEventAction.CONNECTION_ADDED: "fa-link",
    ClientEventAction.CONNECTION_REMOVED: "fa-ban",
    ClientEventAction.CONTACT_ADDED: "fa-user-plus",
    ClientEventAction.CONTACT_REMOVED: "fa-trash",
    ClientEventAction.REPORT_ADDED: "fa-file-lines",
    # A case's business role swapping FROM one label TO another — the two-
    # headed arrow reads as an exchange, distinct from every icon above it
    # (LABEL_ADDED/REMOVED already own the tag/eraser pair, so this is not a
    # relabelling of them) and matches ``ev.subject_role``'s own frozen
    # "OLD → NEW" text (see marketing/services.py::log_case_role_change).
    ClientEventAction.CASE_ROLE_CHANGED: "fa-right-left",
}


def _timeline_with_icons(entries) -> list:
    """``services.client_timeline`` rows, each carrying the icon for its action.

    The entries are left otherwise untouched — same keys, same order, same
    frozen actor snapshots — because the template renders the SENTENCE for each
    action itself, exactly the way ``cases/templates/cases/case_detail.html``
    writes "Edited {{ ev.form_kind }} form" in the template rather than in
    Python. Only the icon is decided here: it is a fixed per-action lookup with
    no wording in it, and an eight-branch chain of ``{% if %}`` in the template
    to pick one class name would be markup pretending to be a dictionary.

    ``.get`` with a fallback rather than ``[...]``: a row written by an older
    version of this app, or one whose action was renamed since, must still
    render — the same forgiving rule ``ClientEvent.action_label`` follows.
    """
    return [dict(entry, icon=_TIMELINE_ICONS.get(entry["action"], "fa-circle-info"))
            for entry in entries]


def _contact_rows(client, request, access) -> list:
    """``services.list_contacts`` rows, each carrying whether THIS viewer may
    remove THAT contact.

    ``services.remove_contact`` is the rule, and this only predicts its answer
    so the detail page can draw the control on exactly the rows where pressing
    it would do something. That rule has two halves, and both are reproduced
    here and nowhere else:

    * ``can_edit`` — the view-only tier (the GM and the platform admin) removes
      nothing, whatever their scope. ``remove_contact`` itself does not check
      this, and does not need to: ``contact_remove`` below refuses them before
      the service is ever called, exactly as ``contact_add`` does.
    * scope — ``remove_contact`` is documented as SCOPE-AWARE rather than
      strictly owner-bound (a contact is a shared fact about the company, not
      one Expert's private opinion), so ``scope == "all"`` (the Marketing
      Supervisor, here) may remove any contact on the company, while an
      ordinary Expert may remove only their own — which is precisely the
      ``removable`` flag ``services._contact_row`` already puts on every row.

    Drawing the control is not the grant. ``contact_remove`` re-runs
    ``access_for`` and hands the same scope to the same service, so a
    hand-built POST from a viewer this function said False for still removes
    nothing.
    """
    return [
        dict(row, can_remove=bool(
            access.can_edit and (access.scope == "all" or row["removable"])))
        for row in services.list_contacts(client, request.user, access.scope)
    ]


def _pi_money_total(case_rows, case_access) -> str:
    """The company's total PI money over EXACTLY ``case_rows``, formatted — or "".

    THE OWNER ASKED THE COMPANY PAGE FOR THE NUMBER THE ARCHIVE ALREADY SHOWS,
    so this reuses that page's own two helpers rather than computing money a
    second time. ``cases/services.py::archive_attach_money`` returns the
    ``{case id: amount}`` map (VAT-inclusive PI grand totals), and
    ``cases/export_data.py::format_money_amount`` renders the sum — which is
    line for line what ``cases/views.py::archive`` does for its "Grand total
    (all PI)" banner, down to printing "—" for a genuine zero. There is
    deliberately no arithmetic here that the archive does not also do: a company
    page and an archive banner disagreeing about the same money would be worse
    than neither existing.

    OVER THE VISIBLE ROWS, NOT THE WHOLE COMPANY. ``case_rows`` is exactly what
    the Cases tab lists (``_visible_case_rows``, scoped by
    ``access.case_access_for``), for the reason ``_counts_over`` above already
    documents for the outcome counts: a total that summed cases the reader is
    not being shown would sit above a three-row table and quote a figure those
    three rows cannot add up to. A Commercial MANAGER now sees every case on the
    company (see ``access.case_access_for``), so their total is the company's
    real total; an expert's is the total of their own cases, and that is the
    honest answer for them.

    RETURNS "" — not "0", not "—" — WHEN THIS VIEWER MAY NOT SEE MONEY AT ALL.
    ``case_access.show_money`` is "money follows the cases" (see
    ``access._case_money_visible``): a viewer who may see these case rows may
    see what they are worth, and one who may see no case rows gets no figure —
    which is the same answer the empty ``ids`` guard below would have reached
    anyway, from the other direction. The empty string is what tells the
    template there is no figure to draw, as opposed to a figure that happens to
    be nothing. ``archive_attach_money`` is not called at all in that case —
    the same short-circuit ``cases/views.py::archive`` uses, so the expensive
    proforma decode is never paid for a reader who would not be shown the
    result.
    """
    if not case_access.show_money:
        return ""
    ids = [row["case_id"] for row in case_rows if row.get("case_id") is not None]
    if not ids:
        return ""
    from cases import services as case_services
    from cases.export_data import format_money_amount

    # ``archive_attach_money`` wants objects with a ``pk`` (it also stamps a
    # per-row display string onto each, which nothing here reads — the MAP is
    # what this function is after, exactly as the archive's drill-down banner
    # uses it). ``.only("id")`` because that is the only column it touches.
    gt_map = case_services.archive_attach_money(
        list(Case.objects.filter(pk__in=ids).only("id")))
    total = sum(gt_map.values()) if gt_map else 0.0
    return format_money_amount(total) if total else "—"


def _can_manage_roles(access) -> bool:
    """May this viewer ADD to the shared ``ContactRole`` vocabulary?

    The owner's rule is "a Marketing Supervisor AND the platform admin may add
    contact-role options; ordinary Marketing users may only pick an existing
    role", and that is ``Access.can_manage_config`` — one narrowly-named
    capability, decided once in ``marketing/access.py::access_for``, read here.

    THIS USED TO BE ``can_edit and scope == "all"``, WHICH LEFT THE ADMIN OUT.
    That expression names the Marketing Supervisor and nobody else, because
    ``access_for`` makes ``can_edit`` unconditionally False for both the admin
    and the General Manager. The reasoning recorded at the time was sound as
    far as it went — the view-only tier must not gain a write on this section's
    working data — but it answered the wrong question: a ``ContactRole`` is not
    working data, it is a configuration vocabulary, and on an install with no
    Marketing Supervisor the old expression meant NOBODY could create one.
    Since the contact form makes the role mandatory, that blocked contact
    creation outright.

    THE FIX IS NOT TO LOOSEN ``can_edit``. It stays False for the admin and the
    GM exactly as before, so neither gains a single write on a label, a
    connection or a contact; ``can_manage_config`` is a separate, additive
    grant that covers this one list and nothing else, and the GM does not have
    it. See ``Access.can_manage_config`` for the full reasoning.

    THE ADMIN'S ACTUAL SCREEN IS ``marketing/admin.py``, not this one:
    ``contact_add`` below is still gated on ``can_edit``, because ADDING A
    CONTACT is working data and the admin is view-only over it. So this
    function returning True for the admin does not, by itself, put a form in
    front of them — the Django admin's ``ContactRole`` registration is where
    they manage the list. It is read here so that the capability has ONE
    definition, and so a future in-app role-management screen gates on the same
    answer instead of re-deriving it.
    """
    return bool(access.can_manage_config)


def _can_manage_options(access) -> bool:
    """May this viewer ADD to the shared ``ReportOption`` vocabulary?

    THE SAME ONE CAPABILITY ``_can_manage_roles`` ABOVE READS, over the second
    of the two lists it governs (``marketing/models.py::ReportOption``, whose
    own docstring says so): a Marketing Supervisor and the platform admin, not
    the General Manager. Read that function for the whole argument — why a
    configuration vocabulary is not working data, why the answer is deliberately
    NOT ``can_edit and scope == "all"``, and why the admin holding it does not
    by itself put a write in front of them.

    A SECOND NAMED FUNCTION RATHER THAN ``access.can_manage_config`` INLINE, and
    rather than calling ``_can_manage_roles`` under a misleading name, for the
    reason that one exists at all: each vocabulary's views ask one narrowly-named
    question, so the day the two lists stop sharing a capability there is a
    place to say so instead of a bare flag threaded through four call sites.

    THE ROUTE IT NOW UNLOCKS IS THE POINT OF THIS FUNCTION. ``ReportOption``
    used to be manageable ONLY through ``marketing/admin.py``, and the Django
    admin needs ``is_staff`` — which a Marketing Supervisor does not have. So
    the one in-app screen that reads this capability (the report form) computed
    True for them and then pointed them at a login wall. ``ContactRole`` never
    had that problem because it has always had a second route, the inline
    "add a new role" field on the contact form; the report form now has the
    identical field, gated by ABSENCE in exactly the same way (see
    ``marketing/forms.py::ReportForm`` and ``report_create`` below).
    """
    return bool(access.can_manage_config)


@login_required
def company_detail(request, pk):
    """One company: its labels, its contacts, its cases and its timeline.

    MODELLED ON THE CASE DETAIL PAGE, deliberately, down to the markup: the
    same ``.page-head`` with chips beside the title, the same ``.infobox``
    summary boxes, the same ``.tabs``/``.tab`` strip over ``.tab-panel``
    panels, the same ``.card``/``.card-head`` panels inside them, and — for the
    timeline — the same ``<ul class="timeline">`` with ``.tl-action`` /
    ``.tl-meta`` / ``.tl-comment`` rows and the same ``|jalali`` filter on
    every stamp. A Marketing reader who already knows the case page should not
    have to learn a second visual language for the same kinds of information.

    THE FOUR THINGS ON IT, and where each comes from:

    * LABELS — ``services.labels_for_clients`` for this one company: the same
      manual + case-derived merge, scoped, that the chart and the directory
      list both show, so the chips here cannot disagree with the chips there.
    * CONTACTS — ``services.list_contacts``, which enforces the visibility rule
      itself: an ordinary Marketing user sees only the contacts they added, a
      Supervisor/GM/admin sees every one. This view passes the scope and does
      not filter anything a second time. Each row picks up one display flag,
      ``can_remove`` — see ``_contact_rows``.
    * CASES — ``_visible_case_rows``, scoped by ``access.case_access_for``: the
      one decision the chart's own panel and the all-cases search also go
      through, so this page and the chart can never show the same viewer two
      different sets of cases. ``_counts_over`` gives the approved / cancelled /
      no-result split of exactly those rows.
      ``services.case_status_counts`` is ALSO read, unscoped, purely so the
      page can say honestly how many cases the company has in total when the
      viewer is only being shown some of them.
    * TIMELINE — ``services.client_timeline``, the native ``ClientEvent`` rows
      plus the derived registration row, with one icon attached per row. WHICH
      ROWS appear is ``access.scope`` alone: the timeline no longer has a case
      half to scope, since the owner removed the synthesised "a case was opened"
      entries ("it is not needed to record that someone opened a case").
      ``case_access`` is passed all the same, and for a different job — some of
      those stored rows FROZE a case's document number into their own text when
      they were written (a report's ``subject``, a connection's ``comment``),
      and the actor-based scope says nothing about who may read a case number.
      It is the same one decision the Cases tab makes, applied to those frozen
      strings at read time; see ``services._redact_case_numbers``. No row is
      dropped by it. The CASES TAB above is still a separate code path
      (``_visible_case_rows``) with its own scoping.
    * REPORTS — ``services.list_reports``, which enforces its visibility rule
      itself through the SAME ``_scoped`` helper contacts use: an ordinary
      Marketing user sees only the reports they wrote, a Supervisor/GM/admin
      sees every one. It takes ``case_access`` for the same reason the timeline
      does, and it is the same ONE decision again: a report's attached case is
      NAMED only to a viewer who may see that case, and is otherwise shown
      unnamed rather than removed — the report is Marketing's data, the case
      number is not. Passed to the template for its own section.

    AND THE MONEY. ``pi_total_display`` is the total PI money over exactly the
    case rows above — see ``_pi_money_total``, which reuses the case archive's
    own ``archive_attach_money`` / ``format_money_amount`` rather than computing
    money a second time. It is drawn for exactly the viewers the Cases tab draws
    rows for: ``access._case_money_visible`` ties money visibility to case
    visibility, so a seat with cases here gets the total over them and a seat
    with no cases here (a Marketing-only seat) gets neither the rows nor a
    figure. Both context values below are what the template's own two-condition
    gate reads; see it for why it tests both.
    """
    access = access_for(request)
    if not access.can_view:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    # ONE decision, both tabs — see the TIMELINE bullet above.
    case_access = case_access_for(request, access)
    case_rows = _visible_case_rows(client, request, case_access)
    counts = _counts_over(case_rows)
    # Unscoped, company-wide — used ONLY to tell the reader that cases exist
    # which this page is not showing them. Never mixed into the four numbers
    # above, which describe the visible rows.
    all_counts = services.case_status_counts(client, request.user)

    return render(request, "marketing/company_detail.html", {
        "client": client,
        "labels": services.labels_for_clients(
            [client.pk], request.user, access.scope).get(client.pk, []),
        "contacts": _contact_rows(client, request, access),
        "case_rows": case_rows,
        "counts": counts,
        "hidden_case_count": max(all_counts["total"] - counts["total"], 0),
        # The archive's own "Grand total (all PI)" figure, over the visible rows
        # only — empty string when this viewer may not be shown money at all.
        "pi_total_display": _pi_money_total(case_rows, case_access),
        "show_money": case_access.show_money,
        "timeline": _timeline_with_icons(
            services.client_timeline(client, request.user, access.scope,
                                     case_access=case_access)),
        "reports": services.list_reports(client, request.user, access.scope,
                                         case_access=case_access),
        # Whether the reports list this viewer is looking at is the WHOLE list
        # or only their own — the same sentence, for the same reason, that
        # ``sees_all_contacts`` below produces for contacts, and decided by the
        # same thing (the SCOPE, not ``can_edit``), because reports reuse
        # contacts' visibility rule exactly.
        "sees_all_reports": access.scope == "all",
        "can_edit": access.can_edit,
        # Whether the contact list this viewer is looking at is the WHOLE list
        # or only their own rows — the page says so in words, because a scoped
        # list of two contacts is indistinguishable from a company that really
        # has two. It is the SCOPE that decides this and not ``can_edit``:
        # ``scope == "all"`` is exactly the population ``services.list_contacts``
        # shows everything to (a Marketing Supervisor, the GM and the platform
        # admin), and those three do not line up with ``can_edit`` on either
        # side — a Supervisor can edit AND sees everything, while the GM/admin
        # tier sees everything and can edit nothing. Deriving the sentence from
        # ``can_edit`` would tell a Supervisor they were seeing only their own
        # contacts while the table beside it showed the whole unit's.
        "sees_all_contacts": access.scope == "all",
    })


@login_required
def contact_add(request, pk):
    """Add one person at this company — a full page, not a modal.

    A PAGE, because that is what this platform does with a Django ``Form``:
    ``cases/templates/cases/client_form.html``,
    ``accounts/templates/accounts/user_form.html`` and
    ``people/templates/people/person_form.html`` are all the same shape — a
    ``method="post"`` form in a ``.card``, field errors in a ``ul.errorlist``
    under the field, form-level errors in a ``.flash.flash-error`` above them —
    and a modal would have to re-invent every one of those, plus a way to
    re-open itself carrying server-side errors. The modals in this app
    (``rc-modal-*`` on the chart) are JSON-driven pickers with no Django form
    behind them at all, which is a different problem.

    GATED ON ``can_edit``, checked here and not merely hidden on the detail
    page: the view-only tier (the General Manager and the platform admin — see
    ``access_for``) must not be able to write, and a template that does not
    draw a button is not a permission. Refused with the same 403 page the
    section's own gate uses, for the same reason the module docstring gives.

    The inline "add a new role" field is gated a second time, on
    ``_can_manage_roles``, and gated by ABSENCE: the form is constructed
    without that field for anyone who may not create roles, so a hand-built
    POST cannot reach the ``get_or_create`` below.

    ``services.add_contact`` does the writing (and the timeline row) — this
    view resolves the role and hands over ``cleaned_data``, so there is exactly
    one place in the app that creates a ``CompanyContact``.
    """
    access = access_for(request)
    if not access.can_view or not access.can_edit:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    can_manage_roles = _can_manage_roles(access)

    if request.method == "POST":
        form = ContactForm(request.POST, can_manage_roles=can_manage_roles)
        if form.is_valid():
            data = form.cleaned_data
            role = data.get("role")
            new_role = (data.get("new_role") or "").strip() if can_manage_roles else ""
            if new_role:
                # get_or_create, not create: ``ContactRole.name`` is globally
                # unique and re-typing a title that already exists means the
                # same thing as having picked it from the list, so it must not
                # be an IntegrityError. ``created_by`` is recorded on first
                # creation only, exactly as ``services.create_connection``
                # stamps it.
                role, _created = ContactRole.objects.get_or_create(
                    name=new_role,
                    defaults={"created_by": request.user},
                )
            services.add_contact(
                client, request.user,
                first_name=data["first_name"], last_name=data["last_name"],
                gender=data["gender"], role=role,
                phone_prefix=data["phone_prefix"], phone=data["phone"],
                phone_ext=data["phone_ext"], email=data["email"],
            )
            return redirect("marketing:company_detail", pk=client.pk)
    else:
        form = ContactForm(can_manage_roles=can_manage_roles)

    return render(request, "marketing/contact_form.html", {
        "client": client,
        "form": form,
        "can_manage_roles": can_manage_roles,
    })


@login_required
@require_POST
def contact_remove(request, pk, contact_id):
    """Remove one person from this company's contact list.

    POST-ONLY, and reached from a real ``<form method="post">`` in the contacts
    table rather than a link: this deletes a row, and a destructive action
    behind a GET is one prefetching browser or one crawled URL away from doing
    itself. ``@require_POST`` plus the CSRF token the form carries is the same
    protection every other mutating path in this app has.

    GATED EXACTLY LIKE ``contact_add`` ABOVE — ``can_view and can_edit``, the
    same 403 page — because it is the same kind of write on the same working
    data, and the view-only tier (the GM and the platform admin) must not reach
    either. Nothing about ``Access.can_manage_config`` applies here: that
    capability covers the ``ContactRole`` vocabulary, not the contacts filed
    under it.

    WHICH CONTACT MAY ACTUALLY GO is not decided here. ``access.scope`` is
    handed to ``services.remove_contact``, which is documented as scope-aware:
    an ordinary Marketing Expert deletes only rows they added, a Supervisor may
    delete any contact on the company. A request for a contact outside this
    viewer's scope, for a contact at some OTHER company, or for one that no
    longer exists, is indistinguishable from all the others by design — the
    service returns False and this view redirects back exactly as it would on
    success, so the response cannot be used to probe whether a contact exists.

    Redirects to the company page on both outcomes rather than rendering
    anything of its own: the contacts table it came from IS the confirmation,
    and the timeline row ``remove_contact`` writes is the record.
    """
    access = access_for(request)
    if not access.can_view or not access.can_edit:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    services.remove_contact(client, contact_id, request.user, access.scope)
    return redirect("marketing:company_detail", pk=client.pk)


# --------------------------------------------------------------------------- #
# Reports — the two-step "write a report on this company" flow
# --------------------------------------------------------------------------- #
# THE OWNER DESCRIBED TWO STEPS, and they are two views because they are two
# different KINDS of request, not because a wizard needs a step counter:
#
#   step 1 (``report_add``)     — choose a case to attach, or Skip. Reads only.
#   step 2 (``report_create``)  — pick options / write the text, then confirm.
#                                 Writes, and is therefore POST-only.
#
# Both are gated exactly like ``contact_add`` above — ``can_view and can_edit``,
# checked on the URL and not merely hidden on the detail page — because writing
# a report is a write on Marketing's working data, which the view-only tier (the
# General Manager and the platform admin) does not have. That is deliberate and
# is not in tension with those two seats SEEING every report: reading the unit's
# work and adding to it are two different grants, and this app has kept them
# apart since ``access_for`` was written.


def _report_case_choices(client, request, case_access) -> list:
    """The cases step 1 may offer, as ``{"case_id", "doc_no", "label_fa"}`` rows.

    ``_visible_case_rows`` — the SAME helper the company page's Cases tab uses,
    not a second query with a second idea of what this viewer may see. Never
    offer a case they cannot open: a picker that lists a case is telling the
    reader that case exists, and the whole point of ``case_access_for`` is that
    a document number is case identity. Reusing the helper also means the two
    lists on the same company can never disagree — the writer attaches a report
    to a case they can see listed one tab away.

    ``report_create`` re-derives this same list and checks the submitted id
    against it, so this function decides what is OFFERED, never what is
    ACCEPTED.
    """
    return _visible_case_rows(client, request, case_access)


@login_required
def report_add(request, pk):
    """Step 1: which case is this report about — or none.

    A FULL PAGE, like ``contact_add``, and for its reasons. GET renders the
    picker; the only POST it accepts is the one its own two buttons make
    ("Continue" with a case, or "Skip"), which carries the choice forward and
    renders step 2. It never writes, so it is deliberately NOT ``require_POST``
    — a reader must be able to open, bookmark and reload step 1.

    ATTACHING A CASE IS OPTIONAL, by the owner's own wording, and the Skip
    button is that option made explicit rather than left implied by an empty
    dropdown. Both buttons land on the same step 2; the only difference is
    whether a case id travels with it.

    A case id that is not in this viewer's own visible list is dropped here and
    again in ``report_create`` — see ``_report_case_choices``.

    STEP 2 IS BUILT WITH ``can_manage_options``, the same value the template is
    told, so the inline "add a new option" field EXISTS on the form only for a
    viewer who may create one — the gate is the field's absence, not the
    template's ``{% if %}``. See ``_can_manage_options`` and
    ``marketing/forms.py::ReportForm``.
    """
    access = access_for(request)
    if not access.can_view or not access.can_edit:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    case_access = case_access_for(request, access)
    case_choices = _report_case_choices(client, request, case_access)
    can_manage_options = _can_manage_options(access)

    if request.method == "POST":
        # Whatever arrived, kept only if it is one of the rows this viewer was
        # actually offered. ``skip`` needs no special handling: it simply sends
        # no case id, which is the same state as "picked nothing".
        chosen_id = _int_or_none(request.POST.get("case_id"))
        allowed = {row["case_id"] for row in case_choices}
        case_id = chosen_id if chosen_id in allowed else None
        return render(request, "marketing/report_form.html", {
            "client": client,
            "form": ReportForm(initial={"case_id": case_id},
                               can_manage_options=can_manage_options),
            "case_row": next(
                (r for r in case_choices if r["case_id"] == case_id), None),
            "can_manage_options": can_manage_options,
        })

    return render(request, "marketing/report_case.html", {
        "client": client,
        "case_choices": case_choices,
    })


@login_required
@require_POST
def report_create(request, pk):
    """Step 2's confirm: validate and record the report.

    POST-ONLY, for ``contact_remove``'s reason turned the other way round: this
    is the one request in the flow that writes a row, and a write behind a GET
    is one prefetching browser or one crawled URL away from doing itself.

    THE "AT LEAST ONE OF OPTIONS / TEXT" RULE IS THE FORM'S
    (``forms.ReportForm.clean``), enforced server-side; an invalid submit
    re-renders step 2 with the errors and everything the writer already typed,
    including the case they picked in step 1 (carried in a hidden field), so
    nobody has to walk the wizard again to fix a typo.

    THE CASE IS RE-VALIDATED HERE, against this viewer's own visible rows, and
    that is not belt-and-braces — it is the actual check. Step 1's list decides
    what is offered; a hidden field is a value the browser sends, so an id that
    is not in the list is simply dropped and the report is filed with no case,
    exactly as a Skip would have filed it.

    ``services.add_report`` does the writing (and the timeline row), so there is
    exactly one place in the app that creates a ``CompanyReport``.

    THE INLINE "ADD A NEW OPTION" FIELD IS GATED A SECOND TIME, on
    ``_can_manage_options``, and gated by ABSENCE — word for word the shape
    ``contact_add`` uses for ``new_role``: the form is constructed WITHOUT that
    field for anyone who may not create options, so a hand-built POST carrying
    ``new_option=...`` from an ordinary Marketing Expert never reaches the
    ``get_or_create`` below, because the value never reaches ``cleaned_data``
    at all. The ``if can_manage_options else ""`` on the read is the same
    belt-and-braces line ``contact_add`` carries, and for the same reason: it
    makes the gate legible at the write, not because the form could leak.
    """
    access = access_for(request)
    if not access.can_view or not access.can_edit:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    case_access = case_access_for(request, access)
    case_choices = _report_case_choices(client, request, case_access)
    can_manage_options = _can_manage_options(access)

    form = ReportForm(request.POST, can_manage_options=can_manage_options)
    if form.is_valid():
        data = form.cleaned_data
        case_id = data.get("case_id")
        allowed = {row["case_id"] for row in case_choices}
        case = (Case.objects.filter(pk=case_id).first()
                if case_id in allowed else None)
        options = list(data["options"])
        new_option = (data.get("new_option") or "").strip() if can_manage_options else ""
        if new_option:
            # get_or_create, not create, and stamping ``created_by`` on first
            # creation only: verbatim what ``contact_add`` does with a typed
            # ``ContactRole``, for verbatim its reasons — ``ReportOption.name``
            # is globally unique, so re-typing an option that already exists
            # means the same thing as having ticked it and must not be an
            # IntegrityError.
            option, _created = ReportOption.objects.get_or_create(
                name=new_option,
                defaults={"created_by": request.user},
            )
            # Attached to THIS report as well as added to the list, because
            # that is what the writer asked for — ``ReportForm.clean`` counts a
            # typed option as the report having said something precisely on the
            # strength of this line. Appended only when it is not already among
            # the ticked boxes, so re-typing a preset the writer also ticked
            # cannot double it up.
            if option.pk not in {o.pk for o in options}:
                options.append(option)
        services.add_report(
            client, request.user,
            text=data["text"],
            options=options,
            case=case,
        )
        return redirect("marketing:company_detail", pk=client.pk)

    # Invalid: back to step 2 with the errors, and with whichever case the
    # writer had already attached still attached (read from the bound form's own
    # cleaned value, so a rejected id is not re-offered as if it had been kept).
    kept_id = form.cleaned_data.get("case_id") if hasattr(form, "cleaned_data") else None
    return render(request, "marketing/report_form.html", {
        "client": client,
        "form": form,
        "case_row": next(
            (r for r in case_choices if r["case_id"] == kept_id), None),
        "can_manage_options": can_manage_options,
    })


# --------------------------------------------------------------------------- #
# Reminders — set one on a company, and the person's own list of them
# --------------------------------------------------------------------------- #
# THREE SCREENS' WORTH OF ROUTES, AND ONE RULE THAT IS NOT LIKE THE REST OF THIS
# FILE: a reminder belongs to exactly one person and nobody else ever reads it —
# not a Marketing Supervisor, not the General Manager, not the platform admin.
# See ``marketing/models.py::Reminder`` for the owner's own wording and for why
# the Supervisor/GM/admin precedent that CONTACTS and REPORTS follow is
# deliberately not followed here. Nothing below takes an ``access.scope``,
# because there is no wider list for a scope to widen to; ownership is enforced
# inside ``marketing/reminders.py``, in one place, for both mutations.
#
# WHO MAY OPEN THEM is still an ordinary question, and it is answered by the two
# gates this file already uses:
#
#   * SETTING one (``reminder_add``) is a write about a COMPANY, so it is gated
#     exactly like ``contact_add`` and ``report_add`` — ``can_view and
#     can_edit`` — and the view-only tier (the GM and the platform admin) is
#     refused, on the URL and not merely in the template.
#   * READING and RE-TIMING your own list is gated on
#     ``access.can_reach_marketing`` instead, which is the existing union "may
#     open the section now, OR holds a Marketing seat they are not sitting in".
#     That is deliberately the WIDER of the two tests and it grants nothing
#     extra: the page shows the signed-in person their own rows and no company
#     data at all, and the notification that links here is rendered for exactly
#     that same population (see ``marketing/context_processors.py``). Gating it
#     on ``access_for`` instead would answer 403 to a dual-seat person who
#     clicked their own notification from a case screen — a permission wall in
#     front of their own note to self.


def _reminder_case_choices(client, request, case_access) -> list:
    """The cases the reminder screen may offer, as ``_visible_case_rows`` rows.

    THE SAME HELPER, FOR THE SAME REASON ``_report_case_choices`` GIVES: never
    offer a case this viewer cannot open. A picker that lists a case is telling
    the reader that case exists, and a document number is case identity. Reusing
    the Cases tab's own rows also means the two lists on one company can never
    disagree.

    ``ReminderForm`` turns these into its ``case_id`` choices, so an id outside
    them is refused by form validation as well; ``reminder_add`` still re-derives
    the list and checks the cleaned value against it before writing, because a
    ``<select>`` is a value the browser sends and not a fact.
    """
    return _visible_case_rows(client, request, case_access)


@login_required
def reminder_add(request, pk):
    """Set a reminder for yourself on this company — one page, one POST.

    A FULL PAGE, like ``contact_add``, and for its reasons. One screen rather
    than the report flow's two: see ``marketing/forms.py::ReminderForm``.

    GATED ON ``can_view and can_edit``, checked here and not merely hidden on
    the company page — the same gate ``contact_add`` and ``report_add`` carry,
    because this is a write reached from the company's own screen and the
    view-only tier does not write in this section. That the ROW is private to
    its owner is a separate question, answered by the model and by
    ``marketing/reminders.py``; it does not make the section's own gate
    irrelevant.

    ``marketing/reminders.py::create`` does the writing (and clears this
    person's cached notification), so there is exactly one place in the app that
    creates a ``Reminder``.
    """
    access = access_for(request)
    if not access.can_view or not access.can_edit:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    case_access = case_access_for(request, access)
    case_choices = _reminder_case_choices(client, request, case_access)

    if request.method == "POST":
        form = ReminderForm(request.POST, case_choices=case_choices)
        if form.is_valid():
            data = form.cleaned_data
            # Re-resolved against this viewer's own visible rows, exactly as
            # ``report_create`` re-resolves its own hidden field. The form's
            # ChoiceField has already refused an id outside the offered list;
            # this is what turns the surviving id into a real ``Case``, and it
            # falls back to None rather than 404ing on a case that vanished
            # between the render and the submit — a reminder on the company
            # itself is a supported answer.
            allowed = {row["case_id"] for row in case_choices}
            case_id = data.get("case_id")
            case = (Case.objects.filter(pk=case_id).first()
                    if case_id in allowed else None)
            reminders.create(
                request.user, client,
                note=data["note"], due_at=data["due_at"], case=case,
            )
            return redirect("marketing:company_detail", pk=client.pk)
    else:
        form = ReminderForm(case_choices=case_choices)

    return render(request, "marketing/reminder_form.html", {
        "client": client,
        "form": form,
    })


def _reminder_rows(request) -> list:
    """This person's own reminders, each carrying whether it is DUE right now
    and whether its attached case may still be NAMED to them.

    ``due`` is derived, never stored — ``marketing/models.py::ReminderState``
    explains why at length, and the short version is that storing it would need
    something to run at the moment it became true. One ``timezone.now()`` for
    the whole page rather than one per row, so two rows a millisecond apart
    cannot be judged against two different "now"s.

    ONE QUERY for the whole list (``reminders.list_for_user`` select_relates the
    company and the case), plus ONE for the visibility check below.

    THE DOCUMENT NUMBER GOES THROUGH ``case_access_for`` LIKE EVERY OTHER ONE.
    This function used to render it straight off the row, on the argument that a
    reminder has a single reader who was shown that number by the scoped picker
    when they chose it. That argument missed a real case: a seat can LOSE case
    access after the reminder is set (its Commercial PersonRole is removed), and
    the number then went on being printed here and in the top-of-page banner
    while the company page's own Cases tab showed that same person nothing — two
    screens contradicting each other about one viewer, which is the exact thing
    ``access.case_access_for`` exists to prevent. ``show_case_no`` is what the
    template reads; the row itself is always kept, because it is the person's own
    note and only the case identity on it is in question.
    """
    from django.utils import timezone

    now = timezone.now()
    rows = reminders.list_for_user(request.user)
    visible = services._visible_case_ids(
        [r.case_id for r in rows], case_access_for(request))
    for row in rows:
        row.is_due = (row.state == ReminderState.OPEN and row.due_at <= now)
        row.show_case_no = (row.case_id is not None and row.case_id in visible)
    return rows


def _render_reminder_list(request, *, time_form=None, retime_id=None):
    """The reminders list page, rendered from one place.

    Two routes end here — the list itself and both mutations' re-render or
    redirect paths — and factoring it out is what keeps a failed re-time from
    having to rebuild the page a second, slightly different way.

    ``time_form`` is a BOUND ``ReminderTimeForm`` carrying an error, and
    ``retime_id`` is the row it belongs to, so the template can print the
    message under that row's own box instead of at the top of a list where it
    would not say which reminder it was about.
    """
    return render(request, "marketing/reminder_list.html", {
        "rows": _reminder_rows(request),
        "retime_error": (time_form.errors.get("due_at")
                         if time_form is not None else None),
        "retime_id": retime_id,
    })


@login_required
def reminder_list(request):
    """Your own reminders — every one you set, soonest first.

    THE DESTINATION THE NOTIFICATION LINKS TO. The owner's flow is: the banner
    appears, you click it, you land here, you set a new time (or mark the thing
    dealt with) and the banner goes. Both controls are on this page and nowhere
    else.

    SHOWS YOUR OWN ROWS AND ONLY YOUR OWN — ``reminders.list_for_user`` filters
    on ``owner`` and takes no scope at all. There is no supervisor view of this
    page and no admin view; see the section header above.

    Gated on ``can_reach_marketing`` rather than ``access_for`` — see the
    section header for why the wider of the two tests is the right one for a
    page that shows the viewer nothing but their own notes.
    """
    if not can_reach_marketing(request):
        return render(request, "marketing/denied.html", status=403)
    return _render_reminder_list(request)


@login_required
@require_POST
def reminder_retime(request, reminder_id):
    """Set a new time on one of your own reminders.

    POST-ONLY, from a real form with a CSRF token, for ``contact_remove``'s
    reason: it changes a row, and a mutation behind a GET is one prefetching
    browser away from doing itself.

    THIS IS THE LOOP THE OWNER DESCRIBED — reminded, report written, next
    reminder set — so it also REOPENS a row that had been marked dealt with;
    see ``reminders.reschedule``, which is where that rule lives.

    A reminder id belonging to someone else is indistinguishable from one that
    does not exist: ``reminders.reschedule`` answers False to both and this
    redirects exactly as it would on success, so the response cannot be used to
    discover that another person's reminder exists.
    """
    if not can_reach_marketing(request):
        return render(request, "marketing/denied.html", status=403)
    form = ReminderTimeForm(request.POST)
    if not form.is_valid():
        # Re-rendered rather than redirected, because a redirect would drop the
        # message telling the person what was wrong with what they typed.
        return _render_reminder_list(
            request, time_form=form, retime_id=_int_or_none(reminder_id))
    reminders.reschedule(request.user, reminder_id, form.cleaned_data["due_at"])
    return redirect("marketing:reminder_list")


@login_required
@require_POST
def reminder_done(request, reminder_id):
    """Mark one of your own reminders dealt with.

    The other way to act on the notification, and the one that ends the loop
    rather than continuing it. POST-only and ownership-checked exactly as
    ``reminder_retime`` above; the row is kept rather than deleted, because this
    person's own list is where they see what they have already handled.
    """
    if not can_reach_marketing(request):
        return render(request, "marketing/denied.html", status=403)
    reminders.mark_done(request.user, reminder_id)
    return redirect("marketing:reminder_list")
