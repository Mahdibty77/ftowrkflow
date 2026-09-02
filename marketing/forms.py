"""Forms for the Marketing company directory: adding a contact, writing a
report, setting a reminder.

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

:class:`ReportForm` is built on the identical split, for the identical reason.
``services.add_report`` writes and checks nothing a screen could report better;
this form owns the one rule that IS about the screen — "a report that says
nothing is not a report", i.e. at least one preset option chosen or some free
text written — and owns it in ``clean()``, server-side, with no JavaScript
counterpart. Two different forms, one convention.

WHAT THIS FORM DOES NOT DO. It never writes. ``clean()`` validates and
``cleaned_data`` is handed to ``services.add_contact`` by the view, so the
service stays the single writer of a ``CompanyContact`` (and the single writer
of the ``ClientEvent`` that records it). The same is true of the inline
"add a new role" field: this form only decides that the name is usable and
that this viewer is allowed to offer one, and the view does the
``get_or_create`` on ``ContactRole``.

:class:`ReportForm` CARRIES THE SAME INLINE-VOCABULARY FIELD, for
``marketing/models.py::ReportOption``, built the same way and for the same
reason — see ``new_option`` below. That list used to have exactly one route
into existence, the Django admin, which a Marketing Supervisor cannot open at
all (they are not ``is_staff``) even though
``marketing/access.py::Access.can_manage_config`` names them as one of the two
seats that administer it — and the report screen told them to go there anyway.
One capability governing two vocabularies now has one mechanism for both.

:class:`ReminderForm` and :class:`ReminderTimeForm` are the third and fourth,
and they follow the same split again — they validate, they never write
(``marketing/reminders.py`` does) — with one thing the other two do not need:
they are the only forms in this app that take a Jalali DATE AND TIME. See
``_clean_jalali_datetime`` for why that parse lives here rather than being
borrowed from ``cases/forms.py``, and why it is written once for both.
"""
from __future__ import annotations

from django import forms

from .models import ContactGender, ContactRole, ReportOption


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


class ReportForm(forms.Form):
    """Step 2 of writing a report: what it says.

    THE OWNER'S FLOW HAS TWO STEPS, and only the second one is a Django form.
    Step 1 offers the writer their own cases at this company so they can attach
    one, with an explicit Skip — it chooses a destination, it writes nothing,
    and it is a plain ``<select>`` and two buttons in
    ``marketing/templates/marketing/report_case.html`` rather than a form class
    of its own. What arrives here from it is ``case_id``, carried through as a
    hidden field below so a re-rendered step 2 (a validation error, a stale tab)
    cannot silently lose the case the writer already picked.

    ``case_id`` IS VALIDATED HERE ONLY AS A NUMBER, NEVER AS A PERMISSION. This
    form has no request, no viewer and no company, so it cannot possibly know
    which cases this person may attach; the VIEW resolves it against exactly the
    scoped rows the company page's own Cases tab lists (see
    ``marketing/views.py::report_create``) and drops anything else. A hidden
    field is a value the browser sends, not a fact, and it is treated as one.

    THE ONE RULE THIS FORM OWNS is in :meth:`clean` — at least one option or
    some text. Both fields are ``required=False`` at the field level for
    ``ContactForm.role``'s exact reason: they are two ways of satisfying ONE
    requirement, and a field-level ``required=True`` on either would reject a
    writer who legitimately used the other.

    ``can_manage_options`` (keyword-only, defaults to False — FAIL CLOSED, the
    same default ``ContactForm.can_manage_roles`` uses) is resolved by the view
    from ``marketing/access.py::access_for`` and decides whether the inline
    ``new_option`` field exists on this form AT ALL, exactly as
    ``can_manage_roles`` decides that for ``new_role``. It is removed from
    ``self.fields`` rather than merely hidden in the template, so a hand-built
    POST carrying ``new_option=...`` from an ordinary Marketing Expert is not a
    gate to re-check further down — the field simply is not part of the form,
    and its value never reaches ``cleaned_data``. See the view for the other
    half of that gate (the ``ReportOption`` row is created there, and only
    there).
    """

    def use_required_attribute(self, field):
        # Same override, same reasoning, as ``ContactForm`` above: nothing on
        # this form carries the native ``required`` attribute, because the rule
        # that actually matters here is a form-level one no browser attribute
        # can express, and a half-enforced client-side rule is worse than none —
        # it would let the browser block a legitimate "options only, no text"
        # submit while still not catching the empty one.
        return False

    options = forms.ModelMultipleChoiceField(
        queryset=ReportOption.objects.all(), required=False,
        label="What is this report about?",
        widget=forms.CheckboxSelectMultiple,
    )
    text = forms.CharField(
        required=False, label="Report",
        widget=forms.Textarea(attrs={
            "rows": 6, "dir": "auto", "autocomplete": "off",
            "placeholder": "Write the report in your own words…",
        }),
    )
    # Hidden, and carried rather than re-picked — see the class docstring.
    case_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, can_manage_options: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_manage_options = bool(can_manage_options)
        if self.can_manage_options:
            # Added here rather than declared above so that a viewer without
            # the grant has no such field at all — see the class docstring.
            # Word for word the mechanism ``ContactForm.__init__`` uses for
            # ``new_role``, because it is the same grant over the same kind of
            # list, and two different mechanisms for one capability would be
            # two chances to disagree about who holds it.
            self.fields["new_option"] = forms.CharField(
                max_length=200, required=False, label="…or add a new option",
                widget=forms.TextInput(attrs={
                    "autocomplete": "off", "dir": "auto",
                    "placeholder": "e.g. On-site visit",
                }),
            )
        # The preset list is a managed vocabulary that can legitimately be EMPTY
        # on a fresh install, exactly like ``ContactRole`` (see
        # ``marketing/models.py::ReportOption``). Unlike the contact form, an
        # empty list is not a dead end here — the free text alone is a complete
        # report — but the template still says so, so a writer who was told to
        # "pick an option" is not left hunting for a list that does not exist
        # yet. This flag is what it asks; ``ContactForm.roles_available`` is the
        # same idiom.
        self.options_available = ReportOption.objects.exists()

    def clean_new_option(self):
        """The typed new option name, stripped — never a bare duplicate.

        ``ContactForm.clean_new_role``'s rule, on the other vocabulary, and for
        its reasons. Only ever called when the field exists, i.e. when this
        viewer may manage options. ``ReportOption.name`` is globally unique (one
        shared vocabulary — see the model), so a name that already exists is not
        an error here: the view get-or-creates, and re-typing an existing option
        means the same thing as having ticked it. All this does is normalise the
        whitespace so " On-site visit " and "On-site visit" cannot become two
        rows that read identically in every checklist.
        """
        return (self.cleaned_data.get("new_option") or "").strip()

    def clean_text(self):
        """The typed report, stripped.

        Whitespace-only text is not text: without this, a stray newline would
        satisfy :meth:`clean`'s at-least-one rule and store a report that says
        nothing, which is the exact outcome that rule exists to prevent.
        """
        return (self.cleaned_data.get("text") or "").strip()

    def clean(self):
        """The one rule that is about the FORM, not about any one field.

        A REPORT HAS TO SAY SOMETHING. The owner's step 2 is "pick one or more
        preset options AND/OR type free text, then confirm" — the AND/OR is the
        point, so either alone is enough and neither alone is required, but
        NEITHER is not a report at all.

        A TYPED NEW OPTION COUNTS AS AN OPTION, because it becomes one: the
        view get-or-creates the ``ReportOption`` and attaches it to this very
        report (see ``marketing/views.py::report_create``), so a Supervisor who
        writes the report entirely out of a preset they are adding right now has
        said exactly as much as one who ticked an existing box. Read through
        ``self.can_manage_options`` and not straight off ``cleaned_data`` for
        ``ContactForm.clean``'s reason — the value is only ever there for a
        viewer who holds the grant, and the expression should say so.

        AND UNLIKE ``ContactForm``'s ROLE, TICKING BOXES *AND* TYPING A NEW
        OPTION IS NOT AN ERROR. That form rejects "picked a role AND typed one"
        because a contact holds exactly ONE title and two answers contradict
        each other; ``options`` here is a multi-select, so "these two presets,
        plus this third one I am adding" is a coherent single answer, not a
        contradiction to guess at.

        Raised as a NON-FIELD error, for the reason ``ContactForm.clean``'s
        phone/email rule is: it is true of neither field on its own, and blaming
        ``text`` would be arbitrary when ticking an option fixes it just as
        well. The template renders non-field errors the way every other form in
        this codebase does.

        Enforced HERE, on the server. Nothing in this section re-implements it
        in JavaScript, so a writer with scripting off, a stale tab, or a
        hand-built POST meets exactly this check.
        """
        cleaned = super().clean()
        options = cleaned.get("options")
        text = (cleaned.get("text") or "").strip()
        new_option = (
            (cleaned.get("new_option") or "").strip()
            if self.can_manage_options else ""
        )
        if not options and not text and not new_option:
            raise forms.ValidationError(
                "A report has to say something — tick at least one option, "
                "write the report yourself, or do both."
            )
        return cleaned


# --------------------------------------------------------------------------- #
# Reminders — one screen to set one, and the one-field form that re-times it
# --------------------------------------------------------------------------- #
# TWO FORMS, ONE DATE. The creation screen and the "set a new time" control on
# the reminders list both take a Jalali date and time, from the same picker, in
# the same text. The field and the parse are therefore written ONCE, here, and
# both forms use them — a second copy would be a second chance for the two
# boxes to disagree about what "1405-03-27 14:30" means, which is the same
# argument this codebase already makes for having one Jalali implementation.


def _due_at_field(label: str = "Remind me at (Jalali date and time)"):
    """The Jalali date-and-time box, built the same way for both forms below.

    A plain ``CharField``, NOT a ``DateTimeField``: the box is filled by
    ``static/js/jalali_picker.js``, which writes JALALI text
    ("1405-03-27 14:30"). A ``DateTimeField`` would read that as Gregorian and
    either reject it outright or — far worse — silently accept a year-1405
    date. The conversion is :func:`_clean_jalali_datetime`'s job.

    ``data-jalali-datetime`` is what attaches the picker (the same attribute
    ``cases/forms.py``'s deadline field and the archive's date filters carry),
    and ``data-required`` is how a screen in this platform marks a box as
    required given that the native attribute is deliberately not emitted — see
    ``use_required_attribute`` on the forms above.
    """
    return forms.CharField(
        required=True, label=label,
        widget=forms.TextInput(attrs={
            "data-jalali-datetime": "1", "autocomplete": "off",
            "data-required": "1",
        }),
    )


def _clean_jalali_datetime(raw):
    """The Jalali "YYYY-MM-DD HH:MM" the picker writes, as an aware datetime.

    PARSED HERE RATHER THAN BORROWED FROM ``cases/forms.py``. That module's
    ``CaseCreateForm.clean_deadline`` reads the identical text and is the
    obvious thing to reuse, but it is a bound method of a form about cases and
    it carries a rule these screens must not inherit: a deadline may not be in
    the past. A reminder legitimately may be — "remind me about this now" is a
    coherent thing to ask for, a time that has just gone by makes a reminder DUE
    rather than invalid, and refusing it would make the re-time control on the
    list page reject the fastest way to bring something back.

    WHAT IS SHARED IS THE ARITHMETIC. ``cases.jalali`` is imported and called,
    exactly as ``people/fields.py`` and ``reports/views.py`` call it, so there
    is one Jalali implementation in this codebase and this is not a second one.

    THE ROUND-TRIP CHECK IS THE POINT OF THE ``try`` BLOCK, and it is taken from
    both of those callers: ``jalali_to_gregorian`` does no range checking of its
    own, so month 13, or Esfand 30 in a year that is not a leap year, comes back
    as a real date somewhere else in the calendar rather than as an error.
    Converting the result back and comparing is what turns that into a message
    the person can act on.

    The time part is optional and defaults to midnight — the picker always
    writes one, but a value typed by hand may not, and "the 27th" is a perfectly
    clear thing to mean. An empty string returns ``None`` rather than raising;
    the FIELD's own ``required=True`` is what reports a blank box, and reporting
    it twice would put two errors under one control.
    """
    import datetime as _dt

    from django.utils import timezone

    from cases.jalali import gregorian_to_jalali, jalali_to_gregorian

    text = (raw or "").strip()
    if not text:
        return None
    try:
        date_part, _, time_part = text.partition(" ")
        # Dot, slash and dash are all accepted: the picker writes dashes, the
        # rendered stamps on every other screen print dots, and a person
        # retyping one should not have to notice the difference.
        norm = date_part.replace("/", "-").replace(".", "-")
        jy, jm, jd = (int(x) for x in norm.split("-"))
        if time_part:
            hh, mm = (int(x) for x in (time_part.split(":") + ["0", "0"])[:2])
        else:
            hh, mm = 0, 0
        if not (1 <= jm <= 12 and 1 <= jd <= 31
                and 0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("out of range")
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        if gregorian_to_jalali(gy, gm, gd) != (jy, jm, jd):
            raise ValueError("no such Jalali date")
        naive = _dt.datetime(gy, gm, gd, hh, mm)
    except (ValueError, TypeError, OverflowError):
        raise forms.ValidationError(
            "Enter the date and time as a Jalali value, e.g. 1405-03-27 14:30.")
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(naive, tz)
            if timezone.is_naive(naive) else naive)


class ReminderForm(forms.Form):
    """Set a reminder on a company: when, what about, and optionally which case.

    ONE SCREEN, NOT TWO. The report flow is a two-step wizard because the owner
    described it that way and because picking a case there is a decision the
    writer makes before they know what they are going to write. A reminder is
    three fields the person already has in their head when they press the
    button, so splitting it would be ceremony. Everything else follows the
    conventions of ``ContactForm``'s screen — a ``method="post"`` form in a
    ``.card``, ``.field`` rows, a ``.btn-row`` at the bottom.

    ``case_choices`` (keyword-only) is the list of scoped case rows the VIEW
    resolved — the very same ``marketing/views.py::_visible_case_rows`` the
    company page's Cases tab renders. It becomes this form's ``case_id``
    choices, which means an id outside that list is refused by ordinary form
    validation rather than by a hand-written check: NEVER OFFER A CASE THE
    VIEWER CANNOT OPEN is a rule this app has had to repair twice, so the
    offered list and the accepted list are deliberately the same object. The
    view re-resolves it and checks again before writing — a ``<select>`` is a
    value the browser sends, not a fact — but the form can no longer be the
    weaker of the two.

    Gating a field by ABSENCE, the way ``ContactForm`` gates ``new_role``, has
    no counterpart here: there is no shared vocabulary to extend on this screen,
    only the person's own private note.
    """

    def use_required_attribute(self, field):
        # ``ContactForm``'s override, for its reason: the case <select> below
        # carries ``data-combo`` and static/js/ui.js hides it behind a drawn
        # combo, and a hidden control carrying the native ``required`` attribute
        # makes the browser refuse to submit with an error nobody can act on.
        # Every requirement here is still enforced on the server.
        return False

    note = forms.CharField(
        required=True, label="Reminder note",
        widget=forms.Textarea(attrs={
            "rows": 4, "dir": "auto", "autocomplete": "off",
            "placeholder": "What should you do when this comes up?",
        }),
    )
    due_at = _due_at_field()
    case_id = forms.ChoiceField(
        required=False, label="About one specific case (optional)",
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": "Search a case...",
        }),
    )

    def __init__(self, *args, case_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        rows = list(case_choices or ())
        # The empty option first, because attaching a case is optional by the
        # owner's own wording and the default has to be the optional answer.
        self.fields["case_id"].choices = [("", "- No case -")] + [
            (str(row["case_id"]),
             "%s · %s · %s" % (row.get("doc_no", ""),
                               row.get("label_fa") or row.get("label", ""),
                               row.get("status_fa", "")))
            for row in rows
        ]
        # The template says so in words when this is empty: "no cases you may
        # see", which is not the same statement as "no cases exist" — the list
        # is scoped. ``ContactForm.roles_available`` is the same idiom.
        self.cases_available = bool(rows)

    def clean_note(self):
        """The typed note, stripped.

        Whitespace is not a note: without this a stray newline would satisfy
        ``required`` and store a reminder that says nothing, which is exactly
        the failure ``ReportForm.clean_text`` exists to prevent on the other
        screen.
        """
        return (self.cleaned_data.get("note") or "").strip()

    def clean_case_id(self):
        """The chosen case id as an int, or None.

        ``ChoiceField`` has already refused anything that is not one of the rows
        this viewer was offered (see the class docstring), so all that is left
        here is turning the submitted string into a number. Empty means "no
        case", which is a supported answer and not an error.
        """
        raw = (self.cleaned_data.get("case_id") or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def clean_due_at(self):
        return _clean_jalali_datetime(self.cleaned_data.get("due_at"))


class ReminderTimeForm(forms.Form):
    """Just the time — the "set a new time" control on the reminders list page.

    ONE FIELD, BUILT BY THE SAME FACTORY AND PARSED BY THE SAME FUNCTION as
    :class:`ReminderForm`'s, for the reason given at the top of this section.

    A form class rather than a bare ``request.POST`` read in the view because
    the parse CAN fail, and a failure needs somewhere to put its message: the
    list page re-renders carrying it, exactly as every other screen in this
    section does.
    """

    def use_required_attribute(self, field):
        # Same override, same reason, as the two forms above.
        return False

    due_at = _due_at_field(label="New time")

    def clean_due_at(self):
        return _clean_jalali_datetime(self.cleaned_data.get("due_at"))
