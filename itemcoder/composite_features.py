"""Compound-feature awareness driven by ``asign_code.json``.

Some product features are stored in ONE coding-table column but are really the
concatenation of several logical features.  ``asign_code.json`` declares this
with ``&`` inside a key, e.g. for pipe::

    "material & grade_material & spec": "col_5"

That means the three variables ``material``, ``grade_material`` and ``spec``
(built by ``data.json`` as ``material_<group>_<type>`` etc.) together form one
displayed unit such as ``API 5L Gr.B PSL1``.

The rule/offer engine matches and colors each variable independently.  This
module lets the engine treat a ``&`` group as a single unit for COLORING only:
when one member is colored (offer green / alert orange), its siblings get the
same color so the whole unit reads consistently.  It never changes matching or
code-assignment logic.
"""
from __future__ import annotations

import re

from .regex_patterns import load_json_file
from .resource_paths import json_path
from .composite_keys import get_by_alias


# Cache: (group_l, type_l) -> {feature_name -> (sibling_name, ...)}.  Cleared by
# constants.clear_data_caches() together with the other reference caches.
_COMPOUND_SIBLINGS_CACHE: dict = {}
# Cache: (group_l, type_l) -> {feature_name -> (ordered full group members)}.
_COMPOUND_GROUP_CACHE: dict = {}


def _split_compound_key(key: str) -> list:
    """Return the individual feature names in a ``&`` compound key.

    ``or1_`` OR-group prefixes are stripped from each part so the names match
    the ``<feature>_<group>_<type>`` variables built by the extractor.
    """
    parts = [p.strip() for p in str(key).split("&") if p.strip()]
    cleaned = []
    for p in parts:
        # An OR wrapper like ``or1_phisic_sch`` still refers to feature phisic_sch.
        m = re.match(r"^or\d+[_](.+)$", p)
        cleaned.append((m.group(1) if m else p).strip())
    return cleaned


def compound_siblings(group, type_) -> dict:
    """Map every feature in a ``&`` group to the OTHER features of that group.

    Example (pipe): ``{"material": ("grade_material", "spec"),
    "grade_material": ("material", "spec"), "spec": ("material",
    "grade_material")}``.  Features that are not part of any compound key are
    simply absent.  Returns an empty dict when asign_code.json is missing or has
    no compound keys for this group/type.
    """
    group_l = str(group or "").strip().lower()
    type_l = str(type_ or "").strip().lower()
    cache_key = (group_l, type_l)
    if cache_key in _COMPOUND_SIBLINGS_CACHE:
        return _COMPOUND_SIBLINGS_CACHE[cache_key]

    siblings: dict = {}
    try:
        mapping_all = load_json_file(json_path("asign_code.json"))
        group_map = get_by_alias(mapping_all, group_l)
        feature_map = get_by_alias(group_map, type_l) if isinstance(group_map, dict) else None
        if isinstance(feature_map, dict):
            for key in feature_map.keys():
                if "&" not in str(key):
                    continue
                members = _split_compound_key(key)
                if len(members) < 2:
                    continue
                lowered = [m.lower() for m in members]
                for i, name in enumerate(lowered):
                    others = tuple(m for j, m in enumerate(lowered) if j != i)
                    # Merge if a feature somehow appears in two compound keys.
                    prev = siblings.get(name, ())
                    merged = list(prev)
                    for o in others:
                        if o not in merged:
                            merged.append(o)
                    siblings[name] = tuple(merged)
    except Exception:
        siblings = {}

    _COMPOUND_SIBLINGS_CACHE[cache_key] = siblings
    return siblings


def compound_groups(group, type_) -> dict:
    """Map every compound member to the ORDERED full member list of its group.

    Example (pipe): ``{"material": ("material", "grade_material", "spec"),
    "grade_material": ("material", "grade_material", "spec"), ...}`` — the tuple
    preserves the order declared in asign_code.json so callers can join the
    parts in the correct sequence (material + grade_material + spec).
    """
    group_l = str(group or "").strip().lower()
    type_l = str(type_ or "").strip().lower()
    cache_key = (group_l, type_l)
    if cache_key in _COMPOUND_GROUP_CACHE:
        return _COMPOUND_GROUP_CACHE[cache_key]

    groups: dict = {}
    try:
        mapping_all = load_json_file(json_path("asign_code.json"))
        group_map = get_by_alias(mapping_all, group_l)
        feature_map = get_by_alias(group_map, type_l) if isinstance(group_map, dict) else None
        if isinstance(feature_map, dict):
            for key in feature_map.keys():
                if "&" not in str(key):
                    continue
                members = tuple(m.lower() for m in _split_compound_key(key))
                if len(members) < 2:
                    continue
                for name in members:
                    groups[name] = members
    except Exception:
        groups = {}

    _COMPOUND_GROUP_CACHE[cache_key] = groups
    return groups


def compound_group_members(group, type_, feature):
    """Return the ordered member tuple of the compound group that ``feature``
    belongs to, or ``None`` when the feature is not part of a compound key."""
    return compound_groups(group, type_).get(str(feature or "").strip().lower())


def clear_cache() -> None:
    _COMPOUND_SIBLINGS_CACHE.clear()
    _COMPOUND_GROUP_CACHE.clear()
