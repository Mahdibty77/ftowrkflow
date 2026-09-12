"""Workflow engine for cases.

All state changes go through these functions so the rules stay in one place and
every change is recorded in the timeline (``CaseEvent``). Views call these and
never mutate ``Case.status`` directly.
"""
from __future__ import annotations

import logging
import re

from django.db import transaction

from accounts.constants import Role, Unit, SupplyKind

from .codes import build_doc_no, next_case_serial, year_month_token
from .constants import CaseStatus, EventAction, FormKind, OfferType, PriceType, Side
from .models import Case, CaseEvent, CaseForm, Client, LineItem, SerialCounter, Supplier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sequential client codes
# ---------------------------------------------------------------------------
def normalize_client_code(raw) -> str:
    """Normalize a client code for storage and document numbers.

    Numeric codes shorter than 4 digits are zero-padded to 3 places
    (1 → 001, 12 → 012, 100 → 100). Four-or-more-digit codes stay as-is
    (1235 → 1235). Non-numeric codes are trimmed only.
    """
    import math

    if raw is None:
        return ""
    if isinstance(raw, bool):
        return str(raw).strip()
    if isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            return ""
        if raw == int(raw):
            raw = int(raw)
        else:
            raw = str(raw).strip()
    if isinstance(raw, int):
        n = int(raw)
        if n < 0:
            return str(n)
        return str(n).zfill(3) if n < 1000 else str(n)
    s = str(raw).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    if s.isdigit():
        n = int(s)
        return str(n).zfill(3) if n < 1000 else str(n)
    return s


def _max_numeric_client_code() -> int:
    """Highest numeric client code currently stored (0 if none)."""
    max_n = 0
    for code in Client.objects.values_list("code", flat=True):
        s = str(code or "").strip()
        if s.isdigit():
            max_n = max(max_n, int(s))
    return max_n


@transaction.atomic
def sync_client_code_counter() -> int:
    """Align ``client_code`` SerialCounter with the highest existing code."""
    max_n = _max_numeric_client_code()
    counter, _ = SerialCounter.objects.select_for_update().get_or_create(
        key="client_code", defaults={"value": max_n},
    )
    if counter.value < max_n:
        counter.value = max_n
        counter.save(update_fields=["value"])
    return counter.value


def _max_numeric_supplier_code() -> int:
    """Highest numeric supplier code currently stored (0 if none)."""
    max_n = 0
    for code in Supplier.objects.values_list("code", flat=True):
        s = str(code or "").strip()
        if s.isdigit():
            max_n = max(max_n, int(s))
    return max_n


def next_supplier_code(width: int = 3) -> str:
    """Next sequential supplier code — Supplier's own next_client_code."""
    max_n = _max_numeric_supplier_code()
    counter, _ = SerialCounter.objects.select_for_update().get_or_create(
        key="supplier_code", defaults={"value": max_n},
    )
    if counter.value < max_n:
        counter.value = max_n
    counter.value += 1
    counter.save(update_fields=["value"])
    n = int(counter.value)
    return str(n).zfill(width) if n < 1000 else str(n)


@transaction.atomic
def next_client_code(width: int = 3) -> str:
    """Next sequential client code (padded to ``width`` when under 1000).

    Always continues from the greater of the stored counter and the highest
    numeric code already in the Clients table (e.g. last 582 → next 583).
    """
    max_n = _max_numeric_client_code()
    counter, _ = SerialCounter.objects.select_for_update().get_or_create(
        key="client_code", defaults={"value": max_n},
    )
    if counter.value < max_n:
        counter.value = max_n
    counter.value += 1
    counter.save(update_fields=["value"])
    n = int(counter.value)
    return str(n).zfill(width) if n < 1000 else str(n)


def import_clients_from_excel(file_obj, user) -> tuple[int, int, list]:
    """Import clients from a 2-column Excel (Code, Name).

    Returns ``(created, updated, warnings)``. Spools the upload to disk so
    openpyxl can read large files safely, normalizes numeric codes, and uses
    bulk create/update so gunicorn does not time out on big lists.
    """
    import os
    import tempfile

    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    warnings: list[str] = []
    suffix = ".xlsx"
    name = getattr(file_obj, "name", "") or ""
    if name.lower().endswith(".xls") and not name.lower().endswith(".xlsx"):
        raise ValueError(
            "Old .xls format is not supported. Save the file as .xlsx and upload again."
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            if hasattr(file_obj, "chunks"):
                for chunk in file_obj.chunks():
                    tmp.write(chunk)
            else:
                tmp.write(file_obj.read())

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
        except InvalidFileException as exc:
            raise ValueError(
                "Could not read the Excel file. Use a valid .xlsx with two columns: Code, Name."
            ) from exc
        except Exception as exc:
            raise ValueError(f"Could not open the Excel file: {exc}") from exc

        ws = wb.active
        if ws is None:
            wb.close()
            raise ValueError("The Excel file has no active sheet.")

        # code -> Client, and lower(name) -> code (for uniqueness checks)
        by_code = {c.code: c for c in Client.objects.all().only("id", "code", "name")}
        name_owner = {c.name.strip().lower(): c.code for c in by_code.values() if c.name}

        to_create: list[Client] = []
        to_update: list[Client] = []
        seen_codes: set[str] = set()
        seen_names: set[str] = set()

        for idx, raw in enumerate(ws.iter_rows(values_only=True)):
            cells = list(raw or ()) + [None, None]
            code = normalize_client_code(cells[0])
            name_val = ("" if cells[1] is None else str(cells[1]).strip())
            if idx == 0:
                code_l = code.lower() if code else ""
                name_l = name_val.lower() if name_val else ""
                if code_l in {"code", "کد", "name", "نام"} or name_l in {
                    "name", "نام", "client", "مشتری", "client name",
                }:
                    continue
            if not code or not name_val:
                continue
            if len(code) > 20:
                warnings.append(f"Row {idx + 1}: code too long, skipped ({code[:24]}…)")
                continue
            if len(name_val) > 200:
                name_val = name_val[:200]

            name_key = name_val.lower()
            if code in seen_codes:
                warnings.append(f"Duplicate code in file skipped: {code}")
                continue
            seen_codes.add(code)

            existing = by_code.get(code)
            # Also match unpadded / alternate padded forms already in DB.
            if existing is None and code.isdigit():
                alt = str(int(code))
                alt3 = alt.zfill(3)
                existing = by_code.get(alt) or by_code.get(alt3)
                if existing is not None:
                    # Prefer keeping the normalized code on the existing row.
                    code = existing.code

            owner = name_owner.get(name_key)
            if owner and (existing is None or existing.code != owner):
                warnings.append(
                    f"Name “{name_val}” already used by client {owner}; "
                    f"row with code {code} skipped."
                )
                continue
            if name_key in seen_names:
                warnings.append(f"Duplicate name in file skipped: {name_val}")
                continue
            seen_names.add(name_key)

            if existing is not None:
                if existing.name != name_val:
                    existing.name = name_val
                    to_update.append(existing)
                    name_owner[name_key] = existing.code
                continue

            obj = Client(code=code, name=name_val, created_by=user)
            to_create.append(obj)
            by_code[code] = obj
            name_owner[name_key] = code

        wb.close()

        created = 0
        if to_create:
            Client.objects.bulk_create(to_create, batch_size=500)
            created = len(to_create)
        updated = 0
        if to_update:
            Client.objects.bulk_update(to_update, ["name"], batch_size=500)
            updated = len(to_update)

        sync_client_code_counter()
        return created, updated, warnings
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@transaction.atomic
def wipe_all_clients() -> int:
    """Delete every client and reset the client-code counter.

    Raises ``ProtectedError`` (or a clear ValueError) when cases still reference
    clients — admin must clear cases first, or use a fresh DB.
    """
    from django.db.models.deletion import ProtectedError

    count = Client.objects.count()
    try:
        Client.objects.all().delete()
    except ProtectedError as exc:
        raise ValueError(
            "Cannot wipe clients while cases still reference them. "
            "Clear or flush cases first, then wipe clients and re-upload."
        ) from exc
    SerialCounter.objects.update_or_create(
        key="client_code", defaults={"value": 0},
    )
    return count


@transaction.atomic
def delete_client_if_unused(client: Client) -> None:
    """Delete one client when no case uses its code; otherwise raise ValueError."""
    from django.db.models.deletion import ProtectedError

    if client.cases.exists():
        raise ValueError(
            f"Cannot delete client {client.code} — at least one case was created "
            f"with this client. Remove or reassign those cases first."
        )
    try:
        client.delete()
    except ProtectedError as exc:
        raise ValueError(
            f"Cannot delete client {client.code} — it is still referenced by cases."
        ) from exc


# ---------------------------------------------------------------------------
# Event logging
# ---------------------------------------------------------------------------
def _person_display_name(user) -> str:
    """Human name for freeze/display — prefer the linked Person's Latin name.

    Never prefer a vacant seat username (``_user6``): after release that is what
    ``get_full_name()`` falls back to, and it must not rewrite case history.
    """
    if user is None:
        return ""
    link = getattr(user, "person_link", None)
    person = getattr(link, "person", None) if link is not None else None
    if person is not None:
        en = (getattr(person, "full_name_en", None) or "").strip()
        if not en:
            en = f"{(person.first_name_en or '').strip()} {(person.last_name_en or '').strip()}".strip()
        if en:
            return en
        # Never surface Persian names on platform chrome / frozen snapshots.
        return (getattr(person, "username", None) or "").strip()
    name = (user.get_full_name() or "").strip()
    if name:
        return name
    uname = (user.username or "").strip()
    if uname.startswith("_"):
        return ""
    return uname


def _expert_display_for(user, code: str = "") -> str:
    """``Aria Parsa (205)`` — frozen onto the case, not the live seat username."""
    name = _person_display_name(user)
    code = (code or _expert_code_for(user) or "").strip()
    if name and code:
        return f"{name} ({code})"
    if name:
        return name
    if code:
        return f"({code})"
    return (getattr(user, "username", None) or "").strip()


def freeze_commercial_expert(case: Case, user=None) -> None:
    """Stamp commercial expert display once (create). Idempotent if already set."""
    if (case.commercial_expert_display or "").strip():
        return
    user = user or case.created_by
    case.commercial_expert_display = _expert_display_for(
        user, case.expert_code or _expert_code_for(user),
    )
    case.save(update_fields=["commercial_expert_display"])


def freeze_technical_expert(case: Case, user) -> None:
    """Stamp technical expert display once (first TO). Idempotent if already set."""
    if (case.technical_expert_display or "").strip():
        return
    if user is None:
        return
    case.technical_expert_display = _expert_display_for(user)
    case.save(update_fields=["technical_expert_display"])


def _actor_snapshot(user) -> tuple[str, str]:
    """(display name, title) frozen onto a CaseEvent at the instant it's written.

    Captured once, here, and never re-derived later — this is what keeps a
    later rename, promotion or departure from silently rewriting a historical
    timeline entry. See CaseEvent.actor_name / actor_role_label.

    While the actor holds the seat as a substitute (Translate), the role label
    is prefixed with English ``Substitute`` so history stays tagged. Detection
    uses the active seat from ``work_context`` (secondary seats) first, then
    the login user (when they signed in as the seat itself).
    """
    if user is None:
        return "", ""
    name = _person_display_name(user) or (user.username or "").strip()
    profile = getattr(user, "profile", None)
    title = profile.title_line if profile is not None else ""
    try:
        from people.role_nav import get_bound_work_seat, open_substitute_tenure
        candidates = []
        seat = get_bound_work_seat()
        if seat is not None:
            candidates.append(seat)
        if user is not None and (
            seat is None or getattr(seat, "pk", None) != getattr(user, "pk", None)
        ):
            candidates.append(user)
        is_sub = False
        for cand in candidates:
            if open_substitute_tenure(cand) is not None:
                is_sub = True
                break
        if is_sub:
            # Frozen at write time: "Substitute · Commercial · Expert"
            title = f"Substitute · {title}" if title else "Substitute"
    except Exception:
        pass
    return name, title


def log(case: Case, actor, action: str, *, comment: str = "",
        from_unit: str = "", to_unit: str = "",
        form_kind: str = "", form_version=None, two_stage: bool = False,
        side: str = "") -> CaseEvent:
    actor_name, actor_role_label = _actor_snapshot(actor)
    # actor_name/actor_role_label above freeze the real human's display name —
    # unchanged. The FK below is a different question: which User archive_scope
    # / inbox_filter_q will later match this event against. Those filter on the
    # ACTIVE SEAT (people.role_nav.work_context's ctx.seat_user), bound into
    # this ContextVar for the whole request precisely so this line could read
    # it (see the module docstring of people.role_nav). Writing the bare login
    # ``actor`` here instead, for someone working a secondary/substitute seat,
    # meant this event satisfied none of archive_scope's seat_user clauses —
    # the case vanished from that person's Archive AND Search after they acted,
    # with nothing refused and nothing logged wrong to look at.
    from people.role_nav import get_bound_work_seat
    routing_actor = get_bound_work_seat() or actor
    return CaseEvent.objects.create(
        case=case, actor=routing_actor, action=action, comment=comment,
        from_unit=from_unit, to_unit=to_unit,
        form_kind=form_kind, form_version=form_version, two_stage=two_stage,
        side=side, actor_name=actor_name, actor_role_label=actor_role_label,
    )


def add_comment(case: Case, actor, comment: str, side: str = "") -> CaseEvent:
    return log(case, actor, EventAction.COMMENT, comment=comment,
               from_unit=_unit_of(actor), side=side)


def _unit_of(user) -> str:
    profile = getattr(user, "profile", None)
    return profile.unit if profile else ""

def _last_sender_unit(case: Case) -> str:
    """Unit that most recently handed the case to its current holder."""
    ev = (case.events.filter(to_unit=case.holder_unit).exclude(from_unit="")
          .order_by("-created_at").first())
    return ev.from_unit if ev else ""


def _mark_unit_form_sent(case: Case, from_unit: str):
    """Mark the leaving unit's current form (every side) as sent."""
    kind = None
    if from_unit == Unit.TECHNICAL:
        kind = FormKind.TO
    elif from_unit == Unit.SUPPLY:
        kind = FormKind.PI
    if not kind:
        return
    for side in (case.sides or [""]):
        _mark_form_leaving(case.current_form(kind, side))


# ---------------------------------------------------------------------------
# Case creation
# ---------------------------------------------------------------------------
def _dedupe_exact_double(rows: list) -> list:
    """Guard against a doubled inquiry paste (N rows submitted as 2N).

    If the row list is exactly its first half repeated twice — the classic
    "grid + Excel both posted" doubling — collapse it back to the first half.
    Any other list is returned unchanged.
    """
    rows = list(rows or [])
    n = len(rows)
    if n >= 2 and n % 2 == 0:
        half = n // 2

        def _norm(r):
            r = r or {}
            return (
                str(r.get("description", "")).strip(),
                str(r.get("size", "")).strip(),
                str(r.get("quantity", "")).strip(),
                str(r.get("unit", "")).strip(),
                str(r.get("client_row", "")).strip(),
            )

        if [_norm(r) for r in rows[:half]] == [_norm(r) for r in rows[half:]]:
            return rows[:half]
    return rows


@transaction.atomic
def create_case(*, creator, kind: str, offer_type: str, client: Client,
                order_no: str = "", deadline=None, rows: list[dict] | None = None,
                price_type: str = "INTERNAL", client_commercial_expert: str = "",
                client_commercial_phone: str = "",
                client_technical_expert: str = "", client_technical_phone: str = "",
                marketing_label: str = "") -> Case:
    """Create a new case, its inquiry rows and the first Inquiry form."""
    serial = next_case_serial()
    ym = year_month_token()
    expert_code = (_expert_code_for(creator) or "").strip()
    if not expert_code or expert_code == "000":
        raise ValueError(
            "This commercial account has no internal code. "
            "Open People → Details and set a 3-digit internal code before creating a case."
        )

    doc_no = build_doc_no(
        kind=kind, ym=ym, expert_code=expert_code,
        client_code=client.code, serial=serial, version=0,
    )

    case = Case.objects.create(
        doc_no=doc_no, kind=kind, offer_type=offer_type, year_month=ym,
        expert_code=expert_code, client=client, serial=serial, version=0,
        order_no=order_no, deadline=deadline, price_type=price_type,
        client_commercial_expert=client_commercial_expert,
        client_commercial_phone=client_commercial_phone,
        client_technical_expert=client_technical_expert,
        client_technical_phone=client_technical_phone,
        marketing_label=marketing_label,
        attach_no=f"FT-ATT-{ym}-{serial:05d}",
        status=CaseStatus.DRAFT, holder_unit=Unit.COMMERCIAL, created_by=creator,
        commercial_expert_display=_expert_display_for(creator, expert_code),
    )

    # Internal & External cases run as two independent streams from the very
    # start, so every unit can act on each side separately.
    if case.has_internal and case.has_external:
        case.split_active = True
        case.internal_status = case.external_status = CaseStatus.DRAFT
        case.internal_holder = case.external_holder = Unit.COMMERCIAL
        case.save(update_fields=["split_active", "internal_status", "external_status",
                                 "internal_holder", "external_holder"])

    rows = rows or []
    prepped = []
    for idx, row in enumerate(rows, start=1):
        r = dict(row or {})
        if not str(r.get("client_row", "")).strip():
            r["client_row"] = idx
        prepped.append(r)
    rows = _dedupe_exact_double(prepped)
    line_items = []
    for idx, row in enumerate(rows, start=1):
        # The client row (#) defaults to the creation position; if the client's
        # exported file carried its own number we honour it.
        try:
            cr = int(str(row.get("client_row", "")).strip() or idx)
        except (ValueError, TypeError):
            cr = idx
        line_items.append(LineItem(
            case=case, row_no=idx, client_row=cr,
            description=str(row.get("description", "")).strip(),
            size=str(row.get("size", "")).strip(),
            unit=str(row.get("unit", "")).strip(),
            quantity=str(row.get("quantity", "")).strip(),
        ))
    if line_items:
        LineItem.objects.bulk_create(line_items)

    _snapshot_inquiry(case, creator)
    log(case, creator, EventAction.CREATE, to_unit=Unit.COMMERCIAL)
    return case


def _expert_code_for(user) -> str:
    # Prefer the person's internal code (source of truth), then profile.
    # Do not invent "000" — callers that need a doc code must require a real one.
    link = getattr(user, "person_link", None)
    person = getattr(link, "person", None) if link is not None else None
    if person is not None and str(getattr(person, "internal_code", "") or "").strip():
        return str(person.internal_code).strip()
    profile = getattr(user, "profile", None)
    if profile and str(getattr(profile, "internal_code", "")).strip():
        return str(profile.internal_code).strip()
    expert = getattr(user, "expert_code", None)
    if expert and getattr(expert, "code", None):
        return str(expert.code).strip()
    return ""


def sync_fresh_draft_price_type(case: Case) -> None:
    """Realign split flags / side holders after a fresh-draft price_type change.

    Call only while the case is still an unsent draft. Creates missing inquiry
    streams for newly active sides and removes forms for sides that are no
    longer part of ``price_type`` so Internal/External tabs match the save.
    """
    if case.has_internal and case.has_external:
        case.split_active = True
        case.internal_status = CaseStatus.DRAFT
        case.external_status = CaseStatus.DRAFT
        case.internal_holder = Unit.COMMERCIAL
        case.external_holder = Unit.COMMERCIAL
    else:
        case.split_active = False
        case.internal_status = CaseStatus.DRAFT if case.has_internal else ""
        case.external_status = CaseStatus.DRAFT if case.has_external else ""
        case.internal_holder = Unit.COMMERCIAL if case.has_internal else ""
        case.external_holder = Unit.COMMERCIAL if case.has_external else ""

    active = set(case.sides)
    for sc in (Side.INTERNAL, Side.EXTERNAL):
        if sc not in active:
            case.forms.filter(side=sc).delete()


def _snapshot_inquiry(case: Case, actor, version: int | None = None, sides=None,
                      table_override=None):
    """Store the current line items as an Inquiry form snapshot per side.

    ``sides`` restricts which side streams are written (defaults to all of the
    case's sides), so a split case can re-version one side independently.
    ``table_override`` — optional pre-built inquiry rows (keeps soft-delete /
    add markers that LineItem cannot store).
    """
    columns = ["#", "Item", "Description", "Size", "Qty", "Unit"]
    if table_override is not None:
        table = list(table_override)
    else:
        table = [
            {
                # "#" is the client's original row number (persists across versions);
                # "Item" is the live 1..N sequence.
                "#": (li.client_row or li.row_no),
                "Item": li.row_no,
                "Description": li.description,
                "Size": li.size,
                "Qty": li.quantity,
                "Unit": li.unit,
            }
            for li in case.line_items.all()
        ]
    meta = {"Document kind": case.get_kind_display() if hasattr(case, "get_kind_display") else case.kind,
            "Offer type": case.get_offer_type_display() if hasattr(case, "get_offer_type_display") else case.offer_type}
    target_sides = sides if sides is not None else (case.sides or [""])
    form = None
    for side in target_sides:
        if version is not None:
            form = CaseForm(case=case, kind=FormKind.INQUIRY, side=side, version=version,
                            created_by=actor, unit_at_creation=Unit.COMMERCIAL)
        else:
            form = case.current_form(FormKind.INQUIRY, side)
            if form is None:
                # Form versions are 0-based (the first inquiry is v00).
                form = CaseForm(case=case, kind=FormKind.INQUIRY, side=side, version=0,
                                created_by=actor, unit_at_creation=Unit.COMMERCIAL)
        form.columns = columns
        form.table = table
        form.meta = meta
        form.is_current = True
        form.save()
        form.make_current()
    return form


# ---------------------------------------------------------------------------
# Form versioning (TO / PI)
# ---------------------------------------------------------------------------
def _norm_cell(v) -> str:
    """Normalise a single inquiry cell for change-detection (trim + str)."""
    return str(v if v is not None else "").strip()


def _norm_client_row(v) -> str:
    """Normalize a client row (#) for stable comparison across versions."""
    s = _norm_cell(v)
    if not s:
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def _inquiry_forms_qs(case: Case, side: str = ""):
    """Inquiry forms for a side, falling back to blank-side legacy rows."""
    qs = case.forms.filter(kind=FormKind.INQUIRY)
    if side:
        side_qs = qs.filter(side=side)
        if side_qs.exists():
            return side_qs
    if case.primary_side:
        primary_qs = qs.filter(side=case.primary_side)
        if primary_qs.exists():
            return primary_qs
    return qs.filter(side="")


def inquiry_v00_table(case: Case, side: str = "") -> list:
    """Return the original inquiry baseline (v00, or earliest version if v00 missing)."""
    qs = _inquiry_forms_qs(case, side)
    # Which GENERATION of v00 is wanted has to be said out loud. A two-stage
    # upgrade keeps the version NUMBER it supersedes, so a case upgraded while
    # still at version 00 has two rows with version=0 — the original and
    # "00 · Two Stage" — and CaseForm.Meta orders the two-stage one first (it is
    # the newer snapshot, which is right for "current" and wrong here). This is
    # the BASELINE the + / − row marks are measured against: the client's
    # original inquiry, before anything was added to it. So ask for the oldest
    # generation explicitly, and settle a remaining tie by id rather than
    # leaving it to the database.
    form = qs.filter(version=0).order_by("two_stage", "id").first()
    if form is None:
        form = qs.order_by("version", "two_stage", "id").first()
    return list(form.table or []) if form else []


def v00_client_row_set(case: Case, side: str = "") -> set:
    """Client row numbers (#) present in the inquiry baseline (v00 / earliest)."""
    return {
        _norm_client_row(r.get("#", r.get("client_row", "")))
        for r in inquiry_v00_table(case, side)
        if _norm_client_row(r.get("#", r.get("client_row", "")))
    }


def apply_inquiry_row_marks_vs_v00(rows, v00_rows: set) -> list:
    """Recompute soft-add / soft-delete marks for the new-version inquiry editor.

    + only when # was not in the inquiry baseline (v00 / earliest version);
    − when the row is soft-deleted. Rows that existed in the baseline carry no +.
    """
    baseline = {_norm_client_row(x) for x in (v00_rows or set()) if _norm_client_row(x)}
    out = []
    for r in rows or []:
        r = dict(r or {})
        cr = _norm_client_row(r.get("client_row", r.get("#", "")))
        is_del = str(r.get("_deleted", r.get("deleted", "")) or "") == "1"
        if is_del:
            r["_deleted"] = "1"
            r.pop("_added", None)
            r.pop("added", None)
        elif cr and baseline and cr not in baseline:
            r["_added"] = "1"
            r.pop("_deleted", None)
            r.pop("deleted", None)
        else:
            r.pop("_added", None)
            r.pop("added", None)
            if not is_del:
                r.pop("_deleted", None)
                r.pop("deleted", None)
        out.append(r)
    return out


def _inquiry_comment_map(table, meta=None) -> dict:
    """Commercial's row notes for one inquiry version: ``{client row # -> note}``.

    The note is written *about* a row but it is not data *of* the row. It is
    something Commercial says while handing a version over ("please confirm
    brand"), so it belongs to the version — ``CaseForm.meta``, beside the other
    per-version facts like ``update_price`` and ``_sent_to`` — and never to
    ``CaseForm.table``. That placement is the whole point: every export, the
    inquiry table and the proforma detail tables all read ``table``, so a note
    kept out of ``table`` cannot leak onto a client-facing row or a sheet.

    Keyed by client row (#), the identity ``_rb_fingerprint`` and
    ``_index_rows_by_client`` already use to follow one row across versions, and
    the number the announcement itself prints.

    Versions saved before the note moved off the row still carry it inside the
    row dict. They are read here in that old shape, so an old case still opens,
    still announces the text it always announced, and needs no migration; the
    first new version saved from such a case lifts the notes into meta as it
    passes.
    """
    stored = (meta or {}).get("_comm_comments")
    if isinstance(stored, dict):
        return {str(k): str(v or "").strip()
                for k, v in stored.items() if str(v or "").strip()}
    legacy = {}
    for r in (table or []):
        r = r or {}
        note = str(r.get("_comm_comment", r.get("comment", "")) or "").strip()
        if not note:
            continue
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if cr:
            legacy[cr] = note
    return legacy


def _inquiry_signature(rows, comments=None) -> list:
    """Return a comparable signature of an inquiry table.

    Two inquiry tables are considered IDENTICAL (no new version warranted) when
    they carry the same ordered rows with the same client row number (#) and the
    same Description / Size / Qty / Unit / soft-delete / add markers. Soft-
    deleting a row (keeping it with ``_deleted=1``) or adding a row changes the
    signature. The live "Item" sequence (1..N) is intentionally ignored.

    The Commercial row note still takes part, so a version whose only edit is a
    comment is a real change and does get saved. It now arrives as ``comments``
    (the version's note map) rather than out of the row; passing nothing falls
    back to reading it off the rows, which is how an old version compares.
    """
    if comments is None:
        comments = _inquiry_comment_map(rows)
    sig = []
    for r in (rows or []):
        r = r or {}
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        sig.append((
            cr,
            _norm_cell(r.get("Description", r.get("description", ""))),
            _norm_cell(r.get("Size", r.get("size", ""))),
            _norm_cell(r.get("Qty", r.get("quantity", ""))),
            _norm_cell(r.get("Unit", r.get("unit", ""))),
            "1" if str(r.get("_deleted", "") or "") == "1" else "0",
            "1" if str(r.get("_added", "") or "") == "1" else "0",
            _norm_cell(comments.get(cr, "")),
        ))
    return sig


def _inquiry_content_signature(rows) -> list:
    """Like ``_inquiry_signature`` but ignores the Commercial row comments.

    Used so “Update price” version labelling cares about real table edits
    (cells / add / delete), not comment-only changes. It takes no ``comments``
    argument on purpose — there is nothing to ignore once the note lives on the
    version, and an old version's ``_comm_comment`` row key is not among the
    keys read below, so both shapes stay comment-blind here.
    """
    sig = []
    for r in (rows or []):
        r = r or {}
        sig.append((
            _norm_cell(r.get("#", r.get("client_row", ""))),
            _norm_cell(r.get("Description", r.get("description", ""))),
            _norm_cell(r.get("Size", r.get("size", ""))),
            _norm_cell(r.get("Qty", r.get("quantity", ""))),
            _norm_cell(r.get("Unit", r.get("unit", ""))),
            "1" if str(r.get("_deleted", "") or "") == "1" else "0",
            "1" if str(r.get("_added", "") or "") == "1" else "0",
        ))
    return sig


def _row_change_summary(prior_table, new_table) -> str:
    """Short timeline note: ``delete row: 3, 4   add row: 8, 9   edited: 2``."""
    prior_by = {}
    for r in prior_table or []:
        cr = _norm_cell((r or {}).get("#", (r or {}).get("client_row", "")))
        if cr:
            prior_by[cr] = r or {}
    deleted, added, edited = [], [], []

    def _content_sig(row):
        row = row or {}
        return (
            _norm_cell(row.get("Description", row.get("description", ""))),
            _norm_cell(row.get("Size", row.get("size", ""))),
            _norm_cell(row.get("Qty", row.get("quantity", ""))),
            _norm_cell(row.get("Unit", row.get("unit", ""))),
        )

    for r in new_table or []:
        r = r or {}
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if not cr:
            continue
        prior = prior_by.get(cr) or {}
        was_del = str(prior.get("_deleted", "") or "") == "1"
        is_del = str(r.get("_deleted", "") or "") == "1"
        is_add = str(r.get("_added", "") or "") == "1" or cr not in prior_by
        if is_del and not was_del:
            deleted.append(cr)
        if is_add:
            if cr not in added:
                added.append(cr)
            continue
        # Content edit on an existing, non-deleted row (add/delete already covered).
        if is_del or was_del:
            continue
        if prior and _content_sig(prior) != _content_sig(r):
            edited.append(cr)
    parts = []
    if deleted:
        parts.append("delete row: " + ", ".join(deleted))
    if added:
        parts.append("add row: " + ", ".join(added))
    if edited:
        parts.append("edited: " + ", ".join(edited))
    return "   ".join(parts)


def _with_auto_notes(user_comment: str, *auto_parts: str) -> str:
    """Merge optional user comment with brief English auto notes for the timeline."""
    autos = [p.strip() for p in auto_parts if p and str(p).strip()]
    auto = "   ".join(autos)
    user = (user_comment or "").strip()
    if user and auto:
        return f"{user} — {auto}"
    return user or auto


def _flagged_client_rows(table, flag_key: str) -> list:
    """Client row #s where ``flag_key`` is ``'1'`` (skips soft-deleted rows)."""
    out = []
    for r in table or []:
        r = r or {}
        if str(r.get("_deleted", "") or "") == "1":
            continue
        if str(r.get(flag_key, "") or "") != "1":
            continue
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if cr and cr not in out:
            out.append(cr)
    return out


def _rb_fingerprint(table) -> dict:
    """Per-row remark/brand snapshot used to detect changes across handoffs."""
    out = {}
    for r in table or []:
        r = r or {}
        if str(r.get("_deleted", "") or "") == "1":
            continue
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if not cr:
            continue
        out[cr] = {
            "remark": str(r.get("ریمارک", "") or "").strip(),
            "brand": str(r.get("BRAND", "") or "").strip(),
        }
    return out


def _index_rows_by_client(table) -> dict:
    out = {}
    for r in table or []:
        r = r or {}
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if cr:
            out[cr] = r
    return out


def _rb_change_summary(form, *, counterpart_table=None, only_if_prior: bool = False) -> str:
    """Brief ``remark: …   brand: …`` for rows whose remark/brand changed.

    Compares the current table to ``form.meta['_sent_rb']`` (last handoff
    snapshot). When there is no prior snapshot:
    • remarks: every non-empty remark (first-time notes matter);
    • brands: only rows that differ from the counterpart form's BRAND
      (avoids listing every coded brand on the first TO→Supply).
    ``only_if_prior`` skips the whole note when no snapshot exists (used on
    first Technical→Supply so initial coding brands stay quiet).
    """
    if form is None:
        return ""
    prior = dict((form.meta or {}).get("_sent_rb") or {})
    if only_if_prior and not prior:
        return ""
    cp_by = _index_rows_by_client(counterpart_table)
    remark_rows, brand_rows = [], []
    for r in (form.table or []):
        r = r or {}
        if str(r.get("_deleted", "") or "") == "1":
            continue
        cr = _norm_cell(r.get("#", r.get("client_row", "")))
        if not cr:
            continue
        rem = str(r.get("ریمارک", "") or "").strip()
        br = str(r.get("BRAND", "") or "").strip()
        prev = prior.get(cr) or {}
        if prior:
            prev_rem = str(prev.get("remark", "") or "").strip()
            prev_br = str(prev.get("brand", "") or "").strip()
        else:
            prev_rem = ""
            cp = cp_by.get(cr) or {}
            prev_br = str(cp.get("BRAND", "") or "").strip()
        if rem and rem != prev_rem:
            remark_rows.append(cr)
        if br != prev_br and (br or prev_br):
            brand_rows.append(cr)
    parts = []
    if remark_rows:
        parts.append("remark: " + ", ".join(remark_rows))
    if brand_rows:
        parts.append("brand: " + ", ".join(brand_rows))
    return "   ".join(parts)


def _store_sent_rb_fingerprint(form) -> None:
    """Persist remark/brand snapshot on the form when it leaves the unit."""
    if form is None:
        return
    meta = dict(form.meta or {})
    meta["_sent_rb"] = _rb_fingerprint(form.table)
    form.meta = meta


def form_published_to_unit(form, unit: str) -> bool:
    """True when ``unit`` may see this form snapshot on case detail / export.

    • ``meta["_sent_to"]`` present → only listed recipients (after a handoff).
    • Legacy ``sent=True`` without ``_sent_to`` → visible to any non-owner
      (pre-recipient-visibility data).
    """
    if form is None or not unit:
        return False
    meta = form.meta or {}
    if "_sent_to" in meta:
        return unit in (meta.get("_sent_to") or [])
    return bool(form.sent)


def _add_form_recipient(form, to_unit: str, *, store_rb: bool = False) -> None:
    """Mark one form sent and append ``to_unit`` to ``meta["_sent_to"]``."""
    if form is None or not to_unit:
        return
    meta = dict(form.meta or {})
    sent_to = [u for u in (meta.get("_sent_to") or []) if u]
    if to_unit not in sent_to:
        sent_to.append(to_unit)
    meta["_sent_to"] = sent_to
    if store_rb:
        meta["_sent_rb"] = _rb_fingerprint(form.table)
    form.meta = meta
    form.sent = True
    form.save(update_fields=["meta", "sent"])


def _freeze_signatory(form) -> None:
    """Capture who signs ``form``, now, and never fail the caller.

    The savepoint wraps the whole freeze, not just the row insert: freezing
    reads the database several times before it writes, and this runs inside the
    workflow's own transaction. On PostgreSQL any failed statement inside an
    open transaction marks it for rollback, and catching the exception does not
    undo that — the caller's next statement would then die and the handoff the
    user just performed would return a server error.
    """
    if form is None:
        return
    try:
        from .export_data import freeze_signature_snapshot
        with transaction.atomic():
            freeze_signature_snapshot(form)
    except Exception:
        logger.exception("Could not freeze signatory for form %s",
                         getattr(form, "pk", "?"))


def _freeze_commercial_documents(case, side: str = "") -> None:
    """Freeze the signatory on every current proforma at final approval.

    The counterpart to the Technical Offer being frozen when it leaves
    Technical. A proforma is produced by Supply but signed by the *Commercial*
    manager, so it is not issued when it leaves Supply — Commercial still has
    to approve it. Freezing it here is what makes the name on the document the
    person who actually issued it.
    """
    try:
        with transaction.atomic():
            for form in _forms_for_side(case, FormKind.PI, side):
                _freeze_signatory(form)
    except Exception:
        logger.exception("Could not freeze proforma signatories for case %s",
                         getattr(case, "pk", "?"))


def _publish_current_forms_to(case, to_unit: str, side: str = None,
                              leaving_unit: str = None) -> None:
    """Publish every current Inquiry/TO/PI to ``to_unit`` (recipient visibility).

    On handoff, the receiving unit may see the then-current snapshots of all
    forms that exist. ``leaving_unit`` (Technical/Supply) refreshes the
    remark/brand fingerprint on that unit's leaving form (TO/PI).
    """
    if not to_unit:
        return
    if side in (Side.INTERNAL, Side.EXTERNAL):
        sides = [side]
    else:
        sides = list(case.sides or [""])
    leaving_kind = None
    if leaving_unit == Unit.TECHNICAL:
        leaving_kind = FormKind.TO
    elif leaving_unit == Unit.SUPPLY:
        leaving_kind = FormKind.PI
    for sc in sides:
        for kind in (FormKind.INQUIRY, FormKind.TO, FormKind.PI, FormKind.PURCHASE_INVOICE):
            form = case.current_form(kind, sc)
            if form is None:
                continue
            _add_form_recipient(
                form, to_unit, store_rb=(leaving_kind is not None and kind == leaving_kind))
            # Freeze the Technical Offer's signatory when it leaves Technical.
            #
            # This is the only handoff in the funnel that actually issues a
            # document. A Technical Offer is both produced and signed by
            # Technical, so the moment it leaves that unit it stops being work
            # in progress. Everything else passing through here is movement,
            # not issuance: a proforma is produced by Supply but signed by the
            # Commercial manager, so it is frozen at final approval instead.
            #
            # Deliberately NOT written as "the leaving unit signs this kind".
            # That reads well and is wrong: leaving_unit is Commercial on
            # several call sites below, so the general form would freeze a
            # signatory onto the proforma every time Commercial sent a case
            # back for rework — pinning a name onto a document nobody issued.
            if leaving_unit == Unit.TECHNICAL and kind == FormKind.TO:
                _freeze_signatory(form)


def _mark_form_leaving(form, to_unit: str = None) -> None:
    """Snapshot remark/brand and mark the form as sent (one save).

    When ``to_unit`` is set, append it to ``meta["_sent_to"]`` even if the form
    was already sent (re-handoff of the same version to a new recipient).
    """
    if form is None:
        return
    if to_unit:
        _add_form_recipient(form, to_unit, store_rb=True)
        return
    if form.sent:
        return
    _store_sent_rb_fingerprint(form)
    form.sent = True
    form.save(update_fields=["meta", "sent"])


def _forms_for_side(case, kind: str, side: str = ""):
    forms = _iter_current_forms(case, kind)
    if side in (Side.INTERNAL, Side.EXTERNAL):
        forms = [f for f in forms if f.side == side]
    return forms


def _handoff_auto_notes(case, kind: str, side: str = "", *,
                        include_issue: bool = False,
                        include_unsuppliable: bool = False,
                        include_rb: bool = False,
                        rb_only_if_prior: bool = False) -> str:
    """Build brief English auto notes for a handoff timeline entry."""
    parts = []
    counterpart_kind = FormKind.PI if kind == FormKind.TO else (
        FormKind.TO if kind == FormKind.PI else None)
    for form in _forms_for_side(case, kind, side):
        if include_issue:
            rows = _flagged_client_rows(form.table, "_issue")
            if rows:
                parts.append("tech problem: " + ", ".join(rows))
        if include_unsuppliable:
            rows = _flagged_client_rows(form.table, "_unsuppliable")
            if rows:
                parts.append("not suppliable: " + ", ".join(rows))
        if include_rb:
            cp_table = None
            if counterpart_kind:
                cp = case.current_form(counterpart_kind, form.side or side or "")
                cp_table = cp.table if cp else None
            rb = _rb_change_summary(
                form, counterpart_table=cp_table, only_if_prior=rb_only_if_prior)
            if rb:
                parts.append(rb)
    return "   ".join(parts)


def _inquiry_tables_equal(rows_a, rows_b, comments_a=None, comments_b=None) -> bool:
    """True when two inquiry tables have identical content (see _inquiry_signature).

    The note maps are passed in because the notes no longer travel inside the
    rows; omitting them compares whatever the rows themselves carry, which is
    what an old (pre-move) version has.
    """
    return (_inquiry_signature(rows_a, comments_a)
            == _inquiry_signature(rows_b, comments_b))


def _inquiry_tables_content_equal(rows_a, rows_b) -> bool:
    """True when tables match ignoring the Commercial row comments."""
    return _inquiry_content_signature(rows_a) == _inquiry_content_signature(rows_b)


def _pi_remark_text(pr):
    """PI remark New value as saved (empty New stays empty — never fall back to Prev)."""
    return str((pr or {}).get("ریمارک", "") or "").strip()


def _promote_remark_split(case, kind, side, table):
    """Finalise the REMARK Old/New split on save (point 5).

    • Split row: committed ``ریمارک`` = typed New, including intentional empty.
      Never collapse empty New into Old (TO and PI) — calm Save→Edit must
      restore Prev + empty New, and exports must show blank.
    • Keep ``_prev_remark`` + ``_remark_split`` so calm re-edit restores Prev/New.
    • TO: when a split row is coded again, record the supplier remark it answered
      in ``_pf_ack`` so reopening the TO does not clear the fresh code (until the
      supplier changes the remark once more).
    • PI: after absorbing a Technical remark round, store ``_remark_ack`` (= TO
      ``_pf_ack``) so the next handoff does not re-split unchanged remark rows.
    • Do NOT force ``_remark_split`` on PI just because a remark exists — that
      would invent Prev/New on the next calm Edit.
    """
    if not table:
        return table
    pf_by_cr = {}
    to_pf_ack_by_cr = {}
    if kind == FormKind.TO:
        pi = case.current_form(FormKind.PI, side)
        if pi and pi.table:
            for pr in pi.table:
                cr = _norm_cell(pr.get("#", pr.get("client_row", "")))
                rem = _pi_remark_text(pr)
                if cr and rem:
                    pf_by_cr[cr] = rem
    elif kind == FormKind.PI:
        to = case.current_form(FormKind.TO, side)
        if to and to.table:
            for tr in to.table:
                cr = _norm_cell(tr.get("#", tr.get("client_row", "")))
                if cr:
                    to_pf_ack_by_cr[cr] = str(tr.get("_pf_ack", "") or "").strip()
    out = []
    for row in (table or []):
        r = dict(row)
        # Ephemeral UI markers — recomputed from pf vs _pf_ack on next TO open.
        was_pending = str(r.get("_pf_pending", "") or "") == "1"
        r.pop("_pf_pending", None)
        r.pop("_pf_text", None)
        if str(r.get("_remark_split", "") or "") == "1":
            new = str(r.get("ریمارک", "") or "").strip()
            old = str(r.get("_prev_remark", "") or "").strip()
            # Keep empty New empty on both TO and PI.
            r["_prev_remark"] = old
            r["ریمارک"] = new
            r["_remark_split"] = "1"
            if kind == FormKind.PI:
                cr = _norm_cell(r.get("#", r.get("client_row", "")))
                ack = to_pf_ack_by_cr.get(cr, "") or (new or old)
                if ack:
                    r["_remark_ack"] = ack
            else:
                # Resolved Confirm/Reject → keep Prev/New even when equal.
                # Pending + identical → drop so every row is not forced into split.
                if (not was_pending) or (old != new):
                    cr = _norm_cell(r.get("#", r.get("client_row", "")))
                    pf = pf_by_cr.get(cr, "")
                    if pf and (not was_pending or str(r.get("کد", "") or "").strip()):
                        r["_pf_ack"] = pf
                else:
                    r.pop("_prev_remark", None)
                    r.pop("_remark_split", None)
                    r["ریمارک"] = new or old
        if str(r.get("_remark_split", "") or "") != "1":
            r.pop("_prev_remark", None)
            r.pop("_remark_split", None)
            # Keep ``_remark_ack`` on PI so a later handoff knows this remark
            # round was already absorbed (avoids re-splitting unchanged rows).
            if kind != FormKind.PI:
                r.pop("_remark_ack", None)
            elif kind == FormKind.PI:
                # Even without a visible split, if this PI remark equals the TO
                # ack, persist the absorb marker so calm re-edit stays unlocked.
                cr = _norm_cell(r.get("#", r.get("client_row", "")))
                to_ack = to_pf_ack_by_cr.get(cr, "")
                rem = _pi_remark_text(r)
                if to_ack and rem and to_ack == rem:
                    r["_remark_ack"] = to_ack
        out.append(r)
    return out


def _promote_brand_split(case, kind, side, table):
    """Finalise the BRAND Old/New split on save.

    • Split row: committed ``BRAND`` = typed New, including intentional empty.
      Never collapse empty New into Old (TO and PI).
    • After Confirm/Reject (no longer pending): always keep Prev/New + set
      ``_brand_ack`` so calm re-edit restores the same pair (even when Reject
      made Prev==New).
    • Still-pending save with empty New that equals Old: drop the split so
      unchanged rows do not all become Prev/New on the next Edit.
    • PI: after absorbing Technical's confirmed brand, store ``_brand_ack``.
    • TO non-split: set ``_brand_ack`` to the current PI brand so a Technical-
      only Brand edit does not reopen Prev/New on calm re-edit.
    """
    if not table:
        return table
    pi_brand_by_cr = {}
    if kind == FormKind.TO:
        pi = case.current_form(FormKind.PI, side)
        if pi and pi.table:
            for pr in pi.table:
                cr = _norm_cell(pr.get("#", pr.get("client_row", "")))
                if cr:
                    pi_brand_by_cr[cr] = str(pr.get("BRAND", "") or "")

    def _ns(v):
        return "".join(str(v or "").split())

    out = []
    for row in (table or []):
        r = dict(row)
        was_pending = str(r.get("_brand_pending", "") or "") == "1"
        # Ephemeral UI markers — recomputed on next open.
        r.pop("_brand_pending", None)
        r.pop("_brand_pf_text", None)
        if str(r.get("_brand_split", "") or "") == "1":
            new = str(r.get("BRAND", "") or "")
            old = str(r.get("_prev_brand", "") or "")
            # Keep New as typed (blank New stays blank — do not copy Old).
            if "".join(new.split()):
                r["BRAND"] = new
            else:
                r["BRAND"] = ""
            same = _ns(old) == _ns(r.get("BRAND", ""))
            # Resolved Confirm/Reject → always persist Prev/New.
            # Pending + identical Prev/New → drop (false "all rows split").
            if (not was_pending) or (not same):
                r["_prev_brand"] = old
                r["_brand_split"] = "1"
                if kind == FormKind.TO:
                    cr = _norm_cell(r.get("#", r.get("client_row", "")))
                    if cr in pi_brand_by_cr:
                        if not was_pending or str(r.get("کد", "") or "").strip():
                            r["_brand_ack"] = pi_brand_by_cr[cr]
                elif kind == FormKind.PI:
                    # Absorb marker must stay the Technical TO brand, NOT Supply's
                    # typed New. Overwriting ack with New made calm re-edit
                    # re-absorb (Prev=New, New=TO) and unlock TIME/price.
                    to = case.current_form(FormKind.TO, side)
                    to_b = ""
                    if to and to.table:
                        cr = _norm_cell(r.get("#", r.get("client_row", "")))
                        for tr in to.table:
                            if _norm_cell(tr.get("#", tr.get("client_row", ""))) == cr:
                                to_b = str(tr.get("BRAND", "") or "")
                                break
                    existing_ack = str(r.get("_brand_ack", "") or "").strip()
                    if to_b:
                        r["_brand_ack"] = to_b
                    elif existing_ack:
                        r["_brand_ack"] = existing_ack
                    else:
                        r["_brand_ack"] = str(r.get("BRAND", "") or "")
                    r["_brand_baseline"] = to_b or existing_ack or str(r.get("BRAND", "") or "")
            else:
                r.pop("_prev_brand", None)
                r.pop("_brand_split", None)
        # Non-split rows: drop leftover Prev/New flags. On TO, record the current
        # PI brand as ``_brand_ack`` ("seen") so a Technical-only Brand edit does
        # not reopen Prev/New against an unchanged Supply brand on calm re-edit.
        # On PI, KEEP ``_brand_ack`` after absorbing a Technical answer so the
        # next handoff does not re-split that same row.
        if str(r.get("_brand_split", "") or "") != "1":
            r.pop("_prev_brand", None)
            r.pop("_brand_split", None)
            if kind == FormKind.TO:
                cr = _norm_cell(r.get("#", r.get("client_row", "")))
                if cr in pi_brand_by_cr:
                    ack_val = pi_brand_by_cr[cr]
                    if str(ack_val).strip().lower() in ("nan", "none", "<na>", "null"):
                        ack_val = ""
                    r["_brand_ack"] = ack_val
                else:
                    r.pop("_brand_ack", None)
            # PI non-split: keep existing _brand_ack (absorb marker); only drop
            # densified NaN / null placeholders.
            elif kind == FormKind.PI:
                ack_keep = r.get("_brand_ack", None)
                try:
                    if ack_keep is not None and isinstance(ack_keep, float) and (
                        ack_keep != ack_keep  # NaN
                    ):
                        ack_keep = None
                except Exception:
                    pass
                if ack_keep is not None and str(ack_keep).strip().lower() in (
                    "nan", "none", "<na>", "null",
                ):
                    r.pop("_brand_ack", None)
                elif ack_keep is None:
                    r.pop("_brand_ack", None)
        out.append(r)
    return out


def form_behind_inquiry(form, inq) -> bool:
    """True when ``form`` (a TO/PI snapshot) has been left behind by ``inq``.

    Two different things put a form behind its inquiry, and only counting the
    first one is what made a two-stage upgrade look like an ordinary edit:

    * the inquiry moved to a HIGHER version number, or
    * the inquiry stayed at the SAME number but changed GENERATION — the case
      was upgraded to a TO & PI two stage, so "01" became "01 · Two Stage".

    The second case has to count, because a two-stage snapshot is a new version
    in every sense except its number: the unit must branch a fresh one rather
    than overwrite the offer it already sent. This is the one definition, used
    by the case page's build-button mode and by the coding tool, so those two
    cannot drift apart and disagree about whether a save is an edit or a new
    version. (``save_form`` keeps its own arithmetic because it also has to
    handle the case with no inquiry at all.)
    """
    if form is None or inq is None:
        return False
    if form.version < inq.version:
        return True
    return bool(form.two_stage) != bool(inq.two_stage)


@transaction.atomic
def save_form(case: Case, *, kind: str, columns: list, table: list, meta: dict,
              actor, new_version: bool = False, is_edit: bool = False,
              side: str = "") -> CaseForm:
    """Create or update a TO/PI form snapshot for a given side.

    ``new_version`` True copies the current form into a new, higher version
    (00 -> 01 -> 02 …). Otherwise the current version's data is replaced.
    ``is_edit`` True means this save only rewrites the CURRENT version in
    place, and such a save is NOT written to the timeline at all (see the
    logging block at the end of this function).
    """
    current = case.current_form(kind, side)
    # Commercial FX-only clones (e.g. TO 03 for unit conversion) are not work
    # items for Technical/Supply. When the live inquiry is a *real* revision,
    # treat the latest real snapshot as current so New version lands on that
    # inquiry number (e.g. 04), never on the skipped currency-only intermediate.
    # While Commercial is still on a currency-conversion-only inquiry, keep the
    # clone so Proforma conversion can update it in place.
    if (current is not None and form_is_currency_conversion_only(current)
            and not is_currency_conversion_only(case, side or "")):
        reals = list(
            # Newest first. ``-two_stage`` sits between the two because a
            # two-stage snapshot keeps the version NUMBER it supersedes, so
            # ordering by version alone would leave "01" and "01 · Two Stage"
            # tied and let the database pick the "latest".
            case.forms.filter(kind=kind, side=side or "")
            .order_by("-version", "-two_stage", "-id")
        )
        current = next(
            (f for f in reals if not form_is_currency_conversion_only(f)), None
        )
    inq = case.current_form(FormKind.INQUIRY, side)
    inq_version = inq.version if inq else 0
    # A TO/PI snapshot is tied to BOTH the Inquiry version AND the Inquiry's
    # two-stage generation. Its version number EQUALS the current Inquiry version,
    # and it inherits the Inquiry's ``two_stage`` flag. This makes the relationship
    # explicit and self-enforcing:
    #   • "Send to client" simply checks every form is at the Inquiry version AND
    #     in the same generation (two-stage vs not).
    #   • When a new Inquiry version appears — or the case is upgraded to a TO & PI
    #     two-stage generation at the SAME number — the existing TO/PI is now
    #     behind it, so building again creates the matching new version/generation
    #     (an "edit" on an up-to-date form just overwrites the same record).
    inq_two_stage = bool(inq.two_stage) if inq else False
    if current is None:
        version = inq_version
    elif current.version < inq_version:
        # The inquiry moved ahead -> this build is the new matching version.
        version = inq_version
    elif bool(current.two_stage) != inq_two_stage:
        # Same number but a different generation (the case was upgraded to a
        # two-stage TO & PI): build the matching generation at the same number.
        version = inq_version
    elif new_version:
        version = current.version + 1
    else:
        version = current.version

    # Server-side guard: when the case offer type is TO-only (not TO & PI), the
    # Proforma must NOT carry any prices, no matter what the client sent. Strip
    # the UNIT PRICE column (and the persisted source/raw markers) so a bypassed
    # or stale client can never sneak a price into a TO-only Proforma.
    if kind == FormKind.PI and not case.needs_pricing:
        cleaned = []
        for row in (table or []):
            r = dict(row)
            for key in ("UNIT PRICE", "_price_source", "_unit_price_raw"):
                if key in r:
                    r[key] = ""
            cleaned.append(r)
        table = cleaned

    # Point 5: finalise the REMARK Old/New split — promote a typed New remark to
    # the committed value (keeping Old when New is blank) and record the answered
    # supplier remark on the TO so a re-coded row is not re-cleared on reopen.
    table = _promote_remark_split(case, kind, side, table)
    table = _promote_brand_split(case, kind, side, table)

    # Same seat-vs-login distinction as cases.services.log: created_by feeds
    # archive_scope's ``created_by=seat_user`` clause, so it must be the active
    # seat, not the bare login, or a form built under a secondary seat quietly
    # drops out of that seat's own Archive/Search. signed_by (below) and
    # freeze_technical_expert stay on ``actor`` — those are the human on record,
    # not a routing key.
    from people.role_nav import get_bound_work_seat
    routing_actor = get_bound_work_seat() or actor
    form, _created = CaseForm.objects.get_or_create(
        case=case, kind=kind, side=side, version=version, two_stage=inq_two_stage,
        defaults={"created_by": routing_actor, "unit_at_creation": _unit_of(routing_actor)},
    )
    form.columns = columns
    form.table = table
    # Preserve server-only handoff fingerprints; the tool meta payload does not
    # round-trip ``_sent_rb`` and must not wipe it on every save. New versions
    # inherit the prior form's snapshot so remark/brand diffs still work.
    prev_meta = dict(form.meta or {})
    new_meta = dict(meta or {})
    if "_sent_rb" not in new_meta:
        if "_sent_rb" in prev_meta:
            new_meta["_sent_rb"] = prev_meta["_sent_rb"]
        elif current is not None and getattr(current, "pk", None) != form.pk:
            inherited = dict(current.meta or {}).get("_sent_rb")
            if inherited:
                new_meta["_sent_rb"] = inherited
    # Fresh edit: not published to any unit until the next handoff.
    new_meta.pop("_sent_to", None)
    # "Update price"/"currency conversion" chips are decided once, on the
    # Inquiry, by commit_inquiry_version — they are never in the tool's own
    # POST (itemcoder/bridge.py has no notion of them), so a TO/PI built or
    # edited off THIS inquiry version must copy them down here or its own
    # version chip and _form_table.html badge silently read as a bare
    # "Version NN" while the Inquiry tab, reading the same flags off the
    # Inquiry's own CaseForm, correctly shows "Version NN · Update price".
    inq_meta = (inq.meta or {}) if inq else {}
    for flag in ("update_price", "update_price_requested", "unit_convert_requested"):
        if inq_meta.get(flag):
            new_meta[flag] = True
        else:
            new_meta.pop(flag, None)
    form.meta = new_meta
    form.two_stage = inq_two_stage
    form.signed_by = actor
    form.sent = False  # freshly built/edited -> editable until it leaves the unit
    form.save()
    form.make_current()

    # Freeze the technical expert name on first TO — never rewrite when seats move.
    if kind == FormKind.TO:
        freeze_technical_expert(case, actor)

    # Record the save in the timeline — but ONLY when this save produced a
    # VERSION. Building the first TO/PI, branching a new version, and the
    # implicit new version a moved-on inquiry forces are all real case history
    # and keep their BUILD_TO / BUILD_PI entry, carrying the form kind and
    # version so the reader sees exactly what was written.
    #
    # Re-saving the CURRENT version in place is not. It used to log an
    # ``EventAction.EDIT`` row; the owner asked for edits to stop being
    # recorded, and the five-minute autosave in the coding tool made that
    # urgent — every autosave saves in edit mode, so an hour of coding buried
    # the timeline under a dozen identical "Edited TO form" lines that said
    # nothing about the case. So an in-place edit now writes NO event.
    #
    # Nothing goes blind: the snapshot itself still carries who saved it last
    # and when (``CaseForm.signed_by`` / ``CaseForm.updated_at``, both rewritten
    # above on every save), which is the trace a "who touched this last"
    # question actually needs.
    #
    # EventAction.EDIT itself stays — the choice stays in the model, the two
    # OTHER writers of it (Proforma currency conversion, "Requested manager
    # approval") are untouched, and every reader of it stays in place so the
    # EDIT rows already in the production database keep rendering exactly as
    # they do today.
    if not is_edit:
        action = EventAction.BUILD_PI if kind == FormKind.PI else EventAction.BUILD_TO
        log(case, actor, action, from_unit=_unit_of(actor),
            form_kind=kind, form_version=version, side=side)
    return form


# ---------------------------------------------------------------------------
# What a reader is TOLD a case's status is — one definition, and only one
# ---------------------------------------------------------------------------
# Every surface that prints a status, counts one, groups by one or filters on
# one now goes through :func:`status_view`. That is the whole point of this
# section, and it is a repair, not a tidy-up: two separate defects came from
# the same habit of working "the status" out twice.
#
#   * The inbox summary aggregated the whole-case ``status`` COLUMN in SQL
#     while the rows printed beside it were rendered from the per-side
#     statuses. On a split case the column is a FOSSIL — a leftover from before
#     the sides began moving apart — so the chips counted a status no row
#     showed. A supply expert holding six cases that were all With Supply on
#     their side was told "Draft: 2, With Commercial: 2, With Technical: 2".
#
#   * The Archive derived its group names from ``case.sides`` in a separate
#     comprehension from the pills it printed, and it never narrowed to the
#     sides a reader is entitled to — so the one reader whose case page
#     deliberately shows a single side, the supply expert, read "With Supply"
#     on the case page and "Internal: With Supply / External: With Technical"
#     in the Archive, filed under With Technical.
#
# Both are the same mistake. So there is now ONE function that decides which
# sides a given reader sees and what status is on each, and the pills, the
# de-duplicated labels the inbox summary counts, the archive group names, the
# status filter value and the tab counts are all projections of that single
# list. Nothing downstream re-reads ``case.status``, ``internal_status`` or
# ``external_status``, so there is no second definition left to drift from.
class StatusView:
    """What one reader is shown for one case, and everything derived from it.

    Built from ``entries`` — ``[(side label or None, status CODE)]``, already
    narrowed to this reader and already collapsed. Every attribute below is a
    projection of that one list, which is what makes them incapable of
    disagreeing with each other:

    ``rows``          the pills, exactly as the templates unpack them
                      ``(side label or None, status label, colour)``
    ``labels``        the distinct status labels a reader can READ off the row,
                      in the order they appear — what the inbox summary counts
    ``groups``        the collapsed Archive group names for those same codes —
                      the status tabs, the tab counts and ``status_fval``
    ``sides_differ``  whether the row is showing more than one pill
    ``per_side``      whether these entries came from the SIDE columns at all.
                      This is the answer to "is this case showing per-side
                      statuses", and it is carried on the view rather than
                      worked out again by whoever needs it — the case page asks
                      for it by name (``status_per_side``) instead of testing
                      ``is_split`` for itself, because a template that decides
                      this on its own gate is how the page came to print the
                      whole-case column while the inbox and the Archive printed
                      the sides.
    """

    __slots__ = ("rows", "codes", "labels", "groups", "sides_differ", "per_side")

    def __init__(self, entries, *, per_side=False):
        self.per_side = bool(per_side)
        self.codes = [code for _side, code in entries]
        self.rows = [(side,
                      CaseStatus.LABELS.get(code, code),
                      CaseStatus.COLORS.get(code, "#6b7280"))
                     for side, code in entries]
        self.labels = list(dict.fromkeys(row[1] for row in self.rows))
        # The fallback is the label of the code that produced the PILL, never
        # the whole-case fossil's label: a side status that is not one of the
        # declared codes (legacy or hand-edited data) must still group under
        # the words the reader can see on the row. It will not match a tab —
        # the tab strip is built from ``ARCHIVE_TAB_ORDER`` — which is correct,
        # because there is no tab for a status the system does not declare.
        self.groups = {
            CaseStatus.ARCHIVE_GROUP.get(code, CaseStatus.LABELS.get(code, code))
            for code in self.codes
        }
        self.sides_differ = len(self.rows) > 1


def _pill(code):
    """The (label, colour) pair a status code renders as."""
    return (CaseStatus.LABELS.get(code, code),
            CaseStatus.COLORS.get(code, "#6b7280"))


def _collapse(entries):
    """If every side renders the same pill, collapse to one unlabelled pill.

    Dropping the side label is a claim: it says the one pill left describes the
    whole case. That is only true when the pills being collapsed account for
    every side of the case, so this is applied to the full side list and never
    to a list that was narrowed to one reader — see :func:`_record_sides`.

    Compared on the rendered pill and not on the raw code, so the two codes
    that share the words "Cannot supply" still collapse into the single pill
    they always did rather than printing the same word twice.
    """
    if len({_pill(code) for _side, code in entries}) == 1:
        return [(None, entries[0][1])]
    return entries


def _supply_expert_sides(case, user):
    """The side(s) of a split case a supply EXPERT is personally assigned."""
    out = []
    for sc in case.sides:
        owner = (case.supply_internal_assignee_id if sc == Side.INTERNAL
                 else case.supply_external_assignee_id if sc == Side.EXTERNAL
                 else None)
        if owner == getattr(user, "id", None):
            out.append(sc)
    return out


def _record_sides(case, user):
    """``(sides, narrowed)`` for the RECORD surfaces — case page and Archive.

    The record must not hide a side: everyone who may open the case sees where
    both of them are. The one exception is the supply expert, who works a
    single delegated side and whose case page has always shown only that side;
    the Archive now agrees with it instead of contradicting it.

    ``narrowed`` says which of those two happened, and it is what decides
    whether the pills may be collapsed. When a reader is being shown THEIR
    side(s) rather than the case's, the side label is the whole point of the
    pill — it is what stops "With Supply" from being read as the state of a
    case whose other side is somewhere else entirely — so a narrowed list keeps
    its labels even when it happens to hold one entry, or two that agree. That
    is exactly how the supply expert's case page has always read.

    A supply expert with no delegated side has not been narrowed to anything:
    they are reading the case as a viewer, so they get the full list on the
    same terms as everybody else, and their Archive row and case page say the
    same thing as every other seat's.
    """
    profile = getattr(user, "profile", None)
    if (profile is not None and profile.unit == Unit.SUPPLY
            and profile.role == Role.EXPERT):
        own = _supply_expert_sides(case, user)
        if own:
            return own, True
    return list(case.sides), False


def _held_sides(case, user):
    """The sides currently in this reader's hands — what the INBOX shows.

    The inbox answers "what is waiting for you", so it narrows to the side(s)
    that put the case there. A reader who holds no side (the case reached them
    another way) is shown all of them rather than nothing.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        return list(case.sides)
    if profile.unit == Unit.SUPPLY:
        if profile.role == Role.EXPERT:
            sides = _supply_expert_sides(case, user)
        else:
            sides = [sc for sc in case.sides if case.side_holder(sc) == Unit.SUPPLY]
    elif profile.unit in (Unit.COMMERCIAL, Unit.TECHNICAL):
        sides = [sc for sc in case.sides if case.side_holder(sc) == profile.unit]
    else:
        sides = list(case.sides)
    return sides or list(case.sides)


def status_view(case: Case, user=None, *, surface: str = "record") -> StatusView:
    """The one place a case's status is turned into what a reader is shown.

    ``surface`` is "record" (the case page and the Archive) or "inbox". Every
    surface reads the split gate, the narrowing and the collapse from here —
    including the case page, which is handed ``per_side`` rather than deciding
    for itself — so there is no second place left that can answer any of the
    three questions differently.

    The per-side statuses are read only while the split is live. ``split_active``
    is the same gate :func:`inbox_filter_q` routes on — every branch of it pairs
    ``Q(split_active=True)`` with the side columns and ``Q(split_active=False)``
    with the whole-case one, and none of them consults ``price_type`` — so a case
    cannot be put in someone's inbox by its sides and then labelled by the
    whole-case column. While the split is off the side columns keep stale values:
    :func:`sync_fresh_draft_price_type` turns ``split_active`` off for a
    one-sided price type and writes ``DRAFT`` into that side's status column in
    the same breath, and the whole-case status then moves on without it. That is
    exactly why those columns must not be read while the split is off.

    Nothing here changes a status: ``side_status`` is read, never written.
    """
    if not (case.split_active and case.sides):
        return StatusView([(None, case.status)])
    if surface == "inbox":
        # Not collapsed: the inbox has always named the side on every pill, so
        # a reader holding both sides sees both lines.
        sides = _held_sides(case, user)
        return StatusView([(Side.LABELS.get(sc, sc), case.side_status(sc))
                           for sc in sides], per_side=True)
    sides, narrowed = _record_sides(case, user)
    entries = [(Side.LABELS.get(sc, sc), case.side_status(sc)) for sc in sides]
    # Collapsing drops the side label, which asserts that the remaining pill is
    # the whole case. Only the un-narrowed list can carry that assertion.
    return StatusView(entries if narrowed else _collapse(entries), per_side=True)


def archive_status_rows(case: Case, user=None):
    """(rows, archive group names, sides_differ) for one Archive row.

    A thin projection of :func:`status_view` kept because callers read well
    with it. The group names come back beside the rows — rather than being
    derived from ``case.sides`` all over again — because the row's status
    FILTER value, the tab it is filed under and the tab's count all have to
    describe the very pills that are on screen. Deriving them apart from the
    rows is precisely how the Archive came to file a case under a unit it was
    not with.
    """
    view = status_view(case, user, surface="record")
    return view.rows, view.groups, view.sides_differ


def detail_status_view(case: Case, user) -> StatusView:
    """What the case-detail page shows, rows AND gate, from the one call.

    * Split off -> the single whole-case status, ``per_side`` False.
    * Split on  -> a supply EXPERT sees only the side(s) delegated to them, each
      still named; everyone else sees every side, collapsed to one unlabelled
      row while they are in the same place (e.g. the manager is building both
      himself). ``per_side`` True.

    Returns the whole view rather than just ``rows`` because the page needs both
    halves of the same answer and must not derive the second one itself: the
    template gated on ``is_split`` while this gated on ``split_active``, and on a
    ``split_active`` case with a one-sided price type the two disagreed — the
    Archive and the inbox printed the side, the case page printed the whole-case
    fossil. One call, one answer, both surfaces.
    """
    return status_view(case, user, surface="record")


def inbox_status_view(case: Case, user) -> StatusView:
    """What one inbox row shows: only the side(s) that are in this user's hands
    (the reason the case is in their inbox). Supply experts see only their own
    side; other units see whichever sides they currently hold.

    The whole view again, so the summary chips can count ``labels`` — the very
    labels this row prints, de-duplicated — instead of re-deriving them from the
    rows beside the chips. Same numbers either way; the difference is that there
    is nothing left to re-derive them WRONG.
    """
    return status_view(case, user, surface="inbox")


# ---------------------------------------------------------------------------
# Archive: one scope, one set of column filters, one window
# ---------------------------------------------------------------------------
# The archive page renders a WINDOW of rows and fetches the rest as the reader
# scrolls (cases.views.archive_slice). Two views therefore answer "which cases
# may this seat see, in what order" — and a seat that saw a different set from
# one of them would be a case silently missing from someone's history. So the
# scope is decided exactly once, here, and both views call it.

# How many rows the first window holds, and how many each scroll fetch adds.
#
# Measured, not guessed: `.vscroll-list` is `max-height: calc(100vh - 240px)`,
# so on the densest seat we could find (2560x1440, browser chrome included) the
# list box is 1046 px tall, and an archive row measured 45 px there (60 px at
# 1600x1000, 80 px on a 1366x768 laptop). 1046 / 45 = 23 rows on screen at once
# on the seat that shows the most. The window is two and a half of those
# screenfuls — ceil(23.24 * 2.5) = 58 — so the reader has roughly a screen and
# a half of runway left at the moment the next fetch is triggered.
ARCHIVE_WINDOW = 58
# Nothing may ask the slice endpoint for an unbounded number of rows; four
# windows is already far more than any scroll gesture can consume in one step.
ARCHIVE_MAX_SLICE = ARCHIVE_WINDOW * 4


class ArchiveScope:
    """What one request is allowed to see in the archive, and how it was asked.

    ``qs`` is ordered and ready to slice. Everything else is what the page needs
    to describe itself (headings, toggles, which columns exist).
    """

    __slots__ = ("qs", "profile", "seat_user", "mine_only", "is_unit_manager",
                 "drill_person", "query", "status", "doc_kind", "date_from",
                 "date_to", "unit", "is_admin", "show_money", "select_mode",
                 "select_return", "mine_toggle_label", "show_expert_filter")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


# The separators a Jalali date is written with anywhere on this page: the
# pickers emit ``-``, the rendered cell prints ``.``, and hand-typed values use
# either (or ``/``). Both date paths reduce their input through this.
_ARCHIVE_DATE_SEP = re.compile(r"[-/.]")


def _archive_scope_date(raw):
    """``?from=`` / ``?to=`` as a ``datetime.date``, or ``None`` when unusable.

    Deliberately unannotated: ``datetime`` is imported in the body (it is needed
    nowhere else in this module), so a ``-> datetime.date | None`` here names
    something that does not exist at module level — inert under
    ``from __future__ import annotations`` right up until anything resolves the
    annotation, e.g. ``typing.get_type_hints``, which raises ``NameError``.

    The archive is written in Jalali end to end — every date it prints and every
    date box it offers — so a bare ``1404-06-01`` is a Jalali day and is
    converted before it goes anywhere near ``created_at``. A year of 1500 or
    more cannot be Jalali in this system's lifetime, so such a value is taken as
    the Gregorian date it plainly is; any separator the pickers use is accepted
    and a trailing clock is dropped, since the column is compared per DAY and
    both ends of the range are inclusive.

    Returns ``None`` rather than raising for anything that is not a date: an
    unreadable query string must not take the page down.
    """
    import datetime as _date_mod

    head = str(raw or "").strip().split(" ", 1)[0]
    parts = [p for p in _ARCHIVE_DATE_SEP.split(head) if p]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    year, month, day = (int(p) for p in parts)
    try:
        if year >= 1500:
            return _date_mod.date(year, month, day)
        from .jalali import jalali_to_gregorian
        gy, gm, gd = jalali_to_gregorian(year, month, day)
        return _date_mod.date(gy, gm, gd)
    except (ValueError, TypeError, OverflowError):
        return None


def archive_scope(request):
    """The archive queryset for this request, plus how the request was framed.

    Returns ``None`` when the user has no profile, and for a unit outside the
    TO/PI workflow (Marketing), which has no case history at all — the caller
    redirects.

    Scope:
      admin / general manager     -> the entire archive
      commercial manager (off)    -> the entire archive (incl. experts' cases)
      commercial manager (on)     -> only cases they personally created
      commercial expert           -> only cases they personally created
      technical manager (on)      -> only TOs they personally created
      supply manager (on)         -> only PIs they personally created
      technical / supply manager
        (off, the default)        -> every case that passed through their unit,
                                      same as their unit's supervisor — see below
      unit supervisor             -> every case that passed through their unit
      technical / supply (else)   -> cases they personally participated in
    Scope uses the active seat user so secondary / translated seats see their
    own cases (not the login profile's primary seat alone).

    Archive always includes every case that is also in the user's inbox (and
    drafts), so active files are never missing from history.
    """
    from django.contrib.auth.models import User
    from django.db.models import Q
    from django.utils.http import url_has_allowed_host_and_scheme

    profile = getattr(request.user, "profile", None)
    if profile is None:
        return None
    # A unit outside the TO/PI workflow (Marketing) has no case history to
    # scope, so it gets the same "there is no archive for you" answer a request
    # with no profile gets, and every caller inherits it: archive() redirects
    # and archive_slice() answers 403. Left to fall through it would instead
    # build the "cases you personally participated in" queryset at the bottom,
    # which is empty today only by accident — nobody in Marketing has touched a
    # case — and would start returning rows the moment one of them ever did.
    # As above, a BLANK unit is deliberately not covered: that is a general
    # manager or an unassigned seat, both of which already have an answer here.
    if profile.unit and profile.unit not in Unit.WORKFLOW:
        return None

    select_mode = str(request.GET.get("select") or "").strip() in ("1", "true", "yes")
    select_return = (request.GET.get("return") or "").strip()
    # "Starts with a slash" is not the same as "stays on this site": a
    # protocol-relative URL (//evil.example/x, or /\evil.example) passes that
    # test, and the Cancel link / Confirm-selection script would then carry the
    # picked case ids off to another host. The leading slash is kept so the
    # accepted set is still exactly "a path on this site"; Django's own helper is
    # what recognises the host-bearing forms that sneak through it.
    if select_mode and not (
            select_return.startswith("/")
            and url_has_allowed_host_and_scheme(
                url=select_return, allowed_hosts={request.get_host()})):
        select_return = ""

    qs = Case.objects.select_related("client", "created_by").all()

    # Manager "Only my cases" toggle (default OFF = current wide archive view).
    mine_only = str(request.GET.get("mine", "") or "").strip() in ("1", "true", "on", "yes")
    is_unit_manager = bool(
        profile.role == Role.MANAGER
        and profile.unit in (Unit.COMMERCIAL, Unit.TECHNICAL, Unit.SUPPLY)
        and not profile.is_admin
        and not profile.is_general_manager
    )

    from people.role_nav import work_context
    ctx = work_context(request)
    seat_user = ctx.seat_user
    if ctx.role is not None:
        # Prefer active role for archive unit/role filters.
        profile_unit = ctx.role.unit or profile.unit
        profile_role = ctx.role.role or profile.role
    else:
        profile_unit = profile.unit
        profile_role = profile.role

    if profile.is_admin or profile.is_general_manager:
        pass
    elif profile_unit == Unit.COMMERCIAL and profile_role == Role.MANAGER:
        if mine_only:
            qs = qs.filter(created_by=seat_user)
    elif profile_unit == Unit.COMMERCIAL:
        qs = qs.filter(created_by=seat_user)
        # Substitutes do not see fully closed / terminal archive rows.
        if ctx.is_substitute:
            qs = qs.exclude(status__in=CaseStatus.ENDED)
    # The four branches below used to reach the forms / events tables by JOIN.
    # Every one of them is a *multi-valued* relation, so the join multiplies the
    # case out to one row per matching form and per matching event before
    # SELECT DISTINCT folds it back — on a live-sized archive that is tens of
    # thousands of intermediate rows, and this queryset is then evaluated seven
    # more times below (once per filter dropdown, once for the rows). Measured on
    # a 300-case / 1081-form / 5689-event copy: 46-85 ms per evaluation for a
    # Technical or Supply seat, against 1-4 ms for an Administrator, whose
    # branch has no join at all.
    #
    # Asking the same question as a subquery on the case's primary key returns
    # exactly the same set — "this case has at least one form/event matching"
    # is precisely what an inner join plus DISTINCT means — but the case table is
    # never multiplied, so there is nothing to fold back. Note the two mine_only
    # branches keep both conditions inside ONE subquery, because they were one
    # ``filter()`` call and therefore had to be satisfied by a single form row.
    # ``.distinct()`` stays exactly where it was on every branch: whether this
    # queryset is distinct decides whether the inbox union a few lines down is
    # accepted or raises, and that must not move.
    elif is_unit_manager and mine_only and profile_unit == Unit.TECHNICAL:
        qs = qs.filter(
            pk__in=CaseForm.objects.filter(
                kind=FormKind.TO, created_by=seat_user).values("case_id")
        ).distinct()
    elif is_unit_manager and mine_only and profile_unit == Unit.SUPPLY:
        qs = qs.filter(
            pk__in=CaseForm.objects.filter(
                kind=FormKind.PI, created_by=seat_user).values("case_id")
        ).distinct()
    elif profile_role in (Role.SUPERVISOR, Role.MANAGER) and profile_unit:
        # Reached by a Technical/Supply MANAGER only with "Only my cases" OFF
        # (the ``is_unit_manager and mine_only`` branches above already claim
        # the ON case) — a Commercial manager never reaches here, handled by
        # its own branch earlier. Before this, mine_only-off fell through to
        # the generic "else" bucket below: cases the manager personally
        # created/was assigned/acted on. A case an EXPERT built or sent
        # onward without the manager ever becoming an actor on it — the
        # ordinary path once a manager assigns work away — had no clause
        # there that matched the manager's own seat, so it was invisible to
        # the very manager whose unit it belonged to. The owner's rule is
        # "every case given to my unit stays in my Archive, assigned away or
        # not", which is exactly what the unit supervisor already gets below
        # — a manager needs no narrower a view than their own supervisor.
        u = profile_unit
        qs = qs.filter(
            Q(holder_unit=u)
            | Q(pk__in=CaseEvent.objects.filter(
                Q(from_unit=u) | Q(to_unit=u)).values("case_id"))
            | Q(pk__in=CaseForm.objects.filter(
                unit_at_creation=u).values("case_id"))
        ).distinct()
    else:
        qs = qs.filter(
            Q(created_by=seat_user)
            | Q(assigned_to=seat_user)
            | Q(pk__in=CaseForm.objects.filter(
                created_by=seat_user).values("case_id"))
            | Q(pk__in=CaseEvent.objects.filter(
                actor=seat_user).values("case_id"))
        ).distinct()
        if ctx.is_substitute:
            qs = qs.exclude(status__in=CaseStatus.ENDED)

    # Always include this user's current inbox (and therefore drafts / live
    # files sitting there) so Archive ⊇ Inbox — except when the manager
    # "Only my cases" toggle is ON (that view is intentionally narrower).
    if not mine_only:
        try:
            inbox_qs = inbox_cases_for_request(request)
            if inbox_qs is not None:
                # ``qs | inbox_qs`` is a TypeError whenever exactly one side has
                # had .distinct() applied — "Cannot combine a unique query with a
                # non-unique query", raised by Query.combine on self.distinct !=
                # rhs.distinct. inbox_cases() always ends in .distinct(), while
                # the Commercial branches above deliberately do not, so this
                # raised on EVERY archive load for every Commercial seat (logged
                # once per page view) and the union was silently skipped — the
                # "Archive ⊇ Inbox" guarantee in the docstring quietly did not
                # hold for the unit that relies on it most. Making both sides
                # distinct is what the .distinct() on the result already asked
                # for; it changes no row, only whether the OR is legal to build.
                qs = (qs.distinct() | inbox_qs.distinct()).distinct()
        except Exception:
            logger.exception("archive: failed to union inbox cases for user %s",
                             request.user.pk)

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    doc_kind = request.GET.get("kind", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    # Drill-down from the dashboard: a single expert's cases.
    creator_id = request.GET.get("creator", "").strip()
    assignee_id = request.GET.get("assignee", "").strip()
    drill_person = None
    if creator_id.isdigit():
        qs = qs.filter(created_by_id=int(creator_id))
        drill_person = User.objects.filter(pk=int(creator_id)).first()
    elif assignee_id.isdigit():
        aid = int(assignee_id)
        qs = qs.filter(
            Q(technical_assignee_id=aid) | Q(supply_internal_assignee_id=aid)
            | Q(supply_external_assignee_id=aid) | Q(supply_assignee_id=aid)
            | Q(assigned_to_id=aid)
        ).distinct()
        drill_person = User.objects.filter(pk=aid).first()

    if query:
        flt = (Q(client__name__icontains=query) | Q(client__code__icontains=query)
               | Q(doc_no__icontains=query))
        if query.isdigit():
            flt |= Q(serial=int(query))
        qs = qs.filter(flt)
    if status:
        codes = [c for c in status.split(",") if c]
        qs = qs.filter(status__in=codes) if codes else qs
    if doc_kind:
        kinds = [k for k in doc_kind.split(",") if k]
        qs = qs.filter(kind__in=kinds) if kinds else qs
    # ``?from=`` / ``?to=`` is the OTHER date range this page understands — the
    # one that filters in SQL rather than on the rendered cell. The archive's own
    # date boxes post ``ffrom`` / ``fto`` and never reach here, so this pair only
    # ever arrives on a hand-written or bookmarked URL; when it did, the string
    # went straight into a Gregorian date column, so a Jalali ``1404-06-01`` was
    # read as the year 1404 AD (before every row, i.e. no filter at all) and
    # ``to=`` excluded everything, while the ``YYYY-MM-DD HH:MM`` the picker
    # writes raised ValidationError and returned a 500. Parsed here instead, in
    # the calendar the page is written in, and ignored when it is unusable.
    d_from = _archive_scope_date(date_from)
    d_to = _archive_scope_date(date_to)
    if d_from is not None:
        qs = qs.filter(created_at__date__gte=d_from)
    if d_to is not None:
        qs = qs.filter(created_at__date__lte=d_to)

    # ``-created_at`` alone is NOT a total order, and it never was — two cases
    # created in the same second come back in whatever order the database felt
    # like. That was harmless while the page was one query rendering one list;
    # it is not harmless now that the reader's second and third fetch are
    # separate queries with an OFFSET, because an unstable sort under OFFSET is
    # exactly how a row gets shown twice and another never shown at all. The
    # primary key breaks every tie, so every fetch walks the same list.
    #
    # Descending, not ascending: SQLite already returned tied rows highest-pk
    # first (verified over the whole 300- and 3,000-case fixtures, on the plain,
    # the DISTINCT and the inbox-union forms of this queryset), so this pins
    # today's order rather than choosing a new one.
    qs = qs.order_by("-created_at", "-pk")

    show_money = bool(
        profile.is_admin or profile.is_general_manager
        or profile.unit == Unit.COMMERCIAL
    )
    return ArchiveScope(
        qs=qs,
        profile=profile,
        seat_user=seat_user,
        mine_only=mine_only,
        is_unit_manager=is_unit_manager,
        drill_person=drill_person,
        query=query,
        status=status,
        doc_kind=doc_kind,
        date_from=date_from,
        date_to=date_to,
        unit="" if (profile.is_admin or profile.is_general_manager) else profile.unit,
        is_admin=bool(profile.is_admin or profile.is_general_manager),
        show_money=show_money,
        select_mode=select_mode,
        select_return=select_return,
        mine_toggle_label={
            Unit.COMMERCIAL: "Only cases I created",
            Unit.TECHNICAL: "Only TOs I created",
            Unit.SUPPLY: "Only PIs I created",
        }.get(profile.unit or "", "Only my cases"),
        show_expert_filter=bool(
            profile.role in (Role.SUPERVISOR, Role.MANAGER)
            or profile.is_general_manager or profile.is_admin),
    )


# The archive's column filters, in the order the filter card lists them.
#
# ``param``  the GET name the page and the slice endpoint both use
# ``mode``   contains / equals / gte / lte — the same four the browser had
# ``seat``   when set, the column only exists for some seats, and a filter on a
#            column this seat cannot see is ignored (exactly as before: there
#            was no control to type into, so there was no filter)
ARCHIVE_FILTERS = (
    ("doc", "docno", "contains", None),
    ("order", "order", "contains", None),
    ("client", "client", "contains", "client"),
    ("expert", "expert", "contains", "expert"),
    ("price", "price", "equals", None),
    ("status", "status", "contains", None),
    ("offer", "offer", "equals", None),
    ("kind", "kind", "equals", None),
    ("from", "created", "gte", None),
    ("to", "created", "lte", None),
)


def archive_column_visible(column, *, unit, is_admin) -> bool:
    """Whether a seat's archive shows a column that only some seats get."""
    if column == "client":
        return unit != Unit.SUPPLY
    if column == "expert":
        return bool(is_admin or unit == Unit.TECHNICAL or unit == Unit.SUPPLY)
    return True


def archive_filter_text(case) -> dict:
    """The text each filterable archive cell contains, keyed by column.

    This is the value the reader is filtering ON, and it must be the text they
    can SEE — the same string the cell renders, lower-cased and trimmed, which
    is precisely what the browser used to compare against when it filtered the
    rendered table. Two consequences that look like bugs and are not:

    * the Commercial expert cell prints ``commercial_expert_display`` when the
      case carries one, and only falls back to the creator's name plus expert
      code otherwise — so it is the display string that is matched then, not
      the creator;
    * the Order No. cell prints an em dash when the case has no order number,
      so that is what an order filter sees for those rows.

    ``status`` mirrors the cell's ``data-fval`` (the collapsed archive group
    names), which the browser preferred over the cell text; ``created`` is the
    Jalali stamp the cell prints.

    ``cases.tests`` renders _archive_rows.html and checks every cell's text
    against this function, so the two cannot drift apart.
    """
    from core.templatetags.ft_extras import jalali

    doc = case.doc_no or ""
    if case.is_delegated:
        doc = f"{doc} Delegated"

    if case.commercial_expert_display:
        expert = case.commercial_expert_display
    else:
        name = ""
        if case.created_by_id and case.created_by is not None:
            name = (case.created_by.get_full_name() or "").strip() or case.created_by.username
        expert = f"{name} ({case.expert_code})"

    client = f"{case.client.name} ({case.client.code})" if case.client_id else ""

    return {
        "docno": doc.strip().lower(),
        "order": (case.order_no or "—").strip().lower(),
        "client": client.strip().lower(),
        "expert": expert.strip().lower(),
        "price": (case.price_type_label or "").strip().lower(),
        "status": (getattr(case, "status_fval", "") or "").strip().lower(),
        "offer": (case.offer_stage_label or "").strip().lower(),
        "kind": (case.kind_label or "").strip().lower(),
        "created": (jalali(case.created_at, "Y.m.d H:i") or "").strip().lower(),
    }


def _archive_date_key(value: str) -> str:
    """A Jalali stamp reduced to the ``YYYY.MM.DD`` head two of them compare on.

    Both sides of a date range reach the predicate as text, and they are written
    by different hands: the picker writes ``1404-06-15 09:00`` with hyphens, the
    Created cell prints ``1404.06.15 09:00`` with dots. Since the comparison is
    lexicographic, the separator itself used to decide it — ``.`` sorts after
    ``-`` — so within one Jalali year ``From`` matched every row and ``To``
    matched none, which is the empty table. Reducing both sides to the same
    ``YYYY.MM.DD`` shape is what makes the comparison about the date again.

    Only the DATE survives, never the time: a ``To`` of ``1404-06-15 09:00``
    still has to keep that day's 21:30 rows, and dropping the clock on both
    sides is what keeps a one-day range meaning the whole day. Anything that is
    not three numbers is handed back as-is, so a cell that prints an em dash
    still compares as it did.
    """
    head = (value or "").strip().split(" ", 1)[0][:10]
    parts = [p for p in _ARCHIVE_DATE_SEP.split(head) if p]
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return "%04d.%02d.%02d" % (int(parts[0]), int(parts[1]), int(parts[2]))
    return head


def _archive_cell_matches(text: str, mode: str, raw: str) -> bool:
    """One column's predicate — the browser's, character for character.

    ``gte`` / ``lte`` compare dates as STRINGS, which is what the rendered
    Jalali stamp allows (Y.m.d sorts lexicographically) — but only once both
    sides have been put in the same shape by :func:`_archive_date_key`, which
    also drops the time of day so both ends of a range are inclusive of their
    whole day.
    """
    if mode == "gte":
        return _archive_date_key(text) >= _archive_date_key(raw)
    if mode == "lte":
        return _archive_date_key(text) <= _archive_date_key(raw)
    if mode == "equals":
        return text == raw
    parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p]
    return any(p in text for p in parts)


def archive_filter_params(request, *, unit, is_admin) -> dict:
    """The column filters this request asks for, ignoring the ones it cannot see."""
    out = {}
    for param, column, mode, seat_col in ARCHIVE_FILTERS:
        raw = (request.GET.get("f" + param) or "").strip().lower()
        if not raw:
            continue
        if seat_col and not archive_column_visible(seat_col, unit=unit, is_admin=is_admin):
            continue
        out[param] = (column, mode, raw)
    return out


def archive_apply_column_filters(cases, params):
    """Rows still visible under ``params`` (all of them ANDed), order kept."""
    if not params:
        return cases
    terms = list(params.values())
    kept = []
    for case in cases:
        text = archive_filter_text(case)
        if all(_archive_cell_matches(text.get(col, ""), mode, raw)
               for col, mode, raw in terms):
            kept.append(case)
    return kept


def archive_tab_counts(cases, params):
    """(in_range, filtered, filtered_count, status_tabs) for one archive request.

    The one definition of what the status-tab strip shows, shared by the full
    page and the live-search slice endpoint — so a Document No. search typed
    without a page reload can never print a tab count the full page would not
    have agreed with.

    ``cases`` must already be ``archive_decorate``-d (the pills/status_groups
    this reads come from that). The status tab is itself a filter — it drives
    the hidden ``fstatus`` control — so it is applied LAST, over what every
    OTHER filter already narrowed: ``in_range``. Counting the tab numbers over
    ``in_range`` (not ``filtered``) is what keeps them agreeing with the list
    beneath — with no tab chosen the two sets are the same set, and with one
    chosen that tab's own number is exactly the rows on screen.
    """
    status_params = {k: v for k, v in params.items() if k == "status"}
    other_params = {k: v for k, v in params.items() if k != "status"}
    in_range = archive_apply_column_filters(cases, other_params)
    filtered = archive_apply_column_filters(in_range, status_params)

    tab_counts = {label: 0 for label in CaseStatus.ARCHIVE_TAB_ORDER}
    for c in in_range:
        for g in c.status_groups:
            if g in tab_counts:
                tab_counts[g] += 1

    # WHICH tabs are on the strip comes from the WHOLE set (cases), never from
    # in_range — see archive_tab_counts' docstring / the call site's comment for
    # why the strip itself must not shift under the reader.
    tabs_present = set()
    for c in cases:
        tabs_present.update(c.status_groups)

    status_tabs = []
    for label in CaseStatus.ARCHIVE_TAB_ORDER:
        if label not in tabs_present:
            continue
        # "label" stays the plain English ARCHIVE_GROUP/ARCHIVE_TAB_ORDER
        # string — it is what the template's data-status attribute and the
        # hidden fstatus <option> post back, and both are matched, in
        # cases/services.py and archive_stream.js, against a row's own
        # status_fval (built from the same English ARCHIVE_GROUP values).
        # "display" is the ONLY translated piece: resolved eagerly here
        # (str() on the gettext_lazy proxy), safe because this function runs
        # per request — after LanguageMiddleware has already activated this
        # viewer's language — never at import time. See
        # CaseStatus.ARCHIVE_GROUP_LABELS' own comment for why the two must
        # not be merged into one.
        display = str(CaseStatus.ARCHIVE_GROUP_LABELS.get(label, label))
        status_tabs.append({
            "label": label,
            "display": display,
            "count": tab_counts.get(label, 0),
            "color": CaseStatus.ARCHIVE_TAB_COLORS.get(label, "#64748b"),
            "words": display.split(),
        })
    return in_range, filtered, len(filtered), status_tabs


def archive_decorate(cases, user=None):
    """Attach the status pills / filter value every archive row renders.

    Also what the status tab counts are built from, so a row is filtered under
    exactly the statuses a reader can see printed on it.

    ``user`` is the reader, and it is not optional in spirit: the Archive shows
    a supply expert the side they were delegated, the same one their case page
    shows, and it cannot do that without knowing who is looking. Passing None
    keeps the old reader-blind behaviour (every side listed) for any caller
    that genuinely has no reader.

    No query per row: every column this reads — the side statuses, the side
    holders, the supply assignee ids — is already on the loaded ``Case``, and
    the reader's profile is fetched once and cached on the user object.
    """
    for case in cases:
        view = status_view(case, user, surface="record")
        case.status_view = view
        case.status_rows = view.rows
        case.status_sides_differ = view.sides_differ
        case.status_groups = view.groups
        case.status_fval = " ".join(sorted(view.groups))
    return cases


def archive_attach_money(cases):
    """Attach ``grand_total_display`` to each row; returns the id -> amount map.

    The map is returned because the drill-down banner sums it over every match,
    not just the rows on screen.
    """
    from .export_data import case_pi_grand_totals_map, format_money_amount

    gt_map = case_pi_grand_totals_map([c.pk for c in cases])
    for case in cases:
        amt = gt_map.get(case.pk, 0.0)
        case.grand_total_num = amt
        case.grand_total_display = format_money_amount(amt) if amt else "—"
    return gt_map


def inbox_filter_q(user, *, role=None, work_user=None):
    """The membership rule behind :func:`inbox_cases`, as a bare ``Q``.

    This is the single definition of "is this case in that person's inbox".
    ``inbox_cases`` wraps it into a queryset; ``inbox_counts_for_users`` folds
    several of these into one aggregate so a dashboard drawing a card per expert
    does not pay for a separate inbox query per card. Returning ``None`` means
    "this person has no inbox at all" (admins, general managers, anyone without
    a resolvable profile, and any unit outside the TO/PI workflow such as
    Marketing, which reaches the bare ``return None`` at the bottom because no
    unit branch claims it) — the caller turns that into an empty result rather
    than a filter, exactly as before.

    Every branch below only ever touches columns on ``Case`` itself, never a
    reverse or many-to-many relation. That is what lets the same Q be reused as
    a ``Count(filter=...)``: with no joins there are no duplicate rows, so the
    ``.distinct()`` in ``inbox_cases`` and the ``distinct=True`` in the batched
    count are both no-ops and the two routes cannot disagree.

    ``role`` — when set (active PersonRole), unit/role come from the role rather
    than the login profile (needed for secondary seats that must not rewrite the
    profile). ``work_user`` — seat User whose created_by / assignee FKs own the
    cases (defaults to ``user``; for secondary seats = ``role.source_user``).
    """
    from django.db.models import Q
    from types import SimpleNamespace

    work = work_user or user
    profile = getattr(user, "profile", None)
    if role is not None:
        # Synthetic profile-like object from the active role.
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None or profile.is_admin or profile.is_general_manager:
        return None
    # Supervisors use the same ownership inbox rules as experts for their unit
    # (needed so Delegated tasks land in their Inbox, e.g. a DRAFT they now own).
    unit = profile.unit
    TERM = CaseStatus.TERMINAL

    def side_active_at(u, sc):
        holder = "internal_holder" if sc == Side.INTERNAL else "external_holder"
        status = "internal_status" if sc == Side.INTERNAL else "external_status"
        return Q(**{holder: u}) & ~Q(**{f"{status}__in": TERM})

    split = Q(split_active=True)
    nonsplit = Q(split_active=False)

    if unit == Unit.COMMERCIAL:
        # A final-approved case is still open (it can be Final-Closed) but it
        # should sit in the Archive, not the active inbox. So exclude both the
        # terminal statuses and FINAL_APPROVED from the inbox.
        inbox_hide = list(TERM) + [CaseStatus.FINAL_APPROVED]
        ns = nonsplit & ~Q(status__in=inbox_hide) & Q(holder_unit=Unit.COMMERCIAL)
        sp = split & (side_active_at(Unit.COMMERCIAL, Side.INTERNAL)
                      | side_active_at(Unit.COMMERCIAL, Side.EXTERNAL))
        base = ns | sp
        pending_ns = nonsplit & Q(status=CaseStatus.PENDING_CANCEL)
        pending_sp = split & (
            Q(internal_status=CaseStatus.PENDING_CANCEL)
            | Q(external_status=CaseStatus.PENDING_CANCEL)
        )
        pending = pending_ns | pending_sp
        if profile.role == Role.MANAGER:
            # Manager: own live cases + every cancel/burn awaiting their approval.
            return (base & Q(created_by=work)) | pending
        # Experts / supervisors: only their own cases, and once they request
        # cancel/burn the file leaves their inbox until the manager rejects it.
        # ``exclude()`` on a plain column is precisely ``filter(~Q(...))`` — the
        # difference between the two only shows up across a multi-valued join,
        # and there is none here — so the negations below are the same three
        # exclusions this rule has always applied.
        return (base & Q(created_by=work)
                & ~Q(status=CaseStatus.PENDING_CANCEL)
                & ~Q(internal_status=CaseStatus.PENDING_CANCEL)
                & ~Q(external_status=CaseStatus.PENDING_CANCEL))

    if unit == Unit.TECHNICAL:
        ns = nonsplit & Q(status__in=[CaseStatus.WITH_TECHNICAL, CaseStatus.RETURNED_TO_TECHNICAL])
        if profile.role == Role.MANAGER:
            ns = ns & (Q(assigned_to__isnull=True) | Q(assigned_to=work) | Q(technical_assignee=work))
            # Same exclusion as the non-split case just above: once a split
            # case's Technical side is assigned to a specific expert, it must
            # leave every OTHER manager's inbox exactly as the non-split
            # branch already does (can_act_on_side refuses the manager the
            # moment technical_assignee is set to someone else, so a manager
            # who could still see this here had a case with nothing they
            # could do to it — no action, and no reachable "view" chip out).
            sp = split & (side_active_at(Unit.TECHNICAL, Side.INTERNAL)
                          | side_active_at(Unit.TECHNICAL, Side.EXTERNAL)) \
                & (Q(technical_assignee__isnull=True) | Q(technical_assignee=work))
        else:
            ns = ns & (Q(assigned_to=work) | Q(technical_assignee=work))
            # ``technical_assignee`` has to be matched here as well: Technical
            # delegates a split case as a whole (one expert for both sides), so
            # assign() writes that field and can_act_on_side reads it, while the
            # per-side columns are only ever filled by the side-specific assign
            # path. Matching just the per-side columns hid every case the expert
            # actually owns — including one assigned before a two-stage upgrade
            # split it — from the inbox that is supposed to hand it to them.
            mine = Q(technical_assignee=work)
            sp = split & (
                (side_active_at(Unit.TECHNICAL, Side.INTERNAL)
                 & (Q(technical_internal_assignee=work) | mine))
                | (side_active_at(Unit.TECHNICAL, Side.EXTERNAL)
                   & (Q(technical_external_assignee=work) | mine)))
        return ns | sp

    if unit == Unit.SUPPLY:
        ns = nonsplit & Q(status=CaseStatus.WITH_SUPPLY)
        if profile.role == Role.MANAGER:
            ns = ns & (
                Q(price_type__in=[PriceType.INTERNAL, PriceType.BOTH], supply_internal_assignee__isnull=True)
                | Q(price_type__in=[PriceType.EXTERNAL, PriceType.BOTH], supply_external_assignee__isnull=True)
                | Q(supply_internal_assignee=work) | Q(supply_external_assignee=work)
                | Q(supply_assignee=work)
                | Q(assigned_to=work))
            # Same reasoning as the Technical manager branch above: once a
            # split case's Supply side is individually assigned, it must
            # leave every OTHER manager's inbox, per side.
            sp = split & (
                (side_active_at(Unit.SUPPLY, Side.INTERNAL)
                 & (Q(supply_internal_assignee__isnull=True) | Q(supply_internal_assignee=work)))
                | (side_active_at(Unit.SUPPLY, Side.EXTERNAL)
                   & (Q(supply_external_assignee__isnull=True) | Q(supply_external_assignee=work))))
        else:
            ns = ns & (
                Q(supply_internal_assignee=work) | Q(supply_external_assignee=work)
                | Q(supply_assignee=work))
            sp = split & (
                (side_active_at(Unit.SUPPLY, Side.INTERNAL) & Q(supply_internal_assignee=work))
                | (side_active_at(Unit.SUPPLY, Side.EXTERNAL) & Q(supply_external_assignee=work)))
        return ns | sp

    if unit == Unit.PURCHASING:
        # UNLIKE every branch above, this does NOT key off an exclusive
        # status of its own — a case stays FINAL_APPROVED the entire time
        # Purchasing is working on it (Commercial's own branch above keeps
        # matching the SAME status, on purpose — see accounts/constants.py's
        # "PURCHASING and WAREHOUSE" docstring section for why two units
        # holding real access to one case at once is deliberate here, unlike
        # every other handoff in this workflow). No split-side handling —
        # Purchasing was not asked for one.
        base = nonsplit & Q(status=CaseStatus.FINAL_APPROVED)
        if profile.role == Role.MANAGER:
            return base & (Q(purchasing_assignee__isnull=True) | Q(purchasing_assignee=work))
        return base & Q(purchasing_assignee=work)

    if unit == Unit.WAREHOUSE:
        # This ONE DOES get its own exclusive status — Purchasing sending to
        # Warehouse is an ordinary one-unit-at-a-time handoff, same shape as
        # every pre-existing transition (the FINAL_APPROVED/Purchasing
        # branch above is the only exception in this whole function).
        base = nonsplit & Q(status=CaseStatus.WITH_WAREHOUSE)
        if profile.role == Role.MANAGER:
            return base & (Q(warehouse_assignee__isnull=True) | Q(warehouse_assignee=work))
        return base & Q(warehouse_assignee=work)

    return None


def inbox_cases(user, *, role=None, work_user=None):
    """Cases currently in this user's inbox.

    Non-split cases use the whole-case status/holder. Split (Internal &
    External) cases are tracked per side from creation, so membership is decided
    purely by which side is held by this unit (and, for experts, assigned to
    them). The two sets are combined.

    The rule itself lives in :func:`inbox_filter_q`; this wrapper only turns it
    into the queryset callers order, filter and paginate.
    """
    q = inbox_filter_q(user, role=role, work_user=work_user)
    if q is None:
        return Case.objects.none()
    return Case.objects.filter(q).distinct()


def inbox_counts_for_users(users, *, narrow=None) -> dict:
    """Inbox size for every person in ``users``, as ``{user id: count}``.

    A manager's dashboard draws a card per expert. Every person's number is
    counted through the very same ``Q`` :func:`inbox_cases` would have used, so
    the figures are the ones the Inbox tab itself shows — this must never grow a
    second, hand-written copy of the routing rules.

    ``narrow`` is an optional callable applied to the base queryset before
    counting (the dashboard uses it for its created-at range filter), keeping
    that filter's definition with the caller that owns it.

    THIS IS THE SLOWEST THING ON THE MANAGEMENT DASHBOARD AND SPLITTING IT UP
    IS NOT THE ANSWER — the experiment has been run, please do not repeat it.
    Almost none of the cost below is the database: on the 300-case fixture the
    single statement it issues takes 2-3 ms while the call takes 38-61 ms. The
    rest is Django assembling the aggregate. ``Query.get_aggregation`` calls
    ``replace_expressions`` on every aggregate, which walks the aggregate's
    resolved ``filter`` tree doing ``replacements.get(node)`` at each node — a
    dict lookup, so each node is hashed, and hashing a ``WhereNode`` recursively
    hashes its whole subtree. The routing filters are deep (split sides,
    per-unit assignee columns), so that is quadratic in one person's tree and is
    paid once per person before a row is read.

    Issuing one aggregate per person instead makes each of those trees be
    assembled alone, which is much cheaper in Python and costs one table scan
    per person instead of one for everybody. Both shapes return byte-identical
    numbers (asserted equal for all three units at every size below). Median
    ms, Django 5.2 / SQLite, same process, interleaved:

        cases     unit          one combined aggregate   one aggregate each
          300     Commercial            60.7                   31.5
          300     Technical             38.4                   26.2
          300     Supply                48.1                   35.2
        3 000     Commercial            72.0                   47.2
        3 000     Supply                52.6                   46.2
        9 000     Commercial            93.3                   80.2
        9 000     Supply                64.3                   68.4
       18 000     Commercial           144.6                  213.1
       18 000     Technical             72.2                  135.1
       18 000     Supply                86.8                  186.2

    The per-person shape wins by a third at today's size and loses by a factor
    of two once the case table passes roughly ten thousand rows, because the
    scan cost grows with the data while the assembly cost does not. A workflow
    system accumulates cases forever, so the shape that degrades gracefully is
    the one to keep. Making this genuinely fast needs a smaller routing ``Q``
    or a cheaper hash on Django's side, not a different number of statements.
    """
    from django.db.models import Count

    counts = {}
    conds = {}
    for u in users:
        uid = getattr(u, "id", None) or getattr(u, "pk", None)
        if uid is None:
            continue
        counts[uid] = 0
        # Per person, so one unresolvable profile cannot zero everybody's card —
        # the per-user helper this replaces failed the same way, one card at a time.
        try:
            q = inbox_filter_q(u)
        except Exception:
            q = None
        if q is not None:
            conds[uid] = q
    if not conds:
        return counts
    base = Case.objects.all()
    if narrow is not None:
        base = narrow(base)
    agg = base.aggregate(**{
        f"inbox_{uid}": Count("id", filter=q, distinct=True)
        for uid, q in conds.items()
    })
    for uid in conds:
        counts[uid] = agg.get(f"inbox_{uid}") or 0
    return counts


def inbox_count(user, *, role=None, work_user=None) -> int:
    """How many cases are in this seat's inbox.

    ``.values("pk")`` before the count is not a different question: it is the
    same rows, counted by primary key instead of by whole row. ``inbox_cases``
    ends in ``.distinct()``, and counting a distinct queryset makes the database
    run ``SELECT COUNT(*) FROM (SELECT DISTINCT …)``. Without the ``values`` that
    inner select carries all forty-odd columns of every matching case, so the
    engine materialises and de-duplicates the entire row set to produce one
    integer — on every page in the platform, because the sidebar badge asks for
    this number on every render. Every column in the filter belongs to
    ``cases_case`` itself, so two rows sharing an id are identical in all of
    them and de-duplicating by id yields exactly the same count (verified equal
    for a commercial manager, a commercial expert, a technical expert and a
    supply expert on the 300-case fixture: 52, 12, 4 and 0 either way).

    Median ms for that badge, same process, interleaved, SQLite, by table size:

        cases    commercial manager   whole row 3.71 → by id 3.56
        3 000    commercial manager   whole row 5.53 → by id 4.72
       18 000    commercial manager   whole row 23.81 → by id 20.75

    Small today and growing with the archive, on every page, for free.
    """
    try:
        return inbox_cases(user, role=role, work_user=work_user).values("pk").distinct().count()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# "NEW" marker on inbox rows
#
# A case that has just arrived in somebody's inbox carries a NEW badge until
# that person opens it. The stored state is one CaseSeen row per (case, person);
# what "new again" means is documented on the model. These two functions are the
# only code that reads or writes it.
# ---------------------------------------------------------------------------
def annotate_inbox_seen(user, cases) -> None:
    """Set ``case.is_new_in_inbox`` on every row of an already-loaded inbox page.

    ONE query for the whole page no matter how many rows it has: the seen marks
    for all the case ids on the page are fetched together into a dict, and the
    actual comparison is done in Python against ``case.updated_at``, a column the
    inbox query already selected. The obvious alternative — asking
    ``CaseSeen.objects.filter(case=c, user=u)`` inside the row loop — would cost
    one query per row, and a Commercial inbox lists every case in the system.

    A case reads as NEW when this person has no seen mark for it, or when the
    case has been saved since that mark was written (see
    :class:`cases.models.CaseSeen` for why ``updated_at`` is the comparison
    point). The objects are mutated in place; the queryset, its filtering and its
    order are not touched, so this is purely additive to what the inbox shows.

    A lookup failure leaves every row un-marked rather than raising: a missing
    badge is a far better outcome than a broken inbox.
    """
    from .models import CaseSeen

    rows = list(cases)
    for case in rows:
        case.is_new_in_inbox = False
    user_id = getattr(user, "pk", None)
    if not user_id or not rows:
        return
    try:
        seen = dict(
            CaseSeen.objects
            .filter(user_id=user_id, case_id__in=[c.pk for c in rows])
            .values_list("case_id", "seen_at")
        )
    except Exception:
        logger.exception("annotate_inbox_seen: seen-mark lookup failed for user %s", user_id)
        return
    for case in rows:
        marked_at = seen.get(case.pk)
        case.is_new_in_inbox = bool(
            case.updated_at and (marked_at is None or marked_at < case.updated_at)
        )


def mark_case_seen(case, user) -> bool:
    """Record that ``user`` has now opened ``case``, clearing its NEW marker.

    Called from the case-detail view on every render of that page. Three
    properties matter and each one is deliberate:

    * It writes ONLY the ``CaseSeen`` row. The case is not saved and no
      ``CaseEvent`` is logged, so a read never reaches the audit timeline, never
      bumps ``updated_at`` and never moves the file between inboxes.
    * It is an upsert on the unique ``(case, user)`` pair, so two tabs opening
      the same case at the same moment cannot raise: the loser of the race hits
      the constraint and ``update_or_create`` (whose ``get_or_create`` retries
      the read on ``IntegrityError``) turns the failed insert into an update. It
      opens its own ``atomic`` block, so that failed INSERT rolls back to a
      savepoint instead of poisoning any transaction the caller is inside.
    * Every failure is swallowed and logged. A read receipt must never be the
      reason a case page 500s; the worst consequence of a failed write is that
      the badge survives until the next visit.

    Returns True when the mark was stored, False when it was not.
    """
    from django.utils import timezone

    from .models import CaseSeen

    case_id = getattr(case, "pk", None)
    user_id = getattr(user, "pk", None)
    if not case_id or not user_id:
        return False
    now = timezone.now()
    try:
        # Fast path first, because it is by far the common one: a case page is
        # revisited far more often than a case arrives, so the row almost always
        # exists already and a bare UPDATE settles it in a single query with no
        # savepoint round trip. ``update()`` reports how many rows it touched,
        # so a 0 here is exactly "this person has never opened this case" — and
        # only then do we pay for the race-safe upsert above.
        if CaseSeen.objects.filter(case_id=case_id, user_id=user_id).update(seen_at=now):
            return True
        CaseSeen.objects.update_or_create(
            case_id=case_id, user_id=user_id, defaults={"seen_at": now},
        )
        return True
    except Exception:
        logger.exception(
            "mark_case_seen: could not store seen mark for case %s / user %s",
            case_id, user_id)
        return False


def _request_work_context(request):
    """``work_context`` resolved once per request instead of per caller.

    A single page render asks for it several times (the sidebar context
    processor, the inbox count, then the case page's own permission check), and
    each call re-ran the substitute-tenure lookup. The answer cannot change
    inside one request — it is derived from the session's active role, which only
    changes on a separate role-switch POST — so it is memoised on the request
    object, which belongs to exactly one visitor and dies with the response.

    Keyed on the login user's pk for the same reason the role memo is: if
    anything swaps ``request.user`` mid-request we recompute rather than answer
    with somebody else's seat. The seat re-bind is repeated on every call, memo
    hit or not, because that thread-local is what timeline logging reads and it
    must stay bound exactly as often as before.
    """
    from people.role_nav import bind_work_seat, work_context

    user = getattr(request, "user", None)
    key = getattr(user, "pk", None)
    memo = getattr(request, "_ft_services_work_context", None)
    if isinstance(memo, tuple) and len(memo) == 2 and memo[0] == key:
        ctx = memo[1]
        bind_work_seat(ctx.seat_user)
        return ctx
    ctx = work_context(request)
    try:
        request._ft_services_work_context = (key, ctx)
    except Exception:
        # The memo is an optimisation only; if the request object refuses the
        # attribute we simply resolve again, exactly as before.
        pass
    return ctx


def inbox_cases_for_request(request):
    """Inbox queryset using the active role's seat user (fixes multi-seat 500)."""
    ctx = _request_work_context(request)
    return inbox_cases(ctx.login_user, role=ctx.role, work_user=ctx.seat_user)


def can_do_side_action(case: Case, user, action: str, side: str, *,
                       role=None, work_user=None) -> bool:
    """Whether a user may perform a per-side transition on a split case."""
    from types import SimpleNamespace

    if not (case.is_split and side in (Side.INTERNAL, Side.EXTERNAL)):
        return False
    work = work_user or user
    profile = getattr(user, "profile", None)
    if role is not None:
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None:
        return False
    holder = case.side_holder(side)
    has_pi = case.forms.filter(kind=FormKind.PI, side=side).exists()
    has_to = case.forms.filter(kind=FormKind.TO, side=side).exists()
    owns = can_act_on_side(case, user, side, role=role, work_user=work)

    # --- Assign this side to an expert (manager of the holding unit) ---
    if action == "assign":
        if holder == Unit.SUPPLY:
            existing = (case.supply_internal_assignee_id if side == Side.INTERNAL
                        else case.supply_external_assignee_id)
            return (profile.unit == Unit.SUPPLY and profile.role == Role.MANAGER
                    and not existing and not has_pi)
        if holder == Unit.TECHNICAL:
            existing = (case.technical_internal_assignee_id if side == Side.INTERNAL
                        else case.technical_external_assignee_id)
            return (profile.unit == Unit.TECHNICAL and profile.role == Role.MANAGER
                    and not existing and not has_to)
        return False

    # --- Commercial side actions (side currently with Commercial) ---
    if action in ("submit_to_technical", "send_to_supply_from_commercial",
                  "close", "request_cancel", "send_to_client",
                  "new_inquiry_version", "edit_inquiry", "return_to_supply"):
        if holder != Unit.COMMERCIAL or not owns:
            return False
        # Every terminal writer (cancel/burn/final-close/cannot-supply) parks the
        # side's holder back on Commercial, so "the side is with Commercial" on
        # its own does not mean the side is still live. Without this test a
        # replayed or hand-made POST from the creator would route a finished side
        # onwards again — a cancelled side back into Technical's inbox, a
        # final-closed side back to merely CLOSED. This is the server-side half of
        # the rule the case page already applies when it decides which per-side
        # buttons to draw.
        side_st = case.side_status(side)
        if side_st in CaseStatus.TERMINAL:
            return False
        # A side already sent to the client (CLOSED) or marked final is likewise
        # no longer "at Commercial" for routing or editing. Two deliberate
        # exceptions: "New version" — branching a fresh inquiry off a sent side is
        # exactly how such a side gets revised (see can_new_inquiry_version) — and
        # the cancel request on a FINAL_APPROVED side, which mirrors the cancel
        # the whole-case path offers on a final-approved case. A merely CLOSED
        # side still has no cancel: there it is Final Approved or Burned.
        if (side_st in (CaseStatus.CLOSED, CaseStatus.FINAL_APPROVED)
                and action != "new_inquiry_version"
                and not (action == "request_cancel"
                         and side_st == CaseStatus.FINAL_APPROVED)):
            return False
        if action == "submit_to_technical":
            # Currency-conversion-only reopen stays with Commercial.
            return not is_currency_conversion_only(case, side)
        if action == "send_to_supply_from_commercial":
            return case.needs_pricing and not is_currency_conversion_only(case, side)
        if action == "return_to_supply":
            # Only when this side was received FROM Supply (WITH_COMMERCIAL),
            # never when Technical returned it or after a New Version reopen.
            if is_currency_conversion_only(case, side):
                return False
            return has_pi and case.side_status(side) == CaseStatus.WITH_COMMERCIAL
        if action == "edit_inquiry":
            cur = case.current_form(FormKind.INQUIRY, side)
            return not (cur and cur.sent)
        if action in ("close", "send_to_client"):
            # Forms must be at the current inquiry version for this side — OR the
            # TO is a flagged Technical-Problem offer (which needs no Proforma).
            return _can_send_to_client(case, side)
        return True  # cancel / new_inquiry_version

    # --- Commercial: finalise a side that was already sent to the client ---
    if action == "finalize":
        return (holder == Unit.COMMERCIAL and owns
                and case.side_status(side) == CaseStatus.CLOSED)

    # --- Commercial: Final-Close a side that was already Final-Approved ---
    if action == "final_close":
        return (holder == Unit.COMMERCIAL and owns
                and case.side_status(side) == CaseStatus.FINAL_APPROVED)

    # --- Commercial: Burn a side once it has been sent to the client ---
    # Offered while the side is CLOSED and, since the owner asked for it, while
    # it is FINAL_APPROVED too: a final approval can still fall through. Same
    # transition (burn_side) either way — an expert's burn goes to the commercial
    # manager, a manager's burn takes effect at once.
    if action == "burn":
        return (holder == Unit.COMMERCIAL and owns
                and case.side_status(side) in (CaseStatus.CLOSED,
                                               CaseStatus.FINAL_APPROVED))

    # --- Technical side actions ---
    if action == "send_to_supply":
        if holder != Unit.TECHNICAL or not owns:
            return False
        # Cannot forward to Supply while any TO row is flagged Technical Problem
        # or while any active row still has an empty BRAND.
        if has_to and _to_blocks_supply(case, side):
            return False
        return has_to
    if action == "return_to_commercial":
        return holder == Unit.TECHNICAL and owns
    if action == "return_to_supply":
        # Commercial returns a side to Supply only if it arrived FROM Supply
        # (WITH_COMMERCIAL). Technical returns a side it received back FROM
        # Supply (RETURNED_TO_TECHNICAL) to Supply.
        if holder == Unit.COMMERCIAL and owns:
            return has_pi and case.side_status(side) == CaseStatus.WITH_COMMERCIAL
        if holder == Unit.TECHNICAL and owns:
            if _to_blocks_supply(case, side):
                return False
            return case.side_status(side) == CaseStatus.RETURNED_TO_TECHNICAL
        return False

    # --- Supply / Technical -> Commercial ---
    if action in ("send_to_commercial", "return_to_technical", "cannot_supply"):
        # Technical may submit to Commercial a side it got back from Supply, as
        # long as the current Proforma carries no remark.
        if action == "send_to_commercial" and holder == Unit.TECHNICAL and owns:
            if case.side_status(side) != CaseStatus.RETURNED_TO_TECHNICAL:
                return False
            if not has_to:
                return False
            return not _pi_blocks_commercial(case, side)
        if holder != Unit.SUPPLY or not owns:
            return False
        if action == "send_to_commercial" and not has_pi:
            return False
        # Per-side block: this side's current PI carrying any filled remark cannot
        # be forwarded to commercial (return-to-technical stays available).
        if action == "send_to_commercial" and _pi_blocks_commercial(case, side):
            return False
        return True
    return False


def _iter_current_forms(case, kind: str):
    """Current forms of a kind, served from ``prefetch_related('forms')`` when
    present (no query) and otherwise fetched — so hot paths that prefetch avoid
    N+1 queries while every other caller keeps its original behaviour."""
    cached = getattr(case, "_prefetched_objects_cache", None)
    if cached is not None and "forms" in cached:
        return [f for f in cached["forms"] if f.kind == kind and f.is_current]
    return list(case.forms.filter(kind=kind, is_current=True))


def _to_has_technical_problems(case, side: str = "") -> bool:
    """True when any current TO has rows flagged as Technical Problem (_issue='1').

    Soft-deleted rows are ignored. If *side* is given, only that side's TO is checked.
    """
    try:
        forms = _iter_current_forms(case, FormKind.TO)
        if side in (Side.INTERNAL, Side.EXTERNAL):
            forms = [f for f in forms if f.side == side]
        for form in forms:
            for row in (form.table or []):
                if str((row or {}).get("_deleted", "") or "") == "1":
                    continue
                if str((row or {}).get("_issue", "") or "") == "1":
                    return True
    except Exception:
        return False
    return False


def _side_ready_to_send_client(case, side=None) -> bool:
    """One side (or a non-split case): ready to send to the client.

    Normal path — the TO AND the PI both exist at the current inquiry version.

    Technical-problem path — the TO is at the current inquiry version AND has at
    least one flagged Technical-Problem row. Such an offer can never reach Supply
    (send-to-supply is blocked), so a Proforma is NOT required: Commercial may
    send the flagged offer straight to the client. This lets a version stay
    PI-less (e.g. TO v02 has problems, so Supply never built PI v00/v01/v02).
    """
    inq = case.current_form(FormKind.INQUIRY, side)
    if inq is None:
        return False
    to = case.current_form(FormKind.TO, side)
    if (to is None or to.version < inq.version
            or bool(to.two_stage) != bool(inq.two_stage)):
        return False
    if _to_has_technical_problems(case, side or ""):
        return True  # flagged offer -> no Proforma required
    pi = case.current_form(FormKind.PI, side)
    if (pi is None or pi.version < inq.version
            or bool(pi.two_stage) != bool(inq.two_stage)):
        return False
    return True


def _can_send_to_client(case, side: str = "") -> bool:
    """Send-to-client gate: all forms ready, OR a Technical-Problem offer (which
    needs no Proforma). Whole split case requires every active side to qualify."""
    if case.is_split and not side:
        return all(_side_ready_to_send_client(case, sc) for sc in case.sides)
    return _side_ready_to_send_client(case, side or None)


def is_currency_conversion_only(case, side: str = "") -> bool:
    """True when the current inquiry was opened only to re-convert Proforma currency.

    Such a version does not go to Technical/Supply: Commercial may convert the
    Proforma and Send to client / Cancel only.
    """
    inq = case.current_form(FormKind.INQUIRY, side or None)
    if inq is None:
        return False
    return bool((inq.meta or {}).get("currency_conversion_only"))


def _clone_offer_form_for_version(case, actor, *, kind: str, side: str,
                                  version: int, two_stage: bool,
                                  source_form, clear_currency: bool = False,
                                  currency_only: bool = True):
    """Copy a TO/PI snapshot onto a new inquiry version.

    Two callers, two different clones, chosen by ``currency_only``:

      * A currency-only reopen (default, ``currency_only=True``): the clone is
        a Commercial-only artefact — marked ``currency_conversion_only`` and
        pre-``sent`` — so Technical/Supply never see or work it (tool_for_case
        and archive/inbox filters skip straight past it to the last REAL
        snapshot; see ``form_is_currency_conversion_only``'s callers). Nothing
        changed for Technical/Supply here, so there is nothing to hand them.

      * A content-unchanged Update-Price revision (``currency_only=False``):
        the table did not change either, but "Update Price" — unlike a
        currency reopen — IS a real ask directed at Technical/Supply (go
        re-look at pricing). Without carrying TO/PI forward at all, each unit
        was found "behind" this inquiry version independently and had no way
        to Edit/forward without first running its OWN redundant New Version
        pass — see the version-carry bug this parameter was added to fix.
        This clone is therefore an ordinary, OPEN, visible TO/PI at the new
        version: no ``currency_conversion_only`` marker, not pre-sent.
    """
    if source_form is None:
        return None
    meta = dict(source_form.meta or {})
    if clear_currency:
        for key in ("currency_converted", "currency_rate", "currency_from",
                    "currency_label"):
            meta.pop(key, None)
        # Keep meta["currency"] as the current display unit so the clone starts
        # from the last converted amounts; Commercial can convert again.
    if currency_only:
        meta["currency_conversion_only"] = True
    else:
        meta.pop("currency_conversion_only", None)
    clone = CaseForm(
        case=case, kind=kind, side=side or "",
        version=version, created_by=actor,
        unit_at_creation=Unit.COMMERCIAL,
    )
    clone.columns = list(source_form.columns or [])
    clone.table = [dict(r or {}) for r in (source_form.table or [])]
    clone.meta = meta
    clone.sent = currency_only
    clone.two_stage = bool(two_stage)
    clone.is_current = True
    clone.save()
    clone.make_current()
    return clone


def form_is_currency_conversion_only(form) -> bool:
    """True for inquiry/TO/PI snapshots created only for Proforma FX conversion."""
    if form is None:
        return False
    return bool((getattr(form, "meta", None) or {}).get("currency_conversion_only"))


def _restore_real_offer_current(case, *, side: str, kind: str):
    """After a real inquiry revision, demote currency-only TO/PI clones.

    Technical/Supply must resume from their last real snapshot (or build fresh
    at the new inquiry version) — never from a Commercial FX-only clone.
    """
    forms = list(
        # Newest first, with the two-stage generation ahead of the same-numbered
        # version it supersedes — otherwise "resume from the last real snapshot"
        # could hand the unit back the pre-two-stage offer.
        case.forms.filter(kind=kind, side=side or "")
        .order_by("-version", "-two_stage", "-id")
    )
    real = next((f for f in forms if not form_is_currency_conversion_only(f)), None)
    for f in forms:
        if form_is_currency_conversion_only(f) and f.is_current:
            f.is_current = False
            f.save(update_fields=["is_current"])
    if real is not None:
        real.make_current()


def _pi_row_missing_fields(row, needs_pricing: bool) -> list:
    """Which required PI fields are missing on ONE active row.

    Rule (Supply → Commercial): every active row must carry a BRAND and a TIME;
    when the case also needs pricing (TO & PI) it must carry a UNIT PRICE too.
    FTCO code is required only when ``REQUIRE_FTCO_CODE_TO_SUPPLY`` is True
    (same gate as Technical → Supply). A NOT-SUPPLIABLE row is fully exempt
    (its four columns are fixed/empty on purpose). Deleted rows are exempt.
    Writing a new proforma remark clears the code, so a remarked row
    automatically fails on "code" when codes are required.
    """
    from django.conf import settings as _dj_settings

    if str((row or {}).get("_deleted", "") or "") == "1":
        return []
    if str((row or {}).get("_unsuppliable", "") or "") == "1":
        return []
    missing = []
    require_code = bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", False))
    if require_code and not str((row or {}).get("کد", "") or "").strip():
        missing.append("code")
    if not str((row or {}).get("BRAND", "") or "").strip():
        missing.append("brand")
    if not str((row or {}).get("TIME", "") or "").strip():
        missing.append("time")
    if needs_pricing and not _has_price_value((row or {}).get("UNIT PRICE", "")):
        missing.append("price")
    return missing


def _pi_commercial_blockers(case, side: str = "") -> dict:
    """Per-field lists of client rows (#) that block Supply → Commercial.

    Returns e.g. ``{"code": ["3"], "brand": ["3","5"], "time": [], "price": []}``
    but only keys that actually have offending rows. An empty dict means the
    Proforma is ready to submit to Commercial. Independent per side.
    """
    out = {"code": [], "brand": [], "time": [], "price": []}
    try:
        forms = _iter_current_forms(case, FormKind.PI)
        if side in (Side.INTERNAL, Side.EXTERNAL):
            forms = [f for f in forms if f.side == side]
        needs_pricing = bool(getattr(case, "needs_pricing", True))
        for form in forms:
            for row in (form.table or []):
                for field in _pi_row_missing_fields(row, needs_pricing):
                    out[field].append(_row_client_no(row))
    except Exception:
        return {}
    return {k: v for k, v in out.items() if v}


def _pi_blocks_commercial(case, side: str = "") -> bool:
    """True when the CURRENT Proforma cannot yet be forwarded to Commercial
    because at least one active, suppliable row is missing a required field
    (code / brand / time, plus price for TO & PI). Only the latest version is
    checked, so re-saving with everything filled lifts the block automatically.
    """
    return bool(_pi_commercial_blockers(case, side))


def _row_client_no(row) -> str:
    return str((row or {}).get("#", (row or {}).get("Item Code", (row or {}).get("Item", ""))) or "").strip() or "?"


def _has_price_value(val) -> bool:
    """True when a saved UNIT PRICE holds a real, non-zero number (any format)."""
    digits = re.sub(r"\D", "", str(val or ""))
    return bool(digits) and any(d != "0" for d in digits)


def _to_rows_without_code(case, side: str = "") -> list:
    """Client rows (#) of ACTIVE Technical-Offer rows that carry no FTCO code.

    A TO may only be sent to Supply when every active row is coded. Soft-deleted
    rows are exempt. Technical-Problem rows are handled by the dedicated guard
    (_to_has_technical_problems), so they are skipped here to avoid a confusing
    double message.
    """
    out = []
    try:
        forms = _iter_current_forms(case, FormKind.TO)
        if side in (Side.INTERNAL, Side.EXTERNAL):
            forms = [f for f in forms if f.side == side]
        for form in forms:
            for row in (form.table or []):
                if str((row or {}).get("_deleted", "") or "") == "1":
                    continue
                if str((row or {}).get("_issue", "") or "") == "1":
                    continue
                code = str((row or {}).get("کد", "") or "").strip()
                if not code:
                    out.append(_row_client_no(row))
    except Exception:
        return []
    return out


def _to_rows_without_brand(case, side: str = "") -> list:
    """Client rows (#) of ACTIVE TO rows with an empty BRAND cell.

    Technical may only Submit to Supply when every active row has BRAND filled.
    Soft-deleted and Technical-Problem rows are skipped (problem rows already
    block Supply via ``_to_has_technical_problems``).
    """
    out = []
    try:
        forms = _iter_current_forms(case, FormKind.TO)
        if side in (Side.INTERNAL, Side.EXTERNAL):
            forms = [f for f in forms if f.side == side]
        for form in forms:
            for row in (form.table or []):
                if str((row or {}).get("_deleted", "") or "") == "1":
                    continue
                if str((row or {}).get("_issue", "") or "") == "1":
                    continue
                brand = str((row or {}).get("BRAND", "") or "").strip()
                if not brand:
                    out.append(_row_client_no(row))
    except Exception:
        return []
    return out


def _to_blocks_supply(case, side: str = "") -> bool:
    """True when the current TO cannot be forwarded to Supply."""
    return (
        _to_has_technical_problems(case, side)
        or bool(_to_rows_without_brand(case, side))
    )


def _pi_rows_without_price(case, side: str = "") -> list:
    """Client rows (#) of ACTIVE Proforma rows that carry no price.

    Only relevant when the case needs pricing (TO & PI). A PI may only be sent to
    Commercial when every active row is priced. Soft-deleted and NOT-SUPPLIABLE
    rows are exempt (they legitimately carry no price).
    """
    if not case.needs_pricing:
        return []
    out = []
    try:
        forms = _iter_current_forms(case, FormKind.PI)
        if side in (Side.INTERNAL, Side.EXTERNAL):
            forms = [f for f in forms if f.side == side]
        for form in forms:
            for row in (form.table or []):
                if str((row or {}).get("_deleted", "") or "") == "1":
                    continue
                if str((row or {}).get("_unsuppliable", "") or "") == "1":
                    continue
                if not _has_price_value(row.get("UNIT PRICE", "")):
                    out.append(_row_client_no(row))
    except Exception:
        return []
    return out


# ---------------------------------------------------------------------------
# Permissions: which actions a user may take on a case right now
# ---------------------------------------------------------------------------
def allowed_actions(case: Case, user, *, role=None, work_user=None) -> set[str]:
    """Return the set of action keys the user may perform on this case now.

    Routing model: when a case lands in Technical/Supply it is unassigned and
    only the unit MANAGER sees it. The manager may act directly OR assign it to
    an expert; once assigned to an expert the manager can no longer act (only
    view), and the assigned expert works it. A unit can only hand a case off
    (forward or return) after it has built at least one of its own forms.

    ``role`` / ``work_user`` — same meaning as ``inbox_cases``: active PersonRole
    and its seat User. Ownership checks (created_by / assignees) use
    ``work_user`` so Delegated tasks keep the same action rules after the seat
    FK remapping, including secondary seats.
    """
    from types import SimpleNamespace

    work = work_user or user
    profile = getattr(user, "profile", None)
    if role is not None:
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None or profile.is_admin:
        return set()
    # A unit outside the TO/PI workflow (Marketing) has no action on any case,
    # and says so here rather than by falling through. Without this the fall
    # through is not empty: none of the unit branches below match, so nothing
    # is added and nothing returns early — and then the "export is available to
    # every unit" line at the very bottom, which sits OUTSIDE all of them, hands
    # back {"export"} for a seat that has no business with the case at all.
    # (Nothing could be downloaded with it — every export route also asks
    # user_can_view_case, which refuses — but a set that says "you may export"
    # is the wrong answer to give, and the case page draws its buttons from
    # exactly this set.)
    #
    # The ``profile.unit and`` half is load-bearing and must stay: a BLANK unit
    # is not a non-workflow unit, it is an existing seat kind (a general
    # manager, an account not yet assigned) whose answer here is already
    # settled and must not move. This narrows nothing that exists today — no
    # seat carries a unit outside the three — it only decides the new one.
    if profile.unit and profile.unit not in Unit.WORKFLOW:
        return set()

    actions: set[str] = set()
    unit, role_name = profile.unit, profile.role
    status = case.status
    is_manager = role_name == Role.MANAGER
    work_id = getattr(work, "id", None) or getattr(work, "pk", None)

    # --- Commercial ----------------------------------------------------
    if unit == Unit.COMMERCIAL:
        # Only the case creator (active seat) may take workflow actions. Other
        # Commercial users (manager / supervisor / peers) may view and export.
        is_creator = case.created_by_id == work_id
        has_pi = case.forms.filter(kind=FormKind.PI).exists()
        has_to = case.forms.filter(kind=FormKind.TO).exists()
        if not is_creator:
            actions.add("view")
            if has_to or has_pi:
                actions.add("export")
            # Contact / client-role fields are not the creator's workflow
            # actions (submit, edit_info, …) that stay creator-only below —
            # they are the same "Commercial may always update the four client
            # contact fields on any non-terminal case they can open" grant the
            # creator gets further down, just reached from this branch too.
            # Without this, a case handed off to a different Commercial
            # person (no formal Delegate — e.g. the manager fixing a typo, or
            # a peer covering informally) had no way to open that editor at
            # all while the case sat with Technical/Supply, even though any
            # Commercial seat can already view/export it here.
            if status not in CaseStatus.TERMINAL:
                actions.add("edit_contacts")
            # Cancel / burn approval queue: commercial manager resolves requests
            # filed by experts (whole case or a split side).
            if is_manager and (
                status == CaseStatus.PENDING_CANCEL
                or (case.is_split and any(
                    case.side_status(sc) == CaseStatus.PENDING_CANCEL
                    for sc in case.sides))
            ):
                actions.update({"approve_cancel", "reject_cancel", "comment"})
            return actions

        # Creator whose cancel/burn is waiting on the manager: view only.
        if status == CaseStatus.PENDING_CANCEL:
            actions.update({"view", "comment"})
            if has_to or has_pi:
                actions.add("export")
            return actions
        if case.is_split and any(
                case.side_status(sc) == CaseStatus.PENDING_CANCEL for sc in case.sides):
            # One side pending approval — creator waits; manager acts via the
            # non-creator branch when they open it.
            actions.update({"view", "comment"})
            if has_to or has_pi:
                actions.add("export")
            return actions

        # A new inquiry version may ONLY be made once the previous version has
        # been sent to the client (CLOSED). While the case is mid-flight
        # (returned/with-commercial/cannot-supply) the commercial expert revises
        # by routing, not by branching a new inquiry version.
        can_new_version = (status == CaseStatus.CLOSED)
        if status == CaseStatus.DRAFT:
            # Never sent yet -> fully editable (incl. case information).
            actions.update({"edit", "edit_info", "submit_to_technical",
                            "comment", "request_cancel"})
        elif status == CaseStatus.RETURNED_TO_COMMERCIAL:
            # From Technical (or after New Version): route back to Technical only.
            # No return-to-supply here — that is only when the case arrived from
            # Supply (WITH_COMMERCIAL). After New Version the inquiry is unsent,
            # so Edit stays available until the next routing action marks it sent.
            if is_currency_conversion_only(case):
                # Opened only to re-convert Proforma currency: Cancel + Send to
                # client (no Submit / Return routing).
                actions.update({"comment", "request_cancel", "view"})
                if _can_send_to_client(case):
                    actions.add("close")
            else:
                actions.update({"comment", "submit_to_technical", "request_cancel"})
                cur_inq = case.current_form(FormKind.INQUIRY)
                if not (cur_inq and cur_inq.sent):
                    actions.add("edit")
                # Send-to-client requires every form up to date with the current
                # inquiry version (TO + PI) — OR a flagged Technical-Problem offer,
                # which can never reach Supply and so needs no Proforma.
                if _can_send_to_client(case):
                    actions.add("close")
        elif status == CaseStatus.WITH_COMMERCIAL:
            # Received from Supply — may return to Supply for re-pricing, or
            # also route to Technical when needed.
            if is_currency_conversion_only(case):
                actions.update({"comment", "request_cancel", "view"})
                if _can_send_to_client(case):
                    actions.add("close")
            else:
                actions.update({"comment", "export", "request_cancel",
                                "submit_to_technical"})
                if _can_send_to_client(case):
                    actions.add("close")
                if has_pi:
                    actions.add("return_to_supply")
                if role_name in {Role.MANAGER, Role.SUPERVISOR}:
                    actions.add("margin")
        elif status == CaseStatus.UNSUPPLIABLE:
            # Supply could not fulfil it; the case is back with the commercial
            # expert, who may re-route it to Technical or cancel it (a cancel
            # keeps the "Cannot supply" status rather than becoming "Cancelled").
            # Not a "received from Supply for pricing" handoff → no return_to_supply.
            actions.update({"comment", "submit_to_technical", "request_cancel"})
        if status == CaseStatus.UNSUPPLIABLE:
            actions.add("comment")
        if status == CaseStatus.CLOSED:
            # Sent to the client: Final Approved (keeps it open), Burned
            # (terminal), or branch a brand-new inquiry version to revise. No
            # cancel / submit / return here.
            actions = {"finalize", "burn", "view"}
        if status == CaseStatus.FINAL_APPROVED:
            # A final-approved case is still open. Final Close shuts it, and a
            # final approval can still fall through afterwards — the client walks
            # away, or the order is cancelled — so Burn and the cancel request
            # stay on offer here exactly as they were while the case was merely
            # CLOSED. Same two transitions, same confirm-with-comment control,
            # and an expert's request still goes to the commercial manager.
            actions = {"final_close", "burn", "request_cancel", "comment", "view"}
        if can_new_version:
            actions.add("new_inquiry_version")
        # Two-stage conversion (Internal/External single side -> BOTH) is offered
        # to the commercial owner of the case AT ANY TIME while the case is still
        # alive — including while Technical or Supply is holding it. The two sides
        # of an Internal & External case move independently, so adding the missing
        # side is not a routing decision about the side that already exists: that
        # side keeps its status, its holder, its assignee and its whole history,
        # and the case itself does not come back to Commercial. Only the brand-new
        # side is created, and it lands with Commercial so Commercial can send it
        # on its own.
        #
        # What still rules it out: the case is already Internal & External, it
        # has ended (final-closed / burned / cancelled / cannot-supply-closed)
        # or been marked FINAL_APPROVED (still technically open, but the owner
        # has decided this side is done — creating a second, independent stream
        # off a side awaiting shutdown is exactly the half-finished state this
        # feature must not create), or it has no inquiry to copy the new side
        # from. The earlier holder/status gate and the "TO and PI must be built
        # at the current version" gate are gone — both only ever described a
        # case that had come back to Commercial finished, which is exactly the
        # precondition being lifted. It does NOT need a new version, and
        # disappears once the case is already split.
        if (not case.is_split
                and case.price_type in {PriceType.INTERNAL, PriceType.EXTERNAL}
                and status not in CaseStatus.TERMINAL
                and status != CaseStatus.FINAL_APPROVED
                and case.current_form(FormKind.INQUIRY) is not None):
            actions.add("upgrade_two_stage")
        actions.add("view")
        # Commercial may always update the four client contact fields on any
        # non-terminal case they can open (independent of holder / edit_info).
        if status not in CaseStatus.TERMINAL:
            actions.add("edit_contacts")
        # Export is always available once a TO/PI exists — any unit member who
        # can open the case may download, even when they are not the holder.
        if has_to or has_pi:
            actions.add("export")

    # --- Technical -----------------------------------------------------
    elif unit == Unit.TECHNICAL:
        if status in {CaseStatus.WITH_TECHNICAL, CaseStatus.RETURNED_TO_TECHNICAL}:
            has_to = case.forms.filter(kind=FormKind.TO).exists()
            # Manager acts only while unassigned or assigned to self; experts
            # only when the case is assigned to them.
            can_act = (case.assigned_to_id in (None, work_id)) if is_manager \
                else (case.assigned_to_id == work_id)
            # Return goes back to whoever last handed it to Technical; the
            # forward target is the opposite unit (commercial -> supply,
            # supply -> commercial).
            from_supply = _last_sender_unit(case) == Unit.SUPPLY
            return_action = "return_to_supply" if from_supply else "return_to_commercial"
            forward_action = "send_to_commercial" if from_supply else "send_to_supply"
            if can_act:
                actions.update({"build_to", "comment"})
                # A manager may assign only before any TO exists; once a TO is
                # built the case stays with whoever is handling it.
                if is_manager and not has_to:
                    actions.add("assign")
                # Returning to the sender — blocked for Supply while TO has
                # Technical Problem rows (cannot route to Supply at all).
                if not (return_action == 'return_to_supply'
                        and has_to and _to_blocks_supply(case)):
                    actions.add(return_action)
                # Forwarding onward needs at least one TO form.
                if has_to:
                    # Cannot forward to Supply while Technical Problem flags or
                    # empty BRAND cells remain on the TO.
                    if forward_action == 'send_to_supply' and _to_blocks_supply(case):
                        pass  # blocked — button hidden until resolved
                    # Cannot forward to Commercial while the current Proforma still
                    # carries any remark. Technical can't edit the PF remark, so it
                    # must hand the case back to Supply to resolve it.
                    elif forward_action == 'send_to_commercial' and _pi_blocks_commercial(case):
                        pass  # blocked — return to Supply to clear the remark
                    else:
                        actions.add(forward_action)
                if is_manager and case.awaiting_approval:
                    actions.add("approve_send")
        # A split (Internal & External) case moves per side, and nothing ever
        # writes the whole-case ``status`` again until every side is terminal —
        # so the status gate above never opens for one and delegation would be
        # impossible. Technical still delegates the whole case (one expert works
        # both sides, which is why assign() writes ``technical_assignee``), so it
        # is offered here under exactly the conditions the case page already uses
        # to draw its single "Assign to expert (both sides)" control.
        if (case.is_split and is_manager
                and (case.side_holder(Side.INTERNAL) == Unit.TECHNICAL
                     or case.side_holder(Side.EXTERNAL) == Unit.TECHNICAL)
                and not case.technical_assignee_id
                and not case.forms.filter(kind=FormKind.TO).exists()):
            actions.add("assign")
        actions.add("view")

    # --- Supply --------------------------------------------------------
    elif unit == Unit.SUPPLY:
        if status == CaseStatus.WITH_SUPPLY:
            has_pi = case.forms.filter(kind=FormKind.PI).exists()
            sides = case.sides or [""]
            assignee_of = {
                Side.INTERNAL: case.supply_internal_assignee_id,
                Side.EXTERNAL: case.supply_external_assignee_id,
                "": case.supply_assignee_id,
            }
            my_sides = [s for s in sides if assignee_of.get(s) == work_id]
            unassigned_sides = [s for s in sides if not assignee_of.get(s)]
            # A manager works the unassigned sides; experts work their own.
            can_act = bool(my_sides) or (is_manager and bool(unassigned_sides))
            if can_act:
                actions.update({"build_pi", "cannot_supply", "comment"})
                # The manager may still assign any side that has no expert yet.
                if is_manager and unassigned_sides:
                    actions.add("assign")
                actions.add("return_to_technical")
                if has_pi and not _pi_blocks_commercial(case):
                    actions.add("send_to_commercial")
                if is_manager and case.awaiting_approval:
                    actions.add("approve_send")
        actions.add("view")

    # --- Purchasing ------------------------------------------------------
    # No exclusive status gate here (unlike every branch above and below) —
    # see inbox_filter_q's own PURCHASING branch and assign_purchasing's own
    # docstring for why: Commercial keeps acting on the SAME FINAL_APPROVED
    # case at the same time, on purpose.
    elif unit == Unit.PURCHASING:
        if status == CaseStatus.FINAL_APPROVED:
            can_act = work_id == case.purchasing_assignee_id or (
                is_manager and not case.purchasing_assignee_id)
            if can_act:
                actions.update({"build_purchase_invoice", "comment"})
                if is_manager and not case.purchasing_assignee_id:
                    actions.add("assign_purchasing")
                if case.forms.filter(kind=FormKind.PURCHASE_INVOICE).exists():
                    actions.add("send_to_warehouse")
        actions.add("view")

    # --- Warehouse ---------------------------------------------------------
    # Ordinary exclusive-status branch, same shape as Technical/Supply above
    # — Warehouse's own downstream behaviour (what it actually DOES with a
    # received Purchase Invoice) is not yet designed and deliberately not
    # guessed at here; only the base "manager can hand it to an expert"
    # capability every other unit already has is granted for now.
    elif unit == Unit.WAREHOUSE:
        if status == CaseStatus.WITH_WAREHOUSE:
            can_act = work_id == case.warehouse_assignee_id or (
                is_manager and not case.warehouse_assignee_id)
            if can_act:
                actions.add("comment")
                if is_manager and not case.warehouse_assignee_id:
                    actions.add("assign")
        actions.add("view")

    # Export is available to every unit as soon as a TO or PI exists — even when
    # the viewer is not the current holder of the case.
    if case.forms.filter(kind__in=[FormKind.TO, FormKind.PI], is_current=True).exists():
        actions.add("export")

    return actions


def allowed_actions_for_request(case: Case, request) -> set[str]:
    """``allowed_actions`` using the active role's seat (inbox-consistent)."""
    ctx = _request_work_context(request)
    return allowed_actions(
        case, ctx.login_user, role=ctx.role, work_user=ctx.seat_user,
    )


def user_can_view_case(case: Case, user, *, case_forms=None, case_events=None,
                       role=None, work_user=None) -> bool:
    """Whether ``user`` may see this case at all.

    This is the single, shared rule behind case_detail's own visibility check
    AND — as of the 2026-07 security pass — every export/print route, so a
    document can never be downloaded by someone who could not open the case
    itself in the first place. Previously the export routes only checked
    "does your unit do this kind of export", never "can you see this specific
    case", which let anyone in the right unit pull any case's documents by
    guessing/incrementing the case id. Extracted verbatim from the check that
    already lived in cases.views.case_detail; behaviour for that page is
    unchanged, only now reused instead of duplicated.

    ``case_forms``/``case_events`` let a caller that already prefetched them
    (case_detail) skip a second query; other callers get them fetched here.
    ``role`` / ``work_user`` mirror ``inbox_cases`` for secondary seats.
    """
    from types import SimpleNamespace

    work = work_user or user
    work_id = getattr(work, "id", None) or getattr(work, "pk", None)
    profile = getattr(user, "profile", None)
    if role is not None:
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None:
        return False
    if profile.is_admin or profile.is_general_manager:
        return True
    # "view" and "export" are handed out by allowed_actions unconditionally —
    # every unit member may download a document once one exists, and "view" is
    # added outside the status gate in each branch — so their presence says
    # nothing about whether this person has anything to do with *this* case.
    # Testing the raw set therefore made every Technical/Supply user pass here
    # and left the participation rules below unreachable, which is how any case
    # (and its Technical Offer) could be pulled by walking the id. Only an
    # action-bearing set proves involvement; everything else falls through to the
    # participation / manager / supervisor test, which is the real rule.
    if allowed_actions(case, user, role=role, work_user=work) - {"view", "export"}:
        return True

    if case_forms is None:
        case_forms = list(case.forms.all())
    if case_events is None:
        case_events = list(case.events.all())

    participated = (
        case.created_by_id == work_id
        or case.assigned_to_id == work_id
        or case.technical_assignee_id == work_id
        # Per-side technical assignees count too: assign() writes them for a side
        # whose holder is Technical, and the assignment event is logged under the
        # manager who made it, so the expert has no other trace on the case.
        or case.technical_internal_assignee_id == work_id
        or case.technical_external_assignee_id == work_id
        or case.supply_assignee_id == work_id
        or case.supply_internal_assignee_id == work_id
        or case.supply_external_assignee_id == work_id
        or any(f.created_by_id == work_id for f in case_forms)
        or any(e.actor_id == work_id for e in case_events)
    )
    manager_of_unit = bool(
        profile.role == Role.MANAGER
        and (case.holder_unit == profile.unit
             or any(e.from_unit == profile.unit for e in case_events)
             or any(e.to_unit == profile.unit for e in case_events)
             or any(f.unit_at_creation == profile.unit for f in case_forms))
    )
    supervisor_of_unit = bool(
        profile.role == Role.SUPERVISOR and profile.unit
        and (case.holder_unit == profile.unit
             or any(e.from_unit == profile.unit for e in case_events)
             or any(e.to_unit == profile.unit for e in case_events)
             or any(f.unit_at_creation == profile.unit for f in case_forms))
    )
    supply_side_expert = bool(
        profile.unit == Unit.SUPPLY
        and (case.supply_internal_assignee_id == work_id
             or case.supply_external_assignee_id == work_id
             or case.supply_assignee_id == work_id)
    )
    return bool(
        profile.unit == Unit.COMMERCIAL or participated
        or manager_of_unit or supervisor_of_unit or supply_side_expert
    )


def is_case_commercial_owner(case: Case, request) -> bool:
    """Does the ACTIVE seat working this request literally OWN ``case`` — the
    narrow test the owner asked for before a report or a reminder may be
    filed on a case FROM THE CASE PAGE (``cases/views.py::case_report_add`` /
    ``case_reminder_add``): "دست ان نقش و شخص باشد و مالک ان پرونده ان باشد"
    — held in that role's/that person's hand, AND they are that case's owner.

    THIS IS DELIBERATELY NOT ``user_can_view_case``. That function is wide by
    design — any Commercial unit member may open ANY case
    (``profile.unit == Unit.COMMERCIAL`` alone is enough, see its own
    docstring) — and the owner was explicit that the two questions have
    different answers on this new surface: VIEWING a case's reports/reminders
    follows that same wide "has access to the case" rule
    ("برای پرونده هر شخصی که به ان پرونده با توجه به نقشش دسترسی دارد
    میتواند این گزارش‌ها را ببیند"), while CREATING one is the narrow
    ownership test this function answers alone. ``case_report_add`` /
    ``case_reminder_add`` therefore call this function, not
    ``user_can_view_case``, before writing.

    THE FACT TESTED IS REUSED, NOT REINVENTED, from the two places this
    platform already asks exactly this question:

    * ``allowed_actions``'s own Commercial branch gates EVERY write action on
      ``is_creator = case.created_by_id == work_id`` — a manager or a peer
      expert who did not create the case keeps view/export only, never edit;
    * ``archive_scope``'s own Commercial branches filter by the identical
      fact — unconditionally for an ordinary expert
      (``qs.filter(created_by=seat_user)``), and for a manager's own
      "mine_only" toggle.

    It is also, in substance, the fact ``marketing/access.py::
    case_access_for`` / ``_own_commercial_seat_users`` is built around on the
    OTHER side of the platform — that module's own docstring states it
    plainly: "a case's created_by is the COMMERCIAL seat's User". That module
    reasons across a person's WHOLE seat list because it is asked from a
    MARKETING seat looking at cases across every Commercial seat the viewer
    might separately hold; here we are asked from the CASE's own page, where
    the ACTIVE seat is by construction the one seat that matters (the viewer
    already had to be sitting in a Commercial seat, or switch into one, to
    open this case's action buttons at all — see ``allowed_actions`` above),
    so the narrower, already-active-seat test ``archive_scope`` /
    ``allowed_actions`` already use is the precise match, not a duplicate of
    the marketing one.

    False for anyone not currently working a COMMERCIAL seat — an admin, a
    General Manager, Technical, Supply, or a Marketing seat that has not
    switched into a Commercial one. A Commercial MANAGER answers True only
    for a case THEY personally created, exactly as ``allowed_actions``
    already treats a manager who did not create the case (view/export only) —
    never for a case merely visible to them through the archive's "entire
    archive" manager privilege, which is a VIEW grant, not an ownership fact.
    """
    ctx = _request_work_context(request)
    role = ctx.role
    profile = getattr(request.user, "profile", None)
    if role is not None:
        unit = (role.unit or "").strip() or (getattr(profile, "unit", "") or "")
    else:
        unit = getattr(profile, "unit", "") or ""
    if unit != Unit.COMMERCIAL:
        return False
    seat_user = ctx.seat_user
    work_id = getattr(seat_user, "id", None) or getattr(seat_user, "pk", None)
    return work_id is not None and case.created_by_id == work_id


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_to_technical(case: Case, actor, comment: str = "", side: str = ""):
    if is_currency_conversion_only(case, side or ""):
        raise PermissionError(
            "This version was opened for unit conversion only — "
            "Submit to Technical is not available."
        )
    comment = _with_auto_notes(
        comment,
        *_commercial_handoff_auto_notes(case, side or ""),
    )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        # Publish current forms (Inquiry, …) to Technical so only they can see
        # them until a later handoff reaches Supply/Commercial.
        #
        # ``side=`` is what keeps this to the stream actually being handed over.
        # Without it the helper falls back to every side of the case and stamps
        # the *other* side's current Inquiry as sent — which locks that untouched
        # draft out of editing for good (a sent inquiry may only be revised by
        # branching a new version, and a side still in DRAFT does not qualify for
        # one) and hands Technical a stream nobody gave them.
        _publish_current_forms_to(case, Unit.TECHNICAL, side=side,
                                  leaving_unit=Unit.COMMERCIAL)
        _mark_inquiry_comments_announced(case, side)
        case.set_side_state(side, CaseStatus.WITH_TECHNICAL, Unit.TECHNICAL)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.SUBMIT_TO_TECHNICAL, comment=comment,
            from_unit=Unit.COMMERCIAL, to_unit=Unit.TECHNICAL, side=side)
        _resolve_split_if_unified(case)
        return
    case.status = CaseStatus.WITH_TECHNICAL
    case.holder_unit = Unit.TECHNICAL
    case.assigned_to = case.technical_assignee
    case.awaiting_approval = False
    case.proposed_action = ""
    _sync_sides_to_case(case)
    case.save()
    _publish_current_forms_to(case, Unit.TECHNICAL, leaving_unit=Unit.COMMERCIAL)
    _mark_inquiry_comments_announced(case, side or "")
    log(case, actor, EventAction.SUBMIT_TO_TECHNICAL, comment=comment,
        from_unit=Unit.COMMERCIAL, to_unit=Unit.TECHNICAL)


@transaction.atomic
def _mark_side_form_sent(case: Case, from_unit: str, side: str):
    """Mark the current form for one side of the leaving unit as sent."""
    kind = FormKind.TO if from_unit == Unit.TECHNICAL else (
        FormKind.PI if from_unit == Unit.SUPPLY else None)
    if not kind:
        return
    _mark_form_leaving(case.current_form(kind, side))


def _activate_split_if_needed(case: Case):
    """Turn on independent per-side tracking the first time a supply side is
    delegated on an Internal & External case. Both sides start where the whole
    case currently is (with Supply)."""
    if case.split_active or not (case.has_internal and case.has_external):
        return
    case.split_active = True
    case.internal_status = case.status
    case.external_status = case.status
    case.internal_holder = case.holder_unit
    case.external_holder = case.holder_unit


def _sync_sides_to_case(case: Case):
    """Mirror the whole-case status/holder onto both sides.

    Whole-case transitions only run on a split case when its two sides are
    already together, so this keeps the per-side fields consistent (preventing
    stale per-side holders) without affecting independent movement."""
    if not (case.split_active and case.has_internal and case.has_external):
        return
    case.internal_status = case.status
    case.external_status = case.status
    case.internal_holder = case.holder_unit
    case.external_holder = case.holder_unit


def _resolve_split_if_unified(case: Case):
    """Split (Internal & External) cases run per-side from creation to finish;
    each side reaches its own terminal state independently. There is no
    whole-case re-merge — the case is only finalised once *every* side is
    terminal."""
    if not case.is_split:
        return
    _finalize_split_if_all_terminal(case)


def _finalize_split_if_all_terminal(case: Case):
    """When every side has reached a terminal state, finalise the whole case.

    Terminal sides are now FINAL_CLOSED / BURNED / CANCELLED / UNSUPPLIABLE_CLOSED.
    CLOSED and FINAL_APPROVED are *not* terminal (the case is still open), so a
    case with any side merely closed or final-approved is not rolled up yet.
    """
    if not (case.split_active and case.has_internal and case.has_external):
        return
    statuses = [case.side_status(sc) for sc in case.sides]
    if not all(s in CaseStatus.TERMINAL for s in statuses):
        return
    # Pick the whole-case outcome from the per-side terminal states.
    if all(s == CaseStatus.FINAL_CLOSED for s in statuses):
        case.status = CaseStatus.FINAL_CLOSED
    elif CaseStatus.FINAL_CLOSED in statuses:
        # Some side completed normally; treat the case as final-closed.
        case.status = CaseStatus.FINAL_CLOSED
    elif CaseStatus.BURNED in statuses:
        case.status = CaseStatus.BURNED
    elif CaseStatus.UNSUPPLIABLE_CLOSED in statuses:
        case.status = CaseStatus.UNSUPPLIABLE_CLOSED
    else:
        case.status = CaseStatus.CANCELLED
    case.holder_unit = Unit.COMMERCIAL
    case.save(update_fields=["status", "holder_unit", "updated_at"])


def close_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial sends one side to the client (terminal for that side)."""
    # Hard guard (defence in depth): this side's required forms must all exist at
    # its current inquiry version before it can go to the client (mirrors the
    # whole-case guard in close_case).
    if not _can_send_to_client(case, side):
        raise ValueError(
            "Cannot send this side to client: its TO and PI must be built at the "
            "current inquiry version first (a flagged Technical-Problem offer "
            "needs no PI)."
        )
    case.set_side_state(side, CaseStatus.CLOSED, Unit.COMMERCIAL)
    case.save(update_fields=["internal_status", "external_status",
                             "internal_holder", "external_holder", "updated_at"])
    log(case, actor, EventAction.CLOSE, comment=comment, from_unit=Unit.COMMERCIAL, side=side)
    _finalize_split_if_all_terminal(case)


def _other_side_awaiting_cancel(case: Case, side: str) -> str:
    """The *other* side's code when it is already waiting on cancel/burn approval.

    A pending request lives in the single case-level ``proposed_action`` string
    (``cs:I:PRIOR`` / ``bs:E:PRIOR`` / ``csu:I``), so the case can only carry one
    at a time. Whatever a second request does to the other side's record — an
    expert's request overwrites it, a manager's immediate cancel clears it — the
    first side is left at PENDING_CANCEL with nothing for the manager to resolve,
    and the approve button that stays on offer then falls through to the
    whole-case branch of ``approve_cancel`` and cancels the entire file. Callers
    refuse the second request instead of letting it overwrite the first.
    """
    if not case.is_split:
        return ""
    for sc in case.sides:
        if sc != side and case.side_status(sc) == CaseStatus.PENDING_CANCEL:
            return sc
    return ""


def cancel_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial cancels one side.

    Experts must get manager approval (side → PENDING_CANCEL). Managers cancel
    immediately. If that side had been marked *cannot supply*, the terminal
    outcome stays UNSUPPLIABLE_CLOSED rather than CANCELLED.
    """
    blocked_by = _other_side_awaiting_cancel(case, side)
    if blocked_by:
        raise PermissionError(
            f"The {Side.LABELS.get(blocked_by, blocked_by)} side already has a "
            "cancel/burn request waiting for the commercial manager — it has to "
            "be approved or rejected before another side can be cancelled."
        )
    prior = case.side_status(side)
    resolving_unsuppliable = prior == CaseStatus.UNSUPPLIABLE
    if _commercial_needs_cancel_approval(actor):
        case.set_side_state(side, CaseStatus.PENDING_CANCEL, Unit.COMMERCIAL)
        if resolving_unsuppliable:
            case.proposed_action = f"csu:{_side_token(side)}"[:30]
        else:
            case.proposed_action = f"cs:{_side_token(side)}:{prior}"[:30]
        case.awaiting_approval = True
        case.save(update_fields=[
            "internal_status", "external_status",
            "internal_holder", "external_holder",
            "proposed_action", "awaiting_approval", "updated_at",
        ])
        log(case, actor, EventAction.REQUEST_CANCEL, comment=comment,
            from_unit=Unit.COMMERCIAL, side=side)
        return
    final_status = (CaseStatus.UNSUPPLIABLE_CLOSED if resolving_unsuppliable
                    else CaseStatus.CANCELLED)
    case.set_side_state(side, final_status, Unit.COMMERCIAL)
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=["internal_status", "external_status",
                             "internal_holder", "external_holder",
                             "awaiting_approval", "proposed_action", "updated_at"])
    log(case, actor, EventAction.CANCEL, comment=comment, from_unit=Unit.COMMERCIAL, side=side)
    _finalize_split_if_all_terminal(case)


def finalize_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial marks one already-sent (closed) side as finally approved.

    On an Internal & External (split) case, Final-Approving one side automatically
    cancels the other side wherever it currently sits — recorded in the timeline
    with an explanatory comment — so no further actions can be taken on it.
    """
    case.set_side_state(side, CaseStatus.FINAL_APPROVED, Unit.COMMERCIAL)
    case.save(update_fields=["internal_status", "external_status",
                             "internal_holder", "external_holder", "updated_at"])
    # Commercial is issuing this side's proforma: freeze who signed it now,
    # rather than resolving "the current Commercial manager" at print time.
    _freeze_commercial_documents(case, side)
    log(case, actor, EventAction.FINALIZE, comment=comment, from_unit=Unit.COMMERCIAL, side=side)

    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        other = Side.EXTERNAL if side == Side.INTERNAL else Side.INTERNAL
        other_st = case.side_status(other)
        if other_st not in CaseStatus.TERMINAL and other_st != CaseStatus.FINAL_APPROVED:
            side_label = Side.LABELS.get(side, side)
            other_label = Side.LABELS.get(other, other)
            # Keep this auto-comment short and do not append the Final Approve note.
            auto = f"Auto-cancelled: {other_label} closed after {side_label} Final Approved."
            case.set_side_state(other, CaseStatus.CANCELLED, Unit.COMMERCIAL)
            case.save(update_fields=["internal_status", "external_status",
                                     "internal_holder", "external_holder", "updated_at"])
            log(case, actor, EventAction.CANCEL, comment=auto,
                from_unit=Unit.COMMERCIAL, side=other)

    _finalize_split_if_all_terminal(case)


def convert_pi_currency(case: Case, actor, *, form_id: int, from_unit: str,
                        to_unit: str, rate: float = None, side: str = ""):
    """Commercial applies a currency conversion on one saved PI version.

    Updates UNIT PRICE / TOTAL PRICE in place, stores ``meta.currency`` and
    ``meta.currency_converted=True`` so the conversion UI stays closed until a
    new PI version is built. Exports then use the converted amounts.

    The conversion rate is taken from the Commercial manager's FX board
    (currency → Rial). Manual rates are no longer accepted. Conversion is
    refused when the board is stale (>24h) or incomplete.
    """
    from .export_data import (
        currency_label, format_pi_money, normalize_currency, parse_money,
    )
    from . import fx_rates as fx

    profile = getattr(actor, "profile", None)
    if not profile or profile.unit != Unit.COMMERCIAL:
        raise PermissionError("Only Commercial can convert Proforma currency.")
    if case.created_by_id != actor.id:
        raise PermissionError("Only the case creator can convert Proforma currency.")

    form = CaseForm.objects.filter(pk=form_id, case=case, kind=FormKind.PI).first()
    if form is None:
        raise ValueError("Proforma form not found.")
    if side and form.side and form.side != side:
        raise ValueError("Proforma side mismatch.")
    if form.side and not can_act_on_side(case, actor, form.side):
        raise PermissionError("You cannot act on this side.")
    if case.is_split and form.side:
        st = case.side_status(form.side)
        if st in CaseStatus.TERMINAL:
            raise PermissionError("This side is closed.")
    elif case.status in CaseStatus.TERMINAL:
        raise PermissionError("This case is closed.")

    meta = dict(form.meta or {})
    if meta.get("currency_converted"):
        raise ValueError("Currency already converted on this Proforma version.")

    external = form.side == Side.EXTERNAL or (
        not form.side and case.price_type == PriceType.EXTERNAL
    )
    src = normalize_currency(from_unit, external=external)
    dst = normalize_currency(to_unit, external=external)
    if external and (src == "rial" or dst == "rial"):
        raise ValueError("Rial is not available for External Proformas.")
    if src == dst:
        raise ValueError("Choose a different target currency.")

    if fx.is_rates_stale():
        raise ValueError(
            "FX rates are outdated (more than 24 hours) or incomplete. "
            "Ask the Commercial manager to update Clients & FX → FX Rates."
        )
    try:
        rate = fx.conversion_rate(src, dst)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not rate or rate <= 0:
        raise ValueError("Could not resolve a valid conversion rate from the FX board.")

    # Same convention as the PI tool: rate = how many FROM units equal one TO.
    factor = 1.0 / rate
    label = currency_label(dst, external=external)
    new_table = []
    for row in (form.table or []):
        r = dict(row or {})
        # Convert display finals and editable bases independently so margins
        # baked into UNIT PRICE / SERVICE PRICE on Save PI are preserved.
        up_final = parse_money(r.get("UNIT PRICE"))
        raw = r.get("_unit_price_raw")
        up_base = parse_money(raw) if raw not in (None, "") else up_final
        tp = parse_money(r.get("TOTAL PRICE"))
        if up_final or str(r.get("UNIT PRICE") or "").strip():
            r["UNIT PRICE"] = format_pi_money(up_final * factor, dst, external=external)
        if tp or str(r.get("TOTAL PRICE") or "").strip():
            r["TOTAL PRICE"] = format_pi_money(tp * factor, dst, external=external)
        if raw not in (None, "") or up_base:
            r["_unit_price_raw"] = f"{(up_base * factor):.6f}".rstrip("0").rstrip(".")

        svc_final = parse_money(r.get("SERVICE PRICE"))
        svc_raw = r.get("_service_price_raw")
        svc_base = parse_money(svc_raw) if svc_raw not in (None, "") else svc_final
        if svc_final or str(r.get("SERVICE PRICE") or "").strip():
            r["SERVICE PRICE"] = format_pi_money(svc_final * factor, dst, external=external)
        if svc_raw not in (None, "") or (svc_base and str(r.get("_service_comment") or "").strip()):
            r["_service_price_raw"] = f"{(svc_base * factor):.6f}".rstrip("0").rstrip(".")
        new_table.append(r)

    meta["currency"] = dst
    meta["currency_converted"] = True
    meta["currency_label"] = label
    # Persist the rate + source unit so the Proforma header can show the applied
    # conversion ("Rate: 1,700,000 Rial") for this version, and exports keep it.
    meta["currency_rate"] = rate
    meta["currency_from"] = src
    # The itemcoder PI tool (opened later by Supply's own "View" link into this
    # SAME version) reads its currency label from meta["calc"]["to"], written
    # only when someone saves inside that tool — never by this endpoint. Left
    # alone, a version converted here keeps showing the tool's last-saved
    # currency (often the original "rial") even though the prices above were
    # just rewritten in ``dst`` — correct numbers, stale label, for whichever
    # unit opens the tool next. Keep the two in lockstep here so every viewer
    # of this version, tool or card, agrees on what currency it is in.
    calc = dict(meta.get("calc") or {})
    calc["from"] = src
    calc["to"] = dst
    calc["currency"] = dst
    calc["rate"] = rate
    meta["calc"] = calc
    form.table = new_table
    form.meta = meta
    form.save(update_fields=["table", "meta", "updated_at"])

    log(case, actor, EventAction.EDIT, comment=(
        f"Currency converted on Proforma v{form.version:02d}: "
        f"{src.upper()} → {dst.upper()} (rate {rate:g}, from FX board)."
    ), from_unit=Unit.COMMERCIAL, form_kind=FormKind.PI,
        form_version=form.version, side=form.side or side or "")
    log_currency_conversion(
        case, actor,
        from_code=src, to_code=dst, rate=rate,
        side=form.side or side or "",
        form_kind=FormKind.PI, form_version=form.version,
        source="commercial",
    )
    return form


def log_currency_conversion(case: Case, actor, *, from_code: str, to_code: str,
                            rate=None, side: str = "", form_kind: str = "",
                            form_version=None, source: str = "",
                            reset: bool = False):
    """Record a Proforma currency conversion for the admin/GM audit tab."""
    from .models import CaseCurrencyLog

    src = (from_code or "").strip().lower()
    dst = (to_code or "").strip().lower()
    if not src or not dst or src == dst:
        return None
    rate_text = ""
    if rate not in (None, ""):
        try:
            r = float(rate)
            if abs(r - round(r)) < 1e-9:
                rate_text = f"{int(round(r)):,}"
            else:
                rate_text = f"{r:g}"
        except (TypeError, ValueError):
            rate_text = str(rate)
    if reset:
        label = f"Restored default {dst.upper()} (was {src.upper()})"
    else:
        label = f"{src.upper()} → {dst.upper()}"
        if rate_text:
            label += f" · rate {rate_text}"
    return CaseCurrencyLog.objects.create(
        case=case,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        from_code=src,
        to_code=dst,
        rate=rate_text,
        side=side or "",
        form_kind=form_kind or FormKind.PI,
        form_version=form_version,
        source=source or "",
        label=label,
    )


def final_close_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial shuts one final-approved side for good (terminal)."""
    case.set_side_state(side, CaseStatus.FINAL_CLOSED, Unit.COMMERCIAL)
    case.save(update_fields=["internal_status", "external_status",
                             "internal_holder", "external_holder", "updated_at"])
    log(case, actor, EventAction.FINAL_CLOSE, comment=comment, from_unit=Unit.COMMERCIAL, side=side)
    _finalize_split_if_all_terminal(case)


def burn_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial burns one side (deal fell through).

    Experts need manager approval (side → PENDING_CANCEL); managers burn
    immediately.
    """
    # Same single-slot limitation as cancel_side: one pending request per case.
    blocked_by = _other_side_awaiting_cancel(case, side)
    if blocked_by:
        raise PermissionError(
            f"The {Side.LABELS.get(blocked_by, blocked_by)} side already has a "
            "cancel/burn request waiting for the commercial manager — it has to "
            "be approved or rejected before another side can be burned."
        )
    prior = case.side_status(side)
    if _commercial_needs_cancel_approval(actor):
        case.set_side_state(side, CaseStatus.PENDING_CANCEL, Unit.COMMERCIAL)
        case.proposed_action = f"bs:{_side_token(side)}:{prior}"[:30]
        case.awaiting_approval = True
        case.save(update_fields=[
            "internal_status", "external_status",
            "internal_holder", "external_holder",
            "proposed_action", "awaiting_approval", "updated_at",
        ])
        log(case, actor, EventAction.REQUEST_CANCEL, comment=comment,
            from_unit=Unit.COMMERCIAL, side=side)
        return
    case.set_side_state(side, CaseStatus.BURNED, Unit.COMMERCIAL)
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=["internal_status", "external_status",
                             "internal_holder", "external_holder",
                             "awaiting_approval", "proposed_action", "updated_at"])
    log(case, actor, EventAction.BURN, comment=comment, from_unit=Unit.COMMERCIAL, side=side)
    _finalize_split_if_all_terminal(case)


def case_is_fresh_draft(case: Case) -> bool:
    """True while the whole case is still an unsubmitted draft.

    This is what decides whether "Edit information" opens the FULL case screen
    (client, order no., kind, offer type, price type, deadline, items) or only
    the contacts box: a case may be edited wholesale right up until its inquiry
    first leaves Commercial, and never again.

    It lives here because it is asked in two places — the case page, to choose
    which link to render, and ``edit_items``, to decide whether to serve that
    screen at all — and those two had drifted apart. The page's copy was missing
    the ``sent`` term, so an Internal & External conversion (whose new side is
    created DRAFT, carrying an inquiry that is already ``sent=True``) made the
    page offer the full editor while the screen itself refused it: a control that
    renders and is then bounced, with the working contacts link gone from the
    page entirely. One definition, asked twice, cannot do that.

    ``sent`` is the load-bearing term, not the status — a converted side is DRAFT
    yet has a sent inquiry, which is exactly the case that separated the two.
    """
    cur = case.current_form(FormKind.INQUIRY)
    if case.status != CaseStatus.DRAFT:
        return False
    if cur is not None and cur.version > 1:
        # Versions are 1-based, so the first inquiry is v01; past it the case has
        # a history and is no longer "fresh".
        return False
    if case.forms.filter(kind=FormKind.INQUIRY, sent=True).exists():
        return False
    if case.split_active and case.sides:
        return all(case.side_status(sc) == CaseStatus.DRAFT for sc in case.sides)
    return True


def _is_fresh_converted_side(case: Case, side: str) -> bool:
    """True for the brand-new side created by an Internal & External two-stage
    conversion that has not been worked yet.

    Such a side carries a full inquiry (authored in the items editor as the
    conversion was confirmed, or copied from the prior side) marked sent, but no
    TO/PI of its own; it sits with Commercial at DRAFT — the status a
    just-created side is born in — and must be revised through "New version"
    only. RETURNED_TO_COMMERCIAL is accepted alongside it because that is what
    sides converted before this read, and they must keep working. Once it builds
    its own TO it behaves like any other side (New version only after CLOSED).
    """
    if not (case.is_split and case.price_upgraded_two_stage):
        return False
    if side not in (Side.INTERNAL, Side.EXTERNAL):
        return False
    if case.side_holder(side) != Unit.COMMERCIAL:
        return False
    if case.side_status(side) not in (CaseStatus.RETURNED_TO_COMMERCIAL, CaseStatus.DRAFT):
        return False
    # No TO built for this side yet -> it is still the freshly-seeded side.
    return not case.forms.filter(kind=FormKind.TO, side=side).exists()


def can_new_inquiry_version(case: Case, user, side: str = "", *,
                            role=None, work_user=None) -> bool:
    """Unified gate for the "New version" button / inquiry-editor newver mode.

    Commercial only. A new inquiry version may be started when:
      • (non-split) the case is CLOSED — already sent to the client; or
      • (split) the side is CLOSED; or
      • (split) the side is the freshly-converted two-stage side (it carries a
        copied inquiry it can revise but has not been worked yet).
    """
    from types import SimpleNamespace

    work = work_user or user
    work_id = getattr(work, "id", None) or getattr(work, "pk", None)
    profile = getattr(user, "profile", None)
    if role is not None:
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None or profile.unit != Unit.COMMERCIAL:
        return False
    if case.created_by_id != work_id:
        return False
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if not can_act_on_side(case, user, side, role=role, work_user=work):
            return False
        if case.side_status(side) == CaseStatus.CLOSED:
            return True
        return _is_fresh_converted_side(case, side)
    # Non-split.
    return case.status == CaseStatus.CLOSED


def can_act_on_side(case: Case, user, side: str, *, role=None, work_user=None) -> bool:
    """True when this user may take actions on a given side of a split case.

    Ownership depends on where the side currently sits: its supply expert,
    its technical expert, or — in Commercial — the case creator / a commercial
    manager. An un-delegated side at a unit is handled by that unit's manager.
    """
    from types import SimpleNamespace

    if not case.is_split:
        return True
    work = work_user or user
    work_id = getattr(work, "id", None) or getattr(work, "pk", None)
    profile = getattr(user, "profile", None)
    if role is not None:
        profile = SimpleNamespace(
            is_admin=bool(role.is_admin),
            is_general_manager=bool(role.is_general_manager),
            unit=role.unit or "",
            role=role.role or "",
            supply_kind=role.supply_kind or "",
        )
    if profile is None:
        return False
    holder = case.side_holder(side)
    if holder == Unit.SUPPLY:
        assignee = (case.supply_internal_assignee_id if side == Side.INTERNAL
                    else case.supply_external_assignee_id if side == Side.EXTERNAL else None)
        if assignee:
            return assignee == work_id
        return profile.unit == Unit.SUPPLY and profile.role == Role.MANAGER
    if holder == Unit.TECHNICAL:
        # Technical uses ONE assignee for both sides of a split case.
        if case.technical_assignee_id:
            return case.technical_assignee_id == work_id
        return profile.unit == Unit.TECHNICAL and profile.role == Role.MANAGER
    if holder == Unit.COMMERCIAL:
        # Creator acts on their case; commercial manager may act when a side is
        # waiting for cancel/burn approval.
        if profile.unit != Unit.COMMERCIAL:
            return False
        if case.created_by_id == work_id:
            return True
        return (profile.role == Role.MANAGER
                and case.side_status(side) == CaseStatus.PENDING_CANCEL)
    return False


def assign(case: Case, actor, assignee, comment: str = "", side: str = ""):
    # Technical assigns ONE expert to BOTH sides of a split case (no per-side).
    if case.is_split and not side and (
            case.side_holder(Side.INTERNAL) == Unit.TECHNICAL
            or case.side_holder(Side.EXTERNAL) == Unit.TECHNICAL):
        case.technical_assignee = assignee
        case.assigned_to = assignee
        case.save(update_fields=["technical_assignee", "assigned_to", "updated_at"])
        note = f"Assigned to {assignee.get_full_name() or assignee.username}"
        if comment:
            note += f" — {comment}"
        log(case, actor, EventAction.ASSIGN, to_unit=Unit.TECHNICAL, comment=note)
        return

    # For split cases the target depends on where that side currently sits.
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        holder = case.side_holder(side)
        if holder == Unit.TECHNICAL:
            if side == Side.INTERNAL:
                case.technical_internal_assignee = assignee
            else:
                case.technical_external_assignee = assignee
            fields = ["technical_internal_assignee", "technical_external_assignee", "updated_at"]
        else:  # Supply
            if side == Side.INTERNAL:
                case.supply_internal_assignee = assignee
            else:
                case.supply_external_assignee = assignee
            fields = ["supply_internal_assignee", "supply_external_assignee", "updated_at"]
        case.save(update_fields=fields)
        note = f"Assigned to {assignee.get_full_name() or assignee.username} ({Side.LABELS.get(side, side)})"
        if comment:
            note += f" — {comment}"
        log(case, actor, EventAction.ASSIGN, to_unit=holder, comment=note, side=side)
        return

    # Remember the choice so the case always returns to the same expert.
    if case.holder_unit == Unit.TECHNICAL:
        case.technical_assignee = assignee
        case.assigned_to = assignee
        fields = ["assigned_to", "technical_assignee", "updated_at"]
    elif case.holder_unit == Unit.SUPPLY:
        # Supply assigns each side independently to its expert pool.
        if side == Side.INTERNAL:
            case.supply_internal_assignee = assignee
        elif side == Side.EXTERNAL:
            case.supply_external_assignee = assignee
        else:
            case.supply_assignee = assignee
        if side in (Side.INTERNAL, Side.EXTERNAL):
            _activate_split_if_needed(case)
        # Mirror the Technical branch above: keep the generic ``assigned_to``
        # in step with the specific assignee too. archive_scope's fallback
        # bucket (a plain expert with no other match) reads assigned_to as
        # one of its safety-net clauses; leaving it stale here — pointing at
        # whoever send_to_supply() last put there, often nobody — meant a
        # Supply expert had one fewer of the redundant paths back to their
        # own case that Technical's assignees already had.
        case.assigned_to = assignee
        fields = ["assigned_to", "supply_internal_assignee", "supply_external_assignee",
                  "supply_assignee", "split_active",
                  "internal_status", "external_status",
                  "internal_holder", "external_holder", "updated_at"]
    elif case.holder_unit == Unit.WAREHOUSE:
        # A normal holder_unit-driven handoff (unlike Purchasing, which needed
        # its own separate assign_purchasing() — see that function's own
        # docstring for why: Purchasing's access happens WITHOUT holder_unit
        # ever becoming Unit.PURCHASING, so it can never reach this dispatch
        # at all). Warehouse's own downstream behaviour past this point is
        # still undesigned (deferred), but "the manager can hand an incoming
        # case to one of their experts" is the same base capability every
        # other unit already has, and is safe to give it now.
        case.warehouse_assignee = assignee
        case.assigned_to = assignee
        fields = ["assigned_to", "warehouse_assignee", "updated_at"]
    else:
        case.assigned_to = assignee
        fields = ["assigned_to", "updated_at"]
    case.save(update_fields=fields)
    note = f"Assigned to {assignee.get_full_name() or assignee.username}"
    if side:
        note += f" ({Side.LABELS.get(side, side)})"
    if comment:
        note += f" — {comment}"
    log(case, actor, EventAction.ASSIGN, to_unit=case.holder_unit, comment=note, side=side)


@transaction.atomic
def return_to_commercial(case: Case, actor, comment: str = "", side: str = ""):
    # Auto timeline note: which TO rows are flagged Technical Problem.
    comment = _with_auto_notes(
        comment,
        _handoff_auto_notes(case, FormKind.TO, side, include_issue=True),
    )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        _publish_current_forms_to(
            case, Unit.COMMERCIAL, side=side, leaving_unit=Unit.TECHNICAL)
        case.set_side_state(side, CaseStatus.RETURNED_TO_COMMERCIAL, Unit.COMMERCIAL)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.RETURN_TO_COMMERCIAL, comment=comment,
            from_unit=Unit.TECHNICAL, to_unit=Unit.COMMERCIAL, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(
        case, Unit.COMMERCIAL, leaving_unit=_unit_of(actor))
    case.status = CaseStatus.RETURNED_TO_COMMERCIAL
    case.holder_unit = Unit.COMMERCIAL
    case.assigned_to = case.created_by
    case.awaiting_approval = False
    case.proposed_action = ""
    _sync_sides_to_case(case)
    case.save()
    log(case, actor, EventAction.RETURN_TO_COMMERCIAL, comment=comment,
        from_unit=_unit_of(actor), to_unit=Unit.COMMERCIAL)


@transaction.atomic
def send_to_supply(case: Case, actor, comment: str = "", side: str = ""):
    # Hard guard: a TO with any Technical Problem flag cannot be forwarded to
    # Supply. This protects against any path that bypasses allowed_actions.
    if _to_has_technical_problems(case, side):
        raise ValueError(
            "This Technical Offer has rows flagged as Technical Problem. "
            "Clear every flag (re-edit the TO) before submitting to Supply.")
    # Every active row must have BRAND filled before it can go to Supply.
    _no_brand = _to_rows_without_brand(case, side)
    if _no_brand:
        _rows_txt = ", ".join(f"#{n}" for n in _no_brand[:25])
        raise ValueError(
            "Every item needs a BRAND before submitting to Supply. "
            f"Rows still without a brand: {_rows_txt}. Fill BRAND "
            "(or delete/deactivate the row) first.")
    # Every active row must have an FTCO code before it can go to Supply
    # (unless REQUIRE_FTCO_CODE_TO_SUPPLY is False in settings).
    from django.conf import settings as _dj_settings
    if bool(getattr(_dj_settings, "REQUIRE_FTCO_CODE_TO_SUPPLY", False)):
        _uncoded = _to_rows_without_code(case, side)
        if _uncoded:
            _rows_txt = ", ".join(f"#{n}" for n in _uncoded[:25])
            raise ValueError(
                "Every item needs an FTCO code before submitting to Supply. "
                f"Rows still without a code: {_rows_txt}. Code them "
                "(or delete/deactivate the row) first.")
    # Remark/brand changes vs last handoff (quiet on first TO→Supply).
    comment = _with_auto_notes(
        comment,
        _handoff_auto_notes(
            case, FormKind.TO, side, include_rb=True, rb_only_if_prior=True),
    )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if case.side_holder(side) != Unit.TECHNICAL:
            raise ValueError("This side is not currently with Technical.")
        _publish_current_forms_to(
            case, Unit.SUPPLY, side=side, leaving_unit=Unit.TECHNICAL)
        case.set_side_state(side, CaseStatus.WITH_SUPPLY, Unit.SUPPLY)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.SEND_TO_SUPPLY, comment=comment,
            from_unit=Unit.TECHNICAL, to_unit=Unit.SUPPLY, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(case, Unit.SUPPLY, leaving_unit=Unit.TECHNICAL)
    case.status = CaseStatus.WITH_SUPPLY
    case.holder_unit = Unit.SUPPLY
    case.assigned_to = case.supply_assignee
    case.awaiting_approval = False
    case.proposed_action = ""
    # Internal & External cases are tracked per-side from the moment they reach
    # supply, so each side can be acted on independently (the supply manager has
    # one set of actions per side until a side is delegated to an expert).
    if case.has_internal and case.has_external:
        _activate_split_if_needed(case)
        _sync_sides_to_case(case)
    case.save()
    log(case, actor, EventAction.SEND_TO_SUPPLY, comment=comment,
        from_unit=Unit.TECHNICAL, to_unit=Unit.SUPPLY)


@transaction.atomic
def return_to_technical(case: Case, actor, comment: str = "", side: str = ""):
    # Auto notes: PI remark/brand changes + not-suppliable flags.
    comment = _with_auto_notes(
        comment,
        _handoff_auto_notes(
            case, FormKind.PI, side,
            include_unsuppliable=True, include_rb=True),
    )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if not can_act_on_side(case, actor, side):
            raise ValueError("You can only act on your own side of this case.")
        _publish_current_forms_to(
            case, Unit.TECHNICAL, side=side, leaving_unit=Unit.SUPPLY)
        case.set_side_state(side, CaseStatus.RETURNED_TO_TECHNICAL, Unit.TECHNICAL)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.RETURN_TO_TECHNICAL, comment=comment,
            from_unit=Unit.SUPPLY, to_unit=Unit.TECHNICAL, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(case, Unit.TECHNICAL, leaving_unit=Unit.SUPPLY)
    case.status = CaseStatus.RETURNED_TO_TECHNICAL
    case.holder_unit = Unit.TECHNICAL
    case.assigned_to = case.technical_assignee
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save()
    log(case, actor, EventAction.RETURN_TO_TECHNICAL, comment=comment,
        from_unit=Unit.SUPPLY, to_unit=Unit.TECHNICAL)


@transaction.atomic
def send_to_commercial(case: Case, actor, comment: str = "", side: str = ""):
    # Hard guard: every active, suppliable row must carry a BRAND and a TIME
    # (plus a UNIT PRICE for TO & PI). FTCO code is required only when
    # REQUIRE_FTCO_CODE_TO_SUPPLY is True. NOT-SUPPLIABLE / deleted rows
    # are exempt. Supply completes the missing fields (or marks the row
    # not-suppliable) before it can go to Commercial.
    blockers = _pi_commercial_blockers(case, side)
    if blockers:
        _labels = {"code": "FTCO code", "brand": "BRAND", "time": "TIME", "price": "UNIT PRICE"}
        parts = []
        for field in ("code", "price", "brand", "time"):
            rows = blockers.get(field)
            if rows:
                shown = ", ".join(f"#{n}" for n in rows[:20])
                parts.append(f"{_labels[field]} → {shown}")
        raise ValueError(
            "Some items are not ready to submit to Commercial. Complete the "
            "missing fields (or mark the row not-suppliable): " + "; ".join(parts))
    # Leaving form: PI for Supply, TO when Technical forwards after return.
    leaving_unit = _unit_of(actor)
    leaving_kind = FormKind.TO if leaving_unit == Unit.TECHNICAL else FormKind.PI
    comment = _with_auto_notes(
        comment,
        _handoff_auto_notes(
            case, leaving_kind, side,
            include_unsuppliable=(leaving_kind == FormKind.PI),
            include_rb=True,
            rb_only_if_prior=(leaving_kind == FormKind.TO),
        ),
    )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if not can_act_on_side(case, actor, side):
            raise ValueError("You can only act on your own side of this case.")
        _publish_current_forms_to(
            case, Unit.COMMERCIAL, side=side, leaving_unit=leaving_unit)
        case.set_side_state(side, CaseStatus.WITH_COMMERCIAL, Unit.COMMERCIAL)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.SEND_TO_COMMERCIAL, comment=comment,
            from_unit=leaving_unit, to_unit=Unit.COMMERCIAL, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(case, Unit.COMMERCIAL, leaving_unit=leaving_unit)
    case.status = CaseStatus.WITH_COMMERCIAL
    case.holder_unit = Unit.COMMERCIAL
    case.assigned_to = case.created_by
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save()
    log(case, actor, EventAction.SEND_TO_COMMERCIAL, comment=comment,
        from_unit=leaving_unit, to_unit=Unit.COMMERCIAL)


@transaction.atomic
def propose_send(case: Case, actor, action_key: str, comment: str = ""):
    """An expert proposes an outward send; the unit manager must approve."""
    case.awaiting_approval = True
    case.proposed_action = action_key
    case.save(update_fields=["awaiting_approval", "proposed_action", "updated_at"])
    log(case, actor, EventAction.EDIT, comment=f"Requested manager approval: {action_key}. {comment}",
        from_unit=_unit_of(actor))


@transaction.atomic
def approve_send(case: Case, actor, comment: str = ""):
    """The unit manager approves and performs the previously proposed send."""
    action_key = case.proposed_action
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=["awaiting_approval", "proposed_action", "updated_at"])
    dispatch = {
        "send_to_supply": send_to_supply,
        "send_to_commercial": send_to_commercial,
        "return_to_commercial": return_to_commercial,
        "return_to_technical": return_to_technical,
    }
    func = dispatch.get(action_key)
    if func:
        func(case, actor, comment=comment)


@transaction.atomic
def close_case(case: Case, actor, comment: str = ""):
    # Hard guard (defence in depth): never send to the client unless EVERY
    # required form — both the TO and the PI — is built at the current inquiry
    # version. The UI already hides the button via allowed_actions, but a stale
    # button or a direct POST must not be able to close a case whose TO or PI is
    # missing. This holds for TO-only cases too: a Proforma is still required
    # before sending (it just carries no prices), so the case must have gone
    # through Supply first.
    if not _can_send_to_client(case):
        raise ValueError(
            "Cannot send to client: the TO and PI must be built at the current "
            "inquiry version first (a flagged Technical-Problem offer needs no PI)."
        )
    case.status = CaseStatus.CLOSED
    case.holder_unit = Unit.COMMERCIAL  # stays with commercial for Final Approved / Burned
    case.save(update_fields=["status", "holder_unit", "updated_at"])
    log(case, actor, EventAction.CLOSE, comment=comment, from_unit=_unit_of(actor))


@transaction.atomic
def return_to_supply(case: Case, actor, comment: str = "", side: str = ""):
    """Return the case (or one side) to Supply."""
    leaving_unit = _unit_of(actor)
    # When Technical returns a side to Supply after remark/brand work, note it.
    if leaving_unit == Unit.TECHNICAL:
        comment = _with_auto_notes(
            comment,
            _handoff_auto_notes(
                case, FormKind.TO, side, include_rb=True, rb_only_if_prior=True),
        )
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if not can_act_on_side(case, actor, side):
            raise ValueError("You can only act on your own side of this case.")
        _publish_current_forms_to(
            case, Unit.SUPPLY, side=side, leaving_unit=leaving_unit)
        case.set_side_state(side, CaseStatus.WITH_SUPPLY, Unit.SUPPLY)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.RETURN_TO_SUPPLY, comment=comment,
            from_unit=leaving_unit, to_unit=Unit.SUPPLY, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(case, Unit.SUPPLY, leaving_unit=leaving_unit)
    case.status = CaseStatus.WITH_SUPPLY
    case.holder_unit = Unit.SUPPLY
    case.assigned_to = case.supply_assignee
    case.save()
    log(case, actor, EventAction.RETURN_TO_SUPPLY, comment=comment,
        from_unit=leaving_unit, to_unit=Unit.SUPPLY)


@transaction.atomic
def mark_cannot_supply(case: Case, actor, comment: str = "", side: str = ""):
    """Supply marks the case (or one side) as not suppliable.

    No supply/commercial manager approval is required: it becomes "cannot
    supply" immediately and is handed to the originating commercial expert.
    """
    if case.is_split and side in (Side.INTERNAL, Side.EXTERNAL):
        if not can_act_on_side(case, actor, side):
            raise ValueError("You can only act on your own side of this case.")
        _publish_current_forms_to(
            case, Unit.COMMERCIAL, side=side, leaving_unit=Unit.SUPPLY)
        case.set_side_state(side, CaseStatus.UNSUPPLIABLE, Unit.COMMERCIAL)
        case.save(update_fields=["internal_status", "external_status",
                                 "internal_holder", "external_holder", "updated_at"])
        log(case, actor, EventAction.CANNOT_SUPPLY, comment=comment,
            from_unit=Unit.SUPPLY, to_unit=Unit.COMMERCIAL, side=side)
        _resolve_split_if_unified(case)
        return
    _publish_current_forms_to(case, Unit.COMMERCIAL, leaving_unit=Unit.SUPPLY)
    case.status = CaseStatus.UNSUPPLIABLE
    case.holder_unit = Unit.COMMERCIAL
    case.assigned_to = case.created_by
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save()
    log(case, actor, EventAction.CANNOT_SUPPLY, comment=comment,
        from_unit=Unit.SUPPLY, to_unit=Unit.COMMERCIAL)


# A two-manager approval chain for "cannot supply" used to live here
# (approve_unsuppliable / reject_unsuppliable, plus the _commercial_manager and
# _supply_manager lookups that routed it). It was dead: ``mark_cannot_supply``
# above settles the decision immediately as UNSUPPLIABLE, nothing ever set the
# two PENDING statuses that the approval functions keyed off, and neither
# ``allowed_actions`` nor ``can_do_side_action`` ever granted the approve/reject
# actions — so the transition view rejected those POSTs before reaching them.
# Removed together with the buttons on the case page. The UNSUPPLIABLE_PENDING_*
# constants stay in cases.constants for the benefit of any historical row.


def _commercial_needs_cancel_approval(actor) -> bool:
    """Experts (non-managers) need the commercial manager to approve cancel/burn."""
    profile = getattr(actor, "profile", None)
    return bool(
        profile
        and profile.unit == Unit.COMMERCIAL
        and profile.role != Role.MANAGER
    )


def _side_token(side: str) -> str:
    if side == Side.INTERNAL:
        return "I"
    if side == Side.EXTERNAL:
        return "E"
    return ""


def _side_from_token(token: str) -> str:
    if token == "I":
        return Side.INTERNAL
    if token == "E":
        return Side.EXTERNAL
    return ""


@transaction.atomic
def request_cancel(case: Case, actor, comment: str = ""):
    """Request cancellation of a case.

    Commercial experts put the case into PENDING_CANCEL for the commercial
    manager's inbox. Commercial managers cancel immediately. Cancelling a case
    that is currently "cannot supply" resolves to a terminal Cannot-supply
    state (its status stays "Cannot supply", it is not labelled "Cancelled").
    """
    prior = case.status
    resolving_unsuppliable = prior == CaseStatus.UNSUPPLIABLE
    if _commercial_needs_cancel_approval(actor):
        case.status = CaseStatus.PENDING_CANCEL
        case.holder_unit = Unit.COMMERCIAL
        case.awaiting_approval = True
        if resolving_unsuppliable:
            case.proposed_action = "cancel_unsuppliable"
        else:
            # Encode prior status so reject can restore it (fits CharField 30).
            case.proposed_action = f"cancel:{prior}"[:30]
        case.save(update_fields=[
            "status", "holder_unit", "awaiting_approval", "proposed_action", "updated_at",
        ])
        log(case, actor, EventAction.REQUEST_CANCEL, comment=comment,
            from_unit=Unit.COMMERCIAL)
        return

    final_status = (CaseStatus.UNSUPPLIABLE_CLOSED if resolving_unsuppliable
                    else CaseStatus.CANCELLED)
    case.status = final_status
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=[
        "status", "awaiting_approval", "proposed_action", "updated_at",
    ])
    log(case, actor, EventAction.APPROVE_CANCEL, comment=comment, from_unit=Unit.COMMERCIAL)


@transaction.atomic
def approve_cancel(case: Case, actor, comment: str = ""):
    """Commercial manager approves a pending cancel or burn request."""
    action = (case.proposed_action or "").strip()
    side = ""

    if action.startswith("bs:"):
        # Side burn: bs:I:PRIOR
        parts = action.split(":")
        side = _side_from_token(parts[1] if len(parts) > 1 else "")
        if side:
            case.set_side_state(side, CaseStatus.BURNED, Unit.COMMERCIAL)
            case.awaiting_approval = False
            case.proposed_action = ""
            case.save(update_fields=[
                "internal_status", "external_status",
                "internal_holder", "external_holder",
                "awaiting_approval", "proposed_action", "updated_at",
            ])
            log(case, actor, EventAction.BURN, comment=comment,
                from_unit=Unit.COMMERCIAL, side=side)
            _finalize_split_if_all_terminal(case)
            return
    if action.startswith("csu:"):
        parts = action.split(":")
        side = _side_from_token(parts[1] if len(parts) > 1 else "")
        if side:
            case.set_side_state(side, CaseStatus.UNSUPPLIABLE_CLOSED, Unit.COMMERCIAL)
            case.awaiting_approval = False
            case.proposed_action = ""
            case.save(update_fields=[
                "internal_status", "external_status",
                "internal_holder", "external_holder",
                "awaiting_approval", "proposed_action", "updated_at",
            ])
            log(case, actor, EventAction.APPROVE_CANCEL, comment=comment,
                from_unit=Unit.COMMERCIAL, side=side)
            _finalize_split_if_all_terminal(case)
            return
    if action.startswith("cs:"):
        parts = action.split(":")
        side = _side_from_token(parts[1] if len(parts) > 1 else "")
        if side:
            case.set_side_state(side, CaseStatus.CANCELLED, Unit.COMMERCIAL)
            case.awaiting_approval = False
            case.proposed_action = ""
            case.save(update_fields=[
                "internal_status", "external_status",
                "internal_holder", "external_holder",
                "awaiting_approval", "proposed_action", "updated_at",
            ])
            log(case, actor, EventAction.APPROVE_CANCEL, comment=comment,
                from_unit=Unit.COMMERCIAL, side=side)
            _finalize_split_if_all_terminal(case)
            return

    if action.startswith("burn"):
        case.status = CaseStatus.BURNED
        evt = EventAction.BURN
    elif action == "cancel_unsuppliable":
        case.status = CaseStatus.UNSUPPLIABLE_CLOSED
        evt = EventAction.APPROVE_CANCEL
    else:
        case.status = CaseStatus.CANCELLED
        evt = EventAction.APPROVE_CANCEL
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=[
        "status", "awaiting_approval", "proposed_action", "updated_at",
    ])
    log(case, actor, evt, comment=comment, from_unit=Unit.COMMERCIAL)


@transaction.atomic
def reject_cancel(case: Case, actor, comment: str = ""):
    """Commercial manager rejects a pending cancel/burn — restore prior state."""
    action = (case.proposed_action or "").strip()
    side = ""

    if action.startswith(("bs:", "cs:", "csu:")):
        parts = action.split(":")
        side = _side_from_token(parts[1] if len(parts) > 1 else "")
        if action.startswith("csu:"):
            prior = CaseStatus.UNSUPPLIABLE
        else:
            prior = parts[2] if len(parts) > 2 else CaseStatus.WITH_COMMERCIAL
            if prior not in dict(CaseStatus.CHOICES):
                prior = CaseStatus.WITH_COMMERCIAL
        if side:
            case.set_side_state(side, prior, Unit.COMMERCIAL)
            case.awaiting_approval = False
            case.proposed_action = ""
            case.save(update_fields=[
                "internal_status", "external_status",
                "internal_holder", "external_holder",
                "awaiting_approval", "proposed_action", "updated_at",
            ])
            log(case, actor, EventAction.REJECT_CANCEL, comment=comment,
                from_unit=Unit.COMMERCIAL, side=side)
            return

    if action.startswith("burn:"):
        prior = action.split(":", 1)[1] or CaseStatus.CLOSED
    elif action.startswith("cancel:"):
        prior = action.split(":", 1)[1] or CaseStatus.WITH_COMMERCIAL
    elif action == "cancel_unsuppliable":
        prior = CaseStatus.UNSUPPLIABLE
    else:
        prior = CaseStatus.WITH_COMMERCIAL
    if prior not in dict(CaseStatus.CHOICES):
        prior = CaseStatus.WITH_COMMERCIAL
    case.status = prior
    case.holder_unit = Unit.COMMERCIAL
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=[
        "status", "holder_unit", "awaiting_approval", "proposed_action", "updated_at",
    ])
    log(case, actor, EventAction.REJECT_CANCEL, comment=comment, from_unit=Unit.COMMERCIAL)


@transaction.atomic
def finalize_case(case: Case, actor, comment: str = ""):
    """Commercial marks a closed case as finally approved (visible to everyone).

    The case stays open (held by Commercial) so it can later be Final-Closed or
    Burned.
    """
    case.status = CaseStatus.FINAL_APPROVED
    case.holder_unit = Unit.COMMERCIAL
    case.save(update_fields=["status", "holder_unit", "updated_at"])
    # Commercial is issuing the proforma: freeze who signed it now, rather than
    # resolving "the current Commercial manager" at print time.
    _freeze_commercial_documents(case)
    log(case, actor, EventAction.FINALIZE, comment=comment, from_unit=Unit.COMMERCIAL)


def final_close_case(case: Case, actor, comment: str = ""):
    """Commercial shuts a final-approved case for good (terminal)."""
    case.status = CaseStatus.FINAL_CLOSED
    case.holder_unit = Unit.COMMERCIAL
    case.save(update_fields=["status", "holder_unit", "updated_at"])
    log(case, actor, EventAction.FINAL_CLOSE, comment=comment, from_unit=Unit.COMMERCIAL)


def burn_case(case: Case, actor, comment: str = ""):
    """Commercial burns a case (deal fell through).

    Experts need manager approval (→ PENDING_CANCEL); managers burn immediately.
    """
    prior = case.status
    if _commercial_needs_cancel_approval(actor):
        case.status = CaseStatus.PENDING_CANCEL
        case.holder_unit = Unit.COMMERCIAL
        case.awaiting_approval = True
        case.proposed_action = f"burn:{prior}"[:30]
        case.save(update_fields=[
            "status", "holder_unit", "awaiting_approval", "proposed_action", "updated_at",
        ])
        log(case, actor, EventAction.REQUEST_CANCEL, comment=comment,
            from_unit=Unit.COMMERCIAL)
        return
    case.status = CaseStatus.BURNED
    case.holder_unit = Unit.COMMERCIAL
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save(update_fields=[
        "status", "holder_unit", "awaiting_approval", "proposed_action", "updated_at",
    ])
    log(case, actor, EventAction.BURN, comment=comment, from_unit=Unit.COMMERCIAL)


def assign_purchasing(case: Case, actor, assignee):
    """Purchasing's own manager assigns the case to one of their experts.

    Cannot reuse the generic assign() above: that dispatches on
    case.holder_unit, which stays Unit.COMMERCIAL the entire time a case is
    with Purchasing (Commercial keeps its own Final-Approved actions on it
    simultaneously — see accounts/constants.py's "PURCHASING and WAREHOUSE"
    docstring section, and inbox_filter_q's own PURCHASING branch for the
    read-side half of this same design). No status/holder_unit change here —
    only purchasing_assignee moves, exactly what inbox_filter_q's PURCHASING
    branch and allowed_actions' own PURCHASING branch both key off.
    """
    case.purchasing_assignee = assignee
    case.save(update_fields=["purchasing_assignee", "updated_at"])
    note = f"Assigned to {assignee.get_full_name() or assignee.username}"
    log(case, actor, EventAction.ASSIGN, to_unit=Unit.PURCHASING, comment=note)


def send_to_warehouse(case: Case, actor, comment: str = ""):
    """Purchasing's ONLY action past building the Purchase Invoice — see the
    product owner's own instruction (one action, no other option here).

    An ordinary exclusive handoff (see send_to_supply above for the shape
    this mirrors) — UNLIKE assign_purchasing just above, this one DOES move
    holder_unit/status, because once Purchasing is done, Purchasing's own
    turn is genuinely over (the case leaves Purchasing's inbox exactly the
    way case.status no longer matching FINAL_APPROVED naturally implies),
    the same way every other handoff in this workflow already works.
    """
    _publish_current_forms_to(case, Unit.WAREHOUSE, leaving_unit=Unit.PURCHASING)
    case.status = CaseStatus.WITH_WAREHOUSE
    case.holder_unit = Unit.WAREHOUSE
    case.assigned_to = case.warehouse_assignee
    case.save(update_fields=[
        "status", "holder_unit", "assigned_to", "updated_at",
    ])
    log(case, actor, EventAction.SEND_TO_WAREHOUSE, comment=comment,
        from_unit=Unit.PURCHASING, to_unit=Unit.WAREHOUSE)


PURCHASE_INVOICE_COLUMNS = [
    "#", "Item Code", "FTCO CODE", "FTCO DESCRIPTION", "SIZE", "QTY", "UNIT",
    "BRAND", "BASE PRICE", "MARGIN %", "UNIT PRICE",
    "QTY PURCHASED", "PRICE PURCHASED", "SUPPLIER",
]


def _purchase_invoice_current_rows(case):
    """(rows, editable_form) for the Purchase Invoice build page.

    ``rows`` always reflects the LATEST Proforma's reference columns
    (Item Code / FTCO CODE / FTCO DESCRIPTION / SIZE / QTY / UNIT / BRAND /
    the base-price+margin%+final-price triple the product owner asked the
    old single UNIT PRICE column split into), each row's own "QTY" here
    being the quantity STILL REMAINING to purchase — original Proforma qty
    minus whatever was already purchased on every PAST, already-SENT
    Purchase Invoice for that same row (matched by "#", the stable row
    identity across Inquiry -> TO -> PI -> Purchase Invoice — see
    _row_client_no's own comment for why never "Item Code", which is
    per-form-minted and not unique).

    ``qty_purchased``/``price_purchased``/``supplier_*`` on each row are the
    Purchasing-only columns: whatever is already saved on the CURRENT,
    NOT-YET-SENT Purchase Invoice form, if one is being edited right now
    (0/blank for a brand new one) — this is the ONE invoice's own entry, an
    INCREMENT, never a running cumulative total (the product owner's own
    words: "whatever number is entered, the remaining shows as a label
    under it" — remaining only ever goes down by what THIS invoice takes).

    ``editable_form`` is that in-progress CaseForm, or None when there is
    nothing to continue editing (either no Purchase Invoice exists yet, or
    the latest one was already sent to Warehouse) — save_purchase_invoice
    below uses this same None-ness to decide "update in place" vs. "start a
    new version".
    """
    from .export_data import _cell, _strip_html, parse_money

    latest_pinv = case.forms.filter(kind=FormKind.PURCHASE_INVOICE).order_by("-version").first()
    editable_form = latest_pinv if (latest_pinv is not None and not latest_pinv.sent) else None

    purchased_elsewhere: dict[str, float] = {}
    for f in case.forms.filter(kind=FormKind.PURCHASE_INVOICE, sent=True):
        for pr in (f.table or []):
            key = _row_client_no(pr)
            purchased_elsewhere[key] = purchased_elsewhere.get(key, 0.0) + parse_money(pr.get("qty_purchased"))

    in_progress_by_row = {}
    if editable_form is not None:
        for pr in (editable_form.table or []):
            in_progress_by_row[_row_client_no(pr)] = pr

    # Primary/non-split side only — Purchasing was not asked for an
    # internal/external split the way Technical/Supply have one, so a case
    # that WAS side-split earlier in Inquiry/TO/PI only offers its primary
    # side's Proforma here. Revisit if/when the product owner asks for
    # Purchasing to handle a split case's second side too.
    pi_form = case.current_form(FormKind.PI)
    if pi_form is None:
        return [], editable_form

    rows = []
    for r in (pi_form.table or []):
        if str((r or {}).get("_deleted", "") or "") == "1":
            continue
        if str((r or {}).get("_unsuppliable", "") or "") == "1":
            continue
        key = _row_client_no(r)
        original_qty = parse_money(r.get("qty"))
        remaining = max(0.0, original_qty - purchased_elsewhere.get(key, 0.0))
        prior = in_progress_by_row.get(key) or {}
        final_price = parse_money(r.get("UNIT PRICE"))
        raw_base = r.get("_unit_price_raw")
        base_price = parse_money(raw_base) if raw_base not in (None, "") else final_price
        margin_percent = round((final_price / base_price - 1) * 100, 1) if base_price else 0.0
        rows.append({
            # "#", not just "client_row" — _row_client_no (used both above,
            # reading purchased_elsewhere/in_progress_by_row, and again the
            # NEXT time this exact saved row is read back) looks for "#"
            # first. Without this key a round trip through
            # save_purchase_invoice -> re-render silently lost every row's
            # own qty_purchased/price_purchased/supplier — caught by
            # actually re-reading a save back in testing, not by inspection.
            "#": key,
            "client_row": key,
            "item_code": _cell(r, "Item Code"),
            "ftco_code": _cell(r, "کد"),
            "ftco_description": _strip_html(_cell(r, "Final Arranged Text")),
            "size": _strip_html(_cell(r, "size")),
            "qty": remaining,
            "unit": _strip_html(_cell(r, "unit")),
            "brand": _strip_html(_cell(r, "BRAND")),
            "base_price": base_price,
            "margin_percent": margin_percent,
            "unit_price": final_price,
            "qty_purchased": parse_money(prior.get("qty_purchased")),
            "price_purchased": prior.get("price_purchased") or "",
            "supplier_id": prior.get("supplier_id"),
            "supplier_name": prior.get("supplier_name") or "",
        })
    return rows, editable_form


def purchase_invoice_rows(case) -> list:
    """Public read-only entry point for _purchase_invoice_current_rows —
    used by the build page's GET and by _purchase_invoice_section.html's
    own summary, so both always agree with what a save would validate
    against."""
    rows, _editable_form = _purchase_invoice_current_rows(case)
    return rows


def save_purchase_invoice(case, actor, post_data) -> "CaseForm":
    """Save the Purchase Invoice currently being built/edited (never sends
    it — see send_to_warehouse above for the one and only forward action,
    per the product owner's own instruction).

    ``post_data`` is a QueryDict — ``getlist`` per field, one entry per row,
    in the SAME order _purchase_invoice_current_rows returned them (the
    build template renders one row per list entry and posts back in that
    order — see cases/templates/cases/purchase_invoice_build.html).
    """
    from .export_data import parse_money

    rows, editable_form = _purchase_invoice_current_rows(case)
    qty_list = post_data.getlist("qty_purchased")
    price_list = post_data.getlist("price_purchased")
    supplier_list = post_data.getlist("supplier")

    for i, row in enumerate(rows):
        raw_qty = qty_list[i] if i < len(qty_list) else ""
        new_qty = parse_money(raw_qty) if str(raw_qty).strip() else 0.0
        if new_qty > row["qty"] + 1e-9:
            raise ValueError(
                f"Row #{row['client_row']}: cannot purchase more than the "
                f"remaining quantity ({row['qty']:g})."
            )
        row["qty_purchased"] = new_qty
        raw_price = price_list[i] if i < len(price_list) else ""
        row["price_purchased"] = str(raw_price).strip()
        raw_supplier = supplier_list[i] if i < len(supplier_list) else ""
        raw_supplier = str(raw_supplier).strip()
        if raw_supplier:
            supplier = Supplier.objects.filter(pk=raw_supplier).first()
            row["supplier_id"] = supplier.pk if supplier else None
            row["supplier_name"] = supplier.name if supplier else ""
        else:
            row["supplier_id"] = None
            row["supplier_name"] = ""

    if editable_form is not None:
        editable_form.table = rows
        editable_form.columns = PURCHASE_INVOICE_COLUMNS
        editable_form.save(update_fields=["table", "columns", "updated_at"])
        form = editable_form
        is_new = False
    else:
        latest = case.forms.filter(kind=FormKind.PURCHASE_INVOICE).order_by("-version").first()
        version = (latest.version + 1) if latest is not None else 1
        form = CaseForm.objects.create(
            case=case, kind=FormKind.PURCHASE_INVOICE, side="",
            version=version, table=rows, columns=PURCHASE_INVOICE_COLUMNS,
        )
        form.make_current()
        is_new = True

    log(case, actor, EventAction.BUILD_PURCHASE_INVOICE,
        comment=("Purchase Invoice built" if is_new else "Purchase Invoice updated"),
        form_kind=FormKind.PURCHASE_INVOICE, form_version=form.version)
    return form


def _upgrade_price_to_two_stage(case: Case, actor, *,
                                new_table=None, new_comments=None):
    """Convert a single-side (Internal OR External) case into a combined
    Internal & External (BOTH) split case.

    The progressed side keeps its status/holder and full history (events tagged
    to that side). The new side starts fresh, with its inquiry seeded from the
    prior side's latest inquiry — taking the SAME version number (e.g. prior side
    at v04 -> new side starts at v04) — so it can be revised independently via
    "New version".

    ``new_table`` / ``new_comments`` are what the items editor collected for the
    NEW side before confirming the conversion (see :func:`upgrade_two_stage`).
    When given they replace the copied rows / row notes on the new side's first
    inquiry — the conversion is authored, not merely mirrored. They are the only
    thing they change: the version number, the flags and every write on the
    PRIOR side are identical either way, because what the user typed for the new
    side says nothing about the side that already existed. ``None`` (the
    programmatic caller in ``commit_inquiry_version``) keeps the plain copy.

    The invariant this function is written around: the side that ALREADY EXISTS
    must come out of the split byte-identical, because the two sides are
    independent and creating the missing one says nothing about the other. The
    only columns it may write on the prior side are the per-side ``*_status`` /
    ``*_holder`` pair that restates the whole-case status/holder it already had,
    and the ``side`` tag on rows that were carrying a blank one. Its snapshots,
    their version numbers, their ``sent`` / ``two_stage`` flags, which one is
    current, its assignees and its timeline all stay exactly as they were.
    """
    prior_side = Side.INTERNAL if case.price_type == PriceType.INTERNAL else Side.EXTERNAL
    new_side = Side.EXTERNAL if prior_side == Side.INTERNAL else Side.INTERNAL

    # 0) FIRST, before anything moves: write down which snapshot each form kind
    #    resolves to on the side that already exists. This has to happen here,
    #    ahead of step 2, because ``current_form`` falls back to the blank legacy
    #    side only for the case's PRIMARY side — and ``primary_side`` is derived
    #    from ``price_type``, which step 2 rewrites. Read after the flip, a
    #    prior_side of External would stop seeing its own blank-side rows.
    #    These pks are the definition of "how this side looked before the split",
    #    and step 4 restores exactly them.
    #    Driven off FormKind.CHOICES rather than a hand-written triple so a kind
    #    added later cannot slip through the reconciliation in step 4 unnoticed.
    keep_current = {
        kind: getattr(case.current_form(kind, prior_side), "pk", None)
        for kind, _label in FormKind.CHOICES
    }

    # 1) Tag all existing (sideless) timeline events to the side that has run so
    #    far, so the combined timeline shows that history under the prior side.
    case.events.filter(side="").update(side=prior_side)

    # 2) Flip the price type to BOTH and turn on the independent split streams.
    case.price_type = PriceType.BOTH
    case.split_active = True
    case.price_upgraded_two_stage = True

    # 3) The progressed side keeps the case's current status/holder. The new side
    #    is created here and has never been anywhere, so it starts at DRAFT with
    #    Commercial — the same state every side of a brand-new Internal & External
    #    case is born in (see ``create_case`` / ``sync_fresh_draft_price_type``,
    #    which write DRAFT into both side columns). RETURNED_TO_COMMERCIAL, which
    #    this used to be, said the opposite: that the side had been sent out and
    #    handed back, which never happened.
    #
    #    What the old value was protecting, and how it is still protected:
    #    the reasoning was "the new side is NOT a blank draft (it carries a full
    #    inquiry), so it must be revised through New version, never by editing
    #    the current version in place". That rule is enforced by ``sent=True`` on
    #    the seeded inquiry below, not by the status — ``can_do_side_action``'s
    #    ``edit_inquiry`` gate is ``not (cur and cur.sent)`` and never looks at
    #    the side's status, and the editor's own per-side gate reads the same
    #    flag. Nothing else in the per-side machinery separates the two codes:
    #    ``can_do_side_action`` gates Commercial actions on holder + owner +
    #    "not terminal / not CLOSED / not FINAL_APPROVED", DRAFT and
    #    RETURNED_TO_COMMERCIAL being neither; the inbox rule
    #    (``inbox_filter_q``) asks only "held by Commercial and not terminal";
    #    and ``_is_fresh_converted_side`` — the gate that keeps "New version"
    #    on offer for this very side — already accepts DRAFT alongside
    #    RETURNED_TO_COMMERCIAL. The one visible difference is the pill and the
    #    Archive tab, which is exactly the defect being fixed: "Draft" instead of
    #    "Returned to Commercial", and the Draft tab instead of With Commercial.
    #    Both surfaces read it through ``status_view``, so they cannot disagree.
    cur_status = case.status
    cur_holder = case.holder_unit
    NEW_SIDE_STATUS = CaseStatus.DRAFT
    if prior_side == Side.INTERNAL:
        case.internal_status = cur_status
        case.internal_holder = cur_holder
        case.external_status = NEW_SIDE_STATUS
        case.external_holder = Unit.COMMERCIAL
    else:
        case.external_status = cur_status
        case.external_holder = cur_holder
        case.internal_status = NEW_SIDE_STATUS
        case.internal_holder = Unit.COMMERCIAL

    # 4) Re-tag existing side-less form snapshots (inquiry/TO/PI) to the prior
    #    side so they belong to that side's stream after the split. A split case
    #    matches ``side`` exactly — the blank-side fallback in
    #    :meth:`Case.current_form` is primary-side-only, and the per-side version
    #    chips filter on ``side=`` — so a row left blank would drop out of the
    #    side's history altogether.
    #
    #    Re-tagging on its own is destructive, though, because a live case can
    #    hold BOTH blank-side and sided snapshots of the SAME kind: a "New
    #    version" taken while the case was single-sided writes its snapshot on
    #    one of the two, and ``make_current`` only clears siblings that share its
    #    side, so the other stream keeps an ``is_current`` row of its own. That
    #    is harmless while the case is unsplit — the sided row wins the lookup
    #    and the blank one is simply never resolved — but merging the two streams
    #    leaves TWO rows flagged current for one (kind, side), and
    #    ``CaseForm.Meta.ordering`` (-version, -two_stage, -id) then hands
    #    ``current_form`` whichever sorts first. On the one live case this
    #    feature can be used on that is the blank row: an UNSENT snapshot with
    #    different table content displaces the sent one the side is showing —
    #    stale content published, and a version that was closed to editing
    #    (sent=True) replaced by one that is not, which is a permission the
    #    holder did not have a moment earlier.
    #
    #    So: re-tag, then restate the single fact that must not move — the
    #    snapshot this side resolved to BEFORE the split (step 0) is still its
    #    current one, and every other row of that kind on this side is history.
    #    Nothing else is written: no ``version``, no ``sent``, no ``two_stage``,
    #    no table, no event. Only the ``is_current`` flags that merging the two
    #    streams made ambiguous, and only on the prior side.
    #
    #    Note this deliberately does NOT try to decide which snapshot "should"
    #    have won. Whether a blank-side row that never superseded its sided
    #    sibling was right is a pre-existing question about that case's history;
    #    splitting it is not allowed to answer it in either direction.
    case.forms.filter(side="").update(side=prior_side)
    for kind, keep_pk in keep_current.items():
        if keep_pk is None:
            # This side had no current snapshot of this kind before the split,
            # and re-tagging cannot invent one: every blank-side row belonged to
            # the primary side (= prior_side) already, so if none of them was
            # current then none is current now either. Leave the stream alone.
            continue
        # Only the losers are written. The keeper needs no update of its own:
        # ``current_form`` only ever returns an ``is_current`` row, and nothing
        # between step 0 and here clears that flag — the re-tag above writes
        # ``side`` alone, and the exclude() below skips the keeper.
        (case.forms.filter(kind=kind, side=prior_side, is_current=True)
             .exclude(pk=keep_pk).update(is_current=False))

    case.save(update_fields=[
        "price_type", "split_active", "price_upgraded_two_stage",
        "internal_status", "internal_holder",
        "external_status", "external_holder", "updated_at",
    ])

    # 5/6) Timeline. The prior (progressed) side keeps all its own earlier
    #    history untouched. The brand-new side is a FRESH case: copy its inquiry
    #    from the prior side's latest inquiry (items match). Per the spec, the new
    #    side's first inquiry takes the SAME version number as the prior side's
    #    latest inquiry (e.g. prior side at v04 -> new side starts at v04, an exact
    #    copy), unsent and editable. The new side then behaves exactly like a
    #    standalone closed case: its "New version" only bumps the number when the
    #    table actually changes (or stays put with a two-stage label). A single
    #    CREATE event tagged to the NEW side records the conversion.
    # Seed from the snapshot pinned in step 0 — the one the prior side was
    # showing before anything moved — rather than re-resolving here. Re-resolving
    # would read a case whose ``price_type`` step 2 has already flipped to BOTH,
    # so ``primary_side`` (and with it ``current_form``'s legacy blank-side
    # fallback) no longer means what it meant when the side was measured. Same
    # row in the ordinary case; immune to the ordering of the steps above.
    prior_inq = (case.forms.filter(pk=keep_current[FormKind.INQUIRY]).first()
                 if keep_current.get(FormKind.INQUIRY) else None)
    prior_table = [dict(r) for r in (prior_inq.table or [])] if prior_inq else []
    new_version = prior_inq.version if prior_inq else 0
    new_form = CaseForm(case=case, kind=FormKind.INQUIRY, side=new_side,
                        version=new_version, created_by=actor,
                        unit_at_creation=Unit.COMMERCIAL)
    new_form.columns = (prior_inq.columns if prior_inq else
                        ["#", "Item", "Description", "Size", "Qty", "Unit"])
    # The rows the editor authored for the new side, when there are any;
    # otherwise the plain copy this function has always made.
    new_form.table = ([dict(r) for r in new_table] if new_table is not None
                      else prior_table)
    # ``dict(...)`` and not the prior form's own mapping: the notes below are
    # written into it, and sharing the object would edit the prior side's
    # in-memory meta as well.
    seed_meta = dict((prior_inq.meta if prior_inq else {}) or {})
    if new_comments is not None:
        # Commercial's row notes for the NEW side, replacing (never merging
        # with) whatever the prior side happened to be carrying — the editor's
        # map is the whole of what was said about these rows.
        clean = {str(k): str(v or "").strip()
                 for k, v in (new_comments or {}).items() if str(v or "").strip()}
        if clean:
            seed_meta["_comm_comments"] = clean
        else:
            seed_meta.pop("_comm_comments", None)
        # …and the prior side's record of which notes its timeline already read
        # says nothing about these ones, so it must not suppress announcing them
        # on the new side's first handoff.
        seed_meta.pop("_comm_comments_sent", None)
    new_form.meta = seed_meta
    # Carry the TO & PI two-stage generation onto the copied inquiry: if the case
    # was upgraded to two-stage before the Internal & External split, the new
    # side's inquiry must keep the "· Two Stage" label and the "TO & PI (Two
    # Stage)" offer-type header (its number stays the same, e.g. 01 · Two Stage),
    # so the new side starts in the same generation as the prior side.
    new_form.two_stage = bool(prior_inq.two_stage) if prior_inq else False
    # Sent=True closes the current version to in-place editing: the new side is
    # revised through "New version" only, just like a closed standalone case.
    new_form.sent = True
    new_form.is_current = True
    new_form.save()
    new_form.make_current()
    # two_stage=True so the combined timeline shows the "Two Stage" chip on this
    # "Case created" row's title (next to the side chip), in addition to the
    # "Internal & External Two Stage" note kept in the comment.
    log(case, actor, EventAction.CREATE,
        comment="Internal & External Two Stage",
        to_unit=Unit.COMMERCIAL, side=new_side, two_stage=True)


@transaction.atomic
def upgrade_two_stage(case: Case, actor, comment: str = "", *,
                      new_table=None, comments=None, deadline=None, **_ignored):
    """Commercial action: convert a single-side case (Internal OR External) into a
    combined Internal & External two-stage split — WITHOUT creating a new inquiry
    version on the existing side, and WITHOUT moving the case.

    ``new_table`` / ``comments`` are the rows and Commercial row notes the items
    editor collected for the NEW side; ``deadline`` is the value the editor
    demanded before it would confirm. The editor is the only route the UI
    offers, so in practice all three arrive together — the conversion is
    confirmed by saving the new side's first version, exactly the way every
    other version in this product is created, rather than by a button that acted
    on its own. They are optional so the programmatic caller in
    ``commit_inquiry_version`` keeps working unchanged.

    ``deadline`` is written on the CASE, because that is where a deadline lives
    in this model — there is no per-side column — so it is the same field the
    New-version editor writes from the same box. It is written only after the
    conversion has gone through, inside this transaction: a refused conversion
    leaves the case exactly as it was, deadline included. ``None`` leaves it
    alone.

    Commercial may do this whenever it wants, whoever is currently holding the
    case: the two sides of an Internal & External case are independent streams, so
    creating the missing one says nothing about the one that already exists. The
    already-progressed side keeps its status, its holder, its assignees and its
    whole history (:func:`_upgrade_price_to_two_stage` seeds that side's per-side
    columns from the case's current whole-case status/holder and never writes
    ``status``/``holder_unit``/``assigned_to``); the new side starts fresh with
    Commercial, so Commercial can send it on its own.

    Refused — clearly, and without half-doing it — when the case is already
    Internal & External, when it has ended or been marked FINAL_APPROVED, or
    when there is no inquiry to copy the new side from.
    """
    if case.is_split or case.price_type not in (PriceType.INTERNAL, PriceType.EXTERNAL):
        raise ValueError("This case is already Internal & External.")
    if case.status in CaseStatus.TERMINAL or case.status == CaseStatus.FINAL_APPROVED:
        raise ValueError(
            "This case has ended (final approved / final closed / burned / "
            "cancelled / cannot supply); it can no longer be made Internal & External."
        )
    if case.current_form(FormKind.INQUIRY) is None:
        raise ValueError(
            "This case has no inquiry yet, so there is nothing to copy onto the new side."
        )
    _upgrade_price_to_two_stage(case, actor,
                                new_table=new_table, new_comments=comments)
    if deadline is not None and deadline != case.deadline:
        # ``update_fields`` keeps this to the one column: the conversion has
        # just written the case row, and nothing still held in memory here may
        # be pushed back over it.
        case.deadline = deadline
        case.save(update_fields=["deadline", "updated_at"])


class InquiryUnchanged(Exception):
    """Raised when a 'new version' save carries no table change and no upgrade
    (two-stage / currency-conversion / update-price) — so no new inquiry version
    may be created."""


@transaction.atomic
def commit_inquiry_version(case: Case, actor, *, new_table: list, side: str = "",
                           offer_type: str = "", price_type: str = "",
                           currency_conversion: bool = False,
                           update_price: bool = False,
                           columns: list | None = None, meta: dict | None = None,
                           comments: dict | None = None):
    """Commit a "New version" save coming from the inquiry editor.

    The caller (edit_items) has already collected the freshly-edited rows. The
    rules enforced here implement the business spec exactly:

      • A new inquiry version is created with the NEXT number (e.g. 02 -> 03)
        ONLY when the table actually changed — a cell edited, a row added, or a
        row deleted (deletions show as a gap in #).

      • If nothing in the table changed AND the user did not request a TO -> TO &
        PI two-stage upgrade AND did not request a currency-conversion-only
        reopen AND did not request Update price, NO new version is made:
        ``InquiryUnchanged`` is raised.

      • Special two-stage case: when the case is currently TO-only and the user
        toggled "TO & PI (two stage)" on, a new version IS produced even with no
        table change — but it keeps the SAME version number (02 stays 02), now
        carrying the two-stage label.

      • Currency-conversion-only: when the user toggled "Unit conversion" and
        made no table / TO&PI / Int&Ext / Update-price change, a new version
        number is created labelled for unit conversion. Prior TO/PI are cloned
        to that version so Commercial can re-convert Proforma and Send to client
        without routing to Technical/Supply.

      • Update price / Unit convert labelling:
          – Update price alone, table unchanged → chip “· Update price”
          – Unit convert alone, table unchanged → chip “· Unit convert”
            (currency-conversion-only workflow)
          – Both toggles (any table state), or either toggle with table edits →
            plain “Version NN” chip; badges “Update Price” / “Unit Convert”
            above the inquiry table. Timeline + Technical handoff notes always
            reflect every requested toggle.
        Unit conversion and Update price apply only on TO & PI cases.

    ``comments`` is the Commercial row-note map (``{client row # -> note}``) the
    editor collected for this save. It is stored on the new version's meta, not
    on its rows — see ``_inquiry_comment_map``. ``None`` means "the caller has
    nothing to say about the notes", so the version we branch from keeps its
    own; that is what the ``new_inquiry_version`` shim below needs, since it
    re-commits the current table without ever seeing the editor.
    """
    columns = columns or ["#", "Item", "Description", "Size", "Qty", "Unit"]

    # Unit conversion / Update price are TO & PI only (ignore if somehow posted
    # for a TO-only case).
    if case.offer_type != OfferType.TO_PI:
        currency_conversion = False
        update_price = False

    # --- Resolve the "current" inquiry this save is branching from -------------
    is_split_side = bool(case.is_split and side in (Side.INTERNAL, Side.EXTERNAL))
    cur = case.current_form(FormKind.INQUIRY, side if is_split_side else "")
    if cur is None and not is_split_side:
        # Fall back to the primary/sideless current inquiry.
        cur = case.current_form(FormKind.INQUIRY)
    prior_table = list(cur.table or []) if cur else []
    base_version = cur.version if cur else 0

    # Snapshot prior TO/PI before the new inquiry becomes current (needed to
    # clone them onto a currency-conversion-only version).
    prior_side_hint = (side if is_split_side else (cur.side if cur else ""))
    prior_to = case.current_form(FormKind.TO, prior_side_hint if is_split_side else None)
    prior_pi = case.current_form(FormKind.PI, prior_side_hint if is_split_side else None)
    if prior_to is None and not is_split_side:
        prior_to = case.current_form(FormKind.TO)
    if prior_pi is None and not is_split_side:
        prior_pi = case.current_form(FormKind.PI)

    # --- Did the table actually change? ---------------------------------------
    # The notes are compared alongside the rows so a comment-only edit is still a
    # change worth a version. They are resolved through the map helper, which
    # reads an old version's notes off its rows, so branching from a pre-move
    # version compares like with like instead of seeing every note disappear.
    prior_comments = _inquiry_comment_map(prior_table, cur.meta if cur else None)
    new_comments = ({str(k): str(v or "").strip()
                     for k, v in (comments or {}).items() if str(v or "").strip()}
                    if comments is not None else dict(prior_comments))
    changed = not _inquiry_tables_equal(prior_table, new_table,
                                        prior_comments, new_comments)
    # Real row/cell/add/delete change (comments alone do not clear Update-price chip).
    content_changed = not _inquiry_tables_content_equal(prior_table, new_table)

    # --- Was a TO -> TO & PI two-stage upgrade requested? ---------------------
    two_stage_req = (offer_type == OfferType.TO_PI and case.offer_type == OfferType.TO)

    # --- Was an Internal/External price upgrade requested? (non-split only) ----
    price_upgrade_req = (price_type == PriceType.BOTH
                         and case.price_type in (PriceType.INTERNAL, PriceType.EXTERNAL)
                         and not case.is_split)

    update_price_flag = bool(update_price)

    # Currency-conversion-only is valid only when nothing else changed/upgraded.
    currency_only_req = bool(
        currency_conversion and not changed and not two_stage_req
        and not price_upgrade_req and not update_price_flag
    )

    # Nothing changed and no upgrade of any kind -> refuse (no new version).
    if (not changed and not two_stage_req and not price_upgrade_req
            and not currency_only_req and not update_price_flag):
        raise InquiryUnchanged()

    # --- Apply a price-type (Internal & External) upgrade first, if requested.
    #     This converts the case to a split BOTH case; the freshly-edited rows
    #     below then land on the progressed (pre-upgrade) side's stream. (In the
    #     current UI this path is not reached from the editor — Internal & External
    #     conversion uses its own dedicated button — but it is handled safely.)
    price_upgraded_now = False
    if price_upgrade_req:
        progressed_side = (Side.INTERNAL if case.price_type == PriceType.INTERNAL
                           else Side.EXTERNAL)
        _upgrade_price_to_two_stage(case, actor)
        price_upgraded_now = True
        side = progressed_side
        is_split_side = bool(case.is_split and side in (Side.INTERNAL, Side.EXTERNAL))
        cur = case.current_form(FormKind.INQUIRY, side if is_split_side else "")
        base_version = cur.version if cur else base_version

    # --- Apply a TO -> TO & PI offer-type upgrade (case-level), if requested.
    upgraded_now = False
    if two_stage_req:
        case.offer_type = OfferType.TO_PI
        case.upgraded_two_stage = True
        case.save(update_fields=["offer_type", "upgraded_two_stage", "updated_at"])
        upgraded_now = True

    # --- Decide the version number --------------------------------------------
    #   changed OR currency-only OR update-price -> advance the number
    #   not changed (two-stage only) -> keep the SAME number
    version = (base_version + 1) if (changed or currency_only_req or update_price_flag) else base_version

    # --- Write the inquiry snapshot for the target side -----------------------
    active_sides = case.sides or [""]
    if is_split_side:
        target_side = side
    elif cur is not None and cur.side in active_sides:
        target_side = cur.side
    else:
        target_side = case.primary_side

    form_meta = dict(meta if meta is not None else (cur.meta if cur else {}) or {})
    currency_flag = bool(currency_conversion)

    # Chip suffixes (on the Version pill) — only when ONE toggle is alone and the
    # table content is unchanged:
    #   · Update price   → update_price only, no Unit convert
    #   · Unit convert   → Unit convert only, no Update price
    # When BOTH are on (with or without table edits), the chip stays plain
    # "Version NN" and badges above the inquiry table carry the labels.
    chip_update = bool(update_price_flag and not content_changed and not currency_flag)

    # The Commercial row notes for THIS version. Written here and nowhere near
    # ``new_form.table``, which is what keeps them off the inquiry / proforma
    # detail rows and out of every export.
    if comments is not None:
        if new_comments:
            form_meta["_comm_comments"] = dict(new_comments)
        else:
            form_meta.pop("_comm_comments", None)
    # ``_comm_comments_sent`` — the notes as last read out on the timeline — is
    # inherited with the rest of form_meta and deliberately left alone here. A
    # note the editor seeded forward untouched still matches it and stays quiet
    # on this version's handoff; edit the text, or move it to other rows, and it
    # no longer matches, so the handoff reads out the new one. Comparing the two
    # maps at send time is the whole rule; there is no flag to keep in step.

    if currency_only_req:
        form_meta["currency_conversion_only"] = True
    else:
        form_meta.pop("currency_conversion_only", None)

    if chip_update:
        form_meta["update_price"] = True
    else:
        form_meta.pop("update_price", None)

    # Request flags drive timeline / Technical auto-notes and the above-table
    # badges (shown when the label is NOT already on the version chip).
    if update_price_flag:
        form_meta["update_price_requested"] = True
    else:
        form_meta.pop("update_price_requested", None)
    if currency_flag:
        form_meta["unit_convert_requested"] = True
    else:
        form_meta.pop("unit_convert_requested", None)

    if changed or currency_only_req or update_price_flag:
        # A genuinely new version number: create the new CaseForm row.
        new_form = CaseForm(case=case, kind=FormKind.INQUIRY, side=target_side,
                            version=version, created_by=actor,
                            unit_at_creation=Unit.COMMERCIAL)
        new_form.columns = columns
        new_form.table = list(new_table or [])
        new_form.meta = form_meta
        # Currency-only: mark sent so Commercial cannot edit rows; they only
        # convert Proforma and Send to client / Cancel.
        new_form.sent = bool(currency_only_req)
        new_form.two_stage = bool(case.upgraded_two_stage or upgraded_now)
        new_form.is_current = True
        new_form.save()
        new_form.make_current()
    else:
        # Two-stage-only (no table change): keep the SAME version number but make a
        # DISTINCT new record, flagged two_stage, so the history shows both
        # "Version NN" and "Version NN · Two Stage". It becomes the current,
        # unsent snapshot so the unit can now build the newly-required PI.
        new_form = CaseForm(case=case, kind=FormKind.INQUIRY, side=target_side,
                            version=version, created_by=actor,
                            unit_at_creation=Unit.COMMERCIAL)
        new_form.columns = columns
        new_form.table = list(new_table or [])
        new_form.meta = form_meta
        new_form.sent = False
        new_form.two_stage = bool(case.upgraded_two_stage or upgraded_now)
        new_form.is_current = True
        new_form.save()
        new_form.make_current()

    # Non-split cases have exactly ONE live inquiry side. make_current only clears
    # same-side siblings, so if an older snapshot is still flagged current on a
    # DIFFERENT side (e.g. a stale blank/legacy side), clear it here.
    if not is_split_side:
        stale = case.forms.filter(kind=FormKind.INQUIRY, is_current=True)\
                          .exclude(pk=new_form.pk).exclude(side=target_side)
        for f in stale:
            f.is_current = False
            f.save(update_fields=["is_current"])

    # Currency-conversion-only: clone prior TO/PI onto this version so Send to
    # client is ready and Commercial can re-convert Proforma without Technical.
    # These clones are invisible to Technical/Supply (see case_detail filters).
    if currency_only_req:
        inq_two = bool(new_form.two_stage)
        _clone_offer_form_for_version(
            case, actor, kind=FormKind.TO, side=target_side,
            version=version, two_stage=inq_two, source_form=prior_to)
        _clone_offer_form_for_version(
            case, actor, kind=FormKind.PI, side=target_side,
            version=version, two_stage=inq_two, source_form=prior_pi,
            clear_currency=True)
    elif chip_update:
        # Content-unchanged Update-Price revision: carry TO/PI forward to
        # this version too, same shape as the currency-only path above, but
        # VISIBLE and OPEN (currency_only=False) — Technical/Supply have a
        # real ask here (re-price), unlike a currency reopen. Without this,
        # each unit was independently "behind" the new inquiry version with
        # nothing carried forward, and could only catch up by running its
        # OWN New Version pass first — forcing a redundant re-version at
        # every hop of Technical -> Supply -> Commercial for a version that
        # had already been decided once, upstream, by Commercial.
        inq_two = bool(new_form.two_stage)
        cloned_to = _clone_offer_form_for_version(
            case, actor, kind=FormKind.TO, side=target_side,
            version=version, two_stage=inq_two, source_form=prior_to,
            currency_only=False)
        cloned_pi = _clone_offer_form_for_version(
            case, actor, kind=FormKind.PI, side=target_side,
            version=version, two_stage=inq_two, source_form=prior_pi,
            currency_only=False)
        # The clone above copies the PRIOR TO/PI's own meta (never had these
        # flags — they are new on THIS inquiry version), not the Inquiry's.
        # Stamp them here so Technical's/Supply's own version chip also reads
        # "Update price", exactly like save_form does for a form built the
        # ordinary way (see save_form's identical copy, a few hundred lines
        # up, which this clone path bypasses entirely).
        for cloned in (cloned_to, cloned_pi):
            if cloned is None:
                continue
            cmeta = dict(cloned.meta or {})
            for flag in ("update_price", "update_price_requested", "unit_convert_requested"):
                if form_meta.get(flag):
                    cmeta[flag] = True
                else:
                    cmeta.pop(flag, None)
            cloned.meta = cmeta
            cloned.save(update_fields=["meta"])
    else:
        # A real revision supersedes any Commercial FX-only TO/PI clones so
        # Technical/Supply build at this inquiry version (e.g. 04), not the
        # skipped currency-only intermediate (e.g. 03).
        _restore_real_offer_current(case, side=target_side, kind=FormKind.TO)
        _restore_real_offer_current(case, side=target_side, kind=FormKind.PI)

    # --- Re-open the stream so Commercial can re-work it (mirrors old behaviour)
    if is_split_side:
        if case.side_status(side) == CaseStatus.CLOSED:
            case.set_side_state(side, CaseStatus.RETURNED_TO_COMMERCIAL, Unit.COMMERCIAL)
            fields = ["internal_status", "external_status",
                      "internal_holder", "external_holder", "updated_at"]
            if case.status in CaseStatus.TERMINAL:
                case.status = CaseStatus.WITH_COMMERCIAL
                case.holder_unit = Unit.COMMERCIAL
                fields += ["status", "holder_unit"]
            case.save(update_fields=fields)
    else:
        # A non-split New version is only ever reachable from CLOSED (already sent
        # to the client), so it re-opens the case with Commercial to be re-worked.
        case.status = CaseStatus.RETURNED_TO_COMMERCIAL
        case.holder_unit = Unit.COMMERCIAL
        case.awaiting_approval = False
        case.proposed_action = ""
        case.save(update_fields=["status", "holder_unit", "awaiting_approval",
                                 "proposed_action", "updated_at"])

    # --- Timeline -------------------------------------------------------------
    notes = []
    if upgraded_now:
        notes.append("TO & PI Two Stage")
    if price_upgraded_now:
        notes.append("Int & Ext Two Stage")
    if currency_flag:
        notes.append("Unit Convert")
    if update_price_flag:
        notes.append("Update Price")
    row_note = _row_change_summary(prior_table, new_table) if changed else ""
    if row_note:
        notes.append(row_note)
    comment_note = _inquiry_comment_summary(new_table, new_comments)
    if comment_note:
        notes.append(comment_note)
    note_suffix = (" — " + " · ".join(notes)) if notes else ""
    log(case, actor, EventAction.NEW_VERSION,
        comment=f"New inquiry version {version:02d}{note_suffix}",
        from_unit=Unit.COMMERCIAL, side=(target_side or ""),
        form_kind=FormKind.INQUIRY, form_version=version,
        two_stage=(upgraded_now or price_upgraded_now))
    return version


def _inquiry_comment_groups(table, comments=None) -> list[tuple[str, list]]:
    """Group identical Commercial row comments → (note, [client_row, …]).

    ``comments`` is the version's note map; omitting it reads the notes off the
    rows, the old shape. Either way the grouping still walks the TABLE, so the
    order of the groups and of the row numbers inside each one is the order the
    rows sit in the grid — moving the storage changes nothing the reader sees.
    """
    from collections import OrderedDict
    if comments is None:
        comments = _inquiry_comment_map(table)
    groups: OrderedDict[str, list] = OrderedDict()
    for r in (table or []):
        r = r or {}
        if str(r.get("_deleted", "") or "") == "1":
            continue
        note = str(comments.get(
            _norm_cell(r.get("#", r.get("client_row", ""))), "") or "").strip()
        if not note:
            continue
        cr = _row_client_no(r)
        groups.setdefault(note, []).append(cr)
    return list(groups.items())


def _format_inquiry_comment_line(note: str, client_rows: list, *, active_count: int) -> str:
    """Format one comment group the way Commercial wants it in UI / timeline."""
    note = (note or "").strip()
    rows = [str(c).strip() for c in (client_rows or []) if str(c).strip()]
    if not note:
        return ""
    if active_count > 0 and len(rows) >= active_count:
        return f"all row : {note}"
    if len(rows) == 1:
        return f"rows: #{rows[0]} : {note}"
    if not rows:
        return note
    # rows: #4 , 6 , 8 : comment
    head = f"#{rows[0]}"
    rest = " , ".join(rows[1:])
    return f"rows: {head} , {rest} : {note}"


def _inquiry_active_row_count(table) -> int:
    n = 0
    for r in (table or []):
        if str((r or {}).get("_deleted", "") or "") == "1":
            continue
        n += 1
    return n


def _inquiry_comment_summary(table, comments=None) -> str:
    """Short timeline note listing the Commercial row comments on the inquiry."""
    active = _inquiry_active_row_count(table)
    parts = [
        _format_inquiry_comment_line(note, crs, active_count=active)
        for note, crs in _inquiry_comment_groups(table, comments)
    ]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) > 8:
        return "Comments: " + "; ".join(parts[:8]) + f" (+{len(parts) - 8} more)"
    return "Comments: " + "; ".join(parts)


def is_update_price_version(case, side: str = "") -> bool:
    """True when the current inquiry was opened with the Update price toggle."""
    inq = case.current_form(FormKind.INQUIRY, side or None)
    if inq is None:
        return False
    meta = (inq.meta or {})
    return bool(meta.get("update_price") or meta.get("update_price_requested"))


def _commercial_handoff_auto_notes(case, side: str = "") -> list:
    """Auto notes for Commercial → Technical: Update price + per-row comments."""
    notes = []
    inq = case.current_form(FormKind.INQUIRY, side or None)
    if inq is None and not side:
        inq = case.current_form(FormKind.INQUIRY)
    if inq is None:
        return notes
    meta = (inq.meta or {})
    if bool(meta.get("update_price") or meta.get("update_price_requested")):
        notes.append(
            f"New version {inq.version:02d} — Update Price."
        )
    if bool(meta.get("unit_convert_requested")):
        notes.append(
            f"New version {inq.version:02d} — Unit Convert."
        )
    # A row note is read out on the handoff it was written for, once.
    # ``_comm_comments_sent`` holds the notes as last announced; while they still
    # match, this handoff has nothing new to say. Without that check the same
    # note goes onto the timeline again every time the version comes back from
    # Technical and is sent out afresh, which reads as Commercial repeating an
    # instruction nobody repeated.
    row_notes = _inquiry_comment_map(inq.table, meta)
    if row_notes and meta.get("_comm_comments_sent") != row_notes:
        active = _inquiry_active_row_count(inq.table)
        for note, crs in _inquiry_comment_groups(inq.table, row_notes):
            line = _format_inquiry_comment_line(note, crs, active_count=active)
            if line:
                notes.append(line)
    return notes


def _mark_inquiry_comments_announced(case, side: str = "") -> None:
    """Record on the handed-over inquiry which row notes the timeline just read.

    Resolves the same form ``_commercial_handoff_auto_notes`` read from, so what
    is recorded is exactly what was announced.

    Stored as the note map itself rather than a flag, for two reasons. It answers
    the only question the next handoff asks — "is this the same thing Commercial
    already said?" — and a mapping is a value ``_form_table.html`` skips when it
    prints a form's meta as badges, so this stays out of the case page the way
    ``_sent_to`` and ``_sent_rb`` do. A bare ``True`` would be printed there.

    Called AFTER the handoff has published the forms: ``_publish_current_forms_to``
    rewrites the same ``meta`` to add ``_sent_to`` and the later write wins, so
    going last is what keeps both.

    An old version whose notes still sit on its rows is recorded too — that is
    what lets an existing case stop repeating itself without a migration.
    """
    inq = case.current_form(FormKind.INQUIRY, side or None)
    if inq is None and not side:
        inq = case.current_form(FormKind.INQUIRY)
    if inq is None:
        return
    meta = dict(inq.meta or {})
    announced = _inquiry_comment_map(inq.table, meta)
    stored = meta.get("_comm_comments_sent")
    # Nothing to record, and nothing recorded before: leave meta untouched rather
    # than stamping an empty map onto every inquiry that ever changes hands.
    if stored == announced or (stored is None and not announced):
        return
    meta["_comm_comments_sent"] = announced
    inq.meta = meta
    inq.save(update_fields=["meta"])


@transaction.atomic
def new_inquiry_version(case: Case, actor, *, offer_type: str = "", price_type: str = "",
                        side: str = "", currency_conversion: bool = False,
                        update_price: bool = False, **_ignored):
    """Backwards-compatible shim.

    The new-version workflow now happens entirely in the inquiry editor (the
    table is gathered there and committed via ``commit_inquiry_version`` only if
    it actually changed). This shim is kept so any older code path / direct call
    still produces a sensible result: it commits using the side's CURRENT table
    (i.e. no row change), which is only valid when a two-stage upgrade, a
    currency-conversion-only reopen, or Update price is also requested;
    otherwise it raises ``InquiryUnchanged``.
    """
    is_split_side = bool(case.is_split and side in (Side.INTERNAL, Side.EXTERNAL))
    cur = case.current_form(FormKind.INQUIRY, side if is_split_side else "") \
        or case.current_form(FormKind.INQUIRY)
    table = list(cur.table or []) if cur else []
    return commit_inquiry_version(
        case, actor, new_table=table, side=side,
        offer_type=offer_type, price_type=price_type,
        currency_conversion=currency_conversion,
        update_price=update_price,
    )
