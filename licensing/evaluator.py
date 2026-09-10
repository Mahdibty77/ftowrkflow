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
MACHINE_MOVED = "machine_moved"  # hardware signals no longer match activation
CLOCK_ROLLBACK = "clock_rollback"
EXPIRED = "expired"

# ---- Outcomes of the hardware-signal check ---------------------------------
SIGNALS_NO_RECORD = "no_record"      # pre-existing install, nothing recorded yet
SIGNALS_MATCH = "match"              # a stable signal is present and agrees
SIGNALS_UNDECIDABLE = "undecidable"  # no stable evidence either way -> allow
SIGNALS_MISMATCH = "mismatch"        # a stable signal is present and disagrees

# ---- Which signals actually identify a machine -----------------------------
# The signal check turns on this classification, not on how many names match.
# Counting cannot tell "the customer rebuilt their container" apart from "this is
# somebody else's server", because a rebuild changes just as many names as a move
# does -- which is how a plain quorum managed to lock out honest Docker users and
# still wave clones through.
#
# STABLE / host-scoped -- survive a container rebuild, describe the metal:
#     board      DMI board_serial / product_uuid / product_serial, Win32_BaseBoard
#     disk       serial of the first block device
#     osid_host  a machine-id read from a *mounted host* file
#                (LICENSE_HOST_MACHINE_ID_FILE or /host/etc/machine-id)
#
# VOLATILE / container-scoped -- everything else, in particular:
#     mac        uuid.getnode(); a container gets a fresh one on every rebuild
#     osid       the container's *own* /etc/machine-id, regenerated on rebuild
#     cpu        on Linux this is the CPU *model name* from /proc/cpuinfo -- a
#                string thousands of unrelated machines share, so it is weak
#                corroboration and never identity
#
# Volatile signals are recorded (they are useful when diagnosing a support call)
# but they may never, on their own, refuse anything.
STABLE_SIGNALS = frozenset({"board", "disk", "osid_host"})


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


def is_stable_signal(name: str) -> bool:
    """True for signals that identify the host rather than the container.

    A name we do not recognise counts as volatile.  Unknown names can only come
    from a future or foreign build of this file, and an unknown name must never
    be the thing that refuses a paying customer.
    """
    return name in STABLE_SIGNALS


@dataclass
class SignalCheck:
    """Verdict of comparing recorded hardware signals against live ones."""

    ok: bool
    outcome: str
    stored: dict[str, str]          # what the sealed state file recorded
    live: dict[str, str]            # what the machine answers right now
    record: dict[str, str]          # the set the caller should now persist
    confirmed: tuple[str, ...]      # stable names present live and equal
    contradicted: tuple[str, ...]   # stable names present live and different

    @property
    def should_record(self) -> bool:
        """True when :attr:`record` actually differs from what is on disk."""
        return self.record != self.stored


def adopt_signal_record(
    stored: dict[str, str] | None,
    live: dict[str, str] | None,
) -> dict[str, str]:
    """The set to store once identity is established, absorbing drift.

    The live picture wins, so a rebuilt container's new MAC replaces the old one
    and tomorrow's comparison is made against today's reality.  The exception is
    a *stable* name that was recorded and did not answer this time: it is kept.
    Dropping it would let one run with a hardened /sys quietly disarm the check
    for good, which is precisely the downgrade an attacker would aim for.
    """
    merged = {n: d for n, d in (stored or {}).items() if is_stable_signal(n)}
    merged.update(live or {})
    return merged


def _refresh_volatile(stored: dict[str, str], live: dict[str, str]) -> dict[str, str]:
    """Update only the volatile names, leaving every recorded stable one alone.

    Used when nothing stable could be confirmed.  We still want the MAC of a
    rebuilt container on file for support, but we must not let an unverified host
    overwrite -- or extend -- the stable digests that identify the customer's
    machine.
    """
    merged = dict(stored)
    for name, digest in live.items():
        if not is_stable_signal(name):
            merged[name] = digest
    return merged


def check_machine_signals(
    stored: dict[str, str] | None,
    live: dict[str, str] | None,
) -> SignalCheck:
    """Decide whether the live hardware is still the hardware we activated on.

    This is the check that gives the machine lock teeth.  The combined
    fingerprint alone could not do it, because the only reason a moved install
    keeps working is the ``.machine_fp`` cache file -- and a cache file anyone
    can rewrite proves nothing.  Comparing the *signals* one by one cannot be
    satisfied by editing a value someone hands us.

    The rule is deliberately one-sided, because refusing a paying customer is a
    worse failure than accepting an install we cannot prove:

    * we refuse **only on contradiction of a stable signal** -- a host-scoped
      name that was recorded, *is* present in the live probe, and hashes
      differently.  Absence is never evidence of a move: a probe that stopped
      answering (a hardened container, a stripped VM, a kernel that no longer
      exposes the serial) says nothing at all about where we are running;
    * a contradiction is ignored when some *other* stable signal still agrees.
      That is a repair, not a move -- a swapped disk in the same chassis -- and
      the customer keeps running while the new serial is recorded;
    * volatile signals (MAC, container machine-id, CPU model) never refuse
      anything.  They are exactly the names a ``docker compose up --build``
      regenerates, which is the case the fingerprint cache exists to survive;
    * nothing recorded at all -> allow, and record.  Every install activated
      before signals existed lands here and must keep working (grandfathering).
    """
    stored = dict(stored or {})
    live = dict(live or {})

    confirmed = tuple(sorted(
        name for name, digest in stored.items()
        if is_stable_signal(name) and name in live and live[name] == digest
    ))
    contradicted = tuple(sorted(
        name for name, digest in stored.items()
        if is_stable_signal(name) and name in live and live[name] != digest
    ))

    if not stored:
        # Pre-existing install: it has always worked, and it keeps working.  We
        # take the opportunity to record what this machine looks like, so it
        # gains the protection without the customer re-activating anything.
        return SignalCheck(
            ok=True, outcome=SIGNALS_NO_RECORD, stored=stored, live=live,
            record=dict(live), confirmed=confirmed, contradicted=contradicted,
        )

    if contradicted and not confirmed:
        # Positive evidence of a different machine, and nothing that says
        # otherwise.  This is the only door to a refusal.
        return SignalCheck(
            ok=False, outcome=SIGNALS_MISMATCH, stored=stored, live=live,
            record=dict(stored), confirmed=confirmed, contradicted=contradicted,
        )

    if confirmed:
        return SignalCheck(
            ok=True, outcome=SIGNALS_MATCH, stored=stored, live=live,
            record=adopt_signal_record(stored, live),
            confirmed=confirmed, contradicted=contradicted,
        )

    # Nothing stable to go on: either none was ever recorded, or none of the
    # recorded ones answered this time.  Allow, and be careful what we write.
    if any(is_stable_signal(name) for name in stored):
        # We *do* hold stable digests, they just did not answer.  Keep them --
        # see _refresh_volatile -- and only take the volatile names.
        record = _refresh_volatile(stored, live)
    else:
        # A record with no stable name in it (a grandfathered set, or a host that
        # only ever offered container-scoped signals) protects nothing, so there
        # is nothing to lose by adopting whatever this host now offers -- which
        # is how such an install finally gains a stable signal.  A host that
        # answers nothing at all teaches us nothing, so we keep what we have
        # rather than blanking the record for no reason.
        record = dict(live) if live else dict(stored)
    return SignalCheck(
        ok=True, outcome=SIGNALS_UNDECIDABLE, stored=stored, live=live,
        record=record, confirmed=confirmed, contradicted=contradicted,
    )


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
