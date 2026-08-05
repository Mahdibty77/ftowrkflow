"""Opaque integer IDs for public URLs (anti-enumeration).

Encodes positive integers into short, non-sequential tokens using Sqids with an
alphabet shuffled from ``SECRET_KEY``. Views keep using the decoded ``int`` PK;
templates / ``reverse()`` encode automatically via the ``oid`` path converter.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# URL-safe alphabet (no lookalikes like 0/O/I/l). Sqids requires unique chars.
_BASE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ123456789"


def _shuffle_alphabet(secret: str) -> str:
    """Deterministic shuffle of the alphabet from the project secret."""
    seed = hashlib.sha256(f"ftworkflow-oid|{secret}".encode("utf-8")).digest()
    chars = list(_BASE_ALPHABET)
    # Fisher–Yates driven by successive hash bytes.
    for i in range(len(chars) - 1, 0, -1):
        # Mix position into the stream so every index gets independent entropy.
        block = hashlib.sha256(seed + i.to_bytes(2, "big")).digest()
        j = int.from_bytes(block[:4], "big") % (i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


@lru_cache(maxsize=1)
def _sqids():
    try:
        from sqids import Sqids
    except ImportError as exc:  # pragma: no cover
        raise ImproperlyConfigured(
            "The 'sqids' package is required for opaque URL IDs. "
            "Install it with: pip install sqids"
        ) from exc
    secret = getattr(settings, "SECRET_KEY", "") or ""
    if not secret:
        raise ImproperlyConfigured("SECRET_KEY is required to encode opaque IDs.")
    return Sqids(alphabet=_shuffle_alphabet(secret), min_length=8)


def encode_opaque_id(pk: int) -> str:
    """Encode a positive integer primary key to an opaque URL token."""
    n = int(pk)
    if n < 1:
        raise ValueError("opaque id requires a positive integer primary key")
    return _sqids().encode([n])


def decode_opaque_id(token: str) -> int | None:
    """Decode an opaque token to a PK, or ``None`` if invalid."""
    raw = (token or "").strip()
    if not raw or len(raw) < 8:
        return None
    try:
        numbers = _sqids().decode(raw)
    except Exception:
        return None
    if len(numbers) != 1 or numbers[0] < 1:
        return None
    # Round-trip guard: reject tokens that are not the canonical encoding
    # (blocks some crafted inputs that Sqids would otherwise accept).
    try:
        if encode_opaque_id(numbers[0]) != raw:
            return None
    except ValueError:
        return None
    return int(numbers[0])
