"""Forms used by administrators to create/manage user accounts."""
import secrets

from django import forms
from django.contrib.auth.models import User

from .constants import Gender, Role, Unit, SupplyKind
from .models import PlatformConfig, Profile

try:
    from people.models import Person as _Person
except Exception:  # pragma: no cover — apps not ready during early import
    _Person = None


# Characters chosen to avoid visually-ambiguous pairs (0/O, 1/l/I) since these
# are read off a screen and typed by hand at least once.
_TEMP_PW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    """A fresh, random one-time password for a new or reset account.

    Never stored anywhere in readable form — the caller shows it to the admin
    exactly once (it only exists in this function's return value and in
    whatever transient context renders it) and Django stores only its hash.
    """
    return "".join(secrets.choice(_TEMP_PW_ALPHABET) for _ in range(length))


def _validate_png_upload(f):
    """Reject non-PNG uploads so signature/stamp keep a transparent background."""
    if not f:
        return f
    name = (getattr(f, "name", "") or "").lower()
    content = getattr(f, "content_type", "") or ""
    if not (name.endswith(".png") or content in ("image/png", "image/x-png")):
        raise forms.ValidationError("Only PNG files are accepted.")
    # Peek at magic bytes when available.
    try:
        head = f.read(8)
        f.seek(0)
        if head and head[:8] != b"\x89PNG\r\n\x1a\n":
            raise forms.ValidationError("Only PNG files are accepted.")
    except forms.ValidationError:
        raise
    except Exception:
        pass
    return f


def _validate_avatar_upload(f):
    """Allow common photo formats for the circular profile image."""
    if not f:
        return f
    name = (getattr(f, "name", "") or "").lower()
    content = (getattr(f, "content_type", "") or "").lower()
    ok_ext = name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
    ok_type = content.startswith("image/")
    if not (ok_ext or ok_type):
        raise forms.ValidationError("Upload a photo (PNG, JPEG, WebP or GIF).")
    # Soft size cap (~4 MB) so media stays manageable.
    size = getattr(f, "size", None)
    if size is not None and size > 4 * 1024 * 1024:
        raise forms.ValidationError("Photo must be smaller than 4 MB.")
    return f


def _validate_stamp_upload(f):
    """Allow PNG or SVG for unit seals."""
    if not f:
        return f
    name = (getattr(f, "name", "") or "").lower()
    content_type = (getattr(f, "content_type", "") or "").lower()
    is_png = name.endswith(".png") or content_type in ("image/png", "image/x-png")
    is_svg = name.endswith(".svg") or content_type in (
        "image/svg+xml", "image/svg", "text/xml", "application/xml",
    )
    if not (is_png or is_svg):
        raise forms.ValidationError("Only PNG or SVG files are allowed for stamps.")
    try:
        pos = f.tell()
        head = f.read(256)
        f.seek(pos)
    except Exception:
        head = b""
        try:
            f.seek(0)
        except Exception:
            pass
    if is_png and not name.endswith(".svg"):
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            # Extension says PNG but content does not — reject unless SVG markup.
            text = head.lstrip().lower()
            if not (text.startswith(b"<?xml") or text.startswith(b"<svg")):
                raise forms.ValidationError("Only valid PNG or SVG files are allowed.")
    elif is_svg:
        text = head.lstrip().lower()
        if not (b"<svg" in text or text.startswith(b"<?xml")):
            raise forms.ValidationError("Only valid SVG files are allowed.")
    try:
        f.seek(0)
    except Exception:
        pass
    return f


class PngFileInput(forms.FileInput):
    """File picker without Django's “Currently: … / Clear” chrome."""

    def __init__(self, attrs=None):
        base = {"accept": "image/png,.png"}
        if attrs:
            base.update(attrs)
        super().__init__(attrs=base)


class StampFileInput(forms.FileInput):
    """File picker for PNG/SVG unit stamps."""

    def __init__(self, attrs=None):
        base = {"accept": "image/png,.png,image/svg+xml,.svg"}
        if attrs:
            base.update(attrs)
        super().__init__(attrs=base)


class UserCreateForm(forms.Form):
    """Create an unassigned seat (or assign immediately to a chosen person)."""

    is_general_manager = forms.BooleanField(
        required=False, label="General manager",
        help_text="Sees every unit's dashboard, archive and timeline; cannot manage seats.",
    )
    unit = forms.ChoiceField(choices=[("", "—")] + Unit.CHOICES, required=False)
    role = forms.ChoiceField(choices=[("", "—")] + Role.CHOICES, required=False)
    supply_kind = forms.ChoiceField(
        choices=[("", "—")] + SupplyKind.CHOICES, required=False,
        label="Supply expert type",
        help_text="Only when Unit is Supply and Role is Expert.",
    )
    person = forms.ModelChoiceField(
        queryset=_Person.objects.none() if _Person is not None else User.objects.none(),
        required=False,
        label="Person",
        help_text="Optional. Link this seat to a person now, or leave empty.",
        widget=forms.Select(attrs={
            "data-combo": "1",
            "data-placeholder": "Search people…",
        }),
    )

    def __init__(self, *args, locked_person=None, **kwargs):
        from people.constants import PersonStatus
        from people.models import Person
        from people.usernames import next_seat_index

        self.locked_person = locked_person
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(
            status=PersonStatus.ACTIVE,
        ).order_by("first_name_en", "last_name_en", "detail_code")
        self.fields["person"].label_from_instance = (
            lambda p: f"{p.display_name} · {p.detail_code}"
        )

        gm_exists = Profile.objects.filter(is_general_manager=True).exists()
        self.gm_available = not gm_exists
        if not self.gm_available:
            self.fields["is_general_manager"].disabled = True
            self.fields["is_general_manager"].help_text = (
                "A General Manager seat already exists."
            )

        if locked_person is not None:
            self.fields["person"].initial = locked_person.pk
            self.fields["person"].disabled = True
            self.fields["person"].required = False
            self.fields["person"].help_text = (
                "Locked to this person because you opened Create seat from their Seats page."
            )

        data = self.data if self.is_bound else None
        is_gm = False
        unit = role = supply = ""
        if data is not None:
            is_gm = bool(data.get("is_general_manager")) and self.gm_available
            unit = data.get("unit") or ""
            role = data.get("role") or ""
            supply = data.get("supply_kind") or ""
        self.next_seat_code = next_seat_index(
            unit="" if is_gm else unit,
            role="" if is_gm else role,
            supply_kind="" if is_gm else supply,
            is_general_manager=is_gm,
        )

    def clean(self):
        cleaned = super().clean()
        if not self.gm_available:
            cleaned["is_general_manager"] = False
        if cleaned.get("is_general_manager"):
            cleaned["unit"] = ""
            cleaned["role"] = ""
            cleaned["supply_kind"] = ""
            return cleaned
        if not cleaned.get("unit"):
            self.add_error("unit", "A unit is required.")
        if not cleaned.get("role"):
            self.add_error("role", "A role is required.")
        unit = cleaned.get("unit")
        role = cleaned.get("role")
        if unit == Unit.SUPPLY and role == Role.EXPERT and not cleaned.get("supply_kind"):
            self.add_error("supply_kind", "Choose Internal or External for a Supply expert.")
        if unit != Unit.SUPPLY or role != Role.EXPERT:
            cleaned["supply_kind"] = ""
        if self.locked_person is not None:
            cleaned["person"] = self.locked_person
        return cleaned

    def save(self) -> User:
        from people.seats import SeatError, assign_seat
        from people.usernames import next_seat_index, vacant_login_username

        data = self.cleaned_data
        is_gm = bool(data.get("is_general_manager"))
        unit = "" if is_gm else (data.get("unit") or "")
        role = "" if is_gm else (data.get("role") or "")
        supply = "" if is_gm else (data.get("supply_kind") or "")
        seat_code = next_seat_index(
            unit=unit, role=role, supply_kind=supply,
            is_general_manager=is_gm,
        )
        generated = generate_temp_password()
        # Create user first so vacant username can use _seat<pk>.
        user = User.objects.create_user(
            username=f"_tmp{secrets.token_hex(4)}",
            password=generated,
            first_name="",
            last_name="",
            email="",
        )
        user.username = vacant_login_username(seat_code, user_pk=user.pk)
        user.is_active = False
        user.save(update_fields=["username", "is_active"])

        profile = user.profile
        profile.must_change_password = True
        profile.is_admin = False
        profile.is_general_manager = is_gm
        profile.unit = unit
        profile.role = role
        profile.supply_kind = supply
        profile.internal_code = ""
        profile.org_title = ""
        profile.org_number = ""
        profile.gender = ""
        profile.seat_code = seat_code
        profile.seat_ready = True
        profile.save()

        person = data.get("person")
        if person is not None:
            try:
                assign_seat(person, user, actor=None)
            except SeatError:
                # Seat stays in catalogue; admin can assign from People.
                pass

        self.generated_password = generated
        self.seat_code = seat_code
        return user


def _user_has_case_history(user: User) -> bool:
    """True when this account already touched cases/forms — unit/role must freeze."""
    if user is None or not user.pk:
        return False
    try:
        from cases.models import Case, CaseForm
    except Exception:
        return False
    if Case.objects.filter(created_by=user).exists():
        return True
    if Case.objects.filter(assigned_to=user).exists():
        return True
    if CaseForm.objects.filter(created_by=user).exists():
        return True
    return False


class UserEditForm(forms.Form):
    """Edit a seat. Identity is always from Person (or empty); seat_code set once."""

    seat_code = forms.CharField(
        max_length=20, required=False, label="Index",
        help_text="Three-digit index inside this Unit & role (001, 002, …).",
    )
    first_name = forms.CharField(max_length=150, label="Name", required=False)
    last_name = forms.CharField(max_length=150, label="Last name", required=False)
    email = forms.EmailField(required=False)

    is_admin = forms.BooleanField(required=False, label="Platform administrator")
    is_general_manager = forms.BooleanField(required=False, label="General manager")
    unit = forms.ChoiceField(choices=[("", "—")] + Unit.CHOICES, required=False)
    role = forms.ChoiceField(choices=[("", "—")] + Role.CHOICES, required=False)
    supply_kind = forms.ChoiceField(
        choices=[("", "—")] + SupplyKind.CHOICES, required=False,
        label="Supply expert type",
        help_text="Only when Unit is Supply and Role is Expert.",
    )

    org_number = forms.CharField(
        max_length=40, required=False, label="Organisational number (detail code)",
    )
    gender = forms.ChoiceField(
        choices=[("", "—")] + Gender.CHOICES, required=False,
        help_text="Filled from the person when assigned. Used for Mr./Ms. on exports.",
    )
    signature = forms.ImageField(
        required=False,
        widget=PngFileInput(),
        help_text="PNG only (transparent). Replaces any previous signature.",
    )

    def __init__(self, *args, user: User = None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.held_person = None
        self.role_locked = False
        self.seat_code_locked = False
        link = getattr(user, "person_link", None) if user else None
        if link is not None:
            self.held_person = link.person

        profile = getattr(user, "profile", None) if user else None
        existing_code = (getattr(profile, "seat_code", None) or "").strip() if profile else ""
        self.seat_code_locked = bool(existing_code)

        # Identity always locked here: empty until assign, then from Person.
        for name in ("first_name", "last_name", "org_number", "gender"):
            self.fields[name].disabled = True
            self.fields[name].required = False

        if self.seat_code_locked:
            self.fields["seat_code"].disabled = True
            self.fields["seat_code"].required = False
            self.fields["seat_code"].help_text = "Index is locked after it is set."
        else:
            self.fields["seat_code"].required = True
            self.fields["seat_code"].help_text = (
                "Required once for legacy seats (e.g. 001). Cannot change later."
            )

        # Unit/role freeze after person assigned or any case history.
        self.role_locked = bool(
            self.held_person is not None or _user_has_case_history(user)
        )
        if self.role_locked:
            for name in ("unit", "role", "supply_kind", "is_general_manager"):
                if name in self.fields:
                    self.fields[name].disabled = True
            # Admin flag: only the current admin may keep editing that checkbox.
            if profile and not profile.is_admin:
                self.fields["is_admin"].disabled = True

        # Platform admin flag: only the dedicated admin login may keep it.
        if user and (user.username or "").lower() != "admin":
            self.fields["is_admin"].disabled = True
            if not (profile and profile.is_admin):
                self.fields["is_admin"].widget = forms.HiddenInput()

        # General Manager is create-only (at most one). Never offer it on Edit.
        self.fields["is_general_manager"].widget = forms.HiddenInput()
        self.fields["is_general_manager"].required = False

    def clean_seat_code(self):
        from people.usernames import normalize_seat_code, seat_role_pool_filter

        if self.seat_code_locked:
            return (self.user.profile.seat_code or "").strip()
        raw = self.cleaned_data.get("seat_code", "")
        code = normalize_seat_code(raw)
        if not code:
            raise forms.ValidationError("Enter an index like 001 or just 1.")
        profile = self.user.profile
        pool = seat_role_pool_filter(
            unit=profile.unit or "",
            role=profile.role or "",
            supply_kind=profile.supply_kind or "",
            is_general_manager=bool(profile.is_general_manager),
            is_admin=bool(profile.is_admin),
        )
        clash = Profile.objects.filter(**pool, seat_code=code)
        if self.user:
            clash = clash.exclude(user_id=self.user.pk)
        if clash.exists():
            raise forms.ValidationError(
                f"«{code}» is already used by another seat in this role."
            )
        return code

    def clean_signature(self):
        return _validate_png_upload(self.cleaned_data.get("signature"))

    def clean_is_admin(self):
        # Only the dedicated ``admin`` login may be platform administrator.
        if self.user and self.user.username.lower() == "admin":
            return True
        return False

    def clean(self):
        cleaned = super().clean()
        if self.role_locked:
            return cleaned
        if cleaned.get("is_admin") or cleaned.get("is_general_manager"):
            cleaned["unit"] = ""
            cleaned["role"] = ""
            cleaned["supply_kind"] = ""
            return cleaned
        if not cleaned.get("unit"):
            self.add_error("unit", "A unit is required.")
        if not cleaned.get("role"):
            self.add_error("role", "A role is required.")
        unit = cleaned.get("unit")
        role = cleaned.get("role")
        if unit == Unit.SUPPLY and role == Role.EXPERT and not cleaned.get("supply_kind"):
            self.add_error("supply_kind", "Choose Internal or External for a Supply expert.")
        if unit != Unit.SUPPLY or role != Role.EXPERT:
            cleaned["supply_kind"] = ""
        return cleaned

    def save(self) -> User:
        data = self.cleaned_data
        user = self.user
        person = self.held_person
        profile = user.profile

        # Seat index — set once.
        if not self.seat_code_locked:
            code = data.get("seat_code") or ""
            if code:
                profile.seat_code = code

        if person is not None:
            user.first_name = (person.first_name_en or "").strip()
            user.last_name = (person.last_name_en or "").strip()
            user.is_active = True
        else:
            # Unassigned: never keep a human name on the empty seat.
            user.first_name = ""
            user.last_name = ""
            user.is_active = False

        user.email = data.get("email", "") or ""
        user.save()

        if not self.role_locked:
            want_admin = bool(data.get("is_admin"))
            # Only the dedicated admin account may remain administrator.
            if want_admin and self.user and self.user.username.lower() != "admin":
                want_admin = False
            if want_admin:
                Profile.objects.filter(is_admin=True).exclude(pk=profile.pk).update(
                    is_admin=False)
            profile.is_admin = want_admin
            # GM is create-only; edit never promotes or demotes via checkbox.
            if want_admin:
                profile.is_general_manager = False
            privileged = profile.is_admin or profile.is_general_manager
            profile.unit = "" if privileged else (data.get("unit", "") or "")
            profile.role = "" if privileged else (data.get("role", "") or "")
            profile.supply_kind = (
                data.get("supply_kind", "") or ""
                if (profile.unit == Unit.SUPPLY and profile.role == Role.EXPERT)
                else ""
            )
        else:
            # Locked seats still must not keep a stolen Administrator flag.
            if self.user and self.user.username.lower() != "admin":
                profile.is_admin = False
            elif self.user and self.user.username.lower() == "admin":
                profile.is_admin = True
                profile.is_general_manager = False
                profile.unit = ""
                profile.role = ""
                profile.supply_kind = ""

        if person is not None:
            profile.internal_code = (person.internal_code or "").strip()
            profile.org_number = person.detail_code or ""
            gender_map = {
                "آقا": "MALE", "خانم": "FEMALE",
                "MALE": "MALE", "FEMALE": "FEMALE",
            }
            profile.gender = gender_map.get((person.gender or "").strip(), "") or profile.gender
            profile.seat_ready = False
        else:
            # Keep existing profile code on vacant seats; do not invent one here.
            profile.org_number = ""
            profile.gender = ""
            # Ready for Available seats once it has a seat index.
            profile.seat_ready = bool(profile.seat_code)
        profile.org_title = ""
        if data.get("signature"):
            profile.signature = data["signature"]
        profile.save()

        if person is not None and not self.role_locked:
            from people.models import PersonRole
            from people.seats import roles_of

            roles = list(roles_of(person))
            kw = {
                "unit": profile.unit or "",
                "role": profile.role or "",
                "supply_kind": profile.supply_kind or "",
                "internal_code": profile.internal_code or "",
                "is_admin": bool(profile.is_admin),
                "is_general_manager": bool(profile.is_general_manager),
            }
            if not roles:
                PersonRole.objects.create(person=person, **kw)
            elif len(roles) == 1:
                target = roles[0]
                for k, v in kw.items():
                    setattr(target, k, v)
                target.save(update_fields=list(kw.keys()))
        return user


class AdminPlatformForm(forms.ModelForm):
    """Administrators may customise platform-wide sign-in text and VAT %."""

    class Meta:
        model = PlatformConfig
        fields = ["login_welcome_message", "vat_percent"]
        labels = {
            "login_welcome_message": "Sign-in welcome message",
            "vat_percent": "VAT percent (%)",
        }
        help_texts = {
            "login_welcome_message": (
                "Displayed for every user after “Hi, <name>” on successful sign-in."
            ),
            "vat_percent": (
                "Used on Proforma totals: VAT = Subtotal × this percent / 100. "
                "Shown as “VAT (N%)” on PDF/Excel preview totals."
            ),
        }


class AdminUnitStampsForm(forms.ModelForm):
    """One company seal per working unit — PNG or SVG."""

    class Meta:
        model = PlatformConfig
        fields = ["stamp_commercial", "stamp_technical", "stamp_supply"]
        labels = {
            "stamp_commercial": "Commercial stamp",
            "stamp_technical": "Technical stamp",
            "stamp_supply": "Supply stamp",
        }
        help_texts = {
            "stamp_commercial": "PNG or SVG — stamped on Proforma (PI) exports.",
            "stamp_technical": "PNG or SVG — stamped on Technical Offer (TO) exports.",
            "stamp_supply": "PNG or SVG — reserved for Supply exports.",
        }
        widgets = {
            "stamp_commercial": StampFileInput(),
            "stamp_technical": StampFileInput(),
            "stamp_supply": StampFileInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            self.fields[name].required = False

    def clean_stamp_commercial(self):
        return self._clean_stamp("stamp_commercial")

    def clean_stamp_technical(self):
        return self._clean_stamp("stamp_technical")

    def clean_stamp_supply(self):
        return self._clean_stamp("stamp_supply")

    def _clean_stamp(self, field_name):
        from django.core.files.uploadedfile import UploadedFile

        f = self.cleaned_data.get(field_name)
        if isinstance(f, UploadedFile):
            return _validate_stamp_upload(f)
        return None

    def save(self, commit=True):
        import os
        from django.core.files.uploadedfile import UploadedFile

        cfg = self.instance
        if cfg.pk:
            cfg = PlatformConfig.objects.get(pk=cfg.pk)

        for field_name in ("stamp_commercial", "stamp_technical", "stamp_supply"):
            f = self.cleaned_data.get(field_name)
            if not isinstance(f, UploadedFile):
                continue
            try:
                f.seek(0)
            except Exception:
                pass
            base = os.path.basename(getattr(f, "name", "") or "") or f"{field_name}.png"
            low = base.lower()
            if not (low.endswith(".png") or low.endswith(".svg")):
                # Keep extension from content-type when possible.
                ct = (getattr(f, "content_type", "") or "").lower()
                base = f"{base}.svg" if "svg" in ct else f"{base}.png"
            getattr(cfg, field_name).save(base, f, save=False)

        if commit:
            cfg.save()
        return cfg


class SelfProfileForm(forms.ModelForm):
    """Users update their own signature image (PNG)."""

    class Meta:
        model = Profile
        fields = ["signature"]
        labels = {
            "signature": "Signature",
        }
        help_texts = {
            "signature": "PNG only — transparent handwritten signature. Uploading a new file replaces the previous one.",
        }
        widgets = {
            "signature": PngFileInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["signature"].required = False

    def clean_signature(self):
        from django.core.files.uploadedfile import UploadedFile

        f = self.cleaned_data.get("signature")
        if isinstance(f, UploadedFile):
            return _validate_png_upload(f)
        # No new file chosen — keep whatever is already stored (do not clear).
        return None

    def save(self, commit=True):
        """Persist uploaded PNG bytes explicitly so the file always lands on disk.

        ModelForm + FileInput can leave a DB path with a missing media file when
        the upload stream was partially read during validation. We write the
        UploadedFile ourselves and never clear an existing image unless a new
        one is provided.
        """
        import os
        from django.core.files.uploadedfile import UploadedFile

        profile = self.instance
        if profile.pk:
            # Reload so we never accidentally wipe fields not in this form.
            profile = Profile.objects.get(pk=profile.pk)

        sig = self.cleaned_data.get("signature")
        if isinstance(sig, UploadedFile):
            try:
                sig.seek(0)
            except Exception:
                pass
            base = os.path.basename(getattr(sig, "name", "") or "") or "signature.png"
            if not base.lower().endswith(".png"):
                base = f"{base}.png"
            profile.signature.save(base, sig, save=False)

        if commit:
            profile.save()
        return profile


class SelfPasswordForm(forms.Form):
    """Lets a signed-in user change their own password."""

    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        label="Current password")
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        min_length=6, label="New password")
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Confirm new password")

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        pw = self.cleaned_data["current_password"]
        if not self.user.check_password(pw):
            raise forms.ValidationError("Your current password is incorrect.")
        return pw

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get("new_password")
        confirm = cleaned.get("confirm_password")
        if new and confirm and new != confirm:
            self.add_error("confirm_password", "The new passwords do not match.")
        return cleaned

    def save(self):
        user = self.user
        new = self.cleaned_data["new_password"]
        user.set_password(new)
        user.save()
        prof = getattr(user, "profile", None)
        if prof and prof.must_change_password:
            # They've now set their own password through the normal channel —
            # the forced-change screen (for a temp/reset password) no longer
            # applies.
            prof.must_change_password = False
            prof.save(update_fields=["must_change_password"])
        return user


class ForcePasswordChangeForm(forms.Form):
    """Shown when Profile.must_change_password is set — reached right after
    signing in with a temporary password (freshly created account, or after
    an admin reset). No "current password" field: the one being replaced was
    never really theirs, it was a one-time value generated for this exact
    purpose, already shown to the admin and never stored anywhere.
    """

    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        min_length=6, label="New password")
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Confirm new password")

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get("new_password")
        confirm = cleaned.get("confirm_password")
        if new and confirm and new != confirm:
            self.add_error("confirm_password", "The new passwords do not match.")
        return cleaned

    def save(self):
        user = self.user
        user.set_password(self.cleaned_data["new_password"])
        user.save()
        prof = getattr(user, "profile", None)
        if prof:
            prof.must_change_password = False
            prof.save(update_fields=["must_change_password"])
        return user
