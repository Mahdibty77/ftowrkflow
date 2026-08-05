"""Small text-normalization helpers used by the processor modules.

These helpers do not know anything about Django views, Excel files, or rules.
They only keep the original text-cleaning behavior in one readable place.
"""

import re
from functools import lru_cache

_CLEAN_RE = re.compile(r'[^a-z0-9آ-ی\.]')
_PATTERN_KEY_RE = re.compile(r'^M(\d+)_([A-Z])_(.+)$')
_MARKER_BLOCK_RE = re.compile(r"([\(\[])(.*?)[\)\]]")
_WS_RE = re.compile(r"\s+")


def clean_for_group_and_features(text):
    """
    پاک‌سازی متن برای مقایسه گروه/ویژگی:
    فقط حروف انگلیسی/اعداد و حروف فارسی نگه داشته می‌شود (lowercase).
    """
    return _clean_for_group_and_features_cached(str(text) if text is not None else "")


@lru_cache(maxsize=16384)
def _clean_for_group_and_features_cached(text):
    return _CLEAN_RE.sub('', text.lower())


def preserve_original(text):
    """نگهداری متن اصلی بدون تغییر."""
    return text


def remove_first_occurrence(src, token):
    """حذف اولین رخداد token در src (غیر regex، ساده)."""
    idx = src.find(token)
    if idx == -1:
        return src
    return src[:idx] + src[idx + len(token):]


def parse_feature_pattern_key(key):
    """
    تجزیه کلید الگو مثل M1_A_Seamless
    خروجی: (Mnum:int or None, Letter:str or None, BaseName:str)
    """
    return _parse_feature_pattern_key_cached(str(key) if key is not None else "")


@lru_cache(maxsize=4096)
def _parse_feature_pattern_key_cached(key):
    m = _PATTERN_KEY_RE.match(key)
    if m:
        return int(m.group(1)), m.group(2), m.group(3)
    return None, None, key


def _prepare_pattern_list(pat_values):
    """دریافت لیست رشته‌ها از pat_values و بازگرداندن لیستی از همان‌ها (برای سازگاری)."""
    return pat_values if isinstance(pat_values, list) else [pat_values]


def parse_feature_dependency_markers(key):
    """Return (clean_key, suppress_features, require_features) for JSON keys.

    Supported dependency markers after ``^``:
    - ^(grade_material & material_class): matched value makes those alarms optional
    - ^[grade_material & material_class]: matched value forces those alarms required
    - ^[material_class](grade_material): both forms can be combined after one ``^``

    Markers are configuration-only and must not become extracted values or final text.
    """
    return _parse_feature_dependency_markers_cached(str(key or ""))


@lru_cache(maxsize=4096)
def _parse_feature_dependency_markers_cached(key_s):
    suppress = []
    require = []

    def _split_names(raw):
        return [p.strip().lower() for p in str(raw or "").split("&") if p.strip()]

    # Read marker blocks only from the marker suffix.  The first ``^`` starts
    # configuration metadata; after that, each [] block is required and each ()
    # block is optional.  This supports ^[a](b), ^(b)[a], and repeated blocks.
    marker_start = key_s.find("^")
    marker_part = key_s[marker_start + 1:] if marker_start >= 0 else ""
    if marker_part:
        for kind, raw in _MARKER_BLOCK_RE.findall(marker_part):
            if kind == "[":
                require.extend(_split_names(raw))
            else:
                suppress.extend(_split_names(raw))

    clean_key = key_s[:marker_start] if marker_start >= 0 else key_s
    clean_key = _WS_RE.sub(" ", clean_key).strip()

    # keep order while removing duplicates
    suppress = list(dict.fromkeys(suppress))
    require = list(dict.fromkeys(require))
    return clean_key, suppress, require


def clear_normalizer_caches():
    """Drop LRU caches after admin reference-data edits."""
    _clean_for_group_and_features_cached.cache_clear()
    _parse_feature_pattern_key_cached.cache_clear()
    _parse_feature_dependency_markers_cached.cache_clear()
