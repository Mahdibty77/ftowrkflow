# Staff request unique codes
from django.db import migrations, models


def backfill_request_codes(apps, schema_editor):
    StaffRequest = apps.get_model("people", "StaffRequest")
    from cases.codes import year_month_token
    from cases.models import SerialCounter

    counter, _ = SerialCounter.objects.get_or_create(
        key="staff_request_serial", defaults={"value": 0},
    )
    for req in StaffRequest.objects.select_related("person", "request_type").order_by("pk"):
        if (getattr(req, "request_code", None) or "").strip():
            continue
        counter.value += 1
        type_token = {
            "overtime": "OT",
            "leave": "LV",
            "purchase": "PU",
        }.get(getattr(req.request_type, "code", "") or "", "RQ")
        person_code = (getattr(req.person, "detail_code", None) or str(req.person_id)).strip()
        req.request_code = "-".join([
            "FT",
            type_token,
            year_month_token(),
            person_code,
            f"{counter.value:04d}",
        ])
        req.save(update_fields=["request_code"])
    counter.save(update_fields=["value"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0017_remove_leave_purchase"),
        ("cases", "0010_case_delegated_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffrequest",
            name="request_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Unique request number, e.g. FT-OT-503-100000001-0007",
                max_length=80,
            ),
        ),
        migrations.RunPython(backfill_request_codes, noop_reverse),
        migrations.AddConstraint(
            model_name="staffrequest",
            constraint=models.UniqueConstraint(
                fields=("request_code",),
                name="people_staffrequest_request_code_uniq",
            ),
        ),
    ]
