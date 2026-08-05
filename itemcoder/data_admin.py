"""Admin-only data management for the coding tool.

Lets an administrator manage the tool's reference data while the project runs:
import a group's coding-data table (with preview + confirm), browse/filter the
rows, manage many price lists per code, and publish/rollback versioned JSON
configuration. None of this changes the coding/pricing logic; it only feeds the
existing loaders (which fall back to the original files when the DB is empty).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse

from .models import (CodeTable, CodeTableRow, PriceList, CodePrice, ConfigDocument,
                     GroupFeature, FeatureValue, GroupCodeConfig)
from .tool_access import (
    admin_required,
    price_access_required,
    group_data_access_required,
    can_delete_tool_data,
)

TEMP_DIR = os.path.join(tempfile.gettempdir(), "ft_codify_uploads")

# Soft ceiling for Tool Data uploads (streamed to disk). Large code tables are
# OK; this only rejects pathological multi-tens-of-GB posts that would fill disk.
CODE_UPLOAD_MAX_BYTES = int(os.environ.get("CODE_UPLOAD_MAX_BYTES", str(8 * 1024 * 1024 * 1024)))
_GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _read_table(file_path):
    """Read a CSV/XLSX file into a list-of-columns + list-of-row-lists (strings)."""
    import pandas as pd
    lower = file_path.lower()
    if lower.endswith((".xlsx", ".xls")):
        df = pd.read_excel(file_path, dtype=str)
    else:
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    df = df.fillna("")
    columns = [str(c) for c in df.columns.tolist()]
    rows = [[("" if v is None else str(v)) for v in row] for row in df.values.tolist()]
    return columns, rows


def _safe_upload_suffix(name: str) -> str:
    base = os.path.basename(str(name or "upload")).replace(" ", "_")
    base = re.sub(r"[^A-Za-z0-9._\-]", "", base)[:80] or "upload"
    return "_" + base


def _save_temp(uploaded) -> str:
    os.makedirs(TEMP_DIR, exist_ok=True)
    size = getattr(uploaded, "size", None)
    if size is not None and int(size) > CODE_UPLOAD_MAX_BYTES:
        raise ValueError(
            "File is too large (max %s GB)." % (CODE_UPLOAD_MAX_BYTES // (1024 ** 3))
        )
    fd, path = tempfile.mkstemp(dir=TEMP_DIR, suffix=_safe_upload_suffix(uploaded.name))
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            fd = -1  # ownership transferred to `out`
            for chunk in uploaded.chunks():
                written += len(chunk)
                if written > CODE_UPLOAD_MAX_BYTES:
                    raise ValueError(
                        "File is too large (max %s GB)."
                        % (CODE_UPLOAD_MAX_BYTES // (1024 ** 3))
                    )
                out.write(chunk)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _sync_code_tables():
    """Ensure a CodeTable metadata row exists for every group that has a
    SQLite code database on disk (e.g. pipe, shipped pre-built). This lets
    converted groups appear in the admin without re-importing."""
    try:
        from . import code_db
        import glob
        for path in glob.glob(os.path.join(code_db.DB_DIR, "*.sqlite3")):
            group = os.path.splitext(os.path.basename(path))[0].lower()
            cols = code_db.column_names(group)
            if not cols:
                continue
            rc = code_db.row_count(group)
            obj = CodeTable.objects.filter(group=group).first()
            if obj is None:
                CodeTable.objects.create(group=group, columns=cols, row_count=rc)
            elif obj.row_count != rc or list(obj.columns or []) != list(cols):
                obj.columns = cols
                obj.row_count = rc
                obj.save(update_fields=["columns", "row_count", "updated_at"])
    except Exception:
        pass


def _ensure_features(group):
    """Seed GroupFeature rows from the imported table when none exist yet.

    Features always follow the uploaded CSV headers. A feature_schema JSON is
    never forced over an existing seeding (that used to remap/truncate mains).
    """
    from . import item_builder
    group = group.lower()
    if GroupFeature.objects.filter(group=group).exists():
        return
    try:
        item_builder.seed_group_from_table(group)
    except Exception:
        pass


def _json_response(payload, status=200):
    from django.http import JsonResponse
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def _clear_caches():
    try:
        from . import constants
        constants.clear_data_caches()
    except Exception:
        pass
    try:
        from . import code_db
        for con in list(code_db._CONN_CACHE.values()):
            try:
                con.close()
            except Exception:
                pass
        code_db._CONN_CACHE.clear()
        code_db._CONN_META.clear()
        code_db._COLUMNS_CACHE.clear()
    except Exception:
        pass
    # Tell the OTHER gunicorn workers to drop their caches too, so no worker
    # keeps serving stale reference data until the next restart.
    try:
        from . import cache_sync
        cache_sync.bump_epoch()
    except Exception:
        pass


def _refresh_code_db_view():
    """Browse/API entry: pick up imports done by another gunicorn worker."""
    try:
        from . import cache_sync
        cache_sync.maybe_refresh()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Home
# --------------------------------------------------------------------------- #
@admin_required
def dm_home(request):
    _sync_code_tables()
    ctx = {
        "code_tables": CodeTable.objects.all().order_by("group"),
        "price_lists": PriceList.objects.all(),
    }
    return render(request, "itemcoder/data_admin/home.html", ctx)


@group_data_access_required
def dm_technical_home(request):
    """Landing page for the technical manager: one card per imported group."""
    _sync_code_tables()
    ctx = {"code_tables": CodeTable.objects.all().order_by("group")}
    return render(request, "itemcoder/data_admin/technical_home.html", ctx)


@group_data_access_required
def dm_ea_item_log(request):
    """Read-only audit trail of every item the Technical Assistant (EA) has
    created — visible to Admin and Technical Manager (same access rule as
    the rest of Tool Data's group/coding screens: can_manage_group_data).
    Nothing here is editable; this is a record of what EA already did, not
    a management screen.
    """
    from .models import EaItemCreationLog
    group_filter = (request.GET.get("group") or "").strip().lower()
    entries = EaItemCreationLog.objects.all()
    if group_filter:
        entries = entries.filter(group=group_filter)
    entries = entries.select_related("user")[:500]
    groups = list(
        EaItemCreationLog.objects.order_by("group")
        .values_list("group", flat=True).distinct()
    )
    ctx = {"entries": entries, "groups": groups, "group_filter": group_filter}
    return render(request, "itemcoder/data_admin/ea_item_log.html", ctx)


# --------------------------------------------------------------------------- #
# Code tables: upload -> preview -> confirm import
# --------------------------------------------------------------------------- #
@admin_required
def dm_code_upload(request):
    if request.method == "POST" and request.FILES.get("file"):
        group = (request.POST.get("group") or "").strip().lower()
        if not group:
            messages.error(request, "Please enter the group name (e.g. pipe, fitting).")
            return redirect("dm_code_upload")
        if not _GROUP_NAME_RE.fullmatch(group):
            messages.error(request, "Group name must be letters, digits, underscore or hyphen only.")
            return redirect("dm_code_upload")
        try:
            temp_path = _save_temp(request.FILES["file"])
            # Read only the header + a small sample so even a 1.5M-row file
            # previews instantly (the full file is streamed at confirm time).
            import pandas as pd
            if temp_path.lower().endswith((".xlsx", ".xls")):
                head = pd.read_excel(temp_path, dtype=str, nrows=50).fillna("")
            else:
                head = pd.read_csv(temp_path, dtype=str, keep_default_na=False, nrows=50)
            columns = [str(c) for c in head.columns.tolist()]
            sample = [["" if v is None else str(v) for v in rec]
                      for rec in head.itertuples(index=False, name=None)]
        except Exception as exc:
            messages.error(request, f"Could not read the file: {exc}")
            return redirect("dm_code_upload")
        from . import item_builder
        classed = []
        for i, col in enumerate(columns):
            if i == 0:
                classed.append((col, "big code"))
            elif i == 1:
                classed.append((col, "small code"))
            else:
                _name, kind = item_builder.classify_header(col)
                classed.append((col, kind))
        ctx = {
            "group": group, "columns": columns, "classed": classed,
            "preview_rows": sample[:20], "temp_name": os.path.basename(temp_path),
        }
        return render(request, "itemcoder/data_admin/code_preview.html", ctx)
    return render(request, "itemcoder/data_admin/code_upload.html", {})


# --------------------------------------------------------------------------- #
# Background import jobs (so multi-million-row files never hit the request
# timeout). The confirm view spawns a daemon thread and returns immediately; a
# small JSON status file (shared on disk, so any gunicorn worker can read it)
# drives a polling progress page.
# --------------------------------------------------------------------------- #
JOBS_DIR = os.path.join(TEMP_DIR, "jobs")


def _job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, "job_%s.json" % re.sub(r"[^0-9a-f]", "", str(job_id).lower())[:40])


def _job_write(job_id: str, **fields) -> None:
    """Merge ``fields`` into the job's status file (atomic replace)."""
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        path = _job_path(job_id)
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh) or {}
            except Exception:
                data = {}
        data.update(fields)
        data["updated_at"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _job_read(job_id: str) -> dict:
    try:
        with open(_job_path(job_id), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _estimate_rows(temp_path: str) -> int:
    """Cheap row-count estimate from file size / average line length (CSV only),
    used only to render a progress percentage. Returns 0 when unknown."""
    try:
        size = os.path.getsize(temp_path)
        if temp_path.lower().endswith((".xlsx", ".xls")) or size <= 0:
            return 0
        with open(temp_path, "rb") as fh:
            sample = fh.read(1_000_000)
        lines = sample.count(b"\n")
        if lines <= 1:
            return 0
        avg = len(sample) / lines
        if avg <= 0:
            return 0
        return max(0, int(size / avg) - 1)  # minus header
    except Exception:
        return 0


def _run_import_job(job_id: str, group: str, temp_path: str, params: dict, user_id):
    """Do the whole import off the request thread, reporting progress to the
    job status file. Any error is captured here (never a bare 500)."""
    from django.db import connection as _dj_conn
    try:
        from . import code_db
        import pandas as pd
        total_est = _estimate_rows(temp_path)
        _job_write(job_id, phase="importing", rows=0, total=total_est,
                   label="Reading and importing rows…")

        def _progress(n):
            _job_write(job_id, phase="importing", rows=int(n), total=total_est,
                       label="Importing rows… %s" % ("{:,}".format(int(n))))

        def _status(msg):
            _job_write(job_id, phase="finalizing", rows=total_est or 0, total=total_est,
                       label=str(msg))

        lower = temp_path.lower()
        if lower.endswith((".xlsx", ".xls")):
            df = pd.read_excel(temp_path, dtype=str).fillna("")
            columns = [str(c) for c in df.columns.tolist()]

            def _rows():
                for rec in df.itertuples(index=False, name=None):
                    yield ["" if v is None else str(v) for v in rec]
            total = code_db.build_db_from_rows(
                group, columns, _rows(), on_progress=_progress, on_status=_status)
        else:
            first = pd.read_csv(temp_path, dtype=str, keep_default_na=False, nrows=0)
            columns = [str(c) for c in first.columns.tolist()]

            def _rows():
                for chunk in pd.read_csv(temp_path, dtype=str, keep_default_na=False,
                                         chunksize=50000):
                    chunk = chunk.fillna("")
                    for rec in chunk.itertuples(index=False, name=None):
                        yield ["" if v is None else str(v) for v in rec]
            total = code_db.build_db_from_rows(
                group, columns, _rows(), on_progress=_progress, on_status=_status)

        # Explicit post-replace check (also done inside build_db_from_rows).
        _status("Confirming SQLite file was replaced…")
        verified = code_db.verify_group_db(group, expected_rows=total)
        if not verified.get("replaced") or not os.path.exists(verified["path"]):
            raise RuntimeError(
                "CSV import finished writing but the SQLite file was not found "
                "after replace. Path expected: %s" % code_db.group_db_path(group)
            )

        _job_write(job_id, phase="finalizing", rows=total, total=total,
                   label="Building schema…")

        CodeTable.objects.update_or_create(
            group=group,
            defaults={"columns": columns, "row_count": total, "updated_by_id": user_id},
        )
        CodeTableRow.objects.filter(group=group).delete()

        from . import item_builder
        from .models import GroupCodeConfig
        try:
            item_builder.seed_group_from_table(group, force=True)
        except Exception:
            pass
        try:
            item_builder.ensure_rules_file(group)
            from . import offer_builder
            offer_builder.ensure_offer_file(group)
        except Exception:
            pass
        # Import keeps EVERY CSV row as-is. No automatic junk-row deletion —
        # empty optional cells (SDR/NACE/coating/…) are valid on many items.
        GroupCodeConfig.objects.update_or_create(
            group=group,
            defaults={
                "tech_start": (params.get("tech_start") or "").strip(),
                "tech_group": (params.get("tech_group") or "").strip(),
                "item_start": (params.get("item_start") or "").strip(),
                "item_group": (params.get("item_group") or "").strip(),
                "item_seq_digits": int(params.get("item_seq_digits") or 5),
            },
        )
        _clear_caches()

        # Re-verify after finalize so the success message is always truthful.
        verified = code_db.verify_group_db(group, expected_rows=total)
        ok_label = (
            "SQLite replaced successfully: {rows:,} rows verified · "
            "{size} MB · {path}"
        ).format(
            rows=int(verified["rows"]),
            size=verified["size_mb"],
            path=verified["path"],
        )
        _job_write(
            job_id,
            phase="done",
            rows=int(verified["rows"]),
            total=int(verified["rows"]),
            label=ok_label,
            message=ok_label,
            count=int(verified["rows"]),
            db_path=verified["path"],
            db_size_mb=verified["size_mb"],
            verified=True,
            redirect_url=reverse("dm_code_rows", kwargs={"group": group})
            + "?imported=1&rows=%s" % int(verified["rows"]),
        )
    except Exception as exc:
        _job_write(job_id, phase="error",
                   label="Import failed (previous SQLite kept if replace did not finish): %s" % exc,
                   message=str(exc),
                   verified=False)
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        try:
            _dj_conn.close()  # don't leak the thread's DB connection
        except Exception:
            pass


@admin_required
def dm_code_confirm(request):
    if request.method != "POST":
        return redirect("dm_code_upload")
    group = (request.POST.get("group") or "").strip().lower()
    temp_name = os.path.basename(request.POST.get("temp_name") or "")
    # Reject path tricks; only accept files we created under TEMP_DIR.
    if (
        not group
        or not _GROUP_NAME_RE.fullmatch(group)
        or not temp_name
        or ".." in temp_name
        or not os.path.exists(os.path.join(TEMP_DIR, temp_name))
    ):
        messages.error(request, "Upload session expired. Please upload the file again.")
        return redirect("dm_code_upload")
    temp_path = os.path.join(TEMP_DIR, temp_name)

    job_id = uuid.uuid4().hex
    params = {
        "tech_start": request.POST.get("tech_start") or "",
        "tech_group": request.POST.get("tech_group") or "",
        "item_start": request.POST.get("item_start") or "",
        "item_group": request.POST.get("item_group") or "",
        "item_seq_digits": request.POST.get("item_seq_digits") or "5",
    }
    _job_write(job_id, phase="starting", group=group, rows=0, total=_estimate_rows(temp_path),
               label="Starting import…", started_at=time.time())
    user_id = getattr(request.user, "id", None)
    t = threading.Thread(target=_run_import_job,
                         args=(job_id, group, temp_path, params, user_id),
                         name="code-import-%s" % group, daemon=True)
    t.start()
    return render(request, "itemcoder/data_admin/code_importing.html",
                  {"group": group, "job_id": job_id})


@admin_required
def dm_code_import_status(request, job_id):
    data = _job_read(job_id)
    if not data:
        return _json_response({"phase": "unknown", "label": "This import session is no longer available."},
                              status=404)
    return _json_response(data)


@group_data_access_required
def dm_code_rows_api(request, group):
    """Keyset window of rows for the browse virtual-scroll grid (exact filters
    keyed by column index: ?c3=C.S&c4=...)."""
    _refresh_code_db_view()
    group = group.lower()
    from . import code_db
    if not code_db.has_db(group):
        return _json_response({"rows": [], "next": None, "total": 0})
    filters = {}
    for key, val in request.GET.items():
        if key.startswith("c") and key[1:].isdigit() and str(val).strip():
            filters[int(key[1:])] = str(val).strip()
    try:
        after = int(request.GET.get("after", "-1"))
    except (TypeError, ValueError):
        after = -1
    limit = 100
    rows = code_db.fetch_window(group, after_row_no=after, limit=limit,
                                filters=filters, exact=True)
    nxt = rows[-1]["row_no"] if len(rows) == limit else None
    total = code_db.count_filtered(group, filters, exact=True)
    return _json_response({"rows": rows, "next": nxt, "total": total})


@login_required
@group_data_access_required
def dm_code_rows(request, group):
    group = group.lower()
    _refresh_code_db_view()
    _sync_code_tables()
    from . import code_db

    if request.GET.get("imported") == "1":
        try:
            imported_rows = int(request.GET.get("rows") or code_db.row_count(group) or 0)
        except (TypeError, ValueError):
            imported_rows = code_db.row_count(group)
        db_path = code_db.group_db_path(group)
        size_mb = 0.0
        try:
            if os.path.exists(db_path):
                size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
        except Exception:
            size_mb = 0.0
        messages.success(
            request,
            "Import verified: {rows:,} rows in SQLite ({size} MB) at {path}".format(
                rows=int(imported_rows or 0),
                size=size_mb,
                path=db_path,
            ),
        )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "edit_cell":
            try:
                ok = code_db.update_cell(group, int(request.POST["row_no"]),
                                         int(request.POST["col"]), request.POST.get("value", ""))
                _clear_caches()
                return _json_response({"ok": bool(ok)})
            except Exception as exc:
                return _json_response({"ok": False, "error": str(exc)}, status=400)
        if action == "delete_row":
            if not can_delete_tool_data(request.user):
                return _json_response({"ok": False, "error": "Not allowed"}, status=403)
            try:
                n = code_db.delete_rows(group, [int(request.POST["row_no"])])
                meta = CodeTable.objects.filter(group=group).first()
                if meta:
                    meta.row_count = code_db.row_count(group)
                    meta.save(update_fields=["row_count", "updated_at"])
                _clear_caches()
                return _json_response({"ok": True, "deleted": n})
            except Exception as exc:
                return _json_response({"ok": False, "error": str(exc)}, status=400)
        if action == "wipe":
            if not can_delete_tool_data(request.user):
                messages.error(request, "Only administrators can wipe an entire group.")
                return redirect("dm_code_rows", group=group)
            try:
                code_db.wipe_group(group)
                CodeTable.objects.filter(group=group).delete()
                GroupFeature.objects.filter(group=group).delete()
                FeatureValue.objects.filter(group=group).delete()
                GroupCodeConfig.objects.filter(group=group).delete()
                _clear_caches()
                messages.success(request, f"All data for '{group}' was deleted.")
            except Exception as exc:
                messages.error(request, f"Could not wipe group: {exc}")
            return redirect("dm_home")

    meta = get_object_or_404(CodeTable, group=group)
    columns = list(meta.columns)

    # Make sure the feature schema is known so we can tell editable (sub/info)
    # columns from read-only ones (codes + main features stay locked).
    _ensure_features(group)
    col_kind = {0: "code", 1: "code"}
    col_label = {}
    for gf in GroupFeature.objects.filter(group=group):
        if gf.column_index >= 0:
            col_kind[gf.column_index] = gf.kind
            col_label[gf.column_index] = gf.name   # unified feature name
    editable_flags = [1 if col_kind.get(i, "info") in ("sub", "info") else 0
                      for i in range(len(columns))]
    display_cols = [col_label.get(i, columns[i]) for i in range(len(columns))]
    from . import code_db

    # Price columns are managed in the price lists, so hide them from Browse.
    def _is_price_col(name):
        n = str(name).lower()
        return ("price" in n) or ("قیمت" in str(name)) or ("rial" in n) or ("ریال" in str(name))
    # Code columns (0,1) and every MAIN feature column are ALWAYS shown, no matter
    # what their header text looks like; only non-main, price-like columns hide.
    main_idx = {gf.column_index for gf in
                GroupFeature.objects.filter(group=group, kind="main")
                if gf.column_index is not None and gf.column_index >= 0}
    visible_idx = [i for i in range(len(columns))
                   if i <= 1 or i in main_idx or not _is_price_col(columns[i])]

    # Filter combos: MAIN features only, populated with their values (searchable).
    main_filters = []
    for gf in GroupFeature.objects.filter(group=group, kind="main").order_by("position"):
        vals = list(FeatureValue.objects.filter(group=group, feature=gf.name)
                    .order_by("value").values_list("value", flat=True))
        main_filters.append({"name": gf.name, "col": gf.column_index, "values": vals})

    cols_with_flags = [(i, display_cols[i], editable_flags[i]) for i in visible_idx]

    # Columns whose feature legitimately has an empty/"no" variant (e.g. coating)
    # should render empty cells as "NO COATING" in the grid.
    no_variant = {}
    for gf in GroupFeature.objects.filter(group=group):
        if gf.column_index < 0:
            continue
        has_empty = False
        for fv in FeatureValue.objects.filter(group=group, feature=gf.name):
            v = str(fv.value or "").strip()
            if (not v) or v.lower() == "null" or re.match(r"^\(no\)", v, re.I) or re.match(r"^\$.*\$$", v):
                has_empty = True
                break
        if has_empty:
            no_variant[gf.column_index] = "NO " + str(gf.name).upper()

    ctx = {
        "group": group, "meta": meta, "columns": columns,
        "cols_with_flags": cols_with_flags, "main_filters": main_filters,
        "no_variant": no_variant,
        "total": code_db.row_count(group),
        "can_delete": can_delete_tool_data(request.user),
    }
    return render(request, "itemcoder/data_admin/code_rows.html", ctx)


# --------------------------------------------------------------------------- #
# Price lists
# --------------------------------------------------------------------------- #
@price_access_required
def dm_price_lists(request):
    if request.method == "POST" and request.POST.get("action") == "create":
        name = (request.POST.get("name") or "").strip()
        if name:
            PriceList.objects.get_or_create(
                name=name,
                defaults={"currency": request.POST.get("currency") or "rial",
                          "note": request.POST.get("note") or "",
                          "created_by": request.user})
            messages.success(request, f"Price list '{name}' is ready.")
        return redirect("dm_price_lists")
    lists = PriceList.objects.all()
    for pl in lists:
        pl.count = pl.prices.count()
    return render(request, "itemcoder/data_admin/price_lists.html", {"price_lists": lists})


@price_access_required
def dm_price_upload(request, pk):
    price_list = get_object_or_404(PriceList, pk=pk)
    if request.method == "POST" and request.FILES.get("file"):
        try:
            temp_path = _save_temp(request.FILES["file"])
            columns, rows = _read_table(temp_path)
            os.remove(temp_path)
        except Exception as exc:
            messages.error(request, f"Could not read the file: {exc}")
            return redirect("dm_price_upload", pk=pk)
        # Expect two columns: code, price (header names are flexible).
        added = 0
        with transaction.atomic():
            for cells in rows:
                if len(cells) < 2:
                    continue
                code = str(cells[0]).strip()
                raw = str(cells[1]).strip().replace(",", "")
                if not code or not raw:
                    continue
                try:
                    price = float(raw)
                except ValueError:
                    continue
                CodePrice.objects.update_or_create(
                    price_list=price_list, code=code, defaults={"price": price})
                added += 1
        messages.success(request, f"Updated {added:,} prices in '{price_list.name}'.")
        return redirect("dm_price_upload", pk=pk)
    sample = price_list.prices.all()[:50]
    return render(request, "itemcoder/data_admin/price_upload.html",
                  {"price_list": price_list, "sample": sample, "count": price_list.prices.count()})




# --------------------------------------------------------------------------- #
# Feature schema: attributes + their value/codes (per group)
# --------------------------------------------------------------------------- #
@group_data_access_required
def dm_features(request, group):
    from .models import GroupFeature, GroupCodeConfig, FeatureValue
    from . import item_builder
    group = group.lower()

    if request.method == "POST" and request.POST.get("action") == "seed":
        try:
            n = item_builder.seed_group_from_file(group, force=bool(request.POST.get("force")))
            messages.success(request, f"Loaded {n} features for '{group}' from the schema file.")
        except Exception as exc:
            messages.error(request, f"Could not load schema: {exc}")
        return redirect("dm_features", group=group)

    if request.method == "POST" and request.POST.get("action") == "toggle_small":
        gf = GroupFeature.objects.filter(group=group, name=request.POST.get("feature")).first()
        if gf and gf.kind == GroupFeature.MAIN:
            # Cycle: none -> 1 -> 2 -> none. At most one feature per priority.
            nxt = {0: 1, 1: 2, 2: 0}[gf.small_order]
            if nxt:
                GroupFeature.objects.filter(group=group, small_order=nxt).exclude(id=gf.id)\
                    .update(small_order=0, in_small_code=False)
            gf.small_order = nxt
            gf.in_small_code = bool(nxt)
            gf.save(update_fields=["small_order", "in_small_code", "updated_at"])
        return redirect("dm_features", group=group)

    if request.method == "POST" and request.POST.get("action") == "add_sub":
        # Add a new SUB feature: appends a column to the group's table.
        title = (request.POST.get("title") or "").strip()
        if not title:
            messages.error(request, "Feature name is required.")
        else:
            try:
                from . import code_db
                name, _nm = item_builder.clean_header(title)
                if GroupFeature.objects.filter(group=group, name=name).exists():
                    messages.error(request, f"A feature named '{name}' already exists.")
                else:
                    col_idx = code_db.add_column(group, title) if code_db.has_db(group) else -1
                    pos = (GroupFeature.objects.filter(group=group)
                           .order_by("-position").values_list("position", flat=True).first() or 0) + 1
                    new_kind = request.POST.get("kind", "sub")
                    if new_kind not in (GroupFeature.SUB, GroupFeature.INFO):
                        new_kind = GroupFeature.SUB
                    GroupFeature.objects.create(group=group, name=name,
                                                position=col_idx if col_idx >= 0 else pos,
                                                kind=new_kind, column_index=col_idx)
                    meta = CodeTable.objects.filter(group=group).first()
                    if meta and col_idx >= 0:
                        meta.columns = code_db.column_names(group)
                        meta.save(update_fields=["columns", "updated_at"])
                    _clear_caches()
                    messages.success(request, f"Added sub feature '{name}'.")
            except Exception as exc:
                messages.error(request, f"Could not add feature: {exc}")
        return redirect("dm_features", group=group)

    _ensure_features(group)
    feats = list(GroupFeature.objects.filter(group=group).order_by("position", "name"))
    counts = {}
    for f in feats:
        counts[f.name] = FeatureValue.objects.filter(group=group, feature=f.name).count()
    ctx = {
        "group": group,
        "config": GroupCodeConfig.objects.filter(group=group).first(),
        "features": feats, "counts": counts,
        "has_schema_file": item_builder.has_schema_file(group),
    }
    return render(request, "itemcoder/data_admin/features.html", ctx)


@group_data_access_required
def dm_feature_values(request, group, feature):
    from . import item_builder
    group = group.lower()
    gf = get_object_or_404(GroupFeature, group=group, name=feature)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            value = (request.POST.get("value") or "").strip()
            order = item_builder.main_feature_order(group)
            others = [f for f in order if f != feature]
            relations = {o: request.POST.getlist("rel_" + o) for o in others}
            try:
                code = item_builder.add_value_with_relations(group, feature, value, relations)
                messages.success(request, f"Added '{value}' = {code} and linked it in rules.json.")
            except Exception as exc:
                messages.error(request, str(exc))
        elif action == "delete":
            if not can_delete_tool_data(request.user):
                messages.error(request, "Only administrators can remove feature values.")
                return redirect("dm_feature_values", group=group, feature=feature)
            FeatureValue.objects.filter(group=group, feature=feature,
                                        id=request.POST.get("id")).delete()
            messages.success(request, "Value removed.")
        return redirect("dm_feature_values", group=group, feature=feature)

    values = FeatureValue.objects.filter(group=group, feature=feature).order_by("code", "value")
    order = item_builder.main_feature_order(group)
    fpos = order.index(feature) if feature in order else -1
    others = []
    for o in order:
        if o == feature:
            continue
        ovals = list(FeatureValue.objects.filter(group=group, feature=o)
                     .order_by("code", "value").values_list("value", flat=True))
        others.append({"name": o, "values": ovals,
                       "rel": "upstream" if order.index(o) < fpos else "downstream"})
    ctx = {"group": group, "feature": feature, "gf": gf, "values": values,
           "suggested": item_builder.next_value_code(group, feature),
           "others": others}
    return render(request, "itemcoder/data_admin/feature_values.html", ctx)


# --------------------------------------------------------------------------- #
# Single-item builder
# --------------------------------------------------------------------------- #
@group_data_access_required
def dm_item_new(request, group):
    from .models import GroupFeature, CodeTable
    from . import item_builder, code_db
    group = group.lower()
    if code_db.has_db(group):
        _ensure_features(group)
    mains = item_builder.main_features(group)
    subs = item_builder.sub_features(group)
    infos = list(GroupFeature.objects.filter(group=group, kind="info").order_by("position", "name"))

    if request.method == "POST":
        feats_all = mains + subs + infos
        selected = {f.name: (request.POST.get("f_" + f.name) or "").strip() for f in feats_all}
        # Require every MAIN feature before an item can be created.
        missing_main = [f.name for f in mains if not selected.get(f.name)]
        if missing_main:
            return _json_response({"ok": False, "error": "Select all main features first."}, status=400)
        try:
            technical, item, prefix = item_builder.build_codes(group, selected)
            if not code_db.has_db(group):
                return _json_response({"ok": False, "error": "This group has no code database."}, status=400)
            if code_db.code_exists(group, technical, col=0):
                return _json_response({"ok": False, "error": "This exact item already exists (same technical code)."}, status=400)
            cells = item_builder.build_row_cells(group, selected, technical, item)
            code_db.insert_item(group, cells)
            meta = CodeTable.objects.filter(group=group).first()
            if meta:
                meta.row_count = code_db.row_count(group)
                meta.save(update_fields=["row_count", "updated_at"])
            _clear_caches()
            return _json_response({"ok": True, "technical": technical, "item": item})
        except Exception as exc:
            return _json_response({"ok": False, "error": str(exc)}, status=400)

    value_maps = {f.name: item_builder.value_code_map(group, f.name) for f in mains}
    options = {f.name: sorted(value_maps[f.name].keys()) for f in mains}
    sub_options = {f.name: sorted(item_builder.value_code_map(group, f.name).keys()) for f in subs}
    cfg = None
    try:
        from .models import GroupCodeConfig
        cfg = GroupCodeConfig.objects.filter(group=group).first()
    except Exception:
        pass
    config_dict = {
        "tech_start": cfg.tech_start if cfg else "", "tech_group": cfg.tech_group if cfg else "",
        "item_start": cfg.item_start if cfg else "", "item_group": cfg.item_group if cfg else "",
        "seq_digits": cfg.item_seq_digits if cfg else 5,
    }
    ctx = {
        "group": group, "mains": mains, "subs": subs, "infos": infos,
        # Raw Python objects, rendered in the template with |json_script — NOT
        # pre-dumped strings interpolated with |safe. A stray character from
        # an admin-controlled value (a code-table cell, a feature name) could
        # otherwise break out of the inline <script> block and run as script
        # in the page. json_script (Django's own template filter) escapes
        # </script>, <, and & correctly; the old |safe path did not.
        "value_maps": value_maps,
        "options_json": json.dumps(options, ensure_ascii=False),
        "sub_options": sub_options,
        "small_features": [f.name for f in item_builder.small_code_features(group)],
        "order": [f.name for f in mains],
        "config": config_dict, "has_db": code_db.has_db(group),
        "has_rules": item_builder.has_rules(group),
    }
    return render(request, "itemcoder/data_admin/item_new.html", ctx)


@group_data_access_required
def dm_code_distinct_api(request, group):
    """Distinct values of a column among rows matching the OTHER active filters
    (combined Excel-style filtering). Used by browse and price workspace."""
    _refresh_code_db_view()
    from . import code_db
    group = group.lower()
    try:
        target = int(request.GET.get("target"))
    except (TypeError, ValueError):
        return _json_response({"values": []})
    filters = _filters_from_request(request)
    return _json_response({"values": code_db.distinct_values_filtered(group, target, filters)})


@group_data_access_required
def dm_item_options_api(request, group):
    """Allowed values of a target MAIN feature given prior selections (cascade)."""
    from . import item_builder
    group = group.lower()
    target = (request.GET.get("target") or "").strip()
    selected = {}
    for key, val in request.GET.items():
        if key.startswith("f_") and str(val).strip():
            selected[key[2:]] = str(val).strip()
    selected.pop(target, None)
    return _json_response({"values": item_builder.allowed_values(group, target, selected)})


@group_data_access_required
def dm_item_seq_api(request, group):
    """Live next sequence for a small-code prefix (so the builder can show the
    full small code before saving)."""
    from . import code_db
    group = group.lower()
    prefix = (request.GET.get("prefix") or "").strip()
    cfg_digits = 5
    try:
        from .models import GroupCodeConfig
        c = GroupCodeConfig.objects.filter(group=group).first()
        if c:
            cfg_digits = c.item_seq_digits
    except Exception:
        pass
    seq = code_db.next_sequence(group, prefix) if (prefix and code_db.has_db(group)) else 1
    return _json_response({"sequence": str(seq).zfill(cfg_digits),
                           "item_code": prefix + str(seq).zfill(cfg_digits)})


# --------------------------------------------------------------------------- #
# Price workspace: virtual scroll over a group + filter + bulk pricing
# --------------------------------------------------------------------------- #
@price_access_required
def dm_price_workspace(request, pk):
    from .models import PriceList, CodeTable
    from . import code_db
    plist = get_object_or_404(PriceList, pk=pk)
    _sync_code_tables()
    groups = list(CodeTable.objects.order_by("group").values_list("group", flat=True))
    group = (request.GET.get("group") or (groups[0] if groups else "")).lower()
    columns = code_db.column_names(group) if group else []
    from .models import GroupFeature, FeatureValue
    if group:
        _ensure_features(group)
    main_filters = []
    main_cols = []
    for gf in GroupFeature.objects.filter(group=group, kind="main").order_by("position"):
        vals = list(FeatureValue.objects.filter(group=group, feature=gf.name)
                    .order_by("value").values_list("value", flat=True))
        main_filters.append({"name": gf.name, "col": gf.column_index, "values": vals})
        if gf.column_index >= 0:
            main_cols.append({"idx": gf.column_index, "name": gf.name})
    ctx = {"plist": plist, "groups": groups, "group": group,
           "columns": columns, "main_filters": main_filters, "main_cols": main_cols,
           "has_db": code_db.has_db(group)}
    return render(request, "itemcoder/data_admin/price_workspace.html", ctx)


def _filters_from_request(request, prefix="c"):
    filters = {}
    for key, val in request.GET.items():
        if key.startswith(prefix) and key[len(prefix):].isdigit() and str(val).strip():
            filters[int(key[len(prefix):])] = str(val).strip()
    return filters


@price_access_required
def dm_price_rows_api(request, pk):
    """Keyset page of items (+ current price in this list) for virtual scroll."""
    from .models import PriceList, CodePrice
    from . import code_db
    plist = get_object_or_404(PriceList, pk=pk)
    group = (request.GET.get("group") or "").lower()
    if not group or not code_db.has_db(group):
        return _json_response({"rows": [], "next": None, "total": 0})
    filters = _filters_from_request(request)
    try:
        after = int(request.GET.get("after", "-1"))
    except (TypeError, ValueError):
        after = -1
    limit = 80
    rows = code_db.fetch_window(group, after_row_no=after, limit=limit, filters=filters, exact=True)
    codes = [r["cells"][1] for r in rows if len(r["cells"]) > 1 and r["cells"][1]]
    price_map = {}
    if codes:
        for cp in CodePrice.objects.filter(price_list=plist, code__in=codes):
            price_map[cp.code] = cp.price
    out = []
    for r in rows:
        code = r["cells"][1] if len(r["cells"]) > 1 else ""
        out.append({"row_no": r["row_no"], "cells": r["cells"],
                    "code": code, "price": price_map.get(code, "")})
    next_after = rows[-1]["row_no"] if len(rows) == limit else None
    total = code_db.count_filtered(group, filters, exact=True)
    return _json_response({"rows": out, "next": next_after, "total": total,
                           "columns": code_db.column_names(group)})


@price_access_required
def dm_price_apply(request, pk):
    """Apply (or clear) a price for the whole filtered subset, set-based."""
    from .models import PriceList, CodePrice
    from . import code_db
    plist = get_object_or_404(PriceList, pk=pk)
    if request.method != "POST":
        return _json_response({"ok": False, "error": "POST required"}, status=405)
    group = (request.POST.get("group") or "").lower()
    if not group or not code_db.has_db(group):
        return _json_response({"ok": False, "error": "Unknown group"}, status=400)
    filters = {}
    for key, val in request.POST.items():
        if key.startswith("c") and key[1:].isdigit() and str(val).strip():
            filters[int(key[1:])] = str(val).strip()

    mode = request.POST.get("mode", "set")
    raw_price = (request.POST.get("price") or "").strip()
    count = 0
    if mode == "clear":
        if not can_delete_tool_data(request.user):
            return _json_response({"ok": False, "error": "Not allowed"}, status=403)
        codes = list(code_db.iter_filtered_codes(group, filters, exact=True))
        if codes:
            BATCH = 5000
            for i in range(0, len(codes), BATCH):
                count += CodePrice.objects.filter(price_list=plist,
                                                  code__in=codes[i:i + BATCH]).delete()[0]
    else:
        try:
            price_val = float(raw_price)
        except (TypeError, ValueError):
            return _json_response({"ok": False, "error": "Invalid price"}, status=400)
        buf = []
        for code in code_db.iter_filtered_codes(group, filters, exact=True):
            # update_or_create per row is safe but slower; do a fast upsert by
            # deleting+inserting in batches keyed by code within this list.
            buf.append(code)
            if len(buf) >= 5000:
                count += _bulk_set_prices(plist, buf, price_val)
                buf = []
        if buf:
            count += _bulk_set_prices(plist, buf, price_val)
    return _json_response({"ok": True, "count": count})


def _bulk_set_prices(plist, codes, price_val):
    from .models import CodePrice
    existing = {cp.code: cp for cp in CodePrice.objects.filter(price_list=plist, code__in=codes)}
    to_create, to_update = [], []
    for code in codes:
        cp = existing.get(code)
        if cp is None:
            to_create.append(CodePrice(price_list=plist, code=code, price=price_val))
        else:
            cp.price = price_val
            to_update.append(cp)
    if to_create:
        CodePrice.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        CodePrice.objects.bulk_update(to_update, ["price"])
    return len(codes)


@price_access_required
def dm_price_set_one(request, pk):
    """Set/clear the price of a single code (inline edit in the grid)."""
    from .models import PriceList, CodePrice
    plist = get_object_or_404(PriceList, pk=pk)
    if request.method != "POST":
        return _json_response({"ok": False}, status=405)
    code = (request.POST.get("code") or "").strip()
    raw = (request.POST.get("price") or "").strip()
    if not code:
        return _json_response({"ok": False, "error": "no code"}, status=400)
    if raw == "":
        if not can_delete_tool_data(request.user):
            return _json_response({"ok": False, "error": "Not allowed"}, status=403)
        CodePrice.objects.filter(price_list=plist, code=code).delete()
        return _json_response({"ok": True, "price": ""})
    try:
        price_val = float(raw)
    except ValueError:
        return _json_response({"ok": False, "error": "bad price"}, status=400)
    CodePrice.objects.update_or_create(price_list=plist, code=code,
                                       defaults={"price": price_val})
    return _json_response({"ok": True, "price": price_val})


# --------------------------------------------------------------------------- #
# Global JSON files (shared by every group): upload / replace / download.
# These are the engine-wide configuration files, not per-group data:
#   data.json, asign_code.json, common_rulse.json, data_translation.json,
#   final_arrange.json
# --------------------------------------------------------------------------- #
# Global JSON upload / download UI removed — engine files are edited on disk
# under itemcoder/resources/json (bind-mounted in Docker). Keep helpers that
# other admin paths may still call for atomic writes if needed later.
# --------------------------------------------------------------------------- #
GLOBAL_JSON_FILES = [
    ("data.json", "Feature extraction & regex (main engine file)"),
    ("asign_code.json", "Column mapping for code assignment (compound keys)"),
    ("common_rulse.json", "Global common rules (e.g. thickness allow-lists)"),
    ("data_translation.json", "Display translations"),
    ("final_arrange.json", "Final text arrangement & separator"),
]
GLOBAL_JSON_ALLOWED = {name for name, _desc in GLOBAL_JSON_FILES}


def _global_backup_dir():
    from .resource_paths import RESOURCE_DIR
    return os.path.join(str(RESOURCE_DIR), "json", "_backups")


def _write_global_json(filename, payload):
    """Overwrite a global JSON resource file, keeping a timestamped backup.

    The file is written atomically (temp file + replace) so a failure never
    leaves a half-written config on disk.
    """
    import datetime
    import shutil
    from .resource_paths import json_path
    target = json_path(filename)
    # Backup the current file first (if any).
    if os.path.exists(target):
        bdir = _global_backup_dir()
        os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            shutil.copy2(target, os.path.join(bdir, f"{filename}.{stamp}.bak"))
        except Exception:
            pass
    # Atomic write.
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# Rules file: per-group upload / download (rules_<group>.json)
# --------------------------------------------------------------------------- #
@group_data_access_required
def dm_rules_upload(request, group):
    """Replace a group's cascading-rules file from an uploaded JSON."""
    group = group.lower()
    if request.method != "POST":
        return redirect("dm_features", group=group)
    f = request.FILES.get("rules_file")
    if not f:
        messages.error(request, "Please choose a rules JSON file to upload.")
        return redirect("dm_features", group=group)
    try:
        obj = json.loads(f.read().decode("utf-8"))
    except Exception:
        messages.error(request, "That file is not valid JSON.")
        return redirect("dm_features", group=group)
    try:
        item_builder.replace_rules_from_obj(group, obj)
        _clear_caches()
        messages.success(request, f"Rules for '{group}' were replaced from the uploaded file.")
    except Exception as exc:
        messages.error(request, f"Could not apply the rules file: {exc}")
    return redirect("dm_features", group=group)


@group_data_access_required
def dm_rules_download(request, group):
    from django.http import JsonResponse
    group = group.lower()
    obj = item_builder.rules_export_obj(group)
    resp = JsonResponse(obj, json_dumps_params={"ensure_ascii": False, "indent": 1})
    resp["Content-Disposition"] = f'attachment; filename="rules_{group}.json"'
    return resp


# --------------------------------------------------------------------------- #
# Offer file: per-group page (interactive builder) + upload / download
# --------------------------------------------------------------------------- #
@group_data_access_required
def dm_offer(request, group):
    from . import offer_builder
    group = group.lower()
    _ensure_features(group)
    offer_builder.ensure_offer_file(group)
    mains = offer_builder.main_features(group)
    ctx = {
        "group": group,
        "mains": mains,
        "has_offer": offer_builder.has_offer(group),
    }
    return render(request, "itemcoder/data_admin/offer.html", ctx)


@group_data_access_required
def dm_offer_upload(request, group):
    from . import offer_builder
    group = group.lower()
    if request.method != "POST":
        return redirect("dm_offer", group=group)
    f = request.FILES.get("offer_file")
    if not f:
        messages.error(request, "Please choose an offer JSON file to upload.")
        return redirect("dm_offer", group=group)
    try:
        obj = json.loads(f.read().decode("utf-8"))
    except Exception:
        messages.error(request, "That file is not valid JSON.")
        return redirect("dm_offer", group=group)
    try:
        offer_builder.replace_offer_from_obj(group, obj)
        _clear_caches()
        messages.success(request, f"Offer for '{group}' was replaced from the uploaded file.")
    except Exception as exc:
        messages.error(request, f"Could not apply the offer file: {exc}")
    return redirect("dm_offer", group=group)


@group_data_access_required
def dm_offer_download(request, group):
    from django.http import JsonResponse
    from . import offer_builder
    group = group.lower()
    obj = offer_builder.offer_export_obj(group)
    resp = JsonResponse(obj, json_dumps_params={"ensure_ascii": False, "indent": 1})
    resp["Content-Disposition"] = f'attachment; filename="offer_{group}.json"'
    return resp


@group_data_access_required
def dm_offer_values_api(request, group):
    """Return selectable values for a picked (feature, value) pair and another
    feature, applying cross-value exclusion."""
    from . import offer_builder
    group = group.lower()
    sel_feature = (request.GET.get("feature") or "").strip()
    sel_value = (request.GET.get("value") or "").strip()
    other_feature = (request.GET.get("other") or "").strip()
    if not sel_feature:
        return _json_response({"ok": False, "error": "no feature"}, status=400)
    # List a feature's own values (for the value picker of the chosen feature).
    if not other_feature:
        return _json_response({
            "ok": True,
            "values": offer_builder.values_of_feature_available(group, sel_feature),
        })
    data = offer_builder.available_values(group, sel_feature, sel_value, other_feature)
    return _json_response({"ok": True, **data})


@group_data_access_required
def dm_offer_save_api(request, group):
    from . import offer_builder
    group = group.lower()
    if request.method != "POST":
        return _json_response({"ok": False}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return _json_response({"ok": False, "error": "bad json"}, status=400)
    sel_feature = (payload.get("feature") or "").strip()
    sel_value = (payload.get("value") or "").strip()
    picks = payload.get("picks") or {}
    if not sel_feature or not sel_value:
        return _json_response({"ok": False, "error": "feature and value required"}, status=400)
    try:
        offer_builder.save_pair(group, sel_feature, sel_value, picks)
        _clear_caches()
        return _json_response({"ok": True})
    except Exception as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)


# --------------------------------------------------------------------------- #
# Rebuilt offer builder APIs (searchable pickers + AND conditions). These serve
# the new offer.html; the old dm_offer_* endpoints above stay for compatibility.
# --------------------------------------------------------------------------- #
@group_data_access_required
def dm_offer_feature_values_api(request, group):
    """Return every value of a single feature (for the searchable value picker).

    Values come back as ``{"value","label"}`` pairs so placeholder values such
    as ``$coating$`` are displayed as ``NO COATING`` while the stored value is
    unchanged (matching/coding still treats it as empty)."""
    from . import offer_builder
    group = group.lower()
    feature = (request.GET.get("feature") or "").strip()
    # When building a condition for a (target feature, target value) pair, apply
    # cross-value exclusion: hide values already offered under another value of
    # the same target feature (a value belongs to only one target value).
    target = (request.GET.get("target") or "").strip()
    target_value = (request.GET.get("target_value") or "").strip()
    if not feature:
        return _json_response({"ok": False, "error": "no feature"}, status=400)
    if target and target_value and target != feature:
        values = offer_builder.values_of_feature_labeled_excluding(group, target, target_value, feature)
    else:
        values = offer_builder.values_of_feature_labeled(group, feature)
    return _json_response({"ok": True, "values": values})


@group_data_access_required
def dm_offer_conditions_api(request, group):
    """Return the saved conditions for a (target feature, target value) pair."""
    from . import offer_builder
    group = group.lower()
    feature = (request.GET.get("feature") or "").strip()
    value = (request.GET.get("value") or "").strip()
    if not feature or not value:
        return _json_response({"ok": False, "error": "feature and value required"}, status=400)
    conditions = offer_builder.get_conditions(group, feature, value)
    return _json_response({"ok": True, "conditions": conditions})


@group_data_access_required
def dm_offer_conditions_save_api(request, group):
    """Persist the full condition set for a (target feature, target value) pair
    and write offer_<group>.json immediately."""
    from . import offer_builder
    group = group.lower()
    if request.method != "POST":
        return _json_response({"ok": False}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return _json_response({"ok": False, "error": "bad json"}, status=400)
    feature = (payload.get("feature") or "").strip()
    value = (payload.get("value") or "").strip()
    conditions = payload.get("conditions") or []
    if not feature or not value:
        return _json_response({"ok": False, "error": "feature and value required"}, status=400)
    try:
        offer_builder.save_conditions(group, feature, value, conditions)
        _clear_caches()
        return _json_response({"ok": True})
    except Exception as exc:
        return _json_response({"ok": False, "error": str(exc)}, status=400)
