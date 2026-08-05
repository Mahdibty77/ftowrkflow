"""Code assignment from CSV tables.

All caches and helper functions for assigning the final code are kept here.
The matching algorithm is unchanged; it is only moved out of processor.py.
"""

import json
import os
import re

import pandas as pd
from django.conf import settings

from .resource_paths import json_path, csv_path
from .composite_keys import get_by_alias

from .constants import (
    ASSIGN_CODE_RESULT_CACHE,
    CODE_FEATURE_MAP_CACHE,
    CODE_INDEX_CACHE,
    CODE_MAPPING_CACHE,
    CODE_NORMALIZE_RE,
    CODE_NORMALIZED_CACHE,
    CODE_TABLE_CACHE,
)


def _load_code_table_df_from_db(group):
    """Rebuild a coding-data DataFrame from the database for a group.

    Returns a pandas DataFrame identical in shape/content to the CSV one, or
    ``None`` when the group has no managed rows (so the caller uses the CSV).
    Any error returns ``None`` to guarantee the CSV path keeps working.
    """
    try:
        from .models import CodeTable, CodeTableRow
        meta = CodeTable.objects.filter(group=group).first()
        if meta is None or not meta.columns:
            return None
        rows = list(
            CodeTableRow.objects.filter(group=group).order_by("row_no").values_list("cells", flat=True)
        )
        if not rows:
            return None
        columns = list(meta.columns)
        width = len(columns)
        # Pad/trim defensively so every row matches the header width exactly.
        norm = []
        for cells in rows:
            cells = list(cells or [])
            if len(cells) < width:
                cells = cells + [""] * (width - len(cells))
            elif len(cells) > width:
                cells = cells[:width]
            norm.append([("" if c is None else str(c)) for c in cells])
        return pd.DataFrame(norm, columns=columns, dtype=str)
    except Exception:
        return None


def load_code_resources(group):
    """
    CSV و JSON مربوط به یک group را فقط یک بار لود می‌کند و در cache نگه می‌دارد
    """
    group = group.lower()

    from .regex_patterns import load_json_file, _file_mtime

    json_file_path = json_path("asign_code.json")
    json_mtime = _file_mtime(json_file_path)
    if CODE_MAPPING_CACHE.get("__asign_mtime__") != json_mtime:
        # asign_code.json changed on disk — drop all group mapping caches.
        CODE_TABLE_CACHE.clear()
        CODE_MAPPING_CACHE.clear()
        CODE_MAPPING_CACHE["__asign_mtime__"] = json_mtime

    if group in CODE_TABLE_CACHE:
        return CODE_TABLE_CACHE[group], CODE_MAPPING_CACHE.get(group)

    # Prefer the database-managed table when an admin has imported this group;
    # otherwise read the CSV file exactly as before.
    df = _load_code_table_df_from_db(group)
    if df is None:
        base_csv_dir = csv_path("code_table")
        csv_file_path = os.path.join(base_csv_dir, f"{group}_coding_data.csv")
        if not os.path.exists(csv_file_path) or not os.path.exists(json_file_path):
            CODE_TABLE_CACHE[group] = None
            CODE_MAPPING_CACHE[group] = None
            return None, None
        # ⬅ CSV فقط یک بار
        df = pd.read_csv(csv_file_path, dtype=str, keep_default_na=False)
    elif not os.path.exists(json_file_path):
        CODE_TABLE_CACHE[group] = None
        CODE_MAPPING_CACHE[group] = None
        return None, None

    # mtime-aware: disk edits to asign_code.json apply without a restart.
    mapping_all = load_json_file(json_file_path)

    CODE_TABLE_CACHE[group] = df
    CODE_MAPPING_CACHE[group] = mapping_all
    CODE_MAPPING_CACHE["__asign_all__"] = mapping_all

    return df, mapping_all



def _code_csv_path(group):
    return os.path.join(csv_path("code_table"), f"{str(group).lower()}_coding_data.csv")

def _normalize_for_code(s, keep_punctuation=False):
    if s is None:
        return ""
    s = str(s).strip()
    if keep_punctuation:
        return s.lower()
    # Same absent-cell rule as code_db._normalize: ``$coating$`` / ``(no)coating``
    # must match an empty feature variable (not normalize to ``coating``).
    from .code_db import is_absent_cell
    if is_absent_cell(s):
        return ""
    return CODE_NORMALIZE_RE.sub("", s).lower()


def _get_normalized_code_column(group, df, col_pos):
    cache_key = (group, col_pos)
    if cache_key not in CODE_NORMALIZED_CACHE:
        CODE_NORMALIZED_CACHE[cache_key] = [
            _normalize_for_code(v)
            for v in df.iloc[:, col_pos].astype(str).fillna("").tolist()
        ]
    return CODE_NORMALIZED_CACHE[cache_key]


def _get_code_index(group, df, col_pos):
    cache_key = (group, col_pos)
    if cache_key not in CODE_INDEX_CACHE:
        index_map = {}
        values = _get_normalized_code_column(group, df, col_pos)
        for idx, value in enumerate(values):
            index_map.setdefault(value, set()).add(idx)
        CODE_INDEX_CACHE[cache_key] = index_map
    return CODE_INDEX_CACHE[cache_key]


def _matched_indices_for_code_value(group, df, col_pos, search_val):
    index_map = _get_code_index(group, df, col_pos)
    if search_val in ("", "null"):
        return set(index_map.get("", set())) | set(index_map.get("null", set()))
    return set(index_map.get(search_val, set()))


def _get_assign_code_feature_plan(group_l, type_l, mapping_all):
    cache_key = (group_l, type_l)
    if cache_key in CODE_FEATURE_MAP_CACHE:
        return CODE_FEATURE_MAP_CACHE[cache_key]

    group_map = get_by_alias(mapping_all, group_l)
    if not isinstance(group_map, dict):
        CODE_FEATURE_MAP_CACHE[cache_key] = None
        return None

    feature_map = get_by_alias(group_map, type_l)
    if not isinstance(feature_map, dict):
        CODE_FEATURE_MAP_CACHE[cache_key] = None
        return None
    feature_map_normal = {
        str(feat_name).strip().lower(): str(col_spec).strip().lower()
        for feat_name, col_spec in feature_map.items()
    }

    or_groups = {}
    required_feats = []
    for feat_key, col_spec in feature_map_normal.items():
        m = re.match(r"col[_\-]?(\d+)", col_spec)
        if not m:
            continue
        col_idx = int(m.group(1))

        or_match = re.match(r"(or\d+)[_](.+)", feat_key)
        if or_match:
            or_group = or_match.group(1)
            inner_feat = or_match.group(2)
            or_groups.setdefault(or_group, []).append((inner_feat, col_idx))
        else:
            if "&" in feat_key:
                parts = [p.strip() for p in feat_key.split("&") if p.strip()]
                required_feats.append((feat_key, col_idx, True, parts))
            else:
                required_feats.append((feat_key, col_idx, False, [feat_key]))

    plan = (required_feats, or_groups)
    CODE_FEATURE_MAP_CACHE[cache_key] = plan
    return plan


def _resolve_feature_value(group, type, group_l, type_l, feature_vars, field_name):
    """Resolve a feature value from Feature_Variables for code lookup.

    Extracted verbatim from the in-loop resolver so the SQLite fast path and the
    pandas fallback share ONE implementation (identical results by construction).
    """
    field = str(field_name).strip()
    field_l = field.lower()

    if field_l == "type":
        type_candidates = [
            f"{group_l}_type",
            f"{str(group).strip()}_type",
        ]
        for key in type_candidates:
            if key in feature_vars and str(feature_vars.get(key, "")).strip():
                return feature_vars.get(key, "")

        lowered = {str(k).lower(): k for k in feature_vars.keys()}
        for key in type_candidates:
            real_key = lowered.get(str(key).lower())
            if real_key is not None and str(feature_vars.get(real_key, "")).strip():
                return feature_vars.get(real_key, "")

        # Safe fallback: CSV TYPE columns store the product type itself.
        return str(type).strip()

    candidates = [
        f"{field_l}_{group_l}_{type_l}",
        f"{field}_{group_l}_{type_l}",
        f"{field_l}_{type_l}_{group_l}",
        f"{field}_{type_l}_{group_l}",
    ]

    type_original = str(type).strip()
    group_original = str(group).strip()
    candidates.extend([
        f"{field}_{group_original}_{type_original}",
        f"{field}_{type_original}_{group_original}",
        f"{field_l}_{group_original}_{type_original}",
        f"{field_l}_{type_original}_{group_original}",
    ])

    candidates.extend([f"__alias__{c}" for c in list(candidates)])

    for key in candidates:
        if key in feature_vars:
            return feature_vars.get(key, "")

    lowered = {str(k).lower(): k for k in feature_vars.keys()}
    for key in candidates:
        real_key = lowered.get(str(key).lower())
        if real_key is not None:
            return feature_vars.get(real_key, "")
    return ""


def _assign_code_via_sqlite(group, type, group_l, type_l, feature_vars):
    """Resolve the code using the group's SQLite database.

    Returns the code string, ``""`` (no match), or ``None`` when the SQLite path
    cannot be used (no DB / missing mapping) so the caller falls back to pandas.
    The search-value computation is byte-identical to the pandas path; only the
    candidate-set matching is delegated to indexed SQL.
    """
    from . import code_db
    if not code_db.has_db(group_l):
        return None

    json_file_path = json_path("asign_code.json")
    if not os.path.exists(json_file_path):
        return None
    from .regex_patterns import load_json_file, _file_mtime

    # Reuse the same mtime-aware mapping cache as the pandas path so Build TO
    # does not re-parse asign_code.json on every row.
    json_mtime = _file_mtime(json_file_path)
    if CODE_MAPPING_CACHE.get("__asign_mtime__") != json_mtime:
        CODE_TABLE_CACHE.clear()
        CODE_MAPPING_CACHE.clear()
        CODE_MAPPING_CACHE["__asign_mtime__"] = json_mtime
    mapping_all = CODE_MAPPING_CACHE.get("__asign_all__")
    if mapping_all is None:
        mapping_all = load_json_file(json_file_path)
        CODE_MAPPING_CACHE["__asign_all__"] = mapping_all

    plan = _get_assign_code_feature_plan(group_l, type_l, mapping_all)
    if plan is None:
        return ""

    required_feats, or_groups = plan

    ncols = len(code_db.column_names(group_l))

    def gv(field_name):
        return _resolve_feature_value(group, type, group_l, type_l, feature_vars, field_name)

    search_by_pos = {}
    for _feat_key, col_idx, is_combined, parts in required_feats:
        col_pos = col_idx - 1
        if col_pos < 0 or col_pos >= ncols:
            return ""                       # mirrors the pandas out-of-range break
        if is_combined:
            normalized_vals = [_normalize_for_code(gv(p)) for p in parts]
            search_val = "".join(v for v in normalized_vals if v != "null")
        else:
            search_val = _normalize_for_code(gv(parts[0]))
        search_by_pos[col_pos] = search_val

    or_groups_by_pos = []
    for or_name, feats in or_groups.items():
        lst = []
        for inner_feat, col_idx in feats:
            col_pos = col_idx - 1
            if col_pos < 0 or col_pos >= ncols:
                continue                    # mirrors the pandas OR skip
            if "&" in inner_feat:
                parts = [p.strip() for p in inner_feat.split("&") if p.strip()]
                search_val = "".join(_normalize_for_code(gv(p)) for p in parts)
            else:
                search_val = _normalize_for_code(gv(inner_feat))
            lst.append((col_pos, search_val))
        or_groups_by_pos.append((or_name, lst))

    return code_db.lookup_code(group_l, search_by_pos, or_groups_by_pos)


def build_search_plan(group, type, group_l, type_l, feature_vars):
    """Read-only variant of the ``_assign_code_via_sqlite`` plan-building step.

    Returns ``(search_by_pos, feat_name_by_pos)`` — the same
    ``{col_pos: normalized_value}`` map the real lookup uses, plus a
    ``{col_pos: feature_name}`` map for reporting — or ``None`` when the
    SQLite path is not available. Used only by the Engineering Assistant's
    "why no code" diagnostic; never called from the coding hot path.
    """
    from . import code_db
    if not code_db.has_db(group_l):
        return None

    json_file_path = json_path("asign_code.json")
    if not os.path.exists(json_file_path):
        return None
    from .regex_patterns import load_json_file, _file_mtime

    json_mtime = _file_mtime(json_file_path)
    if CODE_MAPPING_CACHE.get("__asign_mtime__") != json_mtime:
        CODE_TABLE_CACHE.clear()
        CODE_MAPPING_CACHE.clear()
        CODE_MAPPING_CACHE["__asign_mtime__"] = json_mtime
    mapping_all = CODE_MAPPING_CACHE.get("__asign_all__")
    if mapping_all is None:
        mapping_all = load_json_file(json_file_path)
        CODE_MAPPING_CACHE["__asign_all__"] = mapping_all

    plan = _get_assign_code_feature_plan(group_l, type_l, mapping_all)
    if plan is None:
        return None
    required_feats, _or_groups = plan
    if len(required_feats) < 2:
        return None

    ncols = len(code_db.column_names(group_l))

    def gv(field_name):
        return _resolve_feature_value(group, type, group_l, type_l, feature_vars, field_name)

    search_by_pos = {}
    feat_name_by_pos = {}
    for feat_key, col_idx, is_combined, parts in required_feats:
        col_pos = col_idx - 1
        if col_pos < 0 or col_pos >= ncols:
            return None
        if is_combined:
            normalized_vals = [_normalize_for_code(gv(p)) for p in parts]
            search_val = "".join(v for v in normalized_vals if v != "null")
        else:
            search_val = _normalize_for_code(gv(parts[0]))
        search_by_pos[col_pos] = search_val
        feat_name_by_pos[col_pos] = feat_key if is_combined else parts[0]

    return search_by_pos, feat_name_by_pos


def assign_code_from_csv(group, type, feature_vars):
    """
    منطق کددهی مثل نسخه قبلی باقی مانده است، اما گلوگاه سرعت حذف شده است:
    - CSV/JSON فقط یک بار خوانده می‌شوند.
    - ستون‌های CSV یک بار normalize و index می‌شوند.
    - mapping هر group/type یک بار parse می‌شود.
    """
    group_l = str(group).lower()
    type_l = str(type).lower()

    cache_key = (
        group_l,
        type_l,
        tuple(sorted((str(k), str(v)) for k, v in feature_vars.items()))
    )

    if cache_key in ASSIGN_CODE_RESULT_CACHE:
        return ASSIGN_CODE_RESULT_CACHE[cache_key]

    # Fast path: when this group has a SQLite code database, resolve the code with
    # indexed SQL (no multi-million-row pandas table in RAM). Falls through to the
    # original CSV/pandas path when the group has no DB or it can't be used.
    try:
        from . import code_db
        if code_db.has_db(group_l):
            sqlite_result = _assign_code_via_sqlite(group, type, group_l, type_l, feature_vars)
            if sqlite_result is not None:
                ASSIGN_CODE_RESULT_CACHE[cache_key] = sqlite_result
                return sqlite_result
    except Exception:
        pass  # any DB issue -> use the original path below

    df, mapping_all = load_code_resources(group_l)
    if df is None or mapping_all is None:
        ASSIGN_CODE_RESULT_CACHE[cache_key] = ""
        return ""

    plan = _get_assign_code_feature_plan(group_l, type_l, mapping_all)
    if plan is None:
        ASSIGN_CODE_RESULT_CACHE[cache_key] = ""
        return ""

    required_feats, or_groups = plan

    def get_feature_value(field_name):
        # Shared with the SQLite fast path so both resolve values identically.
        return _resolve_feature_value(group, type, group_l, type_l, feature_vars, field_name)

    # Build candidate sets only from the features that actually have values.
    # The previous implementation started with set(range(len(df))).  For the
    # fitting table this allocates/intersects a 1M+ item set for every live edit.
    # Starting from the first matched indexed set keeps the matching algorithm
    # identical but removes the largest per-request allocation.
    candidate_indices = None

    for _feat_key, col_idx, is_combined, parts in required_feats:
        col_pos = col_idx - 1
        if col_pos < 0 or col_pos >= df.shape[1]:
            candidate_indices = set()
            break

        if is_combined:
            vals = [get_feature_value(p) for p in parts]
            normalized_vals = [_normalize_for_code(v) for v in vals]
            search_val = "".join(v for v in normalized_vals if v != "null")
        else:
            search_val = _normalize_for_code(get_feature_value(parts[0]))

        # An empty value (e.g. no coating) matches empty/null cells rather than
        # failing the whole lookup; _matched_indices_for_code_value handles "".
        matched = _matched_indices_for_code_value(group_l, df, col_pos, search_val)
        if candidate_indices is None:
            candidate_indices = matched
        else:
            # Intersect smaller set first to keep Python work minimal.
            if len(matched) < len(candidate_indices):
                candidate_indices = matched & candidate_indices
            else:
                candidate_indices &= matched
        if not candidate_indices:
            break

    if candidate_indices is None:
        candidate_indices = set()

    if candidate_indices and or_groups:
        for _or_group, feats in or_groups.items():
            union_matched = set()
            any_or_value = False

            for inner_feat, col_idx in feats:
                col_pos = col_idx - 1
                if col_pos < 0 or col_pos >= df.shape[1]:
                    continue

                if "&" in inner_feat:
                    parts = [p.strip() for p in inner_feat.split("&") if p.strip()]
                    vals = [get_feature_value(p) for p in parts]
                    search_val = "".join(_normalize_for_code(v) for v in vals)
                else:
                    search_val = _normalize_for_code(get_feature_value(inner_feat))

                if search_val == "":
                    continue

                any_or_value = True
                union_matched |= _matched_indices_for_code_value(group_l, df, col_pos, search_val)

            # Empty OR group (e.g. unused SCH/SDR on a flange with CLASS) is
            # not applicable — skip it. Do not intersect with the empty set.
            if not any_or_value:
                continue

            candidate_indices &= union_matched
            if not candidate_indices:
                break

    code_value = ""
    if candidate_indices:
        chosen_idx = min(candidate_indices)
        try:
            code_value = str(df.iloc[chosen_idx, 1]).strip()
        except Exception:
            code_value = ""

    ASSIGN_CODE_RESULT_CACHE[cache_key] = code_value
    return code_value
