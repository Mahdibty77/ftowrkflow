"""Tamper-evident on-disk license state.

The state file stores three things:

    * ``license``   -- the activated, RSA-signed license string
    * ``last_seen`` -- the most recent timestamp the app has observed
                       (monotonic high-water mark, used to detect a clock that
                        was rolled back)
    * ``machine_signals`` -- ``{signal name: sha256 hex}`` captured at activation
                       (see :mod:`licensing.machine`).  This lives here, rather
                       than beside the ``.machine_fp`` cache, precisely because
                       this file is sealed and carries the signed licence: an
                       attacker who edits it invalidates the seal, whereas the
                       cache file is bare text anyone may rewrite.  Older state
                       files simply do not have the key, and that is legal --
                       see the grandfathering note on :func:`read_signals`.

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

# Key under which the per-signal hardware digests live inside ``data``.
SIGNALS_KEY = "machine_signals"

# A sanity ceiling on how many digests we will read back.  There are five probes
# today; anything wildly larger is a malformed or padded file, and we would
# rather ignore it than let it stuff the comparison with names we never wrote.
_MAX_SIGNALS = 16


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


def read_signals(data: dict | None) -> dict[str, str]:
    """Return the recorded per-signal digests, or ``{}`` when there are none.

    ``{}`` is deliberately indistinguishable from "this state file predates the
    signal binding".  Every install activated before that change has no such key,
    and refusing them would lock out paying customers over a format detail -- so
    the caller treats an empty result as *grandfathered* and records the real set
    on the next successful check.

    Anything that is not a plain ``{str: 64-hex}`` mapping is discarded rather
    than half-read: a partially understood set would silently change which
    signals the machine check believes it recorded.
    """
    if not isinstance(data, dict):
        return {}
    raw = data.get(SIGNALS_KEY)
    if not isinstance(raw, dict) or len(raw) > _MAX_SIGNALS:
        return {}
    cleaned: dict[str, str] = {}
    for name, digest in raw.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            return {}
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            return {}
        cleaned[name] = digest
    return cleaned


def with_signals(data: dict, digests: dict[str, str]) -> dict:
    """Return a copy of ``data`` carrying ``digests`` (dropping the key if empty)."""
    updated = dict(data)
    if digests:
        updated[SIGNALS_KEY] = dict(digests)
    else:
        updated.pop(SIGNALS_KEY, None)
    return updated


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
