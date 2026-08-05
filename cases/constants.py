"""Enumerations for the case lifecycle.

These mirror the business vocabulary:
- DocKind   -> Indent / Tender / Budget  (IN / TE / BU)
- OfferType -> "TO" or "TO & PI"
- FormKind  -> the three forms a case carries: Inquiry, TO, PI
- CaseStatus-> where the case currently sits in the workflow
- EventAction -> every recorded action in the case timeline
"""


class DocKind:
    INDENT = "INDENT"
    TENDER = "TENDER"
    BUDGET = "BUDGET"

    CHOICES = [
        (INDENT, "Indent"),
        (TENDER, "Tender"),
        (BUDGET, "Budget"),
    ]
    # Two-letter token used inside the document number.
    TOKEN = {INDENT: "IN", TENDER: "TE", BUDGET: "BU"}


class OfferType:
    TO = "TO"            # Technical Offer only
    TO_PI = "TO_PI"      # Technical Offer + Proforma Invoice (pricing required)

    CHOICES = [
        (TO, "TO (Technical Offer)"),
        (TO_PI, "TO & PI (Technical Offer + Proforma)"),
    ]


class FormKind:
    INQUIRY = "INQUIRY"
    TO = "TO"
    PI = "PI"

    CHOICES = [
        (INQUIRY, "Inquiry"),
        (TO, "Technical Offer (TO)"),
        (PI, "Proforma Invoice (PI)"),
    ]
    # Token inserted into export file names (FT-TO-... / FT-PI-...).
    EXPORT_TOKEN = {INQUIRY: "INQ", TO: "TO", PI: "PI"}


class PriceType:
    """How a case is priced — drives the Internal / External sub-streams."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    BOTH = "BOTH"

    CHOICES = [
        (INTERNAL, "Internal"),
        (EXTERNAL, "External"),
        (BOTH, "Internal & External"),
    ]
    LABELS = dict(CHOICES)


class Side:
    """A sub-stream of a case (only used when price type involves both)."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"

    CHOICES = [
        (INTERNAL, "Internal"),
        (EXTERNAL, "External"),
    ]
    LABELS = dict(CHOICES)


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
        (DRAFT, "Draft"),
        (WITH_TECHNICAL, "With Technical"),
        (RETURNED_TO_COMMERCIAL, "Returned to Commercial"),
        (WITH_SUPPLY, "With Supply"),
        (RETURNED_TO_TECHNICAL, "Returned to Technical"),
        (WITH_COMMERCIAL, "With Commercial (final)"),
        (UNSUPPLIABLE_PENDING_SUPPLY, "Cannot supply — awaiting Supply manager"),
        (UNSUPPLIABLE_PENDING_COMMERCIAL, "Cannot supply — awaiting Commercial manager"),
        (UNSUPPLIABLE, "Cannot supply"),
        (UNSUPPLIABLE_CLOSED, "Cannot supply"),
        (PENDING_CANCEL, "Cancel — pending approval"),
        (CLOSED, "Closed / Sent to client"),
        (FINAL_APPROVED, "Final Approved"),
        (FINAL_CLOSED, "Final Closed"),
        (BURNED, "Burned"),
        (CANCELLED, "Cancelled"),
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
        (CREATE, "Case created"),
        (SUBMIT_TO_TECHNICAL, "Submitted to Technical"),
        (RETURN_TO_COMMERCIAL, "Returned to Commercial"),
        (ASSIGN, "Assigned to expert"),
        (DELEGATE, "Delegated"),
        (SEND_TO_SUPPLY, "Submitted to Supply"),
        (RETURN_TO_TECHNICAL, "Returned to Technical"),
        (SEND_TO_COMMERCIAL, "Submitted to Commercial"),
        (BUILD_TO, "TO form built"),
        (BUILD_PI, "PI form built"),
        (NEW_VERSION, "New form version"),
        (EDIT, "Edited"),
        (COMMENT, "Comment added"),
        (CLOSE, "Closed — sent to client"),
        (CANNOT_SUPPLY, "Marked cannot supply"),
        (APPROVE_UNSUPPLIABLE, "Cannot-supply approved"),
        (REJECT_UNSUPPLIABLE, "Cannot-supply rejected"),
        (RETURN_TO_SUPPLY, "Returned to Supply"),
        (FINALIZE, "Final Approved"),
        (REQUEST_CANCEL, "Cancellation requested"),
        (APPROVE_CANCEL, "Cancellation approved"),
        (REJECT_CANCEL, "Cancellation rejected"),
        (CANCEL, "Cancelled"),
        (BURN, "Burned"),
        (FINAL_CLOSE, "Final Closed"),
    ]
    LABELS = dict(CHOICES)
