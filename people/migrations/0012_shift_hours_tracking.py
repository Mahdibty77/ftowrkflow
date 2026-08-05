# Shift month snapshots + daily presence logs
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0011_children_and_work_shift"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftMonthSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("jalali_year", models.PositiveIntegerField()),
                ("jalali_month", models.PositiveSmallIntegerField()),
                ("work_start", models.TimeField()),
                ("work_end", models.TimeField()),
                ("planned_minutes", models.PositiveIntegerField(default=0)),
                ("worked_minutes", models.PositiveIntegerField(default=0)),
                ("working_days", models.PositiveSmallIntegerField(default=0)),
                ("weekend_days", models.PositiveSmallIntegerField(default=0)),
                ("holiday_days", models.PositiveSmallIntegerField(default=0)),
                ("frozen", models.BooleanField(default=False)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="shift_months",
                    to="people.person",
                )),
            ],
            options={"ordering": ["-jalali_year", "-jalali_month"]},
        ),
        migrations.CreateModel(
            name="ShiftDayLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day", models.DateField(db_index=True)),
                ("minutes", models.PositiveIntegerField(default=0)),
                ("last_ping", models.DateTimeField(blank=True, null=True)),
                ("person", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="shift_days",
                    to="people.person",
                )),
            ],
            options={"ordering": ["-day"]},
        ),
        migrations.AddConstraint(
            model_name="shiftmonthsnapshot",
            constraint=models.UniqueConstraint(
                fields=("person", "jalali_year", "jalali_month"),
                name="people_shiftmonth_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="shiftdaylog",
            constraint=models.UniqueConstraint(
                fields=("person", "day"),
                name="people_shiftday_unique",
            ),
        ),
    ]
