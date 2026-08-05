"""Inquiry grid column rules (Description / Size / Qty / Unit).

Applied on create, edit, new-version, Excel preview, and paste paths.
"""
from __future__ import annotations

import re

_QTY_RE = re.compile(r"^\d+(\.\d+)?$")
# Letters (any script) plus common unit punctuation: . / - _ % and spaces.
_UNIT_EXTRA = frozenset(".-/_ %")


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


def inquiry_rows_error_message(rows: list[dict] | None) -> str:
    """Single multi-line message, or '' when valid."""
    errs = validate_inquiry_rows(rows)
    if not errs:
        return ""
    return "Inquiry items are invalid:\n" + "\n".join(errs)
