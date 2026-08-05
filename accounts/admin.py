from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import ImpersonationLog, PlatformConfig, Profile


@admin.register(PlatformConfig)
class PlatformConfigAdmin(admin.ModelAdmin):
    list_display = ("login_welcome_message", "vat_percent", "updated_at")

    def has_add_permission(self, request):
        return not PlatformConfig.objects.exists()


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "unit", "role", "is_admin", "internal_code", "org_title")
    list_filter = ("unit", "role", "is_admin")
    search_fields = ("user__username", "user__first_name", "user__last_name", "internal_code")

    # ``plain_password`` is a dead column, kept only so nothing errors if a very
    # old cached page still references it (see the model docstring) and blanked
    # by migration 0007. Leaving it on the default ModelAdmin form rendered it
    # as an ordinary editable text input — which made this screen the one
    # remaining place in the whole platform where a readable password could be
    # written back into the database. Excluding it closes that path without
    # needing a schema change.
    exclude = ("plain_password",)

    # Deleting a Profile row orphans its User and removes the record every
    # permission decision reads through, so this is never the right way to
    # remove someone. Ending access is "Cut off" on the Users page: reversible,
    # immediate, and it leaves every historical record intact.
    def has_delete_permission(self, request, obj=None):
        return False


class UserAdmin(DjangoUserAdmin):
    """Django's own user admin, with permanent deletion removed.

    Hard-deleting a user silently unsigns every document they ever approved:
    the signature and actor links are SET_NULL, so on the document itself the
    result is indistinguishable from it never having been signed at all. The
    platform's own Users page had its delete button removed for exactly this
    reason during the 2026-07 security pass, but this built-in screen stayed
    wired up and still offered both the per-object delete and the bulk
    "delete selected" action.

    Ending someone's access is "Cut off" on the Users page. A full offboarding
    flow — handing open work to a successor and marking the person departed —
    belongs with the Personnel module and is deliberately not built here.
    """

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        # has_delete_permission alone does not reliably remove the bulk action
        # from the dropdown, so drop it explicitly as well.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(ImpersonationLog)
class ImpersonationLogAdmin(admin.ModelAdmin):
    """Read-only audit trail: who logged in as whom, and when."""
    list_display = ("admin_username", "target_username", "started_at", "ended_at", "is_active")
    list_filter = ("started_at",)
    search_fields = ("admin_username", "target_username")
    readonly_fields = ("admin", "admin_username", "target", "target_username",
                       "started_at", "ended_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # An audit trail that can be erased is not an audit trail.
        return False
