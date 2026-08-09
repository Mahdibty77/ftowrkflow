"""Professional A4-landscape PDF export for TO / PI.

Renders the print-tuned HTML templates (based on ``PI_form.html``), paginates
item rows by each row's own content height, then converts to PDF via
headless Chrome/Edge.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from django.template.loader import render_to_string

from .constants import FormKind
from .export_data import (
    VENDOR_NAME,
    build_export_rows,
    client_name_only,
    code_no_for,
    doc_no_export,
    export_columns,
    export_name_for,
    form_date_jalali,
    normalize_terms,
    order_no,
    pi_totals,
    vendor_last_name,
    vendor_title,
    vendor_signature_data_uri,
    vendor_stamp_data_uri,
)

# Approximate layout budget in mm (A4 landscape 297 × 210).
# Kept slightly conservative so printed rows never spill under the
# signature / footer cards.
_PAGE_H = 210.0
_HEADER_H = 25.5          # doc-head (~95px)
_INFO_H = 22.0            # info cards
_SIGN_H = 16.5            # compact approval strip
_FOOTER_H = 11.0          # footer strip + margin
_TABLE_PAD = 6.0          # table-zone vertical margins/padding
_THEAD_H = 8.0
_TOTALS_H = 16.0          # last-page totals panel (PI)
_SAFETY_MM = 8.0          # page inset (side/bottom pad) + stamp overlap slack
_PAGE_INSET_V_MM = 6.0    # document padding top+bottom (2.5mm + 3.5mm)
_MIN_ROW_H = 5.2
_LINE_H_MM = 3.15         # ~10px font × 1.15 line-height
_ROW_PAD_MM = 2.8         # top+bottom cell padding (~5px each side)

# What document.html *actually* renders a wrapped cell with, at 96dpi: 10px
# text on a 1.2 line-height (12px) inside 5px of padding on every side. The two
# rounded figures above stay exactly as they are — every page break in every
# document that fits today is derived from them — but the row splitter cannot
# use them. It is the one caller that packs a cell right up to the top of the
# budget, so where an ordinary row absorbs the rounding in its slack, a split
# part has none left and a tenth of a millimetre per line becomes a clipped line
# of text at the bottom of the sheet.
_CSS_LINE_H_MM = 12 * 25.4 / 96      # 3.1750
_CSS_CELL_PAD_MM = 10 * 25.4 / 96    # 2.6458 (5px each side, both axes)

# The widest a single character can be in a cell. ``_estimate_lines`` divides by
# an *average* glyph width, which is the right answer for a height estimate and
# the wrong one for a guarantee: measured against Chrome, a run of "W", "@", an
# em dash or CJK wraps to as much as 2.5× the estimate, and a part sized to the
# estimate then hangs past the sheet and is clipped away by ``.table-zone``'s
# overflow — text on no sheet at all, which is the one outcome this splitter
# exists to prevent. A full em (10px ≈ 2.65mm) is the widest advance any of
# those glyphs takes; 2.8 carries the measurement's rounding on top. Parts are
# therefore capped at the number of characters that fits *even if every one of
# them is the widest character in the font*, which costs paper on a row that
# already spans sheets and never costs a character.
_MAX_GLYPH_W_MM = 2.8

# Column width fractions of the table (must sum ≈ 1.0)
_PI_WIDTHS = {
    "client_no": 0.04, "item": 0.035, "code": 0.07, "desc_client": 0.11,
    "remark": 0.07, "desc_ftco": 0.11, "size": 0.045, "qty": 0.035, "unit": 0.035,
    "brand": 0.055, "time": 0.06, "unit_price": 0.09, "service_price": 0.09,
    "total_price": 0.09,
}
_TO_WIDTHS = {
    "client_no": 0.05, "item": 0.05, "code": 0.09, "desc_client": 0.16,
    "remark": 0.10, "desc_ftco": 0.16, "size": 0.07, "qty": 0.05, "unit": 0.05,
    "brand": 0.10, "time": 0.12,
}

# The Technical Problems (TO) and Services (PI) sheets carry their own tables
# with their own fixed column widths — the percentages below are the ones
# declared as ``<th style="width: …">`` in document.html. They are needed here
# because pagination has to estimate how tall each row wraps, exactly the way it
# does for the main item table; without them these sheets were emitted as one
# single page and every row past the bottom of the sheet was clipped away by the
# fixed-height ``.document`` box.
_ISSUE_COLUMNS: list[tuple[str, str]] = [
    ("Client No.", "client_no"),
    ("Item No.", "item"),
    ("DESCRIPTION CLIENT", "desc_client"),
    ("Technical Problem Detail", "reason"),
]
_ISSUE_WIDTHS = {"client_no": 0.12, "item": 0.12, "desc_client": 0.36, "reason": 0.40}
_SERVICE_COLUMNS: list[tuple[str, str]] = [
    ("CLIENT ITEM", "client_no"),
    ("FTCO ITEM", "item"),
    ("DESCRIPTION CLIENT", "desc_client"),
    ("SERVICE COMMENT", "comment"),
    ("QTY", "qty"),
    ("UNIT PRICE SERVICE", "unit_svc_price"),
    ("TOTAL PRICE SERVICE", "total_svc_price"),
]
_SERVICE_WIDTHS = {
    "client_no": 0.09, "item": 0.09, "desc_client": 0.24, "comment": 0.20,
    "qty": 0.07, "unit_svc_price": 0.15, "total_svc_price": 0.16,
}
# Those two sheets also print a coloured banner between the info cards and the
# table (~60px ≈ 16mm at 96dpi) which eats into the body height available to
# rows.
_BANNER_H = 16.0

_TABLE_WIDTH_MM = 265.0  # page width minus side margins
_WS_RE = re.compile(r"\s+")


def _plain_cell_text(text) -> str:
    """The characters the reader actually sees in that cell.

    document.html prints the cell through ``{{ cell.text }}``, so Django's
    autoescaping puts the operator's string on paper *verbatim*: a pasted
    ``<br>`` shows as the four characters "<br>", ``&nbsp;`` as the six
    characters "&nbsp;". This used to strip tags and decode entities before
    measuring, which measured a document nobody prints — a cell holding a run of
    ``<br>`` was measured as empty and its 800 printed characters overflowed the
    sheet and were clipped. Measuring the raw string is both what the renderer
    does and the safe direction to err in: an over-estimate costs white space at
    the bottom of a sheet, an under-estimate costs text.
    """
    return str(text or "")


def _chars_per_line(width_mm: float, font_pt: float = 10.0) -> int:
    """How many characters of body text fit on one line of a cell that wide.

    Split out of ``_estimate_lines`` unchanged so the row splitter below can ask
    the same question the height estimator asks — the two must agree exactly or
    a "part" sized by one would not fit the budget checked by the other.
    """
    # Average glyph width for Segoe UI / Helvetica at ~10pt.
    char_w = font_pt * 0.3528 * 0.46
    return max(4, int(width_mm / char_w))


def _estimate_lines(text: str, width_mm: float, font_pt: float = 10.0) -> int:
    """How many lines this cell text wraps to in the export table.

    The export ``<td>`` keeps the CSS default ``white-space: normal``, so the
    browser collapses **every** run of whitespace — spaces, tabs and newlines
    alike — into a single space and breaks lines on width only. A newline in the
    operator's text therefore produces no line of its own on paper, and this
    estimator has to model that or it reports a height the renderer never
    produces. It used to count each "\\n" as a hard break, which over-counted a
    multi-line Excel cell (and, once over-tall rows began to be split, turned
    that over-count into extra physical sheets carrying nothing).

    A blank / whitespace-only cell still occupies one line: the row exists and
    the padding is drawn even with nothing in it.
    """
    plain = _WS_RE.sub(" ", _plain_cell_text(text)).strip()
    if not plain:
        return 1
    per_line = _chars_per_line(width_mm, font_pt)
    return max(1, math.ceil(len(plain) / per_line))


def _row_needed_height(row: dict, columns: list[tuple[str, str]], widths: dict) -> float:
    """Natural height for one item row from its widest wrapped cell."""
    lines = 1
    for _title, key in columns:
        frac = widths.get(key, 0.08)
        cell_text = row.get(key, "")
        if key == "desc_ftco" and str(row.get("_flag_label") or "").strip():
            cell_text = f"{cell_text} [{row.get('_flag_label')}]"
        lines = max(lines, _estimate_lines(cell_text, _TABLE_WIDTH_MM * frac))
    return max(_MIN_ROW_H, _ROW_PAD_MM + lines * _LINE_H_MM)


# Whitespace runs and non-whitespace runs, in order. ``"".join(findall(...))``
# reproduces the subject string character for character, which is what lets the
# splitter below cut a cell up without ever rewriting a single byte of it.
_TOKEN_RE = re.compile(r"\s+|\S+")


def _split_max_lines(avail_mm: float) -> int:
    """Wrapped lines a split part may use inside a body ``avail_mm`` tall."""
    return max(1, int((avail_mm - _CSS_CELL_PAD_MM) / _CSS_LINE_H_MM))


def _safe_chars_per_line(width_mm: float) -> int:
    """Characters that fit on one line of that cell *whatever* they are."""
    return max(1, int((width_mm - _CSS_CELL_PAD_MM) / _MAX_GLYPH_W_MM))


def _part_fits(text: str, width_mm: float, max_lines: int, suffix: str = "") -> bool:
    """Will this piece still be inside the sheet after the browser wraps it?

    Two questions, and a piece has to answer both. ``_estimate_lines`` asks the
    likely one — how tall does this normally wrap — and is what keeps a split
    from being needlessly aggressive on ordinary prose. The character count asks
    the guaranteed one, and is what makes "no part is ever clipped" a property
    of the code rather than a property of the sample data.
    """
    if _estimate_lines(text + suffix, width_mm) > max_lines:
        return False
    # Whitespace is measured the way the browser renders it: any run of it
    # collapses to one space, so a piece cannot be pushed over by line breaks
    # the reader never sees.
    plain = _WS_RE.sub(" ", text + suffix).strip()
    return len(plain) <= max_lines * _safe_chars_per_line(width_mm)


def _split_cell_text(text: str, width_mm: float, max_lines: int,
                     suffix: str = "") -> list[str]:
    """Cut one cell's text into pieces that each fit ``max_lines`` lines.

    "Fit" is ``_part_fits`` — the wrap estimate *and* the worst-case-glyph
    character cap — because a piece that only fits on average is a piece that
    the sheet clips on the day the text is a part number in capitals.

    The pieces are *slices of the original string*: joining the returned list
    back together gives the input back exactly, whitespace included. Nothing is
    normalised, re-ordered or re-flowed — the document must show the operator's
    text verbatim, only broken over more than one sheet.

    ``suffix`` is text the template glues onto the cell without it being part of
    the cell value (today: the ``[ISSUE]``-style flag badge). Room for it is
    reserved in *every* piece: the badge is printed on the last one, where it
    trails the finished description exactly as it does on a row that never had
    to be split, and reserving it everywhere is what keeps that last piece from
    running a line over the budget.
    """
    # …but only while the reservation leaves room for text at all. If the badge
    # alone fills the budget, reserving it makes every fit test below fail,
    # ``_fitting_take`` is forced down to its one-character floor, and the cell
    # comes out one character per sheet with every one of those sheets still
    # over the budget — the exact failure this splitter exists to prevent,
    # reached by trying too hard to avoid it. In that geometry no split can put
    # the badge on paper inside the sheet, so it stops being charged to the
    # text: the description still lands whole and inside the sheet, which is the
    # part a reader cannot do without. Testing one character *plus* the badge is
    # what makes that one-character floor provably safe rather than merely
    # likely — "x" stands for any single character, since both halves of
    # ``_part_fits`` count characters and not glyphs.
    if suffix.strip() and not _part_fits("x", width_mm, max_lines, suffix):
        suffix = ""

    if _part_fits(text, width_mm, max_lines, suffix):
        # Fits as it stands — the overwhelmingly common case, and the reason a
        # short cell on a split row simply prints once and leaves the
        # continuation blank.
        return [text]

    chunks: list[str] = []
    cur = ""
    for token in _TOKEN_RE.findall(text):
        if not token.strip():
            # A whitespace run — however long — collapses to a single space in
            # the browser, so it can never push a piece over the budget and must
            # never be measured as if it could. It simply rides along with
            # whatever piece is open. Measuring it as content is what used to
            # break this loop: a whitespace run was accepted into an empty
            # ``cur`` (a whitespace-only string "fits" by definition), the next
            # word then found the piece full, ``cur.strip()`` was falsy so
            # control fell into the mid-word branch below, and the piece was
            # emitted with the entire run inside it — a run of 200 newlines came
            # out several times the height of the sheet it had to fit on, and
            # everything past the bottom of the sheet was clipped away.
            cur += token
            continue
        while token:
            if _part_fits(cur + token, width_mm, max_lines, suffix):
                cur += token
                token = ""
            elif cur.strip():
                # Full: close this piece and retry the whole word at the start
                # of the next one, so words are never broken needlessly.
                chunks.append(cur)
                cur = ""
            else:
                # A single "word" longer than a whole part (a pasted spec with
                # no spaces, say). The cell already wraps with
                # ``overflow-wrap: anywhere`` in the browser, so cutting inside
                # the word is exactly what the reader would have seen anyway.
                take = _fitting_take(cur, token, width_mm, max_lines, suffix)
                chunks.append(cur + token[:take])
                cur = ""
                token = token[take:]
    if cur:
        chunks.append(cur)
    # Belt and braces. Everything above is *meant* to keep each piece inside the
    # budget, but a piece that is over it must never reach the template: the
    # sheet is a fixed 210mm box with ``overflow: hidden``, so an over-tall part
    # is not a cosmetic slip, it is a character of the description printed on no
    # sheet at all. Whatever the loop produced is therefore re-checked here and
    # cut by character count if it is still too tall — a hard cut can cost a
    # line break, which is acceptable; losing text is not.
    safe: list[str] = []
    for chunk in chunks:
        safe.extend(_hard_wrap(chunk, width_mm, max_lines, suffix))
    return safe or [""]


def _fitting_take(cur: str, token: str, width_mm: float, max_lines: int,
                  suffix: str) -> int:
    """Largest prefix of ``token`` that keeps ``cur + prefix + suffix`` in budget.

    At least one character is always taken so the caller's loop always advances
    and no character of the cell can be dropped. That floor is safe rather than
    merely necessary: callers only ask for a prefix with ``cur`` empty or pure
    whitespace (which collapses to nothing in the browser), and
    ``_split_cell_text`` has already dropped any ``suffix`` that one character
    could not be printed beside — so the floor is always inside the budget.
    """
    # Start from the widest prefix that could possibly fit — the character cap,
    # which is always the tighter of the two halves of ``_part_fits`` — and walk
    # down from there rather than from the whole token.
    take = min(len(token), max(1, _safe_chars_per_line(width_mm) * max_lines))
    while take > 1 and not _part_fits(cur + token[:take], width_mm, max_lines, suffix):
        take -= 1
    return take


def _hard_wrap(text: str, width_mm: float, max_lines: int, suffix: str) -> list[str]:
    """Cut ``text`` by character count until every piece fits the budget.

    Joined back together the pieces are ``text`` again, character for character;
    the only thing a cut here can cost is the whitespace-driven line break that
    the browser would have chosen for us, never a character.
    """
    if _part_fits(text, width_mm, max_lines, suffix):
        return [text]
    pieces: list[str] = []
    rest = text
    while rest and not _part_fits(rest, width_mm, max_lines, suffix):
        take = _fitting_take("", rest, width_mm, max_lines, suffix)
        pieces.append(rest[:take])
        rest = rest[take:]
    if rest:
        pieces.append(rest)
    return pieces


def _split_row(row: dict, columns: list[tuple[str, str]], widths: dict,
               max_lines: int) -> list[dict]:
    """Break one over-tall row into consecutive parts, each ≤ ``max_lines`` tall.

    Every cell is cut independently against its own column width, and part *n*
    of the row carries piece *n* of every cell. A short cell has exactly one
    piece, so it prints on the first part and is **blank** on the continuations.

    That is a deliberate decision, not an accident of the algorithm: repeating
    the item number, quantity and prices on a continuation would read as a
    second, separate line item and would invite double counting by anyone
    totalling the sheet by hand. The continuation instead carries only the rest
    of the prose, is tinted like the row it came from, and is marked with a
    "(cont.)" cue in its first column (see ``exp-row-cont`` in document.html) so
    a reader can see at a glance that it belongs to the item above.
    """
    label = str(row.get("_flag_label") or "").strip()
    pieces_by_key: dict[str, list[str]] = {}
    part_count = 1
    for _title, key in columns:
        width_mm = _TABLE_WIDTH_MM * widths.get(key, 0.08)
        # The flag badge is glued to desc_ftco by the template, exactly as
        # _row_needed_height accounts for it.
        suffix = f" [{label}]" if (key == "desc_ftco" and label) else ""
        pieces = _split_cell_text(
            str(row.get(key, "") or ""), width_mm, max_lines, suffix)
        pieces_by_key[key] = pieces
        part_count = max(part_count, len(pieces))

    # The badge trails the *finished* description — the last part that actually
    # carries desc_ftco text, which is where a reader of an unsplit row sees it.
    # Leaving it on the first part would strand an "[ISSUE]" mid-sentence at a
    # page break, and repeating it on every part would look like several
    # flagged items.
    #
    # It has to be the last *non-empty* desc_ftco piece, and it has to be
    # derived from that column alone. ``part_count`` is the maximum over every
    # column, so a row whose desc_client (TO) or reason / comment (the extra
    # sheets) splits into more parts than desc_ftco would otherwise put the
    # badge on a part number that column never reaches. And a piece can be pure
    # whitespace — a trailing run cut loose at a page break — on which the badge
    # would sit alone in an otherwise blank cell.
    ftco_pieces = pieces_by_key.get("desc_ftco")
    if ftco_pieces is None:
        # No desc_ftco column on this sheet, so no badge is rendered anywhere
        # (see _build_page_rows, which only emits flag_label for that key).
        # Blanking _flag_label on some parts would be picking a winner in a race
        # that is not being run.
        badge_idx = None
    else:
        filled = [i for i, piece in enumerate(ftco_pieces) if piece.strip()]
        badge_idx = filled[-1] if filled else 0

    parts: list[dict] = []
    for idx in range(part_count):
        part = dict(row)  # keeps _issue / _unsuppliable / _service_comment
        for key, pieces in pieces_by_key.items():
            part[key] = pieces[idx] if idx < len(pieces) else ""
        if badge_idx is not None and idx != badge_idx:
            part["_flag_label"] = ""
        if idx:
            part["_continued"] = "1"
        parts.append(part)
    return parts


def _available_body_mm(*, last_page: bool, show_totals: bool, extra_mm: float = 0.0) -> float:
    used = (
        _HEADER_H + _INFO_H + _SIGN_H + _FOOTER_H + _TABLE_PAD + _THEAD_H
        + _SAFETY_MM + _PAGE_INSET_V_MM
    )
    if last_page and show_totals:
        used += _TOTALS_H
    # ``extra_mm`` is chrome that only some sheets carry (today: the banner on
    # the Technical Problems / Services sheets). Callers that do not pass it get
    # the identical budget they always had.
    used += extra_mm
    return max(18.0, _PAGE_H - used)


def _build_page_rows(
    chunk: list[dict],
    columns: list[tuple[str, str]],
    heights: list[float],
) -> list[dict]:
    cell_rows = []
    for row, height_mm in zip(chunk, heights):
        # Set on the 2nd..nth part of a row that _split_row had to break over
        # several sheets. Blank (falsy) on every ordinary row, so the markup of
        # a document that needs no splitting is byte for byte what it was.
        continued = str(row.get("_continued", "") or "") == "1"
        cells = []
        for pos, (_title, key) in enumerate(columns):
            text = str(row.get(key, "") or "")
            cells.append({
                "text": text,
                # The "(cont.)" cue rides in the row's first column, which on a
                # continuation is blank anyway (identifiers print once, on the
                # first part).
                "cont_mark": continued and pos == 0,
                # "reason" / "comment" belong to the Technical Problems and
                # Services sheets; they are free prose and have always been
                # left-aligned there. Neither key exists in the main item
                # table's columns, so listing them here cannot affect it.
                "left": key in {"desc_client", "desc_ftco", "remark", "reason", "comment"},
                "key": key,
                "flag_label": (str(row.get("_flag_label") or "") if key == "desc_ftco" else ""),
                "flag_kind": (
                    "issue" if key == "desc_ftco" and str(row.get("_issue", "") or "") == "1"
                    else "unsup" if key == "desc_ftco" and str(row.get("_unsuppliable", "") or "") == "1"
                    else "service" if key == "desc_ftco" and bool(str(row.get("_service_comment", "") or "").strip())
                         and str(row.get("_service_comment", "") or "").strip().lower() not in ("nan", "none", "<na>", "null")
                    else ""
                ),
            })
        cell_rows.append({
            "cells": cells,
            "height_mm": round(height_mm, 2),
            "continued": continued,
            "issue": str(row.get("_issue", "") or "") == "1",
            "unsuppliable": str(row.get("_unsuppliable", "") or "") == "1",
            "service": bool(str(row.get("_service_comment", "") or "").strip())
                       and str(row.get("_service_comment", "") or "").strip().lower()
                       not in ("nan", "none", "<na>", "null"),
        })
    return cell_rows


def paginate_rows(rows: list[dict], columns: list[tuple[str, str]], *, is_pi: bool,
                  widths: dict | None = None, extra_mm: float = 0.0):
    """Pack item rows into pages by each row's own content height.

    Algorithm:
    1. Estimate a *natural* height for every row from its wrapped cell text
       (includes comfortable top/bottom cell padding so text is not flush
       against the row borders).
    2. Greedy-pack consecutive rows while
       ``sum(natural heights) ≤ available table-body height``.
    3. A tall row only expands itself while packing — short neighbours keep
       their own natural height.
    4. **Full pages** (more item rows continue on the next page): leftover
       space inside the table frame is distributed evenly across the rows on
       that page so the table card has no empty band at the bottom.
    5. **Partial last page**: rows keep natural heights; empty space under the
       last row is fine.
    6. A single row whose own text is taller than one sheet is **split**: its
       cells are cut into page-sized pieces (``_split_row``) and each piece gets
       its own page, so no text is lost and no page grows past 210mm. Every page
       this function returns therefore fits exactly one physical sheet, which is
       what lets the caller's ``len(pages)`` be the true sheet count behind
       "Page N of M".

    ``widths`` / ``extra_mm`` let the two extra sheets (Technical Problems,
    Services) reuse this exact algorithm with their own column widths and their
    banner subtracted from the body budget. Omitted, the behaviour is unchanged.
    """
    widths = widths or (_PI_WIDTHS if is_pi else _TO_WIDTHS)
    if not rows:
        return [{
            "rows": [],
            "fill": False,
            "is_last_items": True,
            "show_totals": is_pi,
        }]

    needed = [_row_needed_height(r, columns, widths) for r in rows]
    pages: list[dict] = []
    i = 0
    n = len(rows)
    while i < n:
        # Always place at least the next row on this page.
        k = 1
        while i + k < n:
            trial = k + 1
            is_last_trial = (i + trial) >= n
            avail = _available_body_mm(
                last_page=is_last_trial,
                show_totals=bool(is_pi and is_last_trial),
                extra_mm=extra_mm,
            )
            trial_sum = sum(needed[i:i + trial])
            if trial_sum <= avail + 0.05:
                k = trial
            else:
                break

        chunk = rows[i:i + k]
        chunk_h = list(needed[i:i + k])
        is_last = (i + k) >= n
        avail = _available_body_mm(
            last_page=is_last,
            show_totals=bool(is_pi and is_last),
            extra_mm=extra_mm,
        )
        natural_sum = sum(chunk_h)

        # Packing never puts two rows on a page unless they fit together, so the
        # only way to exceed the body here is a lone row (k == 1) that is taller
        # than one sheet on its own. Such a row is cut into page-sized parts
        # instead of being clipped (text lost) or allowed to spill onto a second
        # physical sheet (page count lied). Everything below this branch is the
        # untouched path every fitting document takes.
        if k == 1 and natural_sum > avail + 0.05:
            # How many wrapped lines a part may use. ``avail`` is already the
            # budget for this row's page — the smaller totals-bearing budget
            # when this is the last row of a PI — so sizing parts by it means
            # every part fits wherever it lands. Because the row needs more
            # lines than this, the split always yields at least two parts and
            # the loop below always advances. Counted with the stylesheet's real
            # line height rather than the paginator's rounded one: a part is
            # packed to the very top of the budget, so the 0.025mm per line the
            # rounding gives away is a clipped line of text by the thirtieth.
            max_lines = _split_max_lines(avail)
            parts = _split_row(rows[i], columns, widths, max_lines)
            for p_idx, part in enumerate(parts):
                part_last = is_last and p_idx == len(parts) - 1
                part_avail = _available_body_mm(
                    last_page=part_last,
                    show_totals=bool(is_pi and part_last),
                    extra_mm=extra_mm,
                )
                part_h = _row_needed_height(part, columns, widths)
                # Same rule as any other page: while item rows still follow, the
                # row is grown so the table card has no empty band at the
                # bottom; the final part keeps its natural height.
                part_fill = not part_last
                if part_fill and part_h < part_avail - 0.05:
                    part_h = part_avail
                pages.append({
                    "rows": _build_page_rows([part], columns, [part_h]),
                    "fill": part_fill,
                    "is_last_items": part_last,
                    "show_totals": bool(is_pi and part_last),
                })
            i += 1
            continue

        # Full continuation pages: grow rows evenly into leftover body space
        # so the table frame is filled (no empty strip under the last row).
        # Partial last page keeps natural heights.
        fill = (not is_last) and len(chunk_h) > 0
        if fill and natural_sum < avail - 0.05:
            extra = (avail - natural_sum) / len(chunk_h)
            chunk_h = [h + extra for h in chunk_h]
        # This spot has held two failed answers to the same over-tall row. The
        # first clamped it back to the body height, which only meant the cell's
        # own ``overflow: hidden`` swallowed the tail of a long material or
        # standard specification — gone from a signed client document while
        # Excel still showed it whole. The second let its page grow past the
        # sheet, which kept the text but printed one logical page as two
        # physical ones, so "Page 2 of 4" could appear on a five-sheet PDF and
        # the signature strip landed on the spilled sheet. Both are gone: such a
        # row is split into page-sized parts in the branch above, and no page
        # produced here can exceed one sheet.

        pages.append({
            "rows": _build_page_rows(chunk, columns, chunk_h),
            "fill": fill,
            "is_last_items": is_last,
            "show_totals": bool(is_pi and is_last),
        })
        i += k
    return pages


def _chrome_path() -> str | None:
    """Locate a Chromium-based browser on the host or inside Docker."""
    env = (os.environ.get("CHROME_PATH") or os.environ.get("CHROMIUM_PATH") or "").strip()
    candidates: list[str] = []
    if env:
        candidates.append(env)

    # Linux / Docker (debian chromium package)
    candidates.extend([
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
    ])

    # Windows common install locations
    local_app = os.environ.get("LOCALAPPDATA") or ""
    prog = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    prog86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    candidates.extend([
        str(Path(prog) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(prog86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        str(Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe") if local_app else "",
        str(Path(prog) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        str(Path(prog86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ])

    # PATH lookup
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "chrome", "msedge", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # Windows registry (App Paths)
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                ):
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            value, _ = winreg.QueryValueEx(key, None)
                            if value:
                                candidates.append(str(value))
                    except OSError:
                        continue
        except Exception:
            pass

    seen: set[str] = set()
    for path in candidates:
        path = (path or "").strip().strip('"')
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            if Path(path).is_file() and os.access(path, os.X_OK if os.name != "nt" else os.F_OK):
                return path
        except OSError:
            continue
    return None


# A4 landscape in inches (Chromium Page.printToPDF uses inches).
_A4_LANDSCAPE_W_IN = 297.0 / 25.4
_A4_LANDSCAPE_H_IN = 210.0 / 25.4


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get_json(url: str, timeout: float = 15.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ws_connect(ws_url: str, timeout: float = 20.0):
    """Minimal WebSocket client (text frames only) for Chrome DevTools Protocol."""
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise RuntimeError("CDP WebSocket handshake failed (connection closed)")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    if "101" not in status_line:
        sock.close()
        raise RuntimeError(f"CDP WebSocket handshake failed: {status_line}")
    return sock


def _ws_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray()
    n = len(payload)
    header.append(0x81)  # FIN + text
    mask_bit = 0x80
    if n < 126:
        header.append(mask_bit | n)
    elif n < (1 << 16):
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _ws_recv_text(sock: socket.socket) -> str:
    def read_exact(n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("CDP WebSocket closed while reading")
            out += chunk
        return out

    while True:
        b1, b2 = read_exact(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", read_exact(8))[0]
        mask = read_exact(4) if masked else b""
        payload = read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            raise RuntimeError("CDP WebSocket closed by peer")
        if opcode == 0x9:  # ping → pong
            # echo pong
            hdr = bytearray([0x8A, 0x80 | len(payload)])
            m = os.urandom(4)
            hdr.extend(m)
            sock.sendall(bytes(hdr) + bytes(b ^ m[i % 4] for i, b in enumerate(payload)))
            continue
        if opcode in (0x1, 0x2):  # text / binary
            return payload.decode("utf-8")
        # ignore continuation/other


def _cdp_call(sock: socket.socket, state: dict, method: str, params: dict | None = None,
              *, timeout: float = 60.0):
    msg_id = int(state.get("id") or 1)
    state["id"] = msg_id + 1
    payload = {"id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    _ws_send_text(sock, json.dumps(payload))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock.settimeout(max(0.5, deadline - time.monotonic()))
        try:
            raw = _ws_recv_text(sock)
        except socket.timeout:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(f"CDP {method} error: {data['error']}")
            return data.get("result") or {}
    raise TimeoutError(f"CDP {method} timed out")


def _html_to_pdf_cdp(chrome: str, html_uri: str) -> bytes:
    """Print HTML to exact A4 landscape PDF via Chrome DevTools Protocol."""
    port = _free_port()
    user_data = tempfile.mkdtemp(prefix="ft_chrome_")
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data}",
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sock = None
    try:
        version = None
        for _ in range(50):
            try:
                version = _http_get_json(f"http://127.0.0.1:{port}/json/version")
                break
            except Exception:
                time.sleep(0.1)
        if not version:
            raise RuntimeError("Chromium DevTools endpoint did not start")

        # Open a dedicated page target for our file:// document.
        try:
            target = _http_get_json(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(html_uri, safe='')}"
            )
        except Exception:
            # Older Chromium: PUT /json/new
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(html_uri, safe='')}",
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                target = json.loads(resp.read().decode("utf-8"))

        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("No CDP webSocketDebuggerUrl for print target")

        sock = _ws_connect(ws_url)
        state = {"id": 1}
        _cdp_call(sock, state, "Page.enable")
        # Wait until the document is fully loaded (file:// is usually instant).
        for _ in range(40):
            result = _cdp_call(sock, state, "Runtime.evaluate", {
                "expression": "document.readyState",
                "returnByValue": True,
            })
            ready = ((result.get("result") or {}).get("value")) or ""
            if ready == "complete":
                break
            time.sleep(0.05)
        # Give fonts/layout a brief settle.
        time.sleep(0.15)

        result = _cdp_call(sock, state, "Page.printToPDF", {
            "landscape": True,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "paperWidth": _A4_LANDSCAPE_W_IN,
            "paperHeight": _A4_LANDSCAPE_H_IN,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
        }, timeout=120.0)
        data_b64 = result.get("data") or ""
        if not data_b64:
            raise RuntimeError("Page.printToPDF returned empty data")
        pdf = base64.b64decode(data_b64)
        if len(pdf) < 100:
            raise RuntimeError("Page.printToPDF returned truncated PDF")
        return pdf
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(user_data, ignore_errors=True)


def html_to_pdf(html: str) -> bytes:
    """Convert print HTML to A4-landscape PDF bytes via headless Chromium.

    Prefer Chrome DevTools ``Page.printToPDF`` so paper size is exactly A4
    landscape with zero margins and no browser header/footer. Fall back to the
    ``--print-to-pdf`` CLI flag when CDP is unavailable.
    """
    chrome = _chrome_path()
    if not chrome:
        raise RuntimeError(
            "Chrome/Edge/Chromium was not found. "
            "Inside Docker the image must include the chromium package; "
            "on Windows install Google Chrome or set CHROME_PATH."
        )

    with tempfile.TemporaryDirectory(prefix="ft_pdf_") as tmp:
        html_path = Path(tmp) / "document.html"
        pdf_path = Path(tmp) / "document.pdf"
        html_path.write_text(html, encoding="utf-8")
        uri = html_path.resolve().as_uri()

        try:
            return _html_to_pdf_cdp(chrome, uri)
        except Exception as cdp_err:
            # Fallback: CLI print (may use Letter on some builds).
            cmd = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-animations",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=8000",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path}",
                uri,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, check=False,
            )
            if not pdf_path.is_file() or pdf_path.stat().st_size < 100:
                err = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(
                    f"PDF generation failed (browser={chrome}; "
                    f"cdp={cdp_err}; cli={err or 'empty output'})"
                ) from cdp_err
            return pdf_path.read_bytes()


def build_document_context(case, form, terms: dict | None = None, *, pdf_lite: bool = False) -> dict:
    from .constants import Side
    from .constants import PriceType
    from .export_data import (
        form_currency, currency_export_suffix, technical_problem_export_rows,
        service_price_export_rows,
    )

    kind = (form.kind or "").upper()
    is_pi = kind == FormKind.PI
    columns = export_columns(form)
    rows = build_export_rows(case, form)
    pages = paginate_rows(rows, columns, is_pi=is_pi)
    issue_rows = technical_problem_export_rows(form) if kind == FormKind.TO else []
    service_rows = service_price_export_rows(form, case) if is_pi else []
    # The two extra sheets are paginated with the very same packer as the main
    # item table. They used to be dropped into a single page each, which meant
    # every row past the bottom of that one sheet was silently clipped by the
    # fixed-height ``.document`` box — the Excel export of the same form wrote
    # them all, so the two artefacts of one document disagreed. The content is
    # unchanged; only where a page break falls is new.
    issues_pages = paginate_rows(
        issue_rows, _ISSUE_COLUMNS, is_pi=False,
        widths=_ISSUE_WIDTHS, extra_mm=_BANNER_H,
    ) if issue_rows else []
    services_pages = paginate_rows(
        service_rows, _SERVICE_COLUMNS, is_pi=False,
        widths=_SERVICE_WIDTHS, extra_mm=_BANNER_H,
    ) if service_rows else []
    # Counted *after* pagination, so a row that had to be split across sheets is
    # already reflected in ``len(pages)``. Every page the paginator returns is
    # exactly one physical sheet, so this total is the sheet count of the
    # printed PDF and "Page N of M" cannot disagree with it.
    total_pages = len(pages) + len(issues_pages) + len(services_pages) + 1  # + issues? + services? + terms

    for idx, page in enumerate(pages, start=1):
        page["page_no"] = idx
        page["page_label"] = f"{idx} of {total_pages}"

    next_no = len(pages) + 1
    for page in issues_pages:
        page["page_no"] = next_no
        page["page_label"] = f"{next_no} of {total_pages}"
        next_no += 1

    for page in services_pages:
        page["page_no"] = next_no
        page["page_label"] = f"{next_no} of {total_pages}"
        next_no += 1

    terms_page = {
        "page_no": next_no,
        "page_label": f"{next_no} of {total_pages}",
        "terms": normalize_terms(terms, kind=kind),
    }

    side = getattr(form, "side", "") or ""
    is_external = (
        side == Side.EXTERNAL
        or (not side and getattr(case, "price_type", "") == PriceType.EXTERNAL)
    )
    totals = None
    if is_pi:
        cur = form_currency(form, case)
        totals = pi_totals(rows, currency=currency_export_suffix(cur, external=is_external))

    return {
        "case": case,
        "form": form,
        "kind": kind,
        "is_pi": is_pi,
        "title": "PROFORMA INVOICE" if is_pi else "TECHNICAL OFFER",
        "kicker": "Engineering Commercial Document" if is_pi else "Engineering Technical Document",
        "footer_center": "PROFORMA INVOICE" if is_pi else "TECHNICAL OFFER",
        "vendor": VENDOR_NAME,
        "doc_no": doc_no_export(case, form),
        "client": client_name_only(case, form),
        "order_no": order_no(case, form) or "—",
        "code_no": code_no_for(form),
        "form_date": form_date_jalali(form),
        "columns": [
            {"title": t, "key": k, "width_pct": round((_PI_WIDTHS if is_pi else _TO_WIDTHS).get(k, 0.08) * 100, 2)}
            for t, k in columns
        ],
        "pages": pages,
        "issues_pages": issues_pages,
        "services_pages": services_pages,
        "terms_page": terms_page,
        "totals": totals,
        "currency_suffix": (totals or {}).get("currency_suffix", " IRR"),
        "vendor_name": vendor_last_name(form),
        # The seat the signer held when the document was frozen. Blank on
        # documents frozen before this was captured, which renders exactly
        # as they always have — the name alone.
        "vendor_title": vendor_title(form),
        "vendor_signature": vendor_signature_data_uri(form),
        "vendor_stamp": vendor_stamp_data_uri(form),
        "pdf_lite": pdf_lite,
    }


def render_document_html(case, form, terms: dict | None = None, *, print_toolbar: bool = False,
                         pdf_lite: bool = False) -> str:
    ctx = build_document_context(case, form, terms=terms, pdf_lite=pdf_lite)
    if print_toolbar:
        ctx["print_toolbar"] = True
        ctx["export_filename"] = export_name_for(case, form)
    return render_to_string("cases/export/document.html", ctx)


def render_print_view_html(case, form, terms: dict | None = None) -> tuple[str, str]:
    name = export_name_for(case, form)
    html = render_document_html(case, form, terms=terms, print_toolbar=True, pdf_lite=False)
    return html, name + ".html"


def render_form_pdf(case, form, terms: dict | None = None) -> tuple[bytes, str]:
    html = render_document_html(case, form, terms=terms, pdf_lite=True)
    pdf = html_to_pdf(html)
    return pdf, export_name_for(case, form) + ".pdf"
