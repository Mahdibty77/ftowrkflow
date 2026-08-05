"""Handing a seat to a person, and taking it back.

A seat is a ``User`` account carrying a unit and a role. One person has at most
one *login* username (on the earliest role seat). Extra seats stay linked
(Held by), keep a vacant username, contribute ``PersonRole`` rows, and stay
Active while the person is active — but cannot sign in (unusable password).

Assigning the *first* organisational seat puts the person's identity on that
User. A bare profile-only login (no seat_code) is only kept until a real seat
exists; then identity moves onto the seat and the orphan is unlinked.
"""
from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PersonAccount, PersonRole
from .usernames import seat_username_candidates, vacant_login_username

logger = logging.getLogger(__name__)

IDENTITY_FIELDS = ["username", "first_name", "last_name"]


class SeatError(Exception):
    """Something about this assignment cannot be done — with a reason to show."""


def is_blank_org_seat(profile) -> bool:
    """True when a seat has no Unit/role and is not Admin / General Manager."""
    if profile is None:
        return True
    if profile.is_admin or profile.is_general_manager:
        return False
    return not (profile.unit or "").strip() and not (profile.role or "").strip()


@transaction.atomic
def purge_unassigned_seats() -> int:
    """Delete catalogue seats with no Unit & role (the old «Unassigned» group).

    Skips the dedicated ``admin`` login. Seats that cannot be deleted because of
    FK history are left alone but should still be filtered out of UI lists.
    """
    removed = 0
    qs = (
        User.objects.select_related("profile")
        .filter(profile__is_admin=False, profile__is_general_manager=False)
        .exclude(username__iexact="admin")
    )
    for user in qs:
        profile = getattr(user, "profile", None)
        if not is_blank_org_seat(profile):
            continue
        try:
            link = getattr(user, "person_link", None)
            if link is not None:
                link.delete()
            PersonRole.objects.filter(source_user=user).delete()
            user.delete()
            removed += 1
        except Exception:
            logger.exception("Could not purge unassigned seat user=%s", user.pk)
    return removed


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def available_seats():
    """Unassigned seats ready for the Available seats picker (QuerySet)."""
    qs = (
        User.objects.filter(
            person_link__isnull=True,
            profile__seat_ready=True,
        )
        .exclude(profile__seat_code__isnull=True)
        .exclude(profile__seat_code="")
        .select_related("profile")
        .order_by("profile__seat_code", "username")
    )
    extra_admin_ids = list(
        User.objects.filter(
            person_link__isnull=True,
            profile__seat_ready=True,
            profile__is_admin=True,
        )
        .exclude(profile__seat_code__isnull=True)
        .exclude(profile__seat_code="")
        .order_by("pk")
        .values_list("pk", flat=True)[1:]
    )
    if extra_admin_ids:
        qs = qs.exclude(pk__in=extra_admin_ids)
    return qs


def seats_of(person):
    return (
        person.accounts.select_related("user", "user__profile", "assigned_by")
        .order_by("assigned_at", "pk")
    )


def roles_of(person):
    return person.roles.select_related(
        "source_user", "source_user__profile",
    ).order_by("created_at", "pk")


def _role_source_users(person) -> list:
    """Distinct source Users for this person's roles, earliest role first."""
    seen = set()
    out = []
    for role in roles_of(person):
        su = role.source_user
        if su is None or su.pk in seen:
            continue
        seen.add(su.pk)
        out.append(su)
    return out


def primary_login(person):
    """The person's sign-in User: earliest role seat, else linked account."""
    sources = _role_source_users(person)
    if sources:
        return sources[0]

    links = list(
        person.accounts.select_related("user", "user__profile").order_by(
            "assigned_at", "pk",
        )
    )
    if not links:
        return None
    person_user = (person.username or "").strip().lower()
    if person_user:
        for link in links:
            if (link.user.username or "").lower() == person_user:
                return link.user
    # Prefer a bare (no seat_code) profile login over random seat links.
    for link in links:
        if not (link.user.profile.seat_code or "").strip():
            return link.user
    for link in links:
        if link.user.is_active:
            return link.user
    return links[0].user


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
def _write_identity(user, username, *, first_name, last_name) -> bool:
    if username != user.username and User.objects.filter(
            username__iexact=username).exclude(pk=user.pk).exists():
        return False
    try:
        with transaction.atomic():
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.save(update_fields=IDENTITY_FIELDS)
        return True
    except IntegrityError:
        user.refresh_from_db(fields=["username"])
        return False


def apply_person_identity(user, person) -> str:
    """Put ``person``'s Latin name and username on ``user``. Idempotent."""
    base = (person.username or "").strip()
    if not base:
        raise SeatError(
            "This person has no username yet; save their Latin name first.")

    for candidate in seat_username_candidates(base):
        if _write_identity(user, candidate,
                           first_name=(person.first_name_en or "").strip(),
                           last_name=(person.last_name_en or "").strip()):
            return candidate
    raise SeatError("Could not find a free username for this person.")


def _neutral_username_for(user) -> str:
    profile = getattr(user, "profile", None)
    code = (getattr(profile, "seat_code", None) or "").strip()
    if code:
        return vacant_login_username(code, user_pk=user.pk)
    return vacant_login_username(f"u{user.pk}", user_pk=user.pk)


def reset_seat_identity(user) -> str:
    """Clear human name fields; restore vacant login username. Keeps seat_code."""
    for candidate in seat_username_candidates(_neutral_username_for(user)):
        if _write_identity(user, candidate, first_name="", last_name=""):
            profile = getattr(user, "profile", None)
            if profile is not None and profile.must_change_password:
                # Vacant / secondary seats are not signed into — the amber
                # "Pending change" tag only applies to a real login waiting
                # for its first password change.
                profile.must_change_password = False
                profile.save(update_fields=["must_change_password"])
            return candidate
    raise SeatError("Could not find a free placeholder username for this account.")


def _retire_unlinked_user(user) -> None:
    """After unlinking from a person: vacant seat or neutralized bare login."""
    profile = getattr(user, "profile", None)
    code = (getattr(profile, "seat_code", None) or "").strip() if profile else ""
    user.is_active = False
    user.save(update_fields=["is_active"])
    if code:
        try:
            reset_seat_identity(user)
        except SeatError:
            pass
        if profile is not None:
            profile.seat_ready = True
            profile.must_change_password = False
            profile.save(update_fields=["seat_ready", "must_change_password"])
        return
    for candidate in seat_username_candidates(f"_retired{user.pk}"):
        if _write_identity(user, candidate, first_name="", last_name=""):
            break
    user.set_unusable_password()
    user.save(update_fields=["password"])
    if profile is not None and profile.must_change_password:
        profile.must_change_password = False
        profile.save(update_fields=["must_change_password"])


def _harden_secondary_seat(user) -> None:
    """Secondary role seats: vacant username, cannot authenticate."""
    code = (getattr(getattr(user, "profile", None), "seat_code", None) or "").strip()
    if code:
        try:
            reset_seat_identity(user)
        except SeatError:
            user.first_name = ""
            user.last_name = ""
            user.save(update_fields=["first_name", "last_name"])
    else:
        user.first_name = ""
        user.last_name = ""
        user.save(update_fields=["first_name", "last_name"])
    user.set_unusable_password()
    user.save(update_fields=["password"])
    profile = getattr(user, "profile", None)
    if profile is not None and profile.must_change_password:
        profile.must_change_password = False
        profile.save(update_fields=["must_change_password"])


def _transfer_login_secret(from_user, to_user) -> None:
    """Move password (and must-change flag) when the primary seat changes."""
    if from_user is None or to_user is None or from_user.pk == to_user.pk:
        return
    to_user.password = from_user.password
    to_user.save(update_fields=["password"])
    src = getattr(from_user, "profile", None)
    dst = getattr(to_user, "profile", None)
    if src is not None and dst is not None:
        dst.must_change_password = bool(src.must_change_password)
        dst.save(update_fields=["must_change_password"])


def _sync_identity_fields(profile, person):
    if person.detail_code:
        profile.org_number = person.detail_code
    # Live profile code follows the person (for inbox / assignee labels).
    # Historical case displays stay frozen separately.
    profile.internal_code = (person.internal_code or "").strip()
    gender_map = {
        "آقا": "MALE", "خانم": "FEMALE",
        "MALE": "MALE", "FEMALE": "FEMALE",
        "male": "MALE", "female": "FEMALE",
    }
    mapped = gender_map.get((person.gender or "").strip())
    if mapped:
        profile.gender = mapped
    profile.seat_ready = False
    profile.save(update_fields=[
        "org_number", "internal_code", "gender", "seat_ready",
    ])


def _person_internal_code(person) -> str:
    return (getattr(person, "internal_code", None) or "").strip()


def _role_kwargs_from_profile(profile, person=None) -> dict:
    code = ""
    if person is not None:
        code = _person_internal_code(person)
    if not code:
        code = profile.internal_code or ""
    return {
        "unit": profile.unit or "",
        "role": profile.role or "",
        "supply_kind": profile.supply_kind or "",
        "internal_code": code,
        "is_admin": bool(profile.is_admin),
        "is_general_manager": bool(profile.is_general_manager),
    }


def _ensure_person_role(person, profile, *, source_user=None) -> PersonRole:
    kw = _role_kwargs_from_profile(profile, person=person)
    # Match without internal_code so renaming a person's code does not fork roles.
    match = {
        "unit": kw["unit"],
        "role": kw["role"],
        "supply_kind": kw["supply_kind"],
        "is_admin": kw["is_admin"],
        "is_general_manager": kw["is_general_manager"],
    }
    existing = person.roles.filter(**match).first()
    if existing:
        updates = []
        if source_user is not None and existing.source_user_id is None:
            existing.source_user = source_user
            updates.append("source_user")
        if (existing.internal_code or "") != kw["internal_code"]:
            existing.internal_code = kw["internal_code"]
            updates.append("internal_code")
        if updates:
            existing.save(update_fields=updates)
        return existing
    return PersonRole.objects.create(
        person=person, source_user=source_user, **kw,
    )


def _bump_seat_stats(user, *, vacated: bool = False):
    profile = getattr(user, "profile", None)
    if profile is None:
        return
    profile.assignment_count = int(profile.assignment_count or 0) + 1
    fields = ["assignment_count"]
    if vacated:
        profile.last_vacated_at = timezone.now()
        fields.append("last_vacated_at")
    profile.save(update_fields=fields)


def apply_role_to_profile(user, person_role: PersonRole):
    """Write a PersonRole onto the login profile (active role switch).

    Secondary seats (``source_user`` ≠ login) must NOT rewrite unit/role on the
    login profile: that clashes with ``UniqueConstraint`` on
    ``(seat_code, unit, role, …)`` while the login's seat_code stays on its
    own pool. Session ``active_role_id`` + ``work_context`` drive inbox instead.

    General Manager is an exception: the login profile must keep
    ``is_general_manager`` whenever the person still holds a GM PersonRole.
    """
    profile = user.profile
    source = person_role.source_user
    person = person_role.person
    code = _person_internal_code(person) or (person_role.internal_code or "")
    has_gm = bool(person_role.is_general_manager) or person.roles.filter(
        is_general_manager=True,
    ).exists()

    # Secondary / foreign seat — only sync safe display fields (+ GM flag).
    if source is not None and source.pk != user.pk:
        fields = []
        if (profile.internal_code or "") != code:
            profile.internal_code = code
            fields.append("internal_code")
        if bool(profile.is_general_manager) != has_gm:
            profile.is_general_manager = has_gm
            fields.append("is_general_manager")
        if fields:
            profile.save(update_fields=fields)
        return

    profile.unit = person_role.unit or ""
    profile.role = person_role.role or ""
    profile.supply_kind = person_role.supply_kind or ""
    profile.internal_code = code
    # Never promote a non-admin login to platform administrator via a role.
    profile.is_admin = (
        bool(person_role.is_admin)
        and (user.username or "").strip().lower() == "admin"
    )
    profile.is_general_manager = has_gm and not profile.is_admin
    if profile.is_admin:
        profile.unit = ""
        profile.role = ""
        profile.supply_kind = ""
        profile.is_general_manager = False
    try:
        profile.save(update_fields=[
            "unit", "role", "supply_kind", "internal_code",
            "is_admin", "is_general_manager",
        ])
    except IntegrityError:
        # Pool clash — leave organisational fields alone; session still works.
        logger.exception(
            "apply_role_to_profile IntegrityError user=%s role=%s",
            getattr(user, "pk", None), getattr(person_role, "pk", None),
        )
        raise


# ---------------------------------------------------------------------------
# Assigning and releasing
# ---------------------------------------------------------------------------
@transaction.atomic
def assign_seat(person, user, actor=None) -> PersonAccount | PersonRole:
    """Give one seat's role to one person.

    First organisational seat → primary login + identity + PersonRole.
    Further seats → PersonRole + linked (Held by), vacant username, Active
    while the person is active, unusable password. Out of Available until
    released.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        raise SeatError("This account has no profile.")

    if PersonAccount.objects.filter(user=user).exclude(person=person).exists():
        raise SeatError(f"«{user.username}» is already held by someone else.")

    existing_login = primary_login(person)
    new_code = (profile.seat_code or "").strip()

    # Bare profile-only login + assigning a real seat → promote this seat.
    if (
        existing_login is not None
        and existing_login.pk != user.pk
        and new_code
        and not (existing_login.profile.seat_code or "").strip()
    ):
        link = getattr(existing_login, "person_link", None)
        if link is not None and link.person_id == person.pk:
            link.delete()
        _transfer_login_secret(existing_login, user)
        _retire_unlinked_user(existing_login)
        existing_login = None

    # --- Already has a role-seat login: add another role seat -----------------
    if existing_login is not None and existing_login.pk != user.pk:
        if PersonAccount.objects.filter(user=user).exists():
            raise SeatError(f"«{user.username}» is already held by someone else.")
        role = _ensure_person_role(person, profile, source_user=user)
        PersonAccount.objects.create(
            person=person, user=user, assigned_by=actor,
        )
        _harden_secondary_seat(user)
        profile.is_admin = False
        profile.seat_ready = False
        profile.save(update_fields=["seat_ready", "is_admin"])
        _bump_seat_stats(user, vacated=False)
        prim_prof = existing_login.profile
        if role.is_general_manager and not prim_prof.is_general_manager:
            prim_prof.is_general_manager = True
            prim_prof.save(update_fields=["is_general_manager"])
        if not (prim_prof.unit or prim_prof.is_admin or prim_prof.is_general_manager):
            apply_role_to_profile(existing_login, role)
        sync_person_users_active(person)
        logger.info(
            "Role from seat %s linked onto person %s (login %s) by %s",
            user.pk, person.pk, existing_login.pk, getattr(actor, "pk", None),
        )
        _open_owner_tenure(user, person, role, actor=actor)
        _log_seat_event(
            user, "ASSIGNED", actor=actor, to_person=person,
            payload={"role_id": role.pk, "secondary": True},
        )
        return role

    # --- First / primary login ------------------------------------------------
    try:
        with transaction.atomic():
            link, created = PersonAccount.objects.get_or_create(
                user=user,
                defaults={"person": person, "assigned_by": actor},
            )
            if not created and link.person_id != person.pk:
                raise SeatError(f"«{user.username}» is already held by someone else.")
    except IntegrityError as exc:
        raise SeatError(
            f"«{user.username}» is already held by someone else.") from exc

    apply_person_identity(user, person)
    user.is_active = bool(person.is_active)
    user.save(update_fields=["is_active"])
    if profile.is_admin:
        profile.is_admin = False
    role = _ensure_person_role(person, profile, source_user=user)
    _bump_seat_stats(user, vacated=False)
    _sync_identity_fields(profile, person)
    sync_person_users_active(person)
    _open_owner_tenure(user, person, role, actor=actor)
    _log_seat_event(
        user, "ASSIGNED", actor=actor, to_person=person,
        payload={"role_id": role.pk},
    )

    logger.info("Seat %s assigned to person %s by %s",
                user.pk, person.pk, getattr(actor, "pk", None))
    return link


def _log_seat_vacation(user, person, *, actor=None, assigned_at=None) -> None:
    """Record who held this seat before it was freed (for Seats history columns)."""
    from .models import SeatAssignmentLog

    SeatAssignmentLog.objects.create(
        seat_user=user,
        person=person if getattr(person, "pk", None) else None,
        person_name=(getattr(person, "display_name", None) or "").strip(),
        detail_code=(getattr(person, "detail_code", None) or "").strip(),
        assigned_at=assigned_at,
        vacated_at=timezone.now(),
        vacated_by=actor,
    )


def _free_seat_user(user, person=None, *, actor=None) -> str | None:
    """Unlink, reset identity, mark ready — returns vacant username if known."""
    link = getattr(user, "person_link", None)
    assigned_at = getattr(link, "assigned_at", None) if link else None
    if link is not None and (person is None or link.person_id == person.pk):
        if person is None:
            person = link.person
        link.delete()
    if person is not None:
        _log_seat_vacation(user, person, actor=actor, assigned_at=assigned_at)
    try:
        freed = reset_seat_identity(user)
    except SeatError:
        freed = user.username
    user.is_active = False
    user.save(update_fields=["is_active"])
    _bump_seat_stats(user, vacated=True)
    profile = getattr(user, "profile", None)
    if profile is not None:
        profile.seat_ready = True
        profile.save(update_fields=["seat_ready"])
    return freed


@transaction.atomic
def release_role(person_role: PersonRole, *, actor=None) -> str | None:
    """Remove one role and return its source seat to the catalogue.

    Last role frees the primary seat too (so the role stays re-assignable).
    """
    person = person_role.person
    source = person_role.source_user
    person_role.delete()
    remaining = list(roles_of(person))
    login = primary_login(person)

    freed = None
    if source is not None:
        # Free secondary source seats immediately.
        if login is None or source.pk != login.pk:
            freed = _free_seat_user(source, person, actor=actor)
        elif not remaining:
            # Last role on the primary login — free that seat too.
            if login is not None and login.pk == source.pk:
                freed = _free_seat_user(source, person, actor=actor)
            elif login is not None:
                freed = _free_seat_user(login, person, actor=actor)

    if remaining:
        login = primary_login(person)
        if login is not None:
            apply_role_to_profile(login, remaining[0])
        return freed

    # No roles left — ensure no dangling PersonAccount without a role seat.
    login = primary_login(person)
    if login is not None and getattr(login, "person_link", None) is not None:
        # Only free if this was not already freed above.
        if source is None or login.pk != getattr(source, "pk", None):
            freed = _free_seat_user(login, person, actor=actor) or freed
    return freed


@transaction.atomic
def close_role(person_role: PersonRole, *, actor=None) -> str | None:
    """Close a role only when it has zero open tasks (inbox count)."""
    return close_seat(person_role, actor=actor)


@transaction.atomic
def translate_role(person_role: PersonRole, to_person, *, actor=None) -> PersonRole:
    """Temporarily hand the same seat User to another person (substitute).

    Preserves the seat User and all case history. Opens a SUBSTITUTE tenure
    with ``origin_person`` for Return. Live identity becomes the destination.
    """
    from .models import Person, SeatTenure

    if not isinstance(to_person, Person):
        raise SeatError("Choose a person to receive this role.")
    if to_person.pk == person_role.person_id:
        raise SeatError("That person already holds this role.")
    if not to_person.is_active:
        raise SeatError("Cannot translate a role onto a departed person.")

    from_person = person_role.person
    source = person_role.source_user
    if source is None:
        raise SeatError("This role has no seat to translate.")

    # Destination must not already hold the same organisational combo.
    clash = to_person.roles.filter(
        unit=person_role.unit or "",
        role=person_role.role or "",
        supply_kind=person_role.supply_kind or "",
        is_admin=bool(person_role.is_admin),
        is_general_manager=bool(person_role.is_general_manager),
    ).exists()
    if clash:
        raise SeatError(
            f"{to_person.display_name} already has «{person_role.title_line}»."
        )

    # Already a substitute? origin stays the original owner.
    open_sub = (
        SeatTenure.objects.filter(
            source_user=source, kind=SeatTenure.KIND_SUBSTITUTE, ended_at__isnull=True,
        ).first()
    )
    origin_person = open_sub.origin_person if open_sub else from_person

    # Detach from source person without freeing the seat into the pool.
    link = getattr(source, "person_link", None)
    assigned_at = getattr(link, "assigned_at", None) if link else None
    if link is not None and link.person_id == from_person.pk:
        link.delete()
    _log_seat_vacation(
        source, from_person, actor=actor, assigned_at=assigned_at,
    )

    person_role.person = to_person
    person_role.internal_code = _person_internal_code(to_person)
    person_role.save(update_fields=["person", "internal_code"])

    # Remaining roles on the source person — re-home active profile if needed.
    remaining = list(roles_of(from_person))
    old_login = primary_login(from_person)
    if remaining and old_login is not None:
        apply_role_to_profile(old_login, remaining[0])
    elif old_login is not None and getattr(old_login, "person_link", None):
        if old_login.pk != source.pk:
            _free_seat_user(old_login, from_person, actor=actor)

    # Attach seat to destination.
    try:
        with transaction.atomic():
            new_link, created = PersonAccount.objects.get_or_create(
                user=source,
                defaults={"person": to_person, "assigned_by": actor},
            )
            if not created:
                if new_link.person_id != to_person.pk:
                    raise SeatError(
                        f"«{source.username}» is already held by someone else.")
                new_link.person = to_person
                new_link.assigned_by = actor
                new_link.save(update_fields=["person", "assigned_by"])
    except IntegrityError as exc:
        raise SeatError(
            f"«{source.username}» is already held by someone else.") from exc

    dest_sources = _role_source_users(to_person)
    is_primary = not dest_sources or dest_sources[0].pk == source.pk
    if is_primary or (dest_sources and dest_sources[0].pk == source.pk):
        apply_person_identity(source, to_person)
        source.is_active = bool(to_person.is_active)
        source.save(update_fields=["is_active"])
        apply_role_to_profile(source, person_role)
        _sync_identity_fields(source.profile, to_person)
    else:
        _harden_secondary_seat(source)
        source.is_active = bool(to_person.is_active)
        source.save(update_fields=["is_active"])
        if hasattr(source, "profile"):
            source.profile.seat_ready = False
            source.profile.internal_code = _person_internal_code(to_person)
            source.profile.save(update_fields=["seat_ready", "internal_code"])

    _end_open_tenures(source, reason=SeatTenure.REASON_TRANSLATE)
    _open_substitute_tenure(
        source, to_person, person_role, origin_person=origin_person, actor=actor,
    )
    _log_seat_event(
        source, "TRANSLATED", actor=actor,
        from_person=from_person, to_person=to_person,
        payload={
            "role_id": person_role.pk,
            "origin_person_id": origin_person.pk if origin_person else None,
        },
    )

    _bump_seat_stats(source, vacated=False)
    sync_person_users_active(from_person)
    sync_person_users_active(to_person)
    logger.info(
        "Role %s translated from person %s to %s by %s",
        person_role.pk, from_person.pk, to_person.pk, getattr(actor, "pk", None),
    )
    return person_role


@transaction.atomic
def release_seat(link, *, actor=None) -> str:
    """Take one linked seat back (and all roles if it was the last link)."""
    user = link.user
    person = link.person
    person_pk = person.pk
    was_primary = primary_login(person) is not None and primary_login(person).pk == user.pk

    # Drop roles that came from this seat.
    person.roles.filter(source_user=user).delete()
    freed = _free_seat_user(user, person, actor=actor) or user.username

    if was_primary:
        # Re-home login onto another linked seat if any remain.
        other = primary_login(person)
        if other is not None:
            _transfer_login_secret(user, other)
            apply_person_identity(other, person)
            other.is_active = bool(person.is_active)
            other.save(update_fields=["is_active"])
            roles = list(roles_of(person))
            if roles:
                apply_role_to_profile(other, roles[0])
        else:
            person.roles.all().delete()

    logger.info("Seat %s released from person %s, renamed to %s",
                user.pk, person_pk, freed)
    return freed


@transaction.atomic
def reconcile_person_accounts(person) -> None:
    """Align PersonAccounts with role seats; drop orphan logins (e.g. user15).

    When the person has role source seats, those are the only held accounts.
    Identity lives on the earliest role seat; other role seats are hardened.
    """
    if not person.pk:
        return

    sources = _role_source_users(person)
    if not sources:
        sync_person_users_active(person)
        return

    source_ids = {u.pk for u in sources}
    primary = sources[0]

    for su in sources:
        link = getattr(su, "person_link", None)
        if link is None:
            PersonAccount.objects.create(person=person, user=su)
        elif link.person_id != person.pk:
            raise SeatError(
                f"Seat «{su.username}» is held by someone else; cannot reconcile.")

    # Whoever currently holds the real username (often a bare orphan login).
    secret_from = None
    person_user = (person.username or "").strip()
    if person_user:
        for link in seats_of(person):
            if (link.user.username or "").lower() == person_user.lower():
                secret_from = link.user
                break
        if secret_from is None:
            hit = User.objects.filter(username__iexact=person_user).first()
            if hit is not None:
                secret_from = hit

    for link in list(seats_of(person)):
        if link.user_id in source_ids:
            continue
        orphan = link.user
        link.delete()
        if secret_from is not None and secret_from.pk == orphan.pk:
            _transfer_login_secret(orphan, primary)
            secret_from = primary
        _retire_unlinked_user(orphan)
        logger.info(
            "Unlinked orphan login %s from person %s", orphan.pk, person.pk,
        )

    if secret_from is not None and secret_from.pk != primary.pk:
        _transfer_login_secret(secret_from, primary)

    apply_person_identity(primary, person)
    for su in sources[1:]:
        _harden_secondary_seat(su)
        prof = getattr(su, "profile", None)
        if prof is not None:
            prof.seat_ready = False
            if prof.is_admin:
                prof.is_admin = False
            prof.save(update_fields=["seat_ready", "is_admin"])

    _sync_identity_fields(primary.profile, person)
    roles = list(roles_of(person))
    if roles:
        apply_role_to_profile(primary, roles[0])
    sync_person_users_active(person)


def ensure_person_login(person, actor=None):
    """Ensure an active person has a login User (profile-only until roles exist)."""
    from accounts.forms import generate_temp_password

    if not person.pk:
        return None

    # Role seats win over any bare login — repair orphans first.
    if _role_source_users(person):
        reconcile_person_accounts(person)
        login = primary_login(person)
        if not person.is_active:
            return login
        if login is not None:
            apply_person_identity(login, person)
        return login

    if not person.is_active:
        login = primary_login(person)
        sync_person_users_active(person)
        return login

    base = (person.username or "").strip()
    if not base:
        return None

    login = primary_login(person)
    if login is not None:
        apply_person_identity(login, person)
        sync_person_users_active(person)
        return login

    password = generate_temp_password()
    username = base
    if User.objects.filter(username__iexact=username).exists():
        username = None
        for candidate in seat_username_candidates(base):
            if not User.objects.filter(username__iexact=candidate).exists():
                username = candidate
                break
        if not username:
            raise SeatError("Could not allocate a login username for this person.")

    user = User.objects.create_user(
        username=username,
        password=password,
        first_name=(person.first_name_en or "").strip(),
        last_name=(person.last_name_en or "").strip(),
        email=person.email or "",
    )
    user.is_active = True
    user.save(update_fields=["is_active"])
    profile = user.profile
    profile.must_change_password = True
    profile.seat_ready = False
    profile.unit = ""
    profile.role = ""
    profile.supply_kind = ""
    if person.detail_code:
        profile.org_number = person.detail_code
    profile.save()
    PersonAccount.objects.create(person=person, user=user, assigned_by=actor)
    logger.info("Created profile-only login %s for person %s", user.pk, person.pk)
    return user


def sync_person_users_active(person) -> None:
    """Active person → every linked seat Active; departed → all linked off.

    Never deletes users or case history — only flips ``is_active``.
    Sign-in remains only on the primary login (secondaries have unusable
    passwords).
    """
    want = bool(person.is_active)
    for link in seats_of(person):
        user = link.user
        if user.is_active != want:
            user.is_active = want
            user.save(update_fields=["is_active"])


def refresh_person_seats(person) -> int:
    """Re-apply the person's current name to their primary login."""
    if not (person.username or "").strip():
        return 0
    ensure_person_login(person)
    login = primary_login(person)
    if login is None:
        return 0
    before = login.username
    apply_person_identity(login, person)
    sync_person_users_active(person)
    return 1 if login.username != before else 0


# ---------------------------------------------------------------------------
# Tenure / events / Return / Close / Delegate
# ---------------------------------------------------------------------------
def _person_label(person) -> str:
    if person is None:
        return ""
    return (getattr(person, "display_name", None) or "").strip()


def _log_seat_event(
    source_user, event, *, actor=None, from_person=None, to_person=None, payload=None,
):
    from .models import SeatEventLog
    SeatEventLog.objects.create(
        source_user=source_user,
        event=event,
        actor=actor,
        from_person=from_person,
        to_person=to_person,
        from_person_name=_person_label(from_person),
        to_person_name=_person_label(to_person),
        payload=payload or {},
    )


def _end_open_tenures(source_user, *, reason: str):
    from .models import SeatTenure
    now = timezone.now()
    SeatTenure.objects.filter(source_user=source_user, ended_at__isnull=True).update(
        ended_at=now, ended_reason=reason,
    )


def _open_owner_tenure(source_user, person, person_role, *, actor=None):
    from .models import SeatTenure
    _end_open_tenures(source_user, reason=SeatTenure.REASON_ASSIGN)
    SeatTenure.objects.create(
        source_user=source_user,
        person=person,
        person_role=person_role,
        kind=SeatTenure.KIND_OWNER,
        origin_person=None,
    )


def _open_substitute_tenure(
    source_user, person, person_role, *, origin_person, actor=None,
):
    from .models import SeatTenure
    SeatTenure.objects.create(
        source_user=source_user,
        person=person,
        person_role=person_role,
        kind=SeatTenure.KIND_SUBSTITUTE,
        origin_person=origin_person,
    )


def open_task_count(person_role: PersonRole) -> int:
    """Count non-ended cases still tied to this seat (not merely inbox rows)."""
    return open_cases_for_seat(person_role).count()


def current_tenure(source_user):
    from .models import SeatTenure
    if source_user is None:
        return None
    return (
        SeatTenure.objects.filter(source_user=source_user, ended_at__isnull=True)
        .select_related("person", "origin_person", "person_role")
        .first()
    )


@transaction.atomic
def return_role(person_role: PersonRole, *, actor=None) -> PersonRole:
    """Return a SUBSTITUTE tenure to the origin (owner) person."""
    from .models import SeatTenure

    source = person_role.source_user
    if source is None:
        raise SeatError("This role has no seat to return.")
    tenure = current_tenure(source)
    if tenure is None or tenure.kind != SeatTenure.KIND_SUBSTITUTE:
        raise SeatError("This seat is not held by a substitute — nothing to return.")
    origin = tenure.origin_person
    if origin is None:
        raise SeatError("Origin owner is missing; cannot return this seat.")
    if not origin.is_active:
        raise SeatError("Cannot return this seat to a departed owner.")
    if origin.pk == person_role.person_id:
        raise SeatError("This seat already belongs to the origin owner.")

    holder = person_role.person
    # Move role back without opening another SUBSTITUTE tenure.
    clash = origin.roles.filter(
        unit=person_role.unit or "",
        role=person_role.role or "",
        supply_kind=person_role.supply_kind or "",
        is_admin=bool(person_role.is_admin),
        is_general_manager=bool(person_role.is_general_manager),
    ).exclude(pk=person_role.pk).exists()
    if clash:
        raise SeatError(
            f"{origin.display_name} already has «{person_role.title_line}»."
        )

    link = getattr(source, "person_link", None)
    assigned_at = getattr(link, "assigned_at", None) if link else None
    if link is not None and link.person_id == holder.pk:
        link.delete()
    _log_seat_vacation(source, holder, actor=actor, assigned_at=assigned_at)

    person_role.person = origin
    person_role.internal_code = _person_internal_code(origin)
    person_role.save(update_fields=["person", "internal_code"])

    remaining = list(roles_of(holder))
    old_login = primary_login(holder)
    if remaining and old_login is not None:
        apply_role_to_profile(old_login, remaining[0])
    elif old_login is not None and getattr(old_login, "person_link", None):
        if old_login.pk != source.pk:
            _free_seat_user(old_login, holder, actor=actor)

    try:
        with transaction.atomic():
            new_link, created = PersonAccount.objects.get_or_create(
                user=source,
                defaults={"person": origin, "assigned_by": actor},
            )
            if not created:
                if new_link.person_id != origin.pk:
                    raise SeatError(
                        f"«{source.username}» is already held by someone else.")
                new_link.person = origin
                new_link.assigned_by = actor
                new_link.save(update_fields=["person", "assigned_by"])
    except IntegrityError as exc:
        raise SeatError(
            f"«{source.username}» is already held by someone else.") from exc

    dest_sources = _role_source_users(origin)
    is_primary = not dest_sources or dest_sources[0].pk == source.pk
    if is_primary:
        apply_person_identity(source, origin)
        source.is_active = bool(origin.is_active)
        source.save(update_fields=["is_active"])
        apply_role_to_profile(source, person_role)
        _sync_identity_fields(source.profile, origin)
    else:
        _harden_secondary_seat(source)
        source.is_active = bool(origin.is_active)
        source.save(update_fields=["is_active"])
        if hasattr(source, "profile"):
            source.profile.seat_ready = False
            source.profile.internal_code = _person_internal_code(origin)
            source.profile.save(update_fields=["seat_ready", "internal_code"])

    _end_open_tenures(source, reason=SeatTenure.REASON_RETURN)
    _open_owner_tenure(source, origin, person_role, actor=actor)
    _log_seat_event(
        source, "RETURNED", actor=actor,
        from_person=holder, to_person=origin,
        payload={"role_id": person_role.pk},
    )
    _bump_seat_stats(source, vacated=False)
    sync_person_users_active(holder)
    sync_person_users_active(origin)
    return person_role


@transaction.atomic
def close_seat(person_role: PersonRole, *, actor=None) -> str | None:
    """Free the seat when open tasks are zero. Substitutes must Return first
    only if they still have open tasks — Close after Delegate is allowed.
    """
    from .models import SeatTenure

    tasks = open_task_count(person_role)
    if tasks > 0:
        raise SeatError(
            f"Cannot close this seat while it has {tasks} open task(s). "
            "Delegate the open tasks first."
        )
    source = person_role.source_user
    person = person_role.person
    tenure = current_tenure(source)
    if tenure is not None and tenure.kind == SeatTenure.KIND_SUBSTITUTE:
        # Closing while substitute frees the seat (owner does not auto-return).
        pass
    if source is not None:
        _end_open_tenures(source, reason=SeatTenure.REASON_CLOSE)
        _log_seat_event(
            source, "CLOSED", actor=actor, from_person=person,
            payload={"role_id": person_role.pk},
        )
    freed = release_role(person_role, actor=actor)
    if source is not None:
        _log_seat_event(source, "VACANT", actor=actor, from_person=person)
    return freed


def delegate_allowed_roles(person_role: PersonRole) -> set[str]:
    """Roles that may receive open tasks from this seat.

    Same organisational role always. Experts may also hand to the unit
    Supervisor or Manager; Supervisors may hand to the Manager.
    """
    from accounts.constants import Role
    roles = {(person_role.role or "").strip()}
    if person_role.role == Role.EXPERT:
        roles.update({Role.MANAGER, Role.SUPERVISOR})
    elif person_role.role == Role.SUPERVISOR:
        roles.add(Role.MANAGER)
    return {r for r in roles if r}


def _supply_compatible(src_kind: str, dest_kind: str) -> bool:
    src = (src_kind or "").strip()
    dest = (dest_kind or "").strip()
    if not src or not dest:
        return True
    if dest == "BOTH" or src == "BOTH":
        return True
    return src == dest


def find_delegate_dest_role(person_role: PersonRole, to_person):
    """Best PersonRole on ``to_person`` that may receive tasks from ``person_role``."""
    allowed = delegate_allowed_roles(person_role)
    unit = person_role.unit or ""
    supply = person_role.supply_kind or ""
    candidates = list(
        to_person.roles.filter(
            unit=unit,
            role__in=allowed,
            source_user__isnull=False,
            is_admin=False,
            is_general_manager=False,
        ).select_related("source_user")
    )
    candidates = [
        r for r in candidates
        if _supply_compatible(supply, r.supply_kind or "")
    ]
    if not candidates:
        return None
    # Prefer exact same role (+ matching supply), then manager, then supervisor.
    from accounts.constants import Role
    def rank(r):
        same = 0 if r.role == person_role.role else 1
        sk = 0 if (r.supply_kind or "") == supply else 1
        role_ord = {Role.EXPERT: 0, Role.SUPERVISOR: 1, Role.MANAGER: 2}.get(r.role, 9)
        # When not exact role, prefer manager over supervisor.
        if r.role != person_role.role:
            role_ord = {Role.MANAGER: 0, Role.SUPERVISOR: 1, Role.EXPERT: 2}.get(r.role, 9)
        return (same, sk, role_ord, r.pk)
    candidates.sort(key=rank)
    return candidates[0]


def delegate_recipients(person_role: PersonRole, *, exclude_person_id=None):
    """People who can receive delegated tasks (same role, or unit manager/supervisor)."""
    from people.constants import PersonStatus
    from .models import Person

    allowed = delegate_allowed_roles(person_role)
    unit = person_role.unit or ""
    qs = Person.objects.filter(
        status=PersonStatus.ACTIVE,
        roles__unit=unit,
        roles__role__in=allowed,
        roles__source_user__isnull=False,
        roles__is_admin=False,
        roles__is_general_manager=False,
    ).distinct().order_by("first_name_en", "last_name_en", "detail_code")
    if exclude_person_id:
        qs = qs.exclude(pk=exclude_person_id)

    out = []
    for person in qs:
        dest = find_delegate_dest_role(person_role, person)
        if dest is None:
            continue
        out.append({
            "person": person,
            "dest_role": dest,
            "label": f"{person.display_name} · {person.detail_code} · {dest.title_line}",
        })
    return out


def same_role_holders(person_role: PersonRole, *, exclude_person_id=None):
    """People who already hold an active PersonRole with the same combo."""
    from people.constants import PersonStatus
    from .models import Person
    qs = Person.objects.filter(
        status=PersonStatus.ACTIVE,
        roles__unit=person_role.unit or "",
        roles__role=person_role.role or "",
        roles__supply_kind=person_role.supply_kind or "",
        roles__is_admin=bool(person_role.is_admin),
        roles__is_general_manager=bool(person_role.is_general_manager),
        roles__source_user__isnull=False,
    ).distinct().order_by("first_name_en", "last_name_en", "detail_code")
    if exclude_person_id:
        qs = qs.exclude(pk=exclude_person_id)
    return qs


def open_cases_for_seat(person_role: PersonRole):
    """Non-ended cases owned/assigned on this seat's source_user.

    ``ended`` = Final closed / Burned / Cancelled / Cannot-supply closed.
    Everything else (inbox, archive-but-still-actionable, With Supply, …) is
    still an open task and must appear in Delegate.
    """
    from cases.constants import CaseStatus
    from cases.models import Case
    from django.db.models import Q

    source = person_role.source_user
    if source is None:
        return Case.objects.none()
    ended = list(CaseStatus.ENDED)
    return Case.objects.filter(
        Q(created_by=source)
        | Q(assigned_to=source)
        | Q(technical_assignee=source)
        | Q(supply_assignee=source)
        | Q(supply_internal_assignee=source)
        | Q(supply_external_assignee=source)
        | Q(technical_internal_assignee=source)
        | Q(technical_external_assignee=source)
    ).exclude(status__in=ended).distinct()


@transaction.atomic
def delegate_tasks(
    person_role: PersonRole, to_person, case_ids, *, actor=None,
) -> int:
    """Move selected open cases to another person's compatible seat user.

    Recipient may hold the same role, or (for experts) the unit Supervisor /
    Manager seat in the same unit.
    """
    from .models import Person

    if not isinstance(to_person, Person):
        raise SeatError("Choose a person to receive the tasks.")
    if to_person.pk == person_role.person_id:
        raise SeatError("Cannot delegate tasks to the same person.")
    if not to_person.is_active:
        raise SeatError("Cannot delegate to a departed person.")

    dest_role = find_delegate_dest_role(person_role, to_person)
    if dest_role is None or dest_role.source_user_id is None:
        raise SeatError(
            f"{to_person.display_name} has no compatible seat "
            f"(same role, or unit manager/supervisor) for «{person_role.title_line}»."
        )

    source = person_role.source_user
    dest = dest_role.source_user
    if source is None:
        raise SeatError("This role has no seat.")
    if source.pk == dest.pk:
        raise SeatError("Source and destination seats are the same.")

    ids = [int(x) for x in case_ids if str(x).isdigit()]
    if not ids:
        raise SeatError("Select at least one open task to delegate.")

    open_qs = open_cases_for_seat(person_role).filter(pk__in=ids)
    cases = list(open_qs)
    if not cases:
        raise SeatError("No matching open tasks to delegate.")

    now = timezone.now()
    moved_ids = []
    from_name = _person_label(person_role.person) or "—"
    from_title = (person_role.title_line or "").strip()
    to_name = _person_label(to_person) or "—"
    to_title = (dest_role.title_line or "").strip()
    from_bit = f"{from_name} ({from_title})" if from_title else from_name
    to_bit = f"{to_name} ({to_title})" if to_title else to_name
    timeline_comment = f"From {from_bit} → {to_bit}"

    from cases.constants import EventAction
    from cases.services import log as case_log

    for case in cases:
        updates = ["is_delegated", "delegated_from_seat", "delegated_at"]
        case.is_delegated = True
        case.delegated_from_seat = source
        case.delegated_at = now
        if case.created_by_id == source.pk:
            case.created_by = dest
            updates.append("created_by")
        if case.assigned_to_id == source.pk:
            case.assigned_to = dest
            updates.append("assigned_to")
        if case.technical_assignee_id == source.pk:
            case.technical_assignee = dest
            updates.append("technical_assignee")
        if case.supply_assignee_id == source.pk:
            case.supply_assignee = dest
            updates.append("supply_assignee")
        if case.supply_internal_assignee_id == source.pk:
            case.supply_internal_assignee = dest
            updates.append("supply_internal_assignee")
        if case.supply_external_assignee_id == source.pk:
            case.supply_external_assignee = dest
            updates.append("supply_external_assignee")
        if case.technical_internal_assignee_id == source.pk:
            case.technical_internal_assignee = dest
            updates.append("technical_internal_assignee")
        if case.technical_external_assignee_id == source.pk:
            case.technical_external_assignee = dest
            updates.append("technical_external_assignee")
        case.save(update_fields=updates)
        # Case timeline: who delegated, from whom → to whom (date via created_at).
        case_log(
            case, actor, EventAction.DELEGATE,
            comment=timeline_comment,
            from_unit=person_role.unit or "",
            to_unit=dest_role.unit or "",
        )
        moved_ids.append(case.pk)

    _log_seat_event(
        source, "DELEGATED", actor=actor,
        from_person=person_role.person, to_person=to_person,
        payload={
            "case_ids": moved_ids,
            "count": len(moved_ids),
            "to_seat_id": dest.pk,
            "to_role_id": dest_role.pk,
        },
    )
    logger.info(
        "Delegated %s cases from seat %s to seat %s (person %s)",
        len(moved_ids), source.pk, dest.pk, to_person.pk,
    )
    return len(moved_ids)
