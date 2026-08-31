"""The Marketing company directory: shared clients, and Marketing's own tags on them.

WHAT THIS IS NOW. There is no more independent, Marketing-only directory of
named values. The company directory Marketing browses is the SAME table
Commercial uses to create cases — ``cases.models.Client`` — reused directly,
not mirrored into a parallel model. Every company on the chart is a real,
already-registered ``Client`` row, or becomes one the moment Marketing
registers it (see ``marketing/services.py::get_or_create_client``, which
creates a ``Client`` exactly the way ``cases/views.py::client_add`` already
does).

A client can carry any number of the fourteen business-role tags in
``cases.constants.MarketingLabel`` at once (a company can be both Sponsor and
Subcontractor, say) — the twelve in ``MarketingLabel.CHOICES`` plus rival and
supplier from ``MarketingLabel.MANUAL_ONLY_CHOICES`` (see ``ClientLabel.label``
below for why those two are manual-only). Those tags come from two independent
sources that this app merges only at read time (see ``marketing/services.py``,
in particular ``companies_for_label`` / ``connections_of_client``):

* MANUAL — a :class:`ClientLabel` row a Marketing user put on the client by
  hand. That is the only thing this module still models.
* CASE-DERIVED — implied live by ``cases.models.Case.marketing_label`` on
  every case that client has (falling back to OWNER when a case leaves that
  field blank). This is never stored here; it is read straight off
  ``cases.Case`` whenever a label needs computing.

"Our own position" — the chart's "us" card — is connected to a client ONLY
through this case-derived path. There is no manual way to link a client to
"us": a case is the only thing that can ever prove a real business
relationship with our own position, so that connection is never a row a
person can create or delete by hand.

WHAT THIS DELIBERATELY IS NOT, any more. The old ``Entity`` (one named value
per chart field, independently registered per field) and ``EntityLink`` (an
arbitrary undirected connection between any two entities in any two fields)
are gone entirely, along with the "any field can hold names, any two names
can be linked" shape they gave the directory. That shape never matched the
real business object Marketing was actually describing — a single company can
play several roles for several projects, and the truth of "is this company
connected to us" only ever lived in the cases those companies actually have,
never in a link a person drew by hand. See this module's own git history for
the version being replaced.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from cases.constants import MarketingLabel
from cases.models import Client


class ClientLabel(models.Model):
    """One business-role tag a Marketing user has manually put on a company.

    This is the MANUAL half of a client's labels — see
    ``marketing/services.py::companies_for_label`` /
    ``connections_of_client`` (and the module docstring above) for the other
    half: labels implied live by
    ``cases.models.Case.marketing_label``, which are never stored as rows
    here at all. A client can carry any number of manual labels, and the same
    (client, label) pair can be added independently by more than one user —
    a client that already carries a label purely because of its cases can
    ALSO carry that same label here as a manual row; the two are tracked
    independently, and it is ``services.py`` that de-duplicates them for
    display.

    ``created_by`` is who added this tag, and it is what scopes visibility
    (see ``marketing/access.py`` and every ``services.py`` function that
    takes a ``scope`` argument) — the same ownership shape the old ``Entity``
    model used: a Marketing Expert manages only the labels THEY THEMSELVES
    added, a Supervisor or the platform's GM sees the union of every Expert's.
    That is also why uniqueness below is per (client, label, created_by) and
    not just per (client, label): two Marketing Experts who both tag the same
    client "Sponsor" are not the same fact, because each expert's set of tags
    is their own until a supervisor or the GM looks at the union of
    everyone's — exactly the reasoning ``Entity``'s per-owner uniqueness used
    to carry, now applied to labels on a shared client instead of names in a
    field-scoped directory.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="marketing_labels")
    # Fourteen choices, not twelve: MarketingLabel.CHOICES (the twelve roles
    # a CASE's client could hold) PLUS MarketingLabel.MANUAL_ONLY_CHOICES
    # (rival/supplier — two chart fields that can only ever be a MANUAL tag,
    # never a case's own marketing_label). See the boundary spelled out on
    # MarketingLabel itself in cases/constants.py: Case.marketing_label
    # stays on CHOICES alone, deliberately, so this field is the only place
    # the extra two are valid.
    label = models.CharField(max_length=32, choices=MarketingLabel.CHOICES + MarketingLabel.MANUAL_ONLY_CHOICES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label", "client__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "label", "created_by"],
                name="marketing_clientlabel_unique_per_owner",
            ),
        ]

    def __str__(self):
        return f"{self.client.name} — {self.get_label_display()}"
