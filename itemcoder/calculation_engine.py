"""Fast backend calculations for price/weight style columns.

This module is intentionally separated from the text/code processor so future
calculation rules can be changed without touching the existing item-coding
logic.

Customization guide
-------------------
Edit CALCULATION_COLUMNS below. Each dictionary creates one output column.

Supported keys in each item:
- title: visible table column title.
- first_input: first value used by the column.
    * ["group_csv_column", "unit_price"] reads the column configured for the current group in GROUP_CSV_COLUMN_MAP.
    * ["csv_column", 11] still reads a fixed CSV column as fallback/legacy support.
    * "qty", "unit_price", "size", etc. means read an existing row variable.
- second_input: optional second value. When present, first_input × second_input
  is calculated. When omitted, the first_input value is shown directly.
- variable_name: internal variable name stored for that row. Other calculation
  columns can use this name in first_input or second_input.
- enabled: set False to disable the column.
- default_position: fallback table position when table_layout.json does not
  explicitly place the column.
- writable: only works when second_input is omitted. Writable raw columns can be
  edited by double-click in the browser. Calculated columns are always read-only.

Available built-in variables:
- qty: current row quantity value. This is the internal variable name and does
  not depend on the visible column title in table_layout.json.
- size: current row size value.
- unit: current row unit value.
- code: assigned item code.
- group: detected product group.
- type: detected product type.
- feature_vars: extracted feature dictionary for the row.
- csv_row: the matched CSV row as a zero-based list.

Examples
--------
Raw CSV value, editable by double click:
    {
        "title": "UNIT PRICE",
        "first_input": ["csv_column", 11],
        "variable_name": "unit_price",
        "enabled": True,
        "default_position": 9991,
        "writable": True,
    }

CSV column 12 × qty:
    {
        "title": "وزن",
        "first_input": ["csv_column", 12],
        "variable_name": "weigth",
        "second_input": "qty",
        "enabled": True,
        "default_position": 9992,
        "writable": False,
    }

Chained calculation: unit_price × qty. If unit_price is edited in the browser,
this column recalculates from that edited value immediately:
    {
        "title": "TOTAL PRICE",
        "first_input": "unit_price",
        "variable_name": "total_price",
        "second_input": "qty",
        "enabled": True,
        "default_position": 9993,
        "writable": False,
    }

Backward compatibility
----------------------
Older configs that still use csv_column/calculate_variable are still accepted:
- csv_column becomes first_input ["csv_column", <csv_column>]
- calculate_variable becomes second_input
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .code_assigner import load_code_resources, _code_csv_path


_NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?|[-+]?\d*\.\d+")
_NULL_TEXTS = {"", "none", "null", "nan"}


def clean_text(value: Any) -> str:
    """Return a safe text value; null-like strings become blank."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _NULL_TEXTS else text


def safe_variable_name(value: Any, fallback: str) -> str:
    """Return a stable internal variable name.

    Empty/null/nan variable names are not useful for chained calculations, so we
    fall back to a deterministic name. This prevents accidental keys like
    "none", "null" or "nan" from entering row calculation context.
    """
    text = clean_text(value)
    if not text:
        text = clean_text(fallback)
    text = text.replace(" ", "_").strip()
    return text or "calculation_value"


def to_number(value: Any, default: float = 0.0) -> float:
    """Convert user/CSV text to a float without raising errors."""
    text = clean_text(value)
    if not text:
        return default
    text = text.replace("٬", ",").replace("٫", ".")
    match = _NUMBER_RE.search(text)
    if not match:
        return default
    try:
        return float(match.group(0).replace(",", ""))
    except Exception:
        return default


def format_number(value: Any) -> str:
    """Format numbers cleanly while keeping blanks blank."""
    try:
        number = float(value)
    except Exception:
        return clean_text(value)
    if number == 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return (f"{number:.6f}").rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Custom calculation configuration
# ---------------------------------------------------------------------------
# Add/remove/edit items here. The code below automatically creates table columns
# for enabled items and recalculates them on upload and on live row updates.
#
# first_input / second_input rules:
# - ["group_csv_column", "unit_price"] reads the group-specific column from GROUP_CSV_COLUMN_MAP.
# - ["csv_column", 11] reads the matched CSV row column 11 as legacy/fallback support.
# - "qty", "unit_price", "size", etc. read values from the row context.
# - If second_input exists, the result is first_input × second_input.
# - If second_input is omitted, the first_input value is shown directly.
#
# Writable rule:
# - writable only applies to raw columns without second_input.
# - calculated columns are read-only so users do not edit derived values.
CALCULATION_COLUMNS: List[Dict[str, Any]] = [
    {
        "title": "UNIT PRICE",
        # IMPORTANT: the unit price must NOT be auto-filled from the group CSV
        # (those columns are info/secondary reference only). It starts empty and
        # is set ONLY by the user — either a manual entry or by applying a price
        # list in the Proforma tool. So first_input reads the row's own
        # unit_price variable (empty on a fresh seed → shows 0), never the CSV.
        "first_input": "unit_price",
        "variable_name": "unit_price",
        "enabled": True,
        "default_position": 9991,
        "writable": True,
    },
    {
        "title": "وزن",
        "first_input": ["group_csv_column", "weigth"],  # reads the group-specific column from GROUP_CSV_COLUMN_MAP
        "variable_name": "weigth",
        "second_input": "qty",
        "enabled": True,
        "default_position": 9992,
        "writable": False,
    },
    {
        "title": "TOTAL PRICE",
        "first_input": "unit_price",
        "variable_name": "total_price",
        "second_input": "qty",
        "enabled": True,
        "default_position": 9993,
        "writable": False,
    },
]


# ---------------------------------------------------------------------------
# Group-specific CSV column configuration
# ---------------------------------------------------------------------------
# These values override ["csv_column", N] for the given variable_name based on
# the current row group. This keeps one CALCULATION_COLUMNS structure while
# allowing each group CSV to store price/weight in different columns.
#
# Example:
# - pipe unit_price reads column 11, weigth reads column 12.
# - fitting unit_price reads column 13, weigth reads column 15.
#
# If a group or variable is not listed here, the csv_column defined in
# CALCULATION_COLUMNS is used as the fallback. Missing CSV files never raise an
# error; the calculation column simply stays blank.
GROUP_CSV_COLUMN_MAP: Dict[str, Dict[str, int]] = {
    "pipe": {
        "unit_price": 12,
        "weigth": 11,
    },
    "fitting": {
        "unit_price": 14,
        "weigth": 11,
    },
}


def _enabled_items() -> List[Dict[str, Any]]:
    return [item for item in CALCULATION_COLUMNS if item.get("enabled", True) and clean_text(item.get("title"))]


def _item_title(item: Mapping[str, Any]) -> str:
    return clean_text(item.get("title"))


def _item_variable(item: Mapping[str, Any]) -> str:
    return safe_variable_name(item.get("variable_name"), _item_title(item))


def _item_inputs(item: Mapping[str, Any]) -> Tuple[Any, Any]:
    """Return normalized (first_input, second_input) with legacy support."""
    first_input = item.get("first_input", None)
    if first_input is None and clean_text(item.get("csv_column")):
        first_input = ["csv_column", item.get("csv_column")]

    second_input = item.get("second_input", None)
    if second_input is None and clean_text(item.get("calculate_variable")):
        second_input = item.get("calculate_variable")

    return first_input, second_input


def _item_has_calculation(item: Mapping[str, Any]) -> bool:
    """A column is calculated when it has second_input."""
    _first, second = _item_inputs(item)
    return bool(clean_text(second))


@lru_cache(maxsize=16)
def _code_row_index_for_group(group: str) -> Dict[str, List[str]]:
    """Build a one-time code -> CSV row index for calculation columns.

    The previous implementation scanned the whole code CSV once per distinct
    code. For large pipe/fitting CSV files this made upload and live recalcu-
    lation slow. This keeps the same matching rule (code can be in column 2 or
    column 1) but pays the CSV scan only once per group/process.
    """
    group_l = clean_text(group).lower()
    if not group_l:
        return {}
    path = _code_csv_path(group_l)
    index: Dict[str, List[str]] = {}
    try:
        with open(path, newline="", encoding="utf-8") as fp:
            reader = csv.reader(fp)
            next(reader, None)
            for row in reader:
                if not row:
                    continue
                clean_row = [clean_text(v) for v in row]
                for col_pos in (1, 0):
                    if 0 <= col_pos < len(clean_row):
                        code = clean_row[col_pos]
                        if code and code not in index:
                            index[code] = clean_row
        return index
    except Exception:
        return {}


@lru_cache(maxsize=4096)
def _code_row_for_group(group: str, code_value: str) -> Optional[List[str]]:
    group_l = clean_text(group).lower()
    code_s = clean_text(code_value)
    if not group_l or not code_s:
        return None
    # Use the per-group SQLite database when present (no full RAM index, scales
    # to millions of rows); otherwise fall back to the original CSV index.
    try:
        from . import code_db
        if code_db.has_db(group_l):
            return code_db.row_by_code(group_l, code_s)
    except Exception:
        pass
    return _code_row_index_for_group(group_l).get(code_s)

def get_calculation_column_names() -> List[str]:
    """Return enabled calculated/raw output column titles."""
    return [_item_title(item) for item in _enabled_items()]


def get_calculation_column_positions() -> Dict[str, int]:
    """Return optional default positions used when table_layout.json omits them."""
    positions: Dict[str, int] = {}
    for item in _enabled_items():
        title = _item_title(item)
        try:
            positions[title] = int(item.get("default_position", 9999))
        except Exception:
            positions[title] = 9999
    return positions


def get_calculation_variable_map() -> Dict[str, str]:
    """Return {column title: internal variable name} for frontend metadata."""
    return {_item_title(item): _item_variable(item) for item in _enabled_items()}


def is_writable_calculation_column(column_name: str) -> bool:
    """Only raw non-calculated calculation columns can be manually edited."""
    name = clean_text(column_name)
    for item in _enabled_items():
        if _item_title(item) != name:
            continue
        if _item_has_calculation(item):
            return False
        return str(item.get("writable", "false")).lower() == "true"
    return False


def get_writable_calculation_columns() -> List[str]:
    """Return calculation column titles editable in the browser."""
    return [title for title in get_calculation_column_names() if is_writable_calculation_column(title)]


def _read_csv_column(csv_row: List[str], csv_column: Any) -> str:
    """Read a 1-based CSV column from the matched row."""
    try:
        csv_col = int(csv_column) - 1
    except Exception:
        csv_col = -1
    return csv_row[csv_col] if 0 <= csv_col < len(csv_row) else ""


def _resolve_input_value(input_spec: Any, context: Mapping[str, Any], csv_row: List[str], variable_name: str = "") -> str:
    """Resolve one calculation input from CSV or row context.

    Supported forms:
    - ["csv_column", 11]
    - ("csv_column", 11)
    - "qty", "unit_price", "size", ...
    - numeric/text constants as fallback values
    """
    if isinstance(input_spec, (list, tuple)) and len(input_spec) >= 2:
        source = clean_text(input_spec[0]).lower()
        group_key = clean_text(context.get("group")).lower()
        variable_key = clean_text(variable_name)

        if source in {"group_csv_column", "group_col", "group_column"}:
            # Read the CSV column configured for this row group and variable.
            # Example: pipe/unit_price -> column 11, fitting/unit_price -> column 13.
            lookup_key = clean_text(input_spec[1]) or variable_key
            csv_column = GROUP_CSV_COLUMN_MAP.get(group_key, {}).get(lookup_key)
            return clean_text(_read_csv_column(csv_row, csv_column)) if csv_column else ""

        if source in {"csv_column", "csv", "col", "column"}:
            # Legacy/fallback fixed-column mode. If the current variable exists
            # in GROUP_CSV_COLUMN_MAP, the group-specific value overrides the
            # fixed value without changing old configs.
            group_columns = GROUP_CSV_COLUMN_MAP.get(group_key, {})
            csv_column = group_columns.get(variable_key, input_spec[1])
            return clean_text(_read_csv_column(csv_row, csv_column))

    key = clean_text(input_spec)
    if not key:
        return ""

    if key in context:
        return clean_text(context.get(key))

    # Optional convenience: allow direct csv_column:11 text.
    m = re.fullmatch(r"csv_column\s*[:=]\s*(\d+)", key, flags=re.I)
    if m:
        group_key = clean_text(context.get("group")).lower()
        variable_key = clean_text(variable_name)
        group_columns = GROUP_CSV_COLUMN_MAP.get(group_key, {})
        csv_column = group_columns.get(variable_key, m.group(1))
        return clean_text(_read_csv_column(csv_row, csv_column))

    return key


def calculate_row_values(
    *,
    group: str,
    type_: str,
    code_value: str,
    qty: Any = "",
    size: Any = "",
    unit: Any = "",
    feature_vars: Optional[Mapping] = None,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Calculate all configured output columns for one processed row.

    Calculation rules are evaluated in CALCULATION_COLUMNS order so a later rule
    can depend on a previous variable_name. For example, total_price can depend
    on unit_price. User overrides from writable raw columns are applied before
    dependent calculations are evaluated.
    """
    items = _enabled_items()
    result = {_item_title(item): "" for item in items}
    if not items:
        return result

    csv_row = _code_row_for_group(clean_text(group).lower(), clean_text(code_value))
    if not csv_row:
        return result

    # Row context is intentionally keyed by stable internal variable names, not
    # visible column titles. Therefore changing the visible title of qty/size/unit
    # in table_layout.json does not break calculations.
    context: Dict[str, Any] = {
        "qty": qty,
        "size": size,
        "unit": unit,
        "code": code_value,
        "group": group,
        "type": type_,
        "csv_row": csv_row,
        "feature_vars": feature_vars or {},
        # unit_price is NEVER sourced from the CSV. It begins empty so the
        # Proforma shows 0 until the user sets a price manually or via a price
        # list (which arrive here as an override on the "unit_price" variable).
        "unit_price": "",
    }
    overrides = dict(overrides or {})

    for item in items:
        title = _item_title(item)
        variable = _item_variable(item)
        first_input, second_input = _item_inputs(item)

        # Raw writable columns keep the user's manual value if provided by the
        # browser. The value is also stored in context so later chained
        # calculations use the edited value immediately.
        if not clean_text(second_input) and (title in overrides or variable in overrides):
            value = clean_text(overrides.get(title, overrides.get(variable, "")))
        else:
            first_value = _resolve_input_value(first_input, context, csv_row, variable)
            if clean_text(second_input):
                second_value = _resolve_input_value(second_input, context, csv_row, "")
                value = format_number(to_number(first_value) * to_number(second_value))
            else:
                value = clean_text(first_value)

        value = clean_text(value)
        result[title] = value
        context[variable] = value

    return result
