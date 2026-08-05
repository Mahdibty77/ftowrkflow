@login_required
@admin_required
def person_shift(request, pk):
    """Edit daily work-shift hours + monthly/yearly hour reports."""
    from datetime import datetime as dt
    from datetime import time as dtime

    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh
    from .work_shift import shift_window

    person = get_object_or_404(Person, pk=pk)
    start, end = shift_window(person)
    sh.prune_empty_past_snapshots(person)
    sh.freeze_past_months(person)
    current = sh.ensure_month_snapshot(person)
    sh.refresh_worked(person, current)

    hour_choices = [f"{i:02d}" for i in range(24)]
    minute_choices = [f"{i:02d}" for i in range(60)]

    now = sh.now_local()
    jy, jm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    try:
        view_year = int(request.GET.get("year") or jy)
    except (TypeError, ValueError):
        view_year = jy
    if view_year > jy:
        view_year = jy

    month_cards = sh.year_month_cards(person, view_year)
    year = sh.summarize_year_cards(month_cards, view_year)
    year_cards = sh.year_cards_for_person(
        person, jy, current_summary=(year if view_year == jy else None),
    )
    for c in year_cards:
        c["is_selected"] = c["jalali_year"] == view_year

    tracking_start = sh.get_tracking_start()
    ty, tm, td = gregorian_to_jalali(
        tracking_start.year, tracking_start.month, tracking_start.day,
    )

    def _shift_ctx(s: dtime, e: dtime, *, pending=False):
        return {
            "person": person,
            "work_start": s.strftime("%H:%M"),
            "work_end": e.strftime("%H:%M"),
            "start_h": f"{s.hour:02d}",
            "start_m": f"{s.minute:02d}",
            "end_h": f"{e.hour:02d}",
            "end_m": f"{e.minute:02d}",
            "hour_choices": hour_choices,
            "minute_choices": minute_choices,
            "pending_confirm": pending,
            "current": current,
            "month_cards": month_cards,
            "year": year,
            "year_cards": year_cards,
            "report_year": view_year,
            "tracking_label": f"{td} {sh.month_name_en(tm)} {ty}",
            "hours_per_day": round(sh.shift_minutes(s, e) / 60, 2),
        }

    def _parse_posted_time(prefix: str):
        raw = (request.POST.get(prefix) or "").strip()
        if raw:
            return dt.strptime(raw, "%H:%M").time()
        hh = (request.POST.get(f"{prefix}_h") or "").strip()
        mm = (request.POST.get(f"{prefix}_m") or "").strip()
        return dt.strptime(f"{hh}:{mm}", "%H:%M").time()

    if request.method == "POST":
        step = (request.POST.get("confirm_step") or "").strip()
        try:
            new_start = _parse_posted_time("work_start")
            new_end = _parse_posted_time("work_end")
        except ValueError:
            messages.error(request, "Please enter valid start and end times (HH:MM).")
            return redirect("people:person_shift", pk=person.pk)
        if new_start == new_end:
            messages.error(request, "Start and end times must be different.")
            return redirect("people:person_shift", pk=person.pk)

        if step != "2":
            return render(
                request,
                "people/person_shift.html",
                _shift_ctx(new_start, new_end, pending=True),
            )

        sh.apply_shift_change(person, new_start, new_end)
        messages.success(
            request,
            f"Work shift for {person.display_name} saved "
            f"({new_start.strftime('%H:%M')}–{new_end.strftime('%H:%M')}). "
            f"Only the current month's planned hours were updated.",
        )
        return redirect("people:person_shift", pk=person.pk)

    return render(request, "people/person_shift.html", _shift_ctx(start, end))


@login_required
@admin_required
def person_shift_month(request, pk, year, month):
    """Day cards for one Jalali month."""
    from cases.jalali import gregorian_to_jalali

    from . import shift_hours as sh

    person = get_object_or_404(Person, pk=pk)
    month = int(month)
    year = int(year)
    if month < 1 or month > 12:
        messages.error(request, "Invalid month.")
        return redirect("people:person_shift", pk=person.pk)

    sh.freeze_past_months(person)
    days = sh.month_day_details(person, year, month)
    now = sh.now_local()
    cy, cm, _ = gregorian_to_jalali(now.year, now.month, now.day)
    cards = sh.year_month_cards(person, year)
    month_card = next((c for c in cards if c["month"] == month), None)

    return render(request, "people/person_shift_month.html", {
        "person": person,
        "jalali_year": year,
        "jalali_month": month,
        "month_name": sh.month_name_en(month),
        "days": days,
        "month_card": month_card,
        "is_current_month": (year, month) == (cy, cm),
    })
