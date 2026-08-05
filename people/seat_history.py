"""Build a seat History timeline from SeatEventLog (+ created date)."""
from __future__ import annotations

from django.utils import timezone

from people.models import SeatEventLog, format_jalali


def _jalali_dt(value) -> str:
    if not value:
        return ""
    try:
        local = timezone.localtime(value)
        d = format_jalali(local.date())
        if not d:
            return ""
        return f"{d.replace('/', '.')} {local.strftime('%H:%M')}"
    except Exception:
        return ""


def _event_entry(ev) -> dict:
    """Structured timeline row with optional visual person flow."""
    fr = (ev.from_person_name or "").strip()
    to = (ev.to_person_name or "").strip()
    n = (ev.payload or {}).get("count")
    actor_name = ""
    if ev.actor_id:
        try:
            from cases.services import _person_display_name
            actor_name = _person_display_name(ev.actor) or (ev.actor.username or "")
        except Exception:
            actor_name = ev.actor.username or ""

    base = {
        "at": ev.created_at,
        "at_jalali": _jalali_dt(ev.created_at),
        "event": ev.event,
        "actor": actor_name,
        "source": "event",
        "from_name": fr,
        "to_name": to,
        "flow": None,
        "flow_icon": "",
        "badge": "",
        "summary": "",
    }

    if ev.event == SeatEventLog.CREATED:
        base["label"] = "Created"
        base["summary"] = "This seat account was created."
    elif ev.event == SeatEventLog.ASSIGNED:
        base["label"] = "Assigned"
        base["flow"] = "assign"
        base["flow_icon"] = "fa-link"
        base["to_name"] = to or "—"
    elif ev.event == SeatEventLog.TRANSLATED:
        base["label"] = "Translated"
        base["flow"] = "translate"
        base["flow_icon"] = "fa-right-left"
        base["badge"] = "Substitute"
        base["from_name"] = fr or "—"
        base["to_name"] = to or "—"
    elif ev.event == SeatEventLog.RETURNED:
        base["label"] = "Returned"
        base["flow"] = "return"
        base["flow_icon"] = "fa-rotate-left"
        base["badge"] = "Owner"
        base["from_name"] = fr or "—"
        base["to_name"] = to or "—"
    elif ev.event == SeatEventLog.DELEGATED:
        base["label"] = "Delegated"
        base["flow"] = "delegate"
        base["flow_icon"] = "fa-share-from-square"
        base["badge"] = f"{n} task(s)" if n else "Tasks"
        base["from_name"] = fr or "—"
        base["to_name"] = to or "—"
    elif ev.event == SeatEventLog.CLOSED:
        base["label"] = "Closed"
        base["summary"] = (
            f"Seat closed and freed back to the catalogue"
            + (f" (was held by {fr})." if fr else ".")
        )
    elif ev.event == SeatEventLog.VACANT:
        base["label"] = "Vacant"
        base["summary"] = "Seat is vacant" + (f" — last holder {fr}." if fr else ".")
    else:
        base["label"] = ev.event.title()
        base["summary"] = ""
    return base


def build_seat_timeline(seat_user) -> list[dict]:
    """Chronological timeline entries (newest first) for one seat User."""
    entries = []

    for ev in SeatEventLog.objects.filter(source_user=seat_user).select_related(
        "actor", "from_person", "to_person",
    ):
        entries.append(_event_entry(ev))

    joined = getattr(seat_user, "date_joined", None)
    if joined and not any(e["event"] == SeatEventLog.CREATED for e in entries):
        entries.append({
            "at": joined,
            "at_jalali": _jalali_dt(joined),
            "event": SeatEventLog.CREATED,
            "label": "Created",
            "summary": "This seat account was created.",
            "actor": "",
            "source": "created",
            "from_name": "",
            "to_name": "",
            "flow": None,
            "flow_icon": "",
            "badge": "",
        })

    entries.sort(key=lambda e: (e["at"] or timezone.now(),), reverse=True)
    return entries
