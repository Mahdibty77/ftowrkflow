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
import re
from html.parser import HTMLParser

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .calculation_customizer import get_calculation_ui_config


PRICE_COLUMNS = {"UNIT PRICE", "SERVICE PRICE", "TOTAL PRICE"}

# The renderer does not keep the supplier's raw figures in the price cells only:
# views.dataframe_to_html_with_ids also stamps them on the <tr> itself, because
# the pricing JS restores a row's editable base from there. Blanking just the
# <td>s would therefore still ship those numbers to a Technical viewer in the
# page source — the exact "visible in dev tools" leak the masking exists to
# close — so the row attributes are removed with them. Every one of these is
# written with a Django-escaped value inside double quotes, so a plain
# attribute match is enough and can never swallow the rest of the tag.
_ROW_PRICE_ATTR_RE = re.compile(
    r'\s(?:data-row-price-source|data-row-unit-raw'
    r'|data-service-price-raw|data-row-service-raw)="[^"]*"',
    re.I,
)


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

    The same reasoning covers the price data-* attributes on each <tr> (see
    ``_ROW_PRICE_ATTR_RE``): a figure that only the page source reveals is
    still a figure the viewer was not meant to receive.
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
            if tag == "tr":
                self.out.append(_ROW_PRICE_ATTR_RE.sub("", self.get_starttag_text()))
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

# ---------------------------------------------------------------------------
# Technical's Qty / Unit override on the Technical Offer
# ---------------------------------------------------------------------------
# Qty and Unit belong to the CLIENT: the inquiry owns them, and the TO copies
# them down from the latest inquiry of the same side on every open so a TO
# always shows what the client last asked for. That copy-down is the whole
# reason a value typed into those two cells on the TO used to disappear on the
# next open — it is not a UI lock, it is an overwrite.
#
# The intent of the copy-down is preserved exactly: every row still follows the
# inquiry. A row only stops following it for the ONE column Technical actually
# retyped, and only from the save that retyped it. The mark is per row and per
# column, it is recomputed from the submitted values on every TO save (see
# ``_apply_qty_unit_rules``), and it clears itself the moment Technical puts the
# inquiry's own value back — so nothing has to be un-stuck by hand.
_QTY_OVERRIDE_KEY = "_qty_override"
_UNIT_OVERRIDE_KEY = "_unit_override"
# canonical grid column -> the mark that frees it from the inquiry copy-down.
_OVERRIDE_KEY_BY_COL = {"qty": _QTY_OVERRIDE_KEY, "unit": _UNIT_OVERRIDE_KEY}

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


def _seed_dataframe_frame(rows):
    """Run inquiry rows through the tool pipeline -> ``(DataFrame, json_dict)``.

    The FRAME is the grid one step before it becomes HTML, split out for the
    same reason ``_form_grid_frame`` is: the Qty / Unit guard has to compare a
    save
    against the values THAT PAGE WAS RENDERED WITH, and the only way to be sure
    of those is to ask the function that renders them. ``(None, None)`` when
    there is nothing to seed from.
    """
    if not rows:
        return None, None
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
        return None, None

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
    return result_df, json_dict


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
        return not bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", False))
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


def _form_grid_frame(case, form_kind, side=None, mode="edit", *, blank_remark=False):
    """Rebuild an already-saved TO/PI grid -> ``(DataFrame, json_dict)``.

    THIS is where the grid the user sees is decided: every copy-down, every
    soft-delete, every remark split. ``_form_grid_html`` only paints what comes
    out of here, so a caller that needs to know what the page showed — the
    Qty / Unit guard in ``save_from_tool`` — asks this function rather than
    working the answer out a second time. ``(None, None)`` when there is no
    saved form of this kind on this side.

    This function was ``_form_grid_html`` until the frame was split out of it
    (cases/export_data.py still refers to it by that name); the rest of this
    docstring is that function's, unchanged, and describes what the frame holds.

    Re-render an already-saved TO/PI grid (with its remarks/edits) so the
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
        return None, None
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
                        # Technical retyped this ONE cell on this ONE row, so the
                        # inquiry no longer owns it. Every other row and every
                        # other column still mirrors the latest inquiry, which is
                        # what this whole block is for. Without this the value
                        # saved a moment ago is silently replaced on reopen.
                        _ov = _OVERRIDE_KEY_BY_COL.get(key_variants[1])
                        if _ov and str(r.get(_ov, "") or "") == "1":
                            continue
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
                  "_brand_baseline", "_ftco_user_edited",
                  _QTY_OVERRIDE_KEY, _UNIT_OVERRIDE_KEY):
        if extra in df.columns and extra not in cols:
            cols.append(extra)
    for extra in _CODING_KEEP_COLUMNS:
        if extra in df.columns and extra not in cols:
            cols.append(extra)
    if cols:
        df = df[cols]
    return df, json_dict


def _tool_grid_frame(case, form_kind, side, mode):
    """The grid the tool page shows, and the mode it settles in.

    ONE definition with TWO callers, and that is the whole point of it.
    ``tool_for_case`` paints this frame into the page; ``_qty_unit_baseline``
    reads out of the same frame what Qty / Unit the user was shown. A guard
    that refuses a POST for changing a quantity has to be answering the page
    the quantity came from. Anything that works out "what the grid would have
    said" a second time is a second definition of the truth, and it drifts from
    the first the moment either copy-down changes — which is exactly what
    happened: the Proforma page mirrors the Technical Offer's STORED Qty, while
    the guard rebuilt the Technical Offer with the CURRENT inquiry laid over
    it, so a Supply seat opening a Proforma it had never touched was refused
    for changing a quantity it never typed.

    ``mode`` comes in as the mode the page was rendered in and comes back out
    because the two fallbacks below change it: a Proforma with no version yet
    is seeded from the Technical Offer, and a form with nothing to restore is
    seeded from the inquiry. Both land the page in "build", and the page posts
    that back, so passing the posted mode in here reproduces the same choice.
    """
    from cases.constants import FormKind

    df = json_dict = None
    if mode in ("edit", "newversion"):
        df, json_dict = _form_grid_frame(case, form_kind, side, mode=mode)
    if df is None and form_kind == FormKind.PI:
        # Pricing starts from the latest Technical Offer of the SAME side.
        # TO remark must NOT seed PI remark — blank it (independent fields).
        df, json_dict = _form_grid_frame(case, FormKind.TO, side, mode="build",
                                         blank_remark=True)
        mode = "build"
    if df is None:
        rows = (_to_description_rows(case, side) if form_kind == FormKind.PI
                else _inquiry_rows(case, side))
        df, json_dict = _seed_dataframe_frame(rows)
        mode = "build"
    return df, json_dict, mode


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
    # Same rule as the Proforma's price masking in tool_for_case: a Technical
    # user must never receive a financial figure. Masking the PI page alone left
    # this endpoint as the way around it — Technical reads the FTCO codes off its
    # own Technical Offer and asks here for the supplier price behind each one.
    # Privileged accounts (Admin / General Manager) carry a blank unit by
    # construction, so this can never catch them.
    _profile = getattr(request.user, "profile", None)
    if _profile is not None and _profile.unit == "TECHNICAL":
        return JsonResponse({"error": "Not allowed"}, status=403)
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


def _qu_keys(row):
    """(client number, item number) for a grid row — the two keys the copy-down
    in ``_form_grid_frame`` matches rows on, read exactly the same way.

    Rows reach this from two places: a POST, where every value is a string, and
    a rendered frame, where pandas has densified the record and a key missing
    on some rows comes back as NaN. ``prepare_table_cell`` blanks those before
    they are painted, so blank them here too — matching on the text "nan" would
    invent a key the page never showed.
    """
    if not isinstance(row, dict):
        return "", ""

    def first(*names):
        for n in names:
            if n not in row:
                continue
            v = row.get(n)
            try:
                if v is None or pd.isna(v):
                    continue
            except (TypeError, ValueError):
                pass
            text = str(v).strip()
            if text and text.lower() not in ("nan", "none", "<na>"):
                return text
        return ""

    return first("#", "client_row"), first("Item", "Item Code")


def _qu_of(row):
    """The row's Qty / Unit under any of the key spellings a snapshot may use."""
    def pick(*names):
        for n in names:
            if n in row and str(row.get(n, "") or "").strip() != "":
                return str(row.get(n)).strip()
        return ""
    return pick("Qty", "qty"), pick("Unit", "unit")


def _strip_qu_in_place(row):
    """Trim the row's OWN Qty / Unit cells, so the value stored is the value judged.

    ``_qu_of`` reads these two columns stripped, and everything downstream —
    the comparison against the rendered baseline, ``validate_qty_unit``, the
    override marks — therefore judges the trimmed text. The row itself was left
    exactly as it arrived, so a direct POST of ``qty=" 12 "`` passed a rule
    applied to "12" and then stored " 12 ": a snapshot value nothing had
    checked. A browser cannot produce it (the cell is read back with
    ``textContent`` and trimmed), which is precisely why it must be handled
    here rather than there.

    Each key is trimmed in its own place — never copied between the "Qty" and
    "qty" spellings — so a row that carries one empty spelling and one filled
    one keeps saying what it said.
    """
    if not isinstance(row, dict):
        return
    for key in ("Qty", "qty", "Unit", "unit"):
        val = row.get(key)
        if isinstance(val, str) and val != val.strip():
            row[key] = val.strip()


def _index_qu(rows):
    """``{client number: row}`` and ``{item number: row}`` for a stored table.

    Only the inquiry is indexed this way now — the override marks below say
    whether a row still matches the CLIENT's value, which is a question about
    the inquiry and not about what the page showed.
    """
    by_cr, by_item = {}, {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cr, it = _qu_keys(r)
        if cr and cr not in by_cr:
            by_cr[cr] = r
        if it and it not in by_item:
            by_item[it] = r
    return by_cr, by_item


def _rendered_cell_text(col, val, row=None, data_json=None):
    """The text the BROWSER reads out of this cell.

    ``prepare_table_cell`` is the server's half of the paint — the very call
    ``dataframe_to_html_with_ids`` makes for every cell — and tool_save.js's
    collect() reads the result back with ``textContent``: tags gone, entities
    decoded, ends trimmed. Doing both of those here is what makes a value in
    the rendered frame and a value in the POST comparable at all, and it is
    done by CALLING the painter rather than by guessing what it would emit.
    """
    import html as _html_mod
    import re as _re

    from .Initial_changes import prepare_table_cell

    painted = str(prepare_table_cell(col, val, row=row, data_json=data_json) or "")
    painted = _re.sub(r"<br\s*/?>", " ", painted, flags=_re.I)
    return _html_mod.unescape(_re.sub(r"<[^>]+>", "", painted)).strip()


def _rendered_qu(record, data_json=None):
    """One frame record's Qty / Unit AS PAINTED, under any key spelling.

    Same first-non-blank order as ``_qu_of`` reads the POST in, so the two
    sides of the comparison are picked the same way.
    """
    def pick(*names):
        for n in names:
            if n in record:
                text = _rendered_cell_text(n, record.get(n), row=record,
                                           data_json=data_json)
                if text:
                    return text
        return ""

    return pick("Qty", "qty"), pick("Unit", "unit")


def _qty_unit_baseline(case, form_kind, side, mode):
    """What Qty / Unit the grid this save is answering WAS RENDERED WITH.

    ``save_from_tool`` has to be able to say "this POST changed a quantity",
    and the only honest way to say it is to know what the page showed. That is
    not the stored snapshot: ``_form_grid_frame`` copies Qty / Unit down from
    the inquiry (TO) or from the Technical Offer (PI) before the grid is
    painted, so the saved row and the shown row routinely disagree.

    So this does not work the copy-downs out again — it asks
    ``_tool_grid_frame``, the function ``tool_for_case`` paints the page from,
    for the same frame, using the mode the page posted back, and reads the two
    columns out of it through the same painter the cells went through. There is
    no second description of what the grid says, so there is nothing left to
    drift: whatever the page showed is what a save is measured against, today
    and after the next change to either copy-down.

    Returns ``(by_client_row, by_item)``, each ``{key: (qty, unit)}``.
    """
    df, json_dict, _mode = _tool_grid_frame(case, form_kind, side, mode)
    if df is None:
        return {}, {}

    by_cr, by_item = {}, {}
    for record in df.to_dict("records"):
        cr, it = _qu_keys(record)
        qu = _rendered_qu(record, json_dict)
        if cr and cr not in by_cr:
            by_cr[cr] = qu
        if it and it not in by_item:
            by_item[it] = qu
    return by_cr, by_item


def _active_unit(request):
    """The unit of the seat making this request (active role first, then login).

    Same order ``_may_build_form`` reads it in, for the same reason: one person
    may hold several seats and a secondary seat deliberately does not rewrite
    the login Profile.
    """
    from people.role_nav import work_context

    ctx = work_context(request)
    profile = getattr(request.user, "profile", None)
    if ctx.role is not None:
        return (ctx.role.unit or "")
    return (profile.unit if profile else "") or ""


def _apply_qty_unit_rules(request, case, form_kind, side, table, mode):
    """Authorise and validate the Qty / Unit values in a TO/PI save. -> reason or "".

    Qty and Unit are the client's columns everywhere except one place: the owner
    asked that the TECHNICAL unit be able to change them ON THE TECHNICAL OFFER,
    "respecting the rules that exist for quantity and unit". Three things follow,
    and all three are decided here, on the server, because the browser is only
    ever a courtesy:

      * Anybody else — Supply on a Proforma, Commercial, a Technical seat working
        a PI, a read-only viewer who forged the POST — may not move either value.
        Their save is refused outright rather than quietly re-writing the row,
        so a hand-made POST is turned away and not merely hidden from.
      * A changed value must satisfy the SAME Qty / Unit rules a case creation
        applies (``cases.inquiry_validate``) — one definition, not a second one
        that could drift from the inquiry grid's.
      * A TO row whose Qty / Unit no longer matches the inquiry is marked, so the
        inquiry copy-down in ``_form_grid_frame`` leaves that one cell alone and
        the value survives the next open. The mark is recomputed here from the
        submitted values on every TO save, by whoever saves, so it always states
        the truth about the row rather than remembering a claim the client made.

    ``mode`` is the mode the page posted back, and it is here for one reason:
    ``_qty_unit_baseline`` needs it to ask for the SAME frame the page was
    painted from. "Changed" means "differs from what this page showed", never
    "differs from something recomputed on the side".

    SIZE IS NOT GUARDED BY VALUE, AND THAT IS A DECISION, NOT AN OMISSION.
    It is not opened for editing either — the owner asked for two columns, not
    three, so no seat is handed a SIZE editor. The tempting next step is to
    refuse a SIZE that differs from the painted one on the grounds that nobody
    could have typed it. That would be wrong, because a size CAN legitimately
    change without anyone typing in the size cell: row_processor.js paints
    ``Size_Override`` into that cell whenever the coder finds a size token in
    the row's Remark / Revision text, and the very next save posts the new
    value back. Guarding SIZE against the render would refuse that ordinary
    Technical save — the same false refusal this function was just repaired
    for, moved one column across. Qty and Unit have no such path: no script
    writes them, so a difference there really is either the Technical editor or
    a forged POST. The residue is that a hand-made POST can still set a SIZE
    string, on a form its author is already authorised to write; closing that
    needs the override to be a value the server can recompute, not a guess.
    """
    from cases.constants import FormKind
    from cases.inquiry_validate import validate_qty_unit

    if not isinstance(table, list):
        return ""

    is_to = (form_kind == FormKind.TO)
    may_change = is_to and _active_unit(request) == "TECHNICAL"

    by_cr, by_item = _qty_unit_baseline(case, form_kind, side, mode)
    inq = case.current_form(FormKind.INQUIRY, side)
    inq_cr, inq_item = _index_qu(inq.table if inq else [])

    errors = []
    changed = []
    for i, row in enumerate(table, start=1):
        if not isinstance(row, dict):
            continue
        cr, it = _qu_keys(row)
        label = cr or it or i
        # Judge and store the same text — see ``_strip_qu_in_place``.
        _strip_qu_in_place(row)
        qty, unit = _qu_of(row)

        base = by_cr.get(cr) if cr in by_cr else by_item.get(it)
        moved_qty = base is not None and qty != base[0]
        moved_unit = base is not None and unit != base[1]

        if (moved_qty or moved_unit) and not may_change:
            changed.append(str(label))
        elif moved_qty or moved_unit:
            errors.extend(validate_qty_unit(qty, unit, label))

        if not is_to:
            continue
        # Re-derive the override marks for EVERY row of a TO save. A row that
        # matches the inquiry follows it again; a row that does not is pinned.
        src = inq_cr.get(cr) or inq_item.get(it)
        inq_qty, inq_unit = _qu_of(src) if src else ("", "")
        for key, value, inq_value in ((_QTY_OVERRIDE_KEY, qty, inq_qty),
                                      (_UNIT_OVERRIDE_KEY, unit, inq_unit)):
            if inq_value and value != inq_value:
                row[key] = "1"
            else:
                row.pop(key, None)

    if changed:
        shown = ", ".join(changed[:8]) + ("…" if len(changed) > 8 else "")
        what = "Technical Offer" if is_to else "Proforma"
        return (
            f"Qty and Unit cannot be changed on this {what}. They are the "
            f"client's columns; only the Technical unit may change them, and "
            f"only on the Technical Offer. Nothing was saved. "
            f"(Rows: {shown}.)"
        )
    if errors:
        return "Qty / Unit are invalid; nothing was saved:\n" + "\n".join(errors)
    return ""


def _own_side_refusal(case, side):
    """The refusal shown when a seat asks for a side that is not theirs.

    The first sentence is the message this refusal has always carried, word for
    word. What is added is the reason, and it is added because the refusal on
    its own is unanswerable: a Supply manager who had delegated the side read
    "your own side" as a malfunction — the case is their unit's, the case page
    had offered the button a moment earlier (it was loaded before the side was
    delegated), and nothing told them that delegating a side is what hands it
    over. Naming the expert turns the same refusal into an instruction.

    Nothing here decides anything. It runs only after the caller has already
    refused, it reads only columns the case page prints on the same panel, and
    it returns text.
    """
    from accounts.constants import Unit
    from cases.constants import Side

    label = Side.LABELS.get(side, side) or "this"
    # Read the assignee that belongs to the unit HOLDING this side, never a
    # leftover column from a unit the side has since left — otherwise a side
    # sitting with Technical would be explained by the Supply expert who priced
    # it three handoffs ago.
    holder = case.side_holder(side)
    who = None
    if holder == Unit.SUPPLY:
        who = (case.supply_internal_assignee if side == Side.INTERNAL
               else case.supply_external_assignee if side == Side.EXTERNAL
               else case.supply_assignee)
    elif holder == Unit.TECHNICAL:
        who = case.technical_assignee
    base = "You can only work on your own side of this case."
    if who is not None:
        name = who.get_full_name() or who.username
        return (f"{base} The {label} side is assigned to {name}; once a side is "
                f"assigned to an expert, only that expert can build or revise "
                f"its forms.")
    return (f"{base} The {label} side is not with your unit right now, so its "
            f"forms cannot be opened from here.")


def _may_build_form(request, case, form_kind, side):
    """May this request's active seat WRITE this TO/PI right now? -> (ok, reason).

    ONE definition with TWO callers, and that is the whole point of extracting
    it. ``save_from_tool`` refuses when it says no, and ``tool_for_case`` opens
    the grid READ-ONLY when it says no. Because the page's read-only state is
    the exact negation of the check that guards the save endpoint, a viewer's
    hand-crafted POST is turned away by the very rule that put their page in
    that mode — there is no second, softer copy of the rule that could drift.
    Disabling the inputs in the browser is only a courtesy layered on top; this
    function is what actually decides.

    ``reason`` is the message to show; the two texts are kept distinct exactly
    as they were before this was one function.
    """
    from cases import services
    from cases.constants import FormKind
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
        return False, "You are not allowed to build this form right now."
    # On split cases, only the side's owner may save that side.
    if case.is_split and not services.can_act_on_side(
            case, request.user, side, role=ctx.role, work_user=ctx.seat_user):
        return False, _own_side_refusal(case, side)
    return True, ""


@login_required
def tool_for_case(request, case_id, kind):
    """Open the coding/pricing tool.

    mode=build (default): seed a fresh grid from the latest inquiry (TO) or the
    latest TO descriptions (PI). mode=edit / mode=newversion: reload the last
    saved TO/PI grid so prior edits (e.g. remarks) are preserved.

    A unit that cannot write this form right now — Technical or Supply looking
    at their own offer after the case has moved on — gets the SAME grid in
    READ-ONLY mode instead of being turned away: same rows, same filters, no
    way to change a value. See ``_may_build_form`` for why that cannot be
    written through.
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
    # The grid this view renders IS the case: client descriptions, FTCO codes,
    # brands and — on a Proforma — the supplier prices. Opening it must therefore
    # require at least what opening /cases/<id>/ requires, and that rule lives in
    # one place (services.user_can_view_case, shared with the export routes).
    # Without this check a user of an uninvolved unit who is turned away from the
    # case page could still walk case ids through /tool/case/<id>/PI/ and read
    # every row. The active role's seat is used so a substitute keeps the access
    # the seat they are covering has — the same context save_from_tool authorises
    # against, so nobody who may build a form can be refused here.
    from people.role_nav import work_context
    _ctx = work_context(request)
    if not services.user_can_view_case(case, request.user,
                                       role=_ctx.role, work_user=_ctx.seat_user):
        messages.error(request, "You do not have access to this case.")
        return redirect("cases:inbox")
    kind = kind.upper()
    mode = request.GET.get("mode", "build")
    side = request.GET.get("side", "")
    if side not in (Side.INTERNAL, Side.EXTERNAL):
        side = case.primary_side
    # For split (Internal & External) cases each side is private to its owner:
    # a user may only open the tool for a side they are allowed to act on.
    if case.is_split:
        # Ask about the ACTIVE ROLE and the seat being worked, not the bare
        # login. One person may hold several seats (people.role_nav.work_context
        # / PersonRole / SeatTenure) and a secondary seat deliberately does not
        # rewrite the login Profile, so ``request.user.profile`` still describes
        # the seat they are NOT working — which turned away, for example, a
        # Supply manager opening a Proforma through their manager seat. This is
        # the same context ``_may_build_form`` and ``save_from_tool`` already
        # authorise against (and the ``user_can_view_case`` check above), so the
        # page, its read-only flag and the save endpoint now agree on who is
        # asking. ``can_act_on_side`` itself is untouched: only the identity put
        # to it changes, and a seat that may not act on this side is refused
        # exactly as before.
        if not services.can_act_on_side(case, request.user, side,
                                        role=_ctx.role,
                                        work_user=_ctx.seat_user):
            # NB: no local ``from django.contrib import messages`` here. This
            # module already imports it at the top, and re-importing it inside
            # the function made ``messages`` a local name for the WHOLE function
            # — so the earlier user_can_view_case refusal above raised
            # UnboundLocalError and returned 500 instead of its redirect.
            messages.error(request, _own_side_refusal(case, side))
            return redirect(f"/cases/{case.pk}/")
    form_kind = FormKind.PI if kind == "PI" else FormKind.TO

    # READ ONLY (the "View" control on the case page). A seat that may not save
    # this form gets to look at it and nothing else. Deriving the flag from the
    # save endpoint's own authorisation — rather than from a ``mode=view`` in
    # the query string, which the visitor controls — is what makes the mode
    # honest: it is on exactly when a save would be refused, so it can never be
    # turned off by editing the URL, and a save can never slip past it.
    read_only = not _may_build_form(request, case, form_kind, side)[0]

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
            # ``-two_stage`` keeps the two-stage generation ahead of the
            # same-numbered version it supersedes; without it the two rows are
            # tied on version and the database decides which is "latest".
            case.forms.filter(kind=form_kind, side=side or "")
            .order_by("-version", "-two_stage", "-id")
        )
        current = next(
            (f for f in reals
             if not services.form_is_currency_conversion_only(f)), None)
    inq = case.current_form(FormKind.INQUIRY, side)
    # "Behind" is not only a lower version NUMBER: a two-stage upgrade leaves
    # the number alone and changes the generation, and that snapshot must be
    # branched as a new version too, not edited in place. See
    # services.form_behind_inquiry for the single definition.
    behind = services.form_behind_inquiry(current, inq)
    if mode == "edit":
        if current is None:
            mode = "build"
        elif behind:
            mode = "newversion"
    elif mode == "newversion" and current is None:
        mode = "build"
    if read_only and current is not None:
        # A viewer looks at what WAS saved, never at a branch of it. The
        # newversion pass drops rows the inquiry deleted and appends freshly
        # coded ones — a useful starting point for somebody about to save, and
        # a misleading picture for somebody who cannot.
        mode = "edit"

    # Building/loading the grid runs the vendored coding pipeline (pandas +
    # openpyxl + the schema JSON). If anything in that pipeline raises we must
    # NOT return a 500 — the unit still needs to open the tool. We log the real
    # traceback and fall back to an empty grid so the page always loads.
    seed_error = None
    table_html = None
    # QTY and UNIT open for the TECHNICAL unit, in the TO tool, only.
    #
    # Three conditions, and each is doing work. ``kind == "TO"`` keeps the
    # Proforma exactly as locked as it is today, for Supply and everyone else.
    # ``not read_only`` is the same authorisation the save endpoint applies, so a
    # viewer is never handed an editor for a value they could not save. And the
    # unit test is the owner's actual instruction — the Technical unit and no
    # other — stated here rather than inferred from the fact that today only
    # Technical can build a TO.
    #
    # SIZE stays out: the owner asked for two columns, not three.
    qty_unit_editable = (
        ("qty", "unit")
        if kind == "TO" and not read_only and _active_unit(request) == "TECHNICAL"
        else None
    )
    try:
        # Which rows this page shows, and which mode it ends up in, is decided
        # in ``_tool_grid_frame`` — the same function the Qty / Unit guard reads
        # its baseline out of, so the page and the rule that judges its POST can
        # never describe the grid differently. Everything left to do here is
        # paint, and the only thing painting decides is which cells are open.
        grid_df, grid_json, mode = _tool_grid_frame(case, form_kind, side, mode)
        if grid_df is not None:
            table_html = dataframe_to_html_with_ids(
                grid_df, data_json=grid_json,
                editable_columns=qty_unit_editable,
            )
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

    if hide_pricing:
        # The saved calc state is the other half of the pricing, and it does not
        # travel in the table HTML: tool_case.html emits it through json_script
        # as #ft-saved-calc, so masking the price cells and the row price
        # attributes above still shipped a Technical viewer the margin
        # percentages and the FX rate this Proforma was built with — the same
        # "only the page source shows it" leak mask_price_columns exists to
        # close, one element further down the page.
        #
        # KEEP-list, not a drop-list, for the same reason the masking above
        # replaces the whole <td> rather than its text: a key added to
        # CalcSerializeState later must not leak by default. What is kept is
        # only the currency IDENTITY of the document — which currency it is
        # written in — because calculation_controls.js restores the conversion
        # selects from it and the grid's currency labels follow those. No
        # amount, percentage or rate rides in any of the three:
        #   from     — the currency the prices were entered in
        #   to       — the currency the document is shown in
        #   currency — the unit label painted next to a figure
        # Dropped: ``rate`` (the FX rate used), ``groupMargins`` (the per-group
        # margin steps) and ``rowMargins`` (the per-row overrides).
        if isinstance(saved_calc, dict):
            saved_calc = {k: v for k, v in saved_calc.items()
                          if k in ("from", "to", "currency")}

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
        "read_only": read_only,
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
        "require_ftco_code": bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", False)),
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


class _AutosaveWouldVersion(Exception):
    """Raised to roll a refused AUTOMATIC save back out of the database.

    Private to ``save_from_tool``: it is never allowed to escape that function,
    and it carries no message because nothing reads one — it exists only so the
    ``transaction.atomic()`` block it is raised inside unwinds. See the comment
    at its raise site for why an automatic save is verified by its outcome
    rather than by predicting one.
    """


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

    # Permission: the user must currently be allowed to build this form. This is
    # the SAME call tool_for_case makes to decide whether to open the grid
    # read-only, so a viewer who forges this POST is refused by the rule that
    # made their page read-only in the first place.
    may_save, refusal = _may_build_form(request, case, form_kind, side)
    if not may_save:
        messages.error(request, refusal)
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
    # WHO ASKED FOR THIS SAVE.
    #
    # tool_save.js stamps every POST it makes: ``manual`` for a press of the
    # Save button, ``auto`` for the five-minute unattended timer. Anything that
    # sends no marker at all is treated as manual, i.e. exactly as this endpoint
    # has always behaved — and no client can reach here without the marker by
    # accident, because tool_case.html loads tool_save.js under a ``?v=`` cache
    # key that is bumped in the same change, so a browser holding the previous
    # script also holds the previous (marker-less, timer-less) file that never
    # posts by itself.
    automatic = (request.POST.get("intent", "") == "auto")

    # QTY AND UNIT ARE AUTHORISED BY VALUE, NOT ONLY BY SEAT.
    #
    # ``_may_build_form`` above answers "may this seat write this form at all".
    # It has never answered "may this seat write THIS COLUMN", and it cannot: a
    # Supply expert legitimately saves the whole Proforma grid, quantities
    # included, on every pricing round. So a POST that rewrote every row's Qty
    # and Unit was accepted from any seat that could save the form — the browser
    # simply never offered a way to type one.
    #
    # Now that Technical is given that way on the Technical Offer, the rule has
    # to exist somewhere that a hand-made POST cannot go around, which is here,
    # before anything is written. This also stamps the per-row override marks
    # that let a Technical value survive the inquiry copy-down on reopen.
    qu_reason = _apply_qty_unit_rules(request, case, form_kind, side, table, mode)
    if qu_reason:
        if automatic:
            return JsonResponse({"ok": False, "saved": False, "reason": qu_reason},
                                status=409)
        messages.error(request, qu_reason)
        return redirect("cases:case_detail", pk=case.pk)

    current = case.current_form(form_kind, side)
    inq = case.current_form(FormKind.INQUIRY, side)
    # A form left behind by the inquiry must become a new version; a current
    # form may be edited in place even after it was sent and returned. "Behind"
    # covers a higher inquiry NUMBER and a two-stage generation change at the
    # same number — the latter is a new version in every sense but its number,
    # so saving over it would silently rewrite the offer already sent. Without
    # this the save was logged as an EDIT of "Version 01" even though it wrote
    # the separate "Version 01 · Two Stage" record.
    behind = services.form_behind_inquiry(current, inq)

    # A TIMER MAY NEVER PUBLISH A VERSION.
    #
    # Versions are a deliberate act. The arithmetic below is right and is not
    # touched: a form built against an older inquiry genuinely needs a new
    # version, and a person pressing Save gets exactly that, today and after
    # this change. What must not happen is the unattended timer doing it.
    #
    # An automatic save is therefore only ever allowed to be an in-place
    # overwrite of the current version, and there are three ways it could stop
    # being one:
    #
    #  * ``mode`` is not "edit". tool_for_case renders the mode, so "build"
    #    (no current form, or a fresh build over one that exists) and
    #    "newversion" both mean this POST would mint a version. tool_save.js
    #    already refuses to arm its timer outside edit mode; this is the same
    #    rule on the side of the wire that cannot be lied to.
    #  * the form has fallen BEHIND its inquiry SINCE the page was rendered.
    #    This is the live hazard: the page was rendered mode=edit because the
    #    form was up to date at the time, and then a new inquiry version landed
    #    from another session while the tool tab sat open. Both the upgrade to
    #    "newversion" below and, independently, save_form's own
    #    ``version = inq_version`` bump would then write a NEW CaseForm row —
    #    unattended. So the automatic save is refused BEFORE save_form is
    #    called at all, and nothing is written.
    #  * there is NO current version to overwrite. ``form_behind_inquiry`` is
    #    False when ``current`` is None — nothing can be behind an inquiry it
    #    does not exist against — so the two tests above sail straight past this
    #    case, and ``save_form`` then resolves ``version = inq_version`` and
    #    CREATES the first snapshot. Measured on the scratch database: a
    #    mode=edit / intent=auto POST for a side with no Proforma took
    #    CaseForm.objects.count() from 21 to 22 and left a brand-new "v00"
    #    current, with nobody having pressed anything. A freshly rendered page
    #    cannot say mode=edit here (tool_for_case rewrites it to "build" when
    #    ``current`` is None), but a tab that has been open for hours can: the
    #    form it was opened on may since have been re-versioned onto another
    #    side, or dropped with a side the case no longer has
    #    (``_apply_sides`` deletes the forms of a side that was switched off).
    #    That is precisely the shape of hazard this whole guard exists for.
    #
    # The refusal is a 409 carrying its own reason, not a redirect with a queued
    # Django message: the automatic client reads the reason directly, tells the
    # user what happened and why, and stops its timer — and no message is left
    # in the queue to ambush the user on their next click. Nothing is saved and
    # nothing is lost: the grid is still in the page, Save is still enabled, and
    # pressing it does today's thing, branching the new version deliberately.
    if automatic and (mode != "edit" or behind or current is None):
        if behind:
            reason = (f"Auto-save skipped: a newer Inquiry version has arrived "
                      f"while this tab was open, so saving now would create a "
                      f"new {kind} version. Nothing has been saved. Press "
                      f"Save {kind} when you want to create that version.")
        else:
            reason = ("Auto-save skipped: this grid has not been saved as a "
                      "version yet, so only a deliberate Save may create one. "
                      "Nothing has been saved.")
        return JsonResponse({"ok": False, "saved": False, "reason": reason},
                            status=409)

    if mode == "edit" and behind:
        mode = "newversion"
    is_edit = (mode == "edit")
    if mode == "newversion":
        new_version = True
    elif mode == "edit":
        new_version = False
    else:  # build: start a new version only if one already exists
        new_version = current is not None

    # AND THEN CHECK THAT IT REALLY DID NOT.
    #
    # Everything above is an ARGUMENT that this automatic save can only
    # overwrite: it reads the same ``form_behind_inquiry`` the page's mode was
    # rendered from and reasons that save_form's own version arithmetic will
    # therefore land on the row that is already current. The argument is only as
    # good as the claim that those two agree, and they are not the same code —
    # save_form resolves ``current`` differently (it steps back past Commercial
    # currency-only clones) and has branches for cases this view does not model
    # (no inquiry at all, a two-stage flag with nothing to compare it to). Every
    # one of those is a way for the reasoning to be right today and wrong after
    # the next change to save_form.
    #
    # So for an automatic save the conclusion is ENFORCED rather than trusted.
    # The save runs inside a savepoint and must come back having written the
    # very row that was current before it — same primary key, therefore same
    # version, same generation, same side. If it wrote anything else, whether a
    # newly minted row or a different existing one it would then have made
    # current, the savepoint is rolled back and the user gets the same honest
    # 409 they would have got had the check above caught it. Nothing reaches the
    # database, and no new definition of "would this create a version" is
    # introduced that could itself drift: the invariant is stated in terms of
    # the outcome, which is the thing the owner actually cares about.
    #
    # A MANUAL save does not go near this. It takes the identical call it has
    # always taken, and it may still create the version — a person asked for it.
    #
    # Cost on the ordinary path: no extra queries (``current`` is already in
    # hand and ``save_form`` already returns the form it wrote), and one
    # SAVEPOINT/RELEASE pair around a block that was already opening a
    # transaction of its own, since ``save_form`` is ``@transaction.atomic``.
    if automatic:
        try:
            with transaction.atomic():
                written = services.save_form(
                    case, kind=form_kind, columns=columns, table=table,
                    meta=meta, actor=request.user, side=side,
                    new_version=new_version, is_edit=is_edit)
                if written.pk != current.pk:
                    raise _AutosaveWouldVersion()
        except _AutosaveWouldVersion:
            logger.warning(
                "Auto-save for case #%s %s (side=%r) would have written a new "
                "%s version; rolled back and refused.", case.pk, kind, side, kind)
            return JsonResponse({"ok": False, "saved": False, "reason": (
                f"Auto-save skipped: saving now would create a new {kind} "
                f"version, and only a deliberate Save may do that. Nothing has "
                f"been saved. Press Save {kind} when you want to create it."
            )}, status=409)
    else:
        services.save_form(case, kind=form_kind, columns=columns, table=table,
                           meta=meta, actor=request.user, side=side,
                           new_version=new_version, is_edit=is_edit)
    label = (" (" + Side.LABELS.get(side, "") + ")") if side else ""
    messages.success(request, f"{kind}{label} saved.")
    return redirect("cases:case_detail", pk=case.pk)
