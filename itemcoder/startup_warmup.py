"""Warm static processor resources at server startup.

The project still accepts CSV/JSON resources as the source of truth.  This module
only loads and indexes them in RAM once, so upload/live-edit requests do not pay
that cost later.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Iterable, Set

from .resource_paths import JSON_DIR, CSV_DIR, json_path, resolve_resource_path
from .regex_patterns import load_json_file, load_feature_values, parse_csv_for_field
from .table_layout_manager import load_table_layout_config, _get_group_lookup_plan
from .calculation_engine import _code_row_index_for_group
from .code_assigner import load_code_resources, _get_assign_code_feature_plan, _get_code_index

_WARMED = False


def _iter_csv_refs(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_csv_refs(key)
            yield from _iter_csv_refs(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_csv_refs(item)
    elif isinstance(obj, str) and obj.strip().lower().endswith('.csv'):
        yield obj.strip()


def _warm_json_files() -> dict:
    loaded = {}
    for name in (
        'data.json', 'asign_code.json', 'table_layout.json', 'confind_size.json',
        'final_arrange.json', 'common_rulse.json', 'offer.json',
        'data_translation.json',
    ):
        path = json_path(name)
        if os.path.exists(path):
            try:
                loaded[name] = load_json_file(path)
            except Exception:
                loaded[name] = None
    return loaded


def _warm_feature_csvs(loaded_json: dict) -> None:
    seen: Set[str] = set()
    for data in loaded_json.values():
        for ref in _iter_csv_refs(data):
            if ref in seen:
                continue
            seen.add(ref)
            try:
                # Feature files have two access paths in the current code.
                load_feature_values(ref)
                parse_csv_for_field(ref)
            except Exception:
                # Some refs are not feature-list CSVs or may be optional paths.
                pass


def _warm_code_tables(loaded_json: dict) -> None:
    """Warm code-lookup paths without loading multi-million-row tables into pandas.

    Groups with a SQLite DB only open a read connection and parse asign_code
    plans (matching stays identical). CSV/pandas indexes are built only when
    no SQLite exists — avoids ~GB RAM × GUNICORN_WORKERS.
    """
    mapping_all = loaded_json.get('asign_code.json')
    if not isinstance(mapping_all, dict):
        try:
            mapping_all = load_json_file(json_path('asign_code.json'))
        except Exception:
            mapping_all = {}

    groups: Set[str] = set()
    code_dir = os.path.join(str(CSV_DIR), 'code_table')
    if os.path.isdir(code_dir):
        for filename in os.listdir(code_dir):
            if filename.endswith('_coding_data.csv'):
                groups.add(filename[:-len('_coding_data.csv')].lower())

    try:
        from . import code_db
        if os.path.isdir(code_db.DB_DIR):
            for filename in os.listdir(code_db.DB_DIR):
                if filename.endswith('.sqlite3') and not filename.endswith('.building'):
                    groups.add(filename[:-len('.sqlite3')].lower())
    except Exception:
        code_db = None  # type: ignore

    for group in sorted(groups):
        try:
            from . import code_db as _cdb
            if _cdb.has_db(group):
                # Open mmap'd connection + column meta; lookup uses SQL indexes.
                _cdb.column_names(group)
                group_map = None
                for k, v in (mapping_all or {}).items():
                    if str(k).strip().lower() == group:
                        group_map = v
                        break
                if isinstance(group_map, dict):
                    for type_name in group_map.keys():
                        _get_assign_code_feature_plan(
                            group, str(type_name).strip().lower(), mapping_all
                        )
                continue

            df, mapping = load_code_resources(group)
            if df is None:
                continue

            group_map = None
            for k, v in (mapping_all or {}).items():
                if str(k).strip().lower() == group:
                    group_map = v
                    break
            if isinstance(group_map, dict):
                for type_name in group_map.keys():
                    plan = _get_assign_code_feature_plan(
                        group, str(type_name).strip().lower(), mapping
                    )
                    if not plan:
                        continue
                    required, or_groups = plan
                    cols = {col_idx - 1 for _f, col_idx, _c, _p in required if col_idx > 0}
                    for feats in or_groups.values():
                        cols.update(col_idx - 1 for _f, col_idx in feats if col_idx > 0)
                    for col_pos in cols:
                        if 0 <= col_pos < df.shape[1]:
                            _get_code_index(group, df, col_pos)

            # CSV-only groups: compact code→row map for price/weight.
            _code_row_index_for_group(group)
        except Exception:
            continue


def _warm_table_layout() -> None:
    try:
        config = load_table_layout_config()
        for group in list(config.keys()):
            if group in {'column_layout', 'extra_column_layout'}:
                continue
            _get_group_lookup_plan(config, group)
    except Exception:
        pass


def warm_all_runtime_caches(force: bool = False) -> None:
    global _WARMED
    if _WARMED and not force:
        return
    loaded = _warm_json_files()
    _warm_feature_csvs(loaded)
    _warm_table_layout()
    _warm_code_tables(loaded)
    _WARMED = True


def should_warm_on_ready() -> bool:
    if os.environ.get('ITEMCODER_DISABLE_WARMUP') == '1':
        return False
    argv = ' '.join(sys.argv).lower()
    # In runserver with autoreload, warm only in the child process.
    if 'runserver' in argv:
        return os.environ.get('RUN_MAIN') == 'true'
    # Warm in production WSGI/ASGI process as well.
    return any(token in argv for token in ('gunicorn', 'uwsgi', 'daphne', 'uvicorn')) or 'manage.py' not in argv
