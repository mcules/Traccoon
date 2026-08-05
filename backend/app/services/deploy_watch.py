"""Der Bühnen-Watcher für Deployments — Statuswechsel werden Schrittzeilen.

Die `deployments`-Tabelle wird vom `deployer`-Sidecar geschrieben, der von Traccoons
Ereignisstrom nichts weiß. Damit ein Deploy im Büro sichtbar wird, braucht er eine
**echte `run_steps`-Zeile**: `seq = id * 4 + slot` ist die Ankunftsreihenfolge, und wer
in den Strom will, muss durch eine Zeile. Damit gilt automatisch alles Übrige —
Monotonie, Slot-Arithmetik, Schnappschuss, Wiederverbindungsprotokoll. Ein eigener
`seq`-Raum für Deployments schied aus (`Recorder.push` entdoppelt **ausschließlich** über
`seq`, kollidierende Zahlen hießen sofort Datenverlust), ein zweiter Redis-Kanal ebenso
(er verdoppelte WS-Brücke und Schnappschuss-Paginierung samt eigener Kappungsregel), und
ein synthetischer `Run` je Deployment hätte `runs`, Kostenansichten und Roster mit
tokenlosen Geisterläufen verseucht.

**Eigene Schleife mit 3 s Takt**, nicht der Betriebs-Tick: der läuft alle 30 s und ist
damit länger als ein mittlerer Deploy (12,5 s). Der `building`-Zustand wäre regelmäßig
vorbei, bevor wir hinsehen, und die Bühne bekäme nur noch das Urteil. Wo das trotzdem
passiert (ein Deploy zwischen zwei Takten komplett durch), wird der Auftakt nachgeholt —
`states_for` liefert dann `start` und Urteil zusammen. Ein Urteil ohne Auftakt wäre ein
Rack, das aufleuchtet, ohne dass je jemand hingelaufen ist.

**Idempotenz über eine Spalte, nicht über Prozessgedächtnis.** `announced_status` sagt,
was schon erzählt wurde; gelesen wird `WHERE status <> announced_status`. Neustartfest,
doppelfrei, und „was wurde schon erzählt" ist eine Tatsache in der Datenbank statt einer
Vermutung im RAM.

**An welchen Lauf die Zeile gehängt wird** — ein Deployment hat keinen eigenen Aktor
(siehe oben), es *gehört* dem Lauf, der es ausgelöst hat:

    Agenten-Werkzeug   `worktree <> ''`   der Lauf, dessen `deploy`-Aufruf gerade wartet:
                                          über `issue_id` der jüngste `running`-Lauf,
                                          sonst der jüngste überhaupt
    Merge/Workflow     `issue_id` gesetzt der jüngste Lauf dieses Tickets
    Wartungs-Update    `self_deploy`,     **kein Anker → kein Bühnen-Ereignis**
                       kein `issue_id`

Der dritte Fall bekommt bewusst nichts, und das ist keine Lücke: ein Self-Deploy
recreated den Backend-Container, der die Bühne beliefert. Der WebSocket fällt mitten in
der Animation, der Prozess, der sie zeichnen ließe, stirbt an ihr. Einen Vorgang zu
animieren, der den Animierenden tötet, ist ein Kategorienfehler. Diese Zeilen leben in
der Liste (`api/deployments.py`), nicht im Raum — und ein Test nagelt die Entscheidung
fest, damit sie niemand „repariert".

**Nur das jüngste Fenster.** Die Bestandszeilen haben `announced_status = ''` und wären
sonst allesamt „neu" — der erste Takt nach dem Aufspielen erzählte 186 Deployments aus
drei Monaten, als wären sie eben passiert. Ein Deploy, der älter ist als
`ANNOUNCE_WINDOW_HOURS`, ist keine Nachricht mehr; die Historie zeigt der Lesepfad
(`services/office.deployment_events`) mit geliehener `seq` an seiner richtigen Stelle.
Wovon eine Zeile bereits erzählt wurde (`announced_status <> ''`), bleibt trotzdem im
Blick: eine angefangene Geschichte wird zu Ende erzählt, auch nach einem langen Ausfall.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models.agents import Run, RunStep
from ..models.ops import Deployment
from .office import (
    DEPLOY_LOG_HEAD_CHARS, RunCtx, add_step, deploy_content, deploy_state, deploy_target,
    publish_step,
)

log = logging.getLogger("deploy_watch")

# Kürzer als ein mittlerer Deploy (12,5 s) — sonst sieht der Raum den Auftakt nie.
TICK_SECONDS = 3
# Ab wann ein Statuswechsel keine Nachricht mehr ist (siehe Modul-Docstring).
ANNOUNCE_WINDOW_HOURS = 6

# Status, nach denen nichts mehr kommt.
TERMINAL_STATUS = ("ok", "failed", "rolledback")
# Status, der den Auftakt bereits erzählt hat.
STARTED_STATUS = ("building",)

# Der Wert der `ok`-Spalte am Schritt je Zustand. Dreiwertig wie überall im Haus: beim
# Auftakt weiß niemand etwas, und ein geratenes True malte die Ansicht grün.
_OK_BY_STATE: dict[str, bool | None] = {"start": None, "ok": True, "fail": False, "back": False}


def states_for(announced: str, status: str) -> list[str]:
    """Welche Zustände dieser Übergang erzählt — die ganze Idempotenz-Logik, rein.

    Leer, wenn der Status im Raum nichts zu suchen hat (`pending`, `cancelled`). Sonst
    der neue Zustand — und ihm vorangestellt der Auftakt, falls der noch nie erzählt
    wurde, weil der Deploy zwischen zwei Takten komplett durchgelaufen ist.
    """
    state = deploy_state(status)
    if not state:
        return []
    if state == "start" or announced in STARTED_STATUS or announced in TERMINAL_STATUS:
        return [state]
    return ["start", state]


async def _anchor_run(db: AsyncSession, dep: Deployment) -> Run | None:
    """Der Lauf, an dessen Erzählung das Deployment hängt (oder None, siehe Docstring).

    Beide Anker-Fälle gehen über `issue_id` — der Deploy-Aufruf des Agenten kennt sein
    Ticket, und `ix_runs_issue_started` deckt die Abfrage. Ohne Ticket gibt es keinen
    Lauf, dem der Vorgang gehören könnte; dann bleibt der Raum still.
    """
    if not dep.issue_id:
        return None
    if (dep.worktree or "").strip():
        # Agenten-Werkzeug: `_do_deploy` wartet inline auf das Ergebnis, der auslösende
        # Lauf ist also noch `running` — und genau der soll zum Rack laufen. Der Rückfall
        # auf den jüngsten Lauf greift, wenn er inzwischen am Timeout gestorben ist.
        laufend = (await db.execute(
            select(Run).where(Run.issue_id == dep.issue_id, Run.status == "running")
            .order_by(Run.id.desc()).limit(1))).scalars().first()
        if laufend is not None:
            return laufend
    return (await db.execute(
        select(Run).where(Run.issue_id == dep.issue_id)
        .order_by(Run.id.desc()).limit(1))).scalars().first()


async def announce(db: AsyncSession, dep: Deployment) -> list[RunStep]:
    """Einen Statuswechsel erzählen: Schrittzeilen schreiben, quittieren, senden.

    Quittiert wird IMMER — auch wenn es keinen Anker gibt (Wartungs-Update) oder der
    Status nichts zu zeigen hat. Sonst läge dieselbe Zeile bei jedem Takt wieder auf dem
    Tisch und der Watcher liefe ewig im Kreis.
    """
    vorher = dep.announced_status or ""
    states = states_for(vorher, dep.status or "")
    steps: list[RunStep] = []
    ctx: RunCtx | None = None

    if states:
        run = await _anchor_run(db, dep)
        if run is not None:
            ctx = RunCtx.from_run(run)
            # `RunStep.seq` ist der laufende Zähler DES LAUFS; der Watcher schreibt in
            # einen fremden Lauf und muss dort anknüpfen, statt bei 1 anzufangen.
            ctx.seq = int((await db.execute(
                select(func.max(RunStep.seq)).where(RunStep.run_id == run.id))).scalar() or 0)
            for state in states:
                steps.append(await add_step(
                    db, ctx, role="system", kind="deploy", target=deploy_target(dep),
                    ok=_OK_BY_STATE.get(state),
                    # Beim Auftakt ist der Log noch leer bzw. gehört einem früheren
                    # Versuch — ihn mitzuschicken hieße, ein Ergebnis vorwegzunehmen.
                    content=deploy_content(dep.id, state,
                                           "" if state == "start"
                                           else (dep.log or "")[:DEPLOY_LOG_HEAD_CHARS]),
                    commit=False))

    # Freier Anschluss: der Triggername steht seit jeher in `BUILTIN_EVENTS` und hat noch
    # nie gefeuert. Hier ist die Stelle, an der alles beisammen ist. Vor dem Commit, damit
    # Quittung und gestartete Abläufe in EINER Transaktion liegen — ein Absturz dazwischen
    # verlöre sonst das Ereignis, während die Quittung stünde.
    if dep.status in TERMINAL_STATUS and vorher not in TERMINAL_STATUS:
        from .events import emit
        await emit(db, "deployment.finished", project_id=dep.project_id,
                   issue_id=dep.issue_id, source_ref=f"deployment:{dep.id}",
                   payload={"deployment": {
                       "id": dep.id, "status": dep.status, "ok": dep.status == "ok",
                       "source": dep.source or "", "stack_dir": dep.stack_dir or "",
                       "self_deploy": bool(dep.self_deploy),
                       "check_only": bool(dep.check_only)}})

    dep.announced_status = dep.status or ""
    await db.commit()
    if steps:
        log.info("Deployment %s: %s → %s im Raum von Lauf %s",
                 dep.id, vorher or "—", dep.status, ctx.run_id if ctx else "—")
    # Erst nach dem Commit senden: vorher hat die Zeile keine `id` und damit keine `seq`.
    for step in steps:
        await publish_step(ctx, step)
    return steps


async def tick(db: AsyncSession) -> int:
    """Ein Durchgang. Liefert die Zahl der erzählten Zeilen (für Test und Log)."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=ANNOUNCE_WINDOW_HOURS)
    offen = (await db.execute(
        select(Deployment).where(
            Deployment.status != Deployment.announced_status,
            # Junge Zeilen — oder solche, deren Geschichte schon angefangen wurde. Das
            # zweite Glied schließt die Lücke, wenn ein Deploy den Auftakt bekam und sein
            # Ausgang erst nach einem langen Backend-Ausfall feststand: eine angefangene
            # Erzählung wird zu Ende erzählt, egal wie alt sie inzwischen ist.
            or_(Deployment.created_at >= cutoff, Deployment.announced_status != ""),
        ).order_by(Deployment.id))).scalars().all()
    erzaehlt = 0
    for dep in offen:
        erzaehlt += len(await announce(db, dep))
    return erzaehlt


async def run_deploy_watch() -> None:
    log.info("deploy-watch gestartet (tick=%ss)", TICK_SECONDS)
    await asyncio.sleep(5)
    while True:
        try:
            async with SessionLocal() as db:
                await tick(db)
        except Exception:  # noqa: BLE001 — die Bühne ist ein Zuschauer, kein Beteiligter
            log.exception("deploy-watch fehlgeschlagen")
        await asyncio.sleep(TICK_SECONDS)
