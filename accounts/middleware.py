"""Two unrelated per-request middlewares that both live on accounts.Profile:

    LanguageMiddleware          - activates the signed-in person's own saved
                                  platform CHROME language for this request.
    MustChangePasswordMiddleware - the forced password-change gate.

They are kept in one module because both are small, both read nothing but
``request.user.profile``, and both belong to accounts the same way
people.middleware.WorkShiftMiddleware belongs to people — see that module for
the pattern this one follows.
"""
from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation


class LanguageMiddleware:
    """Activates English or Persian for this request, from the signed-in
    person's own ``Profile.language`` — never from a cookie, a session key,
    or the browser's Accept-Language header.

    Why not Django's own ``django.middleware.locale.LocaleMiddleware``: that
    one is built around exactly the per-device signals this feature must NOT
    use — a session key it writes via ``django.views.i18n.set_language``, a
    fallback cookie, and finally the browser's own Accept-Language header. Any
    of those would make the language follow the *browser*, not the *person* -
    log in from a colleague's machine, or a fresh browser with a different
    Accept-Language, and the setting would silently appear to have reset. The
    owner was explicit that this has to follow the person across devices, so
    the one and only source of truth is the ``Profile`` row (already synced to
    every device the moment it's saved, being an ordinary database column),
    and the simplest correct implementation is to read it directly and call
    ``translation.activate()`` ourselves — exactly the "small per-request
    middleware reading request.user.profile cheaply" shape
    ``people.middleware.WorkShiftMiddleware`` already established here, not a
    new convention.

    Placement (settings.MIDDLEWARE): immediately after
    AuthenticationMiddleware, because ``request.user`` must already be
    resolved, and before every other project middleware and the view itself,
    so that anything they go on to render — a redirect target's page, a
    ``messages.success()`` flash — already comes out in the language this
    request is entitled to. There is exactly one extra per-request cost this
    adds beyond what the app already pays: none. ``request.user.profile`` is a
    cheap FK lookup this codebase already performs on most authenticated
    requests (see WorkShiftMiddleware, MustChangePasswordMiddleware below, and
    most view functions) — Django caches it on the User instance for the life
    of the request, so reading ``.language`` off an already-fetched profile
    costs nothing further, and ``translation.activate()`` plus the gettext
    catalog lookup it enables are Django's own built-in, in-memory, no-I/O
    machinery (the whole point of ``USE_I18N``), not something added here.

    An anonymous request (no ``request.user``, or an authenticated user with
    no ``Profile`` yet — cannot normally happen in this app, but costs nothing
    to guard) falls back to ``settings.LANGUAGE_CODE``'s language cleanly.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = None
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            profile = getattr(user, "profile", None)
            if profile is not None:
                language = (profile.language or "").strip() or None
        # Every request reaches this line and calls activate() unconditionally
        # (even the fallback case), which is what makes this safe under a
        # threaded server: translation.activate() only sets thread-local
        # state, so if the previous request handled on this same worker
        # thread belonged to a different person, this call overwrites that
        # leftover state before the view ever runs — nothing here depends on
        # cleanup happening at the end of the previous request.
        translation.activate(language or settings.LANGUAGE_CODE)
        # Conventional Django attribute (what LocaleMiddleware itself sets) —
        # harmless to provide and lets any template/admin code that expects it
        # work exactly as it would under the stock middleware.
        request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)


class MustChangePasswordMiddleware:
    """Platform-wide gate: an account with must_change_password set can reach
    nothing except the password-change screen itself, until that's done.

    Mirrors licensing.middleware.LicenseGateMiddleware exactly (same idea, a
    different gate) — placed after it in settings.MIDDLEWARE, so an invalid
    license still takes priority and sends everyone to activation regardless
    of password state.

    Handling this centrally, rather than only redirecting at the moment of
    login, closes a real gap: a login that arrives via a "next" deep link
    (e.g. a bookmarked page hit after a session was ended by a cut-off or a
    password reset) would otherwise land straight on that page instead of
    being routed through the change-password screen first.
    """

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
