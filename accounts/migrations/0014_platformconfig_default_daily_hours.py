# Default daily hours on PlatformConfig
from datetime import time

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_seat_index_per_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfig",
            name="default_work_start",
            field=models.TimeField(
                default=time(8, 0),
                help_text="Default shift start applied to all people.",
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="default_work_end",
            field=models.TimeField(
                default=time(17, 0),
                help_text="Default shift end applied to all people.",
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="default_float_seconds",
            field=models.PositiveIntegerField(
                default=900,
                help_text="Default floating time in seconds (e.g. 900 = 15:00).",
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="default_reconnect_grace_seconds",
            field=models.PositiveIntegerField(
                default=600,
                help_text="Default reconnect time in seconds (e.g. 600 = 10:00).",
            ),
        ),
    ]
