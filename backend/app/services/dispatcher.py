"""Board-Spiegel und Betriebs-Tick.

Was hier früher stand — der komplette Zwei-Phasen-Ablauf planning→plan_review→approved→
execution→to_test→done samt Fortsetzung, Aufteilung und Torprüfung — ist jetzt ein
gestaltbarer Prozess (Slot `ticket_lifecycle`, siehe `services/workflow_seed.py`). Übrig
bleiben die zwei Dinge, die kein Prozess sein sollen:

* `sync_board_status` — spiegelt den Agent-Status auf die Board-Spalte,
* der Betriebs-Tick — Wartungs-Update (kein Agent läuft → Self-Deploy) und Aufräumen nach
  einem Neustart.

Die Torprüfung vor jedem Agentenlauf (Zeitfenster, Runner-Limit, Runaway-Bremse) steckt in
`services/agent_gate.py`; das Weiterschalten wartender Läufe erledigt der Engine-Tick.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select

from ..core.redis import get_flag, lauf_lebt, peek_result, set_flag
from ..db import SessionLocal
from ..models.agents import Run, RunStep
from ..models.enums import HoldReason, StatusCategory, TicketAgentStatus
from ..models.ticket import Issue, WorkflowStatus

log = logging.getLogger("dispatcher")

TICK_SECONDS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# Agent-Status → Board-Spalte (per Name). Fehler/Rückfrage/Freigabe-Gate/zu-testen → „Warten",
# aktive Bearbeitung → „In Arbeit", fertig → „Fertig". open/None lässt die Spalte unangetastet.
_AGENT_STATUS_TO_BOARD = {
    TicketAgentStatus.failed: "Warten",
    TicketAgentStatus.hold: "Warten",
    TicketAgentStatus.plan_review: "Warten",
    # Testumgebungs-Flow (ABC-18): eigene Spalte zwischen „In Arbeit" und „Fertig".
    # Fehlt sie im Projekt, greift der Fallback auf „Warten" (s. sync_board_status).
    TicketAgentStatus.to_test: "Testen",
    TicketAgentStatus.testing: "Testen",
    TicketAgentStatus.planning: "In Arbeit",
    TicketAgentStatus.approved: "In Arbeit",
    TicketAgentStatus.in_progress: "In Arbeit",
    TicketAgentStatus.done: "Fertig",
}


async def sync_board_status(db, issue: Issue) -> None:
    """Board-Spalte an den Agent-Status koppeln (verschiebt das Ticket in die passende Spalte,
    falls sie im Projekt existiert). So landen Fehler/Rückfragen/zu-testende Tickets in „Warten"
    statt in „To Do" zu bleiben. Ein manuell in eine „done"-Spalte gesetztes Ticket wird nie
    zurückgezogen (menschliche Abnahme hat Vorrang)."""
    target = _AGENT_STATUS_TO_BOARD.get(issue.agent_status)
    if not target:
        return
    stats = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == issue.project_id))).scalars().all()
    cur = next((s for s in stats if s.id == issue.status_id), None)
    if cur and cur.category == StatusCategory.done and target != "Fertig":
        return  # bereits (manuell) abgenommen — nicht nach „Warten" zurückziehen
    st = next((s for s in stats if s.name == target), None)
    if st is None and target == "Testen":
        # Bestandsprojekt ohne „Testen"-Spalte: einmalig anlegen (vor „Fertig" einsortiert),
        # sonst Fallback auf „Warten" — der Übergang darf nie am fehlenden Status scheitern.
        st = await _ensure_testing_status(db, issue.project_id, stats)
    if st and issue.status_id != st.id:
        issue.status_id = st.id


async def _ensure_testing_status(db, project_id: int, stats: list[WorkflowStatus]):
    """Legt die „Testen"-Spalte für ein Bestandsprojekt an (idempotent) und hängt sie ans Board."""
    from ..models.ticket import Board, BoardColumn
    done = next((s for s in stats if s.category == StatusCategory.done), None)
    order = (done.order if done else max([s.order for s in stats], default=0) + 1)
    st = WorkflowStatus(project_id=project_id, name="Testen",
                        category=StatusCategory.in_progress, order=order)
    db.add(st)
    if done is not None:
        done.order = order + 1
    await db.flush()
    board = (await db.execute(select(Board).where(Board.project_id == project_id))).scalars().first()
    if board is not None:
        db.add(BoardColumn(board_id=board.id, status_id=st.id, order=order))
    return st


# ── Puls des Workers ─────────────────────────────────────────────────────────
# Der Worker schreibt alle 5 s `runner:heartbeat` (ex=10). Bleibt der aus, während Aufträge
# in der Warteschlange liegen, steht der Worker — genau das passierte am 2026-07-30 über
# eine Stunde lang, ohne dass es irgendwo aufgefallen wäre: der Assistent schwieg einfach,
# und das ist von „hat nichts zu sagen" nicht zu unterscheiden. Lieber einmal melden.
WORKER_STILL_SEC = 180
_puls_gemeldet = False


async def _pruefe_worker_puls() -> None:
    global _puls_gemeldet
    from ..core.redis import PREFIX, QUEUE, get_redis
    from ..models.notification import Notification
    from ..models.user import User
    r = get_redis()
    puls = await r.get(f"{PREFIX}runner:heartbeat")
    wartend = await r.llen(QUEUE)
    steht = puls is None and wartend > 0
    if steht and not _puls_gemeldet:
        log.error("Worker ohne Puls, %s Auftrag/Aufträge warten", wartend)
        async with SessionLocal() as db:
            # An den Betreiber: ohne Worker läuft weder Assistent noch Agent.
            admin = (await db.execute(select(User).where(User.telegram_chat_id.isnot(None))
                                      .order_by(User.id))).scalars().first()
            if admin:
                db.add(Notification(
                    user_id=admin.id, kind="worker_down", title="⚠ Worker steht",
                    body=f"Kein Lebenszeichen des Workers, {wartend} Auftrag/Aufträge warten. "
                         "Assistent und Agenten laufen gerade nicht.",
                    chat_id=admin.telegram_chat_id))
                await db.commit()
        _puls_gemeldet = True
    elif not steht and _puls_gemeldet:
        log.info("Worker wieder da")
        _puls_gemeldet = False


# ── Betriebs-Tick ────────────────────────────────────────────────────────────

async def _tick() -> None:
    """Wartungs-Update: sobald der letzte Agent fertig ist, das Wartungsprojekt über den
    Deployer-Sidecar self-deployen. Während des Updates startet `agent_gate` ohnehin
    keine neuen Läufe."""
    await _pruefe_worker_puls()
    if not await get_flag("update_pending"):
        return
    from ..models.ops import Deployment
    from .appsettings import get_setting
    async with SessionLocal() as db:
        running = (await db.execute(
            select(func.count()).select_from(Issue).where(Issue.agent_working.is_(True)))).scalar() or 0
        if running:
            return
        mp = await get_setting(db, "maintenance_project_id", "")
        if mp.isdigit():
            db.add(Deployment(project_id=int(mp), stack_dir="", self_deploy=True,
                              status="pending", source="maintenance"))
            await db.commit()
            log.info("Wartungs-Update: letzter Agent fertig → Self-Deploy eingereiht (Projekt %s)", mp)
    await set_flag("update_pending", False)
    await set_flag("update_in_progress", True)


async def run_dispatcher() -> None:
    log.info("betriebs-tick gestartet (tick=%ss)", TICK_SECONDS)
    await asyncio.sleep(5)
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            log.exception("betriebs-tick failed")
        await asyncio.sleep(TICK_SECONDS)


# Läuft ein Worker-Run noch, wenn sein letzter run_step jünger als dies ist? Der Worker
# ist ein eigener Container und überlebt einen Backend-Reload (uvicorn --reload) — dann
# darf sein Lauf NICHT als „interrupted" abgeschossen werden, sondern wird wieder angebunden.
REATTACH_FRESH_SECONDS = 300


async def recover_on_start() -> None:
    """Nach Backend-Neustart aufräumen.

    Das Wieder-Anbinden laufender Agenten macht die Engine (`recover_workflow_agents`, sie
    kennt den wartenden Schritt). Hier bleibt: Wartungs-Flags quittieren und `agent_working`
    von Tickets zurücksetzen, deren Lauf nachweislich tot ist — sonst blockierten sie über
    das Runner-Limit alle weiteren Läufe.
    """
    just_updated = await get_flag("update_in_progress") or await get_flag("update_pending")
    if just_updated:
        await set_flag("update_in_progress", False)
        await set_flag("update_pending", False)
        log.info("Wartungs-Update abgeschlossen — Betrieb fortgesetzt.")
    async with SessionLocal() as db:
        if just_updated:
            from .appsettings import set_setting
            await set_setting(db, "last_update_completed_at", _now().isoformat())
        stuck = (await db.execute(
            select(Issue).where(Issue.agent_working.is_(True)))).scalars().all()
        for issue in stuck:
            run = (await db.execute(select(Run).where(Run.issue_id == issue.id)
                                    .order_by(Run.id.desc()))).scalars().first()
            alive = False
            if run and run.task_id:
                # Erste und beste Auskunft: der Puls des Workers zu genau diesem Auftrag.
                # Ohne ihn galt ein Lauf schon als tot, wenn er länger als fünf Minuten an
                # einer einzigen Antwort saß — die Engine hing derweil weiter am selben Lauf.
                if await lauf_lebt(run.task_id):
                    alive = True
                if not alive and run.finished_at is None:
                    last_step = (await db.execute(select(func.max(RunStep.created_at))
                                                  .where(RunStep.run_id == run.id))).scalar()
                    ref = last_step or run.started_at
                    if ref and (_now() - ref).total_seconds() < REATTACH_FRESH_SECONDS:
                        alive = True
                if not alive and await peek_result(run.task_id):
                    alive = True  # Worker war fertig, Ergebnis liegt noch in Redis
            if alive:
                log.info("recover: Lauf %s lebt — Engine bindet ihn wieder an", run.task_id)
                continue
            issue.agent_working = False
            if issue.agent_status == TicketAgentStatus.in_progress:
                from .artifacts import set_ticket_status
                await set_ticket_status(db, issue, TicketAgentStatus.hold,
                                        reason=HoldReason.interrupted)
        await db.commit()
