"""End-of-day report lifecycle: filing one, and detecting past days a person
never filed one for.

Two callers, two different questions:

  * people.signals.on_user_logged_in asks missing_report_days() once, right
    at login, and parks the answer on the session (see that module) so the
    per-request gate (people.middleware.EndOfDayReportGateMiddleware) never
    has to run this query itself — a real query on every single request,
    forever, for every shift-tracked person, is exactly the "measurable
    slowdown" the owner ruled out when this feature was designed. Login is
    the one moment "does this person owe a report" can change without the
    person doing anything (a new calendar day starting), so it is the one
    moment worth spending a real query on.
  * people.views.eod_report asks it again on submit, to decide whether more
    catch-up days remain — a rare page view, not a hot path, so a fresh
    query there costs nothing worth avoiding.

LOOKBACK_DAYS bounds both queries so a long-dormant account (weeks of no
login) cannot make either one, or the catch-up flow's own "day 1 of N"
sequence, grow without limit.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

LOOKBACK_DAYS = 30


def missing_report_days(person, *, today: date | None = None) -> list[date]:
    """Past (strictly before ``today``) tracked shift days ``person`` has
    not filed an end-of-day report for, oldest first.

    "Tracked" means a ShiftDayLog row with minutes > 0 — a day they were
    actually present for, not merely a calendar date. "Past" is deliberately
    ``< today``, never ``<= today``: today's own shift, if it has already
    ended, is the LIVE flow's job (see people.views.eod_report's own
    docstring), not a catch-up — the same line the product owner drew
    explicitly (a same-day reconnect only ever owes the presence-gap
    explanation, never this).
    """
    if person is None:
        return []
    from .models import EndOfDayReport, ShiftDayLog

    today = today or timezone.localdate()
    since = today - timedelta(days=LOOKBACK_DAYS)
    tracked_days = set(
        ShiftDayLog.objects.filter(
            person=person, day__gte=since, day__lt=today, minutes__gt=0,
        ).values_list("day", flat=True)
    )
    if not tracked_days:
        return []
    filed_days = set(
        EndOfDayReport.objects.filter(
            person=person, work_day__in=tracked_days,
        ).values_list("work_day", flat=True)
    )
    return sorted(tracked_days - filed_days)


def submit_report(person, work_day: date, *, requests_for_supervisor, ideas_for_today):
    """File (or refile) ``person``'s report for ``work_day``.

    Each of the two field groups arrives as a raw list of strings (one per
    "+"-added row on the form, in order, including possible blanks from an
    empty trailing row) and is trimmed down to just the non-blank ones here
    — the one place both the live and catch-up submit paths funnel through.
    """
    from .models import EndOfDayReport

    requests_for_supervisor = [s.strip() for s in (requests_for_supervisor or []) if s and s.strip()]
    ideas_for_today = [s.strip() for s in (ideas_for_today or []) if s and s.strip()]
    obj, _created = EndOfDayReport.objects.update_or_create(
        person=person, work_day=work_day,
        defaults={
            "requests_for_supervisor": requests_for_supervisor,
            "ideas_for_today": ideas_for_today,
        },
    )
    return obj
