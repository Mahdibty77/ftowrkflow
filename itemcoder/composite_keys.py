"""Reading JSON keys that carry several spellings, separated by ``||``.

data.json and its siblings let one key stand for several names, e.g.
``"Equal Tee 90°||equaltee90||tee90"``. Every lookup into those files must go
through ``get_by_alias`` rather than ``dict.get``, or a perfectly valid spelling
silently misses. feature_extractor, rule_engine, text_processor, constants and
composite_features all use these helpers for exactly that reason.

``get_by_alias`` also implements the project-wide ``all_in`` wildcard: a block
keyed ``all_in`` applies to every type. It is checked LAST, after exact and alias
matches, so a specific mapping always beats the wildcard.
"""

ALIAS_SEPARATOR = "||"


def split_alias_key(key):
    return [part.strip() for part in str(key).split(ALIAS_SEPARATOR) if part.strip()]


def alias_key_matches(key, value):
    value_s = str(value or "").strip()
    if not value_s:
        return False
    return any(part == value_s or part.lower() == value_s.lower() for part in split_alias_key(key))


def get_by_alias(mapping, value, default=None):
    if not isinstance(mapping, dict):
        return default
    if value in mapping:
        return mapping[value]
    value_s = str(value or "").strip()
    for key, val in mapping.items():
        if alias_key_matches(key, value_s):
            return val

    # ``all_in`` is a project-wide wildcard for type-dependent JSON blocks.
    # It is intentionally checked only after exact and || alias matches, so
    # old specific mappings keep priority and the existing logic remains intact.
    if "all_in" in mapping:
        return mapping["all_in"]
    for key, val in mapping.items():
        if str(key).strip().lower() == "all_in":
            return val
    return default


def iter_alias_items(mapping):
    if not isinstance(mapping, dict):
        return
    for key, val in mapping.items():
        aliases = split_alias_key(key)
        if not aliases:
            aliases = [str(key)]
        for alias in aliases:
            yield alias, val
