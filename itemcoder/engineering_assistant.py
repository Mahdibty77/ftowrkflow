"""Engineering Assistant (EA) — TO-only helpers for alarm / orange / unmatched.

Read-only against rules_*.json, data.json and the group's SQLite code DB.
Never runs on the hot typing / regex / coding path — only when the EA panel opens.

One deliberate exception: ea_create_size_item, added 2026-07, is the one
write-capable entry point in this file — reachable only when a user
explicitly confirms EA's "create this size" dialog, never on the read/typing
path the rest of this module promises to stay off of. See its own docstring
for the safety design (it writes nothing new: every actual database/file
mutation is delegated to the exact same functions the admin-facing Add Item
and Feature Values screens already use and have already been exercising in
production).
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .code_assigner import (
    CODE_MAPPING_CACHE,
    _get_assign_code_feature_plan,
    _normalize_for_code,
    _resolve_feature_value,
)
from . import code_db
from .composite_features import compound_groups
from .composite_keys import get_by_alias
from .final_feature_display import feature_base_name
from .item_builder import (
    _allowed_set_for_feature,
    _fmap_get,
    _group_rules,
    _is_allowed_value,
    _iter_feature_rule_subjects,
    _load_rules,
    _load_schema_maps,
    _lookup_rule_entry,
    _norm_name,
    _norm_val,
    _schema_value_index,
    allowed_values,
    main_features,
    size_value_in_rules,
)
from .normalizers import parse_feature_dependency_markers, parse_feature_pattern_key, clean_for_group_and_features
from .regex_patterns import load_feature_values, load_json_file
from .resource_paths import json_path
from .feature_extractor import _clean_type_key
from .text_processor import (
    _build_features_container,
    _feature_vocab_and_canonical_map,
    process_text_record_live,
)

logger = logging.getLogger(__name__)


# Rules nest names that may hold values for a data.json feature that is not
# itself a nested key in rules_*.json (e.g. grade_material / phisic).
_DATA_TO_RULES_NESTS = {
    "grade_material": ("material", "grade_material"),
    "phisic": ("schedule", "phisic_sch", "wt", "sdr", "pn", "sn", "phisic"),
    "phisic_sch": ("schedule", "phisic_sch", "phisic"),
    "group": ("group", "type"),
    "type": ("type", "group"),
}

_NEST_ALIASES = {
    "group": ("group", "type"),
    "type": ("type", "group"),
    "phisic": ("schedule", "phisic_sch", "wt", "sdr", "pn", "sn", "phisic"),
    "phisic_sch": ("schedule", "phisic_sch"),
    "grade_material": ("grade_material", "material"),
}

# Physical nests that act as exclusive OR branches under a design-standard
# (or similar) sidecar entry — if the entry lists schedule but not sdr,
# SDR must not be offered.
_PHYSICAL_NEST_CANON = {
    "schedule": "schedule",
    "phisic_sch": "schedule",
    "sch": "schedule",
    "sdr": "sdr",
    "phisic_sdr": "sdr",
    "pn": "pn",
    "sn": "sn",
    "wt": "wt",
    "phisic": "phisic",
}


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def _feature_map_from_vars(feature_vars: dict, group: str, type_: str) -> Dict[str, str]:
    """Build {base_feature: plain_value} from Feature_Variables."""
    fmap: Dict[str, str] = {}
    if not isinstance(feature_vars, dict):
        return fmap
    g = str(group or "").strip()
    t = str(type_ or "").strip()
    for k, v in feature_vars.items():
        ks = str(k)
        if ks.startswith("__") or ks.startswith("display_"):
            continue
        plain = _strip_html(v)
        if not plain or plain.lower() == "null":
            continue
        base = feature_base_name(ks, group_key=g, type_key=t)
        if base:
            bl = str(base).strip().lower()
            plain_s = plain
            fmap[str(base).strip().lower()] = plain_s
            fmap[str(base).strip()] = plain_s
            # Phisic schedule keys often resolve to ``sch`` — also expose the
            # canonical nest names the rest of EA / asign_code expect.
            if bl in ("sch", "phisic_sch"):
                fmap["schedule"] = plain_s
                fmap["phisic_sch"] = plain_s
                fmap["sch"] = plain_s
            if bl in ("sdr", "phisic_sdr"):
                fmap["sdr"] = plain_s
                fmap["phisic_sdr"] = plain_s
    return fmap


def _selected_for_rules(fmap: dict, group: str, type_: str,
                        exclude_features: Optional[set] = None) -> Dict[str, str]:
    """Values that act as rules keys for cascade suggestions."""
    exclude = {str(x).strip().lower() for x in (exclude_features or set())}
    selected: Dict[str, str] = {}
    rules = _group_rules(group)
    rules_ci = {_norm_val(k) for k in rules.keys()} if rules else set()
    # Sidecar keys (design standards, NACE, …) live beside the group block.
    sidecar_ci: Set[str] = set()
    try:
        full = _load_rules(group) or {}
        g = str(group).strip().lower()
        for k, entry in full.items():
            if str(k).strip().lower() == g:
                continue
            if isinstance(entry, (dict, list)):
                sidecar_ci.add(_norm_val(k))
    except Exception:
        pass
    key_ci = rules_ci | sidecar_ci

    for val, parts in _iter_feature_rule_subjects(fmap, group, type_ or ""):
        if not val or _norm_val(val) not in key_ci:
            continue
        skip = False
        if exclude:
            for p in parts:
                for feat, fv in fmap.items():
                    if str(fv).strip() == str(p).strip() and str(feat).strip().lower() in exclude:
                        skip = True
                        break
                if skip:
                    break
        if skip:
            continue
        selected[f"k{_norm_val(val)}"] = val

    for feat, val in list(fmap.items()):
        if str(feat).strip().lower() in exclude:
            continue
        if not val or not str(val).strip():
            continue
        if _norm_val(val) in key_ci:
            selected[str(feat)] = str(val).strip()
    return selected


def _schema_feature_values(group: str, target_feature: str) -> List[str]:
    try:
        feats, _cfg, _small = _load_schema_maps(group)
    except Exception:
        return []
    tgt = _norm_name(target_feature)
    for f in feats or []:
        if _norm_name(f.get("name")) == tgt:
            return sorted((f.get("vmap") or {}).keys())
    return []


def _type_names_from_data_json(group: str, json_dict: Optional[dict]) -> List[str]:
    """Canonical type labels from data.json ``group.<g>.type`` keys."""
    group_dict = get_by_alias((json_dict or {}).get("group", {}), group, {})
    if not isinstance(group_dict, dict):
        return []
    types = group_dict.get("type") or {}
    if not isinstance(types, dict):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for raw_key in types.keys():
        name = str(_clean_type_key(raw_key) or "").strip()
        if not name or name.lower() == "null":
            continue
        n = _norm_val(name)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(name)
    return out


def _group_names_from_data_json(json_dict: Optional[dict]) -> List[str]:
    """Top-level group names from data.json (skip ``G_*`` alias buckets)."""
    gmap = (json_dict or {}).get("group") or {}
    if not isinstance(gmap, dict):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for key, val in gmap.items():
        ks = str(key or "").strip()
        if not ks or ks.startswith("G_") or not isinstance(val, dict):
            continue
        n = _norm_val(ks)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(ks)
    return sorted(out, key=lambda s: s.lower())


def _rules_entry_for_value(rules: dict, value: str):
    if not rules or not value:
        return None
    if value in rules:
        return rules[value]
    nv = _norm_val(value)
    for k, entry in rules.items():
        if _norm_val(k) == nv:
            return entry
    return None


def _rules_entry_anywhere(group: str, value: str):
    """Lookup in the group type-tree and in file-level sidecar keys."""
    return _lookup_rule_entry(group, _group_rules(group) or {}, value)


def _physical_nests_allowed(group: str, selected: Dict[str, str]) -> Optional[Set[str]]:
    """Intersect physical nests declared by constraining rule entries.

    Returns ``None`` when no selected value constrains physical nests (caller
    keeps the legacy "any nest that has values" behaviour). Returns a set
    (possibly empty) when at least one sidecar/type entry lists schedule/sdr/…
    — e.g. Asme B16.9 → ``{\"schedule\"}`` so SDR is hidden.
    """
    constrained: Optional[Set[str]] = None
    for pv in (selected or {}).values():
        entry = _rules_entry_anywhere(group, pv)
        if not isinstance(entry, dict):
            continue
        present: Set[str] = set()
        for k, vals in entry.items():
            if not isinstance(vals, list):
                continue
            canon = _PHYSICAL_NEST_CANON.get(_norm_name(k))
            if canon:
                present.add(canon)
        if not present:
            continue
        if constrained is None:
            constrained = set(present)
        else:
            constrained &= present
    return constrained


def _data_feature_order(group: str, type_: str, json_dict: Optional[dict]) -> List[str]:
    """Ordered base feature names from data.json for this group/type."""
    group_dict = get_by_alias((json_dict or {}).get("group", {}), group, {})
    if not isinstance(group_dict, dict):
        return []
    fc = _build_features_container(group_dict, group, type_)
    if not isinstance(fc, dict):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for fkey in fc.keys():
        base = _feature_key_base(fkey)
        if not base or base in seen:
            continue
        seen.add(base)
        out.append(base)
    return out


def _compound_primary_label(group: str, type_: str, feature: str,
                            json_dict: Optional[dict] = None) -> str:
    """Title for a compound asign_code field = first member in data.json order.

    Example: ``material & grade_material & material_class`` → ``material``.
    """
    members = _compound_members_for(group, type_, feature)
    if not members:
        return str(feature or "").strip()
    order = _data_feature_order(group, type_ or "", json_dict)
    member_l = {str(m).strip().lower(): str(m).strip() for m in members}
    for name in order:
        hit = member_l.get(str(name).strip().lower())
        if hit:
            return hit
    return str(members[0]).strip()


def _prefer_longer_schedule_values(values: List[str]) -> List[str]:
    """Drop short schedule tokens covered by a longer ``A × B`` sibling.

    Keeps ``10 × 10`` and drops bare ``10`` when both appear, so EA inserts
    the longer spelling the code table expects for reducers.
    """
    cleaned = _clean_suggestion_list(values)
    if len(cleaned) <= 1:
        return cleaned
    norms = {_norm_val(v): v for v in cleaned}
    drop: Set[str] = set()
    for v in cleaned:
        vs = str(v).strip()
        # Compound schedule forms contain × / x between parts.
        if not re.search(r"[×xX]", vs):
            continue
        # Leading token before × (``10`` from ``10 × 10`` / ``Sch10 × 10``).
        head = re.split(r"\s*[×xX]\s*", re.sub(r"(?i)^sch\s*", "", vs), maxsplit=1)[0].strip()
        if not head:
            continue
        hn = _norm_val(head)
        for other_n, other_v in norms.items():
            if other_n == _norm_val(vs):
                continue
            if other_n == hn or _norm_val(_format_sch_display(other_v)) == _norm_val(
                _format_sch_display(head)
            ):
                drop.add(_norm_val(other_v))
    return [v for v in cleaned if _norm_val(v) not in drop]


def _canonical_feature_value(group: str, feature: str, value: str) -> str:
    """Prefer the FeatureValue spelling that shares a code-normalized form."""
    from .models import FeatureValue
    from .code_assigner import _normalize_for_code

    raw = str(value or "").strip()
    if not raw:
        return ""
    feat = str(feature or "").strip()
    hit = FeatureValue.objects.filter(
        group=group, feature__iexact=feat, value__iexact=raw
    ).first()
    if hit:
        return str(hit.value).strip()
    target = _normalize_for_code(raw)
    if not target:
        return raw
    for fv in FeatureValue.objects.filter(group=group, feature__iexact=feat):
        if _normalize_for_code(fv.value) == target:
            return str(fv.value).strip()
    return raw


def _feature_value_exists(group: str, feature: str, value: str) -> bool:
    from .models import FeatureValue
    from .code_assigner import _normalize_for_code

    raw = str(value or "").strip()
    if not raw:
        return False
    if FeatureValue.objects.filter(
        group=group, feature__iexact=feature, value__iexact=raw
    ).exists():
        return True
    target = _normalize_for_code(raw)
    if not target:
        return False
    for fv in FeatureValue.objects.filter(group=group, feature__iexact=feature):
        if _normalize_for_code(fv.value) == target:
            return True
    return False


def _join_assign_parts(parts: List[str], get_value) -> str:
    """Join asign_code compound members in declared order, skipping empties.

    Dedupes when a later part already contains an earlier one
    (``Concentric`` + ``Concentric Reducer`` → ``Concentric Reducer``).
    """
    ordered: List[str] = []
    for p in parts:
        v = str(get_value(p) or "").strip()
        if not v or v.lower() == "null":
            continue
        ordered.append(v)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return ordered[0]
    # Drop a token that is already contained (norm) in a longer neighbour.
    kept: List[str] = []
    for i, tok in enumerate(ordered):
        tn = _norm_val(tok)
        covered = False
        for j, other in enumerate(ordered):
            if i == j:
                continue
            on = _norm_val(other)
            if tn and on and tn != on and tn in on:
                covered = True
                break
        if not covered:
            kept.append(tok)
    return " ".join(kept) if kept else ordered[0]


def _resolve_type_for_code(group: str, type_: str, get_value,
                           compound_parts: Optional[List[str]] = None) -> str:
    """Resolve Type column value for coding / EA create-size.

    asign_code joins ``prefix & type & degree & sufix``. Extraction often
    stores type=``Reducer`` + prefix=``Concentric``; the code table wants
    ``Concentric Reducer``. Prefer any candidate that already exists in
    FeatureValue.
    """
    parts = list(compound_parts or ["prefix", "type", "degree", "sufix"])
    joined = _join_assign_parts(parts, get_value)
    tval = str(get_value("type") or type_ or "").strip()
    candidates = []
    for c in (joined, tval, str(type_ or "").strip()):
        if c and c not in candidates:
            candidates.append(c)
    for c in candidates:
        if _feature_value_exists(group, "Type", c) or _feature_value_exists(group, "type", c):
            return _canonical_feature_value(group, "Type", c)
    return _canonical_feature_value(group, "Type", joined or tval)


def _fmap_schedule_raw(fmap: dict) -> str:
    for key in ("schedule", "phisic_sch", "sch", "phisic"):
        v = _fmap_get(fmap, key)
        if v and str(v).strip() and str(v).strip().lower() != "null":
            return str(v).strip()
    for k, v in (fmap or {}).items():
        kl = str(k).strip().lower()
        if not kl.startswith("sch"):
            continue
        if v and str(v).strip() and str(v).strip().lower() != "null":
            return str(v).strip()
    return ""


def _absent_label(feature_name: str) -> str:
    n = re.sub(r"\s+", " ", str(feature_name or "").strip())
    if not n:
        return "NO"
    return f"NO {n.upper()}"


def _build_code_selected(group: str, type_: str, fmap: dict,
                         feature_vars: Optional[dict] = None) -> Tuple[Dict[str, str], List[dict]]:
    """Map live fmap → main-feature selected dict + display rows for Create-size.

    Compounds from asign_code.json are joined; absent mains become empty
    (code) / ``NO …`` (display). Schedule uses the raw phisic spelling
    (``10 × 10``), not an internal ``sch_fitting_*`` key name.
    """
    group_l = str(group or "").strip().lower()
    type_l = str(type_ or "").strip().lower()
    fv = feature_vars if isinstance(feature_vars, dict) else {}
    mains = list(main_features(group_l))
    main_names = [f.name for f in mains]
    selected: Dict[str, str] = {n: "" for n in main_names}
    display_rows: List[dict] = []

    # Column name (code table / GroupFeature) ← asign_code plan value.
    col_values: Dict[str, str] = {}
    try:
        mapping_all = CODE_MAPPING_CACHE.get("__asign_all__")
        if mapping_all is None:
            mapping_all = load_json_file(json_path("asign_code.json"))
            CODE_MAPPING_CACHE["__asign_all__"] = mapping_all
        plan = _get_assign_code_feature_plan(group_l, type_l, mapping_all)
        cols = code_db.column_names(group_l) if code_db.has_db(group_l) else []
    except Exception:
        plan = None
        cols = []

    def _part_value(part: str) -> str:
        p = str(part or "").strip()
        if not p:
            return ""
        v = _fmap_get(fmap, p)
        if v and str(v).strip() and str(v).strip().lower() != "null":
            return str(v).strip()
        if fv:
            try:
                raw = _resolve_feature_value(group, type_, group_l, type_l, fv, p)
            except Exception:
                raw = ""
            plain = _strip_html(raw)
            if plain and plain.lower() != "null":
                return plain
        return ""

    if plan and cols:
        required_feats, or_groups = plan
        for feat_key, col_idx, is_combined, parts in required_feats:
            col_pos = col_idx - 1
            if col_pos < 0 or col_pos >= len(cols):
                continue
            col_name = cols[col_pos]
            if is_combined:
                # Type compound (prefix & type & degree & sufix): join so
                # Reducer+Concentric → Concentric Reducer for the code table.
                if any(str(p).strip().lower() == "type" for p in parts):
                    col_values[col_name] = _resolve_type_for_code(
                        group_l, type_, _part_value, compound_parts=parts
                    )
                    continue
                # Material (+ grade + class) and any other compounds.
                col_values[col_name] = _join_assign_parts(parts, _part_value)
            else:
                col_values[col_name] = _part_value(parts[0])

        for _or_name, entries in (or_groups or {}).items():
            for inner_feat, col_idx in entries:
                col_pos = col_idx - 1
                if col_pos < 0 or col_pos >= len(cols):
                    continue
                col_name = cols[col_pos]
                inner = str(inner_feat or "").strip().lower()
                if inner in ("phisic_sch", "schedule", "sch") or inner.endswith("_sch"):
                    val = _fmap_schedule_raw(fmap)
                elif inner in ("phisic_sdr", "sdr") or inner.endswith("_sdr"):
                    val = _part_value("sdr") or _part_value("phisic_sdr")
                else:
                    # strip leading phisic_
                    base = inner[len("phisic_"):] if inner.startswith("phisic_") else inner
                    val = _part_value(base) or _part_value(inner)
                if val:
                    col_values[col_name] = val

    # Fall back: match main feature names onto fmap / compounds.
    for name in main_names:
        if col_values.get(name):
            continue
        nl = name.strip().lower()
        if nl == "schedule":
            col_values[name] = _fmap_schedule_raw(fmap)
            continue
        if nl == "type":
            col_values[name] = _resolve_type_for_code(
                group_l, type_, _part_value
            )
            continue
        if nl in ("material standard", "material"):
            col_values[name] = _join_assign_parts(
                ["material", "grade_material", "material_class"], _part_value
            )
            continue
        # Connection / Material Group / …
        snake = nl.replace(" ", "_")
        col_values[name] = (
            _part_value(snake)
            or _part_value(nl)
            or _fmap_get(fmap, snake)
            or _fmap_get(fmap, name)
            or ""
        )
        if col_values[name]:
            col_values[name] = str(col_values[name]).strip()

    for name in main_names:
        raw = str(col_values.get(name) or "").strip()
        if not raw or raw.lower() == "null" or _ea_is_absent_attr(raw):
            selected[name] = ""
            display_rows.append({
                "name": name,
                "value": _absent_label(name),
                "absent": True,
            })
            continue
        canon = _canonical_feature_value(group_l, name, raw)
        selected[name] = canon
        display_rows.append({
            "name": name,
            "value": canon,
            "absent": False,
        })

    return selected, display_rows


def _nest_names_for(feature: str) -> Tuple[str, ...]:
    fl = _norm_name(feature)
    aliases = _NEST_ALIASES.get(fl) or _DATA_TO_RULES_NESTS.get(fl)
    if aliases:
        return aliases
    return (feature,)


def _feature_key_base(fkey: str) -> str:
    """``grade_material_4`` / ``phisic_9`` → ``grade_material`` / ``phisic``."""
    s = str(fkey or "").strip()
    s = re.sub(r"_\d+$", "", s)
    return s.lower()


def _values_from_data_json(group: str, type_: str, target_feature: str,
                           json_dict: dict) -> List[str]:
    """Canonical display values for a feature from data.json (+ CSV lists)."""
    group_dict = get_by_alias((json_dict or {}).get("group", {}), group, {})
    if not isinstance(group_dict, dict):
        return []
    fc = _build_features_container(group_dict, group, type_)
    if not isinstance(fc, dict):
        return []

    tgt = str(target_feature or "").strip().lower()
    tgt_n = _norm_name(target_feature)
    out: List[str] = []
    seen: Set[str] = set()

    def _add(v: str):
        s = str(v or "").strip()
        if not s or s.lower() == "null":
            return
        n = _norm_val(s)
        if not n or n in seen:
            return
        seen.add(n)
        out.append(s)

    for fkey, fval in fc.items():
        base = _feature_key_base(fkey)
        if base != tgt and _norm_name(base) != tgt_n and not base.startswith(tgt):
            # phisic_* matches target phisic
            if not (tgt.startswith("phisic") and base.startswith("phisic")):
                continue
        if not isinstance(fval, dict):
            continue
        for pat_key, pat_values in fval.items():
            clean_key, _sup, _req = parse_feature_dependency_markers(pat_key)
            if "null" in str(clean_key).lower():
                continue
            _m, _letter, base_name = parse_feature_pattern_key(clean_key)
            display = str(base_name or "").strip()
            # CSV / list payloads
            try:
                loaded = load_feature_values(pat_values)
            except Exception:
                loaded = []
            if isinstance(pat_values, str) and str(pat_values).endswith(".csv"):
                for v in loaded or []:
                    _add(v)
            else:
                if display and "null" not in display.lower():
                    # Strip trailing alias noise like ``sch&رده&sch.-sch-``
                    if "&" in display or display.endswith("-"):
                        pass
                    else:
                        # Dropdowns use the canonical JSON key name only.
                        # Aliases stay in extractor vocab; listing every alias
                        # (Forged Gate / Compact Forged Gate / …) floods EA.
                        _add(display)
    return out


def _intersect_with_rules_nests(group: str, nest_names: Tuple[str, ...],
                                selected: Dict[str, str],
                                candidates: List[str]) -> List[str]:
    """Keep candidates that appear in the intersected rules nest allow-lists."""
    if not candidates:
        return []
    if not selected:
        return candidates

    result_norms = None
    pool = {_norm_val(v): v for v in candidates}
    constrained = False
    for nest in nest_names:
        nest_result = None
        nest_constrained = False
        for pv in selected.values():
            entry = _rules_entry_anywhere(group, pv)
            if entry is None:
                continue
            sub = _allowed_set_for_feature(entry, nest)
            if sub is None:
                continue
            nest_constrained = True
            if isinstance(entry, dict):
                for k, vals in entry.items():
                    if _norm_name(k) == _norm_name(nest) and isinstance(vals, list):
                        for x in vals:
                            pool.setdefault(_norm_val(x), str(x))
            nest_result = set(sub) if nest_result is None else (nest_result & sub)
        if nest_constrained and nest_result is not None:
            constrained = True
            result_norms = nest_result if result_norms is None else (result_norms & nest_result)

    if not constrained or result_norms is None:
        return candidates

    # Match candidates that equal / prefix-match an allowed nest value
    # (grade ``Gr.B`` inside ``ASTM A53 Gr.B``, sch ``40`` inside schedule list).
    out = []
    seen = set()
    for c in candidates:
        nc = _norm_val(c)
        ok = False
        if _is_allowed_value(c, result_norms):
            ok = True
        else:
            for a in result_norms:
                if a.endswith(nc) or nc in a:
                    ok = True
                    break
        if ok and nc not in seen:
            seen.add(nc)
            out.append(c)
    # Also surface full allowed nest spellings not already in candidates
    for a in sorted(result_norms):
        if a in seen:
            continue
        # Prefer human spelling from pool
        out.append(pool.get(a, a))
        seen.add(a)
    return out


def _suggest_from_rules(group: str, target_feature: str,
                        selected: Dict[str, str]) -> List[str]:
    nest_names = list(_nest_names_for(target_feature))
    if target_feature not in nest_names:
        nest_names.insert(0, target_feature)

    best: List[str] = []
    for nest in nest_names:
        schema_vals = _schema_feature_values(group, nest)
        pool: Dict[str, str] = {_norm_val(v): v for v in schema_vals}
        result_norms = {_norm_val(v) for v in schema_vals} if schema_vals else None
        constrained = False
        for pv in selected.values():
            entry = _rules_entry_anywhere(group, pv)
            if entry is None:
                continue
            sub = _allowed_set_for_feature(entry, nest)
            if sub is None:
                continue
            constrained = True
            if isinstance(entry, dict):
                for k, vals in entry.items():
                    if _norm_name(k) == _norm_name(nest) and isinstance(vals, list):
                        for x in vals:
                            pool.setdefault(_norm_val(x), str(x))
            if result_norms is None:
                result_norms = set(sub)
            else:
                result_norms &= sub

        if not constrained:
            if not best and nest == nest_names[0]:
                best = schema_vals
            continue
        if result_norms is None:
            continue
        out = [v for v in schema_vals if _norm_val(v) in result_norms]
        seen = {_norm_val(v) for v in out}
        for n in sorted(result_norms):
            if n in seen:
                continue
            out.append(pool.get(n, n))
            seen.add(n)
        return out
    return best


def _format_sch_display(value: str) -> str:
    """Present schedule tokens as ``Sch40`` / ``SchSTD`` for the EA dropdown."""
    s = str(value or "").strip()
    if not s:
        return s
    if re.match(r"(?i)^sch\s*", s):
        body = re.sub(r"(?i)^sch\s*", "", s).strip()
        return f"Sch{body}" if body else s
    return f"Sch{s}"


def _schedule_storage_token(display: str) -> str:
    """Convert ``Sch40`` back to the bare token process-row expects (``sch40``)."""
    s = str(display or "").strip()
    if re.match(r"(?i)^sch", s):
        body = re.sub(r"(?i)^sch\s*", "", s).strip()
        return f"sch{body}" if body else s
    return s


def _compound_members_for(group: str, type_: str, feature: str) -> List[str]:
    feat = str(feature or "").strip().lower()
    try:
        for members in compound_groups(group, type_ or "").values():
            lows = [str(m).strip().lower() for m in members]
            if feat in lows:
                return [str(m).strip() for m in members]
    except Exception:
        pass
    if feat in ("material", "grade_material"):
        return ["material", "grade_material"]
    return [str(feature).strip()]


def _replace_values_for(fmap: dict, group: str, type_: str, feature: str) -> List[str]:
    """Current tokens to remove when applying a new value for ``feature``."""
    members = _compound_members_for(group, type_, feature)
    out: List[str] = []
    seen = set()
    parts = []
    for m in members:
        v = _fmap_get(fmap, m)
        if not v:
            continue
        s = str(v).strip()
        if not s:
            continue
        parts.append(s)
        n = _norm_val(s)
        if n not in seen:
            seen.add(n)
            out.append(s)
    if len(parts) >= 2:
        joined = " ".join(parts)
        n = _norm_val(joined)
        if n not in seen:
            out.append(joined)
    return out


def _clean_suggestion_list(values: List[str]) -> List[str]:
    out = []
    seen = set()
    for v in values or []:
        s = str(v or "").strip()
        if not s or s.lower() == "null":
            continue
        if s.startswith("$") and s.endswith("$"):
            continue
        n = _norm_val(s)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(s)
    return out


def _selected_anchors(fmap: dict, group: str, type_: str,
                      exclude_features: Optional[set] = None,
                      ignore_size: bool = True) -> Dict[str, str]:
    """Cascade anchors for ``allowed_values``.

    ``ignore_size`` (default True): never use live size as a cascade key so
    orange/alarm dropdowns for other features are not filtered by size.
    """
    selected = _selected_for_rules(fmap, group, type_, exclude_features=exclude_features)
    if not ignore_size:
        size_v = _fmap_get(fmap, "size")
        if size_v and size_value_in_rules(group, str(size_v)):
            selected = dict(selected)
            selected["size"] = str(size_v).strip()
        return selected

    size_v = _fmap_get(fmap, "size")
    size_n = _norm_val(size_v) if size_v else ""
    out: Dict[str, str] = {}
    for k, v in selected.items():
        if _norm_name(k) == "size":
            continue
        if size_n and _norm_val(v) == size_n:
            continue
        out[k] = v
    return out


def _entry_allows_value(entry, other_value: str) -> bool:
    """True when ``other_value`` fits any nest/list under a rules entry."""
    if entry is None or not other_value:
        return False
    ov = str(other_value).strip()
    on = _norm_val(ov)
    if isinstance(entry, list):
        aset = {_norm_val(x) for x in entry}
        if _is_allowed_value(ov, aset) or on in aset:
            return True
        return any(on and (a.startswith(on) or on.startswith(a) or on in a) for a in aset)
    if not isinstance(entry, dict):
        return False
    for _nest, allowed in entry.items():
        if not isinstance(allowed, list):
            continue
        aset = {_norm_val(x) for x in allowed}
        if _is_allowed_value(ov, aset) or on in aset:
            return True
        for a in aset:
            if on and (a.startswith(on) or on.startswith(a) or on in a):
                return True
    return False


def _canonical_rules_value(group: str, type_: str, candidate: str,
                           json_dict: Optional[dict] = None,
                           canon_map: Optional[dict] = None) -> str:
    """Map alias display (e.g. Forged Globe) → canonical rules key name."""
    s = str(candidate or "").strip()
    if not s:
        return s
    rules = _group_rules(group)
    if rules and _rules_entry_for_value(rules, s) is not None:
        return s

    cmap = canon_map
    if cmap is None:
        jd = json_dict
        if jd is None:
            try:
                jd = load_json_file(json_path("data.json"))
            except Exception:
                jd = {}
        group_dict = get_by_alias((jd or {}).get("group", {}), group, {})
        if isinstance(group_dict, dict):
            fc = _build_features_container(group_dict, group, type_ or "")
            _vocab, cmap = _feature_vocab_and_canonical_map(fc)
        else:
            cmap = {}

    cleaned = clean_for_group_and_features(s)
    canon = (cmap or {}).get(cleaned) if cleaned else None
    if canon:
        return str(canon).strip()
    # Fallback: longest rules key whose norm ends with candidate norm
    # (Forged Globe → Compact Forged Globe when data map missed it).
    if rules:
        nv = _norm_val(s)
        best = None
        for k in rules.keys():
            kn = _norm_val(k)
            if not kn or not nv:
                continue
            if kn == nv or (len(nv) >= 6 and kn.endswith(nv)):
                if best is None or len(kn) > len(_norm_val(best)):
                    best = k
        if best:
            return str(best)
    return s


def _pair_compatible(group: str, a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are mutually acceptable in rules (either direction)."""
    if not a or not b:
        return True
    if _norm_val(a) == _norm_val(b):
        return True
    rules = _group_rules(group)
    if not rules:
        return True
    ea = _rules_entry_for_value(rules, a)
    eb = _rules_entry_for_value(rules, b)
    if ea is None and eb is None:
        return True
    a_ok = ea is not None and _entry_allows_value(ea, b)
    b_ok = eb is not None and _entry_allows_value(eb, a)
    if ea is not None and eb is not None:
        return a_ok or b_ok
    if ea is not None:
        return a_ok
    return b_ok


def _candidate_fits_live_fmap(group: str, candidate: str, fmap: dict,
                              exclude_features: Optional[set] = None,
                              type_: str = "",
                              json_dict: Optional[dict] = None,
                              canon_map: Optional[dict] = None) -> bool:
    """True when picking ``candidate`` would not clash with live row values.

    If ``candidate`` is a rules key (or alias of one), only nests that exist
    under that key are checked (e.g. Compact Forged Globe → material). Live
    values whose feature has no nest under the candidate are ignored — so
    CLASS/end do not wipe a group pick that only constrains material.

    Size is never used here.
    """
    if not candidate or not fmap:
        return True
    rules = _group_rules(group)
    if not rules:
        return True
    canon = _canonical_rules_value(
        group, type_ or "", candidate,
        json_dict=json_dict, canon_map=canon_map,
    )
    entry = _rules_entry_for_value(rules, canon)
    if entry is None:
        entry = _rules_entry_for_value(rules, candidate)
    if entry is None or not isinstance(entry, dict):
        return True

    excl = {str(x).strip().lower() for x in (exclude_features or set())}
    excl.add("size")

    for feat, val in fmap.items():
        fl = str(feat).strip().lower()
        if fl in excl or _norm_name(feat) == "size":
            continue
        v = str(val or "").strip()
        if not v or v.lower() == "null":
            continue
        # Only enforce nests this rules key actually declares.
        constrained = False
        allowed = False
        for nest in _nest_names_for(feat):
            sub = _allowed_set_for_feature(entry, nest)
            if sub is None:
                continue
            constrained = True
            if _is_allowed_value(v, sub) or _norm_val(v) in sub:
                allowed = True
                break
            nv = _norm_val(v)
            for a in sub:
                if nv and (a.startswith(nv) or nv.startswith(a) or nv in a or a in nv):
                    allowed = True
                    break
            if allowed:
                break
        if constrained and not allowed:
            return False
    return True


def _filter_compatible_with_peers(group: str, values: List[str],
                                  peer_values: List[str],
                                  exclude_values: Optional[List[str]] = None) -> List[str]:
    """Keep candidates compatible with every peer; drop current bad spellings."""
    excl_n = {_norm_val(x) for x in (exclude_values or []) if x}
    peers = [str(p).strip() for p in (peer_values or []) if str(p).strip()]
    out = []
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        if _norm_val(s) in excl_n:
            continue
        if any(_norm_val(s) == _norm_val(x) for x in (exclude_values or [])):
            continue
        ok = True
        for peer in peers:
            if not _pair_compatible(group, s, peer):
                ok = False
                break
        if ok:
            out.append(s)
    return out


def _filter_candidates_by_size(group: str, size_val: str, values: List[str]) -> List[str]:
    """Only used when the target feature itself is size."""
    if not size_val or not size_value_in_rules(group, size_val):
        return values
    rules = _group_rules(group)
    if not rules:
        return values
    out = []
    for v in values:
        entry = _rules_entry_for_value(rules, v)
        if entry is None:
            out.append(v)
            continue
        size_set = _allowed_set_for_feature(entry, "size")
        if size_set is None or _is_allowed_value(size_val, size_set):
            out.append(v)
    return out


def suggest_for_feature(group: str, type_: str, fmap: dict, target_feature: str,
                        exclude_self: bool = False,
                        json_dict: Optional[dict] = None,
                        ignore_size: bool = True,
                        peer_values: Optional[List[str]] = None,
                        exclude_values: Optional[List[str]] = None) -> List[str]:
    """Fast dropdown values via rules cascade.

    * Non-size targets never cascade on size (``ignore_size=True``).
    * ``peer_values``: other orange/conflict values the pick must stay compatible with.
    * ``exclude_values``: current bad spelling(s) to omit from the list.
    """
    excl = {str(target_feature).strip().lower()} if exclude_self else set()
    if exclude_self and str(target_feature).strip().lower() == "material":
        excl.add("grade_material")
    if exclude_self and str(target_feature).strip().lower() == "grade_material":
        excl.add("material")

    feat_l = str(target_feature or "").strip().lower()
    use_ignore_size = ignore_size and feat_l != "size"
    selected = _selected_anchors(
        fmap, group, type_, exclude_features=excl, ignore_size=use_ignore_size
    )

    def _finalize(raw: List[str]) -> List[str]:
        vals = _clean_suggestion_list(raw)
        if peer_values or exclude_values:
            vals = _filter_compatible_with_peers(
                group, vals, peer_values or [], exclude_values=exclude_values
            )
        # Always drop picks that would orange against live row values
        # (e.g. Compact Forged Globe / Forged Globe vs ASTM A216 Gr.WCB).
        # Type/group alarms are the choice that unlocks rules — do not filter
        # them by current material (list must come from data.json).
        if feat_l not in ("size", "type", "group") and vals:
            jd = json_dict
            if jd is None:
                try:
                    jd = load_json_file(json_path("data.json"))
                except Exception:
                    jd = {}
            group_dict = get_by_alias((jd or {}).get("group", {}), group, {})
            cmap = {}
            if isinstance(group_dict, dict):
                fc = _build_features_container(group_dict, group, type_ or "")
                _vocab, cmap = _feature_vocab_and_canonical_map(fc)
            vals = [
                v for v in vals
                if _candidate_fits_live_fmap(
                    group, v, fmap,
                    exclude_features=excl,
                    type_=type_,
                    json_dict=jd,
                    canon_map=cmap,
                )
            ]
        return vals

    # Type / group: always from data.json maps (not feature patterns).
    if feat_l in ("type", "group"):
        jd = json_dict
        if jd is None:
            try:
                jd = load_json_file(json_path("data.json"))
            except Exception:
                jd = {}
        if feat_l == "type":
            vals = _type_names_from_data_json(group, jd)
            if not vals:
                vals = _schema_feature_values(group, "type")
        else:
            vals = _group_names_from_data_json(jd)
        return _finalize(vals)

    # Material (+ grade): full compound spellings from rules ``material`` nest.
    if feat_l in ("material", "grade_material"):
        try:
            vals = list(allowed_values(group, "material", selected) or [])
        except Exception:
            vals = []
        if not vals:
            vals = _suggest_from_rules(group, "material", selected)
        return _finalize(vals)

    # Physical exclusivity from design-standard (etc.) sidecar entries.
    phys_allowed = _physical_nests_allowed(group, selected)

    # Schedule → Sch-prefixed display; prefer longer ``A × B`` spellings.
    if feat_l in ("phisic", "phisic_sch", "schedule", "sch"):
        if phys_allowed is not None and "schedule" not in phys_allowed:
            return []
        try:
            raw = list(allowed_values(group, "schedule", selected) or [])
        except Exception:
            raw = []
        if not raw:
            raw = _suggest_from_rules(group, "schedule", selected)
        raw = _prefer_longer_schedule_values(raw)
        return [_format_sch_display(v) for v in _finalize(raw)]

    if feat_l in ("phisic_sdr", "sdr") or feat_l.endswith("_sdr"):
        if phys_allowed is not None and "sdr" not in phys_allowed:
            return []
        try:
            vals = list(allowed_values(group, "sdr", selected) or [])
        except Exception:
            vals = []
        if not vals:
            vals = _suggest_from_rules(group, "sdr", selected)
        return _finalize(vals)

    if feat_l == "size":
        try:
            vals = list(allowed_values(group, "size", selected) or [])
        except Exception:
            vals = []
        if not vals:
            vals = _suggest_from_rules(group, "size", selected)
        return _finalize(vals)

    # Universal path: candidates from data.json, then intersect with rules nests
    # when the selected anchors declare that nest (yoke / seat_type / …).
    # Features that are themselves rules keys (e.g. gride → Compact Forged Gate)
    # are further filtered in ``_finalize`` via live-fmap compatibility.
    jd = json_dict
    if jd is None:
        try:
            jd = load_json_file(json_path("data.json"))
        except Exception:
            jd = {}
    data_vals = _values_from_data_json(group, type_, target_feature, jd)
    if data_vals:
        nests = _nest_names_for(target_feature)
        filtered = _intersect_with_rules_nests(group, nests, selected, data_vals)
        return _finalize(filtered or data_vals)

    try:
        vals = list(allowed_values(group, target_feature, selected) or [])
        if vals:
            return _finalize(vals)
    except Exception:
        logger.exception("allowed_values failed for %s/%s", group, target_feature)
    return _finalize(_suggest_from_rules(group, target_feature, selected))


def _expand_phisic_alarm_fields(group: str, type_: str, fmap: dict,
                                json_dict: dict) -> List[dict]:
    """Split a generic ``phisic`` alarm into sch / sdr rule-backed fields."""
    fields = []
    selected = _selected_anchors(fmap, group, type_)
    phys_allowed = _physical_nests_allowed(group, selected)

    def _nest_has_values(nest: str) -> bool:
        canon = _PHYSICAL_NEST_CANON.get(_norm_name(nest), _norm_name(nest))
        if phys_allowed is not None and canon not in phys_allowed:
            return False
        for pv in selected.values():
            entry = _rules_entry_anywhere(group, pv)
            if entry is None:
                continue
            sub = _allowed_set_for_feature(entry, nest)
            if sub:
                return True
        return False

    if _nest_has_values("schedule") or _nest_has_values("phisic_sch"):
        sch_vals = suggest_for_feature(group, type_, fmap, "phisic_sch", json_dict=json_dict)
        if sch_vals:
            fields.append({
                "feature": "phisic_sch",
                "label": "schedule (Sch)",
                "values": sch_vals,
                "apply_kind": "sch",
                "replace_values": _replace_values_for(fmap, group, type_, "phisic_sch"),
            })
    if _nest_has_values("sdr") or _nest_has_values("phisic_sdr"):
        sdr_vals = suggest_for_feature(group, type_, fmap, "phisic_sdr", json_dict=json_dict)
        if sdr_vals:
            fields.append({
                "feature": "phisic_sdr",
                "label": "sdr",
                "values": sdr_vals,
                "apply_kind": "sdr",
                "replace_values": _replace_values_for(fmap, group, type_, "phisic_sdr"),
            })
    if fields:
        return fields
    vals = suggest_for_feature(group, type_, fmap, "phisic", json_dict=json_dict)
    return [{
        "feature": "phisic",
        "label": "phisic",
        "values": vals,
        "apply_kind": "phisic",
        "replace_values": _replace_values_for(fmap, group, type_, "phisic"),
    }]


def _value_in_rules(group: str, feature: str, value: str,
                    selected: Dict[str, str]) -> bool:
    """True when ``value`` appears as a rules key or in an allowed nest list."""
    if str(feature or "").strip().lower() == "size":
        return size_value_in_rules(group, value)
    if not value:
        return False
    nv = _norm_val(value)
    if _rules_entry_anywhere(group, value) is not None:
        return True
    anchors = list(selected.values()) if selected else []
    if not anchors:
        return False
    for nest in _nest_names_for(feature):
        for pv in anchors:
            entry = _rules_entry_anywhere(group, pv)
            if entry is None:
                continue
            sub = _allowed_set_for_feature(entry, nest)
            if sub is None:
                continue
            if _is_allowed_value(value, sub) or nv in sub:
                return True
            if nest == "material" or nest == "grade_material":
                for a in sub:
                    if nv and (a.endswith(nv) or nv in a):
                        return True
    return False


def _colored_features(fmap: dict, target_values_map: dict, group: str,
                      type_: str = "", colors=("orange",)) -> List[Dict[str, str]]:
    """Map TVM-colored values → feature names for the EA panel."""
    if not isinstance(target_values_map, dict):
        return []
    want = {str(c).strip().lower() for c in colors}
    feat_of, _pos = _schema_value_index(group)
    out: List[Dict[str, str]] = []
    seen = set()

    value_to_feats: Dict[str, List[str]] = {}
    for feat, val in fmap.items():
        if not val:
            continue
        nv = _norm_val(val)
        value_to_feats.setdefault(nv, []).append(str(feat).strip())

    try:
        seen_m = set()
        for members in compound_groups(group, type_).values():
            if members in seen_m:
                continue
            seen_m.add(members)
            parts = []
            for m in members:
                v = _fmap_get(fmap, m)
                if v:
                    parts.append(str(v).strip())
            if len(parts) >= 2:
                joined = " ".join(parts)
                value_to_feats.setdefault(_norm_val(joined), []).append(members[0])
    except Exception:
        pass

    for raw_val, color in target_values_map.items():
        if str(color).strip().lower() not in want:
            continue
        plain = _strip_html(raw_val)
        if not plain:
            continue
        nv = _norm_val(plain)
        feats = value_to_feats.get(nv) or []
        if not feats:
            schema_feat = feat_of.get(nv)
            if schema_feat:
                feats = [schema_feat]
        if not feats:
            continue
        for feat in feats:
            fl = str(feat).strip().lower()
            if fl == "grade_material":
                continue
            key = (fl, nv)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "feature": str(feat).strip(),
                "value": plain,
                "color": str(color).strip().lower(),
            })
    return out


def _orange_features(fmap: dict, target_values_map: dict, group: str,
                     type_: str = "") -> List[Dict[str, str]]:
    return _colored_features(fmap, target_values_map, group, type_, colors=("orange",))


def _rank_orange_items(items: List[dict], group: str, type_: str, fmap: dict,
                       json_dict: dict) -> List[dict]:
    """Priority: non-size missing/conflicts first; size always last."""
    if len(items) <= 1:
        return items

    selected = _selected_for_rules(fmap, group, type_)
    size_items = []
    missing = []
    present = []
    for it in items:
        feat = str(it.get("feature") or "").strip().lower()
        if feat == "size":
            size_items.append(it)
        elif _value_in_rules(group, it["feature"], it["value"], selected):
            present.append(it)
        else:
            missing.append(it)
    return missing + present + size_items


def _build_search_by_pos(group: str, type_: str, feature_vars: dict):
    """Build the same required + OR search plan that assign/lookup uses.

    Returns ``(search_by_pos, labels, or_groups_by_pos)`` or
    ``(None, [], [])`` when the SQLite assign path is unavailable.

    ``labels`` lists only non-empty required features (for leave-one-out blame).
    Empty required values (e.g. coating) stay in ``search_by_pos`` so diagnose
    matches absent cells the same way assign does.
    """
    group_l = str(group or "").strip().lower()
    type_l = str(type_ or "").strip().lower()
    if not group_l or not code_db.has_db(group_l):
        return None, [], []

    json_file_path = json_path("asign_code.json")
    try:
        mapping_all = CODE_MAPPING_CACHE.get("__asign_all__")
        if mapping_all is None:
            mapping_all = load_json_file(json_file_path)
            CODE_MAPPING_CACHE["__asign_all__"] = mapping_all
    except Exception:
        return None, [], []

    plan = _get_assign_code_feature_plan(group_l, type_l, mapping_all)
    if plan is None:
        return None, [], []
    required_feats, or_groups = plan
    ncols = len(code_db.column_names(group_l))

    def gv(field_name):
        return _resolve_feature_value(group, type_, group_l, type_l, feature_vars, field_name)

    search_by_pos: Dict[int, str] = {}
    labels: List[Tuple[str, int]] = []
    for feat_key, col_idx, is_combined, parts in required_feats:
        col_pos = col_idx - 1
        if col_pos < 0 or col_pos >= ncols:
            continue
        if is_combined:
            normalized_vals = [_normalize_for_code(gv(p)) for p in parts]
            search_val = "".join(v for v in normalized_vals if v != "null")
            label = " & ".join(parts)
        else:
            search_val = _normalize_for_code(gv(parts[0]))
            label = parts[0]
        # Keep empty/null in the search map (absent-cell match), same as assign.
        search_by_pos[col_pos] = search_val
        if search_val not in ("", "null"):
            labels.append((label, col_pos))

    or_groups_by_pos: List[Tuple[str, List[Tuple[int, str]]]] = []
    for or_name, feats in (or_groups or {}).items():
        lst: List[Tuple[int, str]] = []
        for inner_feat, col_idx in feats:
            col_pos = col_idx - 1
            if col_pos < 0 or col_pos >= ncols:
                continue
            if "&" in inner_feat:
                parts = [p.strip() for p in inner_feat.split("&") if p.strip()]
                search_val = "".join(_normalize_for_code(gv(p)) for p in parts)
            else:
                search_val = _normalize_for_code(gv(inner_feat))
            lst.append((col_pos, search_val))
        or_groups_by_pos.append((or_name, lst))

    return search_by_pos, labels, or_groups_by_pos


def diagnose_unmatched(group: str, type_: str, feature_vars: dict) -> Tuple[List[str], bool]:
    """Leave-one-out diagnosis for a no-code row.

    Returns ``(unmatched_features, code_would_match)``.

    Uses the same required + OR constraints as ``assign_code_from_csv`` /
    ``lookup_code`` so EA never reports "code features match" when assign
    would return empty (or the reverse).
    """
    from .code_assigner import assign_code_from_csv

    # Authoritative: if assign finds a code, features match.
    try:
        if assign_code_from_csv(group, type_, feature_vars or {}):
            return [], True
    except Exception:
        logger.exception("diagnose assign_code_from_csv failed")

    search_by_pos, labels, or_groups_by_pos = _build_search_by_pos(group, type_, feature_vars)
    if search_by_pos is None:
        return [], False
    if not search_by_pos and not or_groups_by_pos:
        return [], False

    full = code_db.count_matches(group, search_by_pos, or_groups_by_pos)
    if full > 0:
        # Count and assign can briefly disagree (e.g. cache); treat as match.
        return [], True

    remaining = dict(search_by_pos)
    blamed: List[str] = []
    label_of = {pos: lab for lab, pos in labels}

    # Only drop labeled (non-empty) required features during leave-one-out.
    drop_order_positions = [pos for _lab, pos in labels]
    for _ in range(len(drop_order_positions)):
        if code_db.count_matches(group, remaining, or_groups_by_pos) > 0:
            break
        best_pos = None
        best_gain = -1
        for pos in list(remaining.keys()):
            if pos not in label_of:
                continue
            trial = {k: v for k, v in remaining.items() if k != pos}
            n = code_db.count_matches(group, trial, or_groups_by_pos)
            if n > best_gain:
                best_gain = n
                best_pos = pos
        if best_pos is None:
            break
        blamed.append(label_of.get(best_pos, f"col_{best_pos + 1}"))
        del remaining[best_pos]
        if best_gain > 0:
            if code_db.count_matches(group, remaining, or_groups_by_pos) > 0:
                break
    return blamed, False


def build_assistant_context(text: str, group: str, type_: str,
                            remark: str, revision: str,
                            clean_size: str = "",
                            row_index: int = 0,
                            locked_group: str = "",
                            locked_type: str = "") -> dict:
    """One-shot context for the EA panel (alarms + orange/red + diagnose)."""
    data_path = json_path("data.json")
    json_dict = load_json_file(data_path)
    locked_g = str(locked_group or "").strip() or None
    locked_t = str(locked_type or "").strip() or None
    result = process_text_record_live(
        text,
        json_dict,
        group_key_input=group,
        type_key_input=type_,
        remark=remark,
        revision=revision,
        clean_size=clean_size,
        row_index=row_index,
        allow_code_lookup=False,
        # Honour a Revision Confirm lock so EA rebuilds under the new group
        # (description alone would otherwise keep the previous group).
        locked_group=locked_g,
        locked_type=locked_t,
        confirm_group_change=True if locked_g else None,
    )
    g = str(result.get("Group") or locked_g or group or "").strip()
    t = str(result.get("Type") or locked_t or type_ or "").strip()
    fv = result.get("Feature_Variables") or {}
    alarms = list(result.get("Alarm") or [])
    tvm = result.get("Target_Values_Map") or {}
    fmap = _feature_map_from_vars(fv, g, t)

    # --- Red (size not in rules) + Orange ---
    red_items = _colored_features(fmap, tvm, g, t, colors=("red",))
    orange = _orange_features(fmap, tvm, g, t)
    orange = _rank_orange_items(orange, g, t, fmap, json_dict)

    def _peer_values_for(item: dict, pool: List[dict]) -> List[str]:
        """Other conflict values used to constrain this dropdown.

        Material + grade_material are emitted as one compound peer
        (``ASTM A216 Gr.WCB``) — never as bare ``ASTM A216`` / ``Gr.WCB``,
        which are not rules keys and would wipe valid trim/etc. suggestions.
        Missing half of the compound is filled from the live fmap.
        """
        self_n = _norm_val(item.get("value"))
        self_f = str(item.get("feature") or "").strip().lower()
        by_feat: Dict[str, str] = {}
        for it in pool:
            feat = str(it.get("feature") or "").strip().lower()
            if not feat or feat == "size" or feat == self_f:
                continue
            v = str(it.get("value") or "").strip()
            if not v or _norm_val(v) == self_n:
                continue
            by_feat[feat] = v

        peers: List[str] = []
        mat = str(by_feat.pop("material", "") or "").strip()
        grade = str(by_feat.pop("grade_material", "") or "").strip()
        # Orange chips often show material without grade (grade skipped in
        # _colored_features) — pull the live grade/material from fmap.
        if mat and not grade:
            grade = str(_fmap_get(fmap, "grade_material") or "").strip()
        if grade and not mat:
            mat = str(_fmap_get(fmap, "material") or "").strip()
        if mat and grade:
            if _norm_val(grade) in _norm_val(mat):
                peers.append(mat)
            else:
                peers.append(f"{mat} {grade}".strip())
        elif mat:
            peers.append(mat)
        elif grade:
            peers.append(grade)
        for _feat, v in by_feat.items():
            peers.append(v)
        return peers

    conflict_pool = list(orange) + list(red_items)

    red_fields = []
    for item in red_items:
        feat_s = item["feature"]
        # Size suggestions: cascade from other features only (ignore_size N/A).
        vals = suggest_for_feature(
            g, t, fmap, feat_s,
            exclude_self=True,
            json_dict=json_dict,
            ignore_size=False,
            peer_values=_peer_values_for(item, conflict_pool),
            exclude_values=[item["value"]],
        )
        red_fields.append({
            "feature": feat_s,
            "value": item["value"],
            "values": vals,
            "priority": "missing",
            "color": "red",
            "replace_values": _replace_values_for(fmap, g, t, feat_s),
            "apply_kind": "size" if feat_s.lower() == "size" else "value",
        })

    orange_fields = []
    for item in orange:
        feat_s = item["feature"]
        is_size = feat_s.lower() == "size"
        vals = suggest_for_feature(
            g, t, fmap, feat_s,
            exclude_self=True,
            json_dict=json_dict,
            ignore_size=not is_size,
            peer_values=[] if is_size else _peer_values_for(item, conflict_pool),
            exclude_values=[item["value"]],
        )
        in_rules = _value_in_rules(
            g, feat_s, item["value"],
            _selected_for_rules(fmap, g, t, exclude_features={feat_s.lower()}),
        )
        orange_fields.append({
            "feature": feat_s,
            "value": item["value"],
            "values": vals,
            "priority": "missing" if not in_rules else "conflict",
            "color": "orange",
            "replace_values": _replace_values_for(fmap, g, t, feat_s),
            "apply_kind": "material" if feat_s.lower() in ("material", "grade_material") else "value",
        })

    has_orange = any(str(c).lower() == "orange" for c in tvm.values())
    has_red = any(str(c).lower() == "red" for c in tvm.values())

    def _is_size_item(it: dict) -> bool:
        return str(it.get("feature") or "").strip().lower() == "size"

    non_size_orange = [o for o in orange_fields if not _is_size_item(o)]
    # Size-only orange/red must NOT block alarm fields — user can fill alarms
    # while size remains the last conflict.
    conflict_blocks_alarms = bool(non_size_orange)

    # Alarms after non-size oranges are cleared (size may still be orange/red).
    alarm_fields = []
    if not conflict_blocks_alarms:
        seen_alarm = set()
        for feat in alarms:
            feat_s = str(feat).strip()
            if not feat_s:
                continue
            fl = feat_s.lower()
            if fl == "phisic" or fl.startswith("phisic"):
                for af in _expand_phisic_alarm_fields(g, t, fmap, json_dict):
                    key = af["feature"].lower()
                    if key in seen_alarm:
                        continue
                    seen_alarm.add(key)
                    alarm_fields.append(af)
                continue
            # A raw feature that is compound-grouped WITH "type" in
            # asign_code.json ("prefix & type & degree & sufix" for Fitting)
            # is redirected to the group's actual primary selector: Type.
            # "type" is not derived FROM prefix/degree/sufix — it is its own
            # value (Feature_Variables["<group>_type"], e.g. "Elbow 90° LR"),
            # already correctly resolved by _resolve_feature_value and
            # already correctly suggested by suggest_for_feature's own
            # feat_l == "type" branch (data.json's type list). Offering that
            # instead of the bare "prefix" alarm — and applying the picked
            # type NAME as inserted description text via the ordinary
            # apply_kind="value" path — lets the same extraction pipeline
            # that already derives prefix/degree/sufix from client text
            # (e.g. a description that says "Elbow 90 LR") do so again here,
            # rather than this file trying to re-derive that mapping itself.
            type_members = None
            try:
                for members in compound_groups(g, t or "").values():
                    lows = {str(m).strip().lower() for m in members}
                    if fl in lows and "type" in lows:
                        type_members = lows
                        break
            except Exception:
                type_members = None
            if type_members:
                if "type" in seen_alarm:
                    continue
                seen_alarm.add("type")
                seen_alarm.update(type_members)
                type_vals = suggest_for_feature(
                    g, t, fmap, "type", exclude_self=False, json_dict=json_dict, ignore_size=True
                )
                alarm_fields.append({
                    "feature": "type",
                    "label": "Type",
                    "values": type_vals,
                    "apply_kind": "value",
                    "replace_values": _replace_values_for(fmap, g, t, "type"),
                })
                continue
            # Material compound (material & grade_material & material_class):
            # title = first member in data.json order (usually ``material``);
            # values stay the joined compound spellings from rules.
            mat_members = None
            try:
                for members in compound_groups(g, t or "").values():
                    lows = {str(m).strip().lower() for m in members}
                    if fl in lows and "material" in lows:
                        mat_members = lows
                        break
            except Exception:
                mat_members = None
            if mat_members or fl in ("material", "grade_material", "material_class"):
                primary = _compound_primary_label(g, t, "material", json_dict)
                primary_l = str(primary).strip().lower() or "material"
                if primary_l in seen_alarm or "material" in seen_alarm:
                    if mat_members:
                        seen_alarm.update(mat_members)
                    seen_alarm.update({"material", "grade_material", "material_class"})
                    continue
                seen_alarm.add(primary_l)
                seen_alarm.update({"material", "grade_material", "material_class"})
                if mat_members:
                    seen_alarm.update(mat_members)
                mat_vals = suggest_for_feature(
                    g, t, fmap, "material", exclude_self=False, json_dict=json_dict, ignore_size=True
                )
                alarm_fields.append({
                    "feature": "material",
                    "label": primary,
                    "values": mat_vals,
                    "apply_kind": "material",
                    "replace_values": _replace_values_for(fmap, g, t, "material"),
                })
                continue
            if fl in seen_alarm:
                continue
            seen_alarm.add(fl)
            vals = suggest_for_feature(
                g, t, fmap, feat_s, exclude_self=False, json_dict=json_dict, ignore_size=True
            )
            apply_kind = "material" if fl in ("material", "grade_material") else "value"
            if fl in ("phisic_sch", "schedule"):
                apply_kind = "sch"
            if fl == "group":
                apply_kind = "group"
            alarm_fields.append({
                "feature": feat_s,
                "label": feat_s,
                "values": vals,
                "apply_kind": apply_kind,
                "replace_values": _replace_values_for(fmap, g, t, feat_s),
            })

    unmatched: List[str] = []
    code_ok = False
    non_size_red = [r for r in red_fields if not _is_size_item(r)]
    # Size-only orange/red must not block diagnose: code lookup may clear a
    # stale size orange, or leave-one-out may offer create-size.
    conflict_open = bool(non_size_orange) or bool(non_size_red)
    if not alarms and not conflict_open:
        try:
            unmatched, code_ok = diagnose_unmatched(g, t, fv)
        except Exception:
            logger.exception("diagnose_unmatched failed")
            unmatched, code_ok = [], False

    # If the code table matched, drop size-only conflict chips so EA does not
    # show Conflict · Orange alongside All clear (process used
    # allow_code_lookup=False so TVM may still hold a stale size orange).
    if code_ok and (not non_size_orange) and (not non_size_red):
        if orange_fields or red_fields or has_orange or has_red:
            orange_fields = [o for o in orange_fields if not _is_size_item(o)]
            red_fields = [r for r in red_fields if not _is_size_item(r)]
            for val in list(tvm.keys()):
                if str(tvm.get(val) or "").strip().lower() in ("orange", "red"):
                    tvm.pop(val, None)
            has_orange = False
            has_red = False

    code_selected: Dict[str, str] = {}
    display_attrs: List[dict] = []
    try:
        code_selected, display_attrs = _build_code_selected(g, t, fmap, feature_vars=fv)
    except Exception:
        logger.exception("build code_selected failed")
        code_selected, display_attrs = {}, []

    return {
        "Group": g,
        "Type": t,
        "Alarm": alarms,
        "Has_Orange_Alert": has_orange or has_red,
        "Has_Red_Alert": has_red,
        "red_fields": red_fields,
        "alarm_fields": alarm_fields,
        "orange_fields": orange_fields,
        "unmatched": unmatched,
        "code_matched": code_ok,
        "ok": (not alarms and not conflict_open and code_ok),
        # Raw fmap (feature extractor names) — kept for compatibility.
        "resolved_values": fmap,
        # Main-feature map for Preview / create-size (asign_code compounds,
        # canonical FeatureValue spellings, empty = NO / absent).
        "code_selected": code_selected,
        "display_attrs": display_attrs,
    }



@login_required
@require_POST
def assistant_context_ajax(request):
    try:
        text = request.POST.get("text", "").strip()
        group = request.POST.get("group", "").strip()
        type_ = request.POST.get("type", "").strip()
        remark = request.POST.get("remark", "").strip()
        revision = request.POST.get("revision", "").strip()
        clean_size = request.POST.get("clean_size", "").strip()
        locked_group = request.POST.get("locked_group", "").strip()
        locked_type = request.POST.get("locked_type", "").strip()
        try:
            row_index = int(request.POST.get("row_index", "0"))
        except Exception:
            row_index = 0
        ctx = build_assistant_context(
            text, group, type_, remark, revision, clean_size, row_index,
            locked_group=locked_group, locked_type=locked_type,
        )
        return JsonResponse(ctx)
    except Exception as exc:
        logger.exception("assistant_context_ajax failed")
        return JsonResponse({"error": str(exc) or "failed", "alarm_fields": [],
                             "orange_fields": [], "unmatched": []}, status=500)


@login_required
@require_POST
def assistant_options_ajax(request):
    try:
        text = request.POST.get("text", "").strip()
        group = request.POST.get("group", "").strip()
        type_ = request.POST.get("type", "").strip()
        remark = request.POST.get("remark", "").strip()
        revision = request.POST.get("revision", "").strip()
        clean_size = request.POST.get("clean_size", "").strip()
        target = request.POST.get("target", "").strip()
        exclude_self = request.POST.get("exclude_self", "") in ("1", "true", "yes")
        locked_group = request.POST.get("locked_group", "").strip() or None
        locked_type = request.POST.get("locked_type", "").strip() or None
        if not target:
            return JsonResponse({"values": []})
        json_dict = load_json_file(json_path("data.json"))
        result = process_text_record_live(
            text, json_dict,
            group_key_input=group, type_key_input=type_,
            remark=remark, revision=revision, clean_size=clean_size,
            allow_code_lookup=False,
            locked_group=locked_group,
            locked_type=locked_type,
            confirm_group_change=True if locked_group else None,
        )
        g = str(result.get("Group") or locked_group or group or "").strip()
        t = str(result.get("Type") or locked_type or type_ or "").strip()
        fmap = _feature_map_from_vars(result.get("Feature_Variables") or {}, g, t)
        vals = suggest_for_feature(g, t, fmap, target, exclude_self=exclude_self, json_dict=json_dict)
        return JsonResponse({"feature": target, "values": vals})
    except Exception as exc:
        logger.exception("assistant_options_ajax failed")
        return JsonResponse({"error": str(exc), "values": []}, status=500)


# --------------------------------------------------------------------------- #
# EA-driven item creation — Size extension ONLY.
# --------------------------------------------------------------------------- #
def _ea_selected_get(selected: dict, name: str) -> str:
    """Case-insensitive lookup of a feature value in the resolved map."""
    if not isinstance(selected, dict) or not name:
        return ""
    if name in selected:
        return str(selected.get(name) or "").strip()
    nl = str(name).strip().lower()
    for k, v in selected.items():
        if str(k).strip().lower() == nl:
            return str(v or "").strip()
    return ""


def _ea_is_absent_attr(val: str) -> bool:
    """Empty / NO COATING / NO NACE / $coating$ — intentional absent, not a gap."""
    from .code_db import is_absent_cell
    s = str(val or "").strip()
    if not s:
        return True
    if is_absent_cell(s):
        return True
    # Display labels like "NO NACE" / "NO COATING" that may not already
    # match is_absent_cell's regex in every spelling.
    if re.match(r"(?i)^no[\s_-]+", s):
        return True
    return False


def _ea_absent_relation_token(group: str, feature_name: str) -> str:
    """FeatureValue placeholder used when a main feature is intentionally absent.

    ``add_value_with_relations`` requires every other main feature to be linked.
    For Coating / NACE / SDR / Pressure Class the schema stores ``$coating$``
    (etc.); that is what the Feature Values screen selects for \"NO …\".
    """
    from .models import FeatureValue
    from .item_builder import _is_empty_variant

    feat = str(feature_name or "").strip()
    if not feat:
        return ""
    snake = re.sub(r"[^a-z0-9]+", "_", feat.lower()).strip("_")
    preferred = [f"${snake}$", f"${feat.lower()}$"]
    for cand in preferred:
        if FeatureValue.objects.filter(
            group=group, feature__iexact=feat, value__iexact=cand
        ).exists():
            hit = FeatureValue.objects.filter(
                group=group, feature__iexact=feat, value__iexact=cand
            ).first()
            return str(hit.value).strip() if hit else cand
    for fv in FeatureValue.objects.filter(group=group, feature__iexact=feat):
        if _is_empty_variant(fv.value):
            return str(fv.value).strip()
    return ""


def _ea_relation_value(group: str, feature_name: str, selected_val: str) -> str:
    """Value to link in rules for ``feature_name`` (placeholder if absent)."""
    raw = str(selected_val or "").strip()
    if raw and not _ea_is_absent_attr(raw):
        return raw
    return _ea_absent_relation_token(group, feature_name)


def _ea_normalize_selected(group: str, selected: dict, main_names: list,
                           size_feature: str, type_: str = "") -> dict:
    """Map resolved values onto canonical main-feature names.

    Absent / unspecified attributes become empty strings (stored/displayed
    as NO … in the code table). Type is filled from the request when the
    resolved map omitted it.
    """
    out: Dict[str, str] = {}
    for name in main_names:
        val = _ea_selected_get(selected, name)
        if name.lower() == "type" and not val and type_:
            val = str(type_).strip()
        if _ea_is_absent_attr(val) and name.lower() != size_feature:
            out[name] = ""
        else:
            out[name] = val
    # Keep any extra keys the client sent (harmless for build_codes).
    if isinstance(selected, dict):
        for k, v in selected.items():
            if k not in out:
                out[k] = str(v or "").strip()
    return out


def _ea_validate_size_extension(group: str, selected: dict, size_feature: str,
                                type_: str = ""):
    """Server-side re-verification — the one gate this whole feature exists
    for. Never trusts what the client claims "already matched"; re-checks
    every value against the real database.

    Absent attributes (empty / NO COATING / NO NACE / …) are treated as
    matched: they carry through to the new size item as the same absent
    value, matching how diagnose_unmatched already ignores them.

    Returns (main_feature_names, size_value, normalized_selected) on success.
    Raises ValueError with a message safe to show the user on any failure.
    """
    from . import item_builder
    from .models import FeatureValue

    group = str(group or "").strip().lower()
    if not group:
        raise ValueError("No group selected.")

    mains = item_builder.main_features(group)
    if not mains:
        raise ValueError("This group has no configured features.")
    main_names = [f.name for f in mains]

    size_feature = str(size_feature or "size").strip().lower()
    if size_feature not in {n.lower() for n in main_names}:
        raise ValueError(
            "This group has no 'size' feature configured — EA cannot "
            "create an item here."
        )

    size_value = _ea_selected_get(selected, size_feature)
    if not size_value:
        raise ValueError("No size value to add.")

    normalized = _ea_normalize_selected(
        group, selected or {}, main_names, size_feature, type_=type_
    )
    # Always keep the new size under the canonical feature name.
    for name in main_names:
        if name.lower() == size_feature:
            normalized[name] = size_value
            break

    missing = []
    for name in main_names:
        if name.lower() == size_feature:
            continue
        val = str(normalized.get(name, "") or "").strip()
        if _ea_is_absent_attr(val):
            # Intentional NO / empty — same as an existing coded row that
            # stores this feature blank. Not a blocker for size extension.
            continue
        exists = FeatureValue.objects.filter(
            group=group, feature=name, value__iexact=val).exists()
        if not exists:
            # Feature name casing in FeatureValue may differ from GroupFeature.
            exists = FeatureValue.objects.filter(
                group=group, feature__iexact=name, value__iexact=val).exists()
        if not exists:
            # Spelling variants (``ASTM A403`` vs ``ASTM A 403``) share a
            # code-normalized form — accept those and rewrite to the stored
            # FeatureValue spelling so build_codes finds the right code.
            canon = _canonical_feature_value(group, name, val)
            if canon and canon != val:
                exists = FeatureValue.objects.filter(
                    group=group, feature__iexact=name, value__iexact=canon
                ).exists()
                if exists:
                    normalized[name] = canon
                    val = canon
        if not exists:
            missing.append(name)

    if missing:
        raise ValueError(
            "Every other attribute must already exist in the code table "
            "before EA can add a new size. Not yet matched: "
            + ", ".join(missing)
            + ". Adding a new attribute combination requires Technical "
              "Manager access via Tool Data."
        )

    # Size itself must genuinely be new — if it already has a code, this
    # isn't a "create" at all (the caller should have just used the
    # existing code; ea_create_size_item checks this too, but refusing here
    # keeps this validator's contract unambiguous: it only ever approves a
    # combination that is a real, currently-missing size extension).
    already_coded = FeatureValue.objects.filter(
        group=group, feature__iexact=size_feature, value__iexact=size_value
    ).exists()
    if already_coded:
        raise ValueError(
            "This size already has a code for this group — nothing new to create."
        )

    return main_names, size_value, normalized


@login_required
@require_POST
def ea_create_size_item(request):
    """EA's one write path: add a new database item that is an existing
    attribute combination plus one new Size.

    Deliberately writes no new code-generation or file-rewriting logic of
    its own. Every actual mutation below is the exact same function the
    admin-facing screens already use:
      - item_builder.add_value_with_relations: the Feature Values screen's
        own "add a new value" action (auto code + rules.json wiring).
      - item_builder.build_codes / build_row_cells and code_db.insert_item:
        the Add Item screen's own creation path.
    This view's only real job is _ea_validate_size_extension above — refusing
    anything that isn't a pure size extension of an already-fully-matched
    combination — plus the audit log entry, which is the one thing neither
    of those existing screens needed before.

    Pass dry_run=1 to validate and preview technical/item codes without writing.
    """
    from . import item_builder, code_db
    from .models import CodeTable, FeatureValue, EaItemCreationLog

    group = (request.POST.get("group") or "").strip().lower()
    type_ = (request.POST.get("type") or "").strip()
    size_feature = (request.POST.get("size_feature") or "size").strip().lower()
    case_id_raw = (request.POST.get("case_id") or "").strip()
    row_client_no = (request.POST.get("row_client_no") or "").strip()
    dry_run = (request.POST.get("dry_run") or "").strip().lower() in ("1", "true", "yes")

    import json as _json
    try:
        selected = _json.loads(request.POST.get("selected") or "{}")
        if not isinstance(selected, dict):
            selected = {}
    except Exception:
        return JsonResponse({"ok": False, "error": "Malformed attribute selection."}, status=400)

    # Always resolve asign_code compounds (Type = prefix+type+…, Material
    # Standard = material+grade+…) before validation — the client may send
    # either raw fmap fragments or a partial code_selected map.
    try:
        rebuilt, _disp = _build_code_selected(group, type_, selected, feature_vars=None)
        if rebuilt:
            # Keep explicit Size from the client when present.
            size_keep = _ea_selected_get(selected, size_feature) if size_feature else ""
            if not size_keep:
                size_keep = _ea_selected_get(selected, "Size") or _ea_selected_get(selected, "size")
            selected = dict(rebuilt)
            if size_keep:
                for n in selected:
                    if str(n).strip().lower() == "size":
                        selected[n] = size_keep
                        break
                else:
                    selected["Size"] = size_keep
    except Exception:
        logger.exception("ea_create_size_item: code_selected rebuild failed")

    try:
        case_id = int(case_id_raw) if case_id_raw else None
    except ValueError:
        case_id = None

    try:
        main_names, size_value, selected = _ea_validate_size_extension(
            group, selected, size_feature, type_=type_
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("ea_create_size_item validation failed")
        return JsonResponse({"ok": False, "error": "Could not validate this item."}, status=500)

    if not code_db.has_db(group):
        return JsonResponse({"ok": False, "error": "This group has no code database."}, status=400)

    try:
        technical, item_code, _prefix = item_builder.build_codes(group, selected)
    except Exception as exc:
        logger.exception("ea_create_size_item: build_codes failed")
        return JsonResponse({"ok": False, "error": f"Could not build codes: {exc}"}, status=500)

    if dry_run:
        return JsonResponse({
            "ok": True,
            "preview": True,
            "technical": technical,
            "item": item_code,
            "size": size_value,
        })

    try:
        # Register the new size (auto code + rules.json wiring), reusing the
        # exact mechanism the Feature Values screen uses — "relations" tells
        # it which other already-chosen values this size should be linked
        # to, exactly like an admin filling in that screen's own form would.
        # Canonical main-feature spelling (``Size`` not ``size``) — required
        # by add_value_with_relations which matches GroupFeature.name exactly.
        size_main = next(
            (n for n in main_names if str(n).strip().lower() == size_feature),
            None,
        )
        if not size_main:
            return JsonResponse({
                "ok": False,
                "error": "This group has no 'size' feature configured.",
            }, status=400)
        relations = {}
        for name in main_names:
            if name == size_main:
                continue
            link = _ea_relation_value(
                group, name, str(selected.get(name, "") or "")
            )
            if link:
                relations[name] = [link]
        item_builder.add_value_with_relations(group, size_main, size_value, relations)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception:
        logger.exception("ea_create_size_item: add_value_with_relations failed")
        return JsonResponse({"ok": False, "error": "Could not register the new size."}, status=500)

    try:
        if code_db.code_exists(group, technical, col=0):
            return JsonResponse({
                "ok": False,
                "error": "This exact item already exists (same technical code).",
            }, status=400)
        cells = item_builder.build_row_cells(group, selected, technical, item_code)
        code_db.insert_item(group, cells)
        meta = CodeTable.objects.filter(group=group).first()
        if meta:
            meta.row_count = code_db.row_count(group)
            meta.save(update_fields=["row_count", "updated_at"])
        try:
            from .data_admin import _clear_caches
            _clear_caches()
        except Exception:
            pass
    except Exception as exc:
        # The new size's FeatureValue may now exist with no row using it yet
        # (see module docstring on add_value_with_relations' caller side) —
        # harmless and self-correcting: retrying this same request will find
        # the size already registered, skip straight to this block, and
        # succeed once whatever failed here is fixed.
        logger.exception("ea_create_size_item: row creation failed")
        return JsonResponse({"ok": False, "error": f"Could not create the item: {exc}"}, status=500)

    log_user = request.user if getattr(request.user, "is_authenticated", False) else None
    EaItemCreationLog.objects.create(
        user=log_user,
        user_username=(log_user.username if log_user else ""),
        group=group, item_type=type_,
        selected_values=selected,
        new_feature=size_feature, new_value=size_value,
        technical_code=technical, item_code=item_code,
        case_id=case_id, row_client_no=row_client_no,
    )

    return JsonResponse({"ok": True, "technical": technical, "item": item_code})
