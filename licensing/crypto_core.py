"""Cryptographic core for offline license verification.

This module is intentionally small and dependency-light.  It contains ONLY the
public-key side of the scheme:

    * canonical JSON encoding of the signed payload
    * packing / unpacking the compact, copy-paste friendly license string
    * RSA-PSS + SHA-256 signature *verification* with the bundled public key

It never imports Django and never contains a private key, so it can also be
imported by the standalone security-test script.

License string format
---------------------
    "FTL1." + urlsafe_base64( zlib.compress( json(envelope) ) )

where ``envelope`` is::

    {
        "v":   1,                       # format version
        "alg": "RS-PSS-SHA256",         # signature algorithm
        "p":   { ...license payload...},# the signed data (see below)
        "s":   "<base64 signature>"     # signature over canonical(p)
    }

and the signed ``payload`` looks like::

    {
        "machine_id": "<sha256 hex of the target machine fingerprint>",
        "expiry":     "YYYY-MM-DD",     # absolute expiry date (UTC date)
        "issued":     "YYYY-MM-DD",     # issue date (informational)
        "grace_days": 3,                # extra days after expiry (optional)
        "license_id": "<uuid4>",        # unique id (informational)
        "note":       ""                # free text (optional)
    }

Security note
-------------
The expiry that the application enforces is ALWAYS re-derived by verifying this
signed string with the public key.  Without the matching private key an attacker
cannot produce a string whose signature verifies, so they cannot forge or extend
a license -- *even if they can read this code and know exactly where it lives.*
"""

from __future__ import annotations

import base64
import json
import os
import zlib

LICENSE_PREFIX = "FTL1."

# Numeric salt length (== SHA-256 digest size).  Using a fixed value keeps
# sign/verify unambiguous across cryptography library versions.
_PSS_SALT_LEN = 32

# Marker used inside the shipped placeholder public key file.
_PLACEHOLDER_MARKER = "REPLACE-WITH-REAL-PUBLIC-KEY"


# ---------------------------------------------------------------------------
# Canonical encoding -- MUST be identical on the signing (tool) side.
# ---------------------------------------------------------------------------
def canonical_payload_bytes(payload: dict) -> bytes:
    """Return the exact bytes that are signed/verified for a payload."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# License string <-> envelope
# ---------------------------------------------------------------------------
def pack_license(envelope: dict) -> str:
    """Serialize an envelope dict into the compact license string."""
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    blob = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    return LICENSE_PREFIX + blob


def unpack_license(license_string: str) -> dict:
    """Reverse :func:`pack_license`.  Raises ValueError on malformed input."""
    if not license_string:
        raise ValueError("empty license string")
    text = "".join(license_string.split())  # drop all whitespace / newlines
    if text.startswith(LICENSE_PREFIX):
        text = text[len(LICENSE_PREFIX):]
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(text.encode("ascii")))
        envelope = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any decoding error == invalid
        raise ValueError(f"malformed license string: {exc}") from exc
    if not isinstance(envelope, dict) or "p" not in envelope or "s" not in envelope:
        raise ValueError("license envelope missing fields")
    return envelope


# ---------------------------------------------------------------------------
# Public key loading
# ---------------------------------------------------------------------------
def _default_public_key_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_key.pem")


def public_key_path() -> str:
    """Path of the public key file (overridable for tests via env var)."""
    return os.environ.get("LICENSE_PUBLIC_KEY_FILE", _default_public_key_path())


def load_public_key():
    """Load the RSA public key, or return ``None`` if not configured/invalid.

    A ``None`` result means the software is *locked* (no trusted key), but the
    caller must not crash -- the activation page still works and shows the
    machine id.
    """
    path = public_key_path()
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if _PLACEHOLDER_MARKER.encode("ascii") in data:
        return None  # placeholder key is shipped -> treat as "not configured"
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        return load_pem_public_key(data)
    except Exception:  # noqa: BLE001 - corrupt/invalid key -> locked, not crash
        return None


def public_key_is_configured() -> bool:
    return load_public_key() is not None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify_license_string(license_string: str, public_key=None) -> dict | None:
    """Verify a license string's signature.

    Returns the signed *payload* dict when the signature is valid, otherwise
    ``None``.  This checks ONLY cryptographic authenticity -- callers still must
    check ``machine_id`` and ``expiry`` against the running machine / clock.
    """
    if public_key is None:
        public_key = load_public_key()
    if public_key is None:
        return None
    try:
        envelope = unpack_license(license_string)
    except ValueError:
        return None

    payload = envelope.get("p")
    sig_b64 = envelope.get("s")
    if not isinstance(payload, dict) or not isinstance(sig_b64, str):
        return None

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        signature = base64.b64decode(sig_b64)
        public_key.verify(
            signature,
            canonical_payload_bytes(payload),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=_PSS_SALT_LEN),
            hashes.SHA256(),
        )
    except Exception:  # noqa: BLE001 - InvalidSignature or any error -> reject
        return None
    return payload
