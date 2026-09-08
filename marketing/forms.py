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

:class:`ReminderForm` is the third, and it follows the same split again — it
validates, it never writes (``marketing/reminders.py`` does) — with one thing
the other two do not need: it is one of the forms in this app that takes a
Jalali DATE AND TIME (:class:`TaskForm`, in ``my_tasks``' own section further
down, is the other). See ``_clean_jalali_datetime`` for why that parse lives
here rather than being borrowed from ``cases/forms.py``, and why it is
written once for every form that needs it.

THERE USED TO BE A FOURTH, ``ReminderTimeForm`` — "just the time", backing the
old "Set new time" control on the pre-"My Tasks" reminders list and on
``company_detail.html``'s own Reminders tab. It is gone, removed alongside
the two views that were its only callers
(``marketing/views.py::reminder_retime``/``reminder_done`` — see the comment
now sitting where they used to be defined) when those were retired as a
live bypass of this round's mandatory close-only-via-a-report cycle. A
reminder's time is no longer ever changed on its own; the only way to act on
one now is to close it out with a report
(``marketing/reminders.py::close_with_report``) and, if there is a next step,
set a brand-new one through :class:`TaskForm`.
"""
from __future__ import annotations

from itertools import zip_longest

from django import forms
from django.utils.translation import gettext_lazy as _

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
        max_length=120, required=True, label=_("First name"),
        widget=forms.TextInput(attrs={"autocomplete": "off", "dir": "auto"}),
    )
    last_name = forms.CharField(
        max_length=120, required=True, label=_("Last name"),
        widget=forms.TextInput(attrs={"autocomplete": "off", "dir": "auto"}),
    )
    # required=False at the FIELD level, required in ``clean()`` — because
    # "pick an existing role" and "type a new one" are two ways of satisfying
    # ONE requirement, and a field-level ``required=True`` here would reject a
    # supervisor who legitimately used the second one. See ``clean()``.
    role = forms.ModelChoiceField(
        queryset=ContactRole.objects.all(), required=False,
        label=_("Contact role"), empty_label=_("— Select a contact role —"),
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": _("Search contact role…"),
        }),
    )
    gender = forms.ChoiceField(
        choices=[("", _("— Select —"))] + list(ContactGender.CHOICES),
        required=True, label=_("Gender"),
    )
    # NO phone_prefix/phone/phone_ext FIELDS DECLARED HERE ANY MORE — a
    # contact can now carry any number of phone rows (see
    # ``marketing/models.py::ContactPhone``), and a fixed trio of Django
    # ``Field`` objects can bind to only one value per name each. The rows are
    # instead multiple same-named ``<input name="phone_prefix">`` /
    # ``name="phone">`` / ``name="phone_ext">`` controls the template repeats
    # once per row (``static/js/contact_phones.js`` appends and removes whole
    # rows of three), read back with the raw ``QueryDict.getlist`` in
    # :meth:`_submitted_phone_rows` below rather than through a declared
    # field — the same reason ``pasted_table`` on
    # ``cases/forms.py::CaseCreateForm`` reads
    # its grid data out of one hidden field instead of one Django field per
    # cell: the NUMBER of rows a submission carries is decided by the user in
    # the browser, not known when this class is written.
    email = forms.EmailField(
        required=False, label=_("Email"),
        widget=forms.EmailInput(attrs={"autocomplete": "off", "dir": "ltr"}),
    )

    def __init__(self, *args, can_manage_roles: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_manage_roles = bool(can_manage_roles)
        if self.can_manage_roles:
            # Added here rather than declared above so that a viewer without
            # the grant has no such field at all — see the class docstring.
            self.fields["new_role"] = forms.CharField(
                max_length=120, required=False, label=_("…or add a new role"),
                widget=forms.TextInput(attrs={
                    "autocomplete": "off", "dir": "auto",
                    "placeholder": _("e.g. Head of QC"),
                }),
            )
        # The role list is a managed vocabulary that can legitimately be EMPTY
        # on a fresh install (see ``marketing/models.py::ContactRole``), and an
        # empty required dropdown is a dead end for anyone who cannot add to
        # it. The template says so in words; this flag is what it asks.
        self.roles_available = ContactRole.objects.exists()
        # The phone rows already typed, so a validation error on some OTHER
        # field (a missing role, a bad email) re-renders the page with every
        # phone row the user had already filled in still there rather than
        # silently dropping them — the same courtesy Django gives every
        # declared field's ``value`` automatically, extended by hand to the
        # rows this form does not declare as fields. See
        # :meth:`_submitted_phone_rows` for how these are read off the raw
        # POST, and the template for how they are redrawn. An unbound form
        # (a fresh GET) has nothing submitted, so this is one empty row — a
        # single set of blank prefix/number/ext boxes to start from, exactly
        # what the old single-phone form always rendered.
        self.phone_rows = self._submitted_phone_rows() or [
            {"prefix": "", "number": "", "ext": ""},
        ]

    def _submitted_phone_rows(self) -> list[dict]:
        """The phone rows this POST carried, as a list of ``{prefix, number, ext}``.

        READS THE RAW ``QueryDict`` RATHER THAN A DECLARED FIELD, because
        there is no declared field to read: see the "NO phone_prefix/phone/
        phone_ext FIELDS" comment above ``email`` for why. The template emits
        one ``<input name="phone_prefix">`` / ``name="phone">`` /
        ``name="phone_ext">`` triple per row the user added in the browser (see
        ``static/js/contact_phones.js``), and a browser posts repeated
        same-named inputs in DOM order — so ``QueryDict.getlist`` on each of
        the three names returns three lists that line up index-for-index into
        rows, PROVIDED every row always emits all three inputs together, which
        the template does even for a box the user left blank (an empty string
        still occupies its slot in the list). ``zip`` then pairs them back up
        row by row.

        Returns ``[]`` for an unbound form (``self.data`` is the empty dict
        ``forms.Form`` uses when constructed with no data) — there is nothing
        submitted yet to read.
        """
        getlist = getattr(self.data, "getlist", None)
        if getlist is None:
            return []
        prefixes = getlist("phone_prefix")
        numbers = getlist("phone")
        exts = getlist("phone_ext")
        rows = []
        for prefix, number, ext in zip_longest(prefixes, numbers, exts, fillvalue=""):
            rows.append({
                "prefix": (prefix or "").strip(),
                "number": (number or "").strip(),
                "ext": (ext or "").strip(),
            })
        return rows

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
           neither a phone number nor an email is a name with no purpose. It
           used to be checked against the single ``phone`` field; now that a
           contact can carry any number of :class:`ContactPhone` rows (see
           :meth:`_submitted_phone_rows`), it is checked against the SAME
           thing that rule always meant — at least one row whose ``number``
           part was actually filled in, a prefix or extension typed alone
           does not count, exactly as a lone ``phone_prefix`` with no
           ``phone`` never satisfied the old single-field version either. It
           is raised as a NON-FIELD error because it is true of neither
           "phones" nor ``email`` on its own — blaming one would be arbitrary
           when filling in the other fixes it just as well — and the template
           renders non-field errors the way every other form in this codebase
           does.

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
                _("Either pick a contact role from the list or add a new one — not both."),
            )
        elif not role and not new_role:
            self.add_error("role", _("A contact role is required."))

        # Only rows with SOMETHING in them are kept — a blank row left over
        # from an "+ Add phone" click the user then abandoned must not become
        # an empty ContactPhone row on save (see ``ContactPhone``'s own
        # docstring on why the model itself does not forbid an all-blank row
        # at the database layer: the screen is where that judgement belongs).
        phones = [
            row for row in self._submitted_phone_rows()
            if row["prefix"] or row["number"] or row["ext"]
        ]
        cleaned["phones"] = phones
        has_phone_number = any(row["number"] for row in phones)
        email = (cleaned.get("email") or "").strip()
        if not has_phone_number and not email:
            raise forms.ValidationError(
                _("Enter at least one way to contact this person — a phone number "
                  "or an email address.")
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
        label=_("What is this report about?"),
        widget=forms.CheckboxSelectMultiple,
    )
    text = forms.CharField(
        required=False, label=_("Report"),
        widget=forms.Textarea(attrs={
            "rows": 6, "dir": "auto", "autocomplete": "off",
            "placeholder": _("Write the report in your own words…"),
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
                max_length=200, required=False, label=_("…or add a new option"),
                widget=forms.TextInput(attrs={
                    "autocomplete": "off", "dir": "auto",
                    "placeholder": _("e.g. On-site visit"),
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
                _("A report has to say something — tick at least one option, "
                  "write the report yourself, or do both.")
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


def _due_at_field(label=_("Remind me at (Jalali date and time)")):
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

    ``placeholder`` IS EXPLICIT HERE, not left for the picker to invent one:
    ``jalali_picker.js``'s own ``build()`` reads the real input's own
    ``placeholder`` attribute and copies it onto the visible display box it
    swaps in, but falls back to a hard-coded English "Pick a date" when that
    attribute is missing — and this field never carried one before this round,
    so that fallback text used to show up on screen regardless of which
    interface language was active. Every OTHER Jalali box on these same
    screens (the "From date"/"To date" range filters in company_detail.html,
    _my_tasks_reminders.html, _my_tasks_reports.html) already sets its own
    ``placeholder="{% trans ... %}"`` directly in the template, which is why
    only this one shared helper needed the fix. The example text mirrors
    ``_clean_jalali_datetime``'s own validation-error example below, digits
    kept as plain ASCII the same way "e.g. 021"/"e.g. 214" above do — only the
    word "e.g." itself is translated.
    """
    return forms.CharField(
        required=True, label=label,
        widget=forms.TextInput(attrs={
            "data-jalali-datetime": "1", "autocomplete": "off",
            "data-required": "1",
            "placeholder": _("e.g. 1405-03-27 14:30"),
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
            _("Enter the date and time as a Jalali value, e.g. 1405-03-27 14:30."))
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
        required=True, label=_("Reminder note"),
        widget=forms.Textarea(attrs={
            "rows": 4, "dir": "auto", "autocomplete": "off",
            "placeholder": _("What should you do when this comes up?"),
        }),
    )
    due_at = _due_at_field()
    case_id = forms.ChoiceField(
        required=False, label=_("About one specific case (optional)"),
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": _("Search a case..."),
        }),
    )

    def __init__(self, *args, case_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        rows = list(case_choices or ())
        # The empty option first, because attaching a case is optional by the
        # owner's own wording and the default has to be the optional answer.
        self.fields["case_id"].choices = [("", _("- No case -"))] + [
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


def _shift_window_text(user) -> str:
    """"HH:MM–HH:MM" for ``user``'s own work-shift window, or "" if it cannot
    be resolved (no request-less way to ask, or the lookup itself failed).

    THE SAME PAIR ``marketing/reminders.py::validate_due_at_shift`` READS,
    through the identical ``person_for_user``/``shift_window`` call — never a
    second guess at the window, only a second, human-readable RENDERING of the
    one that function already enforces. Used by :class:`TaskForm` for the
    inline hint under its own due-at field, and reused as the wording inside
    the rejection message when a submitted time falls outside it, so the hint
    a person reads BEFORE typing and the error they get AFTER typing something
    outside it can never disagree about what the window actually is.
    """
    if user is None:
        return ""
    try:
        from people.work_shift import person_for_user, shift_window
        person = person_for_user(user)
        start, end = shift_window(person)
        return "%s–%s" % (start.strftime("%H:%M"), end.strftime("%H:%M"))
    except Exception:
        return ""


class TaskForm(forms.Form):
    """Set a reminder from "My Tasks" — the platform-wide, company-INDEPENDENT
    screen every person with a linked Person record reaches from the
    sidebar's own "Personal" group, not from any one company's page. This is
    the third form built on ``ReminderForm``'s own shape, widened for the one
    thing that screen does not need and this one does: a COMPANY field of its
    own, because there is no client id in this page's URL for one to be
    implied by (see ``marketing/views.py::my_tasks``).

    THE OWNER'S OWN WORDING IS THE SPEC FOR WHAT IS OPTIONAL: "می‌تواند به اسم
    یک شرکت یا پرونده وصل کند یا حتی نه" — it may be attached to a company's
    name, or to a case, or to neither. So both ``client_id`` and ``case_id``
    are ``required=False`` here, independently of one another — picking a
    case never requires having picked a company first, and picking a company
    never requires a case. ``marketing/views.py::my_tasks_add`` is what
    decides, once both are cleaned, what the reminder's OWN company ends up
    being when a case was picked but no company was — see that view's own
    comment for the judgement call and why it is documented there rather than
    silently baked into this form's ``clean()``.

    ``case_id``'S CHOICES COME FROM ``_visible_case_rows_all``, PLATFORM-WIDE,
    NOT FROM ONE COMPANY'S CASES — the whole reason this form exists rather
    than reusing ``ReminderForm`` unchanged. The picker still NEVER offers a
    case this viewer's own commercial seat access would refuse — see
    ``marketing/views.py::_visible_case_rows_all`` and its own docstring's
    "PERMISSION RULE FOR THE CASE PICKER" section for why that authority is
    not re-derived here, and re-checked again by the view on submit, exactly
    as ``ReminderForm``'s own docstring already argues for its narrower,
    single-company version of the same list.

    THE DUE-AT TIME IS VALIDATED AGAINST ``user``'S OWN WORK-SHIFT WINDOW,
    SERVER-SIDE, in :meth:`clean_due_at` — the one rule this form owns beyond
    what ``ReminderForm``'s shared ``_clean_jalali_datetime`` already checks.
    ``marketing/reminders.py::validate_due_at_shift`` is the single decision
    (see that function's own docstring for why ``people/work_shift.py`` is
    the authority reused rather than re-derived); this method is only the
    one place a violation of it is turned into a message a person filling in
    a screen can act on, exactly the split ``ReportForm.clean`` and
    ``ContactForm.clean`` already document for their own screen-only rules.
    ``user`` is threaded through ``__init__`` rather than read off a request
    this form does not have, the same way ``can_manage_options`` reaches
    ``ReportForm``.
    """

    def use_required_attribute(self, field):
        # Same override, same reason, as ReminderForm above: the two <select>
        # fields below carry data-combo and are hidden behind a drawn combo by
        # static/js/ui.js, and a hidden control carrying the native `required`
        # attribute blocks a legitimate submit with an error nobody can act on.
        return False

    client_id = forms.ChoiceField(
        required=False, label=_("Company (optional)"),
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": _("Search a company..."),
        }),
    )
    case_id = forms.ChoiceField(
        required=False, label=_("Case (optional)"),
        widget=forms.Select(attrs={
            "data-combo": "1", "data-placeholder": _("Search a case..."),
        }),
    )
    note = forms.CharField(
        required=True, label=_("What should you do when this comes up?"),
        widget=forms.Textarea(attrs={
            "rows": 4, "dir": "auto", "autocomplete": "off",
            "placeholder": _("What should you do when this comes up?"),
        }),
    )
    due_at = _due_at_field(label=_("Due at (Jalali date and time)"))

    def __init__(self, *args, user=None, client_choices=(), case_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user
        # Empty option first for both — the same reason ReminderForm's own
        # case_id choices lead with one: neither attachment is required, and
        # the default answer has to be the one that satisfies neither.
        self.fields["client_id"].choices = [("", _("- No company -"))] + [
            (str(c.pk), c.name) for c in (client_choices or ())
        ]
        rows = list(case_choices or ())
        self.fields["case_id"].choices = [("", _("- No case -"))] + [
            (str(row["case_id"]),
             "%s · %s · %s" % (row.get("doc_no", ""),
                               row.get("client_name", ""),
                               row.get("label_fa") or row.get("label", "")))
            for row in rows
        ]
        # Said in words when empty, ReminderForm's own idiom — "no cases you
        # may see" is a statement about THIS viewer's access, never "no cases
        # exist" at all.
        self.cases_available = bool(rows)
        # Rendered as an inline hint under the due-at field — see
        # _shift_window_text's own docstring for why this is the identical
        # pair clean_due_at enforces, not a second guess at it.
        self.shift_window_text = _shift_window_text(user)

    def clean_note(self):
        return (self.cleaned_data.get("note") or "").strip()

    def clean_client_id(self):
        """The chosen company id as an int, or None — ChoiceField has already
        refused anything outside the offered rows (see ``__init__``)."""
        raw = (self.cleaned_data.get("client_id") or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def clean_case_id(self):
        """The chosen case id as an int, or None — same shape as
        ``clean_client_id``, and ``ReminderForm.clean_case_id``'s reason."""
        raw = (self.cleaned_data.get("case_id") or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def clean_due_at(self):
        """The parsed due-at, ALSO checked against ``user``'s own shift
        window — see the class docstring's "THE DUE-AT TIME IS VALIDATED"
        section. Raised on THIS field (not as a non-field error) because it
        is true of exactly one box on the screen and the person should see
        the message right where they need to fix it, the same placement
        ``_clean_jalali_datetime``'s own round-trip error already uses for a
        malformed date.

        Skipped when ``due_at`` failed to parse at all (``None``) or when this
        form was built with no ``user`` (should never happen from a real
        request; a defensive no-op rather than a crash if it ever does) — a
        blank or unparsable box already has its own error from
        ``_clean_jalali_datetime`` and does not need a second one stacked on
        top of it.
        """
        due_at = _clean_jalali_datetime(self.cleaned_data.get("due_at"))
        if due_at is not None and self._user is not None:
            from . import reminders as _reminders
            if not _reminders.validate_due_at_shift(self._user, due_at):
                window = self.shift_window_text or _shift_window_text(self._user)
                raise forms.ValidationError(
                    _("Pick a time inside your own work shift%s.") % (
                        " (%s)" % window if window else ""))
        return due_at


