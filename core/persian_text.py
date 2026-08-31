"""Persian/Arabic letter-variant normalization for search.

Persian and Arabic share a writing system but Unicode gives several letters
two different codepoints depending on which language typed them - a person
typing on a Persian keyboard and one typing on an Arabic keyboard can produce
visually near-identical or identical-looking text that does not byte-match.
The three pairs below are the ones that actually come up in Iranian company
names and search boxes:

    آ أ إ ٱ  (Arabic alef variants: with madda, with
                                 hamza above/below, wasla)      -> ا
    ك  (Arabic kaf)                                        -> ک (Persian keheh)
    ي ى  (Arabic yeh, alef maksura)                   -> ی (Persian yeh)

Normalizing BOTH the stored text and the typed query to this one canonical
form before comparing is what makes two names that only differ by which
keyboard typed a shared letter match correctly regardless of which variant
either side happens to use.
"""
from __future__ import annotations

_MAP = str.maketrans({
    "آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا",
    "ك": "ک",
    "ي": "ی", "ى": "ی",
})


def normalize_persian(text: str) -> str:
    """text with Arabic letter-variants folded to their Persian form.

    Safe on None/empty/non-Persian text - anything that isn't one of the six
    mapped codepoints passes through unchanged, so this is safe to apply
    unconditionally to any free-text search field, not just ones known to be
    Persian.
    """
    return (text or "").translate(_MAP)
