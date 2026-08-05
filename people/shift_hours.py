"""Planned / worked hours from a person's daily work shift + Iran calendar."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from cases.jalali import gregorian_to_jalali, jalali_to_gregorian

from .iran_holidays import WEEKEND_WEEKDAYS, is_official_holiday
from .work_shift import _DEFAULT_END, _DEFAULT_START, now_local, shift_window

# Accidental disconnect / closed tab: default gap that still counts (10:00).
RECONNECT_GRACE_SECONDS = 10 * 60
DEFAULT_FLOAT_SECONDS = 15 * 60  # 15:00 → 15 minutes

JMONTHS_EN = (
    "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
)


def month_name_en(jm: int) -> str:
    if 1 <= jm <= 12:
        return JMONTHS_EN[jm - 1]
    return str(jm)


def shift_minutes(start: time, end: time) -> int:
    """Length of one working day in minutes (supports overnight shifts)."""
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    if e > s:
        return e - s
    if e == s:
        return 0
    return (24 * 60 - s) + e


def jalali_month_length(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    g1 = date(*jalali_to_gregorian(jy, 12, 1))
    g_next = date(*jalali_to_gregorian(jy + 1, 1, 1))
    return (g_next - g1).days


def _safe_gdate(jy: int, jm: int, jd: int) -> date | None:
    length = jalali_month_length(jy, jm)
    if jd < 1 or jd > length:
        return None
    try:
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        return date(gy, gm, gd)
    except Exception:
        return None


def get_tracking_start() -> date:
    """Gregorian date when shift-hour tracking began on this deployment."""
    from .models import ShiftTrackingConfig

    row = ShiftTrackingConfig.objects.filter(pk=1).first()
    if row is None:
        today = now_local().date()
        row = ShiftTrackingConfig.objects.create(pk=1, started_on=today)
        return today
    return row.started_on


def plan_month(
    jy: int,
    jm: int,
    *,
    start: time | None = None,
    end: time | None = None,
    from_date: date | None = None,
) -> dict[str, Any]:
    """Planned working days/hours for one Jalali month from ``from_date`` onward.

    Days before ``from_date`` (site tracking start) are excluded from planned
    totals. Weekends = Thu+Fri; official holidays on weekdays also excluded.
    """
    start = start or _DEFAULT_START
    end = end or _DEFAULT_END
    per_day = shift_minutes(start, end)
    length = jalali_month_length(jy, jm)
    track_from = from_date if from_date is not None else get_tracking_start()

    weekend_days = 0
    holiday_extra = 0
    working_days = 0
    holiday_list: list[dict] = []
    day_rows: list[dict] = []
    skipped_before = 0

    for jd in range(1, length + 1):
        g = _safe_gdate(jy, jm, jd)
        if g is None:
            continue
        before = g < track_from
        is_weekend = g.weekday() in WEEKEND_WEEKDAYS
        is_holiday = is_official_holiday(jy, jm, jd)
        off = is_weekend or is_holiday
        if before:
            skipped_before += 1
        else:
            if is_weekend:
                weekend_days += 1
            if is_holiday and not is_weekend:
                holiday_extra += 1
                holiday_list.append({"day": jd, "kind": "official"})
            elif is_holiday and is_weekend:
                holiday_list.append({"day": jd, "kind": "official+weekend"})
            if not off:
                working_days += 1
        day_rows.append({
            "jalali_day": jd,
            "gregorian": g.isoformat(),
            "weekday": g.weekday(),
            "weekend": is_weekend,
            "holiday": is_holiday,
            "before_tracking": before,
            "working": (not off) and (not before),
            "planned_minutes": 0 if (off or before) else per_day,
        })

    planned_minutes = working_days * per_day
    return {
        "jalali_year": jy,
        "jalali_month": jm,
        "month_length": length,
        "work_start": start,
        "work_end": end,
        "minutes_per_day": per_day,
        "hours_per_day": round(per_day / 60, 2),
        "weekend_days": weekend_days,
        "official_extra_days": holiday_extra,
        "off_days": weekend_days + holiday_extra,
        "working_days": working_days,
        "skipped_before": skipped_before,
        "planned_minutes": planned_minutes,
        "planned_hours": round(planned_minutes / 60, 2),
        "holidays": holiday_list,
        "days": day_rows,
        "from_date": track_from.isoformat(),
    }


def ensure_month_snapshot(person, *, jy: int | None = None, jm: int | None = None):
    from .models import ShiftMonthSnapshot

    now = now_local()
    if jy is None or jm is None:
        jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)

    start, end = shift_window(person)
    snap, _created = ShiftMonthSnapshot.objects.get_or_create(
        person=person,
        jalali_year=jy,
        jalali_month=jm,
        defaults={
            "work_start": start,
            "work_end": end,
            "planned_minutes": 0,
            "worked_minutes": 0,
            "working_days": 0,
            "weekend_days": 0,
            "holiday_days": 0,
            "frozen": False,
            "meta": {},
        },
    )
    if snap.frozen:
        return snap

    plan = plan_month(jy, jm, start=start, end=end)
    snap.work_start = start
    snap.work_end = end
    snap.planned_minutes = plan["planned_minutes"]
    snap.working_days = plan["working_days"]
    snap.weekend_days = plan["weekend_days"]
    snap.holiday_days = plan["official_extra_days"]
    snap.meta = {
        "holidays": plan["holidays"],
        "hours_per_day": plan["hours_per_day"],
        "month_length": plan["month_length"],
        "from_date": plan["from_date"],
        "skipped_before": plan["skipped_before"],
    }
    snap.save()
    return snap


def freeze_past_months(person) -> int:
    from .models import ShiftMonthSnapshot

    now = now_local()
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    n = 0
    for snap in ShiftMonthSnapshot.objects.filter(person=person, frozen=False):
        if (snap.jalali_year, snap.jalali_month) < (jy, jm):
            snap.worked_minutes = worked_minutes_for_month(
                person, snap.jalali_year, snap.jalali_month,
            )
            snap.overtime_minutes = overtime_minutes_for_month(
                person, snap.jalali_year, snap.jalali_month,
            )
            snap.frozen = True
            snap.save(update_fields=["worked_minutes", "overtime_minutes", "frozen"])
            n += 1
    return n


def worked_minutes_for_month(person, jy: int, jm: int) -> int:
    from .models import ShiftDayLog

    length = jalali_month_length(jy, jm)
    g0 = _safe_gdate(jy, jm, 1)
    g1 = _safe_gdate(jy, jm, length)
    if not g0 or not g1:
        return 0
    track = get_tracking_start()
    start_day = max(g0, track)
    if start_day > g1:
        return 0
    total = (
        ShiftDayLog.objects.filter(person=person, day__gte=start_day, day__lte=g1)
        .values_list("minutes", flat=True)
    )
    return int(sum(total))


def overtime_minutes_for_month(person, jy: int, jm: int) -> int:
    from .models import ShiftDayLog

    length = jalali_month_length(jy, jm)
    g0 = _safe_gdate(jy, jm, 1)
    g1 = _safe_gdate(jy, jm, length)
    if not g0 or not g1:
        return 0
    track = get_tracking_start()
    start_day = max(g0, track)
    if start_day > g1:
        return 0
    total = (
        ShiftDayLog.objects.filter(person=person, day__gte=start_day, day__lte=g1)
        .values_list("overtime_minutes", flat=True)
    )
    return int(sum(total))


def refresh_worked(person, snap=None):
    snap = snap or ensure_month_snapshot(person)
    if snap.frozen:
        return snap
    snap.worked_minutes = worked_minutes_for_month(
        person, snap.jalali_year, snap.jalali_month,
    )
    snap.overtime_minutes = overtime_minutes_for_month(
        person, snap.jalali_year, snap.jalali_month,
    )
    snap.save(update_fields=["worked_minutes", "overtime_minutes"])
    return snap


def apply_shift_change(
    person,
    start: time,
    end: time,
    float_seconds: int | None = None,
    reconnect_grace_seconds: int | None = None,
):
    person.work_start = start
    person.work_end = end
    fields = ["work_start", "work_end", "updated_at"]
    if float_seconds is not None:
        person.float_seconds = max(0, int(float_seconds))
        fields.append("float_seconds")
    if reconnect_grace_seconds is not None:
        person.reconnect_grace_seconds = max(0, int(reconnect_grace_seconds))
        fields.append("reconnect_grace_seconds")
    # Keep unique order for update_fields
    seen = set()
    uniq = []
    for f in fields:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    person.save(update_fields=uniq)
    freeze_past_months(person)
    snap = ensure_month_snapshot(person)
    return refresh_worked(person, snap)


def float_seconds_for(person) -> int:
    raw = getattr(person, "float_seconds", None)
    if raw is None:
        return DEFAULT_FLOAT_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_FLOAT_SECONDS


def reconnect_grace_seconds_for(person) -> int:
    raw = getattr(person, "reconnect_grace_seconds", None)
    if raw is None:
        return RECONNECT_GRACE_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return RECONNECT_GRACE_SECONDS


def format_float_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    mm, ss = divmod(seconds, 60)
    return f"{mm:02d}:{ss:02d}"


def _aware_combine(day: date, t: time, tz) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)


def _credit_gap_minutes(
    log,
    when: datetime,
    *,
    per_day: int,
    grace_seconds: int,
    force_skip: bool = False,
) -> int:
    """Minutes to *add* for a reconnect/presence gap (within grace only).

    Beyond-grace deduction is handled by ``_apply_reconnect_gap``.
    """
    if force_skip or getattr(log, "explicit_logout", False):
        return 0
    if log.last_ping is None:
        return 0
    secs = max(0, int((when - log.last_ping).total_seconds()))
    if secs <= 0:
        return 0
    if secs > max(0, int(grace_seconds)):
        return 0
    mins = max(1, secs // 60) if secs >= 30 else 0
    if mins <= 0:
        return 0
    room = max(0, per_day - int(log.minutes or 0))
    return min(room, mins)


def _apply_reconnect_gap(
    log,
    when: datetime,
    *,
    per_day: int,
    grace_seconds: int,
    force_skip: bool = False,
) -> int:
    """Apply reconnect rules; return minutes to *add* (0 if deducted/skipped).

    - Within grace: credit the away gap as worked time.
    - Beyond grace: subtract the full away duration from worked minutes.
    - Explicit Sign out: no gap credit/deduct (caller starts a fresh +1).
    Each disconnect resets the grace window via the next ``last_ping`` stamp.
    """
    if force_skip or getattr(log, "explicit_logout", False):
        return 0
    if log.last_ping is None:
        return 0
    secs = max(0, int((when - log.last_ping).total_seconds()))
    if secs <= 0:
        return 0
    grace = max(0, int(grace_seconds))
    if secs <= grace:
        return _credit_gap_minutes(
            log, when, per_day=per_day, grace_seconds=grace, force_skip=False,
        )
    # Beyond grace → remove the full away window from already-earned minutes.
    away_mins = max(1, secs // 60)
    log.minutes = max(0, int(log.minutes or 0) - away_mins)
    return 0


def prune_empty_past_snapshots(person) -> int:
    from .models import ShiftDayLog, ShiftMonthSnapshot

    now = now_local()
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    track = get_tracking_start()
    ty, tm, _ = gregorian_to_jalali(track.year, track.month, track.day)
    n = 0
    for snap in ShiftMonthSnapshot.objects.filter(person=person):
        if (snap.jalali_year, snap.jalali_month) >= (jy, jm):
            continue
        # Months entirely before tracking start → drop if empty.
        if (snap.jalali_year, snap.jalali_month) < (ty, tm) and not snap.worked_minutes:
            length = jalali_month_length(snap.jalali_year, snap.jalali_month)
            g0 = _safe_gdate(snap.jalali_year, snap.jalali_month, 1)
            g1 = _safe_gdate(snap.jalali_year, snap.jalali_month, length)
            has_log = False
            if g0 and g1:
                has_log = ShiftDayLog.objects.filter(
                    person=person, day__gte=g0, day__lte=g1,
                ).exists()
            if not has_log:
                snap.delete()
                n += 1
    return n


def note_shift_login(person, *, when: datetime | None = None) -> None:
    """Stamp first login, apply floating-time credit, reconnect grace."""
    from .models import ShiftDayLog

    when = when or now_local()
    gday = when.date()
    if gday < get_tracking_start():
        return
    jy, jm, jd = gregorian_to_jalali(when.year, when.month, when.day)
    if gday.weekday() in WEEKEND_WEEKDAYS or is_official_holiday(jy, jm, jd):
        return

    start, end = shift_window(person)
    per_day = shift_minutes(start, end)
    start_dt = _aware_combine(gday, start, when.tzinfo)

    log, _ = ShiftDayLog.objects.get_or_create(
        person=person, day=gday, defaults={"minutes": 0},
    )

    skip_gap = bool(log.explicit_logout)
    grace = reconnect_grace_seconds_for(person)
    gap = _apply_reconnect_gap(
        log, when, per_day=per_day, grace_seconds=grace, force_skip=skip_gap,
    )
    if log.explicit_logout:
        log.explicit_logout = False

    if when < start_dt:
        # Before shift start — wait for in-window presence; do not start the clock.
        log.save(update_fields=["minutes", "explicit_logout"])
        return

    if log.first_login is None:
        float_secs = float_seconds_for(person)
        when_min = when.replace(second=0, microsecond=0)
        latest_ok = (start_dt + timedelta(seconds=float_secs)).replace(
            second=0, microsecond=0,
        )
        if when_min <= latest_ok:
            log.first_login = start_dt
            credited = int((when_min - start_dt).total_seconds() // 60)
            if when_min == latest_ok and float_secs:
                credited = max(credited, float_secs // 60)
            if credited <= 0:
                credited = 1  # On-time arrival still counts the opening minute.
            log.minutes = min(per_day, int(log.minutes or 0) + credited)
        else:
            # Beyond floating time — shift starts at the actual login minute.
            log.first_login = when_min
            log.minutes = min(per_day, int(log.minutes or 0) + 1)
    elif skip_gap:
        # Fresh session after explicit Sign out — count the reconnect minute only.
        log.minutes = min(per_day, int(log.minutes or 0) + 1)
    elif gap:
        log.minutes = min(per_day, int(log.minutes or 0) + gap)
    # Beyond-grace deduction already applied inside _apply_reconnect_gap.

    log.last_ping = when
    log.save(update_fields=["minutes", "last_ping", "first_login", "explicit_logout"])
    freeze_past_months(person)
    refresh_worked(person)


def note_shift_logout(person, *, when: datetime | None = None, explicit: bool = False) -> None:
    from .models import ShiftDayLog

    when = when or now_local()
    gday = when.date()
    if gday < get_tracking_start():
        return
    log = ShiftDayLog.objects.filter(person=person, day=gday).first()
    if log is None:
        return
    log.last_logout = when
    fields = ["last_logout"]
    if explicit:
        log.explicit_logout = True
        fields.append("explicit_logout")
    log.save(update_fields=fields)


def _fmt_hm(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    local = dt
    if dt.tzinfo is not None:
        local = dt.astimezone(now_local().tzinfo)
    return local.strftime("%H:%M")


_WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _hours1(minutes: int | float) -> float:
    return round(max(0, float(minutes or 0)) / 60.0, 1)


def _excess_hours(done_h: float, plan_h: float) -> float:
    return round(max(0.0, float(done_h or 0) - float(plan_h or 0)), 1)


def _bar_pct(done_h: float, plan_h: float) -> float:
    """Progress 0.0–100.0 with one decimal (for bar fill + label)."""
    plan = float(plan_h or 0)
    if plan <= 0:
        return 0.0
    return round(min(100.0, 100.0 * float(done_h or 0) / plan), 1)


def month_day_details(person, jy: int, jm: int) -> list[dict[str, Any]]:
    """Per-day rows for a month (Ended / Today / Soon / complete tick)."""
    from django.urls import reverse

    from .models import ShiftDayLog
    from .staff_requests import approved_overtime_request_for_day

    start, end = shift_window(person)
    per = shift_minutes(start, end)
    track = get_tracking_start()
    today = now_local().date()
    plan = plan_month(jy, jm, start=start, end=end, from_date=track)
    length = plan["month_length"]
    g0 = _safe_gdate(jy, jm, 1)
    g1 = _safe_gdate(jy, jm, length)
    logs = {}
    if g0 and g1:
        for row in ShiftDayLog.objects.filter(person=person, day__gte=g0, day__lte=g1):
            logs[row.day.isoformat()] = row

    out = []
    for d in plan["days"]:
        g = date.fromisoformat(d["gregorian"])
        log = logs.get(d["gregorian"])
        worked = int(log.minutes) if log else 0
        ot = int(getattr(log, "overtime_minutes", 0) or 0) if log else 0
        planned = d["planned_minutes"]
        total = worked + ot
        is_weekend = bool(d["weekend"])
        is_holiday = bool(d["holiday"])
        is_off = is_weekend or is_holiday
        wd = int(d.get("weekday", g.weekday()))

        if d["before_tracking"]:
            status = "ended"
        elif is_off and g >= track:
            status = "off"
        elif g > today:
            status = "soon"
        elif g == today:
            status = "today"
        elif planned and worked >= planned:
            status = "complete"
        else:
            status = "done"

        login_t = _fmt_hm(log.first_login) if log else "—"
        logout_t = _fmt_hm(log.last_logout) if log and log.last_logout else (
            _fmt_hm(log.last_ping) if log and log.last_ping and g == today else "—"
        )
        if status == "today" and log and log.first_login and not log.last_logout:
            logout_t = "…"
        elif (
            status == "today"
            and log
            and log.first_login
            and getattr(log, "explicit_logout", False)
        ):
            logout_t = _fmt_hm(log.last_logout)

        if is_holiday and is_weekend:
            off_reason = "Holiday · Weekend"
        elif is_holiday:
            off_reason = "Holiday"
        elif is_weekend:
            off_reason = "Weekend"
        else:
            off_reason = ""

        plan_h = round(planned / 60, 1) if planned else 0
        done_h = _hours1(total)
        excess_h = _excess_hours(done_h, plan_h)
        ot_req = approved_overtime_request_for_day(person, g)
        ot_url = reverse("people:request_detail", args=[ot_req.pk]) if ot_req else ""

        out.append({
            "jalali_day": d["jalali_day"],
            "label": f"{d['jalali_day']} {month_name_en(jm)}",
            "weekday": wd,
            "weekday_short": _WEEKDAY_SHORT[wd],
            "status": status,
            "is_off": is_off,
            "is_weekend": is_weekend,
            "is_holiday": is_holiday,
            "off_reason": off_reason,
            "login": login_t,
            "logout": logout_t,
            "worked_hours": done_h,
            "overtime_minutes": ot,
            "excess_hours": excess_h,
            "ot_request_url": ot_url,
            "ot_request_code": (ot_req.request_code if ot_req else "") or "",
            "planned_hours": plan_h,
            "complete": status == "complete",
            "bar_pct": _bar_pct(done_h, plan_h),
        })
    return out


def _month_card_from_snap(person, jy, m, snap, *, status: str) -> dict[str, Any]:
    plan = plan_month(jy, m, start=snap.work_start, end=snap.work_end)
    ot = int(getattr(snap, "overtime_minutes", 0) or 0)
    plan_h = float(snap.planned_hours or 0)
    done_h = round((int(snap.worked_minutes or 0) + ot) / 60, 1)
    excess_h = _excess_hours(done_h, plan_h)
    return {
        "month": m,
        "name": month_name_en(m),
        "label": month_name_en(m),
        "status": status,
        "planned_hours": snap.planned_hours,
        "worked_hours": done_h,
        "overtime_minutes": ot,
        "overtime_hours": round(ot / 60, 1),
        "excess_hours": excess_h,
        "total_hours": done_h,
        "working_days": snap.working_days,
        "off_days": snap.weekend_days + snap.holiday_days,
        "hours_per_day": plan["hours_per_day"],
        "shift_label": f"{snap.work_start.strftime('%H:%M')}–{snap.work_end.strftime('%H:%M')}",
        "bar_pct": _bar_pct(done_h, plan_h),
        "snap": snap,
    }


def year_month_cards(person, jy: int) -> list[dict[str, Any]]:
    """Twelve month cards for ``jy`` (current-year outer grid)."""
    from .models import ShiftMonthSnapshot

    freeze_past_months(person)
    prune_empty_past_snapshots(person)

    now = now_local()
    cy, cm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    track = get_tracking_start()
    ty, tm, _ = gregorian_to_jalali(track.year, track.month, track.day)
    start, end = shift_window(person)

    # Only materialize current month (and keep existing snaps).
    if jy == cy:
        ensure_month_snapshot(person)

    by_m = {
        r.jalali_month: r
        for r in ShiftMonthSnapshot.objects.filter(person=person, jalali_year=jy)
    }
    cards = []
    for m in range(1, 13):
        is_current = (jy, m) == (cy, cm)
        is_future = (jy, m) > (cy, cm)
        is_past = (jy, m) < (cy, cm)
        before_track = (jy, m) < (ty, tm)
        snap = by_m.get(m)

        if before_track and snap is None:
            cards.append({
                "month": m, "name": month_name_en(m), "label": month_name_en(m),
                "status": "idle", "planned_hours": 0, "worked_hours": 0,
                "overtime_hours": 0, "excess_hours": 0,
                "working_days": 0, "off_days": 0,
                "hours_per_day": round(shift_minutes(start, end) / 60, 2),
                "shift_label": "—", "bar_pct": 0.0, "snap": None,
            })
            continue

        if is_current:
            snap = ensure_month_snapshot(person, jy=jy, jm=m)
            refresh_worked(person, snap)
            cards.append(_month_card_from_snap(person, jy, m, snap, status="current"))
            continue

        if snap is not None:
            if is_past and not snap.frozen:
                snap.worked_minutes = worked_minutes_for_month(person, jy, m)
                snap.overtime_minutes = overtime_minutes_for_month(person, jy, m)
                snap.frozen = True
                snap.save(update_fields=["worked_minutes", "overtime_minutes", "frozen"])
            status = "frozen" if snap.frozen else "open"
            cards.append(_month_card_from_snap(person, jy, m, snap, status=status))
            continue

        if is_future:
            plan = plan_month(jy, m, start=start, end=end)
            cards.append({
                "month": m, "name": month_name_en(m), "label": month_name_en(m),
                "status": "upcoming",
                "planned_hours": plan["planned_hours"],
                "worked_hours": 0,
                "overtime_hours": 0,
                "excess_hours": 0,
                "working_days": plan["working_days"],
                "off_days": plan["off_days"],
                "hours_per_day": plan["hours_per_day"],
                "shift_label": f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}",
                "bar_pct": 0.0, "snap": None,
            })
        else:
            # Past month after tracking start with no snap yet — treat as idle.
            cards.append({
                "month": m, "name": month_name_en(m), "label": month_name_en(m),
                "status": "idle", "planned_hours": 0, "worked_hours": 0,
                "overtime_hours": 0, "excess_hours": 0,
                "working_days": 0, "off_days": 0,
                "hours_per_day": round(shift_minutes(start, end) / 60, 2),
                "shift_label": "—", "bar_pct": 0.0, "snap": None,
            })
    return cards


def summarize_year_cards(cards: list[dict[str, Any]], jy: int) -> dict[str, Any]:
    planned = worked = 0.0
    working_days = off_days = 0
    months = []
    for c in cards:
        if c["status"] in ("current", "frozen", "open", "upcoming"):
            planned += float(c["planned_hours"] or 0)
            worked += float(c["worked_hours"] or 0)
            working_days += int(c.get("working_days") or 0)
            off_days += int(c.get("off_days") or 0)
        plan_h = float(c.get("planned_hours") or 0)
        done_h = float(c.get("worked_hours") or 0)
        excess_h = float(c.get("excess_hours") or _excess_hours(done_h, plan_h))
        months.append({
            "month": c["month"],
            "name": c["name"],
            "planned_hours": c["planned_hours"],
            "worked_hours": c["worked_hours"],
            "overtime_hours": c.get("overtime_hours") or 0,
            "excess_hours": excess_h,
            "working_days": c.get("working_days") or 0,
            "off_days": c.get("off_days") or 0,
            "status": c["status"],
            "bar_pct": c.get("bar_pct") if c.get("bar_pct") is not None else _bar_pct(done_h, plan_h),
            "frozen": c["status"] == "frozen",
        })
    planned_r = round(planned, 1)
    worked_r = round(worked, 1)
    excess_r = _excess_hours(worked_r, planned_r)
    return {
        "jalali_year": jy,
        "planned_hours": planned_r,
        "worked_hours": worked_r,
        "overtime_hours": excess_r,
        "excess_hours": excess_r,
        "working_days": working_days,
        "off_days": off_days,
        "months": months,
        "bar_pct": _bar_pct(worked_r, planned_r),
    }


def year_summary(person, jy: int) -> dict[str, Any]:
    return summarize_year_cards(year_month_cards(person, jy), jy)


def year_cards_for_person(person, current_jy: int, *, current_summary=None) -> list[dict[str, Any]]:
    """Year tiles for the shift page (6-per-row grid). Current year first, then past."""
    from .models import ShiftMonthSnapshot

    cards = []
    if current_summary is None:
        current_summary = summarize_year_cards(year_month_cards(person, current_jy), current_jy)
    current = dict(current_summary)
    current["is_current"] = True
    current["frozen"] = False
    cards.append(current)

    past_years = (
        ShiftMonthSnapshot.objects.filter(person=person, jalali_year__lt=current_jy)
        .values_list("jalali_year", flat=True)
        .distinct()
        .order_by("-jalali_year")
    )
    for y in past_years:
        months = year_month_cards(person, y)
        real = [c for c in months if c["status"] in ("frozen", "open", "current")]
        if not real:
            continue
        summary = summarize_year_cards(months, y)
        summary["is_current"] = False
        summary["frozen"] = True
        cards.append(summary)
    return cards


def archived_years(person, current_jy: int) -> list[dict[str, Any]]:
    """Past years only (compat helper)."""
    return [y for y in year_cards_for_person(person, current_jy) if not y.get("is_current")]


def _credit_ot_presence(person, when: datetime) -> int:
    """Accrue actual minutes spent inside the approved overtime extension."""
    from .models import ShiftDayLog
    from .staff_requests import approved_overtime_minutes_for_day

    gday = when.date()
    log, _ = ShiftDayLog.objects.get_or_create(
        person=person, day=gday, defaults={"minutes": 0, "overtime_minutes": 0},
    )
    cap = int(approved_overtime_minutes_for_day(person, gday) or 0)
    if cap <= 0:
        return int(log.minutes or 0)

    grace = reconnect_grace_seconds_for(person)
    add = 0
    if log.last_ping is None:
        add = 1
    else:
        secs = max(0, int((when - log.last_ping).total_seconds()))
        if secs <= max(0, int(grace)):
            if secs >= 30:
                add = max(1, secs // 60)
            elif secs >= 20:
                add = 1
        # Beyond grace during OT: do not credit the away gap as overtime.

    room = max(0, cap - int(log.overtime_minutes or 0))
    add = min(room, add)
    if add:
        log.overtime_minutes = int(log.overtime_minutes or 0) + add
    log.last_ping = when
    log.save(update_fields=["overtime_minutes", "last_ping"])
    freeze_past_months(person)
    refresh_worked(person)
    return int(log.minutes or 0)


def record_presence_ping(person, *, when: datetime | None = None) -> int:
    from .models import ShiftDayLog

    when = when or now_local()
    gday = when.date()
    if gday < get_tracking_start():
        return 0

    start, end = shift_window(person)
    now_t = when.time().replace(microsecond=0)
    in_base = (
        (start <= end and start <= now_t < end)
        or (start > end and (now_t >= start or now_t < end))
    )
    if not in_base:
        # Approved overtime keeps the session alive; credit actual presence
        # into overtime_minutes (capped at approved OT for the day).
        try:
            from .staff_requests import (
                approved_overtime_minutes_for_day,
                is_within_extended_window,
            )
            if is_within_extended_window(person, when):
                return _credit_ot_presence(person, when)
        except Exception:
            pass
        return 0

    jy, jm, jd = gregorian_to_jalali(when.year, when.month, when.day)
    if gday.weekday() in WEEKEND_WEEKDAYS or is_official_holiday(jy, jm, jd):
        return 0

    per_day = shift_minutes(start, end)
    start_dt = _aware_combine(gday, start, when.tzinfo)
    log, _ = ShiftDayLog.objects.get_or_create(
        person=person, day=gday, defaults={"minutes": 0},
    )

    opened = False
    # First in-window stamp (covers early login before start).
    if log.first_login is None:
        opened = True
        float_secs = float_seconds_for(person)
        when_min = when.replace(second=0, microsecond=0)
        latest_ok = (start_dt + timedelta(seconds=float_secs)).replace(
            second=0, microsecond=0,
        )
        if when_min <= latest_ok:
            log.first_login = start_dt
            credited = int((when_min - start_dt).total_seconds() // 60)
            if when_min == latest_ok and float_secs:
                credited = max(credited, float_secs // 60)
            log.minutes = min(per_day, int(log.minutes or 0) + max(0, credited))
        else:
            log.first_login = when_min

    skip_gap = bool(log.explicit_logout)
    if log.explicit_logout:
        log.explicit_logout = False

    if opened:
        # Float credit already covers late-within-grace minutes; only add the
        # current minute when nothing was credited yet (on-time or late).
        add = 1 if int(log.minutes or 0) == 0 else 0
    else:
        grace = reconnect_grace_seconds_for(person)
        if skip_gap:
            add = 1  # Fresh session after explicit Sign out.
        elif log.last_ping is None:
            add = 1
        else:
            secs = max(0, int((when - log.last_ping).total_seconds()))
            if secs <= max(0, int(grace)):
                add = _credit_gap_minutes(
                    log, when, per_day=per_day, grace_seconds=grace, force_skip=False,
                )
                if add <= 0 and 20 <= secs < 30:
                    add = 1
            else:
                # Beyond grace → subtract full away duration; do not credit gap.
                away_mins = max(1, secs // 60)
                log.minutes = max(0, int(log.minutes or 0) - away_mins)
                add = 0

    log.minutes = min(per_day, max(0, int(log.minutes or 0)) + add)
    log.last_ping = when
    log.save(update_fields=["minutes", "last_ping", "first_login", "explicit_logout"])
    freeze_past_months(person)
    refresh_worked(person)
    return log.minutes
