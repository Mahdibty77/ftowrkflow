"""Regex and lookup helpers used while matching features.

The functions here are intentionally low-level: they load feature value lists,
parse CSV fields, and search special values in the original user text.
"""

import csv
import json
import os
import re

import pandas as pd

from django.conf import settings

from .resource_paths import resolve_resource_path

from .constants import CSV_FIELD_CACHE, FEATURE_VALUES_CACHE, JSON_FILE_CACHE


def is_empty_variant(value):
    """True for values that represent "none / empty" for a feature.

    These are the same shapes the browse grid already renders as "NO <FEATURE>":
    an empty/blank string, the literal ``null``, a ``(no)…`` marker, or a
    ``$placeholder$`` token (e.g. ``$coating$`` / ``$nocoating$``). Matching and
    code assignment treat them as empty; only the display shows a readable
    ``NO COATING``.
    """
    v = str(value or "").strip()
    if not v or v.lower() == "null":
        return True
    if re.match(r"^\(no\)", v, re.I):
        return True
    if re.match(r"^\$.*\$$", v):
        return True
    return False


def load_feature_values(val):
    if isinstance(val, str) and val.endswith(".csv"):
        csv_path = resolve_resource_path(val)

        mtime = _file_mtime(csv_path)
        if csv_path in FEATURE_VALUES_CACHE and _FEATURE_CSV_MTIME_CACHE.get(csv_path) == mtime:
            return FEATURE_VALUES_CACHE[csv_path]

        if not os.path.exists(csv_path):
            # Missing optional feature CSVs (e.g. newly referenced SDR/PN files)
            # must not crash live Remark/Revision — treat as "no values".
            FEATURE_VALUES_CACHE[csv_path] = []
            _FEATURE_CSV_MTIME_CACHE[csv_path] = mtime
            return []

        import csv
        values = []
        with open(csv_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if row and row[0].strip():
                    values.append(row[0].strip())

        FEATURE_VALUES_CACHE[csv_path] = values
        _FEATURE_CSV_MTIME_CACHE[csv_path] = mtime
        return values

    elif isinstance(val, list):
        return val

    return [val]


def _active_config_payload(path):
    """Return an admin-published JSON config for this file, or None.

    Used only as a fallback when the on-disk file is missing. Disk is the
    source of truth (see data_admin uploads); ConfigDocument is history.
    """
    try:
        import os as _os
        from .models import ConfigDocument
        key = _os.path.basename(str(path))
        doc = (ConfigDocument.objects
               .filter(key=key, is_active=True)
               .order_by("-version")
               .first())
        return doc.payload if doc is not None else None
    except Exception:
        return None


# path -> last-seen mtime (float). Lets loaders pick up disk edits without a restart.
_JSON_MTIME_CACHE = {}
_CSV_MTIME_CACHE = {}
_FEATURE_CSV_MTIME_CACHE = {}


def _file_mtime(path):
    try:
        return os.path.getmtime(path) if path and os.path.exists(path) else None
    except OSError:
        return None


def load_json_file(path):
    """Load JSON with an mtime-aware cache.

    Editing ``data.json`` (or any resource JSON) on disk and refreshing the
    page reloads the new content — no server restart / rebuild required.
    Disk is preferred over ConfigDocument when the file exists.
    """
    if not os.path.isabs(path):
        path = resolve_resource_path(path)

    mtime = _file_mtime(path)
    if path in JSON_FILE_CACHE and _JSON_MTIME_CACHE.get(path) == mtime:
        return JSON_FILE_CACHE[path]

    payload = None
    if mtime is not None:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        # Disk file missing — fall back to the latest published ConfigDocument.
        payload = _active_config_payload(path)
        if payload is None:
            raise FileNotFoundError(f"JSON file not found: {path}")

    JSON_FILE_CACHE[path] = payload
    _JSON_MTIME_CACHE[path] = mtime
    return payload


def parse_csv_for_field(csv_path):
    """خواندن CSV ویژگی‌ها با cache مبتنی بر mtime."""
    full_csv_path = csv_path
    if not os.path.isabs(full_csv_path):
        full_csv_path = resolve_resource_path(csv_path)

    mtime = _file_mtime(full_csv_path)
    if full_csv_path in CSV_FIELD_CACHE and _CSV_MTIME_CACHE.get(full_csv_path) == mtime:
        return CSV_FIELD_CACHE[full_csv_path]

    df = pd.read_csv(full_csv_path, header=0, dtype=str, keep_default_na=False)
    header_parts = [p.strip() for p in ",".join(df.columns.astype(str)).split(",") if p.strip()]
    col0 = df.iloc[:, 0].astype(str).tolist()
    result = {"header_parts": header_parts, "values": col0}
    CSV_FIELD_CACHE[full_csv_path] = result
    _CSV_MTIME_CACHE[full_csv_path] = mtime
    return result


def search_special_feature_in_original(original_text, csv_path):
    """
    جستجو برای مقادیر schedule/thickness/rating بر اساس CSV.
    خروجی: (found_value or None, new_original_text)
    """
    parsed = parse_csv_for_field(csv_path)
    values = parsed['values']
    separators = ['', 'cl', 'cl-', 'cl#', '.', '/', '-', '_', ' ']

    for v in values:
        if not v:
            continue
        v_escaped = re.escape(v)
        # prefix / suffix combinations
        for sep in separators:
            pat = re.escape(sep) + r'\s*' + v_escaped
            if re.search(pat, original_text):
                return v, re.sub(pat, '', original_text, count=1)
            pat2 = v_escaped + r'\s*' + re.escape(sep)
            if re.search(pat2, original_text):
                return v, re.sub(pat2, '', original_text, count=1)
        # plain
        if re.search(v_escaped, original_text):
            return v, re.sub(v_escaped, '', original_text, count=1)
        # numeric variants with comma
        v_num = v.replace(',', '.')
        if re.search(re.escape(v_num), original_text):
            return v_num, re.sub(re.escape(v_num), '', original_text, count=1)

    return None, original_text
