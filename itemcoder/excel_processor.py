"""Excel-to-table processing workflow.

The view calls `process_excel`; this module reads the workbook, processes each
row, assigns codes, and returns the final DataFrame used by the template.
"""

import logging
import os

import pandas as pd
from django.conf import settings

from .code_assigner import assign_code_from_csv
from .excel_reader import read_excel_first_four_columns_fast
from .feature_extractor import confind_size, find_group, find_type
from .normalizers import clean_for_group_and_features
from .regex_patterns import load_json_file
from .text_processor import process_text_record, can_run_assign_code, has_orange_alert, clear_size_only_rule_alerts
from .table_layout_manager import apply_table_layout, build_extra_values, load_table_layout_config
from .calculation_engine import calculate_row_values
from .resource_paths import json_path
from .runtime_cache import clear_row_base_cache, store_row_base_cache


logger = logging.getLogger(__name__)

_NULL_HEADER_NAMES = {"", "none", "null", "nan", "unnamed: 0", "unnamed: 1", "unnamed: 2", "unnamed: 3"}
_DEFAULT_INPUT_COLUMNS = ["description", "size", "qty", "unit"]


def _normalize_input_headers(df):
    """Keep the first four input variables stable: description, size, qty, unit.

    Some uploaded files/exporters create blank/None/null/nan headers. Those
    unstable names break later code that expects row variables named size/qty/unit.
    This function only fixes missing/null-like headers; existing real names are
    preserved so old files keep their behavior.
    """
    new_cols = []
    for i, col in enumerate(df.columns.tolist()):
        text = "" if col is None else str(col).strip()
        if text.lower() in _NULL_HEADER_NAMES or text.lower().startswith("unnamed"):
            text = _DEFAULT_INPUT_COLUMNS[i] if i < len(_DEFAULT_INPUT_COLUMNS) else f"Column_{i + 1}"
        new_cols.append(text)
    df.columns = new_cols
    return df


def process_excel_with_json(uploaded_file, json_dict):
    try:
        df = read_excel_first_four_columns_fast(uploaded_file)
    except Exception:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        df = pd.read_excel(uploaded_file, usecols=[0, 1, 2, 3], dtype=str)

    df = _normalize_input_headers(df.fillna(""))
    records = df.to_dict("records")
    return process_inquiry_records(records, json_dict)


def process_inquiry_records(records, json_dict):
    """Run the standard upload pipeline on canonical inquiry rows.

    Each record uses lowercase keys: description, size, qty, unit. This is the
    same path as Excel upload but skips the workbook round-trip, so case Build
    TO/PI seeding cannot lose rows or fail on openpyxl IO.
    """
    records = list(records or [])
    input_cols = _DEFAULT_INPUT_COLUMNS

    # Warm JSON/feature/SQLite caches once per Build TO so ~4k-row inquiries do
    # not re-pay first-hit parse costs. Matching/arrange logic is unchanged.
    try:
        from .startup_warmup import warm_all_runtime_caches
        warm_all_runtime_caches()
    except Exception:
        pass

    clear_row_base_cache()
    layout_config = load_table_layout_config()
    extra_columns_enabled = bool(layout_config.get("extra_column_layout"))

    _row_compute_memo = {}

    def _compute_row(original_text, raw_size_value):
        memo_key = (original_text, raw_size_value)
        cached = _row_compute_memo.get(memo_key)
        if cached is not None:
            return cached
        clean = clean_for_group_and_features(original_text)
        group, label_group = find_group(clean, json_dict)
        group_dict = json_dict["group"].get(group, {}) if group else {}
        type_ = ""
        if group_dict:
            type_, matched_type_token, clean_after_type = find_type(clean, group_dict)
            type_ = type_ or ""
        # Size-table lookup is best-effort. A missing CSV / bad type must not
        # prevent process_text_record (group/regex/FAT/alarm) from running.
        try:
            size_result = confind_size(group, type_, raw_size_value)
        except Exception:
            size_result = {"clean_size": "null", "display_size": raw_size_value}
        if size_result.get("clean_size") == "nan":
            size_result["clean_size"] = "null"
        clean_size = size_result.get("clean_size", "null")
        display_size = size_result.get("display_size", raw_size_value)
        # Never paint a literal "null" in the TO Size column when inquiry had a value.
        if (not display_size or str(display_size).strip().lower() in {"null", "nan"}) and raw_size_value:
            display_size = raw_size_value
        feature_size_value = clean_size
        if (not feature_size_value or str(feature_size_value).strip().lower() in {"", "null", "nan"}) and raw_size_value:
            feature_size_value = raw_size_value
        r = process_text_record(original_text, json_dict, feature_size_value)
        code_value = ""
        if r["Group"] and can_run_assign_code(
            r.get("Alarm", []),
            r.get("Target_Values_Map", {}),
            feature_vars=r.get("Feature_Variables"),
        ):
            code_value = assign_code_from_csv(r["Group"], r["Type"], r["Feature_Variables"])
            if code_value:
                clear_size_only_rule_alerts(
                    r.get("Target_Values_Map"),
                    feature_vars=r.get("Feature_Variables"),
                )
        computed = {
            "r": r, "display_size": display_size,
            "feature_size_value": feature_size_value, "code_value": code_value,
        }
        _row_compute_memo[memo_key] = computed
        return computed

    def _safe_minimal_flat(row_number, row):
        """A never-failing row: keep the client's own columns, blank the coded
        ones. Guarantees Build TO always opens (even on a row the coder chokes
        on) instead of failing the whole grid with an error banner."""
        flat = {"Item Code": row_number, "کد": ""}
        for col in input_cols:
            flat[col] = "" if row.get(col) is None else str(row.get(col, "") or "")
        flat.update({
            "Final Arranged Text": "", "Group": "", "Type": "",
            "Alarm_Features": "", "Filled_Features": "",
            "اصلاحیه": "", "ریمارک": "",
        })
        return flat

    def _build_row_flat(row_number, row):
        original_text = str(row.get("description", "") or "")
        raw_size_value = str(row.get("size", "") or "").strip()

        computed = _compute_row(original_text, raw_size_value)
        r = computed["r"]
        display_size = computed["display_size"]
        feature_size_value = computed["feature_size_value"]
        code_value = computed["code_value"]

        store_row_base_cache(
            row_number - 1,
            original_text=original_text,
            clean_size=feature_size_value,
            group=r.get("Group", ""),
            type_=r.get("Type", ""),
            features_container=r.get("_Features_Container", {}),
            feature_vars_raw=r.get("_Base_Feature_Variables", r.get("Feature_Variables", {})),
        )

        filled_features = r.get("Filled_Features", "")
        flat = {"Item Code": row_number, "کد": code_value}
        for col in input_cols:
            if col == "size":
                flat[col] = display_size
            else:
                flat[col] = row.get(col, "")

        flat.update({
            "Final Arranged Text": r["Final_Text"],
            "Group": r["Group"],
            "Type": r["Type"],
            "Alarm_Features": "<br>".join(r["Alarm"]),
            "Filled_Features": filled_features,
            "Feature_Variables": r.get("Feature_Variables", {}) or {},
            "اصلاحیه": "",
            "ریمارک": "",
        })

        if extra_columns_enabled:
            flat.update(build_extra_values(
                row_index=row_number - 1,
                group=r["Group"],
                type_=r["Type"],
                feature_vars=r.get("Feature_Variables", {}),
                code_value=code_value,
                config=layout_config,
            ))

        flat.update(calculate_row_values(
            group=r["Group"],
            type_=r["Type"],
            code_value=code_value,
            qty=flat.get("qty", ""),
            size=flat.get("size", ""),
            unit=flat.get("unit", ""),
            feature_vars=r.get("Feature_Variables", {}),
        ))
        return flat

    results = []
    for row_number, row in enumerate(records, start=1):
        row = row or {}
        # One misbehaving row must never blow up the whole Build TO. If the coder
        # raises on a specific description/size, keep that row's client data and
        # continue — the unit still gets a full, editable grid for every item.
        try:
            flat = _build_row_flat(row_number, row)
        except Exception:
            logger.exception("Coding failed for inquiry row %s; using a safe minimal row", row_number)
            flat = _safe_minimal_flat(row_number, row)
        results.append(flat)

    return apply_table_layout(pd.DataFrame(results), layout_config)


def process_excel(uploaded_file):
    """رابط برای views.py — بارگذاری JSON از itemcoder/resources/json/data.json"""
    json_path_value = json_path("data.json")
    if not os.path.exists(json_path_value):
        raise FileNotFoundError(f"JSON file not found at {json_path_value}")
    json_dict = load_json_file(json_path_value)
    return process_excel_with_json(uploaded_file, json_dict)
