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
    historical record. ``email`` is ``blank=True`` for the same reason: which
    of phone-numbers/email a given contact actually has varies per person, and
    only the combination — at least one phone row, or an email, or both — is
    required, not any one of them. Applied now to "at least one
    :class:`ContactPhone` row exists" rather than to a single field, since the
    phone number itself moved off this model onto that one — see
    ``marketing/forms.py::ContactForm.clean`` for where the rule actually
    lives, unchanged in spirit.

    THE PHONE NUMBER(S) LIVE ON :class:`ContactPhone`, NOT HERE — A DELIBERATE
    SPLIT, NOT THE ORIGINAL SHAPE. This model used to carry its own
    ``phone_prefix``/``phone``/``phone_ext`` triple directly, good for exactly
    one phone number per person. The owner asked for a contact to be reachable
    on several numbers — a desk line and a mobile, say — which a flat triple of
    columns cannot express without inventing ``phone_2``/``phone_3`` and a hard
    ceiling nobody asked for. A child table has no such ceiling: one row per
    number, as many rows as the person actually has. See
    :class:`ContactPhone`'s own docstring for the shape of that row (it is the
    exact three-part shape this model's fields used to be, moved rather than
    redesigned) and this file's git history for the migration that carried
    every existing single phone number across into one ``ContactPhone`` row
    apiece before the old columns were dropped, so that move lost no data on
    the way.

    NO DENORMALISED "PRIMARY PHONE" CONVENIENCE FIELD KEPT HERE ALONGSIDE THE
    CHILD TABLE — CONSIDERED AND DELIBERATELY REJECTED. It would save a
    ``select_related``/prefetch on the handful of screens that only ever want
    "the one number to call", at the cost of a second place a phone number
    can live and a second thing every future write path has to keep in sync
    with the child rows (add a number here and forget the denormalised copy,
    or vice versa, and the two silently disagree about which number is
    "primary"). Nothing measured about this table's size or these screens'
    read pattern argues for that trade — ``list_contacts`` already
    ``select_related``s in one extra query the same way it does for ``role``,
    and a ``prefetch_related("phones")`` costs the same one extra query per
    list rather than per row. Simpler wins here.
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


class ContactPhone(models.Model):
    """One phone number belonging to a :class:`CompanyContact`.

    WHY THIS TABLE EXISTS AT ALL — see ``CompanyContact``'s own docstring,
    under "THE PHONE NUMBER(S) LIVE ON ContactPhone, NOT HERE", for the full
    reasoning. In one line: a person can have more than one number, and a
    fixed set of columns on ``CompanyContact`` cannot express "as many as this
    person actually has" without inventing a ceiling nobody asked for.

    THE SAME THREE-PART SHAPE THE OLD SINGLE FIELD USED, MOVED VERBATIM, NOT
    REDESIGNED. ``phone_prefix``/``phone``/``phone_ext`` are kept SEPARATE
    rather than concatenated into one string for the exact reason
    ``CompanyContact`` used to give for its own three columns: Iranian office
    numbers are dialled as (area/dialling code, number, internal extension),
    and a call list that has to split a joined string back apart to dial an
    extension has lost information the person entering it already had. Moving
    house to a child table is not an excuse to lose that distinction — every
    row here still carries all three parts, independently, exactly as the
    single field on the parent used to.

    ONE ROW PER PHONE NUMBER, ORDERED BY CREATION (``created_at``, then
    ``pk`` as the tie-breaker for two rows written in the same transaction,
    the same tie-break ``Reminder.Meta.ordering`` uses for its own
    same-instant rows). NOT ordered by number or by which part is filled in —
    the order a person typed their numbers in (desk line first, then mobile,
    say) is itself information, and re-sorting it alphabetically or numerically
    would throw that away for no reason any screen has asked for.

    ``on_delete=CASCADE`` — a phone number has no meaning apart from the
    contact who answers it; deleting the contact (``services.remove_contact``)
    must remove every number filed under them, the same way removing a
    ``Client`` cascades to remove every ``CompanyContact`` at it.

    NO "AT LEAST ONE PART FILLED IN" CONSTRAINT HERE, for
    ``CompanyContact``'s own reason: the three parts are ``blank=True`` at the
    database layer so a legitimately partial row (an import, a backfill) is
    never rejected outright, and the screen-level rule — a row only counts as
    "a real number" once ``phone`` itself is non-blank — lives in
    ``marketing/forms.py::ContactForm``, exactly where ``CompanyContact``'s own
    "at least one of phone/email" rule always lived, for the identical reason:
    a violation can be reported against the field the user actually left
    blank, and the database is not made to enforce a rule a future import
    should not have to satisfy.

    NO ``related_name`` PREFIXED ``marketing_`` — deliberately unlike the
    reverse accessors ``CompanyContact``/``ClientLabel``/``Connection`` put on
    ``cases.Client``. That prefix exists so a reader of a FOREIGN app's model
    (``cases.models.Client``) can tell at a glance which of its many reverse
    accessors belong to this app. ``CompanyContact`` is not a foreign model —
    it lives in this same file — so its own reverse accessor for its own
    child rows needs no such disambiguation, exactly as ``ContactRole.contacts``
    (also a plain, unprefixed name) needs none for the same reason.
    """

    contact = models.ForeignKey(CompanyContact, on_delete=models.CASCADE, related_name="phones")
    phone_prefix = models.CharField(max_length=16, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    phone_ext = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        bits = [self.phone_prefix, self.phone]
        text = " ".join(b for b in bits if b)
        if self.phone_ext:
            text = f"{text} ext.{self.phone_ext}"
        return text or f"phone #{self.pk}"


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

    THERE IS NO "DERIVED" HALF ANY MORE. This class used to carry a second
    vocabulary — ``CASE_CREATED`` / ``DERIVED_CHOICES`` / ``DERIVED_LABELS``,
    and an ``ALL_LABELS`` that merged the two — for one action that was never
    stored: a "Case NNNN was opened for this company" row that
    ``marketing/services.py::client_timeline`` synthesised live from
    ``cases.models.Case``. The owner removed that entry from the timeline
    outright ("it is not needed to record that someone opened a case"), and the
    machinery went with it rather than being left behind as a vocabulary with
    no members. ``CHOICES`` is therefore now simply the set of actions a
    ``ClientEvent`` row can hold, and ``LABELS`` the one lookup a timeline
    template needs.

    A DERIVED ENTRY IS STILL POSSIBLE, and one still exists — the REGISTRATION
    row ``marketing/services.py::_registration_entry`` synthesises from
    ``cases.models.Client.created_by``. It needs no vocabulary of its own
    because it reuses ``CLIENT_REGISTERED``, which is a genuinely storable
    action (the chart's own "+ Add company" flow writes it as a real row). That
    is exactly why the split above stopped earning its keep: it existed for one
    action, and that action is gone.

    ``CASE_ROLE_CHANGED`` IS DIFFERENT FROM THE DERIVED HALF ABOVE — a real,
    storable action, not a synthesised one, even though what it records lives
    on ``cases.models.Case``. The distinction the class docstring draws
    elsewhere in this file is about a case's mere EXISTENCE (an "a case was
    opened" row, which the owner had removed and which stays gone), not about
    an EDIT someone made to one of its fields. Changing
    ``cases.models.Case.marketing_label`` — which (client, role) pair a case
    anchors to on the chart — is a real business decision a Marketing user
    needs to see on the COMPANY'S OWN timeline, the same way any other
    ``ClientEvent`` is: who did it, what changed, and when. See
    ``marketing/services.py::log_case_role_change``, the one writer for this
    action, and ``cases/views.py``'s two case-edit paths, its only callers.
    """

    CLIENT_REGISTERED = "CLIENT_REGISTERED"
    LABEL_ADDED = "LABEL_ADDED"
    LABEL_REMOVED = "LABEL_REMOVED"
    CONNECTION_ADDED = "CONNECTION_ADDED"
    CONNECTION_REMOVED = "CONNECTION_REMOVED"
    CONTACT_ADDED = "CONTACT_ADDED"
    CONTACT_REMOVED = "CONTACT_REMOVED"
    REPORT_ADDED = "REPORT_ADDED"
    CASE_ROLE_CHANGED = "CASE_ROLE_CHANGED"

    CHOICES = [
        (CLIENT_REGISTERED, "Company registered"),
        (LABEL_ADDED, "Label added"),
        (LABEL_REMOVED, "Label removed"),
        (CONNECTION_ADDED, "Connection added"),
        (CONNECTION_REMOVED, "Connection removed"),
        (CONTACT_ADDED, "Contact added"),
        (CONTACT_REMOVED, "Contact removed"),
        (REPORT_ADDED, "Report added"),
        (CASE_ROLE_CHANGED, "Case role changed"),
    ]
    LABELS = dict(CHOICES)


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

    ``subject_role`` is a SECOND frozen slot beside it, and it exists because
    one action genuinely needs two nouns. A CONNECTION_ADDED row is about
    another company AND about the ROLE that company was connected AS ("connected
    to Ofogh Novin Homa **as Supervision Consultant**"), and the owner asked for
    that role to be rendered as its own tag rather than buried in the
    ``comment`` line, where it used to travel as half of an "anchor → target"
    string. Splitting it out is what lets the template print a chip instead of a
    sentence fragment.

    IT IS FROZEN TEXT, NOT A ROLE KEY TO LOOK UP AT RENDER TIME, and that is
    deliberate rather than convenient: every other value on this model is a
    snapshot taken at write time precisely so nothing that happens afterwards
    can rewrite history, and a role's Persian display name is exactly the kind
    of thing that gets reworded (``cases.constants.MarketingLabel``'s texts have
    changed more than once). Resolving it live would let a rename silently
    rewrite what a two-year-old row says happened. ``blank=True`` with an empty
    default so every row written before this field existed — and every action
    that has no role to name — still renders unchanged.

    NOT A MIRROR OF CASE HISTORY. This model records only what MARKETING did
    to a company inside its own directory, and — since the owner removed the
    derived "a case was opened" entry — the company's CASES are no longer part
    of its timeline at all, neither as rows here nor as a read-time merge. The
    Cases tab of the company detail page is where a company's cases are listed,
    under its own scoping (``marketing/views.py::_visible_case_rows``); this
    model and ``marketing/services.py::client_timeline`` no longer say anything
    about them.
    """

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="marketing_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="marketing_client_events",
    )
    action = models.CharField(max_length=32, choices=ClientEventAction.CHOICES)
    subject = models.CharField(max_length=200, blank=True)
    # The role the ``subject`` was connected AS — see the class docstring.
    # Same width as ``actor_role_label`` above, since both hold a role's
    # display text and there is no reason for the two to truncate differently.
    subject_role = models.CharField(max_length=160, blank=True)
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
        return ClientEventAction.LABELS.get(self.action, self.action)

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


class ReportOption(models.Model):
    """One preset thing a marketing report can say — e.g. "بازدید حضوری".

    THE SAME KIND OF OBJECT AS :class:`ContactRole`, and deliberately built the
    same way: a tiny, managed lookup table rather than a constant class in
    ``cases/constants.py``. Read that class's docstring for the full reasoning;
    it applies here word for word. The short version is that the members of this
    list are not structural — the workflow does not change when Marketing starts
    recording a new kind of visit — so the owner has to be able to add one
    without a developer, a migration and a deploy, and only a table can do that.

    WHO MAY EXTEND IT is exactly who may extend ``ContactRole``:
    ``marketing/access.py::Access.can_manage_config`` — a Marketing Supervisor
    and the platform admin, and NOT the General Manager. That flag exists
    precisely so a configuration vocabulary can be administered by people who
    hold no write at all on Marketing's working data (see its own comment on
    ``Access``), and this is the second vocabulary it governs.

    TWO ROUTES INTO EXISTENCE, exactly the two ``ContactRole`` has, and for a
    concrete reason: ``marketing/admin.py`` registers this model the way it
    registers that one, but the Django admin requires ``is_staff`` and a
    MARKETING SUPERVISOR IS NOT STAFF — so an admin-only list would be
    administrable by only one of the two seats ``can_manage_config`` names,
    while the report screen told the other one to go there. The second route is
    the inline "…or add a new option" field on
    ``marketing/forms.py::ReportForm``, the same field
    ``marketing/forms.py::ContactForm`` has always offered for a new job title,
    gated the same way (the field does not EXIST for a viewer without the
    grant) and written in the same place (the view get-or-creates; see
    ``marketing/views.py::report_create``).

    ``name`` is globally unique for ``ContactRole``'s reason: this is ONE shared
    vocabulary, and two people each creating their own "site visit" row would
    fragment every checklist that reads from it and silently split
    :class:`CompanyReport` rows across duplicate options that read identically.

    ``created_by`` is recorded for audit but, again like ``ContactRole``,
    deliberately does NOT scope visibility — a list of options only an Expert's
    own supervisor can see is not a shared vocabulary. ``SET_NULL`` so removing
    a user never removes an option the whole unit depends on.
    """

    name = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CompanyReport(models.Model):
    """One marketing person's written report about one company.

    The owner's description, in full: "a marketing person opens a company, adds
    a report, and it is recorded against that company with who wrote it, the
    text, the date and time, and optionally which case it was about. Each
    marketing user sees only the reports THEY wrote; the manager and the admin
    see all of them."

    Every clause of that maps onto a column below, and none of them invents a
    rule this app did not already have:

    * WHO WROTE IT — ``created_by``, named that and not ``author`` on purpose.
      It is what ``marketing/services.py::_scoped`` filters on, and reusing that
      helper is what makes the visibility rule above be THE SAME RULE
      :class:`CompanyContact` already follows rather than a second one that can
      drift (see ``services.list_reports``). ``marketing/access.py::access_for``
      already resolves an ordinary Marketing seat to scope ``"own"`` and a
      Supervisor / the GM / the admin to ``"all"``, which is precisely the two
      populations the owner named.
    * THE TEXT — ``text``, free-form and ``blank=True``; and
    * THE PRESET OPTIONS — ``options``, a many-to-many onto
      :class:`ReportOption`, also allowed to be empty.

      NEITHER IS REQUIRED AT THIS LAYER, AND THE COMBINATION IS. "A report that
      says nothing is not a report" is a real rule and it IS enforced — in
      ``marketing/forms.py::ReportForm.clean()``, server-side, exactly where
      ``CompanyContact``'s own "at least one of phone/email" rule lives and for
      the identical reason (see that class's docstring): a violation can be
      reported against the fields the person actually left blank, instead of
      arriving as an ``IntegrityError`` with nothing to attach it to, and a
      future import or backfill of legitimately partial historical records is
      not made impossible by a database constraint.
    * THE DATE AND TIME — ``created_at``, indexed because reports are read
      newest-first and nothing else orders them.
    * WHICH CASE IT WAS ABOUT — ``case``, OPTIONAL by the owner's own wording
      ("optionally which case"), which is why the flow that creates one offers
      an explicit Skip. ``SET_NULL`` rather than ``CASCADE``, for
      :class:`Connection`'s reason: the report was still written and still says
      what it says, even if the case that prompted it is later removed.

    ``author_name`` FREEZES THE WRITER'S DISPLAY NAME at write time, exactly
    as :class:`ClientEvent` freezes ``actor_name`` and for exactly that
    docstring's reason — a later rename, a promotion, a transfer, or the person
    leaving must never silently rewrite who a report says wrote it. It is
    populated by ``marketing/services.py::add_report`` through the same
    ``cases.services._actor_snapshot`` helper the timeline uses, so a name here
    and a name on the timeline row recording the same act cannot disagree.
    Only the NAME is frozen, not the role label: the owner asked for the
    timeline to stop showing role labels beside names, and there is no screen
    that wants one here.
    """

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="marketing_reports")
    # See the class docstring: optional by the owner's rule, SET_NULL so a
    # removed case never removes the report that was written about it.
    case = models.ForeignKey(
        "cases.Case", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="marketing_reports",
    )
    text = models.TextField(blank=True)
    options = models.ManyToManyField(
        ReportOption, blank=True, related_name="reports")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    # The frozen author snapshot — see the class docstring.
    author_name = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client_id} · report by {self.author_name or self.created_by_id}"

    @property
    def author_display_name(self) -> str:
        """The name to show — frozen first, live only as a last resort.

        ``ClientEvent.actor_display_name``'s rule, applied to the same kind of
        frozen column. The live fallback covers only a row written outside
        ``services.add_report``; every row that function writes carries a
        populated ``author_name`` (or an honest empty string for an anonymous
        writer, which no real flow produces).
        """
        if self.author_name:
            return self.author_name
        if self.created_by_id and self.created_by:
            return self.created_by.get_full_name() or self.created_by.username
        return ""


class ReminderState:
    """The two states a :class:`Reminder` is actually STORED in.

    THREE STATES ARE VISIBLE TO THE PERSON, AND ONLY TWO ARE STORED. The owner
    asked to be able to tell "still waiting", "due and not yet dealt with" and
    "dealt with" apart, and the first two are the SAME stored state seen from
    two sides of one moment: an open reminder whose ``due_at`` is still in the
    future is waiting, and the same row is due the instant the clock passes it.
    Storing a third value would mean something has to WRITE it when that moment
    arrives — a scheduler, a cron job, or a save on every page load — which is
    exactly the background machinery the owner ruled out ("this must not make
    the site slow or heavy"). Comparing a timestamp costs nothing and can never
    be stale, so "due" is derived and never stored. See
    ``marketing/reminders.py::due_notification``, which is that comparison.

    A ``CharField`` rather than a boolean, deliberately: the owner said they
    will specify more detail later, and a two-value character column can grow a
    third state (a dismissal, a snooze that is not a reschedule) with one
    migration and no rewrite of every ``is_done`` test in the codebase. It also
    reads correctly in the composite index this model carries — see
    :class:`Reminder`.
    """

    OPEN = "OPEN"
    DONE = "DONE"

    CHOICES = [
        (OPEN, "Open"),
        (DONE, "Dealt with"),
    ]
    LABELS = dict(CHOICES)


class Reminder(models.Model):
    """One person's private note to themselves about a company, at a time.

    The owner's description, in full: "a marketing person sets a reminder for
    themselves against a company — and optionally against one specific case of
    that company — by picking a date and time and writing a note. Each person
    sees only their OWN reminders. When one falls due a notification appears at
    the top of their screen; it stays until they act on it, and it takes them to
    their reminders list where they can set a new time." The working rhythm
    behind it is a loop: a case gets a reminder, the person is reminded, they
    write a report, they set the next reminder, until the case ends.

    OWNED BY ``owner``, WITH SEEING AND ACTING ON A ROW NOW DECIDED
    DIFFERENTLY — a CHANGE FROM AN EARLIER VERSION OF THIS DOCSTRING, STATED
    PLAINLY RATHER THAN QUIETLY OVERWRITTEN. That earlier version argued at
    length that a reminder was PRIVATE, full stop: unlike :class:`CompanyContact`
    and :class:`CompanyReport` (scoped by ``created_by`` through
    ``marketing/services.py::_scoped``, so an ordinary seat sees its own rows
    and a Supervisor, the General Manager and the platform admin see
    everyone's), Reminders deliberately did NOT reuse that helper and were not
    offered to those three at all — the owner's own wording at the time was
    "هر شخص لیست یادآور خود را می‌تواند ببیند" (each person can see THEIR OWN
    reminder list), and "supervision over a private to-do list is a different
    decision from supervision over someone's output" was the argument given
    for why that was not asked for.

    THE OWNER HAS NOW EXPLICITLY ASKED FOR EXACTLY THAT, IN A LATER ROUND: "make
    Reminders work exactly like Reports already do" — an ordinary Marketing
    Expert sees only their own, a Marketing Supervisor sees the union of
    everyone's, and (for consistency with every other elevated-access surface
    in this app) the platform admin and the General Manager do too. This is
    named here as a reversal, not silently folded into the paragraph above,
    because a future reader comparing this file's git history to its own
    docstring deserves to see that the rule genuinely changed and on whose
    instruction, not to be left wondering whether an earlier claim was simply
    wrong.

    WHAT ACTUALLY WIDENED, AND WHAT DID NOT — the distinction matters and is
    easy to blur:

    * VIEWING widened. ``marketing/reminders.py::list_for_user`` (the
      standalone "My reminders" page, and the new company-page Reminders tab)
      now takes a ``scope`` argument, the same "own"/"all" shape
      ``services._scoped`` already uses, resolved by the view from
      ``marketing/access.py::access_for`` exactly like every other scoped read
      in this app.
    * CREATING AND ACTING ON A ROW DID NOT. Setting a reminder
      (``reminders.create``) is still something only the person themselves
      does, about themselves. Giving one a new time or marking it dealt with
      (``reminders.reschedule`` / ``reminders.mark_done``, both going through
      ``reminders._own``) is STILL strictly owner-only, with no elevated
      escape hatch — unlike ``toggle_manual_label``'s or ``remove_connection``'s
      ``elevated=True`` branch for a Supervisor, there is no such branch here,
      because the owner asked only to let a Supervisor SEE the unit's
      reminders, not to let them re-time or close out somebody else's private
      note. A Supervisor who sees a colleague's overdue reminder acts on it the
      way the model has always intended a THIRD party to act on someone else's
      note: by talking to them, not by editing their row.
    * THE TOP-OF-PAGE DUE NOTIFICATION DID NOT WIDEN EITHER, deliberately, and
      that is a judgement call made THIS round, not a leftover of the old
      rule: see ``marketing/context_processors.py::reminder_notice`` and
      ``marketing/reminders.py::due_notification`` for the reasoning. In
      short, a banner that greets a Supervisor with the whole unit's overdue
      items on every page they open is a different, heavier feature than "you
      have something to deal with", and it is not what was asked for here.
    * This model is still deliberately absent from ``marketing/admin.py`` —
      unrelated to any of the above, since that is Django's own STAFF-gated
      admin site, a different audience from this app's Marketing
      Supervisor/GM/platform-admin tier, and this round changes nothing about
      who is ``is_staff``.

    ``owner`` CASCADES, unlike the ``created_by`` on every other model here,
    which is ``SET_NULL``. Those columns are audit: who added this contact, who
    wrote this report — the fact outlives the account, so the row must survive
    the user's deletion with a NULL actor. A reminder is the opposite kind of
    object: it has no meaning apart from the person it belongs to, and an
    ownerless private note is a row nobody can ever see, act on or delete.
    ``related_name="marketing_reminders"`` keeps this app's ``marketing_*``
    prefix on the reverse accessor, exactly as ``ClientLabel`` and
    ``CompanyContact`` do.

    ``case`` IS OPTIONAL, by the owner's own wording, and ``SET_NULL`` for
    :class:`CompanyReport`'s reason: the reminder is still a real note about
    the company even if the case that prompted it is later removed. WHICH cases
    may be attached is not this model's decision and is not enforceable here —
    it is ``marketing/access.py::case_access_for``'s, applied by the view over
    the same scoped rows the company page's Cases tab lists (see
    ``marketing/views.py::reminder_add``), because never offering a case the
    viewer cannot open is a rule this app has had to repair twice already.

    ``due_at`` IS INDEXED ONLY AS PART OF THE COMPOSITE BELOW, never alone. The
    one query that reads this table IN ANGER — meaning on every page load,
    cached or not — is still the due-notification check, and it is still
    always the same shape: this owner, still open, due at or before now
    (``marketing/reminders.py::due_notification`` deliberately keeps that
    query owner-scoped even now that OTHER reads of this table are not — see
    that function's own comment). ``(owner, state, due_at)`` is that query's
    exact column order — the two equalities first, the range last — so it is
    served by an index range scan over one person's open rows and never scans
    the table. A separate index on ``due_at`` alone would earn nothing: the
    one per-page-load query this index exists for is still always scoped to
    one owner, even though the (uncached, occasional) list pages built on
    :func:`~marketing.reminders.list_for_user` can now, for a Supervisor/GM/
    admin, read every owner's rows through this same index with no ``owner``
    equality supplied at all — a full scan of a small table on a page nobody
    loads every request, not the hot path this index was built to protect.

    ``Meta.ordering`` is soonest-first, which is the order the list page wants
    (what should I deal with next). The notification asks for the opposite —
    the most RECENTLY due row — and orders explicitly for it rather than
    relying on the default; see ``marketing/reminders.py``.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="marketing_reminders",
    )
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="marketing_reminders")
    case = models.ForeignKey(
        "cases.Case", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="marketing_reminders",
    )
    note = models.TextField()
    due_at = models.DateTimeField()
    state = models.CharField(
        max_length=8, choices=ReminderState.CHOICES,
        default=ReminderState.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_at", "pk"]
        indexes = [
            models.Index(
                fields=["owner", "state", "due_at"],
                name="marketing_reminder_due_idx",
            ),
        ]

    def __str__(self):
        return f"{self.client_id} · reminder for {self.owner_id} at {self.due_at:%Y-%m-%d %H:%M}"

    @property
    def is_done(self) -> bool:
        return self.state == ReminderState.DONE
