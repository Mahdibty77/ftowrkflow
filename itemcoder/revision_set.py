"""The ``set(var:value)`` escape hatch an operator can type in Revision.

Normally a feature value has to be recognised in the text. ``set(coating:none)``
lets the operator state one outright, for the cases where the description simply
does not say. text_processor drives all three functions in order, once per row:
``parse_set_commands`` reads them, ``strip_set_commands`` removes them from the
text (so ``set`` and the variable name are not themselves matched as features),
and ``apply_set_commands`` writes the values after the alarms are built.

Only features whose data.json definition includes an ``m2_A_null`` (or similar
null-variant) pattern can be targeted — a feature that has no legitimate "not
applicable" state must not be silenced this way. Applied commands set the feature
variable directly and suppress its alarm entry, and the keys they changed are
passed to the vocabulary scrub as ``keep_keys`` so a deliberate override is never
dropped for not being a data.json word.
"""

import re

SET_PATTERN = re.compile(
    r"set\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^)]*)\s*\)",
    re.IGNORECASE,
)


def parse_set_commands(text):
    """Return ordered ``(var_base, value)`` pairs from revision text."""
    commands = []
    for match in SET_PATTERN.finditer(text or ""):
        var_base = match.group(1).strip().lower()
        value = match.group(2).strip()
        commands.append((var_base, value))
    return commands


def strip_set_commands(text):
    """Remove ``set(...)`` tokens so they are not parsed as ordinary features."""
    cleaned = SET_PATTERN.sub(" ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def feature_has_null_variant(features_container, var_base):
    """True when *var_base* maps to a feature with a null-variant pattern."""
    if not isinstance(features_container, dict):
        return False
    var_base_l = str(var_base).strip().lower()
    for feat_key, feat_val in features_container.items():
        if not isinstance(feat_val, dict):
            continue
        key_base = (
            re.sub(r"_\d+$", "", feat_key)
            if re.search(r"_\d+$", feat_key)
            else feat_key
        )
        if str(key_base).strip().lower() != var_base_l:
            continue
        for pat_key in feat_val.keys():
            if "null" in str(pat_key).lower():
                return True
    return False


def apply_set_commands(feature_vars, commands, features_container, group_key, type_key, alarms):
    """Apply eligible ``set()`` commands; return the variable keys that changed."""
    if not commands:
        return set()

    group_l = str(group_key or "").strip().lower()
    type_l = str(type_key or "").strip().lower()
    changed = set()
    suppressed_bases = set()

    for var_base, value in commands:
        if not feature_has_null_variant(features_container, var_base):
            continue
        var_key = f"{var_base}_{group_l}_{type_l}"
        feature_vars[var_key] = value
        changed.add(var_key)
        suppressed_bases.add(var_base.lower())

    if suppressed_bases and isinstance(alarms, list):
        alarms[:] = [
            a for a in alarms
            if str(a).strip().lower() not in suppressed_bases
        ]

    return changed
