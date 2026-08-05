"""Final-arrange display helpers.

This module contains reusable display utilities used by Final Arranged Text and
Filled_Features.  It intentionally does not decide the custom arrangement order;
that responsibility stays in final_arrange_builder.py and final_arrange.json.

Keeping these helpers separate makes final_arrange_builder.py focused only on
JSON-driven word placement and template rendering.
"""

import re


def colored_display(value, color=None):
    """Wrap a displayed value with its current color style.

    Values are shown upper-cased for consistency with the rest of the app
    (only Latin letters change; numbers/Persian are unaffected).
    """
    color = color or "black"
    return f"<span style='color:{color}'>{str(value).upper()}</span>"


def join_filled_features(feature_items):
    """Render Filled_Features consistently for initial render and AJAX updates."""
    return "<br>".join(feature_items)


def extract_order(key):
    """Read the numeric order suffix used by the extractor, if present."""
    try:
        return int(str(key).split("_")[-1])
    except Exception:
        return 999


def is_clean_value(value):
    """Return True when a feature value should be visible."""
    return value is not None and str(value).strip() and str(value).strip().lower() != "null"


def feature_base_name(key, group_key="", type_key=""):
    """Normalize an internal feature key to the simple name used in JSON templates.

    Canonical variable naming is <feature>_<group>_<type>. The type feature
    itself is stored as <group>_type and is exposed to final_arrange.json as
    the simple feature name ``type``.
    """
    key_s = str(key or "")

    if key_s.startswith("display_"):
        return key_s[len("display_"):]

    if key_s.startswith("phisic_"):
        raw = key_s[len("phisic_"):]
        if group_key and type_key:
            suffix = f"_{group_key}_{type_key}"
            if raw.lower().endswith(suffix.lower()):
                raw = raw[: -len(suffix)]
        return raw

    base = re.sub(r"_\d+$", "", key_s)

    if group_key and base.lower() == f"{group_key}_type".lower():
        return "type"

    if group_key and type_key:
        suffix = f"_{group_key}_{type_key}"
        if base.lower().endswith(suffix.lower()):
            return base[: -len(suffix)]

    if group_key and base.lower().endswith(f"_{group_key}".lower()):
        base = base[: -len(f"_{group_key}")]
    return base


def build_visible_feature_entries(feature_vars, group_key="", type_key=""):
    """Convert raw feature variables to the visible features used in final text.

    Physical values can have both raw ``phisic_*`` keys and display keys such as
    ``display_thk(in)``. This keeps the existing behavior: final text shows only
    one complete display value, for example ``thk(in): 0.250``.
    """
    visible_features = []
    included_phisic_cores = set()

    for key, value in feature_vars.items():
        if not is_clean_value(value):
            continue

        key_s = str(key)
        if key_s.startswith("__"):
            continue
        value_s = str(value).strip()

        if key_s.startswith("display_"):
            core = key_s[len("display_"):]
            related_phisic_exists = any(str(fk).startswith(f"phisic_{core}_") for fk in feature_vars.keys())
            if related_phisic_exists:
                if core not in included_phisic_cores:
                    visible_features.append((key, value_s))
                    included_phisic_cores.add(core)
            else:
                visible_features.append((key, value_s))

        elif key_s.startswith("phisic_"):
            core = key_s.replace("phisic_", "").split("_")[0]
            if core in included_phisic_cores:
                continue

            display_key = f"display_{core}"
            if display_key in feature_vars and str(feature_vars[display_key]).strip():
                visible_features.append((display_key, str(feature_vars[display_key]).strip()))
            else:
                visible_features.append((key, f"{core}: {value_s}"))
            included_phisic_cores.add(core)

        else:
            visible_features.append((key, value_s))

    entries = []
    for index, (key, value) in enumerate(visible_features):
        if not is_clean_value(value):
            continue
        base_name = feature_base_name(key, group_key=group_key, type_key=type_key)
        is_type_feature = (
            base_name == "type"
            and group_key
            and str(key).lower() == f"{str(group_key).strip().lower()}_type"
        )
        entries.append({
            "key": key,
            "base": base_name,
            "value": value,
            # Type is a normal feature, but in default Final_Text it must be
            # placed immediately after the group prefix, not duplicated inside
            # the prefix.
            "order": (0 if is_type_feature else extract_order(key)),
            "index": index,
            "is_type_feature": is_type_feature,
        })

    return sorted(entries, key=lambda item: (item["order"], item["index"]))


def color_for_feature(entry, feature_vars, target_values_map, changed_feature_keys):
    """Return the same color that the existing final-arrange logic uses.

    Priority: a rules.json incompatibility (orange) wins over everything — a
    value that breaks a rule must be flagged even when it came from a remark
    (which would otherwise paint it blue). Then blue for changed values, then
    any other rule color, then black.
    """
    key = entry["key"]
    value = entry["value"]

    # 1) Incompatibility highlight always wins (red > orange).
    tvm_color = target_values_map.get(value)
    if tvm_color == "red":
        return "red"
    if tvm_color == "orange":
        return "orange"
    if str(key).startswith("display_"):
        core = str(key)[len("display_"):]
        phisic_key = next((pk for pk in feature_vars if str(pk).startswith(f"phisic_{core}_")), None)
        if phisic_key:
            pc = target_values_map.get(feature_vars.get(phisic_key))
            if pc == "red":
                return "red"
            if pc == "orange":
                return "orange"

    # 2) Blue when remark/revision changed a variable.
    if key in changed_feature_keys:
        return "#001aff"

    # A display_* item represents a full physical display value. Color the full
    # display if its raw phisic value was changed or targeted by rules.
    if str(key).startswith("display_"):
        core = str(key)[len("display_"):]
        if any(str(changed_key).startswith(f"phisic_{core}_") for changed_key in changed_feature_keys):
            return "#001aff"

        phisic_key = next((pk for pk in feature_vars if str(pk).startswith(f"phisic_{core}_")), None)
        if phisic_key:
            phisic_val = feature_vars.get(phisic_key)
            return target_values_map.get(value) or target_values_map.get(phisic_val) or "black"

    return target_values_map.get(value) or "black"


def is_phisic_entry(entry):
    """Return True for entries that represent a physical feature."""
    key_s = str(entry.get("key", ""))
    if key_s.startswith("phisic_"):
        return True
    if key_s.startswith("display_"):
        core = key_s[len("display_"):]
        return bool(core)
    return False


def physical_core_from_entry(entry):
    """Return the physical feature name, for example ``sch`` or ``thk(in)``."""
    key_s = str(entry.get("key", ""))
    if key_s.startswith("display_"):
        return key_s[len("display_"):]
    if key_s.startswith("phisic_"):
        return key_s[len("phisic_"):].split("_")[0]
    return str(entry.get("base", ""))


def raw_phisic_value(entry, feature_vars):
    """Find the raw physical value behind an entry."""
    key_s = str(entry.get("key", ""))
    if key_s.startswith("phisic_"):
        return str(feature_vars.get(key_s, entry.get("value", ""))).strip()

    if key_s.startswith("display_"):
        core = key_s[len("display_"):]
        raw_key = next((pk for pk in feature_vars if str(pk).startswith(f"phisic_{core}_")), None)
        if raw_key:
            return str(feature_vars.get(raw_key, entry.get("value", ""))).strip()

    return str(entry.get("value", "")).strip()


def format_phisic_alias_value(entry, feature_vars):
    """Format one physical feature for the JSON template alias ``phisic``.

    Schedule is intentionally compacted to match the requested display
    (``sch40`` instead of ``sch: 40``). Other physical values keep their label
    format so thickness/diameter remain readable.
    """
    core = physical_core_from_entry(entry)
    raw_value = raw_phisic_value(entry, feature_vars)
    if not is_clean_value(raw_value):
        return ""

    compact_cores = {"sch", "shc", "cl"}
    display_value = str(entry.get("value", "")).strip()
    label = str(core).strip()
    m = re.match(r"^([^:]+):\s*(.+)$", display_value)
    if m:
        label = m.group(1).strip()
        raw_value = m.group(2).strip() or raw_value

    if str(label).lower() in compact_cores:
        return f"{label}{raw_value}"
    return f"{label}: {raw_value}"


def build_phisic_alias_entry(entries, feature_vars, target_values_map, changed_feature_keys):
    """Build the synthetic ``phisic`` entry used by final_arrange.json."""
    physical_entries = [entry for entry in entries if is_phisic_entry(entry)]
    if not physical_entries:
        return None, set()

    html_parts = []
    consumed_bases = set()
    positions = []

    for entry in physical_entries:
        alias_value = format_phisic_alias_value(entry, feature_vars)
        if not is_clean_value(alias_value):
            continue
        color = color_for_feature(entry, feature_vars, target_values_map, changed_feature_keys)
        html_parts.append(colored_display(alias_value, color))
        consumed_bases.add(entry.get("base"))
        positions.append((entry.get("order", 999), entry.get("index", 999)))

    if not html_parts:
        return None, set()

    return {
        "key": "__phisic_alias__",
        "base": "phisic",
        "value": " ".join(format_phisic_alias_value(entry, feature_vars) for entry in physical_entries),
        "html_value": " ".join(html_parts),
        "order": min(pos[0] for pos in positions) if positions else 999,
        "index": min(pos[1] for pos in positions) if positions else 999,
        "consumes": consumed_bases,
    }, consumed_bases
