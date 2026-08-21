"""Server side texts in the language of the person who reads them.

The browser has its own catalog; this one covers what the server writes: notifications, the
setup checklist, messages that go out by mail or chat. Same idea, same override table, so a
typo is fixed in the admin area instead of in a deployment.

Two catalogs are bundled (German as the source, English as the shipped translation), and
anything an admin changed at runtime is layered on top. The lookup order is deliberate:
override, then the catalog of the wanted language, then German, then the key itself. A
missing translation shows German text, never an empty screen or a raw key.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.i18n import UiTranslation

log = logging.getLogger("traccoon.i18n")

_FOLDER = Path(__file__).resolve().parent.parent / "i18n"
QUELLSPRACHE = "de"

# The bundled catalogs, read once at import. They are part of the code and change only with
# a deployment, so there is nothing to invalidate here.
KATALOG: dict[str, dict[str, str]] = {}
for _file in sorted(_FOLDER.glob("*.json")):
    try:
        KATALOG[_file.stem] = json.loads(_file.read_text(encoding="utf-8"))
    except Exception:                                        # pragma: no cover - Startfehler
        log.exception("catalog %s is not readable", _file)

# Overrides from the database. They are read for every language at once and kept for a short
# while: a text lookup happens per notification, a query per notification would be absurd.
_UEBERSCHREIBUNGEN: dict[str, dict[str, str]] = {}
_GELADEN: dt.datetime | None = None
FRESHNESS_SECONDS = 30.0


def verwerfen() -> None:
    """Drop the cache, called after an admin edited a text."""
    global _GELADEN
    _GELADEN = None


async def _ueberschreibungen(db: AsyncSession) -> dict[str, dict[str, str]]:
    global _GELADEN, _UEBERSCHREIBUNGEN
    now = dt.datetime.now(tz=dt.timezone.utc)
    if _GELADEN is not None and (now - _GELADEN).total_seconds() < FRESHNESS_SECONDS:
        return _UEBERSCHREIBUNGEN
    rows = (await db.execute(select(UiTranslation))).scalars().all()
    new: dict[str, dict[str, str]] = {}
    for r in rows:
        if r.text:
            new.setdefault(r.locale, {})[r.key] = r.text
    _UEBERSCHREIBUNGEN = new
    _GELADEN = now
    return new


def _insert(text: str, values: dict[str, object]) -> str:
    """Replace {name} placeholders. Unknown ones stay as they are, so a text with a stray
    brace looks odd but nothing blows up in the middle of a notification."""
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


async def tr(db: AsyncSession, key: str, locale: str | None = None, **values: object) -> str:
    """One text, in `locale`, with placeholders filled in."""
    lc = (locale or QUELLSPRACHE).lower()
    ueber = await _ueberschreibungen(db)
    text = (ueber.get(lc, {}).get(key)
            or KATALOG.get(lc, {}).get(key)
            or ueber.get(QUELLSPRACHE, {}).get(key)
            or KATALOG.get(QUELLSPRACHE, {}).get(key)
            or key)
    return _insert(text, values) if values else text


def source() -> dict[str, str]:
    """The German catalog, the list of texts the admin area offers for translation."""
    return dict(KATALOG.get(QUELLSPRACHE, {}))
