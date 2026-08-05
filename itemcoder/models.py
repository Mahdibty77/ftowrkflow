"""Data-management models for the coding tool.

These tables let an administrator manage the tool's reference data while the
project is running, WITHOUT changing any of the coding/pricing logic:

* ``CodeTable`` / ``CodeTableRow`` hold each group's coding-data table (the big
  ``<group>_coding_data.csv``) row-by-row. The loader rebuilds a byte-identical
  pandas DataFrame from these rows, so the positional matching logic is
  unchanged. When a group has no DB rows the loader falls back to the CSV file,
  so existing behaviour is preserved until an admin imports the data.
* ``PriceList`` / ``CodePrice`` allow many price lists per code (instead of a
  single price column), each updatable independently with per-column filters.
* ``ConfigDocument`` stores versioned JSON configuration (data.json, rules,
  alerts ...) so configs can be updated and rolled back from the admin panel.

All of these are additive: nothing here is required for the tool to run.
"""
from django.conf import settings
from django.db import models


class CodeTable(models.Model):
    """Header/metadata for one group's coding-data table."""

    group = models.CharField(max_length=40, unique=True, db_index=True)
    columns = models.JSONField(default=list)          # ordered header names
    row_count = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["group"]

    def __str__(self):
        return f"CodeTable<{self.group}> ({self.row_count} rows)"


class CodeTableRow(models.Model):
    """A single coding-data row, stored positionally as a list of strings."""

    group = models.CharField(max_length=40, db_index=True)
    row_no = models.PositiveIntegerField()
    cells = models.JSONField(default=list)            # list[str], by column order

    class Meta:
        ordering = ["row_no"]
        indexes = [models.Index(fields=["group", "row_no"])]

    def __str__(self):
        return f"{self.group}#{self.row_no}"


class PriceList(models.Model):
    """A named price list (e.g. 'Steel-1404Q1'). Codes can appear in many."""

    name = models.CharField(max_length=80, unique=True)
    currency = models.CharField(max_length=10, default="rial")
    note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CodePrice(models.Model):
    """The price of one code inside one price list."""

    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="prices")
    code = models.CharField(max_length=80, db_index=True)
    price = models.DecimalField(max_digits=18, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("price_list", "code")
        indexes = [models.Index(fields=["code", "price_list"])]

    def __str__(self):
        return f"{self.code} @ {self.price_list_id} = {self.price}"


class ConfigDocument(models.Model):
    """A versioned JSON configuration document (data.json, rules, alerts ...)."""

    key = models.CharField(max_length=80, db_index=True)     # e.g. "data.json"
    group = models.CharField(max_length=40, blank=True)      # blank = global
    payload = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["key", "-version"]
        indexes = [models.Index(fields=["key", "is_active"])]

    def __str__(self):
        return f"{self.key} v{self.version}{' (active)' if self.is_active else ''}"


# --------------------------------------------------------------------------- #
# Per-group feature schema (attributes + value/codes) for the item builder.
# Structural config (which features, order, which feed the small code) is
# seeded from itemcoder/resources/json/feature_schema/<group>.json and may be
# adjusted in code; attribute VALUES and their codes are managed in the admin.
# --------------------------------------------------------------------------- #
class GroupCodeConfig(models.Model):
    """Code-building constants for one product group (mirrors the generator)."""

    group = models.CharField(max_length=40, unique=True, db_index=True)
    tech_start = models.CharField(max_length=8, blank=True, default="")
    tech_group = models.CharField(max_length=8, blank=True, default="")
    item_start = models.CharField(max_length=8, blank=True, default="")
    item_group = models.CharField(max_length=8, blank=True, default="")
    item_seq_digits = models.PositiveSmallIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"code-config[{self.group}]"


class GroupFeature(models.Model):
    """One column of a product group's code table.

    kind:
      * main  – a primary attribute (has codes, takes part in the big/small code,
                cascades by rules in the item builder; defined by the first
                uploaded header without a "(not main)" marker).
      * sub   – a secondary attribute ("(not main)" in the header or an OR-feature
                in asign_code.json, or added later by the admin): no code, no
                rules, appears after the main features in the builder.
      * info  – not an attribute at all (price / weight / custom data columns):
                no code, editable per row, never affects the code.
    """

    MAIN, SUB, INFO = "main", "sub", "info"
    KIND_CHOICES = [(MAIN, "Main"), (SUB, "Sub"), (INFO, "Info")]

    group = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=8, choices=KIND_CHOICES, default=MAIN)
    in_small_code = models.BooleanField(default=False)
    # Priority of this feature within the small code (1 = first, 2 = second,
    # 0 = not part of the small code). At most two features per group.
    small_order = models.PositiveSmallIntegerField(default=0)
    # Column index in the group's code table this feature maps to (-1 = none).
    column_index = models.IntegerField(default=-1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group", "position", "name"]
        unique_together = [("group", "name")]
        indexes = [models.Index(fields=["group", "position"])]

    def __str__(self):
        return f"{self.group}.{self.name} ({self.kind})"


class FeatureValue(models.Model):
    """An allowed value of a feature plus its code (e.g. C.S -> 01)."""

    group = models.CharField(max_length=40, db_index=True)
    feature = models.CharField(max_length=80, db_index=True)
    value = models.CharField(max_length=200)
    code = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["group", "feature", "code"]
        unique_together = [("group", "feature", "value")]
        indexes = [models.Index(fields=["group", "feature"])]

    def __str__(self):
        return f"{self.group}.{self.feature}: {self.value}={self.code}"


class EaItemCreationLog(models.Model):
    """Audit trail: every database item created through the Technical
    Assistant (EA), as opposed to the admin Add Item / Tool Data screens.

    EA may only ever create an item by adding a new Size to an attribute
    combination that already exists — never a brand-new combination of
    other attributes (that stays a Technical Manager action via Tool Data).
    This log exists specifically so that distinction is visible and
    reviewable after the fact, not just enforced at the moment of creation.
    Visible to Admin and Technical Manager in Tool Data — read-only, like
    accounts.ImpersonationLog and cases.SignatureSnapshot elsewhere in the
    project; nothing here is ever edited after creation, only read.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="ea_item_creations",
    )
    user_username = models.CharField(max_length=150, blank=True)
    group = models.CharField(max_length=40, db_index=True)
    item_type = models.CharField(max_length=80, blank=True)

    # The full attribute combination used to build the new row, and which
    # single feature was the one actually new (should always be the size
    # feature — kept explicit rather than assumed, so this log is a
    # faithful record of what was validated, not just what the code intended.
    selected_values = models.JSONField(default=dict, blank=True)
    new_feature = models.CharField(max_length=80, blank=True)
    new_value = models.CharField(max_length=200, blank=True)

    technical_code = models.CharField(max_length=64, blank=True)
    item_code = models.CharField(max_length=64, blank=True)

    case_id = models.PositiveIntegerField(null=True, blank=True)
    row_client_no = models.CharField(max_length=40, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["group", "-created_at"], name="itemcoder_ea_grp_dt_idx")]

    def __str__(self):
        return f"EA created {self.group}/{self.item_code} by {self.user_username}"
