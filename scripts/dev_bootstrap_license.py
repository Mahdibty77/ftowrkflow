#!/usr/bin/env python
"""Development-only helper: mint and activate a local license.

The platform ships a *real* RSA public key in ``licensing/public_key.pem`` and
gates every request behind a valid, machine-bound, signed license (see
``licensing/``). Production licenses are issued by the vendor with the matching
private key, which is intentionally NOT in this repository.

For local development / Cloud Agent environments there is no vendor private key,
so this script provisions a throwaway dev keypair and activates a long-lived
license against it, entirely through the documented environment overrides:

    LICENSE_PUBLIC_KEY_FILE  -> a dev public key generated here
    LICENSE_STATE_DIR        -> where the sealed license state + machine
                                fingerprint are stored

Nothing here touches application code and it cannot affect a production install:
the dev keypair only verifies against the dev public key that the developer
explicitly points ``LICENSE_PUBLIC_KEY_FILE`` at. With the stock
``licensing/public_key.pem`` (the real one) this dev license does not verify.

The generated private key + license state live under ``.dev-license/`` which is
git-ignored. Safe to re-run: it reuses an existing keypair and only (re)writes
the license when the current state is missing or invalid.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import sys
import uuid
from pathlib import Path

# --- Resolve the dev license locations (env-overridable) --------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DEV_DIR = Path(os.environ.get("DEV_LICENSE_DIR", REPO_ROOT / ".dev-license"))
STATE_DIR = Path(os.environ.get("LICENSE_STATE_DIR", DEV_DIR / "state"))
PUBLIC_KEY_FILE = Path(
    os.environ.get("LICENSE_PUBLIC_KEY_FILE", DEV_DIR / "public_key.pem")
)
PRIVATE_KEY_FILE = DEV_DIR / "private_key.pem"

DEV_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Make sure Django + the licensing service use these dev locations.
os.environ["LICENSE_PUBLIC_KEY_FILE"] = str(PUBLIC_KEY_FILE)
os.environ["LICENSE_STATE_DIR"] = str(STATE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ftworkflow.settings")
os.environ.setdefault("DJANGO_DEBUG", "1")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import django

django.setup()

from licensing import crypto_core, service  # noqa: E402  (after django.setup)


def _load_or_create_private_key():
    if PRIVATE_KEY_FILE.exists():
        return serialization.load_pem_private_key(
            PRIVATE_KEY_FILE.read_bytes(), password=None
        )
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    PRIVATE_KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    PUBLIC_KEY_FILE.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return key


def _sign_license(private_key, machine_id: str, days: int) -> str:
    today = dt.date.today()
    payload = {
        "machine_id": machine_id,
        "expiry": (today + dt.timedelta(days=days)).isoformat(),
        "issued": today.isoformat(),
        "grace_days": 7,
        "license_id": str(uuid.uuid4()),
        "note": "DEV-ONLY local license (not vendor issued)",
    }
    signature = private_key.sign(
        crypto_core.canonical_payload_bytes(payload),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=crypto_core._PSS_SALT_LEN,
        ),
        hashes.SHA256(),
    )
    envelope = {
        "v": 1,
        "alg": "RS-PSS-SHA256",
        "p": payload,
        "s": base64.b64encode(signature).decode("ascii"),
    }
    return crypto_core.pack_license(envelope)


def main() -> int:
    status = service.current_status(force=True)
    if status.ok:
        print(f"Dev license already valid (expires {status.expiry_iso}). Nothing to do.")
        return 0

    private_key = _load_or_create_private_key()
    machine_id = service.machine_id()
    days = int(os.environ.get("DEV_LICENSE_DAYS", "3650"))
    license_string = _sign_license(private_key, machine_id, days)

    ok, message = service.activate(license_string)
    print(message)
    if not ok:
        return 1

    final = service.current_status(force=True)
    print(
        "License status:",
        "OK" if final.ok else f"NOT OK ({final.reason_text})",
        "| expires", final.expiry_iso,
        "| machine", machine_id[:12] + "…",
    )
    return 0 if final.ok else 1


if __name__ == "__main__":
    sys.exit(main())
