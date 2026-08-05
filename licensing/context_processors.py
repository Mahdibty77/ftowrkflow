"""Expose the current license status (and a role-gated warning flag) to every
template, including the Django admin.
"""
from . import service


def license_status(request):
    try:
        status = service.current_status()
    except Exception:  # noqa: BLE001 - never break page rendering over licensing
        return {}

    show_kartabl_warning = False
    user = getattr(request, "user", None)
    if status.show_warning and user is not None and getattr(user, "is_authenticated", False):
        profile = getattr(user, "profile", None)
        is_admin = bool(getattr(user, "is_superuser", False) or getattr(profile, "is_admin", False))
        is_gm = bool(getattr(profile, "is_general_manager", False))
        show_kartabl_warning = is_admin or is_gm

    return {
        "license_status": status,
        "license_show_kartabl_warning": show_kartabl_warning,
    }
