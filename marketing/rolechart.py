"""The project role chart: its slots, its geometry, and nothing else.

WHAT THIS DRAWS. One project, and every outside party that stands between the
money and the metal, in a single top-to-bottom flow: sponsor, owner, the
project itself, its phase, project management, the design chain, the four
contract types (drawn unconditionally now — there is no contract-model
gating any more; see the module's own git history if that phrase means
nothing to you), a subcontractor and a third-party inspector, the
laboratory, our own position, and finally supplier and rival, stacked
together in the chart's bottom-left corner. Nineteen fields in total —
``SLOTS`` plus "project" and "us" — and every one of them appears EXACTLY
ONCE, unconditionally, as its own clickable card.

    SLOTS       -- the seventeen, in reading order
    ALL_FIELDS  -- SLOTS plus the project and us, in chart order
    build(counts) -- counts -> a drawable chart

WHERE THE MODEL PLUGS IN. ``build()`` takes just ``counts`` — a
``{field key: how many companies}`` mapping built in
``marketing/views.py::home`` (from ``services.label_counts`` for the twelve
labelable fields and ``services.us_connections`` for "us"), already scoped to
the viewer — and returns a finished geometry dict the template walks. It
touches no model, no request and no database itself. The organisation-name,
contract-model and per-buying-chain concepts that used to live here (one
sample organisation per slot, four duplicated sub/inspector chains, a dashed
"not identified yet" edge style) are gone: what a field HOLDS is now either a
label on the shared ``cases.Client`` directory (twelve of the nineteen
fields — see ``marketing/models.py::ClientLabel`` and
``marketing/services.py``) or, for "us", every client connected through an
actual case; this module only draws the card a field sits on and how many
companies a viewer can see under it.

GEOMETRY. A 1300-unit-wide viewBox, four columns plus a centre lane, rows 92
apart — the same base grid the previous version of this chart used. Every row
transition is drawn by ``_connect``: one point to one point is a line, one to
many (or many to one) fans from that single point, and many to many converge
on the centre lane and fan back out — there is no bus bar and no per-chain
special case, with one deliberate exception: the final two rows (rival, then
supplier, stacked in the bottom-left corner) get no incoming edge at all —
see ``_NO_INCOMING_EDGE`` in ``build()``. The
template receives finished coordinates and path data and makes no arithmetic
decision of its own.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Grid
# --------------------------------------------------------------------------- #
VIEW_W = 1300
NODE_W = 224
NODE_H = 62

COL = (200, 500, 800, 1100)     # the four contract columns
CEN = 650                       # the centre lane
PAIR = (COL[1], COL[2])         # the two columns a side-by-side pair sits on

_GAP = 92
_GAP_STACK = 26    # the tight breathing gap between rival and supplier below it
R1 = 18            # sponsor
R2 = R1 + _GAP     # owner
R3 = R2 + _GAP     # the project
R3B = R3 + _GAP    # phase
R4 = R3B + _GAP    # pmt / mc
R5 = R4 + _GAP     # licensor / design / supervision
R6 = R5 + _GAP     # the four contract types
R7 = R6 + _GAP     # sub / tpi
R8 = R7 + _GAP     # laboratory
R9 = R8 + _GAP     # us
R10 = R9 + _GAP           # rival
R11 = R10 + NODE_H + _GAP_STACK   # supplier, close beneath rival
VIEW_H = R11 + NODE_H + 26

Y_ROLE = 26        # the Persian role name's baseline, from the box's top edge
Y_ABBR = 45        # the English abbreviation's baseline


# --------------------------------------------------------------------------- #
# The seventeen slots
# --------------------------------------------------------------------------- #
# (key, Persian role name, English abbreviation). No colour family any more:
# the first version of this chart gave every field a family hue, and once
# every field became its own clickable card too, that reads as exactly what
# the owner called it — "خیلی رنگارنگ" (too colourful). A field's state is
# now shown by its border/text treatment alone (see rolechart.css), not by
# which of eight hues it happens to belong to.
SLOTS = (
    ("sponsor",     "سرمایه‌گذار",                 "SPONSOR / INVESTOR"),
    ("owner",       "کارفرمای اصلی",               "OWNER / CLIENT"),
    ("phase",       "فاز پروژه",                   "PROJECT PHASE"),
    ("pmt",         "مجری طرح",                    "PMT — PROJECT MGMT TEAM"),
    ("mc",          "مدیریت طرح",                  "MC / PMC — MGMT CONTRACTOR"),
    ("licensor",    "لیسانسور",                    "LICENSOR"),
    ("design",      "مشاور طراح",                  "DESIGN CONSULTANT — FEED / DED"),
    ("supervision", "مشاور نظارت",                 "SUPERVISION"),
    ("c",           "پیمانکار اجرا",               "C — CONSTRUCTION ONLY"),
    ("p",           "پیمانکار خرید",               "P — PROCUREMENT ONLY"),
    ("pc",          "پیمانکار خرید و اجرا",        "PC — PROCUREMENT + CONSTRUCTION"),
    ("epc",         "پیمانکار طرح، خرید و اجرا",   "EPC — ENG. PROC. CONSTRUCTION"),
    ("sub",         "پیمانکار جزء",                "SUBCONTRACTOR"),
    ("tpi",         "بازرس ثالث",                  "TPI — THIRD PARTY INSP."),
    ("laboratory",  "آزمایشگاه",                   "LABORATORY"),
    ("supplier",    "تأمین‌کننده",                 "SUB-SUPPLIER"),
    ("rival",       "رقیب احتمالی",                "COMPETITOR"),
)
SLOT_COUNT = len(SLOTS)
_SLOT_BY_KEY = {s[0]: s for s in SLOTS}

# The two boxes that are not slots — never empty, because the project is the
# subject of the chart and we are always on it. Neither carries a label
# directory of its own: "project" is one of the six inert fields (see
# _kind_of below), and "us" is the one field backed by case data instead of
# a label at all.
PROJECT_ROLE = "نام پروژه"
PROJECT_ABBR = "PROJECT"
US_ROLE = "فولاد تبار"
US_ABBR = "VENDOR / SUPPLIER"

# Every field on the chart, in reading order — the one list marketing/views.py
# and marketing/services.py both walk (via ALL_FIELDS/LABEL_KEYS below) so the
# chart's own card order, the Companies tab's tab strip, and the twelve
# MarketingLabel choices on a case can never drift apart on what a field is
# called.
_FIELD_ORDER = (
    "sponsor", "owner", "project", "phase", "pmt", "mc", "licensor", "design",
    "supervision", "c", "p", "pc", "epc", "sub", "tpi", "laboratory", "us",
    "supplier", "rival",
)


def _field_entry(key):
    if key == "project":
        return (key, PROJECT_ROLE, PROJECT_ABBR)
    if key == "us":
        return (key, US_ROLE, US_ABBR)
    _k, fa, ab = _SLOT_BY_KEY[key]
    return (key, fa, ab)


ALL_FIELDS = tuple(_field_entry(k) for k in _FIELD_ORDER)
_LABEL_BY_KEY = {k: (fa, ab) for k, fa, ab in ALL_FIELDS}

# Every field's behavioural GROUP, for the chart's own click handling in
# chart_interact.js: "label" for the twelve MarketingLabel keys a card can be
# tagged/untagged under (see marketing/services.py's own LABEL_KEYS, which
# this mirrors exactly), "us" for the single "our own position" card, and
# "inert" for the six fields no data source feeds this round (the same six
# marketing/views.py names as its own ``_INERT_FIELDS``). Kept here as a
# plain tuple rather than an import from either module, so rolechart.py still
# touches no model, no request and no database — see the module docstring.
# Exposed on every node as ``kind``, and from there as a ``data-kind``
# attribute in the template, so the JS never has to hardcode which of the
# nineteen keys falls in which group.
LABEL_KEYS = (
    "sponsor", "owner", "pmt", "mc", "licensor", "design", "supervision",
    "c", "p", "pc", "epc", "sub",
)
_INERT_KEYS = ("project", "phase", "laboratory", "tpi", "supplier", "rival")


def _kind_of(key):
    if key == "us":
        return "us"
    if key in _INERT_KEYS:
        return "inert"
    return "label"


# The rows of the chart, top to bottom — each a (y, ((field key, x), ...)).
# Reordering or reshaping a row is the only change a future layout tweak
# needs; ``build`` walks this once for the nodes and once more, pairwise,
# for the connectors between one row and the next.
#
# rival and supplier sit stacked in the chart's OWN bottom-left corner
# (COL[0], the outermost established column — not a new constant), rival at
# R10 directly above supplier at R11, _GAP_STACK apart rather than the
# standard _GAP: the owner asked for them detached, with no edge tying them
# to anything else on the default chart. See ``_NO_INCOMING_EDGE``. The
# bottom-right corner (COL[3], where rival used to sit alone) is left
# deliberately empty here — a later phase grows a panel there in the chart's
# own JS/CSS, so nothing else should be placed at COL[3] on this row.
_ROWS = (
    (R1,  (("sponsor", CEN),)),
    (R2,  (("owner", CEN),)),
    (R3,  (("project", CEN),)),
    (R3B, (("phase", CEN),)),
    (R4,  (("pmt", PAIR[0]), ("mc", PAIR[1]))),
    (R5,  (("licensor", COL[0]), ("design", COL[1]), ("supervision", COL[2]))),
    (R6,  (("c", COL[0]), ("p", COL[1]), ("pc", COL[2]), ("epc", COL[3]))),
    (R7,  (("sub", PAIR[0]), ("tpi", PAIR[1]))),
    (R8,  (("laboratory", CEN),)),
    (R9,  (("us", CEN),)),
    (R10, (("rival", COL[0]),)),
    (R11, (("supplier", COL[0]),)),
)

# The one row transition ``build()`` deliberately does NOT draw an edge into.
_NO_INCOMING_EDGE = frozenset({"supplier", "rival"})


# --------------------------------------------------------------------------- #
# Small builders
# --------------------------------------------------------------------------- #
def _badge(count, x, y, w):
    """The small count pill drawn in a node's top-right corner."""
    text = str(count) if count <= 999 else "999+"
    bw = max(20, 9 * len(text) + 14)
    bh = 18
    bx = x + w - bw - 6
    by = y + 6
    return {
        "x": bx, "y": by, "w": bw, "h": bh,
        "cx": bx + bw / 2.0, "text_y": by + bh / 2.0 + 4, "text": text,
    }


def _node(key, role_fa, abbr, cx, y, count):
    """One clickable card. ``count`` is how many entities the viewer can see
    in this field — the only thing, besides its two labels, a node shows."""
    has_entries = count > 0
    x = cx - NODE_W // 2
    return {
        "key": key,
        "role_fa": role_fa,
        "abbr": abbr,
        "count": count,
        "kind": _kind_of(key),
        "x": x,
        "y": y,
        "cx": cx,
        "w": NODE_W,
        "h": NODE_H,
        "y_role": y + Y_ROLE,
        "y_abbr": y + Y_ABBR,
        "cls": "rc-node " + ("has-entries" if has_entries else "is-empty"),
        "title": "%s — %s — %d %s" % (
            role_fa, abbr, count, "entity" if count == 1 else "entities",
        ),
        "badge": _badge(count, x, y, NODE_W),
    }


def _route(x1, y1, x2, y2, r=16):
    """A path from one node's bottom edge to another's top edge.

    Straight when the two columns line up; otherwise an S-shaped elbow with
    rounded corners at its midpoint. This is the "fresh, modern" routing the
    redesign asked for in place of the old right-angled, dashed lines — the
    owner's own words: "نه خط‌های معمولی مثل تورفتگی، یک طرح جدید مدرن" (not
    an ordinary grooved look, a fresh modern one).
    """
    if x1 == x2:
        return "M%g %g L%g %g" % (x1, y1, x2, y2)
    ymid = (y1 + y2) / 2.0
    rr = max(1.0, min(r, abs(ymid - y1), abs(y2 - ymid), abs(x2 - x1) / 2.0))
    sx = 1 if x2 > x1 else -1
    sy = 1 if y2 > y1 else -1
    return "M%g %g L%g %g Q%g %g %g %g L%g %g Q%g %g %g %g L%g %g" % (
        x1, y1,
        x1, ymid - rr * sy,
        x1, ymid, x1 + rr * sx, ymid,
        x2 - rr * sx, ymid,
        x2, ymid, x2, ymid + rr * sy,
        x2, y2,
    )


def _connect(parents, y_parent_bottom, children, y_child_top):
    """Every edge between one row and the next — see the module docstring."""
    if len(parents) == 1 and len(children) == 1:
        ds = [_route(parents[0], y_parent_bottom, children[0], y_child_top)]
    elif len(parents) == 1:
        ds = [_route(parents[0], y_parent_bottom, x, y_child_top) for x in children]
    elif len(children) == 1:
        ds = [_route(x, y_parent_bottom, children[0], y_child_top) for x in parents]
    else:
        waist = (y_parent_bottom + y_child_top) / 2.0
        ds = [_route(x, y_parent_bottom, CEN, waist) for x in parents]
        ds += [_route(CEN, waist, x, y_child_top) for x in children]
    return [{"d": d} for d in ds]


# --------------------------------------------------------------------------- #
# The chart
# --------------------------------------------------------------------------- #
def build(counts):
    """Return everything the template needs to draw the chart.

    ``counts``  ``{field key: how many companies}`` — built in
                ``marketing/views.py::home`` from ``services.label_counts``
                (the twelve labelable fields) and ``services.us_connections``
                ("us"), already scoped to the viewer. A field missing from it
                counts as zero.
    """
    counts = counts or {}
    nodes = []
    edges = []
    for i, (y, row) in enumerate(_ROWS):
        for key, x in row:
            fa, ab = _LABEL_BY_KEY[key]
            nodes.append(_node(key, fa, ab, x, y, counts.get(key, 0)))
        if i + 1 < len(_ROWS):
            y_next, row_next = _ROWS[i + 1]
            next_keys = {k for k, _x in row_next}
            if not next_keys & _NO_INCOMING_EDGE:
                edges.extend(_connect(
                    [x for _k, x in row], y + NODE_H,
                    [x for _k, x in row_next], y_next,
                ))

    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "nodes": nodes,
        "edges": edges,
    }
