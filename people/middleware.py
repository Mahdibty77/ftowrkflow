"""Kick a user out when their work shift ends mid-session."""
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class WorkShiftMiddleware:
    """Outside shift → log out (admin / GM exempt; impersonation stays)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            # While impersonating, the real admin is driving — do not kick them.
            if not request.session.get("impersonator_id"):
                path = request.path or ""
                # Never interrupt login/logout/static/media.
                if not (
                    path.startswith("/static/")
                    or path.startswith("/media/")
                    or path.startswith("/accounts/login")
                    or path.startswith("/accounts/logout")
                ):
                    try:
                        from people.work_shift import (
                            shift_ended_message, shift_exempt, shift_status,
                        )
                        if not shift_exempt(user):
                            st = shift_status(user)
                            if not st["allowed"]:
                                msg = shift_ended_message(user)
                                logout(request)
                                messages.warning(request, msg)
                                return redirect(reverse("accounts:login"))
                    except Exception:
                        pass
        return self.get_response(request)
