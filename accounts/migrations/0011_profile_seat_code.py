"""Add Profile.seat_code (display index user1, user2, …) and seed admin → user1."""
import re

from django.db import migrations, models


_USER_N_RE = re.compile(r"^user(\d+)$", re.IGNORECASE)


def seed_admin_seat_code(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("accounts", "Profile")
    PersonAccount = apps.get_model("people", "PersonAccount")

    admin = User.objects.filter(username__iexact="admin").first()
    if admin is None:
        admin = User.objects.filter(is_superuser=True).order_by("pk").first()
    if admin is not None:
        profile = Profile.objects.filter(user_id=admin.pk).first()
        if profile is not None:
            taken = Profile.objects.filter(seat_code__iexact="user1").exclude(
                pk=profile.pk,
            ).exists()
            if not taken:
                profile.seat_code = "user1"
                profile.save(update_fields=["seat_code"])

    # Unassigned non-admin seats stay inactive until a person is assigned.
    linked = set(PersonAccount.objects.values_list("user_id", flat=True))
    for profile in Profile.objects.select_related("user").all():
        user = profile.user
        if profile.is_admin:
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active"])
            continue
        if user.pk in linked:
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active"])
            continue
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_profile_seat_fields"),
        ("people", "0005_restore_legacy_logins"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="seat_code",
            field=models.CharField(
                blank=True,
                help_text="Short seat index such as user1, user2. Unique when set.",
                max_length=20,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(seed_admin_seat_code, noop),
    ]
