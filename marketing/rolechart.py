"""The project role chart: its slots, its geometry, and nothing else.

WHAT THIS DRAWS. One project, and every outside party that stands between the
money and the metal: who pays for the plant, who owns it, who manages it, who
designs it, who is contracted to build or to buy it, who inspects it — and,
at the bottom of that graph, where WE stand as the vendor. Fifteen slots, plus
the project box itself and our own position, which are not slots because they
are never empty: the project is the subject of the chart and we are always on
it.

    SLOTS                       -- the fifteen, in reading order
    build(...)                  -- slots + assignments -> a drawable chart
    sample_assignments()        -- the placeholder data this step ships with

WHERE THE MODEL WILL PLUG IN (the seam, and the only seam).
``build()`` takes an ``assignments`` mapping — ``{slot key: organisation
name}`` — plus a project name and a contract model, and returns a finished
geometry dict the template walks. It touches no model, no request and no
database, and it never will: everything about a chart that is not its shape
comes in through that one argument. So when the owner specifies the model,
the whole change is

    - a new ``marketing/models.py`` holding the project and its role rows;
    - ``sample_assignments()`` in this module deleted, and the view's one call
      to it replaced by a query that builds the same ``{key: name}`` dict;

and NOTHING in ``build()``, in the template, or in the stylesheet moves. The
keys in ``SLOTS`` are the contract between the two halves — a role row will
carry one of them, so name them in the model rather than re-deriving them.

TWO THINGS THIS DELIBERATELY DOES NOT DO, so they are not mistaken for
oversights:

* The construction-only contractor (C) has no subcontractor and no inspector
  hanging under it, and no path from it down to us. That is not a missing
  branch — a contractor engaged for construction only buys no material, so
  there is no order that can reach us along it. The three chains that CAN
  reach us (P, PC, EPC) each carry the pair.
* Only one contract type can be the package's model at a time, so only the
  matching contractor box, and only the chain beneath it, can hold an
  organisation. The other three are drawn anyway, empty, because "which of
  these four is it" is the question the row exists to answer.

GEOMETRY. A 1300-unit-wide viewBox, four columns plus a centre lane, rows 92
apart. The numbers below are the mockup's own and are kept exactly, so a chart
drawn here lands on the same grid as the chart the owner approved. Everything
is computed in this module — the template receives finished coordinates and
path data and makes no arithmetic decision of its own.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Grid
# --------------------------------------------------------------------------- #
VIEW_W = 1300
NODE_W = 224
NODE_H = 62

COL = (200, 500, 800, 1100)     # the four contract columns
CEN = 650                       # the centre lane: owner -> project -> us

_GAP = 92
R1 = 18                         # sponsor
R2 = R1 + _GAP                  # owner
R3 = R2 + _GAP                  # the project
R4 = R3 + _GAP                  # PMT / MC
R5 = R4 + _GAP                  # licensor / design / supervision
R6 = R5 + _GAP                  # the four contract types
R7 = R6 + _GAP                  # subcontractors
R8 = R7 + _GAP                  # third-party inspectors
R9 = R8 + _GAP + 30             # us
R10 = R9 + _GAP + 26            # sub-supplier / competitor
VIEW_H = R10 + NODE_H + 26

# Text baselines inside a box, measured from its top edge.
Y_ROLE = 18                     # the Persian role name
Y_NAME = 36                     # the organisation, or "Not identified"
Y_ABBR = 53                     # the English abbreviation

# An organisation name longer than this is cut with an ellipsis; the full name
# stays in the node's <title> so nothing is actually lost.
NAME_MAX = 24


# --------------------------------------------------------------------------- #
# Colour families
# --------------------------------------------------------------------------- #
# A role family reads as a colour — the mockup's idea, kept. The colours are
# not the mockup's: every one of them is already in this platform's palette
# (core/theming.py and the tokens at the top of static/css/app.css), so the
# chart speaks the same colour language as the rest of the UI instead of
# importing a second one. The actual values live in the stylesheet as
# --rc-<family>; this list only names them and orders the legend.
FAMILIES = (
    ("principal", "Principal"),
    ("mgmt", "Project management"),
    ("design", "Engineering"),
    ("contract", "Contractor"),
    ("supply", "Supply chain"),
    ("inspect", "Inspection"),
    ("us", "Our position"),
    ("rival", "Competitor"),
)


# --------------------------------------------------------------------------- #
# The fifteen slots
# --------------------------------------------------------------------------- #
# (key, Persian role name, English abbreviation, colour family)
#
# Both labels are carried on purpose. The role names are the Persian terms the
# business actually uses and are the field itself; the abbreviations are the
# contract shorthand an English-reading colleague needs. Dropping either one
# would be dropping a field.
SLOTS = (
    ("sponsor",     "سرمایه‌گذار",                 "SPONSOR / INVESTOR",            "principal"),
    ("owner",       "کارفرمای اصلی",               "OWNER / CLIENT",                "principal"),
    ("pmt",         "مجری طرح",                    "PMT — PROJECT MGMT TEAM",       "mgmt"),
    ("mc",          "مدیریت طرح",                  "MC / PMC — MGMT CONTRACTOR",    "mgmt"),
    ("licensor",    "لیسانسور",                    "LICENSOR",                      "design"),
    ("design",      "مشاور طراح",                  "DESIGN CONSULTANT — FEED / DED", "design"),
    ("supervision", "مشاور نظارت",                 "SUPERVISION",                   "design"),
    ("c",           "پیمانکار اجرا",               "C — CONSTRUCTION ONLY",         "contract"),
    ("p",           "پیمانکار خرید",               "P — PROCUREMENT ONLY",          "contract"),
    ("pc",          "پیمانکار خرید و اجرا",        "PC — PROCUREMENT + CONSTRUCTION", "contract"),
    ("epc",         "پیمانکار طرح، خرید و اجرا",   "EPC — ENG. PROC. CONSTRUCTION", "contract"),
    ("sub",         "پیمانکار جزء",                "SUBCONTRACTOR",                 "supply"),
    ("tpi",         "بازرس ثالث",                  "TPI — THIRD PARTY INSP.",       "inspect"),
    ("supplier",    "تأمین‌کننده",                 "SUB-SUPPLIER",                  "supply"),
    ("rival",       "رقیب احتمالی",                "COMPETITOR",                    "rival"),
)
SLOT_COUNT = len(SLOTS)
_SLOT_BY_KEY = {s[0]: s for s in SLOTS}

# The two boxes that are not slots.
PROJECT_ROLE = "نام پروژه"
PROJECT_ABBR = "PROJECT"
US_ROLE = "موقعیت ما"
US_ABBR = "VENDOR / SUPPLIER"

# The dashed edge that is not a contract: the design consultant writes the
# specification our offer has to meet, whoever ends up placing the order.
INFLUENCE_LABEL = "مشخصات فنی را تعیین می‌کند"
INFLUENCE_LABEL_EN = "sets the technical specification"

# Which contract types can carry an order down to us, and which contractor box
# is the buyer in each. "C" and an unrecorded model both mean the material is
# the owner's own purchase, and then no order path is drawn at all.
BUYING_MODELS = ("P", "PC", "EPC")
CONTRACT_MODELS = ("C",) + BUYING_MODELS

# One chain per buying model: (slot key, column x, corridor x, corridor side).
# The corridor is the lane the inspector's own edge runs down, outside the
# column so it cannot be mistaken for the contractual line beside it.
_CHAINS = (
    ("p",   COL[1],  350, -1),
    ("pc",  COL[2],  950, +1),
    ("epc", COL[3], 1250, +1),
)
# Where each chain hands its order to us: (column x, lane x, lane y).
_CONVERGE = (
    (COL[1], 562, R8 + NODE_H + 14),
    (COL[2], 600, R8 + NODE_H + 30),
    (COL[3], 698, R8 + NODE_H + 46),
)


# --------------------------------------------------------------------------- #
# Small builders
# --------------------------------------------------------------------------- #
def _pill_width(text: str) -> int:
    """Width of the pill behind ``text`` at the stylesheet's 11.5px.

    An SVG ``<rect>`` cannot size itself to the ``<text>`` in front of it, so
    the width has to be estimated here — the template does no arithmetic (see
    the module docstring). The estimate is per script rather than one constant
    per character: Persian sets far narrower than Latin at the same size, and a
    single Latin-shaped constant is what left the mockup's pill nearly half as
    wide again as the words inside it. Measured in the browser at 11.5px Inter,
    the Persian label advances ~5.6 units a letter; 6.1 is that with headroom
    for a fallback face, and the 30 is the padding either side.
    """
    w = 0.0
    for ch in text:
        o = ord(ch)
        if ch == " ":
            w += 3.1
        elif 0x0600 <= o <= 0x06FF or o in (0x200C, 0x200F, 0x200E):
            w += 6.1                      # Persian / Arabic letter or joiner
        else:
            w += 6.6                      # Latin, digits, punctuation
    return int(w + 30)


def _clip(name: str) -> str:
    name = (name or "").strip()
    if len(name) > NAME_MAX:
        return name[: NAME_MAX - 1] + "…"
    return name


def _node(key, role_fa, abbr, family, cx, y, org="", extra=""):
    """One box. ``org`` empty means the slot is not identified yet."""
    org = (org or "").strip()
    filled = bool(org)
    classes = ["rc-node", "rc-f-" + family, "is-filled" if filled else "is-empty"]
    if extra:
        classes.append(extra)
    title = "%s (%s)" % (role_fa, abbr)
    title += " — " + org if filled else " — not identified yet"
    # A ring is drawn outside the box for the three that carry emphasis (the
    # project, our own position, the contract type that is this package's
    # model). Its geometry is computed here so the template never does
    # arithmetic — see the module docstring.
    ring = extra in ("is-project", "is-us", "is-model")
    return {
        "key": key,
        "role_fa": role_fa,
        "abbr": abbr,
        "family": family,
        "org": org,
        "org_clipped": _clip(org),
        "filled": filled,
        "x": cx - NODE_W // 2,
        "y": y,
        "cx": cx,
        "w": NODE_W,
        "h": NODE_H,
        "y_role": y + Y_ROLE,
        "y_name": y + Y_NAME,
        "y_abbr": y + Y_ABBR,
        "cls": " ".join(classes),
        "title": title,
        "ring": ring,
        "ring_x": cx - NODE_W // 2 - 5,
        "ring_y": y - 5,
        "ring_w": NODE_W + 10,
        "ring_h": NODE_H + 10,
    }


def _edge(d, live, kind="link"):
    """One path. ``kind`` is link | arrow | influence | halo."""
    cls = "rc-edge rc-" + kind
    cls += " is-live" if live else " is-pending"
    return {"d": d, "cls": cls,
            "marker": ("url(#rcArrowLive)" if live else "url(#rcArrowPending)")
            if kind == "arrow" else ""}


def _v(x, y1, y2, live, kind="link"):
    return _edge("M%d %d L%d %d" % (x, y1, x, y2), live, kind)


def _h(x1, x2, y, live, kind="link"):
    return _edge("M%d %d L%d %d" % (x1, y, x2, y), live, kind)


# --------------------------------------------------------------------------- #
# The chart
# --------------------------------------------------------------------------- #
def build(assignments=None, *, project_name="", contract_model=""):
    """Return everything the template needs to draw the chart.

    ``assignments``    {slot key: organisation name}. A key that is missing, or
                       whose name is blank, is an unidentified slot.
    ``project_name``   the name in the project box.
    ``contract_model`` one of CONTRACT_MODELS, or "" when it is not recorded.
                       Only the matching contractor box, and only the chain
                       under it, can hold an organisation — see the module
                       docstring.
    """
    given = {k: (v or "").strip() for k, v in (assignments or {}).items()}
    model = (contract_model or "").strip().upper()
    if model not in CONTRACT_MODELS:
        model = ""
    buyer = model if model in BUYING_MODELS else ""

    def org(key):
        """The organisation in a slot, honouring the contract model."""
        if key in ("c", "p", "pc", "epc") and key.upper() != model:
            return ""
        return given.get(key, "")

    def chain_org(key, chain_key):
        """A subcontractor / inspector belongs to the buying chain only."""
        if not buyer or chain_key.upper() != buyer:
            return ""
        return given.get(key, "")

    has = {key: bool(org(key)) for key, _fa, _ab, _fam in SLOTS}
    has["sub"] = bool(given.get("sub", "")) and bool(buyer)
    has["tpi"] = bool(given.get("tpi", "")) and bool(buyer)
    project_name = (project_name or "").strip()

    nodes, edges = [], []

    def slot(key, cx, y, org_name=None, extra=""):
        _k, fa, ab, fam = _SLOT_BY_KEY[key]
        nodes.append(_node(key, fa, ab, fam, cx, y,
                           org(key) if org_name is None else org_name, extra))

    # -- the spine: sponsor -> owner -> project ----------------------------- #
    slot("sponsor", CEN, R1)
    edges.append(_v(CEN, R1 + NODE_H, R2, has["sponsor"] and has["owner"]))
    slot("owner", CEN, R2)
    edges.append(_v(CEN, R2 + NODE_H, R3, has["owner"]))
    nodes.append(_node("project", PROJECT_ROLE, PROJECT_ABBR, "principal",
                       CEN, R3, project_name, "is-project"))

    # -- the owner's own arm, then the management contractor it engages ----- #
    b4 = R4 - 22
    edges.append(_v(CEN, R3 + NODE_H, b4, has["owner"]))
    edges.append(_h(COL[1], CEN, b4, has["owner"]))
    edges.append(_v(COL[1], b4, R4, has["pmt"]))
    slot("pmt", COL[1], R4)
    edges.append(_h(COL[1] + NODE_W // 2, COL[2] - NODE_W // 2, R4 + NODE_H // 2,
                    has["pmt"] and has["mc"], kind="arrow"))
    slot("mc", COL[2], R4)

    # -- the design chain: licensor -> FEED/DED, and supervision alongside -- #
    b5 = R5 - 22
    edges.append(_v(COL[2], R4 + NODE_H, b5, has["mc"]))
    edges.append(_h(COL[0], COL[2], b5, has["mc"]))
    edges.append(_v(COL[0], b5, R5, has["licensor"]))
    edges.append(_v(COL[2], b5, R5, has["supervision"]))
    slot("licensor", COL[0], R5)
    edges.append(_h(COL[0] + NODE_W // 2, COL[1] - NODE_W // 2, R5 + NODE_H // 2,
                    has["licensor"] and has["design"], kind="arrow"))
    slot("design", COL[1], R5)
    slot("supervision", COL[2], R5)

    # -- the four contract types ------------------------------------------- #
    b6 = R6 - 18
    any_contractor = any(has[k] for k in ("c", "p", "pc", "epc"))
    edges.append(_v(950, b5, b6, any_contractor))
    edges.append(_h(COL[0], COL[3], b6, any_contractor))
    for i, key in enumerate(("c", "p", "pc", "epc")):
        edges.append(_v(COL[i], b6, R6, has[key]))
        slot(key, COL[i], R6,
             extra="is-model" if key.upper() == model else "")

    # -- under each buying contractor: a subcontractor and an inspector ----- #
    for chain_key, x, corridor, side in _CHAINS:
        sub_name = chain_org("sub", chain_key)
        tpi_name = chain_org("tpi", chain_key)
        edges.append(_v(x, R6 + NODE_H, R7, bool(sub_name)))
        slot("sub", x, R7, org_name=sub_name)
        edges.append(_v(x, R7 + NODE_H, R8, bool(sub_name and tpi_name)))
        y_top = R6 + NODE_H + 12
        y_side = R8 + 36
        rim = x + side * (NODE_W // 2)
        edges.append(_v(x, R6 + NODE_H, y_top, bool(tpi_name)))
        edges.append(_h(corridor, x, y_top, bool(tpi_name)))
        edges.append(_v(corridor, y_top, y_side, bool(tpi_name)))
        edges.append(_h(rim, corridor, y_side, bool(tpi_name)))
        slot("tpi", x, R8, org_name=tpi_name)

    # -- every chain converges on us --------------------------------------- #
    for (x, lane, y), (chain_key, _x, _c, _s) in zip(_CONVERGE, _CHAINS):
        live = bool(chain_org("tpi", chain_key))
        edges.append(_v(x, R8 + NODE_H, y, live))
        edges.append(_h(lane, x, y, live))
        edges.append(_v(lane, y, R9, live))

    nodes.append(_node("us", US_ROLE, US_ABBR, "us", CEN, R9,
                       given.get("us", ""), "is-us"))
    edges.append(_v(CEN, R9 + NODE_H, R10, has["supplier"]))
    slot("supplier", CEN, R10)
    slot("rival", COL[0], R10)

    # -- the one edge that is not a contract -------------------------------- #
    #
    # It runs down the centre lane and crosses two contractual lines on the way,
    # so each segment is drawn twice: once wide in the panel colour to cut a gap
    # in whatever it passes over, then again as the dashed amber line. That is
    # the whole reason ``halo`` exists as an edge kind.
    influence = []
    if has["design"]:
        y_a = R5 + NODE_H + 6
        influence = [
            ("M%d %d L%d %d" % (COL[1], R5 + NODE_H, COL[1], y_a)),
            ("M%d %d L%d %d" % (CEN, y_a, COL[1], y_a)),
            ("M%d %d L%d %d" % (CEN, y_a, CEN, R9)),
        ]
        for d in influence:
            edges.append({"d": d, "cls": "rc-edge rc-halo", "marker": ""})
        for d in influence:
            edges.append({"d": d, "cls": "rc-edge rc-influence", "marker": ""})

    # The label sits in the one band of the centre lane that nothing else uses:
    # below the contractor row, above the subcontractor row. The mockup put it
    # on the contractor bus line, where it cut that line in half.
    influence_label = None
    if influence:
        cy = R6 + NODE_H + 16
        w = _pill_width(INFLUENCE_LABEL)
        influence_label = {
            "fa": INFLUENCE_LABEL,
            "en": INFLUENCE_LABEL_EN,
            "cx": CEN,
            "x": CEN - w // 2,
            "y": cy - 12,
            "w": w,
            "h": 24,
            "text_y": cy + 4,
        }

    filled = sum(1 for key, _fa, _ab, _fam in SLOTS if has[key])
    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "nodes": nodes,
        "edges": edges,
        "influence_label": influence_label,
        "families": [{"key": k, "label": lb} for k, lb in FAMILIES],
        "filled": filled,
        "total": SLOT_COUNT,
        "contract_model": model,
        "buyer": buyer,
        "project_name": project_name,
    }


# --------------------------------------------------------------------------- #
# Placeholder data
# --------------------------------------------------------------------------- #
# Everything below this line is scaffolding for THIS step and is meant to be
# deleted, not extended. It exists so the screen can be judged as a design —
# an all-empty chart would never show a family colour, and a chart with every
# slot filled would never show an empty one. Seven of the fifteen are filled,
# which shows both, and both edge states with them.
#
# The names are visibly placeholders ("شرکت نمونه …" is "Sample Company …") and
# the page says so above the chart, so nothing here can be mistaken for a real
# organisation on a real project.
_SAMPLE_PROJECT = "پروژه نمونه"
_SAMPLE_MODEL = "EPC"
_OUR_ORG = "Foolad Tabar"


def sample_assignments():
    """The placeholder ``{slot key: organisation}`` this step ships with.

    Delete this function when the model lands; the view's call to it is the
    only reference, and ``build()`` neither knows nor cares where its argument
    came from.
    """
    return {
        "sponsor": "شرکت نمونه الف",
        "owner": "شرکت نمونه ب",
        "pmt": "شرکت نمونه پ",
        "design": "شرکت نمونه ت",
        "epc": "شرکت نمونه ث",
        "tpi": "شرکت نمونه ج",
        "supplier": "شرکت نمونه چ",
        "us": _OUR_ORG,
    }


def sample_chart():
    """``build()`` over :func:`sample_assignments`, for the view."""
    return build(sample_assignments(),
                 project_name=_SAMPLE_PROJECT,
                 contract_model=_SAMPLE_MODEL)
