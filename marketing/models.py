"""The Marketing entity directory: named values, and links between them.

WHAT THIS IS. Every field on the project role chart — sponsor, owner, each of
the four contract types, and so on, see ``rolechart.ALL_FIELDS`` for the full
list — owns its own directory of named values. "Sponsor" does not hold one
organisation; it holds every organisation that has ever been a sponsor, the
same way a case's client field is really a client LIST the platform already
knows. An :class:`Entity` is one such value: one name, in one field.

Two entities in different fields (an investor and a subcontractor, say) can be
declared connected — an :class:`EntityLink` — and from either end of that link
a person can ask "what else is directly connected to this one," across every
field, which is what lights up a field's card when one of its values is
selected elsewhere. See ``marketing/services.py`` for that query.

WHAT THIS DELIBERATELY IS NOT, yet. No project, no assignment of a value to a
project's own slot, and no connection to ``cases`` at all — the owner asked
for exactly this step ("دیتابیس مستقل" — its own, independent database) and
said explicitly that wiring it to a project or to a case is a later,
separately-requested step. The chart on the Roles tab still draws from
``rolechart.sample_chart()``, unchanged; this directory is reachable from its
own tab and touches nothing there.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from .rolechart import ALL_FIELDS

FIELD_CHOICES = [(key, label) for key, label, _abbr in ALL_FIELDS]


class Entity(models.Model):
    """One named value inside one field's directory (e.g. one sponsor)."""

    field = models.CharField(max_length=32, choices=FIELD_CHOICES, db_index=True)
    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["field", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["field", "name"], name="marketing_entity_unique_field_name",
            ),
        ]

    def __str__(self):
        return self.name


class EntityLink(models.Model):
    """An undirected connection between two entities, in any two fields.

    Undirected in meaning, stored as one directed row: ``entity_a`` is always
    the lower primary key of the pair, ``entity_b`` the higher — see
    ``services.link_entities``, the only place a link is created. That is what
    lets the unique constraint below actually stop a duplicate reverse edge;
    without a fixed order, (A, B) and (B, A) would be two different rows.
    """

    entity_a = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="links_as_a")
    entity_b = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="links_as_b")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity_a", "entity_b"], name="marketing_link_unique_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(entity_a=models.F("entity_b")),
                name="marketing_link_no_self",
            ),
        ]
