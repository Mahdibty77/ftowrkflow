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
    # Esfand is 29 or 30 days, decided by where the next new year falls. A year
    # far outside the calendar's range (a hand-typed /shift/<year>/ URL) has no
    # Gregorian date to compare against, so answer the ordinary 29 rather than
    # letting the page die on a ValueError; every day of such a month is then
    # dropped by _safe_gdate anyway.
    try:
        g1 = date(*jalali_to_gregorian(jy, 12, 1))
        g_next = date(*jalali_to_gregorian(jy + 1, 1, 1))
    except (ValueError, OverflowError):
        return 29
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


def _shift_start_dt(start: time, end: time, when: datetime) -> datetime:
    """When the shift that ``when`` falls in actually started.

    An overnight shift (22:00–06:00) is still yesterday's shift once the clock
    passes midnight. Anchoring on ``when.date()`` alone put its start ~20 hours
    in the future, which made every post-midnight arrival look early: the login
    was never opened and the stamped In time was tonight's start, not the
    person's real arrival. Mirrors the same correction in work_shift.
    """
    start_dt = _aware_combine(when.date(), start, when.tzinfo)
    if start > end and when.time() < end:
        start_dt -= timedelta(days=1)
    return start_dt


def _credit_gap_minutes(
    log,
    when: datetime,
    *,
    per_day: int,
    grace_seconds: int,
    force_skip: bool = False,
) -> int:
    """Minutes to *add* for a reconnect/presence gap (within grace only).

    Beyond-grace gaps are handled by ``_apply_reconnect_gap``: they add nothing
    and are only written to the audit column.
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
    # The open tab pings every few seconds, so one gap is almost never a whole
    # minute. Judging each gap on its own threw those seconds away and credited
    # a person nothing for a whole day at the desk; the seconds are banked on
    # the day row instead and spent the moment they make up a minute, so what is
    # credited follows the time actually spent present.
    banked = int(getattr(log, "carry_seconds", 0) or 0) + secs
    mins = banked // 60
    log.carry_seconds = banked % 60
    if mins <= 0:
        return 0
    room = max(0, per_day - int(log.minutes or 0))
    return min(room, mins)


def _is_working_day(g: date) -> bool:
    """True when the shift is actually expected to be worked on ``g``."""
    if g.weekday() in WEEKEND_WEEKDAYS:
        return False
    jy, jm, jd = gregorian_to_jalali(g.year, g.month, g.day)
    return not is_official_holiday(jy, jm, jd)


# An absence can never be booked for more than one day's planned length (see
# ``_record_away``), so anything older than this cannot change the answer. The
# bound stops a stale ``last_ping`` — a person back after a month's leave — from
# turning the day-by-day scan below into a walk over the whole calendar.
_AWAY_SCAN_DAYS = 32


def _in_shift_seconds(start: time, end: time, lo: datetime, hi: datetime) -> int:
    """Seconds of the interval ``[lo, hi)`` that fall inside the working window.

    The audit column answers "how much of the shift did this person miss", so
    only the part of an absence that lands inside the shift may count. Without
    this clipping the gap between a 16:59 ping and the 20:00 login that the
    shift middleware then blocks booked three evening hours nobody was expected
    to be present for — ``note_shift_login`` fires on *every* successful
    authentication, so that was ordinary operation, not an edge case.

    Each occurrence of the shift is anchored on the date it *starts* and allowed
    to run past midnight, so an overnight 22:00–06:00 shift is one interval
    rather than two halves and a gap straddling midnight is a plain
    intersection like any other. Days the person does not work (weekend,
    official holiday) contribute nothing.
    """
    if start == end:
        return 0  # Zero-length shift — shift_minutes() already calls this 0.
    if lo.tzinfo is not None and hi.tzinfo is not None:
        # ``last_ping`` comes back from the database in UTC while ``when`` is
        # local; the window times are wall-clock, so both ends are read in the
        # same local zone the rest of this module anchors on.
        lo = lo.astimezone(hi.tzinfo)
    if hi <= lo:
        return 0
    floor_lo = hi - timedelta(days=_AWAY_SCAN_DAYS)
    if lo < floor_lo:
        lo = floor_lo

    total = 0
    # Start one day early: an overnight window opened yesterday can still be
    # running when the gap begins.
    day = lo.date() - timedelta(days=1)
    last = hi.date()
    while day <= last:
        if _is_working_day(day):
            begin = _aware_combine(day, start, hi.tzinfo)
            finish = _aware_combine(day, end, hi.tzinfo)
            if end < start:
                finish += timedelta(days=1)
            lo_i = max(lo, begin)
            hi_i = min(hi, finish)
            if hi_i > lo_i:
                total += int((hi_i - lo_i).total_seconds())
        day += timedelta(days=1)
    return total


def _record_away(
    log,
    prev: datetime | None,
    when: datetime,
    *,
    start: time,
    end: time,
    per_day: int,
) -> None:
    """Write a beyond-grace absence onto the day row (audit only, never a cost).

    An absence is already paid for by the minutes it did not earn: no ping
    arrives while the person is gone, so nothing is credited for that window.
    Booking it a second time — which is what the old
    ``log.minutes -= secs // 60`` did — charged a 17-minute absence 34 minutes.
    The owner asked for exactly its own duration, once, so the deduction is gone
    and only the record remains.

    What this column does and does not mean — read this before building a
    report on it:

        away_minutes == the in-window shift credit lost to absences that both
        BEGAN and ENDED while the person was being observed, and
        0 <= away_minutes <= the day's planned length.

    Two things follow from the first half. Only the in-window part of a gap
    counts — time outside the shift was never going to be credited, so nothing
    was lost there. And the running total is capped at the day's planned length,
    because a day cannot lose more credit than it could ever have earned.

    The important limit is in the words "began and ended". An absence is only
    ever booked when a later ping or sign-in closes it, so the two commonest
    shapes of a short day record NOTHING here: someone who signs in at 10:00 on
    an 08:00 shift, and someone who works to 15:00 and goes home. Both lose
    around two hours of credit and both leave away_minutes at 0, because no gap
    was ever closed. So (minutes + away_minutes) reconciles against a full day
    only when every absence was bracketed by presence; for a late start or an
    early finish the shortfall shows up in ``minutes`` alone.

    Read it as "time away between two observed presences", not as "credit lost".
    Closing that gap would mean booking the head and tail of the shift as well,
    which is a different feature — attendance rather than reconnect accounting —
    and it is not what this column was added for.

    Absences accumulate across the day because a day can hold several, and the
    question being answered later is "how much of this day was the person
    away", not "how long was the worst gap". Whole minutes are rounded down, to
    match the credit that was actually lost — the earlier floor of one minute
    per absence rounded *up* instead, which is precisely the over-report the
    column is being fixed for, and it broke the reconciliation above.
    """
    if prev is None:
        return
    away = _in_shift_seconds(start, end, prev, when) // 60
    if away <= 0:
        return
    ceiling = max(0, int(per_day))
    log.away_minutes = min(
        ceiling, int(getattr(log, "away_minutes", 0) or 0) + away,
    )


def _apply_reconnect_gap(
    log,
    when: datetime,
    *,
    start: time,
    end: time,
    per_day: int,
    grace_seconds: int,
    force_skip: bool = False,
) -> int:
    """Apply reconnect rules; return minutes to *add* (0 if away/skipped).

    - Within grace: credit the away gap as worked time.
    - Beyond grace: credit nothing, and record the away time for the audit.
      Worked minutes already earned are left alone — see ``_record_away``.
    - Explicit Sign out: no gap credit (caller starts a fresh +1).
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
    # Beyond grace → the window earns nothing, which is the whole of its cost;
    # note down the part of it that fell inside the shift and leave the minutes
    # earned before the absence untouched.
    _record_away(log, log.last_ping, when, start=start, end=end, per_day=per_day)
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
    start_dt = _shift_start_dt(start, end, when)

    log, _ = ShiftDayLog.objects.get_or_create(
        person=person, day=gday, defaults={"minutes": 0},
    )

    skip_gap = bool(log.explicit_logout)
    grace = reconnect_grace_seconds_for(person)
    gap = _apply_reconnect_gap(
        log, when, start=start, end=end, per_day=per_day, grace_seconds=grace,
        force_skip=skip_gap,
    )
    if log.explicit_logout:
        log.explicit_logout = False

    if when < start_dt:
        # Before shift start — wait for in-window presence; do not start the clock.
        # carry_seconds and away_minutes go with it: the gap helper may already
        # have banked the sub-minute remainder of this reconnect, or booked the
        # absence, onto the row.
        #
        # last_ping is advanced here for the same reason it is advanced on the
        # normal path: a gap that has been measured must not be measurable a
        # second time. This branch used to return without touching it, so every
        # later sign-in re-measured the same interval from the same stale stamp
        # and booked the same absence again — on an overnight shift, six routine
        # sign-ins turned half an hour away into a reported six hours, climbing
        # until it hit the per-day clamp. Advancing the stamp credits nothing
        # (the clock still has not started); it only stops the same absence
        # being counted more than once.
        log.last_ping = when
        log.save(update_fields=[
            "minutes", "last_ping", "explicit_logout", "carry_seconds",
            "away_minutes",
        ])
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
    # A beyond-grace gap adds nothing here; _apply_reconnect_gap has already
    # recorded it on the row and deliberately left the earned minutes alone.

    log.last_ping = when
    log.save(update_fields=[
        "minutes", "last_ping", "first_login", "explicit_logout",
        "carry_seconds", "away_minutes",
    ])
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
    from .staff_requests import approved_overtime_requests_by_day

    start, end = shift_window(person)
    per = shift_minutes(start, end)
    track = get_tracking_start()
    today = now_local().date()
    plan = plan_month(jy, jm, start=start, end=end, from_date=track)
    length = plan["month_length"]
    g0 = _safe_gdate(jy, jm, 1)
    g1 = _safe_gdate(jy, jm, length)
    logs = {}
    ot_by_day = {}
    if g0 and g1:
        for row in ShiftDayLog.objects.filter(person=person, day__gte=g0, day__lte=g1):
            logs[row.day.isoformat()] = row
        # Day rows are already batched; the overtime request was the one lookup
        # left running once per calendar day.
        ot_by_day = approved_overtime_requests_by_day(person, g0, g1)

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
        ot_req = ot_by_day.get(g)
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
            # Same banked-seconds rule as the base shift: a few seconds per ping
            # never reaches a minute on its own, so it is carried until it does.
            banked = int(getattr(log, "carry_seconds", 0) or 0) + secs
            add = banked // 60
            log.carry_seconds = banked % 60
        # Beyond grace during OT: do not credit the away gap as overtime.

    room = max(0, cap - int(log.overtime_minutes or 0))
    add = min(room, add)
    if add:
        log.overtime_minutes = int(log.overtime_minutes or 0) + add
    log.last_ping = when
    log.save(update_fields=["overtime_minutes", "last_ping", "carry_seconds"])
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
    start_dt = _shift_start_dt(start, end, when)
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
            else:
                # Beyond grace → the gap is not credited, and nothing already
                # earned is taken back either; the lost credit is the cost. Same
                # rule as ``_apply_reconnect_gap`` on the login path.
                _record_away(
                    log, log.last_ping, when,
                    start=start, end=end, per_day=per_day,
                )
                add = 0

    log.minutes = min(per_day, max(0, int(log.minutes or 0)) + add)
    log.last_ping = when
    log.save(update_fields=[
        "minutes", "last_ping", "first_login", "explicit_logout",
        "carry_seconds", "away_minutes",
    ])
    freeze_past_months(person)
    refresh_worked(person)
    return log.minutes
