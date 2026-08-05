# Tracking start date + day login/logout stamps
from datetime import date

from django.db import migrations, models


def seed_tracking_start(apps, schema_editor):
    ShiftTrackingConfig = apps.get_model("people", "ShiftTrackingConfig")
    if not ShiftTrackingConfig.objects.filter(pk=1).exists():
        ShiftTrackingConfig.objects.create(pk=1, started_on=date.today())


def unseed_tracking_start(apps, schema_editor):
    ShiftTrackingConfig = apps.get_model("people", "ShiftTrackingConfig")
    ShiftTrackingConfig.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0012_shift_hours_tracking"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftTrackingConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_on", models.DateField(help_text="Gregorian date from which planned/worked hours are counted.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Shift tracking config",
                "verbose_name_plural": "Shift tracking config",
            },
        ),
        migrations.AddField(
            model_name="shiftdaylog",
            name="first_login",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shiftdaylog",
            name="last_logout",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(seed_tracking_start, unseed_tracking_start),
    ]
