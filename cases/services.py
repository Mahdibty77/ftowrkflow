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
from .models import Case, CaseEvent, CaseForm, Client, LineItem, SerialCounter

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
    return CaseEvent.objects.create(
        case=case, actor=actor, action=action, comment=comment,
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
                client_technical_expert: str = "", client_technical_phone: str = "") -> Case:
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
    form = qs.filter(version=0).first()
    if form is None:
        form = qs.order_by("version").first()
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


def _inquiry_signature(rows) -> list:
    """Return a comparable signature of an inquiry table.

    Two inquiry tables are considered IDENTICAL (no new version warranted) when
    they carry the same ordered rows with the same client row number (#) and the
    same Description / Size / Qty / Unit / soft-delete / add markers. Soft-
    deleting a row (keeping it with ``_deleted=1``) or adding a row changes the
    signature. The live "Item" sequence (1..N) is intentionally ignored.
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
            _norm_cell(r.get("_comm_comment", r.get("comment", ""))),
        ))
    return sig


def _inquiry_content_signature(rows) -> list:
    """Like ``_inquiry_signature`` but ignores per-row Commercial comments.

    Used so “Update price” version labelling cares about real table edits
    (cells / add / delete), not comment-only changes.
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
        for kind in (FormKind.INQUIRY, FormKind.TO, FormKind.PI):
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


def _inquiry_tables_equal(rows_a, rows_b) -> bool:
    """True when two inquiry tables have identical content (see _inquiry_signature)."""
    return _inquiry_signature(rows_a) == _inquiry_signature(rows_b)


def _inquiry_tables_content_equal(rows_a, rows_b) -> bool:
    """True when tables match ignoring Commercial per-row comments."""
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


@transaction.atomic
def save_form(case: Case, *, kind: str, columns: list, table: list, meta: dict,
              actor, new_version: bool = False, is_edit: bool = False,
              side: str = "") -> CaseForm:
    """Create or update a TO/PI form snapshot for a given side.

    ``new_version`` True copies the current form into a new, higher version
    (00 -> 01 -> 02 …). Otherwise the current version's data is replaced.
    ``is_edit`` records the timeline entry as an edit rather than a build.
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
            case.forms.filter(kind=kind, side=side or "")
            .order_by("-version", "-id")
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

    form, _created = CaseForm.objects.get_or_create(
        case=case, kind=kind, side=side, version=version, two_stage=inq_two_stage,
        defaults={"created_by": actor, "unit_at_creation": _unit_of(actor)},
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
    form.meta = new_meta
    form.two_stage = inq_two_stage
    form.signed_by = actor
    form.sent = False  # freshly built/edited -> editable until it leaves the unit
    form.save()
    form.make_current()

    # Freeze the technical expert name on first TO — never rewrite when seats move.
    if kind == FormKind.TO:
        freeze_technical_expert(case, actor)

    # Record the save in the timeline. A brand-new version (or first build) is a
    # BUILD; re-saving an existing version is an EDIT. Both carry the form kind
    # and version so the reader sees exactly what was touched.
    if is_edit:
        log(case, actor, EventAction.EDIT, from_unit=_unit_of(actor),
            form_kind=kind, form_version=version, side=side)
    else:
        action = EventAction.BUILD_PI if kind == FormKind.PI else EventAction.BUILD_TO
        log(case, actor, action, from_unit=_unit_of(actor),
            form_kind=kind, form_version=version, side=side)
    return form


# ---------------------------------------------------------------------------
# Permissions: which actions a user may take on a case right now
# ---------------------------------------------------------------------------
def _collapse_rows(rows):
    """If every row shows the same status, collapse to one unlabelled row."""
    distinct = {(r[1], r[2]) for r in rows}
    if len(distinct) == 1:
        return [(None, rows[0][1], rows[0][2])]
    return rows


def _row_for_side(case, sc):
    st = case.side_status(sc)
    return (Side.LABELS.get(sc, sc),
            CaseStatus.LABELS.get(st, st),
            CaseStatus.COLORS.get(st, "#6b7280"))


def detail_status_rows(case: Case, user):
    """Status rows for the case-detail page.

    * Non-split -> the single whole-case status.
    * Split -> a supply EXPERT sees only the side they own; everyone else sees
      both sides (collapsed to one row while the two sides are in the same
      place, e.g. the manager is building both himself).
    """
    if not case.is_split:
        return [(None, case.status_label, case.status_color)]
    profile = getattr(user, "profile", None)
    if (profile is not None and profile.unit == Unit.SUPPLY
            and profile.role == Role.EXPERT):
        rows = []
        for sc in case.sides:
            owner = (case.supply_internal_assignee_id if sc == Side.INTERNAL
                     else case.supply_external_assignee_id if sc == Side.EXTERNAL else None)
            if owner == user.id:
                rows.append(_row_for_side(case, sc))
        return rows or [(None, case.status_label, case.status_color)]
    return _collapse_rows([_row_for_side(case, sc) for sc in case.sides])


def inbox_status_rows(case: Case, user):
    """Status rows for the inbox: only the side(s) that are in this user's hands
    (the reason the case is in their inbox). Supply experts see only their own
    side; other units see whichever sides they currently hold."""
    if not case.is_split:
        return [(None, case.status_label, case.status_color)]
    profile = getattr(user, "profile", None)
    sides = []
    if profile is None:
        sides = list(case.sides)
    elif profile.unit == Unit.SUPPLY:
        if profile.role == Role.EXPERT:
            for sc in case.sides:
                owner = (case.supply_internal_assignee_id if sc == Side.INTERNAL
                         else case.supply_external_assignee_id if sc == Side.EXTERNAL else None)
                if owner == user.id:
                    sides.append(sc)
        else:
            sides = [sc for sc in case.sides if case.side_holder(sc) == Unit.SUPPLY]
    elif profile.unit == Unit.COMMERCIAL:
        sides = [sc for sc in case.sides if case.side_holder(sc) == Unit.COMMERCIAL]
    elif profile.unit == Unit.TECHNICAL:
        sides = [sc for sc in case.sides if case.side_holder(sc) == Unit.TECHNICAL]
    else:
        sides = list(case.sides)
    if not sides:
        sides = list(case.sides)
    return [_row_for_side(case, sc) for sc in sides]


def inbox_cases(user, *, role=None, work_user=None):
    """Cases currently in this user's inbox.

    Non-split cases use the whole-case status/holder. Split (Internal &
    External) cases are tracked per side from creation, so membership is decided
    purely by which side is held by this unit (and, for experts, assigned to
    them). The two sets are combined.

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
        return Case.objects.none()
    # Supervisors use the same ownership inbox rules as experts for their unit
    # (needed so Delegated tasks land in their Inbox, e.g. a DRAFT they now own).
    unit = profile.unit
    TERM = CaseStatus.TERMINAL

    def side_active_at(u, sc):
        holder = "internal_holder" if sc == Side.INTERNAL else "external_holder"
        status = "internal_status" if sc == Side.INTERNAL else "external_status"
        return Q(**{holder: u}) & ~Q(**{f"{status}__in": TERM})

    qs = Case.objects.all()
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
        base = qs.filter(ns | sp)
        pending_ns = nonsplit & Q(status=CaseStatus.PENDING_CANCEL)
        pending_sp = split & (
            Q(internal_status=CaseStatus.PENDING_CANCEL)
            | Q(external_status=CaseStatus.PENDING_CANCEL)
        )
        pending = qs.filter(pending_ns | pending_sp)
        if profile.role == Role.MANAGER:
            # Manager: own live cases + every cancel/burn awaiting their approval.
            return (base.filter(created_by=work) | pending).distinct()
        # Experts / supervisors: only their own cases, and once they request
        # cancel/burn the file leaves their inbox until the manager rejects it.
        return (base.filter(created_by=work)
                .exclude(status=CaseStatus.PENDING_CANCEL)
                .exclude(internal_status=CaseStatus.PENDING_CANCEL)
                .exclude(external_status=CaseStatus.PENDING_CANCEL)
                .distinct())

    if unit == Unit.TECHNICAL:
        ns = nonsplit & Q(status__in=[CaseStatus.WITH_TECHNICAL, CaseStatus.RETURNED_TO_TECHNICAL])
        if profile.role == Role.MANAGER:
            ns = ns & (Q(assigned_to__isnull=True) | Q(assigned_to=work) | Q(technical_assignee=work))
            sp = split & (side_active_at(Unit.TECHNICAL, Side.INTERNAL)
                          | side_active_at(Unit.TECHNICAL, Side.EXTERNAL))
        else:
            ns = ns & (Q(assigned_to=work) | Q(technical_assignee=work))
            sp = split & (
                (side_active_at(Unit.TECHNICAL, Side.INTERNAL) & Q(technical_internal_assignee=work))
                | (side_active_at(Unit.TECHNICAL, Side.EXTERNAL) & Q(technical_external_assignee=work)))
        return qs.filter(ns | sp).distinct()

    if unit == Unit.SUPPLY:
        ns = nonsplit & Q(status=CaseStatus.WITH_SUPPLY)
        if profile.role == Role.MANAGER:
            ns = ns & (
                Q(price_type__in=[PriceType.INTERNAL, PriceType.BOTH], supply_internal_assignee__isnull=True)
                | Q(price_type__in=[PriceType.EXTERNAL, PriceType.BOTH], supply_external_assignee__isnull=True)
                | Q(supply_internal_assignee=work) | Q(supply_external_assignee=work)
                | Q(supply_assignee=work)
                | Q(assigned_to=work))
            sp = split & (side_active_at(Unit.SUPPLY, Side.INTERNAL)
                          | side_active_at(Unit.SUPPLY, Side.EXTERNAL))
        else:
            ns = ns & (
                Q(supply_internal_assignee=work) | Q(supply_external_assignee=work)
                | Q(supply_assignee=work))
            sp = split & (
                (side_active_at(Unit.SUPPLY, Side.INTERNAL) & Q(supply_internal_assignee=work))
                | (side_active_at(Unit.SUPPLY, Side.EXTERNAL) & Q(supply_external_assignee=work)))
        return qs.filter(ns | sp).distinct()

    return Case.objects.none()


def inbox_count(user, *, role=None, work_user=None) -> int:
    try:
        return inbox_cases(user, role=role, work_user=work_user).count()
    except Exception:
        return 0


def inbox_cases_for_request(request):
    """Inbox queryset using the active role's seat user (fixes multi-seat 500)."""
    from people.role_nav import work_context
    ctx = work_context(request)
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
    # Allowed only while merely CLOSED; once a side is FINAL_APPROVED it can only
    # be Final-Closed.
    if action == "burn":
        return (holder == Unit.COMMERCIAL and owns
                and case.side_status(side) == CaseStatus.CLOSED)

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


def _all_forms_ready_for_client(case, side: str = "", *, pi_required: bool = True) -> bool:
    """True when every required form is built AT the current inquiry version.

    "Send to client" is only allowed once the offer is fully assembled for the
    latest inquiry: the TO must exist at the inquiry's version AND the PI must
    also exist at that version. A Proforma is part of EVERY client deliverable —
    even a TO-only case must have its PI built before it can be sent to the
    client (for a TO-only case the Proforma simply carries no prices; see the
    price-stripping guard in save_form). This enforces that a new inquiry
    version cannot be sent until Technical and Supply have produced their
    matching new versions.

    ``pi_required`` defaults to True (the send-to-client rule above). The
    Internal/External two-stage *upgrade* gate passes
    ``pi_required=case.needs_pricing`` to keep its original behaviour: that
    conversion is about the pricing split, not about sending to the client, so
    it must not start demanding a Proforma a TO-only case would not yet have.
    """
    def _ready(sd):
        inq = case.current_form(FormKind.INQUIRY, sd)
        if inq is None:
            return False
        to = case.current_form(FormKind.TO, sd)
        if to is None or to.version < inq.version \
           or bool(to.two_stage) != bool(inq.two_stage):
            return False
        if pi_required:
            pi = case.current_form(FormKind.PI, sd)
            if pi is None or pi.version < inq.version \
               or bool(pi.two_stage) != bool(inq.two_stage):
                return False
        return True

    if case.is_split and not side:
        # Whole-case readiness: every active side must be ready.
        return all(_ready(sc) for sc in case.sides)
    return _ready(side or None)


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
                                  source_form, clear_currency: bool = False):
    """Copy a TO/PI snapshot onto a new inquiry version (currency-only reopen).

    Clones are Commercial-only artefacts: marked ``currency_conversion_only`` so
    Technical/Supply never see or work them. A later real inquiry revision
    advances past this version number.
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
    meta["currency_conversion_only"] = True
    clone = CaseForm(
        case=case, kind=kind, side=side or "",
        version=version, created_by=actor,
        unit_at_creation=Unit.COMMERCIAL,
    )
    clone.columns = list(source_form.columns or [])
    clone.table = [dict(r or {}) for r in (source_form.table or [])]
    clone.meta = meta
    clone.sent = True
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
        case.forms.filter(kind=kind, side=side or "").order_by("-version", "-id")
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
            # A final-approved case is still open but can only be Final-Closed
            # (terminal). Burning is no longer offered once it is final-approved.
            actions = {"final_close", "comment", "view"}
        if can_new_version:
            actions.add("new_inquiry_version")
        # Two-stage conversion (Internal/External single side -> BOTH) is offered
        # whenever the case is at Commercial with both required forms built for
        # the current version — even before send-to-client. It does NOT need a new
        # version, and disappears once the case is already split.
        if (not case.is_split
                and case.price_type in {PriceType.INTERNAL, PriceType.EXTERNAL}
                and status in {CaseStatus.WITH_COMMERCIAL,
                               CaseStatus.RETURNED_TO_COMMERCIAL, CaseStatus.CLOSED}
                and _all_forms_ready_for_client(case, pi_required=case.needs_pricing)):
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

    # Export is available to every unit as soon as a TO or PI exists — even when
    # the viewer is not the current holder of the case.
    if case.forms.filter(kind__in=[FormKind.TO, FormKind.PI], is_current=True).exists():
        actions.add("export")

    return actions


def allowed_actions_for_request(case: Case, request) -> set[str]:
    """``allowed_actions`` using the active role's seat (inbox-consistent)."""
    from people.role_nav import work_context
    ctx = work_context(request)
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
    if allowed_actions(case, user, role=role, work_user=work):
        return True

    if case_forms is None:
        case_forms = list(case.forms.all())
    if case_events is None:
        case_events = list(case.events.all())

    participated = (
        case.created_by_id == work_id
        or case.assigned_to_id == work_id
        or case.technical_assignee_id == work_id
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
        _publish_current_forms_to(case, Unit.TECHNICAL, leaving_unit=Unit.COMMERCIAL)
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


def cancel_side(case: Case, actor, side: str, comment: str = ""):
    """Commercial cancels one side.

    Experts must get manager approval (side → PENDING_CANCEL). Managers cancel
    immediately. If that side had been marked *cannot supply*, the terminal
    outcome stays UNSUPPLIABLE_CLOSED rather than CANCELLED.
    """
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


def _is_fresh_converted_side(case: Case, side: str) -> bool:
    """True for the brand-new side created by an Internal & External two-stage
    conversion that has not been worked yet.

    Such a side carries a full inquiry (copied from the prior side, marked sent)
    but no TO/PI of its own, sits at RETURNED_TO_COMMERCIAL with Commercial, and
    must be revised through "New version" only. Once it builds its own TO it
    behaves like any other side (New version only after CLOSED).
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
        fields = ["supply_internal_assignee", "supply_external_assignee",
                  "supply_assignee", "split_active",
                  "internal_status", "external_status",
                  "internal_holder", "external_holder", "updated_at"]
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


def _commercial_manager():
    from django.contrib.auth.models import User
    return (User.objects.filter(profile__unit=Unit.COMMERCIAL,
                                profile__role=Role.MANAGER, is_active=True).first())


def _supply_manager():
    from django.contrib.auth.models import User
    return (User.objects.filter(profile__unit=Unit.SUPPLY,
                                profile__role=Role.MANAGER, is_active=True).first())


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


@transaction.atomic
def approve_unsuppliable(case: Case, actor, comment: str = ""):
    """Approve a cannot-supply request at whichever stage it is in."""
    if case.status == CaseStatus.UNSUPPLIABLE_PENDING_SUPPLY:
        # Supply manager approved -> escalate to the commercial manager.
        case.status = CaseStatus.UNSUPPLIABLE_PENDING_COMMERCIAL
        case.holder_unit = Unit.COMMERCIAL
        case.assigned_to = _commercial_manager()
        case.awaiting_approval = True
    else:
        # Commercial manager approved -> case is resolved as cannot-supply.
        case.status = CaseStatus.UNSUPPLIABLE
        case.holder_unit = Unit.COMMERCIAL
        case.assigned_to = case.created_by
        case.awaiting_approval = False
        case.proposed_action = ""
    case.save()
    log(case, actor, EventAction.APPROVE_UNSUPPLIABLE, comment=comment, from_unit=_unit_of(actor))


@transaction.atomic
def reject_unsuppliable(case: Case, actor, comment: str = ""):
    """Reject a cannot-supply request (with a comment)."""
    if case.status == CaseStatus.UNSUPPLIABLE_PENDING_SUPPLY:
        # Back to Supply to keep working on it.
        case.status = CaseStatus.WITH_SUPPLY
        case.holder_unit = Unit.SUPPLY
    else:
        # Commercial manager rejected -> the related expert can re-work / route it.
        case.status = CaseStatus.RETURNED_TO_COMMERCIAL
        case.holder_unit = Unit.COMMERCIAL
        case.assigned_to = case.created_by
    case.awaiting_approval = False
    case.proposed_action = ""
    case.save()
    log(case, actor, EventAction.REJECT_UNSUPPLIABLE, comment=comment, from_unit=_unit_of(actor))


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


def _upgrade_price_to_two_stage(case: Case, actor):
    """Convert a single-side (Internal OR External) case into a combined
    Internal & External (BOTH) split case.

    The progressed side keeps its status/holder and full history (events tagged
    to that side). The new side starts fresh, with its inquiry copied from the
    prior side's latest inquiry — taking the SAME version number (e.g. prior side
    at v04 -> new side starts at v04) — so it can be revised independently via
    "New version".
    """
    prior_side = Side.INTERNAL if case.price_type == PriceType.INTERNAL else Side.EXTERNAL
    new_side = Side.EXTERNAL if prior_side == Side.INTERNAL else Side.INTERNAL

    # 1) Tag all existing (sideless) timeline events to the side that has run so
    #    far, so the combined timeline shows that history under the prior side.
    case.events.filter(side="").update(side=prior_side)

    # 2) Flip the price type to BOTH and turn on the independent split streams.
    case.price_type = PriceType.BOTH
    case.split_active = True
    case.price_upgraded_two_stage = True

    # 3) The progressed side keeps the case's current status/holder. The new side
    #    is seeded as a fresh-but-closed-like stream sitting with Commercial: it
    #    is NOT a blank draft (it carries a full inquiry copied from the prior
    #    side). It therefore starts at RETURNED_TO_COMMERCIAL so the inquiry can
    #    only be revised through "New version" (exactly like a closed standalone),
    #    never by editing the current version in place.
    cur_status = case.status
    cur_holder = case.holder_unit
    NEW_SIDE_STATUS = CaseStatus.RETURNED_TO_COMMERCIAL
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
    #    side so they belong to that side's stream after the split.
    case.forms.filter(side="").update(side=prior_side)

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
    prior_inq = case.current_form(FormKind.INQUIRY, prior_side)
    prior_table = [dict(r) for r in (prior_inq.table or [])] if prior_inq else []
    new_version = prior_inq.version if prior_inq else 0
    new_form = CaseForm(case=case, kind=FormKind.INQUIRY, side=new_side,
                        version=new_version, created_by=actor,
                        unit_at_creation=Unit.COMMERCIAL)
    new_form.columns = (prior_inq.columns if prior_inq else
                        ["#", "Item", "Description", "Size", "Qty", "Unit"])
    new_form.table = prior_table
    new_form.meta = (prior_inq.meta if prior_inq else {})
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
def upgrade_two_stage(case: Case, actor, comment: str = "", **_ignored):
    """Commercial action: convert a single-side case (Internal OR External) into a
    combined Internal & External two-stage split — WITHOUT creating a new inquiry
    version. Only valid while the case is non-split, set to INTERNAL or EXTERNAL,
    and both required forms (TO, and PI when pricing) exist at the current inquiry
    version. The already-progressed side keeps its history; the other side starts
    fresh and independent.
    """
    if case.is_split or case.price_type not in (PriceType.INTERNAL, PriceType.EXTERNAL):
        raise ValueError("This case can't be converted to two stage.")
    if not _all_forms_ready_for_client(case, pi_required=case.needs_pricing):
        raise ValueError("Build the TO (and PI) for the latest version before converting.")
    _upgrade_price_to_two_stage(case, actor)


class InquiryUnchanged(Exception):
    """Raised when a 'new version' save carries no table change and no upgrade
    (two-stage / currency-conversion / update-price) — so no new inquiry version
    may be created."""


@transaction.atomic
def commit_inquiry_version(case: Case, actor, *, new_table: list, side: str = "",
                           offer_type: str = "", price_type: str = "",
                           currency_conversion: bool = False,
                           update_price: bool = False,
                           columns: list | None = None, meta: dict | None = None):
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
    changed = not _inquiry_tables_equal(prior_table, new_table)
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
    comment_note = _inquiry_comment_summary(new_table)
    if comment_note:
        notes.append(comment_note)
    note_suffix = (" — " + " · ".join(notes)) if notes else ""
    log(case, actor, EventAction.NEW_VERSION,
        comment=f"New inquiry version {version:02d}{note_suffix}",
        from_unit=Unit.COMMERCIAL, side=(target_side or ""),
        form_kind=FormKind.INQUIRY, form_version=version,
        two_stage=(upgraded_now or price_upgraded_now))
    return version


def _inquiry_comment_groups(table) -> list[tuple[str, list]]:
    """Group identical Commercial row comments → (note, [client_row, …])."""
    from collections import OrderedDict
    groups: OrderedDict[str, list] = OrderedDict()
    for r in (table or []):
        if str((r or {}).get("_deleted", "") or "") == "1":
            continue
        note = str((r or {}).get("_comm_comment", "") or "").strip()
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


def _inquiry_comment_summary(table) -> str:
    """Short timeline note listing Commercial per-row comments on the inquiry."""
    active = _inquiry_active_row_count(table)
    parts = [
        _format_inquiry_comment_line(note, crs, active_count=active)
        for note, crs in _inquiry_comment_groups(table)
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
    active = _inquiry_active_row_count(inq.table)
    for note, crs in _inquiry_comment_groups(inq.table):
        line = _format_inquiry_comment_line(note, crs, active_count=active)
        if line:
            notes.append(line)
    return notes


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
