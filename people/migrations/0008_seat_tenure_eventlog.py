from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("people", "0007_guarantees_internal_seatlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="SeatTenure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(
                    choices=[("OWNER", "Owner"), ("SUBSTITUTE", "Substitute")],
                    db_index=True, default="OWNER", max_length=20,
                )),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("ended_reason", models.CharField(
                    blank=True,
                    choices=[
                        ("ASSIGN", "Assign"),
                        ("TRANSLATE", "Translate"),
                        ("RETURN", "Return"),
                        ("CLOSE", "Close"),
                        ("RELEASE", "Release"),
                    ],
                    max_length=20,
                )),
                ("origin_person", models.ForeignKey(
                    blank=True, help_text="Owner person when kind is SUBSTITUTE (for Return).",
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="origin_seat_tenures", to="people.person",
                )),
                ("person", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="seat_tenures", to="people.person",
                )),
                ("person_role", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="tenures", to="people.personrole",
                )),
                ("source_user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="seat_tenures", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Seat tenure",
                "verbose_name_plural": "Seat tenures",
                "ordering": ["-started_at", "-pk"],
            },
        ),
        migrations.CreateModel(
            name="SeatEventLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.CharField(
                    choices=[
                        ("CREATED", "Created"),
                        ("ASSIGNED", "Assigned"),
                        ("TRANSLATED", "Translated"),
                        ("RETURNED", "Returned"),
                        ("DELEGATED", "Delegated"),
                        ("CLOSED", "Closed"),
                        ("VACANT", "Vacant"),
                    ],
                    db_index=True, max_length=20,
                )),
                ("from_person_name", models.CharField(blank=True, max_length=200)),
                ("to_person_name", models.CharField(blank=True, max_length=200)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="seat_events_acted", to=settings.AUTH_USER_MODEL,
                )),
                ("from_person", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to="people.person",
                )),
                ("source_user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="seat_event_logs", to=settings.AUTH_USER_MODEL,
                )),
                ("to_person", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to="people.person",
                )),
            ],
            options={
                "verbose_name": "Seat event log",
                "verbose_name_plural": "Seat event logs",
                "ordering": ["-created_at", "-pk"],
            },
        ),
        migrations.AddIndex(
            model_name="seattenure",
            index=models.Index(fields=["source_user", "ended_at"], name="people_seat_source__7c2a1d_idx"),
        ),
    ]
