"""Platform-wide gate: an account with must_change_password set can reach
nothing except the password-change screen itself, until that's done.

Mirrors licensing.middleware.LicenseGateMiddleware exactly (same idea, a
different gate) — placed after it in settings.MIDDLEWARE, so an invalid
license still takes priority and sends everyone to activation regardless of
password state.

Handling this centrally, rather than only redirecting at the moment of login,
closes a real gap: a login that arrives via a "next" deep link (e.g. a
bookmarked page hit after a session was ended by a cut-off or a password
reset) would otherwise land straight on that page instead of being routed
through the change-password screen first.
"""
from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class MustChangePasswordMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._target_path: str | None = None

    def _target(self) -> str:
        if self._target_path is None:
            try:
                self._target_path = reverse("accounts:force_password_change")
            except Exception:  # noqa: BLE001 - URLConf not ready yet
                self._target_path = "/accounts/force-password-change/"
        return self._target_path

    def _is_allowlisted(self, path: str) -> bool:
        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
        if path.startswith(static_url) or path.startswith(media_url):
            return True
        if path == self._target():
            return True
        # Always allow signing out — a user who wants to bail out of the
        # forced-change screen without setting a new password can still do
        # that, they just can't reach anything else first.
        try:
            if path == reverse("accounts:logout"):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            profile = getattr(user, "profile", None)
            if (profile is not None and profile.must_change_password
                    and not self._is_allowlisted(request.path)):
                return redirect(self._target())
        return self.get_response(request)
