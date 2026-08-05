"""Role-based access for the coding-tool data admin pages.

Platform administrators (``Profile.is_admin``) have full access including
delete/wipe.  Supply managers may manage price lists (create & edit, no bulk
clear).  Technical managers may browse features and offers per group (add &
edit, no delete/wipe).
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from accounts.constants import Role, Unit


def _profile(user):
    return getattr(user, "profile", None) if getattr(user, "is_authenticated", False) else None


def is_platform_admin(user) -> bool:
    p = _profile(user)
    return bool(p and p.is_admin)


def is_supply_manager(user) -> bool:
    p = _profile(user)
    return bool(p and not p.is_admin and p.unit == Unit.SUPPLY and p.role == Role.MANAGER)


def is_technical_manager(user) -> bool:
    p = _profile(user)
    return bool(p and not p.is_admin and p.unit == Unit.TECHNICAL and p.role == Role.MANAGER)


def can_manage_prices(user) -> bool:
    return is_platform_admin(user) or is_supply_manager(user)


def can_manage_group_data(user) -> bool:
    return is_platform_admin(user) or is_technical_manager(user)


def can_delete_tool_data(user) -> bool:
    """Only the platform administrator may wipe groups or remove saved values."""
    return is_platform_admin(user)


def tool_data_home_url_name(user) -> str:
    if is_platform_admin(user):
        return "dm_home"
    if is_supply_manager(user):
        return "dm_price_lists"
    if is_technical_manager(user):
        return "dm_technical_home"
    return "cases:inbox"


def admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_platform_admin(request.user):
            messages.error(request, "Only administrators can manage tool data.")
            return redirect("cases:inbox")
        return view(request, *args, **kwargs)
    return wrapped


def price_access_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage_prices(request.user):
            messages.error(request, "You do not have access to pricing management.")
            return redirect("cases:inbox")
        return view(request, *args, **kwargs)
    return wrapped


def group_data_access_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage_group_data(request.user):
            messages.error(request, "You do not have access to coding reference data.")
            return redirect("cases:inbox")
        return view(request, *args, **kwargs)
    return wrapped
