"""Request gate that enforces the license on every page.

Placed near the end of ``MIDDLEWARE`` — second to last, with
``accounts.MustChangePasswordMiddleware`` after it — because it needs
``request.user``, which only exists below ``AuthenticationMiddleware``. That is
the whole constraint: this gate never touches ``django.contrib.messages``, so
sitting under ``MessageMiddleware`` is simply where the end of Django's own
stack falls, not a requirement of its own. It runs before the password gate so
that an unlicensed install sends everyone to activation rather than hiding the
problem behind a password prompt.

When the software is not in a valid licensed state every request is redirected
to the activation page, except for an allow-list that must stay reachable while
locked:

    * the activation page itself (so it can be used to unlock)
    * static files (so the activation page is styled)

The expensive work is done by :func:`service.current_status`, which caches its
result, so this middleware adds negligible per-request cost.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from . import service


class LicenseGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._activation_url: str | None = None

    # -- helpers -----------------------------------------------------------
    def _activation_path(self) -> str:
        if self._activation_url is None:
            try:
                self._activation_url = reverse("licensing:activate")
            except Exception:  # noqa: BLE001 - URLConf not ready yet
                self._activation_url = "/activate/"
        return self._activation_url

    def _is_allowlisted(self, path: str) -> bool:
        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        if static_url and path.startswith(static_url):
            return True
        if path.startswith(self._activation_path()):
            return True
        return False

    # -- entry point -------------------------------------------------------
    def __call__(self, request):
        if self._is_allowlisted(request.path):
            # The activation page carries no authentication of its own, because a
            # locked application must stay unlockable. Once the licence is valid
            # that justification is gone, and leaving it open would hand any
            # passer-by the machine fingerprint and the exact expiry date, so on a
            # licensed system it is only shown to someone who has signed in.
            # Renewal still works: an expired licence re-locks the app, and the
            # branch below reopens the page to everyone again.
            if (
                request.path.startswith(self._activation_path())
                and service.current_status().ok
            ):
                user = getattr(request, "user", None)
                if user is not None and not user.is_authenticated:
                    return redirect(settings.LOGIN_URL)
            return self.get_response(request)

        if service.current_status().ok:
            return self.get_response(request)

        # Locked: send everything to the activation screen.
        return redirect(self._activation_path())
