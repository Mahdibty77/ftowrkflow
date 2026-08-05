from django.contrib import admin

from .models import (Case, CaseEvent, CaseForm, Client, ExpertCode, LineItem,
                     SerialCounter, SignatureSnapshot)


class LineItemInline(admin.TabularInline):
    model = LineItem
    extra = 0


class CaseEventInline(admin.TabularInline):
    model = CaseEvent
    extra = 0
    readonly_fields = ("actor", "action", "from_unit", "to_unit", "comment", "created_at")
    can_delete = False


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("doc_no", "kind", "offer_type", "client", "status",
                    "holder_unit", "assigned_to", "created_at")
    list_filter = ("status", "kind", "offer_type", "holder_unit")
    search_fields = ("doc_no", "serial", "client__name", "client__code", "order_no")
    inlines = [LineItemInline, CaseEventInline]


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "created_by", "created_at")
    search_fields = ("code", "name")
    filter_horizontal = ("assigned_experts",)


@admin.register(ExpertCode)
class ExpertCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "user")
    search_fields = ("code", "name")


@admin.register(CaseForm)
class CaseFormAdmin(admin.ModelAdmin):
    list_display = ("case", "kind", "version", "is_current", "created_by", "created_at")
    list_filter = ("kind", "is_current")


admin.site.register(SerialCounter)


@admin.register(CaseEvent)
class CaseEventAdmin(admin.ModelAdmin):
    """Read-only: the case timeline is the audit trail.

    This was previously registered bare, which left every timeline row
    editable and deletable from this screen — including the frozen actor name
    and title that exist precisely so history cannot be rewritten. An audit
    trail that can be edited or erased is not an audit trail.
    """
    list_display = ("case", "action", "actor_display_name", "from_unit", "to_unit", "created_at")
    list_filter = ("action", "from_unit", "to_unit")
    search_fields = ("case__doc_no", "actor_name", "comment")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SignatureSnapshot)
class SignatureSnapshotAdmin(admin.ModelAdmin):
    """Read-only: each row is a frozen historical fact, never meant to be edited."""
    list_display = ("form", "signer_name", "signer_title", "created_at")
    search_fields = ("signer_name", "signer_title")
    readonly_fields = ("form", "signer_user", "signer_name", "signer_title",
                       "signature_image", "stamp_image", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting a snapshot silently un-freezes the document it belongs to:
        # rendering falls back to a live lookup and the signer can change.
        return False
