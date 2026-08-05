"""Bridge between a workflow case and the vendored item-coding tool.

The original tool (this app) is used **unchanged**: it still reads its CSV/JSON
resources directly and runs the exact same coding/pricing logic. These two views
only (a) feed a case's inquiry rows into the tool's own Excel pipeline so the
grid opens pre-filled, and (b) take the finished grid back and store it as a
versioned Technical Offer / Proforma on the case.

Nothing here changes the coding or calculation behaviour.
"""
import json
import logging
from html.parser import HTMLParser

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .calculation_customizer import get_calculation_ui_config


PRICE_COLUMNS = {"UNIT PRICE", "SERVICE PRICE", "TOTAL PRICE"}


def mask_price_columns(html: str, columns=PRICE_COLUMNS) -> str:
    """Replace every <td data-col-name="X">...</td> for X in `columns` with an
    empty <td data-col-name="X"></td> — used to keep Technical unit users
    from ever receiving a financial figure in the PI tool page's HTML at all
    (not just visually hidden by CSS, which a browser's dev tools would still
    expose). The ENTIRE original element — attributes, any nested input's
    value=, any data-calc-value, any nested span — is discarded and replaced,
    not just its visible text, since a price can legitimately show up in any
    of those places depending on the cell's editable state.

    Column count/order is preserved (the replacement td keeps the same
    data-col-name), so this never disturbs table layout for any other column.
    Only ever called for kind=="PI" when the viewer's profile.unit is
    Technical — see tool_for_case. Every other caller of the rendering
    pipeline is completely unaffected; this is a post-processing step applied
    to the final HTML string, not a change to the rendering pipeline itself.
    """
    class _Masker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out = []
            self.skip_depth = None
            self.td_depth = 0

        def handle_starttag(self, tag, attrs):
            if self.skip_depth is not None:
                if tag == "td":
                    self.td_depth += 1
                return
            attrs_d = dict(attrs)
            if tag == "td" and attrs_d.get("data-col-name") in columns:
                self.out.append('<td data-col-name="%s"></td>' % attrs_d.get("data-col-name"))
                self.skip_depth = 0
                self.td_depth = 0
                return
            self.out.append(self.get_starttag_text())

        def handle_startendtag(self, tag, attrs):
            if self.skip_depth is not None:
                return
            self.out.append(self.get_starttag_text())

        def handle_endtag(self, tag):
            if self.skip_depth is not None:
                if tag == "td":
                    if self.td_depth == 0:
                        self.skip_depth = None
                    else:
                        self.td_depth -= 1
                return
            self.out.append("</%s>" % tag)

        def handle_data(self, data):
            if self.skip_depth is not None:
                return
            self.out.append(data)

        def handle_entityref(self, name):
            if self.skip_depth is not None:
                return
            self.out.append("&%s;" % name)

        def handle_charref(self, name):
            if self.skip_depth is not None:
                return
            self.out.append("&#%s;" % name)

        def handle_comment(self, data):
            if self.skip_depth is not None:
                return
            self.out.append("<!--%s-->" % data)

    m = _Masker()
    m.feed(html or "")
    return "".join(m.out)

from .processor import load_json_file, process_inquiry_records
from .resource_paths import json_path
from .views import dataframe_to_html_with_ids

logger = logging.getLogger(__name__)


def _clean_cell_value(val):
    """Normalize stored table values: no NaN, no spurious 1.0 decimals."""
    if val is None:
        return ""
    try:
        import math
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return ""
    except Exception:
        pass
    try:
        if pd.isna(val):
            return ""
    except Exception:
        pass
    s = str(val).strip()
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


# Display titles that older saves / layout passes may still use as row keys.
_DISPLAY_TO_CANONICAL = {
    "CLIENT DISCRIPTION": "description",
    "FTCO DISCRIPTION": "Final Arranged Text",
    # Legacy typo kept so older TO/PI snapshots still restore correctly.
    "FTCO DISCTIPTION": "Final Arranged Text",
    "FTCO CODE": "کد",
    "Item No.": "Item Code",
    "SIZE": "size",
    "QTY": "qty",
    "UNIT": "unit",
    "ALARM": "Alarm_Features",
    "REVISION": "اصلاحیه",
    "REMARK": "ریمارک",
    "Description": "description",
    "Size": "size",
    "Qty": "qty",
    "Unit": "unit",
}

# Coding fields that must survive the form.columns filter after Edit restore.
_CODING_KEEP_COLUMNS = (
    "Final Arranged Text",
    "Group",
    "Type",
    "Alarm_Features",
    "کد",
    "Filled_Features",
    "Feature_Variables",
)


def _canonicalize_form_table(table, columns):
    """Map display-titled keys back to canonical names used by the coder/JS.

    Some stored TO/PI snapshots mixed layout titles (``FTCO DISCRIPTION``,
    ``CLIENT DISCRIPTION``) with canonical keys. Restore then looked for
    ``description`` / ``Final Arranged Text``, found nothing, skipped the row,
    and the grid stayed blank until Remark/Revision forced a live AJAX pass.
    """
    def _canon_key(k):
        return _DISPLAY_TO_CANONICAL.get(str(k), k)

    new_table = []
    for r in table or []:
        if not isinstance(r, dict):
            continue
        nr = {}
        for k, v in r.items():
            ck = _canon_key(k)
            if ck not in nr:
                nr[ck] = v
                continue
            # Prefer a non-empty value when the same field appears under two names.
            if not str(nr.get(ck, "") or "").strip() and str(v or "").strip():
                nr[ck] = v
        new_table.append(nr)

    new_cols = []
    for c in columns or []:
        ck = _canon_key(c)
        if ck not in new_cols:
            new_cols.append(ck)
    return new_table, new_cols


def _row_description(r):
    """Client description text from any known key on the row."""
    if not isinstance(r, dict):
        return ""
    for k in ("description", "Description", "CLIENT DISCRIPTION"):
        v = str(r.get(k, "") or "").strip()
        if v:
            return v
    return ""


def _ensure_coding_columns(columns, table):
    """Keep coding outputs in the column list so restore results are not dropped."""
    cols = list(columns or [])
    for c in _CODING_KEEP_COLUMNS:
        if c not in cols:
            cols.append(c)
    for r in table or []:
        if not isinstance(r, dict):
            continue
        for c in _CODING_KEEP_COLUMNS:
            if c not in r:
                r[c] = ""
    return cols


def _blank_to_row_fields(row, columns_hint=None):
    """Ensure TO-only empty fields render as blank strings, never NaN."""
    for col in columns_hint or ():
        if col in row:
            row[col] = _clean_cell_value(row.get(col))
    for col in ("کد", "اصلاحیه", "ریمارک", "Item Code", "Item",
                "Final Arranged Text", "Group", "Type"):
        if col in row:
            row[col] = _clean_cell_value(row.get(col))
    return row

def _inquiry_row_from_mapping(r, default_client_row=""):
    """Normalize one inquiry table / line-item row to the seeding shape."""
    if not isinstance(r, dict):
        return None
    description = r.get("Description", r.get("description", ""))
    size = r.get("Size", r.get("size", ""))
    unit = r.get("Unit", r.get("unit", ""))
    quantity = r.get("Qty", r.get("quantity", r.get("qty", "")))
    client_row = r.get("#", r.get("client_row", default_client_row))
    if not any(str(v).strip() for v in (description, size, unit, quantity)):
        return None
    return {
        "description": description,
        "size": size,
        "unit": unit,
        "quantity": quantity,
        "client_row": client_row,
    }


def _inquiry_rows(case, side=None):
    """Return [{description, size, unit, quantity, client_row}, …] from the
    case's current inquiry. ``client_row`` (#) is the client's own row number,
    preserved across versions so deletions leave a visible gap."""
    from cases.constants import FormKind
    form = case.current_form(FormKind.INQUIRY, side)
    rows = []
    if form and form.table:
        for idx, r in enumerate(form.table, start=1):
            cr = r.get("#", r.get("client_row", idx))
            mapped = _inquiry_row_from_mapping(r, default_client_row=cr)
            if mapped:
                if str(r.get("_deleted", "") or "") == "1":
                    mapped["_deleted"] = "1"
                if str(r.get("_added", "") or "") == "1":
                    mapped["_added"] = "1"
                for item_key in ("Item", "Item Code"):
                    if r.get(item_key) not in (None, ""):
                        mapped[item_key] = _clean_cell_value(r.get(item_key))
                rows.append(mapped)
    if not rows:
        # Fallback when the inquiry snapshot is missing but line items still exist
        # (legacy data or a partial side edit).
        for li in case.line_items.all().order_by("row_no"):
            mapped = _inquiry_row_from_mapping({
                "#": li.client_row or li.row_no,
                "Description": li.description,
                "Size": li.size,
                "Qty": li.quantity,
                "Unit": li.unit,
            }, default_client_row=li.client_row or li.row_no)
            if mapped:
                rows.append(mapped)
    # Guard: if the stored inquiry table is an exactly-doubled sequence, collapse
    # it back to a single set (protects against a historical snapshot bug).
    n = len(rows)
    if n % 2 == 0 and n >= 2:
        half = n // 2
        def _sig(r):
            return (str(r.get("description", "")).strip(), str(r.get("size", "")).strip(),
                    str(r.get("quantity", "")).strip(), str(r.get("unit", "")).strip(),
                    str(r.get("client_row", "")).strip())
        if [_sig(x) for x in rows[:half]] == [_sig(x) for x in rows[half:]]:
            rows = rows[:half]
    return rows


def _inquiry_client_rows(case, side=None):
    """Map the current inquiry's Item number -> client row (#), used to drop rows
    that the client deleted in a newer inquiry version when re-versioning TO/PI."""
    from cases.constants import FormKind
    form = case.current_form(FormKind.INQUIRY, side)
    keep = []
    if form and form.table:
        for r in form.table:
            cr = r.get("#", "") or r.get("client_row", "")
            if str(cr).strip():
                keep.append(str(cr).strip())
    return keep


def _coded_rows_from_inquiry(inq_rows, columns_hint=None):
    """Run new inquiry rows through the coding pipeline and return TO-shaped dicts.

    Used when a New Version TO/PI must pick up soft-added inquiry rows that did
    not exist on the prior form. Soft-delete / add markers are preserved.
    """
    records = []
    meta = []
    for r in inq_rows or []:
        mapped = _inquiry_row_from_mapping(r)
        if not mapped:
            continue
        records.append({
            "description": str(mapped.get("description", "") or ""),
            "size": str(mapped.get("size", "") or ""),
            "qty": str(mapped.get("quantity", mapped.get("qty", "")) or ""),
            "unit": str(mapped.get("unit", "") or ""),
        })
        meta.append({
            "client_row": _clean_cell_value(mapped.get("client_row", "")),
            "item_no": _clean_cell_value((r or {}).get("Item", (r or {}).get("Item Code", ""))),
            "_deleted": "1" if str((r or {}).get("_deleted", "") or "") == "1" else "",
            "_added": "1" if str((r or {}).get("_added", "") or "") == "1" else "",
            "inquiry": mapped,
        })
    if not records:
        return []
    try:
        json_dict = load_json_file(json_path("data.json"))
        result_df = process_inquiry_records(records, json_dict)
    except Exception:
        logger.exception("Failed to code newly added inquiry rows for TO new version")
        out = []
        for r in inq_rows or []:
            mapped = _inquiry_row_from_mapping(r)
            if not mapped:
                continue
            row = {
                "#": _clean_cell_value(mapped.get("client_row", "")),
                "Item Code": _clean_cell_value((r or {}).get("Item", (r or {}).get("Item Code", ""))),
                "Item": _clean_cell_value((r or {}).get("Item", (r or {}).get("Item Code", ""))),
                "description": str(mapped.get("description", "") or ""),
                "size": str(mapped.get("size", "") or ""),
                "qty": str(mapped.get("quantity", "") or ""),
                "unit": str(mapped.get("unit", "") or ""),
                "کد": "",
                "اصلاحیه": "",
                "ریمارک": "",
                "Final Arranged Text": "",
                "_added": "1",
            }
            if str((r or {}).get("_deleted", "") or "") == "1":
                row["_deleted"] = "1"
            out.append(_blank_to_row_fields(row, columns_hint))
        return out
    # process_inquiry_records returns DISPLAY-named columns (Item No., FTCO CODE,
    # FTCO DISCRIPTION, REVISION, REMARK, …). Rename them back to the CANONICAL
    # keys the saved TO rows use (Item Code, کد, Final Arranged Text, اصلاحیه,
    # ریمارک, …) so appended rows share the exact same shape as every prior TO
    # row. Otherwise the mixed shapes collide when the grid is reassembled and
    # every prior row ends up blank / non-editable.
    display_to_canonical = result_df.attrs.get("display_to_canonical", {}) if hasattr(result_df, "attrs") else {}
    if display_to_canonical:
        result_df = result_df.rename(columns=display_to_canonical)
    out = []
    for i, rec in enumerate(result_df.to_dict("records")):
        row = {k: _clean_cell_value(v) for k, v in dict(rec).items()}
        m = meta[i] if i < len(meta) else {}
        inq = m.get("inquiry") or {}
        cr = m.get("client_row", "")
        if cr:
            row["#"] = cr
        # Client-owned columns always mirror the inquiry for newly appended rows.
        for src_k, dest_keys in (
            ("description", ("Description", "description")),
            ("size", ("Size", "size")),
            ("quantity", ("Qty", "qty")),
            ("unit", ("Unit", "unit")),
        ):
            val = inq.get(src_k, "")
            if str(val).strip():
                for dk in dest_keys:
                    row[dk] = val
        if m.get("_deleted") == "1":
            row["_deleted"] = "1"
        if m.get("_added") == "1":
            row["_added"] = "1"
        item_no = m.get("item_no", "")
        if item_no:
            row["Item Code"] = item_no
            row["Item"] = item_no
        row = _blank_to_row_fields(row, columns_hint)
        row.pop("Feature_Variables", None)
        out.append(row)
    return out


def _to_client_rows(case, side=None):
    """Client rows (#) present in the latest Technical Offer of this side. Used to
    drop rows from a NEW Proforma version that Technical removed on its TO (the
    Proforma must follow the TO's row set, which itself follows the inquiry)."""
    from cases.constants import FormKind
    form = case.current_form(FormKind.TO, side)
    keep = []
    if form and form.table:
        for r in form.table:
            cr = r.get("#", "") or r.get("client_row", "")
            if str(cr).strip():
                keep.append(str(cr).strip())
    return keep


def _to_description_rows(case, side=None):
    """For pricing: reuse the latest TO's description column when present,
    otherwise fall back to the inquiry. Keeps the tool input valid either way."""
    from cases.constants import FormKind
    to = case.current_form(FormKind.TO, side)
    rows = []
    if to and to.table:
        for r in to.table:
            mapped = _inquiry_row_from_mapping(r)
            if not mapped or not str(mapped.get("description", "")).strip():
                continue
            rows.append(mapped)
    return rows or _inquiry_rows(case, side)


def _seed_dataframe_html(rows):
    """Run inquiry rows through the tool pipeline and return table-body HTML."""
    if not rows:
        return None
    def _sig(r):
        return (str(r.get("description", "")).strip(), str(r.get("size", "")).strip(),
                str(r.get("quantity", "")).strip(), str(r.get("unit", "")).strip(),
                str(r.get("client_row", "") or "").strip())
    n = len(rows)
    if n % 2 == 0 and n >= 2:
        half = n // 2
        if [_sig(r) for r in rows[:half]] == [_sig(r) for r in rows[half:]]:
            rows = rows[:half]

    records = []
    client_rows = []
    deleted_flags = []
    added_flags = []
    for r in rows:
        mapped = _inquiry_row_from_mapping(r)
        if not mapped:
            continue
        client_rows.append(str(mapped.get("client_row", "") or "").strip())
        deleted_flags.append("1" if str((r or {}).get("_deleted", "") or "") == "1" else "")
        added_flags.append("1" if str((r or {}).get("_added", "") or "") == "1" else "")
        records.append({
            "description": str(mapped.get("description", "") or ""),
            "size": str(mapped.get("size", "") or ""),
            "qty": str(mapped.get("quantity", mapped.get("qty", "")) or ""),
            "unit": str(mapped.get("unit", "") or ""),
        })
    if not records:
        return None

    json_dict = load_json_file(json_path("data.json"))
    result_df = process_inquiry_records(records, json_dict)

    if any(client_rows):
        result_df = result_df.copy()
        crs = (client_rows + [""] * len(result_df))[:len(result_df)]
        if "#" in result_df.columns:
            result_df["#"] = crs
        else:
            result_df.insert(0, "#", crs)
        result_df["_deleted"] = (deleted_flags + [""] * len(result_df))[:len(result_df)]
        result_df["_added"] = (added_flags + [""] * len(result_df))[:len(result_df)]
    return dataframe_to_html_with_ids(result_df, data_json=json_dict)


def _plain_ftco_text(html_or_text):
    """Strip highlight HTML / entities from an FTCO DISCRIPTION cell value.

    Handles both live markup (``<span style=…>``) and already-escaped markup
    (``&lt;span…&gt;``) that previously leaked into the editable textarea as
    visible tags.
    """
    import html as _html
    import re as _re

    s = str(html_or_text or "")
    if not s:
        return ""
    # Escaped colour markup saved as literal text → unescape so tags can drop.
    low = s.lower()
    if "&lt;" in low and any(t in low for t in ("span", "bdi", "br", "font")):
        s = _html.unescape(s)
    s = _re.sub(r"<br\s*/?>", " ", s, flags=_re.I)
    s = _re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    return _re.sub(r"\s+", " ", s).strip()


def _ftco_code_optional():
    try:
        from django.conf import settings as _dj_settings
        return not bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", True))
    except Exception:
        return False


def _apply_ftco_user_override(r, saved_ftco, regex_ftco):
    """Keep a per-row manual FTCO DISCRIPTION when the user edited that row.

    Regex / coding outputs stay on the row; only the visible Final Arranged Text
    is overridden. Empty saved text (user cleared the cell) always takes regex.

    Manual overrides are always stored as **plain text** (never colour ``<span>``
    markup) so reopening TO never shows raw tags in the editable cell.
    """
    if not _ftco_code_optional():
        r.pop("_ftco_user_edited", None)
        return
    saved_plain = _plain_ftco_text(saved_ftco)
    regex_plain = _plain_ftco_text(regex_ftco)
    flagged = str(r.get("_ftco_user_edited", "") or "") == "1"
    if flagged and saved_plain:
        r["Final Arranged Text"] = saved_plain
        r["_ftco_user_edited"] = "1"
        return
    if flagged and not saved_plain:
        r["Final Arranged Text"] = regex_ftco
        r.pop("_ftco_user_edited", None)
        return
    # Legacy saves without the flag: treat plain!=regex as a manual edit for
    # THIS row only (never shared via the description memo). Store plain text
    # so escaped/colour HTML cannot resurface as visible tags.
    if saved_plain and saved_plain != regex_plain:
        r["Final Arranged Text"] = saved_plain
        r["_ftco_user_edited"] = "1"
        return
    r["Final Arranged Text"] = regex_ftco
    r.pop("_ftco_user_edited", None)


def _recode_to_row_inplace(r, desc, json_dict, idx):
    """Re-run the full coding pipeline for ONE Technical-Offer row.

    Used when the inquiry description changed, and when Technical opens Edit so
    every eligible row gets a fresh group/type/regex/colour/alarm/code pass.

    Group and Type are always re-detected from the description (inputs left
    empty) and the per-row RAM cache is bypassed: otherwise a prior Build that
    left Group blank (common for flange/fitting while pipe was fine) would be
    reused and never re-run ``find_group`` until the user typed in Revision.

    Returns the regex ``Final_Text`` (before any per-row user override), or
    ``None`` on failure. Never raises: on any failure the row is left untouched.
    """
    from .text_processor import process_text_record_live
    try:
        result = process_text_record_live(
            desc,
            json_dict,
            # Force rediscovery from description — do not lock to a stale/empty
            # saved Group/Type from a previous incomplete identification.
            group_key_input="",
            type_key_input="",
            remark=str(r.get("ریمارک", "") or "").strip(),
            revision=str(r.get("اصلاحیه", "") or "").strip(),
            clean_size=str(r.get("size", r.get("Size", "")) or "").strip(),
            # Bypass ROW_BASE_FEATURE_CACHE so empty-group Build entries cannot
            # short-circuit find_group on Edit entry.
            row_index=None,
            allow_code_lookup=True,
        )
    except Exception:
        logger.debug("Re-code failed for TO row %s; leaving it unchanged", idx, exc_info=True)
        return None

    # Always write coding outputs (including clears) so a prior empty Build
    # cannot leave stale blanks that the column filter then keeps forever.
    # Manual FTCO DISCRIPTION is applied per-row AFTER regex (never memoised).
    saved_ftco = r.get("Final Arranged Text", "")
    regex_ftco = str(result.get("Final_Text", "") or "")
    r["Final Arranged Text"] = regex_ftco
    filled = result.get("Filled_Features", None)
    if filled is not None:
        r["Filled_Features"] = filled
    r["Group"] = str(result.get("Group", "") or "")
    r["Type"] = str(result.get("Type", "") or "")
    fv = result.get("Feature_Variables", None)
    if fv is not None:
        r["Feature_Variables"] = fv
    alarms = result.get("Alarm", []) or []
    if isinstance(alarms, (list, tuple)):
        r["Alarm_Features"] = "<br>".join(str(a) for a in alarms)
    else:
        r["Alarm_Features"] = str(alarms)
    # Recognised → assign the fresh code; not recognised → clear it so the row
    # is blocked from pricing / sending until it is re-coded.
    if result.get("Can_Assign_Code"):
        r["کد"] = str(result.get("Code", "") or "")
    else:
        r["کد"] = ""
    _apply_ftco_user_override(r, saved_ftco, regex_ftco)
    return regex_ftco


def _restore_final_arranged_colors(table, json_dict, skip_crs=None):
    """On every TO edit/reload: full regex + colours + alarms + code for ALL rows.

    Technical must see group/type identification and the complete pipeline on
    every eligible row as soon as they enter Edit — not only after focusing a
    row or typing in Remark/Revision.

    ``skip_crs`` — client-row / Item numbers that must not run the pipeline
    (unhandled Proforma-remark / brand-pending rows wait for Reject / Confirm).
    Soft-deleted and Technical-Problem rows are also skipped.

    Memo caches regex outputs by (desc, remark, revision, size) only. A manual
    FTCO DISCRIPTION edit on one row must NEVER be copied onto sibling rows that
    share the same description inputs.
    """
    try:
        from .startup_warmup import warm_all_runtime_caches
        warm_all_runtime_caches()
    except Exception:
        pass

    # Drop Build-time empty-group cache entries so Edit re-runs find_group.
    try:
        from .runtime_cache import clear_row_base_cache
        clear_row_base_cache()
    except Exception:
        pass

    skip = set(skip_crs or ())
    memo = {}
    for idx, r in enumerate(table or []):
        if str(r.get("_issue", "") or "") == "1":
            continue
        if str(r.get("_deleted", "") or "") == "1":
            continue
        if str(r.get("_pf_pending", "") or "") == "1":
            continue
        if str(r.get("_brand_pending", "") or "") == "1":
            continue
        cr = str(_clean_cell_value(r.get("#", "") or r.get("client_row", "")) or "").strip()
        it = str(_clean_cell_value(r.get("Item Code", "") or r.get("Item", "")) or "").strip()
        if (cr and cr in skip) or (it and it in skip):
            continue
        desc = _row_description(r)
        if not desc:
            continue
        remark = str(r.get("ریمارک", "") or "").strip()
        revision = str(r.get("اصلاحیه", "") or "").strip()
        size = str(r.get("size", r.get("Size", r.get("SIZE", ""))) or "").strip()
        # Memo by description inputs only — Group/Type are re-detected.
        memo_key = (desc, remark, revision, size)
        cached_row = memo.get(memo_key)
        if cached_row is not None:
            # Keep THIS row's saved FTCO before applying shared regex outputs.
            saved_ftco = r.get("Final Arranged Text", "")
            for key in (
                "Filled_Features", "Group", "Type",
                "Feature_Variables", "Alarm_Features", "کد",
            ):
                if key in cached_row:
                    r[key] = cached_row[key]
            regex_ftco = cached_row.get("Final Arranged Text", "")
            r["Final Arranged Text"] = regex_ftco
            _apply_ftco_user_override(r, saved_ftco, regex_ftco)
            continue
        regex_ftco = _recode_to_row_inplace(r, desc, json_dict, idx)
        if regex_ftco is None:
            continue
        # Always memo the REGEX Final Arranged Text — never a user override.
        memo[memo_key] = {
            "Final Arranged Text": regex_ftco,
            "Filled_Features": r.get("Filled_Features"),
            "Group": r.get("Group"),
            "Type": r.get("Type"),
            "Feature_Variables": r.get("Feature_Variables"),
            "Alarm_Features": r.get("Alarm_Features"),
            "کد": r.get("کد"),
        }


def _should_open_remark_brand_round(case, form) -> bool:
    """True when a fresh Prev/New round should run (after a workflow action).

    Calm Edit immediately after Save must restore the exact saved cells
    (including an existing Prev/New pair). After a real unit handoff / action
    following ``form.updated_at``, keep the existing after-action behaviour.
    """
    if form is None:
        return False
    updated = getattr(form, "updated_at", None)
    if updated is None:
        return False
    from cases.constants import EventAction
    skip = {
        EventAction.CREATE,
        EventAction.EDIT,
        EventAction.BUILD_TO,
        EventAction.BUILD_PI,
        EventAction.NEW_VERSION,
        EventAction.COMMENT,
    }
    events = getattr(case, "_prefetched_objects_cache", {}).get("events")
    if events is None:
        events = case.events.all()
    for e in events:
        created = getattr(e, "created_at", None)
        if created is None or created <= updated:
            continue
        action = getattr(e, "action", "") or ""
        if action in skip:
            continue
        fu = getattr(e, "from_unit", "") or ""
        tu = getattr(e, "to_unit", "") or ""
        if fu and tu and fu != tu:
            return True
        if action:
            return True
    return False


def _apply_remark_split(case, form_kind, side, table, *, mode="edit", current_form=None):
    """Turn the REMARK column into read-only OLD + editable NEW — but ONLY for the
    rows that actually carry a proforma remark. Every other row keeps a single
    (normal) remark field.

    Rules:
      • OLD = this side's OWN last committed remark (read-only).
      • NEW starts empty; only the NEW text re-codes (TO) / re-prices (PI).
      • TO row splits iff the current Proforma has a remark for that row.
      • PI row splits iff it carries its own proforma remark.
      • When a supplier remark is still UNHANDLED on the TO (``pf != _pf_ack``)
        only the FTCO CODE is cleared, ``_pf_pending`` is set, and regex/coding
        is deferred until Technical clicks Reject or Confirm on that row.
        Remark New + Revision stay locked until then. FTCO DISCRIPTION is KEPT.
      • Calm re-edit (no action since save): restore exact saved New, and keep
        Prev/New only when that pair was already saved (``_remark_split``).
      • After an action / newversion: existing Prev←committed, New empty behaviour.
    """
    from cases.constants import FormKind

    def _norm(v):
        return str(_clean_cell_value(v) if v is not None else "").strip()

    def _cr(r):
        return _norm(r.get("#", "") or r.get("client_row", ""))

    def _item(r):
        return _norm(r.get("Item Code", "") or r.get("Item", ""))

    def _restore_saved_remark_split(r, committed):
        """Calm path: keep Prev/New after Confirm/Reject (incl. equal Prev/New)."""
        if str(r.get("_remark_split", "") or "") == "1":
            prev = str(r.get("_prev_remark", "") or "")
            # TO uses ``_pf_ack``; PI uses ``_remark_ack`` after absorbing a
            # Technical answer. Either marker must keep the saved Prev/New pair
            # even when Prev==New (empty-New save collapsed to Old) — otherwise
            # the split is dropped, the remark looks like a fresh Supply remark,
            # and calm Save→Edit wrongly re-locks TIME/price.
            has_ack = bool(
                str(r.get("_pf_ack", "") or "").strip()
                or str(r.get("_remark_ack", "") or "").strip()
            )
            if prev != committed or has_ack:
                r["_remark_split"] = "1"
                r["_prev_remark"] = prev
                r["ریمارک"] = committed
                r.pop("_pf_pending", None)
                r.pop("_pf_text", None)
                return True
        r.pop("_remark_split", None)
        r.pop("_prev_remark", None)
        r.pop("_pf_pending", None)
        r.pop("_pf_text", None)
        r["ریمارک"] = committed
        return False

    open_round = (
        mode == "newversion"
        or (mode == "edit" and _should_open_remark_brand_round(case, current_form))
    )

    if form_kind == FormKind.TO:
        pi = case.current_form(FormKind.PI, side)
        pf_by_cr = {}
        pf_by_item = {}
        if pi and pi.table:
            for pr in pi.table:
                rem = _pi_row_remark(pr)
                if not rem:
                    continue
                cr = _cr(pr)
                it = _item(pr)
                if cr:
                    pf_by_cr[cr] = rem
                if it:
                    pf_by_item[it] = rem
        for r in table:
            if str(r.get("_deleted", "") or "") == "1":
                continue
            pf = pf_by_cr.get(_cr(r), "") or pf_by_item.get(_item(r), "")
            committed = str(r.get("ریمارک", "") or "").strip()
            if not pf:
                # No current PF remark — calm may still restore a saved Prev/New.
                if not open_round:
                    _restore_saved_remark_split(r, committed)
                else:
                    r.pop("_remark_split", None)
                    r.pop("_prev_remark", None)
                    r.pop("_pf_pending", None)
                    r.pop("_pf_text", None)
                continue
            ack = str(r.get("_pf_ack", "") or "").strip()
            pending = pf != ack
            if pending:
                # Unhandled supplier remark — Confirm/Reject (unchanged).
                r["_remark_split"] = "1"
                r["_prev_remark"] = committed
                r["ریمارک"] = ""
                r["کد"] = ""
                r["_pf_pending"] = "1"
                r["_pf_text"] = pf
            else:
                # Already acknowledged (or calm). Do NOT invent Prev/New for
                # every row that still carries a PF remark after handoff —
                # only restore a split that was already saved for THIS row.
                _restore_saved_remark_split(r, committed)
    else:  # PI
        to = case.current_form(FormKind.TO, side)
        to_by_cr = {}
        to_by_item = {}
        if to and to.table:
            for tr in to.table:
                cr = _cr(tr)
                it = _item(tr)
                if cr:
                    to_by_cr[cr] = tr
                if it:
                    to_by_item[it] = tr
        for r in table:
            if str(r.get("_deleted", "") or "") == "1":
                continue
            committed = str(r.get("ریمارک", "") or "").strip()
            src = to_by_cr.get(_cr(r)) or to_by_item.get(_item(r))
            # Fresh Prev/New after handoff only when THIS row was in a remark
            # round on TO and PI has not yet absorbed that answer (``_remark_ack``).
            to_remark_round = (
                src is not None
                and str(src.get("_remark_split", "") or "") == "1"
            )
            to_pf_ack = str((src or {}).get("_pf_ack", "") or "").strip()
            pi_r_ack = str(r.get("_remark_ack", "") or "").strip()
            if (
                open_round
                and committed
                and to_remark_round
                and to_pf_ack
                and to_pf_ack != pi_r_ack
            ):
                r["_remark_split"] = "1"
                r["_prev_remark"] = committed
                r["ریمارک"] = ""
            elif not open_round:
                _restore_saved_remark_split(r, committed)
            else:
                # Handoff with no new Technical remark answer for THIS row.
                r.pop("_remark_split", None)
                r.pop("_prev_remark", None)
                r["ریمارک"] = committed
                # Remark already answered on TO — keep an absorb marker so calm
                # Save→Edit does not treat leftover remark text as a fresh lock.
                if to_pf_ack and committed and to_pf_ack == committed:
                    r["_remark_ack"] = to_pf_ack
            r.pop("_pf_pending", None)
            r.pop("_pf_text", None)

        # Drop same-code-sync pollution: remarks copied onto rows that were never
        # in a Technical remark round (would false-lock TIME/price on calm edit).
        round_texts = set()
        for r in table:
            if str(r.get("_deleted", "") or "") == "1":
                continue
            src = to_by_cr.get(_cr(r)) or to_by_item.get(_item(r))
            if src is None or str(src.get("_remark_split", "") or "") != "1":
                continue
            for t in (
                str(r.get("_prev_remark", "") or "").strip(),
                str(src.get("_pf_ack", "") or "").strip(),
                _pi_row_remark(r),
            ):
                if t:
                    round_texts.add(t)
        if round_texts:
            for r in table:
                if str(r.get("_deleted", "") or "") == "1":
                    continue
                if str(r.get("_remark_split", "") or "") == "1":
                    continue
                src = to_by_cr.get(_cr(r)) or to_by_item.get(_item(r))
                if src is not None and str(src.get("_remark_split", "") or "") == "1":
                    continue
                rem = str(r.get("ریمارک", "") or "").strip()
                if rem and rem in round_texts:
                    r["ریمارک"] = ""
                    r.pop("_remark_ack", None)


def _brand_significant(a, b):
    """True when non-space characters differ (spaces-only edits do not count)."""
    def _ns(v):
        return "".join(str(v or "").split())
    return _ns(a) != _ns(b)


def _pi_row_remark(pr):
    """PI remark New value as saved (empty New stays empty — never fall back to Prev)."""
    return str((pr or {}).get("ریمارک", "") or "").strip()


def _ensure_brand_column(columns, table):
    """Insert BRAND after ALARM when a saved form predates the TO brand column."""
    cols = list(columns or [])
    if "BRAND" not in cols:
        if "Alarm_Features" in cols:
            cols.insert(cols.index("Alarm_Features") + 1, "BRAND")
        elif "اصلاحیه" in cols:
            cols.insert(cols.index("اصلاحیه"), "BRAND")
        elif "ریمارک" in cols:
            cols.insert(cols.index("ریمارک"), "BRAND")
        else:
            cols.append("BRAND")
    for r in table or []:
        if "BRAND" not in r:
            r["BRAND"] = ""
    return cols


def _ensure_time_column(columns, table):
    """Ensure TIME exists for every Proforma grid (TO-only and TO & PI).

    TIME is a Supply column: it is not on the Technical Offer layout, so PI
    grids seeded from TO often lack it. Without this, the header/body never
    carry TIME and the client injector used to skip it when BRAND was already
    present.
    """
    cols = list(columns or [])
    if "TIME" not in cols:
        if "BRAND" in cols:
            cols.insert(cols.index("BRAND") + 1, "TIME")
        elif "UNIT PRICE" in cols:
            cols.insert(cols.index("UNIT PRICE"), "TIME")
        elif "ریمارک" in cols:
            cols.insert(cols.index("ریمارک"), "TIME")
        else:
            cols.append("TIME")
    for r in table or []:
        if "TIME" not in r:
            r["TIME"] = ""
    return cols


def _ensure_service_price_column(columns, table):
    """Ensure SERVICE PRICE exists for a PI grid that already has service data.

    Unlike TIME/BRAND (always present on every PI), SERVICE PRICE is an
    opt-in feature — most PIs will never use it. The toggle itself is a
    client-side UI state (service_price.js) with nothing new to persist on
    the CaseForm; the column reappearing correctly when a case with service
    data is reopened is driven entirely by whether any row already carries a
    service comment or price, which the client toggling on writes exactly
    like every other cell (see tool_save.js's generic per-column collector).
    Reusing that generic path means this never needed a new save-time flag or
    a database migration — the row data already carries everything needed to
    know the feature was in use.
    """
    cols = list(columns or [])
    rows = table or []
    already_used = any(
        str(r.get("SERVICE PRICE", "") or "").strip()
        or str(r.get("_service_comment", "") or "").strip()
        for r in rows
    )
    if not already_used:
        return cols
    if "SERVICE PRICE" not in cols:
        if "UNIT PRICE" in cols:
            cols.insert(cols.index("UNIT PRICE") + 1, "SERVICE PRICE")
        elif "TOTAL PRICE" in cols:
            cols.insert(cols.index("TOTAL PRICE"), "SERVICE PRICE")
        else:
            cols.append("SERVICE PRICE")
    for r in rows:
        if "SERVICE PRICE" not in r:
            r["SERVICE PRICE"] = ""
    return cols


def _order_pi_commercial_columns(columns):
    """PI visual order (LTR): REMARK, BRAND, TIME, then UNIT PRICE.

    Pulls those three out of whatever prior positions they had and places the
    block immediately before UNIT PRICE (or at the end when price is absent).
    """
    cols = list(columns or [])
    block_names = ("ریمارک", "BRAND", "TIME")
    block = [c for c in block_names if c in cols]
    rest = [c for c in cols if c not in block_names]
    if not block:
        return rest
    if "UNIT PRICE" in rest:
        i = rest.index("UNIT PRICE")
        return rest[:i] + block + rest[i:]
    return rest + block


def _apply_brand_split(case, form_kind, side, table, *, mode="edit", current_form=None):
    """BRAND Old/New split mirroring remark — Supply brand changes vs Technical.

    TO:
      • Pending when PI brand significantly differs from ``_brand_ack`` (or from
        the TO committed brand when never acknowledged).
      • OLD = Technical's committed brand; NEW = Supply's PI brand.
      • Pending rows clear FTCO code until Reject / Confirm.
      • Calm re-edit after Confirm/Reject: restore saved Prev/New exactly.
    PI:
      • After Technical answers a brand round (TO has ``_brand_ack``) and PI has
        not yet absorbed that answer (``_brand_ack`` on PI), show Old/New:
        OLD = Supply's previous brand, NEW = Technical's confirmed brand.
      • Calm re-edit: exact saved brand; keep Prev/New only if saved as split.
      • No Reject/Confirm buttons on PI.
    """
    from cases.constants import FormKind
    _ = (mode, current_form)

    def _norm(v):
        return str(_clean_cell_value(v) if v is not None else "").strip()

    def _cr(r):
        return _norm(r.get("#", "") or r.get("client_row", ""))

    def _item(r):
        return _norm(r.get("Item Code", "") or r.get("Item", ""))

    def _restore_saved_brand_split(r, committed):
        """Calm re-edit: restore Prev/New after Confirm/Reject.

        Keep the split when Prev≠New, or when this row was answered
        (``_brand_ack`` present — including Reject where Prev==New).
        Drop leftover split flags on rows that were never answered and have
        identical Prev/New (would otherwise split every unchanged brand).
        """
        if str(r.get("_brand_split", "") or "") == "1":
            prev = str(r.get("_prev_brand", "") or "")
            if _brand_significant(prev, committed) or (
                "_brand_ack" in r
                and r.get("_brand_ack") is not None
                and str(r.get("_brand_ack", "")).strip().lower()
                not in ("", "nan", "none", "<na>", "null")
            ):
                r["_brand_split"] = "1"
                r["_prev_brand"] = prev
                r["BRAND"] = committed
                r.pop("_brand_pending", None)
                r.pop("_brand_pf_text", None)
                return True
        r.pop("_brand_split", None)
        r.pop("_prev_brand", None)
        r.pop("_brand_pending", None)
        r.pop("_brand_pf_text", None)
        r["BRAND"] = committed
        return False

    if form_kind == FormKind.TO:
        pi = case.current_form(FormKind.PI, side)
        brand_by_cr = {}
        brand_by_item = {}
        if pi and pi.table:
            for pr in pi.table:
                b = str(pr.get("BRAND", "") or "")
                cr = _cr(pr)
                it = _item(pr)
                if cr:
                    brand_by_cr[cr] = b
                if it:
                    brand_by_item[it] = b
        for r in table:
            if str(r.get("_deleted", "") or "") == "1":
                continue
            pi_b = brand_by_cr.get(_cr(r), "")
            if _cr(r) not in brand_by_cr:
                pi_b = brand_by_item.get(_item(r), pi_b)
            # Only consider PI brand when a matching PI row exists.
            has_pi = (_cr(r) in brand_by_cr) or (_item(r) in brand_by_item)
            if not has_pi:
                r.pop("_brand_split", None)
                r.pop("_prev_brand", None)
                r.pop("_brand_pending", None)
                r.pop("_brand_pf_text", None)
                continue
            to_b = str(r.get("BRAND", "") or "")
            # Densified empty/NaN _brand_ack on non-split rows must NOT count
            # as acknowledged (would mark every unchanged row pending).
            ack_raw = r.get("_brand_ack", None) if "_brand_ack" in r else None
            try:
                if ack_raw is not None and pd.isna(ack_raw):
                    ack_raw = None
            except Exception:
                pass
            if ack_raw is not None and str(ack_raw).strip().lower() in (
                "nan", "none", "<na>", "null",
            ):
                ack_raw = None
            has_split = str(r.get("_brand_split", "") or "") == "1"
            ack_set = ack_raw is not None and (
                has_split or bool(str(ack_raw).strip())
            )
            ack = str(ack_raw or "") if ack_set else ""
            # Unresolved when PI brand differs from last ack, or (first round)
            # PI has a non-empty brand that differs from TO — never treat a blank
            # PI brand cell as a Supply change (would split every unchanged row).
            if ack_set:
                pending = _brand_significant(pi_b, ack)
            else:
                pending = (
                    bool("".join(str(pi_b).split()))
                    and _brand_significant(pi_b, to_b)
                )
            if pending:
                r["_brand_split"] = "1"
                r["_prev_brand"] = to_b
                r["BRAND"] = pi_b
                r["کد"] = ""
                r["_brand_pending"] = "1"
                r["_brand_pf_text"] = pi_b
            else:
                # Not pending: restore saved Prev/New (calm after Confirm/Reject)
                # or stay single-field when never split.
                _restore_saved_brand_split(r, to_b)
    else:  # PI
        to = case.current_form(FormKind.TO, side)
        to_by_cr = {}
        to_by_item = {}
        if to and to.table:
            for tr in to.table:
                cr = _cr(tr)
                it = _item(tr)
                if cr:
                    to_by_cr[cr] = tr
                if it:
                    to_by_item[it] = tr
        open_round = (
            mode == "newversion"
            or (mode == "edit" and _should_open_remark_brand_round(case, current_form))
        )
        for r in table:
            if str(r.get("_deleted", "") or "") == "1":
                continue
            src = to_by_cr.get(_cr(r)) or to_by_item.get(_item(r))
            to_b = str((src or {}).get("BRAND", "") or "")
            pi_b = str(r.get("BRAND", "") or "")
            pi_ack = str(r.get("_brand_ack", "") or "")
            # Baseline for PI TIME/UNIT PRICE lock = Technical's current TO brand.
            if src is not None:
                r["_brand_baseline"] = to_b
            elif "_brand_baseline" not in r:
                r["_brand_baseline"] = pi_b
            # Absorb a Technical brand answer ONLY on handoff/newversion
            # (``open_round``). Calm Save→Edit must restore the exact saved
            # Prev/New (e.g. Prev=A, New=A/B). Re-absorbing here swapped them
            # to Prev=A/B, New=TO and unlocked TIME/price.
            to_answered_split = (
                open_round
                and src is not None
                and str(src.get("_brand_split", "") or "") == "1"
                and "_brand_ack" in src
                and src.get("_brand_ack") is not None
                and str(src.get("_brand_ack", "")).strip().lower()
                not in ("", "nan", "none", "<na>", "null")
            )
            if to_answered_split and _brand_significant(to_b, pi_ack):
                r["_brand_split"] = "1"
                r["_prev_brand"] = pi_b
                r["BRAND"] = to_b
                # Absorb marker = Technical brand, not Supply's later New edit.
                r["_brand_ack"] = to_b
            elif not open_round:
                # Calm: exact saved brand; keep Prev/New only if saved as split.
                _restore_saved_brand_split(r, pi_b)
                # Lock baseline always tracks latest Technical brand.
                if src is not None:
                    r["_brand_baseline"] = to_b
            else:
                # Handoff: no Supply↔Technical brand round on THIS row.
                # Still mirror the latest TO brand — Technical-only Brand edits
                # (e.g. G→A/G) must appear on PI. Keeping stale PI brand left
                # baseline=TO and cell=old, which locked TIME/price after Save.
                r.pop("_brand_split", None)
                r.pop("_prev_brand", None)
                r["BRAND"] = to_b if src is not None else pi_b
                r["_brand_baseline"] = to_b if src is not None else pi_b
                if src is not None:
                    r["_brand_ack"] = to_b
            r.pop("_brand_pending", None)
            r.pop("_brand_pf_text", None)


def _form_grid_html(case, form_kind, side=None, mode="edit", *, blank_remark=False):
    """Re-render an already-saved TO/PI grid (with its remarks/edits) so the
    user can Edit it or branch a New version without losing prior work.

    ``blank_remark`` — wipe ``ریمارک`` on every row. Used when seeding a new
    Proforma from the Technical Offer: TO remark and PI proforma-remark are
    independent; PI must start empty.

    On a NEW version (mode="newversion") two rules apply:
      • Rows whose client number (#) was deleted from the latest inquiry are
        dropped automatically, so the offer follows the client's deletions.
      • For PI, the four columns size / qty / unit / description are refreshed
        from the latest Technical Offer (matched by #), while every other column
        (brand, time, unit price, PI's own remark, labels …) is copied from the
        prior PI — never from TO remark.
    """
    form = case.current_form(form_kind, side)
    if not form or not form.table:
        return None
    from cases.constants import FormKind, Side
    columns = form.columns or list(form.table[0].keys())
    table = [dict(r) for r in form.table]
    table, columns = _canonicalize_form_table(table, columns)
    columns = _ensure_brand_column(columns, table)
    if form_kind == FormKind.PI:
        columns = _ensure_time_column(columns, table)
        columns = _ensure_service_price_column(columns, table)
        columns = _order_pi_commercial_columns(columns)
    columns = _ensure_coding_columns(columns, table)

    # ---- NEW VERSION: honour soft-deletes and newly added inquiry/TO rows. ----
    # Keep prior form rows that still exist in the source (#); drop only those
    # whose # was hard-removed from the source. Then APPEND any source rows
    # that are missing here (e.g. commercial soft-added a row) so coding rules
    # run on them like every other item.
    if mode == "newversion":
        def _cr(r):
            return str(r.get("#", "") or r.get("client_row", "") or "").strip()

        if form_kind == FormKind.PI:
            # A new PI version follows the LATEST TO's row set (which itself
            # follows the inquiry). Deleted/added rows sync from the TO; every
            # surviving row keeps its OWN prior PI values (prices, brand, time…).
            to_form = case.current_form(FormKind.TO, side)
            source_rows = list(to_form.table or []) if to_form else []
            keep = _to_client_rows(case, side) or _inquiry_client_rows(case, side)
        else:
            # A new TO version follows the LATEST inquiry's row set. Deleted/added
            # rows sync from the inquiry; every surviving row keeps its OWN prior
            # TO values (FTCO code/description, revision, remark, labels…).
            inq_form = case.current_form(FormKind.INQUIRY, side)
            source_rows = list(inq_form.table or []) if inq_form else []
            keep = _inquiry_client_rows(case, side)

        if keep:
            keep_set = set(keep)
            # Keep prior rows still present in the source (by #), plus any
            # soft-deleted rows (shown struck-through, never hard-dropped).
            if any(_cr(r) for r in table):
                table = [r for r in table if (_cr(r) in keep_set or not _cr(r)
                                               or str(r.get("_deleted", "") or "") == "1")]
            present = {_cr(r) for r in table if _cr(r)}
            missing_src = [sr for sr in source_rows if _cr(sr) and _cr(sr) not in present]
            if missing_src:
                if form_kind == FormKind.TO:
                    # Code the newly added inquiry rows into full TO shape.
                    table.extend(_coded_rows_from_inquiry(missing_src, columns))
                else:
                    # PI: take the new rows from the TO with blank PI-only fields
                    # (price / brand / time / PI remark); keep the TO's soft marks.
                    for sr in missing_src:
                        nr = dict(sr)
                        # Keep TO BRAND (seed brand from Technical); blank other PI-only fields.
                        for k in ("TIME", "UNIT PRICE", "TOTAL PRICE",
                                  "_price_source", "_unit_price_raw", "ریمارک"):
                            if k in nr:
                                nr[k] = ""
                        # New PI rows never inherit the TO's remark/brand-split state.
                        nr.pop("_remark_split", None)
                        nr.pop("_prev_remark", None)
                        nr.pop("_pf_ack", None)
                        nr.pop("_pf_pending", None)
                        nr.pop("_pf_text", None)
                        nr.pop("_brand_split", None)
                        nr.pop("_prev_brand", None)
                        nr.pop("_brand_ack", None)
                        nr.pop("_brand_pending", None)
                        nr.pop("_brand_pf_text", None)
                        table.append(nr)
        # Item No. follows the inquiry (by #); display order still 1..N when missing.
        inq_for_items = (case.current_form(FormKind.INQUIRY, side)
                           if form_kind == FormKind.TO
                           else case.current_form(FormKind.TO, side) if form_kind == FormKind.PI else None)
        inq_item_by_cr = {}
        if inq_for_items and inq_for_items.table:
            for ir in inq_for_items.table:
                cr = str(ir.get("#", "") or ir.get("client_row", "") or "").strip()
                if cr:
                    inq_item_by_cr[cr] = _clean_cell_value(
                        ir.get("Item", ir.get("Item Code", "")))
        for seq_no, r in enumerate(table, start=1):
            cr = str(r.get("#", "") or r.get("client_row", "") or "").strip()
            item_val = inq_item_by_cr.get(cr) or seq_no
            for item_key in ("Item", "Item Code"):
                if item_key in r or item_key in (columns or []):
                    r[item_key] = item_val

    # ---- TO: refresh description / size / qty / unit from the latest INQUIRY
    # of the same side (matched by # then Item), on BOTH edit and new version.
    # The client owns these four columns, so a new TO version must carry the
    # inquiry's CURRENT values for every surviving row while keeping all of the
    # TO's own columns (FTCO code, FTCO description, revision, remark, labels …)
    # copied from the prior TO. ----
    if form_kind == FormKind.TO:
        # Rows with an unhandled Proforma remark must NOT be re-coded on entry
        # (code stays cleared; Technical resolves via Reject / Confirm first).
        def _norm_pf(v):
            return str(_clean_cell_value(v) if v is not None else "").strip()

        def _cr_pf(r):
            return _norm_pf(r.get("#", "") or r.get("client_row", ""))

        def _item_pf(r):
            return _norm_pf(r.get("Item Code", "") or r.get("Item", ""))

        pending_pf_crs = set()
        pending_pf_items = set()
        pending_brand_crs = set()
        pending_brand_items = set()
        pi_for_pf = case.current_form(FormKind.PI, side)
        if pi_for_pf and pi_for_pf.table:
            pf_by_cr = {}
            pf_by_item = {}
            brand_by_cr = {}
            brand_by_item = {}
            for pr in pi_for_pf.table:
                rem = _pi_row_remark(pr)
                cr = _cr_pf(pr)
                it = _item_pf(pr)
                if rem:
                    if cr:
                        pf_by_cr[cr] = rem
                    if it:
                        pf_by_item[it] = rem
                b = str(pr.get("BRAND", "") or "")
                if cr:
                    brand_by_cr[cr] = b
                if it:
                    brand_by_item[it] = b
            for r in table:
                cr = _cr_pf(r)
                it = _item_pf(r)
                pf = pf_by_cr.get(cr, "") or pf_by_item.get(it, "")
                ack = str(r.get("_pf_ack", "") or "").strip()
                if pf and pf != ack:
                    if cr:
                        pending_pf_crs.add(cr)
                    if it:
                        pending_pf_items.add(it)
                has_pi = (cr in brand_by_cr) or (it in brand_by_item)
                if has_pi:
                    pi_b = brand_by_cr.get(cr, "") if cr in brand_by_cr else brand_by_item.get(it, "")
                    to_b = str(r.get("BRAND", "") or "")
                    b_ack_raw = r.get("_brand_ack", None) if "_brand_ack" in r else None
                    try:
                        if b_ack_raw is not None and pd.isna(b_ack_raw):
                            b_ack_raw = None
                    except Exception:
                        pass
                    if b_ack_raw is not None and str(b_ack_raw).strip().lower() in (
                        "nan", "none", "<na>", "null",
                    ):
                        b_ack_raw = None
                    b_has_split = str(r.get("_brand_split", "") or "") == "1"
                    b_ack_set = b_ack_raw is not None and (
                        b_has_split or bool(str(b_ack_raw).strip())
                    )
                    b_ack = str(b_ack_raw or "") if b_ack_set else ""
                    if b_ack_set:
                        b_pending = _brand_significant(pi_b, b_ack)
                    else:
                        # Match _apply_brand_split: blank PI brand is not a change.
                        b_pending = (
                            bool("".join(str(pi_b).split()))
                            and _brand_significant(pi_b, to_b)
                        )
                    if b_pending:
                        if cr:
                            pending_brand_crs.add(cr)
                        if it:
                            pending_brand_items.add(it)

        inq = case.current_form(FormKind.INQUIRY, side)
        if inq and inq.table:
            inq_by_cr = {}
            inq_by_item = {}
            for ir in inq.table:
                cr = str(ir.get("#", "") or ir.get("client_row", "") or "").strip()
                if cr:
                    inq_by_cr[cr] = ir
                it = str(ir.get("Item") or ir.get("Item Code") or "").strip()
                if it:
                    inq_by_item.setdefault(it, ir)
            refreshed = []
            for _idx, r in enumerate(table):
                cr = str(r.get("#", "") or r.get("client_row", "") or "").strip()
                it = str(r.get("Item") or r.get("Item Code") or "").strip()
                src = inq_by_cr.get(cr) or inq_by_item.get(it)
                if src is not None:
                    for key_variants in (("Size", "size"), ("Qty", "qty"),
                                          ("Unit", "unit"),
                                          ("Description", "description")):
                        val = None
                        for k in key_variants:
                            if k in src and str(src.get(k, "")).strip() != "":
                                val = src.get(k); break
                        if val is not None:
                            # Size from inquiry is raw (e.g. DN 100) — map via
                            # find_size_<group>.csv so the cell shows NPS in
                            # red parens when that group's file exists.
                            if key_variants[0] == "Size":
                                try:
                                    from .feature_extractor import confind_size
                                    grp = str(
                                        r.get("Group") or src.get("Group") or ""
                                    ).strip()
                                    mapped = confind_size(grp or None, None, val)
                                    disp = mapped.get("display_size") or val
                                    if disp and str(disp).strip().lower() not in {"null", "nan"}:
                                        val = disp
                                except Exception:
                                    pass
                            for k in key_variants:
                                if k in r:
                                    r[k] = val
                            # Always keep the canonical description key populated.
                            if key_variants[0] == "Description":
                                r["description"] = val
                            if key_variants[0] == "Size":
                                r["size"] = val
                                if "Size" in r:
                                    r["Size"] = val
                    # Soft-delete / add marks follow the inquiry.
                    if str(src.get("_deleted", "") or "") == "1":
                        r["_deleted"] = "1"
                    elif "_deleted" in r:
                        r.pop("_deleted", None)
                    if str(src.get("_added", "") or "") == "1":
                        r["_added"] = "1"
                    elif "_added" in r:
                        r.pop("_added", None)
                    # Description/size updates above; full regex+code for every
                    # eligible row runs once in _restore_final_arranged_colors
                    # (avoids double process_text_record_live on changed rows).
                refreshed.append(r)
            table = refreshed
        # Expose pending set to the colour-restore step below via a local name
        # that the next block can see (same function scope).
        _pending_pf_crs = (pending_pf_crs | pending_pf_items
                           | pending_brand_crs | pending_brand_items)
    else:
        _pending_pf_crs = set()

    # ---- PI: always refresh size / qty / unit / description from the latest TO
    # of the same side (matched by # then Item), on BOTH edit and new version.
    # Technical may have edited these four columns on its TO; the Proforma must
    # mirror the latest TO values for every row while keeping all of its OWN
    # columns (brand, time, unit price, proforma remark, labels …).
    # Never copy TO ``ریمارک`` into PI — that field is independent. ----
    if form_kind == FormKind.PI:
        to = case.current_form(FormKind.TO, side)
        if to and to.table:
            to_by_cr = {}
            to_by_item = {}
            for tr in to.table:
                cr = str(tr.get("#", "") or tr.get("client_row", "") or "").strip()
                if cr:
                    to_by_cr[cr] = tr
                it = str(tr.get("Item") or tr.get("Item Code") or "").strip()
                if it:
                    to_by_item[it] = tr
            refreshed = []
            for r in table:
                cr = str(r.get("#", "") or r.get("client_row", "") or "").strip()
                it = str(r.get("Item") or r.get("Item Code") or "").strip()
                src = to_by_cr.get(cr) or to_by_item.get(it)
                if src is not None:
                    for key_variants in (("Size", "size"), ("Qty", "qty"),
                                          ("Unit", "unit"),
                                          ("Description", "description")):
                        val = None
                        for k in key_variants:
                            if k in src and str(src.get(k, "")).strip() != "":
                                val = src.get(k); break
                        if val is not None:
                            for k in key_variants:
                                if k in r:
                                    r[k] = val
                    # FTCO code + description + coding metadata ALWAYS mirror the
                    # latest TO (even when empty). So when technical re-codes a row
                    # after a proforma-remark round, the Proforma shows the SAME
                    # new code + FTCO description and can be priced; a row that
                    # technical has NOT re-coded stays uncoded here (and locked
                    # against pricing). Two-way sync on both edit and new version.
                    for k in ("کد", "Final Arranged Text", "Group", "Type", "Alarm_Features",
                              "Filled_Features", "Feature_Variables", "_ftco_user_edited"):
                        if k in src:
                            r[k] = src.get(k, "")
                    if str(src.get("_ftco_user_edited", "") or "") != "1":
                        r.pop("_ftco_user_edited", None)
                    # Soft-delete / add marks follow the Technical Offer.
                    if str(src.get("_deleted", "") or "") == "1":
                        r["_deleted"] = "1"
                    elif "_deleted" in r:
                        r.pop("_deleted", None)
                    if str(src.get("_added", "") or "") == "1":
                        r["_added"] = "1"
                    elif "_added" in r:
                        r.pop("_added", None)
                refreshed.append(r)
            table = refreshed

    # PI seed from TO: blank technical remark so Supply starts with an empty
    # proforma-remark field (shown later on TO as read-only PROFORMA REMARK).
    # BRAND is COPIED from TO (Technical typed it); do not blank brand.
    if blank_remark:
        for r in table:
            r["ریمارک"] = ""
            # A PI seeded from the TO must NOT inherit the TO's remark/brand-split state.
            r.pop("_remark_split", None)
            r.pop("_prev_remark", None)
            r.pop("_pf_ack", None)
            r.pop("_pf_pending", None)
            r.pop("_pf_text", None)
            r.pop("_brand_split", None)
            r.pop("_prev_brand", None)
            r.pop("_brand_ack", None)
            r.pop("_brand_pending", None)
            r.pop("_brand_pf_text", None)
            # Lock baseline = Technical brand copied onto this new PI.
            r["_brand_baseline"] = str(r.get("BRAND", "") or "")

    json_dict = load_json_file(json_path("data.json"))
    # When seeding a new Proforma from the Technical Offer (blank_remark=True),
    # keep the TO's already-coded FTCO DESCRIPTION / code / alarms as-is.
    # Re-running the regex pipeline here would rewrite Final Arranged Text and
    # break the TO→PI mirror the Supply unit must see.
    if form_kind == FormKind.TO and not blank_remark:
        _restore_final_arranged_colors(table, json_dict, skip_crs=_pending_pf_crs)

    # Point 5: once a proforma-remark round has happened for a row, split its
    # REMARK into read-only OLD (own last committed remark) + editable NEW.
    # Unhandled PF-remark rows keep code cleared and wait for Reject / Confirm.
    # Brand split mirrors the same Confirm/Reject gate on TO.
    if not blank_remark and mode in ("edit", "newversion"):
        _apply_remark_split(
            case, form_kind, side, table, mode=mode, current_form=form,
        )
        _apply_brand_split(
            case, form_kind, side, table, mode=mode, current_form=form,
        )

    for r in table:
        for k, v in list(r.items()):
            r[k] = _clean_cell_value(v)
        # Pending PF-remark / brand rows must keep an empty FTCO code after clean.
        if str(r.get("_pf_pending", "") or "") == "1":
            r["_pf_pending"] = "1"
            r["کد"] = ""
            if r.get("_pf_text") is not None:
                r["_pf_text"] = str(r.get("_pf_text") or "")
        if str(r.get("_brand_pending", "") or "") == "1":
            r["_brand_pending"] = "1"
            r["کد"] = ""
            if r.get("_brand_pf_text") is not None:
                r["_brand_pf_text"] = str(r.get("_brand_pf_text") or "")

    # Every row (prior form rows AND freshly coded added rows) now shares the
    # SAME canonical keys, so select/order by the saved form columns. This keeps
    # the exact prior values on every surviving row and renders remark/revision
    # as editable canonical columns (اصلاحیه / ریمارک). dataframe_to_html_with_ids
    # maps canonical -> display titles, so the header order matches Build TO.
    # Coding columns must stay after restore — otherwise form.columns filtering
    # silently drops freshly computed FTCO DISCRIPTION / Group / ALARM.
    columns = _ensure_coding_columns(columns, table)
    df = pd.DataFrame(table)
    cols = [c for c in (columns or []) if c in df.columns]
    if "#" in df.columns and "#" not in cols:
        cols = ["#"] + cols
    # Carry per-row feature values + flags through (consumed for data-vars / row
    # state, not shown as visible columns).
    for extra in ("Feature_Variables", "Filled_Features", "_unsuppliable", "_issue",
                  "_issue_reason", "_price_source", "_unit_price_raw", "_deleted", "_added",
                  "_service_comment", "_service_price_raw",
                  "_remark_split", "_prev_remark", "_pf_ack", "_pf_pending", "_pf_text",
                  "_remark_ack",
                  "_brand_split", "_prev_brand", "_brand_ack", "_brand_pending", "_brand_pf_text",
                  "_brand_baseline", "_ftco_user_edited"):
        if extra in df.columns and extra not in cols:
            cols.append(extra)
    for extra in _CODING_KEEP_COLUMNS:
        if extra in df.columns and extra not in cols:
            cols.append(extra)
    if cols:
        df = df[cols]
    return dataframe_to_html_with_ids(df, data_json=json_dict)


@login_required
def tool_features(request):
    """Return each FTCO code's MAIN feature values so the Proforma tool can
    populate its in-tool feature filter (even for forms saved before per-row
    feature values were persisted).

    POST items=[{"group": <group>, "code": <FTCO code>}] ->
        {"features": {code: {feature_name: value}}}
    Values are read from the per-group code DB by Item_Code (read-only)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from . import code_db
    from .models import GroupFeature
    try:
        items = json.loads(request.POST.get("items", "[]"))
    except (ValueError, TypeError):
        items = []

    # Group the requested codes by product group.
    by_group = {}
    for it in items:
        g = str((it or {}).get("group", "") or "").strip()
        c = str((it or {}).get("code", "") or "").strip()
        if g and c:
            by_group.setdefault(g, set()).add(c)

    features = {}
    for group, codes in by_group.items():
        # Main features of this group, with their raw column positions + names.
        mains = list(GroupFeature.objects.filter(group=group, kind=GroupFeature.MAIN)
                     .order_by("position", "id").values_list("column_index", "name"))
        if not mains:
            continue
        col_indices = [ci for ci, _n in mains]
        idx_to_name = {ci: n for ci, n in mains}
        rows_map = code_db.features_for_codes(group, codes, col_indices)
        for code, idx_vals in rows_map.items():
            features[code] = {idx_to_name.get(ci, str(ci)): val for ci, val in idx_vals.items()}

    return JsonResponse({"features": features})


@login_required
def tool_prices(request):
    """Pricing data for the Proforma tool.

    POST: items=[{code, qty}, ...]  (qty optional, default 1), list_id (optional).
    Returns, for the given item codes:
      - lists:      every active price list (id, name, currency)
      - prices:     {code: unit_price} for the requested list_id (if any)
      - comparison: per list -> covered count + sum of unit prices (info) AND
                    common_total = sum(unit_price x qty) over the items priced by
                    EVERY list (the fair comparison set)
      - suggestion: the list with the lowest common_total
    No coding logic is touched; this only reads CodePrice rows.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from .models import PriceList, CodePrice
    # Accept either items=[{code,qty}] (preferred) or a bare codes=[...] list.
    qty_by_code = {}
    codes = []
    try:
        items = json.loads(request.POST.get("items", "[]"))
    except Exception:
        items = []
    if items:
        for it in items:
            c = str(it.get("code", "")).strip()
            if not c:
                continue
            codes.append(c)
            try:
                q = float(it.get("qty", 1) or 0)
            except (TypeError, ValueError):
                q = 0.0
            qty_by_code[c] = qty_by_code.get(c, 0.0) + (q if q > 0 else 0.0)
    else:
        try:
            codes = [str(c).strip() for c in json.loads(request.POST.get("codes", "[]")) if str(c).strip()]
        except Exception:
            codes = []
        for c in codes:
            qty_by_code.setdefault(c, 1.0)
    code_set = set(codes)

    lists = list(PriceList.objects.filter(is_active=True).order_by("name"))
    # External PI: never surface Rial price lists (USD/EUR only).
    if (request.POST.get("external") or "").strip() in ("1", "true", "True"):
        lists = [pl for pl in lists if (pl.currency or "").lower() != "rial"]
    out_lists = [{"id": pl.id, "name": pl.name, "currency": pl.currency} for pl in lists]

    price_by_list = {}
    if code_set:
        for pl in lists:
            price_by_list[pl.id] = {cp.code: float(cp.price) for cp in
                                    CodePrice.objects.filter(price_list=pl, code__in=code_set)}

    # Common set = codes priced by EVERY active list (fair comparison).
    common = set(code_set)
    if lists:
        for pl in lists:
            common &= set(price_by_list.get(pl.id, {}).keys())
    else:
        common = set()

    def line_total(pl, c):
        return price_by_list[pl.id][c] * (qty_by_code.get(c, 1.0) or 0.0)

    comparison = []
    for pl in lists:
        m = price_by_list.get(pl.id, {})
        comparison.append({
            "id": pl.id, "name": pl.name, "currency": pl.currency,
            "covered": len(m),
            "total": round(sum(m.values()), 2),                       # sum of unit prices (info)
            "common_total": round(sum(line_total(pl, c) for c in common), 2),  # sum(price x qty) over common
        })

    suggestion = None
    if len(lists) >= 2 and common:
        best = min(lists, key=lambda pl: sum(line_total(pl, c) for c in common))
        suggestion = {"id": best.id, "name": best.name, "count": len(common),
                      "common_total": round(sum(line_total(best, c) for c in common), 2),
                      "currency": best.currency}

    prices = {}
    list_id = request.POST.get("list_id")
    if list_id:
        try:
            prices = price_by_list.get(int(list_id)) or {}
            if not prices and code_set:
                pl = PriceList.objects.filter(pk=int(list_id)).first()
                if pl:
                    prices = {cp.code: float(cp.price) for cp in
                              CodePrice.objects.filter(price_list=pl, code__in=code_set)}
        except (TypeError, ValueError):
            prices = {}

    return JsonResponse({"lists": out_lists, "prices": prices,
                         "comparison": comparison, "suggestion": suggestion})


@login_required
def tool_for_case(request, case_id, kind):
    """Open the coding/pricing tool.

    mode=build (default): seed a fresh grid from the latest inquiry (TO) or the
    latest TO descriptions (PI). mode=edit / mode=newversion: reload the last
    saved TO/PI grid so prior edits (e.g. remarks) are preserved.
    """
    from cases.models import Case
    from cases.constants import FormKind, Side, PriceType
    from cases import services
    try:
        from . import cache_sync
        cache_sync.maybe_refresh()
    except Exception:
        pass
    case = get_object_or_404(Case, pk=case_id)
    kind = kind.upper()
    mode = request.GET.get("mode", "build")
    side = request.GET.get("side", "")
    if side not in (Side.INTERNAL, Side.EXTERNAL):
        side = case.primary_side
    # For split (Internal & External) cases each side is private to its owner:
    # a user may only open the tool for a side they are allowed to act on.
    if case.is_split:
        if not services.can_act_on_side(case, request.user, side):
            from django.contrib import messages
            messages.error(request, "You can only work on your own side of this case.")
            return redirect(f"/cases/{case.pk}/")
    form_kind = FormKind.PI if kind == "PI" else FormKind.TO

    # Reconcile the requested mode with reality. Editing the current form is
    # allowed whenever it is at the current inquiry version (even if it was sent
    # and came back). A form left behind by a newer inquiry version must be
    # branched as a new version; if there is no current form yet, build one.
    current = case.current_form(form_kind, side)
    # Skip Commercial FX-only clones when deciding mode for Technical/Supply —
    # those versions were never their work; they resume from the last real TO/PI.
    if (current is not None and services.form_is_currency_conversion_only(current)
            and not services.is_currency_conversion_only(case, side or "")):
        reals = list(
            case.forms.filter(kind=form_kind, side=side or "")
            .order_by("-version", "-id")
        )
        current = next(
            (f for f in reals
             if not services.form_is_currency_conversion_only(f)), None)
    inq = case.current_form(FormKind.INQUIRY, side)
    inq_v = inq.version if inq else 0
    behind = bool(current and current.version < inq_v)
    if mode == "edit":
        if current is None:
            mode = "build"
        elif behind:
            mode = "newversion"
    elif mode == "newversion" and current is None:
        mode = "build"

    # Building/loading the grid runs the vendored coding pipeline (pandas +
    # openpyxl + the schema JSON). If anything in that pipeline raises we must
    # NOT return a 500 — the unit still needs to open the tool. We log the real
    # traceback and fall back to an empty grid so the page always loads.
    seed_error = None
    table_html = None
    try:
        if mode in ("edit", "newversion"):
            table_html = _form_grid_html(case, form_kind, side, mode=mode)
        if table_html is None and kind == "PI":
            # Pricing starts from the latest Technical Offer of the SAME side.
            # TO remark must NOT seed PI remark — blank it (independent fields).
            table_html = _form_grid_html(
                case, FormKind.TO, side, mode="build", blank_remark=True,
            )
            mode = "build"
        if table_html is None:
            rows = _to_description_rows(case, side) if kind == "PI" else _inquiry_rows(case, side)
            table_html = _seed_dataframe_html(rows)
            mode = "build"
    except Exception:
        logger.exception(
            "Failed to seed %s grid for case #%s (side=%r, mode=%r)",
            kind, case.pk, side, mode)
        seed_error = (
            "The item table could not be prepared automatically. You can still "
            "build the offer by pasting or entering rows manually.")

    json_dict = load_json_file(json_path("data.json"))
    group_options = sorted((json_dict.get("group", {}) or {}).keys())

    # When TECHNICAL builds a TO and a current Proforma exists for this side,
    # surface the supplier's per-row PROFORMA REMARK as a read-only reference
    # column. Rows are matched by the stable "Item Code" row number (the PI was
    # seeded 1:1 from the TO, so the numbers line up).
    proforma_remarks = {}
    if kind == "TO":
        pi_form = case.current_form(FormKind.PI, side)
        if pi_form is not None and pi_form.table:
            for prow in pi_form.table:
                key = str(prow.get("Item Code", "") or "").strip()
                rem = str(prow.get("ریمارک", "") or "").strip()
                if key and rem:
                    proforma_remarks[key] = rem

    # For the feature filter (both PI and TO): field names come from the feature
    # schema and are aligned with data.json extractor bases (material_group,
    # material_type, phisic_sch, size, …). Allowed values also come from the
    # schema; the dropdown only offers those that appear on the live table.
    group_main_features = {}
    group_feature_aliases = {}
    group_feature_values = {}
    # For each group: {member_feature: [ordered sibling members]} for every
    # feature that is one part of an asign_code.json compound column (e.g.
    # pipe's material/grade_material/spec all map to one column). Reuses
    # composite_features.compound_groups directly — the exact function the
    # Engineering Assistant already relies on for the same information — so
    # the filter and EA can never see two different answers to "what is this
    # feature compound-grouped with" for the same group.
    group_compound_map = {}
    if kind in ("PI", "TO"):
        from . import item_builder
        from .models import GroupFeature
        from .composite_features import compound_groups

        # Soft aliases only — never cross material_group (C.S) with material_type (SMLS).
        _FILTER_ALIASES = {
            "phisic_sch": ("phisic_sch", "schedule"),
            "schedule": ("phisic_sch", "schedule"),
            "production_method": ("material_type",),  # legacy schema name
        }

        for g in group_options:
            gl = str(g).strip().lower()
            names = []
            value_map = {}
            try:
                feats, _cfg, _s = item_builder._load_schema_maps(gl)
                names = [f["name"] for f in feats if f.get("name")]
                for f in feats:
                    nm = f.get("name")
                    if not nm:
                        continue
                    vals = [str(v) for v in (f.get("vmap") or {}).keys() if str(v).strip()]
                    if vals:
                        value_map[nm] = vals
            except Exception:
                names = []
            if not names:
                try:
                    names = list(
                        GroupFeature.objects.filter(group=gl, kind=GroupFeature.MAIN)
                        .order_by("position", "id")
                        .values_list("name", flat=True)
                    )
                except Exception:
                    names = []
            if not names:
                logger.debug("No schema/GroupFeature names for group %r", gl)
                continue
            group_main_features[gl] = names
            if value_map:
                group_feature_values[gl] = value_map
            try:
                # type_=gl: groups keyed by their own name in asign_code.json
                # (pipe -> "pipe") match exactly; every other group only has
                # an "all_in" entry, which get_by_alias falls back to for any
                # type string that isn't a literal key — gl never literally
                # matches those, so this correctly reaches "all_in" for them.
                cg = compound_groups(gl, gl)
                if cg:
                    group_compound_map[gl] = {k: list(v) for k, v in cg.items()}
            except Exception:
                logger.debug("compound_groups lookup failed for group %r", gl)

            aliases_for_g = {}
            for name in names:
                als = []
                lookup = str(name).strip().lower().replace(" ", "_")
                for a in _FILTER_ALIASES.get(lookup, ()):
                    if a and a not in als and a != name and a != lookup:
                        als.append(a)
                if lookup and lookup not in als and lookup != name:
                    als.append(lookup)
                if als:
                    aliases_for_g[name] = als
            if aliases_for_g:
                group_feature_aliases[gl] = aliases_for_g

    # Restore the saved calc state (currency conversion + margins) so an EDIT
    # shows exactly what was saved, and a NEW VERSION carries the latest one
    # forward. Build mode starts clean.
    saved_calc = None
    if mode in ("edit", "newversion") and current is not None:
        try:
            saved_calc = (current.meta or {}).get("calc")
        except Exception:
            saved_calc = None

    # Supply users must never see the client name anywhere.
    profile = getattr(request.user, "profile", None)
    hide_client = bool(profile and profile.unit == "SUPPLY")

    # Technical unit users (Experts, Supervisors, and Managers alike — this is
    # not a management-only restriction) must never see financial figures in
    # the Proforma: Subtotal/VAT/Grand Total and the Unit/Service/Total Price
    # columns. Privileged accounts (Admin, General Manager) always carry a
    # blank unit by construction (see accounts.forms), so this can never
    # accidentally also apply to them.
    hide_pricing = bool(kind == "PI" and profile and profile.unit == "TECHNICAL")
    pricing_applied = False
    if hide_pricing and table_html:
        if current is not None and current.table:
            pricing_applied = any(
                str((r or {}).get("UNIT PRICE", "") or "").strip()
                for r in current.table
            )
        table_html = mask_price_columns(table_html)

    # Give the tool the same per-unit accent the rest of the site uses.
    from core.theming import theme_for_unit
    from cases.export_data import vat_percent as _vat_percent
    from django.conf import settings as _dj_settings
    unit_code = (profile.unit if profile and not profile.is_admin else "ADMIN") or "ADMIN"
    unit_theme = theme_for_unit(unit_code)

    return render(request, "itemcoder/tool_case.html", {
        "case": case,
        "kind": kind,
        "doc_kind": case.kind,
        "side": side,
        "side_label": Side.LABELS.get(side, ""),
        "table_html": table_html,
        "seed_error": seed_error,
        "calculation_ui_config": json.dumps(get_calculation_ui_config(), ensure_ascii=False),
        "group_options": group_options,
        "hide_client": hide_client,
        "hide_pricing": hide_pricing,
        "pricing_applied": pricing_applied,
        "unit_theme": unit_theme,
        "save_url": f"/tool/case/{case.pk}/{kind}/save/?side={side}",
        "new_version_default": "1" if mode == "newversion" else "0",
        "tool_mode": mode,
        # Passed as plain Python objects and rendered with Django's ``json_script``
        # (unicode-escapes </script> etc.) instead of |safe, so a stray character
        # in the data can never break out of the JSON block.
        "proforma_remarks_map": proforma_remarks,
        "proforma_remark_count": len(proforma_remarks),
        "group_features_map": group_main_features,
        "group_feature_aliases": group_feature_aliases,
        "group_feature_values": group_feature_values,
        "group_compound_map": group_compound_map,
        # Saved currency-conversion + margins for this version (restored on edit,
        # carried into a new version). Rendered via json_script (safe).
        "saved_calc_map": saved_calc,
        # When the case offer type is TO-only (not TO & PI), the Proforma's
        # UNIT PRICE / TOTAL PRICE must be fully locked — no manual entry, no
        # price list. Pricing is allowed only when the case needs pricing.
        "pricing_locked": (kind == "PI" and not case.needs_pricing),
        "require_ftco_code": bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", True)),
        # External side/case: PI currency is USD/EUR only (no Rial).
        "external_currency": (
            kind == "PI" and (
                side == Side.EXTERNAL
                or (not side and case.price_type == PriceType.EXTERNAL)
            )
        ),
        # After a workflow handoff back to Supply, commercial locks from the
        # prior PI session must open until the user types in New again.
        "unlock_commercial": (
            kind == "PI"
            and mode in ("edit", "newversion")
            and current is not None
            and _should_open_remark_brand_round(case, current)
        ),
        "vat_percent": _vat_percent(),
    })


@login_required
def tool_for_case_status(request, case_id, kind):
    """Lightweight poll for an open tool: is this case/side still editable?

    On a split (Internal & External) case, Final-Approving one side CANCELS the
    other side immediately (terminal). A user actively building the TO (Technical)
    or PI (Supply) on the cancelled side must be thrown out of the tool at once —
    not only blocked when they finally press Save. The tool page polls this and
    redirects to the case as soon as the side/case turns terminal.
    """
    from cases.models import Case
    from cases.constants import Side, CaseStatus

    case = Case.objects.filter(pk=case_id).first()
    if case is None:
        return JsonResponse({"active": False, "redirect": "/",
                             "reason": "This case no longer exists."})

    side = request.GET.get("side", "")
    if side not in (Side.INTERNAL, Side.EXTERNAL):
        side = case.primary_side

    active, reason = True, ""
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        st = case.side_status(side)
        if st in CaseStatus.TERMINAL:
            active = False
            reason = (f"The {Side.LABELS.get(side, side)} side has been closed "
                      f"({CaseStatus.LABELS.get(st, st)}). Editing is no longer possible.")
    elif case.status in CaseStatus.TERMINAL:
        active = False
        reason = (f"This case has been closed ({case.status_label}). "
                  f"Editing is no longer possible.")

    return JsonResponse({
        "active": active,
        "reason": reason,
        "redirect": f"/cases/{case.pk}/",
    })


@login_required
def save_from_tool(request, case_id, kind):
    """Store the finished tool grid as a versioned TO/PI on the case."""
    from cases import services
    from cases.constants import FormKind
    from cases.models import Case

    case = get_object_or_404(Case, pk=case_id)
    kind = kind.upper()
    if request.method != "POST":
        return redirect("cases:case_detail", pk=case.pk)

    from cases.constants import Side
    form_kind = FormKind.PI if kind == "PI" else FormKind.TO
    side = request.GET.get("side", "") or request.POST.get("side", "")
    if side not in (Side.INTERNAL, Side.EXTERNAL):
        side = case.primary_side

    # Permission: the user must currently be allowed to build this form.
    from people.role_nav import work_context
    ctx = work_context(request)
    allowed = services.allowed_actions(
        case, request.user, role=ctx.role, work_user=ctx.seat_user,
    )
    needed = "build_pi" if form_kind == FormKind.PI else "build_to"
    permitted = needed in allowed or "open_assistant" in allowed
    # Split cases freeze the case status, so the whole-case permission above does
    # not see per-side building; authorise it explicitly per side instead.
    if not permitted and case.is_split:
        holder = case.side_holder(side)
        profile = getattr(request.user, "profile", None)
        role = ctx.role
        unit = (role.unit if role is not None else (profile.unit if profile else "")) or ""
        role_name = (role.role if role is not None else (profile.role if profile else "")) or ""
        seat_id = getattr(ctx.seat_user, "id", None)
        if form_kind == FormKind.PI:
            permitted = (unit == "SUPPLY"
                         and holder == "SUPPLY"
                         and services.can_act_on_side(
                             case, request.user, side, role=ctx.role, work_user=ctx.seat_user))
        else:  # TO
            tech_owns = (unit == "TECHNICAL"
                         and (role_name == "MANAGER"
                              or case.technical_assignee_id == seat_id))
            permitted = bool(tech_owns and holder == "TECHNICAL")
    if not permitted:
        messages.error(request, "You are not allowed to build this form right now.")
        return redirect("cases:case_detail", pk=case.pk)
    # On split cases, only the side's owner may save that side.
    if case.is_split and not services.can_act_on_side(
            case, request.user, side, role=ctx.role, work_user=ctx.seat_user):
        messages.error(request, "You can only work on your own side of this case.")
        return redirect("cases:case_detail", pk=case.pk)

    try:
        columns = json.loads(request.POST.get("columns", "[]"))
        table = json.loads(request.POST.get("table", "[]"))
        meta = json.loads(request.POST.get("meta", "{}"))
    except json.JSONDecodeError:
        messages.error(request, "The tool sent malformed data; nothing was saved.")
        return redirect("cases:case_detail", pk=case.pk)

    # Normalize FTCO DISCRIPTION on save: manual edits stay plain text; any
    # escaped colour markup that would reopen as visible <span> tags is stripped.
    if isinstance(table, list):
        for row in table:
            if not isinstance(row, dict):
                continue
            ftco = row.get("Final Arranged Text")
            if ftco is None:
                continue
            flagged = str(row.get("_ftco_user_edited", "") or "") == "1"
            text = str(ftco)
            low = text.lower()
            escaped = ("&lt;" in low and any(t in low for t in ("span", "bdi", "br")))
            if flagged or escaped:
                row["Final Arranged Text"] = _plain_ftco_text(text)

    mode = request.POST.get("mode", "build")
    current = case.current_form(form_kind, side)
    inq = case.current_form(FormKind.INQUIRY, side)
    inq_v = inq.version if inq else 0
    # A form left behind by a newer inquiry version must become a new version;
    # a current form may be edited in place even after it was sent and returned.
    if mode == "edit" and current is not None and current.version < inq_v:
        mode = "newversion"
    is_edit = (mode == "edit")
    if mode == "newversion":
        new_version = True
    elif mode == "edit":
        new_version = False
    else:  # build: start a new version only if one already exists
        new_version = current is not None
    services.save_form(case, kind=form_kind, columns=columns, table=table,
                       meta=meta, actor=request.user, side=side,
                       new_version=new_version, is_edit=is_edit)
    label = (" (" + Side.LABELS.get(side, "") + ")") if side else ""
    messages.success(request, f"{kind}{label} saved.")
    return redirect("cases:case_detail", pk=case.pk)
