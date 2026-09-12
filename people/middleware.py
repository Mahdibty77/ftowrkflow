"""Kick a user out when their work shift ends mid-session, and separately
gate further access behind a missing end-of-day report."""
from urllib.parse import urlencode

from django.conf import settings
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
            # The deferred-logout end-of-day report page (see
            # people.views.eod_report's own docstring): showEnd()/the manual
            # sign-out handler in core/templates/base.html now send a
            # session that has already reached its end here INSTEAD of
            # logging out immediately, specifically so this exact page can
            # still render and be submitted. Without this exemption this
            # same middleware would catch that page's own next request and
            # end the session before the view ever ran, which is the one
            # thing the whole deferred-logout design exists to avoid.
            or path.startswith("/people/shift/eod-report")
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


class EndOfDayReportGateMiddleware:
    """Platform-wide gate: a login with an unfiled PAST end-of-day report can
    reach nothing except the report page itself, until it is filed.

    Mirrors accounts.middleware.MustChangePasswordMiddleware exactly — same
    idea (redirect, never logout, so the person can still finish and
    continue), a different condition. Placed after WorkShiftMiddleware in
    settings.MIDDLEWARE so a shift that is actively ending right now still
    gets that flow first; placed after MustChangePasswordMiddleware so a
    forced password change still takes priority over this.

    THE CONDITION IS A SESSION READ, NOT A QUERY — see
    people.eod_reports's own module docstring for why: the real
    "which past days are missing" query runs exactly once, at login
    (people.signals._stamp_missing_eod_reports), and again on submit; this
    middleware, which runs on every single authenticated request, only ever
    reads the list that login already parked on the session (already
    deserialized for every authenticated request regardless — SessionMiddleware's
    own job), so the hard non-negotiable "no measurable slowdown" requirement
    this whole feature was built under costs nothing extra here.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._target_path: str | None = None

    def _target(self) -> str:
        if self._target_path is None:
            try:
                self._target_path = reverse("people:eod_report")
            except Exception:  # noqa: BLE001 - URLConf not ready yet
                self._target_path = "/people/shift/eod-report/"
        return self._target_path

    def _is_allowlisted(self, path: str) -> bool:
        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
        if path.startswith(static_url) or path.startswith(media_url):
            return True
        if path.startswith(self._target()):
            return True
        # Same reasoning as MustChangePasswordMiddleware's own allowlist: a
        # person who wants to bail out without filing can still sign out.
        try:
            if path == reverse("accounts:logout") or path.startswith("/accounts/login"):
                return True
        except Exception:  # noqa: BLE001
            pass
        # licensing.middleware.LicenseGateMiddleware (which runs BEFORE this
        # one) lets its own activation page pass through even while the
        # license is invalid — so an unlicensed install can still be
        # unlocked. If THIS middleware redirected that page away instead of
        # also exempting it, the two would bounce the request back and forth
        # forever: activate -> (still missing) -> eod-report -> (still
        # unlicensed) -> activate. Found by testing this exact middleware
        # against a local install with no valid license.
        try:
            if path.startswith(reverse("licensing:activate")):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if not request.session.get("impersonator_id"):
                if not self._is_allowlisted(request.path or ""):
                    missing = request.session.get("ft_eod_missing")
                    if missing:
                        return redirect(self._target())
        return self.get_response(request)
