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

UPDATE — the link concept is back, deliberately, in a narrower shape. The
paragraph above is no longer fully true and is kept only so a future reader
understands what was being reacted against. The owner asked for it back,
confirmed directly: not ``EntityLink``'s arbitrary undirected edge between
any two named values in any two fields, but :class:`Connection` — a
SCOPED and DIRECTED edge from one specific (client, role) pair to another
specific (client, role) pair. The differences from the old, rejected shape
are exactly the differences that make this one legitimate:

* It is directed and role-specific, not an undirected link between two bare
  names — an edge always reads as "THIS client, playing THIS role, connects
  to THAT client, playing THAT role", never a vague "these two things are
  related somehow".
* It is created only through the chart's own Attach flow on an existing
  card (see ``marketing/services.py::create_connection``), not through a
  free-floating "add a link" screen with no anchor in the chart at all.
* It can optionally be tied to the specific ``cases.Case`` that justified it
  (see :class:`Connection`'s own docstring for the visibility rule that
  gives that field its meaning), keeping it consistent with this module's
  standing principle that a real business relationship traces back to an
  actual case wherever one exists — a general (case-less) connection is
  still allowed, but a case-scoped one is the stronger, preferred form.

So the true statement, going forward, is: the "any field, any two names,
arbitrary undirected link" shape is still gone and is not coming back:
:class:`Connection` never lets two bare names be linked outside the
client/role structure this module already enforces everywhere else.
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


class Connection(models.Model):
    """A directed, role-specific edge: FROM one (client, role) pair TO another.

    See this module's own docstring, under "UPDATE — the link concept is
    back, deliberately", for why this exists at all and how it differs in
    kind from the old, deliberately-removed ``EntityLink``. What follows here
    is the shape itself.

    ANCHOR / TARGET. ``anchor_client``/``anchor_role`` is the (client, role)
    pair the connection is drawn FROM — the card the chart's Attach flow was
    opened on. ``target_client``/``target_role`` is the (client, role) pair
    it points TO — the card the user picked to attach. The direction matters
    and is never symmetric: a row anchored at (Water & Sewage, owner) ->
    target (Ofogh Novin Homa, supervision) means "Ofogh Novin Homa, playing
    Supervision Consultant, is connected to Water & Sewage's Owner role" —
    it does NOT mean Water & Sewage itself picks up a Supervision tag, and it
    does not, on its own, create the reverse fact either (nothing here
    auto-inserts a mirror row anchored the other way). See
    ``marketing/services.py::connections_of_client`` for exactly how a row
    like this is read back out for display.

    HOW THIS DIFFERS FROM ``ClientLabel``. ``ClientLabel`` is one client
    holding one role itself — a single client/role pair, full stop.
    ``Connection`` is a relationship BETWEEN two clients, each in its own
    role — two clients and two roles, directed from one to the other. They
    answer different questions: "what roles does this client hold" (
    ``ClientLabel``, plus case-derived roles) versus "who is connected to
    this client, through which of its roles, playing which role themselves"
    (``Connection``). A client can appear as an anchor in one row and a
    target in another, and its own directly-held labels (manual or
    case-derived) are computed exactly as before, untouched by any of this.

    NO ``choices=`` ON ``anchor_role``/``target_role`` — DELIBERATE, and NOT
    an oversight to "fix" into matching ``ClientLabel.label`` above.
    ``ClientLabel.label`` can afford a ``choices=`` list because it is
    checked once, at the model layer. This field's valid values are the
    exact same labelable key set (``marketing/services.py::LABEL_KEYS``,
    itself derived from ``MarketingLabel.CHOICES`` +
    ``MarketingLabel.MANUAL_ONLY_CHOICES``) — but that set has grown in
    nearly every recent round of work on this app, and a ``choices=`` list
    here would need its own migration every single time it does. Validity is
    checked at the service layer instead, by reusing the same
    ``is_label()`` helper ``ClientLabel``'s effective choice set already
    goes through (see ``marketing/services.py::create_connection``) — one
    source of truth for "what counts as a role", not two lists that can
    silently drift apart.

    ``case`` — OPTIONAL, and its presence or absence changes WHO SEES this
    connection, not just what it documents. ``None`` (a "general" connection)
    is always visible, in every Inquiry, regardless of which case (if any) is
    the context for that particular Inquiry run. A specific ``Case`` scopes
    the connection to that case's own context: it is only surfaced when
    Inquiry is being run with that same case as context, and stays invisible
    otherwise — see ``marketing/services.py::connections_of_client``'s
    ``case`` parameter for the exact visibility rule this implements. Uses
    ``on_delete=SET_NULL`` (not ``CASCADE``): a case-scoped connection
    outliving its case is demoted to a general one rather than deleted — the
    business relationship the connection records did happen, even if the
    specific case that first evidenced it is later removed.

    UNIQUENESS is on the full five-tuple (anchor_client, anchor_role,
    target_client, target_role, case) — deliberately including ``case``, not
    stopping at the first four. A case-scoped connection and a general one
    (``case=None``) between the exact SAME two (client, role) pairs are
    allowed to coexist as two different facts: e.g. "these two are connected
    in general" and "these two are ALSO, specifically, connected because of
    case #41" are both true at once and neither should silently collapse the
    other. Two case-scoped rows for two DIFFERENT cases are likewise two
    distinct facts, each visible only in its own case's context.

    There is no per-owner scoping in this uniqueness constraint the way
    ``ClientLabel`` has (``created_by`` is not part of it): unlike a manual
    tag, where the same (client, label) added by two different Experts is
    treated as two independent facts until a Supervisor/GM looks at the
    union, a Connection is idempotent regardless of who creates it — see
    ``marketing/services.py::create_connection``, which get-or-creates rather
    than always inserting. ``created_by`` is still recorded, and still gates
    removal exactly the way it does on ``ClientLabel`` (see
    ``marketing/services.py::remove_connection``), it just is not part of
    what makes two rows "the same fact".
    """

    anchor_client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="connections_out")
    anchor_role = models.CharField(max_length=32)
    target_client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="connections_in")
    # See the class docstring's "NO choices=" section for why these two role
    # fields deliberately do not repeat ClientLabel.label's choices= list.
    target_role = models.CharField(max_length=32)
    case = models.ForeignKey(
        "cases.Case", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="marketing_connections",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["anchor_client__name", "anchor_role", "target_role"]
        constraints = [
            models.UniqueConstraint(
                fields=["anchor_client", "anchor_role", "target_client", "target_role", "case"],
                name="marketing_connection_unique_edge",
            ),
        ]

    def __str__(self):
        return f"{self.anchor_client.name} ({self.anchor_role}) -> {self.target_client.name} ({self.target_role})"
