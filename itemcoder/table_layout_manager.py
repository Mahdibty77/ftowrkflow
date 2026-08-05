"""Dynamic table layout and fast extra-column lookup.

This module is intentionally backend-only and optimized for upload speed:
- The JSON config is loaded once and cached.
- Each referenced CSV file is read once and indexed by normalized column values.
- Extra columns are calculated in one pass while Excel rows are processed.

JSON file:
    itemcoder/resources/json/table_layout.json

Supported keys:
- column_layout: controls display title and final column position for normal columns.
- extra_column_layout: controls display title and final column position for extra output columns.
- <group>: one or more file rules used to fill extra output columns from CSV files.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
from django.conf import settings

from .resource_paths import json_path, resolve_resource_path

from .final_feature_display import feature_base_name, is_clean_value
from .calculation_engine import get_calculation_column_names, get_calculation_column_positions


CONFIG_RELATIVE_PATH = "table_layout.json"

# Built-in column names used by the current processor before display-layout is applied.
# These are the CANONICAL (internal) names. The coding/coloring/lookup logic and the
# tool JS reference columns by these canonical names (data-col-name), so they must NOT
# change. Only the *display titles* below are user-facing.
DEFAULT_COLUMN_ORDER = [
    "Item Code",
    "کد",
    "description",
    "size",
    "qty",
    "unit",
    "Final Arranged Text",
    "Group",
    "Type",
    "Alarm_Features",
    "BRAND",
    "Filled_Features",
    "اصلاحیه",
    "ریمارک",
]

# ---------------------------------------------------------------------------
# FIXED, SINGLE-SOURCE column titles for the codify tool & table.
# Rename a value here and BOTH the tool and the exported table change, with no
# effect on the coding logic (which keys off the canonical names on the left).
# Columns left out of TECHNICAL_VISIBLE are computed but hidden in the grid.
# To SHOW "Filled_Features", add its canonical name to TECHNICAL_VISIBLE below.
# ---------------------------------------------------------------------------
COLUMN_TITLES = {
    "Item Code": "Item No.",
    "کد": "FTCO CODE",
    "description": "CLIENT DISCRIPTION",
    "size": "SIZE",
    "qty": "QTY",
    "unit": "UNIT",
    "Final Arranged Text": "FTCO DISCRIPTION",
    "Group": "Group",
    "Type": "Type",
    "Alarm_Features": "ALARM",
    "BRAND": "BRAND",
    "Filled_Features": "Filled_Features",
    "اصلاحیه": "REVISION",
    "ریمارک": "REMARK",
}
# Columns kept in the data (the tool needs Group/Type for per-row colouring &
# lookup; Filled_Features carries the technical breakdown) but HIDDEN in the
# grid via CSS keyed on their canonical data-col-name. To SHOW Filled_Features
# for the technical build, remove "Filled_Features" from HIDDEN_COLUMNS (and the
# matching rule in style.css).
HIDDEN_COLUMNS = ["Group", "Type", "Filled_Features"]

DEFAULT_CONFIG = {
    # display-title -> position; position is the 1-based index of the CANONICAL
    # column in DEFAULT_COLUMN_ORDER, so renaming the title never moves the data.
    # ALL canonical columns are listed so none are dropped from the DataFrame;
    # the ones in HIDDEN_COLUMNS are simply hidden in the UI.
    "column_layout": {
        COLUMN_TITLES[name]: str(DEFAULT_COLUMN_ORDER.index(name) + 1)
        for name in DEFAULT_COLUMN_ORDER
    },
    "extra_column_layout": {},
}

_CONFIG_CACHE = None
_CSV_CACHE: Dict[Tuple[str, float], "CsvTable"] = {}
_LOOKUP_PLAN_CACHE = {}

_NORMALIZE_RE = re.compile(r"[^a-zA-Z0-9]+")


def normalize_lookup_value(value) -> str:
    """Normalize values for CSV matching without changing user-visible output."""
    if value is None:
        return ""
    value_s = str(value).strip()
    if value_s.lower() in {"", "null", "nan", "none"}:
        return ""
    return _NORMALIZE_RE.sub("", value_s).lower()


def _config_path() -> str:
    return json_path(CONFIG_RELATIVE_PATH)


def load_table_layout_config(force_reload: bool = False) -> dict:
    """Load and cache table_layout.json with safe defaults (mtime-aware)."""
    global _CONFIG_CACHE
    path = _config_path()
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else None
    except OSError:
        mtime = None
    cached_mtime = getattr(load_table_layout_config, "_mtime", object())
    if _CONFIG_CACHE is not None and not force_reload and cached_mtime == mtime:
        return _CONFIG_CACHE

    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp) or {}
        except Exception:
            data = {}

    config = dict(DEFAULT_CONFIG)
    config.update(data if isinstance(data, dict) else {})
    # Column titles/order are fixed in code (COLUMN_TITLES). Do not let any
    # leftover JSON override them, so renaming is done in ONE place.
    config["column_layout"] = DEFAULT_CONFIG["column_layout"]
    config.setdefault("extra_column_layout", {})
    _CONFIG_CACHE = config
    load_table_layout_config._mtime = mtime
    return config


def _position(value, default=9999) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


@dataclass
class CsvTable:
    """A cached CSV table with fast column indexes."""

    path: str
    rows: List[List[str]]
    indexes: Dict[int, Dict[str, List[int]]]

    def index_for_col(self, col_pos: int) -> Dict[str, List[int]]:
        if col_pos not in self.indexes:
            index: Dict[str, List[int]] = {}
            for row_idx, row in enumerate(self.rows):
                value = row[col_pos] if 0 <= col_pos < len(row) else ""
                index.setdefault(normalize_lookup_value(value), []).append(row_idx)
            self.indexes[col_pos] = index
        return self.indexes[col_pos]

    def value(self, row_idx: int, col_pos: int) -> str:
        try:
            return self.rows[row_idx][col_pos]
        except Exception:
            return ""


def _resolve_path(path_value: str) -> str:
    """Resolve a configured CSV path. Empty paths intentionally mean no lookup."""
    path_value = str(path_value or "").strip()
    if not path_value:
        return ""
    if os.path.isabs(path_value):
        return path_value
    return resolve_resource_path(path_value)


def load_csv_table(path_value: str) -> Optional[CsvTable]:
    """Read a CSV once and keep indexed rows in memory for fast repeated lookups.

    Empty/missing paths are valid configuration states. They simply disable the
    lookup rule so uploads never fail with FileNotFoundError when the user leaves
    a CSV path blank in table_layout.json.
    """
    path = _resolve_path(path_value)
    if not path or not os.path.isfile(path):
        return None

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0

    cache_key = (path, mtime)
    if cache_key in _CSV_CACHE:
        return _CSV_CACHE[cache_key]

    rows: List[List[str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        for row in reader:
            rows.append(["" if cell is None else str(cell).strip() for cell in row])

    table = CsvTable(path=path, rows=rows, indexes={})
    _CSV_CACHE[cache_key] = table
    return table


def _parse_factors(rule: Mapping) -> List[Tuple[str, int]]:
    """Parse [feature, column, feature, column, ...] into zero-based CSV columns."""
    factors = rule.get("factors", []) if isinstance(rule, Mapping) else []
    if not isinstance(factors, list):
        return []

    parsed: List[Tuple[str, int]] = []
    for i in range(0, len(factors) - 1, 2):
        feature = str(factors[i]).strip()
        col_pos = _position(factors[i + 1], default=0) - 1
        if feature and col_pos >= 0:
            parsed.append((feature, col_pos))
    return parsed


def _parse_outputs(rule: Mapping) -> Dict[str, int]:
    """Parse output-1_name: col_2 into {name: 1}."""
    outputs: Dict[str, int] = {}
    if not isinstance(rule, Mapping):
        return outputs

    for key, value in rule.items():
        key_s = str(key)
        if not key_s.startswith("output-") or "_" not in key_s:
            continue
        output_name = key_s.split("_", 1)[1].strip()
        value_s = str(value or "").strip()
        if not value_s:
            continue
        m = re.search(r"col[_-]?(\d+)", value_s, flags=re.I)
        if output_name and m:
            outputs[output_name] = int(m.group(1)) - 1
    return outputs


def _feature_value(feature_name: str, feature_vars: Mapping, *, group: str, type_: str, code_value: str) -> str:
    """Return the row value used for a factor name such as material or code."""
    name = str(feature_name or "").strip()
    if name.lower() == "code":
        return str(code_value or "").strip()

    # First try the same simple-name logic used by final_arrange.json templates.
    for key, value in feature_vars.items():
        if feature_base_name(key, group_key=group, type_key=type_) == name and is_clean_value(value):
            return str(value).strip()

    # Fallbacks for exact/raw keys.
    if name in feature_vars and is_clean_value(feature_vars.get(name)):
        return str(feature_vars.get(name)).strip()

    lower_name = name.lower()
    for key, value in feature_vars.items():
        if str(key).lower().startswith(lower_name) and is_clean_value(value):
            return str(value).strip()

    return ""


def _match_rule_row(table: CsvTable, rule: Mapping, row_index: int, feature_vars: Mapping, group: str, type_: str, code_value: str) -> Optional[int]:
    """Return the matched CSV row index for one file rule."""
    factors = _parse_factors(rule)

    # No factors: use matching upload row number if available, otherwise first CSV row.
    if not factors:
        if 0 <= row_index < len(table.rows):
            return row_index
        return 0 if table.rows else None

    candidates: Optional[set] = None
    for feature_name, col_pos in factors:
        search_value = normalize_lookup_value(_feature_value(feature_name, feature_vars, group=group, type_=type_, code_value=code_value))
        if not search_value:
            return None
        matched = set(table.index_for_col(col_pos).get(search_value, []))
        candidates = matched if candidates is None else candidates & matched
        if not candidates:
            return None

    return min(candidates) if candidates else None


def _feature_lookup_map(feature_vars: Mapping, group: str, type_: str, code_value: str) -> Dict[str, str]:
    """Build a one-row normalized feature lookup map for faster CSV factors."""
    lookup: Dict[str, str] = {"code": str(code_value or "").strip()}
    for key, value in feature_vars.items():
        if not is_clean_value(value):
            continue
        value_s = str(value).strip()
        raw_key = str(key).strip()
        if raw_key:
            lookup.setdefault(raw_key.lower(), value_s)
        base = feature_base_name(key, group_key=group, type_key=type_)
        if base:
            lookup.setdefault(str(base).lower(), value_s)
    return lookup


def _feature_value_fast(feature_name: str, lookup: Mapping[str, str]) -> str:
    """Fast value lookup used after _feature_lookup_map() is built once per row."""
    name = str(feature_name or "").strip().lower()
    if not name:
        return ""
    if name in lookup:
        return str(lookup.get(name) or "").strip()
    for key, value in lookup.items():
        if str(key).startswith(name) and is_clean_value(value):
            return str(value).strip()
    return ""


def _get_group_lookup_plan(config: Mapping, group: str):
    """Return pre-parsed usable CSV lookup rules for a group.

    The expensive parts (output parsing, factor parsing, path resolution and CSV
    loading) are cached per config/group, so each upload row only does matching.
    """
    cache_key = ("group_lookup_plan", id(config), str(group or ""))
    if cache_key in _LOOKUP_PLAN_CACHE:
        return _LOOKUP_PLAN_CACHE[cache_key]

    plan = []
    group_rules = config.get(str(group or ""), {}) if isinstance(config, Mapping) else {}
    if isinstance(group_rules, dict):
        for _file_key, rule in group_rules.items():
            if not isinstance(rule, dict):
                continue
            outputs = _parse_outputs(rule)
            if not outputs:
                continue
            path_value = str(rule.get("path", "") or "").strip()
            if not path_value:
                continue
            table = load_csv_table(path_value)
            if not table:
                continue
            plan.append({
                "table": table,
                "factors": _parse_factors(rule),
                "outputs": outputs,
            })

    _LOOKUP_PLAN_CACHE[cache_key] = plan
    return plan


def _match_plan_row(table: CsvTable, factors: Sequence[Tuple[str, int]], row_index: int, feature_lookup: Mapping[str, str]) -> Optional[int]:
    """Match a CSV row using a cached rule plan."""
    if not factors:
        if 0 <= row_index < len(table.rows):
            return row_index
        return 0 if table.rows else None

    candidates: Optional[set] = None
    for feature_name, col_pos in factors:
        search_value = normalize_lookup_value(_feature_value_fast(feature_name, feature_lookup))
        if not search_value:
            return None
        matched = set(table.index_for_col(col_pos).get(search_value, []))
        candidates = matched if candidates is None else candidates & matched
        if not candidates:
            return None
    return min(candidates) if candidates else None


def get_extra_column_names(config: Optional[dict] = None) -> List[str]:
    """Return extra columns ordered by extra_column_layout positions.

    This is called for every processed row, so the ordered result is cached per
    config object to avoid repeated sorting during large uploads.
    """
    config = config or load_table_layout_config()
    cache_key = ("extra_names", id(config))
    if cache_key in _LOOKUP_PLAN_CACHE:
        return _LOOKUP_PLAN_CACHE[cache_key]

    layout = config.get("extra_column_layout", {}) if isinstance(config, dict) else {}
    if not isinstance(layout, dict):
        names = []
    else:
        names = [name for name, _pos in sorted(layout.items(), key=lambda kv: _position(kv[1]))]

    _LOOKUP_PLAN_CACHE[cache_key] = names
    return names


def build_extra_values(row_index: int, group: str, type_: str, feature_vars: Mapping, code_value: str, config: Optional[dict] = None) -> Dict[str, str]:
    """Fill configured extra output columns for one processed row.

    Fast exits are important for upload speed: if extra_column_layout is empty or
    a group has no usable CSV path, this returns immediately without touching the
    filesystem.
    """
    config = config or load_table_layout_config()
    extra_names = get_extra_column_names(config)
    if not extra_names:
        return {}

    result = {name: "" for name in extra_names}
    plan = _get_group_lookup_plan(config, str(group or ""))
    if not plan:
        return result

    feature_lookup = _feature_lookup_map(feature_vars, str(group or ""), str(type_ or ""), code_value)
    for rule_plan in plan:
        table = rule_plan["table"]
        matched_idx = _match_plan_row(table, rule_plan["factors"], row_index, feature_lookup)
        if matched_idx is None:
            continue
        for output_name, col_pos in rule_plan["outputs"].items():
            if output_name in result:
                result[output_name] = table.value(matched_idx, col_pos)

    return result


def is_writable_extra_column(column_name: str, group: str = "", config: Optional[dict] = None) -> bool:
    """Return true when any rule writing this extra column is marked writable."""
    config = config or load_table_layout_config()
    if not get_extra_column_names(config):
        return False

    cache_key = ("writable", id(config), str(group or ""))
    if cache_key in _LOOKUP_PLAN_CACHE:
        return str(column_name) in _LOOKUP_PLAN_CACHE[cache_key]

    writable = set()
    rule_groups = [config.get(group, {})] if group else [v for k, v in config.items() if k not in {"column_layout", "extra_column_layout"}]
    for rules in rule_groups:
        if not isinstance(rules, dict):
            continue
        for rule in rules.values():
            if not isinstance(rule, dict):
                continue
            if str(rule.get("writable", "false")).lower() != "true":
                continue
            writable.update(_parse_outputs(rule).keys())

    _LOOKUP_PLAN_CACHE[cache_key] = writable
    return str(column_name) in writable


def apply_table_layout(df: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
    """Rename/reorder DataFrame columns according to table_layout.json.

    The returned DataFrame keeps a ``display -> canonical`` map in ``attrs`` so
    views/JS can identify columns even if the user changes visible titles.
    """
    config = config or load_table_layout_config()
    normal_layout = config.get("column_layout", {}) if isinstance(config, dict) else {}
    extra_layout = config.get("extra_column_layout", {}) if isinstance(config, dict) else {}

    items = []
    used_existing = set()
    display_to_canonical = {}

    # Normal columns: JSON key is the visible title; matching source column uses
    # the same key when present, otherwise the built-in column with the same slot.
    sorted_normal = sorted(normal_layout.items(), key=lambda kv: _position(kv[1])) if isinstance(normal_layout, dict) else []
    for _default_idx, (display_name, pos) in enumerate(sorted_normal):
        canonical = display_name if display_name in df.columns else None

        # If the visible title is renamed, use its configured position to find
        # the original built-in column. This keeps mappings correct even when
        # another column is removed from column_layout.
        if canonical is None:
            default_pos = _position(pos) - 1
            if 0 <= default_pos < len(DEFAULT_COLUMN_ORDER):
                candidate = DEFAULT_COLUMN_ORDER[default_pos]
                if candidate in df.columns:
                    canonical = candidate
        if canonical is None:
            canonical = display_name
            df[canonical] = ""
        items.append((_position(pos), display_name, canonical))
        used_existing.add(canonical)
        display_to_canonical[display_name] = canonical

    # Extra columns are already added to df by canonical output name.
    if isinstance(extra_layout, dict):
        for display_name, pos in sorted(extra_layout.items(), key=lambda kv: _position(kv[1])):
            canonical = display_name
            if canonical not in df.columns:
                df[canonical] = ""
            items.append((_position(pos), display_name, canonical))
            used_existing.add(canonical)
            display_to_canonical[display_name] = canonical

    # Calculation columns are configured in calculation_engine.py. They are
    # auto-visible so the user does not have to edit table_layout.json for the
    # first price/weight setup. If a user later wants exact ordering, they can
    # add these titles to extra_column_layout or adjust default_position there.
    calc_positions = get_calculation_column_positions()
    for calc_name in get_calculation_column_names():
        if calc_name not in df.columns or calc_name in used_existing:
            continue
        pos = _position(extra_layout.get(calc_name), default=calc_positions.get(calc_name, 9999)) if isinstance(extra_layout, dict) else calc_positions.get(calc_name, 9999)
        items.append((pos, calc_name, calc_name))
        used_existing.add(calc_name)
        display_to_canonical[calc_name] = calc_name

    # Do not append columns that are not present in table_layout.json, except
    # calculation columns intentionally enabled by calculation_engine.py.
    # The JSON file is now the single source of truth for visible table columns.
    # If the user removes "Filled_Features" or any other default column from
    # column_layout, that column must not appear in the HTML table.
    #
    # Backend logic still keeps the calculated values internally before this
    # display step; only the final rendered DataFrame is filtered here.
    ordered = sorted(items, key=lambda item: item[0])
    out = df[[canonical for _pos, _display, canonical in ordered]].copy()
    out.columns = [display for _pos, display, _canonical in ordered]
    out.attrs["display_to_canonical"] = display_to_canonical
    out.attrs["canonical_to_display"] = {canonical: display for display, canonical in display_to_canonical.items()}
    return out
