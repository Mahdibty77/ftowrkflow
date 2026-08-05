"""Business rules and rule-based color target calculation.

`apply_rules` keeps the previous behavior: it updates feature values when an
offer rule applies and produces `target_values_map` for green/orange coloring.

Orange comes from ``rules_<group>.json`` (via ``flag_incompatible``) and from
``common_rulse.json`` global alerts. Offer green comes from ``offer_*.json``.
"""

import os
import re

from .constants import rules, rules_path, CODE_NORMALIZE_RE
from .composite_keys import get_by_alias, split_alias_key
from .composite_features import compound_siblings, compound_group_members, compound_groups
from .regex_patterns import is_empty_variant


def colored_display(value, target_value, current_value=None):
    """
    بر اساس target_values_map رنگ می‌دهد (مقادیر با حروف بزرگ نمایش داده می‌شوند)
    """
    color = "black"  # پیش‌فرض مشکی
    if target_value is not None:
        color = target_value  # استفاده مستقیم از رنگی که apply_rules ساخته
    return f"<span style='color:{color}'>{str(value).upper()}</span>"


def _feature_map_from_vars(feature_vars, group=None, type_=None):
    """Build {feature_name: value} from ``material_group_pipe_pipe``-style keys."""
    fmap = {}
    if not isinstance(feature_vars, dict):
        return fmap

    def _is_null(v):
        return v is None or str(v).strip() == "" or str(v).strip().lower() == "null"

    # Suffix match is case-insensitive: Type may be "Gate Valve" while keys use
    # ``_valve_gate valve``. Length of the lowercased suffix still matches ``ks``.
    g = str(group or "").strip().lower()
    t = str(type_ or "").strip().lower()
    if g and t:
        suffix = f"_{g}_{t}"
        legacy = f"_{t}_{g}"
        for k, v in feature_vars.items():
            ks = str(k)
            if ks.startswith("__") or ks.startswith("display_") or _is_null(v):
                continue
            ks_l = ks.lower()
            if ks_l.endswith(suffix):
                fmap[ks[: -len(suffix)]] = str(v).strip()
            elif ks_l.endswith(legacy):
                fmap[ks[: -len(legacy)]] = str(v).strip()
        if fmap:
            return fmap

    for k, v in feature_vars.items():
        ks = str(k)
        if ks.startswith("__") or ks.startswith("display_") or _is_null(v):
            continue
        # Allow spaces in the type segment (e.g. ``rating_valve_gate valve``).
        m = re.match(r"^(.+)_([A-Za-z0-9]+)_([A-Za-z0-9][A-Za-z0-9 ]*)$", ks)
        if m:
            fmap[m.group(1)] = str(v).strip()
    return fmap


def apply_rules(feature_vars, changed_keys=None):

    # Pick up disk edits to offer/common/rules JSON without a server restart.
    try:
        from .constants import ensure_rules_fresh
        ensure_rules_fresh()
    except Exception:
        pass

    target_values_map = {}
    # Feature vars the user just changed via Revision/Remark. Only these can
    # trigger the OFFER CONFLICT REPAIR below, so plain uploads keep the old
    # behavior and only a live edit re-derives the auto-filled (green) words.
    changed_keys = set(changed_keys or ())

    def _mark(value, color):
        """Assign a display color. Priority: red > orange > green/other.

        A size-not-in-rules flag (red) and a rules violation (orange) must win
        over offer greens. Red is never downgraded to orange.
        """
        if color == "red":
            target_values_map[value] = "red"
            return
        if color == "orange":
            if target_values_map.get(value) == "red":
                return
            target_values_map[value] = "orange"
            return
        if target_values_map.get(value) in ("orange", "red"):
            return
        target_values_map[value] = color

    def _apply_rules_flags(fmap, group_name, type_name, *, authoritative=False):
        """Flag incompatibles, then apply size-isolation (orange vs red).

        When ``authoritative`` is True (known group/type final pass), also
        *clear* oranges on live fmap values that are no longer incompatible.
        Without that, a stale early pass (wrong auto-detected group) can leave
        false oranges that the correct flange/pipe check never removes.
        """
        try:
            from . import item_builder
            gname = str(group_name or "").strip()
            # Never run rules colouring with an unknown group: auto-detect from
            # bare values often picks the wrong family (e.g. fitting instead of
            # flange for C.S + ASTM A105 + Asme B16.5) and paints false oranges.
            if not gname:
                return
            _vals = [v for v in (fmap or {}).values() if v]
            raw_bad = set(item_builder.flag_incompatible(
                _vals, gname, feature_map=fmap, type_=type_name or ""
            ) or [])
            orange_set, red_set = item_builder.refine_size_conflict(
                fmap or {}, gname, type_name or "", raw_bad
            )
            still_flagged = orange_set | red_set
            # Drop cascade oranges that size-isolation cleared.
            for v in raw_bad:
                if v in still_flagged:
                    continue
                if target_values_map.get(v) == "orange":
                    target_values_map.pop(v, None)
            if authoritative:
                # Reconcile: any fmap value previously orange/red but no longer
                # incompatible under the real group must return to uncolored
                # (offer greens may re-apply later / already applied).
                live_vals = {str(v).strip() for v in (fmap or {}).values() if v}
                for v in list(target_values_map.keys()):
                    if v not in live_vals:
                        continue
                    if target_values_map.get(v) not in ("orange", "red"):
                        continue
                    if v in still_flagged:
                        continue
                    target_values_map.pop(v, None)
            for v in orange_set:
                _mark(v, "orange")
            for v in red_set:
                _mark(v, "red")
        except Exception:
            pass

    # ---------- helper ----------
    def is_null(v):
        return v is None or str(v).strip() == "" or str(v).strip().lower() == "null"

    def green(v):
        return v

    def normalize(v):
        return str(v).strip()

    # ---------- load rules ----------

    if not os.path.exists(rules_path):
        return feature_vars, {}

    # =========================================================
    # COMMON RULE - ALERT (GLOBAL)  — from common_rulse.json
    # =========================================================
    common_alert = rules.get("common_rule", {}).get("alert", {})

    def _common_alert_allows(actual: str, allowed_norm: list, rule_key: str) -> bool:
        """True when ``actual`` is listed, or is a compound schedule of listed parts.

        ``phisic_sch`` in common_rulse.json only enumerates short tokens
        (``10``, ``STD``, …). Reducer schedules like ``10 × 10`` must still
        pass when every segment is an allowed short token — otherwise the
        longer spelling (correct for coding) is falsely marked orange.
        """
        act = normalize(actual)
        if not act:
            return True
        if act in allowed_norm:
            return True
        allowed_ci = {str(a).strip().lower() for a in allowed_norm}
        if act.lower() in allowed_ci:
            return True
        rk = str(rule_key or "").strip().lower()
        if "sch" not in rk and "schedule" not in rk:
            return False
        body = re.sub(r"(?i)^sch\s*:\s*", "", act).strip()
        body = re.sub(r"(?i)^sch\s*", "", body).strip()
        parts = re.split(r"\s*[×xX]\s*", body)
        if len(parts) < 2:
            return False
        return all(
            p.strip().lower() in allowed_ci
            for p in parts
            if p.strip()
        )

    for rule_key, allowed_values in common_alert.items():
        allowed_norm = [normalize(v) for v in allowed_values]

        for var_name, var_value in feature_vars.items():
            if str(var_name).startswith("__"):
                continue

            if is_null(var_value):
                continue

            # 🔑 prefix / contains match
            if any(alias in var_name for alias in split_alias_key(rule_key)):
                actual = normalize(var_value)

                if not _common_alert_allows(actual, allowed_norm, rule_key):
                    # رنگ برای مقدار واقعی
                    _mark(var_value, "orange")

                    # 🔑 رنگ برای display نهایی
                    core = var_name.replace("phisic_", "").split("_")[0]
                    display_key = f"display_{core}"

                    if display_key in feature_vars:
                        display_val = feature_vars.get(display_key)
                        if display_val:
                            _mark(display_val, "orange")

    # ---------- extract group & type ----------
    group = None
    type_ = None

    # Prefer the explicit <group>_type variable, e.g. fitting_type = cap.
    for k, v in feature_vars.items():
        k_s = str(k)
        if k_s.startswith("__") or not k_s.endswith("_type"):
            continue
        possible_group = k_s[:-5]
        possible_type = str(v or "").strip().lower()
        if possible_type and (possible_group in rules or get_by_alias(rules, possible_group) is not None):
            group = possible_group
            type_ = possible_type
            break

    # Fallback: infer from canonical <feature>_<group>_<type> variables.
    if not group or not type_:
        for k in feature_vars.keys():
            k_s = str(k)
            if k_s.startswith("__") or k_s.startswith("display_"):
                continue
            m = re.match(r".+_([a-zA-Z0-9]+)_([a-zA-Z0-9]+)$", k_s)
            if m:
                maybe_group = m.group(1)
                maybe_type = m.group(2)
                if maybe_group in rules or get_by_alias(rules, maybe_group) is not None:
                    type_ = maybe_type
                    group = maybe_group
                    break

    if not group or not type_:
        return feature_vars, target_values_map

    group_block = get_by_alias(rules, group, {})
    if not isinstance(group_block, dict):
        return feature_vars, target_values_map

    rule_block = get_by_alias(group_block, type_, {})
    if not isinstance(rule_block, dict):
        return feature_vars, target_values_map

    def _var_name(feature_name):
        canonical = f"{feature_name}_{group}_{type_}"
        legacy = f"{feature_name}_{type_}_{group}"
        if canonical in feature_vars:
            return canonical
        return legacy

    def _get_feature(feature_name, default=""):
        return feature_vars.get(_var_name(feature_name), default)

    # asign_code.json compound groups (e.g. "material & grade_material & spec").
    # When one member of a group is colored, its siblings must read in the same
    # color because they render as one unit (e.g. "API 5L Gr.B PSL1").
    _siblings = compound_siblings(group, type_)

    def _color_compound_siblings(feature_name, color):
        """Give the same color to the sibling members of a compound feature.

        Only real (non-null) sibling values are colored; nothing is matched or
        overwritten, so code assignment and rule matching stay identical.
        """
        for sib in _siblings.get(str(feature_name).strip().lower(), ()):  # sib names are lower-case
            sib_val = _get_feature(sib, "")
            if not is_null(sib_val):
                _mark(sib_val, color)

    def _squash(value):
        """Alphanumeric-only, lower-cased form (same idea as code normalization)."""
        return CODE_NORMALIZE_RE.sub("", str(value or "")).lower()

    def _joined_compound_value(feature_name):
        """Join a compound feature's parts (material + grade_material + spec) in
        asign_code.json order, skipping null parts. Returns None for a feature
        that is not part of any compound key."""
        members = compound_group_members(group, type_, feature_name)
        if not members:
            return None
        parts = []
        for m in members:
            val = _get_feature(m, "")
            if not is_null(val):
                parts.append(str(val).strip())
        return " ".join(parts)

    def _cond_matches(feature_name, rule_value):
        """True if ``rule_value`` matches ``feature_name``'s current value.

        Preserves the original exact/normalized match. Additionally, for a
        compound feature (declared with ``&`` in asign_code.json) it matches
        when the rule value is a prefix of the JOINED value of all parts — so a
        rules/offer entry written as the full combined string
        ("API 5L Gr.B PSL1") matches even though ``material`` alone extracts to
        just "API 5L". The parts are ordered per asign_code.json, so a partial
        value like "API 5L" or "API 5L Gr.B" also matches correctly.
        """
        actual = _get_feature(feature_name, "")
        # "No coating"/placeholder ($coating$ etc.) matches an empty/absent value.
        if is_empty_variant(rule_value) and is_null(actual):
            return True
        if normalize(actual) == normalize(rule_value):
            return True
        rv = _squash(rule_value)
        if rv and _squash(actual) == rv:
            return True
        joined = _joined_compound_value(feature_name)
        if joined is not None and rv and _squash(joined).startswith(rv):
            return True
        return False

    # =========================================================
    # 1️⃣ OFFER RULES  (فقط مقداردهی + سبز)
    # =========================================================
    offer_block = rule_block.get("offer", {})

    # Values the offer AUTO-FILLED into an empty feature (target_var -> (feature,
    # value)). These are only suggestions: unlike a value the user typed, an
    # auto-filled value that later turns out to break the rules is repaired or
    # dropped (see the OFFER CONFLICT REPAIR pass below), never flagged orange.
    offer_added = {}

    for feature_name, feature_rules in offer_block.items():
        target_var = _var_name(feature_name)
        current_val = feature_vars.get(target_var)

        for target_value, conditions in feature_rules.items():
            rule_matched = False

            for cond_key, cond_val in conditions.items():

                # list
                if isinstance(cond_val, list):
                    if any(_cond_matches(cond_key, x) for x in cond_val):
                        rule_matched = True

                # OR
                elif "||" in cond_key:
                    keys = [k.strip() for k in cond_key.split("||")]
                    vals = [v.strip() for v in cond_val.split("||")]
                    for k, v in zip(keys, vals):
                        if _cond_matches(k, v):
                            rule_matched = True
                            break

                # AND
                elif "&" in cond_key:
                    keys = [k.strip() for k in cond_key.split("&")]
                    vals = [v.strip() for v in cond_val.split("&")]
                    ok = True
                    for k, v in zip(keys, vals):
                        if not _cond_matches(k, v):
                            ok = False
                            break
                    if ok:
                        rule_matched = True

                # single feature written as a plain string value
                else:
                    if _cond_matches(cond_key, cond_val):
                        rule_matched = True

                if rule_matched:
                    break

            # apply OFFER
            if rule_matched:
                if is_null(current_val):
                    feature_vars[target_var] = green(target_value)
                    _mark(target_value, "green")
                    offer_added[target_var] = (feature_name, target_value)
                    # Compound unit (asign_code.json "a & b & c"): color the
                    # sibling members green too so the whole displayed value
                    # (e.g. "API 5L Gr.B PSL1") reads as one offered unit.
                    _color_compound_siblings(feature_name, "green")
                elif _cond_matches(feature_name, target_value):
                    # The offered value is already present (possibly as the full
                    # combined value): color the unit green without changing it.
                    # _mark keeps an existing orange flag (a rules violation wins
                    # over an offer, e.g. C.S stays orange when its design
                    # standard breaks the C.S rule).
                    _mark(current_val, "green")
                    _color_compound_siblings(feature_name, "green")
                break

    # =========================================================
    # 1️⃣.5 OFFER CONFLICT REPAIR  (only for AUTO-FILLED values)
    # =========================================================
    # After every offer has run, a value the offer auto-filled (green) can clash
    # with a value the user actually typed (original text / Revision / Remark).
    # Example: the user types ASME B36.19 in Revision; the offer had auto-filled
    # material_type=C.S, but the C.S rule allows only ASME B36.10/API 5L, so C.S
    # now breaks the rules.
    #
    # Auto-fill is only a suggestion, so a clashing suggestion is:
    #   * UPDATED to the value the user's own words imply, when an offer ties the
    #     two together (design_standard:{"ASME B36.19":{"material_type":["S.S"]}}
    #     means ASME B36.19 -> material_type S.S), or
    #   * DROPPED entirely when no such offer exists.
    # A value the USER typed is never touched here — the rules.json pass at the
    # top already flags it orange.  This makes green words re-update (or vanish)
    # whenever another word changes in Revision/Remark, as requested.
    if offer_added and changed_keys:
        try:
            from . import item_builder

            def _reverse_offer_value(feat_name, anchor_var):
                """The value for feat_name implied by the CLASHING feature the
                user just changed, through an offer that lists feat_name as a
                condition. Only the offer anchored on ``anchor_var`` counts, so
                we pick the value that resolves this clash — e.g.
                design_standard=ASME B36.19 implies material_type=S.S — instead
                of any listed value."""
                for other_feat, other_rules in offer_block.items():
                    if other_feat == feat_name or not isinstance(other_rules, dict):
                        continue
                    if _var_name(other_feat) != anchor_var:
                        continue
                    for other_val, conds in other_rules.items():
                        if not isinstance(conds, dict) or not _cond_matches(other_feat, other_val):
                            continue
                        cand = conds.get(feat_name)
                        if isinstance(cand, list):
                            cand = next((c for c in cand if not is_empty_variant(c)), None)
                        if cand and not is_empty_variant(cand):
                            return str(cand).strip()
                return None

            # The user values changed in this edit are the only allowed anchors.
            changed_pairs = [(k, feature_vars[k]) for k in changed_keys
                             if not str(k).startswith("__") and not str(k).startswith("display_")
                             and k not in offer_added and not is_null(feature_vars.get(k))]

            for target_var, (feat_name, added_val) in list(offer_added.items()):
                # Repair only when the auto-filled value clashes with a value the
                # user JUST changed (checked pairwise so an unrelated baseline
                # incompatibility never withdraws a good suggestion).
                anchor_var = None
                for k, changed_val in changed_pairs:
                    if added_val in item_builder.flag_incompatible([added_val, changed_val], group):
                        anchor_var = k
                        break
                if anchor_var is None:
                    continue
                replacement = _reverse_offer_value(feat_name, anchor_var)
                target_values_map.pop(added_val, None)
                if replacement and normalize(replacement) != normalize(added_val):
                    feature_vars[target_var] = replacement
                    _mark(replacement, "green")
                    _color_compound_siblings(feat_name, "green")
                    offer_added[target_var] = (feat_name, replacement)
                else:
                    # No implied value -> withdraw the suggestion completely.
                    feature_vars[target_var] = ""
                    offer_added.pop(target_var, None)
        except Exception:
            pass

    # =========================================================
    # 2️⃣ FINAL RULES CONSISTENCY  (whole FTCO text vs rules_*.json)
    # =========================================================
    # Re-check EVERY value now present — including ones an offer auto-filled or the
    # repair just updated (e.g. C.S -> S.S) — against the per-group rules_*.json, so
    # the FTCO description is always rule-consistent, both the first time the row is
    # shown and after any Remark/Revision edit. This is the authoritative pass:
    # it flags true incompatibles AND clears stale oranges left by any earlier
    # heuristic (never auto-detect group from bare values — that mis-picks
    # fitting vs flange and falsely oranges legal C.S + ASTM A105 + Asme B16.5).
    # Orange wins over the offer green via `_mark`, and the compound pass below
    # spreads it across the whole unit.
    try:
        _fmap = _feature_map_from_vars(feature_vars, group, type_)
        _apply_rules_flags(_fmap, group, type_, authoritative=True)
    except Exception:
        pass

    # =========================================================
    # 3️⃣ COMPOUND UNIT COLOR CONSISTENCY (asign_code.json "a & b & c")
    # =========================================================
    # A compound feature such as "material & grade_material & spec" is displayed
    # as ONE unit ("API 5L Gr.B PSL1").  If ANY member ended up colored by ANY of
    # the mechanisms above — the rules_*.json compatibility check
    # (flag_incompatible), the common-rule alert, or an offer (green) — the whole
    # unit must read in a single color so the three parts never disagree.  Orange
    # (a rule violation) wins over green, so a non-compliant unit is never shown
    # half-offered.  This only adjusts display colors; feature values and code
    # assignment are untouched.
    _seen_groups = set()
    for _members in compound_groups(group, type_).values():
        if _members in _seen_groups:
            continue
        _seen_groups.add(_members)
        _vals, _unit_color = [], None
        for _m in _members:
            _v = _get_feature(_m, "")
            if is_null(_v):
                continue
            _vals.append(_v)
            _c = target_values_map.get(_v)
            if _c == "red":
                _unit_color = "red"
            elif _c == "orange" and _unit_color != "red":
                _unit_color = "orange"
            elif _c == "green" and _unit_color is None:
                _unit_color = "green"
        if _unit_color:
            for _v in _vals:
                # _mark keeps red/orange as top priority, so a green unit never
                # downgrades a member that a rule already flagged.
                _mark(_v, _unit_color)

    return feature_vars, target_values_map
