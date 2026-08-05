"""Shared TO/PI export row builders (Excel + PDF).

Column order and Remark/Brand/Time rules are identical for Excel and PDF:

PI columns:
  CLIENT NO., ITEM, CODE FTCO., DESCRIPTION CLIENT, REMARK,
  DESCRIPTION FTCO., SIZE, QTY, UNIT, BRAND, TIME, UNIT PRICE, TOTAL PRICE

TO columns:
  CLIENT NO., ITEM, CODE FTCO., DESCRIPTION CLIENT, REMARK,
  DESCRIPTION FTCO., SIZE, QTY, UNIT, BRAND, TIME

Remark always comes from the matching TO row (never from PI remark).
Brand/Time for TO exports are taken from the matching PI row when present.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.constants import Role, Unit
from .codes import build_export_name
from .constants import FormKind

_logger = logging.getLogger(__name__)


# Fixed export column specs: (display_title, value_key)
# value_key is resolved by ``cell_for_export``.
PI_COLUMNS: list[tuple[str, str]] = [
    ("CLIENT NO.", "client_no"),
    ("ITEM", "item"),
    ("CODE FTCO.", "code"),
    ("DESCRIPTION CLIENT", "desc_client"),
    ("REMARK", "remark"),
    ("DESCRIPTION FTCO.", "desc_ftco"),
    ("SIZE", "size"),
    ("QTY", "qty"),
    ("UNIT", "unit"),
    ("BRAND", "brand"),
    ("TIME", "time"),
    ("UNIT PRICE", "unit_price"),
    ("TOTAL PRICE", "total_price"),
]

TO_COLUMNS: list[tuple[str, str]] = [
    ("CLIENT NO.", "client_no"),
    ("ITEM", "item"),
    ("CODE FTCO.", "code"),
    ("DESCRIPTION CLIENT", "desc_client"),
    ("REMARK", "remark"),
    ("DESCRIPTION FTCO.", "desc_ftco"),
    ("SIZE", "size"),
    ("QTY", "qty"),
    ("UNIT", "unit"),
    ("BRAND", "brand"),
    ("TIME", "time"),
]

VENDOR_NAME = "Sanat Foolad Tabar Co."
PI_CODE_NO = "FT-IMS-CO-FR-001-Rev 03"
TO_CODE_NO = "FT-IMS-TE-FR-001-Rev 03"


def export_columns(form) -> list[tuple[str, str]]:
    kind = (getattr(form, "kind", "") or "").upper()
    cols = list(PI_COLUMNS if kind == FormKind.PI else TO_COLUMNS)
    if kind == FormKind.PI:
        rows = getattr(form, "table", None) or []
        has_svc = any(_meta_text((r or {}).get("_service_comment", "")) for r in rows)
        if has_svc:
            out: list[tuple[str, str]] = []
            for title, key in cols:
                out.append((title, key))
                if key == "unit_price":
                    out.append(("UNIT SVC PRICE", "service_price"))
            cols = out
    return cols


def _meta_text(val: Any) -> str:
    """Sanitize row meta (esp. pandas NaN → \"nan\") for flags / comments."""
    if val is None:
        return ""
    try:
        import math
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return ""
    except Exception:
        pass
    try:
        import pandas as pd
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    if s.lower() in ("nan", "none", "<na>", "null"):
        return ""
    return s

def export_name_for(case, form, group_suffix: str = "") -> str:
    return build_export_name(
        form_kind=form.kind, kind=case.kind, ym=case.year_month,
        expert_code=case.expert_code, client_code=case.client.code,
        serial=case.serial, version=form.version, group_suffix=group_suffix,
    )


def client_name_only(case, form=None) -> str:
    meta = (form.meta if form is not None else None) or {}
    raw = str(meta.get("CLIENT") or getattr(getattr(case, "client", None), "name", "") or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"\s*[\(\[]\s*\d+\s*[\)\]]\s*$", "", raw).strip()
    cleaned = re.sub(r"\s*[-–—]\s*\d+\s*$", "", cleaned).strip()
    return cleaned or raw


def order_no(case, form) -> str:
    meta = form.meta or {}
    return str(meta.get("ORDER NO.") or case.order_no or "").strip()


def form_date_jalali(form) -> str:
    """Last edit date if the form was saved again; otherwise creation date.

    Uses ``updated_at`` when present (auto-updated on every save). Falls back
    to ``created_at``. Jalali ``Y.m.d`` only — no time.
    """
    dt = getattr(form, "updated_at", None) or getattr(form, "created_at", None)
    if not dt:
        return ""
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    from .jalali import gregorian_to_jalali
    try:
        jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    except Exception:
        return dt.strftime("%Y-%m-%d")
    return f"{jy}.{jm:02d}.{jd:02d}"


def doc_no_export(case, form) -> str:
    """Document number shown on PDF/Excel header = export file name pattern."""
    return export_name_for(case, form)


def code_no_for(form) -> str:
    kind = (getattr(form, "kind", "") or "").upper()
    return PI_CODE_NO if kind == FormKind.PI else TO_CODE_NO


def _cell(row: dict, *keys) -> str:
    if not row:
        return ""
    for key in keys:
        if key in row and row[key] is not None and str(row[key]).strip() != "":
            return str(row[key])
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key])
    return ""


def _strip_html(value) -> str:
    import html as html_mod
    s = "" if value is None else str(value)
    if "<" not in s:
        return s
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html_mod.unescape(s).strip()


def _item_key(row: dict) -> str:
    return str(_cell(row, "Item Code", "#", "item") or "").strip()


def _index_by_item(table) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in table or []:
        key = _item_key(row)
        if key:
            out[key] = row
    return out


def unit_manager(unit_code: str):
    """Return the Profile for the manager of ``unit_code``, or None.

    ``user__is_active=True`` is the part that matters. Without it a manager
    whose access had been cut off was still chosen as the signatory of
    brand-new documents — somebody who can no longer even sign in appearing as
    the person who signed. Ending someone's access must also stop their name
    going out on new paperwork.

    The ordering is deterministic but carries no business meaning: with two
    managers in one unit it picks whichever last name sorts first, forever and
    silently. Today each unit has exactly one, so it never fires — but
    resolving the signatory from the approval record is what this has to become
    before a second holder of the same seat is possible.
    """
    from accounts.models import Profile
    return (
        Profile.objects
        .select_related("user")
        .filter(unit=unit_code, role=Role.MANAGER, is_admin=False, user__is_active=True)
        .order_by("user__last_name", "user__id")
        .first()
    )


def _ambiguous_unit_managers(unit_code: str) -> int:
    """How many active managers this unit has (1 is the expected answer)."""
    from accounts.models import Profile
    return (
        Profile.objects
        .filter(unit=unit_code, role=Role.MANAGER, is_admin=False, user__is_active=True)
        .count()
    )


def vendor_signatory(form):
    """Manager whose name/signature appear in the Vendor box.

    TO → Technical manager; PI → Commercial manager.
    """
    kind = (getattr(form, "kind", "") or "").upper()
    unit = Unit.TECHNICAL if kind == FormKind.TO else Unit.COMMERCIAL
    profile = unit_manager(unit)
    try:
        count = _ambiguous_unit_managers(unit)
        if count == 0:
            # The case that can happen today, and the worse one: cut off the
            # only manager of a unit and every document it signs goes out with
            # no name and no signature — but the seal still prints, so it looks
            # deliberate rather than broken. Nothing else reported this.
            _logger.error(
                "No active manager for unit %s: form %s will be issued with no "
                "signatory name and no signature. Assign an active manager.",
                unit, getattr(form, "pk", "?"))
        elif count > 1:
            _logger.warning(
                "Signatory for form %s is ambiguous: unit %s has %d active "
                "managers; picked user %s by last-name order.",
                getattr(form, "pk", "?"), unit, count,
                getattr(profile, "user_id", "?"))
    except Exception:
        pass
    return profile


def vendor_last_name(form) -> str:
    snap = _get_or_create_signature_snapshot(form)
    if snap is not None:
        return snap.signer_name
    profile = vendor_signatory(form)
    if not profile:
        return ""
    return profile.honorific_last_name


def vendor_signature_url(form) -> str:
    profile = vendor_signatory(form)
    if not profile or not profile.signature:
        return ""
    try:
        return profile.signature.url
    except Exception:
        return ""


def vendor_stamp_url(form) -> str:
    """Unit stamp for this export: PI → Commercial, TO → Technical."""
    field = _unit_stamp_field(form)
    if not field:
        return ""
    try:
        return field.url
    except Exception:
        return ""


def _stamp_unit_for_form(form) -> str:
    """Which working unit's seal belongs on this form's exports."""
    kind = (getattr(form, "kind", "") or "").upper()
    if kind == FormKind.TO:
        return Unit.TECHNICAL
    # PI and anything else that stamps commercially (proforma).
    return Unit.COMMERCIAL


def _unit_stamp_field(form):
    """Live FileField for the unit stamp that should appear on this form."""
    from accounts.models import PlatformConfig

    unit = _stamp_unit_for_form(form)
    cfg = PlatformConfig.load()
    field = cfg.stamp_for_unit(unit)
    if field:
        return field
    # Legacy fallback: single admin Profile.stamp (pre–per-unit stamps).
    if unit == Unit.COMMERCIAL:
        profile = _admin_stamp_profile()
        if profile and getattr(profile, "stamp", None):
            return profile.stamp
    return None


def _admin_stamp_profile():
    """First active admin that has uploaded a legacy company stamp."""
    from accounts.models import Profile
    for profile in (
        Profile.objects.filter(is_admin=True, user__is_active=True)
        .select_related("user")
        .order_by("user_id")
    ):
        if profile.stamp:
            return profile
    return None


def _file_to_light_data_uri(path, *, max_w: int = 340, max_h: int = 120) -> str:
    """Embed signature/stamp as a transparent PNG (or SVG) data-URI.

    Keeps alpha (no white background flatten) so handwritten signatures and
    stamps stay sharp and do not cover each other with an opaque box. Sized to
    display dimensions so PDF XObjects stay modest.
    """
    import base64
    import io
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return ""
    # SVG seals: pass through as-is (Chrome/WeasyPrint render them in <img>).
    if p.suffix.lower() == ".svg":
        try:
            raw = p.read_bytes()
            if len(raw) > 200_000:
                return ""
            return f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            return ""
    try:
        from PIL import Image
        with Image.open(p) as src:
            had_alpha = src.mode in ("RGBA", "LA", "PA")
            im = src.convert("RGBA") if src.mode != "RGBA" else src.copy()
            im.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            # Legacy JPEG/opaque uploads: knock out near-white so they don't
            # paint a solid box over the stamp/signature pair.
            if not had_alpha:
                pixels = im.load()
                w, h = im.size
                for y in range(h):
                    for x in range(w):
                        r, g, b, a = pixels[x, y]
                        if r > 245 and g > 245 and b > 245:
                            pixels[x, y] = (r, g, b, 0)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            raw = buf.getvalue()
            # Soft cap: if still huge, re-encode at a smaller size.
            if len(raw) > 120_000:
                im.thumbnail((max(80, max_w // 2), max(40, max_h // 2)), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG", optimize=True)
                raw = buf.getvalue()
            return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception:
        try:
            raw = p.read_bytes()
            if len(raw) > 120_000 or not str(p).lower().endswith(".png"):
                return ""
            return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            return ""


def _get_or_create_signature_snapshot(form):
    """The frozen signatory for this exact form version, creating it on first
    use if this is the very first time this version is being exported.

    Business rule for *who* signs is untouched (still vendor_signatory: the
    current holder of the relevant manager position) — this only decides
    whether that lookup happens fresh every time (the old behaviour, which
    let a later management change silently rewrite an already-issued
    document) or is captured once and reused (the fix). Returns None only
    when there is no manager to freeze in the first place, in which case
    callers fall back to the same live/empty behaviour as before.
    """
    from .models import SignatureSnapshot

    existing = getattr(form, "signature_snapshot", None)
    if existing is not None:
        return existing

    profile = vendor_signatory(form)
    if profile is None:
        return None

    # honorific_last_name is "Mr. Bayati" / "Ms. Rahim" when gender is set,
    # or the bare last name when it isn't (Profile.gender is a new, optional
    # field — nothing forces every existing account to have it set, and an
    # unset gender must keep showing exactly what every export showed before
    # this feature existed, not something newly wrong).
    name = profile.honorific_last_name
    # Freeze the seat as text too. A pointer to the live profile would restate
    # the signer's rank on every past document the moment they are promoted or
    # moved — the same failure the frozen name and image already prevent.
    snap = SignatureSnapshot(
        form=form, signer_user=profile.user, signer_name=name,
        signer_title=(profile.title_line or ""))

    if profile.signature:
        try:
            import os
            with profile.signature.open("rb") as fh:
                snap.signature_image.save(
                    os.path.basename(profile.signature.name), fh, save=False)
        except Exception:
            pass

    stamp_field = _unit_stamp_field(form)
    if stamp_field is not None:
        try:
            import os
            with stamp_field.open("rb") as fh:
                snap.stamp_image.save(
                    os.path.basename(stamp_field.name), fh, save=False)
        except Exception:
            pass

    try:
        # The savepoint matters and is not decoration. This function is now
        # also reached from inside the workflow's atomic blocks (a form leaving
        # its unit freezes its signatory). On PostgreSQL, any failed statement
        # inside an open transaction marks it for rollback, and catching the
        # error does not undo that — the caller's next statement then dies with
        # TransactionManagementError and the handoff the user just performed
        # returns a server error.
        with transaction.atomic():
            snap.save()
    except Exception:
        # Most likely a concurrent export created it a moment ago — use that.
        snap = SignatureSnapshot.objects.filter(form=form).first()
    else:
        # Warm the reverse-relation cache on this same in-memory `form` so the
        # other two vendor_* calls in this same export (name / signature /
        # stamp all run against the same form instance) reuse it instead of
        # each re-querying or re-creating.
        try:
            form.signature_snapshot = snap
        except Exception:
            pass
    return snap


def freeze_signature_snapshot(form):
    """Capture the signatory for ``form`` now, at the moment it is issued.

    Until this existed the snapshot was only created by the export path — the
    first time somebody pressed print. Between a document being issued and
    somebody printing it, a change of manager silently reattributed it to the
    new manager, who may never have touched it. Export keeps its own lazy
    creation as a fallback, so anything issued before this change still freezes
    correctly on first print rather than staying live for ever.

    Never raises: a signature snapshot must not be able to fail a workflow
    action the user just performed.
    """
    try:
        return _get_or_create_signature_snapshot(form)
    except Exception:
        _logger.exception("Could not freeze signature snapshot for form %s",
                          getattr(form, "pk", "?"))
        return None


def vendor_title(form) -> str:
    """The signer's seat as frozen at signing time ("Technical · Manager").

    Empty for snapshots taken before the title was captured, and for documents
    not yet frozen — in both cases the block renders as it always did, with the
    name alone.
    """
    snap = _get_or_create_signature_snapshot(form)
    return (snap.signer_title or "") if snap is not None else ""


def vendor_signature_data_uri(form) -> str:
    """Embed signature as data-URI so headless Chrome can print without auth."""
    from pathlib import Path

    snap = _get_or_create_signature_snapshot(form)
    if snap is not None:
        if not snap.signature_image:
            return ""
        try:
            return _file_to_light_data_uri(Path(snap.signature_image.path), max_w=340, max_h=120)
        except Exception:
            return ""
    profile = vendor_signatory(form)
    if not profile or not profile.signature:
        return ""
    try:
        return _file_to_light_data_uri(Path(profile.signature.path), max_w=340, max_h=120)
    except Exception:
        return ""


def vendor_stamp_data_uri(form) -> str:
    """Embed the unit stamp (Commercial for PI, Technical for TO) as a data-URI."""
    from pathlib import Path

    snap = _get_or_create_signature_snapshot(form)
    if snap is not None:
        if not snap.stamp_image:
            return ""
        try:
            return _file_to_light_data_uri(Path(snap.stamp_image.path), max_w=420, max_h=240)
        except Exception:
            return ""
    field = _unit_stamp_field(form)
    if not field:
        return ""
    try:
        return _file_to_light_data_uri(Path(field.path), max_w=420, max_h=240)
    except Exception:
        return ""


def build_export_rows(case, form) -> list[dict[str, str]]:
    """Build normalized export row dicts for Excel/PDF.

    Soft-deleted inquiry/TO/PI rows (``_deleted=1``) are excluded. Surviving
    rows keep their client ``#`` and get a fresh sequential Item No. Flag
    labels are carried for row tinting in exports.
    """
    from . import exports as _exports  # remark alias helpers live in exports

    kind = (form.kind or "").upper()
    side = getattr(form, "side", "") or ""
    to_form = form if kind == FormKind.TO else case.current_form(FormKind.TO, side)
    pi_form = form if kind == FormKind.PI else case.current_form(FormKind.PI, side)

    to_index = _index_by_item(getattr(to_form, "table", None) if to_form else [])
    pi_index = _index_by_item(getattr(pi_form, "table", None) if pi_form else [])

    rows_out: list[dict[str, str]] = []
    item_no = 0
    for row in form.table or []:
        if str((row or {}).get("_deleted", "") or "") == "1":
            continue
        item_no += 1
        item = _item_key(row)
        to_row = to_index.get(item) or (row if kind == FormKind.TO else {})
        pi_row = pi_index.get(item) or (row if kind == FormKind.PI else {})

        # Prefer the source form's own row for shared fields; fall back sensibly.
        src = row
        group = _cell(to_row or src, "Group")
        type_key = _cell(to_row or src, "Type")
        raw_remark = _strip_html(_cell(to_row, "ریمارک"))
        remark = ""
        if raw_remark:
            remark = _exports._remark_from_aliases(raw_remark, group, type_key) or raw_remark

        desc_ftco = _strip_html(_cell(src, "Final Arranged Text"))
        is_issue = str((src or {}).get("_issue", "") or "") == "1"
        is_unsup = str((src or {}).get("_unsuppliable", "") or "") == "1"
        svc_comment = _meta_text((src or {}).get("_service_comment", ""))
        has_service = bool(svc_comment)
        flag_label = ""
        if is_issue:
            flag_label = "TECHNICAL PROBLEM"
        elif is_unsup:
            flag_label = "NOT SUPPLIABLE"
        elif has_service:
            flag_label = "SERVICE"

        if kind == FormKind.TO:
            brand = _strip_html(_cell(src, "BRAND"))
            # Intentional empty New on a brand split must stay blank in exports
            # (do not fall back to the PI brand).
            if not brand and str((src or {}).get("_brand_split", "") or "") != "1":
                brand = _strip_html(_cell(pi_row, "BRAND"))
        else:
            brand = _strip_html(_cell(pi_row, "BRAND"))
            if not brand and str((pi_row or {}).get("_brand_split", "") or "") != "1":
                brand = _strip_html(_cell(to_row, "BRAND"))

        unit_price_raw = _strip_html(_cell(pi_row if kind == FormKind.PI else src, "UNIT PRICE"))
        total_price_raw = _strip_html(_cell(pi_row if kind == FormKind.PI else src, "TOTAL PRICE"))
        svc_raw = ""
        if has_service and kind == FormKind.PI:
            svc_raw = (
                _strip_html(_cell(pi_row, "SERVICE PRICE"))
                or _meta_text((pi_row or {}).get("_service_price_raw", ""))
            )

        cur = form_currency(form, case) if kind == FormKind.PI else "rial"
        rows_out.append({
            "client_no": _strip_html(_cell(src, "#", "Item Code")),
            "item": str(item_no),
            "code": _strip_html(_cell(src, "کد")),
            "desc_client": _strip_html(_cell(src, "description", "Description")),
            "remark": remark,
            "desc_ftco": desc_ftco,
            "size": _strip_html(_cell(src, "size")),
            "qty": _strip_html(_cell(src, "qty")),
            "unit": _strip_html(_cell(src, "unit")),
            "brand": brand,
            "time": _strip_html(_cell(pi_row, "TIME")),
            "unit_price": (
                format_pi_money(unit_price_raw, cur) if kind == FormKind.PI else unit_price_raw
            ),
            "service_price": (
                format_pi_money(svc_raw, cur) if (kind == FormKind.PI and svc_raw) else svc_raw
            ),
            "total_price": (
                format_pi_money(total_price_raw, cur) if kind == FormKind.PI else total_price_raw
            ),
            "_group": _strip_html(_cell(src, "Group", "group")) or "general",
            "_issue": "1" if is_issue else "",
            "_unsuppliable": "1" if is_unsup else "",
            "_flag_label": flag_label,
            "_issue_reason": _meta_text((src or {}).get("_issue_reason", "")),
            "_service_comment": svc_comment,
        })
    return rows_out


def technical_problem_export_rows(form) -> list[dict[str, str]]:
    """Rows for the Technical Problems sheet (before Terms)."""
    out = []
    for row in getattr(form, "table", None) or []:
        if str((row or {}).get("_deleted", "") or "") == "1":
            continue
        if str((row or {}).get("_issue", "") or "") != "1":
            continue
        reason = _meta_text((row or {}).get("_issue_reason", ""))
        out.append({
            "client_no": _strip_html(_cell(row, "#", "Item Code")),
            "item": _strip_html(_cell(row, "Item Code", "#")),
            "reason": reason or "—",
            "desc_client": _strip_html(_cell(row, "description", "Description")),
        })
    return out


def service_price_export_rows(form, case=None) -> list[dict[str, str]]:
    """Rows for the Services sheet (PI only, before Terms).

    Columns: CLIENT ITEM, FTCO ITEM, DESCRIPTION CLIENT, SERVICE COMMENT,
    QTY, UNIT PRICE SERVICE, TOTAL PRICE SERVICE.
    """
    cur = form_currency(form, case)
    out = []
    for row in getattr(form, "table", None) or []:
        if str((row or {}).get("_deleted", "") or "") == "1":
            continue
        if str((row or {}).get("_unsuppliable", "") or "") == "1":
            continue
        comment = _meta_text((row or {}).get("_service_comment", ""))
        if not comment:
            continue
        unit_raw = (
            _strip_html(_cell(row, "SERVICE PRICE"))
            or _meta_text((row or {}).get("_service_price_raw", ""))
        )
        unit_n = parse_money(unit_raw)
        qty_raw = _strip_html(_cell(row, "qty"))
        qty_n = parse_money(qty_raw)
        total_n = unit_n * qty_n
        out.append({
            "client_no": _strip_html(_cell(row, "#", "Item Code")),
            "item": _strip_html(_cell(row, "Item Code", "#")),
            "desc_client": _strip_html(_cell(row, "description", "Description")),
            "comment": comment,
            "qty": qty_raw,
            "unit_svc_price": format_pi_money(unit_n, cur) if unit_raw else "",
            "total_svc_price": format_pi_money(total_n, cur) if (unit_raw or qty_raw) else "",
            # Legacy keys kept for older Excel helpers.
            "service_price": format_pi_money(unit_n, cur) if unit_raw else "",
        })
    return out


def row_values(row: dict[str, str], columns: list[tuple[str, str]]) -> list[str]:
    return [str(row.get(key, "") or "") for _title, key in columns]


def parse_money(value: Any) -> float:
    s = re.sub(r"[^\d.\-]", "", str(value or "").replace(",", ""))
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def normalize_currency(code: str | None, *, external: bool = False) -> str:
    """Return canonical currency key (rial / usd / eur / other FX codes)."""
    c = (code or "").strip().lower()
    if c in ("usd", "$", "dollar", "dollars"):
        return "usd"
    if c in ("eur", "€", "euro", "euros"):
        return "eur"
    if c in ("rial", "irr", "ریال"):
        return "rial"
    # Pass through other ISO-like codes used on the FX board (gbp, aed, …).
    if c and re.fullmatch(r"[a-z]{3}", c) and c != "irr":
        return c
    return "usd" if external else "rial"


def currency_label(code: str | None, *, external: bool = False) -> str:
    """Display label matching the PI tool (Rial / $ / € / SYMBOL)."""
    c = normalize_currency(code, external=external)
    if c == "usd":
        return "$"
    if c == "eur":
        return "€"
    if c == "rial":
        return "Rial"
    try:
        from .fx_rates import list_rates
        for row in list_rates():
            if row.code == c:
                return (row.symbol or row.code.upper()).strip() or c.upper()
    except Exception:
        pass
    return c.upper()


def currency_export_suffix(code: str | None, *, external: bool = False) -> str:
    """Suffix used on PDF/Excel total cards (IRR / USD / EUR / CODE)."""
    c = normalize_currency(code, external=external)
    return {"usd": "USD", "eur": "EUR", "rial": "IRR"}.get(c, c.upper())


def pi_rate_note(form) -> str:
    """Applied conversion rate for a PI version, formatted with the FROM unit.

    Returns e.g. ``"1,700,000 Rial"`` (3-digit grouped) when this Proforma
    version carried a real currency conversion (rate ≠ 1); empty otherwise.
    Reads the Commercial change-currency fields first, then falls back to the
    PI tool's saved ``meta.calc`` conversion.
    """
    meta = (getattr(form, "meta", None) or {}) if form is not None else {}
    rate = meta.get("currency_rate")
    frm = meta.get("currency_from")
    if rate in (None, "", 0):
        calc = meta.get("calc") or {}
        if isinstance(calc, dict):
            r, f, t = calc.get("rate"), calc.get("from"), calc.get("to")
            if r not in (None, "", 0) and f and t and str(f) != str(t):
                rate, frm = r, f
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return ""
    if not rate or abs(rate - 1.0) < 1e-9:
        return ""
    label = currency_label(frm)
    if abs(rate - round(rate)) < 1e-9:
        return f"{int(round(rate)):,} {label}"
    return f"{rate:,.3f} {label}"


def format_pi_money(value: Any, currency: str | None = None, *, external: bool = False) -> str:
    """Format a price for PI display: number + unit (e.g. '1,234 Rial' / '12.50 $')."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    n = parse_money(raw)
    if n == 0 and not re.search(r"\d", raw):
        return ""
    cur = normalize_currency(currency, external=external)
    label = currency_label(cur, external=external)
    if cur == "rial":
        return f"{n:,.0f} {label}"
    return f"{n:,.2f} {label}"


def form_currency(form, case=None) -> str:
    """Resolve the PI form's currency from meta, falling back by side/case."""
    from .constants import PriceType, Side

    meta = (getattr(form, "meta", None) or {}) if form is not None else {}
    stored = meta.get("currency")
    side = getattr(form, "side", "") or ""
    external = (
        side == Side.EXTERNAL
        or (not side and case is not None
            and getattr(case, "price_type", "") == PriceType.EXTERNAL)
    )
    return normalize_currency(stored, external=external)


def vat_percent() -> float:
    """Platform VAT % (admin-editable). Defaults to 10."""
    try:
        from accounts.models import PlatformConfig
        value = PlatformConfig.load().vat_percent
        return float(value) if value is not None else 10.0
    except Exception:
        return 10.0


def pi_totals(rows: list[dict[str, str]], percent: float | None = None,
              *, currency: str = "IRR") -> dict[str, str]:
    """Subtotal = Σ Total Price; VAT = Subtotal × %;
    Total Service = Σ (unit service × qty); Grand = Subtotal + VAT + Service.

    NOT SUPPLIABLE (and soft-deleted) rows contribute 0 to every total — same
    rule as the live PI strip.
    """
    if percent is None:
        percent = vat_percent()

    def _active(r: dict) -> bool:
        if str((r or {}).get("_deleted", "") or "") == "1":
            return False
        if str((r or {}).get("_unsuppliable", "") or "") == "1":
            return False
        return True

    active = [r for r in rows if _active(r or {})]
    subtotal = sum(parse_money(r.get("total_price")) for r in active)
    service = 0.0
    for r in active:
        if not _meta_text((r or {}).get("_service_comment")):
            continue
        unit = parse_money((r or {}).get("service_price"))
        qty = parse_money((r or {}).get("qty"))
        service += unit * qty
    vat = subtotal * (float(percent) / 100.0)
    grand = subtotal + vat + service
    # Accept both export suffixes (IRR/USD/EUR) and form keys (rial/usd/eur).
    cur_key = normalize_currency(currency, external=(currency or "").upper() in {"USD", "EUR", "$", "€"})
    use_dec = cur_key in {"usd", "eur"}

    def fmt(n: float) -> str:
        if use_dec:
            return f"{n:,.2f}"
        return f"{n:,.0f}"

    suffix = currency_export_suffix(cur_key)
    return {
        "subtotal": fmt(subtotal),
        "vat": fmt(vat),
        "total_service": fmt(service),
        "grand_total": fmt(grand),
        "vat_percent": percent,
        "vat_label": f"VAT ({percent:g}%)",
        "currency": suffix,
        "currency_suffix": f" {suffix}",
        "grand_total_num": grand,
        "subtotal_num": subtotal,
        "total_service_num": service,
        "has_service": service > 0 or any(_meta_text((r or {}).get("_service_comment")) for r in active),
    }


def case_pi_grand_total_num(case) -> float:
    """VAT-inclusive grand total from every current Proforma on the case.

    Soft-deleted and not-suppliable rows are skipped. Returns 0.0 when there is
    no priced PI yet.
    """
    from .constants import FormKind
    from .models import CaseForm

    total = 0.0
    forms = CaseForm.objects.filter(
        case_id=getattr(case, "pk", case), kind=FormKind.PI, is_current=True,
    )
    for form in forms:
        for row in (form.table or []):
            r = row or {}
            if str(r.get("_deleted", "") or "") == "1":
                continue
            if str(r.get("_unsuppliable", "") or "") == "1":
                continue
            total += parse_money(r.get("TOTAL PRICE") or r.get("total_price") or "")
            if _meta_text(r.get("_service_comment")):
                qty = parse_money(r.get("qty") or "")
                unit = parse_money(
                    r.get("SERVICE PRICE") or r.get("_service_price_raw") or ""
                )
                total += unit * qty
    if total <= 0:
        return 0.0
    return total * (1.0 + float(vat_percent()) / 100.0)


def case_pi_grand_totals_map(case_ids) -> dict:
    """Map case_id → VAT-inclusive grand total for a batch of cases."""
    from collections import defaultdict
    from .constants import FormKind
    from .models import CaseForm

    ids = [int(x) for x in (case_ids or []) if x]
    if not ids:
        return {}
    by_case = defaultdict(float)
    for form in CaseForm.objects.filter(
        case_id__in=ids, kind=FormKind.PI, is_current=True,
    ).only("case_id", "table"):
        for row in (form.table or []):
            r = row or {}
            if str(r.get("_deleted", "") or "") == "1":
                continue
            if str(r.get("_unsuppliable", "") or "") == "1":
                continue
            by_case[form.case_id] += parse_money(
                r.get("TOTAL PRICE") or r.get("total_price") or "")
            if _meta_text(r.get("_service_comment")):
                qty = parse_money(r.get("qty") or "")
                unit = parse_money(
                    r.get("SERVICE PRICE") or r.get("_service_price_raw") or ""
                )
                by_case[form.case_id] += unit * qty
    factor = 1.0 + float(vat_percent()) / 100.0
    return {cid: amt * factor for cid, amt in by_case.items() if amt > 0}


def format_money_amount(n: float, *, currency: str = "IRR") -> str:
    """Format a numeric amount for dashboard / archive display."""
    cur_key = normalize_currency(currency, external=False)
    if cur_key in {"usd", "eur"}:
        return f"{n:,.2f}"
    return f"{n:,.0f}"


# ---------------------------------------------------------------------------
# Default bilingual Terms (reset on every PDF export — never persisted)
# ---------------------------------------------------------------------------
DEFAULT_PI_TERMS = {
    "intro_en": "The following conditions form an integral part of this document.",
    "intro_fa": "شرایط زیر جزء لاینفک این سند محسوب می‌شود.",
    "categories": [
        {
            "title_en": "Offer Basis (FTCO DISCRIPTION)",
            "title_fa": "مبنای پیشنهاد (FTCO DISCRIPTION)",
            "full": True,
            "items_en": [
                "The basis for the delivery of goods and for this technical & financial offer is the FTCO DISCRIPTION column; by approving the technical & financial offer, the esteemed client hereby confirms these goods.",
            ],
            "items_fa": [
                "مبنای تحویل کالا و پیشنهاد فنی و مالی، ستون (FTCO DISCRIPTION) می‌باشد و کارفرمای محترم با تأیید پیشنهاد فنی و مالی، این کالا را تأیید می‌نماید.",
            ],
        },
        {
            "title_en": "Validity & Order Confirmation",
            "title_fa": "اعتبار و تأیید سفارش",
            "items_en": [
                "Validity: This document shall be valid for 25 days from the date of issue.",
                "Any changes to specs, quantities, or delivery location shall be subject to review and execution of a new revision document.",
                "Supply capabilities are subject to final confirmation at order placement.",
            ],
            "items_fa": [
                "اعتبار: این سند از تاریخ صدور به مدت ۲۵ روز معتبر است.",
                "هرگونه تغییر در مشخصات، مقادیر یا محل تحویل منوط به بررسی و صدور نسخه اصلاحی جدید است.",
                "توانایی تأمین منوط به تأیید نهایی در زمان ثبت سفارش است.",
            ],
        },
        {
            "title_en": "Payment Terms",
            "title_fa": "شرایط پرداخت",
            "items_en": [
                "Buyer shall pay 25% of total value as advance allocation within 10 working days.",
                "Procurement timeline starts strictly upon receipt of confirmed payment allocation logs.",
                "Remaining balance shall be fully settled prior to dispatch authorization routines.",
            ],
            "items_fa": [
                "خریدار موظف است ۲۵٪ از مبلغ کل را ظرف ۱۰ روز کاری به‌عنوان پیش‌پرداخت پرداخت نماید.",
                "زمان‌بندی تأمین صرفاً پس از دریافت تأییدیه تخصیص پرداخت آغاز می‌شود.",
                "مانده مبلغ باید پیش از مجوز ارسال به‌طور کامل تسویه شود.",
            ],
        },
        {
            "title_en": "Delivery & Readiness",
            "title_fa": "تحویل و آمادگی",
            "items_en": [
                "Goods shall be ready within 20 working days following full advance settlement routines.",
                "Stated duration covers factory warehouse layout readiness only, excluding client inspection periods.",
            ],
            "items_fa": [
                "کالا ظرف ۲۰ روز کاری پس از تسویه کامل پیش‌پرداخت آماده خواهد شد.",
                "مدت اعلام‌شده صرفاً آمادگی در انبار کارخانه را پوشش می‌دهد و شامل زمان بازرسی کارفرما نیست.",
            ],
        },
        {
            "title_en": "Inspection & Acceptance",
            "title_fa": "بازرسی و پذیرش",
            "items_en": [
                "The Buyer shall execute technical validation inspections within 10 working days from notification.",
                "Absence of inspection arrangement within the window constitutes absolute automated material acceptance.",
            ],
            "items_fa": [
                "خریدار باید ظرف ۱۰ روز کاری از اعلام آمادگی، بازرسی فنی را انجام دهد.",
                "عدم هماهنگی بازرسی در مهلت مقرر به‌منزله پذیرش خودکار کالا است.",
            ],
        },
        {
            "title_en": "Warranty & Ownership",
            "title_fa": "گارانتی و مالکیت",
            "items_en": [
                "Standard equipment is covered under a 24-month manufacturer defect warranty framework.",
                "Unconditional title and legal property ownership remain with the Seller until 100% financial clearance.",
            ],
            "items_fa": [
                "تجهیزات استاندارد تحت گارانتی ۲۴ ماهه عیوب ساخت سازنده قرار دارند.",
                "مالکیت قانونی کالا تا تسویه ۱۰۰٪ مبلغ نزد فروشنده باقی می‌ماند.",
            ],
        },
        {
            "title_en": "Costs, Taxes & Official Invoice",
            "title_fa": "هزینه‌ها، مالیات و فاکتور رسمی",
            "items_en": [
                "Testing, logistic layout loading, and transit insurance parameters remain allocated to the Buyer.",
                "Prices are exclusive of Value Added Tax (VAT) allocations; tax elements are added inside corporate platforms.",
            ],
            "items_fa": [
                "هزینه آزمایش، بارگیری و بیمه حمل بر عهده خریدار است.",
                "قیمت‌ها بدون احتساب مالیات بر ارزش افزوده است؛ مالیات در سامانه رسمی اضافه می‌شود.",
            ],
        },
        {
            "title_en": "Legal Status & Final Acceptance",
            "title_fa": "وضعیت حقوقی و پذیرش نهایی",
            "full": True,
            "items_en": [
                "This layout structure does not create an unconditional commitment. Binding execution relies entirely on formal Purchase Orders (PO).",
            ],
            "items_fa": [
                "این سند به‌تنهایی تعهد قطعی ایجاد نمی‌کند. اجرای الزام‌آور منوط به صدور سفارش خرید رسمی (PO) است.",
            ],
        },
    ],
}

# Backwards-compatible alias used by older call sites.
DEFAULT_TERMS = DEFAULT_PI_TERMS

DEFAULT_TO_TERMS = {
    "intro_en": "The following technical assumptions, clarifications and exclusions form an integral part of this Technical Offer.",
    "intro_fa": "فرضیات فنی، توضیحات و استثناهای زیر جزء لاینفک این پیشنهاد فنی می‌باشند.",
    "categories": [
        {
            "title_en": "Offer Basis (FTCO DISCRIPTION)",
            "title_fa": "مبنای پیشنهاد (FTCO DISCRIPTION)",
            "full": True,
            "items_en": [
                "The basis for the delivery of goods and for this technical & financial offer is the FTCO DISCRIPTION column; by approving the technical & financial offer, the esteemed client hereby confirms these goods.",
            ],
            "items_fa": [
                "مبنای تحویل کالا و پیشنهاد فنی و مالی، ستون (FTCO DISCRIPTION) می‌باشد و کارفرمای محترم با تأیید پیشنهاد فنی و مالی، این کالا را تأیید می‌نماید.",
            ],
        },
        {
            "title_en": "Packaging & Handling",
            "title_fa": "بسته‌بندی و حمل",
            "items_en": [
                "All items shall be packed in accordance with applicable standards and in a manner that ensures protection of the goods during transportation, handling, and storage.",
            ],
            "items_fa": [
                "کلیه اقلام مطابق با استانداردهای مربوطه و به‌گونه‌ای بسته‌بندی خواهند شد که از سلامت کالا در طول حمل، تخلیه و انبارش اطمینان حاصل گردد.",
            ],
        },
        {
            "title_en": "Delivery Location & Schedule",
            "title_fa": "محل و زمان‌بندی تحویل",
            "items_en": [
                "Delivery Location: Foulad Tabar Company Warehouse.",
                "Delivery schedules are based on the international calendar. The lead times stated in this offer do not include the Purchaser's inspection, quality control, or approval procedures.",
            ],
            "items_fa": [
                "محل تحویل کالا درب انبار شرکت فولاد تبار.",
                "زمان‌بندی تحویل بر اساس تقویم بین‌المللی محاسبه شده و زمان‌های اعلام‌شده در این پیشنهاد شامل فرآیندهای بازرسی، کنترل کیفیت و تأییدات کارفرما نمی‌باشد.",
            ],
        },
        {
            "title_en": "Stock Availability",
            "title_fa": "موجودی انبار",
            "items_en": [
                "For items available in stock, delivery shall be within 5 to 10 working days. Reported stock availability is valid for 4 working days only. In case of stock unavailability, delivery shall be subject to manufacturing and procurement lead times.",
            ],
            "items_fa": [
                "در خصوص اقلام موجود در انبار، زمان تحویل ۵ الی ۱۰ روز کاری بوده و موجودی اعلام‌شده حداکثر به مدت ۴ روز کاری معتبر است. در صورت اتمام موجودی، زمان تحویل بر اساس برنامه ساخت و تأمین اعلام خواهد شد.",
            ],
        },
        {
            "title_en": "Pipe Supply Basis",
            "title_fa": "مبنای تأمین لوله",
            "items_en": [
                "Pipes shall be calculated and supplied based on the standard length of 6 meters.",
            ],
            "items_fa": [
                "لوله‌ها بر مبنای طول استاندارد ۶ متر محاسبه و تحویل می‌گردند.",
            ],
        },
        {
            "title_en": "Certificates & Documentation",
            "title_fa": "گواهینامه‌ها و مستندات",
            "items_en": [
                "Technical certificates, material certificates, and relevant documentation shall be submitted to the Purchaser's Quality Control Department during inspection.",
                "Certificate of Origin (COO), manufacturer documentation, and other relevant technical documents shall be provided upon the Purchaser's request, where applicable.",
            ],
            "items_fa": [
                "گواهینامه‌های فنی، سرتیفیکیت مواد و مستندات مربوطه در زمان بازرسی کالا به واحد کنترل کیفیت کارفرما ارائه خواهد شد.",
                "گواهی کشور سازنده (COO)، مدارک تولیدکننده و سایر مستندات فنی مرتبط، حسب درخواست کارفرما و در صورت قابلیت ارائه، تأمین خواهد شد.",
            ],
        },
        {
            "title_en": "Availability, Deviations & Approval",
            "title_fa": "موجودی، مغایرت‌ها و تأیید کارفرما",
            "items_en": [
                "The availability and supply capability of the proposed items are subject to final confirmation at the time of order placement.",
                "Any deviation from the requested technical specifications, standards, or requirements has been clearly identified in this offer and shall be subject to the Purchaser's approval.",
            ],
            "items_fa": [
                "موجودی و قابلیت تأمین اقلام پیشنهادی منوط به تأیید نهایی در زمان ثبت سفارش می‌باشد.",
                "هرگونه مغایرت یا انحراف احتمالی از مشخصات فنی، استانداردها یا شرایط مندرج در استعلام، به‌صورت شفاف در پیشنهاد اعلام شده و منوط به تأیید کارفرما خواهد بود.",
            ],
        },
        {
            "title_en": "Technical Compliance",
            "title_fa": "انطباق فنی",
            "full": True,
            "items_en": [
                "All offered items comply with the technical specifications and standards stated in the technical data sheets and schedules.",
            ],
            "items_fa": [
                "کلیه اقلام ارائه‌شده در این پیشنهاد مطابق با مشخصات فنی و استانداردهای درج‌شده در جداول فنی می‌باشند.",
            ],
        },
    ],
}


def default_terms_for(kind: str) -> dict:
    kind_u = (kind or "").upper()
    src = DEFAULT_TO_TERMS if kind_u == FormKind.TO else DEFAULT_PI_TERMS
    return {
        "intro_en": src["intro_en"],
        "intro_fa": src["intro_fa"],
        "categories": [dict(c) for c in src["categories"]],
    }


def normalize_terms(payload: dict | None, kind: str = "PI") -> dict:
    """Merge posted terms with kind-specific defaults; never persist across exports."""
    defaults = default_terms_for(kind)
    base = {
        "intro_en": defaults["intro_en"],
        "intro_fa": defaults["intro_fa"],
        "categories": [],
    }
    src = payload or {}
    base["intro_en"] = str(src.get("intro_en") or defaults["intro_en"]).strip()
    base["intro_fa"] = str(src.get("intro_fa") or defaults["intro_fa"]).strip()

    posted_cats = src.get("categories")
    default_cats = defaults["categories"]
    if not isinstance(posted_cats, list) or not posted_cats:
        base["categories"] = [dict(c) for c in default_cats]
        return base

    cats = []
    for i, default in enumerate(default_cats):
        posted = posted_cats[i] if i < len(posted_cats) and isinstance(posted_cats[i], dict) else {}
        items_en = posted.get("items_en")
        items_fa = posted.get("items_fa")
        if not isinstance(items_en, list):
            items_en = default["items_en"]
        if not isinstance(items_fa, list):
            items_fa = default["items_fa"]
        cats.append({
            "title_en": str(posted.get("title_en") or default["title_en"]).strip(),
            "title_fa": str(posted.get("title_fa") or default["title_fa"]).strip(),
            "full": bool(default.get("full")),
            "items_en": [str(x).strip() for x in items_en if str(x).strip()],
            "items_fa": [str(x).strip() for x in items_fa if str(x).strip()],
        })
    base["categories"] = cats
    return base
