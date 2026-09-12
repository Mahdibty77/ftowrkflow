"""Auth hooks for work-shift day login/logout stamps.

Connected from ``PeopleConfig.ready``. Every successful sign-in stamps the
person's ``ShiftDayLog`` via ``shift_hours.note_shift_login`` and every sign-out
closes it via ``note_shift_logout``; those two functions hold all the accrual
rules, and ``people.shift_hours`` explains the model they implement.

The one judgement made here is when NOT to stamp. An administrator using "log
in as user" fires the same auth signals as the real person would, so
impersonation must not write attendance for someone who is not at their desk —
``_is_impersonating`` catches both ends of that (starting the impersonation, and
returning from it). ``_safe_person`` is the other filter: it yields nothing for
an account that is exempt from the shift gate at all (administrators, the
General Manager) or that has no linked ``Person`` to stamp against.
``_explicit_sign_out`` separately distinguishes a person
clicking Sign out from a session simply ending, because the two mean different
things to the reconnect grace.
"""
from django.contrib.auth.signals import user_logged_in, user_logged_out


def _is_impersonating(request) -> bool:
    if request is None:
        return False
    if getattr(request, "_ft_skip_shift_stamp", False):
        return True
    try:
        session = getattr(request, "session", None)
        if session is not None and session.get("impersonator_id"):
            return True
    except Exception:
        pass
    return False


def _safe_person(user):
    try:
        from .work_shift import person_for_user, shift_exempt
        if shift_exempt(user):
            return None
        return person_for_user(user)
    except Exception:
        return None


def _explicit_sign_out(request) -> bool:
    """True only when the person confirmed Sign out from the profile menu."""
    if request is None:
        return False
    try:
        if (request.POST.get("explicit_shift_end") or "").strip() == "1":
            return True
    except Exception:
        pass
    return False


def on_user_logged_in(sender, request, user, **kwargs):
    # Admin/GM "Log in as" must never stamp or accrue the target's shift.
    if _is_impersonating(request):
        return
    person = _safe_person(user)
    if person is None:
        return
    try:
        from .shift_hours import note_shift_login
        note_shift_login(person)
    except Exception:
        pass
    _stamp_missing_eod_reports(request, person)


def _stamp_missing_eod_reports(request, person) -> None:
    """Compute this person's own unfiled past-day end-of-day reports once,
    right here at login, and park the answer on the session.

    Why the session, and not a fresh query every request: see
    people.eod_reports's own module docstring — the per-request gate
    (EndOfDayReportGateMiddleware) needs to be free (a session-dict read,
    already-loaded for every authenticated request) rather than a query on
    its own, and login is the one moment this answer can change without the
    person doing anything, so it is the one moment worth paying for.
    """
    if request is None:
        return
    try:
        from .eod_reports import missing_report_days
        missing = missing_report_days(person)
        request.session["ft_eod_missing"] = [d.isoformat() for d in missing]
    except Exception:
        pass


def on_user_logged_out(sender, request, user, **kwargs):
    if user is None:
        return
    if _is_impersonating(request):
        return
    person = _safe_person(user)
    if person is None:
        return
    try:
        from .shift_hours import note_shift_logout
        note_shift_logout(person, explicit=_explicit_sign_out(request))
    except Exception:
        pass


def connect():
    user_logged_in.connect(on_user_logged_in, dispatch_uid="people.shift_login")
    user_logged_out.connect(on_user_logged_out, dispatch_uid="people.shift_logout")
