"""Single-record and live row processing.

This module contains the main text-processing workflows used by both the initial
Excel upload and AJAX row updates. URL/view code remains in views.py.
"""

import re

from .alarm_builder import build_alarms
from .code_assigner import assign_code_from_csv
from .feature_extractor import (
    find_group,
    find_group_features,
    find_type,
    refresh_alarm_dependency_metadata,
    apply_type_dependency_metadata,
    _clean_type_key,
)
from .regex_patterns import load_feature_values
from .final_arrange_builder import build_final_arrange_and_features
from .normalizers import (
    clean_for_group_and_features,
    preserve_original,
    remove_first_occurrence,
    parse_feature_pattern_key,
    parse_feature_dependency_markers,
)
from .composite_keys import alias_key_matches, get_by_alias
from .revision_set import apply_set_commands, parse_set_commands, strip_set_commands
from .rule_engine import apply_rules
from .runtime_cache import get_row_base_cache, store_row_base_cache

# Vocabulary scrub maps are identical for a given group/type; build once.
_VOCAB_CANON_CACHE = {}


def clear_vocab_caches():
    _VOCAB_CANON_CACHE.clear()


def _strip_group_type_identity_tokens(clean_text, group_dict, group_key, type_key):
    """Remove group/type identity tokens before feature extraction.

    When group/type are forced (e.g. Revision wrote ``pipe``), those words must
    not remain in the cleaned text. Otherwise short feature aliases such as
    ``pe`` match inside ``pipe`` and invent false materials like PE.

    Longer feature aliases win: do not strip ``globe`` out of ``forgedglobe``
    when ``Forged Globe`` is a known feature alias — otherwise EA/Revision
    picks for alarm ``group`` never apply on Globe Valve rows.
    """
    from .feature_extractor import (
        _clean_type_key,
        _collect_feature_alias_cleans,
        _find_unblocked_alias_index,
    )

    text = clean_text or ""
    protect = set()
    try:
        features_container = _build_features_container(
            group_dict if isinstance(group_dict, dict) else {},
            group_key or "",
            type_key or "",
        )
        protect = _collect_feature_alias_cleans(features_container)
    except Exception:
        protect = set()

    def _safe_remove(tok: str) -> None:
        nonlocal text
        tc = clean_for_group_and_features(str(tok or ""))
        if not tc or not text:
            return
        aliases = set(protect)
        aliases.add(tc)
        idx = _find_unblocked_alias_index(tc, text, aliases)
        if idx < 0:
            return
        text = text[:idx] + text[idx + len(tc):]

    type_l = str(type_key or "").strip().lower()
    if type_l and isinstance(group_dict, dict):
        types = group_dict.get("type", {}) or {}
        if isinstance(types, dict):
            for raw_key, patterns in types.items():
                visible = _clean_type_key(raw_key)
                if str(visible).strip().lower() != type_l and not alias_key_matches(visible, type_key):
                    continue
                for pat in sorted(load_feature_values(patterns), key=lambda v: -len(str(v or ""))):
                    _safe_remove(pat)
                _safe_remove(visible)
                break
        _safe_remove(type_key)

    _safe_remove(group_key)
    return text


def _feature_value_vocabulary(features_container):
    """Canonical tokens allowed in FTCO DISCRIPTION from data.json features.

    Free-text leftovers from Client Description (e.g. ``sf``, ``gfs``) are never
    in this set, so they cannot appear after the group prefix.
    """
    vocab, _canon = _feature_vocab_and_canonical_map(features_container)
    return vocab


def _feature_vocab_and_canonical_map(features_container, cache_key=None):
    """Build scrub vocabulary and cleaned-token → canonical base_name map.

    Aliases like ``#150`` / ``A105`` must resolve to the JSON key display names
    ``Class 150`` / ``ASTM A105`` (spaces preserved), never the short matched
    token alone.
    """
    if cache_key is not None:
        hit = _VOCAB_CANON_CACHE.get(cache_key)
        if hit is not None:
            return set(hit[0]), dict(hit[1])

    vocab = set()
    canon = {}
    if not isinstance(features_container, dict):
        return vocab, canon

    def _remember(token, canonical):
        tok = str(token or "").strip()
        can = str(canonical or "").strip()
        if not tok or not can or tok.lower() == "null":
            return
        cleaned = clean_for_group_and_features(tok)
        if cleaned:
            vocab.add(cleaned)
            # Longer canonical wins when two keys share a cleaned alias edge-case.
            prev = canon.get(cleaned)
            if prev is None or len(can) >= len(prev):
                canon[cleaned] = can
        vocab.add(tok.lower())
        if can:
            vocab.add(clean_for_group_and_features(can))
            vocab.add(can.lower())
            can_clean = clean_for_group_and_features(can)
            if can_clean:
                canon[can_clean] = can

    for _feature_key, feature_val in features_container.items():
        if str(_feature_key).startswith("phisic") or not isinstance(feature_val, dict):
            continue
        for pat_key, pat_values in feature_val.items():
            clean_pat_key, _suppress, _require = parse_feature_dependency_markers(pat_key)
            if "null" in str(clean_pat_key).lower():
                continue
            _mnum, _letter, base_name = parse_feature_pattern_key(clean_pat_key)
            base = str(base_name or "").strip()
            if not base:
                continue
            _remember(base, base)
            for raw in load_feature_values(pat_values):
                _remember(raw, base)

    if cache_key is not None:
        _VOCAB_CANON_CACHE[cache_key] = (frozenset(vocab), dict(canon))
    return vocab, canon


def _normalize_to_vocabulary_phrases(plain, vocab, canon_map):
    """Keep only data.json phrases; expand aliases to full canonical names.

    ``Class 150`` stays ``Class 150`` (not ``150``).  ``A105`` becomes
    ``ASTM A105`` when that is the JSON key.  Unknown freestyle words are dropped.
    """
    plain = str(plain or "").strip()
    if not plain:
        return ""

    full_clean = clean_for_group_and_features(plain)
    if full_clean in canon_map:
        return canon_map[full_clean]
    if full_clean in vocab or plain.lower() in vocab:
        return plain

    words = plain.split()
    kept = []
    i = 0
    while i < len(words):
        matched = None
        # Longest word-span first so "Class 150" wins over bare "150".
        for j in range(len(words), i, -1):
            span = " ".join(words[i:j])
            span_clean = clean_for_group_and_features(span)
            if span_clean in canon_map:
                matched = canon_map[span_clean]
                i = j
                break
            if span_clean in vocab or span.lower() in vocab:
                matched = span
                i = j
                break
        if matched:
            kept.append(matched)
        else:
            i += 1
    return " ".join(kept)


def _scrub_non_vocabulary_features(
    feature_vars,
    features_container,
    group_key,
    type_key,
    target_values_map=None,
    keep_keys=None,
):
    """Drop / normalize feature tokens against data.json vocabulary.

    - Freestyle Client Description words (``SF``, ``G``, …) are removed.
    - Multi-word canonical names keep their spaces (``Class 150``, ``ASTM A105``).
    - Short aliases are expanded to the JSON key name when possible.
    """
    feature_vars = dict(feature_vars or {})
    keep_keys = set(keep_keys or [])
    vocab_key = None
    if group_key or type_key:
        vocab_key = (
            str(group_key or "").strip().lower(),
            str(type_key or "").strip().lower(),
        )
    vocab, canon_map = _feature_vocab_and_canonical_map(features_container, cache_key=vocab_key)
    if group_key:
        g = str(group_key).strip()
        vocab.add(clean_for_group_and_features(g))
        vocab.add(g.lower())
        canon_map[clean_for_group_and_features(g)] = g
    if type_key:
        t = str(type_key).strip()
        vocab.add(clean_for_group_and_features(t))
        vocab.add(t.lower())
        canon_map[clean_for_group_and_features(t)] = t
    # Offer / rule colours may introduce canonical values — keep those too.
    for raw in (target_values_map or {}):
        plain = re.sub(r"<[^>]+>", "", str(raw or "")).strip()
        if not plain:
            continue
        cleaned = clean_for_group_and_features(plain)
        vocab.add(cleaned)
        vocab.add(plain.lower())
        if cleaned and cleaned not in canon_map:
            canon_map[cleaned] = plain

    group_l = str(group_key or "").strip().lower()
    for key, value in list(feature_vars.items()):
        key_s = str(key)
        if key_s in keep_keys:
            continue
        if key_s.startswith(("__", "display_", "phisic_", "size_")):
            continue
        if group_l and key_s.lower() == f"{group_l}_type":
            continue
        if key_s.endswith("_type") and key_s.count("_") == 1:
            continue

        had_html = "<" in str(value or "")
        plain = re.sub(r"<[^>]+>", "", str(value or "")).strip()
        if not plain or plain.lower() == "null":
            continue

        normalized = _normalize_to_vocabulary_phrases(plain, vocab, canon_map)
        if not normalized:
            feature_vars[key] = "null"
        elif normalized != plain:
            # Preserve rule-engine colour wrapper when the visible text is unchanged
            # enough that only canonical expansion happened — rebuild plain text.
            feature_vars[key] = normalized
        elif had_html:
            # unchanged plain text; keep original coloured HTML
            pass

    return feature_vars


def _format_size_override(size_expr):
    """Return normalized inch size from remark/revision text after the word size.

    Examples:
    - size5/8 -> 5/8"
    - size5 -> 5"
    - size 2 * 1 1/2 -> 2" x 1 1/2"
    """
    expr = str(size_expr or "").strip()
    if not expr:
        return ""
    expr = expr.replace("×", "*").replace("X", "*").replace("x", "*")
    expr = re.sub(r"\s*\*\s*", "*", expr)
    parts = [p.strip() for p in expr.split("*") if p.strip()]
    out = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip().strip('\"')
        if not part:
            continue
        out.append(f'{part}\"')
    return " x ".join(out)


def _extract_size_override(text):
    """Find a size override token in free text without consuming other features."""
    text_s = str(text or "")
    # Capture only numeric/fraction/dimension characters after the word size and
    # stop before commas or feature words such as astm, gr, c.s, etc.
    m = re.search(r"(?i)(?:^|[^a-z0-9])size\s*[:=]?\s*([0-9][0-9\s/\.\-*xX×]*)", text_s)
    if not m:
        return ""
    return _format_size_override(m.group(1))




def has_orange_alert(target_values_map):
    """True when rule engine marked any value as orange/red alert."""
    if not isinstance(target_values_map, dict):
        return False
    return any(str(color).strip().lower() in ("orange", "red") for color in target_values_map.values())


def _size_value_from_feature_vars(feature_vars):
    """Best display size spelling from ``feature_vars`` (``size_*`` keys)."""
    if not isinstance(feature_vars, dict):
        return ""
    for key, val in feature_vars.items():
        if not str(key).lower().startswith("size"):
            continue
        s = str(val or "").strip()
        if s and s.lower() not in ("null", "nan"):
            return s
    return ""


def _norm_alert_token(value):
    """Loose normalize for comparing TVM keys to the size spelling."""
    s = str(value or "").strip().lower()
    if not s:
        return ""
    # Drop spaces / inch marks so ``32"`` matches ``32`` etc.
    s = s.replace('"', "").replace("″", "").replace(" ", "")
    return s


def _alert_values(target_values_map):
    """Values currently painted orange or red in the target-values map."""
    if not isinstance(target_values_map, dict):
        return []
    out = []
    for val, color in target_values_map.items():
        if str(color).strip().lower() in ("orange", "red"):
            out.append(val)
    return out


def _alerts_are_size_only(target_values_map, feature_vars=None, size_value=None):
    """True when every orange/red TVM entry is the row's size spelling.

    Size-only rule conflicts must not block FT-code lookup: the code table is
    authoritative for "does this exact combo exist". Non-size oranges still
    block assign (real feature conflicts).
    """
    alerts = _alert_values(target_values_map)
    if not alerts:
        return True
    size_val = str(size_value or "").strip() or _size_value_from_feature_vars(feature_vars)
    if not size_val:
        return False
    size_n = _norm_alert_token(size_val)
    if not size_n:
        return False
    for val in alerts:
        if _norm_alert_token(val) != size_n:
            return False
    return True


def can_run_assign_code(alarms, target_values_map, feature_vars=None, size_value=None):
    """Heavy code lookup when the row is complete enough to search.

    Blocks on alarms and on non-size orange/red conflicts. Size-only alerts
    still allow lookup so a DB hit can clear a stale rules-vs-size orange.
    """
    if alarms:
        return False
    if not has_orange_alert(target_values_map):
        return True
    return _alerts_are_size_only(target_values_map, feature_vars=feature_vars, size_value=size_value)


def clear_size_only_rule_alerts(target_values_map, feature_vars=None, size_value=None):
    """Drop size orange/red from TVM after a successful code assign.

    When the code table matched the full feature set (including size), a
    size-only rules conflict is stale relative to the database.
    """
    if not isinstance(target_values_map, dict):
        return target_values_map
    if not _alerts_are_size_only(target_values_map, feature_vars=feature_vars, size_value=size_value):
        return target_values_map
    size_val = str(size_value or "").strip() or _size_value_from_feature_vars(feature_vars)
    size_n = _norm_alert_token(size_val)
    if not size_n:
        return target_values_map
    for val in list(target_values_map.keys()):
        if str(target_values_map.get(val) or "").strip().lower() not in ("orange", "red"):
            continue
        if _norm_alert_token(val) == size_n:
            target_values_map.pop(val, None)
    return target_values_map


def _collapse_duplicate_size_keys(feature_vars, group_key, type_key):
    """Keep a single ``size_<group>_<type>`` value (drop duplicate size_* keys)."""
    fv = dict(feature_vars or {})
    size_keys = [k for k in list(fv.keys()) if str(k).lower().startswith("size_")]
    if len(size_keys) <= 1:
        return fv
    g = str(group_key or "").strip().lower()
    t = str(type_key or "").strip().lower()
    preferred = f"size_{g}_{t}" if g else size_keys[0]
    value = ""
    if preferred in fv and str(fv.get(preferred) or "").strip():
        value = str(fv.get(preferred)).strip()
    if not value:
        for k in size_keys:
            v = str(fv.get(k) or "").strip()
            if v and v.lower() != "null":
                value = v
                break
    for k in size_keys:
        fv.pop(k, None)
    if value:
        fv[preferred] = value
    return fv


def _build_features_container(group_dict, group_key, type_key):
    """Collect the feature dictionary for a group/type from data.json.

    Shared by the initial Build-TO path and live Remark/Revision processing so
    both sides always see the same feature set.
    """
    features_container = {}
    if not isinstance(group_dict, dict):
        return features_container

    group_features = group_dict.get("features", {})
    if isinstance(group_features, dict):
        features_container.update(group_features)

    if type_key:
        for composite_key, composite_val in group_dict.items():
            if isinstance(composite_val, dict) and "features" in composite_val:
                if alias_key_matches(composite_key, type_key) or str(composite_key).strip().lower() == "all_in":
                    features_container.update(composite_val["features"])

        type_specific_block = get_by_alias(group_dict, type_key, {})
        if isinstance(type_specific_block, dict) and "features" in type_specific_block:
            features_container.update(type_specific_block["features"])

        combined_key = f"{group_key}-{type_key}"
        if get_by_alias(group_dict, combined_key) is not None:
            combined_block = get_by_alias(group_dict, combined_key, {})
            if isinstance(combined_block, dict) and "features" in combined_block:
                features_container.update(combined_block["features"])

        type_section = group_dict.get("type", {})
        if isinstance(type_section, dict):
            inner_type_block = get_by_alias(type_section, type_key, {})
            if isinstance(inner_type_block, dict) and "features" in inner_type_block:
                features_container.update(inner_type_block["features"])

    return features_container


def process_text_record(original_text, json_dict, clean_size=None):
    """Initial Build-TO / upload path — identical engine to live when remark/revision are empty."""
    return process_text_record_live(
        original_text,
        json_dict,
        group_key_input=None,
        type_key_input=None,
        remark="",
        revision="",
        clean_size=clean_size,
        row_index=None,
        allow_code_lookup=False,
    )


def process_text_record_live(original_text, json_dict, group_key_input=None, type_key_input=None, remark="", revision="", clean_size=None, row_index=None, allow_code_lookup=True, confirm_group_change=None, locked_group=None, locked_type=None):
    """
    نسخه Live اصلاح‌شده:
    - تابع دو بار اجرا می‌شود (متن اصلی + متن remark/revision)
    - ویژگی‌های به‌دست‌آمده ادغام می‌شوند (اولویت با remark/revision)
    - اگر متغیرهای phisic متفاوت باشند، فقط جدید باقی می‌ماند (هم در Feature_Variables و هم در Final_Text)

    ``confirm_group_change``:
      None  — first pass; if Revision would change an already-set Group, do not
              override yet and return ``Pending_Group_Change``.
      True  — user confirmed; apply Revision group override.
      False — user rejected; keep existing Group and extract features in it.

    ``locked_group`` / ``locked_type``:
      After the user Confirms a Revision group change, the UI keeps sending the
      confirmed group until Revision is cleared. Further typing must not re-prompt
      or flip to another detected group.

    Initial Build TO calls this with empty remark/revision so entry and live
    edits share one pipeline and the same data.json / offer / rules / arrange.
    """
    if not clean_size or clean_size.strip().lower() == "null":
        clean_size = ""
    else:
        Real_size = re.search(r'\((.*?)\)', clean_size)
        if Real_size:
            clean_size = Real_size.group(1)
        else:
            clean_size = clean_size.strip()

    # Group/Type are never taken from the client (dropdowns removed). Always
    # rediscover from the description; Revision alone may override group/type
    # (with confirm when a group is already set — see below).
    active_group_input = None
    active_type_input = None
    set_commands = parse_set_commands(revision or "")
    revision_for_processing = strip_set_commands(revision or "")
    revision_clean = clean_for_group_and_features(revision_for_processing)
    revision_group = None
    revision_type_from_rev = ""
    pending_group_change = None
    locked_group = str(locked_group or "").strip() or None
    locked_type = str(locked_type or "").strip() or None
    if revision_clean:
        revision_group, _revision_group_label = find_group(revision_clean, json_dict)
        if revision_group:
            revision_group_dict = get_by_alias(json_dict.get("group", {}), revision_group, {})
            if not isinstance(revision_group_dict, dict):
                revision_group_dict = {}
            revision_type_from_rev, _mt, _ca = find_type(revision_clean, revision_group_dict)
            revision_type_from_rev = revision_type_from_rev or ""

    def extract_features_for_text(text, clean_size):

        """تابع کمکی برای استخراج feature_vars و اطلاعات اولیه از یک متن"""
        orig = preserve_original(text)
        clean = clean_for_group_and_features(text)

        # --- گروه ---
        if active_group_input:
            group_key = active_group_input
            group_label = active_group_input
        else:
            group_key, group_label = find_group(clean, json_dict)

        if not group_key:
            return "", "", {}, {}, clean, orig

        group_dict = get_by_alias(json_dict.get("group", {}), group_key, {})
        if not isinstance(group_dict, dict):
            group_dict = {}

        # --- تایپ ---
        if active_type_input:
            type_key = active_type_input
            # Still strip identity tokens — do NOT leave "pipe" in the text.
            clean_after_type = _strip_group_type_identity_tokens(
                clean, group_dict, group_key, type_key
            )
        else:
            type_key, matched_type_token, clean_after_type = find_type(clean, group_dict)
            type_key = type_key or ""
            clean_after_type = clean_after_type if type_key else clean
            if type_key:
                clean_after_type = _strip_group_type_identity_tokens(
                    clean_after_type, group_dict, group_key, type_key
                )

        features_container = _build_features_container(group_dict, group_key, type_key)

        # --- استخراج ویژگی‌ها ---
        feature_vars, rest_clean_final = find_group_features(
            orig, clean_after_type, features_container, type_key, group_key , clean_size
        )


        return group_key, type_key, features_container, feature_vars, clean_after_type, orig

    # --- اجرای بخش اول برای متن اصلی ---
    # Fast path: after upload, the original row features are cached in RAM.
    # When only Remark changes, reuse them and extract regex only from the
    # Remark/Revision delta. If Revision explicitly changes group/type, fall
    # back to the original full extraction so old behavior stays intact.
    #
    # Never reuse a cache entry that has no Group: that happens when Build/edit
    # previously failed to identify non-pipe groups (e.g. flange). Reusing it
    # would skip find_group forever until Revision forces a re-detect.
    cached_base = None
    # Detect description group first (without revision override) so we can
    # decide whether a Revision group change needs user confirm.
    # Peek cache without forcing revision bypass yet.
    if row_index is not None:
        cached_base = get_row_base_cache(row_index, original_text=original_text, clean_size=clean_size)
        if cached_base and not str(cached_base.get("group") or "").strip():
            cached_base = None

    if cached_base:
        group_key_main = cached_base.get("group") or ""
        type_key_main = cached_base.get("type") or ""
        features_container_main = dict(cached_base.get("features_container") or {})
        feature_vars_main = dict(cached_base.get("feature_vars_raw") or {})
        clean_after_type_main = clean_for_group_and_features(original_text)
        orig_main = preserve_original(original_text)
    else:
        group_key_main, type_key_main, features_container_main, feature_vars_main, clean_after_type_main, orig_main = extract_features_for_text(original_text, clean_size)

    # Decide Revision group override vs confirm gate.
    def _g_norm(g):
        return str(g or "").strip().lower()

    revision_changed_group = False

    # Sticky Confirm: keep the user-confirmed group until Revision is cleared.
    # Do not re-prompt and do not flip to a newly detected Revision group.
    if locked_group:
        active_group_input = locked_group
        active_type_input = locked_type or None
        revision_changed_group = True
        pending_group_change = None
        # Type-only Revision words (e.g. "slip on") against the locked group.
        if revision_clean and not active_type_input:
            locked_gd = get_by_alias(json_dict.get("group", {}), locked_group, {})
            if isinstance(locked_gd, dict):
                _lt, _mt, _ca = find_type(revision_clean, locked_gd)
                if _lt:
                    active_type_input = _lt
                    revision_type_from_rev = _lt
    elif revision_group:
        if not _g_norm(group_key_main):
            # No group yet — set immediately (same as before).
            active_group_input = revision_group
            active_type_input = revision_type_from_rev
            revision_changed_group = True
        elif _g_norm(revision_group) != _g_norm(group_key_main):
            if confirm_group_change is True:
                active_group_input = revision_group
                active_type_input = revision_type_from_rev
                revision_changed_group = True
            elif confirm_group_change is False:
                # Keep existing group; still extract revision features in it.
                active_group_input = None
                active_type_input = None
            else:
                # Pending confirm — process under existing group, ask UI.
                active_group_input = None
                active_type_input = None
                pending_group_change = {
                    "from": group_key_main,
                    "to": revision_group,
                    "from_type": type_key_main or "",
                    "to_type": revision_type_from_rev or "",
                }
        # same group as description — not an override; features extract below

    # Revision may name a type without a group word (e.g. "slip on" on a flange
    # row). Detect type against the current/target group so Type leaves Alarm
    # and type-scoped features load — even when find_group on Revision alone
    # would have failed before pattern cleaning, or Revision has no group token.
    if (
        revision_clean
        and not revision_type_from_rev
        and not locked_group
        and confirm_group_change is not False
    ):
        g_for_type = None
        if revision_group and (
            not _g_norm(group_key_main)
            or _g_norm(revision_group) == _g_norm(group_key_main)
            or confirm_group_change is True
            or revision_changed_group
        ):
            g_for_type = revision_group
        elif _g_norm(group_key_main) and (
            not revision_group or _g_norm(revision_group) == _g_norm(group_key_main)
        ):
            g_for_type = group_key_main
        if g_for_type:
            g_dict = get_by_alias(json_dict.get("group", {}), g_for_type, {})
            if isinstance(g_dict, dict):
                _rt, _mt, _ca = find_type(revision_clean, g_dict)
                revision_type_from_rev = _rt or ""

    # If Revision forces a new group, re-extract the description under that
    # group/type context (bypass stale Build-time cache values).
    if revision_changed_group:
        group_key_main, type_key_main, features_container_main, feature_vars_main, clean_after_type_main, orig_main = extract_features_for_text(original_text, clean_size)

    # Same group: description has no type but Revision found one (e.g. equaltee90
    # → Equal Tee 90°). Promote that type so features_container builds, Alarm
    # type clears, and size keys use a single type suffix.
    elif (
        revision_type_from_rev
        and str(group_key_main or "").strip()
        and not str(type_key_main or "").strip()
        and (
            not revision_group
            or _g_norm(revision_group) == _g_norm(group_key_main)
            or locked_group
        )
    ):
        active_group_input = group_key_main
        active_type_input = revision_type_from_rev
        group_key_main, type_key_main, features_container_main, feature_vars_main, clean_after_type_main, orig_main = extract_features_for_text(
            original_text, clean_size
        )

    # Remark/revision snippets often contain only feature words (for example
    # "s.s, a350, gr.lf2") and not the group/type words.  In that case they
    # must still be parsed against the row's current group/type.  If Revision
    # explicitly changed group/type above, active_group_input is already that
    # new value; otherwise use the group/type detected from the original row.
    if not active_group_input:
        active_group_input = group_key_main
    if not active_type_input:
        active_type_input = type_key_main
    

    # --- اجرای بخش دوم برای remark + revision ---
    # IMPORTANT: revision and remark are TWO independent fields. They must be
    # extracted SEPARATELY, never concatenated, because clean_for_group_and_features
    # strips spaces — so "sch80" (revision) + "sch40" (remark) would merge into
    # "sch80sch40" and be mis-detected as a single token ("sch80s"). Extracting
    # each field on its own keeps the boundary.
    #
    # Each short snippet is run through the full feature extractor, which fills
    # unmatched features with "null". Those nulls mean "not mentioned here", NOT
    # "clear this feature". If we naively .update() them, a later field (e.g.
    # remark="sch100") would wipe real values found in the other field (e.g.
    # revision="astm a53" → material). Only merge non-null hits so BOTH fields
    # apply to different features; on a true conflict the later field (remark)
    # still wins.
    revision_snippet = str(revision_for_processing or "").strip()
    remark_snippet = str(remark or "").strip()
    size_override_rev = _extract_size_override(revision_snippet) if revision_snippet else ""
    size_override_rem = _extract_size_override(remark_snippet) if remark_snippet else ""
    size_override = size_override_rem or size_override_rev
    feature_vars_remark = {}
    for _snippet in (revision_snippet, remark_snippet):
        if not _snippet:
            continue
        _, _, _, _snippet_fv, _, _ = extract_features_for_text(_snippet, clean_size)
        if not _snippet_fv:
            continue
        for _k, _v in _snippet_fv.items():
            if _v is None:
                continue
            _vs = str(_v).strip()
            if not _vs or _vs.lower() == "null":
                continue
            feature_vars_remark[_k] = _v  # remark processed last -> overrides on conflict
    if size_override and group_key_main and type_key_main:
        feature_vars_remark[f"size_{str(group_key_main).strip().lower()}_{str(type_key_main).strip().lower()}"] = size_override



    # --- ادغام feature_vars (اولویت با remark/revision) ---
    merged_features = dict(feature_vars_main or {})

    # استخراج phisicها در هر دو بخش
    remark_phisic = {k: v for k, v in (feature_vars_remark or {}).items() if k.startswith("phisic_")}
    main_phisic_keys = [k for k in merged_features.keys() if k.startswith("phisic_")]

    # اگر remark/revision phisic دارد → phisicهای اصلی را حذف کن و phisicهای remark را جایگزین کن
    if remark_phisic:
        # 1) حذف phisicهای قدیمی از merged_features
        for pk in main_phisic_keys:
            merged_features.pop(pk, None)

            # 2) حذف display مرتبط با آن phisic (مثال: phisic_sch_pipe_pipe -> display_sch)
            # منطق تولید display در find_group_features:
            # key_display = k.replace('phisic_', '').split('_')[0]
            try:
                key_display = pk.replace('phisic_', '').split('_')[0]
                display_key = f"display_{key_display}"
                merged_features.pop(display_key, None)
            except Exception:
                # اگر هر مشکلی پیش آمد، نادیده بگیر
                pass

        # 3) اضافه کردن phisicهای جدید از remark/revision
        merged_features.update(remark_phisic)

        # 4) همچنین اگر remark بخش display_<...> مربوطه دارد، آن displayها در feature_vars_remark وجود دارند
        #    و بعداً در مرحله‌ی ادغام کلی اضافه خواهند شد (در حلقه‌ی زیر).
    # سپس سایر کلیدها را ادغام کن (اولویت با remark)
    for k, v in (feature_vars_remark or {}).items():
        if not v or not str(v).strip():
            continue  # خالی یا None نادیده گرفته شود

        # اگر مقدار remark/revision "null" است و متن اصلی مقدار واقعی دارد، متن اصلی حفظ شود
        main_val = feature_vars_main.get(k)
        if str(v).strip().lower() == "null" and main_val and str(main_val).strip().lower() != "null":
            merged_features[k] = main_val  # اولویت با متن اصلی
        else:
            merged_features[k] = v  # در غیر این صورت اولویت با remark/revision

    # One size key only — description may have size_fitting_ while Revision type
    # detect wrote size_fitting_equal tee 90° with the same value.
    merged_features = _collapse_duplicate_size_keys(
        merged_features, group_key_main, type_key_main
    )

    group_dict_main = get_by_alias(json_dict.get('group', {}), group_key_main, {})
    merged_features = refresh_alarm_dependency_metadata(merged_features, features_container_main, group_key_main, type_key_main)
    merged_features = apply_type_dependency_metadata(merged_features, group_dict_main, group_key_main, type_key_main)

    # --- تشخیص featureهایی که واقعاً توسط remark/revision تغییر کرده‌اند ---
    # هایلایت آبی باید در سطح کل مقدار feature باشد، نه اختلاف حرف‌به‌حرف Final_Text.
    def _norm_feature_value_for_compare(val):
        if val is None:
            return ""
        return re.sub(r"\s+", " ", str(val).strip()).lower()

    changed_feature_keys = set()
    if revision_snippet or remark_snippet:
        for _k, _v in merged_features.items():
            if str(_k).startswith("display_"):
                continue
            _main_v = (feature_vars_main or {}).get(_k)
            if _k in (feature_vars_remark or {}) and (
                _norm_feature_value_for_compare(_main_v) != _norm_feature_value_for_compare(_v)
            ):
                changed_feature_keys.add(_k)

    # --- اعمال قواعد ---
    original_feature_vars = merged_features.copy()  # برای حفظ منطق قبلی رنگ‌های سبز/نارنجی
    # changed_feature_keys lets apply_rules re-derive auto-filled (green) words
    # when a Revision/Remark edit makes them clash (offer conflict repair).
    feature_vars, target_values_map = apply_rules(merged_features, changed_feature_keys)
    feature_vars = refresh_alarm_dependency_metadata(feature_vars, features_container_main, group_key_main, type_key_main)
    feature_vars = apply_type_dependency_metadata(feature_vars, group_dict_main, group_key_main, type_key_main)


    # Keep the base size unless remark/revision explicitly overrides it.
    size_key = f"size_{str(group_key_main).strip().lower()}_{str(type_key_main).strip().lower()}"
    if size_override:
        feature_vars[size_key] = size_override
        changed_feature_keys.add(size_key)
    elif clean_size:
        # Extractor may leave the literal ``null`` when size is not in the
        # description — treat that as missing so the SIZE column wins.
        cur_size = str(feature_vars.get(size_key) or "").strip()
        if not cur_size or cur_size.lower() == "null":
            feature_vars[size_key] = clean_size
    feature_vars = _collapse_duplicate_size_keys(
        feature_vars, group_key_main, type_key_main
    )

    # --- ایجاد آلارم‌ها ---
    expected_features = list(features_container_main.keys()) if isinstance(features_container_main, dict) else []
    alarms = build_alarms(expected_features, feature_vars, type_key_main, group_key_main)

    set_changed = apply_set_commands(
        feature_vars,
        set_commands,
        features_container_main,
        group_key_main,
        type_key_main,
        alarms,
    )
    if set_changed:
        changed_feature_keys.update(set_changed)

    if not group_key_main and "group" not in alarms:
        alarms.append("group")
    if not type_key_main and "type" not in alarms:
        alarms.append("type")

    # Never let free-text Client Description leftovers into FTCO DISCRIPTION.
    # Values written by set(...) commands are intentional overrides — keep them.
    feature_vars = _scrub_non_vocabulary_features(
        feature_vars,
        features_container_main,
        group_key_main,
        type_key_main,
        target_values_map,
        keep_keys=set_changed,
    )
    original_feature_vars = _scrub_non_vocabulary_features(
        original_feature_vars,
        features_container_main,
        group_key_main,
        type_key_main,
        target_values_map,
        keep_keys=set_changed,
    )

    # --- ساخت Final_Text و Filled_Features با منطق مشترک Initial_changes ---
    final_arranged_text, filled_features_summary = build_final_arrange_and_features(
        group_key_main,
        type_key_main,
        feature_vars,
        original_feature_vars=original_feature_vars,
        target_values_map=target_values_map,
        changed_feature_keys=changed_feature_keys,
    )

    # --- فراخوانی assign_code_from_csv ---
    # This is the heavy stage. During live typing it runs when the row has no
    # alarm and no non-size orange/red conflict. Size-only rule alerts still
    # allow lookup so a code-table hit can clear a stale size orange.
    code_value = ""
    assign_allowed = can_run_assign_code(
        alarms, target_values_map, feature_vars=feature_vars
    )
    if assign_allowed and allow_code_lookup and group_key_main:
        code_value = assign_code_from_csv(group_key_main, type_key_main, feature_vars)
        if code_value:
            clear_size_only_rule_alerts(target_values_map, feature_vars=feature_vars)

    # --- خروجی نهایی ---
    result = {
        "Input String": original_text,
        "Group": group_key_main or "",
        "Type": type_key_main or "",
        "Feature_Variables": feature_vars,
        "Alarm": alarms,
        "Final_Text": final_arranged_text,
        "Filled_Features": filled_features_summary,
        "Code": code_value,
        "Can_Assign_Code": assign_allowed,
        "Has_Orange_Alert": has_orange_alert(target_values_map),
        "Target_Values_Map": target_values_map,
        "Changed_Feature_Keys": sorted(changed_feature_keys),
        "Size_Override": size_override,
        "Pending_Group_Change": pending_group_change,
        # Raw description extraction (before remark merge / rules) for row cache.
        "_Base_Feature_Variables": dict(feature_vars_main or {}),
        "_Features_Container": dict(features_container_main or {}) if isinstance(features_container_main, dict) else {},
    }

    return result
