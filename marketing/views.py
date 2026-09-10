"""The Marketing unit's own screens.

THREE screens now, and they are deliberately separate pages rather than tabs
of one — the third, "My Tasks", is also the one screen in this file that is
NOT gated on Marketing access at all; see its own section below for why:

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
* REMINDERS (``reminder_add``) — a person's own notes-to-self about a
  company, set from the company detail page and listed there (a Reminders
  tab). ``reminder_list`` — the URL that used to be this section's OWN
  standalone list — is now a plain redirect to "My Tasks" below, which
  replaced it; see that view's own docstring. SEEING a company's Reminders
  tab now follows the SAME "own"/"all" split as Reports — an ordinary
  Marketing Expert sees only their own, a Supervisor sees the union of
  everyone's, and (for consistency with every other elevated-access surface
  in this app) so do the General Manager and the platform admin — a
  DELIBERATE REVERSAL of an earlier round's decision that this app's own
  docstrings used to describe as permanent; see
  ``marketing/models.py::Reminder`` for that history stated plainly rather
  than quietly rewritten. What did NOT widen: CREATING one, and CLOSING one
  out, both stay strictly owner-only (see ``marketing/reminders.py::_own``),
  and the top-of-page due notification stays scoped to the viewer alone (see
  ``marketing/context_processors.py::reminder_notice``) — see the section
  above those views for the full split, and ``marketing/reminders.py``, which
  owns the due check and its cache. CLOSING is now the ONLY way to act on a
  reminder, and it is done from ``my_tasks_report`` below, never from this
  section: ``reminder_retime``/``reminder_done`` — a plain reschedule or a
  mark-done with no report ever required — used to live here too, and have
  been removed as a live bypass of the mandatory close-only-via-a-report
  cycle "My Tasks" was built around (see the comment left in this file where
  they used to be defined). The company page's own Reminders tab now carries
  the same "Submit report" control My Tasks does, linking to the same view.
* MY TASKS (``my_tasks``, ``my_tasks_add``, ``my_tasks_report``) — the
  platform-wide personal hub, reached from the sidebar's own "Personal" nav
  group rather than from anywhere under this section's Marketing-gated
  navigation, and open to every login with a linked ``people.Person`` record
  — Marketing seat or none at all. It REPLACED the standalone "My reminders"
  page (``reminder_list``, now a redirect into it). See the dedicated "My
  Tasks" section further down this file for the full story: why it is gated
  differently from every other view above, why its own listing stays
  strictly owner-scoped even though Reminders elsewhere in this app can now
  answer "all", and how its case picker answers the one genuinely new
  permission question this section asks.

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

import dataclasses

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from cases.constants import CaseStatus
from cases.models import Case, Client

from . import reminders, rolechart, services
from .access import (
    access_for, case_access_for, case_open_url,
    marketing_seat_role, scope_case_rows,
)
from .forms import ContactForm, ReminderForm, ReportForm, TaskForm
from .models import (
    ClientEventAction, CompanyReport, ContactRole, ReminderState, ReportOption,
)


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
        # Every string chart_interact.js used to set literally (a mode-banner
        # label, an empty-list placeholder, a tooltip) — see that file's own
        # top-of-file "---- i18n ----" comment for how it reads this, and
        # home.html's own {% block scripts %} for where it is handed over via
        # json_script.
        #
        # BUILT HERE, IN PYTHON, RATHER THAN AS A RUN OF {% trans %} TAGS IN
        # THE TEMPLATE — deliberately, not a style preference: Django's own
        # {% trans %} tag, given a literal string argument, doubles every '%'
        # in it (Variable.resolve()'s own defensive escaping, so a translated
        # literal can later survive an old-style %-format call unchanged)
        # BEFORE the doubled string ever reaches gettext() — so a template tag
        # written as {% trans "...%(name)s..." %} does not look up the catalog
        # entry keyed on "...%(name)s..." at all, it looks up one keyed on
        # "...%%(name)%%s...", which does not exist, and silently renders the
        # original English back. Caught live on this exact page: the plain-
        # word keys (no '%' in them) translated correctly through a {% trans
        # %} tag, but every %(name)s-style composite here quietly stayed
        # English. gettext() called directly, from Python, has no such
        # escaping step, so building the whole dict here sidesteps the bug
        # rather than working around it string-by-string in the template.
        "chart_i18n": {
            "caseMode": _("Case mode"),
            "editingCaseFor": _("Editing chart for case %(doc)s — %(client)s"),
            "leaveCaseMode": _("Leave case mode"),
            "active": _("Active"),
            "deactivate": _("Deactivate"),
            "showing": _("Showing"),
            "query": _("Query"),
            "clear": _("Clear"),
            "clearAll": _("Clear all"),
            "editsAttachTo": _("edits attach to %(name)s"),
            "editsAttachToRole": _("edits attach to %(name)s — %(role)s"),
            "chartShowing": _("chart is showing %(name)s"),
            "chartShowingRole": _("chart is showing %(name)s — %(role)s"),
            "noCompaniesMatch": _('No companies match "%(q)s".'),
            "noCompaniesYet": _("No companies yet."),
            # The "+ Add company" panel and the Quick Inquiry company field
            # both create a brand-new client through the SAME shared
            # createCompanyPicker() helper (chart_interact.js), so this one
            # entry covers the create-row text in both places.
            "addQuoted": _('+ Add "%(name)s"'),
            "noRolesYetPeriod": _("No roles yet."),
            "noRolesMatch": _('No roles match "%(q)s".'),
            "casesConnectedToUs": _("Cases connected to Us"),
            "showingOfCases": _("Showing %(shown)s of %(total)s cases"),
            "openInArchive": _("Open %(name)s in the case archive →"),
            "addCompanyBtn": _("+ Add company"),
            "addCompanyTitle": _("Add a company"),
            "closeAddCompanyPanel": _("Close add-company panel"),
            "searchWholeDirectory": _("Search the whole directory…"),
            "add": _("Add"),
            "nothingConnectedYet": _("Nothing connected yet."),
            "notConnectedToAnythingYet": _("Not connected to anything yet."),
            "noCompaniesTaggedYet": _("No companies tagged yet."),
            "noCasesFound": _("No cases found."),
            "removeCompany": _("Remove %(name)s"),
            "activateAsThis": _("Activate as this"),
            "activateAsAnchor": _("Activate %(name)s as this card's anchor"),
            "connectionOnly": _("connection only"),
            "chooseARole": _("Choose a role…"),
            "noRolesYet": _("No roles yet"),
            "loadingRoles": _("Loading roles…"),
            "pickCompanyFirst": _("Pick a company first…"),
            "chooseRoleToAdd": _("Choose a role to add…"),
            "alreadyHasEveryRole": _("Already has every role"),
            "noCasesMatch": _('No cases match "%(q)s".'),
            "noCasesYet": _("No cases yet."),
        },
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
    labels_map = services.labels_for_clients(
        [c.pk for c in clients], request.user, access.scope,
        elevated=access.can_manage_config)
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
        case_access=case_access_for(request, access),
        elevated=access.can_manage_config)
    return JsonResponse({"ok": True, "label": label, "companies": companies})


@login_required
@require_POST
def label_toggle(request):
    """POST client_id=, label=, add=1|0 -> add/remove a manual tag — THIS
    USER's own, unless this viewer is elevated (see below).

    ``elevated`` is passed through as ``access.can_manage_config`` — the
    Marketing Supervisor or the platform admin (see
    ``marketing/access.py::Access.can_manage_config`` for why that flag,
    already established for exactly this "Supervisor and admin, not an
    ordinary editor" distinction, is reused here rather than a second check
    being invented). It only ever WIDENS the ``add=0`` (remove) branch — see
    ``services.toggle_manual_label``'s own docstring — per the owner's
    explicit instruction that a Marketing Supervisor's access to remove or
    fix another user's work is unrestricted. The frontend already only draws
    the removal control when ``removable`` says so (see
    ``services.companies_for_label``/``connections_of_client``, which this
    view's siblings compute with the same ``elevated`` flag), so a genuine
    Expert never even sees a control that would reach this branch for a row
    they do not own; this is the server-side half of that same grant, and the
    one a forged request cannot get around.

    Returns ``count``, the field's fresh viewer-scoped total, alongside
    ``ok`` — added so the chart's own JS can update that card's badge/fill
    THE INSTANT this write lands, instead of only after a full page reload.
    Before this, the client had no way to know the field's new count short of
    re-fetching the whole chart: the badge/class pair is baked into the SVG
    server-side at page load (see ``marketing/rolechart.py::_node``) and
    nothing after that ever refreshed it — see chart_interact.js's
    ``applyLiveCount`` for the write side of this fix.

    ``services.label_counts`` is called again here, once, for exactly this
    reason: its own docstring says it is two queries total REGARDLESS of
    client count, so paying for it a second time after a single write is
    cheap — nowhere near the cost of a second per-key query, which is the
    N+1 shape that function was written to replace in the first place. Only
    the one field that could have changed (``label`` — the POST field this
    view already validated above) is read back out of it; toggling a manual
    label can never move any OTHER field's count.
    """
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
    services.toggle_manual_label(client, label, request.user, add,
                                 elevated=access.can_manage_config)
    count = services.label_counts(request.user, access.scope).get(label, 0)
    return JsonResponse({"ok": True, "count": count})


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

    Returns ``count``, the TARGET field's fresh viewer-scoped total —
    the same live-update need ``label_toggle`` above now serves, and for the
    same reason: the target card's badge/fill on the main chart has to move
    the instant a connection lands, not only after a reload (see
    chart_interact.js's ``applyLiveCount``). The ANCHOR's own field never
    needs this: creating or removing a connection never adds or removes a
    ``ClientLabel`` on the anchor (it already holds its own role — that is
    what makes it eligible to be an anchor at all), so the anchor's card
    never has anything to refresh here. The TARGET's field genuinely CAN
    move on ``add`` — ``services.create_connection`` may register a fresh
    ``ClientLabel`` for the target if it did not already hold ``target_role``
    (see that function's own docstring) — and by the same rule the target's
    count is UNCHANGED on ``remove``: ``services.remove_connection``
    deliberately never undoes that grant (a role, once given, stays — see
    its own docstring). ``count`` is still computed and returned on the
    ``remove`` branch too, rather than only on ``add``: the alternative is an
    ``if add`` in the JSON body, which would just push that same branch onto
    every caller in chart_interact.js instead of removing it — one extra,
    already-cheap ``label_counts`` call here buys a single shared
    ``applyLiveCount(field, count)`` call on the JS side for both directions.
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
        # ``elevated`` — see ``label_toggle``'s identical comment just above in
        # this file, and ``services.remove_connection``'s own docstring: the
        # Marketing Supervisor/platform admin bypass applies to REMOVAL only,
        # so it has no counterpart on the ``create_connection`` branch above.
        services.remove_connection(anchor_client, anchor_role, target_client, target_role, case, request.user,
                                   elevated=access.can_manage_config)
    count = services.label_counts(request.user, access.scope).get(target_role, 0)
    return JsonResponse({"ok": True, "count": count})


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
        case_access=case_access_for(request, access),
        elevated=access.can_manage_config)
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


def _visible_case_rows_all(request, case_access) -> list:
    """``services.all_cases()`` narrowed to what this viewer may see — the
    SAME shape :func:`_visible_case_rows` returns (plus ``client_id``/
    ``client_name``, since a row with no single company in scope has to say
    which one it belongs to), but over EVERY case platform-wide instead of
    one client's.

    FOR A PAGE THAT HAS NO CLIENT IN ITS OWN URL AT ALL — the later "My
    Tasks" stage's reminder/report picker, which is reached from nowhere that
    already names a company the way ``company_detail`` does. That picker
    still has to obey the exact same "never offer a case this viewer cannot
    open" rule ``_visible_case_rows`` already enforces (see that function's
    own comment above it), it just cannot narrow the CANDIDATE set to one
    client first, because there is no client to narrow it to.

    THIS IS NOT A NEW AUTHORIZATION RULE — IT IS THE SAME ONE, GENUINELY
    UN-NARROWED, AND THAT IS A DELIBERATE, VERIFIED CLAIM, NOT AN ASSUMPTION.
    ``marketing/access.py::case_access_for`` and ``scope_case_rows`` both take
    NO ``client`` argument at all — read their own docstrings: the four-way
    table (admin/GM see everything; a Commercial Manager seat sees the
    entire archive; an ordinary Commercial Expert seat sees only cases
    ``created_by`` their own seat; a Marketing-only seat, holding no
    Commercial seat at all, sees nothing) is already resolved platform-wide,
    with no per-client component anywhere in it. ``_visible_case_rows``
    reaches that same platform-wide answer and then narrows the CANDIDATE
    ROWS to one client before scoping; this function skips only that
    narrowing step and scopes the full candidate set with the identical
    ``case_access``/``scope_case_rows`` call — so a viewer sees EXACTLY the
    union, across every company, of what ``_visible_case_rows`` would have
    shown them company by company, never more.
    """
    rows = scope_case_rows(services.all_cases(), case_access)
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


# The Cases CARD's own raw-status bucket, folded in with BURNED — the owner's
# own words, verbatim: "کنسل شده ها که شامل پرونده های سوخته شده و کنسل شده
# میشه جمع انها" (the cancelled bucket sums the burned and the cancelled
# cases together). Named separately from ``services._STATUS_CANCELLED``
# rather than imported from it: that constant happens to hold the identical
# two members today, but the two are read for two different reasons by two
# different callers (see ``_case_card_counts``'s own docstring for why they
# must not become one shared name), and a future change to either rule must
# not silently retarget the other just because they once agreed.
_CASE_CARD_CANCELLED = frozenset({CaseStatus.BURNED, CaseStatus.CANCELLED})


def _case_card_bucket(status) -> str:
    """Which of the Cases CARD's four buckets a raw ``status`` falls into —
    ``"closed"``, ``"approved"``, ``"cancelled"`` or ``"pending"``.

    Pulled out of ``_case_card_counts`` below so that ``_pi_money_by_bucket``
    (added this round, for the card's per-bucket PI totals) classifies every
    case EXACTLY the same way the counts do, from one place, rather than
    carrying its own copy of this if/elif chain that could quietly drift from
    the counts' version over some future edit. Two functions computing "which
    bucket" by two separate rules is exactly the trap ``_case_card_counts``'s
    own docstring warns about for ``_counts_over`` vs. this card — the fix
    here is the same one: one rule, read by both callers.
    """
    if status == CaseStatus.FINAL_CLOSED:
        return "closed"
    if status == CaseStatus.FINAL_APPROVED:
        return "approved"
    if status in _CASE_CARD_CANCELLED:
        return "cancelled"
    return "pending"


def _case_card_counts(case_rows) -> dict:
    """``{"total","approved","closed","cancelled","pending"}`` over exactly
    ``case_rows`` — the Cases CARD's own four-way split, for the company
    detail page's redesigned summary row.

    A DELIBERATELY SEPARATE BUCKETING FROM ``_counts_over`` RIGHT ABOVE IT,
    NOT A WIDENING OF IT, even though the two look almost alike and share a
    caller's worth of case rows. ``_counts_over`` backs TWO callers:
    ``company_detail`` (until this function replaced it here) and
    ``client_case_counts`` — the JSON endpoint behind the relationship
    chart's "cases connected to Us" panel, which ``services.py``'s own module
    docstring documents as wanting "exactly three buckets, named exactly this
    way, and nothing finer". Widening ``_counts_over`` itself to carve a
    fourth "closed" bucket out of "approved" would silently re-carve that
    chart panel's own Approved number too, which nobody asked for and which
    would contradict that panel's own documented three-bucket rule. So this
    is a new, narrow function used ONLY by ``company_detail``'s Cases card
    (and the matching header chips on its Cases TAB, for one page that agrees
    with itself), reading the RAW ``status`` on each row rather than the
    already-three-way-bucketed ``status_fa`` ``_counts_over`` reads, and
    ``_counts_over``/``client_case_counts``/the chart panel are completely
    untouched by it.

    THE FOUR BUCKETS, PER THE OWNER'S OWN WORDING THIS ROUND:

    * ``closed`` — ``CaseStatus.FINAL_CLOSED`` alone: a case Commercial has
      fully shut. THIS BUCKET DID NOT EXIST ON THIS PAGE BEFORE; the owner
      asked for it by name ("Closed").
    * ``approved`` — ``CaseStatus.FINAL_APPROVED`` alone, now EXCLUDING
      ``FINAL_CLOSED`` (which the shared, chart-facing bucketing above still
      folds into its own "approved" — see the note above on why that stays
      untouched). Splitting "closed" out of "approved" for exactly this card
      is the whole point of a fourth bucket: without the split, a
      final-closed case would count under two headings on the same card and
      the four numbers would no longer sum to the total.
    * ``cancelled`` — BURNED and CANCELLED SUMMED INTO ONE LINE, per the
      owner's own words quoted on ``_CASE_CARD_CANCELLED`` above.
    * ``pending`` — everything else (still-moving statuses, and the various
      "cannot supply" states) — the existing "no result" bucket, reused as-is
      because its existing meaning already matches what "no result yet" means
      here; nothing about it changes.

    ``approved + cancelled + closed + pending == total`` always, the same
    invariant the three-bucket version keeps, just over one more heading.
    """
    counts = {"total": 0, "approved": 0, "closed": 0, "cancelled": 0, "pending": 0}
    for row in case_rows:
        counts["total"] += 1
        counts[_case_card_bucket(row["status"])] += 1
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


def _pi_money_by_bucket(case_rows, case_access) -> dict:
    """The Cases CARD's four PI totals — one per ``_case_card_counts`` bucket
    (``"approved"``, ``"cancelled"``, ``"closed"``, ``"pending"``) — each
    already formatted, or ``"—"`` for a bucket whose total is genuinely zero.

    THE OWNER ASKED THE ONE GRAND TOTAL SPLIT INTO FOUR, one beside each
    count line on the Cases card, so that a reader does not have to guess how
    much of the single figure belonged to, say, the closed cases versus the
    ones still pending. This is a SIBLING of ``_pi_money_total`` above, not a
    replacement of it: that function still backs the Cases TAB's own "Grand
    total (all PI)" banner (``pi_total_display`` in this view's context),
    which the owner did not ask this round to touch, so it is left computing
    the one company-wide figure exactly as it always has. Both functions call
    the SAME ``archive_attach_money`` for the SAME visible ``case_rows`` —
    there is no second, independent read of the money here, only a second way
    of adding up the one map ``archive_attach_money`` already returned.

    EVERY CASE IS PUT IN A BUCKET WITH ``_case_card_bucket``, the identical
    per-row rule ``_case_card_counts`` uses for the count lines this dict sits
    beside — so the count and the money total on any one line always describe
    the same set of cases. Computing the bucket here by some second rule of
    this function's own would risk a line whose count and total quietly
    describe two different groups of cases, which is exactly the failure mode
    the owner's "reuse the exact same bucket assignment" instruction rules
    out.

    GATED IDENTICALLY TO ``_pi_money_total``: an empty dict when
    ``case_access.show_money`` is False, so the template's own
    ``{% if show_money %}`` continues to be the one gate that decides whether
    any money renders on this card at all — this function does not add a
    second, looser way to see a figure the grand total already hides.
    """
    if not case_access.show_money:
        return {}
    # Every case_id sorted into its bucket up front — mirrors the counts'
    # own single pass over case_rows, just keeping the ids instead of a tally.
    bucket_ids = {"approved": [], "cancelled": [], "closed": [], "pending": []}
    for row in case_rows:
        case_id = row.get("case_id")
        if case_id is None:
            continue
        bucket_ids[_case_card_bucket(row["status"])].append(case_id)
    all_ids = [cid for ids in bucket_ids.values() for cid in ids]
    if not all_ids:
        return {bucket: "—" for bucket in bucket_ids}

    from cases import services as case_services
    from cases.export_data import format_money_amount

    # One call over every visible case, exactly as ``_pi_money_total`` makes
    # — the per-bucket split below is pure arithmetic over the map it hands
    # back, not a second query.
    gt_map = case_services.archive_attach_money(
        list(Case.objects.filter(pk__in=all_ids).only("id")))
    result = {}
    for bucket, ids in bucket_ids.items():
        bucket_total = sum(gt_map.get(cid, 0.0) for cid in ids) if gt_map else 0.0
        result[bucket] = format_money_amount(bucket_total) if bucket_total else "—"
    return result


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
    """One company: its roles, its contacts, its cases, its reports, its
    reminders and its timeline.

    A FULL REDESIGN, THIS ROUND, OF THE TOP OF THIS PAGE — the owner's own
    spec, followed point by point rather than "the spirit of it": three cards
    side by side (Company / Roles / Cases) in the SAME card-grid language
    ``cases/templates/cases/case_detail.html`` already uses — ``.grid.grid-3``
    of ``.card``/``.card-head`` panels, ``dl.kv-ic.compact`` for an icon+
    label+value list — replacing the old horizontal ``.infobox`` row of stat
    tiles and the lopsided 2-column-plus-1 card row. The TABS below them keep
    their own established shape (``.tabs``/``.tab`` over ``.tab-panel``, the
    same ``<ul class="timeline">`` markup for the Timeline tab) and gain a
    fifth one, Reminders, immediately before Timeline — see
    ``marketing/templates/marketing/company_detail.html`` for the markup
    itself and this docstring for what feeds each panel.

    THE THREE CARDS, TOP OF PAGE:

    * COMPANY — name, code, the (already viewer-scoped) contact and report
      counts, PLUS TWO NEW NUMBERS THIS ROUND: ``contributor_count``, the
      count of DISTINCT PEOPLE who have ever added a role or a connection to
      this company (``services.contributor_count`` — unscoped, a company-wide
      fact, not a per-viewer visibility question; see that function's own
      docstring), and ``own_open_reminder_count``, THIS VIEWER'S OWN open
      reminders on this company (``reminders.count_open_for_client`` —
      deliberately owner-scoped ALWAYS, independent of whatever the Reminders
      tab below ends up showing a Supervisor; see that function's own
      docstring for why the two must not be confused).
    * ROLES — ``services.labels_for_clients`` for this one company, unchanged
      as a data source (the same manual + case-derived merge, scoped, that the
      chart and the directory list both show), restyled from a single row of
      chips into a two-column list — see the template.
    * CASES — ``_visible_case_rows`` for the rows, and ``_case_card_counts``
      for this card's OWN four-way split (total / approved / cancelled /
      closed / no-result), a NEW bucketing distinct from ``_counts_over`` —
      see that function's own docstring for why the two must stay separate
      functions rather than one widened in place. The PI money total
      (``_pi_money_total``, gated by ``show_money``) is unchanged from before
      this round.

    THE FIVE TABS BELOW THEM, in order (Contacts, Cases, Reports, Reminders,
    Timeline — Reminders sits immediately before Timeline, per the owner's own
    words, "قبل از تب تایم لاین"), and where each comes from:

    * CONTACTS — ``services.list_contacts``, which enforces the visibility rule
      itself: an ordinary Marketing user sees only the contacts they added, a
      Supervisor/GM/admin sees every one. This view passes the scope and does
      not filter anything a second time. Each row picks up one display flag,
      ``can_remove`` — see ``_contact_rows``. The tab also carries a search box
      this round, filtered client-side (``static/js/ui.js``'s existing
      ``data-filter-table`` pass — see the template) by name OR any of a
      contact's now-several :class:`~marketing.models.ContactPhone` numbers.
    * CASES — ``_visible_case_rows``, scoped by ``access.case_access_for``: the
      one decision the chart's own panel and the all-cases search also go
      through, so this page and the chart can never show the same viewer two
      different sets of cases. ``services.case_status_counts`` is ALSO read,
      unscoped, purely so the page can say honestly how many cases the company
      has in total when the viewer is only being shown some of them.
    * REPORTS — ``services.list_reports``, which enforces its visibility rule
      itself through the SAME ``_scoped`` helper contacts use: an ordinary
      Marketing user sees only the reports they wrote, a Supervisor/GM/admin
      sees every one. It takes ``case_access`` for the same reason the timeline
      does, and it is the same ONE decision again: a report's attached case is
      NAMED only to a viewer who may see that case, and is otherwise shown
      unnamed rather than removed — the report is Marketing's data, the case
      number is not. NOW A PROPER TABLE, with a filter card above it (case
      text + a Jalali date range on the Report's own date) mirroring the case
      archive's own filter card look, per the owner's ask.
    * REMINDERS — ``_reminder_rows(request, scope=access.scope, client=client)``,
      NEW THIS ROUND, and it is the same "own"/"all" widening
      ``marketing/reminders.py`` and ``marketing/models.py::Reminder`` document
      at length — an ordinary Marketing Expert sees only their own reminders on
      THIS company, a Supervisor/GM/admin see everyone's. Also a proper table
      with the SAME kind of filter card as Reports (case text + date range, on
      the reminder's own ``due_at``). "Set new reminder" reuses the existing
      ``reminder_add`` page (and ``reminders.create``) unchanged — this round
      gives it a prominent home inside this tab (the same
      ``btn btn-sm btn-primary`` treatment "Add contact"/"Add report" already
      get) rather than rebuilding creation from scratch.
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
      (``_visible_case_rows``) with its own scoping. Reminders are STILL NOT
      part of this timeline — see ``reminders.create``'s own docstring for why
      a private note staying private (no ``ClientEvent``) is unaffected by this
      round's change to who may LIST them.

    THE "SET REMINDER" BUTTON THAT USED TO SIT LOOSE IN THE PAGE HEAD IS GONE.
    It linked straight out to ``reminder_add`` with no in-page reminders list
    at all; the owner asked for reminders to move fully into their own tab, so
    the button moved with them (see the Reminders tab bullet above and the
    template's own comment on the removed markup).
    """
    access = access_for(request)
    if not access.can_view:
        return render(request, "marketing/denied.html", status=403)

    client = get_object_or_404(Client, pk=pk)
    # ONE decision, several tabs — see the TIMELINE bullet above.
    case_access = case_access_for(request, access)
    case_rows = _visible_case_rows(client, request, case_access)
    # The Cases CARD's own four-way split (see ``_case_card_counts``'s own
    # docstring for why this is a separate function from ``_counts_over``,
    # which the chart's own "cases connected to Us" panel still relies on,
    # untouched, through ``client_case_counts``). Kept in a context var named
    # ``counts`` — not ``case_counts`` — because the Cases TAB's own header
    # chips read the identical dict for the identical reason: one page should
    # not show two different "approved" numbers for the same rows.
    counts = _case_card_counts(case_rows)
    # Unscoped, company-wide — used ONLY to tell the reader that cases exist
    # which this page is not showing them. Never mixed into the numbers above,
    # which describe the visible rows.
    all_counts = services.case_status_counts(client, request.user)
    contacts = _contact_rows(client, request, access)
    reports = services.list_reports(client, request.user, access.scope,
                                    case_access=case_access)
    reminder_rows = _reminder_rows(request, scope=access.scope, client=client)

    return render(request, "marketing/company_detail.html", {
        "client": client,
        "labels": services.labels_for_clients(
            [client.pk], request.user, access.scope,
            elevated=access.can_manage_config).get(client.pk, []),
        "contacts": contacts,
        "case_rows": case_rows,
        "counts": counts,
        "hidden_case_count": max(all_counts["total"] - counts["total"], 0),
        # The archive's own "Grand total (all PI)" figure, over the visible rows
        # only — empty string when this viewer may not be shown money at all.
        # UNCHANGED by this round: the owner's spec keeps this gate exactly as
        # it was.
        "pi_total_display": _pi_money_total(case_rows, case_access),
        # The Cases CARD's own four per-bucket totals (approved/cancelled/
        # closed/pending), replacing that card's old single grand-total line
        # — see ``_pi_money_by_bucket`` for why this is a sibling of
        # ``_pi_money_total`` above rather than a replacement of it. Gated
        # identically: an empty dict, same as "" above, when show_money is
        # False.
        "pi_totals": _pi_money_by_bucket(case_rows, case_access),
        "show_money": case_access.show_money,
        "timeline": _timeline_with_icons(
            services.client_timeline(client, request.user, access.scope,
                                     case_access=case_access)),
        "reports": reports,
        # Whether the reports list this viewer is looking at is the WHOLE list
        # or only their own — the same sentence, for the same reason, that
        # ``sees_all_contacts`` below produces for contacts, and decided by the
        # same thing (the SCOPE, not ``can_edit``), because reports reuse
        # contacts' visibility rule exactly.
        "sees_all_reports": access.scope == "all",
        "reminders": reminder_rows,
        # Reminders' own version of ``sees_all_reports``/``sees_all_contacts``
        # — see this round's widening in ``marketing/reminders.py``/
        # ``marketing/models.py::Reminder``. Decided by ``access.scope`` for
        # the identical reason those two are: it is the same population
        # (Supervisor, GM, platform admin) seeing the same kind of "everyone's,
        # not just mine" union.
        "sees_all_reminders": access.scope == "all",
        # THIS VIEWER'S OWN Company-card number — see
        # ``reminders.count_open_for_client``'s own docstring for why this
        # stays owner-scoped unconditionally, independent of
        # ``sees_all_reminders`` above.
        "own_open_reminder_count": reminders.count_open_for_client(request.user, client),
        # The Company card's new distinct-contributor number — an unscoped,
        # company-wide fact; see ``services.contributor_count``.
        "contributor_count": services.contributor_count(client),
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
                phones=data["phones"], email=data["email"],
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
# SEEING is the same shape CONTACTS and REPORTS already use, and ACTING ON
# one is not. See ``marketing/models.py::Reminder`` for the full history of
# this: it used to say, at length, that a reminder belongs to exactly one
# person and NOBODY else ever reads it, and the owner has since explicitly
# reversed that half of it — Reminders now work like Reports: an ordinary
# Marketing Expert sees only their own, a Supervisor sees everyone's, and
# (for consistency with every other elevated-access surface in this app) so
# do the General Manager and the platform admin.
#
# WHAT STAYS OWNER-ONLY, WITH NO ``elevated`` ESCAPE HATCH AT ALL, UNLIKE
# ``toggle_manual_label``/``remove_connection``: CLOSING a reminder out
# (``marketing/views.py::my_tasks_report``, which goes through
# ``marketing/reminders.py::close_with_report`` — see that function's own
# docstring, called with no ``scope`` argument, which stays strictly
# ``owner=user``). The owner asked to let a Supervisor SEE the unit's
# reminders, not to let one close out somebody else's private note — those
# are a materially different, bigger grant that was never asked for. THIS IS
# THE ONLY WAY A REMINDER EVER CLOSES, DELIBERATELY, per the mandatory
# close-only-via-a-report cycle this whole round was built around — a
# reschedule or a mark-done with no report ever required USED to be possible
# here too, through ``reminder_retime``/``reminder_done``, which have since
# been REMOVED (routes and views both — see the comment where they used to
# sit, just below ``reminder_list``) precisely because they were a second,
# working path around this rule that nothing in this section's own history
# ever closed off.
#
# WHO MAY OPEN THESE SCREENS AT ALL is still answered by the two gates this
# file already uses, and neither of them is what decides "own" vs "all" —
# that is ``access.scope``, resolved separately once the gate has already let
# the request through:
#
#   * SETTING one (``reminder_add``) is a write about a COMPANY, so it is gated
#     exactly like ``contact_add`` and ``report_add`` — ``can_view and
#     can_edit`` — and the view-only tier (the GM and the platform admin) is
#     refused, on the URL and not merely in the template.
#   * READING one's OWN list is gated on having a linked ``people.Person``
#     record at all (``_my_tasks_person_or_redirect``, below) — the SAME,
#     wider population ``core/templates/base.html``'s own "Personal" nav
#     group and due-reminder banner are now offered to (see
#     ``marketing/context_processors.py::reminder_notice``), not
#     ``access.can_reach_marketing`` — a Marketing-seat-only gate would 403 a
#     Commercial or Technical seat with no Marketing seat at all who clicked
#     their own notification, which is exactly the population "My Tasks" was
#     built to include. ``reminder_list`` itself (immediately below) no
#     longer renders anything of its own; it is a plain, ungated redirect
#     onto ``my_tasks``, which runs this gate on arrival.


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
            # ``?tab=reminders`` so the reader lands back on the tab they just
            # acted from, rather than the company page's default Contacts tab
            # — see marketing/static/marketing/js/directory.js, which reads
            # this parameter once on load. A cosmetic touch only: the write
            # itself is already done by the time this redirect is built.
            return redirect(
                "%s?tab=reminders" % reverse("marketing:company_detail", args=[client.pk]))
    else:
        form = ReminderForm(case_choices=case_choices)

    return render(request, "marketing/reminder_form.html", {
        "client": client,
        "form": form,
    })


def _reminder_rows(request, *, scope: str = "own", client=None) -> list:
    """Reminders this viewer may see under ``scope`` (optionally narrowed to
    ``client``), each carrying whether it is DUE right now, whether ITS
    VIEWER may act on it, and whether its attached case may still be NAMED to
    them.

    ``due`` is derived, never stored — ``marketing/models.py::ReminderState``
    explains why at length, and the short version is that storing it would need
    something to run at the moment it became true. One ``timezone.now()`` for
    the whole page rather than one per row, so two rows a millisecond apart
    cannot be judged against two different "now"s.

    ``row.is_own`` IS NEW, AND IT IS WHAT LETS A TEMPLATE DRAW THIS SAFELY NOW
    THAT ``scope="all"`` CAN RETURN SOMEBODY ELSE'S ROW. Re-time and
    mark-dealt-with stay strictly owner-only at the service layer
    (``marketing/reminders.py::_own``, called with no ``scope`` argument by
    both mutations) — this flag is what tells the TEMPLATE not to draw those
    controls on a row where pressing them would silently do nothing, the same
    judgement ``_contact_rows``'s ``can_remove`` already makes for contacts.
    Drawing the control is still not the grant; the view re-checks regardless.

    ONE QUERY for the whole list (``reminders.list_for_user`` select_relates the
    company, the case and — now that a row can belong to somebody else — the
    owner too), plus ONE for the visibility check below.

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
    note and only the case identity on it is in question. This check is about
    THE CURRENT VIEWER'S OWN case access regardless of whose reminder the row
    is — a Supervisor reading a colleague's row is still subject to their own
    ``case_access_for``, not the row owner's.
    """
    from django.utils import timezone

    now = timezone.now()
    rows = reminders.list_for_user(request.user, scope, client=client)
    visible = services._visible_case_ids(
        [r.case_id for r in rows], case_access_for(request))
    for row in rows:
        row.is_due = (row.state == ReminderState.OPEN and row.due_at <= now)
        row.show_case_no = (row.case_id is not None and row.case_id in visible)
        row.is_own = (row.owner_id == getattr(request.user, "pk", None))
    return rows


@login_required
def reminder_list(request):
    """GONE AS A PAGE OF ITS OWN — "My Tasks" (``my_tasks`` below) REPLACES
    IT, and this view now exists only so the URL name (and every link that
    still points at it) keeps working. See ``my_tasks``'s own docstring for
    the full page this became part of.

    THE OWNER'S OWN WORDS ARE WHY: this page used to be reachable only by
    someone ``can_reach_marketing`` let in, and the owner has since asked for
    a personal hub "even with no seat at all" — a wider population than this
    URL was ever gated for. Rather than widen THIS view's own gate in place
    (which would leave two pages answering "your reminders", one of them
    stranded with no link pointing at it any more — the exact "second,
    separate reminders list" the owner asked NOT to end up with), every
    caller of this URL name is simply sent on to the page that replaced it:
    ``core/templates/base.html``'s own due-reminder banner links straight at
    ``my_tasks`` now and does not pass through here at all, but
    ``marketing/templates/marketing/directory.html`` and
    ``reminder_form.html`` still carry an older link by this name, and a
    redirect is what keeps them correct with no template edit required
    beyond this one function's body.

    A PLAIN, UNGATED REDIRECT — ``my_tasks`` runs its OWN gate on arrival
    (a linked Person record, not ``can_reach_marketing``), so re-checking
    anything here would only risk the two gates drifting apart; the one true
    answer to "may this viewer open the page reminders now live on" belongs
    in exactly one place.
    """
    return redirect("marketing:my_tasks")


# ``reminder_retime``/``reminder_done`` USED TO LIVE HERE, AND HAVE BEEN
# REMOVED ENTIRELY RATHER THAN LEFT WORKING AND UNLINKED. They were the
# original "set a new time" / "mark dealt with" pair — a plain reschedule or
# a mark-done with NO report ever required — from before this round's
# mandatory close-only-via-a-report cycle existed
# (``marketing/reminders.py::close_with_report``,
# ``marketing/views.py::my_tasks_report``). Retiring the CYCLE without
# retiring these two left a live, working bypass of it: any Marketing-seat
# viewer could still close out their own reminder here with zero report
# behind it, or silently reschedule one straight past
# ``TaskForm``'s own work-shift-window check, from a control that
# ``marketing/templates/marketing/company_detail.html``'s own Reminders tab
# (a tab this same round's company-page task edited) went on rendering.
# ``marketing/templates/marketing/company_detail.html``'s Reminders tab is
# the only template that still posted to them; it now carries the same
# "Submit report" control My Tasks already has, wired to the SAME
# ``my_tasks_report`` view below — see that template's own comment. With no
# caller left anywhere in the repo (confirmed by grepping for
# ``reminder_retime``/``reminder_done``/``reminders.reschedule``/
# ``reminders.mark_done`` before deleting any of it), a route that still
# worked would have been a live backdoor a saved bookmark or a forged POST
# could still hit — a REMOVED route 404s, which is the safe failure mode; a
# route that quietly still works is not, and is exactly what the reviewer
# who asked for this fix reproduced. The two service functions they alone
# called, ``marketing/reminders.py::reschedule``/``mark_done``, are removed
# with them for the identical reason — see that module's own comment where
# they used to be defined.


# --------------------------------------------------------------------------- #
# "My Tasks" (کارهای من) — the personal hub, open to every person with a
# linked Person record, seat or no seat at all
# --------------------------------------------------------------------------- #
# A DIFFERENT KIND OF PAGE FROM EVERYTHING ELSE IN THIS FILE, AND GATED
# DIFFERENTLY ON PURPOSE. Every other view above answers "may this viewer
# reach the MARKETING section" (``access_for``/``can_reach_marketing``) before
# it answers anything else; this page answers a wider question — "does this
# login have a linked personnel record at all" — the SAME question
# ``core/templates/base.html``'s own "Personal" nav group already asks for
# the neighbouring "Requests" entry (``nav_show_person_requests``, in
# ``core/context_processors.py``). The owner's own words are why: this page
# exists for every person, "even with no seat at all", so a Supply expert or
# a Technical manager who holds no Marketing seat at all still gets their own
# private to-do list here, the same one a Marketing Supervisor gets.
#
# REPLACES ``reminder_list`` ("My reminders") RATHER THAN SITTING BESIDE IT —
# see that view's own docstring, now a plain redirect here. AN EARLIER STAGE
# BUILT ONLY ITS REMINDERS TAB; THIS STAGE IS THE "LATER, ADDITIVE STAGE" that
# earlier one's own comments named — the Reports tab now sits beside it, on
# the exact same ``.tabs``/``.tab-panel`` shape ``company_detail.html`` already
# uses for its own five tabs, added with no change to that earlier tab's own
# markup or view logic — see ``marketing/templates/marketing/my_tasks.html``'s
# own head comment.
#
# WHAT DID NOT WIDEN, EVEN HERE: the LISTING this stage shows is deliberately
# ``scope="own"`` and nothing else — see ``my_tasks``'s own docstring for why
# an admin-wide equivalent is a later stage's job, not something to fold in
# quietly because ``reminders.list_for_user`` happens to be able to answer
# "all" now.
#
# THE CASE PICKER IS THE ONE GENUINELY NEW PERMISSION QUESTION THIS SECTION
# ASKS, because it is the first surface in this app that is NOT itself gated
# on Marketing access yet still needs to know which cases a viewer may see —
# see ``_my_tasks_case_access``'s own docstring for the full argument and why
# it is NOT simply ``case_access_for(request, access_for(request))`` the way
# every other Marketing page's is.


def _my_tasks_person_or_redirect(request):
    """This viewer's own linked Person record, or ``None`` after already
    sending them back to the workspace with a message telling them why.

    THE SAME GATE ``core/templates/base.html``'s own "Personal" nav group
    uses for the entry beside this one (``nav_show_person_requests`` in
    ``core/context_processors.py``) — a linked ``people.Person`` record and
    NOTHING role- or seat-specific — because My Tasks hangs off that exact
    group and must open for exactly the population it is offered to.
    ``people/views_requests.py::_person_or_redirect`` is the identical shape
    for "Requests", the neighbouring entry in that same group; this is that
    pattern applied here, not a second rule that could quietly drift from it.
    """
    from people.work_shift import person_for_user

    person = person_for_user(request.user)
    if person is None:
        messages.error(request, _("No personnel record is linked to this login."))
    return person


def _my_tasks_is_admin(request) -> bool:
    """Whether THIS login may open My Tasks' admin-wide variant
    (``scope="all"``, reached only through ``marketing:my_tasks_all`` — see
    ``my_tasks``'s own docstring for the full story of that entry point).

    REUSES ``people/views.py::_is_admin`` — THE EXACT SAME TEST THAT ALREADY
    GATES THE PEOPLE SECTION'S OWN ADMIN PAGES (``person_list``,
    ``person_seats`` and every other view behind ``admin_required``/
    ``admin_or_impersonating_admin_required`` in that file), rather than
    inventing a second admin check here or reaching for this app's own,
    BROADER ``marketing/access.py::Access.is_gm_or_admin`` tier, which also
    covers the General Manager. That is a deliberate, narrower choice, not an
    oversight: the owner's own wording for this entry point was "ادمین در
    قسمت اشخاص" — "the ADMIN, in the People section" — naming the
    administrator specifically, from the exact screen this check already
    gates, not the wider "Supervisor/GM/admin" population several OTHER
    Marketing screens use for their own "sees everyone" split (see, e.g.,
    ``company_detail``'s ``sees_all_reports``/``sees_all_reminders``, both
    keyed on ``access.is_gm_or_admin``). A Marketing Supervisor already sees
    every report and reminder ON ONE COMPANY from that company's own page,
    per an earlier round — a real, but NARROWER grant than this screen's
    "every row, on every company, from one list", which stays admin-only
    until the owner asks in as many words to widen it to the GM too.

    Imported locally, the same way ``_my_tasks_person_or_redirect`` reaches
    into ``people.work_shift`` rather than at module level — ``people.views``
    already imports ``marketing.models`` the identical way, inside a function
    body, for the identical reason: importing either module at the top of the
    other would be circular.
    """
    from people.views import _is_admin

    return _is_admin(request.user)


def _task_person_map(user_ids) -> dict:
    """``{user_id: Person}`` for every login among ``user_ids`` that has one
    linked — built for the admin-wide My Tasks list's own "Person" column and
    filter, so a reminder's ``owner`` or a report's ``created_by`` can be
    shown and searched by the PERSON who holds that login rather than by the
    raw account.

    ONE QUERY FOR THE WHOLE PAGE, not one per row — the same reasoning
    ``_task_rows``/``_task_report_rows`` already give for their own single
    case-visibility query, applied here to the new lookup this round adds.
    Goes through ``people.PersonAccount`` — the single seat-to-person link
    every login goes through (see that model's own docstring) — the SAME
    join ``people/work_shift.py::person_for_user`` reads for one user at a
    time; this is that lookup, batched.

    A ``user_id`` with no matching row (a seat-only login nobody has claimed
    as their own Person, or a ``None`` from a deleted ``created_by``) is
    simply absent from the returned dict — callers fall back to the account's
    own display name rather than treating a missing Person as an error, the
    same "show something rather than nothing" reasoning
    ``CompanyReport.author_display_name`` already applies to a vanished
    account.
    """
    from people.models import PersonAccount

    ids = {i for i in user_ids if i is not None}
    if not ids:
        return {}
    return {
        a.user_id: a.person
        for a in PersonAccount.objects.filter(user_id__in=ids)
                                       .select_related("person")
    }


def _my_tasks_case_access(request):
    """The case-visibility decision for My Tasks — the SAME four-way table
    ``marketing/access.py::case_access_for`` documents on its own docstring
    (admin/GM see every case; a Commercial MANAGER seat sees the entire
    archive; an ordinary Commercial seat sees only cases IT created; no
    Commercial seat at all sees none) — reached WITHOUT first requiring
    MARKETING-section access, because this page does not require it either.

    ``case_access_for``'S OWN FIRST LINE — ``if not access.can_view: return
    CaseAccess(can_open=False, all_cases=False)`` — has never had to be
    questioned before now. EVERY EXISTING caller of that function (the
    company page, the chart's own panel, the all-cases search) is itself a
    MARKETING page, already refused to anyone ``access_for`` would not let
    past its own gate — so that ``can_view`` check has only ever repeated a
    decision the caller had already made, one query earlier. My Tasks is the
    first caller that is NOT such a page (see this section's own header
    comment and ``my_tasks``'s docstring): an ordinary Commercial Expert with
    no Marketing access AT ALL would fail that very first line, for a reason
    (no Marketing access) that has nothing to do with which cases their OWN
    commercial seat created — and would be shown an empty case picker on a
    page that explicitly promises them their own cases, exactly the table
    ``_visible_case_rows_all``'s own docstring quotes.

    So this passes ``case_access_for`` a COPY of the real
    ``access_for(request)`` result with ``can_view`` forced ``True`` — the
    ONE field that guard reads. ``is_gm_or_admin`` (the only other field
    ``case_access_for``/``_case_money_visible`` ever look at) is left exactly
    as ``access_for`` computed it, so the admin/GM branch is still answered by
    the REAL decision, never by this override. Every branch reached past that
    guard — the Commercial Manager line, an ordinary Commercial seat's own
    created cases, no Commercial seat at all — reads THIS request's own seat
    list directly (``access.py::_own_commercial_seat_users`` and neighbours)
    and never reads ``can_view`` again, so the override touches exactly the
    one line it means to and nothing beneath it.
    """
    access = access_for(request)
    return case_access_for(request, dataclasses.replace(access, can_view=True))


def _task_rows(request, case_access, scope: str = "own") -> list:
    """This viewer's OWN reminders — or, when ``scope="all"``, EVERYONE's,
    for the admin-wide variant of this page (see ``my_tasks``'s own
    docstring's "``scope``" section for who may ever pass "all" here) — plus
    the display fields the template and its client-side filter/grouping both
    need:

    * ``person_name`` / ``person_key`` — ONLY MEANINGFUL, AND ONLY COMPUTED,
      WHEN ``scope="all"``: the admin-wide list's new "Person" column and
      filter target (see ``_task_person_map``). ``person_key`` is the row's
      owner's linked ``Person.detail_code`` — a stable, unique, never-reused
      identity (see ``people/models.py::Person.detail_code``'s own docstring)
      — rather than the display name, so two people who happen to share a
      name can never be confused by the equals-filter the way a name-keyed
      picker could risk (the same reason ``client_names`` below is safe to
      key on the plain company NAME instead: ``cases.Client`` names have no
      such collision risk documented anywhere in this app, ``Person`` full
      names are never asserted unique). A row whose owner holds no linked
      Person at all falls back to the account's own display name for
      ``person_name`` and an empty ``person_key`` — present in the list,
      simply unreachable through the Person filter, the identical "show
      something, filter on nothing" degrade ``_task_person_map`` documents.

    * ``is_due`` / ``state_text`` — derived off ONE ``timezone.now()`` for the
      whole page, the same "due is a comparison, never a stored value"
      reasoning ``marketing/models.py::ReminderState`` documents at length;
      ``state_text`` is the exact chip word the table prints ("Due" /
      "Waiting" / "Dealt with"), read straight back by the missed-only
      toggle's own client-side filter (see the template) so the two can
      never print one word and filter on another.
    * ``show_case_no`` / ``case_open_url`` — the SAME "a document number is
      case identity, withheld the instant this viewer's own access to it is
      gone" rule ``_reminder_rows`` already enforces for the company page,
      over THIS page's own ``case_access`` (see ``_my_tasks_case_access``)
      rather than the plain ``case_access_for(request)`` every other caller
      of this pattern uses — the reason is identical: this page's own
      population is wider than Marketing access alone.
    * ``day_key`` / ``hour_key`` — plain LOCAL strings (``YYYY-MM-DD`` and a
      zero-padded 24-hour ``HH``), computed off ``timezone.localtime`` the
      same way ``core/templatetags/ft_extras.py::jalali`` converts before it
      prints a stamp, so a row's own group key and its own printed time can
      never disagree about which calendar day or hour it falls in.
      ``day_key`` is read in TWO places: the CLIENT-SIDE day pills
      (``marketing/static/marketing/js/my_tasks.js``, unchanged this round)
      AND, server-side, ``_task_hour_groups`` below, which only ever places
      a row in an hour box when its own ``day_key`` is today's. ``hour_key``
      USED to also drive a client-side hour-pill click-filter; that pill row
      is gone (see ``_my_tasks_reminders.html``'s own head comment and
      ``_task_hour_groups``'s own docstring), so it is now read ONLY
      server-side, by that same function, to decide which box a row's
      content actually renders inside.

    ``row.created_at`` NEEDS NO COMPUTATION HERE AT ALL — ``Reminder.created_at``
    (``auto_now_add=True``) IS ALREADY ON EVERY ROW ``reminders.list_for_user``
    hands back, this function simply never read it into the template before
    this round. It is the "Created" column the owner asked for alongside the
    existing "Due" one — WHEN the reminder was SET, not when it falls due —
    on BOTH the personal and the admin-wide table (their own wording: shown
    "به آن شخص و ادمین", to that person and to admin, i.e. every viewer of
    either table). Nothing else about this field is special-cased per
    ``scope`` the way ``person_name``/``person_key`` are — every row, in
    either view, already carries its own real creation stamp regardless of
    who set it.

    ONE QUERY for the rows (``reminders.list_for_user``, already
    ``select_related`` for client/case/owner) plus ONE for the case-
    visibility check, plus — ``scope="all"`` ONLY — ONE MORE for
    ``_task_person_map``, still a fixed, small number of queries regardless
    of how many rows come back, the same two-(now three-)query shape
    ``_reminder_rows`` already uses for the company page's own Reminders tab.
    """
    from django.utils import timezone

    now = timezone.now()
    rows = reminders.list_for_user(request.user, scope=scope)
    visible = services._visible_case_ids(
        [r.case_id for r in rows], case_access)
    # See this function's own docstring's "person_name / person_key" section
    # — computed only for the admin-wide list, since an ordinary "own" visit
    # never renders a Person column or filter to begin with, and every row
    # in that case is this exact viewer regardless.
    person_map = _task_person_map(
        (r.owner_id for r in rows)) if scope == "all" else {}
    for row in rows:
        row.is_due = (row.state == ReminderState.OPEN and row.due_at <= now)
        # scope="all" can hand back somebody else's row (see this function's
        # own docstring) - the template's "Submit report" control must only
        # ever draw for a row this viewer actually owns, the exact is_own
        # pairing _reminder_rows already computes for the company page's own
        # Reminders tab (see that function, a few hundred lines up). Without
        # this, the admin-wide list drew the control on every row regardless
        # of owner - my_tasks_report's own server-side ownership check
        # (reminders.get_own) always refused it, so nothing was ever exposed,
        # but a control the server will refuse is still a bug (this file's
        # own stated principle, quoted in company_detail.html's own header).
        row.is_own = (row.owner_id == getattr(request.user, "pk", None))
        row.show_case_no = (row.case_id is not None and row.case_id in visible)
        row.case_open_url = (
            case_open_url(case_access, row.case_id) if row.show_case_no else "")
        local_due = timezone.localtime(row.due_at)
        row.day_key = local_due.date().isoformat()
        row.hour_key = "%02d" % local_due.hour
        if scope == "all":
            person = person_map.get(row.owner_id)
            row.person_name = (
                person.full_name if person is not None
                else row.owner.get_full_name() or row.owner.username)
            row.person_key = person.detail_code if person is not None else ""
        row.state_text = (
            "Dealt with" if row.is_done
            else "Due" if row.is_due
            else "Waiting"
        )
    return rows


def _task_day_groups(rows) -> list:
    """The day-pill data for My Tasks' Reminders tab — Today and Tomorrow
    ALWAYS (even carrying nothing, so the person can still open Today and see
    their own now-empty shift laid out in hour blocks), then every FUTURE day
    past tomorrow that has at least one of THESE rows due on it and no
    others — the owner's own instruction, "do not show empty future days": a
    pill for a day the table underneath has nothing in is a control that
    opens an empty list for no reason.

    BUILT OFF THE SAME ``rows`` THE TABLE RENDERS, never a second query — it
    groups by ``row.day_key`` (see ``_task_rows``), so the count printed on
    each pill and the rows that pill's click actually reveals (matched on
    that identical key, client-side) can never disagree.
    """
    import datetime as _dt
    from collections import Counter

    from django.utils import timezone

    today = timezone.localtime(timezone.now()).date()
    tomorrow = today + _dt.timedelta(days=1)
    today_key, tomorrow_key = today.isoformat(), tomorrow.isoformat()
    counts = Counter(row.day_key for row in rows)
    groups = [
        # "label" is display text only — the day pill's own CLICK/FILTER
        # behaviour matches on "key" (a plain ISO date string) and
        # never on this word, so translating it here cannot affect which
        # rows a pill reveals; see this function's own docstring and
        # _my_tasks_reminders.html's own day-pill markup, which prints
        # ``g.label`` verbatim.
        {"key": today_key, "label": _("Today"), "date": None,
         "count": counts.get(today_key, 0), "is_today": True},
        {"key": tomorrow_key, "label": _("Tomorrow"), "date": None,
         "count": counts.get(tomorrow_key, 0), "is_today": False},
    ]
    for key in sorted(k for k in counts if k > tomorrow_key):
        groups.append({
            "key": key, "label": None, "date": _dt.date.fromisoformat(key),
            "count": counts[key], "is_today": False,
        })
    return groups


def _task_hour_blocks(user) -> list:
    """Hour blocks for TODAY, spanning THIS person's own work-shift window
    rather than a flat 24-hour grid — the owner's own wording, "طبق شیفت
    کاری اش" (according to their own work shift). USED TO be rendered as a
    row of clickable pills (see ``_task_hours_with_reminders``'s own
    docstring for the click-filter this round retired); now each one is an
    always-visible box (``_task_hour_groups`` below, and
    ``_my_tasks_reminders.html``'s own head comment), but the blocks
    themselves — which hours, and their own "HH:00" label — are unchanged;
    only what the template does with them is different. Built off
    ``people/work_shift.py::shift_window(person_for_user(user))`` — the SAME
    pair ``marketing/reminders.py::validate_due_at_shift`` reads, so the
    blocks a person sees here and the window their own submitted time is
    actually checked against can never disagree.

    REUSES ``_in_window`` RATHER THAN RE-DERIVING THE SAME COMPARISON A
    SECOND TIME — the identical overnight-safe helper
    ``validate_due_at_shift`` already leans on for its own reason (see that
    function's docstring). ONE PROBE PER HOUR, at its first minute and its
    last, is what turns "is this instant inside the window" into "does this
    whole hour overlap the window at all" with no separate wrap-around case
    to get wrong a second time — a shift that legitimately wraps past
    midnight still produces the right blocks instead of none.
    """
    import datetime as _dt

    from people.work_shift import _in_window, person_for_user, shift_window

    person = person_for_user(user)
    start, end = shift_window(person)
    blocks = []
    for h in range(24):
        if (_in_window(_dt.time(h, 0), start, end)
                or _in_window(_dt.time(h, 59), start, end)):
            blocks.append({"key": "%02d" % h, "label": "%02d:00" % h})
    return blocks


def _task_hours_with_reminders(rows) -> set:
    """Which of TODAY's own hour blocks (see ``_task_hour_blocks`` above)
    actually contain at least one of this viewer's own reminders — the
    owner's own ask for the hour-block row: every block used to render
    identically whether it held something due or nothing at all, so seeing
    what needs attention meant reading each one's own content in turn
    (originally: opening each hour's click-filter to find out; now: reading
    the box itself — see ``_task_hour_groups`` below for the click-filter
    this round retired). This just answers "which keys", nothing about
    count or which reminder — ``my_tasks.html`` only ever needs a yes/no per
    box to give it a distinct look (``has_reminder``, merged onto each block
    by the view, one flag per hour, muted styling for a "no" — see
    ``_my_tasks_reminders.html``'s own head comment).

    REUSES ``row.day_key``/``row.hour_key`` RATHER THAN RE-DERIVING THEM FROM
    ``row.due_at`` A SECOND TIME — the exact pair ``_task_rows`` already
    computes off ``timezone.localtime`` (see that function's own docstring);
    recomputing here off the raw (UTC-stored) ``due_at`` would risk a
    reminder just after local midnight disagreeing with which day/hour the
    table and this set say it falls in. ``today_key`` is computed the same
    ``timezone.localtime`` way for the identical reason — a naive ``date()``
    on ``now()`` would be the SERVER's date, not this viewer's own local
    one, on any deployment where they ever differ.

    Takes plain ``rows`` (ALL of them, any day) rather than a scope/user pair
    — the view already has them in hand for the table itself, and this is a
    one-line filter over an in-memory list, not a query of its own.
    """
    from django.utils import timezone

    today_key = timezone.localtime(timezone.now()).date().isoformat()
    return {row.hour_key for row in rows if row.day_key == today_key}


def _task_hour_groups(rows, hour_blocks) -> list:
    """Attaches TODAY's own reminders onto their matching hour block, for
    the always-visible per-hour boxes ``_my_tasks_reminders.html`` now
    renders instead of the click-to-filter hour-pill row this round retired
    — the owner's own instruction, "کلا تب های ساعت‌ها ... را خالی بزار"
    (empty the hour-tabs concept out entirely): rather than one flat table a
    click narrowed by hour, every shift hour is now its own box, and a box
    whose ``has_reminder`` is true renders its OWN matching rows directly,
    with no click needed to reach them.

    TAKES ``hour_blocks`` AFTER ``has_reminder`` HAS ALREADY BEEN STAMPED
    ONTO IT (the view's own call order, just below) and returns the SAME
    list with one more key, ``rows``, added to each block — never a second,
    competing notion of which hour a reminder belongs to: both keys are
    read off the identical ``row.hour_key`` (``_task_rows``), so a block's
    own "does it have anything" flag and the actual rows its box renders
    can never disagree with each other.

    ONE PASS OVER ``rows`` — a plain dict keyed by ``hour_key``, built once
    and looked up once per block — rather than the nested "for every block,
    scan every row" a template-side grouping loop would otherwise have to
    do; with a shift window in the dozens of hours at most and a viewer's
    own reminder count typically far smaller neither shape would ever be
    slow in practice, but a single pass costs nothing extra to write and
    keeps the template a flat loop instead of a nested one — see this
    page's own docstring for why the grouping was done here, server-side,
    rather than in the template.

    TODAY ONLY, the exact restriction ``_task_hour_blocks``/
    ``_task_hours_with_reminders`` already carry — a row whose ``day_key``
    is not today's own local date is simply never placed in any group,
    exactly as it was never reachable through the old hour-pill row either.

    MUTATES AND RETURNS THE SAME ``hour_blocks`` LIST — the same shape
    ``_task_rows`` already uses when it stamps display fields directly onto
    each row rather than building a parallel structure; there is exactly
    ONE list of hour blocks on this page, this function's own job is only
    ever to add one more key to each entry already in it.
    """
    from django.utils import timezone

    today_key = timezone.localtime(timezone.now()).date().isoformat()
    by_hour: dict = {}
    for row in rows:
        if row.day_key == today_key:
            by_hour.setdefault(row.hour_key, []).append(row)
    for block in hour_blocks:
        block["rows"] = by_hour.get(block["key"], [])
    return hour_blocks


def _task_report_rows(request, case_access, scope: str = "own") -> list:
    """My Tasks' own Reports tab rows — every ``CompanyReport``
    ``services.list_reports_for_user`` returns for this viewer under
    ``scope`` (see ``my_tasks``'s own docstring for who may ever pass
    ``"all"``), each carrying the display fields the template needs beyond
    what ``_report_row`` already puts on the dict:

    * ``case_open_url`` — WHERE this row's case number actually links to, the
      identical ``access.case_open_url`` call ``_task_rows`` already makes for
      the Reminders tab, over this page's own ``case_access`` (see
      ``_my_tasks_case_access``) rather than the plain
      ``case_access_for(request)`` every Marketing-gated page uses — computed
      only when ``case_id`` survived ``_report_row``'s own redaction (i.e. is
      not ``None``), since a redacted case has no number to link either.
    * ``has_timeline`` — whether THIS report closed out a reminder, i.e.
      whether ``close_with_report`` (and not the ordinary ``add_report`` path)
      is the row's writer. ``reminder_set_at`` is the one of the three frozen
      fields that can never be a legitimately-blank value on a row that DID
      close a reminder (an owner who set one with no note still has a real
      "set" instant), so testing it — and not, say, ``reminder_note``, which
      a standalone report also leaves blank — is the one test that cannot
      mistake a plain report for a closed-reminder one or the reverse. See
      ``marketing/models.py::CompanyReport``'s own docstring for why these
      three columns can only ever be a frozen copy in the first place.
    * ``person_name`` / ``person_key`` — ``scope="all"`` ONLY, the identical
      pair ``_task_rows`` computes for the Reminders tab and for the same
      reason (see that function's own docstring); here keyed off
      ``created_by_id`` rather than ``owner_id`` since a report's writer is
      recorded under that name. A report whose ``created_by`` has since been
      removed (``SET_NULL`` — see ``CompanyReport``'s own docstring) falls
      back to the row's own FROZEN ``author`` name rather than the account
      lookup, which finds nothing for a ``None`` id — the one column on this
      row that was built to survive exactly that.

    ONE QUERY for the rows (``services.list_reports_for_user``, itself already
    ``select_related``/``prefetch_related`` for a company-spanning list) plus
    ONE for the case-visibility check, plus — ``scope="all"`` ONLY — ONE MORE
    for ``_task_person_map``, the same fixed-count shape ``_task_rows`` uses
    for the identical reason.
    """
    rows = services.list_reports_for_user(request.user, case_access, scope=scope)
    person_map = _task_person_map(
        (r.get("created_by_id") for r in rows)) if scope == "all" else {}
    for row in rows:
        row["case_open_url"] = (
            case_open_url(case_access, row["case_id"])
            if row.get("case_id") is not None else "")
        row["has_timeline"] = row.get("reminder_set_at") is not None
        if scope == "all":
            person = person_map.get(row.get("created_by_id"))
            row["person_name"] = (
                person.full_name if person is not None else row["author"])
            row["person_key"] = person.detail_code if person is not None else ""
    return rows


@login_required
def my_tasks(request, scope: str = "own"):
    """"My Tasks" (کارهای من) — the personal hub every person with a linked
    Person record reaches from the sidebar's own "Personal" group, seat or
    no seat at all. AN EARLIER STAGE BUILT ITS REMINDERS TAB; A LATER ONE
    ADDED THE REPORTS TAB BESIDE IT, on the same page — see the template's
    own head comment for the tab-strip structure that addition slots into,
    with no change to the Reminders tab's own markup or the view logic
    behind it.

    ``scope`` — "own" (the default, every existing URL into this view) OR
    "all", reached ONLY through the separate ``marketing:my_tasks_all`` URL
    (``/marketing/tasks/all/``) the People section's own sidebar links to
    (``people/templates/people/_nav.html``) — is THIS ROUND'S OWN ADDITION,
    the admin-wide variant of this exact page the owner asked for: "ادمین در
    قسمت اشخاص" ("the admin, in the People section") should be able to see
    EVERYONE's reports and reminders, not just their own, with one more
    filter — by PERSON — beyond the case/company/date-range filters this
    page already had.

    THIS IS DELIBERATELY THE SAME VIEW AND THE SAME TEMPLATE, PARAMETERISED
    BY ``scope``, NOT A SECOND, PARALLEL PAGE — the owner's own instruction
    for this round, and the shape ``marketing/reminders.py::_scoped`` and
    ``marketing/access.py``'s own ``scope`` concept already use everywhere
    else in this app for exactly an admin/GM-sees-everyone split. Every
    difference between the two visits — whose rows come back
    (``_task_rows``/``_task_report_rows``, both threaded ``scope``), and
    whether the Person filter field and column exist at all
    (``scope_all`` — see ``_my_tasks_reminders.html``/
    ``_my_tasks_reports.html``) — is confined to those two call sites and a
    handful of ``{% if scope_all %}`` blocks in the templates; the layout,
    the tab strip, the day/hour grouping, and every OTHER filter are the
    SAME markup for both visits, so a fix to one can never silently miss the
    other the way two separate templates could drift.

    GATED, FOR ``scope="all"`` ONLY, ON ``_my_tasks_is_admin`` — see that
    helper's own docstring for exactly which population this is (admin
    only, not the wider Marketing "Supervisor/GM/admin" tier several other
    screens in this app use for their own "sees everyone" split) and why.
    Checked HERE, on the view, not merely hidden from nav — the nav link in
    ``_nav.html`` is reachable only from that same "People section, admin
    only" sidebar group to begin with, but a link is not a permission, and a
    typed URL must be refused exactly as one that arrives from nowhere at
    all. Refused with the SAME ``marketing/denied.html`` 403 page every
    other admin/access refusal in this app already uses, never a redirect —
    the section header comment above gives the reason: a redirect would have
    to guess a destination for a login this decision does not cover, and "you
    may not open this" is the honest answer to give. An UNRECOGNISED ``scope``
    value (there is no way to reach one through either real URL, both of
    which pass a literal string, but a defensive read costs nothing) is
    folded into "own" before anything else runs — the same fail-closed
    direction ``reminders.py::_scoped`` documents for its own unrecognised
    ``scope``: too little rather than somebody else's private notes.

    THE PERSONAL-PERSON-RECORD GATE (``_my_tasks_person_or_redirect``, see
    below) APPLIES ONLY TO ``scope="own"``. The admin-wide visit is not a
    personal hub at all — it is reached from an ADMIN-ONLY sidebar group by
    a login that has already cleared ``_my_tasks_is_admin``, and the
    reserved platform ``admin`` account this app's own docstrings describe
    elsewhere (``accounts/models.py``: "the reserved ``admin`` login and
    nothing else") is never guaranteed a linked ``people.Person`` at all —
    so requiring one here would lock the very login this screen is built for
    out of it. ``person`` is still resolved, without the redirect, so the
    "Work calendar" button keeps working for an admin who DOES happen to
    hold a linked Person record, and simply does not render for one who does
    not (see the template's own existing ``{% if person %}`` guard — nothing
    new needed there).

    THE REPORTS TAB, IN ONE LINE: every ``CompanyReport`` this exact viewer
    wrote (``created_by=request.user`` — or, under ``scope="all"``, every
    ``CompanyReport`` anyone wrote; see ``services.list_reports_for_user``'s
    own docstring for that split), across every company at once, filterable
    the same way the Reminders tab is (a searchable case picker, a searchable
    company picker, a Jalali date-range pair — this time on the report's own
    ``created_at``, its natural date field, plus, under ``scope="all"``, the
    same Person picker the Reminders tab gains), and
    rendered by ``_task_report_rows``/``marketing/templates/marketing/
    _my_tasks_reports.html``. A report that CLOSED OUT a reminder carries its
    three frozen ``reminder_set_at``/``reminder_due_at``/``reminder_note``
    fields (see ``marketing/models.py::CompanyReport``'s own docstring) and
    the template draws them as a horizontal Set → Due → Reported timeline;
    ``my_tasks_add``'s own ``closed_report`` display already shows the
    identical three fields once, right after closing, but THIS tab is where
    the report actually lives afterwards, so the same timeline is drawn here
    too rather than being a one-time confirmation nobody can find again. A
    PLAIN report — never tied to a reminder — has all three blank and is
    shown with no timeline at all; see ``_task_report_rows``'s own
    ``has_timeline`` flag, which is exactly that test. "ADD REPORT"
    (``my_tasks_report_add``) is a separate, standalone creation form, not a
    third step bolted onto the reminder-closing flow — see that view's own
    docstring for why company and case are independently optional there too.

    REPLACES the standalone "My reminders" page (``reminder_list``, now a
    redirect here — see that view's own docstring) rather than living beside
    it: one page answers "what do I need to deal with", not two.

    GATED ON A LINKED PERSON RECORD, NOT ON ``access_for``/
    ``can_reach_marketing`` — see this section's own header comment and
    ``_my_tasks_person_or_redirect``. Deliberately the SAME, wider population
    ``core/templates/base.html``'s own "Personal" nav group is offered to.

    LISTING STAYS THE ORDINARY, OWNER-SCOPED VIEW FOR EVERY VISIT EXCEPT THE
    ADMIN-WIDE ONE — ``scope="own"`` is still the one and only value every
    pre-existing URL into this view ever passes, so "MY Tasks" reached from
    the "Personal" nav group still shows exactly what it always has, never
    "everyone's", the exact surprise widening an earlier round's version of
    this docstring warned against. ``scope="all"`` is that widening, made
    real at last — but ONLY through the separate, admin-gated
    ``marketing:my_tasks_all`` URL this round adds, never by loosening what
    the ordinary ``marketing:my_tasks`` URL means.

    THE CASE PICKER GOES THROUGH ``_visible_case_rows_all``/
    ``_my_tasks_case_access`` — see that helper's own docstring for why this
    page cannot simply reuse ``case_access_for(request, access_for(request))``
    the way every other Marketing page does, and never offers a case this
    viewer's own commercial seat access would refuse.

    THE DAY PILL AND THE FILTER CARD ARE BOTH CLIENT-SIDE, over the ONE
    table this view renders every one of this viewer's own rows into —
    ``static/js/ui.js``'s existing ``data-filter-table`` pass, the exact
    mechanism the case archive and ``company_detail.html``'s own Reports/
    Reminders tabs already use, driven here by a few extra HIDDEN columns
    (Day/State) and a small page-only script
    (``marketing/static/marketing/js/my_tasks.js``) that sets those hidden
    controls' values and re-runs the SAME filter pass ui.js already exposes
    (``table.ftApplyFilters``) rather than teaching the table a second way to
    hide a row. See the template for the full wiring.

    THE HOUR BOXES ARE SERVER-SIDE GROUPING, NOT A THIRD HIDDEN COLUMN — an
    earlier version of this page ALSO put Hour behind a hidden column and a
    click-filter pill row the way Day and State still are; that pill row is
    gone this round (``_task_hour_groups``, and ``_my_tasks_reminders.html``
    's own head comment, both explain why), replaced by boxes the view
    itself groups ``rows`` onto. The filter card still has to reach the
    reminders rendered inside those boxes, which are plain ``<div>``s, not
    the ``<tr>``s ``data-filter-table`` already knows how to hide — see the
    template's own head comment and ``my_tasks.js``'s own for the small,
    page-owned second pass that keeps them in step with the SAME filter
    card instead.

    ``?from=``/``?to=`` PRE-FILL THE REMINDERS TAB'S OWN DUE-DATE RANGE, A
    NEW ENTRY POINT ADDED FOR THE SHIFT PAGE'S OPEN-REMINDER BADGES
    (``people/views.py::person_shift``/``person_shift_month``, each card's
    own count of THIS person's open reminders due in that year/month/day).
    ``people/views.py::_my_tasks_period_url`` builds exactly this URL. THIS
    IS NOT A SECOND FILTERING MECHANISM: the two values are handed straight
    back onto the SAME ``data-jalali-datetime``/``data-filter-mode="gte"``/
    ``"lte"`` inputs the Reminders tab's own filter card already carries
    (see ``_my_tasks_reminders.html``) via their ``value=`` attribute, the
    identical pattern ``cases/templates/cases/archive.html`` already uses
    for its own ``?ffrom=``/``?fto=`` (see ``cases/views.py``'s
    ``f_active``) — ``static/js/ui.js``'s existing filter pass runs on page
    load regardless of whether a value arrived by typing or by markup, so no
    new row-hiding logic was written for this, only two more values threaded
    through to inputs that already drive it. A visit with neither parameter
    (every ordinary link into this page) renders those two inputs exactly as
    empty as before — this is purely additive.
    """
    # See this function's own docstring's "``scope``" section — the only
    # two values either real URL ever passes are "own" and "all"; anything
    # else (unreachable through either, but read defensively) fails closed
    # to "own", the same direction ``reminders.py::_scoped`` documents for
    # its own unrecognised ``scope``.
    if scope != "all":
        scope = "own"

    if scope == "all":
        if not _my_tasks_is_admin(request):
            return render(request, "marketing/denied.html", status=403)
        # See the docstring's "THE PERSONAL-PERSON-RECORD GATE" section — no
        # redirect on a miss here, unlike the "own" branch just below: this
        # visit is not gated on holding a linked Person at all.
        from people.work_shift import person_for_user
        person = person_for_user(request.user)
    else:
        person = _my_tasks_person_or_redirect(request)
        if person is None:
            return redirect("core:home")

    case_access = _my_tasks_case_access(request)
    case_choices = _visible_case_rows_all(request, case_access)
    rows = _task_rows(request, case_access, scope=scope)
    report_rows = _task_report_rows(request, case_access, scope=scope)
    # The hour-box row's own "does this hour actually have something due"
    # mark — see _task_hours_with_reminders's own docstring. Computed over
    # THIS viewer's own `rows` (already in hand for the table itself, not a
    # second query) and merged onto `hour_blocks` here, in the view, rather
    # than in the template: a template loop deciding membership in a set
    # built from a second loop is exactly the kind of two-source-of-truth
    # drift this file's own rows elsewhere are built to avoid.
    hour_blocks = _task_hour_blocks(request.user)
    hours_with_reminders = _task_hours_with_reminders(rows)
    for block in hour_blocks:
        block["has_reminder"] = block["key"] in hours_with_reminders
    # THEN, and only then, hand hour_blocks (has_reminder already stamped)
    # to _task_hour_groups so it can add each block's own `rows` — see that
    # function's own docstring for why this order matters and why the
    # grouping happens here rather than as a nested loop in the template.
    hour_groups = _task_hour_groups(rows, hour_blocks)

    return render(request, "marketing/my_tasks.html", {
        "active_tab": "reminders",
        # This viewer's own linked Person, when they have one — used only by
        # the "Work calendar" entry point in the page head (see the
        # template), which links to ``people:person_shift`` for THIS exact
        # ``pk`` and is itself guarded by ``{% if person %}`` there.
        # Guaranteed non-``None`` for ``scope="own"`` by the redirect above;
        # may genuinely be ``None`` for ``scope="all"`` (see the docstring).
        "person": person,
        # ``scope``/``scope_all`` — read by the template for every place the
        # two visits differ: page heading and helper text, the Person filter
        # field and column on both tabs, and (see ``my_tasks.html``'s own
        # head comment) nothing about the tab strip or layout itself, which
        # stays identical either way.
        "scope": scope,
        "scope_all": scope == "all",
        "rows": rows,
        "case_choices": case_choices,
        "client_names": sorted({r.client.name for r in rows if r.client_id}),
        # The Reminders tab's own Person picker — ``[]`` outside
        # ``scope="all"``, which is fine: the template never draws the field
        # at all outside ``scope_all``, so this is simply unused then.
        # GUARDED ON ``scope``, NOT MERELY ON ``r.person_key`` — ``_task_rows``
        # never sets ``person_name``/``person_key`` on a ``scope="own"`` row
        # at all (see that function's own docstring), so reading either
        # attribute unconditionally here would raise ``AttributeError``
        # rather than quietly finding nothing.
        "person_choices": sorted(
            {(r.person_name, r.person_key) for r in rows if r.person_key}
        ) if scope == "all" else [],
        "day_groups": _task_day_groups(rows),
        # Each hour block PLUS its own matching rows (`_task_hour_groups`
        # above) — the always-visible per-hour boxes render straight off
        # this, one flat loop, rather than filtering `rows` themselves once
        # per hour (see that function's own docstring).
        "hour_groups": hour_groups,
        # The Reminders tab's own Due-date range, pre-filled from the query
        # string — see this view's own docstring's "``?from=``/``?to=``"
        # section. Blank on every ordinary visit (``request.GET`` carries
        # neither key), so this changes nothing for any link into this page
        # that does not ask for it.
        "due_from": (request.GET.get("from") or "").strip(),
        "due_to": (request.GET.get("to") or "").strip(),
        # The Reports tab — see this view's own docstring's "THE REPORTS TAB"
        # section. ``case_choices`` above is reused as-is for its own case
        # picker (the SAME scoped rows, the SAME reason ``_reminder_case_choices``
        # and ``_report_case_choices`` already share one list on the company
        # page): never offer, on either tab, a case the other one would have
        # refused.
        "report_rows": report_rows,
        "report_client_names": sorted(
            {r["client_name"] for r in report_rows if r["client_id"]}),
        # The Reports tab's own Person picker — same reasoning as
        # ``person_choices`` above, over the Reports tab's own rows.
        "report_person_choices": sorted(
            {(r["person_name"], r["person_key"])
             for r in report_rows if r.get("person_key")}),
    })


@login_required
def my_tasks_add(request):
    """Add a task from My Tasks — OR, when reached with ``?client=``/
    ``?case=``/``?next=`` in the query string, the "set your next reminder"
    step that follows closing one out (see ``my_tasks_report`` below, and
    ``marketing/reminders.py::close_with_report``'s own docstring for the
    full two-step cycle this is the second half of). ONE VIEW SERVES BOTH,
    deliberately: the form is identical either way — company optional, case
    optional, a note, a due time — and the only real difference is which
    values arrive already picked. The query string is read ONLY for that
    pre-fill and for the template's own heading/button wording
    (``is_next_step``), never to change what gets validated or written.

    THE OWNER'S OWN WORDING IS WHY BOTH FIELDS ARE INDEPENDENTLY OPTIONAL —
    see ``marketing/forms.py::TaskForm``'s own class docstring: "می‌تواند به
    اسم یک شرکت یا پرونده وصل کند یا حتی نه".

    WHEN A CASE IS PICKED BUT NO COMPANY WAS, this view attaches that case's
    OWN company as the reminder's company — a judgement call, not a rule the
    owner spelled out (their wording allows either reading), made on the
    reasoning that "a reminder about this case" already says which company it
    is about, so leaving the reminder's own company blank in that situation
    would be a worse answer than the one already implied by the case. AN
    EXPLICITLY PICKED COMPANY IS NEVER OVERRIDDEN BY THIS — a person naming a
    company that differs from their chosen case's own is a coherent thing to
    mean ("about this company, particularly this case elsewhere in it"), not
    a mistake for this view to silently correct.

    THE CASE IS RE-VALIDATED AGAINST THIS VIEWER'S OWN VISIBLE ROWS, ON
    WRITE, exactly as every other case-attaching view in this app already
    does (see ``reminder_add``) — the form's own ``ChoiceField`` has already
    refused anything outside what it was offered, but a hidden/POSTed value
    is a value the browser sends, never a fact.

    ``?report=`` (carried alongside ``?client=``/``?case=`` — see
    ``my_tasks_report``'s own redirect) IS WHERE THE CLOSED-OUT REPORT'S
    THREE FROZEN DATES FIRST GET SHOWN — the owner's own requirement that
    "when the reminder was SET, when it was DUE, and when the REPORT was
    SUBMITTED" appear together as one timeline SOMEWHERE the report is
    displayed. This screen is that "somewhere" for THIS stage (a later
    stage's Reports tab is free to show the identical three fields again —
    they are columns on the row, not a one-time message). Scoped to
    ``created_by=request.user`` on the lookup — never trust the id alone —
    so a guessed or tampered ``?report=`` value cannot surface a report
    written by somebody else on THIS, otherwise fully personal, screen.
    """
    person = _my_tasks_person_or_redirect(request)
    if person is None:
        return redirect("core:home")

    case_access = _my_tasks_case_access(request)
    case_choices = _visible_case_rows_all(request, case_access)
    # EVERY COMPANY IN THE DIRECTORY, not just this viewer's own reminders'
    # companies — see ``marketing/forms.py::TaskForm``'s own docstring: unlike
    # the case picker, there is no access rule at all over WHICH company a
    # bare marketing note may name (``Client`` rows are shared, platform-wide
    # directory data — see ``marketing/models.py``'s own module docstring),
    # so the full directory is the more useful list to search, the same
    # queryset ``marketing/views.py::directory`` itself starts from.
    client_choices = Client.objects.all()

    is_next_step = bool(
        request.GET.get("client") or request.GET.get("case")
        or request.GET.get("next"))
    # See the docstring's "``?report=``" section — a plain-scoped lookup,
    # never trusted on the id alone, so this is safe even from a hand-edited
    # URL that names a report belonging to somebody else.
    report_id = _int_or_none(request.GET.get("report"))
    closed_report = (
        CompanyReport.objects.filter(pk=report_id, created_by=request.user)
        .prefetch_related("options").first()
        if report_id is not None else None
    )

    # THE JUST-CLOSED REMINDER'S OWN company/case — read ONCE, off the query
    # string, regardless of request.method. Three readers share this exact
    # pair from here on: the GET branch below uses it to PRE-FILL the form;
    # the POST branch uses the SAME pair to LOCK what actually gets written
    # (see "LOCKED ON THE NEXT-REMINDER STEP" a few lines down — the owner's
    # own instruction that a next reminder set from this step must stay on
    # the SAME case/company, never a different one); and the template uses
    # it a third time to print the read-only, locked display in place of the
    # ordinary pickers. One value, three readers, so none of the three can
    # ever end up disagreeing about what this step is "for".
    client_raw = _int_or_none(request.GET.get("client"))
    case_raw = _int_or_none(request.GET.get("case"))
    # DISPLAY-ONLY LOOKUPS for the locked, read-only fields task_form.html
    # draws on this step (see that template's own comment) — never consulted
    # by the write path below, which re-validates client_raw/case_raw on its
    # own. ``case_choices`` is the exact, already-fetched list a few lines up
    # (the same one the picker's own ``<option>``s come from on every OTHER
    # visit), so finding this one row in it costs no new query; ``client``
    # rows are shared, platform-wide directory data with no access rule to
    # re-check (see ``TaskForm``'s own docstring), so a single by-pk lookup
    # is all that is needed for its name.
    locked_case_row = (
        next((row for row in case_choices if row["case_id"] == case_raw), None)
        if case_raw is not None else None
    )
    locked_client = (
        Client.objects.filter(pk=client_raw).first()
        if client_raw is not None else None
    )

    if request.method == "POST":
        form = TaskForm(
            request.POST, user=request.user,
            client_choices=client_choices, case_choices=case_choices,
        )
        if form.is_valid():
            data = form.cleaned_data
            allowed_case_ids = {row["case_id"] for row in case_choices}
            # LOCKED ON THE "SET YOUR NEXT REMINDER" STEP — the owner's own
            # wording: "اگر یادآور مجدد کاربر خواست بزاره باید برای همان
            # پرونده یا شرکت بزاره و قابل تغییر نباید باشند" (if the user
            # wants to set a next reminder, it must be for the SAME case or
            # company — not changeable). task_form.html draws both fields as
            # a plain, non-interactive read-only display on this step, so
            # nothing about them is even offered to change in the ordinary
            # UI — but that is a client-side courtesy only: a disabled
            # <select> is simply never submitted by a real browser, and a
            # hand-built POST could still carry a DIFFERENT client_id/
            # case_id the ChoiceField would happily accept (it only checks
            # "is this among the values THIS viewer may pick at all", not
            # "does it match what this particular screen was locked to"). So
            # on this step the form's own cleaned client_id/case_id are never
            # even read for the write — client_raw/case_raw (the SAME query-
            # string pair this view was reached with, the just-closed
            # reminder's own company/case) are used unconditionally instead.
            # They still pass through the IDENTICAL re-validation any other
            # pick would (``case_id in allowed_case_ids`` below) — a query-
            # string value is still user-controlled input, not yet a proven
            # fact, even though this time it did not arrive in the POST body.
            case_id = case_raw if is_next_step else data.get("case_id")
            client_id = client_raw if is_next_step else data.get("client_id")
            case = (Case.objects.filter(pk=case_id).select_related("client").first()
                    if case_id in allowed_case_ids else None)
            client = (Client.objects.filter(pk=client_id).first()
                      if client_id else None)
            # See the docstring's "WHEN A CASE IS PICKED BUT NO COMPANY WAS"
            # section: this only ever FILLS IN a blank, never overrides an
            # explicit pick.
            if client is None and case is not None:
                client = case.client
            reminders.create(
                request.user, client,
                note=data["note"], due_at=data["due_at"], case=case,
            )
            return redirect("marketing:my_tasks")
    else:
        initial = {}
        if client_raw is not None:
            initial["client_id"] = str(client_raw)
        if case_raw is not None:
            initial["case_id"] = str(case_raw)
        form = TaskForm(
            initial=initial, user=request.user,
            client_choices=client_choices, case_choices=case_choices,
        )

    return render(request, "marketing/task_form.html", {
        "form": form,
        "is_next_step": is_next_step,
        "closed_report": closed_report,
        "locked_client": locked_client,
        "locked_case_row": locked_case_row,
    })


@login_required
def my_tasks_report(request, reminder_id):
    """STEP 1 of closing a reminder from My Tasks: write the report that
    accounts for it. NO CASE PICKER AT ALL — the case (if any) is whichever
    one this reminder was already about, exactly the shape
    ``cases/views.py::case_report_add`` already uses for its own inline,
    case-IMPLIED report form (see that view's own docstring, which this one
    follows point for point): ``marketing.forms.ReportForm`` is reused
    UNCHANGED, its own hidden ``case_id`` field simply never read —
    ``marketing/reminders.py::close_with_report`` takes no case argument at
    all, it reads ``reminder.client``/``reminder.case`` itself, so a
    tampered hidden field could not attach the report to a different case
    even if this view tried to read it.

    OWNER-SCOPED BEFORE THIS VIEW EVEN RENDERS A FORM — ``reminders.get_own``
    is the SAME ownership check ``close_with_report`` itself runs a second
    time on submit; a reminder that does not exist and one that belongs to
    somebody else are answered identically here, a 404, for
    ``marketing/reminders.py::_own``'s own reason — a response must never be
    usable to discover that somebody else's private note exists.

    ON A VALID SUBMIT, ``reminders.close_with_report`` DOES EVERYTHING STAGE
    1 DESIGNED IT TO DO, IN ONE TRANSACTION — writes the report, freezes the
    closed reminder's own set/due/note dates onto it, and deletes the
    reminder — and this view's only remaining job is THE REDIRECT: on to
    ``my_tasks_add`` with ``?client=``/``?case=`` carrying the just-CLOSED
    report's OWN client/case (never anything from this request), so the "set
    your next reminder" step lands pre-filled with exactly what this
    reminder was about. See ``my_tasks_add``'s own docstring for that half of
    the cycle, and ``close_with_report``'s own docstring for why "then open a
    form for the next reminder" is deliberately a VIEW-level redirect and not
    something that function does itself: if the person cancels or navigates
    away at that next step, nothing more happens — the report this view just
    wrote is already saved and the old reminder is already gone regardless.
    """
    person = _my_tasks_person_or_redirect(request)
    if person is None:
        return redirect("core:home")

    reminder = reminders.get_own(request.user, reminder_id)
    if reminder is None:
        raise Http404("No such reminder.")

    access = access_for(request)
    can_manage_options = _can_manage_options(access)

    if request.method == "POST":
        form = ReportForm(request.POST, can_manage_options=can_manage_options)
        if form.is_valid():
            data = form.cleaned_data
            options = list(data["options"])
            new_option = (
                (data.get("new_option") or "").strip()
                if can_manage_options else ""
            )
            if new_option:
                # get_or_create, not create — same shape, same reason, as
                # ``report_create``'s own identical block.
                option, _created = ReportOption.objects.get_or_create(
                    name=new_option, defaults={"created_by": request.user})
                if option.pk not in {o.pk for o in options}:
                    options.append(option)
            report = reminders.close_with_report(
                reminder_id, request.user, text=data["text"], options=options)
            params = {"next": "1", "report": report.pk}
            if report.client_id:
                params["client"] = report.client_id
            if report.case_id:
                params["case"] = report.case_id
            return redirect(
                "%s?%s" % (reverse("marketing:my_tasks_add"), urlencode(params)))
    else:
        form = ReportForm(can_manage_options=can_manage_options)

    return render(request, "marketing/task_report_form.html", {
        "reminder": reminder,
        "form": form,
        "can_manage_options": can_manage_options,
    })


@login_required
def my_tasks_report_add(request):
    """"ADD REPORT" on My Tasks' own Reports tab — a STANDALONE creation
    form, entirely independent of ``my_tasks_report`` above. That view closes
    ONE SPECIFIC reminder and offers no case/company picker at all, because
    the reminder it closes already names both (or names neither, for a bare
    personal note); THIS view is reached with no reminder in play whatsoever
    — the owner's own ask for a report a person can simply sit down and write,
    the platform-wide equivalent of the company page's own ``report_add`` ->
    ``report_create`` pair, but with the company itself now an optional pick
    too rather than fixed by the URL.

    COMPANY AND CASE ARE BOTH INDEPENDENTLY OPTIONAL, exactly the shape
    ``marketing/forms.py::TaskForm`` already argues at length for a bare
    reminder — "می‌تواند به اسم یک شرکت یا پرونده وصل کند یا حتی نه" applies
    here word for word, a report is no less allowed to name neither. Neither
    field is a Django ``ChoiceField`` on any form class: they are plain
    ``<select data-combo>`` controls in the template, unbound to any Form
    object, the exact same shape ``report_case.html``'s own case picker
    already uses for the company page's step 1 — a picker that only ever
    DECIDES which screen to draw or which id to carry forward is not a rule
    that needs ``clean()``, and the VIEW re-validates whatever comes back
    against this viewer's own visible rows regardless (see below), a
    ``<select>`` being a value the browser sends and never a fact.

    THE REPORT ITSELF — OPTIONS, FREE TEXT, THE "AT LEAST ONE OF THEM" RULE,
    THE INLINE "ADD A NEW OPTION" FIELD — IS ``marketing/forms.py::ReportForm``,
    REUSED WHOLLY UNCHANGED, the identical class the company page's
    ``report_create`` and the case page's ``cases/views.py::case_report_add``
    already build on. Its own declared ``case_id`` (a plain, hidden
    ``IntegerField`` — see that class's own docstring for why it is validated
    here only as a NUMBER, never as a permission) is simply never rendered by
    this screen's own template; the visible case ``<select>`` below carries
    the SAME field name instead, so Django's own POST binding reads it in
    exactly the same way regardless of which control on the page wrote it —
    one extra widget in the markup, not a second form class or a change to
    this one.

    WHEN A CASE IS PICKED BUT NO COMPANY WAS, this view attaches that case's
    OWN company to the report — ``my_tasks_add``'s identical judgement call,
    restated here for a REASON THAT IS NOT MERELY COSMETIC ON THIS SCREEN: a
    report attached to a case but naming no company of its own would never
    appear on that case's own company page at all. ``services.list_reports``
    (the company page's own reader) filters ``CompanyReport.objects.filter
    (client=client)`` FIRST and only THEN narrows to ``case=case``
    (``cases/views.py::_case_marketing_reports``, the case page's own caller,
    passes ``case.client`` as exactly that first filter) — so a report whose
    ``client`` disagrees with its own ``case``'s company would silently vanish
    from both the company page's Reports tab and the case page's, the exact
    "should just work" claim this round was asked to verify rather than
    assume. Filling the blank in here is what keeps that claim true, not an
    afterthought. AN EXPLICITLY PICKED COMPANY IS NEVER OVERRIDDEN BY THIS —
    a person naming a company that differs from their chosen case's own is a
    coherent thing to mean, not a mistake for this view to silently correct.

    THE COMPANY PICKER SEARCHES THE FULL DIRECTORY, ``Client.objects.all()``,
    the identical queryset ``my_tasks_add`` already builds ``client_choices``
    from and for the identical reason (see ``TaskForm``'s own docstring): a
    ``Client`` is shared, platform-wide directory data with no access rule of
    its own, so there is nothing to scope here the way the case picker must
    be scoped. THE CASE PICKER IS ``_visible_case_rows_all``/
    ``_my_tasks_case_access``, the SAME pair every other My Tasks screen uses
    — never offer a case this viewer's own commercial seat access would
    refuse.

    ``services.add_report`` DOES THE WRITING (and the timeline row, when the
    resolved company is not ``None``), so there remains exactly one place in
    the app that creates a ``CompanyReport`` — this view resolves the company/
    case/options and hands ``cleaned_data`` over, precisely as
    ``report_create`` already does for the company page's own flow.

    REDIRECTS TO ``my_tasks`` WITH ``?tab=reports`` on success — the SAME
    ``?tab=`` mechanism ``reminder_add`` already uses to land back on the tab
    a write was made from (``marketing/static/marketing/js/directory.js``
    reads it once on load; the tab strip this page shares with
    ``company_detail.html`` needs no code of its own for this), so the writer
    sees the report they just filed rather than the Reminders tab's default.
    """
    person = _my_tasks_person_or_redirect(request)
    if person is None:
        return redirect("core:home")

    case_access = _my_tasks_case_access(request)
    case_choices = _visible_case_rows_all(request, case_access)
    # EVERY COMPANY IN THE DIRECTORY — see the docstring's "THE COMPANY
    # PICKER SEARCHES THE FULL DIRECTORY" section.
    client_choices = Client.objects.all()

    access = access_for(request)
    can_manage_options = _can_manage_options(access)

    # Read here, before validation, so an invalid submit can still re-render
    # the page with whichever company/case the writer had already picked —
    # the same courtesy ``report_create``'s own ``kept_id`` gives its own
    # case picker on a rejected step 2.
    selected_client_id = None
    selected_case_id = None

    if request.method == "POST":
        selected_client_id = _int_or_none(request.POST.get("client_id"))
        selected_case_id = _int_or_none(request.POST.get("case_id"))
        form = ReportForm(request.POST, can_manage_options=can_manage_options)
        if form.is_valid():
            data = form.cleaned_data
            allowed_case_ids = {row["case_id"] for row in case_choices}
            case_id = data.get("case_id")
            case = (Case.objects.filter(pk=case_id).select_related("client").first()
                    if case_id in allowed_case_ids else None)
            client = (Client.objects.filter(pk=selected_client_id).first()
                      if selected_client_id else None)
            # See the docstring's "WHEN A CASE IS PICKED BUT NO COMPANY WAS"
            # section — fills in a blank only, never overrides an explicit pick.
            if client is None and case is not None:
                client = case.client
            options = list(data["options"])
            new_option = (
                (data.get("new_option") or "").strip()
                if can_manage_options else ""
            )
            if new_option:
                # get_or_create, not create — the same shape, the same reason,
                # as every other inline-vocabulary write in this file.
                option, _created = ReportOption.objects.get_or_create(
                    name=new_option, defaults={"created_by": request.user})
                if option.pk not in {o.pk for o in options}:
                    options.append(option)
            services.add_report(
                client, request.user,
                text=data["text"], options=options, case=case,
            )
            return redirect(
                "%s?tab=reports" % reverse("marketing:my_tasks"))
    else:
        form = ReportForm(can_manage_options=can_manage_options)

    return render(request, "marketing/task_report_add.html", {
        "form": form,
        "client_choices": client_choices,
        "case_choices": case_choices,
        "can_manage_options": can_manage_options,
        "selected_client_id": selected_client_id,
        "selected_case_id": selected_case_id,
    })
