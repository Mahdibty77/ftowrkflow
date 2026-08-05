"""Per-group feature schema (main/sub/info), code building, seeding, and rules.

Column classification is decided ONLY by the uploaded header marker:
    "<title>"            -> main  (primary attribute: has codes, drives the code,
                                   cascades by rules.json in the item builder)
    "<title> (main)"     -> main  (optional explicit marker; stripped from the name)
    "<title> (not main)" -> sub   (secondary attribute: no code, no rules; shown
                                   after the main features in the builder)
    "<title> (info)"     -> info  (price / weight / data column: no code, never in
                                   the builder, editable per row)
Columns 0 and 1 are always the Technical (big) and Item (small) codes.

Features are always taken from the uploaded table headers. A feature_schema
JSON (when present) only supplies value→code maps and small-code ranks for
matching main columns — it does NOT rename columns, force a fixed count, or
demote extra mains to info.

Code construction uses MAIN features only:
  technical = tech_start + tech_group + concat(code of every main feature)
  item      = item_start + item_group + concat(code of the small-code features,
              ordered by small_order 1 then 2) + sequence.zfill(item_seq_digits)
The per-prefix sequence is read from the group's SQLite table (max + 1), so the
small code is unique; the technical code must also be unique (duplicate guard).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .models import GroupCodeConfig, GroupFeature, FeatureValue
from .resource_paths import RESOURCE_DIR

_INFO_RE = re.compile(r"\s*\(\s*info\s*\)\s*$", re.I)
_NOT_MAIN_RE = re.compile(r"\s*\(\s*not\s*main\s*\)\s*$", re.I)
_MAIN_RE = re.compile(r"\s*\(\s*main\s*\)\s*$", re.I)

# ---------------------------------------------------------------------------
# Reference-file caches. rules_*.json and feature_schema/*.json are static data
# (only the admin changes them, through _save_rules / the data admin). Reading
# and parsing them once per row was the dominant cost of Build TO (a 380-row
# offer re-read the same files ~3400 times). Every cache below is keyed by the
# file's mtime, so any on-disk edit is picked up automatically on the next call
# WITHOUT changing a single coding/coloring result. clear_builder_caches()
# drops them explicitly after a write (see _save_rules) for immediate freshness.
# ---------------------------------------------------------------------------
_JSON_FILE_CACHE: Dict[str, tuple] = {}          # path -> (mtime, parsed_json)
_SCHEMA_MAPS_CACHE: Dict[str, tuple] = {}         # path -> (mtime, (feats, cfg, small))
_SCHEMA_VALUE_INDEX_CACHE: Dict[str, tuple] = {}  # path -> (mtime, (feat_of, pos_of))
_GROUP_RULES_CACHE: Dict[str, tuple] = {}         # path -> (mtime, group_rule_dict)
_ALL_RULES_CACHE: dict = {"sig": None, "data": None, "checked_at": 0.0}
_ALL_RULES_KEYS_CACHE: dict = {"data_id": None, "keys": None}  # normalized keys per group
_SIZE_NORMS_CACHE: Dict[str, set] = {}  # group -> set of normalized size spellings
_ALL_RULES_CHECK_INTERVAL_SEC = 1.0


def clear_builder_caches() -> None:
    """Drop every reference-file cache (call after writing rules/schema)."""
    _JSON_FILE_CACHE.clear()
    _SCHEMA_MAPS_CACHE.clear()
    _SCHEMA_VALUE_INDEX_CACHE.clear()
    _GROUP_RULES_CACHE.clear()
    _SIZE_NORMS_CACHE.clear()
    _ALL_RULES_CACHE["sig"] = None
    _ALL_RULES_CACHE["data"] = None
    _ALL_RULES_CACHE["checked_at"] = 0.0
    _ALL_RULES_KEYS_CACHE["data_id"] = None
    _ALL_RULES_KEYS_CACHE["keys"] = None
    try:
        _norm_val_cached.cache_clear()
    except Exception:
        pass


def _mtime_or_none(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _read_json_file_cached(path: str):
    """Parse a JSON file, caching the result by mtime (None on any failure)."""
    mtime = _mtime_or_none(path)
    if mtime is None:
        return None
    cached = _JSON_FILE_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    _JSON_FILE_CACHE[path] = (mtime, data)
    return data


def schema_file(group: str) -> str:
    return os.path.join(str(RESOURCE_DIR), "json", "feature_schema",
                        f"{str(group).strip().lower()}.json")


def has_schema_file(group: str) -> bool:
    return os.path.exists(schema_file(group))


def _norm_name(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def classify_header(title: str) -> Tuple[str, str]:
    """Return (display_name, kind) from a column header, by marker only."""
    raw = str(title or "").strip()
    if _INFO_RE.search(raw):
        return _INFO_RE.sub("", raw).strip(), GroupFeature.INFO
    if _NOT_MAIN_RE.search(raw):
        return _NOT_MAIN_RE.sub("", raw).strip(), GroupFeature.SUB
    if _MAIN_RE.search(raw):
        return _MAIN_RE.sub("", raw).strip(), GroupFeature.MAIN
    return raw, GroupFeature.MAIN


# Back-compat helper used by a couple of callers.
def clean_header(title: str) -> Tuple[str, bool]:
    name, kind = classify_header(title)
    return name, (kind != GroupFeature.MAIN)


def _load_schema_maps(group: str):
    """Return (ordered_features, config, small_indexes).

    ordered_features = [{"name": str, "vmap": {value: code}}, ...] in file order.
    """
    path = schema_file(group)
    mtime = _mtime_or_none(path)
    if mtime is None:
        return [], {}, []
    cached = _SCHEMA_MAPS_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = _read_json_file_cached(path)
    if not isinstance(data, dict):
        result = ([], {}, [])
        _SCHEMA_MAPS_CACHE[path] = (mtime, result)
        return result
    feats = data.get("features", []) or []
    ordered = []
    for f in feats:
        ordered.append({
            "name": str(f.get("name", "")).strip(),
            "vmap": {str(v.get("value", "")).strip(): str(v.get("code", "")).strip()
                     for v in (f.get("values", []) or []) if str(v.get("value", "")).strip()},
        })
    cfg = data.get("config", {}) or {}
    small_idx = [int(i) for i in (cfg.get("item_feature_indexes") or [])]
    result = (ordered, cfg, small_idx)
    _SCHEMA_MAPS_CACHE[path] = (mtime, result)
    return result


def seed_group_from_table(group: str, *, force: bool = False) -> int:
    """Build GroupFeature / FeatureValue / GroupCodeConfig from the code-table
    columns. Classification is by header marker only. Idempotent unless force.

    Header names and kinds always win. When a feature_schema JSON exists it is
    used only to attach value→code maps (and small-code ranks) to main columns
    — never to rename features or demote extras to info.

    Schema codes attach in two passes: name match first (SIZE→size), then
    positional fallback for remaining mains (legacy MATERIAL_GROUP layout).
    """
    group = str(group).strip().lower()
    from . import code_db
    columns = code_db.column_names(group)
    if not columns:
        from .models import CodeTable
        meta = CodeTable.objects.filter(group=group).first()
        columns = list(meta.columns) if (meta and meta.columns) else []
    if not columns:
        raise ValueError(f"Group '{group}' has no code table columns yet.")

    if not force and GroupFeature.objects.filter(group=group).exists():
        return GroupFeature.objects.filter(group=group).count()
    if force:
        GroupFeature.objects.filter(group=group).delete()
        FeatureValue.objects.filter(group=group).delete()

    schema_feats, cfg, small_idx = _load_schema_maps(group)
    schema_by_norm = {_norm_name(sf["name"]): (i + 1, sf)
                      for i, sf in enumerate(schema_feats)}

    # Code config: keep an existing one (set at upload) unless the schema file
    # provides values and none exists yet.
    if not GroupCodeConfig.objects.filter(group=group).exists() and cfg:
        GroupCodeConfig.objects.create(
            group=group,
            tech_start=str(cfg.get("tech_start", "") or ""),
            tech_group=str(cfg.get("tech_group", "") or ""),
            item_start=str(cfg.get("item_start", "") or ""),
            item_group=str(cfg.get("item_group", "") or ""),
            item_seq_digits=int(cfg.get("item_seq_digits", 5) or 5),
        )

    classified = []  # (idx, name, kind)
    for idx, header in enumerate(columns):
        if idx in (0, 1):
            continue
        name, kind = classify_header(header)
        if not name:
            name = str(header or "").strip() or f"col_{idx}"
        classified.append((idx, name, kind))

    # Schema code attachment: name match first, then positional for leftovers.
    # idx -> (rank, schema_feature_dict)
    code_of = {}
    used_ranks = set()
    if schema_feats:
        for idx, name, kind in classified:
            if kind != GroupFeature.MAIN:
                continue
            hit = schema_by_norm.get(_norm_name(name))
            if hit is not None and hit[0] not in used_ranks:
                code_of[idx] = hit
                used_ranks.add(hit[0])
        schema_ptr = 0
        for idx, name, kind in classified:
            if kind != GroupFeature.MAIN or idx in code_of:
                continue
            while schema_ptr < len(schema_feats) and (schema_ptr + 1) in used_ranks:
                schema_ptr += 1
            if schema_ptr >= len(schema_feats):
                break
            rank = schema_ptr + 1
            code_of[idx] = (rank, schema_feats[schema_ptr])
            used_ranks.add(rank)
            schema_ptr += 1

    n = 0
    for idx, name, kind in classified:
        rank, sf = code_of.get(idx, (0, None))
        small_order = 0
        if kind == GroupFeature.MAIN and rank and rank in small_idx:
            small_order = small_idx.index(rank) + 1  # 1 or 2
        GroupFeature.objects.create(group=group, name=name, position=idx, kind=kind,
                                    in_small_code=bool(small_order),
                                    small_order=small_order, column_index=idx)
        n += 1
        if kind == GroupFeature.MAIN and sf is not None and sf["vmap"]:
            FeatureValue.objects.bulk_create(
                [FeatureValue(group=group, feature=name, value=val, code=code)
                 for val, code in sf["vmap"].items()], ignore_conflicts=True)
    return n


def seed_group_from_file(group: str, *, force: bool = False) -> int:
    return seed_group_from_table(group, force=force)


def value_code_map(group: str, feature: str) -> Dict[str, str]:
    return {fv.value: fv.code for fv in
            FeatureValue.objects.filter(group=group, feature=feature)}


def next_value_code(group: str, feature: str, width: int = 2) -> str:
    mx, w = 0, width
    for fv in FeatureValue.objects.filter(group=group, feature=feature):
        c = str(fv.code).strip()
        w = max(w, len(c))
        if c.isdigit():
            mx = max(mx, int(c))
    return str(mx + 1).zfill(w)


def main_features(group: str) -> List[GroupFeature]:
    return list(GroupFeature.objects.filter(group=group, kind=GroupFeature.MAIN)
                .order_by("position", "name"))


def sub_features(group: str) -> List[GroupFeature]:
    return list(GroupFeature.objects.filter(group=group, kind=GroupFeature.SUB)
                .order_by("position", "name"))


def info_features(group: str) -> List[GroupFeature]:
    return list(GroupFeature.objects.filter(group=group, kind=GroupFeature.INFO)
                .order_by("position", "name"))


def small_code_features(group: str) -> List[GroupFeature]:
    return list(GroupFeature.objects.filter(group=group, small_order__gt=0)
                .order_by("small_order"))


def _is_empty_variant(v) -> bool:
    """True when a value means 'absent' for code matching: empty, null,
    '$coating$' (schema placeholder) or '(no)coating' (upload convention)."""
    s = str(v or "").strip()
    if not s or s.lower() == "null":
        return True
    if re.match(r"^\(no\)", s, re.I):
        return True
    if re.match(r"^\$.*\$$", s):
        return True
    return False


def build_codes(group: str, selected: Dict[str, str]) -> Tuple[str, str, str]:
    """Build (technical_code, item_code, small_prefix) from chosen MAIN values."""
    group = str(group).strip().lower()
    cfg = GroupCodeConfig.objects.filter(group=group).first()
    tech_start = cfg.tech_start if cfg else ""
    tech_group = cfg.tech_group if cfg else ""
    item_start = cfg.item_start if cfg else ""
    item_group = cfg.item_group if cfg else ""
    seq_digits = cfg.item_seq_digits if cfg else 5

    feats = main_features(group)

    def _ci_map(feature):
        # case-insensitive value -> code (values may be shown/stored uppercased)
        return {_norm_val(v): c for v, c in value_code_map(group, feature).items()}
    code_maps = {f.name: _ci_map(f.name) for f in feats}

    tech = [tech_start, tech_group]
    for f in feats:
        val = (selected.get(f.name) or "").strip()
        tech.append(code_maps.get(f.name, {}).get(_norm_val(val), ""))
    technical_code = "".join(tech)

    small = [item_start, item_group]
    for f in small_code_features(group):
        val = (selected.get(f.name) or "").strip()
        small.append(code_maps.get(f.name, {}).get(_norm_val(val), ""))
    prefix = "".join(small)

    from . import code_db
    seq = code_db.next_sequence(group, prefix) if code_db.has_db(group) else 1
    item_code = prefix + str(seq).zfill(seq_digits)
    return technical_code, item_code, prefix


def build_row_cells(group: str, selected: Dict[str, str],
                    technical_code: str, item_code: str) -> List[str]:
    """col0=technical, col1=item, main+sub features write their column; info blank."""
    from .models import CodeTable
    from . import code_db
    ncols = len(code_db.column_names(group))
    if not ncols:
        meta = CodeTable.objects.filter(group=group).first()
        ncols = len(meta.columns) if (meta and meta.columns) else 2
    cells = [""] * max(ncols, 2)
    cells[0] = technical_code
    cells[1] = item_code
    for f in GroupFeature.objects.filter(group=group):
        if f.kind in (GroupFeature.MAIN, GroupFeature.SUB) and \
                0 <= f.column_index < len(cells) and f.column_index not in (0, 1):
            val = (selected.get(f.name) or "").strip()
            # The "no/absent" variant is stored as an empty cell so it matches
            # existing empty rows; other values are stored upper-cased.
            cells[f.column_index] = "" if _is_empty_variant(val) else val.upper()
    return cells


# --------------------------------------------------------------------------- #
# Cascading rules (item builder, main features only). rules.json is authored by
# the admin. Shape: { "<group>": { "<value>": ["allowed", ...] }, ... } or a flat
# { "<value>": ["allowed", ...] }. A value V is allowed for the next feature only
# if, for every already-chosen value P, V is listed under P. Absent file / group
# -> no filtering.
# --------------------------------------------------------------------------- #
def _rules_path(group: str = "") -> str:
    """Path to a group's rules file.

    Each group now has its OWN rules file (e.g. rules_pipe.json) so the data
    stays tidy and one group can be replaced without touching another. The old
    shared rules.json is still read as a fallback for groups that have not been
    migrated yet.
    """
    g = str(group).strip().lower()
    if g:
        return os.path.join(str(RESOURCE_DIR), "json", f"rules_{g}.json")
    return os.path.join(str(RESOURCE_DIR), "json", "rules.json")


def _legacy_rules_path() -> str:
    return os.path.join(str(RESOURCE_DIR), "json", "rules.json")


def _load_rules(group: str = ""):
    """Load rules for a group. Prefers the per-group file; falls back to the
    legacy shared rules.json. Returns the group's rule dict wrapped as
    {group: {...}} to keep the historical shape the rest of the code expects."""
    g = str(group).strip().lower()
    if g:
        p = _rules_path(g)
        data = _read_json_file_cached(p)
        # A per-group file may be stored either as {group:{...}} or as the
        # bare {...} rule map. Normalise to {group:{...}}.
        if isinstance(data, dict):
            if g in {str(k).lower() for k in data.keys()}:
                return data
            return {g: data}
    # Fallback: the legacy shared file.
    return _read_json_file_cached(_legacy_rules_path())


def has_rules(group: str) -> bool:
    data = _load_rules(group)
    if not isinstance(data, dict) or not data:
        return False
    g = str(group).strip().lower()
    if g in {str(k).lower() for k in data.keys()}:
        return True
    return not any(isinstance(v, dict) for v in data.values())


def _group_rules(group: str) -> Dict[str, list]:
    g = str(group).strip().lower()
    # Cache the resolved per-group rule map keyed by the source file's mtime.
    # Key includes the group so groups sharing the legacy file never collide.
    src_path = _rules_path(g) if g and os.path.exists(_rules_path(g)) else _legacy_rules_path()
    mtime = _mtime_or_none(src_path)
    cache_key = f"{src_path}|{g}"
    if mtime is not None:
        cached = _GROUP_RULES_CACHE.get(cache_key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    data = _load_rules(group)
    if not isinstance(data, dict):
        result = {}
    else:
        result = {}
        for k, v in data.items():
            if str(k).lower() == g and isinstance(v, dict):
                result = v
                break
        else:
            if data and not any(isinstance(v, dict) for v in data.values()):
                result = data
    if mtime is not None:
        _GROUP_RULES_CACHE[cache_key] = (mtime, result)
    return result


def _norm_val(s: str) -> str:
    """Normalize feature values for rules lookup.

    Keeps ``/`` (and ``.``) so fractional sizes like ``3/4"`` do not collide
    with whole-inch sizes like ``34"`` (both used to collapse to ``34`` when
    only alphanumerics were kept).
    """
    return _norm_val_cached(str(s) if s is not None else "")


@lru_cache(maxsize=16384)
def _norm_val_cached(s: str) -> str:
    return "".join(
        ch for ch in s.strip().lower()
        if ch.isalnum() or ch in "/."
    )


def _allowed_set_for_feature(entry, feature_name):
    """Normalized allowed-value set that a rule entry imposes on ONE feature.

    Nested entry {"feat":[vals], ...}: returns the set for the matching feature
    key, or None when the entry says nothing about this feature (so it must NOT
    constrain it). Flat list (legacy): applies to every feature. Matching values
    strictly per feature avoids collisions like size 60" vs schedule 60."""
    if isinstance(entry, list):
        return {_norm_val(x) for x in entry}
    if isinstance(entry, dict):
        tgt = _norm_name(feature_name)
        for k, vals in entry.items():
            if _norm_name(k) == tgt and isinstance(vals, list):
                return {_norm_val(x) for x in vals}
        return None
    return None


def _flat_allowed(entry) -> set:
    """Allowed-value set from a rule entry across ALL features (kept for any
    legacy callers); per-feature logic should use _allowed_set_for_feature."""
    if isinstance(entry, dict):
        s = set()
        for vals in entry.values():
            if isinstance(vals, list):
                s |= {_norm_val(x) for x in vals}
        return s
    if isinstance(entry, list):
        return {_norm_val(x) for x in entry}
    return set()


def _lookup_rule_entry(group: str, rules: dict, value: str):
    """Find a rules entry for ``value`` in the group map or file-level sidecars.

    Per-group files often keep design-standard / NACE blocks next to the
    ``{group: {Type: {...}}}`` tree, e.g. ``\"Asme B16.9\": {\"size\": [...],
    \"schedule\": [...]}``. Those sidecars must constrain cascades the same
    way type-level entries do.
    """
    if not value:
        return None
    pv = str(value).strip()
    if rules:
        if pv in rules:
            return rules[pv]
        nv = _norm_val(pv)
        for k, entry in rules.items():
            if _norm_val(k) == nv:
                return entry
    full = _load_rules(group) or {}
    if not isinstance(full, dict):
        return None
    g = str(group).strip().lower()
    nv = _norm_val(pv)
    for k, entry in full.items():
        if str(k).strip().lower() == g:
            continue
        if not isinstance(entry, (dict, list)):
            continue
        if str(k).strip() == pv or _norm_val(k) == nv:
            return entry
    return None


def allowed_values(group: str, target_feature: str, selected: Dict[str, str]) -> List[str]:
    """Allowed values of target_feature given prior MAIN selections (cascade).

    For each already-chosen value we intersect target_feature's values with the
    chosen value's list FOR target_feature specifically (nested rules.json). A
    value with no entry for the target feature imposes no limit. Matching per
    feature prevents cross-feature normalization collisions.
    """
    group = str(group).strip().lower()
    all_vals = sorted(value_code_map(group, target_feature).keys())
    if not all_vals:
        # A feature that shares its code-table column with siblings in a
        # compound asign_code.json group (e.g. "material" in
        # "material & grade_material & spec") never gets its own column,
        # so it was often never seeded into FeatureValue at all — that table
        # is populated per code-table COLUMN, and a compound feature has no
        # column of its own. Fall back to its raw value list straight from
        # the feature schema file (feature_schema/<group>.json), which is
        # keyed by feature NAME, not by column, and always has one. This
        # only ever runs when the DB-backed list above is genuinely empty —
        # it adds a source of values, it never removes or overrides one.
        try:
            feats, _cfg, _s = _load_schema_maps(group)
            for f in feats:
                if str(f.get("name", "")).strip().lower() == str(target_feature).strip().lower():
                    all_vals = sorted((f.get("vmap") or {}).keys())
                    break
        except Exception:
            pass
    rules = _group_rules(group)
    if not rules:
        return all_vals
    chosen = [v for v in selected.values() if str(v).strip()]
    if not chosen:
        return all_vals
    result = {_norm_val(v) for v in all_vals}
    for pv in chosen:
        entry = _lookup_rule_entry(group, rules, pv)
        if entry is None:
            continue
        sub = _allowed_set_for_feature(entry, target_feature)
        if sub is None:
            continue            # this value places no limit on the target feature
        result &= sub
    return [v for v in all_vals if _norm_val(v) in result]


# --------------------------------------------------------------------------- #
# rules.json compatibility check for the codify tool (orange highlighting).
# Given the feature values found on a line, return the set of values that are
# mutually incompatible per rules_*.json (e.g. S.S + ASTM A106 Gr.B when S.S
# only allows stainless materials). Used additively by the tool so a
# remark/correction that breaks a rule is highlighted.
# --------------------------------------------------------------------------- #

# Nested keys in rules_*.json → feature names that may hold the live value.
# Coding uses material_type for production methods; the item-builder schema
# often names that column production_method. Compound "material" is joined
# from asign_code.json members (material + grade_material + …).
_RULE_FEAT_SOURCES = {
    "material_group": ("material_group",),
    "material_type": ("material_type", "production_method"),
    "production_method": ("production_method", "material_type"),
    "material": ("material",),
    "schedule": ("schedule", "phisic_sch"),
    "phisic_sch": ("phisic_sch", "schedule"),
    "design_standard": ("design_standard",),
    "size": ("size",),
}


def _schema_value_index(group: str):
    """Return (value_norm -> feature_name, value_norm -> position) from the
    feature schema, so values can be ordered and their feature identified."""
    path = schema_file(group)
    mtime = _mtime_or_none(path)
    if mtime is not None:
        cached = _SCHEMA_VALUE_INDEX_CACHE.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    feats, _cfg, _small = _load_schema_maps(group)
    feat_of, pos_of = {}, {}
    for i, f in enumerate(feats):
        for val in f["vmap"].keys():
            nv = _norm_val(val)
            feat_of[nv] = f["name"]
            pos_of[nv] = i
    result = (feat_of, pos_of)
    if mtime is not None:
        _SCHEMA_VALUE_INDEX_CACHE[path] = (mtime, result)
    return result


def _schedule_norm(s: str) -> str:
    """Normalize schedule tokens so ``SCHXS`` / ``SCH 40`` match ``XS`` / ``40``."""
    n = _norm_val(s)
    if n.startswith("sch"):
        n = n[3:]
    return n


def _is_strict_prefix_extension(shorter: str, longer: str) -> bool:
    """True when ``longer`` extends ``shorter`` with a non-numeric continuation.

    Intended for partial extracts vs full rules entries, e.g. ``astma216`` →
    ``astma216grwcb``. Rejects digit-extends-digit cases so numbered codes stay
    exact: ``apitrim1`` must not match ``apitrim13``, and ``class150`` must not
    match ``class1500``.
    """
    if not shorter or not longer or longer == shorter:
        return False
    if not longer.startswith(shorter):
        return False
    rest = longer[len(shorter):]
    if shorter[-1].isdigit() and rest[:1].isdigit():
        return False
    return True


def _is_allowed_value(candidate: str, allowed_set: set) -> bool:
    """True when ``candidate`` matches an allowed rules value.

    Exact normalized match, or a *strict* prefix extension so a partial extract
    (``ASTM A216``) still matches a full allowed entry (``ASTM A216 Gr.WCB``),
    without treating ``API Trim 13`` as allowed under ``API Trim 1``.
    Schedule values also match with an optional ``SCH`` prefix stripped.
    """
    nc = _norm_val(candidate)
    if not nc:
        return True
    if nc in allowed_set:
        return True
    for a in allowed_set:
        if not a:
            continue
        if len(a) > len(nc):
            if _is_strict_prefix_extension(nc, a):
                return True
        elif len(nc) > len(a):
            if _is_strict_prefix_extension(a, nc):
                return True
    # SCHXS ↔ XS, SCH40 ↔ 40, etc.
    ns = _schedule_norm(candidate)
    if ns and ns != nc:
        if ns in allowed_set:
            return True
        for a in allowed_set:
            if _schedule_norm(a) == ns:
                return True
    return False


def _leading_family_token(normed: str) -> str:
    """Leading alphabetic run of a normalized value (before the first digit).

    Examples: ``astma216grwcb`` → ``astma``, ``class600`` → ``class``,
    ``pn16`` → ``pn``, ``boltedbonnet/cover`` → ``boltedbonnet``.
    Used to decide domain membership without mistaking unrelated values that
    only share a short prefix (``bolted`` bonnet vs ``bolted``-on yoke).
    """
    s = str(normed or "")
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    return s[:i]


def _belongs_to_allowed_domain(candidate: str, allowed_set: set) -> bool:
    """True when ``candidate`` looks like it belongs to this allowed-list's domain.

    Used so size/schedule values are not compared against a material list.
    Domain is the full leading alphabetic family token (ASTM/A…, CLASS, PN, …),
    not a short shared prefix — otherwise ``Bolted Bonnet`` falsely matches a
    ``Bolted-on Yoke`` allow-list via ``bolt``.
    """
    nc = _norm_val(candidate)
    if not nc or not allowed_set:
        return False
    if _is_allowed_value(candidate, allowed_set):
        return True
    fam = _leading_family_token(nc)
    # PN / EN / DN are valid 2-letter families; anything shorter is noise.
    if len(fam) < 2:
        return False
    for a in allowed_set:
        if fam == _leading_family_token(a):
            return True
    return False


def _fmap_get(feature_map: dict, name: str):
    """Case/format-tolerant lookup in a feature_name → value map."""
    if not feature_map or not name:
        return None
    if name in feature_map:
        return feature_map[name]
    tgt = _norm_name(name)
    for k, v in feature_map.items():
        if _norm_name(k) == tgt:
            return v
    return None


def _resolve_rule_feature_current(rule_feat, feature_map, group: str, type_: str, allowed_set: set):
    """Resolve the live value for a nested rules key.

    Returns ``(check_string, parts_to_flag)`` or ``None`` when that feature is
    absent on the row (no orange — missing is not a violation).
    """
    if not feature_map:
        return None
    rf = str(rule_feat or "").strip().lower()
    sources = _RULE_FEAT_SOURCES.get(rf, (rf,))
    # Each candidate: (check_string, parts, trusted)
    # trusted=True when read from the primary feature name for this rules key
    # (or its compound), so we always validate even without a shared prefix.
    candidates = []

    for src in sources:
        primary = _norm_name(src) == _norm_name(rf)
        parts = []
        members = None
        if group and type_:
            try:
                from .composite_features import compound_group_members
                members = compound_group_members(group, type_, src)
            except Exception:
                members = None
        if members:
            for m in members:
                v = _fmap_get(feature_map, m)
                if v is not None and str(v).strip():
                    parts.append(str(v).strip())
            if parts:
                candidates.append((" ".join(parts), parts, primary))
                continue
        v = _fmap_get(feature_map, src)
        if v is not None and str(v).strip():
            s = str(v).strip()
            candidates.append((s, [s], primary))

    if not candidates:
        return None

    def _score(cand, trusted):
        score = 0
        if _is_allowed_value(cand, allowed_set):
            score += 100
        elif _belongs_to_allowed_domain(cand, allowed_set):
            score += 50
        if trusted:
            score += 30
        return score

    best = max(candidates, key=lambda c: _score(c[0], c[2]))
    cand, parts, trusted = best
    score = _score(cand, trusted)
    # Alias-only hit that does not look like this list's domain (e.g. schema
    # material_type=S.S against a production-method allow-list) → ignore.
    if not trusted and score < 50:
        return None
    return (cand, parts)


def _iter_feature_rule_subjects(feature_map: dict, group: str, type_: str = ""):
    """Yield ``(lookup_value, parts_to_flag)`` for rules-key checks.

    Includes every live feature value, plus joined compound units from
    ``asign_code.json`` (e.g. material + grade_material → ``ASTM A216 Gr.WCB``)
    so nested allow-lists under that rules key are actually evaluated.
    """
    seen = set()
    for _feat, val in list(feature_map.items()):
        if val is None or not str(val).strip():
            continue
        s = str(val).strip()
        n = _norm_val(s)
        if not n or n in seen:
            continue
        seen.add(n)
        yield s, [s]

    try:
        from .composite_features import compound_groups
        seen_members = set()
        for members in compound_groups(group, type_).values():
            if members in seen_members:
                continue
            seen_members.add(members)
            parts = []
            for m in members:
                v = _fmap_get(feature_map, m)
                if v is not None and str(v).strip():
                    parts.append(str(v).strip())
            if len(parts) < 2:
                continue
            joined = " ".join(parts)
            n = _norm_val(joined)
            if not n or n in seen:
                continue
            seen.add(n)
            yield joined, parts
    except Exception:
        return


def _flag_incompatible_by_features(feature_map: dict, group: str, type_: str = "") -> set:
    """Orange set using feature_name → value (coding / offer path)."""
    rules = _group_rules(group)
    if not rules or not feature_map:
        return set()
    rules_ci = {_norm_val(k): v for k, v in rules.items()}
    bad = set()

    for val, subject_parts in _iter_feature_rule_subjects(feature_map, group, type_):
        entry = rules_ci.get(_norm_val(val))
        if not isinstance(entry, (dict, list)):
            continue
        if isinstance(entry, list):
            allowed_set = {_norm_val(x) for x in entry}
            for _f2, v2 in feature_map.items():
                if v2 is None or not str(v2).strip():
                    continue
                if _norm_val(v2) == _norm_val(val):
                    continue
                if _belongs_to_allowed_domain(str(v2), allowed_set) and not _is_allowed_value(str(v2), allowed_set):
                    for p in subject_parts:
                        bad.add(p)
                    bad.add(str(v2).strip())
            continue
        for nested_feat, allowed in entry.items():
            if not isinstance(allowed, list):
                continue
            allowed_set = {_norm_val(x) for x in allowed}
            resolved = _resolve_rule_feature_current(
                nested_feat, feature_map, group, type_, allowed_set
            )
            if resolved is None:
                continue
            check_str, parts = resolved
            if _is_allowed_value(check_str, allowed_set):
                continue
            for p in subject_parts:
                if p:
                    bad.add(p)
            for p in parts:
                if p:
                    bad.add(p)
    return bad


def _flag_incompatible_by_values(values, group: str) -> set:
    """Fallback when only bare values are available (no feature names)."""
    values = [v for v in values if str(v).strip()]
    if len(values) < 2:
        return set()
    rules = _group_rules(group)
    if not rules:
        return set()
    feat_of, pos_of = _schema_value_index(group)
    rules_ci = {_norm_val(k): v for k, v in rules.items()}
    ordered = sorted(values, key=lambda v: pos_of.get(_norm_val(v), 999))
    bad = set()

    # Alias: schema production_method ↔ rules nested material_type, etc.
    def _feat_aliases(name: str):
        n = _norm_name(name)
        if n in (_norm_name("material_type"), _norm_name("production_method")):
            return {_norm_name("material_type"), _norm_name("production_method")}
        return {n} if n else set()

    def _collect_domain_parts(nested_feat, allowed_set, skip_vals):
        nested_aliases = _feat_aliases(nested_feat) | {_norm_name(nested_feat)}
        skip_n = {_norm_val(x) for x in skip_vals}
        domain_parts = []
        for vj in ordered:
            if _norm_val(vj) in skip_n:
                continue
            vj_feat = feat_of.get(_norm_val(vj))
            in_schema = vj_feat is not None and _norm_name(vj_feat) in nested_aliases
            # Known different feature → never fuzzy-match into this nest.
            if vj_feat is not None and not in_schema:
                continue
            in_domain = _belongs_to_allowed_domain(vj, allowed_set)
            if in_schema or in_domain:
                domain_parts.append(vj)
        return domain_parts

    def _flag_nested_dict(entry, subject_parts):
        for nested_feat, allowed in entry.items():
            if not isinstance(allowed, list):
                continue
            allowed_set = {_norm_val(x) for x in allowed}
            domain_parts = _collect_domain_parts(nested_feat, allowed_set, subject_parts)
            if not domain_parts:
                continue
            joined = " ".join(domain_parts)
            check = joined if len(domain_parts) > 1 else domain_parts[0]
            if _is_allowed_value(check, allowed_set):
                continue
            if len(domain_parts) > 1 and _is_allowed_value(domain_parts[0], allowed_set):
                continue
            for p in subject_parts:
                bad.add(p)
            for p in domain_parts:
                bad.add(p)

    # Compound units (material + grade → "ASTM A216 Gr.WCB") as rules subjects.
    try:
        from .composite_features import compound_groups
        by_feat = {}
        for v in ordered:
            f = feat_of.get(_norm_val(v))
            if f:
                by_feat[_norm_name(f)] = str(v).strip()
        seen_members = set()
        for members in compound_groups(group, "").values():
            if members in seen_members:
                continue
            seen_members.add(members)
            parts = [by_feat[m] for m in members if m in by_feat]
            if len(parts) < 2:
                continue
            joined = " ".join(parts)
            entry = rules_ci.get(_norm_val(joined))
            if isinstance(entry, dict):
                _flag_nested_dict(entry, parts)
            elif isinstance(entry, list):
                allowed_set = {_norm_val(x) for x in entry}
                for vj in ordered:
                    if vj in parts:
                        continue
                    if _belongs_to_allowed_domain(vj, allowed_set) and not _is_allowed_value(vj, allowed_set):
                        for p in parts:
                            bad.add(p)
                        bad.add(vj)
    except Exception:
        pass

    for i, vi in enumerate(ordered):
        entry = rules_ci.get(_norm_val(vi))
        if not isinstance(entry, (dict, list)):
            continue
        if isinstance(entry, list):
            allowed_set = {_norm_val(x) for x in entry}
            for vj in ordered[i + 1:]:
                if _belongs_to_allowed_domain(vj, allowed_set) and not _is_allowed_value(vj, allowed_set):
                    bad.add(vi)
                    bad.add(vj)
            continue
        _flag_nested_dict(entry, [vi])
    return bad


def _load_all_rules() -> dict:
    """Merge every group's rules into one {group: {...}} dict, reading each
    per-group file (rules_<group>.json) and the legacy shared file."""
    import glob
    import time
    now = time.monotonic()
    # Reuse merged dict within a short window — sig building alone was glob+stat
    # on every flag_incompatible call (once+ per row during TO restore).
    if (
        _ALL_RULES_CACHE["data"] is not None
        and (now - float(_ALL_RULES_CACHE.get("checked_at") or 0.0))
        < _ALL_RULES_CHECK_INTERVAL_SEC
    ):
        return _ALL_RULES_CACHE["data"]

    legacy = _legacy_rules_path()
    pattern = os.path.join(str(RESOURCE_DIR), "json", "rules_*.json")
    paths = ([legacy] if os.path.exists(legacy) else []) + sorted(glob.glob(pattern))
    # Cheap signature (paths + mtimes, no JSON parse). When unchanged, reuse the
    # already-merged dict instead of re-reading every rules file for each row.
    sig = tuple((p, _mtime_or_none(p)) for p in paths)
    _ALL_RULES_CACHE["checked_at"] = now
    if _ALL_RULES_CACHE["sig"] == sig and _ALL_RULES_CACHE["data"] is not None:
        return _ALL_RULES_CACHE["data"]

    merged: dict = {}
    # Legacy shared file first (lowest priority).
    if os.path.exists(legacy):
        d = _read_json_file_cached(legacy)
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    merged[str(k).lower()] = v
    # Per-group files override.
    for path in sorted(glob.glob(pattern)):
        base = os.path.basename(path)
        g = base[len("rules_"):-len(".json")].lower()
        d = _read_json_file_cached(path)
        if isinstance(d, dict):
            block = d.get(g) if g in {str(k).lower() for k in d.keys()} else d
            if isinstance(block, dict):
                merged[g] = block
    _ALL_RULES_CACHE["sig"] = sig
    _ALL_RULES_CACHE["data"] = merged
    return merged


def _detect_group_for_values(values) -> str:
    """Pick the rules group whose keys best match these values."""
    data = _load_all_rules()
    if not isinstance(data, dict):
        return ""
    # Normalized key sets per group are derived purely from ``data``; cache them
    # against the merged-rules object identity so they are computed once, not per
    # row. _load_all_rules returns the SAME dict while the files are unchanged.
    if _ALL_RULES_KEYS_CACHE["data_id"] != id(data):
        _ALL_RULES_KEYS_CACHE["keys"] = {
            str(g).lower(): {_norm_val(k) for k in block.keys()}
            for g, block in data.items() if isinstance(block, dict)
        }
        _ALL_RULES_KEYS_CACHE["data_id"] = id(data)
    keys_by_group = _ALL_RULES_KEYS_CACHE["keys"] or {}
    norm_vals = {_norm_val(v) for v in values if str(v).strip()}
    best, best_score = "", 0
    for g, keys in keys_by_group.items():
        score = len(norm_vals & keys)
        if score > best_score:
            best, best_score = g, score
    return best


def flag_incompatible(values, group: str = "", feature_map=None, type_: str = "") -> set:
    """Return the subset of values that violate ``rules_<group>.json``.

    ``feature_map`` (optional) is ``{feature_name: value}`` from the live row
    (e.g. material_group=S.S, material=ASTM A106, grade_material=Gr.B). When
    present, compound features are joined per asign_code.json and checked
    against the nested allow-lists under each rules key — this is what makes
    ``S.S`` + ``ASTM A106 Gr.B`` orange. Without it, a values-only fallback is
    used (pairwise offer-repair, etc.).
    """
    fmap = {}
    if isinstance(feature_map, dict):
        for k, v in feature_map.items():
            if v is not None and str(v).strip() and str(v).strip().lower() != "null":
                fmap[str(k)] = str(v).strip()

    value_list = [v for v in (values or []) if str(v).strip()]
    if not fmap and len(value_list) < 2:
        return set()

    group = (group or _detect_group_for_values(value_list or list(fmap.values()))).lower()
    if not group:
        return set()

    if fmap:
        return _flag_incompatible_by_features(fmap, group, type_ or "")
    return _flag_incompatible_by_values(value_list, group)


def _collect_rules_size_norms(group: str) -> set:
    """All normalized size spellings that appear anywhere in ``rules_<group>``.

    Cached per group — scanning the full rules map is too heavy to redo per call.
    """
    g = str(group or "").strip().lower()
    if not g:
        return set()
    cached = _SIZE_NORMS_CACHE.get(g)
    if cached is not None:
        return cached
    rules = _group_rules(g)
    out = set()
    if rules:
        for key, entry in rules.items():
            kn = _norm_val(key)
            if kn and any(ch.isdigit() for ch in kn) and (
                '"' in str(key) or "/" in str(key) or kn.endswith("in")
            ):
                out.add(kn)
            if not isinstance(entry, dict):
                continue
            for nest, vals in entry.items():
                if _norm_name(nest) != "size" or not isinstance(vals, list):
                    continue
                for x in vals:
                    n = _norm_val(x)
                    if n:
                        out.add(n)
    _SIZE_NORMS_CACHE[g] = out
    return out


def size_value_in_rules(group: str, size_value: str) -> bool:
    """True when ``size_value`` is a known size spelling in the group's rules."""
    if not size_value or not group:
        return False
    norms = _collect_rules_size_norms(group)
    if not norms:
        return False
    nv = _norm_val(size_value)
    if nv in norms:
        return True
    return _is_allowed_value(size_value, norms)


def _fmap_size_value(fmap: dict) -> Optional[str]:
    """Live size string from a feature map, if any."""
    if not fmap:
        return None
    for key in ("size", "Size"):
        v = _fmap_get(fmap, key)
        if v is not None and str(v).strip() and str(v).strip().lower() != "null":
            return str(v).strip()
    for k, v in fmap.items():
        if _norm_name(k) == "size" and v is not None and str(v).strip():
            return str(v).strip()
    return None


def refine_size_conflict(feature_map: dict, group: str, type_: str,
                         bad_values) -> Tuple[set, set]:
    """Size isolation for orange/red highlighting.

    Always separates ``size`` from other flagged values, re-checks the rest
    without size, and:

    * if the rest is compatible → only size stays flagged
    * if size is absent from ``rules_<group>`` → size is **red** (not orange)
    * otherwise size stays **orange** when it still conflicts

    Returns ``(orange_values, red_values)``.
    """
    bad = {str(v).strip() for v in (bad_values or []) if str(v).strip()}
    if not bad:
        return set(), set()

    fmap = {}
    if isinstance(feature_map, dict):
        for k, v in feature_map.items():
            if v is not None and str(v).strip() and str(v).strip().lower() != "null":
                fmap[str(k)] = str(v).strip()

    group = str(group or "").strip().lower()
    size_val = _fmap_size_value(fmap)
    if not size_val or not group:
        return bad, set()

    size_n = _norm_val(size_val)
    size_in_bad = any(_norm_val(v) == size_n for v in bad)
    # Even when size was not listed in ``bad`` (rare), still isolate if the
    # size spelling itself is unknown to rules and other values are orange.
    size_known = size_value_in_rules(group, size_val)
    if not size_in_bad and size_known:
        return bad, set()

    trial = {k: v for k, v in fmap.items() if _norm_name(k) != "size"}
    try:
        bad_rest = set(flag_incompatible([], group, feature_map=trial, type_=type_ or "") or [])
    except Exception:
        bad_rest = {v for v in bad if _norm_val(v) != size_n}

    # Prefer original spellings from ``bad`` / fmap for the rest.
    rest_orange = set()
    bad_rest_n = {_norm_val(v) for v in bad_rest}
    for v in bad:
        if _norm_val(v) == size_n:
            continue
        if _norm_val(v) in bad_rest_n:
            rest_orange.add(v)
    for v in bad_rest:
        if _norm_val(v) == size_n:
            continue
        if not any(_norm_val(x) == _norm_val(v) for x in rest_orange):
            rest_orange.add(v)

    # When rest is clean, only size remains flagged.
    if not bad_rest:
        if size_known:
            return {size_val}, set()
        return set(), {size_val}

    # Rest still conflicts: keep those oranges; size is red if unknown, else
    # orange when it participated in the original conflict.
    if not size_known:
        return rest_orange, {size_val}
    if size_in_bad:
        rest_orange.add(size_val)
    return rest_orange, set()


# --------------------------------------------------------------------------- #
# Writing rules.json when the admin adds a new feature value.
# A new value must be linked to at least one value of every OTHER main feature
# (upstream values gain the new value in their downstream list under this
# feature; the new value gains the chosen downstream values). Without links the
# value could never be reached in the item builder, so links are required.
# --------------------------------------------------------------------------- #
def _save_rules(data, group: str = "") -> None:
    """Write rules. When a group is given, write that group's own file
    (rules_<group>.json) storing just that group's rule map; otherwise write the
    legacy shared file."""
    g = str(group).strip().lower()
    if g:
        path = _rules_path(g)
        # Store the bare rule map for the group (normalised shape on load).
        payload = data.get(g, data) if isinstance(data, dict) else data
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({g: payload}, fh, ensure_ascii=False, indent=1)
        clear_builder_caches()
        return
    path = _legacy_rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    clear_builder_caches()


def replace_rules_from_obj(group: str, obj) -> None:
    """Replace a group's rules with an uploaded JSON object. Accepts either the
    bare rule map {...} or the wrapped {group:{...}} shape."""
    g = str(group).strip().lower()
    if not isinstance(obj, dict):
        raise ValueError("The uploaded file must be a JSON object.")
    block = obj.get(g) if g in {str(k).lower() for k in obj.keys()} else obj
    if not isinstance(block, dict):
        raise ValueError("The uploaded rules file has an unexpected shape.")
    _save_rules({g: block}, g)


def ensure_rules_file(group: str) -> str:
    """Make sure a per-group rules file exists (migrating from the legacy shared
    file if present, else an empty skeleton). Returns the path."""
    g = str(group).strip().lower()
    path = _rules_path(g)
    if not os.path.exists(path):
        block = {}
        legacy = _legacy_rules_path()
        if os.path.exists(legacy):
            try:
                d = json.load(open(legacy, encoding="utf-8"))
                if isinstance(d, dict):
                    for k, v in d.items():
                        if str(k).lower() == g and isinstance(v, dict):
                            block = v
                            break
            except Exception:
                pass
        _save_rules({g: block}, g)
    return path


def rules_export_obj(group: str) -> dict:
    """Return the group's rules as a JSON-serialisable object for download."""
    g = str(group).strip().lower()
    return {g: _group_rules(g)}


def main_feature_order(group: str) -> List[str]:
    return [f.name for f in main_features(group)]


def add_value_with_relations(group: str, feature: str, value: str,
                             relations: Dict[str, List[str]]) -> str:
    """Create a feature value (auto code) and wire it into rules.json.

    relations maps OTHER main-feature name -> chosen values of that feature.
    Returns the assigned code. Raises ValueError if a required link is missing.
    """
    group = str(group).strip().lower()
    feature = str(feature).strip()
    value = str(value).strip()
    if not value:
        raise ValueError("Value is required.")
    if FeatureValue.objects.filter(group=group, feature=feature, value=value).exists():
        raise ValueError(f"'{value}' already exists for {feature}.")

    order = main_feature_order(group)
    if feature not in order:
        raise ValueError("Relations can only be set for main features.")
    fpos = order.index(feature)
    others = [f for f in order if f != feature]

    # Every other main feature needs at least one linked value.
    missing = [f for f in others if not relations.get(f)]
    if missing:
        raise ValueError("Link at least one value of: " + ", ".join(missing))

    data = _load_rules(group) or {}
    if not isinstance(data, dict):
        data = {}
    block = data.get(group)
    if not isinstance(block, dict):
        block = {}
        data[group] = block

    # Downstream features: the new value gains those values.
    down_entry = block.get(value)
    if not isinstance(down_entry, dict):
        down_entry = {}
    for other in others:
        if order.index(other) > fpos:
            chosen = [str(x).strip() for x in relations.get(other, []) if str(x).strip()]
            if chosen:
                cur = set(down_entry.get(other, []))
                cur.update(chosen)
                down_entry[other] = sorted(cur)
    if down_entry:
        block[value] = down_entry

    # Upstream features: each chosen upstream value gains the new value under
    # this feature's downstream list.
    for other in others:
        if order.index(other) < fpos:
            for uv in [str(x).strip() for x in relations.get(other, []) if str(x).strip()]:
                ue = block.get(uv)
                if not isinstance(ue, dict):
                    ue = {}
                lst = set(ue.get(feature, []))
                lst.add(value)
                ue[feature] = sorted(lst)
                block[uv] = ue

    _save_rules(data, group)

    code = next_value_code(group, feature)
    FeatureValue.objects.create(group=group, feature=feature, value=value, code=code)
    return code
