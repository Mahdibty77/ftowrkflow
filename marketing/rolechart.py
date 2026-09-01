"""The project role chart: its slots, its geometry, and nothing else.

WHAT THIS DRAWS. One project, and every outside party that stands between the
money and the metal, in a single top-to-bottom flow: sponsor paired with its
licensor, the owner on its own row beneath them (no longer paired with the
licensor — see the module's own git history), the project paired with its
phase, project management, the design chain, the four contract types (drawn
unconditionally now — there is no contract-model gating any more; see the
module's own git history if that phrase means nothing to you), a
subcontractor card under each of the three contract types that can actually
hold one, third-party inspection paired with the laboratory in one merged
row beneath those three, our own position, and finally supplier — directly
beneath our own position — and rival, detached alone in the chart's
bottom-left corner. Twenty-one fields in total — ``SLOTS`` plus "project" and
"us" — and every one of them appears EXACTLY ONCE, unconditionally, as its own
clickable card.

    SLOTS       -- the nineteen, in reading order
    ALL_FIELDS  -- SLOTS plus the project and us, in chart order
    build(counts) -- counts -> a drawable chart

WHERE THE MODEL PLUGS IN. ``build()`` takes just ``counts`` — a
``{field key: how many companies}`` mapping built in
``marketing/views.py::home`` (from ``services.label_counts`` for the sixteen
labelable fields and ``services.us_connections`` for "us"), already scoped to
the viewer — and returns a finished geometry dict the template walks. It
touches no model, no request and no database itself. The organisation-name,
contract-model and per-buying-chain concepts that used to live here (one
sample organisation per slot, four duplicated sub/inspector chains, a dashed
"not identified yet" edge style) are gone: what a field HOLDS is now either a
label on the shared ``cases.Client`` directory (sixteen of the twenty-one
fields — see ``marketing/models.py::ClientLabel`` and
``marketing/services.py``) or, for "us", every client connected through an
actual case; this module only draws the card a field sits on and how many
companies a viewer can see under it.

GEOMETRY. A 1300-unit-wide viewBox, four columns plus a centre lane, rows 105
apart (up from the original 92, to give an expanded card room to grow — see
``_GAP``'s own comment for the full story). Every row
transition is drawn by ``_connect``: one point to one point is a line, one to
many (or many to one) fans from that single point, and many to many converge
on the centre lane and fan back out — there is no bus bar and no per-chain
special case, with one deliberate exception: in the final row, rival — alone
in the bottom-left corner — gets no incoming edge at all, while supplier, its
row-mate directly beneath "us" in the centre lane, gets an ordinary one — see
``_NO_INCOMING_EDGE`` in ``build()``. The template receives finished
coordinates and path data and makes no arithmetic decision of its own.
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

# Row gap — was 92, raised to 140 (roughly +52%) last round, now brought back
# down to 105 this round. Still comfortably more than the original 92: a
# later phase lets a card grow taller WHILE an Inquiry is active, to show a
# list of connected names (see
# ``marketing/services.py::connections_of_client``'s ``connected`` field)
# inside the card itself, and that phase still needs real, pre-existing
# breathing room between every row so an expanded card has somewhere to grow
# into without the whole chart needing genuine dynamic reflow. But
# noticeably shorter than 140 overall now, both from this smaller gap and
# from _ROWS below carrying two fewer rows than last round: licensor moved
# up to pair with sponsor instead of owner (net zero rows — owner simply
# takes the row licensor vacated), while project/phase and tpi/laboratory
# each folded from two separate rows into one shared row apiece, for two
# rows removed overall. Every row Y below (R1..R10) and VIEW_H are formulas
# that chain off this one constant, so changing it alone is enough —
# nothing past this point needed to change by hand for the new spacing to
# take effect.
_GAP = 105
R1 = 18            # sponsor / licensor
R2 = R1 + _GAP     # owner
R3 = R2 + _GAP     # project / phase
R4 = R3 + _GAP     # pmt / mc
R5 = R4 + _GAP     # design / supervision
R6 = R5 + _GAP     # the four contract types
R7 = R6 + _GAP     # sub / sub_pc / sub_epc
R8 = R7 + _GAP     # tpi / laboratory
R9 = R8 + _GAP     # us
R10 = R9 + _GAP    # rival / supplier
VIEW_H = R10 + NODE_H + 26

Y_ROLE = 26        # the Persian role name's baseline, from the box's top edge
Y_ABBR = 45        # the English abbreviation's baseline


# --------------------------------------------------------------------------- #
# The nineteen slots
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
    ("sub",         "پیمانکار جزء P",              "SUBCONTRACTOR — P"),
    ("sub_pc",      "پیمانکار جزء PC",             "SUBCONTRACTOR — PC"),
    ("sub_epc",     "پیمانکار جزء EPC",            "SUBCONTRACTOR — EPC"),
    ("tpi",         "بازرس ثالث",                  "TPI — THIRD PARTY INSP."),
    ("laboratory",  "آزمایشگاه",                   "LABORATORY"),
    ("supplier",    "تأمین‌کننده",                 "SUB-SUPPLIER"),
    ("rival",       "رقیب احتمالی",                "COMPETITOR"),
)
SLOT_COUNT = len(SLOTS)
_SLOT_BY_KEY = {s[0]: s for s in SLOTS}

# The two boxes that are not slots — never empty, because the project is the
# subject of the chart and we are always on it. Neither carries a label
# directory of its own: "project" is one of the four inert fields (see
# _kind_of below), and "us" is the one field backed by case data instead of
# a label at all.
PROJECT_ROLE = "نام پروژه"
PROJECT_ABBR = "PROJECT"
US_ROLE = "فولاد تبار"
US_ABBR = "VENDOR / SUPPLIER"

# Every field on the chart, in reading order — the one list marketing/views.py
# and marketing/services.py both walk (via ALL_FIELDS/LABEL_KEYS below) so the
# chart's own card order, the Companies tab's tab strip, and the fourteen
# MarketingLabel choices on a case can never drift apart on what a field is
# called.
_FIELD_ORDER = (
    "sponsor", "owner", "project", "phase", "pmt", "mc", "licensor", "design",
    "supervision", "c", "p", "pc", "epc", "sub", "sub_pc", "sub_epc", "tpi",
    "laboratory", "us", "supplier", "rival",
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
# chart_interact.js: "label" for the sixteen MarketingLabel keys a card can
# be tagged/untagged under (see marketing/services.py's own LABEL_KEYS, which
# this mirrors exactly), "us" for the single "our own position" card, and
# "inert" for the four fields no data source feeds this round (the same four
# marketing/views.py names as its own ``_INERT_FIELDS``). Kept here as a
# plain tuple rather than an import from either module, so rolechart.py still
# touches no model, no request and no database — see the module docstring.
# Exposed on every node as ``kind``, and from there as a ``data-kind``
# attribute in the template, so the JS never has to hardcode which of the
# twenty-one keys falls in which group.
#
# supplier and rival joined this list (they used to sit in _INERT_KEYS
# below, with no data source and no click interaction at all): a Marketing
# user can now hand-tag a company under either one via
# ``marketing/models.py::ClientLabel`` — see
# ``cases.constants.MarketingLabel.MANUAL_ONLY_CHOICES`` for why they are
# manual-tag-only (no case can ever hold them, unlike the other fourteen).
# Their GEOMETRY (bottom-left corner, no incoming edge, see _ROWS and
# _NO_INCOMING_EDGE below) is unrelated to this and does not change.
LABEL_KEYS = (
    "sponsor", "owner", "pmt", "mc", "licensor", "design", "supervision",
    "c", "p", "pc", "epc", "sub", "sub_pc", "sub_epc", "supplier", "rival",
)
_INERT_KEYS = ("project", "phase", "laboratory", "tpi")


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
# This round re-paired three of these rows to shorten the chart: licensor
# moved up to sit beside sponsor on R1, leaving owner alone on its own row
# (R2) again instead of paired with licensor; project and phase — previously
# two separate rows — now share one row (R3), paired the same way
# sponsor/licensor and pmt/mc already were; and tpi and laboratory —
# likewise previously two separate rows — now share one row (R8) the same
# way. None of this needed a new edge shape in ``_connect``: it already
# handles 2-parent-to-1-child (R1 -> R2), 1-to-2 (R2 -> R3), and 3-to-2 via
# centre-lane convergence (R7 -> R8, three subcontractor cards down to the
# tpi/laboratory pair) — the same generic shapes it already drew elsewhere
# on this chart.
#
# rival sits alone in the chart's OWN bottom-left corner (COL[0], the
# outermost established column — not a new constant): the owner asked for it
# detached, with no edge tying it to anything else on the default chart. See
# ``_NO_INCOMING_EDGE``. supplier shares this same final row but sits in the
# centre lane instead, directly beneath "us" — an ordinary child with an
# ordinary incoming edge, not detached at all; only rival gets the no-edge
# treatment. The bottom-right corner (COL[3], where rival used to sit alone)
# is left deliberately empty here — a later phase grows a panel there in the
# chart's own JS/CSS, so nothing else should be placed at COL[3] on this row.
_ROWS = (
    (R1,  (("sponsor", PAIR[0]), ("licensor", PAIR[1]))),
    (R2,  (("owner", CEN),)),
    (R3,  (("project", PAIR[0]), ("phase", PAIR[1]))),
    (R4,  (("pmt", PAIR[0]), ("mc", PAIR[1]))),
    (R5,  (("design", COL[1]), ("supervision", COL[2]))),
    (R6,  (("c", COL[0]), ("p", COL[1]), ("pc", COL[2]), ("epc", COL[3]))),
    (R7,  (("sub", COL[1]), ("sub_pc", COL[2]), ("sub_epc", COL[3]))),
    (R8,  (("tpi", PAIR[0]), ("laboratory", PAIR[1]))),
    (R9,  (("us", CEN),)),
    (R10, (("rival", COL[0]), ("supplier", CEN))),
)

# The one field build() deliberately draws no incoming edge into: rival,
# alone in its bottom-left corner. Its row-mate supplier gets an ordinary
# edge like everything else — see build()'s per-child filtering below.
_NO_INCOMING_EDGE = frozenset({"rival"})


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


def _node(key, role_fa, abbr, cx, y, count, row_index):
    """One clickable card. ``count`` is how many entities the viewer can see
    in this field — the only thing, besides its two labels, a node shows.
    ``row_index`` is the 0-based position of this node's row within
    ``_ROWS`` — every card sharing a row gets the same value. Plumbed
    through only so a later phase can colour-code cards by which row they
    sit on; nothing in this module reads it."""
    has_entries = count > 0
    x = cx - NODE_W // 2
    return {
        "key": key,
        "role_fa": role_fa,
        "abbr": abbr,
        "count": count,
        "kind": _kind_of(key),
        "row_index": row_index,
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
                (the sixteen labelable fields) and ``services.us_connections``
                ("us"), already scoped to the viewer. A field missing from it
                counts as zero.
    """
    counts = counts or {}
    nodes = []
    edges = []
    for i, (y, row) in enumerate(_ROWS):
        for key, x in row:
            fa, ab = _LABEL_BY_KEY[key]
            nodes.append(_node(key, fa, ab, x, y, counts.get(key, 0), i))
        if i + 1 < len(_ROWS):
            y_next, row_next = _ROWS[i + 1]
            # Filtered per CHILD, not per row: a row can mix an excluded
            # field (rival) with an ordinary one (supplier) now that both
            # sit in the same final row — see _NO_INCOMING_EDGE above. This
            # still draws nothing when EVERY child in the next row is
            # excluded (the old all-or-nothing case), so no other row
            # transition's edges change.
            included_next = [(k, x) for k, x in row_next if k not in _NO_INCOMING_EDGE]
            if included_next:
                edges.extend(_connect(
                    [x for _k, x in row], y + NODE_H,
                    [x for _k, x in included_next], y_next,
                ))

    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "nodes": nodes,
        "edges": edges,
    }
