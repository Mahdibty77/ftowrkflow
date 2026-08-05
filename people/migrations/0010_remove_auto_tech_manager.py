"""Remove auto-created vacant tech_manager seat from an older migration.

people.0006 used to invent a Technical Manager seat named tech_manager when
missing. That is no longer desired: seats must come only from real legacy users
or deliberate admin creation. This cleanup deletes that placeholder only when
it still looks auto-generated (empty identity, unassigned, inactive).
"""
from __future__ import annotations

from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("accounts", "Profile")
    PersonAccount = apps.get_model("people", "PersonAccount")

    user = User.objects.filter(username__iexact="tech_manager").first()
    if user is None:
        return
    if PersonAccount.objects.filter(user_id=user.pk).exists():
        return  # linked to a person — keep
    if (user.first_name or "").strip() or (user.last_name or "").strip():
        return  # real identity — keep
    if (user.email or "").strip():
        return
    prof = Profile.objects.filter(user_id=user.pk).first()
    if prof is None:
        return
    if (prof.unit or "") != "TECHNICAL" or (prof.role or "") != "MANAGER":
        return
    # Vacant auto-seat: drop it.
    Profile.objects.filter(user_id=user.pk).delete()
    user.delete()


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0009_seat_tenure_index_rename"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
