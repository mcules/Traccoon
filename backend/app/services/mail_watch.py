"""Postfächer, die sich von selbst melden — IMAP IDLE (RFC 2177).

Vorher fragte die Oberfläche jede Minute nach. Das ist für einen Zähler in Ordnung und für
Post ärgerlich: Eine Nachricht, die vor 55 Sekunden ankam, ist noch nicht da, und wer
daneben sitzt, drückt nach. IDLE dreht die Richtung um — die Verbindung bleibt offen, und
der Server sagt Bescheid.

Wie es hier läuft:

* Ein Wächter je aktivem Postfach, in einem eigenen Thread, weil `imapclient` blockiert.
* Er meldet nur, DASS sich etwas getan hat. Was genau, holt sich der Browser danach über die
  normalen Wege — sonst hätten wir zwei Quellen für denselben Stand.
* Niemand da, kein Wächter: Solange kein Fenster dieser Person offen ist, hält niemand eine
  Verbindung offen. Ein Postfach ist kein Abonnement, das im Hintergrund Strom zieht.
* Eine Verbindung, die stirbt, wird neu aufgebaut — mit wachsender Pause, damit ein Server,
  der gerade nicht mag, nicht im Sekundentakt gefragt wird.
"""
from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import select

from ..db import SessionLocal
from ..models.mail import MailAccount

log = logging.getLogger("mail_watch")

AN = os.getenv("MAIL_IDLE", "1") not in ("0", "false", "no")
# Nach spätestens 29 Minuten muss IDLE erneuert werden (RFC 2177 nennt 29 als sichere Grenze;
# viele Server werfen früher raus).
ERNEUERN = 20 * 60
# Wieviele Postfächer gleichzeitig beobachtet werden. Jedes ist eine offene Verbindung.
MAX_WAECHTER = int(os.getenv("MAIL_IDLE_MAX", "20"))

_laufend: dict[int, asyncio.Task] = {}
_aufseher: asyncio.Task | None = None


def _idle_runde(account: MailAccount, dauer: int) -> list:
    """Eine Runde IDLE, blockierend — gehört deshalb in einen Thread."""
    from ..services.mailbox import _imap

    with _imap(account) as client:
        client.select_folder("INBOX", readonly=True)
        client.idle()
        try:
            return client.idle_check(timeout=dauer) or []
        finally:
            try:
                client.idle_done()
            except Exception:  # noqa: BLE001 — die Verbindung wird ohnehin geschlossen
                pass


async def _waechter(konto_id: int, user_id: int) -> None:
    from ..api.ws import personen

    pause = 5
    while True:
        try:
            async with SessionLocal() as db:
                konto = await db.get(MailAccount, konto_id)
                if konto is None or not konto.enabled:
                    return
                # Losgelöst von der Sitzung weiterbenutzen: der Thread darf nicht an einer
                # Datenbankverbindung hängen, während er minutenlang wartet.
                db.expunge(konto)

            if not personen.jemand_da(user_id):
                await asyncio.sleep(20)
                continue

            ereignisse = await asyncio.to_thread(_idle_runde, konto, ERNEUERN)
            pause = 5
            # EXISTS (neue Nachricht), EXPUNGE (weg), FETCH (Flag geändert) — welches davon,
            # ist der Oberfläche egal: sie holt sich den Stand ohnehin frisch.
            if ereignisse:
                await personen.senden(user_id, {
                    "type": "mail",
                    "data": {"account_id": konto_id, "folder": "INBOX"},
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Wächter für Konto %s: %s — neuer Versuch in %ss",
                     konto_id, str(exc)[:120], pause)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 300)


async def _aufseher_schleife() -> None:
    """Startet und beendet Wächter, wenn Postfächer dazukommen oder verschwinden."""
    while True:
        try:
            async with SessionLocal() as db:
                konten = (await db.execute(select(MailAccount).where(
                    MailAccount.enabled.is_(True),
                    MailAccount.imap_host != ""))).scalars().all()
                gewollt = {k.id: k.owner_user_id for k in konten[:MAX_WAECHTER]}

            for kid, uid in gewollt.items():
                task = _laufend.get(kid)
                if task is None or task.done():
                    _laufend[kid] = asyncio.create_task(_waechter(kid, uid))
            for kid in list(_laufend):
                if kid not in gewollt:
                    _laufend.pop(kid).cancel()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Aufseher der Postfach-Wächter gestolpert")
        await asyncio.sleep(60)


async def starten() -> None:
    global _aufseher
    if not AN or _aufseher is not None:
        return
    _aufseher = asyncio.create_task(_aufseher_schleife())
    log.info("Postfach-Wächter (IMAP IDLE) gestartet")


async def stoppen() -> None:
    global _aufseher
    if _aufseher is not None:
        _aufseher.cancel()
        _aufseher = None
    for task in _laufend.values():
        task.cancel()
    _laufend.clear()
