"""Template context for the work-shift countdown banner."""


def work_shift_banner(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if getattr(request, "session", None) and request.session.get("impersonator_id"):
        return {}
    try:
        from datetime import datetime, timedelta

        from django.urls import reverse

        from .work_shift import (
            countdown_message,
            display_first_name,
            now_local,
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
        }
        if st.get("exempt"):
            return ctx
        if not st.get("allowed"):
            return ctx

        end = st.get("effective_end") or st.get("end")
        start = st.get("start")
        when = now_local()
        seconds_left = None
        if end is not None:
            # Prefer full datetime from shift_status when OT extends past midnight.
            end_dt = datetime.combine(when.date(), end, tzinfo=when.tzinfo)
            if start and start > end and when.time() >= start:
                end_dt += timedelta(days=1)
            # If OT minutes push past base end, shift_status already folded that
            # into minutes_left — recompute from minutes_left when available.
            if st.get("minutes_left") is not None:
                seconds_left = max(0, int(st["minutes_left"]) * 60)
            else:
                seconds_left = max(0, int((end_dt - when).total_seconds()))

        ctx["shift_ping_url"] = reverse("people:shift_ping")
        ctx["shift_seconds_left"] = seconds_left
        if st.get("warn") and st.get("minutes_left") is not None:
            msg = countdown_message(st["minutes_left"])
            person = st.get("person")
            ot_link = ""
            try:
                from .staff_requests import person_has_access
                from .models import RequestType
                if person_has_access(person, RequestType.CODE_OVERTIME):
                    ot_link = reverse("people:overtime_form")
                    msg = (
                        f"{msg}. If you need more time, you can submit an Overtime request."
                    )
            except Exception:
                ot_link = ""
            ctx.update({
                "shift_warn": True,
                "shift_minutes_left": st["minutes_left"],
                "shift_warn_message": msg,
                "shift_end_label": (
                    end.strftime("%H:%M") if hasattr(end, "strftime") else ""
                ),
                "shift_overtime_link": ot_link,
                "shift_overtime_hint": bool(ot_link),
            })
        return ctx
    except Exception:
        return {}
