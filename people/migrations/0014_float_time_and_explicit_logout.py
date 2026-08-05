# Floating time + explicit Sign-out flag for reconnect grace
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0013_shift_tracking_start"),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="float_seconds",
            field=models.PositiveIntegerField(
                default=900,
                help_text="Floating time grace after shift start, in seconds (default 15:00).",
            ),
        ),
        migrations.AddField(
            model_name="shiftdaylog",
            name="explicit_logout",
            field=models.BooleanField(default=False),
        ),
    ]
