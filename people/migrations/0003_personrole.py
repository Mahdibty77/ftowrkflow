import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("people", "0002_seats"),
        ("accounts", "0010_profile_seat_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("unit", models.CharField(blank=True, max_length=20)),
                ("role", models.CharField(blank=True, max_length=20)),
                ("supply_kind", models.CharField(blank=True, max_length=20)),
                ("internal_code", models.CharField(blank=True, max_length=40)),
                ("is_admin", models.BooleanField(default=False)),
                ("is_general_manager", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="roles",
                        to="people.person",
                    ),
                ),
                (
                    "source_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="absorbed_person_roles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Person role",
                "verbose_name_plural": "Person roles",
                "ordering": ["created_at", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="personrole",
            constraint=models.UniqueConstraint(
                fields=("person", "unit", "role", "supply_kind", "is_admin", "is_general_manager"),
                name="people_personrole_unique_combo",
            ),
        ),
    ]
