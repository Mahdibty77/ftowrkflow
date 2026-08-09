# Audit column for the reconnect rule.
#
# A disconnect longer than the reconnect grace stopped the pings, so no minutes
# were credited for the time away — that missing credit is the entire cost of
# the absence. Nothing on the day row said an absence had happened, which made a
# short month impossible to explain to the person it belonged to. This column
# records the beyond-grace away time so the arithmetic can be shown.
#
# Existing rows get 0: the absences they hold were never recorded and cannot be
# reconstructed after the fact, so history stays honestly blank rather than
# guessed at. Totals are untouched — away_minutes is written, never subtracted.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0020_shift_carry_seconds_and_field_alignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='shiftdaylog',
            name='away_minutes',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
