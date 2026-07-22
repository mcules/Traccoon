"""Ticket-Dispatcher: verarbeitet AUSSCHLIESSLICH Tickets mit gesetztem assigned_agent.

Kein Auto-Pickup unzugewiesener Tickets (bewusster Unterschied zu DevTeam).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid

from sqlalchemy import func, or_, select

from zoneinfo import ZoneInfo

from ..core.redis import (
    enqueue_task, get_flag, get_user_flag, peek_result, publish_event, set_flag, wait_result,
)
from ..db import SessionLocal
from ..models.agents import Run, RunStep
from ..models.enums import HoldReason, StatusCategory, TicketAgentStatus
from ..models.project import Project
from ..models.ticket import Comment, Issue, WorkflowStatus
from ..models.user import User

log = logging.getLogger("dispatcher")

TICK_SECONDS = 30
MAX_CONCURRENT = 3
TZ = ZoneInfo("Europe/Berlin")
PICKUP_STATES = (TicketAgentStatus.planning, TicketAgentStatus.approved)


async def _gate_ok(db, issue: Issue) -> bool:
    """Gates für APPROVED-Tickets: start_at, per-Owner shift_end, night_task-Fenster.
    PLANNING läuft immer (nur global_pause zählt)."""
    if issue.agent_status != TicketAgentStatus.approved:
        return True
    if issue.start_at and issue.start_at > _now():
        return False
    owner_id = issue.assigned_by_user_id or issue.reporter_id
    if await get_user_flag("shift_end", owner_id):
        return False
    if issue.night_task:
        user = await db.get(User, owner_id) if owner_id else None
        if user and not user.night_override:
            now = dt.datetime.now(TZ)
            if now.weekday() not in (user.night_days or [0, 1, 2, 3, 4, 5, 6]):
                return False
            s, e, h = user.night_start_hour, user.night_end_hour, dt.datetime.now(TZ).hour
            in_window = (s <= h < e) if s < e else (h >= s or h < e)
            if not in_window:
                return False
    return True


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# Agent-Status → Board-Spalte (per Name). Fehler/Rückfrage/Freigabe-Gate/zu-testen → „Warten",
# aktive Bearbeitung → „In Arbeit", fertig → „Fertig". open/None lässt die Spalte unangetastet.
_AGENT_STATUS_TO_BOARD = {
    TicketAgentStatus.failed: "Warten",
    TicketAgentStatus.hold: "Warten",
    TicketAgentStatus.plan_review: "Warten",
    TicketAgentStatus.to_test: "Warten",
    TicketAgentStatus.testing: "Warten",
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
    if st and issue.status_id != st.id:
        issue.status_id = st.id


async def _plan_role(issue: Issue, project: Project) -> str:
    # Planung erstellt IMMER der Architekt — auch bei PM-Zuweisung. Der PM plant nie
    # selbst, sondern delegiert nach dem Standard-Muster: Architekt plant, Developer
    # setzt um. (issue.plan_agent kann pro Ticket abweichen, project.plan_agent ist der
    # Projekt-Default = "architect".)
    return issue.plan_agent or project.plan_agent or "architect"


async def _exec_role(issue: Issue, project: Project) -> str:
    # Umsetzung (Code) macht der Developer. Bei PM-Zuweisung bewusst NICHT der PM,
    # sondern der Projekt-Ausführungsagent (Default "developer").
    if issue.assigned_agent == "project_manager":
        return issue.exec_agent or project.exec_agent or "developer"
    return issue.assigned_agent or issue.exec_agent or project.exec_agent or "developer"


MAX_CONTINUATIONS = 30

# Harte Runaway-Bremse pro Ticket, UNABHÄNGIG von continuation_count. Fängt jeden
# Re-Dispatch-Pfad ab (Review/Kommentar/Accept-Konflikt/…) — auch wenn der Zähler
# nie erhöht wurde. Beleg: TRA-19 lief 41 Runs / 11,9 Mio Input-Tokens bei
# continuation_count=0, weil die Cap-Prüfung bisher nur im loop_exhausted-Zweig
# griff. Beim Erreichen → hold (Mensch), statt weiter Tokens zu verbrennen.
MAX_RUNS_PER_TICKET = 30
MAX_INPUT_TOKENS_PER_TICKET = 8_000_000


async def _process(issue_id: int) -> None:
    async with SessionLocal() as db:
        issue = await db.get(Issue, issue_id)
        if issue is None or issue.assigned_agent is None:
            return
        project = await db.get(Project, issue.project_id)

        # Runaway-Bremse: bevor ein (weiterer) Run gestartet wird, harte Obergrenze
        # pro Ticket prüfen. Greift für JEDEN Dispatch-Pfad, weil alle durch _process
        # laufen — im Gegensatz zur continuation_count-Prüfung, die nur loop_exhausted
        # abdeckt. Über der Schwelle → hold statt erneutem Agent-Lauf.
        agg = (
            await db.execute(
                select(func.count(Run.id), func.coalesce(func.sum(Run.input_tokens), 0))
                .where(Run.issue_id == issue.id)
            )
        ).one()
        run_count, in_tok = int(agg[0]), int(agg[1] or 0)
        if run_count >= MAX_RUNS_PER_TICKET or in_tok >= MAX_INPUT_TOKENS_PER_TICKET:
            issue.agent_working = False
            issue.agent_status = TicketAgentStatus.hold
            issue.hold_reason = HoldReason.cap
            await sync_board_status(db, issue)
            await db.commit()
            log.warning(
                "Ticket %s: Runaway-Cap erreicht (%d Runs / %d Input-Tokens) → hold",
                issue.key, run_count, in_tok,
            )
            return

        planning = issue.agent_status == TicketAgentStatus.planning
        phase = "planning" if planning else "execution"
        role = await (_plan_role if planning else _exec_role)(issue, project)

        issue.agent_working = True
        if not planning:
            issue.agent_status = TicketAgentStatus.in_progress
        # Sobald ein Agent zu arbeiten beginnt, muss das Ticket in „In Arbeit" stehen
        # (planning wie in_progress mappen beide dorthin). Ohne diesen Sync bliebe es in
        # der Spalte, in der es vorher lag (z. B. „Warten" nach plan_review/Continuation).
        await sync_board_status(db, issue)
        await db.commit()

        cont = issue.continuation_count
        # EINDEUTIG pro Dispatch: key/phase/cont bleiben lesbar (Logs/Reattach), der uuid-Suffix
        # verhindert, dass mehrere Runs desselben Tickets denselben result:{task_id}-Key teilen.
        # Sonst könnte wait_result ein VERALTETES/fremdes Ergebnis sofort zurückliefern und einen
        # Spurious-Re-Dispatch auslösen (Beleg: TRA-19, 41 Runs alle mit task_id TRA-19-execution-0,
        # weil continuation_count auf 0 einfror). continuation_index im Payload bleibt = cont.
        task_id = f"{issue.key}-{phase}-{cont}-{uuid.uuid4().hex[:8]}"
        # Continuation-Hinweis = Zusammenfassung des letzten Runs
        hint = ""
        if cont > 0:
            last = (
                await db.execute(select(Run).where(Run.issue_id == issue.id).order_by(Run.id.desc()))
            ).scalars().first()
            hint = (last.summary or last.last_text or "") if last else ""

        payload = {
            "task_id": task_id, "issue_id": issue.id, "issue_key": issue.key, "project_id": project.id,
            "role": role, "phase": phase, "continuation_index": cont, "continuation_hint": hint,
        }
        await publish_event(project.id, {"type": "agent_status", "agent": role, "status": "working",
                                         "issue_key": issue.key})
        await enqueue_task(payload)

    result = await wait_result(task_id, timeout=1800)
    await _finalize(issue_id, result, role=role, phase=phase, task_id=task_id)


async def _finalize(issue_id: int, result: dict | None, *, role: str, phase: str,
                    task_id: str) -> None:
    """Nachbearbeitung eines abgeschlossenen Runs (Status/Board/Notiz/Notify). Ausgelagert
    aus _process, damit auch ein nach Backend-Neustart wieder-angebundener Lauf (_reattach)
    dieselbe Logik durchläuft."""
    async with SessionLocal() as db:
        issue = await db.get(Issue, issue_id)
        project = await db.get(Project, issue.project_id)
        issue.agent_working = False
        status = (result or {}).get("status")
        enqueue_after_commit = None

        if result is None or status == "failed":
            issue.agent_status = TicketAgentStatus.failed
            issue.hold_reason = None
        elif status == "planned":
            issue.plan = result.get("output", "")
            issue.agent_status = TicketAgentStatus.plan_review
            # Enthält der Plan einen <subtickets>-Block → Split-Vorschlag (andere Freigabe)
            issue.hold_reason = (HoldReason.plan_split if "<subtickets>" in (issue.plan or "")
                                 else HoldReason.plan_review)
        elif status == "blocked":
            kind = (result.get("blocker") or {}).get("kind")
            issue.agent_status = TicketAgentStatus.hold
            issue.hold_reason = {"permission": HoldReason.permission, "review": HoldReason.review}.get(
                kind, HoldReason.question)
        elif status == "loop_exhausted":
            # Continuation vs. Eskalation (Stall-Erkennung per Fingerprint)
            fp = result.get("worktree_fingerprint")
            prev = (
                await db.execute(
                    select(Run).where(Run.issue_id == issue.id, Run.worktree_fingerprint.isnot(None))
                    .order_by(Run.id.desc())
                )
            ).scalars().all()
            prev_fps = [r.worktree_fingerprint for r in prev[1:2]]  # vorletzter
            stalled = fp and prev_fps and fp == prev_fps[0]
            issue.continuation_count += 1
            if not project.auto_continue or issue.continuation_count > MAX_CONTINUATIONS or stalled:
                issue.agent_status = TicketAgentStatus.hold
                issue.hold_reason = HoldReason.stuck if stalled else HoldReason.cap
            else:
                # Continuation: DIESELBE Phase fortsetzen. Ein in der PLANUNG ausgelaufener
                # Lauf muss WEITER PLANEN (zurück auf planning) — sonst würde ohne fertigen
                # Plan in die Ausführung gesprungen (Developer ohne Plan). Ausführung setzt
                # via approved fort.
                issue.agent_status = (TicketAgentStatus.planning if phase == "planning"
                                      else TicketAgentStatus.approved)
        elif status == "done":
            issue.hold_reason = None
            if result.get("merge_status") == "conflict":
                issue.merge_status = "conflict"
            if issue.parent_ticket_id:
                # Split-Kind: fertig → Sub-Branch in den Sammelticket-Branch mergen.
                # Die Freigabe des nächsten Teils erfolgt erst NACH dem Merge (accept-Handler),
                # damit Teil n+1 auf dem Ergebnis von Teil n aufbaut. Accept-Task wird erst
                # nach dem Commit eingereiht (sonst sieht der Worker das „done" evtl. noch nicht).
                issue.agent_status = TicketAgentStatus.done
                issue.resolved_at = _now()
                enqueue_after_commit = {"kind": "accept", "task_id": f"accept-{issue.key}",
                                        "issue_id": issue.id, "project_id": project.id}
            else:
                needs_review = project.managed or bool(project.verify_command)
                issue.agent_status = TicketAgentStatus.to_test if needs_review else TicketAgentStatus.done
                if issue.agent_status == TicketAgentStatus.done:
                    issue.resolved_at = _now()
        else:
            issue.agent_status = TicketAgentStatus.failed

        # Agent-Notiz ins Ticket: jeder abgeschlossene Run hinterlässt eine kurze
        # Spur (wer = role, was = Zusammenfassung). „blocked" schreibt der Worker
        # bereits selbst (Rückfrage/Berechtigung) — hier nicht doppeln.
        summary = ((result or {}).get("summary") or (result or {}).get("output") or "").strip()
        note = None
        if status == "planned":
            note = "📋 Plan erstellt — bereit zur Freigabe." + (f"\n{summary}" if summary else "")
        elif status == "done":
            tail = " — bereit zur Abnahme" if issue.agent_status == TicketAgentStatus.to_test else " — erledigt"
            note = (summary or "Arbeit abgeschlossen.") + tail
        elif status == "loop_exhausted":
            note = ("⏸ Pausiert (Limit/Feststecker)" if issue.agent_status == TicketAgentStatus.hold
                    else "⏭ Zwischenstand, arbeite weiter") + (f":\n{summary}" if summary else ".")
        elif status == "failed" or result is None:
            err = ((result or {}).get("output") or summary or "unbekannter Fehler").strip()
            note = f"❌ Fehlgeschlagen: {err}"
        if note:
            db.add(Comment(issue_id=issue.id, author_id=None, author_label=role,
                           body=note[:1500], kind="agent"))

        # Benachrichtigungen bei relevanten Zuständen
        from .notify import notify_issue
        st = issue.agent_status
        if st == TicketAgentStatus.plan_review:
            await notify_issue(db, issue, "plan_review", f"{issue.key}: Plan bereit", issue.summary)
        elif st == TicketAgentStatus.to_test:
            await notify_issue(db, issue, "to_test", f"{issue.key}: bereit zur Abnahme", issue.summary)
        elif st == TicketAgentStatus.failed:
            await notify_issue(db, issue, "failed", f"{issue.key}: fehlgeschlagen",
                               (result or {}).get("output", "")[:400])
        elif st == TicketAgentStatus.hold:
            hr = issue.hold_reason.value if issue.hold_reason else ""
            await notify_issue(db, issue, "blocked", f"{issue.key}: blockiert ({hr})",
                               (result or {}).get("summary", ""))

        await sync_board_status(db, issue)   # Board-Spalte an den neuen Agent-Status koppeln
        await db.commit()
        if enqueue_after_commit:
            await enqueue_task(enqueue_after_commit)
        await publish_event(project.id, {"type": "issue_update", "issue_key": issue.key,
                                         "agent_status": issue.agent_status.value if issue.agent_status else None})
    log.info("processed %s phase=%s → %s", task_id, phase, status)


async def _promote_split(db, child: Issue, project: Project) -> None:
    """Split-Kette: nächstes geparktes Geschwister freigeben; alle fertig → Umbrella."""
    umbrella_id = child.parent_ticket_id
    sibs = (
        await db.execute(select(Issue).where(Issue.parent_ticket_id == umbrella_id)
                         .order_by(Issue.split_order).with_for_update())
    ).scalars().all()
    nxt = next((s for s in sibs if s.agent_status is None), None)
    if nxt is not None:
        nxt.agent_status = TicketAgentStatus.approved
        await sync_board_status(db, nxt)
        db.add(Comment(
            issue_id=nxt.id, author_id=None, author_label="System", kind="internal",
            body=f"▶️ Automatisch freigegeben — Vorgänger #{child.number} ({child.key}) ist fertig.",
        ))
        return
    # alle Kinder fertig → Umbrella abschließen
    if all(s.agent_status == TicketAgentStatus.done for s in sibs):
        umbrella = await db.get(Issue, umbrella_id)
        if umbrella:
            needs_review = project.managed or bool(project.verify_command)
            umbrella.agent_status = TicketAgentStatus.to_test if needs_review else TicketAgentStatus.done
            if umbrella.agent_status == TicketAgentStatus.done:
                umbrella.resolved_at = _now()
                # Ohne Abnahme-Gate: Sammelticket-Branch direkt in den Ziel-Branch mergen.
                await enqueue_task({"kind": "accept", "task_id": f"accept-{umbrella.key}",
                                    "issue_id": umbrella.id, "project_id": project.id})
            umbrella.hold_reason = None
            await sync_board_status(db, umbrella)
            done_lbl = "zur Abnahme bereit" if needs_review else "abgeschlossen"
            db.add(Comment(
                issue_id=umbrella.id, author_id=None, author_label="System", kind="internal",
                body=f"✅ Alle {len(sibs)} Sub-Tickets fertig — Sammelticket {done_lbl}.",
            ))


async def _tick() -> None:
    # Wartungs-Update: keine neuen Agenten starten; wenn der letzte Agent fertig ist,
    # das Wartungsprojekt über den Deployer-Sidecar self-deployen.
    if await get_flag("update_pending") or await get_flag("update_in_progress"):
        if await get_flag("update_pending"):
            from ..models.nexus import Deployment
            from .appsettings import get_setting
            async with SessionLocal() as db:
                running = (await db.execute(
                    select(func.count()).select_from(Issue).where(Issue.agent_working.is_(True)))).scalar() or 0
                if running == 0:
                    mp = await get_setting(db, "maintenance_project_id", "")
                    if mp.isdigit():
                        db.add(Deployment(project_id=int(mp), stack_dir="", self_deploy=True, status="pending"))
                        await db.commit()
                        log.info("Wartungs-Update: letzter Agent fertig → Self-Deploy eingereiht (Projekt %s)", mp)
                    await set_flag("update_pending", False)
                    await set_flag("update_in_progress", True)
        return  # während des Updates keine neuen Agenten dispatchen
    if await get_flag("global_pause"):
        return
    async with SessionLocal() as db:
        candidates = (
            await db.execute(
                select(Issue).where(
                    Issue.assigned_agent.isnot(None),
                    Issue.agent_working.is_(False),
                    or_(*[Issue.agent_status == s for s in PICKUP_STATES]),
                ).order_by(Issue.updated_at)
            )
        ).scalars().all()
        # Bereits laufende Läufe je Eigentümer — Basis für das Pro-Nutzer-Limit (max_runners).
        running = (await db.execute(select(Issue).where(Issue.agent_working.is_(True)))).scalars().all()
        per_owner: dict[int, int] = {}
        for r in running:
            oid = r.assigned_by_user_id or r.reporter_id
            per_owner[oid] = per_owner.get(oid, 0) + 1
        caps: dict[int, int] = {}

        allowed: list[int] = []
        for issue in candidates:
            if not await _gate_ok(db, issue):
                continue
            owner_id = issue.assigned_by_user_id or issue.reporter_id
            if owner_id not in caps:
                owner = await db.get(User, owner_id) if owner_id else None
                caps[owner_id] = owner.max_runners if owner else MAX_CONCURRENT
            if per_owner.get(owner_id, 0) >= caps[owner_id]:
                continue  # dieser Nutzer lastet seine Runner schon aus
            per_owner[owner_id] = per_owner.get(owner_id, 0) + 1
            allowed.append(issue.id)
            if len(allowed) >= MAX_CONCURRENT:
                break
    if allowed:
        await asyncio.gather(*[_process(i) for i in allowed])


async def run_dispatcher() -> None:
    log.info("dispatcher gestartet (tick=%ss)", TICK_SECONDS)
    await asyncio.sleep(5)
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            log.exception("dispatcher tick failed")
        await asyncio.sleep(TICK_SECONDS)


# Läuft ein Worker-Run noch, wenn sein letzter run_step jünger als dies ist? Der Worker
# ist ein eigener Container und überlebt einen Backend-Reload (uvicorn --reload) — dann
# darf sein Lauf NICHT als „interrupted" abgeschossen werden, sondern wird wieder angebunden.
REATTACH_FRESH_SECONDS = 300


async def _reattach(issue_id: int, task_id: str, role: str, phase: str) -> None:
    """Bindet einen laufenden/fertigen Worker-Run nach Backend-Neustart wieder an: wartet auf
    sein Ergebnis (persistiert in Redis) und fährt die normale Nachbearbeitung."""
    try:
        result = await wait_result(task_id, timeout=1800)
        await _finalize(issue_id, result, role=role, phase=phase, task_id=task_id)
        log.info("reattach finalisiert %s → %s", task_id, (result or {}).get("status"))
    except Exception:  # noqa: BLE001
        log.exception("reattach fehlgeschlagen für %s", task_id)


async def recover_on_start() -> None:
    """Nach Backend-Neustart aufräumen. Läufe mit noch lebendem Worker (frische run_steps)
    oder bereits vorliegendem Ergebnis werden WIEDER ANGEBUNDEN statt unterbrochen — sonst
    würde ein reiner Backend-Reload den unabhängig weiterlaufenden Worker-Run verwaisen."""
    just_updated = await get_flag("update_in_progress") or await get_flag("update_pending")
    if just_updated:
        await set_flag("update_in_progress", False)
        await set_flag("update_pending", False)
        log.info("Wartungs-Update abgeschlossen — Betrieb fortgesetzt.")
    reattach: list[tuple[int, str, str, str]] = []
    async with SessionLocal() as db:
        if just_updated:
            from .appsettings import set_setting
            await set_setting(db, "last_update_completed_at", _now().isoformat())
        rows = (
            await db.execute(select(Issue).where(Issue.agent_status == TicketAgentStatus.in_progress))
        ).scalars().all()
        for issue in rows:
            run = (await db.execute(
                select(Run).where(Run.issue_id == issue.id).order_by(Run.id.desc()))).scalars().first()
            alive = False
            if run and run.task_id:
                if run.finished_at is None:
                    last_step = (await db.execute(
                        select(func.max(RunStep.created_at)).where(RunStep.run_id == run.id))).scalar()
                    ref = last_step or run.started_at
                    if ref and (_now() - ref).total_seconds() < REATTACH_FRESH_SECONDS:
                        alive = True
                if not alive and await peek_result(run.task_id):
                    alive = True  # Worker war fertig, Ergebnis liegt noch in Redis
            if alive:
                reattach.append((issue.id, run.task_id, run.agent, run.phase))
                continue
            issue.agent_status = TicketAgentStatus.hold
            issue.hold_reason = HoldReason.interrupted
            issue.agent_working = False
        # verwaiste agent_working-Flags (nur die NICHT wieder angebundenen)
        keep = {r[0] for r in reattach}
        stuck = (await db.execute(select(Issue).where(Issue.agent_working.is_(True)))).scalars().all()
        for issue in stuck:
            if issue.id not in keep:
                issue.agent_working = False
        await db.commit()
    for issue_id, task_id, role, phase in reattach:
        log.info("reattach: Lauf %s lebt/hat Ergebnis — binde wieder an statt zu unterbrechen", task_id)
        asyncio.create_task(_reattach(issue_id, task_id, role, phase))
