# Generated manually for People / Seats overhaul.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_internal_codes(apps, schema_editor):
    Person = apps.get_model("people", "Person")
    PersonRole = apps.get_model("people", "PersonRole")
    PersonAccount = apps.get_model("people", "PersonAccount")
    Profile = apps.get_model("accounts", "Profile")

    for person in Person.objects.all():
        code = ""
        # Prefer a role snapshot, then a held profile.
        for role in PersonRole.objects.filter(person_id=person.pk).order_by("pk"):
            if (role.internal_code or "").strip():
                code = role.internal_code.strip()
                break
        if not code:
            for link in PersonAccount.objects.filter(person_id=person.pk).select_related():
                try:
                    profile = Profile.objects.get(user_id=link.user_id)
                except Profile.DoesNotExist:
                    continue
                if (profile.internal_code or "").strip():
                    code = profile.internal_code.strip()
                    break
        if code:
            Person.objects.filter(pk=person.pk).update(internal_code=code)


# A second copy of the seat renumbering lived here and was never listed in the
# operations below, so reading this file suggested the 001-per-pool indexes were
# assigned at this point in history. They are not: the renumbering that actually
# runs is in accounts/0013_seat_index_per_role.py, which depends on this
# migration and therefore happens later, after further seat repairs. The dead
# copy is gone so there is only one answer to where seat indexes come from.


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("people", "0006_repair_seats_and_tech_manager"),
    ]

    operations = [
        migrations.RemoveField(model_name="person", name="departed_on"),
        migrations.RemoveField(model_name="person", name="hired_on"),
        migrations.RemoveField(model_name="person", name="job_title"),
        migrations.RemoveField(model_name="person", name="notes"),
        migrations.AddField(
            model_name="person",
            name="guarantee_amount",
            field=models.DecimalField(
                blank=True, decimal_places=0, max_digits=18, null=True,
                verbose_name="مبلغ تضمین (ریال)",
            ),
        ),
        migrations.AddField(
            model_name="person",
            name="guarantee_type",
            field=models.CharField(
                blank=True,
                choices=[("PROMISSORY", "سفته")],
                default="",
                max_length=20,
                verbose_name="نوع تضمین",
            ),
        ),
        migrations.AddField(
            model_name="person",
            name="internal_code",
            field=models.CharField(
                blank=True, max_length=40, verbose_name="کد داخلی",
            ),
        ),
        migrations.CreateModel(
            name="SeatAssignmentLog",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False, verbose_name="ID",
                )),
                ("person_name", models.CharField(blank=True, max_length=200)),
                ("detail_code", models.CharField(blank=True, max_length=20)),
                ("assigned_at", models.DateTimeField(blank=True, null=True)),
                ("vacated_at", models.DateTimeField()),
                ("person", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="seat_assignment_logs",
                    to="people.person",
                )),
                ("seat_user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="seat_assignment_logs",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("vacated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="seat_vacations_logged",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Seat assignment log",
                "verbose_name_plural": "Seat assignment logs",
                "ordering": ["-vacated_at", "-pk"],
            },
        ),
        migrations.RunPython(copy_internal_codes, migrations.RunPython.noop),
    ]
