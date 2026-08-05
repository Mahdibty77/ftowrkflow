"""Kick a user out when their work shift ends mid-session."""
from urllib.parse import urlencode

from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


class WorkShiftMiddleware:
    """Outside shift → goodbye screen, then login (admin / GM exempt).

    Previously this logged the user out and redirected straight to login, so the
    designed end-of-shift overlay never appeared. HTML requests now go to the
    goodbye page; the presence ping is allowed through so the open tab can show
    the same overlay without a hard navigation race.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            # While impersonating, the real admin is driving — do not kick them.
            if not request.session.get("impersonator_id"):
                path = request.path or ""
                if not self._is_exempt_path(path):
                    try:
                        from people.work_shift import shift_exempt, shift_status

                        if not shift_exempt(user):
                            st = shift_status(user)
                            if not st["allowed"]:
                                return self._end_shift(request, st)
                    except Exception:
                        pass
        return self.get_response(request)

    @staticmethod
    def _is_exempt_path(path: str) -> bool:
        return (
            path.startswith("/static/")
            or path.startswith("/media/")
            or path.startswith("/accounts/login")
            or path.startswith("/accounts/logout")
            or path.startswith("/people/shift/ping")
            or path.startswith("/people/shift/ended")
        )

    @staticmethod
    def _end_shift(request, st: dict):
        name = (st.get("name") or "colleague").strip() or "colleague"
        is_ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or "")
        )
        logout(request)
        if is_ajax:
            return JsonResponse({
                "ok": False,
                "allowed": False,
                "shift_ended": True,
                "seconds_left": 0,
                "name": name,
            })
        qs = urlencode({"n": name})
        return redirect(f"{reverse('people:shift_ended')}?{qs}")
