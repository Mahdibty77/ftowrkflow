"""Import a group's code table (CSV or Excel) into the per-group SQLite DB.

Same destination as Tool Data → Import group. Rows are streamed so multi-GB
files never need to sit fully in RAM. Powers ``manage.py import_codes`` and
``seed_demo``.
"""
from __future__ import annotations

import csv
import os
import re
from typing import Iterable, List, Optional

_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def _iter_csv_rows(path: str) -> Iterable[List[str]]:
    with open(path, newline="", encoding="utf-8", errors="replace") as fp:
        reader = csv.reader(fp)
        for row in reader:
            yield [("" if c is None else str(c)) for c in row]


def _iter_excel_rows(path_or_file) -> Iterable[List[str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path_or_file, read_only=True, data_only=True)
    try:
        ws = wb.active
        for raw in ws.iter_rows(values_only=True):
            yield [("" if c is None else str(c)) for c in raw]
    finally:
        wb.close()


def _row_iterator(source, filename: str) -> Iterable[List[str]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        yield from _iter_excel_rows(source)
    elif isinstance(source, str) and os.path.exists(source):
        yield from _iter_csv_rows(source)
    else:
        data = source.read()
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        reader = csv.reader(text.splitlines())
        for row in reader:
            yield [("" if c is None else str(c)) for c in row]


def is_valid_group_name(name: str) -> bool:
    return bool(_GROUP_RE.fullmatch(str(name or "").strip().lower()))


def import_code_table(
    group_name: str,
    source,
    *,
    filename: str = "",
    replace: bool = True,
    progress=None,
    user_id: Optional[int] = None,
) -> dict:
    """Stream a code table into ``itemcoder`` SQLite + CodeTable metadata.

    Always replaces the group's SQLite file (append is not supported for
    multi-million-row SQLite rebuilds; use a full re-import). Returns a summary
    dict compatible with the former coding importer shape.
    """
    from . import code_db, item_builder
    from .models import CodeTable, CodeTableRow

    if not replace:
        raise ValueError(
            "Append is not supported for SQLite code tables. "
            "Re-import the full file (omit --append) so the group DB stays consistent."
        )

    group_name = str(group_name).strip().lower()
    if not group_name:
        raise ValueError("A group name is required.")
    if not is_valid_group_name(group_name):
        raise ValueError("Group name must be letters, digits, underscore or hyphen only.")

    rows = _row_iterator(source, filename or (source if isinstance(source, str) else ""))
    try:
        header = next(rows)
    except StopIteration:
        raise ValueError("The file is empty.")

    columns = [str(c) for c in header]
    width = len(columns)

    def _progress(n):
        if progress:
            progress(n)

    total = code_db.build_db_from_rows(group_name, columns, rows, on_progress=_progress)
    verified = code_db.verify_group_db(group_name, expected_rows=total)
    if not verified.get("replaced") or not os.path.exists(verified["path"]):
        raise RuntimeError(
            "Import finished writing but the SQLite file was not found after replace. "
            "Path expected: %s" % code_db.group_db_path(group_name)
        )

    CodeTable.objects.update_or_create(
        group=group_name,
        defaults={
            "columns": columns,
            "row_count": total,
            "updated_by_id": user_id,
        },
    )
    CodeTableRow.objects.filter(group=group_name).delete()

    try:
        item_builder.seed_group_from_table(group_name, force=True)
    except Exception:
        pass
    try:
        item_builder.ensure_rules_file(group_name)
        from . import offer_builder
        offer_builder.ensure_offer_file(group_name)
    except Exception:
        pass

    try:
        from . import constants
        constants.clear_data_caches()
    except Exception:
        pass
    code_db.reset_connection(group_name)

    return {
        "group": group_name,
        "rows": total,
        "columns": width,
        "price_lists": 0,
        "path": verified["path"],
    }
