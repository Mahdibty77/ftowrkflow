import json
import re
from html import escape


def colored_display(value, color=None):
    color = color or "black"
    return f"<span style='color:{color}'>{value}</span>"


def join_filled_features(feature_items):
    """
    Render Filled_Features exactly the same way for initial page load and AJAX updates.
    Keeping this in backend prevents the first JavaScript update from replacing a
    <br>-based cell with a comma-based cell, which was causing row-height jumps.
    """
    return "<br>".join(feature_items)


def _extract_order(key):
    try:
        return int(str(key).split("_")[-1])
    except Exception:
        return 999


def _clean(v):
    return v is not None and str(v).strip() and str(v).strip().lower() != "null"


# Final arranged text is now built from final_arrange_builder.py so the
# arrangement can be customized from final_arrange.json without keeping
# hard-coded material/grade or separator rules in this initial-render module.
from .final_arrange_builder import build_final_arrange_and_features

def highlight_parentheses(text):
    return re.sub(r"\([^)]+\)", r'<span class="highlight-red">\g<0></span>', str(text or ""))


def _plain_ftco_for_cell(text: str) -> str:
    """Strip colour / escaped markup from an FTCO cell for safe display."""
    import html as _html
    s = str(text or "")
    if not s:
        return ""
    low = s.lower()
    if "&lt;" in low and any(t in low for t in ("span", "bdi", "br")):
        s = _html.unescape(s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _looks_like_escaped_ftco_html(text: str) -> bool:
    """True when a cell stores colour markup as escaped text (visible tags)."""
    low = str(text or "").lower()
    if "&lt;" not in low:
        return False
    return any(t in low for t in ("span", "bdi", "br /", "br/>", "br>"))


def prepare_table_cell(col, val, row=None, data_json=None):
    """Initial HTML transformations that should happen once in backend."""
    col_s = str(col)
    val_s = ""
    if val is not None:
        try:
            import math
            if isinstance(val, float) and math.isnan(val):
                val = ""
        except Exception:
            pass
        try:
            import pandas as pd
            if pd.isna(val):
                val = ""
        except Exception:
            pass
        s = str(val).strip()
        val_s = "" if s.lower() in ("nan", "none", "<na>") else s

    if col_s == "اصلاحیه" or col_s == "ریمارک":
        # The textarea is fully prepared by backend on first render.
        # Fixed rows/class avoid any JavaScript sizing pass after paint.
        return f'<textarea class="remark-revision-textarea" rows="1">{escape(val_s)}</textarea>'

    if col_s == "Alarm_Features":
        # Render each missing-feature token as its own chip so the cell wraps
        # tidily from the very first paint. The server-side coder joins tokens
        # with <br>, so split on <br> as well as whitespace/commas.
        tokens = [t for t in re.split(r"(?:<br\s*/?>)|[\s,]+", val_s.strip(), flags=re.I) if t]
        return " ".join(f'<span class="alarm-chip">{escape(t)}</span>' for t in tokens)

    if col_s in ("BRAND", "TIME"):
        # Always-on writable text area — supports wrap for longer values.
        return f'<textarea class="cell-input pi-text-area" rows="1">{escape(val_s)}</textarea>'

    if col_s == "Group":
        # Plain text only — no Group dropdown. Empty stays empty; alarms carry
        # the "group" token until Revision/description identifies it.
        return escape(val_s)

    if col_s == "Type":
        # Plain text only — no Type dropdown (same reason as Group).
        return escape(val_s)

    # Size/display column: keep initial red parentheses without JS doing it after paint.
    if col_s == "size":
        return highlight_parentheses(val_s)

    if col_s == "Final Arranged Text":
        # Manual FTCO edits must never paint colour markup (or escaped markup
        # that would show as literal <span>…</span> text) into the cell.
        user_edited = False
        if isinstance(row, dict):
            user_edited = str(row.get("_ftco_user_edited", "") or "") == "1"
        if user_edited or _looks_like_escaped_ftco_html(val_s):
            return escape(_plain_ftco_for_cell(val_s))
        if val_s and ("<span" in val_s.lower() or "<bdi" in val_s.lower()):
            return val_s
        return escape(val_s) if val_s else val_s

    return val_s
