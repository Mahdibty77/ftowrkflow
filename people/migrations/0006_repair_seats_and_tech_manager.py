"""Repair seat identity display and demote false admins.

Do NOT invent seats that were not already in the database (e.g. tech_manager).
Legacy unit/role users become vacant seats; missing organisational seats are
created only when an administrator adds them deliberately.
"""
from __future__ import annotations

from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("accounts", "Profile")
    PersonAccount = apps.get_model("people", "PersonAccount")
    Person = apps.get_model("people", "Person")

    # --- Only real admin keeps is_admin --------------------------------------
    admin = User.objects.filter(username__iexact="admin").first()
    if admin is None:
        admin = User.objects.filter(is_superuser=True).order_by("pk").first()
    admin_id = admin.pk if admin else None
    if admin_id is None:
        # Neither a login called "admin" nor any superuser — which is a perfectly
        # ordinary shape for this platform, because being an administrator here is
        # Profile.is_admin and has nothing to do with the username or with Django's
        # is_superuser flag. Whoever set the site up may simply have called the
        # account something else.
        #
        # What must not happen is what the plain `admin_id is None` test below used
        # to do: read "we could not identify the administrator" as "so demote every
        # administrator". The last loop in this function then deactivates every user
        # that is neither is_admin nor held by a person, so with no admin left it
        # deactivated ALL of them — an intact database with nobody able to sign in
        # and no administrator left to assign anyone, unrecoverable through the UI.
        #
        # Falling back to the earliest is_admin profile keeps exactly one
        # administrator, which is what this migration is trying to achieve anyway,
        # and is the same fallback the sibling migration 0004 already uses for the
        # identical situation. On any database that does have an "admin" login or a
        # superuser — every one built by this project's own setup, and the live
        # server — admin_id is not None and nothing here behaves differently.
        admin_id = (
            Profile.objects.filter(is_admin=True)
            .order_by("pk")
            .values_list("user_id", flat=True)
            .first()
        )
    for profile in Profile.objects.select_related("user").filter(is_admin=True):
        if admin_id is None or profile.user_id != admin_id:
            profile.is_admin = False
            profile.save(update_fields=["is_admin"])

    # --- Secondary seats: clear human names; only primary login active -------
    for person in Person.objects.all():
        links = list(
            PersonAccount.objects.filter(person_id=person.pk)
            .select_related("user")
            .order_by("assigned_at", "pk")
        )
        if not links:
            continue
        person_user = (person.username or "").strip().lower()
        primary = None
        if person_user:
            for link in links:
                if (link.user.username or "").lower() == person_user:
                    primary = link.user
                    break
            if primary is None:
                for link in links:
                    # Prefer account without seat_code (person-only login).
                    # Historical models: profile via Profile.objects
                    pass
        if primary is None:
            # Prefer matching username, else earliest with person's name identity.
            for link in links:
                prof = Profile.objects.filter(user_id=link.user_id).first()
                code = (getattr(prof, "seat_code", None) or "").strip() if prof else ""
                if not code:
                    primary = link.user
                    break
            if primary is None:
                primary = links[0].user

        for link in links:
            user = link.user
            prof = Profile.objects.filter(user_id=user.pk).first()
            is_primary = user.pk == primary.pk
            if is_primary:
                if person.status == "ACTIVE" and not user.is_active:
                    user.is_active = True
                    user.save(update_fields=["is_active"])
                if person.first_name or person.last_name:
                    user.first_name = person.first_name or ""
                    user.last_name = person.last_name or ""
                    user.save(update_fields=["first_name", "last_name"])
            else:
                # Secondary seat: vacant identity, inactive.
                if user.first_name or user.last_name:
                    user.first_name = ""
                    user.last_name = ""
                    user.save(update_fields=["first_name", "last_name"])
                if user.is_active:
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                if prof and prof.is_admin:
                    prof.is_admin = False
                    prof.save(update_fields=["is_admin"])

        if person.status != "ACTIVE":
            for link in links:
                if link.user.is_active:
                    link.user.is_active = False
                    link.user.save(update_fields=["is_active"])

    # Unassigned seats stay inactive (except platform admin).
    linked = set(PersonAccount.objects.values_list("user_id", flat=True))
    for user in User.objects.all():
        if user.pk in linked:
            continue
        prof = Profile.objects.filter(user_id=user.pk).first()
        if prof and prof.is_admin:
            continue
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_profile_seat_code"),
        ("people", "0005_restore_legacy_logins"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
