"""Per-group SQLite code database (fast lookup / filter / pagination).

This module stores each product group's coding-data table in its own on-disk
SQLite file under ``itemcoder/resources/db/<group>.sqlite3``.  Compared with
holding the whole table as an in-RAM pandas DataFrame, this scales to millions
of rows with tiny memory use and O(log n) indexed lookups.

The matching algorithm is intentionally identical to the previous pandas /
inverted-index implementation in ``code_assigner.assign_code_from_csv``:

* every feature value is normalized with the SAME ``_normalize_for_code`` rule;
* required features must match (AND), OR-groups are unioned then AND-ed in;
* the chosen row is the one with the SMALLEST original row order
  (``ORDER BY row_no LIMIT 1``), and column index 1 (the small Item_Code) is
  returned.

This was verified row-for-row against the pandas path on the full pipe table
(37,153 rows, 0 mismatches), so switching a group to SQLite does not change any
output.  Groups without a SQLite file keep using the original CSV/pandas path.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

from .resource_paths import RESOURCE_DIR

DB_DIR = os.path.join(str(RESOURCE_DIR), "db")

# Same normalization the code matcher uses (letters/digits + Persian, lowered).
_NORM_RE = re.compile(r"[^0-9a-zA-Z\u0600-\u06FF]")
# Absent cell markers written in CSV / schema: $coating$, $nocoating$, (no)coating
_ABSENT_DOLLAR_RE = re.compile(r"^\$.*\$$")
_ABSENT_NO_RE = re.compile(r"^\(no\)", re.I)

# One cached read-only connection per group (SQLite connections are cheap, but
# re-opening per request is wasteful).  Guarded by a lock for thread safety.
_CONN_CACHE: Dict[str, sqlite3.Connection] = {}
_CONN_LOCK = threading.Lock()
# (mtime, size) of the file the cached connection was opened against. After an
# import replaces pipe.sqlite3, other gunicorn workers must not keep reading the
# old inode through a stale fd (Linux keeps the old file alive until close).
_CONN_META: Dict[str, Tuple[float, int]] = {}

# column_names() is hit on every code lookup during Build TO; cache by mtime.
_COLUMNS_CACHE: Dict[str, Tuple[float, List[str]]] = {}


# Bounded memo for the regex path only. Code tables have highly repetitive
# feature values (materials, methods, standards …); memoizing them turns tens of
# millions of regex calls during a big import into a handful. Bounded so unique
# code columns can never bloat memory. _normalize stays a pure function, so the
# cache is always correct.
_NORM_MEMO: Dict[str, str] = {}
_NORM_MEMO_MAX = 200_000

# Filtered DISTINCT results for browse/price dropdowns. Keyed by
# (group, col, filter-tuple, limit) + validated against the DB file mtime so an
# import never serves stale options. Bounded so long sessions cannot grow forever.
_DISTINCT_CACHE: Dict[Tuple, Tuple[float, List[str]]] = {}
_DISTINCT_CACHE_MAX = 512
_DISTINCT_CACHE_LOCK = threading.Lock()


def is_absent_cell(value) -> bool:
    """True for blank / null / ``$coating$`` / ``$nocoating$`` / ``(no)coating``.

    These mean the feature is intentionally absent. Display shows ``NO COATING``;
    code lookup treats them like an empty feature variable.
    """
    s = str(value or "").strip()
    if not s or s.lower() == "null":
        return True
    if _ABSENT_NO_RE.match(s) or _ABSENT_DOLLAR_RE.match(s):
        return True
    return False


def _absent_cell_sql(pos: int) -> str:
    """SQL predicate: cell is empty OR a legacy absent placeholder still in ``c``."""
    p = int(pos)
    return (
        f"("
        f"n{p} IN ('','null') "
        f"OR TRIM(COALESCE(c{p},'')) = '' "
        f"OR LOWER(TRIM(c{p})) = 'null' "
        f"OR TRIM(c{p}) LIKE '$%$' "
        f"OR LOWER(TRIM(c{p})) LIKE '(no)%'"
        f")"
    )


def _normalize(value) -> str:
    if value is None:
        return ""
    s = str(value)
    # Must run BEFORE stripping punctuation, otherwise ``$coating$`` becomes
    # ``coating`` and no longer matches an empty feature lookup.
    if is_absent_cell(s):
        return ""
    # Fast path: plain ASCII alphanumeric (the big/item code columns and most
    # numeric cells) need no regex — just lowercase. Identical output to the
    # regex, but avoids the sub() entirely for the highest-cardinality columns.
    if s.isascii() and s.isalnum():
        return s.lower()
    cached = _NORM_MEMO.get(s)
    if cached is not None:
        return cached
    r = _NORM_RE.sub("", s.strip()).lower()
    if len(_NORM_MEMO) < _NORM_MEMO_MAX:
        _NORM_MEMO[s] = r
    return r


def group_db_path(group: str) -> str:
    return os.path.join(DB_DIR, f"{str(group).strip().lower()}.sqlite3")


def has_db(group: str) -> bool:
    return os.path.exists(group_db_path(group))


def _replace_db_file(tmp: str, path: str, group: str) -> None:
    """Atomically put the new DB in place, with Windows file-lock retries.

    Another worker may still hold a read-only handle on the old ``.sqlite3``.
    On Windows that makes ``os.replace`` fail with PermissionError; we close
    our cache, retry, then fall back to delete+rename.
    """
    g = str(group).strip().lower()
    last_err: Optional[BaseException] = None
    for attempt in range(10):
        reset_connection(g)
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_err = exc
            time.sleep(0.35 * (attempt + 1))
        except OSError as exc:
            last_err = exc
            time.sleep(0.35 * (attempt + 1))
    # Last resort: remove the locked destination, then rename the temp file.
    reset_connection(g)
    try:
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
        return
    except Exception as exc:
        raise RuntimeError(
            f"Could not replace database file '{path}' "
            f"(is it open elsewhere?): {last_err or exc}"
        ) from exc


def verify_group_db(group: str, *, expected_rows: Optional[int] = None) -> dict:
    """Confirm the group's SQLite file exists, is final (not .building), and
    its live COUNT(*) matches ``expected_rows`` when given.

    Returns a small info dict for the import success message. Raises
    ``RuntimeError`` when the replace did not land correctly.
    """
    g = str(group).strip().lower()
    path = group_db_path(g)
    building = path + ".building"
    if os.path.exists(building):
        raise RuntimeError(
            f"Temporary build file still exists (replace did not finish): {building}"
        )
    if not os.path.exists(path):
        raise RuntimeError(f"SQLite database was not created at: {path}")

    reset_connection(g)
    con = sqlite3.connect(path)
    try:
        actual = int(con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        meta = con.execute("SELECT v FROM meta WHERE k='row_count'").fetchone()
        meta_n = int(meta[0]) if meta and str(meta[0]).strip().lstrip("-").isdigit() else -1
    finally:
        con.close()

    if expected_rows is not None and actual != int(expected_rows):
        raise RuntimeError(
            f"Row-count mismatch after replace: SQLite has {actual:,} rows, "
            f"import expected {int(expected_rows):,}."
        )
    if expected_rows is not None and meta_n != int(expected_rows):
        raise RuntimeError(
            f"Meta row_count mismatch after replace: meta={meta_n:,}, "
            f"expected {int(expected_rows):,}."
        )

    size = os.path.getsize(path)
    return {
        "path": path,
        "rows": actual,
        "meta_rows": meta_n,
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "replaced": True,
    }

def _file_sig(path: str) -> Optional[Tuple[float, int]]:
    try:
        st = os.stat(path)
        return (float(st.st_mtime), int(st.st_size))
    except OSError:
        return None


def _open(group: str) -> Optional[sqlite3.Connection]:
    """Return a cached, read-optimized connection for a group (or None).

    Reopens automatically when the on-disk file is replaced (import), so other
    gunicorn workers never keep serving a deleted inode's old row count.
    """
    g = str(group).strip().lower()
    path = group_db_path(g)
    sig = _file_sig(path)
    if sig is None:
        return None
    with _CONN_LOCK:
        con = _CONN_CACHE.get(g)
        if con is not None and _CONN_META.get(g) == sig:
            return con
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
            _CONN_CACHE.pop(g, None)
            _CONN_META.pop(g, None)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA mmap_size=268435456")  # memory-map up to 256MB
        except Exception:
            pass
        _CONN_CACHE[g] = con
        _CONN_META[g] = sig
        return con


def reset_connection(group: str) -> None:
    """Drop the cached connection (call after rebuilding/inserting)."""
    g = str(group).strip().lower()
    with _CONN_LOCK:
        con = _CONN_CACHE.pop(g, None)
        _CONN_META.pop(g, None)
        _COLUMNS_CACHE.pop(g, None)
    with _DISTINCT_CACHE_LOCK:
        dead = [k for k in _DISTINCT_CACHE if k and k[0] == g]
        for k in dead:
            _DISTINCT_CACHE.pop(k, None)
    if con is not None:
        try:
            con.close()
        except Exception:
            pass


def _distinct_cache_key(group: str, col_pos: int,
                        filters: Optional[Dict[int, str]], limit: int) -> Tuple:
    other = tuple(sorted(
        (int(k), str(v)) for k, v in (filters or {}).items() if int(k) != int(col_pos)
    ))
    return (str(group).strip().lower(), int(col_pos), other, int(limit))


def _db_mtime(group: str) -> float:
    path = group_db_path(group)
    if not path or not os.path.exists(path):
        return 0.0
    try:
        return float(os.path.getmtime(path))
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def _index_positions_for_group(group: str) -> List[int]:
    """0-based column positions the lookup matches on, from asign_code.json.

    Returning an empty list simply means 'index nothing special'; the table is
    still usable (lookup falls back to a full scan, which we avoid by always
    indexing here).  Any error returns [] so building never fails.
    """
    try:
        from .regex_patterns import load_json_file
        from .resource_paths import json_path
        from .composite_keys import get_by_alias
        mapping = load_json_file(json_path("asign_code.json"))
        gmap = get_by_alias(mapping, str(group).strip().lower())
        positions = set()
        if isinstance(gmap, dict):
            for _type_key, feat_map in gmap.items():
                if not isinstance(feat_map, dict):
                    continue
                for _feat, col_spec in feat_map.items():
                    m = re.match(r"col[_\-]?(\d+)", str(col_spec).strip().lower())
                    if m:
                        positions.add(int(m.group(1)) - 1)
        return sorted(p for p in positions if p >= 0)
    except Exception:
        return []


def build_db_from_rows(group: str, columns: List[str], rows: Iterable[List[str]],
                       *, batch: int = 5000, on_progress=None, on_status=None) -> int:
    """Create/replace ``<group>.sqlite3`` from header + row iterator.

    ``rows`` may be a generator so multi-million-row imports never hold the whole
    table in memory.  Returns the number of rows written.

    ``on_progress`` (optional) is called with the running row count roughly once
    per batch, so a background importer can report live progress for very large
    files without slowing the insert loop.

    ``on_status`` (optional) receives short human labels for phases after the
    row insert (indexes / replace / verify).
    """
    g = str(group).strip().lower()
    os.makedirs(DB_DIR, exist_ok=True)
    reset_connection(g)
    path = group_db_path(g)
    tmp = path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)

    def _status(msg: str) -> None:
        if on_status is None:
            return
        try:
            on_status(str(msg))
        except Exception:
            pass

    ncols = len(columns)
    craw = ", ".join(f"c{i} TEXT" for i in range(ncols))
    cnorm = ", ".join(f"n{i} TEXT" for i in range(ncols))

    _status("Writing rows into temporary database…")
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute(f"CREATE TABLE items(row_no INTEGER PRIMARY KEY, {craw}, {cnorm})")
        con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        import json as _json
        con.execute("INSERT INTO meta(k,v) VALUES('columns',?)",
                    (_json.dumps([str(c) for c in columns], ensure_ascii=False),))
        con.execute("INSERT INTO meta(k,v) VALUES('ncols',?)", (str(ncols),))

        placeholders = ",".join(["?"] * (1 + 2 * ncols))
        insert_sql = f"INSERT INTO items VALUES({placeholders})"

        buf, n = [], 0
        for cells in rows:
            cells = list(cells or [])
            if len(cells) < ncols:
                cells = cells + [""] * (ncols - len(cells))
            elif len(cells) > ncols:
                cells = cells[:ncols]
            craw_vals = []
            for c in cells:
                raw = "" if c is None else str(c)
                # Store absent placeholders as empty so n{i}='' and browse shows
                # NO <FEATURE> via the feature-name label (same as Item Builder).
                if is_absent_cell(raw):
                    raw = ""
                craw_vals.append(raw)
            cnorm_vals = [_normalize(c) for c in craw_vals]
            buf.append([n] + craw_vals + cnorm_vals)
            n += 1
            if len(buf) >= batch:
                con.executemany(insert_sql, buf)
                buf = []
                if on_progress is not None:
                    try:
                        on_progress(n)
                    except Exception:
                        pass
        if buf:
            con.executemany(insert_sql, buf)
        if on_progress is not None:
            try:
                on_progress(n)
            except Exception:
                pass
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('row_count',?)", (str(n),))

        # Index the columns the matcher uses + the item-code column.
        _status("Building code-lookup indexes…")
        positions = _index_positions_for_group(g)
        for p in positions:
            if 0 <= p < ncols:
                con.execute(f"CREATE INDEX IF NOT EXISTS ix_n{p} ON items(n{p})")
        if positions:
            # Composite index over the (required + or) matched columns speeds the
            # AND part; SQLite picks the most selective automatically.
            cols = ",".join(f"n{p}" for p in positions if 0 <= p < ncols)
            if cols:
                con.execute(f"CREATE INDEX IF NOT EXISTS ix_match ON items({cols})")
        if ncols > 1:
            con.execute("CREATE INDEX IF NOT EXISTS ix_c1 ON items(c1)")

        # Browse/filter path uses exact matches on c{i}. Index every column so
        # DISTINCT and filtered scans stay fast on multi-million-row groups.
        _status("Building browse/filter indexes…")
        for i in range(ncols):
            con.execute(f"CREATE INDEX IF NOT EXISTS ix_c{i} ON items(c{i})")

        # Precompute distinct values per column for instant unfiltered dropdowns.
        _status("Caching distinct filter values…")
        con.execute("CREATE TABLE col_distinct(col INTEGER NOT NULL, v TEXT NOT NULL, "
                    "PRIMARY KEY(col, v)) WITHOUT ROWID")
        for i in range(ncols):
            con.execute(
                f"INSERT OR IGNORE INTO col_distinct(col, v) "
                f"SELECT {int(i)}, c{i} FROM items "
                f"WHERE c{i} IS NOT NULL AND TRIM(c{i}) <> '' "
                f"GROUP BY c{i}"
            )

        try:
            con.execute("ANALYZE")
        except Exception:
            pass
        con.commit()
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    else:
        con.close()

    _status("Replacing database file on disk…")
    _replace_db_file(tmp, path, g)
    reset_connection(g)
    # Tell every gunicorn worker immediately: the on-disk DB changed.
    try:
        from . import cache_sync
        cache_sync.bump_epoch()
    except Exception:
        pass

    _status("Verifying replaced database…")
    verify_group_db(g, expected_rows=n)
    return n

def build_db_from_dataframe(group: str, df) -> int:
    columns = [str(c) for c in df.columns.tolist()]

    def _row_iter():
        for rec in df.itertuples(index=False, name=None):
            yield ["" if v is None else str(v) for v in rec]

    return build_db_from_rows(group, columns, _row_iter())


# --------------------------------------------------------------------------- #
# Lookup (exact replica of the pandas/inverted-index algorithm)
# --------------------------------------------------------------------------- #
def features_for_codes(group: str, codes: Iterable[str],
                       col_indices: List[int]) -> Dict[str, Dict[int, str]]:
    """Return ``{Item_Code: {column_index: raw_value}}`` for the given codes.

    Used to hydrate per-row feature values in the PI tool (so the in-tool
    feature filter works even for forms saved before feature values were
    persisted). ``col_indices`` are the raw column positions of the group's
    main features. Read-only, indexed on c1 (Item_Code)."""
    code_list = [str(c).strip() for c in codes if str(c).strip()]
    if not group or not col_indices or not code_list:
        return {}
    path = group_db_path(group)
    if not path or not os.path.exists(path):
        return {}
    out: Dict[str, Dict[int, str]] = {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        try:
            cols = ", ".join("c%d" % i for i in col_indices)
            # Chunk the IN() list to stay well under SQLite's variable limit.
            for start in range(0, len(code_list), 400):
                chunk = code_list[start:start + 400]
                qs = ",".join("?" * len(chunk))
                sql = f"SELECT c1, {cols} FROM items WHERE c1 IN ({qs})"
                for row in con.execute(sql, chunk):
                    code = str(row[0])
                    out[code] = {col_indices[i]: ("" if row[i + 1] is None else str(row[i + 1]))
                                 for i in range(len(col_indices))}
        finally:
            con.close()
    except Exception:
        return out
    return out


def _where_from_search(
    search_by_pos: Dict[int, str],
    or_groups_by_pos: Optional[List[Tuple[str, List[Tuple[int, str]]]]] = None,
) -> Tuple[List[str], List[str]]:
    """Build SQL WHERE fragments + params shared by lookup and count.

    Empty OR groups (every member ``""``) are skipped — they mean "this OR
    dimension is unused" (e.g. flange SCH/SDR when CLASS is set), not "match
    nothing". A non-empty OR group with only ``null`` members still constrains
    to absent cells.
    """
    where: List[str] = []
    params: List[str] = []
    for pos, sv in (search_by_pos or {}).items():
        if sv == "" or sv == "null":
            # Empty / null feature → match empty cells AND legacy CSV placeholders
            # still stored as ``$coating$`` / ``(no)coating`` (pre-normalize DBs).
            where.append(_absent_cell_sql(pos))
            continue
        where.append(f"n{pos}=?")
        params.append(sv)

    for _name, feats in (or_groups_by_pos or []):
        ors = []
        for pos, sv in feats:
            if sv == "":
                continue
            if sv == "null":
                ors.append(_absent_cell_sql(pos))
            else:
                ors.append(f"n{pos}=?")
                params.append(sv)
        if not ors:
            # All members empty → OR group not applicable; do not kill the query.
            continue
        where.append("(" + " OR ".join(ors) + ")")
    return where, params


def lookup_code(group: str, search_by_pos: Dict[int, str],
                or_groups_by_pos: Optional[List[Tuple[str, List[Tuple[int, str]]]]] = None
                ) -> Optional[str]:
    """Run the code lookup against a group's SQLite file.

    ``search_by_pos``    : {required_col_pos: normalized_search_value}
    ``or_groups_by_pos`` : [(or_group_name, [(col_pos, normalized_value), ...]), ...]

    Returns the matched Item_Code (column 1), ``""`` when nothing matches, or
    ``None`` when the group has no SQLite file (caller should use the CSV path).
    The selection rule is ``ORDER BY row_no LIMIT 1`` == smallest original row.
    """
    con = _open(group)
    if con is None:
        return None

    where, params = _where_from_search(search_by_pos, or_groups_by_pos)

    if not where:
        return ""
    sql = "SELECT c1 FROM items WHERE " + " AND ".join(where) + " ORDER BY row_no LIMIT 1"
    try:
        row = con.execute(sql, params).fetchone()
    except Exception:
        return ""
    if row is None:
        return ""
    return "" if row[0] is None else str(row[0]).strip()


def count_matches(group: str, search_by_pos: Dict[int, str],
                  or_groups_by_pos: Optional[List[Tuple[str, List[Tuple[int, str]]]]] = None
                  ) -> int:
    """``COUNT(*)`` variant of :func:`lookup_code` — read-only diagnostic used by
    the Engineering Assistant to explain a "no code found" row (which feature's
    value has zero matching rows). Never used on the hot coding path.

    Empty ``search_by_pos`` (and no OR constraints) means "no constraints" →
    full table row count. Pass the same ``or_groups_by_pos`` assign uses so
    diagnose stays aligned with real code lookup.
    """
    con = _open(group)
    if con is None:
        return 0
    where, params = _where_from_search(search_by_pos, or_groups_by_pos)
    if not where:
        try:
            row = con.execute("SELECT COUNT(*) FROM items").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            return 0

    sql = "SELECT COUNT(*) FROM items WHERE " + " AND ".join(where)
    try:
        row = con.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# Metadata / browse helpers (used by the admin price + item screens)
# --------------------------------------------------------------------------- #
def column_names(group: str) -> List[str]:
    g = str(group).strip().lower()
    path = group_db_path(g)
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else -1.0
    except OSError:
        mtime = -1.0
    cached = _COLUMNS_CACHE.get(g)
    if cached is not None and cached[0] == mtime:
        return list(cached[1])

    con = _open(g)
    if con is None:
        return []
    try:
        import json as _json
        row = con.execute("SELECT v FROM meta WHERE k='columns'").fetchone()
        cols = _json.loads(row[0]) if row else []
        _COLUMNS_CACHE[g] = (mtime, list(cols))
        return list(cols)
    except Exception:
        return []


def row_count(group: str) -> int:
    con = _open(group)
    if con is None:
        return 0
    try:
        row = con.execute("SELECT v FROM meta WHERE k='row_count'").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# Columns that may legitimately be empty on a valid row. Steel pipe often has
# no SDR/PN/SN/WT/NACE; coating/schedule may also be absent. Only these are
# offered as "NO X" filter tokens; they must NEVER be used as junk-row deletes.
NULLABLE_COLUMN_HINTS = (
    "coat", "schedule", "sch",
    "sdr", "pn", "sn", "wt", "nace",
    "rating", "pressure",
)


def _is_nullable_col(name) -> bool:
    n = str(name or "").strip().lower()
    return any(h in n for h in NULLABLE_COLUMN_HINTS)


def delete_rows_with_empty_columns(group: str, col_indices) -> int:
    """Delete rows where ANY of the given (required) feature columns is empty.

    Used after an import to drop junk rows — e.g. a pipe row missing its
    material_type. Coating/schedule are NOT passed here because they may be
    legitimately empty. Returns the number of rows removed.
    """
    g = str(group).strip().lower()
    cols = [int(i) for i in (col_indices or []) if int(i) >= 0]
    if not cols:
        return 0
    path = group_db_path(g)
    if not os.path.exists(path):
        return 0
    reset_connection(g)
    con = sqlite3.connect(path)
    try:
        conds = " OR ".join(
            [f"(TRIM(COALESCE(c{i},''))='' OR TRIM(COALESCE(n{i},''))='')" for i in cols])
        cur = con.execute(f"DELETE FROM items WHERE {conds}")
        removed = cur.rowcount or 0
        # Derive the new count from the stored row_count (set during build) so we
        # avoid a second full-table COUNT(*) scan over millions of rows.
        prev = con.execute("SELECT v FROM meta WHERE k='row_count'").fetchone()
        if prev is not None and str(prev[0]).strip().lstrip("-").isdigit():
            n = max(0, int(prev[0]) - removed)
        else:
            n = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('row_count',?)", (str(n),))
        con.commit()
        return removed
    finally:
        con.close()
        reset_connection(g)


def _is_empty_token(v) -> bool:
    """A filter value that means 'absent': empty, '(no)coating', '$coating$'."""
    return is_absent_cell(v)


def _filter_clause(filters: Optional[Dict[int, str]], exact: bool = False):
    where, params = [], []
    for pos, needle in (filters or {}).items():
        needle = str(needle).strip()
        if not needle:
            continue
        if _is_empty_token(needle):
            # "NO COATING" / empty selects absent cells (empty + placeholders).
            where.append(_absent_cell_sql(int(pos)))
            continue
        if exact:
            where.append(f"c{int(pos)} = ?")
            params.append(needle)
        else:
            where.append(f"c{int(pos)} LIKE ? ESCAPE '\\'")
            safe = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{safe}%")
    return where, params


def count_filtered(group: str, filters: Optional[Dict[int, str]] = None,
                   exact: bool = False) -> int:
    con = _open(group)
    if con is None:
        return 0
    where, params = _filter_clause(filters, exact)
    sql = "SELECT COUNT(*) FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    try:
        return int(con.execute(sql, params).fetchone()[0])
    except Exception:
        return 0


def fetch_window(group: str, *, after_row_no: int = -1, limit: int = 100,
                 filters: Optional[Dict[int, str]] = None, exact: bool = False) -> List[dict]:
    """Keyset-paginated rows for virtual scrolling (O(log n) per page)."""
    con = _open(group)
    if con is None:
        return []
    where, params = _filter_clause(filters, exact)
    where.append("row_no > ?")
    params.append(int(after_row_no))
    ncols = len(column_names(group))
    cols = ",".join(f"c{i}" for i in range(ncols))
    sql = (f"SELECT row_no,{cols} FROM items WHERE " + " AND ".join(where) +
           " ORDER BY row_no LIMIT ?")
    params.append(int(limit))
    try:
        out = []
        for r in con.execute(sql, params):
            out.append({"row_no": r[0], "cells": [("" if r[i + 1] is None else str(r[i + 1])) for i in range(ncols)]})
        return out
    except Exception:
        return []


def distinct_values_filtered(group: str, col_pos: int,
                             filters: Optional[Dict[int, str]] = None,
                             limit: int = 1000) -> List[str]:
    """Distinct non-empty values of one column among rows matching the OTHER
    filters (the target column's own filter is ignored). Powers combined,
    Excel-style filters where each field only offers values still reachable.

    Unfiltered lookups use the ``col_distinct`` cache built at import time so
    opening a dropdown on a multi-million-row table stays O(distinct count).

    Filtered lookups are memoized in-process (keyed by group/col/filters and
    invalidated when the SQLite file changes) so reopening the same combo is
    cheap.
    """
    g = str(group).strip().lower()
    col_pos = int(col_pos)
    limit = int(limit)
    cache_key = _distinct_cache_key(g, col_pos, filters, limit)
    mtime = _db_mtime(g)
    with _DISTINCT_CACHE_LOCK:
        hit = _DISTINCT_CACHE.get(cache_key)
        if hit is not None and hit[0] == mtime:
            return list(hit[1])

    con = _open(g)
    if con is None:
        return []
    other = {k: v for k, v in (filters or {}).items() if int(k) != col_pos}
    where, params = _filter_clause(other, exact=True)
    vals: List[str] = []
    has_empty = False
    cols = column_names(g)
    cname = cols[col_pos] if 0 <= col_pos < len(cols) else "value"
    nullable = _is_nullable_col(cname)
    try:
        if not where:
            try:
                rows = con.execute(
                    "SELECT v FROM col_distinct WHERE col=? ORDER BY v LIMIT ?",
                    (col_pos, limit),
                ).fetchall()
                vals = [str(r[0]) for r in rows]
            except Exception:
                rows = con.execute(
                    f"SELECT DISTINCT c{col_pos} AS v FROM items "
                    f"WHERE c{col_pos} IS NOT NULL AND TRIM(c{col_pos}) <> '' "
                    f"ORDER BY v LIMIT ?",
                    (limit,),
                ).fetchall()
                vals = [str(r[0]) for r in rows]
            if nullable:
                try:
                    has_empty = con.execute(
                        f"SELECT 1 FROM items WHERE {_absent_cell_sql(col_pos)} LIMIT 1"
                    ).fetchone() is not None
                except Exception:
                    has_empty = False
        else:
            nonempty = f"(c{col_pos} IS NOT NULL AND TRIM(c{col_pos}) <> '')"
            sql = (f"SELECT DISTINCT c{col_pos} AS v FROM items WHERE "
                   + " AND ".join(where + [nonempty])
                   + f" ORDER BY v LIMIT {limit}")
            vals = [str(r[0]) for r in con.execute(sql, params).fetchall()]
            if nullable:
                try:
                    empty_sql = ("SELECT 1 FROM items WHERE "
                                 + " AND ".join(where + [_absent_cell_sql(col_pos)])
                                 + " LIMIT 1")
                    has_empty = con.execute(empty_sql, params).fetchone() is not None
                except Exception:
                    has_empty = False
    except Exception:
        return []

    out = []
    for v in vals:
        if is_absent_cell(v):
            has_empty = True
            continue
        if str(v).strip() == "":
            continue
        out.append(v)
    if has_empty and nullable:
        out.insert(0, "(no)" + str(cname).strip().lower())

    with _DISTINCT_CACHE_LOCK:
        if len(_DISTINCT_CACHE) >= _DISTINCT_CACHE_MAX:
            for old in list(_DISTINCT_CACHE.keys())[: max(1, _DISTINCT_CACHE_MAX // 4)]:
                _DISTINCT_CACHE.pop(old, None)
        _DISTINCT_CACHE[cache_key] = (mtime, list(out))
    return out


def distinct_values(group: str, col_pos: int, limit: int = 500) -> List[str]:
    """Distinct non-empty raw values of a column (for filter dropdowns)."""
    return distinct_values_filtered(group, col_pos, filters=None, limit=limit)


def iter_filtered_codes(group: str, filters: Optional[Dict[int, str]] = None,
                        code_col: int = 1, *, chunk: int = 10000, exact: bool = False) -> Iterable[str]:
    """Yield the Item_Code of every row matching ``filters`` (for bulk pricing)."""
    con = _open(group)
    if con is None:
        return
    base_where, base_params = _filter_clause(filters, exact)
    after = -1
    while True:
        where = list(base_where) + ["row_no > ?"]
        params = list(base_params) + [after]
        sql = (f"SELECT row_no, c{int(code_col)} FROM items WHERE " +
               " AND ".join(where) + " ORDER BY row_no LIMIT ?")
        params.append(chunk)
        got = con.execute(sql, params).fetchall()
        if not got:
            break
        for r in got:
            after = r[0]
            code = "" if r[1] is None else str(r[1]).strip()
            if code:
                yield code
        if len(got) < chunk:
            break


# --------------------------------------------------------------------------- #
# Single-item insert (used by the admin item builder)
# --------------------------------------------------------------------------- #
def _rw_connect(group: str):
    g = str(group).strip().lower()
    path = group_db_path(g)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No code database for group '{g}'.")
    reset_connection(g)
    return sqlite3.connect(path), g, path


def add_column(group: str, title: str) -> int:
    """Append a new column to a group's table (e.g. a sub feature / info column).

    Returns the new 0-based column index. The new column starts empty for all
    rows; SQLite ADD COLUMN is a cheap metadata-only change even for millions of
    rows. The display title is appended to the stored ``columns`` list.
    """
    con, g, _ = _rw_connect(group)
    try:
        cols = column_names(g)
        new_idx = len(cols)
        con.execute(f"ALTER TABLE items ADD COLUMN c{new_idx} TEXT DEFAULT ''")
        con.execute(f"ALTER TABLE items ADD COLUMN n{new_idx} TEXT DEFAULT ''")
        try:
            con.execute(f"CREATE INDEX IF NOT EXISTS ix_c{new_idx} ON items(c{new_idx})")
        except Exception:
            pass
        cols = cols + [str(title)]
        import json as _json
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('columns',?)",
                    (_json.dumps(cols, ensure_ascii=False),))
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('ncols',?)", (str(len(cols)),))
        con.commit()
    finally:
        con.close()
    reset_connection(g)
    return new_idx


def _note_distinct(con, col: int, value: str) -> None:
    """Keep the unfiltered distinct cache fresh after a cell write."""
    v = "" if value is None else str(value).strip()
    if not v:
        return
    try:
        con.execute("INSERT OR IGNORE INTO col_distinct(col, v) VALUES(?,?)",
                    (int(col), v))
    except Exception:
        pass


def update_cell(group: str, row_no: int, col: int, value: str) -> bool:
    """Update one cell (raw + normalized) of one row. Returns True on success."""
    con, g, _ = _rw_connect(group)
    try:
        ncols = len(column_names(g))
        if not (0 <= int(col) < ncols):
            return False
        v = "" if value is None else str(value).strip()
        # Browse may show "NO COATING" / "$coating$"; persist as empty so lookup
        # keeps treating the cell as an absent feature.
        if is_absent_cell(v) or re.match(r"^no\s+\S", v, re.I):
            v = ""
        con.execute(f"UPDATE items SET c{int(col)}=?, n{int(col)}=? WHERE row_no=?",
                    (v, _normalize(v), int(row_no)))
        _note_distinct(con, int(col), v)
        con.commit()
        return True
    finally:
        con.close()
        reset_connection(g)


def delete_rows(group: str, row_nos) -> int:
    """Delete rows by row_no. Returns number deleted."""
    row_nos = [int(r) for r in row_nos if str(r).strip() != ""]
    if not row_nos:
        return 0
    con, g, _ = _rw_connect(group)
    try:
        n = 0
        BATCH = 500
        for i in range(0, len(row_nos), BATCH):
            chunk = row_nos[i:i + BATCH]
            ph = ",".join(["?"] * len(chunk))
            cur = con.execute(f"DELETE FROM items WHERE row_no IN ({ph})", chunk)
            n += cur.rowcount or 0
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('row_count',?)",
                    (str(con.execute("SELECT COUNT(*) FROM items").fetchone()[0]),))
        con.commit()
        return n
    finally:
        con.close()
        reset_connection(g)


def wipe_group(group: str) -> bool:
    """Delete the entire SQLite database file for a group."""
    g = str(group).strip().lower()
    reset_connection(g)
    path = group_db_path(g)
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False


def next_sequence(group: str, prefix: str, *, code_col: int = 1) -> int:
    """Next sequence number for Item_Codes starting with ``prefix``.

    Reads the max numeric suffix already stored for that prefix and returns +1,
    so each prefix keeps an independent counter exactly like the generator.
    """
    con = _open(group)
    if con is None:
        return 1
    plen = len(prefix)
    try:
        rows = con.execute(
            f"SELECT c{int(code_col)} FROM items WHERE c{int(code_col)} LIKE ?",
            (prefix.replace("%", "").replace("_", "") + "%",)).fetchall()
    except Exception:
        return 1
    mx = 0
    for r in rows:
        code = "" if r[0] is None else str(r[0]).strip()
        if len(code) > plen and code.startswith(prefix):
            tail = code[plen:]
            if tail.isdigit():
                mx = max(mx, int(tail))
    return mx + 1


def insert_item(group: str, cells: List[str]) -> int:
    """Append one row (row_no = max+1). Returns the new row_no.

    Opens its own read/write connection (the cached one is read-only) and resets
    the cache afterwards so later reads see the new row.
    """
    g = str(group).strip().lower()
    path = group_db_path(g)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No code database for group '{g}'.")
    ncols = len(column_names(g))
    cells = list(cells or [])
    if len(cells) < ncols:
        cells = cells + [""] * (ncols - len(cells))
    elif len(cells) > ncols:
        cells = cells[:ncols]
    craw = [("" if c is None else str(c)) for c in cells]
    cnorm = [_normalize(c) for c in craw]
    reset_connection(g)
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT COALESCE(MAX(row_no),-1)+1 FROM items").fetchone()
        new_no = int(row[0])
        placeholders = ",".join(["?"] * (1 + 2 * ncols))
        con.execute(f"INSERT INTO items VALUES({placeholders})", [new_no] + craw + cnorm)
        for i, v in enumerate(craw):
            _note_distinct(con, i, v)
        con.execute("UPDATE meta SET v=CAST((SELECT COUNT(*) FROM items) AS TEXT) WHERE k='row_count'")
        con.commit()
    finally:
        con.close()
    reset_connection(g)
    return new_no


_NULL_TEXTS = {"", "none", "null", "nan"}


def _clean_cell(value) -> str:
    """Match calculation_engine.clean_text: strip + null-like -> blank."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _NULL_TEXTS else text


def row_by_code(group: str, code: str) -> Optional[List[str]]:
    """Return the cleaned cells of the earliest row whose Item_Code (c1) or
    Technical_Code (c0) equals ``code`` — used by calculation columns
    (weight/price).  Mirrors the CSV code->row index exactly (min row order,
    c1/c0 match), but as a single indexed query instead of a full RAM index.
    """
    con = _open(group)
    if con is None:
        return None
    code_s = _clean_cell(code)
    if not code_s:
        return None
    ncols = len(column_names(group))
    cols = ",".join(f"c{i}" for i in range(ncols))
    try:
        row = con.execute(
            f"SELECT {cols} FROM items WHERE c1=? OR c0=? ORDER BY row_no LIMIT 1",
            (code_s, code_s)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return [_clean_cell(row[i]) for i in range(ncols)]


def code_exists(group: str, value: str, *, col: int = 0) -> bool:
    """True if a raw value already exists in column ``col`` (duplicate guard)."""
    con = _open(group)
    if con is None:
        return False
    try:
        row = con.execute(
            f"SELECT 1 FROM items WHERE c{int(col)}=? LIMIT 1", (str(value),)).fetchone()
        return row is not None
    except Exception:
        return False
