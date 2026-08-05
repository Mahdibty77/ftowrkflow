"""Turn a card spec into Django form fields, and back into stored values.

One builder serves both screens that use the spec — the administrator adding a
person, and the candidate at the kiosk — so a change to a label or an option
list happens once and shows up in both. It is also what makes the next form
cheap: describe it in ``spec.py`` and it renders, validates and stores itself.

Deliberately plain: no widget subclasses, no custom rendering. The template
walks the cards and asks each bound field for its widget, which means every
input on the page is a normal Django widget carrying the platform's own CSS
classes. Nothing here knows what anything looks like.
"""
from django import forms

from .fields import JalaliDateField
from .validators import normalize_digits


# A field's ``col`` (out of the card's twelve) as the class that expresses it.
# Three is the default and needs no class, so the common case adds no markup.
COL_CLASSES = {4: "ppl-c4", 6: "ppl-c6", 12: "ppl-wide"}


def _attrs(f, extra=None):
    a = {"autocomplete": "off"}
    if f.get("placeholder"):
        a["placeholder"] = f["placeholder"]
    if f.get("numeric"):
        a["inputmode"] = "numeric"
    if f.get("money"):
        a["inputmode"] = "numeric"
        a["data-money-input"] = "1"
        a["class"] = (a.get("class", "") + " ppl-rial-input").strip()
    if f.get("dir"):
        # A Latin name or an IBAN inside an otherwise right-to-left form needs
        # to be typed and read left to right, or the caret jumps around.
        a["dir"] = f["dir"]
    a.update(extra or {})
    return a


def build_field(f):
    """One spec field -> one Django form field (or None for kinds with no input)."""
    kind = f.get("kind", "text")
    common = {
        "label": f["label"],
        "required": bool(f.get("required")),
        "help_text": f.get("help", ""),
    }

    if kind == "textarea":
        return forms.CharField(
            widget=forms.Textarea(attrs=_attrs(f, {"rows": 3})), **common)

    if kind == "select":
        opts = f.get("options") or []
        labels = f.get("option_labels") or {}
        choices = [("", "—")] + [(o, labels.get(o, o)) for o in opts]
        return forms.ChoiceField(
            choices=choices, widget=forms.Select(attrs=_attrs(f)), **common)

    if kind == "radio":
        opts = f.get("options") or []
        return forms.ChoiceField(
            choices=[(o, o) for o in opts],
            widget=forms.RadioSelect(attrs={"class": "ppl-radio"}), **common)

    if kind == "checkbox":
        return forms.MultipleChoiceField(
            choices=[(o, o) for o in (f.get("options") or [])],
            widget=forms.CheckboxSelectMultiple(), **common)

    if kind == "number":
        return forms.IntegerField(
            min_value=0, widget=forms.NumberInput(attrs=_attrs(f)), **common)

    if kind == "date_fa":
        return JalaliDateField(**common)

    if kind == "email":
        return forms.EmailField(widget=forms.EmailInput(attrs=_attrs(f)), **common)

    if kind == "file":
        accept = f.get("accept")
        return forms.FileField(
            widget=forms.ClearableFileInput(
                attrs={"accept": accept} if accept else {}), **common)

    # text, tel and anything unrecognised
    return forms.CharField(
        max_length=f.get("max_length", 255),
        widget=forms.TextInput(attrs=_attrs(f)), **common)


def add_card_fields(form, cards, *, skip=()):
    """Add every simple field of ``cards`` to ``form``. Row tables are skipped.

    Returns the layout the template walks: a list of cards, each carrying its
    own field names, so the page can be grouped exactly as the spec describes
    rather than as one flat list of inputs.
    """
    layout = []
    for card in cards:
        entry = {"card": card, "items": [], "columns": card.get("columns")}
        for f in card.get("fields", []):
            if f["name"] in skip:
                continue
            built = build_field(f)
            if built is None:
                continue
            form.fields[f["name"]] = built
            # The presentation flags travel with the layout rather than being
            # smuggled through widget attrs, so the template can stay a plain
            # loop and never has to reach inside a field to lay it out.
            #
            # ``col`` is pre-resolved to a class name here rather than in the
            # template: Django's template language cannot build one from a
            # number, and the alternative is a chain of ifs in the markup.
            entry["items"].append({
                "name": f["name"],
                "wide": bool(f.get("wide")),
                "newline": bool(f.get("newline")),
                "col_class": COL_CLASSES.get(f.get("col"), ""),
                "heading": f.get("heading", ""),
                "group": f.get("group", ""),
            })
        layout.append(entry)
    return layout


# ---------------------------------------------------------------------------
# Row tables (education / employment / courses)
# ---------------------------------------------------------------------------
# These arrive as parallel lists — edu_level[], edu_field[], … — because that
# is what a table of repeating rows posts. Collected here into a list of dicts,
# which is what gets stored and what the template re-renders on the way back.

def collect_rows(data, card):
    """Parallel POST lists -> ``[{column: value}, …]``, blank rows dropped."""
    cols = card.get("columns") or []
    prefix = card["key"]
    series = {c["name"]: data.getlist(f"{prefix}__{c['name']}[]") for c in cols}
    length = max((len(v) for v in series.values()), default=0)

    rows = []
    for i in range(length):
        row = {}
        for c in cols:
            vals = series[c["name"]]
            v = (vals[i] if i < len(vals) else "") or ""
            v = v.strip()
            if c.get("numeric"):
                v = normalize_digits(v)
            row[c["name"]] = v
        # A row where every cell is empty is the empty row the page always
        # shows, not something the user typed. Storing it would put a blank
        # line in every printed CV.
        if any(row.values()):
            rows.append(row)
    return rows


def gather(form, card):
    """The cleaned values of one card, keyed by field name with the prefix kept."""
    out = {}
    for f in card.get("fields", []):
        if f["name"] in form.cleaned_data:
            v = form.cleaned_data[f["name"]]
            if v not in (None, "", []):
                out[f["name"]] = v
    return out
