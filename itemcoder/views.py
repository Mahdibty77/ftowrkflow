import json
import logging
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.html import escape

from .forms import UploadFileForm
from .processor import load_json_file, process_excel_with_json, process_text_record_live
from .Initial_changes import prepare_table_cell
from .table_layout_manager import build_extra_values, is_writable_extra_column, load_table_layout_config, get_extra_column_names, COLUMN_TITLES as _COLUMN_TITLES
from .calculation_engine import calculate_row_values, is_writable_calculation_column, get_calculation_variable_map
from .calculation_customizer import get_calculation_ui_config
from .resource_paths import json_path

logger = logging.getLogger(__name__)


def _refresh_reference_caches():
    """Sync this worker's reference-data caches with any admin edit made in
    another gunicorn worker (cheap, throttled). No-op on failure."""
    try:
        from . import cache_sync
        cache_sync.maybe_refresh()
    except Exception:
        pass


def _display_cell_value(val):
    """No NaN / spurious float decimals in grid cells."""
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
    if s.lower() in ("nan", "none", "<na>"):
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def _hash_cell_html(val, deleted=False, added=False):
    import re
    raw = _display_cell_value(val)
    raw = re.sub(r"[\s−+\-]+$", "", raw).strip()
    txt = escape(raw)
    marks = ""
    if deleted:
        marks += '<span class="row-mark row-mark-del" title="Deleted">−</span>'
    if added:
        marks += '<span class="row-mark row-mark-add" title="Added">+</span>'
    return f'<span class="client-no-text">{txt}</span>{marks}'


def dataframe_to_html_with_ids(df, data_json=None):
    """
    مبدل دیتافریم به ردیف‌های خالص HTML برای بدنه جدول جهت جلوگیری از تداخل با هدر فریز شده
    """
    display_to_canonical = df.attrs.get("display_to_canonical", {}) if hasattr(df, "attrs") else {}
    parts = [] # تگ‌های table و thead کاملاً از اینجا حذف شدند

    calculation_variable_map = get_calculation_variable_map()
    columns = [c for c in df.columns if c not in (
        "Feature_Variables", "_unsuppliable", "_issue", "_issue_reason",
        "_price_source", "_unit_price_raw", "_deleted", "_added",
        "_service_comment", "_service_price_raw",
        "_remark_split", "_prev_remark", "_pf_ack", "_pf_pending", "_pf_text",
        "_remark_ack",
        "_brand_split", "_prev_brand", "_brand_ack", "_brand_pending", "_brand_pf_text",
        "_brand_baseline", "_ftco_user_edited",
    )]
    records = df.to_dict("records")

    for row_idx, row in enumerate(records):
        feature_vars = row.get('Feature_Variables', {})
        # Pandas / JSON storage may leave this as NaN, a JSON string, or None.
        try:
            import math
            if feature_vars is None:
                feature_vars = {}
            elif isinstance(feature_vars, float) and (math.isnan(feature_vars) or math.isinf(feature_vars)):
                feature_vars = {}
        except Exception:
            if feature_vars is None:
                feature_vars = {}
        if isinstance(feature_vars, str):
            try:
                feature_vars = json.loads(feature_vars) if feature_vars.strip() else {}
            except Exception:
                feature_vars = {}
        if not isinstance(feature_vars, dict):
            feature_vars = {}
        feature_vars_json = escape(json.dumps(feature_vars, ensure_ascii=False))
        unsup = str(row.get('_unsuppliable', '') or '') == '1'
        issue = str(row.get('_issue', '') or '') == '1'
        deleted = str(row.get('_deleted', '') or '') == '1'
        added = str(row.get('_added', '') or '') == '1'
        remark_split = str(row.get('_remark_split', '') or '') == '1'
        prev_remark = str(row.get('_prev_remark', '') or '')
        pf_ack = str(row.get('_pf_ack', '') or '')
        pf_pending = str(row.get('_pf_pending', '') or '') == '1'
        pf_text = str(row.get('_pf_text', '') or '')
        brand_split = str(row.get('_brand_split', '') or '') == '1'
        prev_brand = str(row.get('_prev_brand', '') or '')
        # Pandas densifies sparse row dicts (NaN for missing keys). Never treat
        # densified NaN / empty as a real brand ack — that made every other row
        # look "acknowledged" and re-open as Prev/New after the next Save.
        _ack_raw = row.get('_brand_ack', None) if '_brand_ack' in row else None
        try:
            import math
            if _ack_raw is not None and isinstance(_ack_raw, float) and math.isnan(_ack_raw):
                _ack_raw = None
        except Exception:
            pass
        try:
            if _ack_raw is not None and pd.isna(_ack_raw):
                _ack_raw = None
        except Exception:
            pass
        if _ack_raw is not None and str(_ack_raw).strip().lower() in ('nan', 'none', '<na>', 'null'):
            _ack_raw = None
        # Only emit ack together with an active brand split (Confirm/Reject round),
        # OR a non-empty PI absorb marker (so later handoffs do not re-split).
        brand_ack_set = _ack_raw is not None and (
            brand_split or bool(str(_ack_raw).strip())
        )
        brand_ack = str(_ack_raw or '') if brand_ack_set else ''
        brand_pending = str(row.get('_brand_pending', '') or '') == '1'
        brand_pf_text = str(row.get('_brand_pf_text', '') or '')
        if brand_pf_text.strip().lower() in ('nan', 'none', '<na>', 'null'):
            brand_pf_text = ''
        remark_ack = str(row.get('_remark_ack', '') or '')
        if remark_ack.strip().lower() in ('nan', 'none', '<na>', 'null'):
            remark_ack = ''
        issue_reason = str(row.get('_issue_reason', '') or '')
        price_src = str(row.get('_price_source', '') or '')
        unit_raw = str(row.get('_unit_price_raw', '') or '')
        # NaN from pandas is truthy — never emit "nan" as a service comment.
        service_comment = _display_cell_value(row.get('_service_comment', ''))
        service_raw = _display_cell_value(row.get('_service_price_raw', ''))
        if not service_raw:
            service_raw = _display_cell_value(row.get('SERVICE PRICE', ''))
            # Strip currency labels that may have been saved from older builds.
            import re as _re
            m = _re.search(r'[-+]?\d[\d,]*\.?\d*', service_raw.replace(',', ''))
            service_raw = m.group(0) if m else ''
        # Only keep a service price when there is a real comment (attached row).
        if not service_comment:
            service_raw = ''
        ftco_user_edited = str(row.get('_ftco_user_edited', '') or '') == '1'
        flag_attrs = (' data-unsuppliable="1"' if unsup else '') + (' data-issue="1"' if issue else '')
        flag_attrs += (' data-deleted="1"' if deleted else '') + (' data-added="1"' if added else '')
        if ftco_user_edited:
            flag_attrs += ' data-ftco-user-edited="1"'
        if remark_split:
            flag_attrs += ' data-remark-split="1"'
            flag_attrs += f' data-prev-remark="{escape(prev_remark)}"'
        if pf_ack:
            flag_attrs += f' data-pf-ack="{escape(pf_ack)}"'
        if remark_ack:
            flag_attrs += f' data-remark-ack="{escape(remark_ack)}"'
        if pf_pending:
            flag_attrs += ' data-pf-pending="1"'
            if pf_text:
                flag_attrs += f' data-pf-text="{escape(pf_text)}"'
        if brand_split:
            flag_attrs += ' data-brand-split="1"'
            flag_attrs += f' data-prev-brand="{escape(prev_brand)}"'
        if brand_ack_set:
            flag_attrs += f' data-brand-ack="{escape(brand_ack)}"'
        if brand_pending:
            flag_attrs += ' data-brand-pending="1"'
            if brand_pf_text:
                flag_attrs += f' data-brand-pf-text="{escape(brand_pf_text)}"'
        # PI change-detection baseline = Technical TO brand (for TIME/price lock).
        _bl_raw = row.get('_brand_baseline', None) if '_brand_baseline' in row else None
        try:
            import math
            if _bl_raw is not None and isinstance(_bl_raw, float) and math.isnan(_bl_raw):
                _bl_raw = None
        except Exception:
            pass
        try:
            if _bl_raw is not None and pd.isna(_bl_raw):
                _bl_raw = None
        except Exception:
            pass
        brand_baseline = str(_bl_raw or '').strip()
        if brand_baseline.lower() in ('nan', 'none', '<na>', 'null'):
            brand_baseline = ''
        if not brand_baseline:
            brand_baseline = str(row.get('BRAND', '') or '')
            if brand_baseline.strip().lower() in ('nan', 'none', '<na>', 'null'):
                brand_baseline = ''
        if brand_baseline or brand_split or brand_ack_set:
            flag_attrs += f' data-brand-baseline="{escape(brand_baseline)}"'
        if issue_reason:
            flag_attrs += f' data-issue-reason="{escape(issue_reason)}"'
        if price_src:
            flag_attrs += f' data-row-price-source="{escape(price_src)}"'
        if unit_raw:
            flag_attrs += f' data-row-unit-raw="{escape(unit_raw)}"'
        if service_comment:
            flag_attrs += f' data-service-comment="{escape(service_comment)}"'
        if service_raw:
            flag_attrs += f' data-service-price-raw="{escape(service_raw)}"'
            flag_attrs += f' data-row-service-raw="{escape(service_raw)}"'
        row_classes = 'row' + (' row-unsuppliable' if unsup else '') + (' row-issue' if issue else '')
        row_classes += (' row-soft-deleted' if deleted else '') + (' row-soft-added' if added else '')
        canonical_row = {display_to_canonical.get(display_col, display_col): row.get(display_col, "") for display_col in columns}
        row_group = str(canonical_row.get("Group", "")).strip()
        row_type = str(canonical_row.get("Type", "")).strip()
        parts.append(
            f'<tr id="row-{row_idx}" class="{row_classes}" data-vars="{feature_vars_json}" '
            f'data-group="{escape(row_group)}" data-type="{escape(row_type)}"{flag_attrs}>'
        )

        for col in columns:
            canonical_col = display_to_canonical.get(col, col)
            # Header label: prefer the fixed COLUMN_TITLES title (so the live tool
            # shows FTCO DISCRIPTION / CLIENT DISCRIPTION, never the canonical
            # "Final Arranged Text" / "description"), then any layout display name.
            display_name = _COLUMN_TITLES.get(str(canonical_col)) or col
            val = row.get(col, "")
            clean_col = str(canonical_col).replace(" ", "_")
            if str(canonical_col) == "#":
                prepared_val = _hash_cell_html(val, deleted=deleted, added=added)
            elif str(canonical_col) == "Item Code":
                prepared_val = escape(_display_cell_value(val))
            else:
                prepared_val = prepare_table_cell(canonical_col, val, row=canonical_row, data_json=data_json)

            writable_cell = False
            if canonical_col not in {"اصلاحیه", "ریمارک"}:
                writable_cell = (
                    is_writable_extra_column(str(canonical_col), row_group)
                    or is_writable_calculation_column(str(canonical_col))
                )

            td_extra_attrs = ' data-editable="1"' if writable_cell else ''
            calc_var = calculation_variable_map.get(str(canonical_col), '')
            if calc_var:
                raw_text_value = escape(str(val if val is not None else ""))
                td_extra_attrs += (
                    f' data-variable-name="{escape(calc_var)}"'
                    f' data-calc-variable="{escape(calc_var)}"'
                    f' data-calc-raw="{raw_text_value}"'
                )

            parts.append(
                f'<td id="cell-{row_idx}-{escape(clean_col)}" '
                f'class="col-{escape(clean_col)}" '
                f'data-col-name="{escape(str(canonical_col))}" '
                f'data-display-name="{escape(str(display_name))}"{td_extra_attrs}>{prepared_val}</td>'
            )

        parts.append('</tr>\n')

    return ''.join(parts) # خروجی فقط شامل ردیف‌های خالص tr است


@login_required
def upload_excel(request):
    _refresh_reference_caches()
    table_html = None
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['file']
            data_path = json_path("data.json")
            json_dict = load_json_file(data_path)
            df_result = process_excel_with_json(excel_file, json_dict)
            table_html = dataframe_to_html_with_ids(df_result, data_json=json_dict)
    else:
        form = UploadFileForm()

    group_options = sorted((load_json_file(json_path("data.json")).get("group", {}) or {}).keys())
    return render(request, 'itemcoder/table.html', {
        'form': form,
        'table_html': table_html,
        'calculation_ui_config': json.dumps(get_calculation_ui_config(), ensure_ascii=False),
        'group_options': group_options,
    })


@login_required
def process_row_ajax(request):
    """
    پردازش AJAX هر ردیف؛ منطق خروجی تغییر نکرده است.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=400)
    try:
        return _process_row_ajax_impl(request)
    except Exception as exc:
        logger.exception("process_row_ajax failed")
        return JsonResponse(
            {"error": str(exc) or "row processing failed", "Final_Text": "", "Code": "", "Alarm": []},
            status=500,
        )


def _process_row_ajax_impl(request):
    _refresh_reference_caches()

    original_text = request.POST.get("text", "").strip()
    group = request.POST.get("group", "").strip()
    type_ = request.POST.get("type", "").strip()
    remark = request.POST.get("remark", "").strip()
    revision = request.POST.get("revision", "").strip()
    clean_size = request.POST.get("clean_size", "").strip()
    qty_value = request.POST.get("qty", "").strip()
    unit_value = request.POST.get("unit", "").strip()
    try:
        calculation_overrides = json.loads(request.POST.get("calculation_overrides", "{}"))
    except Exception:
        calculation_overrides = {}
    try:
        row_index = int(request.POST.get("row_index", "0"))
    except Exception:
        row_index = 0
    assign_mode = request.POST.get("assign_mode", "full").strip().lower()
    allow_code_lookup = assign_mode == "full"
    confirm_raw = str(request.POST.get("confirm_group_change", "")).strip().lower()
    if confirm_raw in ("1", "true", "yes", "confirm"):
        confirm_group_change = True
    elif confirm_raw in ("0", "false", "no", "reject"):
        confirm_group_change = False
    else:
        confirm_group_change = None
    locked_group = str(request.POST.get("locked_group", "") or "").strip()
    locked_type = str(request.POST.get("locked_type", "") or "").strip()

    data_path = json_path("data.json")
    json_dict = load_json_file(data_path)

    # Reject poisoned group/type from a <select>'s concatenated textContent
    # (e.g. "-- Select group --pipefittingflange…") so find_group re-runs.
    known_groups = {
        str(k)[2:].strip().lower()
        for k in (json_dict.get("group") or {})
        if str(k).startswith("G_")
    }
    if group and group.strip().lower() not in known_groups:
        group = ""
        type_ = ""
    if "انتخاب" in group or "Select group" in group or "Select type" in group:
        group = ""
        type_ = ""
    if locked_group and locked_group.strip().lower() not in known_groups:
        locked_group = ""
        locked_type = ""

    result = process_text_record_live(
        original_text,
        json_dict,
        group_key_input=group,
        type_key_input=type_,
        remark=remark,
        revision=revision,
        clean_size=clean_size,
        row_index=row_index,
        allow_code_lookup=allow_code_lookup,
        confirm_group_change=confirm_group_change,
        locked_group=locked_group or None,
        locked_type=locked_type or None,
    )

    can_assign_code = bool(result.get("Can_Assign_Code")) and allow_code_lookup
    layout_config = load_table_layout_config()
    extra_values = {}
    if can_assign_code:
        extra_values = build_extra_values(
        row_index=row_index,
        group=result.get("Group", group),
        type_=result.get("Type", type_),
        feature_vars=result.get("Feature_Variables", {}),
        code_value=result.get("Code", ""),
        config=layout_config,
        )
    calculation_values = {}
    if can_assign_code:
        calculation_values = calculate_row_values(
        group=result.get("Group", group),
        type_=result.get("Type", type_),
        code_value=result.get("Code", ""),
        qty=qty_value,
        size=result.get("Size_Override") or clean_size,
        unit=unit_value,
        feature_vars=result.get("Feature_Variables", {}),
        overrides=calculation_overrides,
        )
    else:
        # Keep output structure stable: return all configured extra/calculation
        # columns, but blank them when the row is incomplete or orange-alerted.
        extra_values.update({name: "" for name in get_extra_column_names(layout_config)})
        calculation_values = calculate_row_values(
            group=result.get("Group", group),
            type_=result.get("Type", type_),
            code_value="",
            qty=qty_value,
            size=result.get("Size_Override") or clean_size,
            unit=unit_value,
            feature_vars={},
            overrides=calculation_overrides,
        )
    extra_values.update(calculation_values)

    return JsonResponse({
        "Final_Text": result.get("Final_Text", ""),
        "Filled_Features": result.get("Filled_Features", ""),
        "Feature_Variables": result.get("Feature_Variables", {}) or {},
        "Alarm": result.get("Alarm", []),
        "Code": result.get("Code", ""),
        "Group": result.get("Group", ""),
        "Type": result.get("Type", ""),
        "Rule_Targets": result.get("Target_Values_Map", {}),
        "Extra_Columns": extra_values,
        "Size_Override": result.get("Size_Override", ""),
        "Can_Assign_Code": can_assign_code,
        "Has_Orange_Alert": bool(result.get("Has_Orange_Alert")),
        "Assign_Mode": assign_mode,
        "Assign_Pending": bool(result.get("Can_Assign_Code")) and not allow_code_lookup,
        "Pending_Group_Change": result.get("Pending_Group_Change"),
    })


@login_required
def app_json_resource(request, filename):
    """Serve backend-managed JSON resources to frontend JS.

    JSON files are no longer stored under static/. This small endpoint is used
    only for read-only frontend resources such as data_translation.json.
    """
    _refresh_reference_caches()
    allowed = {"data_translation.json"}
    if filename not in allowed:
        return JsonResponse({"error": "resource not allowed"}, status=404)

    path = json_path(filename)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return JsonResponse(json.load(fp), safe=False)
    except FileNotFoundError:
        return JsonResponse({"error": "resource not found"}, status=404)
