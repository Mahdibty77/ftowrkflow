"""2026-07 security pass: forced password change flag + impersonation audit log.

Purely additive — no existing column is touched or removed here (plain_password
is blanked separately in 0007, as a data migration, so this file stays a clean
schema-only change). Safe to run against the live database: every new column
has a default, so every existing Profile row is valid the instant this applies.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_profile_stamp"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="must_change_password",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ImpersonationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("admin_username", models.CharField(blank=True, max_length=150)),
                ("target_username", models.CharField(blank=True, max_length=150)),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("admin", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            related_name="impersonation_sessions_started",
                                            to=settings.AUTH_USER_MODEL)),
                ("target", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                             related_name="impersonation_sessions_received",
                                             to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
    ]
