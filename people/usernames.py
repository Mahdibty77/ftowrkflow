"""How a person's sign-in name is worked out.

Rule: Latin first name, underscore, Latin last name, then six random digits —
``Mahdi_Bayati309214``. Random digits stop anyone guessing another person's
username from their real name alone.

One human has one login username. Extra organisational roles share that same
account (see people.seats / PersonRole); there is no ``_2`` seat suffix.

Separately, each seat has a short display index ``seat_code`` (``001``,
``002``, …) that is unique **within** a Unit+Role pool (and supply_kind /
GM). That index is never the login username.
"""
import re
import secrets
import unicodedata

from django.contrib.auth.models import User

# Django's own limit on auth_user.username.
MAX_USERNAME = 150

_INDEX_RE = re.compile(r"^(\d{1,3})$")
_USER_N_RE = re.compile(r"^user(\d+)$", re.IGNORECASE)  # legacy during migration


def _latinise(value: str) -> str:
    """Fold to plain ASCII letters and digits, Titlecased."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value).strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", text) if p]
    return "".join(p[:1].upper() + p[1:].lower() for p in parts)


def _six_digits() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def build_username(first_en: str, last_en: str, *, digits: str | None = None) -> str:
    """``Mahdi_Bayati309214``. Never raises; returns "" if there is nothing to build from."""
    first = _latinise(first_en)
    last = _latinise(last_en)
    stem = "_".join(p for p in (first, last) if p)
    if not stem:
        return ""
    tail = digits if digits is not None else _six_digits()
    room = MAX_USERNAME - len(tail)
    return f"{stem[:room]}{tail}"


def person_username(person) -> str:
    """Unique ``First_Last######`` for ``person``.

    Stable across saves: if the person already has a username whose stem still
    matches the current Latin names, keep it. Only mint a new random tail when
    blank or when the Latin name stem changes.
    """
    from .models import Person

    first = _latinise(person.first_name_en)
    last = _latinise(person.last_name_en)
    stem = "_".join(p for p in (first, last) if p)
    if not stem:
        return ""

    existing = (person.username or "").strip()
    if existing:
        # Keep if it still starts with the same stem and ends with digits.
        if existing.startswith(stem) and re.search(r"\d{6,}$", existing):
            return existing
        # Stem changed (rename) — fall through and mint a new handle.

    held_ids = []
    if person.pk:
        held_ids = list(person.accounts.values_list("user_id", flat=True)[:1])

    for _ in range(40):
        candidate = build_username(person.first_name_en, person.last_name_en)
        if not candidate:
            return ""
        clash = Person.objects.filter(username=candidate)
        if person.pk:
            clash = clash.exclude(pk=person.pk)
        user_clash = User.objects.filter(username__iexact=candidate)
        if held_ids:
            user_clash = user_clash.exclude(pk__in=held_ids)
        if not clash.exists() and not user_clash.exists():
            return candidate

    return f"{stem[:MAX_USERNAME - 10]}{secrets.token_hex(4)}"


def format_seat_index(n: int) -> str:
    """Integer → zero-padded three-digit index (``001`` … ``999``)."""
    n = max(1, int(n))
    if n > 999:
        return str(n)
    return f"{n:03d}"


def parse_seat_index(code: str) -> int | None:
    """Accept ``001`` / ``1`` / legacy ``user3`` → integer, or None."""
    raw = (code or "").strip().lower().replace(" ", "")
    if not raw:
        return None
    m = _USER_N_RE.match(raw)
    if m:
        return int(m.group(1))
    if raw.isdigit():
        return int(raw)
    m = _INDEX_RE.match(raw)
    if m:
        return int(m.group(1))
    return None


def seat_role_pool_filter(
    *,
    unit: str = "",
    role: str = "",
    supply_kind: str = "",
    is_general_manager: bool = False,
    is_admin: bool = False,
):
    """Q-kwargs identifying one index pool on Profile."""
    if is_admin:
        return {
            "is_admin": True,
            "is_general_manager": False,
            "unit": "",
            "role": "",
            "supply_kind": "",
        }
    if is_general_manager:
        return {
            "is_admin": False,
            "is_general_manager": True,
            "unit": "",
            "role": "",
            "supply_kind": "",
        }
    return {
        "is_admin": False,
        "is_general_manager": False,
        "unit": unit or "",
        "role": role or "",
        "supply_kind": supply_kind or "",
    }


def next_seat_index(
    *,
    unit: str = "",
    role: str = "",
    supply_kind: str = "",
    is_general_manager: bool = False,
    is_admin: bool = False,
) -> str:
    """Next free ``001``-style index inside the given Unit+Role pool."""
    from accounts.models import Profile

    pool = seat_role_pool_filter(
        unit=unit, role=role, supply_kind=supply_kind,
        is_general_manager=is_general_manager, is_admin=is_admin,
    )
    highest = 0
    for code in (
        Profile.objects.filter(**pool)
        .exclude(seat_code__isnull=True)
        .exclude(seat_code="")
        .values_list("seat_code", flat=True)
    ):
        n = parse_seat_index(code)
        if n is not None:
            highest = max(highest, n)
    n = highest + 1
    while Profile.objects.filter(**pool, seat_code=format_seat_index(n)).exists():
        n += 1
    return format_seat_index(n)


def next_seat_code(**kwargs) -> str:
    """Alias kept for older call sites — prefers kwargs, else global legacy scan."""
    if kwargs:
        return next_seat_index(**kwargs)
    # Fallback: highest numeric index across all pools (create form should pass pool).
    from accounts.models import Profile

    highest = 0
    for code in Profile.objects.exclude(seat_code__isnull=True).exclude(
        seat_code="",
    ).values_list("seat_code", flat=True):
        n = parse_seat_index(code)
        if n is not None:
            highest = max(highest, n)
    return format_seat_index(highest + 1)


def vacant_login_username(seat_code: str, user_pk=None) -> str:
    """Internal Django username for an empty seat (never shown as the Index).

    Prefer a stable ``_seat<pk>`` once the User exists; otherwise a private
    token derived from the display index.
    """
    if user_pk:
        base = f"_seat{user_pk}"
    else:
        code = (seat_code or "").strip() or secrets.token_hex(3)
        base = f"_s{code}"
    if not User.objects.filter(username__iexact=base).exclude(
            pk=user_pk).exists():
        return base[:MAX_USERNAME]
    for _ in range(20):
        candidate = f"{base}_{secrets.token_hex(2)}"
        if not User.objects.filter(username__iexact=candidate).exclude(
                pk=user_pk).exists():
            return candidate[:MAX_USERNAME]
    return f"{base}_{secrets.token_hex(4)}"[:MAX_USERNAME]


def next_placeholder_username() -> str:
    return next_seat_code()


def placeholder_username(user_pk=None) -> str:
    """Neutral login name for a freed seat, based on its seat_code when known."""
    if user_pk:
        from accounts.models import Profile
        profile = Profile.objects.filter(user_id=user_pk).only("seat_code").first()
        if profile and profile.seat_code:
            return vacant_login_username(profile.seat_code, user_pk=user_pk)
    return vacant_login_username(next_seat_code(), user_pk=user_pk)


def seat_username_candidates(base: str, limit: int = 40):
    """Yield ``base`` then fresh random-digit variants of the same stem.

    Used when claiming a username for a login; no ``_2`` seat suffixes.
    """
    if not base:
        return
    yield base
    # Strip trailing digits to rebuild with a new random tail.
    stem = re.sub(r"\d+$", "", base)
    if not stem:
        stem = base
    for _ in range(2, limit + 1):
        # Vacant logins start with _; keep regenerating vacant-style names.
        if base.startswith("_"):
            yield f"{stem}{secrets.token_hex(2)}"[:MAX_USERNAME]
            continue
        tail = _six_digits()
        room = MAX_USERNAME - len(tail)
        yield f"{stem[:room]}{tail}"


def normalize_seat_code(value: str) -> str:
    """Accept ``001`` / ``1`` / legacy ``user3`` → ``001``."""
    n = parse_seat_index(value)
    if n is None:
        return ""
    return format_seat_index(n)


def parse_seat_code_number(code: str) -> int | None:
    """Backward-compatible alias."""
    return parse_seat_index(code)
