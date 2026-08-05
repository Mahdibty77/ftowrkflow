"""Activation screen.

Deliberately requires NO authentication: a locked application must still be
unlockable.  Shows the machine fingerprint (so the customer can send it to the
seller) and the current validity, and accepts the pasted license string.
"""

from __future__ import annotations

from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from . import service


@require_http_methods(["GET", "POST"])
@csrf_protect
def activate(request):
    status = service.current_status(force=True)
    message = None
    error = None

    if request.method == "POST":
        license_string = (request.POST.get("license_key") or "").strip()
        if not license_string:
            error = "Please paste the license string into the box below."
        else:
            ok, msg = service.activate(license_string)
            if ok:
                # Re-read fresh status and, if now valid, bounce to the app.
                status = service.current_status(force=True)
                if status.ok:
                    return redirect(reverse("core:home"))
                message = msg
            else:
                error = msg
        status = service.current_status(force=True)

    context = {
        "machine_id": status.machine_id,
        "status": status,
        "message": message,
        "error": error,
    }
    return render(request, "licensing/activate.html", context)
