# -*- coding: utf-8 -*-
"""Standalone validation pass for the generated Pipe deliverables.

No Django/pip access is available in this environment, so this is a faithful,
from-source reimplementation of the two pieces of itemcoder engine logic that
matter most for correctness -- the M-tier keyword matcher (feature_extractor.
find_group_features) and the assign_code "&"-concatenation + _normalize_for_code
step (code_assigner.py) -- exercised directly against the generated JSON/CSV
output. Every matching rule below is copied verbatim from the real source
files read earlier in this session, not reinvented.
"""
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pipe_source as pipe
from ms_parser import parse_material_standard, PLACEHOLDER as MS_PLACEHOLDER

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
JSON_OUT = os.path.join(OUT, "json")
CSV_OUT = os.path.join(OUT, "csv")

FAIL = []
WARN = []


def fail(msg):
    FAIL.append(msg)
    print("FAIL:", msg)


def warn(msg):
    WARN.append(msg)
    print("WARN:", msg)


def ok(msg):
    print("ok:", msg)


# --------------------------------------------------------------------------- #
# 0. JSON validity + basic shape
# --------------------------------------------------------------------------- #
JSON_FILES = ["data.json", "asign_code.json", "confind_size.json",
              "final_arrange.json", "rules_pipe.json", "offer_pipe.json"]
loaded = {}
for fn in JSON_FILES:
    p = os.path.join(JSON_OUT, fn)
    try:
        with open(p, encoding="utf-8") as f:
            loaded[fn] = json.load(f)
        ok(f"{fn} parses as valid JSON")
    except Exception as e:
        fail(f"{fn} FAILED to parse: {e}")

data = loaded.get("data.json")
assign_code = loaded.get("asign_code.json")
confind_size_json = loaded.get("confind_size.json")
final_arrange = loaded.get("final_arrange.json")
rules_pipe = loaded.get("rules_pipe.json")
offer_pipe = loaded.get("offer_pipe.json")

pipe_features = data["group"]["pipe"]["pipe"]["features"]

# --------------------------------------------------------------------------- #
# 1. No accidental dict-key collisions when the tier lists were turned into
#    dicts (would silently drop an entry).
# --------------------------------------------------------------------------- #
# grade_material_4/spec_5 shift from the "raw parse" counts printed by section
# 1 (369/15+1) because of two deliberate, documented generate_all.py fixes:
#  - the DIN/EN "10CrMo910"+"14MoV63" duplicate spellings are merged into
#    their hyphenated EN twins (-2 grade tokens)
#  - "EN 755 Gr.EN AW-6060"/"...AW-6082" are re-merged into single grade
#    tokens instead of grade="EN"+spec="AW-..." (-1 grade token net, since
#    bare "EN" was not used by any other code; +2 distinct spec tokens
#    removed since those two codes no longer contribute an "AW-6060"/
#    "AW-6082" spec value)
EXPECTED_COUNTS = {
    "material_group_1": 10, "material_type_2": 38, "material_3": 80,
    "grade_material_4": 368, "spec_5": 14, "coating_6": 2,
    "design_standard_7": 23, "nace_8": 3,
}
for key, expected in EXPECTED_COUNTS.items():
    got = len(pipe_features[key])
    if got != expected:
        fail(f"data.json[{key}] has {got} keys, expected {expected} (possible collision)")
    else:
        ok(f"data.json[{key}]: {got} keys, no collisions")

# --------------------------------------------------------------------------- #
# 2. Referenced CSV paths actually exist on disk.
# --------------------------------------------------------------------------- #
REPO_ROOT_STAND_IN = os.path.join(os.path.dirname(__file__), "..", "output")


def check_csv_ref(rel_path, context):
    fn = os.path.basename(rel_path)
    p = os.path.join(CSV_OUT, fn)
    if not os.path.exists(p):
        fail(f"{context}: referenced CSV not found in delivery: {rel_path}")
    else:
        ok(f"{context}: {fn} present")


for k, v in pipe_features["phisic_9"].items():
    check_csv_ref(v, f"data.json phisic_9[{k}]")
# confind_size.json: size CSVs are per-group (find_size_<group>.csv)
_size_ref = None
try:
    _size_ref = (
        confind_size_json.get("pipe", {}).get("all-in", {}).get("size")
        or confind_size_json.get("pipe", {}).get("pipe", {}).get("size")
    )
except Exception:
    _size_ref = None
if _size_ref:
    # Reference may point at a group file that is optional for non-pipe groups;
    # only fail when the pipe file itself is missing.
    check_csv_ref(_size_ref, "confind_size.json size")
else:
    for _fn in ("find_size_pipe.csv", "find-size_pipe.csv", "find-size.csv", "find_size.csv"):
        if os.path.exists(os.path.join(CSV_OUT, _fn)):
            ok(f"confind_size: {_fn} present")
            break
    else:
        fail("confind_size: find_size_pipe.csv not found in delivery")


# --------------------------------------------------------------------------- #
# 3. assign_code.json: every col_N in range, every referenced feature name
#    exists among data.json's pipe features (bare name, "_N" suffix stripped).
# --------------------------------------------------------------------------- #
PIPE_FEATURE_BASE_NAMES = {re.sub(r"_\d+$", "", k) for k in pipe_features.keys()}
PIPE_FEATURE_BASE_NAMES |= {"size"}  # size is a separate confind_size variable, not a data.json feature block

pipe_map = assign_code["pipe"]["pipe"]
seen_cols = {}
for feat_key, col_spec in pipe_map.items():
    m = re.match(r"col_(\d+)$", col_spec)
    if not m:
        fail(f"assign_code.json pipe.pipe[{feat_key!r}] = {col_spec!r} is not a bare col_N")
        continue
    col_n = int(m.group(1))
    if not (1 <= col_n <= 14):
        fail(f"assign_code.json pipe.pipe[{feat_key!r}] col_{col_n} out of range 1-14")
    real_key = re.sub(r"^or\d+_", "", feat_key)
    parts = [p.strip() for p in real_key.split("&")] if "&" in real_key else [real_key]
    for p in parts:
        base = re.sub(r"^phisic_", "", p)
        if p.startswith("phisic_"):
            # phisic_<sub> is a dynamic name (sub = sch/sdr/pn/sn/wt); just
            # check it is one of the five we actually generate.
            if base not in {"sch", "sdr", "pn", "sn", "wt"}:
                fail(f"assign_code.json references unknown phisic sub-feature: {p}")
            continue
        if p not in PIPE_FEATURE_BASE_NAMES:
            fail(f"assign_code.json references feature {p!r} not present in data.json")
    if feat_key.startswith("or"):
        or_name = feat_key.split("_", 1)[0]
        seen_cols.setdefault(or_name, []).append(col_n)
    else:
        seen_cols.setdefault("__required__", []).append(col_n)

dupe_required = [c for c in seen_cols.get("__required__", [])
                 if seen_cols["__required__"].count(c) > 1]
if dupe_required:
    fail(f"assign_code.json: duplicate required column(s): {set(dupe_required)}")
else:
    ok("assign_code.json: no duplicate required columns, all col_N in range, all features resolvable")

# --------------------------------------------------------------------------- #
# 4. THE key correctness test: "material & grade_material & spec" concatenation
#    must reconstruct each of the 533 real Material Standard strings exactly,
#    once both sides go through the SAME normalization
#    (CODE_NORMALIZE_RE = [^0-9a-zA-Z؀-ۿ], lowercased) that
#    code_assigner._normalize_for_code actually applies.
# --------------------------------------------------------------------------- #
CODE_NORMALIZE_RE = re.compile(r"[^0-9a-zA-Z؀-ۿ]")


def normalize_for_code(s):
    return CODE_NORMALIZE_RE.sub("", str(s)).lower()


mismatches = []
for c in pipe.FEATURES["Material Standard"]:
    original = pipe.NAMES[c]
    if re.match(r"^\$.*\$$", original.strip()):
        continue
    material, grade, spec = parse_material_standard(original)
    parts_norm = [normalize_for_code(material)]
    if grade:
        parts_norm.append(normalize_for_code(f"Gr.{grade}"))
    if spec:
        parts_norm.append(normalize_for_code(spec))
    rebuilt_norm = "".join(parts_norm)
    expected_norm = normalize_for_code(original)
    if rebuilt_norm != expected_norm:
        mismatches.append((original, rebuilt_norm, expected_norm))

if mismatches:
    fail(f"{len(mismatches)}/{len(pipe.FEATURES['Material Standard'])} Material Standard "
         f"values do NOT round-trip through the material+grade_material+spec concatenation:")
    for orig, got, exp in mismatches[:15]:
        print(f"    {orig!r}: rebuilt={got!r} != expected={exp!r}")
else:
    ok(f"all {len(pipe.FEATURES['Material Standard'])} Material Standard values round-trip "
       f"correctly through material+grade_material+spec concatenation")

# --------------------------------------------------------------------------- #
# 5. Standalone reimplementation of find_group_features' M-tier matcher, run
#    against data.json's actual generated dicts, to prove every canonical
#    value is recoverable from its OWN bare text with no cross-value
#    collision inside the same facet (the exact bug class the M-tier /
#    longest-first design exists to prevent).
# --------------------------------------------------------------------------- #
_MKEY_RE = re.compile(r'^M(\d+)_([A-Z])_(.+)$')


def clean_for_group_and_features(text):
    return re.sub(r'[^a-z0-9آ-ی\.]', '', text.lower())


def match_simple_facet_on_clean(feature_dict, rest_clean):
    """Faithful port of the relevant slice of find_group_features (the
    non-phisic branch): tier by Mnum, stop after the first M tier that
    produces a hit. Within an M tier, if several values appear in the text,
    the longest cleaned token wins (so gr.304L beats the substring gr.304).
    Letter is only a tie-break. Takes/returns the ALREADY-CLEANED remaining
    text so callers can thread the same shared "rest" through multiple facets.
    """
    parsed = []
    null_present = False
    for pat_key, pat_values in feature_dict.items():
        if "null" in pat_key.lower():
            null_present = True
        m = _MKEY_RE.match(pat_key)
        if m:
            mnum, letter, base_name = int(m.group(1)), m.group(2), m.group(3)
        else:
            mnum, letter, base_name = 9999, 'Z', pat_key
        max_len = max((len(clean_for_group_and_features(str(s))) for s in pat_values), default=0)
        parsed.append((mnum, letter, -max_len, pat_values, base_name))
    parsed.sort(key=lambda x: (x[0], x[1], x[2]))

    found = []
    found_M = False
    idx = 0
    while idx < len(parsed):
        mnum = parsed[idx][0]
        if found_M:
            break
        tier = []
        while idx < len(parsed) and parsed[idx][0] == mnum:
            tier.append(parsed[idx])
            idx += 1

        candidates = []
        for _m, letter, _neg, pv, base_name in tier:
            for raw_val in pv:
                val_clean = str(raw_val).strip()
                if not val_clean or val_clean.lower() == "null":
                    continue
                token_clean = clean_for_group_and_features(val_clean)
                if token_clean and token_clean in rest_clean:
                    candidates.append((-len(token_clean), letter, token_clean, base_name.strip()))

        if not candidates:
            continue

        candidates.sort(key=lambda c: (c[0], c[1]))
        seen_bases = set()
        for _neg_len, _letter, token_clean, base_name in candidates:
            if token_clean not in rest_clean:
                continue
            if base_name in seen_bases:
                continue
            found.append(base_name)
            seen_bases.add(base_name)
            rest_clean = rest_clean.replace(token_clean, '', 1)
            found_M = True

    if not found and null_present:
        found.append("null")
    return " ".join(found).strip(), rest_clean


def match_simple_facet(feature_dict, probe_text):
    """Single-facet convenience wrapper (no shared-rest mutation) -- used by
    the per-facet collision probe (section 5), where each facet is tested in
    isolation on purpose."""
    result, _ = match_simple_facet_on_clean(feature_dict, clean_for_group_and_features(probe_text))
    return result


def canonical_value_of_key(pat_key):
    m = _MKEY_RE.match(pat_key)
    return m.group(3) if m else pat_key


facet_test_plan = [
    ("material_group_1", "material_group"),
    ("material_type_2", "material_type"),
    ("material_3", "material"),
    ("grade_material_4", "grade_material"),
    ("spec_5", "spec"),
    ("coating_6", "coating"),
    ("design_standard_7", "design_standard"),
    ("nace_8", "nace"),
]

# Documented, NOT-fixable-in-JSON exception (see the long comment next to
# GRADE_CANON_OVERRIDE in generate_all.py): these grade PAIRS clean to the
# EXACT SAME search string because clean_for_group_and_features discards the
# one character that distinguishes them ("/" or "¼") rather than normalizing
# it, so no M-tier ordering can separate them -- whichever one is probed can
# come back as the other. Code ASSIGNMENT is unaffected (normalize_for_code
# drops the same characters symmetrically on both sides), only free-text
# disambiguation between these specific rare grades is. Tracked here as a
# known-and-accepted gap, not silently ignored.
KNOWN_UNFIXABLE_GRADE_COLLISION_PAIRS = {
    frozenset({"1/2", "12"}),
    frozenset({"1¼", "1"}),
    frozenset({"2¼", "2"}),
}


def _is_known_grade_collision(bare_canon, bare_result):
    return frozenset({bare_canon, bare_result}) in KNOWN_UNFIXABLE_GRADE_COLLISION_PAIRS

total_probes = 0
collision_fails = []
known_collisions_hit = []
for feat_key, label in facet_test_plan:
    fdict = pipe_features[feat_key]
    for pat_key, patterns in fdict.items():
        canon = canonical_value_of_key(pat_key)
        if canon == "null" or not patterns or patterns == [""]:
            continue
        # Probe with the FIRST listed synonym (the value most likely to be
        # typed verbatim) -- this is the value the facet is actually keyed by
        # for material/grade_material/spec, or the first curated synonym for
        # the small closed facets.
        probe = str(patterns[0])
        total_probes += 1
        result = match_simple_facet(fdict, probe)
        if result != canon:
            bare_canon = canon[3:] if canon.startswith("Gr.") else canon
            bare_result = result[3:] if result.startswith("Gr.") else result
            if label == "grade_material" and _is_known_grade_collision(bare_canon, bare_result):
                known_collisions_hit.append((label, canon, probe, result))
            else:
                collision_fails.append((label, canon, probe, result))

if known_collisions_hit:
    warn(f"{len(known_collisions_hit)} probe(s) hit the documented, unfixable-without-engine-"
         f"changes grade collisions (1/2 vs 12, 1¼ vs 1, 2¼ vs 2) -- expected, see "
         f"GRADE_CANON_OVERRIDE comment in generate_all.py:")
    for label, canon, probe, result in known_collisions_hit:
        print(f"    [{label}] probe={probe!r} (expected {canon!r}) matched {result!r} instead")

if collision_fails:
    fail(f"{len(collision_fails)}/{total_probes} facet-value probes did NOT round-trip "
         f"(UNEXPECTED cross-value collision inside a facet's M-tiers):")
    for label, canon, probe, result in collision_fails[:20]:
        print(f"    [{label}] probe={probe!r} (expected {canon!r}) matched {result!r} instead")
else:
    ok(f"all {total_probes} facet-value probes (material_group/material_type/material/"
       f"grade_material/spec/coating/design_standard/nace) round-trip with zero UNEXPECTED "
       f"cross-value collisions ({len(known_collisions_hit)} documented/accepted exceptions)")

# --------------------------------------------------------------------------- #
# 6. End-to-end: for a sample of REAL pipe.py combinations, build a plausible
#    free-text description, run it through match_simple_facet for material/
#    grade_material/spec, concatenate + normalize, and confirm it reproduces
#    the row's own Material Standard text -- the full path the user's bug
#    report was actually about.
# --------------------------------------------------------------------------- #
# Every non-placeholder code, not just a sample -- it's cheap and this is the
# single most important check in the whole harness. Facets are probed in
# data.json's OWN key order (material_3, then grade_material_4, then spec_5)
# against a SHARED, mutating "rest_clean", exactly mirroring how
# find_group_features walks one feature_group_dict: a match in an earlier
# facet removes that text so a later facet can no longer see it. This is what
# makes it safe for a short spec token (e.g. "3Cr") to be a coincidental
# substring of a longer, unrelated grade string (e.g. "X3CrNiMo17-13-3") --
# whichever facet is scanned FIRST and finds a legitimate hit consumes that
# text before the later facet ever runs.
sample_codes = [c for c in pipe.FEATURES["Material Standard"]
                if not re.match(r"^\$.*\$$", pipe.NAMES[c].strip())]

e2e_fails = []
for c in sample_codes:
    original = pipe.NAMES[c]
    probe_text = original  # simulates a customer typing the standard text verbatim
    rest_clean = clean_for_group_and_features(probe_text)
    mat, rest_clean = match_simple_facet_on_clean(pipe_features["material_3"], rest_clean)
    grd, rest_clean = match_simple_facet_on_clean(pipe_features["grade_material_4"], rest_clean)
    spc, rest_clean = match_simple_facet_on_clean(pipe_features["spec_5"], rest_clean)
    parts = [normalize_for_code(mat)]
    if grd and grd != "null":
        parts.append(normalize_for_code(grd))
    if spc and spc != "null":
        parts.append(normalize_for_code(spc))
    rebuilt = "".join(parts)
    expected = normalize_for_code(original)
    if rebuilt != expected:
        e2e_fails.append((original, mat, grd, spc, rebuilt, expected))

if e2e_fails:
    fail(f"{len(e2e_fails)}/{len(sample_codes)} end-to-end probe(s) failed:")
    for orig, mat, grd, spc, rebuilt, expected in e2e_fails[:15]:
        print(f"    {orig!r}: mat={mat!r} grd={grd!r} spc={spc!r} -> {rebuilt!r} != {expected!r}")
else:
    ok(f"end-to-end: all {len(sample_codes)} real (non-placeholder) Material-Standard "
       f"descriptions extract+concatenate back to their own DB text correctly")

# --------------------------------------------------------------------------- #
# 7. offer_pipe.json / rules_pipe.json shape sanity.
# --------------------------------------------------------------------------- #
op = offer_pipe["pipe"]["pipe"]
bad_shape = 0
for feature_name, block in op.items():
    for value, conds in block.items():
        for cond_key, cond_vals in conds.items():
            if not isinstance(cond_vals, list) or not all(isinstance(v, str) for v in cond_vals):
                bad_shape += 1
if bad_shape:
    fail(f"offer_pipe.json: {bad_shape} condition entries are not a plain list[str]")
else:
    ok("offer_pipe.json: condition-list shapes all valid")

rp = rules_pipe["pipe"]
bad_shape2 = 0
for value, conds in rp.items():
    if not isinstance(conds, dict):
        bad_shape2 += 1
        continue
    for other_feat, vals in conds.items():
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            bad_shape2 += 1
if bad_shape2:
    fail(f"rules_pipe.json: {bad_shape2} entries are not shaped {{other_feature: [values]}}")
else:
    ok("rules_pipe.json: all entries correctly shaped")

# --------------------------------------------------------------------------- #
# 8. Size CSV: find_size_<group>.csv header grammar + resolver probes
# --------------------------------------------------------------------------- #
size_csv_path = None
for _fn in ("find_size_pipe.csv", "find-size_pipe.csv", "find-size.csv", "find_size.csv", "size_pipe_pipe.csv"):
    _p = os.path.join(CSV_OUT, _fn)
    if os.path.exists(_p):
        size_csv_path = _p
        break
if not size_csv_path:
    fail("find_size_pipe.csv missing from delivery size/")
else:
    # Import the production resolver when running inside the app tree; fall
    # back to a minimal local probe if the package import is unavailable.
    try:
        import sys
        _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from itemcoder.find_size import resolve_size, parse_column_header

        # Header grammar smoke checks
        r1 = parse_column_header('(NPS)-"')
        assert r1 and r1["suffixes"] == ['"'] and r1["allow_bare"] is False
        r2 = parse_column_header("DN||IN-[DN]-mm||milimeter")
        assert r2 and r2["prefixes"] == ["DN", "IN"] and r2["allow_bare"] is True
        r3 = parse_column_header("(OD)-mm")
        assert r3 and r3["suffixes"] == ["mm"] and r3["allow_bare"] is False
        ok("find-size header grammar parses (NPS)/[DN]/(OD) columns")

        SIZE_PROBES = [
            ('4"', '4"', '4"'),                    # exact NPS default
            ('0.25"', '1/4"', '0.25" (1/4")'),   # NPS decimal + inch mark
            ("0.25", "null", "0.25"),              # bare decimal: no NPS map, keep raw
            ("DN 100", '4"', "DN 100 (4\")"),
            ("DN100", '4"', "DN100 (4\")"),
            ("100", '4"', "100 (4\")"),            # bare DN (brackets)
            ("8mm", '1/4"', "8mm (1/4\")"),
            ("13.7mm", '1/4"', "13.7mm (1/4\")"),
            ("1.37cm", '1/4"', "1.37cm (1/4\")"),
        ]
        size_fails = []
        for probe, exp_clean, exp_disp in SIZE_PROBES:
            got = resolve_size(probe, group="pipe")
            if got.get("clean_size") != exp_clean or got.get("display_size") != exp_disp:
                size_fails.append((probe, (exp_clean, exp_disp), (got.get("clean_size"), got.get("display_size"))))
        # Missing group file → raw size unchanged (no mapping).
        no_file = resolve_size("DN 100", group="fitting")
        if no_file.get("clean_size") != "DN 100" or no_file.get("display_size") != "DN 100":
            size_fails.append(("fitting/DN 100", ("DN 100", "DN 100"),
                               (no_file.get("clean_size"), no_file.get("display_size"))))
        if size_fails:
            fail(f"find-size probes mismatched: {size_fails}")
        else:
            ok(f"find-size: all {len(SIZE_PROBES)} pipe probes + missing-group passthrough OK")
    except Exception as exc:
        fail(f"find-size resolver probes crashed: {exc}")

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
print()
print("=" * 70)
print(f"VALIDATION SUMMARY: {len(FAIL)} failing checks, {len(WARN)} warnings")
if FAIL:
    print("FAILURES:")
    for m in FAIL:
        print(" -", m)
if WARN:
    print("WARNINGS:")
    for m in WARN:
        print(" -", m)
sys.exit(1 if FAIL else 0)
