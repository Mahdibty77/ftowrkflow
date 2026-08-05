"""A form field that accepts a Jalali date and stores a real date.

The platform already has a Jalali picker, but it is a date *and time* widget
that writes "YYYY-MM-DD HH:MM" — right for a deadline, wrong for a birth date,
and it lives in a shared file this module deliberately does not modify.

So a birth date is typed as text in the format people already read everywhere
else in the app (1370/05/14) and converted here, using the platform's own
conversion rather than a second implementation that could drift from it. The
value is stored as an ordinary date, which keeps it sortable, comparable and
correct for anything that later needs to compute an age or a length of service.
"""
import datetime
import re

from django import forms
from django.core.exceptions import ValidationError

from .validators import normalize_digits

_SEPARATORS = re.compile(r"[/\-.]")


class JalaliDateField(forms.Field):
    """Text in, ``datetime.date`` out."""

    widget = forms.TextInput

    default_error_messages = {
        "invalid": "Enter a Jalali date as year.month.day, for example 1370.05.14.",
        "out_of_range": "That is not a real date — check the month and day.",
    }

    def __init__(self, *args, **kwargs):
        attrs = {
            "placeholder": "1370.05.14",
            "inputmode": "numeric",
            "autocomplete": "off",
            "class": "ppl-jdate",
            "maxlength": "10",
        }
        widget = kwargs.pop("widget", None) or forms.TextInput(attrs=attrs)
        if isinstance(widget, forms.TextInput):
            widget.attrs.setdefault("placeholder", attrs["placeholder"])
            widget.attrs.setdefault("inputmode", attrs["inputmode"])
            widget.attrs.setdefault("autocomplete", attrs["autocomplete"])
            widget.attrs.setdefault("maxlength", attrs["maxlength"])
            existing = widget.attrs.get("class", "")
            if "ppl-jdate" not in existing:
                widget.attrs["class"] = (existing + " ppl-jdate").strip()
        kwargs["widget"] = widget
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        """Show a stored date back to the user in Jalali, not Gregorian."""
        if isinstance(value, datetime.date):
            from .models import format_jalali
            return format_jalali(value)
        return value

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, datetime.date):
            return value

        # Split FIRST, normalise each piece after. Doing it the other way round
        # is a trap: normalize_digits removes separators, so by the time the
        # split ran there was nothing left to split on and "1370/5/14" — the
        # obvious way to type it — came back as one 7-digit blob and was
        # rejected with a message telling the user to type what they had just
        # typed. Only a fully zero-padded value could ever have got through.
        parts = [normalize_digits(p) for p in _SEPARATORS.split(str(value).strip()) if p.strip()]

        # Also accept a run of 8 digits with no separators at all (13700514),
        # since that is what someone typing quickly on a numeric keypad
        # produces and rejecting it would be pedantic rather than helpful.
        if len(parts) == 1 and len(parts[0]) == 8 and parts[0].isdigit():
            d = parts[0]
            parts = [d[0:4], d[4:6], d[6:8]]
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValidationError(self.error_messages["invalid"], code="invalid")

        jy, jm, jd = (int(p) for p in parts)
        if not (1200 <= jy <= 1600) or not (1 <= jm <= 12) or not (1 <= jd <= 31):
            raise ValidationError(self.error_messages["out_of_range"], code="out_of_range")

        try:
            from cases.jalali import gregorian_to_jalali, jalali_to_gregorian
            gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
            result = datetime.date(gy, gm, gd)
            # Round-trip check. The conversion is arithmetic and will happily
            # accept 1370/12/31 in a year where Esfand has 29 days, silently
            # producing the wrong day rather than an error. Converting back and
            # comparing is what turns that into a message the user can act on.
            back = gregorian_to_jalali(result.year, result.month, result.day)
            if tuple(back) != (jy, jm, jd):
                raise ValidationError(
                    self.error_messages["out_of_range"], code="out_of_range")
        except ValidationError:
            raise
        except Exception:
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return result
