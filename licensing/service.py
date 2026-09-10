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
            evaluator.MACHINE_MOVED: (
                "This installation was moved to different hardware "
                "(the machine signals no longer match the activated machine)"
            ),
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


def display_machine_id(status: "Status") -> str:
    """The machine ID the customer should send to the vendor.

    It must be an ID :func:`activate` will actually accept, or the customer pays
    for a licence that cannot be installed -- so this mirrors that function's
    rules rather than blindly printing the bound value:

    * after a real hardware move the bound value is whatever the old server's
      ``.machine_fp`` carried over; a licence for it would be for a server the
      customer no longer has;
    * a bound value that came from the cache file and is *not* backed by a state
      file sealed for it is not accepted at activation either (that is the whole
      point of no longer trusting ``.machine_fp``), so we show the live one.
    """
    if status.reason == evaluator.MACHINE_MOVED:
        return machine.compute_live_machine_id()
    bound = status.machine_id
    live = machine.compute_live_machine_id()
    if bound == live:
        return bound
    if state.read_state(_state_path(), bound) is None:
        return live
    return bound


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

    if result.ok:
        # Hardware binding.  ``mid`` may have come from the ``.machine_fp`` cache
        # file, which anybody can rewrite, so matching it proves nothing on its
        # own.  Re-probing the hardware and comparing it against the digests
        # sealed into this state file is what actually holds the licence here.
        #
        # Deliberately inside the ``result.ok`` branch: an install that already
        # fails an older rule keeps its original reason, and a locked system
        # never pays for the probes.
        stored_signals = state.read_signals(data)
        # Cheap tier first (file reads only).  When a stable signal confirms the
        # machine there is nothing left to learn, so PowerShell / wmic / lsblk are
        # never spawned -- which on the Linux deployment is the normal path.
        live_signals = machine.signal_digests(allow_subprocess=False)
        signals = evaluator.check_machine_signals(stored_signals, live_signals)
        if signals.outcome != evaluator.SIGNALS_MATCH:
            # Inconclusive, or a contradiction the expensive probes might yet
            # answer for.  Only now do we pay for them (once per process).
            live_signals = machine.signal_digests()
            signals = evaluator.check_machine_signals(stored_signals, live_signals)
        if not signals.ok:
            return Status(
                ok=False,
                reason=evaluator.MACHINE_MOVED,
                machine_id=mid,
                key_configured=True,
                has_license=True,
                expiry=result.expiry,
                days_remaining=result.days_remaining,
                grace_days=result.grace_days,
                in_grace=result.in_grace,
            )

        new_data = dict(data)
        dirty = False
        # Advance the monotonic high-water mark when the clock has moved forward.
        if last_seen is None or now > last_seen:
            new_data["last_seen"] = now.isoformat()
            dirty = True
        # Record (or refresh) the signal set opportunistically, so drift is
        # absorbed instead of accumulating against activation day.  This is also
        # what silently upgrades a grandfathered install: it validates as it
        # always did, and comes out of the check bound to its hardware, with no
        # re-activation asked of anyone.  The evaluator decides *what* to store;
        # it will not let an unverified host drop a stable digest.
        if signals.should_record:
            new_data = state.with_signals(new_data, signals.record)
            dirty = True
        if dirty:
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
    # Only a *valid* status is served from the cache. The cache is a module global,
    # so under gunicorn each worker process has its own: activation happens in one
    # worker and the others would otherwise keep bouncing the customer back to the
    # activation screen until their own TTL lapsed. A negative result is therefore
    # always re-read from disk, which is what makes the other workers notice the
    # freshly written state file at once. This costs nothing on a licensed system
    # (the fast path is unchanged) and, while locked, _evaluate_now only reads --
    # it writes the state file solely when the licence verifies.
    if not force and _cache is not None and _cache[1].ok and (now - _cache[0]) < _CACHE_TTL:
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
    payload = crypto_core.verify_license_string(license_string)
    if payload is None:
        if not crypto_core.public_key_is_configured():
            return False, "Public key is not installed in this build. Install the public key first."
        return False, "Invalid license: the signature does not verify, or the license string is corrupted/incomplete."

    path = _state_path()
    claimed = str(payload.get("machine_id", ""))
    bound = machine.get_machine_id()               # may come from .machine_fp
    live = machine.compute_live_machine_id()       # always re-probed, never cached
    live_signals = machine.signal_digests()

    if claimed == live:
        # The vendor signed for the hardware actually in front of us.  Nothing
        # else can outrank that: only the private key can produce this string,
        # and it names this machine.  This is the path a customer who genuinely
        # moved servers takes, and it is also the way back from a refusal --
        # without it a stale value in a cache file they cannot see would leave
        # them permanently locked out of software they have paid for.
        mid = live
        machine.adopt_machine_id(live)
        existing = state.read_state(path, mid)
    elif claimed == bound:
        # The licence names the *bound* fingerprint, which reached us through
        # ``.machine_fp``.  That file is bare text anyone may write, so on its own
        # it is not evidence of anything -- but a sealed state file that opens
        # under the same id is: it was written here, by this install, when that id
        # was live.  This is the ordinary Docker case, where the container's MAC
        # and machine-id were regenerated by a rebuild and the cache is exactly
        # what keeps the licence alive.
        existing = state.read_state(path, bound)
        if existing is None:
            # A fingerprint out of the cache file with nothing behind it.  This is
            # how a copied deployment used to be activated on a stranger's machine
            # after deleting the state file: write the victim's id, paste their
            # licence.  The customer's own recovery path is unaffected -- they
            # take the Machine ID this page now shows, which is the live one.
            return False, (
                "This license was issued for a machine ID that this installation "
                "cannot prove belongs to it (no valid license state file exists "
                "for that ID). Request a license for the Machine ID shown on this page."
            )
        prior = evaluator.check_machine_signals(
            state.read_signals(existing), live_signals
        )
        if not prior.ok:
            # Activation must not be a way to launder a move away: the licence
            # string sits in plain sight inside the state file, so anyone who
            # copied the deployment could read it out, paste it back, and have us
            # re-record the signal set against *their* hardware.  Only a genuine
            # contradiction of a stable signal gets here; drift never does.
            return False, (
                "The stored license state belongs to different hardware. "
                "This installation appears to have been moved to another machine; "
                "request a license for the Machine ID shown on this page."
            )
        mid = bound
    else:
        return False, "This license was not issued for this machine (the machine ID does not match)."

    new_expiry = evaluator._parse_date(payload.get("expiry"))
    if new_expiry is None:
        return False, "The expiry date inside the license is invalid."

    chosen_license = license_string
    chosen_expiry = new_expiry

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
    # Capture what this machine looks like, signal by signal, at the moment of
    # activation.  This is the reference every later validation is measured
    # against; a host that offers no host-scoped signal still gets its set
    # recorded, it just cannot be enforced (see evaluator.check_machine_signals).
    # Going through adopt_signal_record keeps any stable digest the probes have
    # stopped answering for, so re-activating on a hardened host cannot quietly
    # disarm the binding.
    data = state.with_signals(
        data,
        evaluator.adopt_signal_record(state.read_signals(existing), live_signals),
    )
    if not state.write_state(path, data, mid):
        return False, "Failed to write the license state file to disk."

    _invalidate_cache()
    days = (chosen_expiry - now.date()).days
    return True, f"License activated successfully. Valid until {chosen_expiry.isoformat()} ({days} days)."
