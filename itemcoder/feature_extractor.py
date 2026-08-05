"""Feature extraction logic.

This module finds the item group/type and extracts all feature variables from the
original text. The code is separated from views and Excel handling so it can be
maintained and tested independently.
"""

import json
import os
import re

import pandas as pd
from django.conf import settings

from .resource_paths import json_path, resolve_resource_path
from .composite_keys import alias_key_matches, get_by_alias, split_alias_key

from .constants import SIZE_DF_CACHE
from . import constants as _processor_constants
from .normalizers import (
    _prepare_pattern_list,
    clean_for_group_and_features,
    parse_feature_pattern_key,
    parse_feature_dependency_markers,
    preserve_original,
    remove_first_occurrence,
)
from .regex_patterns import (
    load_feature_values,
    load_json_file,
    parse_csv_for_field,
    search_special_feature_in_original,
)

# Alias-set / short-token regex caches (same outputs; avoid rebuild per row).
_ALIAS_CLEANS_CACHE = {}
_SHORT_ALIAS_RE_CACHE = {}


def clear_feature_extractor_caches():
    _ALIAS_CLEANS_CACHE.clear()
    _SHORT_ALIAS_RE_CACHE.clear()
    try:
        from .find_size import clear_find_size_cache
        clear_find_size_cache()
    except Exception:
        pass


def confind_size(group, type, row_size):
    """Resolve a raw size cell via the group's ``find_size_<group>.csv``.

    *type* is accepted for call-site compatibility but unused. When the group
    has no CSV, the original size is returned unchanged (no equivalent).
    """
    from .find_size import resolve_size

    try:
        return resolve_size(row_size, group=group)
    except Exception:
        raw = "" if row_size is None else str(row_size)
        return {"clean_size": "null", "display_size": raw}


def find_group(clean_text, json_dict):
    """
    تعیین گروه:
    1) اول کلیدهای G_ را چک می‌کند
    2) fallback: برای گروه‌هایی که type دارند، الگوهای type را بررسی می‌کند
    خروجی: (group_key, group_label) یا (None, '')
    """
    group_section = json_dict.get('group', {})

    # 1. بررسی G_ keys — clean patterns the same way as clean_text so
    # multi-word aliases (e.g. "pressure relief") match after space stripping.
    for key, patterns in group_section.items():
        if not key.startswith('G_'):
            continue
        group_name = key[2:]
        for pat in patterns:
            pat_clean = clean_for_group_and_features(pat) if pat else ""
            if pat_clean and pat_clean in clean_text:
                return group_name, group_name

    # 2. fallback از روی الگوهای type برای گروه‌های مشخص
    # Must clean type aliases ("Slip on" → "slipon") like find_type does;
    # otherwise group is never found from a type-only phrase and Type stays
    # stuck in Alarm while the token still appears in FTCO DESCRIPTION.
    for group_name, group_val in group_section.items():
        if not isinstance(group_val, dict):
            continue
        if group_name not in ['pipe','fitting','flange','gasket','valve']:
            continue
        type_dict = group_val.get('type', {})
        for _type_name, tpatterns in type_dict.items():
            for pat in load_feature_values(tpatterns):
                pat_clean = clean_for_group_and_features(str(pat or ""))
                if pat_clean and pat_clean in clean_text:
                    return group_name, group_name

    return None, ''


def _clean_type_key(type_key):
    """Return the visible/canonical type name from a JSON type key.

    Type keys may now use the same JSON metadata style as features, e.g.
    ``M1_A_Elbow ^[degree]`` or ``Elbow ^[degree]``.  The marker and M/letter
    prefix are configuration only and must never appear in Final_Text or
    variables.
    """
    clean_key, _suppress_deps, _require_deps = parse_feature_dependency_markers(type_key)
    _mnum, _letter, base_name = parse_feature_pattern_key(clean_key)
    return str(base_name or clean_key).strip()


def _type_sort_key(type_key, patterns):
    clean_key, _suppress_deps, _require_deps = parse_feature_dependency_markers(type_key)
    mnum, letter, _base_name = parse_feature_pattern_key(clean_key)
    vals = load_feature_values(patterns)
    max_len = max((len(str(v)) for v in vals), default=0)
    return (mnum or 9999, letter or 'Z', -max_len)


def find_type(clean_text, group_dict):
    """
    پیدا کردن type از دیکشنری گروه.
    برمی‌گرداند: (type_key, matched_token, cleaned_text_after_removal)

    Type now supports feature-like JSON syntax: M priority prefixes and
    dependency markers ^() / ^[] are parsed, but are not included in the
    returned type value.
    """
    types = group_dict.get('type', {}) if group_dict else {}
    if not isinstance(types, dict):
        return None, '', clean_text

    # Precompute sort keys once — sorted() would otherwise call load_feature_values
    # O(n log n) times via _type_sort_key.
    type_items = list(types.items())
    sort_keys = {k: _type_sort_key(k, v) for k, v in type_items}
    for type_key, patterns in sorted(type_items, key=lambda kv: sort_keys[kv[0]]):
        visible_type = _clean_type_key(type_key)
        aliases = split_alias_key(visible_type)
        values = load_feature_values(patterns)
        for pat in sorted(values, key=lambda v: -len(str(v))):
            pat_s = str(pat or '').strip()
            if not pat_s:
                continue
            pat_clean = clean_for_group_and_features(pat_s)
            # Literal substring (same as re.search(re.escape(...), ...)).
            if pat_clean and pat_clean in clean_text:
                new_clean = remove_first_occurrence(clean_text, pat_clean)
                matched_alias = next(
                    (alias.strip() for alias in aliases if clean_for_group_and_features(alias) == pat_clean or clean_for_group_and_features(alias) in clean_text),
                    visible_type,
                )
                return matched_alias, pat_clean, new_clean
    return None, '', clean_text


def apply_type_dependency_metadata(feature_vars, group_dict, group_key, type_key):
    """Add alarm metadata activated by the matched type key.

    Example JSON: ``Elbow ^[degree]`` means when type is Elbow,
    ``degree_<group>_<type>`` is required even if degree itself has null.
    ``Elbow ^(degree)`` makes it optional.  Both can be combined.
    """
    feature_vars = dict(feature_vars or {})
    types = group_dict.get('type', {}) if isinstance(group_dict, dict) else {}
    if not isinstance(types, dict) or not group_key or not type_key:
        return feature_vars

    group_l = str(group_key).strip().lower()
    type_l = str(type_key).strip().lower()
    optional = set(str(x).strip().lower() for x in feature_vars.get('__alarm_optional__', []) if str(x).strip())
    required = set(str(x).strip().lower() for x in feature_vars.get('__alarm_required__', []) if str(x).strip())

    def _dep_key(name):
        return f"{str(name).strip().lower()}_{group_l}_{type_l}"

    for raw_type_key in types.keys():
        clean_type = _clean_type_key(raw_type_key)
        if not alias_key_matches(clean_type, type_key) and str(clean_type).strip().lower() != type_l:
            continue
        _clean_key, suppress_deps, require_deps = parse_feature_dependency_markers(raw_type_key)
        for dep in suppress_deps:
            optional.add(_dep_key(dep))
        for dep in require_deps:
            required.add(_dep_key(dep))
        break

    if optional:
        feature_vars['__alarm_optional__'] = sorted(optional)
    else:
        feature_vars.pop('__alarm_optional__', None)
    if required:
        feature_vars['__alarm_required__'] = sorted(required)
    else:
        feature_vars.pop('__alarm_required__', None)
    return feature_vars


def find_phisic_feature(original_text, clean_text, feature_dict, group_key, type_key, phisic_base="phisic"):
    """
    استخراج ویژگی‌های فیزیکی با رعایت:
    1. ترتیب M (کوچک به بزرگ)
    2. ترتیب Letter به صورت الفبا
    3. طول مقادیر (بلندترها اول)
    4. متغیر diameter با جفت عدد و prefix یا suffix مستقل
    """
    results_display = {}
    results_var = {}
    rest = clean_text

    phisic_text = re.sub(r'[^a-z0-9آ-ی\.\(\),]', '', original_text.lower(), flags=re.UNICODE)

    # --- مرتب‌سازی بر اساس M و Letter ---
    parsed = []
    null_present = False
    for pat_key, values in feature_dict.items():
        clean_pat_key, _suppress_deps, _require_deps = parse_feature_dependency_markers(pat_key)
        if "null" in str(clean_pat_key).lower():
            null_present = True
        m = re.match(r'^M(\d+)_([A-Z])_', clean_pat_key)
        if m:
            Mnum = int(m.group(1))
            Letter = m.group(2)
        else:
            Mnum, Letter = 9999, 'Z'
        parsed.append((Mnum, Letter, clean_pat_key, values))
    parsed.sort(key=lambda x: (x[0], x[1]))  # Mnum asc, Letter asc

    current_Mnum = None
    found_M = False

    for Mnum, Letter, pat_key, vals in parsed:
        if found_M and Mnum != current_Mnum:
            break
        current_Mnum = Mnum

        rem = re.sub(r'^M\d+_[A-Z]_?', '', pat_key, count=1)
        main_match = re.search(r'-(.+?)-', rem)
        if main_match:
            main_key = main_match.group(1).strip().lower()
        else:
            main_key = rem.split('_')[-1].strip().lower()

        prefix_section = rem.split('-', 1)[0] if '-' in rem else ''
        suffix_section = ''
        if rem.count('-') >= 2:
            suffix_section = rem.split('-', 2)[2]

        prefixes = [p for p in prefix_section.split('&') if p not in ['', '-']]
        suffixes = [s for s in (suffix_section.split('&') if suffix_section else []) if s not in ['', '-']]
        if not prefixes:
            prefixes = [main_key]

        vals = load_feature_values(vals) 

        vals = sorted(vals, key=lambda x: len(str(x)), reverse=True)

        collected_values_var = []
        collected_values_display = []

        for raw_v in vals:
            v_clean = str(raw_v).strip()
            if not v_clean:
                continue

            # -------- DIAMETER --------
            if main_key.startswith("diameter") :
                nums = re.findall(r'[0-9\.]+', v_clean)
                if len(nums) < 2:
                    continue
                val1, val2 = nums[:2]

                found_val1 = found_val2 = False

                # بررسی val1 با یک prefix یا یک suffix
                for p in prefixes:
                    literal = p.lower() + val1
                    if literal in phisic_text:
                        found_val1 = True
                        phisic_text = phisic_text.replace(literal, '', 1)
                        break
                if not found_val1:
                    for s in suffixes:
                        literal = val1 + s.lower()
                        if literal in phisic_text:
                            found_val1 = True
                            phisic_text = phisic_text.replace(literal, '', 1)
                            break

                # بررسی val2 با یک prefix یا یک suffix
                for p in prefixes:
                    literal = p.lower() + val2
                    if literal in phisic_text:
                        found_val2 = True
                        phisic_text = phisic_text.replace(literal, '', 1)
                        break
                if not found_val2:
                    for s in suffixes:
                        literal = val2 + s.lower()
                        if literal in phisic_text:
                            found_val2 = True
                            phisic_text = phisic_text.replace(literal, '', 1)
                            break

                # fallback بدون prefix/suffix فقط اگر داخل پرانتز باشد
                in_paren = v_clean.startswith('(') and v_clean.endswith(')')
                if in_paren:
                    if not found_val1 and val1 in phisic_text:
                        found_val1 = True
                        phisic_text = phisic_text.replace(val1, '', 1)
                    if not found_val2 and val2 in phisic_text:
                        found_val2 = True
                        phisic_text = phisic_text.replace(val2, '', 1)

                if found_val1 and found_val2:
                    display_text = f"OD: {val1} - ID: {val2}"
                    collected_values_var.append(f"({display_text})")
                    collected_values_display.append(display_text)
                    found_M = True
                    break

            else:
                # -------- سایر featureها --------

                # بررسی علامت / برای اجازه جستجوی به تنهایی
                allow_alone_search = v_clean.startswith("/")
                # Same alphabet as ``phisic_text`` above so ``10 × 10`` (CSV)
                # becomes ``1010`` and matches text cleaned from ``sch10 × 10``
                # → ``sch1010``. Keeping ``×`` in the pattern used to make the
                # long schedule never match, so only the short ``10`` won.
                raw_v_clean = re.sub(
                    r"[^a-z0-9آ-ی\.\(\),]",
                    "",
                    v_clean.lstrip("/").lower(),
                    flags=re.UNICODE,
                )

                patterns = []

                # ترکیب با prefix
                for p in prefixes:
                    if p:
                        patterns.append(p.lower() + raw_v_clean)
                # ترکیب با suffix
                for s in suffixes:
                    if s:
                        patterns.append(raw_v_clean + s.lower())

                # اگر علامت / بود، خود مقدار هم به تنهایی جستجو شود
                if allow_alone_search:
                    patterns.append(raw_v_clean)

                patterns = list(dict.fromkeys(patterns))

                matched = False
                for pat in patterns:
                    if pat in phisic_text:
                        # اگر علامت / داشت، فقط مقدار واقعی بدون / ذخیره شود
                        store_val = raw_v_clean if allow_alone_search else raw_v
                        collected_values_var.append(store_val)
                        collected_values_display.append(store_val)
                        phisic_text = phisic_text.replace(pat, "", 1)
                        matched = True
                        found_M = True
                        break
                if matched:
                    break

        if collected_values_var:
            display_val = ' '.join([f"{main_key}: {v}" for v in collected_values_display])
            results_display[main_key] = display_val
            value_text = ' '.join(str(v) for v in collected_values_var).strip()

            # Backward compatible behavior:
            # - Old JSON key like phisic_8 still creates phisic_<detected-name>_<type>_<group>.
            # - New JSON keys like phisic_sch_8 / phisic_class_9 create a variable
            #   with the same base name, so alarms and rules can refer to phisic_sch
            #   or phisic_class instead of the generic phisic bucket.
            if phisic_base and phisic_base != "phisic":
                phisic_base_l = str(phisic_base).strip().lower()
                results_var[f"{phisic_base_l}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"] = value_text
                display_core = phisic_base_l[len("phisic_"):] if phisic_base_l.startswith("phisic_") else phisic_base_l
                results_var[f"display_{display_core}"] = f"{main_key}: {value_text}"
            else:
                main_key_l = str(main_key).strip().lower()
                results_var[f"phisic_{main_key_l}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"] = value_text
                results_var[f"display_{main_key_l}"] = f"{main_key}: {value_text}"

    if not results_var and null_present:
        base = str(phisic_base or "phisic").strip().lower()
        if base != "phisic":
            results_var[f"{base}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"] = "null"
        else:
            results_var[f"phisic_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"] = "null"

    return results_display, results_var, rest



def refresh_alarm_dependency_metadata(feature_vars, feature_group_dict, group_key, type_key):
    """Rebuild JSON marker metadata from the final matched values.

    This keeps ^() / ^[] behavior correct after Remark/Revision changes a value.
    Example: the original row matched ``ASTM A105 ^(grade_material)`` and then
    Remark changes material to ASTM A350. The old optional marker must be removed
    so grade_material can alarm again.
    """
    feature_vars = dict(feature_vars or {})
    feature_vars.pop("__alarm_optional__", None)
    feature_vars.pop("__alarm_required__", None)

    if not isinstance(feature_group_dict, dict):
        return feature_vars

    group_l = str(group_key or "").strip().lower()
    type_l = str(type_key or "").strip().lower()
    optional = set()
    required = set()

    def _norm(v):
        return re.sub(r"\s+", " ", str(v or "").strip()).lower()

    def _dep_key(name):
        return f"{str(name).strip().lower()}_{group_l}_{type_l}"

    for feature_key, feature_val in feature_group_dict.items():
        if not isinstance(feature_val, dict):
            continue
        if str(feature_key).startswith("phisic"):
            # Physical dependency markers are still parsed by the extractor when
            # matched.  Avoid rescanning CSV-backed physical features here.
            continue

        var_base = re.sub(r'_\d+$', '', str(feature_key)) if re.search(r'_\d+$', str(feature_key)) else str(feature_key)
        var_name = f"{var_base.strip().lower()}_{group_l}_{type_l}"
        current_value = _norm(feature_vars.get(var_name, ""))
        if not current_value or current_value == "null":
            continue

        for pat_key in feature_val.keys():
            clean_pat_key, suppress_deps, require_deps = parse_feature_dependency_markers(pat_key)
            if not suppress_deps and not require_deps:
                continue
            _mnum, _letter, base_name = parse_feature_pattern_key(clean_pat_key)
            if _norm(base_name) != current_value:
                continue
            for dep in suppress_deps:
                optional.add(_dep_key(dep))
            for dep in require_deps:
                required.add(_dep_key(dep))

    if optional:
        feature_vars["__alarm_optional__"] = sorted(optional)
    if required:
        feature_vars["__alarm_required__"] = sorted(required)
    return feature_vars

def _collect_feature_alias_cleans(feature_group_dict, cache_key=None):
    """All cleaned aliases/base-names for longest-match guarding across features."""
    if cache_key is not None:
        hit = _ALIAS_CLEANS_CACHE.get(cache_key)
        if hit is not None:
            return set(hit)
    aliases = set()
    if not isinstance(feature_group_dict, dict):
        return aliases
    for feature_key, feature_val in feature_group_dict.items():
        if str(feature_key).startswith("phisic") or not isinstance(feature_val, dict):
            continue
        for pat_key, pat_values in feature_val.items():
            clean_pat_key, _suppress, _require = parse_feature_dependency_markers(pat_key)
            if "null" in str(clean_pat_key).lower():
                continue
            _mnum, _letter, base_name = parse_feature_pattern_key(clean_pat_key)
            if base_name:
                bc = clean_for_group_and_features(str(base_name))
                if bc:
                    aliases.add(bc)
            for raw_val in load_feature_values(pat_values):
                tc = clean_for_group_and_features(str(raw_val or ""))
                if tc:
                    aliases.add(tc)
    if cache_key is not None:
        _ALIAS_CLEANS_CACHE[cache_key] = frozenset(aliases)
    return aliases


def _find_unblocked_alias_index(token_clean, rest_clean, all_aliases):
    """Index of ``token_clean`` in ``rest_clean`` not covered by a longer alias.

    Example: ``class1`` must not match inside ``class150`` when ``class150`` is
    also a known alias — otherwise Class 1 steals Class 150.

    Also: ``globe`` must not match inside ``forgedglobe`` when Forged Globe is a
    known feature alias (Revision/EA group picks on Globe Valve rows).
    """
    if not token_clean or not rest_clean:
        return -1
    start = 0
    while True:
        idx = rest_clean.find(token_clean, start)
        if idx < 0:
            return -1
        blocked = False
        end = idx + len(token_clean)
        for other in all_aliases:
            if len(other) <= len(token_clean):
                continue
            # Longer alias begins at the same index (class1 ⊂ class150).
            if rest_clean[idx:idx + len(other)] == other:
                blocked = True
                break
            # Longer alias fully covers this occurrence (globe ⊂ forgedglobe).
            ostart = 0
            while True:
                oidx = rest_clean.find(other, ostart)
                if oidx < 0:
                    break
                if oidx <= idx and end <= oidx + len(other):
                    blocked = True
                    break
                ostart = oidx + 1
            if blocked:
                break
        if not blocked:
            return idx
        start = idx + 1


def _short_alias_whole_token_in_original(token_clean, original_text):
    """True when a short alias appears as its own token in the original text.

    Cleaned matching concatenates words (``RF, 150#`` → ``rf150``), so short
    aliases like ``rf`` / ``pe`` need a word-boundary check on the original
    string: ``RF`` in ``WN - RF, 150#`` matches, but ``pe`` inside ``pipe`` and
    ``rf`` inside revision ``rfv`` do not.
    """
    text = str(original_text or "")
    if not text or not token_clean:
        return False
    # One case-insensitive pattern ≡ checking upper/lower/capitalize variants.
    pat = _SHORT_ALIAS_RE_CACHE.get(token_clean)
    if pat is None:
        pat = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(token_clean)}(?![A-Za-z0-9])",
            re.I,
        )
        _SHORT_ALIAS_RE_CACHE[token_clean] = pat
    return pat.search(text) is not None


def _alias_token_matches(token_clean, rest_clean, all_aliases, original_text=""):
    """Whether this cleaned alias may be consumed from the remainder."""
    if not token_clean or not rest_clean:
        return False
    if len(token_clean) <= 2:
        # Short aliases: allow (1) exact cleaned remainder (snippet is only "rf"),
        # or (2) whole-token hit in the original text, still present in rest and
        # not covered by a longer known alias.
        if rest_clean == token_clean:
            return True
        if not _short_alias_whole_token_in_original(token_clean, original_text):
            return False
        return _find_unblocked_alias_index(token_clean, rest_clean, all_aliases) >= 0
    return _find_unblocked_alias_index(token_clean, rest_clean, all_aliases) >= 0


def find_group_features(original_text, clean_text, feature_group_dict, type_key, group_key, clean_size=None):

    """
    استخراج سایر ویژگی‌ها (غیر csv) و phisic ها:
    - ترتیب اولویت براساس Mnum رعایت می‌شود (با یافتن مقدار در یک M، Mهای بالاتر بررسی نمی‌شوند).
    - داخل هر سطح M: اگر چند مقدار در متن پیدا شود، اولویت با مقدار با طول cleaned بیشتر است
      (مثلاً gr.304L بر gr.304 که زیررشتهٔ آن است). Letter فقط در تساوی طول به‌کار می‌رود.
    - اگر هیچ مقدار واقعی پیدا نشد و کلید null در JSON موجود بود، مقدار null یک بار اختصاص داده می‌شود.
    - خروجی: dict متغیرها و متن باقی‌مانده
    - مقدار ذخیره‌شده همیشه نام canonical کلید JSON است (مثلاً Class 150)، نه alias کوتاه.
    """
    results = {}
    alarm_optional = set()
    alarm_required = set()

    # Treat the detected type as a normal feature variable.
    # Type is intentionally stored as <group>_type = <type>, for example:
    # fitting_type = cap.  Other features stay <feature>_<group>_<type>.
    if group_key and type_key:
        results[f"{str(group_key).strip().lower()}_type"] = str(type_key).strip()

        # اضافه کردن سایز اگر وجود داشته باشد
    size_var = f"size_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"

    if not clean_size or clean_size in ["", "null", None]:
        results[size_var] = "null"
    else:
        results[size_var] = clean_size.strip()


    rest = clean_text
    alias_key = None
    if group_key or type_key:
        alias_key = (
            str(group_key or "").strip().lower(),
            str(type_key or "").strip().lower(),
        )
    all_aliases = _collect_feature_alias_cleans(feature_group_dict, cache_key=alias_key)

    for feature_key, feature_val in feature_group_dict.items():
        if str(feature_key).startswith('phisic'):
            continue  # phisic‌ها در انتها جدا بررسی می‌شوند

        var_base = re.sub(r'_\d+$', '', feature_key) if re.search(r'_\d+$', feature_key) else feature_key

        parsed = []
        null_present = False

        # 🔹 آماده‌سازی و استخراج Mnum، Letter، و طول cleaned مقدار
        for pat_key, pat_values in feature_val.items():
            clean_pat_key, suppress_deps, require_deps = parse_feature_dependency_markers(pat_key)
            if "null" in clean_pat_key.lower():
                null_present = True
            Mnum, letter, base_name = parse_feature_pattern_key(clean_pat_key)
            pv = load_feature_values(pat_values)
            max_len = max(
                (len(clean_for_group_and_features(str(s))) for s in pv),
                default=0,
            )
            parsed.append((Mnum or 9999, letter or 'Z', -max_len, clean_pat_key, pv, base_name, suppress_deps, require_deps))

        # 🔹 مرتب‌سازی اولیه بر اساس Mnum (گروه‌بندی سطح M)
        parsed.sort(key=lambda x: (x[0], x[1], x[2]))

        found = []
        found_M = False
        idx = 0
        while idx < len(parsed):
            Mnum = parsed[idx][0]
            if found_M:
                break

            # همهٔ کلیدهای همین سطح M را یکجا جمع کن
            tier = []
            while idx < len(parsed) and parsed[idx][0] == Mnum:
                tier.append(parsed[idx])
                idx += 1

            rest_clean = clean_for_group_and_features(rest)
            candidates = []
            for _m, letter, _neg, _pat_key, pv, base_name, suppress_deps, require_deps in tier:
                for raw_val in pv:
                    val_clean = str(raw_val).strip()
                    if not val_clean or val_clean.lower() == "null":
                        continue
                    token_clean = clean_for_group_and_features(val_clean)
                    if not _alias_token_matches(
                        token_clean, rest_clean, all_aliases, original_text=original_text
                    ):
                        continue
                    candidates.append((
                        -len(token_clean),  # longer cleaned token first
                        letter or 'Z',
                        token_clean,
                        str(base_name or "").strip(),  # always store JSON key name
                        suppress_deps,
                        require_deps,
                    ))

            if not candidates:
                continue

            # Longest match in this M tier wins over Letter / key order.
            candidates.sort(key=lambda c: (c[0], c[1]))
            seen_bases = set()
            for _neg_len, _letter, token_clean, base_name, suppress_deps, require_deps in candidates:
                rest_clean = clean_for_group_and_features(rest)
                match_at = _find_unblocked_alias_index(token_clean, rest_clean, all_aliases)
                if match_at < 0:
                    continue
                if not base_name or base_name in seen_bases:
                    continue
                rest = rest_clean[:match_at] + rest_clean[match_at + len(token_clean):]
                found.append(base_name)
                seen_bases.add(base_name)
                for dep in suppress_deps:
                    alarm_optional.add(f"{dep}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}")
                for dep in require_deps:
                    alarm_required.add(f"{dep}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}")
                found_M = True

        # اگر هیچ مقدار واقعی پیدا نشد و null موجود بود
        if not found and null_present:
            found.append("null")

        results[f"{str(var_base).strip().lower()}_{str(group_key).strip().lower()}_{str(type_key).strip().lower()}"] = ' '.join(found).strip()

    # 🔹 پردازش ویژگی‌های phisic به همان شکل قبل
    for feature_key, feature_val in feature_group_dict.items():
        if str(feature_key).startswith('phisic') and isinstance(feature_val, dict):
            phisic_base = re.sub(r'_\d+$', '', str(feature_key)) if re.search(r'_\d+$', str(feature_key)) else str(feature_key)
            phisic_display, phisic_var, rest = find_phisic_feature(original_text, rest, feature_val, group_key, type_key, phisic_base=phisic_base)
            results.update(phisic_var)
            for k, v in phisic_var.items():
                if not str(k).startswith("phisic_"):
                    continue
                if str(v).strip().lower() == "null" or not str(v).strip():
                    continue
                key_display = k.replace('phisic_', '').split('_')[0]
                results.setdefault(f"display_{key_display}", f"{key_display}: {v}")

    if alarm_optional:
        results["__alarm_optional__"] = sorted(alarm_optional)
    if alarm_required:
        results["__alarm_required__"] = sorted(alarm_required)

    return results, rest
