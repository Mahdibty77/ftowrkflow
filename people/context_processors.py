"""Template context for the work-shift countdown banner."""

_WARN_SECONDS = 30 * 60


def work_shift_banner(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if getattr(request, "session", None) and request.session.get("impersonator_id"):
        return {}
    try:
        from django.urls import reverse

        from .work_shift import (
            display_first_name,
            shift_ended_message,
            shift_status,
        )

        st = shift_status(user)
        # Admin / GM have no work shift — never show the Sign out shift dialog.
        signout_confirm = not bool(st.get("exempt"))
        ctx = {
            "shift_warn": False,
            "shift_seconds_left": None,
            "shift_end_name": display_first_name(user, st.get("person")),
            "shift_goodbye": shift_ended_message(user),
            "shift_ping_url": "",
            "shift_signout_confirm": signout_confirm,
            "shift_overtime_link": "",
            "shift_overtime_hint": False,
            "shift_warn_message": "",
            "shift_end_label": "",
            "shift_minutes_left": None,
            "shift_timer_label": "",
            "shift_mins_word": "minutes",
        }
        if st.get("exempt"):
            return ctx
        if not st.get("allowed"):
            return ctx

        # Authoritative remaining time: always raw seconds from shift_status.
        seconds_left = st.get("seconds_left")
        if seconds_left is not None:
            seconds_left = max(0, int(seconds_left))

        ctx["shift_ping_url"] = reverse("people:shift_ping")
        ctx["shift_seconds_left"] = seconds_left

        person = st.get("person")
        ot_link = ""
        try:
            from .models import RequestType
            from .staff_requests import person_has_access
            if person_has_access(person, RequestType.CODE_OVERTIME):
                ot_link = reverse("people:overtime_form")
        except Exception:
            ot_link = ""
        ctx["shift_overtime_link"] = ot_link
        ctx["shift_overtime_hint"] = bool(ot_link)

        end = st.get("effective_end") or st.get("end")
        if end is not None and hasattr(end, "strftime"):
            ctx["shift_end_label"] = end.strftime("%H:%M")

        if seconds_left is not None:
            minutes_disp = (seconds_left + 59) // 60 if seconds_left else 0
            ctx["shift_minutes_left"] = minutes_disp
            ctx["shift_mins_word"] = "minute" if minutes_disp == 1 else "minutes"
            ctx["shift_timer_label"] = _format_timer(seconds_left)
            ctx["shift_warn"] = seconds_left <= _WARN_SECONDS
            # Plain countdown only — OT sentence/link live in separate DOM nodes
            # so client JS can never wipe them while refreshing the timer.
            if minutes_disp == 1:
                ctx["shift_warn_message"] = "1 minute left until your cartable closes"
            else:
                ctx["shift_warn_message"] = (
                    f"{minutes_disp} minutes left until your cartable closes"
                )
        return ctx
    except Exception:
        return {}


def _format_timer(total_seconds: int) -> str:
    total = max(0, int(total_seconds))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"
