"""Forms for the Marketing company directory — currently one: adding a contact.

WHY A FORM AT ALL, when ``marketing/services.py::add_contact`` already exists
and already validates. The two layers deliberately check different things, and
the split is documented on both sides (see ``add_contact``'s own docstring and
``marketing/models.py::CompanyContact``):

* THE SERVICE checks only what the database cannot and what any caller —
  an import, a shell fix-up, a future JSON endpoint — must not be able to get
  wrong: a blank name, an unknown gender key. It raises ``ValueError``, which
  is the right answer for a caller that is not a person filling in a screen.
* THIS FORM checks what only a SCREEN can meaningfully report: the
  "at least one of phone/email" rule, which the model deliberately does not
  express as a database constraint precisely so that a violation can be shown
  next to the fields the user left blank instead of surfacing as an
  ``IntegrityError`` with nothing to attach it to.

So this module owns the rule, and it owns it in ``clean()`` — server-side,
where it cannot be skipped by a reader with JavaScript off, a stale page, or a
hand-built POST. There is no JavaScript counterpart of the rule anywhere: the
one enforcement point is the one below.

WHAT THIS FORM DOES NOT DO. It never writes. ``clean()`` validates and
``cleaned_data`` is handed to ``services.add_contact`` by the view, so the
service stays the single writer of a ``CompanyContact`` (and the single writer
of the ``ClientEvent`` that records it). The same is true of the inline
"add a new role" field: this form only decides that the name is usable and
that this viewer is allowed to offer one, and the view does the
``get_or_create`` on ``ContactRole``.
"""
from __future__ import annotations

from django import forms

from .models import ContactGender, ContactRole


class ContactForm(forms.Form):
    """Add one person at a company: who they are, and how to reach them.

    ``can_manage_roles`` (keyword-only, defaults to False — FAIL CLOSED, the
    same default ``marketing/services.py``'s own scoped functions use) is
    resolved by the view from ``marketing/access.py::access_for`` and decides
    whether the inline ``new_role`` field exists on this form AT ALL. It is
    removed from ``self.fields`` rather than merely hidden in the template when
    the viewer may not create roles, so a hand-built POST carrying
    ``new_role=...`` from an ordinary Marketing Expert is not a gate to
    re-check further down — the field simply is not part of the form, and its
    value never reaches ``cleaned_data``. See the view for the other half of
    that gate (the ``ContactRole`` row is created there, and only there).
    """

    def use_required_attribute(self, field):
        # Verbatim the reasoning ``cases.forms.CaseCreateForm`` documents for
        # the same override, and for the same concrete reason: the role
        # <select> below carries ``data-combo``, and ``static/js/ui.js``
        # upgrades such a control by setting ``display:none`` on it and
        # drawing a searchable text box beside it. A hidden control carrying
        # the native ``required`` attribute makes the browser refuse to submit
        # with "An invalid form control is not focusable" — and it refuses
        # silently, with no message a user can act on. Dropping the ATTRIBUTE
        # changes nothing about validation: every ``required=True`` below is
        # still enforced here, on the server, which is where this form's rules
        # live anyway.
        return False

    first_name = forms.CharField(
        max_length=120, required=True, label="First name",
        widget=forms.TextInput(attrs={"autocomplete": "off", "dir": "auto"}),
    )
    last_name = forms.CharField(
        max_length=120, required=True, label="Last name",
        widget=forms.TextInput(attrs={"autocomplete": "off", "dir": "auto"}),
    )
    # required=False at the FIELD level, required in ``clean()`` — because
    # "pick an existing role" and "type a new one" are two ways of satisfying
    # ONE requirement, and a field-level ``required=True`` here would reject a
    # supervisor who legitimately used the second one. See ``clean()``.
    role = forms.ModelChoiceField(
        queryset=ContactRole.objects.all(), required=False,
        label="Contact role", empty_label="— Select a contact role —",
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": "Search contact role…",
        }),
    )
    gender = forms.ChoiceField(
        choices=[("", "— Select —")] + list(ContactGender.CHOICES),
        required=True, label="Gender",
    )
    phone_prefix = forms.CharField(
        max_length=16, required=False, label="Dialling prefix",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "inputmode": "tel", "placeholder": "e.g. 021",
        }),
    )
    phone_ext = forms.CharField(
        max_length=16, required=False, label="Internal extension",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "inputmode": "tel", "placeholder": "e.g. 214",
        }),
    )
    phone = forms.CharField(
        max_length=32, required=False, label="Phone number",
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "tel"}),
    )
    email = forms.EmailField(
        required=False, label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "off", "dir": "ltr"}),
    )

    def __init__(self, *args, can_manage_roles: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_manage_roles = bool(can_manage_roles)
        if self.can_manage_roles:
            # Added here rather than declared above so that a viewer without
            # the grant has no such field at all — see the class docstring.
            self.fields["new_role"] = forms.CharField(
                max_length=120, required=False, label="…or add a new role",
                widget=forms.TextInput(attrs={
                    "autocomplete": "off", "dir": "auto",
                    "placeholder": "e.g. Head of QC",
                }),
            )
        # The role list is a managed vocabulary that can legitimately be EMPTY
        # on a fresh install (see ``marketing/models.py::ContactRole``), and an
        # empty required dropdown is a dead end for anyone who cannot add to
        # it. The template says so in words; this flag is what it asks.
        self.roles_available = ContactRole.objects.exists()

    def clean_new_role(self):
        """The typed new role name, stripped — never a bare duplicate.

        Only ever called when the field exists, i.e. when this viewer may
        manage roles. ``ContactRole.name`` is globally unique (one shared
        vocabulary — see the model), so a name that already exists is not an
        error here: the view get-or-creates, and re-typing an existing title is
        the same intent as picking it from the list. All this does is normalise
        the whitespace so " Head of QC " and "Head of QC" cannot become two
        rows that read identically in every dropdown.
        """
        return (self.cleaned_data.get("new_role") or "").strip()

    def clean(self):
        """The two rules that are about the FORM, not about any one field.

        1. EXACTLY ONE SOURCE OF THE ROLE. The role is required — the owner
           asked for a contact to carry a job title — but it can be satisfied
           either by picking from the managed list or, for a viewer who may
           manage that list, by typing a new one. Supplying both is rejected
           rather than silently resolved: guessing which of two contradicting
           answers the user meant is how a contact ends up filed under a title
           nobody chose.

        2. AT LEAST ONE WAY TO REACH THE PERSON. The rule the model
           deliberately does not express as a database constraint (see
           ``marketing/models.py::CompanyContact``'s docstring): a contact with
           neither a phone number nor an email is a name with no purpose. It is
           raised as a NON-FIELD error because it is true of neither field on
           its own — blaming ``phone`` would be arbitrary when filling in
           ``email`` fixes it just as well — and the template renders non-field
           errors the way every other form in this codebase does.

        Both rules are enforced HERE, on the server. Nothing in this section
        re-implements either one in JavaScript, so a reader with scripting off,
        a stale tab, or a hand-built POST meets exactly the same two checks.
        """
        cleaned = super().clean()
        role = cleaned.get("role")
        new_role = (cleaned.get("new_role") or "").strip() if self.can_manage_roles else ""

        if role and new_role:
            self.add_error(
                "new_role",
                "Either pick a contact role from the list or add a new one — not both.",
            )
        elif not role and not new_role:
            self.add_error("role", "A contact role is required.")

        phone = (cleaned.get("phone") or "").strip()
        email = (cleaned.get("email") or "").strip()
        if not phone and not email:
            raise forms.ValidationError(
                "Enter at least one way to contact this person — a phone number "
                "or an email address."
            )
        return cleaned
