"""The project role chart: its slots, its geometry, and nothing else.

WHAT THIS DRAWS. One project, and every outside party that stands between the
money and the metal, in a single top-to-bottom flow: licensor paired with its
sponsor (licensor on the LEFT of that pair, sponsor on the right — swapped
this round; see ``_ROWS``), the owner on its own row beneath them (no longer
paired with the licensor — see the module's own git history), the project
paired with its phase, project management, the design chain, the four
contract types (drawn
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
``marketing/views.py::home`` (from ``services.label_counts`` for the twenty
labelable fields and ``services.us_connections`` for "us"), already scoped to
the viewer — and returns a finished geometry dict the template walks. It
touches no model, no request and no database itself. The organisation-name,
contract-model and per-buying-chain concepts that used to live here (one
sample organisation per slot, four duplicated sub/inspector chains, a dashed
"not identified yet" edge style) are gone: what a field HOLDS is now either a
label on the shared ``cases.Client`` directory (twenty of the twenty-one
fields — see ``marketing/models.py::ClientLabel`` and
``marketing/services.py``) or, for "us", every client connected through an
actual case; this module only draws the card a field sits on and how many
companies a viewer can see under it. Every field except "us" is labelable as
of this round: project, phase, tpi and laboratory — the last four cards that
had no data source at all — became manual-only tags alongside supplier and
rival (see ``LABEL_KEYS`` and ``_INERT_KEYS`` below).

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

from django.utils.translation import gettext_lazy as _

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
R1 = 18            # licensor / sponsor
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
#
# THE ENGLISH ABBREVIATION (the third element of each tuple below) IS WRAPPED
# IN ``gettext_lazy``; THE PERSIAN ROLE NAME (the second element) IS NOT, and
# that split is deliberate, not an oversight. The Persian name is drawn on
# every card UNCONDITIONALLY, in every interface language — including the
# platform's default English chrome — which is a pre-existing quirk of this
# chart the i18n rollout inherited rather than introduced; FIXING that (so an
# English-language viewer sees no Persian on this page at all) is explicitly
# a later "Cleanup" stage's job, done once across the whole app rather than
# piecemeal here. This round's own job is narrower: the English abbreviation
# is genuinely this chart's own fixed vocabulary (fourteen of these nineteen
# keys are literally ``cases.constants.MarketingLabel.CHOICES``, the other
# five ``MANUAL_ONLY_CHOICES`` — see that class's docstring), so it is what
# gets translated for the Persian toggle to have an effect on this page.
# ``gettext_lazy`` (not the eager ``gettext``) is required here because SLOTS
# is built once, at import time, long before any request (and therefore any
# active language) exists — a lazy string re-resolves against whichever
# language is active at the moment it is finally rendered to text (inside
# ``_node``'s ``%s`` formatting, per request), exactly the way
# ``accounts/constants.py::Language.CHOICES`` and every other Django choices=
# list already relies on ``gettext_lazy`` for the same reason.
#
# THE LATER "CLEANUP" STAGE THIS COMMENT REFERS TO IS THIS ONE — its own
# whole-repo sweep for Persian text leaking into chrome looked at this file
# on purpose, not by missing it, and the call is: LEAVE THE PERSIAN ROLE NAME
# IN. It is not the same failure the sweep was built to catch (a chrome
# string — a page heading, a button, a tooltip — that never got an English
# base string at all, the way "My Tasks" once sat beside a redundant Persian
# "(کارهای من)" on my_tasks.html). These nineteen names are this chart's own
# fixed business vocabulary for organisational roles on an EPC-style project
# (سرمایه‌گذار, کارفرمای اصلی, پیمانکار اجرا, ...) — the same kind of
# deliberately-always-shown Persian term as ``marketing.models.ContactGender``
# ("آقای — MALE") and the "Inquiry · استعلام" modal button in
# ``_role_chart.html`` (see that file's own comment beside it), not a
# duplicated translation of the English abbreviation sitting next to it.
# Collapsing it away for an English-language viewer would also be a real
# layout change (``_node``'s ``y_role``/``y_abbr`` both stay populated, on
# purpose) touching every card of a chart with its own SVG geometry and JS
# interaction layer — exactly the kind of business-logic/behaviour change
# this cleanup pass was told not to make chasing a text-leak fix. So: kept,
# on purpose, confirmed here rather than left open.
SLOTS = (
    ("sponsor",     "سرمایه‌گذار",                 _("SPONSOR / INVESTOR")),
    ("owner",       "کارفرمای اصلی",               _("OWNER / CLIENT")),
    ("phase",       "فاز پروژه",                   _("PROJECT PHASE")),
    ("pmt",         "مجری طرح",                    _("PMT — PROJECT MGMT TEAM")),
    ("mc",          "مدیریت طرح",                  _("MC / PMC — MGMT CONTRACTOR")),
    ("licensor",    "لایسنسور",                    _("LICENSOR")),
    ("design",      "مشاور طراح",                  _("DESIGN CONSULTANT — FEED / DED")),
    ("supervision", "مشاور نظارت",                 _("SUPERVISION")),
    ("c",           "پیمانکار اجرا",               _("C — CONSTRUCTION ONLY")),
    ("p",           "پیمانکار خرید",               _("P — PROCUREMENT ONLY")),
    ("pc",          "پیمانکار خرید و اجرا",        _("PC — PROCUREMENT + CONSTRUCTION")),
    ("epc",         "پیمانکار طرح، خرید و اجرا",   _("EPC — ENG. PROC. CONSTRUCTION")),
    ("sub",         "پیمانکار جزء P",              _("SUBCONTRACTOR — P")),
    ("sub_pc",      "پیمانکار جزء PC",             _("SUBCONTRACTOR — PC")),
    ("sub_epc",     "پیمانکار جزء EPC",            _("SUBCONTRACTOR — EPC")),
    ("tpi",         "بازرس ثالث",                  _("TPI — THIRD PARTY INSP.")),
    ("laboratory",  "آزمایشگاه",                   _("LABORATORY")),
    ("supplier",    "تأمین‌کننده",                 _("SUB-SUPPLIER")),
    ("rival",       "رقیب احتمالی",                _("COMPETITOR")),
)
SLOT_COUNT = len(SLOTS)
_SLOT_BY_KEY = {s[0]: s for s in SLOTS}

# The two boxes that are not slots — never empty, because the project is the
# subject of the chart and we are always on it. They no longer behave the
# same way as each other: "project" is now an ordinary labelable card like
# the nineteen slots (it joined LABEL_KEYS below this round, as a
# manual-only tag), while "us" remains the one field backed by case data
# instead of a label at all. The Persian/English text below is therefore the
# same text ``cases.constants.MarketingLabel.MANUAL_ONLY_CHOICES`` shows for
# the "project" key, and the two must stay identical.
#
# Same PROJECT_ROLE/US_ROLE (Persian, untouched) vs PROJECT_ABBR/US_ABBR
# (English, gettext_lazy-wrapped) split as SLOTS above, and for the identical
# reason — see that tuple's own comment.
PROJECT_ROLE = "نام پروژه"
PROJECT_ABBR = _("PROJECT")
US_ROLE = "فولاد تبار"
US_ABBR = _("VENDOR / SUPPLIER")

# Every field on the chart, in reading order — the one list marketing/views.py
# and marketing/services.py both walk (via ALL_FIELDS/LABEL_KEYS below) so the
# chart's own card order and the fourteen MarketingLabel choices on a case can
# never drift apart on what a field is called. Anything else that names these
# fields (the separate company-directory section) reads the same two lists
# rather than retyping them.
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
# chart_interact.js: "label" for the twenty MarketingLabel keys a card can be
# tagged/untagged under (the same SET as marketing/services.py's own
# LABEL_KEYS — listed here in the chart's own reading order, which is
# _FIELD_ORDER's, while that module's is MarketingLabel.CHOICES +
# MANUAL_ONLY_CHOICES order; the two have always differed in ORDER and are
# only ever compared as sets), "us" for the single "our own position" card,
# and — historically — "inert" for fields no data source fed. Kept here as a
# plain tuple rather than an import from either module, so rolechart.py still
# touches no model, no request and no database — see the module docstring.
# Exposed on every node as ``kind``, and from there as a ``data-kind``
# attribute in the template, so the JS never has to hardcode which of the
# twenty-one keys falls in which group.
#
# supplier and rival joined this list first (they used to sit in _INERT_KEYS
# below, with no data source and no click interaction at all): a Marketing
# user can now hand-tag a company under either one via
# ``marketing/models.py::ClientLabel`` — see
# ``cases.constants.MarketingLabel.MANUAL_ONLY_CHOICES`` for why they are
# manual-tag-only (no case can ever hold them, unlike the other fourteen).
# Their GEOMETRY (bottom-left corner, no incoming edge, see _ROWS and
# _NO_INCOMING_EDGE below) is unrelated to this and does not change.
#
# This round the LAST four — project, phase, tpi, laboratory — followed them
# by exactly the same route and for exactly the same reason, each inserted at
# its own existing _FIELD_ORDER position rather than appended, so this tuple
# stays literally "_FIELD_ORDER minus us". They are manual-only too (a
# company can never BE a project name or a laboratory as far as a CASE is
# concerned), so all four are in MANUAL_ONLY_CHOICES, not CHOICES. Their
# geometry likewise does not change.
LABEL_KEYS = (
    "sponsor", "owner", "project", "phase", "pmt", "mc", "licensor", "design",
    "supervision", "c", "p", "pc", "epc", "sub", "sub_pc", "sub_epc", "tpi",
    "laboratory", "supplier", "rival",
)

# NOW EMPTY, AND DELIBERATELY STILL HERE. With those last four promoted there
# is no field left on this chart that nothing feeds: every key is either one
# of LABEL_KEYS above or "us". So ``_kind_of`` can no longer return "inert"
# for anything, and the honest reading is that the kind is dead.
#
# It is NOT deleted, because "inert" is not private to this module — it is a
# published contract. It leaves here as each node's ``kind``, reaches the
# template as ``data-kind``, and the frontend still branches on that exact
# string in three places that would have to change in the same commit:
# ``marketing/static/marketing/js/chart_interact.js`` (its ``modalKind ===
# 'inert'`` branch and the "inert cards" click handler) and
# ``marketing/static/marketing/css/rolechart.css`` (its kind-based card
# styling). Emptying the tuple retires the kind at the only place that can
# actually produce it, without touching a frontend this change was not asked
# to touch: those branches simply stop being reached. Removing the branch
# below as well would gain three lines and leave the JS reading a
# ``data-kind`` value the server can no longer emit — a worse, not cleaner,
# state. Whoever next edits chart_interact.js should delete its 'inert'
# handling and only then delete these two lines.
_INERT_KEYS = ()


def _kind_of(key):
    if key == "us":
        return "us"
    if key in _INERT_KEYS:   # unreachable while _INERT_KEYS is empty — see above
        return "inert"
    return "label"


# The rows of the chart, top to bottom — each a (y, ((field key, x), ...)).
# Reordering or reshaping a row is the only change a future layout tweak
# needs; ``build`` walks this once for the nodes and once more, pairwise,
# for the connectors between one row and the next.
#
# R1'S OWN LEFT/RIGHT ORDER WAS SWAPPED THIS ROUND, and that swap is the
# whole of it: licensor now takes PAIR[0] (the left column) and sponsor
# PAIR[1] (the right) — the owner's own request. Nothing else in this module
# assumed the old order. ``_connect`` is handed a row's x values only and
# draws the same 2-parents-to-1-child fan into R2 whichever card sits on
# which column; ``_badge``/``_node``/``_route`` are per-card or per-pair
# coordinate functions with no notion of a row's shape at all; and every
# other list that names these two fields (SLOTS, _FIELD_ORDER/ALL_FIELDS,
# LABEL_KEYS) is its own separate reading order that was never tied to a
# row's left-to-right layout — SLOTS has always listed sponsor first and
# licensor sixth even while both shared R1. The template walks _ROWS in this
# order (via ``rows``/``row_index``), so the CSS's own per-row hue and the
# chart's JS row model follow the swap for free.
#
# An earlier round re-paired three of these rows to shorten the chart:
# licensor moved up to sit beside sponsor on R1, leaving owner alone on its own row
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
    (R1,  (("licensor", PAIR[0]), ("sponsor", PAIR[1]))),
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
    ``_ROWS`` — every card sharing a row gets the same value, and it indexes
    straight into the ``rows`` list ``build()`` returns (``rows[row_index]``
    is always this node's own row, and ``rows[row_index]["y"]`` is always
    equal to this node's ``y``). That is what the chart's JS keys off to
    move a whole row as one unit when a card in it grows taller; nothing in
    this module reads it."""
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
        # "entity"/"entities" is the one bit of running English prose this
        # module produces (everything else on a node is a fixed label) — a
        # native SVG <title> tooltip, wrapped for the same Persian-toggle
        # reason as the abbreviation above.
        "title": "%s — %s — %d %s" % (
            role_fa, abbr, count, _("entity") if count == 1 else _("entities"),
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
    """Every edge between one row and the next — see the module docstring.

    A pure function of its four arguments, like ``_route`` (which it is the
    only caller of): same arguments in, same paths out, no state read or
    written anywhere. That matters now that the chart's JS re-runs this same
    routing itself for shifted rows — see ``build()``'s docstring for the
    exact recipe it mirrors.

    Each returned edge carries its finished ``d`` path AND the four
    coordinates that produced it, so a caller holding a moved row can
    recompute the path instead of being stuck with an opaque string:

        d      -- the SVG path (unchanged; the template draws only this)
        x1, y1 -- where the path starts, exactly as passed to ``_route``
        x2, y2 -- where it ends, likewise
        seg    -- which of the three shapes this edge is, and therefore how
                  its y values must be recomputed when a row moves:
                  "direct"     parent's bottom edge straight to a child's
                               top edge (y1 = y_parent_bottom,
                               y2 = y_child_top)
                  "to_waist"   many-to-many first half: a parent down to the
                               centre-lane waist (y1 = y_parent_bottom,
                               y2 = the waist)
                  "from_waist" many-to-many second half: the waist out to a
                               child (y1 = the waist, y2 = y_child_top)
        parent_bottom_y -- ``y_parent_bottom`` as given here, whatever this
                           edge's own y1/y2 happen to be
        child_top_y     -- ``y_child_top``, likewise

    ``build()`` adds ``parent_row_index``/``child_row_index`` on top of
    these; this function is told nothing about rows and stays row-agnostic.
    """
    if len(parents) == 1 and len(children) == 1:
        segs = [(parents[0], y_parent_bottom, children[0], y_child_top, "direct")]
    elif len(parents) == 1:
        segs = [(parents[0], y_parent_bottom, x, y_child_top, "direct")
                for x in children]
    elif len(children) == 1:
        segs = [(x, y_parent_bottom, children[0], y_child_top, "direct")
                for x in parents]
    else:
        waist = (y_parent_bottom + y_child_top) / 2.0
        segs = [(x, y_parent_bottom, CEN, waist, "to_waist") for x in parents]
        segs += [(CEN, waist, x, y_child_top, "from_waist") for x in children]
    return [
        {
            "d": _route(x1, y1, x2, y2),
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "seg": seg,
            "parent_bottom_y": y_parent_bottom,
            "child_top_y": y_child_top,
        }
        for x1, y1, x2, y2, seg in segs
    ]


# --------------------------------------------------------------------------- #
# The chart
# --------------------------------------------------------------------------- #
def build(counts):
    """Return everything the template needs to draw the chart.

    ``counts``  ``{field key: how many companies}`` — built in
                ``marketing/views.py::home`` from ``services.label_counts``
                (the twenty labelable fields — the same number the module
                docstring above states, and ``services.LABEL_KEYS`` is the
                list) and ``services.us_connections`` ("us", the
                twenty-first). Already scoped to the viewer. A field missing
                from it counts as zero.

    WHAT COMES BACK::

        view_w, view_h  the viewBox, as before
        nodes           one dict per card, as before (each carries its own
                        ``row_index``, see ``_node``)
        rows            NEW — one dict per row of ``_ROWS``, in the same
                        top-to-bottom order:
                            {"index": 0-based position in _ROWS,
                             "y":     that row's ORIGINAL top y,
                             "keys":  [field key, ...] left to right}
                        ``rows[i]["index"] == i`` always, and every node with
                        ``row_index == i`` has ``y == rows[i]["y"]`` and its
                        key in ``rows[i]["keys"]``. Plain lists of plain
                        dicts, like everything else here — this module still
                        touches no model, no request and no database.
        edges           one dict per drawn edge, as before, with its ``d``
                        byte-for-byte unchanged; the other keys are NEW and
                        purely additive, so any existing reader (the
                        template reads ``e.d`` and nothing else) is
                        untouched.

    WHY ``rows`` AND THE EXTRA EDGE KEYS EXIST. While an Inquiry is active
    the chart's JS grows cards taller in place to list connected company
    names inside them, and every row below a grown row has to slide down by
    the same amount (with the SVG growing taller to match). ``rows`` gives
    the JS each row as a unit and, crucially, each row's ORIGINAL y — the
    baseline every shift is measured from, so repeated growth/shrink never
    accumulates drift. The edge keys let it redraw a connector between two
    rows it has just moved, which the opaque ``d`` string alone could not.

    RECOMPUTING AN EDGE FOR SHIFTED ROWS. Every edge carries
    ``parent_row_index``/``child_row_index`` (indices into ``rows``),
    ``parent_bottom_y``/``child_top_y`` (the two ORIGINAL row anchors it was
    routed between: the parent row's bottom edge — its y plus a card's
    height — and the child row's top edge, i.e. its y), its own four
    original endpoint coordinates ``x1, y1, x2, y2`` exactly as handed to
    ``_route``, and ``seg`` — "direct", "to_waist" or "from_waist" (see
    ``_connect``). x never changes when rows move vertically, so given the
    parent row's new bottom edge ``pb`` and the child row's new top edge
    ``ct``::

        seg == "direct"      -> route(x1, pb, x2, ct)
        seg == "to_waist"    -> route(x1, pb, x2, (pb + ct) / 2)
        seg == "from_waist"  -> route(x1, (pb + ct) / 2, x2, ct)

    where ``pb`` is the shifted parent row's y plus the parent row's CURRENT
    height (``NODE_H`` = 62 when nothing in it has grown, taller when it
    has) and ``ct`` is the shifted child row's y. When nothing has grown,
    ``pb == parent_bottom_y`` and ``ct == child_top_y``, and the recipe
    reproduces the original ``d`` exactly.

    ``_route`` and ``_connect`` are pure functions of their arguments —
    nothing else feeds them — so the JS can mirror ``_route`` verbatim
    (``r`` is always 16 here, and ``CEN`` = 650 is the waist's x)::

        function route(x1, y1, x2, y2, r) {
          r = (r === undefined) ? 16 : r;
          if (x1 === x2) return "M" + x1 + " " + y1 + " L" + x2 + " " + y2;
          var ymid = (y1 + y2) / 2;
          var rr = Math.max(1, Math.min(r, Math.abs(ymid - y1),
                                        Math.abs(y2 - ymid),
                                        Math.abs(x2 - x1) / 2));
          var sx = x2 > x1 ? 1 : -1;
          var sy = y2 > y1 ? 1 : -1;
          return "M" + x1 + " " + y1 +
                 " L" + x1 + " " + (ymid - rr * sy) +
                 " Q" + x1 + " " + ymid + " " + (x1 + rr * sx) + " " + ymid +
                 " L" + (x2 - rr * sx) + " " + ymid +
                 " Q" + x2 + " " + ymid + " " + x2 + " " + (ymid + rr * sy) +
                 " L" + x2 + " " + y2;
        }

    (Python writes those numbers with ``%g``; over this chart's range — all
    coordinates well under 1300, halves the only fractions — JS's own number
    formatting prints them identically. Nothing depends on the two strings
    matching byte-for-byte anyway: the JS replaces the whole path.)
    """
    counts = counts or {}
    nodes = []
    edges = []
    rows = []
    for i, (y, row) in enumerate(_ROWS):
        rows.append({"index": i, "y": y, "keys": [k for k, _x in row]})
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
                for edge in _connect(
                    [x for _k, x in row], y + NODE_H,
                    [x for _k, x in included_next], y_next,
                ):
                    # The only thing _connect cannot know: which two rows it
                    # was just asked about. Added here, not there, so
                    # _connect stays a pure function of coordinates alone.
                    edge["parent_row_index"] = i
                    edge["child_row_index"] = i + 1
                    edges.append(edge)

    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "rows": rows,
        "nodes": nodes,
        "edges": edges,
    }
