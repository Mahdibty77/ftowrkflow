"""JSON-driven Final Arranged Text builder.

This module is intentionally focused on the configurable arrangement rules from:

    itemcoder/resources/json/final_arrange.json

Example config::

    {
        "seprator": [","],
        "pipe": {
            "arrange": ["material / (grade_material)"]
        }
    }

Responsibilities kept here:
- Load final_arrange.json.
- Read the default ``seprator`` between final text parts.
- Render custom arrange templates such as ``material / (grade_material)``.
- Insert custom template output at the first original position of its features.

General display helpers, color wrapping, physical-value helpers and
Filled_Features formatting live in final_feature_display.py.
"""

import html
import json
import os
import re

from .resource_paths import json_path
from .final_feature_display import (
    build_phisic_alias_entry,
    build_visible_feature_entries,
    format_phisic_alias_value,
    is_phisic_entry,
    color_for_feature,
    colored_display,
    is_clean_value,
    join_filled_features,
)


DEFAULT_FINAL_ARRANGE_CONFIG = {
    "seprator": [" , "],
}


def _config_path():
    """Return the JSON config path inside itemcoder/resources/json."""
    return json_path("final_arrange.json")


def load_final_arrange_config():
    """Load final arrange settings; fall back safely if the file is missing."""
    from .regex_patterns import load_json_file
    path = _config_path()
    try:
        data = load_json_file(path)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(DEFAULT_FINAL_ARRANGE_CONFIG)


def _extract_template_feature_names(template, candidate_names):
    """Find feature-name placeholders referenced by a custom arrange template."""
    names = []
    for name in sorted(candidate_names, key=len, reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if re.search(pattern, template):
            names.append(name)
    return names


def _cleanup_rendered_template(rendered):
    """Remove empty wrappers and dangling custom separators after optional values."""
    text = rendered
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\(\s*\)", "", text)
        text = re.sub(r"\[\s*\]", "", text)
        text = re.sub(r"\{\s*\}", "", text)
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s*([/_-])\s*(?=\)|\]|\}|$)", "", text)
        text = re.sub(r"(^|[\(\[\{])\s*([/_-])\s*", r"\1", text)
    return text.strip()


def _render_custom_template(template, entries_by_base, feature_vars, target_values_map, changed_feature_keys):
    """Render one JSON arrange template and return rendered HTML plus used names.

    Every identifier token in the template is treated as a feature placeholder.
    Present values are substituted; missing/empty features become "" so names
    like ``grade_material`` never leak into Final Arranged Text.
    """
    present_names = set(entries_by_base.keys())
    # Arrange templates only contain feature placeholders + punctuation, so any
    # identifier in the template is a candidate — even when that feature was
    # not extracted (otherwise the raw name would remain visible).
    template_ids = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(template or "")))
    candidate_names = present_names | template_ids
    used_names = _extract_template_feature_names(template, candidate_names)

    if not used_names:
        return "", set(), None

    # Escape literal template characters such as < and > so they are shown as
    # text in the browser while generated <span> highlights remain valid HTML.
    rendered = html.escape(template, quote=False)
    used_positions = []
    any_value = False

    for name in sorted(used_names, key=len, reverse=True):
        entry = entries_by_base.get(name)
        if entry and is_clean_value(entry["value"]):
            if entry.get("html_value") is not None:
                replacement = entry.get("html_value")
            else:
                color = color_for_feature(entry, feature_vars, target_values_map, changed_feature_keys)
                replacement = colored_display(entry["value"], color)
            used_positions.append((entry["order"], entry.get("index", 999)))
            any_value = True
        else:
            # Missing optional feature (e.g. grade_material): omit entirely.
            replacement = ""

        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        rendered = re.sub(pattern, replacement, rendered)

    rendered = _cleanup_rendered_template(rendered)
    if not rendered or not any_value:
        return "", set(), None

    return rendered, set(used_names), min(used_positions) if used_positions else (999, 999)


def _custom_arranged_items(group_key, entries, feature_vars, target_values_map, changed_feature_keys, config):
    """Build custom arranged HTML items from final_arrange.json for one group."""
    group_config = config.get(group_key, {}) if isinstance(config, dict) else {}
    templates = group_config.get("arrange", []) if isinstance(group_config, dict) else []
    if isinstance(templates, str):
        templates = [templates]
    if not isinstance(templates, list):
        templates = []

    entries_by_base = {entry["base"]: entry for entry in entries}

    # JSON templates can use ``phisic`` as one combined placeholder for all
    # physical values (sch/thk/diameter/etc.).
    phisic_alias_entry, phisic_consumed_bases = build_phisic_alias_entry(
        entries,
        feature_vars,
        target_values_map,
        changed_feature_keys,
    )
    if phisic_alias_entry:
        entries_by_base["phisic"] = phisic_alias_entry

    custom_items = []
    consumed_names = set()

    for template in templates:
        if not isinstance(template, str) or not template.strip():
            continue

        rendered, used_names, order = _render_custom_template(
            template,
            entries_by_base,
            feature_vars,
            target_values_map,
            changed_feature_keys,
        )
        if rendered:
            custom_items.append({"position": order, "html": rendered})
            consumed_names.update(used_names)
            if "phisic" in used_names:
                consumed_names.update(phisic_consumed_bases)

    return custom_items, consumed_names


def _get_seprator(config):
    """Return the default separator configured in final_arrange.json.

    The project intentionally uses the key name ``seprator`` because this is the
    requested JSON API.  ``between`` is accepted only as a safe legacy fallback
    for old saved config files.
    """
    if not isinstance(config, dict):
        return " , "

    value = config.get("seprator", config.get("between", [" , "]))
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return " , "


def build_final_arrange_and_features(group_key, type_key, feature_vars, original_feature_vars=None,
                                     target_values_map=None, changed_feature_keys=None):
    """Build colored Final_Text and Filled_Features for initial and live rendering.

    Extraction, rules and highlighting decisions are untouched. This function
    only controls display order, custom display templates, separator and final
    HTML assembly for already-extracted feature values.
    """
    original_feature_vars = original_feature_vars or {}
    target_values_map = target_values_map or {}
    changed_feature_keys = set(changed_feature_keys or [])
    config = load_final_arrange_config()
    seprator = _get_seprator(config)

    entries = build_visible_feature_entries(feature_vars, group_key=group_key, type_key=type_key)
    custom_items, consumed_names = _custom_arranged_items(
        group_key,
        entries,
        feature_vars,
        target_values_map,
        changed_feature_keys,
        config,
    )

    default_items = []
    group_l = str(group_key or "").strip().lower()
    for entry in entries:
        # Type is a normal feature, but if the detected type text is identical
        # to the group prefix (pipe_type = pipe), showing it would create
        # duplicate text like "pipe ,pipe".  Keep the variable for assign_code
        # and alarms, but suppress only this duplicate display case.
        if entry.get("is_type_feature") and str(entry.get("value", "")).strip().lower() == group_l:
            continue
        # If a feature is used in a JSON custom arrange expression, do not show
        # it again in its old standalone place.
        if entry["base"] in consumed_names:
            continue
        color = color_for_feature(entry, feature_vars, target_values_map, changed_feature_keys)
        display_value = format_phisic_alias_value(entry, feature_vars) if is_phisic_entry(entry) else entry["value"]
        default_items.append({
            "position": (entry["order"], entry.get("index", 999)),
            "html": colored_display(display_value, color),
        })

    ordered_items = sorted(custom_items + default_items, key=lambda item: item["position"])
    ordered_values = [item["html"] for item in ordered_items if str(item.get("html", "")).strip()]

    # Prefix must be the group only. Type is handled as a normal feature
    # (<group>_type) so it can be arranged/colored like other features and is
    # never duplicated as both prefix and feature. Always UPPERCASE like the
    # rest of FTCO DISCRIPTION tokens.
    prefix = str(group_key or "").strip().upper()
    if prefix and ordered_values:
        final_arranged_text = prefix + seprator + seprator.join(ordered_values)
    elif prefix:
        final_arranged_text = prefix
    else:
        final_arranged_text = seprator.join(ordered_values)

    # Filled_Features is intentionally not customized by final_arrange.json.
    # It remains a diagnostic view of raw variables and their colors.
    filled_features_summary = join_filled_features(
        [
            f"{key} = {colored_display(value, ('orange' if target_values_map.get(value) == 'orange' else ('#001aff' if key in changed_feature_keys else (target_values_map.get(value) or 'black'))))}"
            for key, value in feature_vars.items()
            if is_clean_value(value) and not str(key).startswith("display_") and not str(key).startswith("__")
        ]
    )

    return final_arranged_text, filled_features_summary


__all__ = [
    "build_final_arrange_and_features",
    "load_final_arrange_config",
]
