"""Entity/link operations: search, register, connect, and report.

Every function here is a thin, transaction-safe wrapper around the two models
in ``marketing/models.py`` — nothing here knows about HTTP, and nothing here
knows about a project or a case. See the module docstring of ``models.py`` for
why that boundary is deliberate at this step.
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


def field_counts() -> dict:
    """``{field key: how many entities it holds}``, zero-filled for every field."""
    counts = {key: 0 for key in FIELD_KEYS}
    rows = Entity.objects.values_list("field").order_by()
    for (field,) in rows:
        if field in counts:
            counts[field] += 1
    return counts


def search_entities(field: str, query: str = "", limit: int = 25):
    """Entities in ``field`` whose name contains ``query`` (all of them if blank)."""
    qs = Entity.objects.filter(field=field)
    query = (query or "").strip()
    if query:
        qs = qs.filter(name__icontains=query)
    return list(qs.order_by("name")[:limit])


def get_entity(field: str, pk: int):
    return Entity.objects.filter(field=field, pk=pk).first()


def get_or_create_entity(field: str, name: str, user) -> Entity:
    """The entity named ``name`` in ``field`` — existing, or freshly registered.

    A name is unique per field (``Entity.Meta.constraints``), so registering a
    name that already exists in that field simply returns the existing row
    rather than erroring — the owner's own example is picking an EXISTING
    value from a list just as often as typing a new one, and the two should
    not need different calls.
    """
    name = (name or "").strip()
    actor = user if getattr(user, "is_authenticated", False) else None
    entity, _created = Entity.objects.get_or_create(
        field=field, name=name, defaults={"created_by": actor},
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


def connections_of(entity: Entity) -> list:
    """Every entity directly linked to ``entity``, grouped by field.

    Returns an ordered list of ``{"field", "label", "entities"}`` — one entry
    per field that holds at least one directly-connected entity, in chart
    reading order. A field with no connection is simply absent from the list,
    which is the "stays dim" half of the light-up behaviour; the caller does
    not need to compute that separately.
    """
    pairs = EntityLink.objects.filter(
        Q(entity_a=entity) | Q(entity_b=entity)
    ).values_list("entity_a_id", "entity_b_id")
    other_ids = {b if a == entity.pk else a for a, b in pairs}
    others = Entity.objects.filter(pk__in=other_ids).order_by("field", "name")
    by_field = {}
    for e in others:
        by_field.setdefault(e.field, []).append(e)
    return [
        {"field": key, "label": FIELD_LABELS[key], "entities": by_field[key]}
        for key in FIELD_KEYS if key in by_field
    ]
