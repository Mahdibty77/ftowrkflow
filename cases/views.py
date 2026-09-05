"""Views for the case workflow and the commercial master-data screens.

The workflow rules live in :mod:`cases.services`; these views only translate
HTTP requests into service calls, enforce who may see what, and render the
themed templates. Inbox visibility follows the business spec:

* Commercial sees the live status of *every* case (plus its reports).
* Technical / Supply only see the items currently sitting in their unit
  (their kartabl / inbox), sorted so pricing work is deadline-first.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode

from accounts.constants import Role, Unit
from core.persian_text import normalize_persian

from . import codes, exports, services
from .constants import CaseStatus, FormKind, DocKind, MarketingLabel, OfferType, PriceType, EventAction, Side
from .forms import (CaseCreateForm, ClientForm, ClientRenameForm, CommentForm,
                    ExpertCodeForm)
from .inquiry_validate import validate_inquiry_rows
from .models import Case, CaseEvent, CaseForm, Client, ExpertCode, LineItem


def _vat_percent_value() -> float:
    try:
        from accounts.models import PlatformConfig
        return float(PlatformConfig.load().vat_percent or 10)
    except Exception:
        return 10.0

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _profile(user):
    return getattr(user, "profile", None)


def _require_unit(user, *units) -> bool:
    p = _profile(user)
    return bool(p and not p.is_admin and p.unit in units)


def _can_open_marketing_chart(request) -> bool:
    """May this viewer be offered the "Marketing chart" button on this page?

    Two pages ask (``archive`` and ``case_detail``) and both ask this and
    nothing else, so the answer stays one expression in one place. The decision
    itself belongs to the Marketing app and is made there — it needs the
    person's whole seat list, not the active seat, because the viewer is by
    definition sitting in a Commercial seat when they want to go to the chart.

    Imported inside the function: ``marketing`` imports ``cases.models`` at
    module level, so a module-level import back the other way would be a cycle.
    Failures are swallowed to False — a missing button is a nuisance, a case
    archive that will not render is not.
    """
    try:
        from marketing.access import can_reach_marketing
        return bool(can_reach_marketing(request))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Reports / Reminders / a marketing-style Timeline, on the CASE's own page.   #
# --------------------------------------------------------------------------- #
# THREE THIN BRIDGES INTO `marketing`, NOT A SECOND REPORTS/REMINDERS SYSTEM.
# Every row these three functions return comes straight out of
# ``marketing.models.CompanyReport`` / ``Reminder`` / ``ClientEvent`` through
# the exact same service functions the company's own Marketing directory page
# uses (``marketing/services.py::list_reports``/``client_timeline``,
# ``marketing/reminders.py::list_for_user``) — a report or reminder written
# from either page is the same database row and is visible from both. Each
# function is a small, request-scoped ADAPTER: it decides which of that
# function's existing, already-correct arguments this NEW case-scoped caller
# should pass, and nothing about the underlying scoping/redaction rules is
# reimplemented here.
#
# LOCAL IMPORTS THROUGHOUT, for the same reason ``_can_open_marketing_chart``
# above gives: ``marketing`` imports ``cases.models`` at module level, so a
# module-level import back the other way would be a cycle. Failures are
# swallowed to an empty list — precisely as that function swallows to
# ``False`` — because a case that fails to draw its Marketing tabs must still
# render every other tab on this page; a company's own directory page is
# where the authoritative reports/reminders/timeline live regardless.
#
# THE VIEWING RULE ON THIS PAGE IS "CASE ACCESS", NOT "WHO WROTE THE ROW".
# The owner's own words for this surface are explicit: "برای پرونده هر شخصی
# که به ان پرونده با توجه به نقشش دسترسی دارد میتواند این گزارش‌ها را ببیند"
# — for a case, anyone who has access to that case, per their role, may see
# these reports. That is a CASE-visibility question, already settled by
# ``services.user_can_view_case`` before ``case_detail`` ever reaches these
# calls, and it is a DIFFERENT question from the author-privacy ``scope``
# every function in ``marketing/services.py``/``reminders.py`` otherwise
# takes (an ordinary Marketing seat sees only the rows IT wrote; a Supervisor/
# GM/admin sees everyone's). So ``_case_marketing_reports`` below always asks
# for "all" reports on this case, deliberately not deriving a scope from
# whatever Marketing seat this viewer might or might not separately hold —
# every viewer of THIS page already has case access, and the report itself is
# a shared company record the owner said such a viewer may read regardless of
# who wrote it. Reminders are the one exception — see
# ``_case_marketing_reminders``'s own docstring for why that one stays
# owner-scoped instead.
def _case_marketing_reports(case, request) -> list:
    """Every ``CompanyReport`` attached to THIS case — see the section banner
    above for the "all, not scope" reasoning.

    ``marketing/services.py::list_reports`` already knows how to filter to
    one case (``case=case``, added alongside this feature) and how to shape
    each row (``_report_row``); this only supplies the two arguments a
    case-scoped, case-access-gated caller needs that differ from the company
    page's own call.

    ``case_access``: A THROWAWAY, LOCAL ``CaseAccess(all_cases=True)`` — NOT
    a general widening of case visibility, and it is never returned or reused
    anywhere past this one call. ``list_reports`` uses it only to decide
    whether the ATTACHED CASE'S OWN DOCUMENT NUMBER may be printed
    (``marketing/services.py::_report_row``), and every report this call
    returns is, by construction, attached to THIS SAME CASE — whose document
    number is already unconditionally visible to this viewer in this very
    page's own header, because they already passed ``user_can_view_case`` to
    reach it. Passing the real, narrower ``marketing.access.case_access_for``
    here would UNDER-redact nothing that matters (there is only ever one case
    in play) but WOULD wrongly blank a number the viewer already has for any
    Technical/Supply/peer-Commercial viewer whose Marketing-side case access
    happens not to cover this case (that access rule is about a DIFFERENT
    axis — a Marketing seat's own visible cases — and has nothing to say
    about a Commercial/Technical/Supply seat's ordinary right to view a case
    it is already looking at). This is the considered conclusion, not an
    oversight: on THIS page, case identity is never in question, so the gate
    that exists to protect it elsewhere is neither needed nor correct here.
    """
    try:
        from marketing import services as marketing_services
        from marketing.access import CaseAccess
    except Exception:
        return []
    permissive_case_access = CaseAccess(can_open=True, all_cases=True)
    return marketing_services.list_reports(
        case.client, request.user, "all", case=case,
        case_access=permissive_case_access,
    )


def _case_marketing_reminders(case, request, *, see_every_owner: bool) -> list:
    """This case's own reminders — kept OWNER-SCOPED by default, unlike
    reports above, and this is a deliberate, judgement-call difference.

    A REMINDER STAYS A PRIVATE NOTE-TO-SELF EVEN HERE. The owner's plain
    words above ("anyone with access to the case may see these reports")
    used the word "گزارش‌ها" (reports); they do not, on their own, resolve
    reminders one way or the other, which is exactly why this is a judgement
    call rather than a quoted rule. ``marketing/reminders.py``'s own module
    docstring — and this round's widening of it — settles the closest
    precedent available: a reminder is "one person's private notes-to-self",
    and even the round that let a Marketing Supervisor/GM/admin LIST
    everyone's reminders on the company page left re-timing and marking one
    done strictly owner-only, and left it OFF the shared company timeline
    entirely, precisely so the privacy is not accidentally erased by a
    surface built for a different purpose. Extending "anyone who can open
    this CASE" — a population that, unlike the Marketing directory, includes
    Technical, Supply and every other Commercial peer with no Marketing
    seat at all — to read a colleague's private note would be a materially
    bigger widening than anything the owner has asked for, on a surface the
    reminder's own privacy rule was never written with in mind. So the
    default here is the narrow one: ``scope="own"``.

    ``see_every_owner`` IS THE ONE WIDENING KEPT, AND IT IS THE ADMIN/GM
    TIER ALONE — ``case_detail``'s own ``is_admin_view`` (admin or General
    Manager, by profile OR active seat, exactly as that flag is computed for
    every other admin-only affordance already on this page), not a
    Marketing-Supervisor check. A Marketing Supervisor who ALSO holds a
    Commercial seat is, on THIS page, functioning as a Commercial viewer of
    a case — not as a Marketing Supervisor reading their own unit's shared
    list — so the widening that population enjoys on the Marketing side is
    deliberately not carried onto this unrelated surface. The admin/GM tier
    is kept because it already sees everything else on this exact page
    (export logs, currency logs, every admin-only affordance) and — in
    practice — every reminder ``case=this case`` can ever hold was created by
    exactly one person anyway: ``case_reminder_add`` below only ever accepts
    the write from this case's own commercial owner (see
    ``services.is_case_commercial_owner``), so "own" and "all" agree on every
    row except when an admin/GM is the one looking.

    ``marketing/reminders.py::list_for_user``'s new ``case=`` filter (added
    alongside this feature) does the actual query; this only decides which
    ``scope`` to hand it and decorates each row with the same ``is_due``/
    ``is_done`` flags ``marketing/views.py::_reminder_rows`` computes for the
    company page, so the two screens describe the same row identically.
    Acting on a reminder (re-time / mark dealt-with) is deliberately NOT
    offered from this page — that stays the marketing reminders screens' own
    job, which already implement it correctly against these exact rows;
    duplicating those controls here would be a second, competing entry point
    onto the same mutation rather than a shared one.
    """
    try:
        from marketing import reminders as marketing_reminders
        from marketing.models import ReminderState
    except Exception:
        return []
    from django.utils import timezone

    now = timezone.now()
    scope = "all" if see_every_owner else "own"
    rows = marketing_reminders.list_for_user(
        request.user, scope, client=case.client, case=case,
    )
    for row in rows:
        row.is_due = (row.state == ReminderState.OPEN and row.due_at <= now)
        # ``is_done`` is NOT set here — ``marketing.models.Reminder.is_done``
        # is already a read-only ``@property`` computed from ``state``, and
        # the template reads it straight off each row exactly the way
        # ``marketing/views.py::_reminder_rows`` leaves it alone too.
    return rows


def _case_marketing_timeline(case, request) -> list:
    """Marketing's own record of what happened ABOUT this one case — every
    report written against it, and every change to its own business role —
    pulled out of the company's ``ClientEvent`` timeline and filtered down to
    this case alone.

    WHY THESE TWO ACTION KINDS, AND ONLY THESE TWO. Every ``ClientEvent`` a
    case can ever appear on freezes its document number into the row at
    write time (see ``marketing/services.py``'s module docstring, "IT DOES
    STILL CARRY CASE NUMBERS THAT WERE FROZEN INTO IT"), and exactly two
    action kinds do that: ``REPORT_ADDED`` (``add_report`` freezes the
    attached case's ``doc_no`` as the row's ``subject``) and
    ``CASE_ROLE_CHANGED`` (``log_case_role_change`` freezes it the same way,
    from ``cases/views.py``'s own two edit-save paths). Everything else this
    company's timeline can hold — a manual tag, a connection, a contact — is
    about the COMPANY, not about any one of its cases, so it has no case
    number to match and is correctly left off a CASE's own timeline.
    ``CASE_ROLE_CHANGED`` is included DELIBERATELY, not merely because it
    happens to carry a matching number: it is a change to THIS case's own
    ``marketing_label``, i.e. directly about this case, and
    ``marketing/services.py::_scoped_events`` already includes it
    unconditionally in both scopes on the company's own timeline for the
    identical reason ("some facts on this timeline are not one Marketing
    user's private action to own") — omitting it from a CASE-scoped view of
    the very same fact would be a stranger inconsistency than including it.

    REMINDERS DO NOT APPEAR HERE, AND THAT IS NOT AN OMISSION EITHER.
    ``marketing/reminders.py::create`` writes NO ``ClientEvent`` at all, by
    explicit, long-standing design ("a reminder is a private note to self...
    a row on a timeline ... would publish [it]") — there is no case-attached
    row to select in the first place, and inventing one here would be new
    logging behaviour this task never asked for and the model's own docstring
    argues against. A case's reminders are the REMINDERS tab, in full; this
    Timeline tab is only ``ClientEvent`` rows.

    ``case_access=CaseAccess(all_cases=True)`` — A LOCAL, THROWAWAY GRANT,
    identical in spirit and in scope to the one ``_case_marketing_reports``
    above builds and explains at length: it exists only so
    ``client_timeline``'s own ``_redact_case_numbers`` does not blank a
    document number that is, by construction, THIS SAME CASE's own — already
    fully known to this viewer from the page they are reading it on — and it
    is discarded the moment this function returns. ``scope="all"`` on the
    ``client_timeline`` call for the identical reason ``_case_marketing_
    reports`` passes ``"all"``: this page's viewing rule is case access, not
    which Marketing colleague happened to write the row.
    """
    try:
        from marketing import services as marketing_services
        from marketing.access import CaseAccess
        from marketing.models import ClientEventAction
    except Exception:
        return []
    doc_no = (getattr(case, "doc_no", "") or "").strip()
    if not doc_no:
        return []
    permissive_case_access = CaseAccess(can_open=True, all_cases=True)
    entries = marketing_services.client_timeline(
        case.client, request.user, "all", case_access=permissive_case_access,
    )
    wanted = {ClientEventAction.REPORT_ADDED, ClientEventAction.CASE_ROLE_CHANGED}
    return [e for e in entries
            if e.get("action") in wanted and e.get("subject") == doc_no]


def _parse_rows(form, files) -> list[dict]:
    """Return inquiry rows from the pasted grid JSON and/or an Excel upload.

    Both inputs map to the same four business columns: Item, Description,
    Size, Unit (Item is the row number). Excel parsing reads the first four
    columns regardless of their header text.

    IMPORTANT: the two inputs are mutually exclusive. The create form often
    populates the pasted-grid JSON FROM the uploaded Excel (for preview) and then
    submits BOTH, which previously appended the same rows twice (an N-row inquiry
    became 2N in the TO/PI). We therefore take the pasted grid when present and
    only fall back to the Excel file when there is no pasted grid.
    """
    rows: list[dict] = []

    pasted = (form.cleaned_data.get("pasted_table") or "").strip()
    if pasted:
        try:
            data = json.loads(pasted)
            for entry in data:
                if isinstance(entry, dict):
                    rows.append({
                        "client_row": entry.get("client_row") or entry.get("#") or "",
                        "description": entry.get("description") or entry.get("Description") or "",
                        "size": entry.get("size") or entry.get("Size") or "",
                        "unit": entry.get("unit") or entry.get("Unit") or "",
                        "quantity": entry.get("quantity") or entry.get("Qty") or "",
                    })
                elif isinstance(entry, (list, tuple)):
                    cells = list(entry) + ["", "", "", ""]
                    rows.append({
                        "description": cells[1], "size": cells[2],
                        "unit": cells[3], "quantity": "",
                    })
        except (ValueError, TypeError):
            pass

    # Only read the Excel upload when the pasted grid produced nothing, so the
    # same rows are never counted twice.
    if not rows:
        upload = files.get("excel_file")
        if upload:
            rows.extend(_rows_from_excel(upload))

    # Drop fully-empty rows.
    return [r for r in rows if any(
        str(v).strip() for k, v in r.items() if k != "client_row"
    )]


def _rows_from_excel(file_obj, max_rows: int | None = None) -> list[dict]:
    import openpyxl

    rows: list[dict] = []
    filled = 0
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    for idx, raw in enumerate(ws.iter_rows(values_only=True)):
        if idx == 0 and _looks_like_header(raw):
            continue
        cells = [("" if c is None else str(c).strip()) for c in raw]
        # Drop a leading pure-integer row number if the file has one.
        if cells and cells[0].isdigit():
            cells = cells[1:]
        # Source columns are Description, Size, Qty, Unit (qty before unit).
        cells = (cells + ["", "", "", ""])[:4]
        # A row with nothing in any of the four columns is skipped outright.
        # In read-only mode openpyxl yields one tuple per row of the sheet's
        # STORED DIMENSION, not per row of data, so a workbook carrying a
        # handful of items plus formatting applied down a whole column arrives
        # here as tens of thousands of empty tuples. Building a dict for each of
        # them filled a worker with megabytes of blanks, and counting them
        # towards ``max_rows`` refused perfectly ordinary inquiries.
        #
        # Dropping them here cannot change any caller's result: both callers
        # already discard exactly these rows (``_parse_rows`` above and
        # ``preview_excel``'s ``any(r.values())``), and the values are the same
        # stripped strings both filters test.
        if not any(cells):
            continue
        # ``max_rows`` is opt-in and only the preview endpoint sets it: a preview
        # is a convenience for a human-sized inquiry, so an absurd workbook is
        # refused before it is turned into dicts. Case creation deliberately
        # passes no cap, so no inquiry that can be created today stops working.
        filled += 1
        if max_rows is not None and filled > max_rows:
            wb.close()
            raise ValueError(
                f"the sheet has more than {max_rows:,} rows. "
                "Split it into smaller files before previewing."
            )
        rows.append({"description": cells[0], "size": cells[1],
                     "quantity": cells[2], "unit": cells[3]})
    wb.close()
    return rows


def _parse_jalali_deadline(raw: str):
    """Parse a Jalali 'YYYY-MM-DD[ HH:MM]' string into an aware datetime."""
    import datetime as _dt
    from django.utils import timezone
    from .jalali import jalali_to_gregorian
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        date_part, _, time_part = raw.partition(" ")
        # Accept dot, slash or dash as the date separator.
        norm = date_part.replace("/", "-").replace(".", "-")
        jy, jm, jd = [int(x) for x in norm.split("-")]
        if time_part:
            hh, mm = (int(x) for x in (time_part.split(":") + ["0", "0"])[:2])
        else:
            hh, mm = 0, 0
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        naive = _dt.datetime(gy, gm, gd, hh, mm)
    except (ValueError, TypeError):
        return None
    tz = timezone.get_current_timezone()
    return timezone.make_aware(naive, tz) if timezone.is_naive(naive) else naive


def _read_typed_deadline(raw: str, current=None):
    """Read a deadline the user typed, exactly as the New Case screen reads it.

    Returns ``(value, error)`` — ``error`` empty when the value is usable.

    The rule and the wording are not restated here: the box is validated by
    ``CaseCreateForm``'s own ``clean_deadline``, the single place that decides
    what a typed Jalali deadline means (which separators are read, that an
    unreadable value is refused, that a new deadline may not be in the past).
    Every screen that offers the box therefore judges it identically, and the
    user is told the same thing wherever they are standing.

    ``current`` is the deadline already on the case. The editor pre-fills the box
    with it, so re-submitting it unchanged is not the user setting a deadline —
    and a case whose own deadline has since gone by would otherwise refuse every
    save from the moment it passed, on a screen the user opened to edit rows.
    An unchanged value is passed straight through; only a value that actually
    moves the deadline meets the not-in-the-past rule.

    "Unchanged" is judged against what the box SHOWS, which is the deadline to
    the minute (``Y.m.d H:i``). A stored value carrying seconds — nothing the
    editor can type, but an import or a shell can leave one — would otherwise
    never match the value it pre-filled, and a case whose second-carrying
    deadline had passed could then neither be re-sent (refused as past) nor
    cleared (refused as a wipe): the screen would refuse every save there is.

    Callers decide what an EMPTY box means (it differs per screen), so this is
    only called with something typed.
    """
    raw = (raw or "").strip()
    if current is not None and _parse_jalali_deadline(raw) in (
            current, current.replace(second=0, microsecond=0)):
        return current, ""
    form = CaseCreateForm(data={"deadline": raw})
    form.is_valid()          # runs the field's own clean_deadline
    errs = form.errors.get("deadline")
    if errs:
        return None, str(errs[0])
    return form.cleaned_data.get("deadline"), ""



def _looks_like_header(raw) -> bool:
    """True when the first Excel row is a header (contains column titles like
    Description / Size / Qty / Unit) rather than real data, so we can skip it."""
    if not raw:
        return False
    text = " ".join(str(c).lower() for c in raw if c is not None)
    return any(k in text for k in ("description", "item", "size", "unit", "qty", "quantity"))


# Upper bound for the *preview* parse only (see ``_rows_from_excel``). No real
# inquiry comes anywhere near this; it exists so an uploaded workbook cannot be
# expanded into an unbounded list of dicts inside a worker.
MAX_PREVIEW_ROWS = 5000


@login_required
def preview_excel(request):
    """Parse an uploaded Excel file and return its rows as JSON.

    Used by the new-case grid so the commercial user can see and edit the
    imported rows before creating the case.
    """
    from django.http import JsonResponse
    from people.role_nav import work_context
    if request.method != "POST" or not request.FILES.get("excel_file"):
        return JsonResponse({"ok": False, "error": "No file uploaded."}, status=400)
    # Only the two Commercial screens (new case / edit items) embed this URL, and
    # only Commercial may ever create or edit an inquiry — so parsing an upload
    # here is Commercial work. Without this, any logged-in account could feed
    # the parser, which is why the endpoint is gated the same way its callers are.
    profile = _profile(request.user)
    ctx = work_context(request)
    is_comm = bool(
        (ctx.role and ctx.role.unit == Unit.COMMERCIAL)
        or (profile and profile.unit == Unit.COMMERCIAL)
        or (profile and profile.is_admin)
    )
    if not is_comm:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    try:
        rows = _rows_from_excel(request.FILES["excel_file"], max_rows=MAX_PREVIEW_ROWS)
    except Exception as exc:  # pragma: no cover - defensive
        return JsonResponse({"ok": False, "error": f"Could not read the file: {exc}"}, status=400)
    rows = [r for r in rows if any(r.values())]
    errs = validate_inquiry_rows(rows)
    if errs:
        return JsonResponse({
            "ok": False,
            "error": "Upload cancelled — inquiry rules failed:\n" + "\n".join(errs),
            "errors": errs,
        }, status=400)
    return JsonResponse({"ok": True, "rows": rows})


# ---------------------------------------------------------------------------
# Inbox (kartabl)
# ---------------------------------------------------------------------------
@login_required
# The inbox states what is waiting for you RIGHT NOW, so a stored copy of it is
# wrong the moment anything moves. Without this the browser is free to answer
# the BACK button out of its own cache: open a case, press Back, and the page
# that returns is the one from before you opened it — still showing the case as
# unopened, and still missing anything that arrived meanwhile. Refusing to store
# it costs one request on Back and makes the answer always the server's.
@never_cache
def inbox(request):
    profile = _profile(request.user)
    if profile is None or profile.is_admin:
        return redirect("accounts:admin_console")
    if profile.is_general_manager:
        return redirect("reports:dashboard")
    # A unit outside the TO/PI workflow (Marketing) has no inbox — the shared
    # membership rule, services.inbox_filter_q, returns None for it — so this
    # page could only ever render an empty kartabl with a status strip and a
    # "New case" button that is not theirs. Send them to their own workspace
    # instead, which is also where reports.dashboard's own redirect for a
    # non-workflow unit ends up (it redirects here, and here redirects on).
    if profile.unit == Unit.MARKETING:
        return redirect("marketing:home")

    from people.role_nav import work_context
    ctx = work_context(request)
    role = ctx.role
    unit = (role.unit if role is not None else profile.unit) or profile.unit
    # Membership is computed by the shared helper (split-aware) so the inbox and
    # the nav badge always agree; here we only add the per-unit ordering.
    cases = services.inbox_cases_for_request(request).select_related("client")

    # Inbox ordering for EVERY unit: the nearest deadline sits at the top; cases
    # without a deadline sink to the bottom, ordered by creation time.
    cases = cases.extra(select={"no_deadline": "deadline IS NULL"}).order_by(
        "no_deadline", "deadline", "created_at")
    scope = {Unit.SUPPLY: "supply", Unit.TECHNICAL: "technical",
             Unit.COMMERCIAL: "commercial"}.get(unit, "none")
    context_extra = {"scope": scope, "unit": unit}

    # No pagination: every inbox case is rendered once; the table scrolls via
    # .vscroll (same sticky-header pattern as inquiry / TO / PI tables).
    cases_list = list(cases)

    # Status summary, counted off the very rows this page is about to render.
    #
    # It used to be a grouped COUNT in the database over the whole-case
    # ``status`` column, chosen to avoid loading rows. But the rows are loaded
    # anyway — the line below renders every one of them — and that column is a
    # FOSSIL on a split case: the sides have moved on and it has not. So the
    # chips counted one thing and the pills beside them showed another, and the
    # two disagreed exactly where a case is split. That is how an inbox with no
    # Draft row could say "Draft: 1", and how a supply expert whose six cases
    # were all With Supply was told Draft / With Commercial / With Technical.
    #
    # Counting the rows costs no query at all — one fewer, in fact, since the
    # aggregate re-ran the whole membership filter — and no query per row
    # either: ``inbox_status_view`` only reads columns already on the loaded
    # case. A row contributes ONE to each DISTINCT status printed on it, which
    # is the contract in as many words: the number beside a status is the
    # number of visible rows carrying that status. A split row showing two
    # different side-pills is therefore counted under both — it really is
    # waiting in two places, and a reader who clicks either word must find it —
    # while a row whose two sides read the same is counted once, because it
    # only says one thing. The chips are emitted in the declared status order
    # so the strip does not reshuffle between loads.
    counts = {}
    for case in cases_list:
        view = services.inbox_status_view(case, ctx.seat_user)
        case.inbox_status_rows = view.rows
        # ``view.labels`` IS the de-duplicated list of what this row prints, so
        # the chip and the pill cannot be counted from two different readings.
        for label in view.labels:
            counts[label] = counts.get(label, 0) + 1
    summary = {label: counts[label]
               for label in dict.fromkeys(lbl for _code, lbl in CaseStatus.CHOICES)
               if label in counts}
    # Anything the declared list does not name (legacy or hand-edited data) is
    # still shown rather than silently dropped — the pill prints it, so the
    # summary has to count it.
    for label in counts:
        summary.setdefault(label, counts[label])

    # NEW marker: one query for the whole page (never one per row — see
    # services.annotate_inbox_seen). Purely additive: it only hangs an
    # ``is_new_in_inbox`` attribute on rows that were already loaded, so which
    # cases appear, in what order, and every count/summary above are unchanged.
    # Keyed on the seat user because that is whose inbox this is. Belt and braces
    # around the helper's own guard: a read receipt must never break the inbox.
    try:
        services.annotate_inbox_seen(ctx.seat_user, cases_list)
    except Exception:
        logger.exception("inbox: NEW marker lookup failed for user %s", request.user.pk)

    # Two levels of ordering: what this person has not opened yet comes first,
    # and inside each group the deadline order above still decides. Python's
    # sort is stable, so sorting on the unseen flag alone leaves every row's
    # position relative to its group-mates exactly as the database returned it —
    # nearest deadline first, no-deadline last, then creation time.
    #
    # It is done HERE, in Python, and not in the ORDER BY: the seen state lives
    # in a per-reader table that the inbox query does not join, and it has just
    # been resolved for the whole page by the ONE query above. Sorting the list
    # we already hold costs no query at all, while ordering on it in SQL would
    # mean joining that table back into a filter tree that is already the
    # slowest part of this page.
    #
    # A row only moves when the page is fetched again — opening a case marks it
    # read on the server, and the reader sees it drop on their NEXT load rather
    # than sliding out from under the cursor mid-page.
    cases_list.sort(key=lambda c: not getattr(c, "is_new_in_inbox", False))

    fx_stale = False
    is_manager = (
        (role.role if role is not None else profile.role) == Role.MANAGER
    )
    if is_manager and unit == Unit.COMMERCIAL:
        try:
            from .fx_rates import is_rates_stale
            fx_stale = is_rates_stale()
        except Exception:
            fx_stale = False

    from people.role_nav import role_can_create_case
    can_create = False
    if role is not None:
        can_create = role_can_create_case(role, is_substitute=ctx.is_substitute)
    elif not ctx.is_substitute:
        can_create = profile.can_create_case

    context = {
        "cases": cases_list,
        "summary": {k: v for k, v in summary.items() if v},
        "can_create": can_create,
        "fx_stale": fx_stale,
        "is_substitute": ctx.is_substitute,
        **context_extra,
    }
    return render(request, "cases/inbox.html", context)


# ---------------------------------------------------------------------------
# Case creation
# ---------------------------------------------------------------------------
@login_required
def archive(request):
    """Searchable history of cases.

    Commercial users (and admins) may search every case; Technical/Supply see
    the cases that have passed through their hands. Which cases that is — and
    the order they come in — is decided by :func:`cases.services.archive_scope`,
    the one place both this page and :func:`archive_slice` ask.

    The page renders only the FIRST WINDOW of rows (``services.ARCHIVE_WINDOW``,
    sized from the real row height and the tallest realistic list box). The rest
    arrive from ``archive_slice`` as the reader scrolls or filters, so neither
    the bytes nor the DOM nodes for three thousand rows are ever paid for a
    reader who looks at the first twenty.

    What is still worked out over EVERY match, not just the window: the filter
    dropdown option lists, the status tab counts, ``total_count``, and the money
    column plus the drill-down grand total.
    """
    profile = _profile(request.user)
    # Answered before archive_scope, which also says "no archive" for a unit
    # outside the TO/PI workflow but says it by returning None — and the None
    # the line below already handles means "not signed in", so a Marketing seat
    # would be bounced to the sign-in screen while signed in. Same refusal,
    # sent somewhere that makes sense. archive_slice keeps the plain 403.
    if profile is not None and profile.unit == Unit.MARKETING:
        return redirect("marketing:home")

    scope = services.archive_scope(request)
    if scope is None:
        return redirect("accounts:login")

    def _offer_label(offer_type, upgraded):
        if offer_type != OfferType.TO_PI:
            return "TO"
        return "TO & PI (Two Stage)" if upgraded else "TO & PI"

    cases_list = list(scope.qs)
    # Status pills first: the tab counts, the status filter and the pills a row
    # prints all come off the same call, so a row is filtered under exactly the
    # statuses a reader can see on it.
    #
    # The reader is passed in, and it is ``request.user`` — the very user the
    # case page resolves its own pills from (see ``case_detail``) — so the two
    # pages cannot tell one reader two different stories about the same case.
    services.archive_decorate(cases_list, request.user)

    # Filter dropdown choices — CASCADING: each field's list is built from the
    # rows every OTHER active filter leaves, not from the whole scope. Pick a
    # client with two matching rows that are both price_type Internal, and the
    # Price type dropdown offers only Internal — the same "leave one out"
    # faceting a shopping-site filter panel does. A field never narrows against
    # its OWN value (that would trap a reader who wants to switch it), and
    # WHICH tabs/values exist is never hidden — only reachable via ``services
    # .archive_apply_column_filters`` with that one field's own param removed,
    # so clearing a filter always restores every other field's full list.
    #
    # These were six ``values_list(...).distinct()`` round trips, written that way
    # back when this page was paginated. They still cost no query: ``cases_list``
    # is the whole scoped set already in memory (``client`` and ``created_by``
    # are select_related onto it), and each leave-one-out pass is a Python filter
    # over that same in-memory list — six more passes over rows already loaded,
    # not six more database round trips.
    #
    # ``created_by`` is nullable, so the empty-user tuple below stands in for the
    # LEFT JOIN's NULLs and is fed to the very same expression, keeping whatever
    # that expression did with them unchanged.
    params = services.archive_filter_params(
        request, unit=scope.unit, is_admin=scope.is_admin)

    def _cases_excluding(field):
        others = {k: v for k, v in params.items() if k != field}
        if not others:
            return cases_list
        return services.archive_apply_column_filters(cases_list, others)

    _no_user = ("", "", "")
    f_clients = sorted({
        f"{name} ({code})"
        for name, code in {(c.client.name, c.client.code) for c in _cases_excluding("client")}
        if name
    })
    f_experts = sorted({
        f"{((first + ' ' + last).strip() or username)} ({ecode})"
        for first, last, username, ecode in {
            ((c.created_by.first_name, c.created_by.last_name, c.created_by.username)
             if c.created_by_id else _no_user) + (c.expert_code,)
            for c in _cases_excluding("expert")
        }
    })
    f_prices = sorted({
        PriceType.LABELS.get(pt, "")
        for pt in {c.price_type for c in _cases_excluding("price")} if pt
    })
    f_offers = sorted({
        _offer_label(ot, up)
        for ot, up in {(c.offer_type, c.upgraded_two_stage) for c in _cases_excluding("offer")}
    })
    f_kinds = sorted({
        dict(DocKind.CHOICES).get(k, k)
        for k in {c.kind for c in _cases_excluding("kind")} if k
    })
    f_orders = sorted({
        ono for ono in {c.order_no for c in _cases_excluding("order")}
        if (ono or "").strip()
    })

    # Column filters: the same predicate the browser used to run over the
    # rendered table, run here instead — because a filter that can only see the
    # rows that were sent would silently hide the rest. (``params`` was already
    # read above, before the dropdown lists, so their leave-one-out passes and
    # this one row-window pass agree on exactly the same filter values.)

    # The status tabs are themselves a filter — they drive the hidden ``fstatus``
    # control — so they are applied LAST, over the set every other filter has
    # already narrowed. ``in_range`` is that set: the rows the date range and the
    # column filters leave, before a tab is chosen. Counting the tabs over it is
    # what makes them agree with the list underneath: with no tab chosen the two
    # sets are the same set, and with one chosen that tab's number is exactly the
    # rows on screen. Splitting the pass this way also costs nothing when no tab
    # is active — the second call has no terms and hands the list straight back.
    # WHICH tabs are on the strip still comes from the whole archive, and only
    # the numbers printed on them move with the filters — a tab that disappeared
    # the moment a date range emptied it would take the reader's way back out of
    # that range with it, and the hidden <select> is built from this same list,
    # so the tab a request has active must stay in it. Dropdown OPTION lists are
    # the opposite on purpose: those cascade (see the leave-one-out lists above),
    # because a search field narrowing to what is actually reachable is the
    # point of a search field, while the tab strip is navigation and must not
    # shift under the reader.
    in_range, filtered, filtered_count, status_tabs = services.archive_tab_counts(
        cases_list, params)

    # PI grand totals (VAT-inclusive) for every match + drill-down sum. Both stay
    # over the whole set: the banner's "Grand total (all PI)" means all of them,
    # and a row that scrolls in later must show the same figure it would have
    # shown on the first screen.
    drill_grand_total_display = ""
    if scope.show_money:
        from .export_data import format_money_amount
        gt_map = services.archive_attach_money(cases_list)
        if scope.drill_person:
            drill_sum = sum(gt_map.values()) if gt_map else 0.0
            drill_grand_total_display = (
                format_money_amount(drill_sum) if drill_sum else "—"
            )

    # ``?all=1`` renders every matching row in one page. It is the no-JavaScript
    # escape hatch (and what the "Show all N matching cases" link points at), and
    # it is exactly the page this view produced before windowing.
    show_all = str(request.GET.get("all") or "").strip() in ("1", "true", "yes")
    window = filtered if show_all else filtered[:services.ARCHIVE_WINDOW]

    # Everything the filter form must carry through a plain (no-JavaScript) GET
    # submit so the seat, the drill-down and the select flow survive it.
    carry_params = [
        (k, request.GET.get(k))
        for k in ("mine", "select", "return", "creator", "assignee",
                  "q", "status", "kind", "from", "to", "all")
        if (request.GET.get(k) or "").strip()
    ]
    # Echo back exactly what arrived, not the lower-cased text the predicate
    # compares: the form's <option> values and the date boxes carry the original
    # casing, and a re-rendered form has to select the option the reader picked.
    f_active = {
        param: (request.GET.get("f" + param) or "").strip()
        for param, _col, _mode, _seat in services.ARCHIVE_FILTERS
    }
    all_url = "%s?%s" % (
        reverse("cases:archive"),
        urlencode([(k, v) for k, v in request.GET.items() if k != "all"]
                  + [("all", "1")]))

    return render(request, "cases/archive.html", {
        "cases": window,
        # Whether to offer the "Marketing chart" button. Not ``is_admin``: a
        # person who holds a Marketing seat gets it too, even while sitting in
        # the Commercial seat this page belongs to — which is the whole point,
        # since that is the seat they are in when they want the chart. Asked of
        # the seat layer's full role list, not the active seat. Showing the
        # button is not the grant: marketing:home runs its own ``access_for``.
        "can_open_marketing_chart": _can_open_marketing_chart(request),
        "total_count": len(cases_list),
        # What the All tab prints: every row the filters leave with no status tab
        # chosen, which is precisely the list All shows when it is clicked.
        "all_tab_count": len(in_range),
        "filtered_count": filtered_count,
        "window_size": services.ARCHIVE_WINDOW,
        "next_offset": len(window),
        "has_more": filtered_count > len(window),
        "all_url": all_url,
        "carry_params": carry_params,
        "f_active": f_active,
        "f_active_json": {("f" + k): v for k, v in f_active.items() if v},
        "query": scope.query,
        "status": scope.status,
        "doc_kind": scope.doc_kind,
        "date_from": scope.date_from,
        "date_to": scope.date_to,
        "status_choices": CaseStatus.CHOICES,
        "kind_choices": DocKind.CHOICES,
        "unit": scope.unit,
        "is_admin": scope.is_admin,
        "show_expert_filter": scope.show_expert_filter,
        "viewer_unit": scope.profile.unit,
        "drill_person": scope.drill_person,
        "show_money": scope.show_money,
        "drill_grand_total_display": drill_grand_total_display,
        "f_clients": f_clients,
        "f_experts": f_experts,
        "f_prices": f_prices,
        "f_offers": f_offers,
        "f_kinds": f_kinds,
        "f_orders": f_orders,
        "status_tabs": status_tabs,
        "show_mine_toggle": scope.is_unit_manager,
        "mine_only": scope.mine_only,
        "mine_toggle_label": scope.mine_toggle_label,
        "select_mode": scope.select_mode,
        "select_return": scope.select_return,
    })


@never_cache
@login_required
def archive_slice(request):
    """One window of archive rows, rendered from the SAME partial as the page.

    ``offset`` / ``limit`` (limit capped at ``services.ARCHIVE_MAX_SLICE``; a
    request may not ask for an unbounded number of rows) plus the same ``f*``
    column filters the page understands. The queryset comes from
    ``services.archive_scope`` — the identical call the page makes — so what a
    seat may see cannot drift between the first screen and the fifth.

    Replies with the rendered rows and the counts in headers, so the HTML is not
    paid for twice over by JSON string escaping.
    """
    scope = services.archive_scope(request)
    if scope is None:
        return HttpResponse(status=403)

    def _int(name, default):
        try:
            return int(str(request.GET.get(name) or "").strip() or default)
        except (TypeError, ValueError):
            return default

    offset = max(0, _int("offset", 0))
    limit = max(1, min(_int("limit", services.ARCHIVE_WINDOW),
                       services.ARCHIVE_MAX_SLICE))

    params = services.archive_filter_params(
        request, unit=scope.unit, is_admin=scope.is_admin)
    # ``tabs=1`` additionally asks for the status-tab counts in a response
    # header — the live Document No. search (no page reload) needs the tab
    # strip to stay honest exactly the way a full reload keeps it honest. Only
    # that one caller sends it; a scroll-triggered "load more" never does, so
    # the extra decorate-the-whole-scope pass below never runs on the path
    # that fires on every scroll tick — this branch costs that path nothing,
    # unchanged from before ``tabs`` existed.
    want_tabs = str(request.GET.get("tabs") or "").strip() in ("1", "true", "yes")
    tabs_payload = None
    if want_tabs:
        cases_list = list(scope.qs)
        services.archive_decorate(cases_list, request.user)
        in_range, matched, total, status_tabs = services.archive_tab_counts(
            cases_list, params)
        window = matched[offset:offset + limit]
        tabs_payload = {"all": len(in_range), "tabs": status_tabs}
    elif params:
        # A column filter compares the text a cell PRINTS, so the rows have to
        # exist as objects before they can be judged; there is no SQL for
        # "the Jalali stamp this row would render". The status pills are only
        # worked out up front when the status column is actually being filtered,
        # because that is the one filter value that is derived rather than read.
        cases_list = list(scope.qs)
        if "status" in params:
            services.archive_decorate(cases_list, request.user)
        matched = services.archive_apply_column_filters(cases_list, params)
        total = len(matched)
        window = matched[offset:offset + limit]
    else:
        # Nothing to judge in Python: let the database do the window.
        total = scope.qs.count()
        window = list(scope.qs[offset:offset + limit])

    # Same reader as the first screen (and as the case page), so a row that
    # scrolls in later cannot be labelled differently from one already on it.
    services.archive_decorate(window, request.user)
    if scope.show_money:
        services.archive_attach_money(window)

    html = render_to_string("cases/_archive_rows.html", {
        "cases": window,
        "unit": scope.unit,
        "is_admin": scope.is_admin,
        "show_money": scope.show_money,
        "select_mode": scope.select_mode,
        "show_empty": False,
    }, request=request)
    response = HttpResponse(html)
    response["X-Archive-Total"] = str(total)
    response["X-Archive-Offset"] = str(offset)
    response["X-Archive-Count"] = str(len(window))
    response["X-Archive-Next-Offset"] = str(offset + len(window))
    response["X-Archive-Has-More"] = "1" if offset + len(window) < total else "0"
    if tabs_payload is not None:
        # ASCII-safe: a header value is Latin-1 in Django/WSGI, and a client or
        # order number can carry Persian text if a future tab label ever did.
        response["X-Archive-Tabs"] = json.dumps(tabs_payload, ensure_ascii=True)
    return response


# ---------------------------------------------------------------------------
# Case creation
# ---------------------------------------------------------------------------
@login_required
def case_create(request):
    profile = _profile(request.user)
    from people.role_nav import role_can_create_case, work_context
    ctx = work_context(request)
    can_create = False
    if ctx.role is not None:
        can_create = role_can_create_case(ctx.role, is_substitute=ctx.is_substitute)
    elif profile is not None and not ctx.is_substitute:
        can_create = profile.can_create_case
    if profile is None or not can_create:
        if ctx.is_substitute:
            messages.error(request, "Substitutes cannot open a new case. Return the seat first.")
        else:
            messages.error(request, "Only Commercial users can open a new case.")
        return redirect("cases:inbox")

    if request.method == "POST":
        form = CaseCreateForm(request.POST, request.FILES)
        if form.is_valid():
            rows = _parse_rows(form, request.FILES)
            if not rows:
                form.add_error(None, "Add at least one item to the inquiry table.")
            else:
                inq_errs = validate_inquiry_rows(rows)
                if inq_errs:
                    for err in inq_errs:
                        form.add_error(None, err)
                else:
                    try:
                        case = services.create_case(
                            creator=ctx.seat_user,
                            kind=form.cleaned_data["kind"],
                            offer_type=form.cleaned_data["offer_type"],
                            client=form.cleaned_data["client"],
                            order_no=form.cleaned_data.get("order_no", ""),
                            deadline=form.cleaned_data.get("deadline"),
                            price_type=form.cleaned_data.get("price_type", "INTERNAL"),
                            client_commercial_expert=form.cleaned_data.get("client_commercial_expert", ""),
                            client_commercial_phone=form.cleaned_data.get("client_commercial_phone", ""),
                            client_technical_expert=form.cleaned_data.get("client_technical_expert", ""),
                            client_technical_phone=form.cleaned_data.get("client_technical_phone", ""),
                            marketing_label=form.cleaned_data.get("marketing_label", ""),
                            rows=rows,
                        )
                    except ValueError as exc:
                        form.add_error(None, str(exc))
                    else:
                        messages.success(request, f"Case {case.doc_no} created.")
                        return redirect("cases:case_detail", pk=case.pk)
    else:
        form = CaseCreateForm()

    return render(request, "cases/case_create.html", {"form": form})


# ---------------------------------------------------------------------------
# Case detail
# ---------------------------------------------------------------------------
def _fmt_duration(seconds) -> str:
    """Human duration that never shows '0 days'.

    >= 1 day  -> 'Xd Yh'   (hours dropped when zero)
    >= 1 hour -> 'Xh Ym'   (minutes dropped when zero)
    else      -> 'Xm'
    """
    seconds = int(max(0, seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _case_lifecycle_report(case):
    """Lifecycle report(s) for a case (managers/admin only).

    Non-combined  -> a single card (Commercial / Technical / Supply).
    Combined       -> two cards, one per side (Internal / External), each a full
                      independent report for that side with its own people,
                      per-unit durations, item count and total.

    Timing rules:
    * Unit clocks (Commercial / Technical / Supply) accumulate while that unit
      holds the case. They pause while status is CLOSED (With Client) or
      FINAL_APPROVED — those phases have their own rows.
    * With Client runs only while status is CLOSED (from CLOSE until reopen /
      finalize / burn / final-close). A NEW_VERSION after sent-to-client stops
      the client clock; it must not keep ticking after the summary changes.
    * Final Approved runs only while status is FINAL_APPROVED.
    """
    from collections import defaultdict
    from django.utils import timezone
    now = timezone.now()
    events = list(case.events.order_by("created_at", "id"))

    # Statuses whose time is reported on dedicated rows, not on unit clocks.
    UNIT_CLOCK_PAUSE = {CaseStatus.CLOSED, CaseStatus.FINAL_APPROVED}

    # Sent-to-client ends when the case is reopened (new version), finalized,
    # burned, final-closed, or cancelled — not only on finalize/burn.
    CLIENT_END = {
        EventAction.NEW_VERSION,
        EventAction.FINALIZE,
        EventAction.BURN,
        EventAction.FINAL_CLOSE,
        EventAction.CANCEL,
        EventAction.APPROVE_CANCEL,
    }
    FINAL_END = {
        EventAction.FINAL_CLOSE,
        EventAction.BURN,
        EventAction.NEW_VERSION,
        EventAction.CANCEL,
        EventAction.APPROVE_CANCEL,
    }

    def _status_of(side):
        if side is None:
            return case.status
        return case.side_status(side)

    def unit_seconds(side=None):
        evs = events if side is None else [e for e in events if (e.side or "") in (side, "")]
        secs = defaultdict(float)
        # "client" / "final" phases are timed on their own rows — skip them here.
        phase = None
        for i in range(len(evs) - 1):
            e = evs[i]
            if e.action == EventAction.CLOSE:
                phase = "client"
            elif e.action == EventAction.FINALIZE:
                phase = "final"
            elif phase == "client" and e.action in CLIENT_END:
                phase = None
            elif phase == "final" and e.action in FINAL_END:
                phase = None

            if phase is not None:
                continue
            holder = e.to_unit or e.from_unit
            if holder:
                secs[holder] += (evs[i + 1].created_at - e.created_at).total_seconds()

        if evs:
            cur_status = _status_of(side)
            is_terminal = cur_status in CaseStatus.TERMINAL
            # Live unit clock only while actively with a unit (yellow/blue/etc.).
            if (not is_terminal) and cur_status not in UNIT_CLOCK_PAUSE:
                if side is None:
                    cur_holder = case.holder_unit
                else:
                    cur_holder = case.side_holder(side)
                holder = evs[-1].to_unit or evs[-1].from_unit or cur_holder
                if holder:
                    secs[holder] += (now - evs[-1].created_at).total_seconds()
        return secs

    def author(kind, side=None):
        qs = case.forms.filter(kind=kind)
        if side is not None:
            qs = qs.filter(side=side)
        f = qs.order_by("version", "id").first()
        if f and f.created_by:
            return f.created_by.get_full_name() or f.created_by.username
        return "—"

    creator = (case.created_by.get_full_name() or case.created_by.username) if case.created_by else "—"
    item_count = case.line_items.count()

    def phase_seconds(side, start_actions, end_actions, *, live_status):
        """Sum every start→end interval; keep ticking only while live_status."""
        evs = events if side is None else [e for e in events if (e.side or "") in (side, "")]
        total = 0.0
        start_at = None
        for e in evs:
            if start_at is None and e.action in start_actions:
                start_at = e.created_at
            elif start_at is not None and e.action in end_actions:
                total += (e.created_at - start_at).total_seconds()
                start_at = None
        if start_at is not None and _status_of(side) == live_status:
            total += (now - start_at).total_seconds()
        return total

    def make_card(side, label):
        secs = unit_seconds(side)
        cur_status = _status_of(side)
        is_terminal = cur_status in CaseStatus.TERMINAL
        if side is None:
            end_time = events[-1].created_at if (is_terminal and events) else now
        else:
            side_evs = [e for e in events if (e.side or "") in (side, "")]
            end_time = side_evs[-1].created_at if (is_terminal and side_evs) else now
        total_seconds = (end_time - case.created_at).total_seconds() if events else 0

        with_client = phase_seconds(
            side, {EventAction.CLOSE}, CLIENT_END, live_status=CaseStatus.CLOSED)
        final_appr = phase_seconds(
            side, {EventAction.FINALIZE}, FINAL_END, live_status=CaseStatus.FINAL_APPROVED)

        rows = [
            {"unit": "COMMERCIAL", "label": "Commercial", "person": creator,
             "duration": _fmt_duration(secs.get(Unit.COMMERCIAL, 0))},
            {"unit": "TECHNICAL", "label": "Technical", "person": author(FormKind.TO, side),
             "duration": _fmt_duration(secs.get(Unit.TECHNICAL, 0))},
            {"unit": "SUPPLY", "label": "Supply", "person": author(FormKind.PI, side),
             "duration": _fmt_duration(secs.get(Unit.SUPPLY, 0))},
            {"unit": "CLIENT", "label": "With Client", "person": "—",
             "duration": _fmt_duration(with_client)},
            {"unit": "FINAL", "label": "Final Approved", "person": "—",
             "duration": _fmt_duration(final_appr)},
        ]
        return {"side_label": label, "item_count": item_count, "created_at": case.created_at,
                "rows": rows, "total": _fmt_duration(total_seconds), "is_terminal": is_terminal}

    if case.is_split:
        cards = [make_card(Side.INTERNAL, "Internal"), make_card(Side.EXTERNAL, "External")]
    else:
        cards = [make_card(None, "")]
    return {"cards": cards}


def _side_notes(events_recent, side_code, holder_unit):
    """Return (arrival_comment, assign_comment) for one side of a split case.

    Same idea as the case-level notes, but restricted to events tagged with this
    side (legacy untagged events count for both).
    """
    side_events = [e for e in events_recent if (e.side or "") in (side_code, "")]
    arrival = None
    handoff = None
    for e in side_events:
        if e.to_unit == holder_unit and e.from_unit and e.from_unit != e.to_unit:
            handoff = e
            break
    if handoff is not None:
        if handoff.comment:
            arrival = handoff
        else:
            sending = handoff.from_unit
            for e in side_events:
                if (e.comment and e.from_unit == sending
                        and e.created_at <= handoff.created_at
                        and e.action != EventAction.ASSIGN):
                    arrival = e
                    break
    assign = None
    for e in side_events:
        if e.action == EventAction.ASSIGN and e.comment and e.to_unit == holder_unit:
            assign = e
            break
    return arrival, assign


def _form_rank(form):
    """Ascending rank of one form snapshot — the single ordering rule.

    Delegates to ``CaseForm.version_key`` so the version chips, the "which
    snapshot is current" decisions and the export resolver all agree. Sorting
    or ``max()``-ing by ``version`` alone cannot separate a two-stage snapshot
    from the same-numbered version it supersedes (both are, say, version 1),
    which is what used to leave that ordering up to the database.
    """
    return form.version_key


@login_required
def case_detail(request, pk):
    case = (Case.objects
            .select_related("client", "created_by", "assigned_to")
            # Pull every form and event once; current_form()/services read these
            # from the prefetch cache, turning dozens of per-form queries into two.
            .prefetch_related(
                Prefetch("forms", queryset=CaseForm.objects.select_related("created_by")),
                Prefetch("events",
                         queryset=CaseEvent.objects.select_related("actor", "actor__profile")))
            .filter(pk=pk)
            .first())
    if case is None:
        # The case isn't in the database (e.g. a stale/bookmarked URL pointing at
        # a row that no longer exists). Send the user to their inbox with a short
        # note instead of showing a raw 404 page.
        messages.info(request, f"Case #{pk} no longer exists.")
        return redirect("cases:inbox")
    profile = _profile(request.user)
    from people.role_nav import work_context
    ctx = work_context(request)
    role = ctx.role
    seat_user = ctx.seat_user
    seat_id = getattr(seat_user, "id", None)
    # Active PersonRole drives unit/role for secondary seats (login profile stays
    # on the primary seat and must NOT scope forms / timeline / action chrome).
    if role is not None:
        viewer_unit = (role.unit or "").strip() or (profile.unit if profile else None)
        viewer_role = (role.role or "").strip() or (profile.role if profile else None)
        is_admin_view = bool(
            role.is_admin or role.is_general_manager
            or (profile and (profile.is_admin or profile.is_general_manager))
        )
    else:
        viewer_unit = profile.unit if profile else None
        viewer_role = profile.role if profile else None
        is_admin_view = bool(profile and (profile.is_admin or profile.is_general_manager))
    is_supply = viewer_unit == Unit.SUPPLY
    is_tech = viewer_unit == Unit.TECHNICAL
    is_comm = viewer_unit == Unit.COMMERCIAL
    is_mgr = viewer_role == Role.MANAGER

    actions = (
        services.allowed_actions(
            case, request.user, role=role, work_user=seat_user,
        ) if profile else set()
    )

    # In-memory views of the prefetched forms/events (no extra queries). All the
    # repeated filtering below runs over these lists instead of hitting the DB.
    case_forms = list(case.forms.all())
    case_events = list(case.events.all())

    def _forms_of(kind, side=None, sent=None):
        out = [f for f in case_forms if f.kind == kind]
        if side is not None:
            out = [f for f in out if f.side == side]
        if sent is not None:
            out = [f for f in out if bool(f.sent) == sent]
        return out

    # Admins may always inspect a case read-only. Everyone else may view a case
    # they participated in (created, were assigned, authored a form, or acted on),
    # plus Commercial who can view any case, plus a unit MANAGER who can always
    # see cases that belong to (or passed through) their own unit. The actual
    # rule lives in services.user_can_view_case (shared with the export routes
    # below, which used to skip this check entirely).
    if not services.user_can_view_case(case, request.user,
                                        case_forms=case_forms, case_events=case_events,
                                        role=role, work_user=seat_user):
        messages.error(request, "You do not have access to this case.")
        return redirect("cases:inbox")

    # The viewer has now actually opened this case, so drop its inbox NEW badge
    # for them. Deliberately placed *after* the access check, so somebody who was
    # bounced off the page never records a read. The helper writes nothing but
    # the CaseSeen row (no CaseEvent, no save on the case, nothing on the audit
    # timeline) and swallows its own errors, so this line cannot fail the page.
    services.mark_case_seen(case, seat_user or request.user)

    def _hide_fx_only(forms):
        """Technical/Supply never see Commercial-only currency-conversion snapshots.

        Those versions exist for Commercial Proforma FX only and must not appear
        as TO/PI/Inquiry chips for Technical or Supply (they keep their prior
        real versions; the next real revision is e.g. 04, skipping FX-only 03).
        """
        if is_admin_view or is_comm:
            return forms
        return [f for f in forms if not services.form_is_currency_conversion_only(f)]

    def _display_current(kind, side=None):
        """Current form for UI; Technical/Supply skip FX-only Commercial clones."""
        cur = case.current_form(kind, side)
        if cur and services.form_is_currency_conversion_only(cur):
            if not (is_admin_view or is_comm):
                reals = _hide_fx_only(_forms_of(kind, side=side))
                cur = max(reals, key=_form_rank, default=None) if reals else None
        return cur

    forms_by_kind = {
        FormKind.INQUIRY: _display_current(FormKind.INQUIRY),
        FormKind.TO: _display_current(FormKind.TO),
        FormKind.PI: _display_current(FormKind.PI),
    }
    # Each unit sees every version of the form it owns and only the latest
    # version published *to them* for the others; admins/GMs see every version.
    owner_unit = {FormKind.INQUIRY: Unit.COMMERCIAL,
                  FormKind.TO: Unit.TECHNICAL,
                  FormKind.PI: Unit.SUPPLY}

    # Left → right: older versions first, and within one version number the
    # original before its two-stage successor — so ``Version 01`` sits left of
    # ``Version 01 · Two Stage`` and the newest snapshot is always the rightmost
    # chip. This used to lean on ``created_at``, which gave the right answer
    # only because the two-stage row happens to be written later; the rank is
    # now stated as a rule (see CaseForm.version_key) instead of inferred from
    # a timestamp.
    _form_chip_sort_key = _form_rank

    def _forms_published_to_viewer(kind, side=None):
        """Non-owner: latest form version handed to this viewer's unit."""
        forms = _hide_fx_only(_forms_of(kind, side=side))
        visible = [
            f for f in forms
            if services.form_published_to_unit(f, viewer_unit)
        ]
        visible = sorted(visible, key=_form_chip_sort_key)
        return visible[-1:] if visible else []

    version_lists = {}
    for kind in (FormKind.INQUIRY, FormKind.TO, FormKind.PI):
        if is_admin_view or viewer_unit == owner_unit[kind]:
            # Owning unit (incl. its manager) and admin/GM see every version
            # — except Technical/Supply never see FX-only Commercial clones.
            version_lists[kind] = sorted(
                _hide_fx_only(_forms_of(kind)), key=_form_chip_sort_key)
        else:
            version_lists[kind] = _forms_published_to_viewer(kind)
    versions = {
        FormKind.TO: sorted(_hide_fx_only(_forms_of(FormKind.TO)),
                            key=_form_chip_sort_key, reverse=True),
        FormKind.PI: sorted(_hide_fx_only(_forms_of(FormKind.PI)),
                            key=_form_chip_sort_key, reverse=True),
    }

    # Possible assignees when the manager wants to delegate the case.
    assignees = []
    if "assign" in actions:
        assignees = User.objects.filter(
            profile__unit=case.holder_unit,
            profile__role=Role.EXPERT,
            is_active=True,
        ).select_related("profile").order_by("first_name", "username")

    # Timeline scoping: every unit reads the whole history of the case, so they
    # can see what happened to it before and after their own desk. The only entry
    # still scoped narrowly is the TO/PI form-edit record — see
    # _event_visible_to. Admins see everything, unfiltered.
    all_events = case_events
    if is_admin_view:
        events = list(all_events)
    else:
        events = [e for e in all_events if _event_visible_to(e, viewer_unit)]

    # The handoff note shown in the action panel: the last thing the unit that
    # sent the case here said. Either the comment attached to the send action,
    # or — if they commented separately and then sent — that unit's most recent
    # comment made at/just before the handoff. Shown in the sending unit's colour.
    events_recent = sorted(
        all_events, key=lambda e: (e.created_at, e.id), reverse=True
    )
    arrival_comment = None
    handoff = None
    for e in events_recent:
        if e.to_unit == case.holder_unit and e.from_unit and e.from_unit != e.to_unit:
            handoff = e
            break
    if handoff is not None:
        if handoff.comment:
            arrival_comment = handoff
        else:
            sending_unit = handoff.from_unit
            for e in events_recent:
                if (e.comment and e.from_unit == sending_unit
                        and e.created_at <= handoff.created_at
                        and e.action != EventAction.ASSIGN):
                    arrival_comment = e
                    break

    # The latest assignment note made WITHIN the current holder unit (so a
    # previous unit's manager note never leaks to the next unit).
    assign_comment = None
    for e in events_recent:
        if (e.action == EventAction.ASSIGN and e.comment
                and e.to_unit == case.holder_unit):
            assign_comment = e
            break

    # The technical expert who first built the TO (shown in case information).
    to_author = None
    first_to = min(
        (f for f in _forms_of(FormKind.TO) if not services.form_is_currency_conversion_only(f)),
        key=_form_rank, default=None)
    if first_to:
        to_author = first_to.created_by

    # Tool mode for the Build buttons: build a first form, edit the current
    # (still-unsent) form, or branch a new version once it has been sent.
    def _form_mode(kind, side=None):
        cur = case.current_form(kind, side)
        # Commercial FX-only clones are not Technical/Supply work — treat the
        # latest real snapshot as current for mode decisions.
        if cur and services.form_is_currency_conversion_only(cur):
            reals = [
                f for f in _forms_of(kind, side=side)
                if not services.form_is_currency_conversion_only(f)
            ]
            cur = max(reals, key=_form_rank, default=None) if reals else None
        if not cur:
            return "build"
        inq = case.current_form(FormKind.INQUIRY, side)
        # A new inquiry version (beyond this form's version) — OR a new two-stage
        # generation at the SAME number (the case was upgraded to TO & PI two
        # stage) — forces a matching new form version. Otherwise the unit edits
        # its current form in place — even after sending it and getting the case
        # back, as long as the inquiry has not moved on.
        if services.form_behind_inquiry(cur, inq):
            return "newversion"
        return "edit"
    to_mode = _form_mode(FormKind.TO)
    pi_mode = _form_mode(FormKind.PI)

    # ---- Per-side bundles (Internal / External sub-streams) -----------
    side_codes = case.sides or [""]
    supply_assignee_of = {
        Side.INTERNAL: case.supply_internal_assignee_id,
        Side.EXTERNAL: case.supply_external_assignee_id,
        "": case.supply_assignee_id,
    }
    is_supply = viewer_unit == Unit.SUPPLY
    is_mgr = viewer_role == Role.MANAGER
    is_tech = viewer_unit == Unit.TECHNICAL
    is_comm = viewer_unit == Unit.COMMERCIAL
    tech_owns = bool(is_mgr or case.technical_assignee_id == seat_id)
    # Editing items re-snapshots every side's inquiry, so it must stop once any
    # side's inquiry has been sent (otherwise a sent side would be overwritten).
    inq_any_sent = bool(_forms_of(FormKind.INQUIRY, sent=True))
    sides_data = []
    for sc in side_codes:
        s_forms = {
            FormKind.INQUIRY: _display_current(FormKind.INQUIRY, sc),
            FormKind.TO: _display_current(FormKind.TO, sc),
            FormKind.PI: _display_current(FormKind.PI, sc),
        }
        s_vlists = {}
        for kind in (FormKind.INQUIRY, FormKind.TO, FormKind.PI):
            if is_admin_view or viewer_unit == owner_unit[kind]:
                # Owning unit (incl. its manager) and admin/GM see every version
                # — except Technical/Supply never see FX-only Commercial clones.
                s_vlists[kind] = sorted(
                    _hide_fx_only(_forms_of(kind, side=sc)), key=_form_chip_sort_key)
            else:
                s_vlists[kind] = _forms_published_to_viewer(kind, side=sc)
        # Per-side supply permission. For split cases each side is independent:
        # a user acts on a side only while that side is still at Supply and they
        # own it (or, for an un-delegated side, they are the supply manager).
        s_assignee = supply_assignee_of.get(sc)
        s_has_pi = bool(_hide_fx_only(_forms_of(FormKind.PI, side=sc)))
        s_has_to = bool(_hide_fx_only(_forms_of(FormKind.TO, side=sc)))
        side_holder = case.side_holder(sc)
        side_status = case.side_status(sc)
        side_terminal = side_status in CaseStatus.TERMINAL
        side_at_supply = (side_holder == Unit.SUPPLY)
        side_at_tech = (side_holder == Unit.TECHNICAL)
        # A side that has been sent to the client (CLOSED) or marked final
        # (FINAL_APPROVED) is no longer "at Commercial" for ordinary work actions
        # (submit / return / send-to-client). Only Final Approved / Burned /
        # Final Closed remain, and those are gated separately below.
        side_closed_like = side_status in (CaseStatus.CLOSED, CaseStatus.FINAL_APPROVED)
        side_at_comm = (side_holder == Unit.COMMERCIAL
                        and not side_terminal and not side_closed_like)
        tech_assignee_side = (case.technical_internal_assignee_id if sc == Side.INTERNAL
                              else case.technical_external_assignee_id if sc == Side.EXTERNAL else None)
        if case.is_split:
            owns = services.can_act_on_side(
                case, request.user, sc, role=role, work_user=seat_user)
            # Supply
            can_pi_side = is_supply and owns and side_at_supply
            can_assign_side = (is_supply and is_mgr and not s_assignee
                               and side_at_supply and not s_has_pi)
            # WHY the Build / Edit / New version control is missing here.
            #
            # The page has always simply drawn NOTHING when this seat may not
            # act on this side. So a Supply manager who had delegated a side to
            # an expert opened the Proforma tab, found an empty control row and
            # no statement of the rule that emptied it — and, on a page that had
            # been loaded BEFORE the delegation, still saw a live "New version"
            # which the tool then refused with "You can only work on your own
            # side of this case." Neither the missing button nor the refusal
            # said why.
            #
            # These two sentences are derived from ``owns`` — the SAME predicate
            # that hides the control here and the same one ``tool_for_case``
            # refuses on — so a sentence can only ever appear exactly where the
            # control is absent, and never beside one. They are text: no action
            # is added, no permission is widened, and nothing below reads them.
            pi_locked_reason = ""
            if is_supply and side_at_supply and not owns:
                _pi_who = (case.supply_internal_assignee if sc == Side.INTERNAL
                           else case.supply_external_assignee
                           if sc == Side.EXTERNAL else None)
                if _pi_who is not None:
                    pi_locked_reason = (
                        f"Assigned to {_pi_who.get_full_name() or _pi_who.username}. "
                        "Once a side is assigned to a Supply expert, only that "
                        "expert can build or revise its Proforma.")
                else:
                    pi_locked_reason = (
                        "Only the Supply manager can work a side that has no "
                        "expert assigned yet.")
            to_locked_reason = ""
            if is_tech and side_at_tech and not owns:
                _to_who = case.technical_assignee
                if _to_who is not None:
                    to_locked_reason = (
                        f"Assigned to {_to_who.get_full_name() or _to_who.username}. "
                        "Once a case is assigned to a Technical expert, only that "
                        "expert can build or revise its Technical Offer.")
                else:
                    to_locked_reason = (
                        "Only the Technical manager can work a case that has no "
                        "expert assigned yet.")
            # Per-side PI-remark block: this side's current PI carrying any
            # filled remark cannot be forwarded to commercial. Return-to-technical
            # stays available so supply can hand it back.
            side_pi_blocked = services._pi_blocks_commercial(case, sc)
            can_send_side = (is_supply and owns and side_at_supply and s_has_pi
                             and not side_pi_blocked)
            can_return_side = is_supply and owns and side_at_supply
            can_cannot_side = is_supply and owns and side_at_supply
            # Technical
            can_build_to_side = is_tech and owns and side_at_tech
            # Direction: a side that is RETURNED_TO_TECHNICAL came back from
            # Supply (so Technical forwards to Commercial and returns to Supply);
            # a side that is WITH_TECHNICAL came from Commercial (so Technical
            # forwards to Supply and returns to Commercial). This mirrors the
            # non-split rule so each side behaves exactly like a standalone case.
            side_from_supply = (side_status == CaseStatus.RETURNED_TO_TECHNICAL)
            side_to_blocked = services._to_blocks_supply(case, sc)
            side_to_blocked_issues = services._to_has_technical_problems(case, sc)
            side_to_blocked_brand = bool(services._to_rows_without_brand(case, sc))
            side_pi_blocked_tech = services._pi_blocks_commercial(case, sc)
            if side_from_supply:
                # Forward target = Commercial (blocked while PI has any remark,
                # since Technical can't edit the remark — return to Supply instead).
                can_send_supply_side = False
                can_submit_comm_side = (is_tech and owns and side_at_tech and s_has_to
                                        and not side_pi_blocked_tech)
                can_return_comm_side = False
                can_return_supply_from_tech = (is_tech and owns and side_at_tech
                                               and not side_to_blocked)
            else:
                # Forward target = Supply (blocked while any TO problem flag set).
                can_send_supply_side = (is_tech and owns and side_at_tech and s_has_to
                                        and not side_to_blocked)
                can_submit_comm_side = False
                can_return_comm_side = is_tech and owns and side_at_tech
                can_return_supply_from_tech = False
            can_tech_assign_side = False  # technical uses ONE assign (see below)
            # Commercial
            # Send-to-client for a side: TO (+ PI when needed) at the inquiry
            # version, OR a Technical-Problem TO (no Proforma required).
            can_close_side = (is_comm and owns and side_at_comm
                              and side_status != CaseStatus.UNSUPPLIABLE
                              and services._can_send_to_client(case, sc))
            # A FINAL_APPROVED side is deliberately not "at Commercial" for
            # routing (see side_at_comm above), but it does still take the cancel
            # request — same rule the whole-case panel now applies to a
            # final-approved case, and the same one can_do_side_action enforces.
            can_cancel_side = (is_comm and owns
                               and (side_at_comm
                                    or side_status == CaseStatus.FINAL_APPROVED)
                               and side_status != CaseStatus.PENDING_CANCEL)
            currency_only = services.is_currency_conversion_only(case, sc)
            # Currency-conversion-only reopen: no routing to Technical / Supply.
            can_submit_tech_side = (is_comm and owns and side_at_comm
                                    and not currency_only
                                    and side_status != CaseStatus.PENDING_CANCEL)
            # Return to Supply only when this side arrived FROM Supply.
            can_return_supply_side = (is_comm and owns and side_at_comm and s_has_pi
                                      and side_status == CaseStatus.WITH_COMMERCIAL
                                      and not currency_only)
            can_finalize_side = (is_comm and owns
                                 and case.side_status(sc) == CaseStatus.CLOSED)
            can_final_close_side = (is_comm and owns
                                    and case.side_status(sc) == CaseStatus.FINAL_APPROVED)
            can_burn_side = (is_comm and owns
                             and case.side_status(sc) in (CaseStatus.CLOSED,
                                                          CaseStatus.FINAL_APPROVED))
            can_approve_cancel_side = (
                bool(is_comm and is_mgr)
                and side_status == CaseStatus.PENDING_CANCEL
            )
        else:
            can_pi_side = is_supply and ("build_pi" in actions) and (
                s_assignee == seat_id or (is_mgr and not s_assignee))
            can_assign_side = (is_supply and is_mgr and ("assign" in actions)
                               and not s_assignee and not s_has_pi)
            can_send_side = can_return_side = can_cannot_side = False
            can_build_to_side = ("build_to" in actions)
            can_submit_comm_side = False
            can_return_supply_from_tech = False
            can_send_supply_side = False
            can_return_comm_side = can_tech_assign_side = False
            can_submit_tech_side = can_close_side = can_cancel_side = False
            can_return_supply_side = False
            can_finalize_side = False
            can_final_close_side = False
            can_burn_side = False
            can_approve_cancel_side = False
            # Non-split cases never hit the per-side gate, so there is nothing
            # to explain: the seat that may not write still gets the read-only
            # "View" control the panel draws below.
            pi_locked_reason = to_locked_reason = ""
        inq_cur = s_forms[FormKind.INQUIRY]
        inq_sent = bool(inq_cur and inq_cur.sent)
        if case.is_split:
            can_edit_inq = is_comm and owns and side_at_comm and not inq_sent
            # A new inquiry version for a side is allowed once that side has been
            # sent to the client (CLOSED), or for the freshly-converted two-stage
            # side (it carries a copied inquiry to revise via New version).
            can_new_inq = services.can_new_inquiry_version(
                case, request.user, sc, role=role, work_user=seat_user)
        else:
            can_edit_inq = ("edit" in actions)
            can_new_inq = ("new_inquiry_version" in actions)
        s_events = [e for e in events if (e.side == sc or not e.side)]
        s_arrival, s_assign = _side_notes(events_recent, sc, side_holder)
        sides_data.append({
            "code": sc,
            "label": Side.LABELS.get(sc, "Items"),
            "forms": s_forms,
            "version_lists": s_vlists,
            "to_mode": _form_mode(FormKind.TO, sc),
            "pi_mode": _form_mode(FormKind.PI, sc),
            "can_pi": can_pi_side,
            "pi_locked_reason": pi_locked_reason,
            "to_locked_reason": to_locked_reason,
            "can_assign": can_assign_side,
            "can_send": can_send_side,
            "can_return": can_return_side,
            "can_cannot": can_cannot_side,
            "can_build_to": can_build_to_side,
            "can_send_supply": can_send_supply_side,
            # Only when this side would forward to Supply (not returned-from-Supply).
            "to_blocked": (case.is_split and is_tech and owns and side_at_tech
                           and s_has_to and not side_from_supply
                           and services._to_blocks_supply(case, sc)),
            "to_blocked_issues": (case.is_split and is_tech and owns and side_at_tech
                                  and s_has_to and not side_from_supply
                                  and side_to_blocked_issues),
            "to_blocked_brand": (case.is_split and is_tech and owns and side_at_tech
                                 and s_has_to and not side_from_supply
                                 and side_to_blocked_brand),
            "pi_blocked": (case.is_split and s_has_pi
                           and services._pi_blocks_commercial(case, sc)
                           and ((is_supply and owns and side_at_supply)
                                or (is_tech and owns and side_at_tech
                                    and side_status == CaseStatus.RETURNED_TO_TECHNICAL))),
            "can_return_comm": can_return_comm_side,
            "can_submit_comm": can_submit_comm_side,
            "can_return_supply_from_tech": can_return_supply_from_tech,
            "can_tech_assign": can_tech_assign_side,
            "can_submit_tech": can_submit_tech_side,
            "can_close": can_close_side,
            "can_cancel": can_cancel_side,
            "can_approve_cancel": can_approve_cancel_side,
            "can_return_supply": can_return_supply_side,
            "can_finalize": can_finalize_side,
            "can_final_close": can_final_close_side,
            "can_burn": can_burn_side,
            "can_edit_inq": can_edit_inq,
            "can_new_inq": can_new_inq,
            "inq_sent": inq_sent,
            "tech_assignee": (case.technical_internal_assignee if sc == Side.INTERNAL
                              else case.technical_external_assignee if sc == Side.EXTERNAL else None),
            "at_supply": side_at_supply,
            "at_tech": side_at_tech,
            "at_comm": side_at_comm,
            "side_status_label": CaseStatus.LABELS.get(case.side_status(sc), case.side_status(sc)),
            "side_status_color": CaseStatus.COLORS.get(case.side_status(sc), "#6b7280"),
            "side_holder": side_holder,
            "assignee": (case.supply_internal_assignee if sc == Side.INTERNAL
                         else case.supply_external_assignee if sc == Side.EXTERNAL else None),
            "events": s_events,
            "arrival_comment": s_arrival,
            "assign_comment": s_assign,
        })

    # Per-side supply expert pools for the assign dropdowns.
    from accounts.constants import SupplyKind
    pool_internal, pool_external = [], []
    if is_supply and is_mgr:
        pool_internal = list(User.objects.filter(
            profile__unit=Unit.SUPPLY, profile__role=Role.EXPERT,
            profile__supply_kind=SupplyKind.INTERNAL, is_active=True).select_related("profile"))
        pool_external = list(User.objects.filter(
            profile__unit=Unit.SUPPLY, profile__role=Role.EXPERT,
            profile__supply_kind=SupplyKind.EXTERNAL, is_active=True).select_related("profile"))
    tech_pool = []
    if is_tech and is_mgr:
        tech_pool = list(User.objects.filter(
            profile__unit=Unit.TECHNICAL, profile__role=Role.EXPERT,
            is_active=True).select_related("profile"))
    for sd in sides_data:
        sd["pool"] = pool_internal if sd["code"] == Side.INTERNAL else (
            pool_external if sd["code"] == Side.EXTERNAL else [])
        sd["tech_pool"] = tech_pool

    # Technical split case: ONE assign control for both sides. It disappears
    # once an expert is assigned OR the manager has built a TO for either side.
    tech_split_can_assign = bool(
        is_tech and is_mgr and case.is_split
        and (case.side_holder(Side.INTERNAL) == Unit.TECHNICAL
             or case.side_holder(Side.EXTERNAL) == Unit.TECHNICAL)
        and not case.technical_assignee_id
        and not _forms_of(FormKind.TO))

    # Strict per-side visibility: a supply EXPERT only ever sees the side they
    # were assigned (never the other side, never the combined timeline).
    hide_combined = False
    if is_supply and not is_mgr:
        my_codes = []
        if case.supply_internal_assignee_id == seat_id:
            my_codes.append(Side.INTERNAL)
        if case.supply_external_assignee_id == seat_id:
            my_codes.append(Side.EXTERNAL)
        if my_codes:
            sides_data = [sd for sd in sides_data if sd["code"] in my_codes]
        hide_combined = True
    multi_side = len(sides_data) > 1

    # Full case-information editing is only meaningful on a never-submitted
    # draft. The same question decides whether ``edit_items`` will actually SERVE
    # that screen, so both ask services.case_is_fresh_draft rather than each
    # keeping its own copy — see the note on that function for what went wrong
    # when they drifted.
    is_fresh_draft = services.case_is_fresh_draft(case)

    from django.conf import settings as _dj_settings

    # The status this page prints, and whether it is printing per-side statuses,
    # both come out of the one call. ``is_split`` below still gates the ACTION
    # buttons — that is a different question with a different answer, and it is
    # unchanged — but it no longer decides what the status pills say: it used to,
    # and on a ``split_active`` case with a one-sided price type it disagreed
    # with the inbox and the Archive, which read the same case off its sides.
    status = services.detail_status_view(case, request.user)

    # Reports / Reminders / a marketing-style Timeline for THIS case — see
    # the three helper functions above (``_case_marketing_reports`` and
    # neighbours) for what each pulls in and why, and ``services.
    # is_case_commercial_owner`` for the write-permission rule: holding a
    # COMMERCIAL role AND being THIS case's own creator, checked once here and
    # re-checked independently by ``case_report_add``/``case_reminder_add``
    # themselves (a hidden button is never the permission).
    is_case_owner = services.is_case_commercial_owner(case, request)
    case_reports = _case_marketing_reports(case, request)
    case_reminders = _case_marketing_reminders(
        case, request, see_every_owner=is_admin_view)
    case_mkt_timeline = _case_marketing_timeline(case, request)
    # Unbound forms for the inline "write a report" / "set a reminder"
    # panels — built only for the viewer who may actually submit them, so a
    # request that never renders these forms never pays for the extra
    # queries the option/case-choice fields would otherwise run. Reused
    # straight from ``marketing/forms.py`` rather than re-declared: the "at
    # least one option or some text" report rule and the Jalali due-date
    # parsing are validation this app already has exactly once, and a second
    # copy here would be free to drift from it. Neither form is asked for a
    # case picker — this page's own case is the only one either can ever be
    # filed against, so ``case_report_add``/``case_reminder_add`` write
    # ``case=case`` unconditionally and never read whatever (if anything)
    # a submitted ``case_id`` says.
    case_report_form = None
    case_reminder_form = None
    if is_case_owner:
        try:
            from marketing.forms import ReportForm, ReminderForm
            case_report_form = ReportForm(can_manage_options=False)
            case_reminder_form = ReminderForm(case_choices=())
        except Exception:
            pass

    context = {
        "case": case,
        "actions": actions,
        "line_items": case.line_items.all(),
        "forms_by_kind": forms_by_kind,
        "versions": versions,
        "version_lists": version_lists,
        "doc_kinds": DocKind.CHOICES,
        "offer_types": OfferType.CHOICES,
        "events": events,
        "is_admin_view": is_admin_view,
        # See ``archive`` above for what this flag is and why it is not
        # ``is_admin_view``. Same one-line computation, same button.
        "can_open_marketing_chart": _can_open_marketing_chart(request),
        "lifecycle_report": _case_lifecycle_report(case) if is_admin_view else None,
        "comment_form": CommentForm(),
        "assignees": assignees,
        "arrival_comment": arrival_comment,
        "assign_comment": assign_comment,
        "to_author": to_author,
        "to_mode": to_mode,
        "pi_mode": pi_mode,
        "is_fresh_draft": is_fresh_draft,
        "sides_data": sides_data,
        "tech_split_can_assign": tech_split_can_assign,
        "tech_pool": tech_pool,
        "is_split": case.is_split,
        "status_rows": status.rows,
        "status_per_side": status.per_side,
        "multi_side": multi_side,
        "hide_combined": hide_combined,
        "combined_events": events,
        "FormKind": FormKind,
        # Warn when TO has Technical Problem rows — no Supply routing allowed.
        "to_blocked_by_issues": (
            not case.is_split
            and case.holder_unit == Unit.TECHNICAL
            and bool(_forms_of(FormKind.TO))
            and services._to_has_technical_problems(case)
            and viewer_unit == Unit.TECHNICAL
        ),
        # Warn when any active TO row still has an empty BRAND.
        "to_blocked_by_brand": (
            not case.is_split
            and case.holder_unit == Unit.TECHNICAL
            and bool(_forms_of(FormKind.TO))
            and bool(services._to_rows_without_brand(case))
            and not services._to_has_technical_problems(case)
            and viewer_unit == Unit.TECHNICAL
        ),
        # Warn when the current Proforma has remark rows blocking Submit to
        # Commercial. Shown both to Supply (who must clear the remark) and to
        # Technical when the case came back from Supply still carrying a remark
        # (Technical can't edit it, so it can only return to Supply).
        "pi_blocked_by_remark": (
            not case.is_split
            and bool(_forms_of(FormKind.PI))
            and services._pi_blocks_commercial(case)
            and (
                (case.holder_unit == Unit.SUPPLY and viewer_unit == Unit.SUPPLY)
                or
                (case.holder_unit == Unit.TECHNICAL and viewer_unit == Unit.TECHNICAL)
            )
        ),
        "is_comm": is_comm,
        "is_tech": is_tech,
        "is_supply": is_supply,
        "viewer_unit": viewer_unit or "",
        # Admin-only export audit timeline (who exported what, and when).
        "export_logs": (list(case.export_logs.select_related("actor").all()[:300])
                        if is_admin_view else None),
        "currency_logs": (list(case.currency_logs.select_related("actor").all()[:300])
                          if is_admin_view else None),
        "vat_percent": _vat_percent_value(),
        "require_ftco_code": bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", False)),
        # ---------------------------------------------------------------- #
        # Marketing: Reports / Reminders / Timeline — see the helpers above #
        # this function for what each carries and the write-permission     #
        # rule (``is_case_owner``: a COMMERCIAL role AND this case's own    #
        # creator — see ``services.is_case_commercial_owner``).            #
        # ---------------------------------------------------------------- #
        "is_case_owner": is_case_owner,
        "case_reports": case_reports,
        "case_reminders": case_reminders,
        # Whether the reminders list above is scoped to this viewer's own
        # rows or to every owner's — see ``_case_marketing_reminders``'s own
        # docstring for why this is the admin/GM tier alone, not a Marketing
        # Supervisor check. Said in words on the tab, the same way the
        # company page's own ``sees_all_reminders`` sentence is.
        "sees_all_case_reminders": is_admin_view,
        "case_mkt_timeline": case_mkt_timeline,
        "case_report_form": case_report_form,
        "case_reminder_form": case_reminder_form,
    }
    return render(request, "cases/case_detail.html", context)


@login_required
@require_POST
def case_report_add(request, pk):
    """Write one ``marketing.models.CompanyReport`` on THIS case, from the
    case's own page — POST-only, reached from the inline form in the
    Marketing » Reports tab of ``cases/templates/cases/case_detail.html``.

    THE WRITE-PERMISSION RULE, CHECKED HERE AND NOT MERELY HIDDEN IN THE
    TEMPLATE: ``services.is_case_commercial_owner`` — a COMMERCIAL role AND
    being THIS case's own creator, the owner's "دست ان نقش و شخص باشد و مالک
    ان پرونده ان باشد". A hand-built POST from anyone else is refused here,
    exactly as ``marketing/views.py::report_create`` refuses a write from the
    view-only tier regardless of what the template drew.

    ``marketing.services.add_report`` DOES THE WRITING, unchanged and
    un-wrapped — there is exactly one place in the whole platform that
    creates a ``CompanyReport``, and this is not a second one. ``case=case``
    is passed UNCONDITIONALLY: unlike the company page's own two-step wizard
    (``marketing/views.py::report_add``/``report_create``), there is no case
    to pick here — the case is fixed by the URL this form posted to — so the
    form's own ``case_id`` field (present only for the company page's shared
    ``ReportForm`` class) is never read.

    ``marketing.forms.ReportForm`` IS REUSED, NOT REBUILT, for its one real
    rule (``clean()``: at least one option ticked or some text) — a second,
    slightly different copy of that rule here would be exactly the kind of
    drift this codebase's own docstrings warn against elsewhere.
    ``can_manage_options=False`` ALWAYS: adding a new global ``ReportOption``
    is a Marketing-vocabulary privilege
    (``marketing/access.py::Access.can_manage_config`` — a Marketing
    Supervisor or the platform admin), unrelated to owning a case, and this
    page never offers it.

    On an invalid submit this redirects back with a flash message rather than
    re-rendering the form with field errors and whatever the writer already
    typed — a deliberate simplification for this lighter-weight, single-POST
    surface (see the long comment on the three ``_case_marketing_*`` helpers
    above ``case_detail`` for why this page borrows marketing's SERVICES and
    FORMS rather than its full two-screen wizard). The one rule this form
    enforces is simple enough ("write something, or tick a box") that this
    trade-off costs little, and the writer's own case is exactly where they
    landed to try again.
    """
    case = get_object_or_404(Case, pk=pk)
    if not services.is_case_commercial_owner(case, request):
        messages.error(
            request,
            "Only this case's own commercial owner may write a report on it.")
        return redirect("cases:case_detail", pk=case.pk)

    from marketing import services as marketing_services
    from marketing.forms import ReportForm

    form = ReportForm(request.POST, can_manage_options=False)
    if form.is_valid():
        data = form.cleaned_data
        marketing_services.add_report(
            case.client, request.user,
            text=data["text"], options=list(data["options"]), case=case,
        )
        messages.success(request, "Report added.")
    else:
        messages.error(
            request,
            "The report needs at least one option ticked or some text.")
    return redirect(
        "%s?mkttab=reports#marketing" % reverse("cases:case_detail", args=[case.pk]))


@login_required
@require_POST
def case_reminder_add(request, pk):
    """Set one ``marketing.models.Reminder`` on THIS case, from the case's own
    page — POST-only, reached from the inline form in the Marketing »
    Reminders tab of ``cases/templates/cases/case_detail.html``.

    THE SAME WRITE-PERMISSION RULE AS ``case_report_add`` ABOVE, checked the
    same way and for the same reason: ``services.is_case_commercial_owner``.
    A reminder is a private note, but WHO MAY ATTACH ONE TO A CASE is
    governed by case ownership, not by anything about Marketing membership —
    see that function's own docstring.

    ``marketing.reminders.create`` DOES THE WRITING, unchanged — the one
    place in the platform that creates a ``Reminder`` — with ``case=case``
    passed unconditionally for ``case_report_add``'s exact reason: this
    page's own case is the only one a reminder set from here can ever be
    about, so ``ReminderForm``'s ``case_id`` field (built for the company
    page's own multi-case picker) is reused for its ``note``/``due_at``
    validation alone and given ``case_choices=()`` — there is nothing to
    offer, and nothing submitted for it is ever read.
    """
    case = get_object_or_404(Case, pk=pk)
    if not services.is_case_commercial_owner(case, request):
        messages.error(
            request,
            "Only this case's own commercial owner may set a reminder on it.")
        return redirect("cases:case_detail", pk=case.pk)

    from marketing import reminders as marketing_reminders
    from marketing.forms import ReminderForm

    form = ReminderForm(request.POST, case_choices=())
    if form.is_valid():
        data = form.cleaned_data
        marketing_reminders.create(
            request.user, case.client,
            note=data["note"], due_at=data["due_at"], case=case,
        )
        messages.success(request, "Reminder set.")
    else:
        messages.error(request, "Enter a note and a valid Jalali date/time.")
    return redirect(
        "%s?mkttab=reminders#marketing" % reverse("cases:case_detail", args=[case.pk]))


def _event_visible_to(event, unit) -> bool:
    """Whether one timeline entry is shown to a viewer from ``unit``.

    Every unit now reads the WHOLE case history — start to finish, every handoff
    and every comment, whichever units they passed between. A case is one story
    and each unit needs to see what happened to it, not only the chapter their
    own desk touched.

    The single exception is the form-edit entry: ``EventAction.EDIT`` carrying a
    TO or PI form kind, which records that the owning unit re-saved its own
    current form version. That is workshop noise for the unit doing the work, not
    case history, so it keeps the narrower scoping the whole timeline used to
    have — the unit that made the edit (or a unit the entry was handed to) sees
    it, nobody else. Building a form (BUILD_TO / BUILD_PI) is a real event and is
    shown to everyone.

    That exception is now HISTORY ONLY, and it has to stay for exactly that
    reason. ``services.save_form`` no longer writes an EDIT row for an in-place
    edit at all (the owner asked for edits to stop being recorded, and the tool's
    autosave would otherwise have filed one every five minutes), so no new row
    can reach this branch. The rows already in the production database can, and
    they must keep rendering to the same readers they always did — deleting this
    condition would suddenly expose years of other units' workshop noise on every
    old case. Note the branch is deliberately unreachable for the other two EDIT
    writers, currency conversion and "Requested manager approval": neither
    carries a TO/PI ``form_kind``… except conversion, which carries PI and is
    therefore scoped to Commercial exactly as it was before this change.

    Prefer frozen ``from_unit`` / ``to_unit`` over the actor's live profile so
    history stays readable after seat reassignment / Delegate.
    """
    if unit is None:
        return True
    if not (getattr(event, "action", "") == EventAction.EDIT
            and getattr(event, "form_kind", "") in (FormKind.TO, FormKind.PI)):
        return True
    if event.from_unit == unit or event.to_unit == unit:
        return True
    # Always show DELEGATE on the case timeline (frozen ownership transfer).
    if getattr(event, "action", "") == EventAction.DELEGATE:
        return True
    actor = getattr(event, "actor", None)
    actor_unit = getattr(getattr(actor, "profile", None), "unit", None)
    if actor_unit is None:
        return True  # system / unattributed events
    return actor_unit == unit


def _inquiry_table_from_grid(rows):
    """Turn the editor's submitted grid into ``(inquiry table, row notes)``.

    ``#`` is taken from each surviving row's ``data-client`` (so a deletion
    leaves a visible gap); ``Item`` reflows 1..N.

    The Commercial row notes ride in with the grid, one per row, but they do NOT
    go into the table. They are what Commercial says about this version when
    handing it over, not a property of the product on the line, so they come
    back as a separate ``{# -> note}`` map and are stored on the version
    (``CaseForm.meta``) instead — see ``services._inquiry_comment_map``. Every
    export and every price/detail table renders from ``CaseForm.table``, so a
    note that never enters the table can never surface on a row or in a sheet.

    One reader for both moments the editor creates a version: "New version" and
    the Internal & External conversion, which must not be able to disagree about
    what the same grid means.
    """
    new_table, new_comments = [], {}
    for idx, row in enumerate(rows or [], start=1):
        row = row or {}
        try:
            cr = int(str(row.get("client_row", "")).strip() or idx)
        except (ValueError, TypeError):
            cr = idx
        entry = {
            "#": cr,
            "Item": idx,
            "Description": str(row.get("description", "")).strip(),
            "Size": str(row.get("size", "")).strip(),
            "Qty": str(row.get("quantity", "")).strip(),
            "Unit": str(row.get("unit", "")).strip(),
        }
        if str(row.get("deleted", "") or "") == "1":
            entry["_deleted"] = "1"
        if str(row.get("added", "") or "") == "1":
            entry["_added"] = "1"
        note = str(row.get("comment", "") or "").strip()
        if note:
            new_comments[str(cr)] = note
        new_table.append(entry)
    return new_table, new_comments


def _newver_context(case, rows, side, offer_type, price_type, seeded=False,
                    currency_conversion=False, update_price=False,
                    deadline_input="", convert=False, convert_side="",
                    convert_version=None):
    """Build the edit_items render context for "New version" mode.

    ``rows`` is a list of dicts with keys client_row/description/size/quantity/
    unit (seed format). Canonical inquiry keys (#/Description/…) are also
    accepted so a refused save can re-render without wiping the grid.

    ``convert`` marks the Internal & External conversion, which is the same
    screen doing the same job: the user authors the first version of a stream
    and a deadline, and confirming is what creates it. It therefore keeps
    ``newver_mode`` — that flag is what draws the version editor's chrome (the
    row-comment panel, the required-deadline star, the grid's add/delete marks)
    — and adds ``convert_mode`` beside it, which is all the template needs to
    post to the right place and name what is about to happen. ``convert_side``
    is the side being created, for the wording only.
    """
    line_items = []
    for idx, r in enumerate(rows or [], start=1):
        r = r or {}
        cr = r.get("client_row", r.get("#", idx))
        line_items.append(SimpleNamespace(
            client_row=cr if cr not in (None, "") else idx,
            row_no=idx,
            description=r.get("description", r.get("Description", "")),
            size=r.get("size", r.get("Size", "")),
            quantity=r.get("quantity", r.get("Qty", "")),
            unit=r.get("unit", r.get("Unit", "")),
            deleted=str(r.get("deleted", r.get("_deleted", "")) or "") == "1",
            added=str(r.get("added", r.get("_added", "")) or "") == "1",
            # ``comment`` is the grid's own key, put there by every caller below
            # from the version's note map. ``_comm_comment`` is only still read
            # in case a raw pre-move row reaches here directly: the grid showing
            # a stale note is nothing, the grid silently dropping one is not.
            comment=str(r.get("comment", r.get("_comm_comment", "")) or "").strip(),
        ))
    return {
        "case": case,
        "line_items": line_items,
        "editable_meta": False,      # New version: only the table (and deadline) change.
        "editable_contacts": False,
        "show_items": True,
        "show_deadline": True,
        # What the user typed, on a re-render that refused the save. The grid
        # comes back exactly as they left it; the deadline box has to as well,
        # or a refusal quietly replaces their date with the stored one and the
        # message then points at a box that no longer holds what it is about.
        "deadline_input": deadline_input,
        "edit_side": side,
        "newver_mode": True,
        "convert_mode": bool(convert),
        "convert_side": convert_side,
        "convert_side_label": Side.LABELS.get(convert_side, convert_side),
        # The number the new side's first inquiry will take. It is the version
        # the existing side is on (unchanged rule — a case at v00, which is
        # every case this can be used on today, gives the new side 00), and it
        # is passed in rather than written into the wording so the screen can
        # never promise a number the save will not produce.
        "convert_version": convert_version,
        "nv_offer": offer_type,
        "nv_price": price_type,
        "nv_currency": bool(currency_conversion),
        "nv_update_price": bool(update_price),
        "doc_kinds": DocKind.CHOICES, "offer_types": OfferType.CHOICES,
        "price_types": PriceType.CHOICES,
        "clients": Client.objects.all().order_by("name"),
    }


@login_required
def edit_items(request, pk):
    case = get_object_or_404(Case, pk=pk)
    profile = _profile(request.user)
    from people.role_nav import work_context
    ctx = work_context(request)
    side = (request.GET.get("side", "") or request.POST.get("edit_side", "")).strip()
    is_comm = bool(
        (ctx.role and ctx.role.unit == Unit.COMMERCIAL)
        or (profile and profile.unit == Unit.COMMERCIAL)
    )

    # ------------------------------------------------------------------
    # Internal & External conversion mode. Reached from the case page's "Open
    # editor" button (GET) and its own submit (POST) — deliberately the same
    # shape as "New version" below, because it is the same act: the user
    # authors the first version of a stream and its deadline, and CONFIRMING IN
    # THE EDITOR is what creates it. It used to happen instantly on a button
    # press, with no editor and no deadline.
    #
    # Nothing at all is written on the GET. The conversion, the new side's
    # first version and the deadline are one atomic POST, so walking away from the
    # editor — Cancel, Back, a closed tab — leaves the case exactly as it was:
    # still one-sided, same status, same holder, no orphan side.
    # ------------------------------------------------------------------
    convert = (request.GET.get("convert_two_stage", "")
               or request.POST.get("convert_two_stage", "")).strip() == "1"
    if convert:
        # The very permission the button was drawn from, read through the same
        # seat-aware call the case page used: Commercial, the case's creator,
        # case still alive, still single-sided, and carrying an inquiry. Whether
        # Commercial is HOLDING the case is deliberately not part of it — the
        # two sides are independent, so creating the missing one is not a
        # routing decision about the one that exists.
        if "upgrade_two_stage" not in services.allowed_actions_for_request(case, request):
            messages.error(request, "This case can't be made Internal & External right now.")
            return redirect("cases:case_detail", pk=pk)
        cv_prior_side = (Side.INTERNAL if case.price_type == PriceType.INTERNAL
                         else Side.EXTERNAL)
        cv_new_side = (Side.EXTERNAL if cv_prior_side == Side.INTERNAL
                       else Side.INTERNAL)
        cv_new_label = Side.LABELS.get(cv_new_side, cv_new_side)
        # The case is still single-sided while this screen is open, so the
        # baseline the grid's +/− marks are measured against is the case's own
        # v00 — exactly what "New version" uses on a non-split case.
        cv_v00 = services.v00_client_row_set(case, "")
        # The version the new side will be created at: the one the existing
        # side is on. Read before anything moves, so the screen and the save
        # cannot name different numbers.
        _cv_cur = case.current_form(FormKind.INQUIRY)
        cv_version = _cv_cur.version if _cv_cur else 0

        if request.method == "POST":
            try:
                rows = json.loads(request.POST.get("rows", "[]"))
            except ValueError:
                rows = []
            inq_errs = validate_inquiry_rows(rows)
            # The deadline is mandatory here for the reason it is mandatory on
            # New Case and on New version: this save CREATES a commercial
            # stream, and one may not exist without a date. It is read by the
            # same validator those two screens use (``_read_typed_deadline`` ->
            # ``CaseCreateForm.clean_deadline``), so a value that cannot be
            # read, a Jalali date that does not exist (12-30 of a common year)
            # and a date already gone by are refused here in the same words. No
            # second parser: there is one definition of what a typed deadline
            # means. Every refusal rides the same path as a bad inquiry row, so
            # the grid comes back untouched with the date still in the box.
            cv_deadline_raw = (request.POST.get("deadline", "") or "").strip()
            cv_deadline = None
            if not cv_deadline_raw:
                inq_errs = [f"Set the deadline before creating the {cv_new_label} side."] \
                    + list(inq_errs)
            else:
                cv_deadline, cv_deadline_err = _read_typed_deadline(
                    cv_deadline_raw, current=case.deadline)
                if cv_deadline_err:
                    inq_errs = [cv_deadline_err] + list(inq_errs)
            new_table, new_comments = _inquiry_table_from_grid(rows)
            if inq_errs:
                for err in inq_errs:
                    messages.error(request, err)
                # Nothing was written. Put each note back beside its row purely
                # so the grid re-renders the way the user left it; these dicts
                # die with the response.
                shown = [dict(r, comment=new_comments.get(str(r.get("#", "")), ""))
                         for r in new_table]
                return render(request, "cases/edit_items.html",
                              _newver_context(
                                  case,
                                  services.apply_inquiry_row_marks_vs_v00(shown, cv_v00),
                                  "", "", "", deadline_input=cv_deadline_raw,
                                  convert=True, convert_side=cv_new_side,
                                  convert_version=cv_version))
            try:
                # Same one-at-a-time guard the workflow buttons take: the row is
                # locked, the case re-read under the lock and the permission put
                # again to what it has actually become, so a double submit meets
                # the first one's result instead of half-converting a case that
                # is already split.
                with transaction.atomic():
                    _lock_case_row(case.pk)
                    case.refresh_from_db()
                    if "upgrade_two_stage" not in services.allowed_actions_for_request(case, request):
                        raise _TransitionRaceLost
                    services.upgrade_two_stage(
                        case, request.user,
                        new_table=new_table, comments=new_comments,
                        deadline=cv_deadline)
            except _TransitionRaceLost:
                messages.error(request, "This case can't be made Internal & External right now.")
                return redirect("cases:case_detail", pk=pk)
            except ValueError as exc:
                # The service refused (already split / ended / no inquiry) and
                # wrote nothing — its own transaction rolled back.
                messages.error(request, str(exc))
                return redirect("cases:case_detail", pk=pk)
            _made = case.current_form(FormKind.INQUIRY, cv_new_side)
            messages.success(
                request,
                f"Converted to Internal & External. The {cv_new_label} side was "
                f"created at version {(_made.version if _made else cv_version):02d} "
                "and is a Draft in your hands.")
            return redirect(
                f"{reverse('cases:case_detail', args=[pk])}?side={cv_new_side}")

        # GET: the editor, seeded with the rows the new side starts from (the
        # case's current inquiry) and the row notes recorded on that version.
        cv_cur = case.current_form(FormKind.INQUIRY)
        cv_rows = list(cv_cur.table or []) if cv_cur else []
        cv_comments = services._inquiry_comment_map(
            cv_rows, cv_cur.meta if cv_cur else None)
        cv_seed = []
        for r in cv_rows:
            cr = r.get("#", r.get("client_row", ""))
            cv_seed.append({
                "client_row": cr,
                "description": r.get("Description", r.get("description", "")),
                "size": r.get("Size", r.get("size", "")),
                "quantity": r.get("Qty", r.get("quantity", "")),
                "unit": r.get("Unit", r.get("unit", "")),
                "_deleted": r.get("_deleted", ""),
                "_added": r.get("_added", ""),
                "comment": cv_comments.get(str(cr).strip(), ""),
            })
        cv_seed = services.apply_inquiry_row_marks_vs_v00(cv_seed, cv_v00)
        return render(request, "cases/edit_items.html",
                      _newver_context(case, cv_seed, "", "", "", seeded=True,
                                      convert=True, convert_side=cv_new_side,
                                      convert_version=cv_version))

    # ------------------------------------------------------------------
    # "New version" mode. Reached from the New-version button (GET) and its
    # own submit (POST). A NEW inquiry version is committed only if the table
    # actually changes (or a two-stage upgrade is requested); otherwise the
    # editor re-renders with an error and the user stays on the page.
    # ------------------------------------------------------------------
    newver = (request.GET.get("newver", "") or request.POST.get("newver", "")).strip() == "1"
    if newver:
        nv_side = side if (case.is_split and side in (Side.INTERNAL, Side.EXTERNAL)) else ""
        if not services.can_new_inquiry_version(case, request.user, nv_side):
            messages.error(request, "A new version can't be started for this case right now.")
            return redirect("cases:case_detail", pk=pk)

        # Upgrade flags chosen on the New-version toggle (carried as params).
        nv_offer = (request.GET.get("offer_type", "") or request.POST.get("offer_type", "")).strip()
        nv_price = (request.GET.get("price_type", "") or request.POST.get("price_type", "")).strip()
        nv_currency = (request.GET.get("currency_conversion", "")
                       or request.POST.get("currency_conversion", "")).strip() in ("1", "true", "yes", "on")
        nv_update_price = (request.GET.get("update_price", "")
                           or request.POST.get("update_price", "")).strip() in ("1", "true", "yes", "on")
        # Unit conversion / Update price only for TO & PI cases.
        if case.offer_type != OfferType.TO_PI:
            nv_currency = False
            nv_update_price = False

        # The version we are branching from (this side's current inquiry).
        nv_cur = case.current_form(FormKind.INQUIRY, nv_side) or \
                 (case.current_form(FormKind.INQUIRY) if not nv_side else None)
        nv_rows = list(nv_cur.table or []) if nv_cur else []

        if request.method == "POST":
            try:
                rows = json.loads(request.POST.get("rows", "[]"))
            except ValueError:
                rows = []
            inq_errs = validate_inquiry_rows(rows)
            # A commercial case may not exist without a deadline, and a new
            # version is the one other moment the editor offers the box — so it
            # has to be filled here too, and the value the user types is what the
            # case ends up with. It is read by the same validator the New Case
            # screen uses (``_read_typed_deadline``), so an unreadable value and
            # a date already gone by are refused here exactly as they are there
            # rather than being accepted and dropped. Every refusal rides the
            # same path as a bad inquiry row, so the grid the user was editing
            # comes back untouched — with the date they typed still in the box.
            nv_deadline_raw = (request.POST.get("deadline", "") or "").strip()
            nv_deadline = None
            if not nv_deadline_raw:
                inq_errs = ["Set the deadline before saving the new version."] + list(inq_errs)
            else:
                nv_deadline, nv_deadline_err = _read_typed_deadline(
                    nv_deadline_raw, current=case.deadline)
                if nv_deadline_err:
                    inq_errs = [nv_deadline_err] + list(inq_errs)
            if inq_errs:
                for err in inq_errs:
                    messages.error(request, err)
                return render(request, "cases/edit_items.html",
                              _newver_context(case,
                                              services.apply_inquiry_row_marks_vs_v00(
                                                  [
                                                      {
                                                          "client_row": r.get("client_row", ""),
                                                          "description": r.get("description", ""),
                                                          "size": r.get("size", ""),
                                                          "quantity": r.get("quantity", ""),
                                                          "unit": r.get("unit", ""),
                                                          "_deleted": "1" if str(r.get("deleted", "") or "") == "1" else "",
                                                          "_added": "1" if str(r.get("added", "") or "") == "1" else "",
                                                          "comment": str(r.get("comment", "") or "").strip(),
                                                      }
                                                      for r in rows
                                                  ],
                                                  services.v00_client_row_set(case, nv_side)),
                                              nv_side, nv_offer, nv_price,
                                              currency_conversion=nv_currency,
                                              update_price=nv_update_price,
                                              deadline_input=nv_deadline_raw))
            # Build the canonical inquiry table from the submitted grid — the
            # same reader the conversion editor uses (see the helper's note on
            # why the row comments come back separately).
            new_table, new_comments = _inquiry_table_from_grid(rows)
            try:
                version = services.commit_inquiry_version(
                    case, request.user, new_table=new_table, side=nv_side,
                    offer_type=nv_offer, price_type=nv_price,
                    currency_conversion=nv_currency,
                    update_price=nv_update_price,
                    comments=new_comments,
                )
            except services.InquiryUnchanged:
                # No change + no upgrade -> refuse, stay on the page.
                messages.error(
                    request,
                    "No new version was created: change the table (edit a cell, "
                    "add or delete a row) — or turn on Unit conversion / Update "
                    "price / the two-stage upgrade — before saving.")
                # Nothing was saved, so put each note back beside its row purely
                # so the grid re-renders the way the user left it. These dicts
                # die with the response; they are not the table.
                shown = [dict(r, comment=new_comments.get(str(r.get("#", "")), ""))
                         for r in new_table]
                return render(request, "cases/edit_items.html",
                              _newver_context(case,
                                              services.apply_inquiry_row_marks_vs_v00(
                                                  shown, services.v00_client_row_set(case, nv_side)),
                                              nv_side, nv_offer, nv_price,
                                              currency_conversion=nv_currency,
                                              update_price=nv_update_price,
                                              deadline_input=nv_deadline_raw))
            # The version stands, so the deadline the editor insisted on is now
            # the case's deadline. It is written only after the commit: a version
            # that was refused (unchanged table) leaves the case exactly as it
            # was, deadline included. ``update_fields`` keeps this to the one
            # column — the service has just written the case row, and nothing
            # this view still holds in memory may be pushed back over it.
            if nv_deadline is not None and nv_deadline != case.deadline:
                case.deadline = nv_deadline
                case.save(update_fields=["deadline", "updated_at"])
            messages.success(request, f"New inquiry version {version:02d} saved.")
            if nv_side:
                return redirect(f"{reverse('cases:case_detail', args=[pk])}?side={nv_side}")
            return redirect("cases:case_detail", pk=pk)

        # GET: show the editor seeded with the current version's rows.
        v00_rows = services.v00_client_row_set(case, nv_side)
        # The notes come off the version, not off its rows, so re-opening the
        # editor still shows what Commercial wrote and lets them edit it. The
        # helper also reads a version saved before the move, whose notes are
        # still on the rows — those cases open with their comments intact.
        nv_comments = services._inquiry_comment_map(
            nv_rows, nv_cur.meta if nv_cur else None)
        seed = []
        for r in nv_rows:
            cr = r.get("#", r.get("client_row", ""))
            seed.append({
                "client_row": cr,
                "description": r.get("Description", r.get("description", "")),
                "size": r.get("Size", r.get("size", "")),
                "quantity": r.get("Qty", r.get("quantity", "")),
                "unit": r.get("Unit", r.get("unit", "")),
                "_deleted": r.get("_deleted", ""),
                "_added": r.get("_added", ""),
                "comment": nv_comments.get(str(cr).strip(), ""),
            })
        seed = services.apply_inquiry_row_marks_vs_v00(seed, v00_rows)
        return render(request, "cases/edit_items.html",
                      _newver_context(case, seed, nv_side, nv_offer, nv_price, seeded=True,
                                      currency_conversion=nv_currency,
                                      update_price=nv_update_price))

    # Per-side editing for a combined case: a side can be edited independently
    # whenever it is back with Commercial and its current inquiry is unsent —
    # exactly like a standalone (non-combined) case.
    side_edit = False
    contacts_only = (request.GET.get("contacts", "") or request.POST.get("contacts", "")).strip() == "1"
    actions_now = services.allowed_actions_for_request(case, request)
    can_edit_inquiry = "edit" in actions_now
    can_edit_contacts = "edit_contacts" in actions_now

    # Contacts-only mode (Case information → Edit): never touch the inquiry table.
    if contacts_only:
        if not can_edit_contacts:
            messages.error(request, "Contact fields cannot be edited right now.")
            return redirect("cases:case_detail", pk=pk)
        if request.method == "POST":
            case.client_commercial_expert = request.POST.get("client_commercial_expert", "").strip()
            case.client_commercial_phone = request.POST.get("client_commercial_phone", "").strip()
            case.client_technical_expert = request.POST.get("client_technical_expert", "").strip()
            case.client_technical_phone = request.POST.get("client_technical_phone", "").strip()
            # Captured BEFORE the overwrite two lines below — this is the only
            # place the pre-edit value still exists in memory, and it is what
            # marketing.services.log_case_role_change needs to decide whether
            # the business role genuinely changed (that function does not
            # re-check this itself; see its own docstring for why).
            old_marketing_label = case.marketing_label
            posted_label = request.POST.get("marketing_label", "").strip()
            case.marketing_label = posted_label if posted_label in MarketingLabel.LABELS else ""
            case.save(update_fields=[
                "client_commercial_expert", "client_commercial_phone",
                "client_technical_expert", "client_technical_phone",
                "marketing_label", "updated_at",
            ])
            # `cases` stays independent of `marketing` everywhere else on
            # purpose; this is a second, disclosed exception to that rule
            # (the first is client_list's identical local import above) and
            # is imported locally, not at module level, to keep it visible
            # right here rather than baked into the module's import list.
            # The comparison goes through effective_marketing_label rather
            # than the raw strings so a blank<->"owner" pair — not a real
            # change, since blank already means owner — never logs a role
            # change that did not actually happen.
            from marketing import services as marketing_services
            old_effective = marketing_services.effective_marketing_label(old_marketing_label)
            new_effective = marketing_services.effective_marketing_label(case.marketing_label)
            if old_effective != new_effective:
                marketing_services.log_case_role_change(
                    case, request.user, old_effective, new_effective)
            messages.success(request, "Case contacts updated.")
            return redirect("cases:case_detail", pk=pk)
        return render(request, "cases/edit_items.html", {
            "case": case,
            "line_items": [],
            "editable_meta": False,
            "editable_contacts": True,
            "show_items": False,
            "show_deadline": False,
            "contacts_only": True,
            "edit_side": "",
            "doc_kinds": DocKind.CHOICES, "offer_types": OfferType.CHOICES,
            "price_types": PriceType.CHOICES,
            "marketing_labels": MarketingLabel.CHOICES,
            "clients": Client.objects.all().order_by("name"),
        })

    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        cur_s = case.current_form(FormKind.INQUIRY, side)
        side_at_comm = (case.side_holder(side) == Unit.COMMERCIAL
                        and case.side_status(side) not in CaseStatus.TERMINAL)
        owns = services.can_act_on_side(case, request.user, side)
        side_edit = bool(is_comm and owns and side_at_comm and not (cur_s and cur_s.sent))
        if not side_edit:
            messages.error(request, "This side can no longer be edited here.")
            return redirect("cases:case_detail", pk=pk)
    else:
        if not can_edit_inquiry:
            messages.error(request, "This case can no longer be edited here.")
            return redirect("cases:case_detail", pk=pk)
        # On a split case, allow editing as long as the CURRENT inquiry version is
        # unsent. Older sent versions are fine — the user just made a fresh
        # (unsent) version and wants to edit/delete rows before submitting. Only
        # block when the current version has already been sent.
        if case.is_split and can_edit_inquiry:
            cur_any = case.current_form(FormKind.INQUIRY, "") or \
                      case.current_form(FormKind.INQUIRY, Side.INTERNAL) or \
                      case.current_form(FormKind.INQUIRY, Side.EXTERNAL)
            if cur_any and cur_any.sent:
                messages.error(request, "A side has already been submitted. Create a new version for the side you want to change.")
                return redirect("cases:case_detail", pk=pk)

    # Full case-information editing is only offered on a brand-new draft that
    # has never been submitted. The moment any inquiry version has been sent
    # (i.e. at least one action was taken), only the deadline may change — for
    # both combined and non-combined cases.
    cur_inq = case.current_form(FormKind.INQUIRY, side if side_edit else "")
    if side_edit:
        # A per-side edit is judged ONLY by that side: a freshly-created side
        # (e.g. the new External stream after a two-stage conversion) that is at
        # Draft/Commercial with an unsent v01 inquiry may edit and delete rows
        # exactly like a brand-new case — independent of the other side's state.
        side_inq_sent = bool(cur_inq and cur_inq.sent)
        is_fresh_draft = (case.side_status(side) == CaseStatus.DRAFT
                          and not side_inq_sent
                          and (cur_inq is None or cur_inq.version <= 1))
    else:
        # Same question the case page asks to decide which link to render, so it
        # is asked in one place — services.case_is_fresh_draft.
        is_fresh_draft = services.case_is_fresh_draft(case)
    # Deadline may be (re)set while the case is a draft, or for an editable side.
    deadline_editable = (case.status == CaseStatus.DRAFT) or side_edit

    if request.method == "POST":
        # This is the ORDINARY edit, and it is not one of the two moments a
        # deadline is asked for. It covers the fresh-draft "Edit case" screen and
        # the per-side inquiry edit, and it may be reached on a case that has
        # never had a deadline at all — a case opened before the box became
        # mandatory, or a side of one. Demanding one here would stop such a case
        # being saved at all, from a seat that was never asked to set it, so an
        # empty box is accepted whenever the case has no deadline to begin with.
        #
        # What it does refuse is going BACKWARDS. A deadline that exists may be
        # moved but not wiped, so a case that arrived here with one cannot leave
        # without one — which is the whole of what setting it at creation is
        # worth. And a typed value is read by the same validator the New Case
        # screen uses, so junk is refused instead of being quietly ignored: the
        # old "ignore what you cannot read" let a mistyped date look accepted
        # while the case kept the date the user believed they had replaced.
        deadline_err = ""
        new_deadline = case.deadline
        if deadline_editable:
            deadline_raw = (request.POST.get("deadline", "") or "").strip()
            if deadline_raw:
                dt, deadline_err = _read_typed_deadline(
                    deadline_raw, current=case.deadline)
                if not deadline_err:
                    new_deadline = dt
            elif case.deadline is not None:
                deadline_err = ("The deadline cannot be cleared. "
                                "Enter a new one, or leave the current one in place.")
        else:
            deadline_raw = ""

        rows_json = request.POST.get("rows", "[]")
        try:
            rows = json.loads(rows_json)
        except ValueError:
            rows = []

        inq_errs = validate_inquiry_rows(rows)
        if deadline_err:
            # Same refusal path as an invalid inquiry row: nothing is written and
            # the editor comes back with the grid the user was working on.
            inq_errs = [deadline_err] + list(inq_errs)
        if inq_errs:
            for err in inq_errs:
                messages.error(request, err)
            line_items = []
            for idx, r in enumerate(rows, start=1):
                line_items.append(SimpleNamespace(
                    client_row=r.get("client_row") or idx,
                    row_no=idx,
                    description=r.get("description", ""),
                    size=r.get("size", ""),
                    quantity=r.get("quantity", ""),
                    unit=r.get("unit", ""),
                    deleted=str(r.get("deleted", "") or "") == "1",
                    added=str(r.get("added", "") or "") == "1",
                ))
            return render(request, "cases/edit_items.html", {
                "case": case, "line_items": line_items,
                "editable_meta": is_fresh_draft,
                "editable_contacts": False,
                "show_items": True,
                "show_deadline": deadline_editable,
                # See ``_newver_context``: a refused save gives the user back
                # what they typed, not what the case still holds.
                "deadline_input": deadline_raw,
                "edit_side": side if side_edit else "",
                "doc_kinds": DocKind.CHOICES, "offer_types": OfferType.CHOICES,
                "price_types": PriceType.CHOICES,
                "marketing_labels": MarketingLabel.CHOICES,
                "clients": Client.objects.all().order_by("name"),
            })

        # --- Per-side edit on a split case: write ONLY this side's inquiry ---
        # The global line_items pool is shared by both sides, so a per-side edit
        # must NOT delete/recreate it (that would corrupt the other side). We
        # build this side's inquiry table directly from the submitted rows and
        # store it on the side's current inquiry form, leaving the other side and
        # the shared line_items completely untouched.
        #
        # Fresh draft exception: price type / client / kind may still change, and
        # both sides still share one table — fall through to the whole-case path
        # so meta (including price_type) actually persists and inactive side tabs
        # are removed.
        if side_edit and not is_fresh_draft:
            side_table = []
            for idx, row in enumerate(rows, start=1):
                # An already-progressed side keeps each surviving row's original #
                # so soft-deletes keep the row with a − mark.
                try:
                    cr = int(str(row.get("client_row", "")).strip() or idx)
                except (ValueError, TypeError):
                    cr = idx
                entry = {
                    "#": cr,
                    "Item": idx,
                    "Description": str(row.get("description", "")).strip(),
                    "Size": str(row.get("size", "")).strip(),
                    "Qty": str(row.get("quantity", "")).strip(),
                    "Unit": str(row.get("unit", "")).strip(),
                }
                if str(row.get("deleted", "") or "") == "1":
                    entry["_deleted"] = "1"
                if str(row.get("added", "") or "") == "1":
                    entry["_added"] = "1"
                side_table.append(entry)
            inq_form = case.current_form(FormKind.INQUIRY, side)
            if inq_form is not None:
                inq_form.columns = ["#", "Item", "Description", "Size", "Qty", "Unit"]
                inq_form.table = side_table
                inq_form.save(update_fields=["columns", "table"])
            if deadline_editable:
                case.deadline = new_deadline
                case.save(update_fields=["deadline", "updated_at"])
            messages.success(request, "Case updated.")
            return redirect("cases:case_detail", pk=pk)

        # The whole inquiry is rewritten by wiping the pool and re-inserting it,
        # so the delete and the insert have to succeed or fail together. Without
        # the transaction a single unstorable cell (an over-long Unit, a negative
        # client row) let the DELETE commit and the INSERT blow up, leaving the
        # case with no items at all. Rows and numbering are unchanged.
        with transaction.atomic():
            case.line_items.all().delete()
            items = []
            flagged_table = []
            for idx, row in enumerate(rows, start=1):
                # Fresh draft (before the first action): the client row (#) reflows
                # 1..N exactly like Item. After the case has moved (a new version),
                # soft-deleted rows stay in the table with _deleted=1.
                if is_fresh_draft:
                    cr = idx
                else:
                    try:
                        cr = int(str(row.get("client_row", "")).strip() or idx)
                    except (ValueError, TypeError):
                        cr = idx
                items.append(LineItem(
                    case=case, row_no=idx, client_row=cr,
                    description=str(row.get("description", "")).strip(),
                    size=str(row.get("size", "")).strip(),
                    unit=str(row.get("unit", "")).strip(),
                    quantity=str(row.get("quantity", "")).strip(),
                ))
                entry = {
                    "#": cr,
                    "Item": idx,
                    "Description": str(row.get("description", "")).strip(),
                    "Size": str(row.get("size", "")).strip(),
                    "Qty": str(row.get("quantity", "")).strip(),
                    "Unit": str(row.get("unit", "")).strip(),
                }
                if not is_fresh_draft and str(row.get("deleted", "") or "") == "1":
                    entry["_deleted"] = "1"
                if not is_fresh_draft and str(row.get("added", "") or "") == "1":
                    entry["_added"] = "1"
                flagged_table.append(entry)
            if items:
                LineItem.objects.bulk_create(items)

        if is_fresh_draft:
            # Fresh draft: every field is editable (not logged).
            new_kind = request.POST.get("kind", "")
            new_offer = request.POST.get("offer_type", "")
            new_client = request.POST.get("client", "")
            new_order = request.POST.get("order_no", "")
            if new_kind in dict(DocKind.CHOICES):
                case.kind = new_kind
            if new_offer in dict(OfferType.CHOICES):
                case.offer_type = new_offer
            if new_client:
                try:
                    case.client = Client.objects.get(pk=new_client)
                except (Client.DoesNotExist, ValueError):
                    pass
            case.order_no = new_order.strip()
            new_price = request.POST.get("price_type", "")
            if new_price in dict(PriceType.CHOICES):
                case.price_type = new_price
            # Keep split machinery + inquiry streams in sync with price_type
            # (e.g. BOTH → Internal removes the External tab on a fresh draft).
            services.sync_fresh_draft_price_type(case)
            case.client_commercial_expert = request.POST.get("client_commercial_expert", "").strip()
            case.client_commercial_phone = request.POST.get("client_commercial_phone", "").strip()
            case.client_technical_expert = request.POST.get("client_technical_expert", "").strip()
            case.client_technical_phone = request.POST.get("client_technical_phone", "").strip()
            # Captured BEFORE the overwrite two lines below, for the identical
            # reason the contacts-only edit branch above captures it — see
            # that branch's comment. This block's own "(not logged)" above
            # still describes every OTHER field here; the role alone gets the
            # one surgical exception below, once the save has actually landed.
            old_marketing_label = case.marketing_label
            posted_label = request.POST.get("marketing_label", "").strip()
            case.marketing_label = posted_label if posted_label in MarketingLabel.LABELS else ""
            case.deadline = new_deadline
            case.doc_no = codes.build_doc_no(
                ym=case.year_month, expert_code=case.expert_code,
                client_code=case.client.code, serial=case.serial,
            )
            case.save()
            # `cases` stays independent of `marketing` everywhere else on
            # purpose; imported locally, not at module level, to keep this
            # disclosed exception visible right here — see the contacts-only
            # edit branch above (and client_list's identical local import)
            # for the precedent. Compared through effective_marketing_label,
            # not the raw strings, so a blank<->"owner" pair — not a real
            # change, since blank already means owner — never logs a role
            # change that did not actually happen.
            from marketing import services as marketing_services
            old_effective = marketing_services.effective_marketing_label(old_marketing_label)
            new_effective = marketing_services.effective_marketing_label(case.marketing_label)
            if old_effective != new_effective:
                marketing_services.log_case_role_change(
                    case, request.user, old_effective, new_effective)
        elif deadline_editable:
            # Inquiry edit path: only the deadline may change alongside the table.
            case.deadline = new_deadline
            case.save(update_fields=["deadline", "updated_at"])
        # Decide which side streams to (re)write. A per-side edit only touches
        # that side. A whole-case edit writes all sides ONLY while the case is a
        # fresh draft (initial v00, shared by both sides). After that, split
        # sides are independent: a new version on one side must never copy into
        # the other side's stream.
        # Fresh draft (even opened via ?side=) rewrites every active side so a
        # price_type change creates/drops Internal/External streams correctly.
        if side_edit and not is_fresh_draft:
            snap_sides = [side]
        elif case.is_split and not is_fresh_draft:
            # Non-side edit on an already-progressed split case: restrict to the
            # side(s) that are currently editable at Commercial (normally none,
            # but guard anyway so we never overwrite an independent side).
            snap_sides = [sc for sc in case.sides
                          if case.side_holder(sc) == Unit.COMMERCIAL
                          and case.side_status(sc) not in CaseStatus.TERMINAL]
            snap_sides = snap_sides or None
        else:
            snap_sides = None
        services._snapshot_inquiry(case, request.user, sides=snap_sides,
                                   table_override=flagged_table)
        # Inquiry edits are intentionally NOT recorded in any timeline.
        messages.success(request, "Case updated.")
        return redirect("cases:case_detail", pk=pk)

    # Prefer the current inquiry snapshot (keeps soft-delete / add marks).
    edit_seed_side = side if side_edit else (case.primary_side or "")
    inq_for_edit = case.current_form(FormKind.INQUIRY, edit_seed_side) or case.current_form(FormKind.INQUIRY)
    if inq_for_edit and inq_for_edit.table and not is_fresh_draft:
        line_items = []
        for idx, r in enumerate(inq_for_edit.table, start=1):
            r = r or {}
            line_items.append(SimpleNamespace(
                client_row=r.get("#", r.get("client_row", idx)) or idx,
                row_no=idx,
                description=r.get("Description", r.get("description", "")),
                size=r.get("Size", r.get("size", "")),
                quantity=r.get("Qty", r.get("quantity", "")),
                unit=r.get("Unit", r.get("unit", "")),
                deleted=str(r.get("_deleted", "") or "") == "1",
                added=str(r.get("_added", "") or "") == "1",
            ))
    else:
        line_items = list(case.line_items.all())
    return render(request, "cases/edit_items.html", {
        "case": case, "line_items": line_items,
        "editable_meta": is_fresh_draft,
        "editable_contacts": False,
        "show_items": True,
        "show_deadline": deadline_editable,
        "edit_side": side if side_edit else "",
        "doc_kinds": DocKind.CHOICES, "offer_types": OfferType.CHOICES,
        "price_types": PriceType.CHOICES,
        "marketing_labels": MarketingLabel.CHOICES,
        "clients": Client.objects.all().order_by("name"),
    })


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------
class _TransitionRaceLost(Exception):
    """Raised when the case moved on before this POST got to write."""


def _lock_case_row(pk) -> None:
    """Hold the case row against every other transition until this commit.

    ``transition`` reads the case, decides from its status, and only then calls
    the service that writes. Two POSTs that arrive together used to run that
    sequence interleaved: both read CLOSED, both were told yes, and both wrote.
    Measured on this app — two Burns wrote the cancellation request twice, and a
    Burn racing a Finalize left the case FINAL_APPROVED while still carrying the
    burn request, which is a state no single sequence of clicks can produce.

    Serialising it needs a lock that outlives the read, and ``select_for_update``
    is not it: SQLite has no ``SELECT ... FOR UPDATE`` and Django silently drops
    the clause there, so the guard would hold on the deployed Postgres and be
    absent on a SQLite install — the worst of both. A one-row UPDATE locks that
    row on every backend we run on: Postgres takes the row's exclusive lock until
    commit, SQLite takes the write lock and makes the second connection wait for
    it. The row is written back to the value it already holds, so nothing about
    the case changes and no ``auto_now`` fires — ``update()`` never applies it.

    It must be the FIRST statement of the transaction. On SQLite a transaction
    that has already read cannot then take the write lock (its snapshot is stale
    and the write is refused outright rather than waited for), so the lock is
    taken before this view reads anything else inside the block.
    """
    Case.objects.filter(pk=pk).update(updated_at=F("updated_at"))


@login_required
def transition(request, pk):
    case = get_object_or_404(Case, pk=pk)
    if request.method != "POST":
        return redirect("cases:case_detail", pk=pk)

    action = request.POST.get("action", "")
    comment = request.POST.get("comment", "").strip()
    side = request.POST.get("side", "")
    from people.role_nav import work_context
    ctx = work_context(request)
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # Currency conversion is a Commercial-only form edit (not a workflow button).
    if action == "convert_pi_currency":
        try:
            form_id = int(request.POST.get("form_id") or 0)
            services.convert_pi_currency(
                case, request.user,
                form_id=form_id,
                from_unit=request.POST.get("from_unit", ""),
                to_unit=request.POST.get("to_unit", ""),
                side=side,
            )
            if wants_json:
                return JsonResponse({"ok": True})
            messages.success(request, "Proforma currency converted.")
            return redirect("cases:case_detail", pk=pk)
        except Exception as exc:
            logger.exception("Case #%s currency conversion failed", pk)
            if wants_json:
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, f"Currency conversion failed: {exc}")
            return redirect("cases:case_detail", pk=pk)

    # On a split case these actions dispatch to a per-side service below, and only
    # ``can_do_side_action`` looks at that side's own holder and status. The
    # whole-case rule reads ``case.status``, which stays behind on a split case
    # (a two-stage upgrade deliberately never rewrites it), so accepting it here
    # would let a stale whole-case status finalise or burn a side that never went
    # to the client — and finalising auto-cancels the sibling side. Whole-case
    # POSTs (no side) and every other action keep the original rule.
    # ``assign`` is here for the same reason: the whole-case grant is the sideless
    # "both sides" delegation Technical does, so honouring it for a POST that
    # names a side would let a manager assign a side its own unit does not hold —
    # writing an expert from the wrong pool into that side's assignee, which no
    # real holder can then act on or clear. A sided assign belongs to that side's
    # holding unit, which is exactly what ``can_do_side_action`` checks.
    #
    # This is asked TWICE of the very same rules: once here, where a POST that
    # was never allowed is turned away without touching the database, and once
    # more under the row lock below, where the answer is the one that counts.
    # Nothing about who may do what changes — it is the same question put to the
    # case as it stands at the moment of writing rather than as it stood when
    # the page was read.
    def _permitted_for(current):
        allowed = services.allowed_actions(
            current, request.user, role=ctx.role, work_user=ctx.seat_user,
        )
        side_dispatched = (
            current.is_split and side in (Side.INTERNAL, Side.EXTERNAL)
            and action in {"close", "send_to_client", "request_cancel",
                           "finalize", "final_close", "burn", "assign"}
        )
        if side_dispatched or action not in allowed:
            return services.can_do_side_action(
                current, request.user, action, side,
                role=ctx.role, work_user=ctx.seat_user)
        return True

    if not _permitted_for(case):
        messages.error(request, "That action is not available right now.")
        return redirect("cases:case_detail", pk=pk)

    # Cancel / burn / final-close / cannot-supply require a non-empty comment
    # (same rule as the UI ``data-required`` confirm panels).
    if action in ("request_cancel", "burn", "final_close", "cannot_supply") and not comment:
        messages.error(request, "A comment is required for this action.")
        return redirect("cases:case_detail", pk=pk)

    actor = request.user
    try:
        # ONE TRANSITION AT A TIME, decided and written together.
        #
        # The lock is taken first, so a second request that arrived while
        # this one was deciding waits here instead of deciding in parallel;
        # the case is then re-read under it and the same permission rules are
        # put to what the case has actually become. A duplicate click, or a
        # second confirm panel sent from the same page, therefore meets the
        # first one's result and is turned away with the wording a late click
        # has always got — while a case that legitimately transitions once is
        # not affected at all, because the rules themselves are unchanged.
        with transaction.atomic():
            _lock_case_row(case.pk)
            case.refresh_from_db()
            if not _permitted_for(case):
                raise _TransitionRaceLost
            _side = request.POST.get("side", "")
            if action == "submit_to_technical":
                services.submit_to_technical(case, actor, comment, side=_side)
            elif action == "return_to_commercial":
                services.return_to_commercial(case, actor, comment, side=_side)
            elif action == "send_to_supply":
                services.send_to_supply(case, actor, comment, side=_side)
            elif action == "return_to_technical":
                services.return_to_technical(case, actor, comment, side=_side)
            elif action == "send_to_commercial":
                services.send_to_commercial(case, actor, comment, side=_side)
            elif action == "send_to_client" and case.is_split and _side:
                services.close_side(case, actor, _side, comment)
            elif action == "close" and case.is_split and _side:
                services.close_side(case, actor, _side, comment)
            elif action == "request_cancel" and case.is_split and _side:
                services.cancel_side(case, actor, _side, comment)
            # Unreachable today: neither allowed_actions nor can_do_side_action ever
            # grants "propose_send", so the gate above rejects the POST first. Kept
            # wired for the day the propose/approve step is switched back on.
            elif action == "propose_send":
                proposed = request.POST.get("proposed_action", "")
                services.propose_send(case, actor, proposed, comment)
            elif action == "approve_send":
                services.approve_send(case, actor, comment)
            elif action == "assign":
                assignee_id = request.POST.get("assignee")
                # The assignee must come from the same pool the UI offers. services
                # .assign writes the FK without looking at the target's profile, so a
                # hand-crafted pk would park the case on someone outside the holding
                # unit: it then drops out of every inbox and the manager loses the
                # "assign" action, leaving nobody who can undo it. The holding unit is
                # resolved exactly the way services.assign picks its target field.
                if case.is_split and not _side and (
                        case.side_holder(Side.INTERNAL) == Unit.TECHNICAL
                        or case.side_holder(Side.EXTERNAL) == Unit.TECHNICAL):
                    assign_unit = Unit.TECHNICAL
                elif case.is_split and _side in (Side.INTERNAL, Side.EXTERNAL):
                    assign_unit = case.side_holder(_side)
                else:
                    assign_unit = case.holder_unit
                assignee = get_object_or_404(
                    User.objects.filter(profile__unit=assign_unit,
                                        profile__role=Role.EXPERT, is_active=True),
                    pk=assignee_id,
                )
                services.assign(case, actor, assignee, comment=comment,
                                side=request.POST.get("side", ""))
            elif action == "close":
                services.close_case(case, actor, comment)
            elif action == "cannot_supply":
                services.mark_cannot_supply(case, actor, comment, side=request.POST.get("side", ""))
            # There is deliberately no approve/reject step for "cannot supply": the
            # decision goes live immediately via mark_cannot_supply above (status
            # UNSUPPLIABLE, no review). The approve_unsuppliable / reject_unsuppliable
            # branches that used to sit here could never run — nothing granted those
            # actions, so the permission gate above rejected the POST first — and were
            # removed along with their services functions and case-page buttons.
            elif action == "return_to_supply":
                services.return_to_supply(case, actor, comment, side=_side)
            elif action == "finalize" and case.is_split and _side:
                services.finalize_side(case, actor, _side, comment)
            elif action == "finalize":
                services.finalize_case(case, actor, comment)
            elif action == "final_close" and case.is_split and _side:
                services.final_close_side(case, actor, _side, comment)
            elif action == "final_close":
                services.final_close_case(case, actor, comment)
            elif action == "burn" and case.is_split and _side:
                services.burn_side(case, actor, _side, comment)
            elif action == "burn":
                services.burn_case(case, actor, comment)
            elif action == "request_cancel":
                services.request_cancel(case, actor, comment)
            elif action == "approve_cancel":
                services.approve_cancel(case, actor, comment)
            elif action == "reject_cancel":
                services.reject_cancel(case, actor, comment)
            elif action == "new_inquiry_version":
                # The version is NOT created here any more. "New version" simply opens
                # the inquiry editor; a new version is committed there only if the
                # table actually changes (or a two-stage upgrade is requested). Carry
                # the chosen offer/price upgrade through as query params.
                params = {"newver": "1"}
                ot = request.POST.get("offer_type", "")
                pt = request.POST.get("price_type", "")
                if ot:
                    params["offer_type"] = ot
                if pt:
                    params["price_type"] = pt
                if case.is_split and _side:
                    params["side"] = _side
                qs = urlencode(params)
                messages.info(request, "Edit the items — a new version is saved only if you change the table.")
                return redirect(f"{reverse('cases:edit_items', args=[pk])}?{qs}")
            elif action == "upgrade_two_stage":
                # The conversion is NOT performed here any more. Like "New
                # version" above it, the button only opens the inquiry editor;
                # the new side's version 00 — and the deadline it insists on —
                # are created when the user confirms there. Nothing is written
                # on the way through, so this stays safe for an old bookmark or
                # a replayed POST: it lands on the editor rather than splitting
                # a case behind the user's back without a deadline.
                messages.info(request,
                              "Edit the items and set the deadline — the new "
                              "side is created when you confirm.")
                return redirect(
                    f"{reverse('cases:edit_items', args=[pk])}?convert_two_stage=1")
            elif action == "comment":
                if comment:
                    ev = services.add_comment(case, actor, comment,
                                              side=request.POST.get("side", ""))
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                        return JsonResponse({
                            "ok": True,
                            "actor": ev.actor_display_name or (actor.get_full_name() or actor.username),
                            "substitute": bool(ev.actor_is_substitute),
                            "comment": ev.comment,
                        })
                elif request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({"ok": False, "error": "Empty comment."}, status=400)
            else:
                messages.error(request, "Unknown action.")
                return redirect("cases:case_detail", pk=pk)
    except _TransitionRaceLost:
        # Someone else got there first. Nothing was written by this request:
        # raising out of the atomic block rolls it back, lock included.
        messages.error(request, "That action is not available right now.")
        return redirect("cases:case_detail", pk=pk)
    except Exception as exc:  # pragma: no cover - defensive
        # Surface a short message to the user, but also log the full traceback so
        # a failed workflow action is never silently lost from the server logs.
        logger.exception("Case #%s action failed", pk)
        messages.error(request, f"Action failed: {exc}")
        return redirect("cases:case_detail", pk=pk)

    messages.success(request, "Done.")
    return redirect("cases:case_detail", pk=pk)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def _parse_version_token(version):
    """Split the ``?v=`` token into (version number, two-stage generation).

    A version NUMBER does not identify a snapshot on its own: a two-stage
    upgrade keeps the number of the version it supersedes, so "01" and
    "01 · Two Stage" are both ``version == 1``. Both version chips therefore
    used to send the same ``?v=1`` and both exported the same document — the
    unit could look at "Version 01" and download "Version 01 · Two Stage".

    The chips now mark the two-stage generation with a trailing ``s``
    (``?v=1s``); a bare number means the original generation. Returns
    ``(None, None)`` for anything unparseable, which sends the caller to the
    current form.
    """
    text = str(version if version is not None else "").strip().lower()
    if not text:
        return None, None
    two_stage = False
    if text.endswith("s"):
        text, two_stage = text[:-1], True
    try:
        return int(text), two_stage
    except (TypeError, ValueError):
        return None, None


def _resolve_export_form(case, form_kind, side: str = "", version=None):
    kind = (form_kind or "").upper()
    side = (side or "").strip()
    # An explicit version wins: the export follows the version the user selected
    # (e.g. v00 vs v03), not always the latest. Falls back to current when the
    # version is missing/unknown.
    if version is not None and str(version).strip() != "":
        vnum, want_two_stage = _parse_version_token(version)
        if vnum is not None:
            qs = CaseForm.objects.filter(case=case, kind=kind, version=vnum)
            if side:
                qs = qs.filter(side=side)
            f = qs.filter(two_stage=want_two_stage).order_by("-id").first()
            if f is None:
                # No snapshot in the requested generation. This is the path a
                # link written before the suffix existed takes when the number
                # it names has since been superseded by a two-stage snapshot,
                # so fall back to the newest snapshot carrying that number
                # (two-stage first) rather than refusing the export.
                f = qs.order_by("-two_stage", "-id").first()
            if f is not None:
                return f
    if side:
        return case.current_form(kind, side)
    return case.current_form(kind)


def _resolve_export_form_for_viewer(case, form_kind, side: str, version, profile):
    """Resolve an exportable form the viewer is allowed to see.

    Owners/admin get the normal current (or explicit version). Other units get
    the latest snapshot published to them when the live current is not yet
    visible (e.g. Technical edited after sending elsewhere).
    """
    form = _resolve_export_form(case, form_kind, side, version=version)
    if form is None:
        return None
    if _user_can_see_exported_form(profile, form):
        return form
    # Explicit version that is not published to this viewer → deny.
    if version is not None and str(version).strip() != "":
        return None
    if profile is None:
        return None
    if profile.is_admin or profile.is_general_manager:
        return form
    owner = _FORM_OWNER_UNIT.get((form_kind or "").upper()) or _FORM_OWNER_UNIT.get(
        getattr(form, "kind", ""))
    if profile.unit == owner:
        return form
    kind = (form_kind or getattr(form, "kind", "") or "").upper()
    qs = CaseForm.objects.filter(case=case, kind=kind)
    if side:
        qs = qs.filter(side=side)
    elif getattr(form, "side", None):
        qs = qs.filter(side=form.side)
    visible = [
        # Oldest first, so the last entry is the newest snapshot this unit was
        # given. ``two_stage`` has to sit between version and id: the two-stage
        # snapshot carries the SAME number as the version it supersedes, so
        # without it the "latest published to Commercial" was decided by
        # insertion order rather than by which version is actually newer.
        f for f in qs.order_by("version", "two_stage", "id")
        if services.form_published_to_unit(f, profile.unit)
    ]
    return visible[-1] if visible else None


def _parse_terms_post(request, kind: str = "PI") -> dict:
    from .export_data import default_terms_for

    defaults = default_terms_for(kind)
    cats = []
    for i, default in enumerate(defaults["categories"]):
        items_en = [
            ln.strip() for ln in (request.POST.get(f"cat_{i}_items_en") or "").splitlines()
            if ln.strip()
        ]
        items_fa = [
            ln.strip() for ln in (request.POST.get(f"cat_{i}_items_fa") or "").splitlines()
            if ln.strip()
        ]
        cats.append({
            "title_en": request.POST.get(f"cat_{i}_title_en") or default["title_en"],
            "title_fa": request.POST.get(f"cat_{i}_title_fa") or default["title_fa"],
            "full": bool(default.get("full")),
            "items_en": items_en or list(default["items_en"]),
            "items_fa": items_fa or list(default["items_fa"]),
        })
    return {
        "intro_en": request.POST.get("intro_en") or defaults["intro_en"],
        "intro_fa": request.POST.get("intro_fa") or defaults["intro_fa"],
        "categories": cats,
    }


def _remember_export_terms(form, terms) -> None:
    """Record the Terms sheet a PDF / Print-view export was just taken with.

    The unit of memory is the CaseForm row, which *is* one
    (case, kind, side, version, two_stage) snapshot — so the sheet is
    remembered per version exactly as asked, one sheet per version, and the
    next version starts blank again. PDF and Print view deliberately share it:
    they render the same terms page from the same editor, so "the last term
    sheet used for this version" is one answer, not one per format.

    ``update_fields`` is not an optimisation here, it is the whole point:
    ``CaseForm.updated_at`` is ``auto_now`` and it is what
    ``export_data.form_date_jalali`` prints as the document DATE. Saving the
    whole row would move that date on every export, which would change what the
    documents say. Naming the single column keeps ``auto_now`` from firing.

    Never let a bookkeeping failure break an export the user already has.
    """
    if form is None or not isinstance(terms, dict):
        return
    try:
        form.export_terms = terms
        form.save(update_fields=["export_terms"])
    except Exception:
        logger.exception("Could not store export terms for form #%s",
                         getattr(form, "pk", "?"))


_EXPORT_FMT_LABELS = {
    "xlsx": "Excel", "grouped": "Grouped Excel", "pdf": "PDF", "html": "Print view",
}


def _can_export(profile, form_kind, fmt) -> bool:
    """Per-unit export permissions.

    * Commercial  : every export EXCEPT the supply grouped Excel.
    * Technical   : TO-form exports only (never grouped, never PI).
    * Supply      : the grouped Excel only.
    * Admin / GM  : everything.
    """
    if profile is None:
        return False
    if profile.is_admin or profile.is_general_manager:
        return True
    kind = (form_kind or "").upper()
    fmt = (fmt or "").lower()
    unit = profile.unit
    if unit == Unit.COMMERCIAL:
        return fmt != "grouped"
    if unit == Unit.TECHNICAL:
        return kind == "TO" and fmt != "grouped"
    if unit == Unit.SUPPLY:
        return fmt in ("grouped", "xlsx")
    return False


_FORM_OWNER_UNIT = {
    FormKind.INQUIRY: Unit.COMMERCIAL,
    FormKind.TO: Unit.TECHNICAL,
    FormKind.PI: Unit.SUPPLY,
    "INQUIRY": Unit.COMMERCIAL,
    "TO": Unit.TECHNICAL,
    "PI": Unit.SUPPLY,
}


def _user_can_see_exported_form(profile, form) -> bool:
    """Same recipient rule as case-detail tabs: owner/admin always; others only
    if this snapshot was published to their unit on a handoff."""
    if profile is None or form is None:
        return False
    if profile.is_admin or profile.is_general_manager:
        return True
    owner = _FORM_OWNER_UNIT.get(form.kind)
    if profile.unit == owner:
        return True
    return services.form_published_to_unit(form, profile.unit)


def _log_export(case, form, request, fmt: str, side: str = "") -> None:
    """Record an export for the admin-only export audit timeline."""
    try:
        from .models import CaseExportLog
        kind = getattr(form, "kind", "") or ""
        label = "%s %s" % (kind, _EXPORT_FMT_LABELS.get((fmt or "").lower(), (fmt or "").upper()))
        CaseExportLog.objects.create(
            case=case,
            actor=request.user if getattr(request.user, "is_authenticated", False) else None,
            form_kind=kind,
            form_version=getattr(form, "version", None),
            side=(side or getattr(form, "side", "") or ""),
            fmt=(fmt or "").lower(),
            label=label.strip(),
        )
    except Exception:
        logger.exception("Failed to log export for case #%s", getattr(case, "pk", "?"))



@login_required
def log_currency_conversion(request, pk):
    """AJAX: record a PI unit conversion for the admin/GM Conversion Timeline."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    case = get_object_or_404(Case, pk=pk)
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    # This writes an audit row against a specific case, so — exactly like the
    # export routes — being in the right unit is not enough on its own: the
    # caller must also be able to see *this* case, or a guessed pk lets anyone
    # in Supply/Commercial file fabricated entries on files they never touched.
    if not services.user_can_view_case(case, request.user):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    # Supply / Commercial / admin / GM may log conversions they perform.
    if not (profile.is_admin or profile.is_general_manager
            or profile.unit in (Unit.SUPPLY, Unit.COMMERCIAL)):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    from_unit = (request.POST.get("from_unit") or "").strip()
    to_unit = (request.POST.get("to_unit") or "").strip()
    rate = request.POST.get("rate", "")
    side = (request.POST.get("side") or "").strip()
    # …and they must actually be able to PERFORM one. This records a conversion
    # that has happened; somebody who is only looking cannot have performed it.
    # A read-only viewer of a Proforma — a unit that does not hold the case,
    # opening it from the Archive — passes both tests above (they may see the
    # case, and they are in Supply or Commercial), so without this they could
    # file an entry on the admin's Conversion Timeline for a conversion nobody
    # made. The rule asked is the SAME ONE that decides whether their page is
    # read-only in the first place, imported rather than restated so the two
    # cannot drift apart.
    from itemcoder.bridge import _may_build_form
    if not _may_build_form(request, case, FormKind.PI, side)[0]:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    reset = (request.POST.get("reset") or "").strip() in ("1", "true", "yes")
    form = case.current_form(FormKind.PI, side) if side else case.current_form(FormKind.PI)
    try:
        services.log_currency_conversion(
            case, request.user,
            from_code=from_unit, to_code=to_unit, rate=rate,
            side=side or getattr(form, "side", "") or "",
            form_kind=FormKind.PI,
            form_version=getattr(form, "version", None),
            source="tool",
            reset=reset,
        )
    except Exception as exc:
        logger.exception("Currency conversion log failed for case #%s", pk)
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


@login_required
def export_form(request, pk, form_kind, fmt):
    case = get_object_or_404(Case.objects.select_related("client"), pk=pk)
    profile = _profile(request.user)
    if profile is None:
        return redirect("accounts:login")

    # A user must be able to see this specific case before any export of it is
    # considered — previously this route only checked the unit-level rule
    # below ("does your unit do this kind of export"), which let anyone in the
    # right unit download any case's documents by guessing the case id.
    if not services.user_can_view_case(case, request.user):
        messages.error(request, "You do not have access to this case.")
        return redirect("cases:inbox")

    if not _can_export(profile, form_kind, fmt):
        messages.error(request, "You don't have permission to use this export.")
        return redirect("cases:case_detail", pk=pk)

    side = (request.GET.get("side") or "").strip()
    version = request.GET.get("v")
    form = _resolve_export_form_for_viewer(
        case, form_kind, side, version, profile)
    if form is None:
        messages.error(request, "There is no such form to export yet.")
        return redirect("cases:case_detail", pk=pk)

    # PDF / Print view (HTML): first show the editable bilingual Terms page.
    if fmt in ("pdf", "html") and request.method == "GET":
        from .export_data import (
            client_name_only, default_terms_for, doc_no_export, form_date_jalali,
            normalize_terms,
        )
        confirm_name = (
            "cases:export_form_pdf_confirm" if fmt == "pdf"
            else "cases:export_form_html_confirm"
        )
        confirm_url = reverse(confirm_name, args=[pk, form.kind])
        _cparams = {}
        if side or form.side:
            _cparams["side"] = side or form.side
        if version:
            _cparams["v"] = version
        if _cparams:
            confirm_url = f"{confirm_url}?{urlencode(_cparams)}"
        # The sheet this version was last exported with, if it ever was; the
        # kind's defaults otherwise. ``normalize_terms`` merges the stored blob
        # over those same defaults, so a sheet saved before a default clause
        # changed still opens as a complete, well-shaped sheet.
        defaults = default_terms_for(form.kind)
        saved = bool(form.has_export_terms)
        return render(request, "cases/export/terms_editor.html", {
            "case": case,
            "form": form,
            "side": side or form.side,
            "terms": normalize_terms(form.export_terms, form.kind) if saved else defaults,
            # Rendered into the page as JSON so "Reset to default" can restore
            # them without a round trip. See the note in the template.
            "default_terms": defaults,
            "has_saved_terms": saved,
            "doc_no": doc_no_export(case, form),
            "client": client_name_only(case, form),
            "form_date": form_date_jalali(form),
            "confirm_url": confirm_url,
            "export_fmt": fmt,
        })

    try:
        if fmt == "xlsx":
            # Supply must not see employer identity (CLIENT / PROJECT) on Excel.
            hide_identity = bool(profile and profile.unit == Unit.SUPPLY
                                 and not profile.is_admin
                                 and not profile.is_general_manager)
            content, filename = exports.export_form_excel(
                case, form,
                hide_client=hide_identity,
                hide_project=hide_identity,
            )
            _log_export(case, form, request, fmt, side)
            return _file_response(content, filename,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if fmt == "grouped":
            content, filename = exports.export_supply_grouped_excel(case, form)
            _log_export(case, form, request, fmt, side)
            return _file_response(content, filename,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if fmt == "html":
            # Reached only via POST (terms confirm) or legacy callers.
            from .pdf_export import render_print_view_html
            terms = _parse_terms_post(request, form.kind) if request.method == "POST" else None
            html_out, filename = render_print_view_html(case, form, terms=terms)
            if terms is not None:
                _remember_export_terms(form, terms)
            _log_export(case, form, request, fmt, side)
            resp = HttpResponse(html_out, content_type="text/html; charset=utf-8")
            resp["Content-Disposition"] = f'inline; filename="{filename}"'
            return resp
        if fmt == "pdf":
            terms = _parse_terms_post(request, form.kind) if request.method == "POST" else None
            content, filename = exports.export_form_pdf(case, form, terms=terms)
            if terms is not None:
                _remember_export_terms(form, terms)
            _log_export(case, form, request, fmt, side)
            return _file_response(content, filename, "application/pdf")
    except Exception as exc:  # never surface a 500 to the user for an export
        logger.exception("Export failed for case #%s (%s/%s)", pk, form_kind, fmt)
        messages.error(request, f"Could not generate the {fmt.upper()} export: {exc}")
        return redirect("cases:case_detail", pk=pk)

    messages.error(request, "Unknown export format.")
    return redirect("cases:case_detail", pk=pk)


def _is_ajax(request) -> bool:
    return (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest"


@login_required
def export_form_pdf_confirm(request, pk, form_kind):
    """POST from the Terms editor → generate PDF with the edited texts."""
    if request.method != "POST":
        side = (request.GET.get("side") or "").strip()
        url = reverse("cases:export_form", args=[pk, form_kind, "pdf"])
        params = {}
        if side:
            params["side"] = side
        _v = request.GET.get("v")
        if _v:
            params["v"] = _v
        if params:
            url = f"{url}?{urlencode(params)}"
        return redirect(url)

    case = get_object_or_404(Case.objects.select_related("client"), pk=pk)
    profile = _profile(request.user)
    if profile is None:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Authentication required."}, status=401)
        return redirect("accounts:login")

    if not services.user_can_view_case(case, request.user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "You do not have access to this case."}, status=403)
        messages.error(request, "You do not have access to this case.")
        return redirect("cases:inbox")

    if not _can_export(profile, form_kind, "pdf"):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "You don't have permission to use this export."}, status=403)
        messages.error(request, "You don't have permission to use this export.")
        return redirect("cases:case_detail", pk=pk)

    side = (request.GET.get("side") or "").strip()
    version = request.GET.get("v")
    form = _resolve_export_form_for_viewer(
        case, form_kind, side, version, profile)
    if form is None:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "There is no such form to export yet."}, status=404)
        messages.error(request, "There is no such form to export yet.")
        return redirect("cases:case_detail", pk=pk)

    try:
        terms = _parse_terms_post(request, form.kind)
        content, filename = exports.export_form_pdf(case, form, terms=terms)
        # Only once the document actually exists: a sheet that failed to render
        # is not "the last term sheet exported for this version".
        _remember_export_terms(form, terms)
        _log_export(case, form, request, "pdf", side)
        # AJAX: return JSON+base64 so browser extensions (IDM) cannot intercept
        # application/pdf attachment responses and corrupt the download.
        if _is_ajax(request):
            import base64
            return JsonResponse({
                "ok": True,
                "filename": filename,
                "pdf_base64": base64.b64encode(content).decode("ascii"),
            })
        return _file_response(content, filename, "application/pdf")
    except Exception as exc:
        logger.exception("PDF export failed for case #%s (%s)", pk, form_kind)
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": f"Could not generate the PDF export: {exc}"},
                status=500,
            )
        messages.error(request, f"Could not generate the PDF export: {exc}")
        return redirect("cases:case_detail", pk=pk)


@login_required
def export_form_html_confirm(request, pk, form_kind):
    """POST from the Terms editor → generate Print-view HTML with edited texts."""
    if request.method != "POST":
        side = (request.GET.get("side") or "").strip()
        url = reverse("cases:export_form", args=[pk, form_kind, "html"])
        params = {}
        if side:
            params["side"] = side
        _v = request.GET.get("v")
        if _v:
            params["v"] = _v
        if params:
            url = f"{url}?{urlencode(params)}"
        return redirect(url)

    case = get_object_or_404(Case.objects.select_related("client"), pk=pk)
    profile = _profile(request.user)
    if profile is None:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Authentication required."}, status=401)
        return redirect("accounts:login")

    if not services.user_can_view_case(case, request.user):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "You do not have access to this case."}, status=403)
        messages.error(request, "You do not have access to this case.")
        return redirect("cases:inbox")

    if not _can_export(profile, form_kind, "html"):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "You don't have permission to use this export."}, status=403)
        messages.error(request, "You don't have permission to use this export.")
        return redirect("cases:case_detail", pk=pk)

    side = (request.GET.get("side") or "").strip()
    version = request.GET.get("v")
    form = _resolve_export_form_for_viewer(
        case, form_kind, side, version, profile)
    if form is None:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "There is no such form to export yet."}, status=404)
        messages.error(request, "There is no such form to export yet.")
        return redirect("cases:case_detail", pk=pk)

    try:
        from .pdf_export import render_print_view_html
        terms = _parse_terms_post(request, form.kind)
        html_out, filename = render_print_view_html(case, form, terms=terms)
        _remember_export_terms(form, terms)
        _log_export(case, form, request, "html", side)
        if _is_ajax(request):
            import base64
            return JsonResponse({
                "ok": True,
                "filename": filename,
                "html_base64": base64.b64encode(html_out.encode("utf-8")).decode("ascii"),
            })
        resp = HttpResponse(html_out, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = f'inline; filename="{filename}"'
        return resp
    except Exception as exc:
        logger.exception("HTML export failed for case #%s (%s)", pk, form_kind)
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": f"Could not generate the Print view: {exc}"},
                status=500,
            )
        messages.error(request, f"Could not generate the Print view: {exc}")
        return redirect("cases:case_detail", pk=pk)


def _file_response(content: bytes, filename: str, content_type: str) -> HttpResponse:
    from urllib.parse import quote

    safe_name = (filename or "download").replace('"', "").replace("\r", "").replace("\n", "")
    resp = HttpResponse(content, content_type=content_type)
    # ASCII fallback + RFC 5987 so browsers always get a usable name.
    resp["Content-Disposition"] = (
        f'attachment; filename="{safe_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
    )
    # Custom header is always readable by same-origin fetch (unlike some CD configs).
    resp["X-Filename"] = safe_name
    resp["Access-Control-Expose-Headers"] = "Content-Disposition, X-Filename"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp["Pragma"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# Clients (commercial master data) + FX — Commercial manager OR Admin
# ---------------------------------------------------------------------------
def _is_commercial_manager(user) -> bool:
    profile = _profile(user)
    return bool(profile and profile.is_manager and profile.unit == Unit.COMMERCIAL)


def _is_platform_admin(user) -> bool:
    profile = _profile(user)
    return bool(profile and profile.is_admin)


def _can_manage_clients_fx(user) -> bool:
    """Commercial manager or platform admin may open Clients & FX."""
    return _is_commercial_manager(user) or _is_platform_admin(user)


def _deny_clients_fx(request, message: str = "Only the Commercial manager or an Administrator can manage Clients & FX."):
    messages.error(request, message)
    if _is_platform_admin(request.user):
        return redirect("accounts:admin_console")
    return redirect("cases:inbox")


def _parse_rial_price(raw):
    from decimal import Decimal, InvalidOperation
    s = str(raw or "").replace(",", "").replace(" ", "").strip()
    if not s:
        raise ValueError("Enter a Rial price.")
    try:
        n = Decimal(s)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid Rial price.") from exc
    if n < 0:
        raise ValueError("Rial price cannot be negative.")
    return n


@login_required
def master_data_hub(request):
    """Commercial manager / Admin landing: Clients and FX Rates."""
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request)
    from . import fx_rates as fx
    return render(request, "cases/master_data_hub.html", {
        "can_manage_fx": True,
        "fx_stale": fx.is_rates_stale(),
        "is_admin_md": _is_platform_admin(request.user),
    })


@login_required
def fx_rates(request):
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage FX rates.")
    from . import fx_rates as fx
    rates = fx.list_rates()
    for r in rates:
        r.rial_price_display = fx.format_rial_amount(r.rial_price) if r.rial_price else ""
    return render(request, "cases/fx_rates.html", {
        "rates": rates,
        "catalog": fx.catalog_for_add(),
        "latest_update": fx.latest_update_at(),
        "fx_stale": fx.is_rates_stale(),
    })


@login_required
def fx_rate_add(request):
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage FX rates.")
    if request.method != "POST":
        return redirect("cases:fx_rates")
    from . import fx_rates as fx
    from .models import CurrencyRate
    code = fx.normalize_code(request.POST.get("code", ""))
    if not code or code == "rial":
        messages.error(request, "Choose a currency from the list.")
        return redirect("cases:fx_rates")
    catalog = {c["code"]: c for c in fx.CURRENCY_CATALOG}
    meta = catalog.get(code)
    if meta is None:
        messages.error(request, "Unknown currency.")
        return redirect("cases:fx_rates")
    if CurrencyRate.objects.filter(code=code).exists():
        messages.error(request, f"{code.upper()} is already on the board.")
        return redirect("cases:fx_rates")
    try:
        price = _parse_rial_price(request.POST.get("rial_price"))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("cases:fx_rates")
    if price <= 0:
        messages.error(request, "Enter a positive Rial price.")
        return redirect("cases:fx_rates")
    CurrencyRate.objects.create(
        code=code,
        name=meta["name"],
        symbol=meta["symbol"],
        rial_price=price,
        is_builtin=False,
        updated_by=request.user,
    )
    messages.success(request, f"{code.upper()} added at {fx.format_rial_amount(price)} Rial.")
    return redirect("cases:fx_rates")


@login_required
def fx_rate_update(request, pk):
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage FX rates.")
    from . import fx_rates as fx
    from .models import CurrencyRate
    row = get_object_or_404(CurrencyRate, pk=pk)
    if request.method != "POST":
        return redirect("cases:fx_rates")
    try:
        price = _parse_rial_price(request.POST.get("rial_price"))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("cases:fx_rates")
    if price <= 0:
        messages.error(request, "Enter a positive Rial price.")
        return redirect("cases:fx_rates")
    row.rial_price = price
    row.updated_by = request.user
    row.save(update_fields=["rial_price", "updated_by", "updated_at"])
    messages.success(
        request,
        f"{row.code.upper()} updated to {fx.format_rial_amount(price)} Rial.",
    )
    return redirect("cases:fx_rates")


@login_required
def fx_rate_update_all(request):
    """Save every FX board price in one submit and restart the 24h timer.

    Each posted ``rate_<pk>`` is written; ``updated_at`` advances for every
    saved row (same as a single-row Update), so ``is_rates_stale`` resets from
    this moment for the whole board.
    """
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage FX rates.")
    if request.method != "POST":
        return redirect("cases:fx_rates")
    from . import fx_rates as fx
    from .models import CurrencyRate

    rows = list(CurrencyRate.objects.all())
    if not rows:
        messages.error(request, "No currencies on the board yet.")
        return redirect("cases:fx_rates")

    parsed = []
    for row in rows:
        raw = request.POST.get(f"rate_{row.pk}")
        if raw is None:
            messages.error(request, f"Missing price for {row.code.upper()}.")
            return redirect("cases:fx_rates")
        try:
            price = _parse_rial_price(raw)
        except ValueError as exc:
            messages.error(request, f"{row.code.upper()}: {exc}")
            return redirect("cases:fx_rates")
        if price <= 0:
            messages.error(request, f"{row.code.upper()}: enter a positive Rial price.")
            return redirect("cases:fx_rates")
        parsed.append((row, price))

    for row, price in parsed:
        row.rial_price = price
        row.updated_by = request.user
        row.save(update_fields=["rial_price", "updated_by", "updated_at"])

    messages.success(
        request,
        f"Updated {len(parsed)} exchange rate{'s' if len(parsed) != 1 else ''}. "
        f"24-hour timer restarted.",
    )
    return redirect("cases:fx_rates")


@login_required
def fx_rate_delete(request, pk):
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage FX rates.")
    from .models import CurrencyRate
    row = get_object_or_404(CurrencyRate, pk=pk)
    if request.method != "POST":
        return redirect("cases:fx_rates")
    if row.is_builtin or row.code in ("usd", "eur"):
        messages.error(request, "Default currencies (USD / EUR) cannot be removed.")
        return redirect("cases:fx_rates")
    code = row.code.upper()
    row.delete()
    messages.success(request, f"{code} removed from the FX board.")
    return redirect("cases:fx_rates")


@login_required
def fx_rates_api(request):
    """JSON board for PI tool + commercial conversion UIs."""
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=403)
    unit = profile.unit
    if not (profile.is_admin or profile.is_general_manager
            or unit in (Unit.COMMERCIAL, Unit.SUPPLY)):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    from . import fx_rates as fx
    from_unit = request.GET.get("from", "")
    to_unit = request.GET.get("to", "")
    payload = fx.api_payload()
    if from_unit and to_unit:
        try:
            rate, stale = fx.resolve_conversion(from_unit, to_unit)
            payload["from"] = fx.normalize_code(from_unit)
            payload["to"] = fx.normalize_code(to_unit)
            payload["rate"] = rate
            payload["stale"] = stale or payload["stale"]
            payload["convertible"] = (not payload["stale"]) and (
                fx.normalize_code(from_unit) != fx.normalize_code(to_unit)
            )
        except ValueError as exc:
            payload["rate"] = None
            payload["convertible"] = False
            payload["error"] = str(exc)
    return JsonResponse(payload)


@login_required
def client_list(request):
    if not _can_manage_clients_fx(request.user):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can manage clients.")

    query = request.GET.get("q", "").strip()
    profile = _profile(request.user)
    clients = Client.objects.all()
    if query:
        clients = clients.filter(Q(name__icontains=query) | Q(code__icontains=query))
    clients = list(clients.order_by("code", "name"))

    # Commercial's own client table also shows Marketing's label tags (manual
    # tags from ANY Marketing user, plus case-derived ones) — a deliberate,
    # disclosed exception to `cases` staying independent of `marketing`
    # (every other file in `cases` has zero references to `marketing` on
    # purpose); the owner asked for this one screen to show it. Imported
    # locally, not at module level, to keep that exception visible right
    # here rather than baked into the module's import list.
    from marketing import services as marketing_services
    # scope="all": this is Commercial's screen, not a scoped Marketing
    # viewer, so it needs the union across every Marketing user's tags, not
    # just request.user's own. labels_for_clients batches across every
    # client on the page in a constant number of queries — not one lookup
    # per row in the template loop below.
    labels_by_client = marketing_services.labels_for_clients(
        [c.pk for c in clients], request.user, scope="all",
    )
    for c in clients:
        c.labels = labels_by_client.get(c.pk, [])

    return render(request, "cases/client_list.html", {
        "clients": clients,
        "query": query,
        "can_add": bool(profile and profile.can_add_client),
        "can_upload": bool(profile and (profile.can_add_client or _is_platform_admin(request.user))),
        "can_wipe_clients": _is_platform_admin(request.user),
    })


@login_required
def client_add(request):
    profile = _profile(request.user)
    if not (profile and profile.can_add_client):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can add clients.")

    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.code = services.next_client_code()
            client.created_by = request.user
            client.save()
            messages.success(request, f"Client added with code {client.code}.")
            return redirect("cases:client_list")
    else:
        form = ClientForm()
    return render(request, "cases/client_form.html", {"form": form})


@login_required
def client_rename(request, pk):
    client = get_object_or_404(Client, pk=pk)
    profile = _profile(request.user)
    if not (profile and profile.can_add_client):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can rename clients.")

    if request.method == "POST":
        form = ClientRenameForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, "Client renamed (the code stays the same).")
            return redirect("cases:client_list")
    else:
        form = ClientRenameForm(instance=client)
    return render(request, "cases/client_form.html", {"form": form, "client": client})


@login_required
def client_upload(request):
    """Bulk client codes from Excel (Commercial manager or Admin)."""
    profile = _profile(request.user)
    if not (profile and profile.can_add_client):
        return _deny_clients_fx(request, "Only the Commercial manager or an Administrator can upload client codes.")

    if request.method == "POST" and request.FILES.get("excel_file"):
        try:
            created, updated, warnings = services.import_clients_from_excel(
                request.FILES["excel_file"], request.user,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "cases/upload.html", {
                "title": "Upload client codes",
                "hint": "Excel with two columns: Code, Name (.xlsx). Numeric codes become 001, 012, …",
                "action_url": "cases:client_upload",
            })
        except Exception:
            logger.exception("Client Excel upload failed")
            messages.error(
                request,
                "Upload failed unexpectedly. Use a valid .xlsx (Code, Name). "
                "If the file is very large, try again or split it.",
            )
            return render(request, "cases/upload.html", {
                "title": "Upload client codes",
                "hint": "Excel with two columns: Code, Name (.xlsx). Numeric codes become 001, 012, …",
                "action_url": "cases:client_upload",
            })
        messages.success(request, f"Clients imported: {created} new, {updated} updated.")
        for w in (warnings or [])[:12]:
            messages.warning(request, w)
        if warnings and len(warnings) > 12:
            messages.warning(request, f"…and {len(warnings) - 12} more warnings.")
        return redirect("cases:client_list")
    return render(request, "cases/upload.html", {
        "title": "Upload client codes",
        "hint": "Excel with two columns: Code, Name (.xlsx only). Numeric codes are stored as 001, 012, … (4+ digits stay as-is).",
        "action_url": "cases:client_upload",
    })


@login_required
def client_wipe(request):
    """Admin-only: delete every client so a fresh Excel can be uploaded."""
    if not _is_platform_admin(request.user):
        return _deny_clients_fx(request, "Only an Administrator can wipe all clients.")
    if request.method != "POST":
        return redirect("cases:client_list")
    try:
        n = services.wipe_all_clients()
        messages.success(request, f"All clients cleared ({n} removed). You can upload a new Excel now.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("cases:client_list")


@login_required
def client_delete(request, pk):
    """Admin-only: delete one client if no case uses that client code."""
    if not _is_platform_admin(request.user):
        return _deny_clients_fx(request, "Only an Administrator can delete individual clients.")
    client = get_object_or_404(Client, pk=pk)
    if request.method != "POST":
        return redirect("cases:client_list")
    label = f"{client.code} — {client.name}"
    try:
        services.delete_client_if_unused(client)
        messages.success(request, f"Client deleted: {label}")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("cases:client_list")


@login_required
def client_lookup(request):
    """AJAX search used by the case form (search by code or name).

    Persian/Arabic letter variants must compare as equal (see
    ``core.persian_text``), so a plain ``icontains`` is not enough on its
    own. Same small-table Python-side-filter tradeoff as
    ``marketing.services.search_clients`` — fine at the confirmed
    current/foreseeable size, and easy to swap for DB-side normalization
    later without changing this function's shape.
    """
    profile = _profile(request.user)
    if not (profile and (profile.unit == Unit.COMMERCIAL or profile.is_admin)):
        return JsonResponse({"results": []})
    query = request.GET.get("q", "").strip()
    clients = Client.objects.all()
    if query:
        needle = normalize_persian(query).lower()
        clients = [
            c for c in clients
            if needle in normalize_persian(c.name).lower()
            or needle in normalize_persian(c.code).lower()
        ]
        clients = sorted(clients, key=lambda c: c.name)[:20]
    else:
        clients = list(clients.order_by("name")[:20])
    results = [{"id": c.id, "code": c.code, "name": c.name} for c in clients]
    return JsonResponse({"results": results})


# ---------------------------------------------------------------------------
# Expert codes (commercial master data)
# ---------------------------------------------------------------------------
@login_required
def expert_code_list(request):
    profile = _profile(request.user)
    if not (profile and profile.unit == Unit.COMMERCIAL):
        messages.error(request, "Commercial access only.")
        return redirect("cases:inbox")
    return render(request, "cases/expert_code_list.html", {
        "expert_codes": ExpertCode.objects.select_related("user").all(),
        "can_edit": profile.is_manager,
    })


@login_required
def expert_code_add(request):
    profile = _profile(request.user)
    if not (profile and profile.is_manager and profile.unit == Unit.COMMERCIAL):
        messages.error(request, "Only the Commercial manager can edit expert codes.")
        return redirect("cases:expert_code_list")

    if request.method == "POST":
        form = ExpertCodeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Expert code added.")
            return redirect("cases:expert_code_list")
    else:
        form = ExpertCodeForm()
    return render(request, "cases/expert_code_form.html", {"form": form})


@login_required
def expert_code_edit(request, pk):
    profile = _profile(request.user)
    if not (profile and profile.is_manager and profile.unit == Unit.COMMERCIAL):
        messages.error(request, "Only the Commercial manager can edit expert codes.")
        return redirect("cases:expert_code_list")

    expert_code = get_object_or_404(ExpertCode, pk=pk)
    if request.method == "POST":
        form = ExpertCodeForm(request.POST, instance=expert_code)
        if form.is_valid():
            form.save()
            messages.success(request, "Expert code updated.")
            return redirect("cases:expert_code_list")
    else:
        form = ExpertCodeForm(instance=expert_code)
    return render(request, "cases/expert_code_form.html", {"form": form, "expert_code": expert_code})


@login_required
def expert_code_upload(request):
    profile = _profile(request.user)
    if not (profile and profile.is_manager and profile.unit == Unit.COMMERCIAL):
        messages.error(request, "Only the Commercial manager can upload expert codes.")
        return redirect("cases:expert_code_list")

    if request.method == "POST" and request.FILES.get("excel_file"):
        created, updated = _import_two_column(
            request.FILES["excel_file"], ExpertCode, "code", "name", request.user
        )
        messages.success(request, f"Expert codes imported: {created} new, {updated} updated.")
        return redirect("cases:expert_code_list")
    return render(request, "cases/upload.html", {
        "title": "Upload expert codes",
        "hint": "Excel with two columns: Code, Name.",
        "action_url": "cases:expert_code_upload",
    })


def _import_two_column(file_obj, model, code_field, name_field, user) -> tuple[int, int]:
    """Import a simple two-column (code, name) Excel file into a model."""
    import openpyxl

    created = updated = 0
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    for idx, raw in enumerate(ws.iter_rows(values_only=True)):
        cells = list(raw) + [None, None]
        raw_code, raw_name = cells[0], cells[1]
        if model is Client:
            code = services.normalize_client_code(raw_code)
            name = ("" if raw_name is None else str(raw_name).strip())
        else:
            code = ("" if raw_code is None else str(raw_code).strip())
            name = ("" if raw_name is None else str(raw_name).strip())
        if idx == 0 and (code.lower() in {"code", "کد"} or not code):
            continue
        if not code or not name:
            continue
        defaults = {name_field: name}
        if model is Client:
            obj, was_created = model.objects.get_or_create(
                **{code_field: code}, defaults={**defaults, "created_by": user}
            )
        else:
            obj, was_created = model.objects.get_or_create(**{code_field: code}, defaults=defaults)
        if was_created:
            created += 1
        else:
            setattr(obj, name_field, name)
            obj.save(update_fields=[name_field])
            updated += 1
    wb.close()
    return created, updated
