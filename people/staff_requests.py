"""Personnel staff requests (Overtime) — Person-scoped.

Keeps CaseForm / seats out of scope. Reuses work_shift + shift_hours for
overtime window extension and day/month reporting.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Person,
    PersonRequestAccess,
    RequestType,
    ShiftDayLog,
    StaffRequest,
)
from .work_shift import now_local, person_for_user, shift_window


def ensure_request_types() -> list[RequestType]:
    """Idempotent seed used by views if migration seed was skipped."""
    obj, _ = RequestType.objects.get_or_create(
        code=RequestType.CODE_OVERTIME,
        defaults={
            "title": "Overtime",
            "description": "Request extra work time after the daily shift ends.",
            "icon": "fa-stopwatch",
            "sort_order": 10,
            "is_active": True,
        },
    )
    # Drop retired types if any remain outside migrations.
    try:
        RequestType.objects.filter(code__in=["leave", "purchase"]).delete()
    except Exception:
        RequestType.objects.filter(code__in=["leave", "purchase"]).update(is_active=False)
    return [obj]


def active_request_types() -> list[RequestType]:
    ensure_request_types()
    return list(
        RequestType.objects.filter(is_active=True, code=RequestType.CODE_OVERTIME)
        .order_by("sort_order", "title")
    )


def pending_request_count() -> int:
    return StaffRequest.objects.filter(
        status=StaffRequest.STATUS_SUBMITTED,
        request_type__code=RequestType.CODE_OVERTIME,
    ).count()


def person_has_access(person: Person | None, type_code: str) -> bool:
    if person is None:
        return False
    return PersonRequestAccess.objects.filter(
        person=person, request_type__code=type_code, request_type__is_active=True,
    ).exists()


def access_types_for_person(person: Person) -> list[RequestType]:
    return list(
        RequestType.objects.filter(
            is_active=True, person_access__person=person,
        ).order_by("sort_order", "title")
    )


def granted_person_ids(request_type: RequestType) -> set[int]:
    return set(
        PersonRequestAccess.objects.filter(request_type=request_type)
        .values_list("person_id", flat=True)
    )


@transaction.atomic
def set_access_for_type(
    request_type: RequestType,
    person_ids: list[int],
    *,
    granted_by=None,
) -> tuple[int, int]:
    """Replace access list for one type. Returns (added, removed)."""
    wanted = {int(pk) for pk in person_ids}
    current = granted_person_ids(request_type)
    to_add = wanted - current
    to_remove = current - wanted
    if to_remove:
        PersonRequestAccess.objects.filter(
            request_type=request_type, person_id__in=to_remove,
        ).delete()
    for pid in to_add:
        PersonRequestAccess.objects.create(
            person_id=pid, request_type=request_type, granted_by=granted_by,
        )
    return len(to_add), len(to_remove)


def minutes_label(total: int) -> str:
    total = max(0, int(total or 0))
    h, m = divmod(total, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def approved_overtime_minutes_for_day(person: Person, day: date) -> int:
    """Sum of approved overtime minutes applying to ``day``."""
    total = (
        StaffRequest.objects.filter(
            person=person,
            request_type__code=RequestType.CODE_OVERTIME,
            status=StaffRequest.STATUS_APPROVED,
            work_day=day,
        ).aggregate(s=Sum("approved_minutes"))["s"]
        or 0
    )
    return int(total)


def extended_shift_end(person: Person | None, when: datetime | None = None) -> datetime | None:
    """Shift end datetime for ``when.date()``, plus approved overtime that day."""
    if person is None:
        return None
    when = when or now_local()
    start, end = shift_window(person)
    end_dt = datetime.combine(when.date(), end, tzinfo=when.tzinfo)
    if start > end and when.time().replace(microsecond=0) >= start:
        end_dt += timedelta(days=1)
    ot = approved_overtime_minutes_for_day(person, when.date())
    if ot:
        end_dt = end_dt + timedelta(minutes=ot)
    return end_dt


def is_within_extended_window(person: Person | None, when: datetime | None = None) -> bool:
    """True when ``when`` is inside the normal shift or approved OT extension."""
    if person is None:
        return False
    when = when or now_local()
    start, end = shift_window(person)
    now_t = when.time().replace(microsecond=0)
    if start <= end:
        if start <= now_t < end:
            return True
    else:
        if now_t >= start or now_t < end:
            return True
    end_ext = extended_shift_end(person, when)
    if end_ext is None:
        return False
    start_dt = datetime.combine(when.date(), start, tzinfo=when.tzinfo)
    if start > end and now_t < end:
        start_dt -= timedelta(days=1)
    return start_dt <= when < end_ext


def allocate_request_code(person: Person, request_type: RequestType) -> str:
    """Mint a unique staff-request number (FT-OT-{ym}-{detail}-{serial})."""
    from cases.codes import year_month_token
    from cases.models import SerialCounter

    counter, _ = SerialCounter.objects.select_for_update().get_or_create(
        key="staff_request_serial", defaults={"value": 0},
    )
    counter.value += 1
    counter.save(update_fields=["value"])
    type_token = {
        RequestType.CODE_OVERTIME: "OT",
    }.get(request_type.code, "RQ")
    person_code = (person.detail_code or str(person.pk)).strip() or str(person.pk)
    return "-".join([
        "FT",
        type_token,
        year_month_token(),
        person_code,
        f"{counter.value:04d}",
    ])


@transaction.atomic
def submit_overtime(
    *,
    person: Person,
    user,
    case_ids: list[int],
    hours: int,
    minutes: int,
    comment: str = "",
) -> StaffRequest:
    rt = RequestType.objects.get(code=RequestType.CODE_OVERTIME, is_active=True)
    if not person_has_access(person, RequestType.CODE_OVERTIME):
        raise PermissionError("Overtime is not assigned to this person.")
    req_min = max(0, int(hours) * 60 + int(minutes))
    if req_min <= 0:
        raise ValueError("Requested overtime must be greater than zero.")
    ids = []
    seen = set()
    for raw in case_ids:
        pk = int(raw)
        if pk not in seen:
            seen.add(pk)
            ids.append(pk)
    if not ids:
        raise ValueError("Select at least one case from the archive.")
    when = now_local()
    code = allocate_request_code(person, rt)
    req = StaffRequest.objects.create(
        person=person,
        request_type=rt,
        request_code=code,
        status=StaffRequest.STATUS_SUBMITTED,
        case_ids=ids,
        requested_minutes=req_min,
        comment=(comment or "").strip(),
        work_day=when.date(),
        created_by=user,
        submitted_at=when,
        payload={"case_ids": ids},
    )
    return req


@transaction.atomic
def decide_overtime(
    req: StaffRequest,
    *,
    user,
    approve: bool,
    approved_minutes: int | None = None,
    note: str = "",
) -> StaffRequest:
    if req.status != StaffRequest.STATUS_SUBMITTED:
        raise ValueError("Only submitted requests can be decided.")
    if req.request_type.code != RequestType.CODE_OVERTIME:
        raise ValueError("Not an overtime request.")

    when = timezone.now()
    req.decided_by = user
    req.decided_at = when
    req.decision_note = (note or "").strip()
    if approve:
        mins = int(approved_minutes if approved_minutes is not None else req.requested_minutes)
        mins = max(0, mins)
        if mins <= 0:
            raise ValueError("Approved duration must be greater than zero.")
        req.approved_minutes = mins
        req.status = StaffRequest.STATUS_APPROVED
        req.save()
        # Do NOT credit approved minutes as worked OT immediately.
        # OT minutes accrue from actual presence during the extended window.
        from . import shift_hours as sh
        sh.freeze_past_months(req.person)
        sh.refresh_worked(req.person)
    else:
        req.approved_minutes = 0
        req.status = StaffRequest.STATUS_REJECTED
        req.save()
    return req


def _credit_overtime_day(person: Person, day: date, minutes: int) -> None:
    """Refresh day OT from actual presence (kept for compatibility callers)."""
    from . import shift_hours as sh

    # overtime_minutes on the day log is presence-based; only refresh month totals.
    sh.freeze_past_months(person)
    sh.refresh_worked(person)


def linked_cases_display(case_ids: list[int]) -> list[dict[str, Any]]:
    if not case_ids:
        return []
    try:
        from cases.models import Case
    except Exception:
        return [{"pk": pk, "label": f"#{pk}"} for pk in case_ids]
    by_id = {
        c.pk: c
        for c in Case.objects.filter(pk__in=case_ids).select_related("client")
    }
    out = []
    for pk in case_ids:
        c = by_id.get(pk)
        if c is None:
            out.append({"pk": pk, "label": f"Case #{pk}", "missing": True})
        else:
            out.append({
                "pk": c.pk,
                "label": f"{c.doc_no}",
                "client": getattr(getattr(c, "client", None), "name", "") or "",
                "missing": False,
            })
    return out


def gm_pending_overtime():
    return (
        StaffRequest.objects.filter(
            status=StaffRequest.STATUS_SUBMITTED,
            request_type__code=RequestType.CODE_OVERTIME,
        )
        .select_related("person", "request_type", "created_by")
        .order_by("submitted_at", "pk")
    )


def decided_count_for_person(person: Person, type_code: str | None = None) -> int:
    qs = StaffRequest.objects.filter(
        person=person,
        status__in=[StaffRequest.STATUS_APPROVED, StaffRequest.STATUS_REJECTED],
    )
    if type_code:
        qs = qs.filter(request_type__code=type_code)
    return qs.count()


SEEN_IDS_PREFIX = "staff_req_seen_ids_"


def decided_qs_for_type(person: Person, type_code: str):
    return StaffRequest.objects.filter(
        person=person,
        request_type__code=type_code,
        status__in=[StaffRequest.STATUS_APPROVED, StaffRequest.STATUS_REJECTED],
    )


def unread_decided_count(
    person: Person,
    type_code: str,
    *,
    seen_ids=None,
) -> int:
    """Count decided requests the person has not opened yet (by stored pk set)."""
    qs = decided_qs_for_type(person, type_code)
    if seen_ids is not None:
        qs = qs.exclude(pk__in=list(seen_ids))
    return qs.count()


def mark_request_type_seen(session, person: Person, type_code: str) -> None:
    """Snapshot all currently decided request ids for this type as seen."""
    current = set(
        decided_qs_for_type(person, type_code).values_list("pk", flat=True)
    )
    prev_raw = session.get(f"{SEEN_IDS_PREFIX}{type_code}") or []
    try:
        prev = {int(x) for x in prev_raw}
    except (TypeError, ValueError):
        prev = set()
    session[f"{SEEN_IDS_PREFIX}{type_code}"] = sorted(prev | current)
    session.modified = True


def seen_ids_from_session(session, type_code: str):
    """Return seen pk list, or None if this type was never opened."""
    if f"{SEEN_IDS_PREFIX}{type_code}" not in session:
        return None
    raw = session.get(f"{SEEN_IDS_PREFIX}{type_code}") or []
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError):
        return []


def approved_overtime_request_for_day(person: Person, day: date):
    """Latest approved overtime request applying to ``day``, or None."""
    return (
        StaffRequest.objects.filter(
            person=person,
            request_type__code=RequestType.CODE_OVERTIME,
            status=StaffRequest.STATUS_APPROVED,
            work_day=day,
        )
        .order_by("-decided_at", "-pk")
        .first()
    )


def history_for_person(person: Person):
    return (
        StaffRequest.objects.filter(person=person)
        .select_related("request_type", "decided_by", "created_by")
        .order_by("created_at", "pk")
    )


def history_for_gm():
    return (
        StaffRequest.objects.exclude(status=StaffRequest.STATUS_DRAFT)
        .exclude(status=StaffRequest.STATUS_SUBMITTED)
        .select_related("person", "request_type", "decided_by", "created_by")
        .order_by("created_at", "pk")
    )


def history_rows_enriched(qs):
    rows = []
    for r in qs:
        decider = ""
        if r.decided_by_id:
            decider = (
                r.decided_by.get_full_name() or r.decided_by.username or ""
            ).strip()
        rows.append({
            "req": r,
            "cases": linked_cases_display(r.case_ids or []),
            "decider": decider or "—",
        })
    return rows


def filter_options_from_rows(rows: list[dict], *, show_person: bool = False) -> dict:
    codes, people, types, statuses = set(), set(), set(), set()
    for row in rows:
        r = row["req"]
        codes.add((r.request_code or str(r.pk)).strip())
        types.add(r.request_type.title)
        statuses.add(r.get_status_display())
        if show_person:
            people.add(r.person.display_name)
    return {
        "f_codes": sorted(codes),
        "f_people": sorted(people),
        "f_types": sorted(types),
        "f_statuses": sorted(statuses),
    }


def person_for_request_user(user) -> Person | None:
    return person_for_user(user)
