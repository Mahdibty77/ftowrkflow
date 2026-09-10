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

A SECOND, INDEPENDENT difference that must also never make two names compare
as different is whitespace: extra/irregular spacing (double spaces, stray
tabs) or a space missing entirely between two words that should be separate
("البرز" vs "آلبرز" spaced as one run-together word). After the letter
folding above, every whitespace character is stripped out ENTIRELY (not
collapsed to a single space - removed altogether), so word-boundary spacing
can never be the reason two names fail to match. This makes the output
comparison-only: it is no longer word-shaped (spaces are gone), so it must
never be rendered back to a user or re-inserted anywhere - only ever used as
the left- or right-hand side of an equality/substring check.
"""
from __future__ import annotations

import re

_MAP = str.maketrans({
    "آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا",
    "ك": "ک",
    "ي": "ی", "ى": "ی",
})

_WHITESPACE = re.compile(r"\s+")


def normalize_persian(text: str) -> str:
    """text folded to a canonical, whitespace-free form for COMPARISON only.

    Two steps, in order:

    1. Arabic letter-variants folded to their Persian form (see module
       docstring for the three pairs).
    2. Every whitespace character removed entirely - not collapsed, removed
       - so two names differing only by spacing (extra spaces, a missing
       space, tabs/newlines) still compare equal.

    Safe on None/empty/non-Persian text - anything that isn't a mapped
    codepoint or whitespace passes through unchanged, so this is safe to
    apply unconditionally to any free-text search field, not just ones known
    to be Persian. Because step 2 removes spaces, the result is no longer
    fit to display or re-insert anywhere - it exists only to be compared
    against another call's result (see ``marketing.services.search_clients``,
    ``marketing.services.get_or_create_client``, ``cases.views.client_lookup``,
    and the JS twin in ``static/js/ui.js``'s ``buildCombo`` filter, which must
    stay doing the exact same transformation in the exact same order).
    """
    return _WHITESPACE.sub("", (text or "").translate(_MAP))
