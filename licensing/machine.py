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
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid

_CACHE: str | None = None


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
# ---------------------------------------------------------------------------
def _sig_os_machine_id() -> str:
    # Linux: stable per-install id.  Honour a mounted host id when provided.
    for path in (
        os.environ.get("LICENSE_HOST_MACHINE_ID_FILE", ""),
        "/host/etc/machine-id",
        "/etc/machine-id",
        "/var/lib/dbus/machine-id",
    ):
        if path:
            value = _read_file(path)
            if value:
                return value
    # Windows: MachineGuid from the registry.
    if platform.system() == "Windows":
        out = _run([
            "reg", "query",
            r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography",
            "/v", "MachineGuid",
        ])
        for line in out.splitlines():
            if "MachineGuid" in line:
                return line.split()[-1].strip()
    return ""


def _sig_mainboard() -> str:
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
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_BaseBoard).SerialNumber",
        ])
        if not out:
            out = _run(["wmic", "baseboard", "get", "serialnumber"])
        return _clean_windows(out)
    return ""


def _sig_disk() -> str:
    system = platform.system()
    if system == "Linux":
        # Serial of the first block device, when exposed by the kernel.
        for base in ("/sys/block/sda/device/serial", "/sys/block/nvme0n1/device/serial"):
            value = _read_file(base)
            if value:
                return value
        out = _run(["lsblk", "-dno", "SERIAL"])
        first = out.splitlines()[0].strip() if out else ""
        return first
    if system == "Windows":
        out = _run([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1).SerialNumber",
        ])
        if not out:
            out = _run(["wmic", "diskdrive", "get", "serialnumber"])
        return _clean_windows(out)
    return ""


def _sig_cpu() -> str:
    system = platform.system()
    if system == "Linux":
        info = _read_file("/proc/cpuinfo")
        for line in info.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[-1].strip()
        return platform.processor()
    if system == "Windows":
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
    if (node >> 40) & 0x01:  # locally-administered / random -> unreliable
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


def _compute_fingerprint() -> str:
    signals = {
        "osid": _sig_os_machine_id(),
        "board": _sig_mainboard(),
        "disk": _sig_disk(),
        "cpu": _sig_cpu(),
        "mac": _sig_mac(),
    }
    present = {k: v for k, v in signals.items() if v}
    # Join the available signals in a stable order and hash them.
    material = "|".join(f"{k}={present[k]}" for k in sorted(present))
    if not material:
        # Extremely locked-down host: fall back to node name so we still get a
        # deterministic value rather than hashing an empty string.
        material = f"fallback={platform.node()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def get_machine_id(*, use_cache_file: bool = True) -> str:
    """Return the stable machine fingerprint (SHA-256 hex, 64 chars).

    The value is memoised for the process and, by default, persisted to
    ``LICENSE_FP_FILE`` so it survives container rebuilds.
    """
    global _CACHE
    if _CACHE:
        return _CACHE

    if use_cache_file:
        cached = _read_file(_fingerprint_cache_path())
        if cached and len(cached) == 64:
            _CACHE = cached
            return _CACHE

    value = _compute_fingerprint()

    if use_cache_file:
        try:
            path = _fingerprint_cache_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(value)
        except OSError:
            pass  # read-only fs -> still works, just recomputes next time

    _CACHE = value
    return _CACHE


def reset_cache() -> None:
    """Clear the in-process cache (used by tests)."""
    global _CACHE
    _CACHE = None
