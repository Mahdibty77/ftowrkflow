"""Restore legacy logins wrongly deactivated by 0004.

0004 treated every account without a Person link as an empty seat and set
``is_active=False``. Real working accounts (admin, unit managers, …) were
never empty seats — only ``userN`` placeholders created by the new Create
seat flow should stay inactive until assigned.

Also re-attach ``is_admin`` to the real platform administrator (username
``admin`` / a superuser), not whichever Profile happened to have the lowest pk.
"""
from __future__ import annotations

import re

from django.db import migrations

_PLACEHOLDER = re.compile(r"^user\d+$", re.IGNORECASE)


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("accounts", "Profile")

    # --- Real administrator -------------------------------------------------
    admin_user = User.objects.filter(username__iexact="admin").first()
    if admin_user is None:
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
    if admin_user is not None:
        Profile.objects.exclude(user_id=admin_user.pk).filter(is_admin=True).update(
            is_admin=False,
        )
        profile = Profile.objects.filter(user_id=admin_user.pk).first()
        if profile is not None:
            profile.is_admin = True
            profile.seat_ready = False
            profile.save(update_fields=["is_admin", "seat_ready"])
        if not admin_user.is_active:
            admin_user.is_active = True
            admin_user.save(update_fields=["is_active"])

    # --- Reactivate non-placeholder accounts --------------------------------
    for user in User.objects.filter(is_active=False).iterator():
        username = user.username or ""
        if _PLACEHOLDER.match(username) and not (user.first_name or user.last_name):
            continue
        user.is_active = True
        user.save(update_fields=["is_active"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0004_legacy_one_login_roles"),
        ("accounts", "0010_profile_seat_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
