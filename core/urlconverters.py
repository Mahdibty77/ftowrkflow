"""Custom Django path converters."""
from __future__ import annotations

from django.urls.converters import StringConverter

from .opaque_ids import decode_opaque_id, encode_opaque_id


class OpaqueIdConverter(StringConverter):
    """Path segment ↔ positive integer PK via Sqids opaque tokens.

    ``to_python`` returns an ``int`` so views keep ``pk`` / ``case_id`` as ints.
    ``to_url`` encodes ints so ``{% url %}`` / ``reverse()`` emit opaque paths.
    """

    # Letters + digits; pure short digit paths still prefer the legacy <int:>
    # redirect routes which are registered first.
    regex = r"[A-Za-z0-9]{8,}"

    def to_python(self, value: str) -> int:
        pk = decode_opaque_id(value)
        if pk is None:
            raise ValueError(f"invalid opaque id: {value!r}")
        return pk

    def to_url(self, value) -> str:
        return encode_opaque_id(int(value))
