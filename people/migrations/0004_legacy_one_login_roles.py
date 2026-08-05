"""Collapse multi-seat logins into one login + PersonRole rows.

Also:
* mark unassigned users as inactive + seat_ready (legacy seats become available)
* demote every Administrator after the earliest one (keep the first admin)
* ensure each assigned person has at least one PersonRole matching their login
"""
from __future__ import annotations

from django.db import migrations


def _role_kw(profile) -> dict:
    return {
        "unit": profile.unit or "",
        "role": profile.role or "",
        "supply_kind": profile.supply_kind or "",
        "internal_code": profile.internal_code or "",
        "is_admin": bool(profile.is_admin),
        "is_general_manager": bool(profile.is_general_manager),
    }


def _neutral_username(User, user) -> str:
    preferred = f"user{user.pk}"
    if not User.objects.filter(username__iexact=preferred).exclude(pk=user.pk).exists():
        return preferred
    n = 1
    while User.objects.filter(username__iexact=f"user{n}").exists():
        n += 1
    return f"user{n}"


def _ensure_role(PersonRole, person_id, source_user_id, kw: dict):
    """Create a PersonRole if this combo is new; ignore unique races."""
    lookup = {
        "person_id": person_id,
        "unit": kw["unit"],
        "role": kw["role"],
        "supply_kind": kw["supply_kind"],
        "is_admin": kw["is_admin"],
        "is_general_manager": kw["is_general_manager"],
    }
    defaults = {
        "internal_code": kw["internal_code"],
        "source_user_id": source_user_id,
    }
    obj, _created = PersonRole.objects.get_or_create(**lookup, defaults=defaults)
    return obj


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("accounts", "Profile")
    PersonAccount = apps.get_model("people", "PersonAccount")
    PersonRole = apps.get_model("people", "PersonRole")

    # --- Keep the real platform administrator (not lowest Profile pk) --------
    admin_user = User.objects.filter(username__iexact="admin").first()
    if admin_user is None:
        admin_user = User.objects.filter(is_superuser=True).order_by("pk").first()
    if admin_user is not None:
        Profile.objects.exclude(user_id=admin_user.pk).filter(is_admin=True).update(
            is_admin=False,
        )
        kept = Profile.objects.filter(user_id=admin_user.pk).first()
        if kept is not None and not kept.is_admin:
            kept.is_admin = True
            kept.save(update_fields=["is_admin"])
    else:
        admin_ids = list(
            Profile.objects.filter(is_admin=True).order_by("pk").values_list("pk", flat=True)
        )
        if len(admin_ids) > 1:
            Profile.objects.filter(pk__in=admin_ids[1:]).update(is_admin=False)

    # --- Seat-ready + inactive for unassigned users ---------------------------
    linked_user_ids = set(PersonAccount.objects.values_list("user_id", flat=True))
    for profile in Profile.objects.select_related("user").all():
        user = profile.user
        if user.pk in linked_user_ids:
            # Assigned login: active, not a free seat template.
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active"])
            if profile.seat_ready:
                profile.seat_ready = False
                profile.save(update_fields=["seat_ready"])
            continue
        # Unassigned: mark seat_ready so it can appear under Available seats.
        # Do NOT deactivate legacy working accounts — only brand-new placeholder
        # seats (userN with empty name) stay inactive until assigned.
        if not profile.seat_ready:
            profile.seat_ready = True
            profile.save(update_fields=["seat_ready"])
        uname = (user.username or "")
        is_placeholder = (
            uname.lower().startswith("user")
            and uname[4:].isdigit()
            and not (user.first_name or user.last_name)
        )
        if is_placeholder and user.is_active and not profile.is_admin:
            user.is_active = False
            user.save(update_fields=["is_active"])

    # Re-activate the kept administrator if we deactivated them above.
    kept_admin = Profile.objects.filter(is_admin=True).order_by("pk").first()
    if kept_admin is not None:
        u = kept_admin.user
        if not u.is_active:
            u.is_active = True
            u.save(update_fields=["is_active"])

    # --- Collapse multiple PersonAccounts per person --------------------------
    person_ids = (
        PersonAccount.objects.values_list("person_id", flat=True)
        .distinct()
    )
    for person_id in person_ids:
        links = list(
            PersonAccount.objects.filter(person_id=person_id)
            .select_related("user")
            .order_by("assigned_at", "pk")
        )
        if not links:
            continue
        primary = links[0]
        primary_user = primary.user
        primary_profile = Profile.objects.filter(user_id=primary_user.pk).first()

        # Ensure a PersonRole for the primary login's current profile.
        if primary_profile is not None:
            _ensure_role(
                PersonRole, person_id, primary_user.pk, _role_kw(primary_profile),
            )

        for extra in links[1:]:
            extra_user = extra.user
            extra_profile = Profile.objects.filter(user_id=extra_user.pk).first()
            if extra_profile is not None:
                _ensure_role(
                    PersonRole, person_id, extra_user.pk, _role_kw(extra_profile),
                )
                # Bump assignment stats on the freed seat.
                extra_profile.assignment_count = int(extra_profile.assignment_count or 0) + 1
                extra_profile.seat_ready = True
                extra_profile.save(update_fields=["assignment_count", "seat_ready"])

            extra.delete()

            # Return extra user to placeholder pool.
            name = _neutral_username(User, extra_user)
            extra_user.username = name
            extra_user.first_name = ""
            extra_user.last_name = ""
            extra_user.is_active = False
            extra_user.save(update_fields=[
                "username", "first_name", "last_name", "is_active",
            ])

        # Primary stays the person's only login; mark not seat_ready.
        if primary_profile is not None and primary_profile.seat_ready:
            primary_profile.seat_ready = False
            primary_profile.save(update_fields=["seat_ready"])
        if not primary_user.is_active:
            primary_user.is_active = True
            primary_user.save(update_fields=["is_active"])

    # --- People with a login but no PersonRole yet (single-seat legacy) -------
    for link in PersonAccount.objects.select_related("user").all():
        profile = Profile.objects.filter(user_id=link.user_id).first()
        if profile is None:
            continue
        if PersonRole.objects.filter(person_id=link.person_id).exists():
            continue
        _ensure_role(PersonRole, link.person_id, link.user_id, _role_kw(profile))


def backwards(apps, schema_editor):
    # Irreversible data reshape; schema is unchanged.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0003_personrole"),
        ("accounts", "0010_profile_seat_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
