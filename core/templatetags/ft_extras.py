"""Small, dependency-free template helpers used across the platform."""
from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def dictget(value, key):
    """Look up ``value[key]`` inside a template (variable keys are not
    otherwise possible with the dot syntax). Returns an empty string when the
    key is missing or the value is not a mapping."""
    if isinstance(value, dict):
        return value.get(key, "")
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return ""


@register.filter
def get_item(value, key):
    """Alias of :func:`dictget` for readability in some templates."""
    return dictget(value, key)


# --- Jalali (Shamsi) date/time display -------------------------------------
import datetime as _dt
import re as _re

from django.utils import timezone as _tz

_JMONTHS = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
            "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"]


@register.filter
def jalali(value, fmt="Y.m.d H:i"):
    """Render any date/datetime as an exact Jalali (Shamsi) string.

    Dates are stored in UTC; here we first convert timezone-aware values to the
    project's local timezone (Asia/Tehran) so the printed date AND time are
    exact, then convert the calendar to Jalali. Tokens: Y m d (numeric),
    M (month name), H i (hour/minute). The default date separator is a dot
    (e.g. 1405.02.05). Replacement is done in a single pass so letters inside a
    month name are never mangled.
    """
    if not value:
        return "—"
    if isinstance(value, _dt.datetime) and _tz.is_aware(value):
        value = _tz.localtime(value)
    from cases.jalali import gregorian_to_jalali
    try:
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
    except (AttributeError, ValueError):
        return value
    tokens = {
        "Y": str(jy),
        "m": f"{jm:02d}",
        "d": f"{jd:02d}",
        "M": _JMONTHS[jm - 1],
        "H": f"{getattr(value, 'hour', 0):02d}",
        "i": f"{getattr(value, 'minute', 0):02d}",
    }
    pattern = _re.compile("|".join(sorted(map(_re.escape, tokens), key=len, reverse=True)))
    return pattern.sub(lambda m: tokens[m.group(0)], fmt)


@register.filter
def featdisp(value):
    """Display a feature value: upper-cased, with the 'no/absent' variant
    ('$coating$' or '(no)coating') rendered as 'NO COATING'."""
    s = str(value or "").strip()
    m = _re.match(r"^\(no\)\s*(.*)$", s, _re.I)
    if m:
        return "NO " + (m.group(1) or "").upper()
    d = _re.match(r"^\$(.*)\$$", s)
    if d:
        return "NO " + (d.group(1) or "").upper()
    return s.upper()


@register.filter(name="phone_fmt")
def phone_fmt(value):
    """Group a phone number for readability, e.g. 09035847574 -> 0903 584 7574.

    Iranian mobiles (11 digits starting 09) are grouped 4-3-4. Other lengths are
    grouped from the right in blocks of 3-4 so any number stays readable.
    """
    if not value:
        return ""
    digits = _re.sub(r"\D", "", str(value))
    if not digits:
        return str(value)
    if len(digits) == 11 and digits.startswith("0"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    # Generic: split into 3-4 digit groups from the left.
    groups = [digits[i:i + 4] for i in range(0, len(digits), 4)]
    return " ".join(groups)


# --- Saved TO/PI forms: show exactly the tool's visible columns + titles ------
# Canonical column name -> display title (single source mirrors
# itemcoder.table_layout_manager.COLUMN_TITLES). Hidden helper columns are never
# shown; ALARM/REVISION are TO-only, the price columns are PI-only.
_FORM_COL_TITLES = {
    "Item Code": "Item Code", "کد": "FTCO CODE", "description": "CLIENT DISCRIPTION",
    "Description": "CLIENT DISCRIPTION",
    "size": "SIZE", "qty": "QTY", "unit": "UNIT",
    "Final Arranged Text": "FTCO DISCRIPTION", "Alarm_Features": "ALARM",
    "BRAND": "BRAND",
    "اصلاحیه": "REVISION", "ریمارک": "REMARK",
    "UNIT PRICE": "UNIT PRICE", "SERVICE PRICE": "UNIT SVC PRICE", "TOTAL PRICE": "TOTAL PRICE",
}
_FORM_HIDDEN = {"Group", "Type", "Filled_Features", "وزن", "Weight"}
_FORM_PI_HIDE = {"Alarm_Features", "اصلاحیه"}
# In a SAVED Technical Offer, ALARM is for the live tool only — hide it outside.
_FORM_TO_HIDE = {"UNIT PRICE", "TOTAL PRICE", "SERVICE PRICE", "Alarm_Features"}


def _norm_row_key(v) -> str:
    """Normalise a client row (#) for matching (int-like -> '1', keeps others)."""
    s = str(v if v is not None else "").strip()
    if not s:
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


@register.filter
def to_proforma_remarks(form_obj):
    """For a saved Technical Offer, return {client_row(#): latest proforma remark}
    from the case's CURRENT Proforma (same side). Empty dict when there is no PI
    or no remarks — so the read-only TO table only grows a PROFORMA REMARK column
    when there is something to show."""
    if (getattr(form_obj, "kind", "") or "").upper() != "TO":
        return {}
    case = getattr(form_obj, "case", None)
    if case is None:
        return {}
    from cases.constants import FormKind
    pi = case.current_form(FormKind.PI, getattr(form_obj, "side", "") or None)
    out = {}
    if pi and pi.table:
        for r in (pi.table or []):
            cr = _norm_row_key(r.get("#", r.get("client_row", "")))
            rem = str(r.get("ریمارک", "") or "").strip()
            if cr and rem:
                out[cr] = rem
    return out


@register.filter
def pf_remark_of(pf_map, row):
    """Latest proforma remark for a saved TO row (matched by #). '' when none."""
    if not pf_map or not isinstance(row, dict):
        return ""
    cr = _norm_row_key(row.get("#", row.get("client_row", "")))
    return pf_map.get(cr, "")


@register.filter
def visible_form_columns(form_obj):
    """Return [(title, canonical_key), ...] for the columns a saved TO/PI should
    show — same set/titles/order as the live tool, dropping helper columns and
    the columns the other form owns."""
    cols = list(getattr(form_obj, "columns", []) or [])
    kind = (getattr(form_obj, "kind", "") or "").upper()
    hide = set(_FORM_HIDDEN) | (_FORM_PI_HIDE if kind == "PI" else _FORM_TO_HIDE)
    # PI: ensure UNIT SVC PRICE appears when any row has a real service comment,
    # even if an older save omitted the column from ``columns``.
    if kind == "PI" and "SERVICE PRICE" not in cols:
        has_svc = False
        for row in (getattr(form_obj, "table", None) or []):
            if not isinstance(row, dict):
                continue
            c = str(row.get("_service_comment", "") or "").strip()
            if c and c.lower() not in ("nan", "none", "<na>", "null"):
                has_svc = True
                break
        if has_svc:
            if "UNIT PRICE" in cols:
                i = cols.index("UNIT PRICE") + 1
                cols = cols[:i] + ["SERVICE PRICE"] + cols[i:]
            else:
                cols = list(cols) + ["SERVICE PRICE"]
    pairs = []
    for c in cols:
        if c in hide:
            continue
        title = _FORM_COL_TITLES.get(c, c)
        if kind == "PI" and c == "ریمارک":
            title = "PROFORMA REMARK"
        pairs.append((title, c))
    return pairs


@register.filter
def clean_cell_display(value):
    """Strip NaN and float decimals (1.0 → 1) for read-only tables."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


@register.filter
def hash_display(value):
    """Show client row # without embedded +/- suffixes (marks are separate)."""
    s = clean_cell_display(value)
    if not s:
        return ""
    import re
    return re.sub(r"[\s−+\-]+$", "", s).strip()


@register.filter
def ftco_desc_display(value):
    """Plain-text FTCO description for the read-only TO/PI tabs.

    The saved value may carry the coder's colour ``<span>`` markup (kept so the
    TO tool shows colours on edit). In the case-detail tabs we want plain black
    text only — colours belong to the tool, not the read-only table — so strip
    every tag and unescape entities. Returned as normal (auto-escaped) text.
    """
    import re
    import html as _html

    s = str(value if value is not None else "")
    if s.strip().lower() in ("nan", "none", "<na>"):
        return ""
    low = s.lower()
    # Escaped colour markup that would otherwise show as literal <span>… text.
    if "&lt;" in low and any(t in low for t in ("span", "bdi", "br")):
        s = _html.unescape(s)
    # Drop <br> as a space so multi-line arranged text stays readable on one row.
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    # Remove all remaining tags (colour spans, bdi, …) -> plain text.
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


@register.filter
def row_flag_class(row):
    """CSS class for a saved TO/PI table row based on its persisted flags.
    Returns 'row-issue' (Technical Problem), 'row-unsuppliable' (Not
    Suppliable), or 'row-service' so the read-only table mirrors the tool."""
    if not isinstance(row, dict):
        return ""
    if str(row.get("_issue", "") or "") == "1":
        return "row-issue"
    if str(row.get("_unsuppliable", "") or "") == "1":
        return "row-unsuppliable"
    comment = str(row.get("_service_comment", "") or "").strip()
    if comment and comment.lower() not in ("nan", "none", "<na>", "null"):
        return "row-service"
    return ""


@register.filter
def has_service_comment(row):
    """True when a PI row has a real attached service comment (not NaN)."""
    if not isinstance(row, dict):
        return False
    comment = str(row.get("_service_comment", "") or "").strip()
    return bool(comment and comment.lower() not in ("nan", "none", "<na>", "null"))


@register.filter
def service_unit_price(row):
    """Unit service price for display — prefer saved SERVICE PRICE (final).

    After Save PI, SERVICE PRICE holds FX/margin finals; ``_service_price_raw``
    is the editable base used only when reopening the PI tool.
    """
    if not isinstance(row, dict):
        return ""
    col = str(row.get("SERVICE PRICE", "") or "").strip()
    if col and col.lower() not in ("nan", "none", "<na>", "null"):
        return col
    raw = str(row.get("_service_price_raw", "") or "").strip()
    if not raw or raw.lower() in ("nan", "none", "<na>", "null"):
        return ""
    return raw


@register.filter
def technical_issue_rows(form_obj):
    """List of {client_no, reason} for TO rows flagged Technical Problem."""
    out = []
    for row in (getattr(form_obj, "table", None) or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("_deleted", "") or "") == "1":
            continue
        if str(row.get("_issue", "") or "") != "1":
            continue
        reason = str(row.get("_issue_reason", "") or "").strip()
        if not reason:
            continue
        cr = row.get("#", row.get("Item Code", row.get("Item", "")))
        out.append({"client_no": cr, "reason": reason})
    return out


@register.filter
def money_with_currency(value, currency="rial"):
    """Format a saved PI price with its currency unit (Rial / $ / €)."""
    from cases.export_data import format_pi_money
    return format_pi_money(value, currency)


@register.filter
def pi_form_currency(form_obj):
    """Canonical currency key for a saved PI form (rial/usd/eur)."""
    from cases.export_data import form_currency
    return form_currency(form_obj)


@register.filter
def currency_unit_label(currency="rial"):
    from cases.export_data import currency_label
    return currency_label(currency)


@register.filter
def pi_form_rate_note(form_obj):
    """Applied conversion rate for a PI version, formatted with the FROM unit.

    Returns e.g. ``"1,700,000 Rial"`` when this Proforma version carried a real
    currency conversion (rate ≠ 1); empty otherwise.
    """
    from cases.export_data import pi_rate_note
    return pi_rate_note(form_obj)
