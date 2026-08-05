from django.contrib import admin

from .models import Person, PersonAccount, PersonCounter


class PersonAccountInline(admin.TabularInline):
    """The seats a person holds, read-only.

    Read-only on purpose. Adding a row here would create the link without
    renaming the account, which is precisely the half-done state
    people.seats.assign_seat exists to make impossible — the seat would be
    recorded as held while still carrying somebody else's name. Seats are given
    and taken back on the person's own seats page.
    """

    model = PersonAccount
    extra = 0
    can_delete = False
    fields = ("user", "assigned_at", "assigned_by")
    readonly_fields = ("user", "assigned_at", "assigned_by")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("detail_code", "full_name", "username", "status",
                    "seat_count", "created_at")
    list_filter = ("status",)
    search_fields = ("detail_code", "first_name", "last_name",
                     "first_name_en", "last_name_en", "username")
    readonly_fields = ("detail_code", "username", "created_at",
                       "updated_at", "created_by")
    inlines = [PersonAccountInline]

    def get_queryset(self, request):
        # seat_count counts the seats already loaded, so without this the
        # changelist runs one extra query per person just to print a number.
        return super().get_queryset(request).prefetch_related("accounts")

    def has_delete_permission(self, request, obj=None):
        # There is no deleting a person. Everything they were ever assigned,
        # approved or signed points back here; removing the row would strip the
        # name off that history exactly the way deleting a user account used
        # to. Someone who leaves is marked Departed instead.
        return False


@admin.register(PersonAccount)
class PersonAccountAdmin(admin.ModelAdmin):
    """The seat register: who holds what, and since when. Read-only.

    Every change to a seat has to rename an account in the same breath, and
    that is a service (people.seats), not a form. Editing here would produce
    links whose accounts still carry the wrong name.
    """

    list_display = ("user", "person", "assigned_at", "assigned_by")
    search_fields = ("user__username", "person__detail_code",
                     "person__first_name", "person__last_name")
    readonly_fields = ("person", "user", "assigned_at", "assigned_by")
    list_select_related = ("user", "person", "assigned_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PersonCounter)
class PersonCounterAdmin(admin.ModelAdmin):
    """Visible for diagnosis, not for editing.

    Winding this back would hand an already-used detail code to a second
    person, and two humans sharing one identifier is not something the rest of
    the system can recover from on its own.
    """
    list_display = ("key", "value")
    readonly_fields = ("key", "value")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
