"""High-level license service used by the Django layer.

Ties together the machine fingerprint, the sealed state file, signature
verification and the pure evaluator.  Provides:

    * :func:`current_status`  -- cached, called by the middleware / templates
    * :func:`activate`        -- validate and store a pasted license string
    * :func:`machine_id`      -- the running machine fingerprint

The result of :func:`current_status` is cached for a few seconds so that gating
every HTTP request stays cheap (no repeated crypto / disk / subprocess work).
"""

from __future__ import annotations

import datetime as _dt
import os
import threading
from dataclasses import dataclass

from django.utils import timezone

from . import crypto_core, evaluator, machine, state

_CACHE_TTL = int(os.environ.get("LICENSE_CACHE_TTL", "10"))
_lock = threading.Lock()
_cache: tuple[float, "Status"] | None = None


# ---------------------------------------------------------------------------
# Status object handed to the rest of the application
# ---------------------------------------------------------------------------
@dataclass
class Status:
    ok: bool
    reason: str
    machine_id: str
    key_configured: bool
    has_license: bool
    expiry: _dt.date | None = None
    days_remaining: int | None = None
    grace_days: int = evaluator.DEFAULT_GRACE_DAYS
    in_grace: bool = False

    @property
    def show_warning(self) -> bool:
        return (
            self.has_license
            and self.days_remaining is not None
            and self.days_remaining <= evaluator.WARN_DAYS
        )

    @property
    def expiry_iso(self) -> str:
        return self.expiry.isoformat() if self.expiry else ""

    @property
    def reason_text(self) -> str:
        return {
            evaluator.OK: "Active",
            evaluator.NO_KEY: "Public key is not installed yet",
            evaluator.NO_LICENSE: "Software is not activated yet",
            evaluator.TAMPERED: "License state file has been tampered with",
            evaluator.BAD_SIGNATURE: "License signature is invalid",
            evaluator.WRONG_MACHINE: "License was issued for a different machine",
            evaluator.CLOCK_ROLLBACK: "System clock has been moved backwards",
            evaluator.EXPIRED: "License has expired",
        }.get(self.reason, self.reason)


# ---------------------------------------------------------------------------
# Paths / helpers
# ---------------------------------------------------------------------------
def _state_dir() -> str:
    return os.environ.get("LICENSE_STATE_DIR", os.getcwd())


def _state_path() -> str:
    return os.path.join(_state_dir(), ".runtime_state")


def machine_id() -> str:
    return machine.get_machine_id()


def _parse_dt(value) -> _dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------
def _evaluate_now() -> Status:
    mid = machine.get_machine_id()

    if not crypto_core.public_key_is_configured():
        return Status(False, evaluator.NO_KEY, mid, key_configured=False, has_license=False)

    path = _state_path()
    file_exists = os.path.exists(path)
    data = state.read_state(path, mid)
    if data is None:
        reason = evaluator.TAMPERED if file_exists else evaluator.NO_LICENSE
        return Status(False, reason, mid, key_configured=True, has_license=file_exists)

    license_str = data.get("license", "")
    payload = crypto_core.verify_license_string(license_str)
    if payload is None:
        return Status(False, evaluator.BAD_SIGNATURE, mid, key_configured=True, has_license=True)

    now = timezone.now()
    last_seen = _parse_dt(data.get("last_seen"))
    result = evaluator.evaluate_payload(payload, mid, now, last_seen)

    # Advance the monotonic high-water mark when the clock has moved forward.
    if result.ok and (last_seen is None or now > last_seen):
        new_data = dict(data)
        new_data["last_seen"] = now.isoformat()
        state.write_state(path, new_data, mid)

    return Status(
        ok=result.ok,
        reason=result.reason,
        machine_id=mid,
        key_configured=True,
        has_license=True,
        expiry=result.expiry,
        days_remaining=result.days_remaining,
        grace_days=result.grace_days,
        in_grace=result.in_grace,
    )


def current_status(*, force: bool = False) -> Status:
    """Return the (cached) current license status."""
    global _cache
    now = timezone.now().timestamp()
    if not force and _cache is not None and (now - _cache[0]) < _CACHE_TTL:
        return _cache[1]
    with _lock:
        status = _evaluate_now()
        _cache = (now, status)
        return status


def _invalidate_cache() -> None:
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# Activation (and additive renewal safety)
# ---------------------------------------------------------------------------
def activate(license_string: str) -> tuple[bool, str]:
    """Validate ``license_string`` and persist it.  Returns (ok, message_fa).

    Renewal logic: the new license already carries an *absolute* expiry that the
    seller computed as ``previous_expiry + duration`` (so remaining days are
    preserved).  As an extra safety net we never let activation *reduce* the
    stored validity -- if the currently stored license ends later than the new
    one, the longer one is kept.
    """
    mid = machine.get_machine_id()
    payload = crypto_core.verify_license_string(license_string)
    if payload is None:
        if not crypto_core.public_key_is_configured():
            return False, "Public key is not installed in this build. Install the public key first."
        return False, "Invalid license: the signature does not verify, or the license string is corrupted/incomplete."

    if str(payload.get("machine_id", "")) != mid:
        return False, "This license was not issued for this machine (the machine ID does not match)."

    new_expiry = evaluator._parse_date(payload.get("expiry"))
    if new_expiry is None:
        return False, "The expiry date inside the license is invalid."

    path = _state_path()
    chosen_license = license_string
    chosen_expiry = new_expiry

    existing = state.read_state(path, mid)
    last_seen = _parse_dt(existing.get("last_seen")) if existing else None
    if existing:
        cur_payload = crypto_core.verify_license_string(existing.get("license", ""))
        if cur_payload and str(cur_payload.get("machine_id", "")) == mid:
            cur_expiry = evaluator._parse_date(cur_payload.get("expiry"))
            if cur_expiry and cur_expiry > new_expiry:
                chosen_license = existing.get("license", "")
                chosen_expiry = cur_expiry

    now = timezone.now()
    seen = max(now, last_seen) if last_seen else now
    data = {"license": chosen_license, "last_seen": seen.isoformat()}
    if not state.write_state(path, data, mid):
        return False, "Failed to write the license state file to disk."

    _invalidate_cache()
    days = (chosen_expiry - now.date()).days
    return True, f"License activated successfully. Valid until {chosen_expiry.isoformat()} ({days} days)."
