"""Work-shift access window for a person's login.

Admin and General Manager accounts are never gated. Everyone else with a linked
Person uses that person's daily start/end times (defaults 08:00–17:00).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

_DEFAULT_START = time(8, 0)
_DEFAULT_END = time(17, 0)


def _tz():
    name = getattr(settings, "TIME_ZONE", None) or "Asia/Tehran"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Tehran")


def now_local() -> datetime:
    from django.utils import timezone
    return timezone.now().astimezone(_tz())


def person_for_user(user):
    """Primary Person linked to this login, if any."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        from .models import PersonAccount
        link = (
            PersonAccount.objects.select_related("person")
            .filter(user_id=user.pk)
            .order_by("assigned_at", "pk")
            .first()
        )
        return link.person if link else None
    except Exception:
        return None


def shift_exempt(user) -> bool:
    """Platform admin and general manager are always allowed."""
    if user is None:
        return True
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return bool(profile.is_admin or profile.is_general_manager)


def shift_window(person) -> tuple[time, time]:
    start = getattr(person, "work_start", None) if person is not None else None
    end = getattr(person, "work_end", None) if person is not None else None
    if start is not None and end is not None:
        return (start, end)
    try:
        from accounts.models import PlatformConfig
        cfg = PlatformConfig.load()
        return (
            start or cfg.default_work_start or _DEFAULT_START,
            end or cfg.default_work_end or _DEFAULT_END,
        )
    except Exception:
        return (start or _DEFAULT_START, end or _DEFAULT_END)


def display_first_name(user, person=None) -> str:
    person = person if person is not None else person_for_user(user)
    if person is not None:
        name = (person.first_name_en or person.first_name or "").strip()
        if name:
            return name.split()[0]
    if user is not None:
        name = (user.first_name or "").strip()
        if name:
            return name.split()[0]
        return (user.username or "colleague").strip() or "colleague"
    return "colleague"


def _in_window(now_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_t < end
    # Overnight shift (rare): e.g. 22:00–06:00
    return now_t >= start or now_t < end


def shift_status(user, *, when: datetime | None = None) -> dict:
    """Return access status for ``user`` at ``when`` (local now by default).

    When the person has approved overtime for the day, the window stays open
    until shift end + total approved overtime minutes.
    """
    when = when or now_local()
    if when.tzinfo is not None:
        when = when.astimezone(_tz())
    now_t = when.time().replace(microsecond=0)

    if shift_exempt(user):
        return {
            "allowed": True,
            "exempt": True,
            "person": None,
            "minutes_left": None,
            "seconds_left": None,
            "warn": False,
            "start": None,
            "end": None,
            "name": display_first_name(user),
            "overtime_extension": False,
            "effective_end": None,
        }

    person = person_for_user(user)
    start, end = shift_window(person)
    in_base = _in_window(now_t, start, end)

    # Effective end = shift end (+ overnight) + approved OT for the day.
    end_dt = datetime.combine(when.date(), end, tzinfo=when.tzinfo)
    if start > end and now_t >= start:
        end_dt += timedelta(days=1)
    ot_minutes = 0
    if person is not None:
        try:
            from .staff_requests import approved_overtime_minutes_for_day
            ot_minutes = approved_overtime_minutes_for_day(person, when.date())
        except Exception:
            ot_minutes = 0
    if ot_minutes:
        end_dt = end_dt + timedelta(minutes=ot_minutes)

    start_dt = datetime.combine(when.date(), start, tzinfo=when.tzinfo)
    if start > end and now_t < end:
        start_dt -= timedelta(days=1)

    allowed = in_base or (ot_minutes > 0 and start_dt <= when < end_dt)
    # After normal end but still in OT extension.
    in_ot_extension = bool(ot_minutes and allowed and not in_base)

    seconds_left = None
    minutes_left = None
    warn = False
    if allowed:
        seconds_left = max(0, int((end_dt - when).total_seconds()))
        # Ceil-minutes so "30:00" still counts as the 30-minute warning window.
        minutes_left = (seconds_left + 59) // 60 if seconds_left else 0
        warn = seconds_left <= 30 * 60

    return {
        "allowed": allowed,
        "exempt": False,
        "person": person,
        "minutes_left": minutes_left,
        "seconds_left": seconds_left,
        "warn": warn and allowed,
        "start": start,
        "end": end,
        "name": display_first_name(user, person),
        "overtime_extension": in_ot_extension,
        "effective_end": end_dt.time().replace(microsecond=0) if allowed else end,
        "overtime_minutes_today": ot_minutes,
    }


def outside_shift_login_message(user) -> str:
    st = shift_status(user)
    name = st["name"]
    start = st["start"] or _DEFAULT_START
    end = st["end"] or _DEFAULT_END
    return (
        f"Dear {name}, you are outside your work shift "
        f"({start.strftime('%H:%M')}–{end.strftime('%H:%M')}). "
        f"Please sign in during your assigned hours."
    )


def shift_ended_message(user) -> str:
    name = display_first_name(user)
    return (
        f"Dear {name}, your work shift has ended. "
        f"Hope you had a good day — see you next shift."
    )


def countdown_message(minutes_left: int) -> str:
    m = max(0, int(minutes_left))
    if m == 1:
        return "1 minute left until your cartable closes"
    return f"{m} minutes left until your cartable closes"
