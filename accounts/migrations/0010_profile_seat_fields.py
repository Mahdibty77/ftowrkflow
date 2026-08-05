from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_platformconfig_unit_stamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="seat_ready",
            field=models.BooleanField(
                default=False,
                help_text="When True, this unassigned account is listed under Available seats.",
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="last_vacated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the last person was released from this seat.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="assignment_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="How many times this seat has been handed to a person.",
            ),
        ),
    ]
