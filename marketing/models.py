"""The Marketing company directory: shared clients, and Marketing's own tags on them.

WHAT THIS IS NOW. There is no more independent, Marketing-only directory of
named values. The company directory Marketing browses is the SAME table
Commercial uses to create cases — ``cases.models.Client`` — reused directly,
not mirrored into a parallel model. Every company on the chart is a real,
already-registered ``Client`` row, or becomes one the moment Marketing
registers it (see ``marketing/services.py::get_or_create_client``, which
creates a ``Client`` exactly the way ``cases/views.py::client_add`` already
does).

A client can carry any number of the twenty business-role tags in
``cases.constants.MarketingLabel`` at once (a company can be both Sponsor and
Subcontractor, say) — the fourteen in ``MarketingLabel.CHOICES`` plus the six
in ``MarketingLabel.MANUAL_ONLY_CHOICES`` (rival, supplier, project, phase,
tpi and laboratory; see ``ClientLabel.label`` below for why those six are
manual-only). Those tags come from two independent sources that this app
merges only at read time (see ``marketing/services.py``, in particular
``companies_for_label`` / ``connections_of_client``):

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
    # Twenty choices, not fourteen: MarketingLabel.CHOICES (the fourteen
    # roles a CASE's client could hold) PLUS MarketingLabel.MANUAL_ONLY_CHOICES
    # (rival, supplier, project, phase, tpi and laboratory — six chart fields
    # that can only ever be a MANUAL tag, never a case's own
    # marketing_label). See the boundary spelled out on MarketingLabel itself
    # in cases/constants.py: Case.marketing_label stays on CHOICES alone,
    # deliberately, so this field is the only place the extra six are valid.
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


class ContactRole(models.Model):
    """One job title a company contact can hold — e.g. "مدیر فروش" (sales manager).

    A TINY, ADMIN/SUPERVISOR-MANAGED LOOKUP TABLE, not a hardcoded constant
    class, and that is the entire reason it is a model at all. Every other
    fixed vocabulary in this codebase (``cases.constants.MarketingLabel``,
    ``CaseStatus``, ``EventAction``, ...) is a Python constant class precisely
    because its members are structural — the workflow itself would have to
    change for a new one to appear. Job titles are not like that: the owner
    said explicitly that a Marketing Supervisor or the platform admin must be
    able to ADD a new option (a company introduces a "Head of QC", a
    "Procurement Director") without a developer, a code change, a migration
    and a deploy. A constant class cannot do that; a table can.

    ``name`` is globally unique — this is one shared vocabulary, not a
    per-user one. That is a deliberate departure from ``ClientLabel``'s
    per-owner uniqueness above: a manual TAG is one Expert's own opinion
    about a company, so two Experts tagging the same company are two
    independent facts, but a job TITLE is a shared word, and letting two
    people each create their own "Sales Manager" row would fragment the
    dropdown every contact form reads from and silently split
    ``CompanyContact`` rows across duplicate titles that read identically.

    ``created_by`` is recorded (who introduced this title, for the same
    audit reasons every other model here records it) but — unlike
    ``ClientLabel`` and ``CompanyContact`` — it deliberately does NOT scope
    visibility: the list is short, shared, and useless if an Expert cannot
    see the title a Supervisor just added. ``SET_NULL`` so removing a user
    never removes a title the whole unit depends on.
    """

    name = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ContactGender:
    """The gender options a :class:`CompanyContact` may carry.

    A plain constant class, not a lookup table like :class:`ContactRole`
    above, and the contrast between the two is the point: job titles grow
    (see that class's docstring for why the owner needs to add them without a
    deploy), this list does not. It is deliberately SHORT — the owner asked
    for the field, not for a taxonomy — and exists mainly so the UI can
    address a contact correctly ("آقای ..." / "خانم ...") in the messages and
    call lists built on top of this model.

    Stored value is the English key; the display text carries the Persian the
    UI actually shows, in the same "<Persian> — <ENGLISH>" shape
    ``cases.constants.MarketingLabel.CHOICES`` uses, so a template can render
    ``get_gender_display()`` directly without a second lookup table.
    """

    MALE = "MALE"
    FEMALE = "FEMALE"

    CHOICES = [
        (MALE, "آقای — MALE"),
        (FEMALE, "خانم — FEMALE"),
    ]
    LABELS = dict(CHOICES)


class CompanyContact(models.Model):
    """One named person at a company — who to actually call there.

    The company directory (``cases.Client``) answers "which companies do we
    know"; this answers "and who do we speak to at each of them". It hangs
    off the SAME shared ``Client`` table everything else in this app hangs
    off — see the module docstring for why there is no Marketing-only mirror
    of that directory — via ``related_name="marketing_contacts"``, the same
    ``marketing_*`` prefix ``ClientLabel``/``Connection`` already use so a
    reader of ``cases.models.Client`` can tell at a glance which reverse
    accessors belong to this app.

    SCOPING. ``created_by`` is who added this contact, and unlike every other
    model in this file it is the ONLY thing that decides who can see the row:
    the owner's rule is that a Marketing user sees only the contacts THEY
    added, while a Marketing Supervisor, the GM and the platform admin see
    every contact. See ``marketing/services.py::list_contacts`` for the
    implementation and for why that rule, despite sounding different, is
    exactly what the existing ``_scoped`` helper already expresses.

    NO DATABASE-LEVEL "AT LEAST ONE OF phone/email" CONSTRAINT — DELIBERATE,
    NOT AN OVERSIGHT. The business rule ("a contact with no way to contact
    them is useless") is real and IS enforced, but at the FORM layer, where a
    violation can be reported against the specific field the user left blank
    and where the user can fix it in place. A ``CheckConstraint`` here would
    surface the same rule as an ``IntegrityError`` from the database with no
    field to attach it to, and would additionally make every future data
    import, fixture and backfill fail loudly on a legitimately partial
    historical record. Three of the four fields below are ``blank=True`` for
    the same reason: which of prefix/extension/phone/email a given contact
    actually has varies per person, and only the combination is required, not
    any one of them.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="marketing_contacts")
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120)
    # SET_NULL, not CASCADE: retiring a job title from the shared
    # ContactRole vocabulary must never delete the PEOPLE who held it — the
    # contact is still a real person we still call, just with no title on
    # file any more.
    role = models.ForeignKey(
        ContactRole, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="contacts",
    )
    # Required by the owner, and required AT THE FORM LAYER — the field is
    # not ``blank=True``, so a ModelForm renders it required and Django's own
    # validation enforces it, but nothing here forces a value onto a row
    # written directly (a data import, a shell fix-up, a future backfill),
    # for the same reason spelled out in the class docstring above.
    gender = models.CharField(max_length=10, choices=ContactGender.CHOICES)
    # The three phone parts are kept SEPARATE rather than concatenated into
    # one string: Iranian office numbers are dialled as (area/dialling code,
    # number, internal extension), and a call list that has to split a joined
    # string back apart to dial an extension has lost information the person
    # entering it already had.
    phone_prefix = models.CharField(max_length=16, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    phone_ext = models.CharField(max_length=16, blank=True)
    email = models.EmailField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return f"{self.full_name} — {self.client.name}"


class ClientEventAction:
    """Every action a :class:`ClientEvent` can record.

    Lives HERE rather than in ``cases/constants.py`` on purpose. That module
    describes itself, in its own first line, as "Enumerations for the case
    lifecycle", and every member of it (``DocKind``, ``CaseStatus``,
    ``EventAction``, ...) is consumed by the cases app. None of these actions
    is: they are things that happen to a COMPANY inside Marketing's own
    directory, and the cases app neither writes nor reads them. Putting them
    next to the model that stores them keeps this app's history vocabulary
    where its history model is. (``MarketingLabel`` is the one Marketing
    concept that does live in ``cases/constants.py``, and for a reason that
    does not apply here: ``cases.models.Case.marketing_label`` is a field ON
    a case, so the cases app genuinely needs it.)

    STORABLE VS DERIVED — the same discipline ``MarketingLabel`` uses for
    ``CHOICES`` vs ``MANUAL_ONLY_CHOICES``, applied to a different boundary.
    ``CHOICES`` is exactly the set of actions that can ever be WRITTEN as a
    ``ClientEvent`` row, and it is what the model's ``choices=`` uses.
    ``DERIVED_CHOICES`` holds actions that are never stored at all: they are
    synthesised at read time by ``marketing/services.py::client_timeline``
    from case data the cases app owns. Keeping them out of ``CHOICES`` means
    the model can never accidentally accept one, while ``ALL_LABELS`` still
    gives the template one lookup that covers both kinds of entry.
    """

    CLIENT_REGISTERED = "CLIENT_REGISTERED"
    LABEL_ADDED = "LABEL_ADDED"
    LABEL_REMOVED = "LABEL_REMOVED"
    CONNECTION_ADDED = "CONNECTION_ADDED"
    CONNECTION_REMOVED = "CONNECTION_REMOVED"
    CONTACT_ADDED = "CONTACT_ADDED"
    CONTACT_REMOVED = "CONTACT_REMOVED"

    CHOICES = [
        (CLIENT_REGISTERED, "Company registered"),
        (LABEL_ADDED, "Label added"),
        (LABEL_REMOVED, "Label removed"),
        (CONNECTION_ADDED, "Connection added"),
        (CONNECTION_REMOVED, "Connection removed"),
        (CONTACT_ADDED, "Contact added"),
        (CONTACT_REMOVED, "Contact removed"),
    ]
    LABELS = dict(CHOICES)

    # Never stored — see the class docstring. Derived live from
    # ``cases.models.Case`` by ``client_timeline``.
    CASE_CREATED = "CASE_CREATED"

    DERIVED_CHOICES = [
        (CASE_CREATED, "Case created"),
    ]
    DERIVED_LABELS = dict(DERIVED_CHOICES)

    # Storable + derived together — the one lookup a timeline template needs,
    # since a rendered timeline row can be either kind and the template is
    # deliberately not supposed to know which.
    ALL_LABELS = {**LABELS, **DERIVED_LABELS}


class ClientEvent(models.Model):
    """Immutable timeline entry for one company: what happened to it, and when.

    Modelled DELIBERATELY on ``cases.models.CaseEvent`` — same shape, same
    discipline, same reason. A company's history is an audit trail, so a row
    here is written once and never updated: nothing in
    ``marketing/services.py`` edits or deletes one, and "undoing" an action
    (removing a label that was added) writes a SECOND row recording the
    removal rather than deleting the first. A timeline that can be edited
    afterwards is not a timeline.

    FROZEN ACTOR SNAPSHOTS — the whole point, copied verbatim in spirit from
    ``CaseEvent``. ``actor`` is a live FK and is allowed to go NULL when a
    user is deleted; ``actor_name`` and ``actor_role_label`` are the actor's
    display name and unit/role label EXACTLY as they were at the instant the
    event was written, captured once and never re-derived. The timeline must
    always read these two CharFields — never ``ev.actor.get_full_name()`` or
    ``ev.actor.profile.title_line`` live — so that a later rename, a
    promotion, a transfer between units, or the person leaving the company
    outright can never silently rewrite what already happened. An Expert who
    tagged a company in Farvardin and was promoted to Supervisor in Mordad
    must still appear as the Expert they were on that Farvardin row. See
    ``marketing/services.py::log_client_event``, which populates both by
    reusing ``cases.services._actor_snapshot`` — the same function
    ``cases.services.log`` uses, not a second copy of the same logic.

    ``subject`` is free text naming WHAT the action was about — the label's
    Persian display name for a LABEL_ADDED row, the other company's name for
    a CONNECTION_ADDED one, the person's name for a CONTACT_ADDED one. It is
    a frozen snapshot too, and for the same reason: storing an FK to the
    other company would let renaming that company rewrite this row's text,
    and storing an FK to a ``CompanyContact`` would make deleting the contact
    erase the record that it ever existed. A plain string cannot be rewritten
    by anything that happens later.

    NOT A MIRROR OF CASE HISTORY. This model records only what MARKETING did
    to a company inside its own directory. The other half of a company's
    story — its cases — is NOT copied into rows here; it is derived at read
    time straight from ``cases.models.Case`` and merged into the same list.
    See ``marketing/services.py::client_timeline`` for that merge and for why
    it is deliberately a read-time derivation rather than the cases app being
    taught to write rows into this table.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="marketing_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="marketing_client_events",
    )
    # Storable actions only — ClientEventAction.CASE_CREATED is deliberately
    # NOT among these; see that class's docstring.
    action = models.CharField(max_length=32, choices=ClientEventAction.CHOICES)
    subject = models.CharField(max_length=200, blank=True)
    comment = models.TextField(blank=True)
    # The two frozen snapshots — see the class docstring. Same widths as
    # CaseEvent's so the two timelines cannot truncate the same name
    # differently.
    actor_name = models.CharField(max_length=160, blank=True)
    actor_role_label = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client_id} · {self.action}"

    @property
    def action_label(self) -> str:
        """The human label for this row's action.

        Mirrors ``CaseEvent.action_label``, and falls back to the raw stored
        value rather than raising — a row written before an action was
        renamed must still render.
        """
        return ClientEventAction.ALL_LABELS.get(self.action, self.action)

    @property
    def actor_display_name(self) -> str:
        """The name to show — frozen first, live only as a last resort.

        Exactly ``CaseEvent.actor_display_name``'s rule. The live fallback
        exists only for a row somehow written outside ``log_client_event``;
        every row that function writes has ``actor_name`` populated (or an
        honest empty string, when the actor was anonymous), so in practice
        this always returns the frozen value.
        """
        if self.actor_name:
            return self.actor_name
        if self.actor_id and self.actor:
            return self.actor.get_full_name() or self.actor.username
        return ""
