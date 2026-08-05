"""Forms for the people directory."""
from django import forms
from django.db import transaction

from . import spec
from .constants import PersonStatus
from .formbuild import add_card_fields, collect_rows
from .models import Person
from .validators import normalize_digits, validate_iban, validate_national_id


class PersonForm(forms.Form):
    """Add or edit a person, as an administrator.

    Not a ModelForm. The screen is a set of cards from ``spec.py`` and roughly
    half of what it collects is stored as JSON rather than as columns. Driving
    it from the spec keeps one definition of what a person record contains; a
    ModelForm would need the field list restated here and would drift the first
    time the form changed.

    Four things are deliberately NOT on this form:

    *The detail code*, which is assigned on first save and must never be
    editable, since everything downstream keys on it.

    *The photograph and the CV.* An administrator entering forty people is
    entering what they were handed on paper; the files come from the person
    themselves later, on their own screen. Two upload boxes per person that are
    left empty every time are two boxes making the form longer.

    *Employment status.* It is a state, not a fact to be typed — marking
    somebody departed also has to set the leaving date, and a dropdown that
    quietly disagrees with that date is how records end up self-contradictory.
    It is a button on the people list instead, with its own confirmation.

    *The account link.* One person may hold several seats, which is not
    something a single dropdown on a long form can express. It has a page.
    """

    CARDS = spec.ADMIN_PERSON_CARDS
    ROW_CARDS = [c for c in CARDS if c.get("kind") == "rows"]
    # Described in the spec, never rendered here — see the class docstring.
    SKIP_FIELDS = ("p_photo", "p_resume")

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        self.layout = add_card_fields(self, self.CARDS, skip=self.SKIP_FIELDS)

        # Guarantees deposited with the company (replaces the old employment-status card).
        self.fields["guarantee_type"] = forms.ChoiceField(
            label="نوع تضمین", required=False,
            choices=[("", "—")] + list(Person.GUARANTEE_CHOICES),
        )
        self.fields["guarantee_amount"] = forms.CharField(
            label="مبلغ تضمین", required=False,
            widget=forms.TextInput(attrs={
                "class": "ppl-rial-input", "dir": "ltr",
                "inputmode": "numeric", "autocomplete": "off",
                "placeholder": "۰",
                "data-money-input": "1",
            }),
            help_text="مبلغ به ریال؛ هنگام تایپ سه‌رقم‌سه‌رقم جدا می‌شود.",
        )
        self.fields["children_count"] = forms.CharField(
            label="تعداد فرزندان", required=False,
            widget=forms.TextInput(attrs={
                "dir": "ltr", "inputmode": "numeric", "autocomplete": "off",
                "placeholder": "۰", "style": "max-width:6rem;",
            }),
        )
        self.layout.append({
            "card": {
                "key": "guarantees", "index": 13,
                "title": "اسناد و تضامین سپرده به شرکت",
                "note": "در حال حاضر نوع تضمین سفته است.",
            },
            "items": [
                {"name": "guarantee_type", "wide": False, "newline": False,
                 "col_class": "ppl-c4", "heading": "", "group": ""},
                {"name": "guarantee_amount", "wide": False, "newline": False,
                 "col_class": "ppl-c6", "heading": "", "group": ""},
            ],
            "columns": None,
        })

        # Editable only after the person exists — shown on the Record card.
        if instance is not None:
            self.fields["p_internal_code"] = forms.CharField(
                label="Internal code",
                required=False,
                max_length=3,
                widget=forms.TextInput(attrs={
                    "class": "mono",
                    "dir": "ltr",
                    "inputmode": "numeric",
                    "autocomplete": "off",
                    "placeholder": "001",
                    "maxlength": "3",
                    "style": "max-width:6rem;",
                }),
            )
            for name, value in initial_from_person(instance).items():
                self.initial.setdefault(name, value)
            self.rows_initial = {
                c["key"]: getattr(instance, c["key"], None) or [] for c in self.ROW_CARDS
            }
        else:
            self.rows_initial = {c["key"]: [] for c in self.ROW_CARDS}

        # On a failed submission the rows must come back from what was typed,
        # not from the database. Otherwise every education and job line the
        # administrator had just entered disappears the moment any other field
        # on the page is wrong.
        if self.is_bound:
            self.rows_initial = {
                c["key"]: collect_rows(self.data, c) for c in self.ROW_CARDS
            }

    # -- validation ------------------------------------------------------
    def clean_p_national_id(self):
        value = self.cleaned_data.get("p_national_id")
        cleaned = validate_national_id(value) if value else ""
        if not cleaned:
            return ""
        clash = Person.objects.filter(national_id=cleaned)
        if self.instance is not None and self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        other = clash.first()
        if other is not None:
            # Naming the clashing record turns "fix this" into something the
            # administrator can act on: a duplicate here almost always means
            # the person is already on file under a different spelling.
            raise forms.ValidationError(
                f"این کد ملی قبلاً برای {other.full_name} "
                f"(کد تفصیلی {other.detail_code}) ثبت شده است.")
        return cleaned

    def clean_fin_iban(self):
        return validate_iban(self.cleaned_data.get("fin_iban", ""))

    def clean_fin_card_number(self):
        v = normalize_digits(self.cleaned_data.get("fin_card_number", ""))
        if v and (not v.isdigit() or len(v) != 16):
            raise forms.ValidationError("شماره کارت باید ۱۶ رقم باشد.")
        return v

    def clean_p_mobile(self):
        return normalize_digits(self.cleaned_data.get("p_mobile", ""))

    def clean_p_phone(self):
        return normalize_digits(self.cleaned_data.get("p_phone", ""))

    def clean_p_internal_code(self):
        raw = normalize_digits(self.cleaned_data.get("p_internal_code", "") or "")
        raw = raw.replace(" ", "").strip()
        if not raw:
            return ""
        if not raw.isdigit() or len(raw) > 3:
            raise forms.ValidationError("کد داخلی باید حداکثر سه رقم باشد (مثلاً 001).")
        return raw.zfill(3)

    def clean_guarantee_amount(self):
        raw = normalize_digits(self.cleaned_data.get("guarantee_amount", "") or "")
        raw = raw.replace(",", "").replace("٬", "").replace(" ", "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise forms.ValidationError("مبلغ تضمین باید عدد صحیح ریال باشد.")
        from decimal import Decimal
        return Decimal(raw)

    def clean_children_count(self):
        raw = normalize_digits(self.cleaned_data.get("children_count", "") or "")
        raw = raw.replace(",", "").replace("٬", "").replace(" ", "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise forms.ValidationError("تعداد فرزندان باید عدد باشد.")
        return int(raw)

    def clean(self):
        cleaned = super().clean()

        # The sign-in name is built from the Latin spelling, so it has to be
        # there before a person can be saved — otherwise the account screen
        # later has nothing to offer and the administrator has to come back.
        if not (cleaned.get("p_first_name_en") or "").strip():
            self.add_error("p_first_name_en", "برای ساخت نام کاربری لازم است.")
        if not (cleaned.get("p_last_name_en") or "").strip():
            self.add_error("p_last_name_en", "برای ساخت نام کاربری لازم است.")

        gender = (cleaned.get("p_gender") or "").strip()
        if gender == "خانم":
            cleaned["p_military"] = ""
        marital = (cleaned.get("p_marital") or "").strip()
        if marital != "متأهل":
            cleaned["children_count"] = None
        return cleaned

    # -- saving ----------------------------------------------------------
    @transaction.atomic
    def save(self, *, actor=None, post=None) -> Person:
        d = self.cleaned_data
        person = self.instance or Person()

        person.first_name = d.get("p_first_name", "")
        person.last_name = d.get("p_last_name", "")
        person.first_name_en = (d.get("p_first_name_en") or "").strip()
        person.last_name_en = (d.get("p_last_name_en") or "").strip()
        person.father_name = d.get("p_father_name", "")
        person.id_number = normalize_digits(d.get("p_id_number", ""))
        person.national_id = d.get("p_national_id") or None
        person.internal_code = (d.get("p_internal_code") or "").strip()
        person.birth_date = d.get("p_birth_date")
        person.birth_place = d.get("p_birth_place", "")
        person.gender = d.get("p_gender", "")
        person.marital = d.get("p_marital", "")
        person.military = d.get("p_military", "") if person.gender != "خانم" else ""
        person.city = d.get("p_city", "")
        person.mobile = d.get("p_mobile", "")
        person.phone = d.get("p_phone", "")
        person.email = d.get("p_email", "")
        person.address = d.get("p_address", "")
        if person.marital == "متأهل":
            person.children_count = d.get("children_count")
        else:
            person.children_count = None
            person.children = []

        person.card_number = d.get("fin_card_number", "")
        person.iban = d.get("fin_iban", "")
        person.bank_name = d.get("fin_bank", "")
        person.account_holder = d.get("fin_account_holder", "")

        # Status is not read from this form — it is owned by the activate /
        # deactivate button on the list. A new person starts Active, which is
        # the model default, and an edit leaves whatever the button last set.
        gtype = (d.get("guarantee_type") or "").strip()
        person.guarantee_type = gtype
        person.guarantee_amount = d.get("guarantee_amount")

        person.request = {k: v for k, v in {
            "field": d.get("req_field", ""), "title": d.get("req_title", ""),
            "referral": d.get("req_referral", ""), "salary": d.get("req_salary", ""),
            "available_from": str(d.get("req_available_from") or ""),
            "worktype": d.get("req_worktype", ""),
        }.items() if v}
        person.motivation = {k: v for k, v in {
            "reason": d.get("mot_reason", ""), "interests": d.get("mot_interests", ""),
            "priorities": [d.get(f"mot_priority{i}", "") for i in (1, 2, 3)],
        }.items() if v and v != ["", "", ""]}
        person.references = {k: v for k, v in {
            "ref1": {"name": d.get("ref1_name", ""), "relation": d.get("ref1_relation", ""),
                     "phone": normalize_digits(d.get("ref1_phone", ""))},
            "ref2": {"name": d.get("ref2_name", ""), "relation": d.get("ref2_relation", ""),
                     "phone": normalize_digits(d.get("ref2_phone", ""))},
            "emergency": {"name": d.get("emg_name", ""), "relation": d.get("emg_relation", ""),
                          "phone": normalize_digits(d.get("emg_phone", ""))},
        }.items() if any(v.values())}

        if post is not None:
            for card in self.ROW_CARDS:
                rows = collect_rows(post, card)
                if card["key"] == "children" and person.marital != "متأهل":
                    rows = []
                setattr(person, card["key"], rows)

        if actor is not None and person.pk is None:
            person.created_by = actor

        # Person.save() rebuilds `username` from the Latin spelling, so by the
        # time this returns the person's own handle is already current.
        person.save()

        # And every seat they hold is brought up to date with it. Correcting a
        # misspelt name has to reach the accounts too, or the platform goes on
        # printing last week's spelling on everything that account signs — the
        # stale-name problem this module exists to end, arriving by the back
        # door. Renaming is done here, on a deliberate save of the person form,
        # and never from the model's save().
        from .seats import refresh_person_seats
        refresh_person_seats(person)

        return person


def _iso_to_date(value):
    """"2026-08-01" -> date(2026, 8, 1); anything unparseable -> None."""
    import datetime
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def initial_from_person(person) -> dict:
    """Person record -> the flat field names the spec form uses."""
    req = person.request or {}
    mot = person.motivation or {}
    refs = person.references or {}
    pri = mot.get("priorities") or ["", "", ""]
    pri = list(pri) + ["", "", ""]

    out = {
        "p_first_name": person.first_name, "p_last_name": person.last_name,
        "p_first_name_en": person.first_name_en, "p_last_name_en": person.last_name_en,
        "p_father_name": person.father_name, "p_id_number": person.id_number,
        "p_national_id": person.national_id or "",
        "p_internal_code": person.internal_code or "",
        "p_birth_date": person.birth_date,
        "p_birth_place": person.birth_place, "p_gender": person.gender,
        "p_marital": person.marital, "p_military": person.military,
        "p_city": person.city, "p_mobile": person.mobile, "p_phone": person.phone,
        "p_email": person.email, "p_address": person.address,
        "fin_card_number": person.card_number, "fin_iban": person.iban,
        "fin_bank": person.bank_name, "fin_account_holder": person.account_holder,
        "guarantee_type": person.guarantee_type or "",
        "guarantee_amount": (
            str(int(person.guarantee_amount))
            if person.guarantee_amount is not None else ""
        ),
        "children_count": (
            str(person.children_count)
            if person.children_count is not None else ""
        ),
        "req_field": req.get("field", ""), "req_title": req.get("title", ""),
        "req_referral": req.get("referral", ""), "req_salary": req.get("salary", ""),
        "req_worktype": req.get("worktype", ""),
        # Stored as an ISO string but edited as a Jalali date, so it has to go
        # back as a real date object — handing the raw "2026-08-01" to the
        # Jalali field would have it read 2026 as a Jalali year and reject it.
        "req_available_from": _iso_to_date(req.get("available_from", "")),
        "mot_reason": mot.get("reason", ""), "mot_interests": mot.get("interests", ""),
        "mot_priority1": pri[0], "mot_priority2": pri[1], "mot_priority3": pri[2],
    }
    for key, prefix in (("ref1", "ref1"), ("ref2", "ref2"), ("emergency", "emg")):
        block = refs.get(key) or {}
        out[f"{prefix}_name"] = block.get("name", "")
        out[f"{prefix}_relation"] = block.get("relation", "")
        out[f"{prefix}_phone"] = block.get("phone", "")
    return out


class PersonSearchForm(forms.Form):
    """The filter row above the people list.

    English, unlike the rest of this module: it is application chrome sitting
    directly above a table whose headings, pager and buttons are English, in a
    sidebar that is English. The Persian scope starts at the form itself.

    No submit button — the list narrows as you type. Everything here is
    optional and everything degrades to a plain GET form if scripting is off.
    """

    q = forms.CharField(
        required=False, label="Search",
        widget=forms.TextInput(attrs={
            "placeholder": "Detail code, name or username…",
            "autocomplete": "off", "spellcheck": "false"}))
    status = forms.ChoiceField(
        required=False, label="Status",
        choices=[("", "All")] + PersonStatus.CHOICES)
    seats = forms.ChoiceField(
        required=False, label="Seats",
        choices=[("", "All"), ("yes", "Holds a seat"), ("no", "No seat")])
