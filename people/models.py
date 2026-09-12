"""The people directory — the human behind the login.

A user account is a key to the building; a person is the human it was given to.
Until now the platform only modelled the key, which is why an organisational
title lives on the account itself and why nobody who does not use a computer can
be recorded at all.

Two rules shape this module and are worth stating plainly:

*Nothing here creates a person by itself.* There is no backfill, no import, no
"we noticed this account had no person so we made one". Every person on file was
entered by an administrator who is accountable for what it says. Accounts that
existed before this module keep working untouched until somebody deliberately
links them.

*Permissions do not read from here.* The unit and role on a user profile remain
the source of truth for every access decision in the platform. This is a layer
underneath, and it stays inert until a later phase moves those decisions onto it.

STORAGE SHAPE
Identity facts — the ones that are one-per-person and get searched, sorted or
printed — are real columns. Everything that is a list (education, jobs, courses)
or a paragraph (motivation, references) is JSON. Roughly forty sparse columns
were the alternative, most of them empty on most rows, and a migration every
time the form gains a question.

SEAT, PERSON, ROLE — read this before touching anything seat-shaped
This is the one idea in the codebase that cannot be guessed from the code, it
is spread across two apps, and more than one bug has come from conflating its
parts. It is written out here once; ``people.seats`` and ``accounts.models``
point back to this paragraph rather than repeating it.

*A seat is a job, not a human.* A seat is a Django ``User`` account, and what
makes it a seat is its ``accounts.Profile``: a unit and a role — "the
commercial manager", "the internal supply expert" — plus a short ``seat_code``
that numbers it within that unit+role pool. The seat outlives whoever fills it.
Cases, inboxes and archives hang off the seat User, which is exactly why a
person can leave and their successor inherits the work rather than an empty
desk.

*A person is a human.* ``Person`` here is that human, and it exists whether or
not they were ever given a login — that was the whole reason this app was
added. A person id and a user id are unrelated sequences; a number that means
one never means the other (see ``person_photo_path`` below for what that cost).

*``PersonAccount`` is the holding of a seat by a human.* Foreign key to the
person, one-to-one to the seat User. One human may hold several seats at once
(a manager who also covers purchasing); a seat is held by at most one human at
a time.

*One human still gets one login.* Assigning a person their FIRST organisational
seat moves their identity — username, first name, last name — onto that seat's
User, and that is the account they sign in with. Every further seat they take
stays linked and stays active, but is given a vacant username and an unusable
password: it can never be signed into. All of that lives in ``people.seats``.

*``PersonRole`` is how the extra seats keep working.* Each row is one role the
person may act under on that single login, and it remembers ``source_user`` —
the seat User whose unit and role it absorbed. Switching role in the sidebar
writes the chosen row's unit/role onto the login's ``Profile`` and points the
session's work at that ``source_user``, so the second seat's inbox and archive
open under the first seat's password. ``people.role_nav`` resolves which role
is active for a request.

*Permissions read the ``Profile``, never a ``PersonRole``.* The profile always
carries the *currently active* role; ``PersonRole`` is only the menu of roles
available to switch into. Checking one where the other was meant is the single
easiest mistake to make here.

*``SeatTenure`` is the record of holding, kept for history and for undo.* Who
holds a seat User and how: OWNER for a normal assignment, SUBSTITUTE for a
temporary Translate, whose ``origin_person`` is what Return restores the seat
to. A tenure that is still current has ``ended_at`` null. ``SeatEventLog`` and
``people.seat_history`` turn the same events into the readable timeline.
"""
from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django.utils.translation import get_language

from .constants import DETAIL_CODE_COUNTER_KEY, DETAIL_CODE_START, PersonStatus


class PersonCounter(models.Model):
    """Hands out detail codes, one at a time, never reusing one.

    A counter row rather than "highest code so far plus one": the row is locked
    while it is read and bumped, so two administrators saving a person at the
    same moment cannot be handed the same code. Mirrors the counter the case
    document numbers already use.
    """

    key = models.CharField(max_length=40, primary_key=True)
    value = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "Person counter"
        verbose_name_plural = "Person counters"

    def __str__(self):
        return f"{self.key}={self.value}"


@transaction.atomic
def next_detail_code() -> str:
    """The next detail code as a string ("100000001", "100000002", …).

    Text, not a number: it is an identifier, and nothing should ever add to it,
    average it, or drop a leading digit from it.
    """
    counter, _created = PersonCounter.objects.select_for_update().get_or_create(
        key=DETAIL_CODE_COUNTER_KEY,
        defaults={"value": DETAIL_CODE_START - 1},
    )
    counter.value += 1
    counter.save(update_fields=["value"])
    return str(counter.value)


# Personnel files live under "personnel/p<person-id>/". The "p" is load-bearing:
# the media access rules scope a bare "personnel/<n>/" to the USER whose id is
# n, and a person id is not a user id — the two sequences are unrelated, so a
# bare number would have made one employee's photo readable by whichever
# account happened to share that integer. The prefixed form is resolved
# explicitly in core.views instead (person -> linked user, or an admin).
def person_photo_path(instance, filename):
    return f"personnel/p{instance.pk}/photo/{filename}"


def person_document_path(instance, filename):
    return f"personnel/p{instance.pk}/docs/{filename}"


class Person(models.Model):
    """One human on the organisation's books."""

    # --- Identity -------------------------------------------------------
    detail_code = models.CharField(
        "Detail code", max_length=20, unique=True, editable=False,
        help_text="Assigned automatically on first save and never changes.",
    )
    first_name = models.CharField("نام", max_length=80, blank=True)
    last_name = models.CharField("نام خانوادگی", max_length=120)

    # The sign-in name is built from these, so they are typed rather than
    # transliterated: automatic transliteration of a Persian name gets the
    # spelling a person actually uses wrong often enough to matter.
    first_name_en = models.CharField("Name (Latin)", max_length=80, blank=True)
    last_name_en = models.CharField("Last name (Latin)", max_length=120, blank=True)

    father_name = models.CharField(max_length=80, blank=True)
    id_number = models.CharField("شماره شناسنامه", max_length=20, blank=True)

    # NULL rather than "" when unknown: the column is unique, and in SQL two
    # NULLs are not equal while two empty strings are — so blank-as-empty-string
    # would allow exactly one person without a national ID.
    national_id = models.CharField(
        "کد ملی", max_length=10, unique=True, null=True, blank=True,
    )
    # Organisational internal code (HR) — lives on the person, not the seat.
    internal_code = models.CharField("کد داخلی", max_length=40, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    birth_place = models.CharField(max_length=80, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    marital = models.CharField(max_length=20, blank=True)
    military = models.CharField(max_length=30, blank=True)

    # --- Contact --------------------------------------------------------
    city = models.CharField(max_length=80, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    photo = models.ImageField(upload_to=person_photo_path, blank=True, null=True)
    resume = models.FileField(upload_to=person_document_path, blank=True, null=True)

    # --- Bank -----------------------------------------------------------
    card_number = models.CharField(max_length=20, blank=True)
    iban = models.CharField("شبا", max_length=26, blank=True)
    bank_name = models.CharField(max_length=60, blank=True)
    account_holder = models.CharField(max_length=120, blank=True)

    # --- The long tail, as JSON ----------------------------------------
    # Lists and paragraphs. See the module docstring for why these are not
    # columns. Each is a plain list/dict shaped by people.spec.
    education = models.JSONField(default=list, blank=True)
    employment = models.JSONField(default=list, blank=True)
    courses = models.JSONField(default=list, blank=True)
    motivation = models.JSONField(default=dict, blank=True)
    references = models.JSONField(default=dict, blank=True)
    request = models.JSONField(default=dict, blank=True)
    # Dependants — only meaningful when marital status is married.
    children = models.JSONField(default=list, blank=True)
    children_count = models.PositiveSmallIntegerField(null=True, blank=True)

    # Daily work shift (local TIME_ZONE). Admin / GM logins ignore this.
    work_start = models.TimeField(
        null=True, blank=True, default=None,
        help_text="Daily shift start (defaults to 08:00 when empty).",
    )
    work_end = models.TimeField(
        null=True, blank=True, default=None,
        help_text="Daily shift end (defaults to 17:00 when empty).",
    )
    # Late-arrival grace shown as MM:SS (e.g. 15:00 = 15 minutes). Login within
    # start..start+grace still stamps In as start and credits those minutes.
    float_seconds = models.PositiveIntegerField(
        default=15 * 60,
        help_text="Floating time grace after shift start, in seconds (default 15:00).",
    )
    # Brief disconnect buffer (tab close / offline). Admins configure; staff UI
    # does not mention this value. Default 15:00 (15 minutes) — raised from the
    # original 10:00 so an ordinary short outage (a power blip, a Wi-Fi drop)
    # reconnects within grace on its own; only a gap that outlasts this now
    # raises a RequestType.CODE_PRESENCE_GAP StaffRequest asking the person
    # what happened (see people.staff_requests, and shift_hours._record_away
    # where the gap is first detected).
    reconnect_grace_seconds = models.PositiveIntegerField(
        default=15 * 60,
        help_text="Reconnect time after disconnect, in seconds (default 15:00).",
    )

    # --- Status + guarantees deposited with the company -----------------
    status = models.CharField(
        max_length=20, choices=PersonStatus.CHOICES, default=PersonStatus.ACTIVE,
        db_index=True,
    )
    GUARANTEE_PROMISSORY = "PROMISSORY"
    GUARANTEE_CHOICES = (
        (GUARANTEE_PROMISSORY, "سفته"),
    )
    guarantee_type = models.CharField(
        "نوع تضمین", max_length=20, blank=True,
        choices=GUARANTEE_CHOICES, default="",
    )
    guarantee_amount = models.DecimalField(
        "مبلغ تضمین (ریال)", max_digits=18, decimal_places=0,
        null=True, blank=True,
    )

    # Sign-in handle belonging to the human — ``Mahdi_Bayati309214`` (Latin
    # names + six random digits). One login per person; extra roles share it.
    username = models.CharField(max_length=150, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_people", editable=False,
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "People"
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="people_person_name_idx"),
        ]

    def __str__(self):
        return f"{self.detail_code} — {self.full_name}"

    def save(self, *args, **kwargs):
        # The code is assigned once and never touched again: it is the handle
        # everything downstream will key on, so it has to survive every later
        # edit, including a change of name.
        if not self.detail_code:
            self.detail_code = next_detail_code()
        if not self.national_id:
            self.national_id = None

        # Recomputed on every save rather than only when blank: correcting the
        # spelling of a Latin name is a normal thing to do, and a username left
        # frozen at the first typo is exactly the kind of thing nobody notices
        # until it is on a document. The seats this person holds are re-named
        # to match by people.seats.refresh_person_seats(), called from the form
        # — never silently from here, so a fixture load or a shell edit cannot
        # rename live accounts as a side effect.
        from .usernames import person_username
        self.username = person_username(self)

        # update_fields is an explicit promise about which columns are written,
        # so a username just recomputed above has to join that promise or it is
        # worked out and then thrown away. (detail_code needs no such care: it
        # can only ever be assigned on an insert, and Django refuses
        # update_fields on an insert.)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(dict.fromkeys(
                list(update_fields) + ["username"]))
        super().save(*args, **kwargs)

    # -- Display ---------------------------------------------------------
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.detail_code

    @property
    def full_name_en(self) -> str:
        return f"{self.first_name_en} {self.last_name_en}".strip()

    @property
    def display_name(self) -> str:
        """Language-aware name for LIVE UI chrome — the topbar, the People
        list, seat/assignment pickers, My Tasks, request/reminder screens —
        wherever a person's name is shown to whoever is looking at the screen
        right now, in whichever of the two chrome languages they are looking
        at it in.

        Used to be Latin-only, unconditionally ("Never Persian") — written
        back when the platform had no language toggle at all and Latin was
        the only chrome language there was. Now that a viewer can switch
        chrome language per-person in Settings
        (``accounts.models.Profile.language``, activated every request by
        ``accounts.middleware.LanguageMiddleware``), the name has to follow
        that choice the same way every other piece of chrome text already
        does: Persian (``first_name``/``last_name``) while Persian is active,
        Latin (``first_name_en``/``last_name_en``) while English is active —
        both typed by the owner on the same person-details form, never
        transliterated (see those fields' own comments).

        FALLBACK BOTH WAYS, so this can never render blank just because only
        one half of the form was filled in: the language NOT currently active
        is tried next, then the sign-in username, then the permanent detail
        code (which every row always has) as the last resort.

        Reads ``django.utils.translation.get_language()`` — the ACTIVE
        request's language — rather than anything stored on this Person, on
        purpose: this property is very often asked to name someone OTHER than
        the viewer (an admin reading a row on the People list, a manager
        picking a name out of a seat dropdown), so the language that matters
        is whoever is looking, not whoever is being looked at.

        NOT used for FROZEN historical text — a CaseEvent/ClientEvent actor
        snapshot, a CompanyReport author, or the seat-history/timeline
        strings ``people.seats._person_label`` writes onto
        ``SeatEventLog``/``SeatAssignmentLog``/a delegation's ``CaseEvent
        .comment``. Those are written once and must never change meaning
        depending on who reads them later or what language happened to be
        active at the moment they were written, so they deliberately keep
        resolving a Latin, deterministic name of their own
        (``cases.services._person_display_name`` for actor snapshots,
        ``people.seats._person_label`` for seat/case history) instead of
        calling this property. See those functions' own docstrings.
        """
        persian = f"{self.first_name} {self.last_name}".strip()
        latin = self.full_name_en
        preferred, fallback = (
            (persian, latin) if get_language() == "fa" else (latin, persian)
        )
        return (
            preferred
            or fallback
            or (self.username or "").strip()
            or self.detail_code
        )

    @property
    def is_active(self) -> bool:
        return self.status == PersonStatus.ACTIVE

    @property
    def seat_count(self) -> int:
        """How many seat accounts this person holds."""
        if not self.pk:
            return 0
        return len(self.accounts.all())

    @property
    def role_count(self) -> int:
        """How many organisational roles this person holds."""
        if not self.pk:
            return 0
        return len(self.roles.all())

    @property
    def has_seat(self) -> bool:
        return self.seat_count > 0

    @property
    def login_user(self):
        """The User this person signs in with (primary login)."""
        if not self.pk:
            return None
        from .seats import primary_login
        return primary_login(self)

    @property
    def birth_date_jalali(self) -> str:
        return format_jalali(self.birth_date)

    @property
    def guarantee_amount_display(self) -> str:
        if self.guarantee_amount is None:
            return ""
        try:
            n = int(self.guarantee_amount)
        except (TypeError, ValueError):
            return str(self.guarantee_amount)
        return f"{n:,}".replace(",", "٬")


class PersonAccount(models.Model):
    """The single login seat held by one person.

    A person has at most one login ``User``. Extra organisational roles live on
    ``PersonRole`` and are switched in-session without a second username.
    """

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="accounts",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="person_link",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="seats_assigned", editable=False,
    )

    class Meta:
        ordering = ["user__username"]
        verbose_name = "Seat"
        verbose_name_plural = "Seats"

    def __str__(self):
        return f"{self.user_id} → {self.person_id}"

    @property
    def assigned_at_jalali(self) -> str:
        if not self.assigned_at:
            return ""
        from django.utils import timezone
        return format_jalali(timezone.localtime(self.assigned_at))


class PersonRole(models.Model):
    """One organisational role a person may act under (same login).

    The login ``User`` / ``Profile`` carries the *active* unit and role. Extra
    roles are listed here and swapped into the profile when the person picks
    them in the sidebar.
    """

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="roles",
    )
    unit = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=20, blank=True)
    supply_kind = models.CharField(max_length=20, blank=True)
    internal_code = models.CharField(max_length=40, blank=True)
    is_admin = models.BooleanField(default=False)
    is_general_manager = models.BooleanField(default=False)
    # Seat user whose unit/role was absorbed when this role was assigned.
    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="absorbed_person_roles",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "Person role"
        verbose_name_plural = "Person roles"
        constraints = [
            models.UniqueConstraint(
                fields=["person", "unit", "role", "supply_kind", "is_admin", "is_general_manager"],
                name="people_personrole_unique_combo",
            ),
        ]

    def __str__(self):
        if self.is_admin:
            return f"{self.person_id} · Administrator"
        if self.is_general_manager:
            return f"{self.person_id} · General Manager"
        return f"{self.person_id} · {self.unit} {self.role}"

    @property
    def title_line(self) -> str:
        from accounts.constants import Role, Unit
        if self.is_admin:
            return "Administrator"
        if self.is_general_manager:
            return "General Manager"
        # str(...) here for the same reason accounts.models.Profile.unit_label
        # / role_label wrap their own Unit.LABELS/Role.LABELS lookup: those
        # dicts hold gettext_lazy proxies now (accounts.constants.Unit/Role
        # .CHOICES), and the plain ``.join()`` two lines below requires real
        # ``str`` instances — a lazy proxy raises TypeError there even though
        # it is otherwise a fully usable stand-in for one.
        parts = [
            p for p in [
                str(Unit.LABELS.get(self.unit, "")),
                str(Role.LABELS.get(self.role, "")),
            ] if p
        ]
        return " · ".join(parts) if parts else "Unassigned"

    @property
    def seat_code(self) -> str:
        """Display index of the seat this role came from, if any."""
        src = self.source_user
        if src is None:
            return ""
        profile = getattr(src, "profile", None)
        return (getattr(profile, "seat_code", None) or "").strip()

    @property
    def created_at_jalali(self) -> str:
        if not self.created_at:
            return ""
        from django.utils import timezone
        local = timezone.localtime(self.created_at)
        date_part = format_jalali(local.date())
        if not date_part:
            return ""
        # People form uses slash dates; Seats tables use dotted datetime.
        return f"{date_part.replace('/', '.')} {local.strftime('%H:%M')}"


class SeatAssignmentLog(models.Model):
    """Who held a seat before it was vacated — for Seats table history columns."""

    seat_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="seat_assignment_logs",
    )
    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="seat_assignment_logs",
    )
    person_name = models.CharField(max_length=200, blank=True)
    detail_code = models.CharField(max_length=20, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    vacated_at = models.DateTimeField()
    vacated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="seat_vacations_logged",
    )

    class Meta:
        ordering = ["-vacated_at", "-pk"]
        verbose_name = "Seat assignment log"
        verbose_name_plural = "Seat assignment logs"

    def __str__(self):
        return f"{self.seat_user_id}: {self.person_name} until {self.vacated_at}"

    @property
    def vacated_at_jalali(self) -> str:
        if not self.vacated_at:
            return ""
        from django.utils import timezone
        local = timezone.localtime(self.vacated_at)
        date_part = format_jalali(local.date())
        if not date_part:
            return ""
        return f"{date_part.replace('/', '.')} {local.strftime('%H:%M')}"

    @property
    def previous_holder_label(self) -> str:
        name = (self.person_name or "").strip() or "—"
        code = (self.detail_code or "").strip()
        return f"{name} ({code})" if code else name


class SeatTenure(models.Model):
    """Who currently (or previously) holds a seat User, and how.

    OWNER = normal assignment. SUBSTITUTE = temporary Translate; Return restores
    the ``origin_person``. Open tenures have ``ended_at`` null.
    """

    KIND_OWNER = "OWNER"
    KIND_SUBSTITUTE = "SUBSTITUTE"
    # gettext_lazy, not gettext — this list is built once at import time,
    # before any request (and therefore any viewer's language) exists; see
    # cases/constants.py's own CHOICES lists for the full explanation this
    # module reuses everywhere a choices= label needs to translate per viewer.
    KIND_CHOICES = [
        (KIND_OWNER, _("Owner")),
        (KIND_SUBSTITUTE, _("Substitute")),
    ]

    REASON_ASSIGN = "ASSIGN"
    REASON_TRANSLATE = "TRANSLATE"
    REASON_RETURN = "RETURN"
    REASON_CLOSE = "CLOSE"
    REASON_RELEASE = "RELEASE"
    REASON_CHOICES = [
        (REASON_ASSIGN, _("Assign")),
        (REASON_TRANSLATE, _("Translate")),
        (REASON_RETURN, _("Return")),
        (REASON_CLOSE, _("Close")),
        (REASON_RELEASE, _("Release")),
    ]

    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="seat_tenures",
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="seat_tenures",
    )
    person_role = models.ForeignKey(
        "PersonRole", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tenures",
    )
    kind = models.CharField(
        max_length=20, choices=KIND_CHOICES, default=KIND_OWNER, db_index=True,
    )
    origin_person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="origin_seat_tenures",
        help_text="Owner person when kind is SUBSTITUTE (for Return).",
    )
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_reason = models.CharField(
        max_length=20, blank=True, choices=REASON_CHOICES,
    )

    class Meta:
        ordering = ["-started_at", "-pk"]
        verbose_name = "Seat tenure"
        verbose_name_plural = "Seat tenures"
        indexes = [
            models.Index(fields=["source_user", "ended_at"], name="people_seat_source__be81ce_idx"),
        ]

    def __str__(self):
        return f"{self.source_user_id} · {self.kind} · person={self.person_id}"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def is_substitute(self) -> bool:
        return self.kind == self.KIND_SUBSTITUTE and self.ended_at is None


class SeatEventLog(models.Model):
    """Immutable timeline of seat lifecycle events for the History page."""

    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    TRANSLATED = "TRANSLATED"
    RETURNED = "RETURNED"
    DELEGATED = "DELEGATED"
    CLOSED = "CLOSED"
    VACANT = "VACANT"
    # gettext_lazy — see SeatTenure.KIND_CHOICES above / cases/constants.py
    # for why: this list is built once at import time, before any viewer's
    # language is known.
    EVENT_CHOICES = [
        (CREATED, _("Created")),
        (ASSIGNED, _("Assigned")),
        (TRANSLATED, _("Translated")),
        (RETURNED, _("Returned")),
        (DELEGATED, _("Delegated")),
        (CLOSED, _("Closed")),
        (VACANT, _("Vacant")),
    ]

    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="seat_event_logs",
    )
    event = models.CharField(max_length=20, choices=EVENT_CHOICES, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="seat_events_acted",
    )
    from_person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    to_person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    from_person_name = models.CharField(max_length=200, blank=True)
    to_person_name = models.CharField(max_length=200, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Seat event log"
        verbose_name_plural = "Seat event logs"

    def __str__(self):
        return f"{self.source_user_id} · {self.event} @ {self.created_at}"


class ShiftTrackingConfig(models.Model):
    """Site-wide date when work-shift hour tracking began (singleton pk=1).

    Set once on first migrate/deploy so older calendar days are not counted.
    """

    started_on = models.DateField(
        help_text="Gregorian date from which planned/worked hours are counted.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shift tracking config"
        verbose_name_plural = "Shift tracking config"

    def __str__(self):
        return f"Shift tracking from {self.started_on}"


class ShiftMonthSnapshot(models.Model):
    """Planned vs worked hours for one person in one Jalali month.

    Past months are frozen so a later shift-time change cannot rewrite history.
    The current month stays open and recalculates planned hours when the shift
    definition changes.
    """

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="shift_months",
    )
    jalali_year = models.PositiveIntegerField()
    jalali_month = models.PositiveSmallIntegerField()
    work_start = models.TimeField()
    work_end = models.TimeField()
    planned_minutes = models.PositiveIntegerField(default=0)
    worked_minutes = models.PositiveIntegerField(default=0)
    overtime_minutes = models.PositiveIntegerField(default=0)
    working_days = models.PositiveSmallIntegerField(default=0)
    weekend_days = models.PositiveSmallIntegerField(default=0)
    holiday_days = models.PositiveSmallIntegerField(default=0)
    frozen = models.BooleanField(default=False)
    meta = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-jalali_year", "-jalali_month"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "jalali_year", "jalali_month"],
                name="people_shiftmonth_unique",
            ),
        ]

    def __str__(self):
        return f"{self.person_id} {self.jalali_year}/{self.jalali_month}"

    @property
    def planned_hours(self) -> float:
        return round(self.planned_minutes / 60, 1)

    @property
    def worked_hours(self) -> float:
        return round(self.worked_minutes / 60, 1)

    @property
    def overtime_hours(self) -> float:
        return round(self.overtime_minutes / 60, 1)

    @property
    def total_hours(self) -> float:
        return round((self.worked_minutes + self.overtime_minutes) / 60, 1)


class ShiftDayLog(models.Model):
    """Minutes credited on a Gregorian calendar day for a person."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="shift_days",
    )
    day = models.DateField(db_index=True)
    minutes = models.PositiveIntegerField(default=0)
    # Presence is reported by a browser ping every few seconds, so most gaps are
    # a fraction of a minute. Whole minutes land in ``minutes``; what is left
    # over waits here until the next pings make it up to a minute. Without this
    # the remainder was discarded on every ping and a full day at the desk was
    # credited as almost nothing.
    carry_seconds = models.PositiveSmallIntegerField(default=0)
    # Approved overtime credited separately from presence minutes.
    overtime_minutes = models.PositiveIntegerField(default=0)
    # Total time, in whole minutes, that the person was away past the reconnect
    # grace on this day (summed over every such absence). An absence costs the
    # credit it never earned and nothing more, so this column changes no total;
    # it exists purely so a queried month can be explained — "the 40 missing
    # minutes on the 12th were four absences", not an unexplained shortfall.
    away_minutes = models.PositiveIntegerField(default=0)
    # Seconds (not minutes — no carry-bank needed for an audit-only figure)
    # spent where pings kept arriving (tab open, in grace) but the browser
    # reported no real interaction (click/key/scroll/pointer) since the
    # previous ping — "open but not necessarily worked", as distinct from
    # away_minutes ("not open at all"). Same rule as away_minutes: audit
    # only, never subtracted from ``minutes``. Nothing reads this column
    # today; it is being collected now so a later report can be built on
    # real history instead of starting from zero — see
    # record_presence_ping's own comment for what "real interaction" means.
    idle_seconds = models.PositiveIntegerField(default=0)
    first_login = models.DateTimeField(null=True, blank=True)
    last_logout = models.DateTimeField(null=True, blank=True)
    last_ping = models.DateTimeField(null=True, blank=True)
    # True after the person confirms Sign out — reconnect gaps are not credited.
    explicit_logout = models.BooleanField(default=False)

    class Meta:
        ordering = ["-day"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "day"],
                name="people_shiftday_unique",
            ),
        ]

    def __str__(self):
        return f"{self.person_id} {self.day} · {self.minutes}m"


class EndOfDayReport(models.Model):
    """One person's end-of-shift report for one calendar work day.

    Filed either live, right as that day's shift ends (see
    ``people.views.eod_report`` and ``core/templates/base.html``'s
    ``showEnd()``/sign-out handling), or as a forced catch-up the next time
    this person logs in, if a shift ended without one ever being filed (tab
    closed, laptop asleep — ``WorkShiftMiddleware`` caught the next request
    instead) — see ``people.eod_reports.missing_report_days`` and
    ``people.middleware.EndOfDayReportGateMiddleware``.

    Deliberately not ``marketing.models.CompanyReport``: that one is about a
    company/case, optional, any number per day, and read by three unrelated
    screens (a company page, a case page, My Tasks) that know nothing about
    "end of day". This one is about the person's own day, exactly one per
    ``work_day`` (see the unique constraint below), and carries no approval
    workflow at all, unlike ``StaffRequest`` — supervisor-side review is an
    explicitly later phase.
    """

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="eod_reports",
    )
    work_day = models.DateField(db_index=True)
    # Each a list of non-blank strings — the "+"-to-add-another repeatable
    # field group people/templates/people/eod_report.html renders, one entry
    # per request/idea. See people.eod_reports.submit_report for how the
    # posted form fields become this shape.
    requests_for_supervisor = models.JSONField(default=list, blank=True)
    ideas_for_today = models.JSONField(default=list, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-work_day", "-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["person", "work_day"],
                name="people_eodreport_unique_person_day",
            ),
        ]

    def __str__(self):
        return f"{self.person_id} · {self.work_day}"


class RequestType(models.Model):
    """Catalogue of personnel request kinds (Overtime, …)."""

    CODE_OVERTIME = "overtime"
    # Raised by the SYSTEM (shift_hours._record_away), never by the person
    # choosing to open the form the way Overtime is — see
    # people.staff_requests.create_presence_gap_draft. A person only ever
    # acts on one of these to fill in the reason (DRAFT -> SUBMITTED); they
    # cannot create one from nothing, so this type is deliberately never
    # granted through PersonRequestAccess (compare CODE_OVERTIME, which is).
    CODE_PRESENCE_GAP = "presence_gap"

    code = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True, default="")
    icon = models.CharField(
        max_length=60, default="fa-clipboard-check",
        help_text="Font Awesome solid class, e.g. fa-stopwatch",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title


class PersonRequestAccess(models.Model):
    """Which request types a Person may open (Person-scoped, not Seat)."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="request_access",
    )
    request_type = models.ForeignKey(
        RequestType, on_delete=models.CASCADE, related_name="person_access",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "request_type"],
                name="people_personrequestaccess_unique",
            ),
        ]

    def __str__(self):
        return f"{self.person_id} · {self.request_type_id}"


class StaffRequest(models.Model):
    """One personnel request (Overtime, …) belonging to a Person."""

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    # gettext_lazy — see SeatTenure.KIND_CHOICES above / cases/constants.py
    # for why: this tuple is built once at import time, before any viewer's
    # language is known. "Approved" reuses the same English source string
    # marketing/templates/marketing/company_detail.html already passes
    # through {% trans %}, so a request approved here and a case marked
    # Final Approved there read as the identical Persian word rather than
    # two independently-chosen translations of "Approved" drifting apart.
    STATUS_CHOICES = (
        (STATUS_DRAFT, _("Draft")),
        (STATUS_SUBMITTED, _("Submitted")),
        (STATUS_APPROVED, _("Approved")),
        (STATUS_REJECTED, _("Rejected")),
    )

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="staff_requests",
    )
    request_type = models.ForeignKey(
        RequestType, on_delete=models.PROTECT, related_name="requests",
    )
    request_code = models.CharField(
        max_length=80, blank=True, default="",
        help_text="Unique request number, e.g. FT-OT-503-100000001-0007",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True,
    )
    payload = models.JSONField(default=dict, blank=True)
    # Overtime-specific columns for fast queries / shift extension.
    case_ids = models.JSONField(default=list, blank=True)
    requested_minutes = models.PositiveIntegerField(default=0)
    approved_minutes = models.PositiveIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default="")
    # Work-day the overtime applies to (usually submit day).
    work_day = models.DateField(null=True, blank=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="staff_requests_created",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="staff_requests_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    # Unread alarms: cleared on status change; set when that party opens detail.
    requester_seen_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="When the requester last opened this request after a status change.",
    )
    reviewer_seen_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="When a reviewer (GM) last opened this request after it was submitted.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["request_code"],
                name="people_staffrequest_request_code_uniq",
                # The field's empty default is a value like any other to a unique
                # index, so without this only one code-less request could ever
                # exist — the second one would fail on a constraint that is meant
                # to police real request numbers. Same shape as the seat-code
                # index on accounts.Profile.
                condition=~models.Q(request_code=""),
            ),
        ]

    def __str__(self):
        return f"{self.request_type_id}#{self.pk} · {self.status}"

    @property
    def requested_label(self) -> str:
        return _minutes_label(self.requested_minutes)

    @property
    def approved_label(self) -> str:
        if self.approved_minutes is None:
            return "—"
        return _minutes_label(self.approved_minutes)


def _minutes_label(total: int) -> str:
    total = max(0, int(total or 0))
    h, m = divmod(total, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def format_jalali(value) -> str:
    """A stored date rendered Jalali, or "" when unset.

    Dates are stored as ordinary dates so they stay sortable and comparable;
    Jalali is applied at the edges. Uses the platform's own conversion rather
    than a second implementation that could disagree with it.
    """
    if not value:
        return ""
    try:
        from cases.jalali import gregorian_to_jalali
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        return f"{jy}.{jm:02d}.{jd:02d}"
    except Exception:
        return ""
