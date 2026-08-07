"""Redis-Schnittstellenvertrag Backend ↔ Runner (Queue, Result, Events, Flags)."""
from __future__ import annotations

import asyncio
import json
import logging

from redis.asyncio import Redis

from ..config import settings

log = logging.getLogger(__name__)

PREFIX = "traccoon:"
QUEUE = PREFIX + "task_queue"
# Reliable-Queue: Jobs liegen waehrend der Verarbeitung hier (blmove QUEUE→PROCESSING),
# damit ein abgestuerzter Worker sie beim naechsten Start aus PROCESSING zurueck in QUEUE
# holen kann, statt sie zu verlieren (siehe worker/__main__.py: pull_loop-Recovery + ACK).
PROCESSING = QUEUE + ":processing"
# Was der Worker gerade in der Mangel hat (Hash issue_key → Job-Infos). Der Worker leert den
# Hash beim Start; als alleiniges Lebenszeichen taugt er deshalb nicht (ein hart gekillter
# Worker laesst seine Eintraege stehen) — dafuer gibt es den Puls mit Verfallszeit.
ACTIVE = PREFIX + "active_processes"
# Puls je Auftrag: der Worker frischt `alive:<task_id>` waehrend der Verarbeitung auf. Faellt
# er weg, ist der Lauf nachweislich tot — genau das unterscheidet „arbeitet noch lange" von
# „ist verschwunden". Verfallszeit klar ueber dem Auffrisch-Takt, damit ein GC-Hickser im
# Worker keinen Fehlalarm ausloest.
PULS_TAKT = 15
PULS_TTL = 90
# Ergebnisse bleiben einen Tag liegen. Frueher eine Stunde — zu kurz: ein Ergebnis, das erst
# nach einem Backend-Ausfall abgeholt wird, war damit weg und die Arbeit verloren.
ERGEBNIS_TTL = 86400
# So lange darf ein Lauf ohne JEDES Lebenszeichen bleiben, bevor er als verschwunden gilt.
GNADENFRIST = 300
# Takt der Lebenszeichen-Prüfung (der Ergebnis-Poll läuft schneller, kostet aber nur eine
# Abfrage). Als Modul-Konstante, damit Tests ihn auf 0 ziehen können.
PRUEF_TAKT = 5.0

_redis: Redis | None = None


def get_redis() -> Redis:
    """Der gemeinsame Redis-Client — mit denselben Sicherungen wie im Worker.

    Ohne `socket_keepalive`/`health_check_interval`/`socket_timeout` wartet der Client auf
    einer halb toten Verbindung endlos auf Antwort: kein Fehler, kein Timeout, kein Log.
    Der Worker weiß das seit jeher (`_REDIS_KW`), das Backend nicht — und dort hängen die
    Wächter, die auf das Ergebnis eines Agentenlaufs warten.

    Am 2026-08-07 kostete das eine Stunde Stillstand: das Ergebnis für ABC-31 lag um 19:54
    fertig in Redis, der Wächter hing in einem `get`, das nie zurückkam, und niemand holte
    es ab. Von außen sah es aus, als arbeite der Agent noch — er war längst fertig.
    """
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_keepalive=True, health_check_interval=30,
            # Harte Obergrenze pro Kommando: lieber ein Fehler, den der Aufrufer sieht, als
            # ein Warten, das niemand bemerkt. Die Wächter pollen im Sekundentakt — 30 s
            # sind großzügig für ein `get`.
            socket_timeout=30, socket_connect_timeout=10, retry_on_timeout=True)
    return _redis


async def enqueue_task(payload: dict) -> None:
    await get_redis().lpush(QUEUE, json.dumps(payload))


def puls_key(task_id: str) -> str:
    """Key des Lebenszeichens. Der Worker schreibt ihn (mit seiner eigenen Verbindung),
    das Backend liest ihn — der Name gehört deshalb hierher, nicht in eines der beiden."""
    return f"{PREFIX}alive:{task_id}"


async def lauf_lebt(task_id: str) -> bool:
    """Gibt es diesen Auftrag noch — egal wie lange er schon läuft?

    Drei Quellen, jede für sich ausreichend:
    * Puls des Workers (er verarbeitet den Auftrag gerade),
    * Warteschlange (noch nicht drangekommen),
    * Verarbeitungsliste (Worker abgestürzt, die Recovery legt ihn beim nächsten Start
      zurück in die Warteschlange — der Auftrag ist also nicht verloren, nur verzögert).

    Der Hash der aktiven Prozesse zählt zusätzlich, hilft aber nur bei einem Worker, der
    noch läuft; ein gekillter Worker hinterlässt dort Karteileichen (deshalb der Puls).
    """
    r = get_redis()
    if await r.get(puls_key(task_id)) is not None:
        return True
    for liste in (QUEUE, PROCESSING):
        for raw in await r.lrange(liste, 0, -1):
            if task_id in raw:
                return True
    for raw in (await r.hvals(ACTIVE)) or []:
        if task_id in raw:
            return True
    return False


async def wait_result(task_id: str, timeout: float | None = None, poll: float = 0.4,
                      gnadenfrist: float = GNADENFRIST) -> dict | None:
    """Wartet auf result:{task_id} — solange der Lauf lebt, nicht nach der Uhr.

    Ein Agentenlauf darf Stunden dauern (Umsetzung + Review-Runden hängen an EINEM Auftrag).
    Eine feste Wanduhr-Grenze hat genau das kaputtgemacht: der Wächter gab nach 30 Minuten
    auf, das Ticket stand auf „fehlgeschlagen", während der Agent weiterarbeitete und seine
    Arbeit sauber committete. Deshalb wird hier auf ein LEBENSZEICHEN geprüft statt auf die
    verstrichene Zeit — aufgegeben wird nur, wenn der Auftrag `gnadenfrist` Sekunden lang
    nirgends mehr auftaucht (Worker weg, Auftrag nicht in der Queue).

    `timeout` ist ein optionaler harter Deckel (None/0 = keiner) für Knoten, die bewusst
    nicht ewig warten sollen. None bei beidem: verschwundener Lauf oder Deckel erreicht —
    welcher Fall vorlag, unterscheidet der Aufrufer über die verstrichene Zeit.
    """
    r = get_redis()
    key = f"{PREFIX}result:{task_id}"
    uhr = asyncio.get_running_loop().time
    start = uhr()
    tot_seit: float | None = None
    naechste_pruefung = 0.0
    letzte_meldung = start
    while True:
        try:
            raw = await r.get(key)
            if raw is not None:
                await r.delete(key)
                return json.loads(raw)
        except Exception:  # noqa: BLE001 — eine Störung darf den Wächter nicht töten
            # Ein Aussetzer (Timeout, Verbindungsabriss) ist kein Grund, das Warten
            # aufzugeben: das Ergebnis kommt später trotzdem, und ein gestorbener Wächter
            # lässt das Ticket für immer stehen. Beim nächsten Umlauf neu versuchen.
            log.warning("Ergebnis-Abfrage für %s gescheitert — erneuter Versuch", task_id,
                        exc_info=True)
        jetzt = uhr()
        if timeout and jetzt - start >= timeout:
            return None
        # Lebenszeichen nur alle paar Sekunden prüfen — der Ergebnis-Poll läuft schnell,
        # die Prüfung kostet mehrere Redis-Abfragen.
        if jetzt >= naechste_pruefung:
            naechste_pruefung = jetzt + PRUEF_TAKT
            if await lauf_lebt(task_id):
                tot_seit = None
            elif tot_seit is None:
                tot_seit = jetzt
            elif jetzt - tot_seit >= gnadenfrist:
                log.warning("Lauf %s seit %.0fs ohne Lebenszeichen — gilt als verschwunden",
                            task_id, jetzt - tot_seit)
                return None
        if jetzt - letzte_meldung >= 1800:
            letzte_meldung = jetzt
            log.info("warte weiter auf %s (%.0f min, Lauf lebt)", task_id, (jetzt - start) / 60)
        await asyncio.sleep(poll)


async def peek_result(task_id: str) -> bool:
    """True, wenn ein Ergebnis für task_id in Redis liegt (ohne es zu konsumieren).
    Für den Reattach nach Backend-Neustart: erkennt Läufe, die schon fertig sind."""
    return await get_redis().get(f"{PREFIX}result:{task_id}") is not None


async def publish_kill(issue_key: str) -> None:
    """Laufenden Agenten-Lauf abbrechen (Worker hört auf traccoon:kill)."""
    await get_redis().publish(PREFIX + "kill", issue_key)


async def publish_event(project_id: int, event: dict) -> None:
    await get_redis().publish(f"{PREFIX}events:{project_id}", json.dumps(event))


async def runner_connected() -> bool:
    hb = await get_redis().get(f"{PREFIX}runner:heartbeat")
    if hb is None:
        return False
    try:
        import time
        return (time.time() * 1000 - float(hb)) < 15000
    except (ValueError, TypeError):
        return False


async def get_flag(name: str, default: bool = False) -> bool:
    v = await get_redis().get(PREFIX + name)
    if v is None:
        return default
    return v == "1"


async def set_flag(name: str, value: bool) -> None:
    if value:
        await get_redis().set(PREFIX + name, "1")
    else:
        await get_redis().delete(PREFIX + name)


def _user_key(base: str, user_id: int | None) -> str:
    return f"{base}:{user_id or 1}"


async def get_user_flag(base: str, user_id: int | None, default: bool = False) -> bool:
    return await get_flag(_user_key(base, user_id), default)


async def set_user_flag(base: str, user_id: int | None, value: bool) -> None:
    await set_flag(_user_key(base, user_id), value)
