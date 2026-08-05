"""Helpers for JSON keys that may contain multiple aliases separated by ||."""

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


def expand_alias_dict(mapping):
    expanded = {}
    if not isinstance(mapping, dict):
        return expanded
    for alias, val in iter_alias_items(mapping):
        expanded[alias] = val
    return expanded
