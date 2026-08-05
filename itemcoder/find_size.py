"""Per-group NPS size resolver driven by ``resources/csv/size/find_size_<group>.csv``.

Each inquiry row uses the CSV that matches its detected group, e.g.
``find_size_pipe.csv``, ``find_size_fitting.csv``. When that file is missing,
no equivalent mapping is applied and the raw Size cell is kept as-is.

Column-header grammar (examples from the CSV header row)::

    NPS(defualt)                         → canonical / default column (exact match)
    (NPS)-"                              → value + suffix ``"`` only (no bare value)
    DN||IN-[DN]-mm||milimeter            → prefixes DN/IN, suffixes mm/milimeter,
                                           brackets = bare value also allowed
    (OD)-mm                              → value + suffix ``mm`` only

Parentheses around the column name mean: match only with the declared
prefix/suffix forms. Square brackets mean: also accept the bare cell value
(after affix forms have been tried).
"""

from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from typing import Iterable

from .resource_paths import csv_path, resolve_resource_path

_MARKER_RE = re.compile(
    r"^(?:(?P<prefixes>.+?)-)?"
    r"(?:\((?P<paren>[^)]+)\)|\[(?P<bracket>[^\]]+)\])"
    r"(?:-(?P<suffixes>.+))?$"
)

# Normalize fancy inch marks to ASCII double-quote.
_INCH_RE = re.compile(r"[″”‟″]")
_SPACE_RE = re.compile(r"\s+")
_GROUP_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _normalize_cell(text: str) -> str:
    s = _INCH_RE.sub('"', str(text or ""))
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def _group_slug(group) -> str:
    """Normalize a group key to a filename slug (``pipe``, ``fitting``, …)."""
    s = str(group or "").strip().lower()
    if not s or s in {"null", "nan", "none", "<na>"}:
        return ""
    # Drop common prefixes like G_pipe → pipe
    if s.startswith("g_"):
        s = s[2:]
    s = _GROUP_SLUG_RE.sub("_", s).strip("_")
    return s


def _split_affixes(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in str(raw).split("||")]
    return [p for p in parts if p]


def parse_column_header(header: str) -> dict | None:
    """Parse a non-default column header into match rules.

    Returns ``None`` when the header is not a rule column (e.g. the default
    ``NPS(defualt)`` column which has no leading ``(name)`` / ``[name]`` marker
    in the grammar sense used by alternate columns).
    """
    h = str(header or "").strip()
    m = _MARKER_RE.match(h)
    if not m:
        return None
    allow_bare = m.group("bracket") is not None
    return {
        "name": (m.group("bracket") or m.group("paren") or "").strip(),
        "prefixes": _split_affixes(m.group("prefixes")),
        "suffixes": _split_affixes(m.group("suffixes")),
        "allow_bare": allow_bare,
    }


def _values_equal(a: str, b: str) -> bool:
    a = str(a).strip()
    b = str(b).strip()
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return True
    # Numeric equality: 0.25 == 0.250, 8 == 8.0
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _affix_candidates(value: str, prefixes: list[str], suffixes: list[str]) -> list[str]:
    """Build match strings with at least one prefix or suffix applied."""
    prefs = prefixes or [""]
    sufs = suffixes or [""]
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = _normalize_cell(s)
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    has_p = bool(prefixes)
    has_s = bool(suffixes)
    if has_p and has_s:
        for p in prefixes:
            for s in suffixes:
                add(f"{p} {value}{s}")
                add(f"{p}{value}{s}")
                add(f"{p} {value} {s}")
        for p in prefixes:
            add(f"{p} {value}")
            add(f"{p}{value}")
        for s in suffixes:
            add(f"{value}{s}")
            add(f"{value} {s}")
    elif has_p:
        for p in prefixes:
            add(f"{p} {value}")
            add(f"{p}{value}")
    elif has_s:
        for s in suffixes:
            add(f"{value}{s}")
            add(f"{value} {s}")
    return out


def cell_matches_column_value(
    cell: str,
    value: str,
    *,
    prefixes: list[str],
    suffixes: list[str],
    allow_bare: bool,
) -> bool:
    """True when *cell* identifies this column *value* under the header rules."""
    cell_n = _normalize_cell(cell)
    value_n = _normalize_cell(value)
    if not cell_n or not value_n:
        return False

    # 1) Affix forms first (prefix / suffix).
    for cand in _affix_candidates(value_n, prefixes, suffixes):
        if _values_equal(cell_n, cand):
            return True

    # Loose affix check: strip known prefixes/suffixes from the cell and compare
    # the remainder to the CSV value (covers DN100 vs value 100, 13.7MM, etc.).
    cell_body = cell_n
    stripped_affix = False
    lower_cell = cell_n.lower()
    for p in sorted(prefixes, key=len, reverse=True):
        pl = p.lower()
        if lower_cell.startswith(pl):
            rest = cell_n[len(p):].lstrip(" :-")
            cell_body = rest
            stripped_affix = True
            lower_cell = cell_body.lower()
            break
    for s in sorted(suffixes, key=len, reverse=True):
        sl = s.lower()
        if lower_cell.endswith(sl):
            rest = cell_body[: len(cell_body) - len(s)].rstrip(" :-")
            cell_body = rest
            stripped_affix = True
            break
    if stripped_affix and _values_equal(cell_body, value_n):
        return True

    # 2) Bare value only when the header uses [brackets].
    if allow_bare and _values_equal(cell_n, value_n):
        return True
    return False


def resolve_find_size_path(group=None) -> str | None:
    """Return the CSV path for *group*, or ``None`` when it does not exist.

    Looks for ``find_size_<group>.csv`` then ``find-size_<group>.csv`` under
    ``resources/csv/size/``. No global fallback — missing group file means
    no size equivalent mapping.
    """
    slug = _group_slug(group)
    if not slug:
        return None
    candidates = (
        f"itemcoder/resources/csv/size/find_size_{slug}.csv",
        f"itemcoder/resources/csv/size/find-size_{slug}.csv",
    )
    for rel in candidates:
        full = resolve_resource_path(rel)
        if full and os.path.isfile(full):
            return full
    for name in (f"find_size_{slug}.csv", f"find-size_{slug}.csv"):
        direct = csv_path("size", name)
        if os.path.isfile(direct):
            return direct
    return None


# Back-compat alias used by older call sites / validate helpers.
def _resolve_find_size_path(group=None) -> str | None:
    return resolve_find_size_path(group)


@lru_cache(maxsize=32)
def _load_table(path: str, mtime: float) -> tuple[str, tuple[dict, ...], tuple[tuple[str, ...], ...]]:
    """Return (default_header, column_rules, rows_as_tuples)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            headers = next(reader)
        except StopIteration:
            return "", (), ()
    headers = [str(h).strip() for h in headers]
    if not headers:
        return "", (), ()

    default_header = headers[0]
    rules: list[dict] = []
    for h in headers[1:]:
        parsed = parse_column_header(h)
        rules.append({
            "header": h,
            "prefixes": (parsed or {}).get("prefixes") or [],
            "suffixes": (parsed or {}).get("suffixes") or [],
            "allow_bare": bool((parsed or {}).get("allow_bare")),
            # If the header did not parse, fall back to bare-only equality so
            # a mistyped header still does something safe.
            "parsed": parsed is not None,
        })
        if parsed is None:
            # Unparsed alternate column: treat like bracketed bare match only.
            rules[-1]["allow_bare"] = True

    rows: list[tuple[str, ...]] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for raw in reader:
            if not raw:
                continue
            # Pad / trim to header width
            cells = [str(c).strip() for c in raw]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append(tuple(cells[: len(headers)]))

    rule_tuples = tuple(
        (
            r["header"],
            tuple(r["prefixes"]),
            tuple(r["suffixes"]),
            r["allow_bare"],
            r["parsed"],
        )
        for r in rules
    )
    return default_header, rule_tuples, tuple(rows)


def clear_find_size_cache() -> None:
    _load_table.cache_clear()


def load_find_size_table(group=None):
    path = resolve_find_size_path(group)
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    default_header, rule_tuples, rows = _load_table(path, mtime)
    rules = [
        {
            "header": h,
            "prefixes": list(prefs),
            "suffixes": list(sufs),
            "allow_bare": allow_bare,
            "parsed": parsed,
        }
        for (h, prefs, sufs, allow_bare, parsed) in rule_tuples
    ]
    return {
        "path": path,
        "group": _group_slug(group),
        "default_header": default_header,
        "rules": rules,
        "rows": rows,
    }


def resolve_size(row_size, group=None) -> dict:
    """Map a raw size cell to ``clean_size`` (NPS default) + ``display_size``.

    Mapping is **per group**: only ``find_size_<group>.csv`` is consulted. When
    the group is empty or its CSV is missing, the original cell is returned
    unchanged (no equivalent applied).

    * Exact match on the default column → unchanged.
    * Match via an alternate column (in header order) →
      ``display_size = "{original} ({nps})"`` and ``clean_size = nps``.
    * File present but no match → ``clean_size`` is ``"null"`` (no NPS mapping)
      but ``display_size`` keeps the original inquiry cell.
    """
    original = _normalize_cell(row_size)
    if not original or original.lower() in {"null", "nan", "none"}:
        return {"clean_size": "null", "display_size": "null"}

    table = load_find_size_table(group)
    if not table or not table["rows"]:
        # No group file (or empty): do not map — keep the row's Size as written.
        return {"clean_size": original, "display_size": original}

    rows = table["rows"]
    rules = table["rules"]

    # 1) Exact match on the default / NPS column (string identity only —
    #    never float-coerce, or ``4`` could collide with unrelated numerics).
    defaults = [r[0] for r in rows if r and str(r[0]).strip()]
    original_l = original.lower()
    for d in defaults:
        d_norm = _normalize_cell(d)
        if original_l == d_norm.lower():
            return {"clean_size": d, "display_size": d}

    # 2) Walk alternate columns in order.
    for col_idx, rule in enumerate(rules, start=1):
        prefixes = rule["prefixes"]
        suffixes = rule["suffixes"]
        allow_bare = rule["allow_bare"]
        # Affix-required columns with neither prefix nor suffix cannot match
        # anything useful unless allow_bare.
        if not rule["parsed"] and not allow_bare:
            continue

        # Pass A: affix forms only (even for bracket columns).
        for row in rows:
            if col_idx >= len(row):
                continue
            val = str(row[col_idx]).strip()
            if not val or val.lower() in {"nan", "none"}:
                continue
            if cell_matches_column_value(
                original, val,
                prefixes=prefixes, suffixes=suffixes, allow_bare=False,
            ):
                nps = str(row[0]).strip()
                if not nps:
                    continue
                return {
                    "clean_size": nps,
                    "display_size": f"{original} ({nps})",
                }

        # Pass B: bare value (bracket columns only).
        if allow_bare:
            for row in rows:
                if col_idx >= len(row):
                    continue
                val = str(row[col_idx]).strip()
                if not val or val.lower() in {"nan", "none"}:
                    continue
                if cell_matches_column_value(
                    original, val,
                    prefixes=[], suffixes=[], allow_bare=True,
                ):
                    nps = str(row[0]).strip()
                    if not nps:
                        continue
                    return {
                        "clean_size": nps,
                        "display_size": f"{original} ({nps})",
                    }

    # Unmapped size: keep the inquiry spelling visible; coding path treats
    # clean_size "null" as "no NPS" and falls back to the raw cell.
    return {"clean_size": "null", "display_size": original}
