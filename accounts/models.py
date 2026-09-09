"""User profile model.

Each Django ``User`` is extended with one ``Profile`` that stores the unit, the
role, the organisational identity fields requested by the business, and the
uploaded signature image used when a form is approved.

The ``Profile`` is also what turns a bare ``User`` row into a *seat* — a unit
and a role, a job rather than a human — and it always carries the role that
account is acting under *right now*, which is why every permission check in the
platform reads it. How a seat relates to the human holding it, to the other
roles that human may switch into, and to the tenure history of the seat itself
is explained in full in the ``people.models`` module docstring, under "SEAT,
PERSON, ROLE". Read that before changing anything here that a seat depends on.

Also here: ``ImpersonationLog`` (the audit trail for "log in as user", written
by ``accounts.views``) and ``PlatformConfig`` — a single row of site-wide
settings such as the VAT percentage, the login welcome message, the unit stamps
and the default work-shift window inherited by people who have none of their
own.
"""
from datetime import time

from django.conf import settings
from django.db import models

from .constants import Gender, Language, Role, Unit, SupplyKind


def signature_upload_path(instance, filename):
    """Store each signature under media/signatures/<user-id>/<filename>."""
    return f"signatures/{instance.user_id}/{filename}"


def stamp_upload_path(instance, filename):
    """Store each stamp/seal under media/stamps/<user-id>/<filename>."""
    return f"stamps/{instance.user_id}/{filename}"


def stamp_commercial_upload_path(instance, filename):
    return f"stamps/units/commercial/{filename}"


def stamp_technical_upload_path(instance, filename):
    return f"stamps/units/technical/{filename}"


def stamp_supply_upload_path(instance, filename):
    return f"stamps/units/supply/{filename}"


def avatar_upload_path(instance, filename):
    return f"avatars/{instance.user_id}/{filename}"


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # An admin manages the whole platform (creates users, edits code tables,
    # uploads price/coding data). Admins do not belong to a working unit.
    is_admin = models.BooleanField(default=False)

    # A general manager sees everything (dashboard / archive / timeline of all
    # units) but cannot create users or open the admin console.
    is_general_manager = models.BooleanField(default=False)

    unit = models.CharField(max_length=20, choices=Unit.CHOICES, blank=True)
    role = models.CharField(max_length=20, choices=Role.CHOICES, blank=True)
    # Supply experts only: Internal vs External (blank for everyone else).
    supply_kind = models.CharField(max_length=20, choices=SupplyKind.CHOICES, blank=True)

    # Organisational identity (shown on the user's name/signature block).
    internal_code = models.CharField("Internal code", max_length=40, blank=True)
    org_number = models.CharField("Organisational number", max_length=40, blank=True)
    org_title = models.CharField(
        "Organisational title", max_length=120, blank=True,
        help_text="For example: Sales Expert, Technical Manager …",
    )

    # Used only to choose Mr./Ms. before the signer's last name on exported
    # documents (see honorific_last_name below). Blank is a valid, deliberate
    # default — nothing forces every existing account to be set retroactively;
    # a blank gender simply shows the last name with no honorific prefixed,
    # exactly as every export looked before this field existed.
    gender = models.CharField(max_length=10, choices=Gender.CHOICES, blank=True)

    # The platform CHROME language this person has chosen in Settings —
    # sidebar, tabs, buttons, field labels, status labels, page titles. Never
    # the CONTENT (a company's own name, a person's own comment/report/
    # reminder text, anything else someone typed), which is never translated
    # regardless of this setting. Deliberately a field on this per-user record
    # rather than a browser cookie or session key: it is a PER-PERSON
    # preference that must follow that person to any device/browser they sign
    # in from, exactly like every other Profile field, and not something a
    # fresh browser or a colleague's machine should ever reset. See
    # accounts.middleware.LanguageMiddleware for where this is read and
    # activated on every request, and accounts.constants.Language for why the
    # choice values are lowercase unlike this app's other choice fields.
    #
    # default="en" (rather than blank) so a freshly created account with
    # nobody having touched this yet renders in English — the same interface
    # language the platform has always shown, before this field existed.
    # blank=True only so the admin change-list / bulk tools that already treat
    # every Profile field as optional keep working; the Settings-page form is
    # the only place a person actually changes this, and it always posts one
    # of the two real choices.
    language = models.CharField(
        "Language", max_length=5, choices=Language.CHOICES, default=Language.ENGLISH,
        blank=True,
    )

    # Historical clear-text password copy. No longer written to (see the
    # 2026-07 security pass) — the column is kept only so nothing errors if a
    # very old cached page still references it, and is blanked by migration
    # 0007. Credentials are now only ever handed out once, at creation or
    # admin-triggered reset time, and never stored in readable form.
    plain_password = models.CharField(max_length=128, blank=True, default="")

    # Set whenever an admin creates the account or resets its password. The
    # user is forced to choose their own new password before doing anything
    # else, and the temporary one is never shown again after that first time.
    must_change_password = models.BooleanField(default=False)

    signature = models.ImageField(
        upload_to=signature_upload_path, blank=True, null=True,
        help_text="Signature image stamped on approved forms.",
    )
    stamp = models.ImageField(
        upload_to=stamp_upload_path, blank=True, null=True,
        help_text="Company/personal stamp stamped on approved forms.",
    )
    avatar = models.ImageField(
        upload_to=avatar_upload_path, blank=True, null=True,
        help_text="Circular profile photo shown in the header and My profile.",
    )

    # Seat catalogue (People → Available seats). New seats created by an admin
    # are ready immediately; legacy accounts appear only after an admin edits
    # them once (seat_ready flipped True).
    seat_ready = models.BooleanField(
        default=False,
        help_text="When True, this unassigned account is listed under Available seats.",
    )
    last_vacated_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the last person was released from this seat.",
    )
    assignment_count = models.PositiveIntegerField(
        default=0,
        help_text="How many times this seat has been handed to a person.",
    )
    # Display index inside a Unit+Role pool (``001``, ``002``, …) — not the
    # login username. Uniqueness is per role pool (see Meta.constraints).
    seat_code = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Role-local index such as 001. Unique within Unit+Role.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "seat_code", "unit", "role", "supply_kind",
                    "is_general_manager", "is_admin",
                ],
                name="accounts_profile_seat_index_pool",
                condition=models.Q(seat_code__isnull=False) & ~models.Q(seat_code=""),
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.title_line})"

    def save(self, *args, **kwargs):
        # Platform administrator is only the dedicated ``admin`` login.
        # Never grant or keep is_admin on any other seat / person login.
        username = ""
        user = getattr(self, "user", None)
        if user is not None:
            username = (getattr(user, "username", None) or "").strip().lower()
        if username == "admin":
            self.is_admin = True
            self.is_general_manager = False
            self.unit = ""
            self.role = ""
            self.supply_kind = ""
        elif username:
            # Deliberately NOT relaxed for Django superusers. Platform-admin is
            # the reserved ``admin`` login and nothing else, because is_admin is
            # not only a permission — cases.export_data.unit_manager() selects
            # the document signatory with ``is_admin=False``, so granting the
            # flag to a superuser who also holds a manager seat would drop them
            # out of the signatory pool and issue TO/PI documents with no name
            # and no signature. A bootstrap account that cannot reach the admin
            # console is a deployment problem and is solved in entrypoint.sh,
            # not by widening what is_admin means.
            self.is_admin = False
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            fields.add("is_admin")
            if username == "admin":
                fields.update(["is_general_manager", "unit", "role", "supply_kind"])
            kwargs["update_fields"] = list(fields)
        super().save(*args, **kwargs)

    # -- Display helpers ---------------------------------------------------
    @property
    def full_name(self) -> str:
        name = self.user.get_full_name().strip()
        return name or self.user.username

    @property
    def avatar_url(self) -> str:
        """Safe media URL for the profile photo, or empty if missing on disk."""
        field = self.avatar
        if not field or not getattr(field, "name", None):
            return ""
        try:
            if field.storage.exists(field.name):
                return field.url
        except Exception:
            return ""
        return ""

    @property
    def unit_label(self) -> str:
        # str(...) resolves the gettext_lazy proxy Unit.LABELS now holds
        # (accounts.constants.Unit.CHOICES) into a genuine ``str`` right here,
        # at property-access time — which is always during request handling,
        # after accounts.middleware.LanguageMiddleware has already activated
        # this viewer's language, so resolving eagerly loses nothing. It has
        # to happen somewhere before this value can reach title_line's own
        # ``" · ".join(parts)`` below: Python's ``str.join`` requires every
        # item to already be a real ``str`` instance and raises TypeError on
        # a lazy proxy even though the proxy behaves like a string almost
        # everywhere else (f-strings, ``+``, dict keys, template output).
        return str(Unit.LABELS.get(self.unit, ""))

    @property
    def role_label(self) -> str:
        return str(Role.LABELS.get(self.role, ""))

    @property
    def title_line(self) -> str:
        if self.is_admin:
            return "Administrator"
        if self.is_general_manager:
            return "General Manager"
        parts = [p for p in [self.unit_label, self.role_label] if p]
        return " · ".join(parts) if parts else "Unassigned"

    @property
    def honorific_last_name(self) -> str:
        """"Mr. Bayati" / "Ms. Rahim" — the signer's last name for the
        signature/identity box on exported documents, prefixed with the
        honorific that matches this profile's gender.

        No gender set → no honorific, just the bare last name (identical to
        every export before this field existed, so an account nobody has
        gotten around to setting yet degrades to today's exact behaviour
        rather than showing something wrong).
        """
        last_name = (self.user.last_name or self.user.username or "").strip()
        honorific = Gender.HONORIFIC.get(self.gender, "")
        return f"{honorific} {last_name}".strip() if honorific else last_name

    # -- Role predicates ---------------------------------------------------
    @property
    def is_manager(self) -> bool:
        return self.role == Role.MANAGER

    @property
    def is_supervisor(self) -> bool:
        return self.role == Role.SUPERVISOR

    @property
    def is_expert(self) -> bool:
        return self.role == Role.EXPERT

    def in_unit(self, unit_code: str) -> bool:
        return self.unit == unit_code

    # Managers, supervisors and experts can all create cases (per the spec,
    # any of the three Commercial roles may open a new file).
    @property
    def can_create_case(self) -> bool:
        # Supervisors are report-only; only commercial managers and experts open cases.
        return self.unit == Unit.COMMERCIAL and self.role in {
            Role.MANAGER, Role.EXPERT,
        }

    # Only the Commercial manager (or platform admin) may add / rename clients.
    @property
    def can_add_client(self) -> bool:
        if self.is_admin:
            return True
        return self.unit == Unit.COMMERCIAL and self.role == Role.MANAGER

    @property
    def display_first_name(self) -> str:
        """Given name only (the Name field) — used on the sign-in welcome."""
        name = (self.user.first_name or "").strip()
        return name or self.user.username


class ImpersonationLog(models.Model):
    """Durable record of every admin 'log in as user' session.

    Created the moment an admin starts impersonating someone and closed
    (``ended_at`` set) when they return to their own account or their
    session simply expires. This is the audit trail: it does not change how
    actions taken while impersonating are attributed (those still correctly
    record the impersonated user as the actor, exactly as if that user had
    performed them) — it separately answers "who was really at the keyboard,
    and when", by time range, for anyone who needs to check.
    """

    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="impersonation_sessions_started",
    )
    admin_username = models.CharField(max_length=150, blank=True)
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="impersonation_sessions_received",
    )
    target_username = models.CharField(max_length=150, blank=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.admin_username} → {self.target_username} @ {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.ended_at is None


class PlatformConfig(models.Model):
    """Singleton platform-wide settings editable by administrators."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    login_welcome_message = models.CharField(
        max_length=200,
        default="Let's make a great day",
        help_text="Shown on the sign-in screen after “Hi, <name>”.",
    )
    vat_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10,
        help_text="VAT percentage applied on Proforma Subtotal (e.g. 10).",
    )
    # Per-unit company seals (مهر). PI exports use Commercial; TO uses Technical.
    stamp_commercial = models.FileField(
        upload_to=stamp_commercial_upload_path,
        blank=True,
        null=True,
        help_text="Commercial unit stamp — used on Proforma (PI) exports.",
    )
    stamp_technical = models.FileField(
        upload_to=stamp_technical_upload_path,
        blank=True,
        null=True,
        help_text="Technical unit stamp — used on Technical Offer (TO) exports.",
    )
    stamp_supply = models.FileField(
        upload_to=stamp_supply_upload_path,
        blank=True,
        null=True,
        help_text="Supply unit stamp (reserved for future Supply exports).",
    )
    # Default daily work hours — admins set these once; applied to every Person.
    default_work_start = models.TimeField(
        default=time(8, 0),
        help_text="Default shift start applied to all people.",
    )
    default_work_end = models.TimeField(
        default=time(17, 0),
        help_text="Default shift end applied to all people.",
    )
    default_float_seconds = models.PositiveIntegerField(
        default=15 * 60,
        help_text="Default floating time in seconds (e.g. 900 = 15:00).",
    )
    default_reconnect_grace_seconds = models.PositiveIntegerField(
        default=10 * 60,
        help_text="Default reconnect time in seconds (e.g. 600 = 10:00).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform configuration"
        verbose_name_plural = "Platform configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls) -> "PlatformConfig":
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def stamp_for_unit(self, unit_code: str):
        """Return the FileField for a Unit constant, or None."""
        mapping = {
            Unit.COMMERCIAL: self.stamp_commercial,
            Unit.TECHNICAL: self.stamp_technical,
            Unit.SUPPLY: self.stamp_supply,
        }
        field = mapping.get((unit_code or "").upper())
        if field and getattr(field, "name", None):
            return field
        return None
