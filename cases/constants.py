"""Enumerations for the case lifecycle.

These mirror the business vocabulary:
- DocKind   -> Indent / Tender / Budget  (IN / TE / BU)
- OfferType -> "TO" or "TO & PI"
- FormKind  -> the three forms a case carries: Inquiry, TO, PI
- CaseStatus-> where the case currently sits in the workflow
- EventAction -> every recorded action in the case timeline

STATUS/ACTION LABELS ARE ``gettext_lazy``-WRAPPED, NOT PLAIN ``gettext``. Every
``CHOICES`` list below is built once, at import time — long before any request
(and therefore any viewer's chosen language) exists. A plain ``gettext(...)``
call would resolve against whatever language happened to be active at THAT
moment (the process's default) and freeze that text into the tuple forever;
every later request, regardless of the viewer's own language, would see the
same frozen string. ``gettext_lazy`` instead returns a lazy proxy that defers
the actual catalog lookup until the string is finally rendered to text (a
template's ``{{ }}``, an f-string, ``str()``) — which happens per request,
after ``accounts.middleware.LanguageMiddleware`` has activated that viewer's
own language. That per-request re-resolution is exactly what lets one shared
``choices=`` list (and the ``LABELS``/``get_..._display()`` lookups built on
it) show the correct language to every viewer at once, rather than whichever
language happened to be active when the server process started.
"""

from django.utils.translation import gettext_lazy as _


class DocKind:
    INDENT = "INDENT"
    TENDER = "TENDER"
    BUDGET = "BUDGET"

    CHOICES = [
        (INDENT, _("Indent")),
        (TENDER, _("Tender")),
        (BUDGET, _("Budget")),
    ]
    # Two-letter token used inside the document number.
    TOKEN = {INDENT: "IN", TENDER: "TE", BUDGET: "BU"}


class OfferType:
    TO = "TO"            # Technical Offer only
    TO_PI = "TO_PI"      # Technical Offer + Proforma Invoice (pricing required)

    CHOICES = [
        (TO, _("TO (Technical Offer)")),
        (TO_PI, _("TO & PI (Technical Offer + Proforma)")),
    ]


class FormKind:
    INQUIRY = "INQUIRY"
    TO = "TO"
    PI = "PI"

    CHOICES = [
        (INQUIRY, _("Inquiry")),
        (TO, _("Technical Offer (TO)")),
        (PI, _("Proforma Invoice (PI)")),
    ]
    # Token inserted into export file names (FT-TO-... / FT-PI-...).
    EXPORT_TOKEN = {INQUIRY: "INQ", TO: "TO", PI: "PI"}


class PriceType:
    """How a case is priced — drives the Internal / External sub-streams."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    BOTH = "BOTH"

    CHOICES = [
        (INTERNAL, _("Internal")),
        (EXTERNAL, _("External")),
        (BOTH, _("Internal & External")),
    ]
    LABELS = dict(CHOICES)


class Side:
    """A sub-stream of a case (only used when price type involves both)."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"

    CHOICES = [
        (INTERNAL, _("Internal")),
        (EXTERNAL, _("External")),
    ]
    LABELS = dict(CHOICES)


class MarketingLabel:
    """Which business role a case's client played, for Marketing's own chart.

    Optional on every case — see Case.marketing_label. Blank means "not
    specified," which Marketing treats as OWNER (the client's default role)
    rather than as a stored choice of its own; see marketing/services.py for
    where that default is actually applied. The fourteen keys below are
    exactly the fourteen of marketing/rolechart.py's twenty-one chart fields
    that describe a business relationship a case's client could actually
    hold — the other seven are not meaningful answers to "what role did THIS
    CLIENT play," so they are not offered here. Those seven split two ways,
    and the split is the whole reason MANUAL_ONLY_CHOICES exists further
    down:

    * SIX of them — the project itself, its phase, third-party inspection,
      the laboratory, the sub-supplier and a competitor — are still real
      things a COMPANY can be hand-tagged as, even though no CASE can ever
      be "for" one. They live in MANUAL_ONLY_CHOICES below.
    * ONE of them — "us", our own position — is not a tag at all in either
      direction. It is backed by case data (every client with a case) rather
      than by any stored label, so it appears in neither list here. See
      marketing/services.py::us_connections.

    So twenty of the chart's twenty-one fields are labelable in some form
    (fourteen here + six manual-only), and only "us" is not.
    """
    SPONSOR = "sponsor"
    OWNER = "owner"
    PMT = "pmt"
    MC = "mc"
    LICENSOR = "licensor"
    DESIGN = "design"
    SUPERVISION = "supervision"
    C = "c"
    P = "p"
    PC = "pc"
    EPC = "epc"
    SUB = "sub"
    SUB_PC = "sub_pc"
    SUB_EPC = "sub_epc"

    CHOICES = [
        (SPONSOR, "سرمایه‌گذار — SPONSOR / INVESTOR"),
        (OWNER, "کارفرمای اصلی — OWNER / CLIENT"),
        (PMT, "مجری طرح — PMT — PROJECT MGMT TEAM"),
        (MC, "مدیریت طرح — MC / PMC — MGMT CONTRACTOR"),
        (LICENSOR, "لایسنسور — LICENSOR"),
        (DESIGN, "مشاور طراح — DESIGN CONSULTANT — FEED / DED"),
        (SUPERVISION, "مشاور نظارت — SUPERVISION"),
        (C, "پیمانکار اجرا — C — CONSTRUCTION ONLY"),
        (P, "پیمانکار خرید — P — PROCUREMENT ONLY"),
        (PC, "پیمانکار خرید و اجرا — PC — PROCUREMENT + CONSTRUCTION"),
        (EPC, "پیمانکار طرح، خرید و اجرا — EPC — ENG. PROC. CONSTRUCTION"),
        (SUB, "پیمانکار جزء P — SUBCONTRACTOR — P"),
        (SUB_PC, "پیمانکار جزء PC — SUBCONTRACTOR — PC"),
        (SUB_EPC, "پیمانکار جزء EPC — SUBCONTRACTOR — EPC"),
    ]
    LABELS = dict(CHOICES)

    # Six more chart fields — the sub-supplier, a competitor, the project
    # itself, its phase, third-party inspection and the laboratory — that are
    # NOT among the fourteen above, and never will be: per this class's own
    # docstring, none of them is a meaningful answer to "what role did THIS
    # CASE'S CLIENT play," so they stay out of CHOICES/LABELS and therefore
    # out of Case.marketing_label's own choices and the case-creation/edit
    # forms (cases/forms.py, cases/templates/cases/case_create.html,
    # cases/templates/cases/edit_items.html) entirely — a case cannot
    # sensibly be "for" a competitor or a sub-supplier, and it is even more
    # obviously true of the last four: a case's CLIENT is a company, and a
    # company can never BE a project name, a project phase, an inspection
    # body's slot on the chart, or a laboratory reading of "what role did
    # this case's client play". (The four latecomers joined this list after
    # rival/supplier did; they used to be the chart's non-interactive
    # "inert" cards with no data source at all — see
    # marketing/rolechart.py::_INERT_KEYS, now empty, for the other side of
    # that promotion.)
    #
    # But marketing/rolechart.py draws all six of them as ordinary chart
    # cards (see its SLOTS/LABEL_KEYS), and the owner wants a Marketing user
    # able to hand-tag a company under any of them — "this company is a
    # competitor we're tracking," "this company supplies us," "this company
    # is the laboratory on that job" — exactly the kind of fact
    # marketing/models.py::ClientLabel already exists to hold.
    # ClientLabel.label is the ONLY consumer of this list: its ``choices``
    # is ``MarketingLabel.CHOICES + MarketingLabel.MANUAL_ONLY_CHOICES``, so
    # a company can carry these six tags manually while a case still can
    # never hold them. Kept as a SEPARATE list, deliberately never merged
    # into CHOICES/LABELS, so every existing consumer of CHOICES/LABELS
    # (the case forms above, and the ``posted_label in MarketingLabel.LABELS``
    # validation in cases/views.py) keeps rejecting all six as a case's own
    # marketing_label without needing to know this list exists.
    #
    # Same "<Persian> — <ENGLISH>" formatting as CHOICES, and the same
    # Persian role text / English abbreviation marketing/rolechart.py already
    # uses for these six keys (its SLOTS tuple for five of them, its
    # PROJECT_ROLE/PROJECT_ABBR pair for "project", which is drawn as a chart
    # field without being a SLOTS row), so each manual-tag choice in
    # ClientLabel reads identically to the card it tags on the chart.
    RIVAL = "rival"
    SUPPLIER = "supplier"
    PROJECT = "project"
    PHASE = "phase"
    TPI = "tpi"
    LABORATORY = "laboratory"

    MANUAL_ONLY_CHOICES = [
        (RIVAL, "رقیب احتمالی — COMPETITOR"),
        (SUPPLIER, "تأمین‌کننده — SUB-SUPPLIER"),
        (PROJECT, "نام پروژه — PROJECT"),
        (PHASE, "فاز پروژه — PROJECT PHASE"),
        (TPI, "بازرس ثالث — TPI — THIRD PARTY INSP."),
        (LABORATORY, "آزمایشگاه — LABORATORY"),
    ]


class CaseStatus:
    DRAFT = "DRAFT"
    WITH_TECHNICAL = "WITH_TECHNICAL"
    RETURNED_TO_COMMERCIAL = "RETURNED_TO_COMMERCIAL"
    WITH_SUPPLY = "WITH_SUPPLY"
    RETURNED_TO_TECHNICAL = "RETURNED_TO_TECHNICAL"
    WITH_COMMERCIAL = "WITH_COMMERCIAL"   # final form delivered back to commercial
    UNSUPPLIABLE_PENDING_SUPPLY = "UNSUP_PEND_SUP"      # awaiting supply manager
    UNSUPPLIABLE_PENDING_COMMERCIAL = "UNSUP_PEND_COM"  # awaiting commercial manager
    UNSUPPLIABLE = "UNSUPPLIABLE"         # cannot supply, now back with Commercial (active)
    UNSUPPLIABLE_CLOSED = "UNSUP_CLOSED"  # cannot supply, finalised (terminal)
    PENDING_CANCEL = "PENDING_CANCEL"     # cancel requested, awaiting manager approval
    CLOSED = "CLOSED"                     # confirmed / sent to client
    FINAL_APPROVED = "FINAL_APPROVED"     # commercial marked the closed case final (NOT yet shut)
    FINAL_CLOSED = "FINAL_CLOSED"         # commercial shut a final-approved case (terminal)
    BURNED = "BURNED"                     # deal fell through / case burned (terminal)
    CANCELLED = "CANCELLED"               # cancelled with reason (manager approved)

    CHOICES = [
        (DRAFT, _("Draft")),
        (WITH_TECHNICAL, _("With Technical")),
        (RETURNED_TO_COMMERCIAL, _("Returned to Commercial")),
        (WITH_SUPPLY, _("With Supply")),
        (RETURNED_TO_TECHNICAL, _("Returned to Technical")),
        (WITH_COMMERCIAL, _("With Commercial (final)")),
        (UNSUPPLIABLE_PENDING_SUPPLY, _("Cannot supply — awaiting Supply manager")),
        (UNSUPPLIABLE_PENDING_COMMERCIAL, _("Cannot supply — awaiting Commercial manager")),
        # These two codes deliberately share one English source string ("Cannot
        # supply") — see _collapse() in cases/services.py, which relies on that
        # shared wording to merge them into a single pill. Calling gettext_lazy
        # twice here still produces two proxy objects, but both resolve the
        # same msgid, so they carry the identical Persian text too and the pill
        # collapse keeps working exactly as before, in either language.
        (UNSUPPLIABLE, _("Cannot supply")),
        (UNSUPPLIABLE_CLOSED, _("Cannot supply")),
        (PENDING_CANCEL, _("Cancel — pending approval")),
        (CLOSED, _("Closed / Sent to client")),
        (FINAL_APPROVED, _("Final Approved")),
        (FINAL_CLOSED, _("Final Closed")),
        (BURNED, _("Burned")),
        (CANCELLED, _("Cancelled")),
    ]
    LABELS = dict(CHOICES)

    # Colour tokens consumed by the UI status pills.
    COLORS = {
        DRAFT: "#6b7280",
        WITH_TECHNICAL: "#1f5f8b",
        RETURNED_TO_COMMERCIAL: "#b07514",
        WITH_SUPPLY: "#1f7a5a",
        RETURNED_TO_TECHNICAL: "#1f5f8b",
        WITH_COMMERCIAL: "#b07514",
        UNSUPPLIABLE_PENDING_SUPPLY: "#b45309",
        UNSUPPLIABLE_PENDING_COMMERCIAL: "#b45309",
        UNSUPPLIABLE: "#b45309",
        UNSUPPLIABLE_CLOSED: "#b45309",
        PENDING_CANCEL: "#b45309",
        CLOSED: "#15803d",
        FINAL_APPROVED: "#0f766e",
        # The three "shut" outcomes are all black.
        FINAL_CLOSED: "#1a1a1a",
        BURNED: "#1a1a1a",
        CANCELLED: "#1a1a1a",
    }

    # Terminal statuses never appear in any inbox.
    # NOTE: FINAL_APPROVED is intentionally NOT terminal — a final-approved case
    # is still open (it can be Final-Closed). The terminal set is the three black
    # "shut" outcomes plus the cannot-supply closure.
    # Fully finished — no further workflow action is possible on these cases.
    # Alias used by seats Delegate / Close ("open tasks" = not ended).
    TERMINAL = [FINAL_CLOSED, BURNED, CANCELLED, UNSUPPLIABLE_CLOSED]
    ENDED = TERMINAL

    # Collapsed archive groups: several raw statuses share one filter/tab label
    # (e.g. WITH_TECHNICAL + RETURNED_TO_TECHNICAL → "With Technical").
    #
    # DELIBERATELY LEFT AS PLAIN, UNTRANSLATED ENGLISH STRINGS — unlike CHOICES
    # above. These values are not display text; they are internal grouping
    # KEYS: cases/services.py::StatusView builds ``status_fval`` (the archive
    # row's hidden filter value) straight from this dict, and the archive's tab
    # strip / tab counts / the ``data-status`` attribute the tab-filter JS
    # matches against are all built from ARCHIVE_TAB_ORDER below — the very
    # same English strings. Wrapping either in gettext_lazy would make BOTH
    # sides of that match re-resolve per viewer language, which sounds fine
    # until you remember they are compared as plain dict keys/strings in
    # Python code that runs once per request, not re-rendered per keystroke —
    # a subtler bug than a wrong label, and not worth the risk for a filter
    # value nobody ever sees on screen. The PILL text a viewer actually reads
    # (CaseStatus.LABELS, via CaseStatus.CHOICES above) is translated; this
    # bookkeeping layer underneath it stays in English on purpose.
    ARCHIVE_GROUP = {
        DRAFT: "Draft",
        WITH_COMMERCIAL: "With Commercial",
        RETURNED_TO_COMMERCIAL: "With Commercial",
        PENDING_CANCEL: "With Commercial",
        WITH_TECHNICAL: "With Technical",
        RETURNED_TO_TECHNICAL: "With Technical",
        WITH_SUPPLY: "With Supply",
        CLOSED: "Sent to client",
        FINAL_APPROVED: "Final approved",
        FINAL_CLOSED: "Final closed",
        BURNED: "Burned",
        CANCELLED: "Cancelled",
        UNSUPPLIABLE: "Cannot supply",
        UNSUPPLIABLE_CLOSED: "Cannot supply",
        UNSUPPLIABLE_PENDING_SUPPLY: "Cannot supply",
        UNSUPPLIABLE_PENDING_COMMERCIAL: "Cannot supply",
    }

    # Left-to-right archive status tabs (All is rendered separately on the right).
    ARCHIVE_TAB_ORDER = [
        "Draft",
        "With Technical",
        "With Supply",
        "With Commercial",
        "Sent to client",
        "Final approved",
        "Final closed",
        "Cannot supply",
        "Burned",
        "Cancelled",
    ]

    # Accent colour for each archive tab (representative status colour).
    ARCHIVE_TAB_COLORS = {
        "Draft": COLORS[DRAFT],
        "With Technical": COLORS[WITH_TECHNICAL],
        "With Supply": COLORS[WITH_SUPPLY],
        "With Commercial": COLORS[WITH_COMMERCIAL],
        "Sent to client": COLORS[CLOSED],
        "Final approved": COLORS[FINAL_APPROVED],
        "Final closed": COLORS[FINAL_CLOSED],
        "Cannot supply": COLORS[UNSUPPLIABLE],
        "Burned": COLORS[BURNED],
        "Cancelled": COLORS[CANCELLED],
    }


class EventAction:
    CREATE = "CREATE"
    SUBMIT_TO_TECHNICAL = "SUBMIT_TO_TECHNICAL"
    RETURN_TO_COMMERCIAL = "RETURN_TO_COMMERCIAL"
    ASSIGN = "ASSIGN"
    DELEGATE = "DELEGATE"
    SEND_TO_SUPPLY = "SEND_TO_SUPPLY"
    RETURN_TO_TECHNICAL = "RETURN_TO_TECHNICAL"
    SEND_TO_COMMERCIAL = "SEND_TO_COMMERCIAL"
    BUILD_TO = "BUILD_TO"
    BUILD_PI = "BUILD_PI"
    NEW_VERSION = "NEW_VERSION"
    EDIT = "EDIT"
    COMMENT = "COMMENT"
    CLOSE = "CLOSE"
    CANNOT_SUPPLY = "CANNOT_SUPPLY"
    APPROVE_UNSUPPLIABLE = "APPROVE_UNSUPPLIABLE"
    REJECT_UNSUPPLIABLE = "REJECT_UNSUPPLIABLE"
    RETURN_TO_SUPPLY = "RETURN_TO_SUPPLY"
    FINALIZE = "FINALIZE"
    REQUEST_CANCEL = "REQUEST_CANCEL"
    APPROVE_CANCEL = "APPROVE_CANCEL"
    REJECT_CANCEL = "REJECT_CANCEL"
    CANCEL = "CANCEL"
    BURN = "BURN"                 # deal fell through — case burned (terminal)
    FINAL_CLOSE = "FINAL_CLOSE"   # commercial shut a final-approved case (terminal)

    CHOICES = [
        (CREATE, _("Case created")),
        (SUBMIT_TO_TECHNICAL, _("Submitted to Technical")),
        (RETURN_TO_COMMERCIAL, _("Returned to Commercial")),
        (ASSIGN, _("Assigned to expert")),
        (DELEGATE, _("Delegated")),
        (SEND_TO_SUPPLY, _("Submitted to Supply")),
        (RETURN_TO_TECHNICAL, _("Returned to Technical")),
        (SEND_TO_COMMERCIAL, _("Submitted to Commercial")),
        (BUILD_TO, _("TO form built")),
        (BUILD_PI, _("PI form built")),
        (NEW_VERSION, _("New form version")),
        (EDIT, _("Edited")),
        (COMMENT, _("Comment added")),
        (CLOSE, _("Closed — sent to client")),
        (CANNOT_SUPPLY, _("Marked cannot supply")),
        (APPROVE_UNSUPPLIABLE, _("Cannot-supply approved")),
        (REJECT_UNSUPPLIABLE, _("Cannot-supply rejected")),
        (RETURN_TO_SUPPLY, _("Returned to Supply")),
        (FINALIZE, _("Final Approved")),
        (REQUEST_CANCEL, _("Cancellation requested")),
        (APPROVE_CANCEL, _("Cancellation approved")),
        (REJECT_CANCEL, _("Cancellation rejected")),
        (CANCEL, _("Cancelled")),
        (BURN, _("Burned")),
        (FINAL_CLOSE, _("Final Closed")),
    ]
    LABELS = dict(CHOICES)
