"""Inquiry grid column rules (Description / Size / Qty / Unit).

Applied on create, edit, new-version, Excel preview, and paste paths.
"""
from __future__ import annotations

import re

# ASCII digits ONLY — deliberately ``[0-9]`` and not ``\d``.
#
# There are two halves to this rule and they disagreed. The browser half in
# static/js/inquiry_grid_validate.js is ``/^\d+(\.\d+)?$/`` with NO ``u`` flag,
# where ``\d`` is [0-9]; this half was a Python str pattern, where ``\d`` also
# matches every Unicode decimal digit. So Persian '۱۲۳' and Arabic-Indic '١٢٣'
# were refused in the browser and accepted here, and ``float()`` converts them,
# so a quantity typed past the browser (or posted directly) survived into the
# price arithmetic and the exports in a script no document is set in.
#
# ONE path reaches this rule with no browser half in front of it, and it is not
# a forged POST: cases.views.preview_excel runs ``validate_inquiry_rows`` over
# the rows parsed out of an uploaded workbook, server-side, before the grid that
# would validate them exists. So this tightening is a REAL change for exactly
# that path — an Excel sheet whose quantity column holds Persian '۱۲۳' used to
# be accepted and is now refused, row by row, with the message below.
#
# Refusing is the wanted behaviour, and not because ASCII is tidier. A quantity
# is arithmetic in two languages: Python's ``float('۱۲۳')`` is 123.0, but
# JavaScript's ``parseFloat('۱۲۳')`` is NaN — a StringNumericLiteral is ASCII
# digits only — and the Proforma prices every line in the browser
# (pi_pricing.js ``qtyOf`` falls back to 0, service_price.js ``qtyOf`` to 1).
# A Persian-digit quantity accepted here therefore did not stay a display
# question: it reached the pricing grid as zero and priced that line at zero,
# silently. An upload refused at the door with "Row 12: Qty must contain
# numbers only." is a sheet the Commercial user fixes in a minute; a Proforma
# with a zero line is a document that goes to a client.
#
# Every other caller — New Case, Edit items, New version, paste — meets the
# browser half first, so for those this is the same rule stated twice rather
# than a new one. Measured against the live database before the change: 764
# stored LineItem quantities and 4,958 quantity cells across every saved
# CaseForm, of which 0 pass the old pattern but fail this one. No third rule is
# invented, and nothing already stored becomes invalid.
_QTY_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
# Letters (any script) plus common unit punctuation: . / - _ % and spaces.
_UNIT_EXTRA = frozenset(".-/_ %")
# cases.models.LineItem stores quantity and unit as CharField(max_length=40).
_MAX_QTY_UNIT_LEN = 40


def _row_label(row: dict, index: int) -> int:
    """1-based row number shown to the user (# when present, else position)."""
    for key in ("client_row", "#", "Item"):
        raw = row.get(key, "")
        try:
            n = int(str(raw).strip())
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return index


def _is_deleted(row: dict) -> bool:
    return str(row.get("deleted", "") or row.get("_deleted", "") or "") in (
        "1", "true", "True", "yes",
    )


def _field(row: dict, *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def _is_valid_unit(value: str) -> bool:
    """Unit: letters required-or-allowed with punctuation; digits not allowed."""
    if not value:
        return False
    has_letter = False
    for ch in value:
        if ch.isalpha():
            has_letter = True
            continue
        if ch in _UNIT_EXTRA:
            continue
        return False
    return has_letter


def _is_qty(value: str) -> bool:
    return bool(value) and bool(_QTY_RE.fullmatch(value))


def validate_inquiry_rows(rows: list[dict] | None) -> list[str]:
    """Return human-readable error strings; empty list means valid.

    Rules (active / non-deleted rows that have any filled column):
      • Description — any characters, length >= 7
      • Size — length <= 20 (may be empty)
      • Qty — digits only (optional single decimal point), required
      • Unit — letters plus . / - _ % / spaces (no digits), required
    """
    errors: list[str] = []
    for i, raw in enumerate(rows or [], start=1):
        if not isinstance(raw, dict):
            continue
        if _is_deleted(raw):
            continue
        desc = _field(raw, "description", "Description")
        size = _field(raw, "size", "Size")
        qty = _field(raw, "quantity", "Qty", "qty")
        unit = _field(raw, "unit", "Unit")
        if not (desc or size or qty or unit):
            continue
        label = _row_label(raw, i)
        if len(desc) < 7:
            errors.append(
                f"Row {label}: Description must be at least 7 characters "
                f"(currently {len(desc)})."
            )
        if len(size) > 20:
            errors.append(
                f"Row {label}: Size must be at most 20 characters "
                f"(currently {len(size)})."
            )
        if not _is_qty(qty):
            errors.append(
                f"Row {label}: Qty must contain numbers only"
                + (" (cannot be empty)." if not qty else ".")
            )
        if not _is_valid_unit(unit):
            errors.append(
                f"Row {label}: Unit must contain letters"
                + (" (digits not allowed; . / - _ % and spaces are OK)."
                   if unit else " (cannot be empty).")
            )
    return errors


def validate_qty_unit(qty: str, unit: str, label) -> list[str]:
    """The Qty and Unit halves of ``validate_inquiry_rows`` on their own.

    Same two predicates, same two messages, no second definition of either
    rule — this exists only because the Technical Offer grid lets Technical
    change Qty and Unit on a row whose Description and Size it may NOT touch,
    so running the whole four-column validator there would report the client's
    own Description/Size back at a user who cannot do anything about it.

    The length cap comes from ``cases.models.LineItem``, whose ``quantity`` and
    ``unit`` are CharField(max_length=40): the inquiry grid gets it from the
    model field, and a value typed on the TO must fit the same column.
    """
    errors: list[str] = []
    qty = str(qty or "").strip()
    unit = str(unit or "").strip()
    if not _is_qty(qty):
        errors.append(
            f"Row {label}: Qty must contain numbers only"
            + (" (cannot be empty)." if not qty else ".")
        )
    elif len(qty) > _MAX_QTY_UNIT_LEN:
        errors.append(
            f"Row {label}: Qty must be at most {_MAX_QTY_UNIT_LEN} characters "
            f"(currently {len(qty)})."
        )
    if not _is_valid_unit(unit):
        errors.append(
            f"Row {label}: Unit must contain letters"
            + (" (digits not allowed; . / - _ % and spaces are OK)."
               if unit else " (cannot be empty).")
        )
    elif len(unit) > _MAX_QTY_UNIT_LEN:
        errors.append(
            f"Row {label}: Unit must be at most {_MAX_QTY_UNIT_LEN} characters "
            f"(currently {len(unit)})."
        )
    return errors


def inquiry_rows_error_message(rows: list[dict] | None) -> str:
    """Single multi-line message, or '' when valid."""
    errs = validate_inquiry_rows(rows)
    if not errs:
        return ""
    return "Inquiry items are invalid:\n" + "\n".join(errs)
