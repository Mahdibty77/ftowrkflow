"""Shared processor constants, JSON rule paths, and cache containers.

Rules are stored in focused JSON files:

* ``common_rulse.json``: global/common alert rules (e.g. allowed thicknesses).
* ``offer.json`` / ``offer_<group>.json``: group/type offer (green) rules.
* ``rules_<group>.json``: per-group compatibility (orange) — read by item_builder.

For backward compatibility with the rest of the processor, this module rebuilds
one in-memory ``rules`` dictionary with the shape expected by ``apply_rules``
(common_rule + per-group offer blocks).
"""

import json
import os
import re

from django.conf import settings

from .resource_paths import JSON_DIR
from .composite_keys import iter_alias_items


# Base folder that contains all JSON configuration files used by the processor.
JSON_CONFIG_DIR = str(JSON_DIR)

# Split rule files.  The variable names are explicit so future changes are easy.
common_rules_path = os.path.join(JSON_CONFIG_DIR, "common_rulse.json")
offer_rules_path = os.path.join(JSON_CONFIG_DIR, "offer.json")

# Backward-compatible alias used by some existing imports/checks.
# It points to the common rules file because ``rulse.json`` no longer exists.
rules_path = common_rules_path


def _load_json_file(path, default=None):
    """Load a JSON file safely and return ``default`` when it is missing/empty."""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_split_rules(common_rules, offer_rules):
    """Rebuild the in-memory rules structure from common + offer files.

    Shape expected by ``rule_engine.apply_rules``::

        {
            "common_rule": {"alert": {...}},
            "pipe": {
                "pipe": {
                    "offer": {...}
                }
            }
        }

    Compatibility (orange) lives in ``rules_<group>.json`` and is read separately
    by ``item_builder.flag_incompatible``.
    """
    merged_rules = {}

    # common_rulse.json intentionally keeps the old top-level common_rule key.
    if isinstance(common_rules, dict):
        merged_rules.update(common_rules)

    # Add all offer rules under their group/type locations.
    for group_name, group_rules in iter_alias_items(offer_rules or {}):
        if not isinstance(group_rules, dict):
            continue
        group_bucket = merged_rules.setdefault(group_name, {})
        for type_name, type_offer_rules in iter_alias_items(group_rules):
            type_bucket = group_bucket.setdefault(type_name, {})
            type_bucket["offer"] = type_offer_rules

    return merged_rules


def _load_offer_rules_merged():
    """Merge every group's offer file into one dict.

    Each group now has its own offer file (offer_<group>.json) so data stays
    tidy and one group can be replaced independently. The legacy shared
    offer.json is still read as a fallback / for un-migrated groups.
    """
    import glob
    merged = {}
    # Legacy shared file first (lowest priority).
    legacy = _load_json_file(offer_rules_path, {})
    if isinstance(legacy, dict):
        for k, v in legacy.items():
            merged[k] = v
    # Per-group files override.
    for path in glob.glob(os.path.join(JSON_CONFIG_DIR, "offer_*.json")):
        base = os.path.basename(path)
        g = base[len("offer_"):-len(".json")].lower()
        try:
            d = _load_json_file(path, {})
        except Exception:
            d = {}
        if isinstance(d, dict):
            # A per-group file may be stored as {group:{...}} or the bare {...}.
            block = d.get(g) if g in {str(k).lower() for k in d.keys()} else d
            if isinstance(block, dict):
                merged[g] = block
    return merged


# Load rules once at import time, exactly like the previous processor.py did.
common_rules = _load_json_file(common_rules_path, {})
offer_rules = _load_offer_rules_merged()
rules = _merge_split_rules(common_rules, offer_rules)

# mtimes of the rule source files — used so disk edits apply without a restart.
_RULES_MTIME_STAMP = None
# Throttle disk glob/stat: apply_rules used to call this once per row (300×),
# each time globbing every offer_*/rules_*.json. Interval keeps disk edits
# visible within ~1s without changing any coding rules.
_RULES_CHECK_AT = 0.0
_RULES_CHECK_INTERVAL_SEC = 1.0
_RULES_PATH_LIST_CACHE = None  # (checked_at, [paths])


def _rules_source_paths():
    """Cached list of rule JSON paths (re-glob at most once per interval)."""
    import glob
    import time
    global _RULES_PATH_LIST_CACHE
    now = time.monotonic()
    cached = _RULES_PATH_LIST_CACHE
    if cached is not None and (now - cached[0]) < _RULES_CHECK_INTERVAL_SEC:
        return cached[1]
    paths = [
        common_rules_path,
        offer_rules_path,
        *glob.glob(os.path.join(JSON_CONFIG_DIR, "offer_*.json")),
        *glob.glob(os.path.join(JSON_CONFIG_DIR, "rules_*.json")),
    ]
    _RULES_PATH_LIST_CACHE = (now, paths)
    return paths


def _rules_source_mtime():
    """Newest mtime among common / offer_*.json / rules_*.json."""
    newest = 0.0
    for path in _rules_source_paths():
        try:
            if os.path.exists(path):
                newest = max(newest, os.path.getmtime(path))
        except OSError:
            pass
    return newest or None


def ensure_rules_fresh():
    """Reload in-memory rule dicts when any rule JSON on disk has changed.

    Safe to call often: mtime/glob is throttled (~1s). Reloads only when
    something actually changed. Keeps ``from .constants import rules``
    references valid by mutating the existing dicts in place.
    """
    import time
    global _RULES_MTIME_STAMP, _RULES_CHECK_AT
    now = time.monotonic()
    if (
        _RULES_MTIME_STAMP is not None
        and (now - _RULES_CHECK_AT) < _RULES_CHECK_INTERVAL_SEC
    ):
        return False
    _RULES_CHECK_AT = now
    current = _rules_source_mtime()
    if current is not None and current == _RULES_MTIME_STAMP:
        return False
    reload_rules()
    _RULES_MTIME_STAMP = current
    return True


# General data caches.
JSON_FILE_CACHE = {}
FEATURE_VALUES_CACHE = {}
CSV_FIELD_CACHE = {}
SIZE_CONFIG_CACHE = None
SIZE_DF_CACHE = {}

# Code assignment caches.
CODE_TABLE_CACHE = {}
CODE_MAPPING_CACHE = {}
ASSIGN_CODE_RESULT_CACHE = {}
CODE_NORMALIZED_CACHE = {}
CODE_INDEX_CACHE = {}
CODE_FEATURE_MAP_CACHE = {}
CODE_NORMALIZE_RE = re.compile(r"[^0-9a-zA-Z\u0600-\u06FF]")


def clear_data_caches():
    """Drop the in-memory reference-data caches.

    Called after an admin imports a code table or publishes a JSON config so the
    new data is used immediately, without restarting the server. It only clears
    caches; it never changes any coding/pricing logic.
    """
    global SIZE_CONFIG_CACHE
    for _cache in (
        JSON_FILE_CACHE, FEATURE_VALUES_CACHE, CSV_FIELD_CACHE, SIZE_DF_CACHE,
        CODE_TABLE_CACHE, CODE_MAPPING_CACHE, ASSIGN_CODE_RESULT_CACHE,
        CODE_NORMALIZED_CACHE, CODE_INDEX_CACHE, CODE_FEATURE_MAP_CACHE,
    ):
        try:
            _cache.clear()
        except Exception:
            pass
    SIZE_CONFIG_CACHE = None
    try:
        if hasattr(clear_data_caches, "_SIZE_CONFIG_MTIME"):
            delattr(clear_data_caches, "_SIZE_CONFIG_MTIME")
    except Exception:
        pass
    try:
        from .find_size import clear_find_size_cache
        clear_find_size_cache()
    except Exception:
        pass

    # Also drop mtime stamps so the next load_json_file / CSV read re-stats disk.
    try:
        from . import regex_patterns as _rp
        for _name in ("_JSON_MTIME_CACHE", "_CSV_MTIME_CACHE", "_FEATURE_CSV_MTIME_CACHE"):
            _c = getattr(_rp, _name, None)
            if isinstance(_c, dict):
                _c.clear()
    except Exception:
        pass

    # Drop the compound-feature (asign_code.json) sibling cache too.
    try:
        from . import composite_features
        composite_features.clear_cache()
    except Exception:
        pass

    # Drop table-layout cache so a disk edit is picked up.
    try:
        from . import table_layout_manager as _tlm
        _tlm._CONFIG_CACHE = None
        if hasattr(_tlm.load_table_layout_config, "_mtime"):
            _tlm.load_table_layout_config._mtime = object()
    except Exception:
        pass

    # Rebuild the in-memory rule dicts IN PLACE so every module that did
    # ``from .constants import rules`` sees fresh offer/common data after
    # an admin edits offer_<group>.json, rules, or a global JSON file. Mutating
    # the existing dicts (instead of rebinding) keeps those references valid.
    try:
        reload_rules()
        global _RULES_MTIME_STAMP, _RULES_CHECK_AT, _RULES_PATH_LIST_CACHE
        _RULES_PATH_LIST_CACHE = None
        _RULES_MTIME_STAMP = _rules_source_mtime()
        import time as _time
        _RULES_CHECK_AT = _time.monotonic()
    except Exception:
        pass
    try:
        from .normalizers import clear_normalizer_caches
        clear_normalizer_caches()
    except Exception:
        pass
    try:
        from . import text_processor as _tp
        if hasattr(_tp, "clear_vocab_caches"):
            _tp.clear_vocab_caches()
    except Exception:
        pass
    try:
        from . import feature_extractor as _fe
        if hasattr(_fe, "clear_feature_extractor_caches"):
            _fe.clear_feature_extractor_caches()
    except Exception:
        pass


def reload_rules():
    """Re-read the split rule files and refresh the module-level dicts in place.

    ``rules`` (and its ``common_rules`` / ``offer_rules`` sources) are loaded
    once at import for speed. This rebuilds them from disk and updates the SAME
    dict objects so imported references stay valid.
    """
    fresh_common = _load_json_file(common_rules_path, {})
    fresh_offer = _load_offer_rules_merged()
    fresh_rules = _merge_split_rules(fresh_common, fresh_offer)

    for target, fresh in (
        (common_rules, fresh_common),
        (offer_rules, fresh_offer),
        (rules, fresh_rules),
    ):
        if isinstance(target, dict):
            target.clear()
            if isinstance(fresh, dict):
                target.update(fresh)
