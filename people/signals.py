"""Auth hooks for work-shift day login/logout stamps."""
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
