"""Template context processors shared across the whole site."""
from .theming import DEFAULT_THEME, theme_for_unit


def theme(request):
    """Expose the active unit theme + unit code to every template.

    Admins (or anonymous users) get the neutral administration theme. Logged-in
    unit members get their own unit's accent colours.
    """
    unit_code = "ADMIN"
    inbox_count = 0
    fx_stale = False
    nav_roles = []
    show_person_requests = False
    staff_request_count = 0
    is_gm_nav = False
    person_request_answers = 0
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        try:
            from people.role_nav import build_nav_roles, user_has_gm_access, work_context
            is_gm_nav = user_has_gm_access(user)
            nav_roles = build_nav_roles(request, user)
            # Refresh profile after possible active-role sync.
            profile = getattr(user, "profile", None)
            ctx = work_context(request, user)
            if ctx.role is not None and (ctx.role.unit or ""):
                unit_code = ctx.role.unit
            elif profile is not None and not profile.is_admin:
                unit_code = profile.unit or "ADMIN"
        except Exception:
            nav_roles = []
            if profile is not None and not profile.is_admin:
                unit_code = profile.unit or "ADMIN"
            try:
                from people.role_nav import user_has_gm_access
                is_gm_nav = user_has_gm_access(user)
            except Exception:
                is_gm_nav = bool(profile and profile.is_general_manager)
        if not nav_roles and profile is not None and not profile.is_admin and not is_gm_nav:
            unit_code = profile.unit or "ADMIN"
        if is_gm_nav and not nav_roles:
            unit_code = "ADMIN"
        try:
            from cases.services import inbox_cases_for_request
            inbox_count = inbox_cases_for_request(request).count()
        except Exception:
            inbox_count = 0
        # Commercial manager or Admin: warn when FX board is older than 24h / empty.
        try:
            if profile is not None and (
                getattr(profile, "is_admin", False)
                or (getattr(profile, "is_manager", False)
                    and getattr(profile, "unit", None) == "COMMERCIAL")
            ):
                from cases.fx_rates import is_rates_stale
                fx_stale = bool(is_rates_stale())
        except Exception:
            fx_stale = False
        show_person_requests = False
        person_request_answers = 0
        # Person-scoped Requests tab (not under any seat accordion).
        try:
            if profile is not None and not profile.is_admin:
                from people.work_shift import person_for_user
                from people import staff_requests as sr
                person = person_for_user(user)
                show_person_requests = person is not None
                if person is not None:
                    total = 0
                    for t in sr.access_types_for_person(person):
                        total += sr.unread_decided_count(person, t.code)
                    person_request_answers = total
        except Exception:
            show_person_requests = False
            person_request_answers = 0
        # GM unread staff-request badge (new submits not yet opened).
        try:
            if is_gm_nav or (profile is not None and profile.is_admin):
                from people.staff_requests import unread_pending_count
                staff_request_count = int(unread_pending_count() or 0)
        except Exception:
            staff_request_count = 0
    return {
        "active_unit_code": unit_code,
        "unit_theme": theme_for_unit(unit_code),
        "nav_inbox_count": inbox_count,
        "nav_fx_stale": fx_stale,
        "nav_roles": nav_roles,
        "nav_show_person_requests": show_person_requests,
        "nav_staff_request_count": staff_request_count,
        "nav_is_general_manager": is_gm_nav,
        "nav_person_request_answers": person_request_answers,
    }


def tool_data_access(request):
    """Expose tool-data role flags and navigation helpers to every template."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    try:
        from itemcoder.tool_access import (
            can_delete_tool_data,
            can_manage_group_data,
            can_manage_prices,
            is_supply_manager,
            is_technical_manager,
            tool_data_home_url_name,
        )
    except Exception:
        return {}
    return {
        "td_can_delete": can_delete_tool_data(user),
        "td_can_manage_prices": can_manage_prices(user),
        "td_can_manage_group_data": can_manage_group_data(user),
        "td_is_supply_manager": is_supply_manager(user),
        "td_is_technical_manager": is_technical_manager(user),
        "td_home_url_name": tool_data_home_url_name(user),
    }


def impersonation_status(request):
    """Expose whether an admin is currently viewing the platform as someone
    else, so base.html can show the "return to admin" banner.

    Reads straight from the session rather than checking any permission on
    the current user — deliberately, since the whole point is that the
    banner must still appear even while request.user resolves to a low-
    privilege account that couldn't see most of the rest of the admin UI.
    """
    session = getattr(request, "session", None)
    if session is None:
        return {}
    impersonator_id = session.get("impersonator_id")
    if not impersonator_id:
        return {}
    return {
        "is_impersonating": True,
        "impersonator_username": session.get("impersonator_username", ""),
    }
