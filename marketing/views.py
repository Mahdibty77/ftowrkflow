"""The Marketing unit's own screens.

One screen — the Marketing workspace — and, for now, one tab on it, the
project role chart. The tab strip is real markup rather than a heading, so a
second tab (there was one here for a session — an entity-directory grid the
owner has since replaced with the click-on-chart design the frontend half of
this change builds) is a line in ``TABS`` and a block in the template and
nothing else.

WHO MAY OPEN IT, AND HOW MUCH THEY GET. See ``marketing/access.py`` for the
actual decision (``access_for``) — this is the plain-language version of it:

* Marketing Expert — sees and edits only the entities THEY THEMSELVES
  registered, per field. This is their own list, not a filtered view of a
  bigger one.
* Marketing Supervisor — sees and edits the union of everything every
  Marketing Expert has registered. A supervisor who cannot see the work of
  their own unit is not supervising it.
* The platform's actual General Manager (``profile.is_general_manager``) —
  sees that same union, but VIEW ONLY: every mutating endpoint below refuses
  them independently of whatever the page hides, because a GM who can browse
  is not the same grant as a GM who can edit someone else's directory.
* Commercial / Technical / Supply, at every rank, and an administrator — no.
  A hidden nav link is not a permission; this is enforced here, on the URL,
  and answers 403 to a direct GET. An admin who needs this screen can be given
  a Marketing seat, the platform's own answer for "somebody needs to see
  another unit's screen," which leaves a trace that they did.

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

from . import rolechart, services
from .access import access_for
from .models import Entity

# The tab strip on the Marketing workspace. One entry today — see the module
# docstring for why a second one lived here briefly and does not any more.
TABS = (
    ("roles", "Projects & Roles"),
)
DEFAULT_TAB = TABS[0][0]


@login_required
def home(request):
    """The Marketing workspace.

    Every field on the chart is its own clickable card now, backed by its own
    entity directory — there is no more organisation-name placeholder and no
    separate directory tab (see ``marketing/rolechart.py``'s module docstring
    for the redesign, and the docstring above for the tab that used to sit
    beside this one). Field counts are computed once, scoped to this viewer,
    and handed to ``rolechart.build`` for the count badge on each card;
    ``can_edit`` gates every add/attach control the template draws.
    """
    access = access_for(request)
    if not access.can_view:
        return render(request, "marketing/denied.html", status=403)

    active = (request.GET.get("tab") or "").strip() or DEFAULT_TAB
    if active not in dict(TABS):
        active = DEFAULT_TAB

    counts = services.field_counts(request.user, access.scope)
    context = {
        "tabs": [{"key": k, "label": lb, "is_active": k == active}
                 for k, lb in TABS],
        "active_tab": active,
        "chart": rolechart.build(counts),
        "can_edit": access.can_edit,
    }
    return render(request, "marketing/home.html", context)


# --------------------------------------------------------------------------- #
# The entity directory: search, register, link, and the "what is connected"
# report a selection produces. Every route below is JSON-only, gated by the
# same ``access_for`` decision as the page itself, and re-checked independently
# of whatever the calling template shows or hides — see the module docstring.
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
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    field, err = _field_or_400(request)
    if err is not None:
        return err
    query = request.GET.get("q") or ""
    entities = services.search_entities(field, request.user, access.scope, query)
    return JsonResponse({"ok": True, "entities": [_entity_json(e) for e in entities]})


@login_required
@require_POST
def entity_create(request):
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    field, err = _field_or_400(request)
    if err is not None:
        return err
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "A name is required."}, status=400)
    entity = services.get_or_create_entity(field, name, request.user)
    return JsonResponse({"ok": True, "entity": _entity_json(entity)})


def _entity_from_post(request, param, scope):
    pk = request.POST.get(param)
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return services.entity_in_scope(pk, request.user, scope)


@login_required
@require_POST
def entity_link(request):
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    # Re-fetched through the scoped queryset, not a bare Entity.objects.get —
    # this is the actual security boundary, not just a UI nicety: without it
    # an Expert could link two IDs even when one belongs to someone else and
    # was never shown to them.
    a = _entity_from_post(request, "a_id", access.scope)
    b = _entity_from_post(request, "b_id", access.scope)
    if a is None or b is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    if a.pk == b.pk:
        return JsonResponse({"ok": False, "error": "An entity cannot be linked to itself."}, status=400)
    services.link_entities(a, b, request.user)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def entity_unlink(request):
    access = access_for(request)
    if not access.can_edit:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    a = _entity_from_post(request, "a_id", access.scope)
    b = _entity_from_post(request, "b_id", access.scope)
    if a is None or b is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    services.unlink_entities(a, b)
    return JsonResponse({"ok": True})


@login_required
def entity_connections(request):
    access = access_for(request)
    if not access.can_view:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    pk = request.GET.get("entity_id")
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    entity = services.entity_in_scope(pk, request.user, access.scope)
    if entity is None:
        return JsonResponse({"ok": False, "error": "Unknown entity."}, status=400)
    groups = services.connections_of(entity, request.user, access.scope)
    return JsonResponse({
        "ok": True,
        "entity": _entity_json(entity),
        "connections": [
            {"field": g["field"], "label": g["label"],
             "entities": [_entity_json(e) for e in g["entities"]]}
            for g in groups
        ],
    })
