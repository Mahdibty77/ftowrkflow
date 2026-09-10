"""Behavioural snapshot of the platform's business logic.

WHY THIS EXISTS
---------------
This project has no unit tests and a great deal of logic that must not change:
the FTCO coding rules, price calculation, the Jalali calendar, document-number
formatting, export shaping, and the routing rules that decide which unit and
which role may act on a case. Refactoring any of that blind is how a commercial
system quietly starts producing wrong documents.

So instead of asserting what the answers *should* be (which nobody can write
down for 45,000 lines after the fact), this module records what they *are*. It
runs a fixed, deliberately wide set of inputs through the real code and writes
every answer to one JSON file.

Two checkouts that produce byte-identical files behave identically on every
input covered here. That turns "did I change the logic?" from a judgement call
into a diff.

HOW TO USE IT
-------------
Build a database once (any checkout whose migrations run), then snapshot each
side and compare::

    python -m tests.snapshot --db /tmp/snap.sqlite3 --out before.json
    # ... make changes ...
    python -m tests.snapshot --db /tmp/snap.sqlite3 --out after.json
    python -m tests.snapshot --compare before.json after.json

``--db`` is copied to a scratch file first; the file you point at is never
written to.

WHAT IS AND IS NOT COVERED
--------------------------
Covered: every pure-ish decision surface listed in ``SECTIONS`` below.
Not covered: HTTP views, templates, JavaScript, and anything requiring a
browser. Those need their own tests; this file is the floor, not the ceiling.

DETERMINISM RULES
-----------------
Nothing here may depend on the wall clock, on random values, on dict iteration
order, or on database primary keys. Timestamps are never emitted. Sets are
always sorted before they are written. If a section cannot be made
deterministic, it does not belong in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Every section the snapshot records, in the order they are written.
SECTIONS = (
    "jalali",
    "doc_codes",
    "coding_engine",
    "calculation_engine",
    "export_totals",
    "routing_allowed_actions",
)


# ---------------------------------------------------------------------------
# Django bootstrap
# ---------------------------------------------------------------------------
def _bootstrap(db_path: str) -> None:
    """Configure Django to run against ``db_path`` and nothing else.

    The database is redirected by generating a throwaway settings module and
    pointing ``DJANGO_SETTINGS_MODULE`` at it *before* ``django.setup()``.

    It has to happen in that order. Django's ``ConnectionHandler`` caches the
    ``DATABASES`` dict the first time it is read, so assigning to
    ``settings.DATABASES`` after setup looks like it works and silently does
    nothing — the snapshot then runs against the project's real ``db.sqlite3``,
    creating fixture users and cases in it. Overriding through a settings module
    makes that mistake impossible to repeat.
    """
    sys.path.insert(0, str(BASE_DIR))
    os.environ["DJANGO_DEBUG"] = "1"          # ephemeral SECRET_KEY, no prod guard
    os.environ["FT_SKIP_SCHEMA_SYNC"] = "1"   # snapshot must not mutate schema
    os.environ["DJANGO_TIME_ZONE"] = "Asia/Tehran"

    override_dir = Path(tempfile.mkdtemp(prefix="ftsnap-settings-"))
    (override_dir / "ft_snapshot_settings.py").write_text(
        "from ftworkflow.settings import *  # noqa: F401,F403\n"
        "DATABASES = {'default': {\n"
        "    'ENGINE': 'django.db.backends.sqlite3',\n"
        f"    'NAME': {str(db_path)!r},\n"
        "}}\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(override_dir))
    os.environ["DJANGO_SETTINGS_MODULE"] = "ft_snapshot_settings"

    import django
    django.setup()

    # Fail loudly rather than quietly snapshotting the wrong database.
    from django.db import connections
    actual = connections["default"].settings_dict["NAME"]
    if str(actual) != str(db_path):
        raise RuntimeError(
            f"snapshot is pointed at {actual!r}, expected {db_path!r}")


def _safe(fn, *args, **kwargs):
    """Call ``fn`` and record the outcome, including the failure.

    A snapshot entry of ``{"__error__": "..."}`` is a perfectly valid recorded
    behaviour: if the current code raises on some input, the refactored code
    must raise the same way. Swallowing that would hide exactly the regressions
    this file exists to catch.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - recording the error IS the point
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _norm(value):
    """Make a value JSON-stable: sets sorted, tuples listed, keys ordered."""
    if isinstance(value, set):
        return sorted(_norm(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _norm(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Section: Jalali calendar
# ---------------------------------------------------------------------------
def snap_jalali() -> dict:
    """Every Gregorian->Jalali->Gregorian round trip across a 40-year window.

    Sampled rather than exhaustive (one date per week plus every month boundary
    and every leap-day candidate), which is dense enough that an off-by-one in
    the leap-year table cannot survive it.
    """
    import datetime as dt

    from cases.jalali import gregorian_to_jalali, jalali_to_gregorian

    out = {"g2j": {}, "j2g": {}, "roundtrip_failures": []}

    day = dt.date(2000, 1, 1)
    end = dt.date(2040, 12, 31)
    step = dt.timedelta(days=7)
    while day <= end:
        for probe in {day, day.replace(day=1)}:
            key = probe.isoformat()
            if key in out["g2j"]:
                continue
            jal = _safe(gregorian_to_jalali, probe.year, probe.month, probe.day)
            out["g2j"][key] = _norm(jal)
            # Round trip: converting back must land on the original date.
            if isinstance(jal, (list, tuple)) and len(jal) == 3:
                back = _safe(jalali_to_gregorian, *jal)
                if _norm(back) != [probe.year, probe.month, probe.day]:
                    out["roundtrip_failures"].append({"g": key, "j": _norm(jal), "back": _norm(back)})
        day += step

    # Direct Jalali inputs, including the month-length boundaries and the
    # out-of-range values reachable from the shift-month URL.
    for jy in range(1380, 1421):
        for jm in (1, 6, 7, 12):
            for jd in (1, 29, 30, 31):
                out["j2g"][f"{jy}-{jm}-{jd}"] = _norm(_safe(jalali_to_gregorian, jy, jm, jd))
    for jy, jm, jd in ((0, 1, 1), (1404, 0, 1), (1404, 13, 1), (1404, 1, 0), (1404, 12, 31), (9999, 1, 1)):
        out["j2g"][f"edge:{jy}-{jm}-{jd}"] = _norm(_safe(jalali_to_gregorian, jy, jm, jd))

    return out


# ---------------------------------------------------------------------------
# Section: document numbers and version labels
# ---------------------------------------------------------------------------
def snap_doc_codes() -> dict:
    """Document-number and export-name formatting for every kind/offer combo."""
    from cases import codes
    from cases.constants import DocKind, FormKind

    out = {"format_version": {}, "build_doc_no": {}, "build_export_name": {}}

    for version in (0, 1, 2, 9, 10, 11, 99, 100, 101, 999, 1000):
        out["format_version"][str(version)] = _norm(_safe(codes.format_version, version))

    serials = (1, 9, 10, 999, 1000, 10000)
    for kind in (DocKind.INDENT, DocKind.TENDER, DocKind.BUDGET):
        for ym in ("040301", "041230"):
            for expert_code in ("01", "123"):
                for client_code in ("0001", "77"):
                    for serial in serials:
                        key = f"{kind}|{ym}|{expert_code}|{client_code}|{serial}"
                        out["build_doc_no"][key] = _norm(_safe(
                            codes.build_doc_no, ym=ym, expert_code=expert_code,
                            client_code=client_code, serial=serial, kind=kind,
                        ))

    for form_kind in (FormKind.INQUIRY, FormKind.TO, FormKind.PI):
        for kind in (DocKind.INDENT, DocKind.TENDER):
            key = f"{form_kind}|{kind}"
            out["build_export_name"][key] = _norm(_safe(
                codes.build_export_name, form_kind=form_kind, kind=kind,
                ym="040301", expert_code="01", client_code="0001", serial=7,
                version=3,
            ))
    return out


# ---------------------------------------------------------------------------
# Section: the FTCO coding engine
# ---------------------------------------------------------------------------
# A corpus of item descriptions written to exercise each product group and the
# parsing edges that matter: mixed units, fractional inches, Persian digits,
# missing size, redundant whitespace, and text that should NOT match a group.
CODING_CORPUS = [
    # pipe
    'PIPE, CS, SEAMLESS, ASTM A106 GR.B, 2" SCH40',
    'PIPE 6" SCH 80 API 5L GR.B SMLS',
    'pipe, 1/2 inch, sch 160, astm a106 gr b',
    'PIPE CS ERW ASTM A53 GR.B 10" STD',
    'PIPE, SS, ASTM A312 TP316L, 3" SCH10S, SMLS',
    'PIPE PE100 SDR11 PN16 110MM',
    'PIPE, GALVANIZED, 1-1/2", SCH 40',
    # fitting
    'ELBOW 90DEG LR, BW, ASTM A234 WPB, 4" SCH40',
    'TEE EQUAL, BW, A234 WPB, 6" SCH 40',
    'REDUCER CONCENTRIC BW A234 WPB 8"X6" SCH40',
    'CAP, BW, ASTM A234 WPB, 2" SCH 80',
    'ELBOW 45 DEG SR BW A403 WP316L 3" SCH10S',
    'COUPLING, THREADED, 3000LB, A105, 1"',
    # flange
    'FLANGE WN RF 150# A105 4" SCH40',
    'FLANGE, SLIP ON, RF, CLASS 300, ASTM A105, 6"',
    'BLIND FLANGE RF 600# A105 2"',
    'FLANGE WNRF CL900 A182 F316L 3" SCH80S',
    # gasket
    'GASKET SPIRAL WOUND 150# 4" SS316/GRAPHITE',
    'GASKET, RING JOINT, R-45, SOFT IRON, 6" 900#',
    'GASKET NON ASBESTOS 3MM THK 2" 150#',
    # valve
    'GATE VALVE, CS, 150#, FLANGED RF, 4", A216 WCB',
    'BALL VALVE FLOATING 300# 2" A105 FULL BORE',
    'GLOBE VALVE 800LB SW A105 1"',
    'CHECK VALVE SWING 150# FLG 6" WCB',
    # studbolt / nut & bolt
    'STUD BOLT WITH 2 NUTS, ASTM A193 B7 / A194 2H, 5/8" X 120MM',
    'STUDBOLT A193 GR.B7 3/4"X150 WITH 2 HEAVY HEX NUTS A194 2H',
    'NUT, HEAVY HEX, A194 2H, 1"',
    # edges: unicode digits, extra whitespace, no size, nonsense
    'PIPE, CS, ۲ INCH, SCH ۴۰',
    '   PIPE   ,   CS ,  SEAMLESS ,   2"   SCH40   ',
    'PIPE CS SEAMLESS ASTM A106 GR B',
    'MISCELLANEOUS ITEM WITH NO RECOGNISABLE GROUP',
    '',
    '   ',
    '???',
    'ELBOW',
    '2"',
]


def snap_coding_engine() -> dict:
    """Run the whole corpus through the live row processor.

    ``allow_code_lookup`` is exercised both ways: the True path consults the
    per-group SQLite code tables (which may be absent in a clean checkout, and
    that absence is itself recorded), the False path is pure text/feature
    parsing and is always meaningful.
    """
    import json as _json

    from itemcoder.resource_paths import json_path
    from itemcoder.text_processor import process_text_record_live

    with open(json_path("data.json"), encoding="utf-8") as handle:
        json_dict = _json.load(handle)

    interesting = (
        "Final_Text", "Filled_Features", "Feature_Variables", "Alarm", "Code",
        "Group", "Type", "Size_Override", "Can_Assign_Code", "Has_Orange_Alert",
        "Target_Values_Map", "Pending_Group_Change",
    )

    out = {}
    for index, text in enumerate(CODING_CORPUS):
        for allow_lookup in (False, True):
            # row_index is deliberately fixed per case: the engine keeps a
            # per-row cache keyed on it, and a shifting index would make the
            # snapshot depend on iteration order.
            result = _safe(
                process_text_record_live,
                text, json_dict,
                group_key_input="", type_key_input="",
                remark="", revision="", clean_size="",
                row_index=index, allow_code_lookup=allow_lookup,
                confirm_group_change=None, locked_group=None, locked_type=None,
            )
            key = f"{index}|lookup={int(allow_lookup)}"
            if isinstance(result, dict) and "__error__" not in result:
                out[key] = {k: _norm(result.get(k)) for k in interesting}
            else:
                out[key] = _norm(result)

    # Explicit group/type forcing: the operator overrides the detected group.
    for group, type_, text in (
        ("pipe", "", 'ELBOW 90DEG LR BW A234 WPB 4" SCH40'),
        ("fitting", "elbow", 'PIPE CS SMLS A106 GR.B 2" SCH40'),
        ("", "", 'FLANGE WN RF 150# A105 4" SCH40'),
    ):
        result = _safe(
            process_text_record_live, text, json_dict,
            group_key_input=group, type_key_input=type_,
            remark="", revision="", clean_size="", row_index=900,
            allow_code_lookup=False, confirm_group_change=None,
            locked_group=None, locked_type=None,
        )
        key = f"forced|{group}|{type_}"
        out[key] = ({k: _norm(result.get(k)) for k in interesting}
                    if isinstance(result, dict) and "__error__" not in result
                    else _norm(result))
    return out


# ---------------------------------------------------------------------------
# Section: price / weight calculation
# ---------------------------------------------------------------------------
def snap_calculation_engine() -> dict:
    """Calculated columns for a spread of quantities, sizes and overrides.

    Money and weight arithmetic is the part of this engine a refactor is most
    likely to perturb (float vs Decimal, rounding direction, order of
    operations), so the inputs deliberately include values that round badly.
    """
    from itemcoder.calculation_engine import calculate_row_values

    quantities = ("", "0", "1", "3", "10", "2.5", "0.333", "1,234", "-5", "abc")
    sizes = ("", '2"', "50", "1/2", '8"')
    units = ("", "M", "EA", "PCS", "KG")
    overrides = (
        {},
        {"unit_price": "1000"},
        {"unit_price": "1234.567"},
        {"unit_price": "0"},
        {"unit_price": "-10"},
    )

    out = {}
    for group, type_ in (("pipe", "pipe"), ("fitting", "elbow"), ("flange", "wn"), ("valve", "gate")):
        for qty in quantities:
            for size in sizes:
                for unit in units[:2]:
                    for i, override in enumerate(overrides):
                        key = f"{group}|{type_}|{qty}|{size}|{unit}|ov{i}"
                        out[key] = _norm(_safe(
                            calculate_row_values,
                            group=group, type_=type_, code_value="",
                            qty=qty, size=size, unit=unit,
                            feature_vars={}, overrides=override,
                        ))
    return out


# ---------------------------------------------------------------------------
# Section: export totals
# ---------------------------------------------------------------------------
def snap_export_totals() -> dict:
    """Proforma subtotal / VAT / service / grand-total arithmetic.

    Rows are shaped exactly as ``build_export_rows`` emits them, including the
    soft-delete and NOT SUPPLIABLE markers that must contribute zero.
    """
    from cases.export_data import pi_totals

    def row(total, service="", qty="1", flags=None):
        base = {
            "total_price": total, "service_price": service, "qty": qty,
            "unit_price": "", "desc_client": "x", "code": "",
        }
        base.update(flags or {})
        return base

    cases = {
        "empty": [],
        "single": [row("1000")],
        "several": [row("1000"), row("2500.50"), row("0.49")],
        "with_service": [row("1000", service="50"), row("2000", service="25", qty="3")],
        "deleted_row": [row("1000"), row("9999", flags={"_deleted": "1"})],
        "not_suppliable": [row("1000"), row("8888", flags={"_unsuppliable": "1"})],
        "blank_totals": [row(""), row("   "), row("abc"), row("1000")],
        "comma_formatted": [row("1,000"), row("2,500.50")],
        "negative": [row("-500"), row("1000")],
        "big": [row("999999999999"), row("1")],
    }

    out = {}
    for name, rows in cases.items():
        for percent in (None, 0, 9, 10, 10.5):
            for currency in ("IRR", "USD", "EUR", "AED"):
                key = f"{name}|pct={percent}|{currency}"
                out[key] = _norm(_safe(pi_totals, rows, percent, currency=currency))
    return out


# ---------------------------------------------------------------------------
# Section: routing — who may do what, to which case, right now
# ---------------------------------------------------------------------------
# This is the single most important section in the file. Every entry is one
# answer to "may this person take this action on this case at this moment", and
# the whole point of the exercise is that not one of them may change.
def _build_routing_fixture():
    """Create the users and cases the routing matrix is evaluated against.

    Everything is created with fixed, meaningful values; nothing here reads the
    clock or a random source, so two runs produce the same fixture.
    """
    from django.contrib.auth.models import User

    from accounts.constants import Role, SupplyKind, Unit
    from cases.constants import CaseStatus, DocKind, FormKind, OfferType, PriceType, Side
    from cases.models import Case, CaseForm, Client

    # -- people ---------------------------------------------------------
    # One account per meaningful seat, plus a second Commercial expert so the
    # "creator vs. not the creator" distinction is exercised.
    seat_specs = [
        ("com_mgr", Unit.COMMERCIAL, Role.MANAGER, ""),
        ("com_sup", Unit.COMMERCIAL, Role.SUPERVISOR, ""),
        ("com_exp", Unit.COMMERCIAL, Role.EXPERT, ""),
        ("com_exp2", Unit.COMMERCIAL, Role.EXPERT, ""),
        ("tec_mgr", Unit.TECHNICAL, Role.MANAGER, ""),
        ("tec_sup", Unit.TECHNICAL, Role.SUPERVISOR, ""),
        ("tec_exp", Unit.TECHNICAL, Role.EXPERT, ""),
        ("sup_mgr", Unit.SUPPLY, Role.MANAGER, ""),
        ("sup_sup", Unit.SUPPLY, Role.SUPERVISOR, ""),
        ("sup_int", Unit.SUPPLY, Role.EXPERT, SupplyKind.INTERNAL),
        ("sup_ext", Unit.SUPPLY, Role.EXPERT, SupplyKind.EXTERNAL),
    ]
    users = {}
    for username, unit, role, supply_kind in seat_specs:
        user, _ = User.objects.get_or_create(username=username)
        profile = user.profile           # created by accounts.signals
        profile.is_admin = False
        profile.is_general_manager = False
        profile.unit = unit
        profile.role = role
        profile.supply_kind = supply_kind
        profile.save()
        users[username] = user

    gm, _ = User.objects.get_or_create(username="gen_mgr")
    gm.profile.is_general_manager = True
    gm.profile.unit = ""
    gm.profile.role = ""
    gm.profile.save()
    users["gen_mgr"] = gm

    client, _ = Client.objects.get_or_create(name="Snapshot Client", defaults={"code": "9001"})
    creator = users["com_exp"]

    # -- cases ----------------------------------------------------------
    # A case variant per (status x holder) pair that the workflow can actually
    # produce, crossed with the flags that change who may act: split state,
    # offer type, price type, pending approval, and which assignees are set.
    statuses = [
        CaseStatus.DRAFT, CaseStatus.WITH_TECHNICAL, CaseStatus.RETURNED_TO_COMMERCIAL,
        CaseStatus.WITH_SUPPLY,
    ]
    for extra in ("PENDING_CANCEL", "CLOSED", "CANCELLED", "BURNED",
                  "FINAL_CLOSED", "FINAL_APPROVED", "CANNOT_SUPPLY"):
        value = getattr(CaseStatus, extra, None)
        if value and value not in statuses:
            statuses.append(value)

    holders = ["", Unit.COMMERCIAL, Unit.TECHNICAL, Unit.SUPPLY]
    variants = []
    serial = 0
    for status in statuses:
        for holder in holders:
            for offer_type in (OfferType.TO, OfferType.TO_PI):
                for price_type in (PriceType.INTERNAL, PriceType.BOTH):
                    for awaiting in (False, True):
                        for assigned in (False, True):
                            serial += 1
                            split = price_type == PriceType.BOTH
                            case = Case.objects.create(
                                doc_no=f"SNAP-{serial:05d}",
                                kind=DocKind.INDENT,
                                offer_type=offer_type,
                                year_month="040301",
                                expert_code="01",
                                client=client,
                                serial=serial,
                                status=status,
                                holder_unit=holder,
                                price_type=price_type,
                                created_by=creator,
                                awaiting_approval=awaiting,
                                proposed_action="close" if awaiting else "",
                                split_active=split,
                                internal_status=status if split else "",
                                external_status=status if split else "",
                                internal_holder=holder if split else "",
                                external_holder=holder if split else "",
                                technical_assignee=users["tec_exp"] if assigned else None,
                                supply_assignee=users["sup_int"] if assigned else None,
                                supply_internal_assignee=users["sup_int"] if assigned else None,
                                supply_external_assignee=users["sup_ext"] if assigned else None,
                            )
                            # Forms drive has_to / has_pi inside allowed_actions.
                            CaseForm.objects.create(
                                case=case, kind=FormKind.INQUIRY, side="", version=1,
                                is_current=True, columns=[], table=[], meta={},
                            )
                            if status not in (CaseStatus.DRAFT,):
                                CaseForm.objects.create(
                                    case=case, kind=FormKind.TO, side="", version=1,
                                    is_current=True, columns=[], table=[], meta={},
                                )
                            if offer_type == OfferType.TO_PI and holder == Unit.SUPPLY:
                                CaseForm.objects.create(
                                    case=case, kind=FormKind.PI, side="", version=1,
                                    is_current=True, columns=[], table=[], meta={},
                                )
                            variants.append((
                                f"{status}|holder={holder or '-'}|{offer_type}|{price_type}"
                                f"|await={int(awaiting)}|assigned={int(assigned)}",
                                case,
                            ))
    return users, variants, [Side.INTERNAL, Side.EXTERNAL]


def snap_routing_allowed_actions() -> dict:
    """The full (case state x seat) permission matrix, plus the per-side gate."""
    from cases import services

    users, variants, sides = _build_routing_fixture()

    out = {
        "allowed_actions": {},
        "user_can_view_case": {},
        "can_act_on_side": {},
        "can_do_side_action": {},
    }

    for label, case in variants:
        for username in sorted(users):
            key = f"{label}||{username}"
            out["allowed_actions"][key] = _norm(
                _safe(services.allowed_actions, case, users[username]))
            # Recorded separately from allowed_actions because it is a different
            # question with a different answer: allowed_actions says what you may
            # DO, user_can_view_case says whether you may see the case at all.
            # The two were entangled once (visibility was inferred from a
            # non-empty action set, which made every case readable by any
            # authenticated user), so they are pinned independently here.
            out["user_can_view_case"][key] = _norm(
                _safe(services.user_can_view_case, case, users[username]))

    # The per-side authorisation gate used by every split case.
    side_actions = [
        "submit_to_technical", "send_to_supply", "return_to_technical",
        "send_to_commercial", "return_to_commercial", "return_to_supply",
        "close", "finalize", "burn", "assign", "request_cancel",
    ]
    for label, case in variants:
        if not case.split_active:
            continue
        for side in sides:
            for username in sorted(users):
                key = f"{label}||{side}||{username}"
                out["can_act_on_side"][key] = _norm(
                    _safe(services.can_act_on_side, case, users[username], side))
                for action in side_actions:
                    out["can_do_side_action"][f"{key}||{action}"] = _norm(
                        _safe(services.can_do_side_action, case, users[username], side, action))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
_RUNNERS = {
    "jalali": snap_jalali,
    "doc_codes": snap_doc_codes,
    "coding_engine": snap_coding_engine,
    "calculation_engine": snap_calculation_engine,
    "export_totals": snap_export_totals,
    "routing_allowed_actions": snap_routing_allowed_actions,
}


def build_snapshot(db_path: str, only: list[str] | None = None) -> dict:
    """Run every section and return the whole snapshot as a plain dict."""
    scratch = Path(tempfile.mkdtemp(prefix="ftsnap-")) / "snapshot.sqlite3"
    shutil.copyfile(db_path, scratch)
    _bootstrap(str(scratch))

    snapshot = {"__sections__": list(SECTIONS)}
    for name in SECTIONS:
        if only and name not in only:
            continue
        try:
            snapshot[name] = _norm(_RUNNERS[name]())
        except Exception:  # noqa: BLE001 - a broken section must be visible, not fatal
            snapshot[name] = {"__section_error__": traceback.format_exc(limit=6)}
    return snapshot


def compare(before_path: str, after_path: str) -> int:
    """Diff two snapshots key by key. Returns a process exit code."""
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)

    total_diffs = 0
    for section in before.get("__sections__", []):
        left, right = before.get(section), after.get(section)
        if left == right:
            print(f"  OK        {section}")
            continue
        if not isinstance(left, dict) or not isinstance(right, dict):
            print(f"  CHANGED   {section}  (whole section differs)")
            total_diffs += 1
            continue
        changed = [k for k in left if k in right and left[k] != right[k]]
        removed = [k for k in left if k not in right]
        added = [k for k in right if k not in left]
        total_diffs += len(changed) + len(removed) + len(added)
        print(f"  CHANGED   {section}: {len(changed)} changed, "
              f"{len(removed)} removed, {len(added)} added")
        for key in (changed + removed + added)[:15]:
            print(f"      - {key}")
            if key in changed:
                print(f"          before: {json.dumps(left[key], ensure_ascii=False)[:300]}")
                print(f"          after : {json.dumps(right[key], ensure_ascii=False)[:300]}")
        if len(changed + removed + added) > 15:
            print(f"      ... and {len(changed + removed + added) - 15} more")

    print()
    if total_diffs == 0:
        print("IDENTICAL - no behavioural change detected.")
        return 0
    print(f"{total_diffs} behavioural difference(s) detected.")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="SQLite database to copy and run against")
    parser.add_argument("--out", help="Where to write the snapshot JSON")
    parser.add_argument("--only", nargs="*", choices=SECTIONS,
                        help="Limit the run to these sections")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="Compare two snapshot files and exit")
    args = parser.parse_args(argv)

    if args.compare:
        return compare(*args.compare)

    if not args.db or not args.out:
        parser.error("--db and --out are required unless --compare is used")

    snapshot = build_snapshot(args.db, args.only)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1, sort_keys=True)

    counts = {
        name: (len(value) if isinstance(value, dict) else 1)
        for name, value in snapshot.items() if name != "__sections__"
    }
    print(f"Wrote {args.out}")
    for name in SECTIONS:
        print(f"  {name}: {counts.get(name, 0)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
