"""
itemcoder.offer_builder
=======================
Per-group management of the "offer" mapping (offer_<group>.json) and the
interactive builder used in the admin.

File shape (per group), e.g. offer_pipe.json::

    { "pipe": { "pipe": { "<feature>": { "<value>": { "<other_feature>": [values...] } } } } }

i.e. group -> type(=group) -> selected feature -> selected value -> other feature
-> list of allowed values.

Builder logic (matches the admin UX):
  * The admin picks ONE main feature (e.g. material_type) and ONE of its values
    (e.g. C.S). Then, for every OTHER main feature, they tick the values that are
    allowed for that (feature,value) pair. Saving writes them under
    group.group.material_type."C.S".<other_feature> = [ticked values].
  * CROSS-VALUE EXCLUSION: a value already assigned to one value of a feature
    cannot be offered again for another value of that SAME feature. So when the
    admin later builds material_type="S.S", the material list must EXCLUDE every
    material already used under any other material_type value (e.g. C.S). Likewise
    the pool of values shown for the picked feature itself excludes values already
    consumed by other selections. This is enforced by `available_values`.
"""

from __future__ import annotations

import glob
import json
import os

from .resource_paths import RESOURCE_DIR
from . import item_builder


def _offer_path(group: str) -> str:
    g = str(group).strip().lower()
    return os.path.join(str(RESOURCE_DIR), "json", f"offer_{g}.json")


def _legacy_offer_path() -> str:
    return os.path.join(str(RESOURCE_DIR), "json", "offer.json")


def _load_group(group: str) -> dict:
    """Return the inner map for a group: {type: {feature: {value: {other: [...]}}}}.
    Prefers the per-group file, falls back to the legacy shared offer.json."""
    g = str(group).strip().lower()
    p = _offer_path(g)
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if isinstance(d, dict):
                return d.get(g, d) if g in {str(k).lower() for k in d.keys()} else d
        except Exception:
            pass
    # Legacy fallback.
    lp = _legacy_offer_path()
    if os.path.exists(lp):
        try:
            d = json.load(open(lp, encoding="utf-8"))
            if isinstance(d, dict):
                for k, v in d.items():
                    if str(k).lower() == g and isinstance(v, dict):
                        return v
        except Exception:
            pass
    return {}


def _save_group(group: str, inner: dict) -> None:
    g = str(group).strip().lower()
    path = _offer_path(g)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({g: inner}, fh, ensure_ascii=False, indent=1)


def has_offer(group: str) -> bool:
    return bool(_load_group(group))


def _type_key(group: str, inner: dict) -> str:
    """Offer nests one level under a 'type' that is normally the group name."""
    g = str(group).strip().lower()
    if g in {str(k).lower() for k in inner.keys()}:
        for k in inner.keys():
            if str(k).lower() == g:
                return k
    # Fall back to the group name (new files).
    return g


def _feature_block(group: str) -> dict:
    """Return {feature: {value: {other_feature: [values]}}} for the group."""
    inner = _load_group(group)
    if not inner:
        return {}
    tk = _type_key(group, inner)
    blk = inner.get(tk, {})
    return blk if isinstance(blk, dict) else {}


def ensure_offer_file(group: str) -> str:
    """Make sure a per-group offer file exists (migrating from the legacy shared
    file if present, else an empty skeleton). Returns the path."""
    g = str(group).strip().lower()
    path = _offer_path(g)
    if not os.path.exists(path):
        inner = {}
        # Migrate this group out of the legacy shared file if present.
        lp = _legacy_offer_path()
        if os.path.exists(lp):
            try:
                d = json.load(open(lp, encoding="utf-8"))
                if isinstance(d, dict):
                    for k, v in d.items():
                        if str(k).lower() == g and isinstance(v, dict):
                            inner = v
                            break
            except Exception:
                pass
        if not inner:
            inner = {g: {}}   # type bucket keyed by the group name
        _save_group(g, inner)
    return path


def replace_offer_from_obj(group: str, obj) -> None:
    """Replace a group's offer with an uploaded JSON object. Accepts the wrapped
    {group:{...}} shape or the bare inner {type:{...}} shape."""
    g = str(group).strip().lower()
    if not isinstance(obj, dict):
        raise ValueError("The uploaded file must be a JSON object.")
    inner = obj.get(g) if g in {str(k).lower() for k in obj.keys()} else obj
    if not isinstance(inner, dict):
        raise ValueError("The uploaded offer file has an unexpected shape.")
    _save_group(g, inner)


def offer_export_obj(group: str) -> dict:
    g = str(group).strip().lower()
    inner = _load_group(g)
    return {g: inner}


# --------------------------------------------------------------------------- #
# Builder helpers
# --------------------------------------------------------------------------- #
def main_features(group: str) -> list:
    """Ordered list of main-feature names for the group."""
    return [f.name for f in item_builder.main_features(group)]


def all_values(group: str, feature: str) -> list:
    """Every known value of a feature (from the feature schema / FeatureValue)."""
    from .models import FeatureValue
    vals = list(
        FeatureValue.objects.filter(group=str(group).strip().lower(), feature=feature)
        .order_by("value").values_list("value", flat=True)
    )
    # De-dup while keeping order.
    seen, out = set(), []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _values_used_for_other(block: dict, sel_feature: str, sel_value: str,
                           other_feature: str) -> set:
    """All values of `other_feature` already assigned under OTHER values of
    `sel_feature` (i.e. everything except the currently-selected value).

    Reads simple single-feature lists as well as AND (``fA & fB``) and OR
    (``fA || fB``) condition keys, so a value consumed anywhere under a sibling
    value is excluded."""
    used = set()
    feat_map = block.get(sel_feature, {})
    if not isinstance(feat_map, dict):
        return used
    for val, others in feat_map.items():
        if str(val) == str(sel_value):
            continue  # keep the current value's own picks selectable
        if not isinstance(others, dict):
            continue
        for cond_key, cond_val in others.items():
            key = str(cond_key)
            if "&" in key or "||" in key:
                sep = "&" if "&" in key else "||"
                keys = [k.strip() for k in key.split(sep)]
                vals = [v.strip() for v in str(cond_val).split(sep)]
                for i, k in enumerate(keys):
                    if k == other_feature and i < len(vals):
                        used.add(vals[i])
            elif key == other_feature:
                if isinstance(cond_val, list):
                    for v in cond_val:
                        used.add(str(v))
                elif str(cond_val).strip():
                    used.add(str(cond_val))
    return used


def available_values(group: str, sel_feature: str, sel_value: str,
                     other_feature: str) -> dict:
    """Return {"available": [...], "checked": [...]} for the ticker UI.

    * available = every value of `other_feature` MINUS those already consumed by
      other values of `sel_feature` (cross-value exclusion), PLUS the ones this
      (sel_feature, sel_value) pair already has (so they can be un-ticked).
    * checked   = the values this pair currently has saved.
    """
    block = _feature_block(group)
    every = all_values(group, other_feature)
    used_elsewhere = _values_used_for_other(block, sel_feature, sel_value, other_feature)

    # Currently-saved picks for this pair.
    checked = []
    feat_map = block.get(sel_feature, {})
    if isinstance(feat_map, dict):
        vmap = feat_map.get(sel_value, {})
        if isinstance(vmap, dict):
            checked = [str(v) for v in vmap.get(other_feature, []) or []]

    checked_set = set(checked)
    available = [v for v in every if v not in used_elsewhere or v in checked_set]
    return {"available": available, "checked": checked}


def values_of_feature_available(group: str, sel_feature: str) -> list:
    """Values selectable for the PICKED feature itself. Every value is offer-able
    (the picked feature's own value is what we are configuring), so this simply
    lists all its values; cross-value exclusion applies to the OTHER features."""
    return all_values(group, sel_feature)


def values_of_feature(group: str, feature: str) -> list:
    """All values of a feature (no exclusion). Used by the value pickers in the
    rebuilt offer UI where the picked value is the auto-fill *target*."""
    return all_values(group, feature)


def display_label(feature: str, value: str) -> str:
    """Readable label for a feature value.

    Empty/placeholder values (e.g. ``$coating$``) are shown as ``NO COATING`` —
    the same convention as the browse grid — while the stored value is kept
    unchanged so matching/coding still treats it as empty.
    """
    from .regex_patterns import is_empty_variant
    if is_empty_variant(value):
        return "NO " + str(feature).strip().upper()
    return str(value)


def values_of_feature_labeled(group: str, feature: str) -> list:
    """Feature values as ``{"value","label"}`` pairs for the offer pickers.

    ``value`` is stored/saved as-is; ``label`` is what the admin sees (so a
    ``$coating$`` placeholder reads as ``NO COATING``)."""
    return [{"value": v, "label": display_label(feature, v)}
            for v in all_values(group, feature)]


def values_of_feature_labeled_excluding(group: str, target_feature: str,
                                         target_value: str, cond_feature: str) -> list:
    """Condition values as ``{"value","label"}`` pairs WITH cross-value exclusion.

    A value already offered under ANOTHER value of ``target_feature`` is removed:
    e.g. once ``material`` = ``API 5L Gr.B PSL1`` is used for
    ``material_type`` = ``C.S``, it no longer appears when building
    ``material_type`` = ``S.S`` (a value can belong to only one target value).
    The current target value's own saved picks stay so they can be un-ticked."""
    avail = available_values(group, target_feature, target_value, cond_feature).get("available", [])
    return [{"value": v, "label": display_label(cond_feature, v)} for v in avail]


# --------------------------------------------------------------------------- #
# Rebuilt builder: read/write the full condition set for a (target feature,
# target value) pair, supporting BOTH simple single-feature lists and AND
# conditions ("fA & fB": "vA & vB"). OR is expressed by having several separate
# conditions under the same target value (the engine ORs them). This matches the
# exact shapes the coding engine already understands (see rule_engine.py).
# --------------------------------------------------------------------------- #
def get_conditions(group: str, target_feature: str, target_value: str) -> list:
    """Return the saved conditions for a (target_feature, target_value) pair as a
    normalized list the UI can render:

        [
          {"type": "single", "feature": "grade_material", "values": ["Gr.B", ...]},
          {"type": "and", "terms": [["production_method", "SMLS"],
                                     ["material_type", "C.S"]]},
          ...
        ]

    A single-feature key whose stored value is a plain string (e.g.
    "material_type": "S.S") is normalized to a one-item ``values`` list. An AND
    key ("A & B") with a "vA & vB" string becomes an ``and`` term list. OR keys
    ("A || B") are also read back as an ``or`` term list so an uploaded file that
    uses them is not lost, though the UI focuses on single + AND.
    """
    block = _feature_block(group)
    feat_map = block.get(target_feature, {})
    out: list = []
    if not isinstance(feat_map, dict):
        return out
    vmap = feat_map.get(target_value, {})
    if not isinstance(vmap, dict):
        return out
    for cond_key, cond_val in vmap.items():
        key = str(cond_key)
        if "&" in key:
            keys = [k.strip() for k in key.split("&")]
            vals = [v.strip() for v in str(cond_val).split("&")]
            terms = [[k, (vals[i] if i < len(vals) else "")] for i, k in enumerate(keys)]
            out.append({"type": "and", "terms": terms})
        elif "||" in key:
            keys = [k.strip() for k in key.split("||")]
            vals = [v.strip() for v in str(cond_val).split("||")]
            terms = [[k, (vals[i] if i < len(vals) else "")] for i, k in enumerate(keys)]
            out.append({"type": "or", "terms": terms})
        else:
            if isinstance(cond_val, list):
                values = [str(v) for v in cond_val]
            else:
                values = [str(cond_val)] if str(cond_val).strip() else []
            out.append({"type": "single", "feature": key, "values": values})
    return out


def _conditions_to_entry(conditions: list) -> dict:
    """Turn the UI condition list back into the stored dict shape.

    * single -> {feature: [values]}  (list form the engine reads)
    * and    -> {"fA & fB": "vA & vB"}
    * or     -> {"fA || fB": "vA || vB"}

    Later identical single-feature keys merge their value lists; identical AND/OR
    keys keep the last one (the UI never produces duplicates).
    """
    entry: dict = {}
    for cond in conditions or []:
        ctype = (cond or {}).get("type")
        if ctype == "single":
            feature = str(cond.get("feature", "")).strip()
            values = [str(v).strip() for v in (cond.get("values") or []) if str(v).strip()]
            if not feature or not values:
                continue
            existing = entry.get(feature)
            if isinstance(existing, list):
                for v in values:
                    if v not in existing:
                        existing.append(v)
            else:
                # De-dup while keeping order.
                seen, clean = set(), []
                for v in values:
                    if v not in seen:
                        seen.add(v)
                        clean.append(v)
                entry[feature] = clean
        elif ctype in ("and", "or"):
            terms = cond.get("terms") or []
            pairs = [(str(f).strip(), str(v).strip())
                     for f, v in terms if str(f).strip() and str(v).strip()]
            if len(pairs) < 2:
                # An AND/OR needs at least two terms; a single term is really a
                # one-value single condition.
                if len(pairs) == 1:
                    f, v = pairs[0]
                    if isinstance(entry.get(f), list):
                        if v not in entry[f]:
                            entry[f].append(v)
                    else:
                        entry[f] = [v]
                continue
            joiner = " & " if ctype == "and" else " || "
            key = joiner.join(f for f, _ in pairs)
            val = joiner.join(v for _, v in pairs)
            entry[key] = val
    return entry


def save_conditions(group: str, target_feature: str, target_value: str,
                    conditions: list) -> None:
    """Persist the full condition set for a (target_feature, target_value) pair,
    then write the group's offer file immediately (same pattern as rules)."""
    g = str(group).strip().lower()
    if not target_feature or not target_value:
        raise ValueError("target feature and value are required")
    inner = _load_group(g)
    if not inner:
        inner = {g: {}}
    tk = _type_key(g, inner)
    block = inner.setdefault(tk, {})
    if not isinstance(block, dict):
        block = {}
        inner[tk] = block
    feat_map = block.setdefault(target_feature, {})
    if not isinstance(feat_map, dict):
        feat_map = {}
        block[target_feature] = feat_map

    entry = _conditions_to_entry(conditions)
    if entry:
        feat_map[target_value] = entry
    else:
        feat_map.pop(target_value, None)
        if not feat_map:
            block.pop(target_feature, None)
    _save_group(g, inner)


def save_pair(group: str, sel_feature: str, sel_value: str,
              picks: dict) -> None:
    """Persist the ticked values for a (feature, value) pair.

    picks maps other_feature -> [ticked values]. Enforces cross-value exclusion:
    a value already used by another value of sel_feature is rejected.
    """
    g = str(group).strip().lower()
    inner = _load_group(g)
    if not inner:
        inner = {g: {}}
    tk = _type_key(g, inner)
    block = inner.setdefault(tk, {})
    if not isinstance(block, dict):
        block = {}
        inner[tk] = block

    feat_map = block.setdefault(sel_feature, {})
    if not isinstance(feat_map, dict):
        feat_map = {}
        block[sel_feature] = feat_map

    # Validate cross-value exclusion and build the cleaned entry.
    entry = {}
    for other_feature, values in (picks or {}).items():
        vals = [str(v).strip() for v in values if str(v).strip()]
        if not vals:
            continue
        used_elsewhere = _values_used_for_other(block, sel_feature, sel_value, other_feature)
        clash = [v for v in vals if v in used_elsewhere]
        if clash:
            raise ValueError(
                f"These values are already assigned to another {sel_feature}: "
                + ", ".join(clash)
            )
        # De-dup, keep order.
        seen, clean = set(), []
        for v in vals:
            if v not in seen:
                seen.add(v)
                clean.append(v)
        entry[other_feature] = clean

    if entry:
        feat_map[sel_value] = entry
    else:
        # Nothing ticked -> remove the value entry entirely.
        feat_map.pop(sel_value, None)

    _save_group(g, inner)
