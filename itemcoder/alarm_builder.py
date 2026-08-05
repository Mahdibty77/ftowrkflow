"""Alarm/missing-feature calculation.

This module only decides which expected features are still missing after feature
extraction and rule application.
"""

import re


def build_alarms(expected_feature_keys, feature_vars, type_key, group_key):
    """
    تولید لیست featureهایی که مقدار ندارند.

    Supported JSON-only dependency markers are stored by the extractor as hidden
    metadata, so alarm calculation stays fast and does not rescan data.json:
    - ^(a & b): suppress missing alarms for a/b when the marked value matched.
    - ^[a & b]: force missing alarms for a/b even when their own feature has null.
    """
    alarms = []
    group_l = str(group_key or "").strip().lower()
    type_l = str(type_key or "").strip().lower()

    feature_vars = feature_vars or {}
    optional_vars = set(str(x).strip().lower() for x in feature_vars.get("__alarm_optional__", []) if str(x).strip())
    required_vars = set(str(x).strip().lower() for x in feature_vars.get("__alarm_required__", []) if str(x).strip())

    filled_keys = set()
    null_keys = set()
    for k, v in feature_vars.items():
        k_s = str(k).strip().lower()
        if k_s.startswith("__") or k_s.startswith("display_"):
            continue
        v_s = str(v).strip()
        if v_s and v_s.lower() != "null":
            filled_keys.add(k_s)
        elif v_s.lower() == "null":
            null_keys.add(k_s)

    def _var_name(base):
        return f"{base}_{group_l}_{type_l}" if type_key else f"{base}_{group_l}_"

    def _has_phisic_value(base):
        if base == "phisic":
            return any(k.startswith("phisic_") and k.endswith(f"_{group_l}_{type_l}") for k in filled_keys)
        expected = _var_name(base)
        return expected in filled_keys or any(k.startswith(f"{base}_") and k.endswith(f"_{group_l}_{type_l}") for k in filled_keys)

    def _has_phisic_null(base):
        if base == "phisic":
            return any(k.startswith("phisic_") and k.endswith(f"_{group_l}_{type_l}") for k in null_keys)
        expected = _var_name(base)
        return expected in null_keys or any(k.startswith(f"{base}_") and k.endswith(f"_{group_l}_{type_l}") for k in null_keys)

    for feat in expected_feature_keys:
        if feat.startswith('csv_'):
            base = feat.replace('csv_', '')
        elif re.search(r'_\d+$', feat):
            base = re.sub(r'_\d+$', '', feat)
        else:
            base = feat
        base = str(base).strip().lower()
        var_name = _var_name(base).lower()

        if var_name in optional_vars:
            continue

        if base.startswith('phisic'):
            has_value = _has_phisic_value(base)
            has_null = _has_phisic_null(base)
            if var_name in required_vars:
                if not has_value:
                    alarms.append(base)
            elif not has_value and not has_null:
                alarms.append(base)
        else:
            has_value = var_name in filled_keys
            has_null = var_name in null_keys
            if var_name in required_vars:
                if not has_value:
                    alarms.append(base)
            elif not has_value and not has_null:
                alarms.append(base)

    return alarms
