"""Core data model for the case workflow.

Everything the platform does revolves around a ``Case`` (a "file"/پرونده). A case
carries up to three forms (Inquiry, TO, PI), each stored as structured data (not
Excel/PDF) so it can be searched, versioned and re-exported at any time.
"""
from django.conf import settings
from django.db import models

from accounts.constants import Unit

from .constants import CaseStatus, DocKind, EventAction, FormKind, OfferType, PriceType, Side


class SerialCounter(models.Model):
    """Generic monotonic counter (case serials, client codes, …)."""

    key = models.CharField(max_length=40, unique=True)
    value = models.BigIntegerField(default=0)

    def __str__(self):
        return f"{self.key}={self.value}"


class Client(models.Model):
    """A customer (kar-farma).

    A client name is unique so the same customer can never end up with two
    different codes. Codes are sequential and only change through a controlled
    Excel upload by the commercial manager; experts may rename only the clients
    assigned to them, and may search (never create) clients.
    """

    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=20, unique=True)
    assigned_experts = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="clients",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_clients",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class ExpertCode(models.Model):
    """Two-column expert code table (code, name).

    Maintained by the commercial manager (inline edit or Excel upload) and
    optionally linked to a user account. A case stamps the creator's expert
    code into its document number.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="expert_code",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Case(models.Model):
    """A workflow file moving between Commercial, Technical and Supply."""

    # Document-number parts (frozen once the case is created).
    doc_no = models.CharField(max_length=80, unique=True, db_index=True)
    # Folder/attachment reference the commercial user can use on their computer.
    attach_no = models.CharField(max_length=40, blank=True, db_index=True)
    kind = models.CharField(max_length=10, choices=DocKind.CHOICES, default=DocKind.INDENT)
    offer_type = models.CharField(max_length=10, choices=OfferType.CHOICES, default=OfferType.TO)
    year_month = models.CharField(max_length=6)
    expert_code = models.CharField(max_length=20)
    # Frozen at create / first TO: "Latin Name (internal_code)". Never re-derived
    # from the live seat User after release/reassign — same idea as CaseEvent.actor_name.
    commercial_expert_display = models.CharField(max_length=200, blank=True)
    technical_expert_display = models.CharField(max_length=200, blank=True)
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="cases")
    serial = models.PositiveIntegerField(db_index=True)
    version = models.PositiveIntegerField(default=0)

    order_no = models.CharField(max_length=80, blank=True)
    # Client-side contacts captured when the case is opened.
    client_commercial_expert = models.CharField(max_length=120, blank=True)
    client_commercial_phone = models.CharField(max_length=40, blank=True)
    client_technical_expert = models.CharField(max_length=120, blank=True)
    client_technical_phone = models.CharField(max_length=40, blank=True)
    # Internal / External / Both — drives the sub-streams in case detail.
    price_type = models.CharField(
        max_length=10, choices=PriceType.CHOICES, default=PriceType.INTERNAL)
    deadline = models.DateTimeField(
        null=True, blank=True,
        help_text="Required when the offer type is TO & PI.",
    )

    status = models.CharField(
        max_length=30, choices=CaseStatus.CHOICES, default=CaseStatus.DRAFT, db_index=True,
    )
    holder_unit = models.CharField(max_length=20, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_cases",
        help_text="Expert currently working the case inside Technical/Supply.",
    )
    # Once a manager assigns the case to an expert, that choice sticks: when the
    # case later re-enters the unit it goes straight to this expert (not back to
    # the manager's queue).
    technical_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="technical_cases",
    )
    supply_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="supply_cases",
    )
    # Per-side supply experts (Internal goes to internal experts, External to
    # external experts). Sticky like the other assignees.
    supply_internal_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="supply_internal_cases",
    )
    supply_external_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="supply_external_cases",
    )
    # True when the case became TO & PI by upgrading an original TO (two-stage).
    upgraded_two_stage = models.BooleanField(default=False)
    # True when the price type became BOTH by upgrading a single-side case
    # (Internal OR External) to "Internal & External two stage".
    price_upgraded_two_stage = models.BooleanField(default=False)

    # --- Per-side independent state (only for Internal & External cases once a
    #     supply side is delegated). When split_active is False the single
    #     status/holder_unit governs the whole case exactly as before. ---
    split_active = models.BooleanField(default=False)
    internal_status = models.CharField(max_length=30, blank=True)
    external_status = models.CharField(max_length=30, blank=True)
    internal_holder = models.CharField(max_length=20, blank=True)
    external_holder = models.CharField(max_length=20, blank=True)
    technical_internal_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tech_internal_cases")
    technical_external_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tech_external_cases")

    # When a Technical/Supply *expert* wants to send a form outward, it first
    # waits for that unit's manager to approve. Managers send directly.
    awaiting_approval = models.BooleanField(default=False)
    proposed_action = models.CharField(max_length=30, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_cases",
    )
    # Permanent tag when open tasks were moved to another same-role seat.
    is_delegated = models.BooleanField(default=False, db_index=True)
    delegated_from_seat = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="delegated_away_cases",
    )
    delegated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.doc_no

    @property
    def status_label(self) -> str:
        return CaseStatus.LABELS.get(self.status, self.status)

    @property
    def is_ended(self) -> bool:
        """True when no further action can be taken (closed / burned / cancelled).

        Open tasks for seat Delegate/Close are every case that is *not* ended —
        including files that sit in Archive while still actionable (e.g. With Supply).
        """
        if self.status in CaseStatus.ENDED:
            return True
        if self.split_active:
            sides = []
            for st in (self.internal_status, self.external_status):
                if st:
                    sides.append(st in CaseStatus.ENDED)
            if sides and all(sides):
                return True
        return False

    @property
    def kind_label(self) -> str:
        return self.get_kind_display()

    @property
    def offer_type_label(self) -> str:
        return self.get_offer_type_display()

    @property
    def offer_stage_label(self) -> str:
        """Three-way label: TO / TO & PI / TO & PI (Two Stage)."""
        if self.offer_type != OfferType.TO_PI:
            return "TO"
        return "TO & PI (Two Stage)" if self.upgraded_two_stage else "TO & PI"

    @property
    def price_type_label(self) -> str:
        return PriceType.LABELS.get(self.price_type, "")

    @property
    def has_internal(self) -> bool:
        return self.price_type in (PriceType.INTERNAL, PriceType.BOTH)

    @property
    def has_external(self) -> bool:
        return self.price_type in (PriceType.EXTERNAL, PriceType.BOTH)

    @property
    def sides(self) -> list:
        """Active sides for this case (used by the Internal/External UI)."""
        out = []
        if self.has_internal:
            out.append(Side.INTERNAL)
        if self.has_external:
            out.append(Side.EXTERNAL)
        return out

    @property
    def status_color(self) -> str:
        return CaseStatus.COLORS.get(self.status, "#6b7280")

    @property
    def needs_pricing(self) -> bool:
        return self.offer_type == OfferType.TO_PI

    def current_form(self, kind: str, side: str = None):
        """Return the current (latest) form snapshot of a given kind/side.

        ``side`` of ``None`` falls back to the case's primary side. Legacy forms
        created before the Internal/External split carry a blank side, so when
        the primary side has no match we also look up the blank-side form.

        When the caller has ``prefetch_related("forms")`` the lookup is served
        from that in-memory cache (no query) — this is what keeps the case-detail
        page, which resolves the current form dozens of times, to a couple of
        queries instead of dozens. Otherwise it falls back to a normal query, so
        every other caller behaves exactly as before.
        """
        if side is None:
            side = self.primary_side
        cached = getattr(self, "_prefetched_objects_cache", None)
        if cached is not None and "forms" in cached:
            # The related manager's default ordering is ["kind", "-version"], so
            # the first match in this list is the highest version — same as the
            # DB .first() below.
            current = [f for f in cached["forms"] if f.kind == kind and f.is_current]
            form = next((f for f in current if f.side == side), None)
            if form is None and side == self.primary_side:
                form = next((f for f in current if f.side == ""), None)
            return form
        qs = self.forms.filter(kind=kind, is_current=True)
        form = qs.filter(side=side).first()
        if form is None and side == self.primary_side:
            form = qs.filter(side="").first()
        return form

    @property
    def primary_side(self) -> str:
        """The default side used when a caller does not specify one."""
        if self.has_internal:
            return Side.INTERNAL
        if self.has_external:
            return Side.EXTERNAL
        return ""

    # ---- Per-side (split) state helpers -------------------------------
    @property
    def is_split(self) -> bool:
        """True when the two sides move independently (delegated supply)."""
        return bool(self.split_active and self.has_internal and self.has_external)

    def side_status(self, side: str) -> str:
        if side == Side.INTERNAL:
            return self.internal_status or self.status
        if side == Side.EXTERNAL:
            return self.external_status or self.status
        return self.status

    def side_holder(self, side: str) -> str:
        if side == Side.INTERNAL:
            return self.internal_holder or self.holder_unit
        if side == Side.EXTERNAL:
            return self.external_holder or self.holder_unit
        return self.holder_unit

    def set_side_state(self, side: str, status: str, holder: str):
        if side == Side.INTERNAL:
            self.internal_status, self.internal_holder = status, holder
        elif side == Side.EXTERNAL:
            self.external_status, self.external_holder = status, holder

    @property
    def side_status_rows(self) -> list:
        """For display: [(label, status_label, color, holder), …] per side."""
        rows = []
        for sc in self.sides:
            st = self.side_status(sc)
            rows.append((
                Side.LABELS.get(sc, sc),
                CaseStatus.LABELS.get(st, st),
                CaseStatus.COLORS.get(st, "#6b7280"),
                self.side_holder(sc),
            ))
        return rows

    @property
    def all_sides_with_commercial(self) -> bool:
        """True when every active side is back in Commercial's hands."""
        if not self.is_split:
            return self.holder_unit == Unit.COMMERCIAL
        return all(self.side_holder(sc) == Unit.COMMERCIAL for sc in self.sides)


class LineItem(models.Model):
    """One row of the editable 4-column inquiry table.

    Columns map 1:1 to the business spec: item number, description, size, unit.
    Quantity is kept as an optional column used later by the coding assistant.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="line_items")
    row_no = models.PositiveIntegerField()
    # The client's own row number (#). It is assigned when the case is created and
    # preserved across re-versions, so when a later version deletes a row the gap
    # in client_row reveals which original row was removed. 0 = not set (legacy).
    client_row = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    size = models.CharField(max_length=120, blank=True)
    unit = models.CharField(max_length=40, blank=True)
    quantity = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["case", "row_no"]
        unique_together = ("case", "row_no")

    def __str__(self):
        return f"{self.case_id}#{self.row_no}"


class CaseForm(models.Model):
    """A stored, versioned snapshot of one form (Inquiry / TO / PI).

    ``table`` is the row data; ``meta`` holds the header boxes (DATE, DOC NO,
    ORDER NO, CLIENT), totals, VAT and any other scalar values shown on exports.
    Storing forms as structured JSON keeps them searchable and re-exportable to
    Excel / PDF / HTML on demand.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="forms")
    kind = models.CharField(max_length=10, choices=FormKind.CHOICES, db_index=True)
    # Internal / External sub-stream this form belongs to (blank for single-side
    # cases that predate the split).
    side = models.CharField(max_length=10, blank=True, db_index=True)
    version = models.PositiveIntegerField(default=0)
    is_current = models.BooleanField(default=True, db_index=True)

    columns = models.JSONField(default=list, blank=True)   # ordered column titles
    table = models.JSONField(default=list, blank=True)     # list[dict] of rows
    meta = models.JSONField(default=dict, blank=True)       # header boxes + totals
    # True once this version has left its unit (sent/returned). A sent version
    # can no longer be edited — the owner must branch a new version instead.
    sent = models.BooleanField(default=False)
    # True when this snapshot belongs to a TO & PI two-stage generation (set when
    # a TO-only case is upgraded to two-stage, and inherited by the TO/PI built
    # against that inquiry). Distinguishes the two-stage "Version NN" from the
    # original same-numbered version in the per-tab version list and on exports.
    two_stage = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="created_forms",
    )
    unit_at_creation = models.CharField(max_length=20, blank=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="signed_forms",
        help_text="User whose signature/approval is stamped on this form.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Last save/edit time for this snapshot (used on Excel/PDF Date field).
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "-version"]

    def __str__(self):
        from .codes import format_version
        return f"{self.case.doc_no} · {self.get_kind_display()} v{format_version(self.version)}"

    def make_current(self):
        """Mark this snapshot as the current one for its kind and side."""
        CaseForm.objects.filter(
            case=self.case, kind=self.kind, side=self.side
        ).exclude(pk=self.pk).update(is_current=False)
        if not self.is_current:
            self.is_current = True
            self.save(update_fields=["is_current"])


class CaseEvent(models.Model):
    """Immutable timeline entry: every transition, comment and edit.

    Together with ``created_at`` these rows give exact, auditable timing for
    reporting (how long a case stayed in each unit, who acted and when).
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="case_events",
    )
    action = models.CharField(max_length=30, choices=EventAction.CHOICES)
    from_unit = models.CharField(max_length=20, blank=True)
    to_unit = models.CharField(max_length=20, blank=True)
    comment = models.TextField(blank=True)
    # When the event concerns a TO/PI form, record which form and version it was.
    form_kind = models.CharField(max_length=10, blank=True)
    form_version = models.IntegerField(null=True, blank=True)
    # Internal / External sub-stream this event belongs to (blank = case-level).
    side = models.CharField(max_length=10, blank=True, db_index=True)

    @property
    def side_label(self) -> str:
        return Side.LABELS.get(self.side, "")
    # Set on the new-inquiry-version event that upgraded a TO case to two-stage.
    two_stage = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Frozen at the moment the event is written: the actor's display name and
    # unit/role label exactly as they were at that instant. The timeline must
    # always read these two fields (never ev.actor.get_full_name /
    # ev.actor.profile.title_line live) so that a later rename, promotion, or
    # departure can never rewrite what already happened. See
    # cases.services.log() for where these are populated, and migration 0005
    # for the one-time backfill of rows written before this existed.
    actor_name = models.CharField(max_length=160, blank=True)
    actor_role_label = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case_id} · {self.action}"

    @property
    def action_label(self) -> str:
        return EventAction.LABELS.get(self.action, self.action)

    @property
    def side_label(self) -> str:
        return Side.LABELS.get(self.side, "")

    @property
    def actor_display_name(self) -> str:
        """The name to show on the timeline — frozen first, live as a last resort.

        The fallback only matters for the brief window before the backfill
        migration has run, or for a row somehow written outside log(); once
        that migration has run every row has actor_name populated and this
        always returns the frozen value.
        """
        if self.actor_name:
            return self.actor_name
        if self.actor_id and self.actor:
            return self.actor.get_full_name() or self.actor.username
        return ""

    @property
    def actor_is_substitute(self) -> bool:
        """True when this event was written while the actor held a Translate seat."""
        lab = (self.actor_role_label or "").strip()
        if not lab:
            return False
        return lab == "Substitute" or lab.startswith("Substitute ·") or lab.startswith("Substitute ")


class CaseExportLog(models.Model):
    """Audit row for every document export (who / what / when).

    Kept in its own table (not the workflow ``CaseEvent`` timeline) so exports
    never influence reporting, archive participation or lifecycle timing. Shown
    only to admins / general managers in a dedicated export timeline.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="export_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="case_exports",
    )
    form_kind = models.CharField(max_length=10, blank=True)      # TO / PI
    form_version = models.IntegerField(null=True, blank=True)
    side = models.CharField(max_length=10, blank=True)
    fmt = models.CharField(max_length=20, blank=True)            # xlsx / grouped / pdf / html
    label = models.CharField(max_length=120, blank=True)         # human label, e.g. "Proforma PDF"
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case_id} · export {self.fmt}"

    @property
    def side_label(self) -> str:
        return Side.LABELS.get(self.side, "")


class CaseCurrencyLog(models.Model):
    """Audit row for Proforma unit conversions (who / when / from→to / rate).

    Separate from the workflow timeline. Visible only to admins / general
    managers in the Conversion Timeline tab.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="currency_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="case_currency_conversions",
    )
    from_code = models.CharField(max_length=12, blank=True)
    to_code = models.CharField(max_length=12, blank=True)
    rate = models.CharField(max_length=40, blank=True)  # stored as display text
    side = models.CharField(max_length=10, blank=True)
    form_kind = models.CharField(max_length=10, blank=True, default="PI")
    form_version = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=20, blank=True)  # tool / commercial
    label = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case_id} · {self.from_code}→{self.to_code}"

    @property
    def side_label(self) -> str:
        return Side.LABELS.get(self.side, "")


class CurrencyRate(models.Model):
    """Exchange rate vs Iranian Rial, maintained by the Commercial manager.

    `rial_price` is how many Rials equal **one** unit of this currency
    (e.g. USD -> 1,700,000). Used by Supply/Commercial PI conversion so users
    no longer type a manual rate. The board is considered stale when the most
    recent update across all rows is older than 24 hours.
    """

    code = models.CharField(max_length=12, unique=True, db_index=True)
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=12, blank=True)
    rial_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    is_builtin = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='currency_rate_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code.upper()} = {self.rial_price} Rial'


def frozen_signature_upload_path(instance, filename):
    """Store each frozen signature copy under media/signatures/frozen/<form-id>/<filename>."""
    return f"signatures/frozen/{instance.form_id}/{filename}"


def frozen_stamp_upload_path(instance, filename):
    """Store each frozen stamp copy under media/stamps/frozen/<form-id>/<filename>."""
    return f"stamps/frozen/{instance.form_id}/{filename}"


class SignatureSnapshot(models.Model):
    """Who appears as the signatory on an exported document, frozen the first
    time that exact form version is actually exported.

    The business rule for *who* signs a document is unchanged: it is still
    whoever currently holds the relevant manager position
    (``export_data.vendor_signatory``). What this model fixes is a different
    problem — once a document has actually been generated and sent out, a
    later change of manager must not silently change who that already-issued
    document appears to have been signed by. The name and the image are both
    copied here (not referenced), so nothing that happens to the live profile
    afterwards can alter an already-frozen snapshot.

    One snapshot per CaseForm (i.e. per form version, since CaseForm rows are
    already one-per-version) — created lazily by
    cases.export_data on first export, not by the core workflow.
    """

    # db_constraint=False: deliberately does NOT create a database-level
    # foreign key constraint. The relationship (and its uniqueness — one
    # snapshot per form — and its CASCADE delete behaviour) is still fully
    # enforced by Django itself; only the DB-level FK constraint is skipped.
    # This was required to deploy safely: cases_caseform predates every
    # migration in this project and, on the live production database, does
    # not carry whatever constraint Postgres requires to accept a new
    # foreign key pointing at it — every other relationship in this codebase
    # points at auth_user or at other tables, so this table was never
    # exercised this way before. Rather than alter a table that already
    # holds live production data to chase that down, the new table simply
    # doesn't ask the database to enforce the link. In practice this only
    # matters if a CaseForm row is ever deleted by raw SQL outside the ORM
    # (nothing in this codebase does that — forms are versioned and kept,
    # never deleted); a normal .delete() through Django still cascades
    # correctly regardless.
    form = models.OneToOneField(
        CaseForm, on_delete=models.CASCADE, related_name="signature_snapshot",
        db_constraint=False,
    )
    signer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="frozen_signatures",
    )
    signer_name = models.CharField(max_length=160, blank=True)
    # The seat the signer occupied at the moment of signing, as plain text —
    # "Technical · Manager", not a pointer to whatever their title happens to
    # be today. Without this, a promotion or a transfer silently restates the
    # rank on every document that person ever signed, which is the same class
    # of bug the frozen name and image were added to prevent. Blank on rows
    # created before this field existed, which renders exactly as those
    # documents rendered then (name only, no title).
    signer_title = models.CharField(max_length=160, blank=True)
    signature_image = models.ImageField(
        upload_to=frozen_signature_upload_path, blank=True, null=True,
    )
    stamp_image = models.ImageField(
        upload_to=frozen_stamp_upload_path, blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Signature snapshot · {self.form_id} · {self.signer_name}"
