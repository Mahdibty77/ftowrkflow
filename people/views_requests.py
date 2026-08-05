"""Views for personnel Request types / Requests / Overtime / GM queue."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Person, RequestType, StaffRequest
from . import staff_requests as sr
from .views import admin_required


def _is_gm(user) -> bool:
    try:
        from .role_nav import user_has_gm_access
        if user_has_gm_access(user):
            return True
    except Exception:
        pass
    p = getattr(user, "profile", None)
    return bool(p and (p.is_general_manager or p.is_admin))


gm_required = user_passes_test(_is_gm, login_url="accounts:login")


def _person_or_redirect(request):
    person = sr.person_for_request_user(request.user)
    if person is None:
        messages.error(request, "No personnel record is linked to this login.")
        return None
    return person


def _history_rows(qs):
    return sr.history_rows_enriched(qs)


# ---------------------------------------------------------------------------
# Admin — Request types + Assign
# ---------------------------------------------------------------------------
@login_required
@admin_required
def request_types(request):
    types = sr.active_request_types()
    for t in types:
        t.assigned_count = t.person_access.count()
    return render(request, "people/request_types.html", {
        "request_types": types,
    })


@login_required
@admin_required
def request_type_assign(request, type_id):
    rt = get_object_or_404(RequestType, pk=type_id)
    try:
        from .constants import PersonStatus
        people = Person.objects.filter(status=PersonStatus.ACTIVE).order_by(
            "last_name_en", "first_name_en", "last_name", "first_name", "pk",
        )
    except Exception:
        people = Person.objects.order_by("pk")

    granted = sr.granted_person_ids(rt)

    if request.method == "POST":
        raw = request.POST.getlist("person_ids")
        try:
            ids = [int(x) for x in raw]
        except ValueError:
            messages.error(request, "Invalid person selection.")
            return redirect("people:request_type_assign", type_id=rt.pk)
        added, removed = sr.set_access_for_type(rt, ids, granted_by=request.user)
        messages.success(
            request,
            f"Access for {rt.title} updated (+{added} / −{removed}).",
        )
        return redirect("people:request_types")

    rows = []
    for p in people:
        rows.append({
            "person": p,
            "checked": p.pk in granted,
            "label": p.display_name,
            "code": p.detail_code or "",
            "username": p.username or "",
        })
    return render(request, "people/request_type_assign.html", {
        "request_type": rt,
        "rows": rows,
        "granted_count": len(granted),
    })


@login_required
@admin_required
def person_access(request, pk):
    person = get_object_or_404(Person, pk=pk)
    granted_types = sr.access_types_for_person(person)
    cards = [
        {
            "type": t,
            "assign_url": reverse("people:request_type_assign", args=[t.pk]),
        }
        for t in granted_types
    ]
    return render(request, "people/person_access.html", {
        "person": person,
        "access_cards": cards,
    })


# ---------------------------------------------------------------------------
# Employee — Requests hub + Overtime
# ---------------------------------------------------------------------------
@login_required
def my_requests(request):
    person = _person_or_redirect(request)
    if person is None:
        return redirect("core:home")
    types = sr.access_types_for_person(person)
    type_cards = []
    answered_total = 0
    for t in types:
        seen_ids = sr.seen_ids_from_session(request.session, t.code)
        unread = sr.unread_decided_count(person, t.code, seen_ids=seen_ids)
        answered_total += unread
        type_cards.append({
            "type": t,
            "answered_count": unread,
            "url": reverse("people:overtime_form") if t.code == RequestType.CODE_OVERTIME else "",
        })
    return render(request, "people/my_requests.html", {
        "person": person,
        "type_cards": type_cards,
        "answered_total": answered_total,
    })


@login_required
def overtime_form(request):
    person = _person_or_redirect(request)
    if person is None:
        return redirect("core:home")
    if not sr.person_has_access(person, RequestType.CODE_OVERTIME):
        messages.error(request, "Overtime is not assigned to you.")
        return redirect("people:my_requests")

    session_key = "ot_selected_case_ids"
    selected = request.session.get(session_key) or []
    if request.method == "GET" and "cases" in request.GET:
        raw = (request.GET.get("cases") or "").strip()
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        request.session[session_key] = ids
        selected = ids
        return redirect("people:overtime_form")

    if request.method == "POST":
        action = (request.POST.get("action") or "submit").strip()
        if action == "remove_case":
            try:
                rid = int(request.POST.get("case_id") or 0)
            except ValueError:
                rid = 0
            selected = [x for x in selected if x != rid]
            request.session[session_key] = selected
            return redirect("people:overtime_form")
        if action == "clear_cases":
            request.session[session_key] = []
            return redirect("people:overtime_form")

        comment = (request.POST.get("comment") or "").strip()
        try:
            hours = int(request.POST.get("ot_hours") or 0)
            minutes = int(request.POST.get("ot_minutes") or 0)
        except ValueError:
            messages.error(request, "Enter a valid overtime duration.")
            return redirect("people:overtime_form")
        try:
            req = sr.submit_overtime(
                person=person,
                user=request.user,
                case_ids=selected,
                hours=hours,
                minutes=minutes,
                comment=comment,
            )
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
            return redirect("people:overtime_form")
        request.session[session_key] = []
        messages.success(
            request,
            f"Overtime request {req.request_code} submitted ({req.requested_label}). "
            f"Waiting for General Manager review.",
        )
        return redirect("people:overtime_form")

    cases = sr.linked_cases_display(selected)
    hour_choices = [f"{h:02d}" for h in range(0, 13)]
    minute_choices = [f"{m:02d}" for m in range(0, 60, 5)]
    archive_select_url = (
        reverse("cases:archive")
        + "?select=1&return="
        + reverse("people:overtime_form")
    )
    recent = list(sr.history_for_person(person).filter(
        request_type__code=RequestType.CODE_OVERTIME,
    )[:300])
    recent_rows = _history_rows(recent)
    # Entering Overtime marks current decided answers as seen (badge → 0).
    sr.mark_request_type_seen(request.session, person, RequestType.CODE_OVERTIME)
    return render(request, "people/overtime_form.html", {
        "person": person,
        "cases": cases,
        "hour_choices": hour_choices,
        "minute_choices": minute_choices,
        "ot_h": "00",
        "ot_m": "30",
        "archive_select_url": archive_select_url,
        "comment": "",
        "recent_rows": recent_rows,
        **sr.filter_options_from_rows(recent_rows, show_person=False),
    })


@login_required
def request_detail(request, pk):
    """Detail for the requester; GM/admin may also open any request here."""
    req = get_object_or_404(
        StaffRequest.objects.select_related(
            "person", "request_type", "created_by", "decided_by",
        ),
        pk=pk,
    )
    is_gm = _is_gm(request.user)
    person = sr.person_for_request_user(request.user)
    if not is_gm and (person is None or req.person_id != person.pk):
        messages.error(request, "You cannot view this request.")
        return redirect("people:my_requests")

    can_decide = (
        is_gm
        and req.status == StaffRequest.STATUS_SUBMITTED
        and req.request_type.code == RequestType.CODE_OVERTIME
    )
    if is_gm:
        back_url = reverse("people:gm_overtime_inbox")
    elif req.request_type.code == RequestType.CODE_OVERTIME:
        back_url = reverse("people:overtime_form")
    else:
        back_url = reverse("people:my_requests")
    return render(request, "people/request_detail.html", {
        "req": req,
        "cases": sr.linked_cases_display(req.case_ids or []),
        "is_manager_view": is_gm,
        "can_decide": can_decide,
        "back_url": back_url,
        "req_h": f"{(req.requested_minutes or 0) // 60:02d}",
        "req_m": f"{(req.requested_minutes or 0) % 60:02d}",
        "hour_choices": [f"{h:02d}" for h in range(0, 13)],
        "minute_choices": [f"{m:02d}" for m in range(0, 60, 5)],
    })


# ---------------------------------------------------------------------------
# GM — Requests inbox (seat-scoped for General Manager)
# ---------------------------------------------------------------------------
@login_required
@gm_required
def gm_overtime_inbox(request):
    """GM Requests: pending card + decided history with type tabs/filters."""
    pending = list(sr.gm_pending_overtime())
    pending_rows = _history_rows(pending)
    history = list(sr.history_for_gm()[:400])
    history_rows = _history_rows(history)
    type_counts = {}
    for r in history:
        title = r.request_type.title
        type_counts[title] = type_counts.get(title, 0) + 1
    for r in pending:
        title = r.request_type.title
        type_counts[title] = type_counts.get(title, 0) + 1
    colors = ["#2563eb", "#0d9488", "#c2410c", "#7c3aed", "#be123c"]
    type_tabs = []
    for i, (label, count) in enumerate(sorted(type_counts.items())):
        type_tabs.append({
            "label": label,
            "count": count,
            "color": colors[i % len(colors)],
            "active": False,
        })
    return render(request, "people/gm_overtime_inbox.html", {
        "pending_count": len(pending),
        "pending_rows": pending_rows,
        "recent_rows": history_rows,
        "type_tabs": type_tabs,
        "type_tabs_active": False,
        **sr.filter_options_from_rows(history_rows, show_person=True),
    })


@login_required
@gm_required
@require_POST
def gm_overtime_decide(request, pk):
    req = get_object_or_404(
        StaffRequest.objects.select_related("person", "request_type"),
        pk=pk,
    )
    decision = (request.POST.get("decision") or "").strip().lower()
    note = (request.POST.get("decision_note") or "").strip()
    try:
        if decision == "approve":
            try:
                ah = int(request.POST.get("approved_hours") or 0)
                am = int(request.POST.get("approved_minutes") or 0)
            except ValueError as exc:
                raise ValueError("Enter a valid approved duration.") from exc
            sr.decide_overtime(
                req, user=request.user, approve=True,
                approved_minutes=ah * 60 + am, note=note,
            )
            messages.success(
                request,
                f"Overtime approved for {req.person.display_name} "
                f"({sr.minutes_label(req.approved_minutes or 0)}).",
            )
        elif decision == "reject":
            sr.decide_overtime(req, user=request.user, approve=False, note=note)
            messages.success(
                request,
                f"Overtime request for {req.person.display_name} was rejected.",
            )
        else:
            messages.error(request, "Unknown decision.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("people:request_detail", pk=req.pk)
