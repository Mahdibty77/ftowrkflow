"""Blank every existing plain_password value.

This does NOT touch anyone's real password (the auth_user.password hash,
which is what Django actually authenticates against) — it only clears the
convenience clear-text copy. No user's ability to sign in is affected in any
way; this migration is purely a data-hygiene step so that copy no longer sits
in the database (or in any backup taken after this runs).

Deliberately irreversible: there is nothing meaningful to restore on rollback
(the values were a security liability, not a feature), so reverse is a no-op.
"""
from django.db import migrations


def clear_plain_passwords(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")
    Profile.objects.exclude(plain_password="").update(plain_password="")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_must_change_password_and_impersonation"),
    ]

    operations = [
        migrations.RunPython(clear_plain_passwords, noop_reverse),
    ]
