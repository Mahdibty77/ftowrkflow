"""Forms for case creation and the commercial master-data screens."""
from django import forms

from core.persian_text import normalize_persian

from .constants import DocKind, MarketingLabel, OfferType, PriceType
from .models import Client, ExpertCode


class CaseCreateForm(forms.Form):
    """Open a new case.

    Line-item rows are supplied either by pasting a 4-column table or by
    uploading an Excel file; both are parsed in the view, so this form only
    covers the case header fields.
    """

    def use_required_attribute(self, field):
        # The required <select>s are visually replaced by a search combo that
        # hides the native control with display:none. A hidden control carrying
        # the native HTML ``required`` attribute makes the browser abort submit
        # with "An invalid form control is not focusable" *before* any JS submit
        # handler runs — so our own validator never fires. Returning False keeps
        # server-side ``required=True`` validation but stops Django from emitting
        # the native ``required`` attribute, letting our JS validator draw the
        # red borders and banner instead.
        return False

    kind = forms.ChoiceField(
        choices=[("", "— Select document kind —")] + list(DocKind.CHOICES),
        label="Document kind", required=True,
        widget=forms.Select(attrs={"data-combo": "1", "data-placeholder": "Search document kind…", "data-required": "1"}),
    )
    offer_type = forms.ChoiceField(
        choices=[("", "— Select offer type —")] + list(OfferType.CHOICES),
        label="Offer type", required=True,
        widget=forms.Select(attrs={"data-combo": "1", "data-placeholder": "Search offer type…", "data-required": "1"}),
    )
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(), label="Client", required=True,
        empty_label="— Select client —",
        widget=forms.Select(attrs={"data-combo": "1", "data-placeholder": "Search client by name or code…", "data-required": "1"}),
    )
    # REQUIRED AT CREATE TIME ONLY — a deliberate asymmetry with the two
    # edit-save paths in cases/views.py (the contacts-only edit and the
    # fresh-draft full edit), which both keep accepting a blank value exactly
    # as before, and keep writing it straight off ``request.POST`` with no
    # form behind them at all. The owner's instruction was narrow: force an
    # explicit pick the moment a case is opened, so a NEW case can no longer
    # sail through without anyone having thought about which business role
    # its client plays. It says nothing about the two edit paths, which exist
    # partly to let an older, pre-this-requirement case (opened back when the
    # field was optional, or a fresh draft someone genuinely has not gotten
    # to yet) keep saving with the field left blank — the OWNER fallback
    # (``marketing/services.py::_effective_label``) still has to keep working
    # for those.
    #
    # STILL CARRIES A LEADING BLANK CHOICE, even though it is now
    # ``required=True`` — this is NOT the same thing as the old
    # ``required=False`` + "— Not specified —" pair it replaces, and the
    # difference is exactly what makes this genuinely mandatory rather than
    # mandatory-looking. The combo widget (``static/js/ui.js::buildCombo``)
    # reads the underlying ``<select>``'s CURRENT value to decide what the
    # visible search box shows (``setFromValue()``); a bare HTML ``<select>``
    # with no explicit blank ``<option>`` defaults its value to the FIRST
    # real option the instant it renders — here, SPONSOR — which would make
    # the field open pre-filled with a role nobody chose, satisfy
    # ``data-required``'s client-side check without the user ever touching
    # it, and (since "sponsor" is a perfectly valid ``ChoiceField`` value)
    # sail straight through server-side validation too: a mandatory-LOOKING
    # field that is actually impossible to leave unanswered incorrectly. The
    # placeholder choice is what keeps the field's true initial state blank —
    # exactly the same reason ``kind``/``offer_type``/``client`` above each
    # carry one, and the identical mechanism ``required=True`` already uses
    # on all three: Django's ``Field.validate()`` rejects an empty value
    # against ``required`` BEFORE ``ChoiceField`` ever checks whether that
    # value is one of its listed choices, so "" being technically present in
    # ``choices`` (for the widget's sake) never lets it pass.
    marketing_label = forms.ChoiceField(
        choices=[("", "— Select marketing role —")] + list(MarketingLabel.CHOICES),
        label="Marketing role", required=True,
        widget=forms.Select(attrs={"data-combo": "1", "data-placeholder": "Search role…", "data-required": "1"}),
    )
    order_no = forms.CharField(
        max_length=80, required=True,
        label="Order No. / Project Name",
        widget=forms.TextInput(attrs={
            "data-required": "1", "autocomplete": "off", "autocapitalize": "off",
            "spellcheck": "false",
        }),
    )
    client_commercial_expert = forms.CharField(
        max_length=120, required=False, label="Client commercial contact",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "autocapitalize": "off", "spellcheck": "false",
        }))
    client_commercial_phone = forms.CharField(
        max_length=40, required=False, label="Client commercial phone (optional)",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "autocapitalize": "off", "spellcheck": "false",
        }))
    client_technical_expert = forms.CharField(
        max_length=120, required=False, label="Client technical contact",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "autocapitalize": "off", "spellcheck": "false",
        }))
    client_technical_phone = forms.CharField(
        max_length=40, required=False, label="Client technical phone (optional)",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "autocapitalize": "off", "spellcheck": "false",
        }))
    price_type = forms.ChoiceField(
        choices=[("", "— Select price type —")] + list(PriceType.CHOICES),
        label="Price type", required=True,
        widget=forms.Select(attrs={"data-combo": "1", "data-placeholder": "Search price type…", "data-required": "1"}),
    )
    deadline = forms.CharField(
        # A commercial case is worked against its deadline — it is what the
        # inbox orders on and what every unit plans around — so a case may not
        # be opened without one. ``data-required`` is what the New Case screen's
        # own validator looks for (see the note on ``use_required_attribute``
        # above for why the native attribute is not emitted), so this field is
        # marked exactly like client / order no. / kind.
        required=True, label="Deadline (Jalali)",
        widget=forms.TextInput(attrs={
            "data-jalali-datetime": "1", "autocomplete": "off", "data-required": "1",
        }),
    )
    pasted_table = forms.CharField(
        required=False, widget=forms.HiddenInput,
        help_text="JSON rows captured from the paste grid.",
    )
    excel_file = forms.FileField(required=False, label="Or upload Excel (Item, Description, Size, Unit)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Client options read "Name (code)"; the code only feeds the doc number.
        self.fields["client"].label_from_instance = lambda obj: f"{obj.name} ({obj.code})"

    def clean_client_technical_phone(self):
        # Keep the number exactly as the user typed it (no grouping/stripping).
        return (self.cleaned_data.get("client_technical_phone") or "").strip()

    def clean_client_commercial_phone(self):
        return (self.cleaned_data.get("client_commercial_phone") or "").strip()

    def clean_deadline(self):
        """Parse a Jalali 'YYYY-MM-DD[ HH:MM]' string into an aware datetime."""
        import datetime as _dt
        from django.utils import timezone
        from .jalali import gregorian_to_jalali, jalali_to_gregorian

        raw = (self.cleaned_data.get("deadline") or "").strip()
        if not raw:
            return None
        try:
            date_part, _, time_part = raw.partition(" ")
            # Accept dot, slash or dash as the date separator (1405.03.27,
            # 1405/03/27 or 1405-03-27 all work).
            norm = date_part.replace("/", "-").replace(".", "-")
            jy, jm, jd = [int(x) for x in norm.split("-")]
            hh, mm = (int(x) for x in (time_part.split(":") + ["0", "0"])[:2]) if time_part else (0, 0)
            if not (1 <= jm <= 12 and 1 <= jd <= 31 and 0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError("out of range")
            gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
            # jalali_to_gregorian does no range checking of its own: hand it
            # month 13 or day 45 and it returns a real date somewhere past the
            # end of the year rather than complaining, so "1406-13-45" was
            # accepted and silently stored as a day the user never chose. The
            # bounds above catch the obvious nonsense; converting back catches
            # the rest — a day that does not exist in that month, such as 12-30
            # in a year that is not a leap year, comes back as a different date.
            if gregorian_to_jalali(gy, gm, gd) != (jy, jm, jd):
                raise ValueError("no such Jalali date")
            naive = _dt.datetime(gy, gm, gd, hh, mm)
        except (ValueError, TypeError, OverflowError):
            raise forms.ValidationError("Enter the deadline as a Jalali date, e.g. 1405-03-27 14:30.")
        tz = timezone.get_current_timezone()
        aware = timezone.make_aware(naive, tz) if timezone.is_naive(naive) else naive
        if aware < timezone.now():
            raise forms.ValidationError("The deadline cannot be earlier than the current date and time.")
        return aware

    def clean(self):
        cleaned = super().clean()
        # The deadline is mandatory: an empty box is refused by the field itself
        # and a past date by clean_deadline, so there is nothing left to decide
        # across fields here.
        return cleaned


class ClientForm(forms.ModelForm):
    """Add a new client (code is assigned automatically)."""

    class Meta:
        model = Client
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        # A plain name__iexact only catches an EXACT (modulo case) match —
        # two names differing only by a Persian/Arabic letter variant or by
        # whitespace (extra/missing spaces, a run-together word) would both
        # slip past it and create a real duplicate client. Compare normalized
        # forms instead, the same way marketing/services.py's own
        # get_or_create_client and search_clients already do for the
        # Marketing-side "add a company" flows — see core.persian_text for
        # what normalize_persian folds away. The table is small (hundreds of
        # rows), so a Python-side scan is the same acceptable trade-off those
        # functions already made.
        needle = normalize_persian(name).lower()
        if any(normalize_persian(c.name).lower() == needle for c in Client.objects.all()):
            raise forms.ValidationError("A client with this name already exists.")
        return name


class ClientRenameForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name"]


class ExpertCodeForm(forms.ModelForm):
    class Meta:
        model = ExpertCode
        fields = ["code", "name", "user"]


class CommentForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}),
                              label="Comment")


class TransitionForm(forms.Form):
    """Generic transition form carrying an optional comment + the action key."""
    action = forms.CharField(widget=forms.HiddenInput)
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    assignee = forms.IntegerField(required=False, widget=forms.HiddenInput)
