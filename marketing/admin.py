"""Django-admin registrations for the Marketing directory.

NOT THE DAY-TO-DAY TOOL, and this file follows the same convention
``accounts/admin.py`` and ``people/admin.py`` already state for their own apps:
Marketing is worked from ``marketing/views.py`` — the role chart and the
company directory — and this is the back-office view underneath it. So
everything here is narrowed rather than widened, and each narrowing says why.

WHY IT EXISTS AT ALL, given that this app had no ``admin.py`` until now. One
model genuinely needs a management screen that the app's own pages cannot
provide: :class:`marketing.models.ContactRole`, the shared list of job titles a
company contact can hold. The owner's rule is that a Marketing Supervisor AND
the platform admin may extend that list — but the admin is deliberately
view-only over Marketing's working data (``marketing/access.py::access_for``
makes ``can_edit`` False for them, unconditionally), so the in-app "…or add a
new role" field on the contact form is not, and must not become, their route to
it. Without this file the admin had NO route to it, which on an install with no
Marketing Supervisor meant nobody could create a contact role — and since the
contact form makes the role mandatory, that blocked contact creation outright.
See ``marketing/access.py::Access.can_manage_config``, the capability that names
this exact grant, and ``marketing/views.py::_can_manage_roles``, which reads it.

The other two models are registered READ-ONLY, and for reasons this codebase
has already applied to their nearest equivalents:

* :class:`marketing.models.ClientEvent` is an audit trail, exactly as
  ``cases.models.CaseEvent`` is, and ``cases/admin.py`` holds that one
  read-only with the words "an audit trail that can be edited or erased is not
  an audit trail". The same applies verbatim here, including to the frozen
  ``actor_name``/``actor_role_label`` columns that exist precisely so history
  cannot be rewritten.
* :class:`marketing.models.CompanyContact` is working data whose visibility is
  scoped per viewer by ``created_by`` (see ``services.list_contacts``). Editing
  it here would bypass both that scope and the ``ClientEvent`` row every
  contact write in ``marketing/services.py`` produces — a contact would change
  or vanish with nothing in the company's timeline saying so. It is visible for
  diagnosis; it is changed on the company detail page.

``ClientLabel`` and ``Connection`` are deliberately NOT registered. They are one
Marketing user's own scoped rows, they carry the same
"every write leaves a timeline row" discipline, and nothing about them needs a
break-glass screen — the chart creates and removes them, and an admin who needs
to inspect one can read the company's timeline, which is registered above.
"""
from django.contrib import admin

from .models import ClientEvent, CompanyContact, ContactRole


@admin.register(ContactRole)
class ContactRoleAdmin(admin.ModelAdmin):
    """The shared contact-title vocabulary — the one screen here that writes.

    Fully editable on purpose (see the module docstring): this is the platform
    admin's route to the list, and a configuration vocabulary is exactly the
    kind of thing an admin screen is for.

    ``created_by`` is read-only rather than hidden. It records who introduced a
    title, for the same audit reasons every model in this app records it, and it
    is stamped by whichever path created the row (``views.contact_add``'s
    ``get_or_create``, or :meth:`save_model` below for a row added here).
    Leaving it editable would let this screen rewrite that answer; leaving it
    off the form entirely would hide it from the person most likely to be asking
    the question.

    Deletion is allowed, and is safe by construction:
    ``CompanyContact.role`` is ``SET_NULL``, so retiring a title never removes
    the PEOPLE who held it — see that field's own comment.
    """

    list_display = ("name", "created_by", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        # Stamped on first creation only, never overwritten on an edit — the
        # same rule ``services.create_connection`` and ``views.contact_add``
        # already follow for this column. Without this a role added from this
        # screen would be the one row in the table with no author at all,
        # because ``created_by`` is excluded from the form above.
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CompanyContact)
class CompanyContactAdmin(admin.ModelAdmin):
    """Read-only: contacts are added and removed on the company detail page.

    Writing here would skip the per-viewer ``created_by`` scope
    (``services.list_contacts``) and, more importantly, the ``ClientEvent`` row
    that ``services.add_contact``/``remove_contact`` write — a company's
    timeline would silently stop matching its own contact list.
    """

    list_display = ("full_name", "client", "role", "created_by", "created_at")
    list_filter = ("role", "gender")
    search_fields = ("first_name", "last_name", "email", "phone",
                     "client__name", "client__code")
    list_select_related = ("client", "role", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ClientEvent)
class ClientEventAdmin(admin.ModelAdmin):
    """Read-only: the company timeline is the audit trail.

    Held exactly the way ``cases/admin.py`` holds ``CaseEvent``, and for the
    identical reason — see the module docstring. Note that this screen shows
    only the STORED half of a company's timeline; the case-derived and
    registration halves are synthesised at read time by
    ``services.client_timeline`` and have no rows to list here.
    """

    list_display = ("client", "action", "actor_display_name", "subject", "created_at")
    list_filter = ("action",)
    search_fields = ("client__name", "client__code", "actor_name", "subject", "comment")
    list_select_related = ("client",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
