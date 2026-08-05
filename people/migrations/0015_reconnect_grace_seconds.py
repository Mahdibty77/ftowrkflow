# Per-person reconnect grace (default 10:00)
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0014_float_time_and_explicit_logout"),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="reconnect_grace_seconds",
            field=models.PositiveIntegerField(
                default=600,
                help_text="Reconnect time after disconnect, in seconds (default 10:00).",
            ),
        ),
    ]
