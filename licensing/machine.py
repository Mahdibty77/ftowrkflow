"""Hardware / deployment fingerprint used to bind a license to one machine.

The fingerprint is the SHA-256 of several independent signals (OS machine-id,
mainboard / disk / CPU identifiers and the primary network MAC).  Every probe is
wrapped in try/except so that a missing signal on a given OS never crashes the
application; the remaining signals are still combined.

Docker stability
----------------
Inside a container some signals (the container MAC, the container's generated
machine-id) change on every rebuild.  To stay stable across ``docker compose up
--build`` we compute the fingerprint once and cache it on a *persisted volume*
(``LICENSE_STATE_DIR``).  On later boots the cached value is reused, so the bound
license keeps working.  When the host ``/etc/machine-id`` is mounted into the
container it is folded in as well, tying the deployment to the host.

The function works the same off-Docker (e.g. the security-test harness), where
the signals are simply read directly.

Why the cache file is no longer an authority
--------------------------------------------
``.machine_fp`` is a plain text file holding 64 hex characters.  Anyone who can
write it could once hand us any fingerprint they liked and we would believe it,
which made moving a licence to another server a one-line edit.  We still read the
file -- deleting it would break the Docker rebuild story it exists for -- but it
is now only a *hint* that makes the sealed state file open.  Acceptance is
decided elsewhere (:func:`licensing.evaluator.check_machine_signals`) by
re-probing the hardware and comparing it against the per-signal digests recorded
inside the sealed, RSA-backed state file.  A fingerprint that arrives from the
cache file therefore buys nothing on its own.

That is why this module also exposes the signals *individually*
(:func:`signal_digests`): the combined fingerprint is all-or-nothing, so a single
signal drifting after a container rebuild changes it completely and tells us
nothing about how much of the machine is still the same.  Individual digests let
the caller ask the question that matters -- "does anything that identifies the
metal disagree?" -- which tolerates drift but not a move.

Naming carries the scope
------------------------
:func:`signal_digests` reports the OS machine-id under two different names
depending on where it came from: ``osid_host`` when it was read from a mounted
*host* file, and plain ``osid`` when it is the container's own, which Docker
regenerates on every rebuild.  Only the first one identifies a machine, and
:mod:`licensing.evaluator` classifies by exactly that name.  The *fingerprint*
material deliberately keeps the original ``osid`` key either way: renaming it
there would change every existing machine id and invalidate licences in the
field.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid

_CACHE: str | None = None

# Probe results memoised for the life of the process, keyed by whether the
# expensive (subprocess) probes were allowed.  Hardware cannot change under a
# running interpreter, and the Windows probes shell out to PowerShell -- without
# this the licence gate would spawn processes on every single request.
_SIGNALS_CACHE: dict[bool, tuple[dict[str, str], bool]] = {}

# Personalises the per-signal digests so they are specific to this product and
# cannot be lined up against a digest of the same serial taken elsewhere.  Like
# the seal salt in state.py this ships in the source and is not a secret.
_SIGNAL_DIGEST_SALT = "ftworkflow.machine.signal.v1"


# ---------------------------------------------------------------------------
# Low level helpers
# ---------------------------------------------------------------------------
def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False
        )
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001 - tool missing / blocked -> ignore signal
        return ""


# ---------------------------------------------------------------------------
# Individual signals (each returns "" when unavailable)
#
# ``allow_subprocess=False`` asks for the cheap tier: everything that is a plain
# file read.  A cheap probe must return either the same value the full probe
# would, or nothing at all -- never a *different* value, or the two tiers would
# look like a hardware change to the comparison in the evaluator.
# ---------------------------------------------------------------------------
def _sig_os_machine_id(allow_subprocess: bool = True) -> tuple[str, bool]:
    """Return ``(value, host_scoped)``.

    Host-scoped means the id was read from a file the *host* owns and mounted in
    for us.  The container's own /etc/machine-id is generated when the image
    layer is built, so it changes on `docker compose up --build` and cannot
    identify anything -- hence the flag, which decides the name the digest is
    published under and therefore whether it can ever refuse a customer.
    """
    for path, host_scoped in (
        (os.environ.get("LICENSE_HOST_MACHINE_ID_FILE", ""), True),
        ("/host/etc/machine-id", True),
        ("/etc/machine-id", False),
        ("/var/lib/dbus/machine-id", False),
    ):
        if path:
            value = _read_file(path)
            if value:
                return value, host_scoped
    # Windows: MachineGuid from the registry.  Not marked host-scoped: this build
    # never runs in a Windows container, so the guid buys us nothing the
    # mainboard/disk serials do not already give, and treating it as identity
    # could only ever add a way to refuse someone.
    if allow_subprocess and platform.system() == "Windows":
        out = _run([
            "reg", "query",
            r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography",
            "/v", "MachineGuid",
        ])
        for line in out.splitlines():
            if "MachineGuid" in line:
                return line.split()[-1].strip(), False
    return "", False


def _sig_mainboard(allow_subprocess: bool = True) -> str:
    system = platform.system()
    if system == "Linux":
        for path in (
            "/sys/class/dmi/id/board_serial",
            "/sys/class/dmi/id/product_uuid",
            "/sys/class/dmi/id/product_serial",
        ):
            value = _read_file(path)
            if value and value.lower() not in {"none", "to be filled by o.e.m."}:
                return value
    elif system == "Windows":
        if not allow_subprocess:
            return ""
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_BaseBoard).SerialNumber",
        ])
        if not out:
            out = _run(["wmic", "baseboard", "get", "serialnumber"])
        return _clean_windows(out)
    return ""


def _sig_disk(allow_subprocess: bool = True) -> str:
    system = platform.system()
    if system == "Linux":
        # Serial of the first block device, when exposed by the kernel.
        for base in ("/sys/block/sda/device/serial", "/sys/block/nvme0n1/device/serial"):
            value = _read_file(base)
            if value:
                return value
        if not allow_subprocess:
            return ""
        out = _run(["lsblk", "-dno", "SERIAL"])
        first = out.splitlines()[0].strip() if out else ""
        return first
    if system == "Windows":
        if not allow_subprocess:
            return ""
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1).SerialNumber",
        ])
        if not out:
            out = _run(["wmic", "diskdrive", "get", "serialnumber"])
        return _clean_windows(out)
    return ""


def _sig_cpu(allow_subprocess: bool = True) -> str:
    system = platform.system()
    if system == "Linux":
        info = _read_file("/proc/cpuinfo")
        for line in info.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[-1].strip()
        return platform.processor()
    if system == "Windows":
        if not allow_subprocess:
            # platform.processor() would answer here, but it is a *different*
            # string from the ProcessorId the full probe returns; reporting
            # nothing is the only safe cheap answer.
            return ""
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId",
        ])
        if not out:
            out = _run(["wmic", "cpu", "get", "ProcessorId"])
        cleaned = _clean_windows(out)
        return cleaned or platform.processor()
    return platform.processor()


def _sig_mac() -> str:
    # uuid.getnode() returns the primary interface MAC (or a random value with
    # the multicast bit set when it cannot be determined -- which we drop).
    node = uuid.getnode()
    # 0x01 in the first octet is the multicast/group bit, which is exactly what
    # getnode() sets on its random fallback -- that is the value being rejected
    # here. It is deliberately NOT the locally-administered bit (0x02): a
    # locally-administered MAC such as Docker's 02:42:* is still accepted and
    # folded into the fingerprint, which is why a container that comes up on a
    # different IP can hash differently once the .machine_fp cache is gone.
    if (node >> 40) & 0x01:
        return ""
    return f"{node:012x}"


def _clean_windows(raw: str) -> str:
    """Strip the header line that wmic/powershell tables print."""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    for line in lines:
        low = line.lower()
        if low in {"serialnumber", "processorid"}:
            continue
        return line
    return ""


# ---------------------------------------------------------------------------
# Combination + persistence
# ---------------------------------------------------------------------------
def _state_dir() -> str:
    return os.environ.get("LICENSE_STATE_DIR", os.getcwd())


def _fingerprint_cache_path() -> str:
    return os.environ.get(
        "LICENSE_FP_FILE", os.path.join(_state_dir(), ".machine_fp")
    )


def _probe_signals(allow_subprocess: bool = True) -> tuple[dict[str, str], bool]:
    """Run the probes once and return ``(values, osid_is_host_scoped)``.

    Memoised per process and per tier: see ``_SIGNALS_CACHE``.  Hardware does not
    change under a running interpreter, so re-probing would only spend
    PowerShell/wmic/lsblk process spawns on every HTTP request.  The probes are
    looked up as module globals at call time (not captured in a table at import
    time) so the test harness can substitute them.

    The keys here are the *fingerprint* keys and must never be renamed: they are
    the material behind every machine id already issued.
    """
    cached = _SIGNALS_CACHE.get(allow_subprocess)
    if cached is not None:
        return dict(cached[0]), cached[1]

    osid, osid_is_host = _sig_os_machine_id(allow_subprocess)
    signals = {
        "osid": osid,
        "board": _sig_mainboard(allow_subprocess),
        "disk": _sig_disk(allow_subprocess),
        "cpu": _sig_cpu(allow_subprocess),
        "mac": _sig_mac(),
    }
    present = {k: v for k, v in signals.items() if v}
    _SIGNALS_CACHE[allow_subprocess] = (present, bool(osid) and osid_is_host)
    return dict(present), bool(osid) and osid_is_host


def signal_digests(allow_subprocess: bool = True) -> dict[str, str]:
    """Return ``{signal name: sha256 hex}`` for every signal this host exposes.

    Each signal is hashed on its own rather than folded into one fingerprint, so
    a caller can ask "does anything that identifies this machine disagree?"
    instead of only "is it byte-for-byte identical?".  Absent signals are simply
    missing from the mapping -- never present with an empty digest, which would
    otherwise let two different locked-down hosts look alike.

    The machine-id is published as ``osid_host`` when it came from a mounted host
    file and as ``osid`` when it is the container's own, because that is the
    difference between an identifier and a value Docker regenerates.  Call with
    ``allow_subprocess=False`` for the cheap tier (file reads only); the values
    are identical, a cheap call just returns fewer of them.
    """
    values, osid_is_host = _probe_signals(allow_subprocess)
    digests: dict[str, str] = {}
    for name, value in values.items():
        published = "osid_host" if (name == "osid" and osid_is_host) else name
        digests[published] = hashlib.sha256(
            f"{_SIGNAL_DIGEST_SALT}|{published}={value}".encode("utf-8")
        ).hexdigest()
    return digests


def _compute_fingerprint() -> str:
    present, _ = _probe_signals()
    # Join the available signals in a stable order and hash them.
    material = "|".join(f"{k}={present[k]}" for k in sorted(present))
    if not material:
        # Extremely locked-down host: fall back to node name so we still get a
        # deterministic value rather than hashing an empty string.
        material = f"fallback={platform.node()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _looks_like_fingerprint(value: str) -> bool:
    """True for the exact shape we write: 64 lower-case hex characters."""
    if len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def compute_live_machine_id() -> str:
    """Fingerprint of the hardware actually in front of us, ignoring ``.machine_fp``.

    Used where believing the cache file would mislead a *paying* customer: on the
    activation screen after a genuine hardware move, the stale cached value is
    the wrong thing to read out to the vendor.
    """
    return _compute_fingerprint()


def get_machine_id(*, use_cache_file: bool = True) -> str:
    """Return the stable machine fingerprint (SHA-256 hex, 64 chars).

    The value is memoised for the process and, by default, persisted to
    ``LICENSE_FP_FILE`` so it survives container rebuilds.

    Note what this value is and is not: it is the identifier the licence was
    issued against, and it may legitimately come from the cache file.  It is
    *not* evidence that we are on the right machine -- callers must still compare
    the recorded signal digests against the live ones
    (:func:`licensing.evaluator.check_machine_signals`).
    """
    global _CACHE
    if _CACHE:
        return _CACHE

    if use_cache_file:
        cached = _read_file(_fingerprint_cache_path())
        # Shape check only.  We cannot tell a genuine cached fingerprint from a
        # planted one, which is exactly why nothing downstream trusts it.
        if cached and _looks_like_fingerprint(cached):
            _CACHE = cached
            return _CACHE

    value = _compute_fingerprint()

    if use_cache_file:
        _write_fingerprint_cache(value)

    _CACHE = value
    return _CACHE


def _write_fingerprint_cache(value: str) -> None:
    try:
        path = _fingerprint_cache_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value)
    except OSError:
        pass  # read-only fs -> still works, just recomputes next time


def adopt_machine_id(value: str) -> None:
    """Make ``value`` the fingerprint this process and this install will use.

    Called only after a licence signed for ``value`` has verified, i.e. the
    vendor has issued for the machine in front of us.  Refreshing the cache here
    is what lets a customer who genuinely moved to new hardware activate their
    new licence instead of being stuck behind a stale ``.machine_fp``.
    """
    global _CACHE
    if not _looks_like_fingerprint(value):
        return
    _CACHE = value
    _write_fingerprint_cache(value)


def reset_cache() -> None:
    """Clear the in-process caches (used by tests)."""
    global _CACHE
    _CACHE = None
    _SIGNALS_CACHE.clear()
