"""The Marketing unit's own screens.

One screen so far — the Marketing Expert workspace — and one tab on it, the
project role chart. The tab strip is real markup rather than a heading, so the
second tab is a line in ``TABS`` and a block in the template and nothing else;
see the note on ``TABS`` for why it has only one entry today.

WHO MAY OPEN IT. Everything under this app is Marketing's own work, so the
rule is a single one and it is spelled out in ``_viewer_unit`` and applied by
``_marketing_only``: the viewer's ACTIVE seat must belong to the Marketing
unit. That is the whole gate. In particular:

* Marketing Expert  — yes. This is their workspace.
* Marketing Supervisor — yes, and not grudgingly: a supervisor who cannot see
  the work of their own unit is not supervising it. A Marketing Manager, if
  the unit is ever given one, is admitted by the same rule, because the rule
  is about the unit and not about the rank inside it.
* Commercial / Technical / Supply, at every rank — no. A hidden nav link is
  not a permission; this is enforced here, on the URL, and answers 403 to a
  direct GET.
* The general manager — no. The GM's window on this platform is
  ``reports.dashboard``, which is built from case figures; Marketing holds no
  case and has no dashboard card, and inventing a way in here would be
  inventing the report that was deliberately not built. When Marketing's own
  numbers are specified, that is the moment to decide what the GM sees.
* An administrator — no, for the same reason it is not a Commercial screen:
  ``is_admin`` on this platform is the user-and-code-table console, not a
  master key over unit work. An admin who needs to look at this page can be
  given a Marketing seat, which is the platform's own answer for "somebody
  needs to see another unit's screen" and leaves a trace that they did.

Anyone refused gets the same short page with a 403 on it, inside their own
navigation, rather than a redirect — a redirect would have to choose a
destination for a seat kind this app knows nothing about, and "you are not in
this unit" is the honest answer to give.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from accounts.constants import Unit

from . import rolechart, services
from .models import Entity

# The tab strip on the Marketing Expert workspace.
#
# TWO ENTRIES: the static chart from step one, and the entity directory that
# backs every field on it — the owner's own next step, requested in the same
# breath as "connect the fields to cases later." The strip itself, the active
# state and the ``?tab=`` handling were already here from the one-tab version;
# this is the tuple and the {% if %} that version's docstring said would be
# the whole cost of a real second tab.
TABS = (
    ("roles", "Projects & Roles"),
    ("directory", "Entities & Links"),
)
DEFAULT_TAB = TABS[0][0]


def _viewer_unit(request):
    """The unit of the seat this person is actually working right now.

    Resolved exactly the way ``cases.views.case_detail`` resolves it, and for
    the same reason: one human may hold several seats, and on a secondary seat
    the login's own ``Profile`` still describes the PRIMARY one. Reading the
    profile alone would refuse a Commercial expert who also holds a Marketing
    seat and has switched to it — the seat the sidebar is offering this link
    from. The profile is the fallback for a login with no PersonRole rows at
    all, which is most of them.
    """
    profile = getattr(request.user, "profile", None)
    fallback = (getattr(profile, "unit", "") or "").strip()
    try:
        from people.role_nav import work_context
        role = work_context(request).role
    except Exception:
        # The seat layer is an enhancement over the profile, never a
        # precondition for it: if it cannot answer, the profile still can, and
        # a page must not 500 because a seat row is malformed.
        return fallback
    if role is None:
        return fallback
    return (getattr(role, "unit", "") or "").strip() or fallback


def _marketing_only(request):
    """``None`` when the viewer may proceed, otherwise the refusal to return."""
    if _viewer_unit(request) == Unit.MARKETING:
        return None
    return render(request, "marketing/denied.html", status=403)


@login_required
def home(request):
    """The Marketing Expert workspace.

    The chart's data comes from ``rolechart.sample_chart()`` — a plain Python
    dict built from a literal, with no model, no query and no case behind it.
    That call is the seam: when the owner specifies how a project and its role
    rows are stored, this line becomes the query that builds the same
    ``{slot: organisation}`` mapping and hands it to ``rolechart.build()``, and
    neither the template nor the stylesheet changes. See the module docstring
    of ``marketing/rolechart.py``.
    """
    refusal = _marketing_only(request)
    if refusal is not None:
        return refusal

    active = (request.GET.get("tab") or "").strip() or DEFAULT_TAB
    if active not in dict(TABS):
        active = DEFAULT_TAB

    context = {
        "tabs": [{"key": k, "label": lb, "is_active": k == active}
                 for k, lb in TABS],
        "active_tab": active,
        "chart": rolechart.sample_chart(),
    }
    if active == "directory":
        counts = services.field_counts()
        context["directory_fields"] = [
            {"key": key, "label": label, "count": counts[key]}
            for key, label, _abbr in rolechart.ALL_FIELDS
        ]
    return render(request, "marketing/home.html", context)


# --------------------------------------------------------------------------- #
# The entity directory: search, register, link, and the "what is connected"
# report a selection produces. Every route below is JSON-only, reached from
# marketing/static/marketing/js/directory.js on the Entities & Links tab, and
# gated by the same _marketing_only rule as the page itself — a seat that
# cannot open the tab cannot read or write through these either.
# --------------------------------------------------------------------------- #
def _field_or_400(request, key_param="field"):
    field = (request.GET.get(key_param) or request.POST.get(key_param) or "").strip()
    if not services.is_field(field):
        return None, JsonResponse({"ok": False, "error": "Unknown field."}, status=400)
    return field, None


def _entity_json(entity: Entity) -> dict:
    return {"id": entity.pk, "field": entity.field, "name": entity.name}


@login_required
def entity_search(request):
    refusal = _marketing_only(request)
    if refusal is not None:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    field, err = _field_or_400(request)
    if err is not None:
        return err
    query = request.GET.get("q") or ""
    entities = services.search_entities(field, query)
    return JsonResponse({"ok": True, "entities": [_entity_json(e) for e in entities]})


@login_required
@require_POST
def entity_create(request):
    refusal = _marketing_only(request)
    if refusal is not None:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    field, err = _field_or_400(request)
    if err is not None:
        return err
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "A name is required."}, status=400)
    entity = services.get_or_create_entity(field, name, request.user)
    return JsonResponse({"ok": True, "entity": _entity_json(entity)})


def _entity_from_post(request, param):
    pk = request.POST.get(param)
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return Entity.objects.filter(pk=pk).first()


@login_required
@require_POST
def entity_link(request):
    refusal = _marketing_only(request)
    if refusal is not None:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    a = _entity_from_post(request, "a_id")
    b = _entity_from_post(request, "b_id")
    if a is None or b is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    if a.pk == b.pk:
        return JsonResponse({"ok": False, "error": "An entity cannot be linked to itself."}, status=400)
    services.link_entities(a, b, request.user)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def entity_unlink(request):
    refusal = _marketing_only(request)
    if refusal is not None:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    a = _entity_from_post(request, "a_id")
    b = _entity_from_post(request, "b_id")
    if a is None or b is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    services.unlink_entities(a, b)
    return JsonResponse({"ok": True})


@login_required
def entity_connections(request):
    refusal = _marketing_only(request)
    if refusal is not None:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    pk = request.GET.get("entity_id")
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    entity = Entity.objects.filter(pk=pk).first()
    if entity is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    groups = services.connections_of(entity)
    return JsonResponse({
        "ok": True,
        "entity": _entity_json(entity),
        "connections": [
            {"field": g["field"], "label": g["label"],
             "entities": [_entity_json(e) for e in g["entities"]]}
            for g in groups
        ],
    })
