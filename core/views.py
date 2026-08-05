"""Core landing view that routes each user to the right starting screen."""
import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect

from accounts.constants import Role

logger = logging.getLogger(__name__)


@login_required
def home(request):
    """Send the user to their primary workspace.

    Admins go to the Django admin-style management console; unit members go to
    their kartabl (inbox).
    """
    profile = getattr(request.user, "profile", None)
    if profile is None or profile.is_admin:
        return redirect("accounts:admin_console")
    # General managers and unit supervisors are report-only -> dashboard home.
    if profile.is_general_manager or getattr(profile, "role", "") == Role.SUPERVISOR:
        return redirect("reports:dashboard")
    return redirect("cases:inbox")


# ---------------------------------------------------------------------------
# Media access rules
# ---------------------------------------------------------------------------
# Everything under MEDIA_ROOT is served by protected_media below, and every
# request is matched against exactly one rule here. The default is deny: a path
# that matches no rule is refused rather than served, so a future module that
# starts writing files under a new prefix cannot accidentally publish them by
# forgetting to say who may read them. Adding a prefix here is a deliberate,
# reviewable act.
#
# "shared" means any signed-in user. It is the correct answer only for files
# that are already visible to everyone by other means — signatures and stamps
# are embedded in every exported document, which circulates across all three
# units, so the raw image carries nothing the document does not.
#
# "owner" means the person the file belongs to, plus platform administrators.
# Ownership is read from the path itself (…/<user-id>/<filename>), which is how
# the existing upload paths are already laid out. Profile photos under avatars/
# use the same owner rule so only the owner (and admins) can load the image.
_MEDIA_SHARED = ("signatures/", "stamps/")
# avatars/ follows the same layout as personnel/: avatars/<user-id>/<file>
_MEDIA_OWNER_SCOPED = ("personnel/", "avatars/")


def _media_access_granted(request, rel_path: str) -> bool:
    """Whether ``request.user`` may read ``rel_path`` (relative to MEDIA_ROOT)."""
    rel = str(rel_path).replace("\\", "/").lstrip("/")
    profile = getattr(request.user, "profile", None)
    is_admin = bool(profile and profile.is_admin)

    if is_admin:
        return True

    if rel.startswith(_MEDIA_SHARED):
        return True

    if rel.startswith(_MEDIA_OWNER_SCOPED):
        # Layout is "<prefix>/<owner-id>/<filename>". Anything shaped
        # differently — a personnel module that later files things under a
        # non-numeric folder, say — is refused rather than guessed at, and
        # stays administrators-only until a rule is written for it.
        parts = rel.split("/")
        if len(parts) >= 3 and parts[1].isdigit():
            return int(parts[1]) == request.user.pk
        return False

    return False


@login_required
def protected_media(request, path):
    """Serve a file from MEDIA_ROOT, if this user is allowed to read it.

    Replaces django.conf.urls.static.static() in the root URLConf, which —
    deliberately, by Django's own design — contributes zero URL patterns once
    DEBUG is off. Since this deployment correctly always runs with DEBUG=0 in
    production, that meant every file under MEDIA_URL (signature and stamp
    images today) has been returning a 404 since go-live, invisible-but-broken
    rather than loudly broken.

    The first version of this fix required only that the requester be signed
    in. That is the right rule for signatures and stamps and the wrong rule for
    everything the Personnel module is about to store: a national ID scan, a
    résumé, a personnel photo. Upload paths are predictable by construction
    (…/<user-id>/<filename>), so "signed in is enough" meant any account could
    read any colleague's documents by walking the ids. The rule table above
    replaces that with per-prefix rules and a default of deny.

    A refused file returns 404 rather than 403 — deliberately, so that probing
    cannot be used to learn which files exist.

    Still open, and a deployment change rather than a code one: the file bytes
    are streamed by the application itself, so a large download occupies a
    worker for its whole duration. Handing the transfer to the front web server
    after this check passes is the remaining half of this fix.
    """
    root = Path(settings.MEDIA_ROOT).resolve()
    try:
        target = (root / path).resolve()
        target.relative_to(root)  # raises ValueError if path escaped MEDIA_ROOT
    except (ValueError, RuntimeError):
        raise Http404("Not found.")
    if not target.is_file():
        raise Http404("Not found.")

    # Decide on the RESOLVED path, never on the raw request string. The two are
    # not the same: a request for "signatures/%2e%2e/pipe.sqlite3" arrives here
    # already percent-decoded, so the raw string still begins with the allowed
    # "signatures/" prefix while the file it actually resolves to sits
    # somewhere else entirely. Matching the raw string would hand any signed-in
    # user everything under MEDIA_ROOT — and would defeat the owner scoping
    # too, via "personnel/<my-id>/../<their-id>/...". The containment check
    # above already proves the resolved path is inside MEDIA_ROOT; taking the
    # relative form of that same resolved path is what makes the rules mean
    # what they say.
    rel_path = target.relative_to(root).as_posix()
    if not _media_access_granted(request, rel_path):
        logger.info("Denied media request from user %s for %s", request.user.pk, rel_path)
        raise Http404("Not found.")

    content_type, _ = mimetypes.guess_type(str(target))
    # Open via Path so the handle stays tied to a real on-disk file; set
    # Content-Length explicitly so browsers do not treat an empty stream as OK.
    fh = target.open("rb")
    response = FileResponse(fh, content_type=content_type or "application/octet-stream")
    try:
        response["Content-Length"] = str(target.stat().st_size)
    except OSError:
        pass
    return response
