"""Frontend calculation customization settings.

This module only describes how calculated columns should be displayed and which
variables can receive conversion/margin operations in the browser.

The backend calculation values are still produced by calculation_engine.py.
These settings are intentionally lightweight so upload speed is not affected.

Customization guide
-------------------
- view: maps internal variable_name -> display type.
    * "currency": show with selected currency symbol and thousands separators.
    * "number": show as a plain number with selected decimals.
- calculate: variables that are allowed to receive conversion and margin rules.
    Example: ["unit_price"] means margin/conversion is applied to unit_price.
    Dependent columns such as total_price are recalculated in the browser from
    the updated unit_price and qty.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .calculation_engine import CALCULATION_COLUMNS


CALCULATION_UI_SETTINGS: Dict[str, Any] = {
    "view": {
        "unit_price": "currency",
        "total_price": "currency",
        "weigth": "number",
    },
    "calculate": ["unit_price"],
}

CURRENCY_UNITS: List[Dict[str, str]] = [
    {"value": "rial", "label": "Rial", "symbol": "Rial"},
    {"value": "usd", "label": "$", "symbol": "$"},
    {"value": "eur", "label": "€", "symbol": "€"},
]


def _enabled_columns() -> List[Dict[str, Any]]:
    return [item for item in CALCULATION_COLUMNS if item.get("enabled", True)]


def get_calculation_ui_config() -> Dict[str, Any]:
    """Return compact JSON-safe config consumed by calculation_controls.js."""
    columns: List[Dict[str, Any]] = []
    for item in _enabled_columns():
        variable = str(item.get("variable_name") or item.get("title") or "").strip()
        if not variable:
            continue
        columns.append({
            "title": item.get("title", ""),
            "variable": variable,
            "first_input": item.get("first_input", ["csv_column", item.get("csv_column")]) if item.get("first_input") is not None or item.get("csv_column") else "",
            "second_input": item.get("second_input", item.get("calculate_variable", "")),
            "writable": bool(item.get("writable", False)) and not bool(item.get("second_input", item.get("calculate_variable", ""))),
        })

    return {
        "view": CALCULATION_UI_SETTINGS.get("view", {}),
        "calculate": CALCULATION_UI_SETTINGS.get("calculate", []),
        "currency_units": CURRENCY_UNITS,
        "columns": columns,
    }
