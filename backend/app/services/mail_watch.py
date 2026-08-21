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
RENEW = 20 * 60
# Wieviele Postfächer gleichzeitig beobachtet werden. Jedes ist eine offene Verbindung.
MAX_WATCHDOG = int(os.getenv("MAIL_IDLE_MAX", "20"))

_running: dict[int, asyncio.Task] = {}
_overseer: asyncio.Task | None = None


def _idle_round(account: MailAccount, duration: int) -> list:
    """Eine Runde IDLE, blockierend — gehört deshalb in einen Thread."""
    # Bewusst eine EIGENE Verbindung, nicht die aus dem Vorrat: Diese hier steht zwanzig
    # Minuten in IDLE. Sie zurückzulegen hieße, dem nächsten Aufruf eine Leitung zu geben,
    # die auf eine Ankündigung wartet statt auf seine Frage.
    from ..services.mailbox import _join

    client = _join(account)
    try:
        client.select_folder("INBOX", readonly=True)
        client.idle()
        try:
            return client.idle_check(timeout=duration) or []
        finally:
            try:
                client.idle_done()
            except Exception:  # noqa: BLE001 — die Verbindung wird ohnehin geschlossen
                pass
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def _watchdog(account_id: int, user_id: int) -> None:
    from ..api.ws import persons

    pause = 5
    already_warmed = False
    while True:
        try:
            async with SessionLocal() as db:
                account = await db.get(MailAccount, account_id)
                if account is None or not account.enabled:
                    return
                # Losgelöst von der Sitzung weiterbenutzen: der Thread darf nicht an einer
                # Datenbankverbindung hängen, während er minutenlang wartet.
                db.expunge(account)

            if not persons.somebody_there(user_id):
                await asyncio.sleep(20)
                continue

            if not already_warmed:
                # Einmal beim Zusehen: Wer eingeloggt ist, hat den Wächter laufen, bevor er
                # das Postfach überhaupt öffnet. Dann ist auch der erste Blick warm.
                from .mailbox_cache import prewarm
                asyncio.create_task(prewarm(account))
                already_warmed = True

            events = await asyncio.to_thread(_idle_round, account, RENEW)
            pause = 5
            # EXISTS (neue Nachricht), EXPUNGE (weg), FETCH (Flag geändert) — welches davon,
            # ist der Oberfläche egal: sie holt sich den Stand ohnehin frisch.
            if events:
                # Erst vergessen, dann melden: Sonst fragt die Oberfläche im selben Moment
                # nach und bekommt den Stand von vor der neuen Mail.
                from .mailbox_cache import invalidate, prewarm
                await invalidate(account_id)
                # Und gleich neu holen: Die Meldung kommt beim Menschen an, während der
                # Stand schon unterwegs ist — sonst wartet er auf die Sekunde, die wir
                # gerade erst weggeworfen haben.
                asyncio.create_task(prewarm(account))
                await persons.send(user_id, {
                    "type": "mail",
                    "data": {"account_id": account_id, "folder": "INBOX"},
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Wächter für Konto %s: %s — neuer Versuch in %ss",
                     account_id, str(exc)[:120], pause)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 300)


async def _overseer_loop() -> None:
    """Startet und beendet Wächter, wenn Postfächer dazukommen oder verschwinden."""
    while True:
        try:
            async with SessionLocal() as db:
                accounts = (await db.execute(select(MailAccount).where(
                    MailAccount.enabled.is_(True),
                    MailAccount.imap_host != ""))).scalars().all()
                wanted = {k.id: k.owner_user_id for k in accounts[:MAX_WATCHDOG]}

            for kid, uid in wanted.items():
                task = _running.get(kid)
                if task is None or task.done():
                    _running[kid] = asyncio.create_task(_watchdog(kid, uid))
            for kid in list(_running):
                if kid not in wanted:
                    _running.pop(kid).cancel()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Aufseher der Postfach-Wächter gestolpert")
        await asyncio.sleep(60)


async def start() -> None:
    global _overseer
    if not AN or _overseer is not None:
        return
    _overseer = asyncio.create_task(_overseer_loop())
    log.info("Postfach-Wächter (IMAP IDLE) gestartet")


async def stop() -> None:
    global _overseer
    if _overseer is not None:
        _overseer.cancel()
        _overseer = None
    for task in _running.values():
        task.cancel()
    _running.clear()
