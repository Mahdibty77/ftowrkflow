"""Role-aware dashboards.

Who sees what:

* Plain experts (Commercial / Technical / Supply, internal or external) have NO
  dashboard — they are redirected to their inbox.
* A unit MANAGER sees a per-expert report card for every expert in their unit
  (including their own manager card), filtered by a from/to date range.
  SUPERVISORS see the same.
* The GENERAL MANAGER and ADMIN see three colour-coded unit sections
  (Commercial / Technical / Supply), each with its own date range.

Every expert card links to the archive filtered to that person's cases, so the
manager can drill into the list and then a single case exactly like the archive.
"""
from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.constants import Role, Unit
from cases.constants import CaseStatus, EventAction
from cases.jalali import jalali_to_gregorian
from cases.models import Case


# Jalali picker writes "YYYY.MM.DD HH:MM" (dots); also accept - and /.
_JALALI_DT_RE = re.compile(
    r"(\d{3,4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?"
)


# --------------------------------------------------------------------------- #
# Date-range helpers (inputs are Jalali "YYYY.MM.DD HH:MM" from the picker)
# --------------------------------------------------------------------------- #
def _parse_jalali_datetime(raw):
    """Parse a Jalali datetime string to an aware Gregorian datetime, or None.

    Accepts date-only or date+time. Separators may be ``.``, ``-`` or ``/``.
    """
    if not raw:
        return None
    try:
        m = _JALALI_DT_RE.match(str(raw).strip())
        if not m:
            return None
        jy, jm, jd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        naive = _dt.datetime(gy, gm, gd, hh, mm, 0)
        tz = timezone.get_current_timezone()
        if timezone.is_naive(naive):
            return timezone.make_aware(naive, tz)
        return naive
    except Exception:
        return None


def _range_from_request(request, prefix=""):
    """Return (from_dt, to_dt, raw_from, raw_to) for a unit's date filter.

    Display values always use dashes (1404-02-05 09:00), matching the picker.
    """
    raw_from = request.GET.get(f"{prefix}from", "")
    raw_to = request.GET.get(f"{prefix}to", "")

    def _display(raw):
        s = str(raw or "").strip()
        if not s:
            return ""
        # Normalize older dotted values from previous picker versions.
        return s.replace(".", "-")

    return (
        _parse_jalali_datetime(raw_from),
        _parse_jalali_datetime(raw_to),
        _display(raw_from),
        _display(raw_to),
    )


def _apply_range(qs, from_dt, to_dt):
    """Filter a case queryset by created_at within [from_dt, to_dt] inclusive.

    ``to_dt`` includes the whole selected minute (through …:59.999999).
    """
    if from_dt:
        qs = qs.filter(created_at__gte=from_dt)
    if to_dt:
        end = to_dt.replace(second=59, microsecond=999999)
        qs = qs.filter(created_at__lte=end)
    return qs


def _person(user):
    profile = getattr(user, "profile", None)
    return {
        "id": user.id,
        "name": user.get_full_name() or user.username,
        "code": getattr(profile, "internal_code", "") or "",
        "is_manager": bool(profile and profile.role == Role.MANAGER),
    }


def _inbox_counts_in_range(users, from_dt, to_dt):
    """Inbox size per user id, optionally narrowed by created_at.

    One query for the whole section. This used to be a per-user helper called
    once per expert card, so a unit with fifteen experts ran fifteen inbox
    queries to draw one page. ``inbox_counts_for_users`` folds them into a single
    aggregate using the very same rule ``inbox_cases`` applies, which is why the
    routing conditions are not restated here — a second copy in this file would
    drift away from the Inbox tab the numbers are supposed to match.
    """
    from cases.services import inbox_counts_for_users

    try:
        return inbox_counts_for_users(
            users, narrow=lambda qs: _apply_range(qs, from_dt, to_dt))
    except Exception:
        # Same defensive stance as the per-user version it replaces: a dashboard
        # is a read-only summary and must still render, so an unanswerable count
        # falls back to zero rather than 500ing the page. A single unresolvable
        # person is already handled inside inbox_counts_for_users; only a failure
        # of the aggregate itself gets this far.
        return {}


# --------------------------------------------------------------------------- #
# Per-unit expert cards
# --------------------------------------------------------------------------- #
def _case_ids_by_user(qs, id_set, *fields):
    """Map ``user id -> {case ids}``, tallying several columns in one pass.

    A case names its people across three or four different assignee columns, so
    "how many cases is this expert on?" cannot be answered by one column, and
    answering it per user per column would cost a query per card. Pulling the
    id-tuples once for the whole unit and collecting them into a *set* per user
    is what gives the same answer as the per-user ``.distinct()`` counts this
    replaces: a case that names the same person in two columns still counts once.

    Only ids in ``id_set`` are tallied, so a column pointing at somebody outside
    this unit is ignored rather than given a card.
    """
    out = defaultdict(set)
    for row in qs.values_list("id", *fields):
        case_id = row[0]
        for user_id in row[1:]:
            if user_id in id_set:
                out[user_id].add(case_id)
    return out


def _commercial_cards(users, from_dt, to_dt):
    # One grouped query with conditional counts instead of ~6 counts per user.
    from cases.export_data import case_pi_grand_totals_map, format_money_amount

    users = list(users)
    ids = [u.id for u in users]
    qs = _apply_range(Case.objects.filter(created_by_id__in=ids), from_dt, to_dt)
    agg = {
        row["created_by"]: row
        for row in qs.values("created_by").annotate(
            total=Count("id"),
            cancelled=Count("id", filter=Q(status=CaseStatus.CANCELLED)),
            unsuppliable=Count("id", filter=Q(status__in=[
                CaseStatus.UNSUPPLIABLE, CaseStatus.UNSUPPLIABLE_CLOSED])),
            closed=Count("id", filter=Q(status=CaseStatus.CLOSED)),
            final=Count("id", filter=Q(status=CaseStatus.FINAL_APPROVED)),
        )
    }
    # Grand totals for cases in range, grouped by creator.
    case_creator = list(qs.values_list("id", "created_by"))
    gt_map = case_pi_grand_totals_map([cid for cid, _ in case_creator])
    money_by_user = defaultdict(float)
    for cid, uid in case_creator:
        money_by_user[uid] += gt_map.get(cid, 0.0)

    cards = []
    for user in users:
        row = agg.get(user.id, {})
        final = row.get("final", 0)
        card = _person(user)
        gt = money_by_user.get(user.id, 0.0)
        card.update({
            "total": row.get("total", 0),
            "cancelled": row.get("cancelled", 0),
            "unsuppliable": row.get("unsuppliable", 0),
            "sent_to_client": row.get("closed", 0) + final,
            "final_approved": final,
            "grand_total": gt,
            "grand_total_display": format_money_amount(gt) if gt else "—",
            "filter_key": "creator",
        })
        cards.append(card)
    return sorted(cards, key=lambda c: c["total"], reverse=True)


def _technical_cards(users, from_dt, to_dt):
    """Assigned = cases tied to the expert; In inbox = real inbox_cases() count."""
    users = list(users)
    ids = [u.id for u in users]
    id_set = set(ids)

    assigned = _case_ids_by_user(
        _apply_range(
            Case.objects.filter(
                Q(technical_assignee_id__in=ids)
                | Q(assigned_to_id__in=ids)
                | Q(technical_internal_assignee_id__in=ids)
                | Q(technical_external_assignee_id__in=ids)
            ),
            from_dt, to_dt,
        ),
        id_set,
        "technical_assignee",
        "assigned_to",
        "technical_internal_assignee",
        "technical_external_assignee",
    )

    in_inbox = _inbox_counts_in_range(users, from_dt, to_dt)
    cards = []
    for user in users:
        card = _person(user)
        card.update({
            "assigned": len(assigned.get(user.id, ())),
            "in_inbox": in_inbox.get(user.id, 0),
            "filter_key": "assignee",
        })
        cards.append(card)
    return sorted(cards, key=lambda c: c["assigned"], reverse=True)


def _supply_cards(users, from_dt, to_dt):
    # Inbox uses the real inbox_cases() rules so manager/expert/split sides match
    # the Inbox tab; the assignee columns are tallied by _case_ids_by_user.
    users = list(users)
    ids = [u.id for u in users]
    id_set = set(ids)

    assigned = _case_ids_by_user(
        _apply_range(
            Case.objects.filter(
                Q(supply_internal_assignee_id__in=ids) | Q(supply_external_assignee_id__in=ids)
                | Q(supply_assignee_id__in=ids)),
            from_dt, to_dt),
        id_set,
        "supply_internal_assignee", "supply_external_assignee", "supply_assignee",
    )

    # All four conditions have to describe one and the same CaseEvent row. On a
    # multi-valued relation like ``events`` every separate .filter() call gets its
    # own join, so chaining the date bounds would let *any* later event on the case
    # satisfy the range while a different, much older event supplied the
    # CANNOT_SUPPLY action -- inflating the figure whenever a range is applied.
    # Collecting them into a single filter() call keeps them on one joined row.
    unsup_lookups = {
        "events__actor_id__in": ids,
        "events__action": EventAction.CANNOT_SUPPLY,
    }
    if from_dt:
        unsup_lookups["events__created_at__gte"] = from_dt
    if to_dt:
        unsup_lookups["events__created_at__lte"] = to_dt.replace(
            second=59, microsecond=999999)
    unsup_map = _case_ids_by_user(
        Case.objects.filter(**unsup_lookups), id_set, "events__actor")

    in_inbox = _inbox_counts_in_range(users, from_dt, to_dt)
    cards = []
    for user in users:
        card = _person(user)
        card.update({
            "assigned": len(assigned.get(user.id, ())),
            "in_inbox": in_inbox.get(user.id, 0),
            "unsuppliable": len(unsup_map.get(user.id, ())),
            "filter_key": "assignee",
        })
        cards.append(card)
    return sorted(cards, key=lambda c: c["assigned"], reverse=True)


def _unit_users(unit, include_manager):
    roles = [Role.EXPERT]
    if include_manager:
        roles.append(Role.MANAGER)
    return User.objects.filter(
        profile__unit=unit, profile__role__in=roles, is_active=True
    ).select_related("profile").order_by("first_name", "username")


def _unit_section(unit, from_dt, to_dt, include_manager):
    users = _unit_users(unit, include_manager)
    if unit == Unit.COMMERCIAL:
        cards = _commercial_cards(users, from_dt, to_dt)
        kind = "commercial"
    elif unit == Unit.TECHNICAL:
        cards = _technical_cards(users, from_dt, to_dt)
        kind = "technical"
    else:
        cards = _supply_cards(users, from_dt, to_dt)
        kind = "supply"
    return {"unit": unit, "unit_label": Unit.LABELS.get(unit, unit),
            "kind": kind, "cards": cards}


# The status distribution drawn on the Admin / General Manager overview, in the
# order the bars appear. Each bucket is a condition over Case.status only — no
# joins — which is what lets them all be counted in a single query below.
#
# UNSUPPLIABLE / UNSUPPLIABLE_CLOSED are the live statuses; the two PENDING ones
# belonged to a manager-approval chain that has since been removed as
# unreachable, so nothing can produce them any more. They are kept in the bucket
# on purpose: this counts what is *stored*, and a case parked in one of them by
# an older release would otherwise vanish from the totals instead of being
# reported as what it is.
#
# For the same reason every status needs a bucket at all: the bars are only
# readable against the "Total cases" figure if they can add up to it. FINAL_CLOSED
# and BURNED are terminal states a case leaves the other buckets for, and without
# them the bars silently fail to reconcile.
_STATUS_BUCKETS = {
    "draft": Q(status=CaseStatus.DRAFT),
    "with_commercial": Q(status__in=[
        CaseStatus.WITH_COMMERCIAL, CaseStatus.RETURNED_TO_COMMERCIAL,
        CaseStatus.PENDING_CANCEL]),
    "with_technical": Q(status__in=[
        CaseStatus.WITH_TECHNICAL, CaseStatus.RETURNED_TO_TECHNICAL]),
    "with_supply": Q(status=CaseStatus.WITH_SUPPLY),
    "sent_to_client": Q(status=CaseStatus.CLOSED),
    "final_approved": Q(status=CaseStatus.FINAL_APPROVED),
    "cancelled": Q(status=CaseStatus.CANCELLED),
    "unsuppliable": Q(status__in=[
        CaseStatus.UNSUPPLIABLE, CaseStatus.UNSUPPLIABLE_CLOSED,
        CaseStatus.UNSUPPLIABLE_PENDING_SUPPLY,
        CaseStatus.UNSUPPLIABLE_PENDING_COMMERCIAL]),
    "final_closed": Q(status=CaseStatus.FINAL_CLOSED),
    "burned": Q(status=CaseStatus.BURNED),
}


def _platform_overview(from_dt, to_dt):
    """Overall case counts + simple chart series for Admin / General Manager."""
    from cases.export_data import case_pi_grand_totals_map, format_money_amount

    qs = _apply_range(Case.objects.all(), from_dt, to_dt)
    # One round-trip for the total and all ten buckets, where there were eleven
    # separate COUNT queries over the same rows. Each conditional count sees
    # exactly the rows the equivalent .filter().count() saw — the buckets touch
    # no related table, so no join can duplicate a row — and every figure is
    # therefore unchanged.
    counts = qs.aggregate(
        total=Count("id"),
        **{name: Count("id", filter=cond) for name, cond in _STATUS_BUCKETS.items()},
    )
    total = counts["total"]
    by_status = {name: counts[name] for name in _STATUS_BUCKETS}
    by_unit = [
        {"label": Unit.LABELS[Unit.COMMERCIAL], "code": "commercial",
         "count": qs.filter(created_by__profile__unit=Unit.COMMERCIAL).count()},
        {"label": Unit.LABELS[Unit.TECHNICAL], "code": "technical",
         "count": qs.filter(
             Q(technical_assignee__isnull=False)
             | Q(holder_unit=Unit.TECHNICAL)).distinct().count()},
        {"label": Unit.LABELS[Unit.SUPPLY], "code": "supply",
         "count": qs.filter(
             Q(supply_assignee__isnull=False)
             | Q(supply_internal_assignee__isnull=False)
             | Q(supply_external_assignee__isnull=False)
             | Q(holder_unit=Unit.SUPPLY)).distinct().count()},
    ]
    case_ids = list(qs.values_list("id", flat=True))
    gt_map = case_pi_grand_totals_map(case_ids)
    money = sum(gt_map.values()) if gt_map else 0.0
    max_status = max(by_status.values()) if by_status else 1
    max_unit = max((u["count"] for u in by_unit), default=1) or 1
    status_bars = [
        {"key": k, "label": k.replace("_", " ").title(), "count": v,
         "pct": int(round((v / max_status) * 100)) if max_status else 0}
        for k, v in by_status.items() if v
    ]
    for u in by_unit:
        u["pct"] = int(round((u["count"] / max_unit) * 100)) if max_unit else 0
    return {
        "total": total,
        "grand_total": money,
        "grand_total_display": format_money_amount(money) if money else "—",
        "by_status": by_status,
        "status_bars": status_bars,
        "by_unit": by_unit,
    }


# --------------------------------------------------------------------------- #
# Dashboard dispatch
# --------------------------------------------------------------------------- #
@login_required
def dashboard(request):
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return redirect("accounts:login")

    is_admin = profile.is_admin
    is_gm = profile.is_general_manager
    is_manager = profile.role == Role.MANAGER
    is_supervisor = profile.role == Role.SUPERVISOR

    # Experts never get a dashboard.
    if not (is_admin or is_gm or is_manager or is_supervisor):
        return redirect("cases:inbox")

    # ---- Admin / General manager: overview + all three units ---------------
    if is_admin or is_gm:
        ov_f, ov_t, ov_rf, ov_rt = _range_from_request(request, prefix="ov_")
        overview = _platform_overview(ov_f, ov_t)
        sections = []
        for unit in (Unit.COMMERCIAL, Unit.TECHNICAL, Unit.SUPPLY):
            f, t, rf, rt = _range_from_request(request, prefix=f"{unit.lower()}_")
            section = _unit_section(unit, f, t, include_manager=True)
            section["raw_from"], section["raw_to"] = rf, rt
            sections.append(section)
        return render(request, "reports/dashboard.html", {
            "scope": "admin" if is_admin else "general_manager",
            "sections": sections,
            "overview": overview,
            "ov_raw_from": ov_rf,
            "ov_raw_to": ov_rt,
        })

    # ---- Unit manager / supervisor: their own unit ---------------------------
    if profile.unit in (Unit.COMMERCIAL, Unit.TECHNICAL, Unit.SUPPLY):
        f, t, rf, rt = _range_from_request(request)
        # Managers and supervisors both see expert cards PLUS the manager's own
        # card (same metrics / drill-down to archive as any expert).
        section = _unit_section(profile.unit, f, t, include_manager=True)
        section["raw_from"], section["raw_to"] = rf, rt
        return render(request, "reports/dashboard.html", {
            "scope": "unit",
            "sections": [section],
            "overview": None,
        })

    return redirect("cases:inbox")
