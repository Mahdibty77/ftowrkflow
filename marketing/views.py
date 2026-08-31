"""The Marketing unit's own screens.

One screen — the Marketing workspace — with two tabs on it: the project role
chart ("roles"), and a flat, searchable Companies directory beside it
("companies") — see ``marketing/templates/marketing/_companies.html`` and
``marketing/static/marketing/js/companies.js``. The tab strip is real markup
rather than a heading, so each tab is a line in ``TABS`` and a block in the
template and nothing else. A different second tab briefly lived here for a
session — an entity-directory grid the owner replaced with the click-on-chart
design the "roles" tab now uses — before this Companies tab took the second
slot.

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
* Commercial / Technical / Supply, at every rank, and an administrator — no.
  A hidden nav link is not a permission; this is enforced here, on the URL,
  and answers 403 to a direct GET. An admin who needs this screen can be given
  a Marketing seat, the platform's own answer for "somebody needs to see
  another unit's screen," which leaves a trace that they did.

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
from django.shortcuts import render
from django.views.decorators.http import require_POST

from cases.models import Client

from . import rolechart, services
from .access import access_for

# The tab strip on the Marketing workspace — "roles" stays first/default (the
# owner's explicit instruction: Companies sits to ITS right, not the reverse),
# see the module docstring for why a different second tab lived here briefly
# and does not any more.
TABS = (
    ("roles", "Projects & Roles"),
    ("companies", "Companies"),
)
DEFAULT_TAB = TABS[0][0]

# The Persian role name + English abbreviation for each of the twelve
# MarketingLabel keys, read off rolechart.ALL_FIELDS (the chart's own single
# source for this text) rather than retyped here — so the Companies tab's tab
# strip and label-editing panel can never drift from what the chart's own
# cards call the same field.
_LABEL_TEXT = {key: (fa, ab) for key, fa, ab in rolechart.ALL_FIELDS}

# The six chart fields no data source feeds this round (no case field and no
# manual tag can ever apply to them) — always badge 0. See the module
# docstring on ``home`` for where this is used.
_INERT_FIELDS = ("project", "phase", "laboratory", "tpi", "supplier", "rival")


@login_required
def home(request):
    """The Marketing workspace.

    Every field on the chart is its own clickable card. Twelve of the
    nineteen fields (``services.LABEL_KEYS``) are backed by
    ``services.companies_for_label`` — their badge is how many companies
    currently carry that label, manual or case-derived, scoped to this
    viewer. The "us" field is backed by ``services.us_connections`` instead
    (every client with a case, unscoped). The remaining six fields
    (``_INERT_FIELDS``) have no data source this round and always badge 0 —
    see ``marketing/rolechart.py`` for how the chart draws an empty card.
    """
    access = access_for(request)
    if not access.can_view:
        return render(request, "marketing/denied.html", status=403)

    active = (request.GET.get("tab") or "").strip() or DEFAULT_TAB
    if active not in dict(TABS):
        active = DEFAULT_TAB

    counts = {key: 0 for key in _INERT_FIELDS}
    counts.update(services.label_counts(request.user, access.scope))
    counts["us"] = len(services.us_connections(request.user))

    context = {
        "tabs": [{"key": k, "label": lb, "is_active": k == active}
                 for k, lb in TABS],
        "active_tab": active,
        "chart": rolechart.build(counts),
        "can_edit": access.can_edit,
    }
    if active == "companies":
        # Just the label text for the tab strip and the per-company editing
        # panel — the company list itself is never fetched here (see the
        # module docstring's pointer to companies.js): every list this tab
        # shows comes from the same JSON endpoints the chart already uses.
        context["labels"] = [
            {"key": k, "label_fa": _LABEL_TEXT[k][0], "abbr": _LABEL_TEXT[k][1]}
            for k in rolechart.LABEL_KEYS
        ]
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
    ``client_connections`` returns) so the Companies tab's "All" view can show
    label chips without a second round trip per row — batched in a constant
    number of queries via ``services.labels_for_clients``, not one query per
    result.
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
    """POST name= -> register a new client. Companies tab only — the chart
    itself never creates a client, so this is gated on ``can_edit`` alone,
    same as every other mutating endpoint here."""
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
    """GET ?label= -> the companies a chart card shows when clicked."""
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    label, err = _label_or_400(request)
    if err is not None:
        return err
    companies = services.companies_for_label(label, request.user, access.scope)
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
def client_connections(request):
    """GET ?client_id= -> the label/case report Inquiry shows for a company."""
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
    report = services.connections_of_client(client, request.user, access.scope)
    return JsonResponse({
        "ok": True,
        "client": _client_json(client),
        "labels": report["labels"],
        "cases": report["cases"],
    })


@login_required
def us_connections(request):
    """GET, no params -> every client connected to "our own position"."""
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    companies = services.us_connections(request.user)
    return JsonResponse({"ok": True, "companies": companies})
