"""A key out of a name.

Needed in three places (a flow, a store, renaming in the UI), and in each with the same trap: a
German name has umlauts, and whoever only replaces `[^a-z0-9]` turns "Prüfer" into "pr-fer".
Hence once here, with transliteration.
"""
from __future__ import annotations

import re

TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                           "Ä": "ae", "Ö": "oe", "Ü": "ue",
                           "á": "a", "à": "a", "â": "a", "é": "e", "è": "e", "ê": "e",
                           "í": "i", "ì": "i", "ó": "o", "ò": "o", "ô": "o",
                           "ú": "u", "ù": "u", "ç": "c", "ñ": "n"})


def slug(text: str, length: int = 100) -> str:
    """Lower case, digits, hyphens — empty when none of that is left."""
    raw = (text or "").lower().translate(TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:length]
