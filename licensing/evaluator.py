"""Pure license-enforcement decision logic.

Given an already signature-verified payload plus the current machine id, the
current time and the last-seen high-water mark, decide whether the software is
allowed to run.  This module has no Django, crypto, filesystem or network
dependencies, which makes the security rules trivial to unit-test in isolation.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

# ---- Tunable policy constants ---------------------------------------------
DEFAULT_GRACE_DAYS = 3          # days the software keeps working after expiry
WARN_DAYS = 3                   # show the renewal warning when <= this many days
CLOCK_TOLERANCE_SECONDS = 6 * 3600  # ignore small backward clock jitter (NTP)

# ---- Reason codes ----------------------------------------------------------
OK = "ok"
NO_KEY = "no_key"               # public key not installed yet
NO_LICENSE = "no_license"       # never activated / state missing
TAMPERED = "tampered"           # state seal failed
BAD_SIGNATURE = "bad_signature"  # license signature invalid
WRONG_MACHINE = "wrong_machine"  # license issued for a different machine
CLOCK_ROLLBACK = "clock_rollback"
EXPIRED = "expired"


@dataclass
class Result:
    ok: bool
    reason: str
    expiry: _dt.date | None = None
    grace_days: int = DEFAULT_GRACE_DAYS
    days_remaining: int | None = None   # relative to expiry (may be negative)
    in_grace: bool = False

    @property
    def show_warning(self) -> bool:
        """True when a soon-to-expire (or in-grace) license should warn."""
        if self.days_remaining is None:
            return False
        return self.days_remaining <= WARN_DAYS


def _parse_date(value) -> _dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return _dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def evaluate_payload(
    payload: dict,
    machine_id: str,
    now: _dt.datetime,
    last_seen: _dt.datetime | None,
    *,
    clock_tolerance_seconds: int = CLOCK_TOLERANCE_SECONDS,
) -> Result:
    """Apply every rule and return a :class:`Result`.

    ``payload`` must already have a valid signature (verified by ``crypto_core``)
    -- this function does not re-check the signature.
    """
    expiry = _parse_date(payload.get("expiry"))
    if expiry is None:
        return Result(ok=False, reason=BAD_SIGNATURE)

    try:
        grace_days = int(payload.get("grace_days", DEFAULT_GRACE_DAYS))
    except (TypeError, ValueError):
        grace_days = DEFAULT_GRACE_DAYS
    grace_days = max(0, grace_days)

    today = now.date()
    days_remaining = (expiry - today).days
    deadline = expiry + _dt.timedelta(days=grace_days)
    in_grace = today > expiry and today <= deadline

    # (b) license bound to a different machine.
    if str(payload.get("machine_id", "")) != str(machine_id):
        return Result(False, WRONG_MACHINE, expiry, grace_days, days_remaining, in_grace)

    # (e) system clock rolled back compared to what we have already seen.
    if last_seen is not None:
        tolerance = _dt.timedelta(seconds=clock_tolerance_seconds)
        if now < last_seen - tolerance:
            return Result(False, CLOCK_ROLLBACK, expiry, grace_days, days_remaining, in_grace)

    # (d) expired beyond the grace period.
    if today > deadline:
        return Result(False, EXPIRED, expiry, grace_days, days_remaining, in_grace)

    return Result(True, OK, expiry, grace_days, days_remaining, in_grace)
