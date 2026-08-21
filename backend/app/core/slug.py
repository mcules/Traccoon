"""Aus einem Namen ein Schlüssel.

An drei Stellen gebraucht (Ablauf, Ablage, Umbenennen in der Oberfläche), und an jeder mit
derselben Falle: Ein deutscher Name hat Umlaute, und wer nur `[^a-z0-9]` ersetzt, macht aus
„Prüfer" ein „pr-fer". Deshalb einmal hier, mit Umschrift.
"""
from __future__ import annotations

import re

TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                           "Ä": "ae", "Ö": "oe", "Ü": "ue",
                           "á": "a", "à": "a", "â": "a", "é": "e", "è": "e", "ê": "e",
                           "í": "i", "ì": "i", "ó": "o", "ò": "o", "ô": "o",
                           "ú": "u", "ù": "u", "ç": "c", "ñ": "n"})


def slug(text: str, length: int = 100) -> str:
    """Kleinbuchstaben, Ziffern, Bindestriche — leer, wenn nichts davon übrig bleibt."""
    raw = (text or "").lower().translate(TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:length]
