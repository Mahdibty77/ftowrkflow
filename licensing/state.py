"""Tamper-evident on-disk license state.

The state file stores two things:

    * ``license``   -- the activated, RSA-signed license string
    * ``last_seen`` -- the most recent timestamp the app has observed
                       (monotonic high-water mark, used to detect a clock that
                        was rolled back)

The file is sealed with an HMAC keyed on the machine fingerprint, so editing it
in a text editor (e.g. to push the date forward) invalidates it and the software
locks.  This is *tamper evidence*; the real anti-forgery guarantee comes from the
RSA signature on the ``license`` field, which is re-verified on every check.

All functions take explicit paths/ids and never import Django, so the test
harness can exercise them directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

# Baked-in salt mixed into the HMAC key.  Not a secret in the cryptographic
# sense (it ships in the code); it just personalises the key so the sealed file
# is specific to this product + machine.
_HMAC_SALT = b"ftworkflow.runtime.seal.v1"


def _hmac_key(machine_id: str) -> bytes:
    return hashlib.sha256(_HMAC_SALT + machine_id.encode("utf-8")).digest()


def _canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_seal(data: dict, machine_id: str) -> str:
    return hmac.new(_hmac_key(machine_id), _canonical(data), hashlib.sha256).hexdigest()


def read_state(path: str, machine_id: str) -> dict | None:
    """Return the sealed state ``data`` dict, or ``None`` if absent/tampered."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            wrapper = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(wrapper, dict):
        return None
    data = wrapper.get("data")
    seal = wrapper.get("seal")
    if not isinstance(data, dict) or not isinstance(seal, str):
        return None
    expected = compute_seal(data, machine_id)
    if not hmac.compare_digest(expected, seal):
        return None  # tampered or written for a different machine
    return data


def write_state(path: str, data: dict, machine_id: str) -> bool:
    """Write ``data`` sealed for ``machine_id``.  Returns success."""
    wrapper = {"data": data, "seal": compute_seal(data, machine_id)}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(wrapper, handle, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError:
        return False
