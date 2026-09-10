"""First-paint HTML for one grid cell — the server's half of the table render.

``prepare_table_cell`` is the only entry point, and a single caller reaches it:
``views.dataframe_to_html_with_ids``, once per cell as it walks the DataFrame.
That renderer is shared by both ways into the grid — ``views.upload_excel`` for a
standalone upload, and bridge.py for a case-seeded or saved case form — so a
decision made here lands identically on both. Every transformation the browser
would otherwise have to do after paint (wrapping a value in its textarea, cutting
Alarm_Features into chips, highlighting the parentheses in a size) is done here
instead, so the first painted row already looks like the settled one and the
virtual scroller never has to reflow it.

Escaping is decided per column and the reasoning is written at each branch, but
the rule behind all of them is the same: a cell that holds DATA (a customer's
description, a typed size) leaves here escaped, because the templates render the
grid with ``|safe`` and the scroller re-parses it into live nodes; a cell that
holds markup THIS SERVER built (Filled_Features, a coloured FTCO string) is
passed through, because it was already neutralised at its source in
final_feature_display.colored_display. The JS reads cells with ``textContent``,
which decodes the entities back, so saved values are byte-identical either way.

Despite the file name this module does not change any value — it only decides
how a value is displayed the first time.
"""

import re
from html import escape


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
        # The size text is DATA (typed in the grid or read from the client's
        # workbook); only the parenthesis highlight is markup we add. Escape
        # first and wrap second — the other order would escape our own <span>
        # into visible text. Every JS reader of this cell uses textContent,
        # which decodes the entities back, so nothing downstream sees a change.
        return highlight_parentheses(escape(val_s))

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

    if col_s == "Filled_Features":
        # This diagnostic column is markup the server itself built
        # (final_arrange_builder joins coloured <span>s with <br>), and the tool
        # saves the cell back with innerHTML — escaping it here would store the
        # escaped form and escape it again on every reload. Its *values* are
        # neutralised at the source, in final_feature_display.colored_display.
        return val_s

    # Everything else — CLIENT DISCRIPTION, qty, unit, the FTCO code and any
    # extra/calculation column — is plain data that ends up inside a <td> which
    # the templates render with |safe and the virtual scroller re-parses into
    # live nodes. A description arrives from a customer-supplied workbook, so it
    # has to leave here already escaped or an <img onerror=…> in it becomes a
    # real element. The JS reads these cells with textContent, which decodes the
    # entities, so the saved values and the coder's input are unchanged.
    return escape(val_s)
