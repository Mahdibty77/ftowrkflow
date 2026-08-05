"""Freeze commercial / technical expert display names onto Case rows.

Mirrors CaseEvent.actor_name: once stamped, seat reassignment or vacant
usernames (``_user6``) must never rewrite what the case detail already showed.
"""
from django.db import migrations, models


def _person_name(user, person_by_user):
    person = person_by_user.get(user.id)
    if person is not None:
        en = f"{(person.first_name_en or '').strip()} {(person.last_name_en or '').strip()}".strip()
        if en:
            return en
        fa = f"{(person.first_name or '').strip()} {(person.last_name or '').strip()}".strip()
        if fa:
            return fa
    name = f"{(user.first_name or '').strip()} {(user.last_name or '').strip()}".strip()
    if name:
        return name
    uname = (user.username or "").strip()
    if uname.startswith("_"):
        return ""
    return uname


def _fmt(name, code):
    name = (name or "").strip()
    code = (code or "").strip()
    if name and code:
        return f"{name} ({code})"
    if name:
        return name
    if code:
        return f"({code})"
    return ""


def backfill_expert_displays(apps, schema_editor):
    Case = apps.get_model("cases", "Case")
    CaseForm = apps.get_model("cases", "CaseForm")
    CaseEvent = apps.get_model("cases", "CaseEvent")
    PersonAccount = apps.get_model("people", "PersonAccount")
    Profile = apps.get_model("accounts", "Profile")

    person_by_user = {
        link.user_id: link.person
        for link in PersonAccount.objects.select_related("person").all()
    }
    internal_by_user = {
        p.user_id: (p.internal_code or "").strip()
        for p in Profile.objects.all()
    }

    create_name = {}
    build_to_name = {}
    for ev in (
        CaseEvent.objects.filter(action__in=["CREATE", "BUILD_TO"])
        .order_by("case_id", "created_at", "pk")
        .iterator()
    ):
        frozen = (ev.actor_name or "").strip()
        if not frozen or frozen.startswith("_"):
            continue
        if ev.action == "CREATE" and ev.case_id not in create_name:
            create_name[ev.case_id] = frozen
        elif ev.action == "BUILD_TO" and ev.case_id not in build_to_name:
            build_to_name[ev.case_id] = frozen

    to_author = {}
    for form in (
        CaseForm.objects.filter(kind="TO")
        .order_by("case_id", "created_at", "pk")
        .iterator()
    ):
        meta = form.meta or {}
        if meta.get("currency_conversion_only"):
            continue
        if form.case_id not in to_author:
            to_author[form.case_id] = form.created_by

    batch = []
    for case in Case.objects.select_related("created_by").iterator():
        changed = False
        if not (case.commercial_expert_display or "").strip():
            code = (case.expert_code or "").strip() or internal_by_user.get(
                case.created_by_id, ""
            )
            name = _person_name(case.created_by, person_by_user) or create_name.get(
                case.pk, ""
            )
            disp = _fmt(name, code)
            if disp:
                case.commercial_expert_display = disp
                changed = True
        if not (case.technical_expert_display or "").strip():
            author = to_author.get(case.pk)
            if author is not None:
                code = internal_by_user.get(author.id, "")
                name = _person_name(author, person_by_user) or build_to_name.get(
                    case.pk, ""
                )
                disp = _fmt(name, code)
                if disp:
                    case.technical_expert_display = disp
                    changed = True
            elif case.pk in build_to_name:
                case.technical_expert_display = build_to_name[case.pk]
                changed = True
        if changed:
            batch.append(case)
            if len(batch) >= 200:
                Case.objects.bulk_update(
                    batch,
                    ["commercial_expert_display", "technical_expert_display"],
                )
                batch = []
    if batch:
        Case.objects.bulk_update(
            batch,
            ["commercial_expert_display", "technical_expert_display"],
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0008_caseexportlog_create_if_missing"),
        ("people", "0006_repair_seats_and_tech_manager"),
        ("accounts", "0011_profile_seat_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="commercial_expert_display",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="case",
            name="technical_expert_display",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RunPython(backfill_expert_displays, noop_reverse),
    ]
