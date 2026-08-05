# Staff request types + overtime tracking fields
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_request_types(apps, schema_editor):
    RequestType = apps.get_model("people", "RequestType")
    rows = [
        {
            "code": "overtime",
            "title": "Overtime",
            "description": "Request extra work time after the daily shift ends.",
            "icon": "fa-stopwatch",
            "sort_order": 10,
            "is_active": True,
        },
        {
            "code": "leave",
            "title": "Leave",
            "description": "Request time off. Full form comes in a later phase.",
            "icon": "fa-calendar-plus",
            "sort_order": 20,
            "is_active": True,
        },
        {
            "code": "purchase",
            "title": "Purchase",
            "description": "Purchase request. Available in a later phase.",
            "icon": "fa-boxes-packing",
            "sort_order": 30,
            "is_active": True,
        },
    ]
    for row in rows:
        RequestType.objects.update_or_create(code=row["code"], defaults=row)


def unseed_request_types(apps, schema_editor):
    RequestType = apps.get_model("people", "RequestType")
    RequestType.objects.filter(code__in=["overtime", "leave", "purchase"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("people", "0015_reconnect_grace_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftdaylog",
            name="overtime_minutes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="shiftmonthsnapshot",
            name="overtime_minutes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="RequestType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=40, unique=True)),
                ("title", models.CharField(max_length=80)),
                ("description", models.CharField(blank=True, default="", max_length=240)),
                ("icon", models.CharField(default="fa-clipboard-check", help_text="Font Awesome solid class, e.g. fa-stopwatch", max_length=60)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["sort_order", "title"]},
        ),
        migrations.CreateModel(
            name="PersonRequestAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                ("granted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="request_access", to="people.person")),
                ("request_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="person_access", to="people.requesttype")),
            ],
        ),
        migrations.CreateModel(
            name="StaffRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("rejected", "Rejected")], db_index=True, default="draft", max_length=20)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("case_ids", models.JSONField(blank=True, default=list)),
                ("requested_minutes", models.PositiveIntegerField(default=0)),
                ("approved_minutes", models.PositiveIntegerField(blank=True, null=True)),
                ("comment", models.TextField(blank=True, default="")),
                ("work_day", models.DateField(blank=True, db_index=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_requests_created", to=settings.AUTH_USER_MODEL)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff_requests_decided", to=settings.AUTH_USER_MODEL)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="staff_requests", to="people.person")),
                ("request_type", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requests", to="people.requesttype")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="personrequestaccess",
            constraint=models.UniqueConstraint(fields=("person", "request_type"), name="people_personrequestaccess_unique"),
        ),
        migrations.RunPython(seed_request_types, unseed_request_types),
    ]
