"""Small in-process caches for fast upload/live row processing.

These caches do not change business rules or output formats. They only keep
already-computed row/static data in RAM so live edits do not repeat expensive
work when the source row itself has not changed.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

ROW_BASE_FEATURE_CACHE: Dict[int, Dict[str, Any]] = {}


def normalize_cache_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def clear_row_base_cache() -> None:
    ROW_BASE_FEATURE_CACHE.clear()


def store_row_base_cache(row_index: int, *, original_text: Any, clean_size: Any, group: str, type_: str, features_container: Dict, feature_vars_raw: Dict) -> None:
    try:
        idx = int(row_index)
    except Exception:
        return
    ROW_BASE_FEATURE_CACHE[idx] = {
        "original_text": normalize_cache_text(original_text),
        "clean_size": normalize_cache_text(clean_size),
        "group": normalize_cache_text(group),
        "type": normalize_cache_text(type_),
        "features_container": dict(features_container or {}),
        "feature_vars_raw": dict(feature_vars_raw or {}),
    }


def get_row_base_cache(row_index: int, *, original_text: Any, clean_size: Any) -> Optional[Dict[str, Any]]:
    try:
        idx = int(row_index)
    except Exception:
        return None
    cached = ROW_BASE_FEATURE_CACHE.get(idx)
    if not cached:
        return None
    if cached.get("original_text") != normalize_cache_text(original_text):
        return None
    # Size can be displayed as 2" while backend clean size is 2. Keep the cache
    # tolerant: only reject when both sides are non-empty and different.
    req_size = normalize_cache_text(clean_size)
    cached_size = normalize_cache_text(cached.get("clean_size"))
    if req_size and cached_size and req_size != cached_size:
        return None
    return cached
