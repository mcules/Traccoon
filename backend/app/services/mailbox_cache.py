"""Was das Postfach schon weiß, muss es nicht noch einmal fragen.

Der Aufbau der Seite kostete gemessen 1,9 Sekunden, und fast alles davon waren Fragen ans
Postfach, deren Antwort sich zwischen zwei Klicks nicht ändert: die Ordnerliste (33 Ordner)
und die Ungelesen-Zahlen (ein STATUS je Ordner, allein 900 ms).

Zwei Dinge halten den Cache ehrlich:

* Eine **kurze Haltbarkeit**, denn eine Mail kann auch am Telefon gelesen werden, und davon
  erfährt Traccoon erst beim nächsten Blick.
* Ein **Generationszähler** je Konto. Wer etwas ändert (lesen, verschieben, löschen, senden)
  oder vom Postfach eine Ankündigung bekommt (IDLE), zählt ihn hoch — und alle Einträge der
  alten Generation sind damit unerreichbar, ohne dass jemand Schlüssel suchen muss.

Fällt Redis aus, ist das kein Fehler: Dann wird eben jedes Mal gefragt, wie vorher.
"""
from __future__ import annotations

import json
import logging

from ..core.redis import get_redis

log = logging.getLogger("traccoon.mailbox")

PREFIX = "traccoon:mailbox"
# Ordner ändern sich selten, Listen dauernd. Beides überlebt einen Klick, keines eine Pause.
TTL_FOLDER = 120
TTL_LISTING = 45
TTL_UNGELESEN = 60


async def _generation(konto_id: int) -> int:
    try:
        value = await get_redis().get(f"{PREFIX}:{konto_id}:gen")
        return int(value) if value else 0
    except Exception:  # noqa: BLE001 — ohne Redis läuft alles wie vorher, nur langsamer
        return -1


async def entwerten(konto_id: int) -> None:
    """Alles zu diesem Konto vergessen. Ein INCR, kein Suchen nach Schlüsseln."""
    try:
        await get_redis().incr(f"{PREFIX}:{konto_id}:gen")
    except Exception:  # noqa: BLE001
        log.debug("Cache für Konto %s nicht entwertet", konto_id)


async def hole(konto_id: int, part: str):
    gen = await _generation(konto_id)
    if gen < 0:
        return None
    try:
        roh = await get_redis().get(f"{PREFIX}:{konto_id}:{gen}:{part}")
        return json.loads(roh) if roh else None
    except Exception:  # noqa: BLE001
        return None


async def lege(konto_id: int, part: str, value, ttl: int) -> None:
    gen = await _generation(konto_id)
    if gen < 0:
        return
    try:
        await get_redis().set(f"{PREFIX}:{konto_id}:{gen}:{part}",
                              json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:  # noqa: BLE001
        log.debug("Cache für Konto %s nicht geschrieben (%s)", konto_id, part)


async def gecacht(konto_id: int, part: str, ttl: int, fetch):
    """Aus dem Cache, sonst holen und hinlegen.

    `holen` ist die teure Frage ans Postfach; sie läuft nur, wenn der Cache nichts hat.
    """
    vorhanden = await hole(konto_id, part)
    if vorhanden is not None:
        return vorhanden
    frisch = await fetch()
    await lege(konto_id, part, frisch, ttl)
    return frisch


async def vorwaermen(konto) -> None:
    """Den Stand holen, bevor jemand danach fragt.

    Ohne das wäre der Cache genau dann kalt, wenn er gebraucht wird: Eine neue Mail entwertet
    ihn, und der nächste Blick ins Postfach zahlt wieder die volle Sekunde. Wer zusieht, hat
    ohnehin einen Wächter laufen — der weiß es zuerst und kann die Fragen gleich stellen.

    Fehler bleiben hier: Vorwärmen ist eine Bequemlichkeit, kein Auftrag. Was nicht klappt,
    wird beim nächsten Abruf eben normal geholt.
    """
    import asyncio

    from . import mailbox

    async def eins(part: str, ttl: int, fetch):
        try:
            await lege(konto.id, part, await fetch(), ttl)
        except Exception:  # noqa: BLE001
            log.debug("Vorwärmen (%s) für Konto %s ging nicht", part, konto.id)

    await asyncio.gather(
        eins("folders:1", TTL_FOLDER, lambda: mailbox.folder(konto, True)),
        eins("list:INBOX:0:50", TTL_LISTING, lambda: mailbox.listing(konto, "INBOX", "", 0, 50)),
        eins("unread", TTL_UNGELESEN, lambda: mailbox.ungelesen(konto)),
    )
