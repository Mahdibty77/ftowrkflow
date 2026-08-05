"""Forms for case creation and the commercial master-data screens."""
from django import forms

from .constants import DocKind, OfferType, PriceType
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
        required=False, label="Deadline (Jalali) — optional",
        widget=forms.TextInput(attrs={
            "data-jalali-datetime": "1", "autocomplete": "off",
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
        from .jalali import jalali_to_gregorian

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
            gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
            naive = _dt.datetime(gy, gm, gd, hh, mm)
        except (ValueError, TypeError):
            raise forms.ValidationError("Enter the deadline as a Jalali date, e.g. 1405-03-27 14:30.")
        tz = timezone.get_current_timezone()
        aware = timezone.make_aware(naive, tz) if timezone.is_naive(naive) else naive
        if aware < timezone.now():
            raise forms.ValidationError("The deadline cannot be earlier than the current date and time.")
        return aware

    def clean(self):
        cleaned = super().clean()
        # The deadline is always optional; only a past date is rejected
        # (handled in clean_deadline).
        return cleaned


class ClientForm(forms.ModelForm):
    """Add a new client (code is assigned automatically)."""

    class Meta:
        model = Client
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Client.objects.filter(name__iexact=name).exists():
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
