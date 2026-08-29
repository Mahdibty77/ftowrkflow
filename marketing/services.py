"""Entity/link operations: search, register, connect, and report.

Every function here is a thin, transaction-safe wrapper around the two models
in ``marketing/models.py`` — nothing here knows about HTTP, and nothing here
knows about a project or a case. See the module docstring of ``models.py`` for
why that boundary is deliberate at this step.

SCOPE. Most functions below take ``(user, scope)`` — ``scope`` is one of
"own" or "all", decided by ``marketing.access.access_for`` from the caller's
seat and never re-derived here (this module stays request-agnostic on
purpose, see ``marketing/views.py``). "own" filters every query down to rows
``user`` themselves registered; "all" is unfiltered. Nothing here trusts a
caller to have already applied the filter itself — that would make the scope
a UI nicety instead of the actual security boundary it has to be.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from .models import Entity, EntityLink
from .rolechart import ALL_FIELDS

FIELD_LABELS = {key: label for key, label, _abbr in ALL_FIELDS}
FIELD_KEYS = tuple(key for key, _label, _abbr in ALL_FIELDS)


def is_field(key: str) -> bool:
    return key in FIELD_LABELS


def _scoped(qs, user, scope):
    """``qs`` narrowed to ``user``'s own rows when ``scope == "own"``."""
    if scope == "own":
        return qs.filter(created_by=user)
    return qs


def field_counts(user, scope) -> dict:
    """``{field key: how many entities it holds}``, zero-filled for every field.

    Counts only ``user``'s own rows when ``scope == "own"``, every row when
    ``scope == "all"`` — an Expert's badge counts their own registrations, a
    Supervisor's or the GM's counts the whole unit's.
    """
    counts = {key: 0 for key in FIELD_KEYS}
    rows = _scoped(Entity.objects.all(), user, scope).values_list("field").order_by()
    for (field,) in rows:
        if field in counts:
            counts[field] += 1
    return counts


def search_entities(field: str, user, scope, query: str = "", limit: int = 25):
    """Entities in ``field``, within ``scope``, whose name contains ``query``."""
    qs = _scoped(Entity.objects.filter(field=field), user, scope)
    query = (query or "").strip()
    if query:
        qs = qs.filter(name__icontains=query)
    return list(qs.order_by("name")[:limit])


def entity_in_scope(pk: int, user, scope):
    """The entity ``pk``, or ``None`` if it does not exist or sits outside ``scope``.

    This is the actual security boundary for linking/unlinking (see
    ``marketing/views.py``): a bare ``Entity.objects.get(pk=pk)`` would let an
    Expert act on an ID that belongs to a colleague and was never shown to
    them, since the link row itself carries no owner of its own.
    """
    return _scoped(Entity.objects.all(), user, scope).filter(pk=pk).first()


def get_or_create_entity(field: str, name: str, user) -> Entity:
    """The entity named ``name`` in ``field``, owned by ``user`` — existing, or
    freshly registered.

    Uniqueness is per (field, name, created_by) (``Entity.Meta.constraints``),
    so this can only ever match a row already owned by THIS SAME user —
    registering a name a colleague already has in the same field is still a
    fresh row, because their directories are independent. That is correct and
    intentional: nothing here goes looking across owners for a name match.
    """
    name = (name or "").strip()
    entity, _created = Entity.objects.get_or_create(
        field=field, name=name, created_by=user,
    )
    return entity


def _ordered_pair(a: Entity, b: Entity):
    return (a, b) if a.pk < b.pk else (b, a)


@transaction.atomic
def link_entities(a: Entity, b: Entity, user) -> EntityLink:
    """Connect ``a`` and ``b``. Idempotent: linking an already-linked pair is a no-op."""
    if a.pk == b.pk:
        raise ValueError("an entity cannot be linked to itself")
    lo, hi = _ordered_pair(a, b)
    actor = user if getattr(user, "is_authenticated", False) else None
    link, _created = EntityLink.objects.get_or_create(
        entity_a=lo, entity_b=hi, defaults={"created_by": actor},
    )
    return link


def unlink_entities(a: Entity, b: Entity) -> None:
    lo, hi = _ordered_pair(a, b)
    EntityLink.objects.filter(entity_a=lo, entity_b=hi).delete()


def connections_of(entity: Entity, user, scope) -> list:
    """Every entity directly linked to ``entity`` that is within ``scope``, grouped by field.

    Returns an ordered list of ``{"field", "label", "entities"}`` — one entry
    per field that holds at least one directly-connected entity WITHIN SCOPE,
    in chart reading order. The link row itself is not scoped (participating
    in a link is not ownership), but the "others" it reveals are: an Expert
    querying their own focus entity must not see a connected entity owned by a
    different Expert, even though the link row connecting them exists.
    """
    pairs = EntityLink.objects.filter(
        Q(entity_a=entity) | Q(entity_b=entity)
    ).values_list("entity_a_id", "entity_b_id")
    other_ids = {b if a == entity.pk else a for a, b in pairs}
    others = _scoped(Entity.objects.filter(pk__in=other_ids), user, scope).order_by("field", "name")
    by_field = {}
    for e in others:
        by_field.setdefault(e.field, []).append(e)
    return [
        {"field": key, "label": FIELD_LABELS[key], "entities": by_field[key]}
        for key in FIELD_KEYS if key in by_field
    ]
