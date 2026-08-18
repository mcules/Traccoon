"""Ausführungs-Engine für Prozesse (Token-basiert, React-Flow-Graph).

Hier läuft inzwischen ALLES, was Traccoon an Abläufen kennt — auch der KI-Ticket-
Lebenszyklus, der früher fest im Dispatcher verdrahtet war. Ein Prozess ist ein Node-Graph
(`version.graph = {"nodes":[...], "edges":[...]}`); eine Instanz trägt genau EIN aktives
Token, das synchron von Knoten zu Knoten schaltet, bis es wartet (Mensch, Genehmigung,
Agentenlauf, Ereignis, Unter-Prozess) oder ein end-Knoten erreicht ist.

Absicherungen:
- atomarer `advancing`-Claim (UPDATE … WHERE advancing=false RETURNING) gegen Doppel-Advance
  (Tick ↔ Request-Event),
- `MAX_STEPS`-Bremse gegen zyklische Auto-Advance,
- `routed_at` je Schritt: ein erledigter Warte-Knoten wird genau EINMAL in eine Kante
  übersetzt — sonst drehte sich eine Rückkante (Fortsetzung!) endlos im Kreis,
- Torwächter vor jedem Agentenlauf (`services/agent_gate.py`) — Zeitfenster, Runner-Limit
  und Runaway-Bremse gelten unabhängig vom gezeichneten Graphen,
- eigene DB-Session (SessionLocal), niemals die Request-Session.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from dataclasses import dataclass

from sqlalchemy import select, update

from ..db import SessionLocal
from ..models.enums import (
    ProjectRole,
    WorkflowInstanceStatus as IStatus,
    WorkflowNodeType as NType,
    WorkflowStepStatus as SStatus,
    WorkflowSubjectKind,
    WorkflowTokenState as TState,
)
from ..models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowStepRun, WorkflowToken, WorkflowVersion,
)
from ..core.redis import GNADENFRIST, enqueue_task, peek_result, publish_event, wait_result
from .jsonlogic import ALLOWED_OPS, JsonLogicError, collect_operators, safe_eval

log = logging.getLogger("workflow_engine")

MAX_STEPS = 50          # Zyklus-Bremse pro advance-Durchlauf
MAX_DRIVE_ROUNDS = 5    # Nachfassen, wenn während des Durchlaufs ein Ergebnis eintraf
TICK_SECONDS = 30       # Sicherheitsnetz-Loop (Crash-Recovery)
MAX_SUBFLOW_DEPTH = 3   # Schachtelungs-Bremse für subflow-Knoten

# Knoten, die auf ein externes Ereignis warten. Beim Wiedereintritt (Token wieder aktiv)
# NICHT erneut ausführen, sondern die Kante gemäß hinterlegter decision nehmen — aber nur
# EINMAL je Durchlauf (`routed_at`), sonst dreht sich eine Rückkante endlos im Kreis.
WAIT_NODES = ("human_task", "approval", "agent_task", "wait_event", "subflow")

# Standard-Abbildung Worker-Ergebnis → Ausgang. Greift nur, wenn weder `outcomes_map` noch
# ein gleichnamiger Ausgang (z. B. „loop_exhausted") am Knoten verdrahtet ist.
_DEFAULT_AGENT_MAP = {
    "planned": "ok", "done": "ok", "failed": "err",
    "blocked": "blocked", "loop_exhausted": "blocked",
}
# Harter Deckel für das Warten auf einen Agentenlauf: standardmäßig KEINER.
#
# Bis 2026-08-05 stand hier 1800 s, und der Wächter gab nach 30 Minuten auf — obwohl der
# Lauf weiterarbeitete. Ein exec-Schritt umfasst Umsetzung UND Review-Runden in EINEM
# Auftrag; das dauert regelmäßig länger. Ergebnis: Ticket „fehlgeschlagen: unbekannter
# Fehler", während der Agent kurz darauf sauber committete (ABC-2, ABC-6). Gewartet wird
# jetzt am Lebenszeichen des Laufs (`wait_result`), nicht an der Uhr. Wer für einen
# einzelnen Knoten trotzdem eine Grenze will, setzt `timeout_sec` in dessen Konfiguration;
# AGENT_WAIT_LIMIT_SEC ist der globale Notnagel (0 = aus).
AGENT_DEFAULT_TIMEOUT = int(os.getenv("AGENT_WAIT_LIMIT_SEC", "0"))
# Deckel für asynchrone Auto-Aktionen (Merge, Testumgebung). Die sind kurz und begrenzt,
# hier bleibt eine Uhr sinnvoll — aber großzügig, ein Preview-Build zieht sich.
ACTION_DEFAULT_TIMEOUT = int(os.getenv("ACTION_WAIT_LIMIT_SEC", "3600"))


# ── Graph-Helfer ─────────────────────────────────────────────────────────────

def _nodes(graph: dict) -> list[dict]:
    return graph.get("nodes") or []


def _edges(graph: dict) -> list[dict]:
    return graph.get("edges") or []


def _node_by_id(graph: dict, node_id: str) -> dict | None:
    return next((n for n in _nodes(graph) if n.get("id") == node_id), None)


def node_type(node: dict) -> str:
    return node.get("type") or (node.get("data") or {}).get("type") or ""


def node_config(node: dict) -> dict:
    data = node.get("data") or {}
    cfg = data.get("config")
    if isinstance(cfg, dict):
        return cfg
    if isinstance(node.get("config"), dict):
        return node["config"]
    return {}


def _handle_matches(edge_handle, handle) -> bool:
    """Kante passt zu einem Ausgang. „out"/None sind austauschbar (Default-Ausgang)."""
    if handle in (None, "", "out"):
        return edge_handle in (None, "", "out")
    return edge_handle == handle


def next_node(edges: list[dict], node_id: str, handle) -> str | None:
    """Ziel-Knoten der Kante mit source==node_id und passendem sourceHandle."""
    for e in edges:
        if e.get("source") == node_id and _handle_matches(e.get("sourceHandle"), handle):
            return e.get("target")
    return None


def _outgoing_handles(edges: list[dict], node_id: str) -> set[str]:
    out = set()
    for e in edges:
        if e.get("source") == node_id:
            h = e.get("sourceHandle")
            out.add(h if h not in (None, "") else "out")
    return out


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


# Wächter-Tasks (Agentenlauf, asynchrone Aktion) brauchen eine starke Referenz — sonst
# darf der Garbage Collector sie mitten im Warten einsammeln und der Prozess bliebe still
# stehen. Tests warten über `drain()` auf sie.
_BACKGROUND: set[asyncio.Task] = set()


# Schritt-IDs, für die in DIESEM Prozess gerade ein Wächter wartet. Ohne dieses Wissen
# könnte das erneute Anbinden (siehe `recover_workflow_agents`) einen zweiten Wächter auf
# dasselbe Ergebnis setzen — beide würden schalten.
_WAECHTER: set[int] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def drain(timeout: float = 5.0) -> None:
    """Auf alle laufenden Wächter warten (Tests; im Betrieb nicht nötig)."""
    for _ in range(20):
        pending = {t for t in _BACKGROUND if not t.done()}
        if not pending:
            return
        await asyncio.wait(pending, timeout=timeout)


def _to_instance_status(name) -> IStatus:
    if isinstance(name, IStatus):
        return name
    try:
        return IStatus(str(name))
    except ValueError:
        return IStatus.completed


# ── Outcome eines Node-Handlers ──────────────────────────────────────────────

@dataclass
class Outcome:
    handle: str | None = None          # zu nehmender Ausgang (bei Weiterschaltung)
    wait: bool = False                 # Token wartet auf externes Ereignis
    waiting_for: str | None = None     # human_task | approval | agent
    terminal: bool = False             # Instanz endet
    instance_status: str | None = None  # bei terminal: completed | failed | …
    error: str | None = None


# ── Step-/Zuständigen-Helfer ─────────────────────────────────────────────────

async def _latest_step(db, instance_id: int, node_id: str) -> WorkflowStepRun | None:
    return (
        await db.execute(
            select(WorkflowStepRun)
            .where(WorkflowStepRun.instance_id == instance_id, WorkflowStepRun.node_id == node_id)
            .order_by(WorkflowStepRun.id.desc())
        )
    ).scalars().first()


async def _resolve_assignee(db, inst: WorkflowInstance, cfg: dict) -> int | None:
    """Löst den Zuständigen eines human_task/approval auf.

    config["assignee"] = {mode, ...}:
      user     → {user_id}
      role     → {role}          (erstes Projekt-Mitglied dieser Rolle; sonst Projekt-Lead)
      context  → {path}          (User-ID aus instance.context per Dot-Pfad)
      reporter → Reporter des gebundenen Issues
    """
    a = cfg.get("assignee") or {}
    mode = a.get("mode", "user")
    if mode == "user":
        uid = a.get("user_id")
        return int(uid) if uid is not None else None
    if mode == "reporter":
        if inst.issue_id:
            from ..models.ticket import Issue
            issue = await db.get(Issue, inst.issue_id)
            return issue.reporter_id if issue else None
        return None
    if mode == "context":
        from .jsonlogic import _dig
        val = _dig(inst.context or {}, a.get("path") or a.get("var") or "")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    if mode == "role":
        role = a.get("role")
        if not (inst.project_id and role):
            return None
        from ..models.project import Project, ProjectMember
        try:
            prole = ProjectRole(role)
        except ValueError:
            return None
        m = (
            await db.execute(
                select(ProjectMember)
                .where(ProjectMember.project_id == inst.project_id, ProjectMember.role == prole)
                .order_by(ProjectMember.id)
            )
        ).scalars().first()
        if m:
            return m.user_id
        proj = await db.get(Project, inst.project_id)
        return proj.lead_user_id if proj else None
    return None


async def _notify_assignee(db, inst: WorkflowInstance, node: dict, ntype: str, assignee: int | None):
    """In-App/Telegram-Benachrichtigung an den Zuständigen + Ticket-Notiz (falls Issue).

    `config.notify = false` schaltet sie ab — für Wartepunkte, deren Frage schon auf einem
    eigenen Weg gestellt wurde (die Spam-Rückfrage bringt ihre eigene Karte mit Knöpfen mit;
    eine zweite, knopflose Meldung zur selben Mail wäre nur Lärm).
    """
    cfg = node_config(node)
    if cfg.get("notify") is False:
        return
    label = cfg.get("label") or cfg.get("title") or node.get("id")
    verb = "Genehmigung" if ntype == "approval" else "Aufgabe"
    title = f"Workflow: {verb} „{label}“ wartet"
    body = cfg.get("description") or cfg.get("instructions") or ""
    if assignee is not None:
        import os
        from ..models.notification import Notification
        from ..models.user import User
        u = await db.get(User, assignee)
        chat = (u.telegram_chat_id if u else None) or os.getenv("TELEGRAM_OWNER_CHAT", "") or None
        db.add(Notification(
            user_id=assignee, project_id=inst.project_id, issue_id=inst.issue_id,
            kind="workflow_task", title=title[:500], body=body[:4000], chat_id=chat,
        ))
    if inst.issue_id:
        from .comments import add_system_comment
        who = f" (Zuständig: User #{assignee})" if assignee else ""
        await add_system_comment(
            db, inst.issue_id, f"⏳ Workflow wartet auf {verb.lower()}: „{label}“{who}",
            author_label="Workflow",
        )


async def _ensure_wait_step(db, inst, node, ntype, token, waiting_for) -> None:
    """Legt (idempotent) einen wartenden StepRun an und benachrichtigt den Zuständigen."""
    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.waiting:
        return  # wartet bereits — nicht doppelt anlegen/benachrichtigen
    assignee = await _resolve_assignee(db, inst, node_config(node))
    step = WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType(ntype), status=SStatus.waiting, assignee_user_id=assignee,
    )
    db.add(step)
    await db.flush()
    await _notify_assignee(db, inst, node, ntype, assignee)
    await publish_event(inst.project_id or 0, {
        "type": "workflow_step", "instance_id": inst.id, "node_id": node["id"],
        "node_type": ntype, "status": "waiting", "assignee_user_id": assignee,
    })


# ── Node-Handler ─────────────────────────────────────────────────────────────

async def _run_node(db, inst, node, ntype, token, edges, spawn_after: list) -> Outcome:
    cfg = node_config(node)

    if ntype == "start":
        return Outcome(handle="out")

    if ntype == "end":
        return Outcome(terminal=True, instance_status=cfg.get("outcome", "completed"))

    if ntype == "decision":
        for b in cfg.get("branches") or []:
            guard = b.get("guard")
            if guard in (None, {}, True, ""):
                return Outcome(handle=b.get("handle"))
            try:
                if safe_eval(guard, inst.context or {}):
                    return Outcome(handle=b.get("handle"))
            except JsonLogicError as e:
                log.warning("Instanz %s: Guard-Fehler in %s: %s", inst.id, node["id"], e)
                continue
        return Outcome(handle=cfg.get("default_handle", "default"))

    if ntype == "human_task":
        await _ensure_wait_step(db, inst, node, ntype, token, "human_task")
        return Outcome(wait=True, waiting_for="human_task")

    if ntype == "approval":
        await _ensure_wait_step(db, inst, node, ntype, token, "approval")
        return Outcome(wait=True, waiting_for="approval")

    if ntype == "auto_action":
        from .workflow_actions import run_action
        # Idempotenz: eine asynchrone Aktion (z. B. Merge) läuft bereits → nicht neu starten.
        running = await _latest_step(db, inst.id, node["id"])
        if running is not None and running.status == SStatus.running:
            return Outcome(wait=True, waiting_for="action")
        try:
            result = await run_action(db, inst, node)
            wait_spec = result.pop("_wait", None) if isinstance(result, dict) else None
            if wait_spec:
                # Asynchrone Aktion: Schritt bleibt „running", ein Wächter schaltet weiter.
                step = WorkflowStepRun(
                    instance_id=inst.id, token_id=token.id, node_id=node["id"],
                    node_type=NType.auto_action, status=SStatus.running,
                    result={**result, "task_id": wait_spec["task_id"],
                            "context_key": str(wait_spec.get("context_key") or "action")},
                )
                db.add(step)
                await db.flush()
                spawn_after.append(_await_action(
                    inst.id, token.id, step.id, wait_spec["task_id"],
                    int(wait_spec.get("timeout") or ACTION_DEFAULT_TIMEOUT),
                    str(wait_spec.get("context_key") or "action"),
                    dict(cfg.get("outcomes_map") or {}),
                ))
                return Outcome(wait=True, waiting_for="action")
            db.add(WorkflowStepRun(
                instance_id=inst.id, token_id=token.id, node_id=node["id"],
                node_type=NType.auto_action, status=SStatus.done, result=result,
                completed_at=_now(),
            ))
            return Outcome(handle="out")
        except Exception as e:  # noqa: BLE001
            log.exception("Instanz %s: auto_action %s fehlgeschlagen", inst.id, node["id"])
            db.add(WorkflowStepRun(
                instance_id=inst.id, token_id=token.id, node_id=node["id"],
                node_type=NType.auto_action, status=SStatus.failed, error=str(e)[:2000],
                completed_at=_now(),
            ))
            if next_node(edges, node["id"], "error") is not None:
                return Outcome(handle="error")
            return Outcome(terminal=True, instance_status="failed",
                           error=f"auto_action '{node['id']}' fehlgeschlagen: {e}")

    if ntype == "agent_task":
        return await _start_agent_task(db, inst, node, token, cfg, spawn_after)

    if ntype == "wait_event":
        return await _wait_for_event(db, inst, node, token, cfg)

    if ntype == "subflow":
        return await _start_subflow(db, inst, node, token, cfg)

    return Outcome(terminal=True, instance_status="failed",
                   error=f"Unbekannter Knotentyp '{ntype}'")


# ── wait_event: auf ein externes Ereignis warten ─────────────────────────────

DEFAULT_EVENTS = ("comment", "manual")


def _accepted_events(cfg: dict) -> list[str]:
    ev = cfg.get("events")
    if isinstance(ev, str):
        ev = [ev]
    return [str(e) for e in (ev or DEFAULT_EVENTS)]


async def _wait_for_event(db, inst, node, token, cfg) -> Outcome:
    """Hält den Lauf an, bis `resume_on_event` ein passendes Ereignis meldet.

    Damit hängen Rückfragen, abgelehnte Pläne und Fehlversuche im Ticket-Lebenszyklus am
    Kommentar des Menschen, statt über einen versteckten Status-Sprung neu zu starten.
    """
    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.waiting:
        return Outcome(wait=True, waiting_for="event")
    step = WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType.wait_event, status=SStatus.waiting,
        result={"events": _accepted_events(cfg)},
    )
    db.add(step)
    await db.flush()
    await publish_event(inst.project_id or 0, {
        "type": "workflow_step", "instance_id": inst.id, "node_id": node["id"],
        "node_type": "wait_event", "status": "waiting",
    })
    return Outcome(wait=True, waiting_for="event")


async def resume_on_event(issue_id: int, event: str, payload: dict | None = None) -> bool:
    """Meldet ein Ereignis (comment|answer|manual|…) an die Lebenszyklus-Instanz eines Tickets.

    Liefert True, wenn ein wartender wait_event-Knoten das Ereignis angenommen hat. Der
    Aufrufer (z. B. `services/comments.apply_user_comment`) muss vorher committet haben.
    """
    async with SessionLocal() as db:
        from ..models.ticket import Issue
        issue = await db.get(Issue, issue_id)
        if issue is None or issue.workflow_instance_id is None:
            return False
        inst = await db.get(WorkflowInstance, issue.workflow_instance_id)
        if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
            return False
        version = await db.get(WorkflowVersion, inst.version_id)
        graph = (version.graph if version else None) or {}
        token = (await db.execute(
            select(WorkflowToken).where(
                WorkflowToken.instance_id == inst.id,
                WorkflowToken.state == TState.waiting,
                WorkflowToken.waiting_for == "event",
            ).with_for_update())).scalars().first()
        if token is None:
            return False
        node = _node_by_id(graph, token.node_id)
        if node is None:
            return False
        accepted = _accepted_events(node_config(node))
        if event not in accepted and "any" not in accepted:
            return False
        step = await _latest_step(db, inst.id, token.node_id)
        if step is not None and step.status == SStatus.waiting:
            step.status = SStatus.done
            step.decision = event
            step.result = {**(step.result or {}), "event": event, "payload": payload or {}}
            step.completed_at = _now()
        ctx = dict(inst.context or {})
        ctx["event"] = {"name": event, **(payload or {})}
        inst.context = ctx
        token.state = TState.active
        token.waiting_for = None
        inst.status = IStatus.running
        instance_id = inst.id
        await db.commit()
    await advance(instance_id)
    return True


# ── subflow: anderen Ablauf als Kind-Instanz ausführen ───────────────────────

async def _instance_depth(db, inst: WorkflowInstance) -> int:
    depth, cur = 0, inst
    while cur is not None and cur.parent_instance_id and depth <= MAX_SUBFLOW_DEPTH:
        cur = await db.get(WorkflowInstance, cur.parent_instance_id)
        depth += 1
    return depth


async def _start_subflow(db, inst, node, token, cfg) -> Outcome:
    """Startet die für den Slot aufgelöste Definition als Kind-Instanz und wartet auf sie.

    So ruft der Ticket-Lebenszyklus den eigenständigen „Abnahme"-Ablauf auf, ohne ihn zu
    duplizieren — und ein angepasster Abnahme-Prozess wirkt überall, wo er aufgerufen wird.
    """
    from .workflow_sets import resolve_definition

    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.running:
        return Outcome(wait=True, waiting_for="subflow")

    slot = cfg.get("slot") or cfg.get("workflow_slot")
    if not slot:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"subflow-Knoten '{node['id']}' ohne Slot")
    if await _instance_depth(db, inst) >= MAX_SUBFLOW_DEPTH:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"subflow zu tief verschachtelt (> {MAX_SUBFLOW_DEPTH})")

    # Auch ein Unterprozess folgt der Vorgangsart des Tickets, an dem er hängt.
    vorgangsart = None
    if inst.issue_id:
        from ..models.ticket import Issue
        issue = await db.get(Issue, inst.issue_id)
        vorgangsart = issue.type_id if issue else None
    definition = await resolve_definition(db, inst.project_id, slot, vorgangsart)
    if definition is None or definition.current_version_id is None:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"Kein veröffentlichter Ablauf für Slot '{slot}'")

    step = WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType.subflow, status=SStatus.running, result={"slot": slot},
    )
    db.add(step)
    await db.flush()

    ctx = dict(inst.context or {}) if cfg.get("inherit_context", True) else {}
    child = await start_workflow(
        db, definition, subject_kind=definition.subject_kind, issue_id=inst.issue_id,
        hardware_asset_id=inst.hardware_asset_id, context=ctx, actor_id=inst.started_by,
        source=f"subflow:{inst.id}", parent_instance_id=inst.id, parent_node_id=node["id"],
    )
    step.result = {"slot": slot, "child_instance_id": child.id}
    return Outcome(wait=True, waiting_for="subflow")


async def _finish_subflow(parent_id: int, node_id: str, child_status: str,
                          child_context: dict, error: str | None) -> None:
    """Weckt den wartenden subflow-Schritt der Eltern-Instanz, wenn das Kind endet."""
    async with SessionLocal() as db:
        inst = await db.get(WorkflowInstance, parent_id)
        if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
            return
        step = await _latest_step(db, parent_id, node_id)
        if step is None or step.status != SStatus.running:
            return
        step.status = SStatus.done
        step.decision = child_status
        step.result = {**(step.result or {}), "child_status": child_status}
        step.error = error[:2000] if error else None
        step.completed_at = _now()
        # Der Kind-Kontext (z. B. Merge-Ergebnis) fließt zurück nach oben.
        inst.context = {**(inst.context or {}), **(child_context or {})}
        token = (await db.execute(
            select(WorkflowToken).where(
                WorkflowToken.instance_id == parent_id,
                WorkflowToken.state == TState.waiting,
                WorkflowToken.node_id == node_id,
            ).with_for_update())).scalars().first()
        if token is not None:
            token.state = TState.active
            token.waiting_for = None
        if inst.status == IStatus.waiting:
            inst.status = IStatus.running
        await db.commit()
    await advance(parent_id)


# ── agent_task: Brücke zur bestehenden Agent-Queue (Worker) ──────────────────

async def _resolve_agent_role(db, issue, cfg: dict) -> str:
    """Rolle des Laufs. Symbolische Werte binden den Graphen an die Projekt-Einstellungen,
    statt eine Besetzung fest einzubacken (`plan_agent`, `exec_agent`, `review_agent`,
    `assigned`); alles andere gilt als konkreter Rollenname.

    Entspricht `dispatcher._plan_role`/`_exec_role`: geplant wird immer vom Architekten
    (auch bei PM-Zuweisung — der PM plant nie selbst), umgesetzt vom Ausführungs-Agenten.
    """
    from ..models.project import Project
    role = str(cfg.get("agent_role") or "exec_agent")
    if role not in ("plan_agent", "exec_agent", "review_agent", "assigned"):
        return role
    project = await db.get(Project, issue.project_id)
    if role == "plan_agent":
        return issue.plan_agent or (project.plan_agent if project else "") or "architect"
    if role == "review_agent":
        return (project.review_agent if project else "") or "code_reviewer"
    exec_default = issue.exec_agent or (project.exec_agent if project else "") or "developer"
    if role == "assigned":
        return issue.assigned_agent or exec_default
    # exec_agent: bei PM-Zuweisung bewusst NICHT der PM, sondern der Ausführungs-Agent.
    if issue.assigned_agent == "project_manager":
        return exec_default
    return issue.assigned_agent or exec_default


async def _park_on_gate(db, inst, issue, node, token, verdict) -> Outcome:
    """Lauf vertagen, weil ein Tor zu ist. Das Token bleibt auf dem Knoten stehen
    (`waiting_for="gate"`), der Engine-Tick versucht es zyklisch erneut.

    Bei der Runaway-Bremse (`hold`) wird zusätzlich das Ticket angehalten — es geht erst
    weiter, wenn ein Mensch eingreift (z. B. neue Plan-Freigabe setzt das Cap-Fenster neu).
    """
    from ..models.enums import TicketAgentStatus

    issue.agent_working = False
    detail = f"{verdict.reason}: {verdict.detail}" if verdict.detail else verdict.reason
    if verdict.hold:
        from .artifacts import set_ticket_status
        await set_ticket_status(db, issue, TicketAgentStatus.hold,
                                reason=verdict.hold_reason)

    step = await _latest_step(db, inst.id, node["id"])
    if step is None or step.status not in (SStatus.pending,):
        step = WorkflowStepRun(
            instance_id=inst.id, token_id=token.id, node_id=node["id"],
            node_type=NType.agent_task, status=SStatus.pending,
        )
        db.add(step)
    step.token_id = token.id
    step.error = detail[:2000]
    step.result = {**(step.result or {}), "gate": verdict.reason}
    await db.flush()
    log.info("Instanz %s: Agentenlauf vertagt (%s)", inst.id, detail)
    await publish_event(inst.project_id or 0, {
        "type": "workflow_step", "instance_id": inst.id, "node_id": node["id"],
        "node_type": "agent_task", "status": "gate", "reason": verdict.reason,
    })
    return Outcome(wait=True, waiting_for="gate")

async def _start_agent_task(db, inst, node, token, cfg, spawn_after: list) -> Outcome:
    """Startet einen KI-Agenten-Lauf über die Redis-Queue und wartet asynchron auf das
    Ergebnis (Token wartet, `_await_agent` schaltet weiter).

    Vor dem Einreihen entscheidet `services/agent_gate` — Zeitfenster, Nutzer-Limit und
    Runaway-Bremse gelten unabhängig davon, wie der Prozess gezeichnet ist.

    Voraussetzung (durch validate erzwungen): subject_kind=issue, issue_id gesetzt.
    """
    import uuid

    # Idempotenz: läuft für diesen Knoten schon ein Agent → nicht doppelt einreihen.
    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.running:
        return Outcome(wait=True, waiting_for="agent")

    if inst.issue_id is None:
        db.add(WorkflowStepRun(
            instance_id=inst.id, token_id=token.id, node_id=node["id"],
            node_type=NType.agent_task, status=SStatus.failed,
            error="agent_task erfordert ein gebundenes Ticket (subject_kind=issue)",
            completed_at=_now(),
        ))
        return Outcome(terminal=True, instance_status="failed",
                       error="agent_task ohne Ticket-Bindung")

    from ..models.ticket import Issue
    issue = await db.get(Issue, inst.issue_id)
    if issue is None:
        db.add(WorkflowStepRun(
            instance_id=inst.id, token_id=token.id, node_id=node["id"],
            node_type=NType.agent_task, status=SStatus.failed,
            error=f"Gebundenes Ticket #{inst.issue_id} nicht gefunden", completed_at=_now(),
        ))
        return Outcome(terminal=True, instance_status="failed", error="Ticket nicht gefunden")

    # ── Torwächter (Policy, nicht Graph) ────────────────────────────────────
    from . import agent_gate
    verdict = await agent_gate.check(db, issue)
    if not verdict.ok:
        return await _park_on_gate(db, inst, issue, node, token, verdict)

    role = await _resolve_agent_role(db, issue, cfg)
    phase = "planning" if cfg.get("phase") == "planning" else "execution"
    # 0/None = kein Deckel: gewartet wird am Lebenszeichen des Laufs, nicht an der Uhr.
    timeout = int(cfg.get("timeout_sec") or AGENT_DEFAULT_TIMEOUT)
    outcomes_map = cfg.get("outcomes_map") or {}
    task_id = f"wf-{inst.id}-{token.id}-{node['id']}-{uuid.uuid4().hex[:8]}"

    # Ein wartender Gate-Schritt desselben Knotens wird wiederverwendet, damit ein
    # mehrfach vertagter Lauf keine Schritt-Historie aufbläht.
    step = existing if (existing is not None and existing.status == SStatus.pending) else None
    if step is None:
        step = WorkflowStepRun(
            instance_id=inst.id, token_id=token.id, node_id=node["id"],
            node_type=NType.agent_task, assignee_user_id=None,
        )
        db.add(step)
    step.status = SStatus.running
    step.token_id = token.id
    step.error = None
    step.result = {"task_id": task_id}  # task_id für Reattach hinterlegt
    await db.flush()

    # Dieselbe Marke wie im Monitor/„Läuft gerade" und im Pro-Nutzer-Limit.
    issue.agent_working = True
    # Und der sichtbare Zustand: hier — beim tatsächlichen Einreihen — arbeitet der Agent
    # wirklich. Der Graph setzt vorher nur „freigegeben", weil zwischen Freigabe und Start
    # der Torwächter beliebig lange dazwischenstehen kann. Nur die Umsetzung; die Planung
    # hat ihren eigenen Zustand, den `st_planning` schon gesetzt hat.
    if phase == "execution":
        from ..models.enums import TicketAgentStatus
        from .artifacts import set_ticket_status
        # `hold`/`failed` gehören ausdrücklich dazu: läuft wieder ein Agent, ist der alte
        # Grund überholt. Ohne das zeigte ABC-31 stundenlang „hold — merge", während der
        # Entwickler längst wieder arbeitete — das Etikett log, nicht der Prozess.
        if issue.agent_status in (TicketAgentStatus.approved, TicketAgentStatus.plan_review,
                                  TicketAgentStatus.open, TicketAgentStatus.hold,
                                  TicketAgentStatus.failed, None):
            issue.hold_reason = None
            await set_ticket_status(db, issue, TicketAgentStatus.in_progress, board=False)
    cont = int((inst.context or {}).get("continuation") or 0)
    hint = str((inst.context or {}).get("continuation_hint") or "")
    payload = {
        "task_id": task_id, "issue_id": issue.id, "issue_key": issue.key,
        "project_id": inst.project_id, "role": role, "phase": phase,
        "continuation_index": cont, "continuation_hint": hint,
    }
    await enqueue_task(payload)
    await publish_event(inst.project_id or 0, {
        "type": "workflow_step", "instance_id": inst.id, "node_id": node["id"],
        "node_type": "agent_task", "status": "running",
    })
    if inst.issue_id:
        from .comments import add_system_comment
        label = cfg.get("label") or node["id"]
        await add_system_comment(
            db, inst.issue_id, f"🤖 Workflow startet KI-Agent „{role}“ für Schritt „{label}“",
            author_label="Workflow",
        )
    # Ergebnis-Wächter erst NACH dem Commit starten: er liest Schritt und Token in
    # einer EIGENEN Session — vor dem Commit sähe er sie gar nicht (und ein danach
    # nachziehender Commit dieses Durchlaufs würde seine Änderungen überschreiben).
    spawn_after.append(_await_agent(inst.id, token.id, step.id, task_id,
                                    dict(outcomes_map), timeout))
    return Outcome(wait=True, waiting_for="agent")


async def _warte(task_id: str, timeout: int, was: str) -> tuple[dict | None, dict]:
    """Wartet auf ein Worker-Ergebnis und benennt im Fehlerfall, WAS schiefging.

    Ohne Ergebnis kommt ein Ersatz-Ergebnis zurück, das den Grund trägt („Lauf verschwunden"
    vs. „Zeitgrenze") und für die Nachzügler-Abholung markiert ist (`verloren`). Vorher stand
    an dieser Stelle „kein Ergebnis (Timeout)" — im Ticket las sich das als „fehlgeschlagen:
    unbekannter Fehler", obwohl gar nichts fehlgeschlagen war.
    """
    uhr = asyncio.get_running_loop().time
    start = uhr()
    try:
        result = await wait_result(task_id, timeout=timeout or None)
    except Exception:  # noqa: BLE001
        log.exception("wait_result (%s) für %s fehlgeschlagen", was, task_id)
        result = None
    if result is not None:
        return result, result
    dauer = int(uhr() - start)
    if timeout and dauer >= timeout:
        text = (f"Zeitgrenze dieses Schrittes erreicht ({dauer}s ≥ {timeout}s). "
                "Der Lauf selbst kann noch arbeiten.")
    else:
        text = (f"Lauf verschwunden — seit {int(GNADENFRIST / 60)} Minuten kein Lebenszeichen "
                "(kein Worker-Puls, nicht mehr in der Warteschlange).")
    log.warning("Wächter %s (%s) ohne Ergebnis nach %ds: %s", task_id, was, dauer, text)
    return None, {"status": "failed", "success": False, "output": text, "summary": text,
                  "verloren": True, "task_id": task_id}


async def _await_action(instance_id: int, token_id: int, step_id: int, task_id: str,
                        timeout: int, context_key: str, outcomes_map: dict) -> None:
    """Wächter für asynchrone Auto-Aktionen (Merge & Co.): wartet auf das Worker-Ergebnis,
    legt es unter `context.<context_key>` ab und schaltet über den passenden Ausgang weiter.

    Analog `_await_agent` — nur so bleibt die Engine während eines minutenlangen Merges
    ansprechbar, statt Session und Advance-Claim zu blockieren.
    """
    _WAECHTER.add(step_id)
    try:
        await _await_action_inner(instance_id, token_id, step_id, task_id, timeout,
                                  context_key, outcomes_map)
    finally:
        _WAECHTER.discard(step_id)


async def _await_action_inner(instance_id: int, token_id: int, step_id: int, task_id: str,
                              timeout: int, context_key: str, outcomes_map: dict) -> None:
    result, ersatz = await _warte(task_id, timeout, "Aktion")
    status = (result or {}).get("status", "failed")

    async with SessionLocal() as db:
        step = await db.get(WorkflowStepRun, step_id)
        if step is None or step.status != SStatus.running:
            return
        inst = await db.get(WorkflowInstance, instance_id)
        version = await db.get(WorkflowVersion, inst.version_id) if inst else None
        edges = _edges((version.graph if version else None) or {})
        handle = outcomes_map.get(status)
        if not handle:
            handle = status if next_node(edges, step.node_id, status) is not None else "out"
        step.status = SStatus.done
        step.decision = handle
        step.result = {**(step.result or {}), "result": ersatz, "verloren": result is None}
        step.error = result.get("error") if result else ersatz["output"]
        step.completed_at = _now()
        if inst is not None:
            inst.context = {**(inst.context or {}), context_key: ersatz}
            if inst.status == IStatus.waiting:
                inst.status = IStatus.running
        tok = await db.get(WorkflowToken, token_id)
        if tok is not None and tok.state == TState.waiting:
            tok.state = TState.active
            tok.waiting_for = None
        await db.commit()
    await advance(instance_id)


async def _lookup_run_id(db, task_id: str) -> int | None:
    from ..models.agents import Run
    r = (
        await db.execute(select(Run).where(Run.task_id == task_id).order_by(Run.id.desc()))
    ).scalars().first()
    return r.id if r else None


async def _stalled(db, issue_id: int, fingerprint: str | None) -> bool:
    """Steckt der Agent fest? Wahr, wenn der Worktree seit dem VORHERIGEN Lauf unverändert
    ist (Fingerprint-Vergleich) — 1:1 die Stall-Erkennung des früheren Dispatchers."""
    if not fingerprint:
        return False
    from ..models.agents import Run
    rows = (await db.execute(
        select(Run.worktree_fingerprint)
        .where(Run.issue_id == issue_id, Run.worktree_fingerprint.isnot(None))
        .order_by(Run.id.desc()).limit(2))).scalars().all()
    prev = rows[1:2]  # vorletzter Lauf (der letzte ist der gerade beendete)
    return bool(prev) and prev[0] == fingerprint


def _agent_handle(edges: list[dict], node_id: str, status: str, outcomes_map: dict) -> str:
    """Ausgang eines agent_task: explizite Abbildung → gleichnamiger Ausgang → Default.

    Der mittlere Schritt macht Graphen lesbar: wer eine Kante „loop_exhausted" zeichnet,
    bekommt sie auch, ohne outcomes_map pflegen zu müssen.
    """
    mapped = outcomes_map.get(status)
    if mapped:
        return mapped
    if next_node(edges, node_id, status) is not None:
        return status
    return _DEFAULT_AGENT_MAP.get(status, "err")


async def _agent_note(db, issue_id: int, status: str, summary: str, stalled: bool) -> None:
    """Kurze Spur jedes abgeschlossenen Laufs im Ticket-Verlauf (wer/was).

    „blocked" schreibt der Worker bereits selbst (Rückfrage/Berechtigung) — hier nicht doppeln.
    """
    from ..models.ticket import Comment
    note = None
    if status == "planned":
        note = "📋 Plan erstellt — bereit zur Freigabe." + (f"\n{summary}" if summary else "")
    elif status == "done":
        note = summary or "Arbeit abgeschlossen."
    elif status == "loop_exhausted":
        head = "⏸ Pausiert (Feststecker)" if stalled else "⏭ Zwischenstand, arbeite weiter"
        note = head + (f":\n{summary}" if summary else ".")
    elif status == "failed":
        note = f"❌ Fehlgeschlagen: {summary or 'unbekannter Fehler'}"
    if note:
        # `kind` trennt Arbeitsstand von Pannenprotokoll. Der Ticket-Verlauf zeigt beides,
        # der Prompt des nächsten Agenten nur den Arbeitsstand: eine Meldung über einen
        # Worker-Neustart oder einen Deadlock ist nichts, woran er weiterarbeiten könnte —
        # aber genau so hat er sie gelesen. Am 2026-08-07 setzte ein Agent daraufhin
        # „claude: Antwort bei max_tokens abgeschnitten – max_tokens erhöhen" als Aufgabe
        # um und schrieb dafür eine Eskalation in den Provider-Router; das Ticket ging über
        # den Job-Fehler, um den es eigentlich ging.
        art = "agent_fail" if status == "failed" else "agent"
        db.add(Comment(issue_id=issue_id, author_id=None, author_label="Agent",
                       body=note[:1500], kind=art))


async def _await_agent(instance_id: int, token_id: int, step_id: int, task_id: str,
                       outcomes_map: dict, timeout: int) -> None:
    """Wartet auf das Agent-Ergebnis, markiert den Schritt done + decision (Outcome-Handle),
    reaktiviert das Token und schaltet weiter. Der agent_task-Knoten schließt IMMER ab
    (done) — der Agent-Ausgang bestimmt nur den Zweig.

    Das Ergebnis landet zusätzlich unter `context.agent`, damit Entscheidungs-Knoten darauf
    prüfen können (Status, Zusammenfassung, Blocker-Art, Feststecker-Erkennung, Merge-Stand).
    """
    _WAECHTER.add(step_id)
    try:
        await _await_agent_inner(instance_id, token_id, step_id, task_id, outcomes_map, timeout)
    finally:
        _WAECHTER.discard(step_id)


async def _await_agent_inner(instance_id: int, token_id: int, step_id: int, task_id: str,
                             outcomes_map: dict, timeout: int) -> None:
    result, ersatz = await _warte(task_id, timeout, "Agent")
    status = (result or {}).get("status", "failed")

    async with SessionLocal() as db:
        step = await db.get(WorkflowStepRun, step_id)
        if step is None or step.status != SStatus.running:
            return  # schon anderweitig finalisiert (Reattach-Doppelung)
        inst = await db.get(WorkflowInstance, instance_id)
        version = await db.get(WorkflowVersion, inst.version_id) if inst else None
        edges = _edges((version.graph if version else None) or {})
        handle = _agent_handle(edges, step.node_id, status, outcomes_map)

        step.status = SStatus.done
        step.decision = handle
        step.result = ersatz
        # Ohne Ergebnis den GRUND hinschreiben, nicht bloß „Agent-Status: failed" — genau
        # diese Zeile hat die Ursachensuche bei ABC-2/ABC-6 in die Irre geführt.
        step.error = (None if status in ("done", "planned")
                      else f"Agent-Status: {status}" if result else ersatz["output"])
        step.completed_at = _now()
        step.agent_run_id = await _lookup_run_id(db, task_id)

        if inst is not None:
            ctx = dict(inst.context or {})
            summary = ersatz.get("summary") or ersatz.get("output") or ""
            stalled = False
            cont = int(ctx.get("continuation") or 0)
            if inst.issue_id:
                from ..models.ticket import Issue
                issue = await db.get(Issue, inst.issue_id)
                if issue is not None:
                    issue.agent_working = False
                    stalled = await _stalled(db, issue.id, (result or {}).get("worktree_fingerprint"))
                    if status == "planned":
                        # Der Plan ist das Artefakt des Laufs, keine Prozess-Entscheidung —
                        # er wird immer geschrieben, unabhängig vom gezeichneten Graphen.
                        issue.plan = (result or {}).get("output", "")
                    if (result or {}).get("merge_status") == "conflict":
                        issue.merge_status = "conflict"
                    if status == "loop_exhausted":
                        cont += 1
                        issue.continuation_count = cont
                        ctx["continuation_hint"] = summary[:2000]
                    await _agent_note(db, issue.id, status, summary, stalled)
            ctx["continuation"] = cont
            blocker = ((result or {}).get("blocker") or {}).get("kind")
            has_subtickets = "<subtickets>" in ((result or {}).get("output") or "")
            ctx["agent"] = {
                "status": status,
                "summary": summary[:2000],
                "run_id": (result or {}).get("run_id"),
                "blocker": blocker,
                "merge_status": (result or {}).get("merge_status") or "",
                "stalled": stalled,
                "continuation": cont,
                # Vorgekaute Werte für Entscheidungs-Knoten und Status-Vorlagen, damit
                # Graphen ohne Textanalyse auskommen:
                "has_subtickets": has_subtickets,
                "hold_hint": "plan_split" if has_subtickets else "plan_review",
                "stuck_reason": "stuck" if stalled else "cap",
                "blocker_reason": {"permission": "permission", "review": "review"}.get(
                    blocker or "", "question"),
                # Für den gemeinsamen Störungs-Knoten je Phase: welchen Zustand das Ticket
                # annimmt und warum. Ein Fehlschlag ist „failed" (ohne Grund), alles andere
                # ein „hold" mit passendem Grund — dieselben Anzeigen wie mit den früheren
                # Einzelknoten, nur an einer Stelle bestimmt.
                "hold_status": "failed" if status == "failed" else "hold",
                "hold_reason": (
                    "" if status == "failed"
                    else {"permission": "permission", "review": "review"}.get(blocker or "", "question")
                    if status == "blocked"
                    else ("stuck" if stalled else "cap") if status == "loop_exhausted"
                    else "plan_review" if status == "planned"
                    else "question"),
            }
            inst.context = ctx
            if inst.status == IStatus.waiting:
                inst.status = IStatus.running
        tok = await db.get(WorkflowToken, token_id)
        if tok is not None and tok.state == TState.waiting:
            tok.state = TState.active
            tok.waiting_for = None
        await db.commit()
    await advance(instance_id)


# ── Kern: advance ────────────────────────────────────────────────────────────

async def start_workflow(
    db, definition: WorkflowDefinition, *, subject_kind, issue_id: int | None = None,
    hardware_asset_id: int | None = None, context: dict | None = None,
    actor_id: int | None = None, source: str = "manual", source_ref: str | None = None,
    parent_instance_id: int | None = None, parent_node_id: str | None = None,
    advance_now: bool = True,
) -> WorkflowInstance:
    """Einziger Einstiegspunkt: legt Instanz + Start-Token an und schaltet einmal durch.

    Nutzt die übergebene Request-Session zum Anlegen; `advance` läuft danach in einer
    eigenen Session. `inst` wird am Ende neu geladen.
    """
    if definition.current_version_id is None:
        raise ValueError("Workflow hat keine veröffentlichte Version")
    version = await db.get(WorkflowVersion, definition.current_version_id)
    if version is None:
        raise ValueError("Version nicht gefunden")
    graph = version.graph or {}
    start = next((n for n in _nodes(graph) if node_type(n) == "start"), None)
    if start is None:
        raise ValueError("Graph hat keinen Start-Knoten")

    sk = subject_kind if isinstance(subject_kind, WorkflowSubjectKind) else WorkflowSubjectKind(subject_kind)
    # Vorlagen aus einem Satz sind projektlos — die Instanz gehört trotzdem zum Projekt
    # des Subjekts, sonst greifen Rechteprüfung, Live-Events und Zuständigen-Auflösung nicht.
    project_id = definition.project_id
    if project_id is None and issue_id is not None:
        from ..models.ticket import Issue
        subj = await db.get(Issue, issue_id)
        project_id = subj.project_id if subj else None
    if project_id is None and hardware_asset_id is not None:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, hardware_asset_id)
        project_id = asset.project_id if asset else None
    inst = WorkflowInstance(
        definition_id=definition.id, version_id=version.id, project_id=project_id,
        subject_kind=sk, issue_id=issue_id, hardware_asset_id=hardware_asset_id,
        status=IStatus.running, context=dict(context or {}),
        source=source, source_ref=source_ref, started_by=actor_id,
        parent_instance_id=parent_instance_id, parent_node_id=parent_node_id,
    )
    db.add(inst)
    await db.flush()
    db.add(WorkflowToken(instance_id=inst.id, node_id=start["id"], state=TState.active))
    # Der oberste Lauf eines Tickets ist sein Lebenszyklus — Ereignisse (Kommentar, Stopp)
    # und die UI finden ihn über issues.workflow_instance_id. Kind-Läufe (subflow) nicht.
    if issue_id is not None and parent_instance_id is None:
        from ..models.ticket import Issue
        subj = await db.get(Issue, issue_id)
        if subj is not None and definition.slot == "ticket_lifecycle":
            subj.workflow_instance_id = inst.id
    await db.commit()
    inst_id = inst.id
    if not advance_now:
        # Instanz steht bereit, läuft aber noch nicht los — für den Umstieg von
        # Bestandstickets, deren Token gezielt auf einen Warteknoten gesetzt wird.
        return inst
    await advance(inst_id)
    # Request-Session-Sicht auffrischen (advance hat in eigener Session committet)
    fresh = await db.get(WorkflowInstance, inst_id)
    if fresh is not None:
        await db.refresh(fresh)
    return fresh or inst


async def resume_instance(instance_id: int) -> None:
    """Reaktiviert das wartende Token einer Instanz und schaltet weiter (nach human/approval)."""
    async with SessionLocal() as db:
        inst = await db.get(WorkflowInstance, instance_id)
        if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
            return
        token = (
            await db.execute(
                select(WorkflowToken)
                .where(WorkflowToken.instance_id == instance_id,
                       WorkflowToken.state == TState.waiting)
                .with_for_update()
            )
        ).scalars().first()
        if token is not None:
            token.state = TState.active
            token.waiting_for = None
        inst.status = IStatus.running
        await db.commit()
    await advance(instance_id)


async def entscheide_genehmigung(db, inst: WorkflowInstance, decision: str, *,
                                 actor_id: int | None = None, reason: str | None = None,
                                 context: dict | None = None) -> bool:
    """Den wartenden Genehmigungs-Schritt einer Instanz entscheiden — ohne HTTP.

    Für Bedienwege, die nicht durch die API kommen (Telegram-Karte, native Werkzeuge im
    Worker). Liefert False, wenn gerade nichts zu entscheiden ist; der Aufrufer soll das
    sagen können, statt Erfolg zu melden.

    Committet NICHT und schaltet NICHT weiter: `advance` gehört in den Backend-Prozess,
    und der Aufrufer muss vorher committen — sonst sähe die eigene Sitzung der Engine die
    Entscheidung noch nicht.
    """
    if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
        return False
    step = (await db.execute(
        select(WorkflowStepRun).where(
            WorkflowStepRun.instance_id == inst.id,
            WorkflowStepRun.node_type == NType.approval,
            WorkflowStepRun.status == SStatus.waiting)
        .order_by(WorkflowStepRun.id.desc()))).scalars().first()
    if step is None:
        return False
    step.status = SStatus.done
    step.decision = decision
    step.result = {"reason": reason} if reason else None
    step.completed_by = actor_id
    step.completed_at = _now()
    token = (await db.execute(
        select(WorkflowToken).where(
            WorkflowToken.instance_id == inst.id,
            WorkflowToken.node_id == step.node_id,
            WorkflowToken.state == TState.waiting).with_for_update())).scalars().first()
    if token is not None:
        token.state = TState.active
        token.waiting_for = None
    if context:
        inst.context = {**(inst.context or {}), **context}
    inst.status = IStatus.running
    await db.flush()
    return True


async def advance(instance_id: int) -> None:
    """Schaltet das aktive Token synchron weiter bis wait/end.

    Atomarer `advancing`-Claim gegen Doppel-Advance; die eigentliche Ausführung läuft
    in einer eigenen Session (`_drive`). `advancing` wird garantiert zurückgesetzt.
    """
    async with SessionLocal() as db:
        claimed = (
            await db.execute(
                update(WorkflowInstance)
                .where(
                    WorkflowInstance.id == instance_id,
                    WorkflowInstance.advancing.is_(False),
                    WorkflowInstance.status.in_([IStatus.running, IStatus.waiting]),
                )
                .values(advancing=True)
                .returning(WorkflowInstance.id)
            )
        ).first()
        await db.commit()
    if claimed is None:
        return  # anderer Advance läuft bereits ODER Instanz bereits terminal
    try:
        # Erneut fahren, solange wieder ein Token aktiv ist: ein Agenten- oder
        # Aktions-Ergebnis kann eintreffen, WÄHREND dieser Durchlauf noch den Anspruch
        # hält (schnelle Queue, wieder-angebundener Lauf). Ohne diese Schleife bliebe die
        # Instanz bis zum nächsten 30-s-Tick stehen, obwohl alles bereit ist.
        for _ in range(MAX_DRIVE_ROUNDS):
            await _drive(instance_id)
            if not await _has_active_token(instance_id):
                break
    finally:
        async with SessionLocal() as db:
            await db.execute(
                update(WorkflowInstance).where(WorkflowInstance.id == instance_id)
                .values(advancing=False)
            )
            await db.commit()


async def _has_active_token(instance_id: int) -> bool:
    async with SessionLocal() as db:
        row = (await db.execute(
            select(WorkflowToken.id)
            .join(WorkflowInstance, WorkflowInstance.id == WorkflowToken.instance_id)
            .where(WorkflowToken.instance_id == instance_id,
                   WorkflowToken.state == TState.active,
                   WorkflowInstance.status.in_([IStatus.running, IStatus.waiting]))
            .limit(1))).first()
    return row is not None


async def _move(db, inst, token, edges, node, handle) -> bool:
    """Bewegt das Token entlang der Kante <node, handle>. False bei Dangling-Kante (Instanz failed)."""
    target = next_node(edges, node["id"], handle)
    if target is None:
        inst.status = IStatus.failed
        inst.error = f"Keine Kante von '{node['id']}' für Ausgang '{handle}'"
        inst.finished_at = _now()
        token.state = TState.consumed
        log.warning("Instanz %s: Dangling-Kante %s/%s", inst.id, node["id"], handle)
        return False
    token.node_id = target
    token.state = TState.active
    return True


async def _drive(instance_id: int) -> None:
    async with SessionLocal() as db:
        inst = await db.get(WorkflowInstance, instance_id)
        if inst is None or inst.status in (IStatus.completed, IStatus.failed, IStatus.cancelled):
            return
        version = await db.get(WorkflowVersion, inst.version_id)
        graph = version.graph or {} if version else {}
        edges = _edges(graph)
        # Ergebnis-Wächter werden erst nach dem Commit gestartet — sonst könnten sie einen
        # Schritt lesen wollen, den diese Session noch gar nicht festgeschrieben hat.
        spawn_after: list = []

        steps_taken = 0
        while steps_taken < MAX_STEPS:
            token = (
                await db.execute(
                    select(WorkflowToken)
                    .where(WorkflowToken.instance_id == inst.id, WorkflowToken.state == TState.active)
                    .with_for_update()
                )
            ).scalars().first()
            if token is None:
                break  # nichts aktiv → wartet oder fertig

            node = _node_by_id(graph, token.node_id)
            if node is None:
                inst.status = IStatus.failed
                inst.error = f"Knoten '{token.node_id}' fehlt im Graph"
                inst.finished_at = _now()
                token.state = TState.consumed
                break
            ntype = node_type(node)

            # Wiedereintritt: Ein Schritt, der fertig ist und eine Entscheidung trägt
            # (Genehmigung, Agent-Ausgang, Ereignis, Unter-Prozess, asynchrone Aktion),
            # bestimmt die Kante — der Knoten wird NICHT erneut ausgeführt. `routed_at`
            # stempelt das ab; ohne diesen Stempel würde eine Rückkante auf denselben
            # Knoten (Fortsetzungs-Schleife!) beim nächsten Durchlauf sofort wieder routen,
            # statt neu auszuführen. Synchrone Aktionen tragen keine Entscheidung und
            # laufen in einer Schleife bewusst erneut.
            last = await _latest_step(db, inst.id, token.node_id)
            if (last is not None and last.status == SStatus.done and last.routed_at is None
                    and (last.decision or ntype in WAIT_NODES)):
                # Ausgang laut Entscheidung, sonst Fallback auf „out" (der Editor zeichnet
                # oft nur einen out-Ausgang).
                dec = last.decision or "out"
                handle = dec if next_node(edges, token.node_id, dec) is not None else "out"
                last.routed_at = _now()
                if not await _move(db, inst, token, edges, node, handle):
                    break
                steps_taken += 1
                continue

            outcome = await _run_node(db, inst, node, ntype, token, edges, spawn_after)

            if outcome.terminal:
                token.state = TState.consumed
                inst.status = _to_instance_status(outcome.instance_status)
                inst.finished_at = _now()
                if outcome.error:
                    inst.error = outcome.error[:4000]
                break
            if outcome.wait:
                token.state = TState.waiting
                token.waiting_for = outcome.waiting_for
                inst.status = IStatus.waiting
                break
            if not await _move(db, inst, token, edges, node, outcome.handle):
                break
            steps_taken += 1
        else:
            # Zyklus-Bremse
            inst.status = IStatus.failed
            inst.error = f"Zyklus-Bremse: mehr als {MAX_STEPS} Schritte in einem Durchlauf"
            inst.finished_at = _now()
            actives = (
                await db.execute(
                    select(WorkflowToken).where(
                        WorkflowToken.instance_id == inst.id, WorkflowToken.state == TState.active)
                )
            ).scalars().all()
            for t in actives:
                t.state = TState.consumed
            log.warning("Instanz %s: MAX_STEPS erreicht → failed", inst.id)

        await db.commit()
        for watcher in spawn_after:
            _spawn(watcher)
        await publish_event(inst.project_id or 0, {
            "type": "workflow_update", "instance_id": inst.id, "status": inst.status.value,
        })
        # Kind-Lauf beendet → wartenden subflow-Schritt der Eltern-Instanz wecken.
        parent_id, parent_node = inst.parent_instance_id, inst.parent_node_id
        if parent_id and parent_node and inst.status in (
                IStatus.completed, IStatus.failed, IStatus.cancelled):
            await _finish_subflow(parent_id, parent_node, inst.status.value,
                                  dict(inst.context or {}), inst.error)


# ── Validierung ──────────────────────────────────────────────────────────────

def validate_graph(subject_kind, graph: dict) -> list[str]:
    """Prüft einen Graph. Gibt eine (evtl. leere) Liste deutscher Fehlermeldungen zurück."""
    errors: list[str] = []
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return ["Graph muss 'nodes' und 'edges' als Listen enthalten"]

    ids = [n.get("id") for n in nodes]
    dupes = {i for i in ids if ids.count(i) > 1 and i is not None}
    for d in sorted(dupes):
        errors.append(f"Doppelte Knoten-ID: '{d}'")
    id_set = {i for i in ids if i is not None}

    starts = [n for n in nodes if node_type(n) == "start"]
    ends = [n for n in nodes if node_type(n) == "end"]
    if len(starts) != 1:
        errors.append(f"Genau ein Start-Knoten erforderlich (gefunden: {len(starts)})")
    if len(ends) < 1:
        errors.append("Mindestens ein End-Knoten erforderlich")

    # Kanten referenzieren bekannte Knoten
    for e in edges:
        if e.get("source") not in id_set:
            errors.append(f"Kante mit unbekanntem source: '{e.get('source')}'")
        if e.get("target") not in id_set:
            errors.append(f"Kante mit unbekanntem target: '{e.get('target')}'")

    incoming: dict[str, int] = {i: 0 for i in id_set}
    outgoing: dict[str, int] = {i: 0 for i in id_set}
    for e in edges:
        if e.get("target") in outgoing:
            incoming[e["target"]] = incoming.get(e["target"], 0) + 1
        if e.get("source") in incoming:
            outgoing[e["source"]] = outgoing.get(e["source"], 0) + 1

    sk = subject_kind.value if isinstance(subject_kind, WorkflowSubjectKind) else str(subject_kind)

    for n in nodes:
        nid = n.get("id")
        ntype = node_type(n)
        if ntype != "start" and incoming.get(nid, 0) == 0:
            errors.append(f"Knoten '{nid}' hat keine eingehende Kante")
        if ntype != "end" and outgoing.get(nid, 0) == 0:
            errors.append(f"Knoten '{nid}' hat keine ausgehende Kante")

        if ntype == "approval":
            handles = _outgoing_handles(edges, nid)
            for req in ("approved", "rejected"):
                if req not in handles:
                    errors.append(f"Approval-Knoten '{nid}': Kante für Ausgang '{req}' fehlt")

        if ntype == "decision":
            cfg = node_config(n)
            handles = _outgoing_handles(edges, nid)
            branch_handles = {b.get("handle") for b in (cfg.get("branches") or [])}
            default_h = cfg.get("default_handle", "default")
            # Der Standard-Zweig MUSS einer der Zweige sein. Sonst zeigt der Knoten einen
            # Ausgang, den die Konfiguration nicht kennt — beim nächsten Bearbeiten wäre er
            # weg und die Kante hinge in der Luft.
            if cfg.get("branches") and default_h not in branch_handles:
                errors.append(
                    f"Decision-Knoten '{nid}': Standard-Zweig '{default_h}' ist keiner der "
                    f"Zweige ({', '.join(sorted(h for h in branch_handles if h))})")
            needed = {h for h in branch_handles if h} | {default_h}
            for h in sorted(needed):
                if h not in handles:
                    errors.append(f"Decision-Knoten '{nid}': Kante für Ausgang '{h}' fehlt")
            for b in cfg.get("branches") or []:
                guard = b.get("guard")
                if guard in (None, {}, True, ""):
                    continue
                unknown = collect_operators(guard) - ALLOWED_OPS
                for op in sorted(unknown):
                    errors.append(f"Decision-Knoten '{nid}': unbekannter JSONLogic-Operator '{op}'")

        if ntype == "agent_task" and sk != "issue":
            errors.append(f"agent_task-Knoten '{nid}' erfordert subject_kind=issue")

        if ntype == "subflow":
            cfg = node_config(n)
            slot = cfg.get("slot") or cfg.get("workflow_slot")
            if not slot:
                errors.append(f"Subflow-Knoten '{nid}': kein Ablauf (Slot) gewählt")
            else:
                from ..models.enums import WorkflowSlot
                if slot not in {s.value for s in WorkflowSlot}:
                    errors.append(f"Subflow-Knoten '{nid}': unbekannter Slot '{slot}'")

        if ntype == "wait_event":
            cfg = node_config(n)
            bad = [e for e in _accepted_events(cfg) if not str(e).strip()]
            if bad:
                errors.append(f"Ereignis-Knoten '{nid}': leerer Ereignisname")

    # Optional: Erreichbarkeit eines End-Knotens vom Start via BFS
    if starts and ends and not dupes:
        reach = _reachable(starts[0].get("id"), edges)
        if not any(e.get("id") in reach for e in ends):
            errors.append("Kein End-Knoten ist vom Start-Knoten erreichbar")

    return errors


def _reachable(start_id, edges: list[dict]) -> set[str]:
    seen = {start_id}
    stack = [start_id]
    while stack:
        cur = stack.pop()
        for e in edges:
            if e.get("source") == cur and e.get("target") not in seen:
                seen.add(e["target"])
                stack.append(e["target"])
    return seen


# ── Sicherheitsnetz-Loop (Crash-Recovery) ────────────────────────────────────

async def _retry_gated() -> list[int]:
    """Vertagte Agentenläufe (`waiting_for="gate"`) wieder scharf schalten.

    Das ersetzt den alten Dispatcher-Pickup: Tickets, die wegen Nacht-Fenster, Feierabend,
    Runner-Limit oder Cap nicht starten durften, kommen hier zyklisch erneut ans Tor.
    """
    async with SessionLocal() as db:
        tokens = (await db.execute(
            select(WorkflowToken)
            .join(WorkflowInstance, WorkflowInstance.id == WorkflowToken.instance_id)
            .where(
                WorkflowToken.state == TState.waiting,
                WorkflowToken.waiting_for == "gate",
                WorkflowInstance.status.in_([IStatus.running, IStatus.waiting]),
                WorkflowInstance.advancing.is_(False),
            )
            .order_by(WorkflowToken.updated_at)
            .limit(50))).scalars().all()
        ids = []
        for tok in tokens:
            tok.state = TState.active
            tok.waiting_for = None
            ids.append(tok.instance_id)
        if ids:
            await db.commit()
    return ids


async def nachzuegler_einsammeln() -> None:
    """Ergebnisse einsammeln, die NACH dem Aufgeben des Wächters doch noch eintreffen.

    Der Wächter gibt nur auf, wenn ein Lauf nachweislich verschwunden ist — aber „weg" heißt
    nicht „für immer weg": ein neu gestarteter Worker holt seinen Auftrag aus der
    Verarbeitungsliste zurück und legt Stunden später doch ein Ergebnis ab. Ohne diesen
    Einsammler wäre die Arbeit verloren: der Prozess stünde auf dem Störungs-Zweig, das
    Ticket auf „fehlgeschlagen", während der Branch die fertige Arbeit trägt.

    Statt die Verbuchung zu kopieren, wird der Schritt wieder auf „läuft" gesetzt, das Token
    auf seinen Knoten zurückgeholt und derselbe Wächter erneut angehängt — der findet das
    Ergebnis sofort in Redis und schaltet weiter, als wäre nie etwas gewesen.
    """
    wieder: list[tuple] = []
    async with SessionLocal() as db:
        kandidaten = (await db.execute(
            select(WorkflowStepRun)
            .join(WorkflowInstance, WorkflowInstance.id == WorkflowStepRun.instance_id)
            .where(WorkflowStepRun.status == SStatus.done,
                   WorkflowStepRun.node_type.in_([NType.agent_task, NType.auto_action]),
                   WorkflowInstance.status.in_([IStatus.running, IStatus.waiting]))
            .order_by(WorkflowStepRun.id.desc()).limit(200))).scalars().all()
        for s in kandidaten:
            res = s.result or {}
            if not res.get("verloren"):
                continue
            task_id = res.get("task_id")
            if not task_id or not await peek_result(task_id):
                continue
            # Läuft für diese Instanz schon wieder etwas, gilt das Neue — nicht der Nachzügler.
            laeuft = (await db.execute(
                select(WorkflowStepRun.id).where(
                    WorkflowStepRun.instance_id == s.instance_id,
                    WorkflowStepRun.status == SStatus.running).limit(1))).first()
            if laeuft:
                continue
            token = (await db.execute(
                select(WorkflowToken).where(
                    WorkflowToken.instance_id == s.instance_id,
                    WorkflowToken.state == TState.waiting).with_for_update())).scalars().first()
            if token is None:
                continue
            inst = await db.get(WorkflowInstance, s.instance_id)
            version = await db.get(WorkflowVersion, inst.version_id) if inst else None
            node = _node_by_id((version.graph if version else None) or {}, s.node_id)
            if inst is None or node is None:
                continue
            cfg = node_config(node)
            # Den Umweg über den Störungs-Zweig zurückbauen: wartende Schritte verwerfen,
            # den Agenten-Schritt wieder scharf machen.
            for w in (await db.execute(
                    select(WorkflowStepRun).where(
                        WorkflowStepRun.instance_id == s.instance_id,
                        WorkflowStepRun.status == SStatus.waiting))).scalars().all():
                w.status = SStatus.skipped
                w.completed_at = _now()
            s.status = SStatus.running
            s.decision = None
            s.error = None
            s.routed_at = None
            s.completed_at = None
            s.result = {**res, "verloren": False, "nachgetragen": True}
            token.node_id = s.node_id
            token.state = TState.waiting
            token.waiting_for = "agent" if s.node_type == NType.agent_task else "action"
            inst.status = IStatus.waiting
            if inst.issue_id:
                from .comments import add_system_comment
                await add_system_comment(
                    db, inst.issue_id,
                    "↩ Das Ergebnis des verloren geglaubten Laufs ist doch noch eingetroffen "
                    "— der Prozess läuft an dieser Stelle weiter.", author_label="Workflow")
            omap = dict(cfg.get("outcomes_map") or {})
            if s.node_type == NType.agent_task:
                wieder.append(("agent", s.instance_id, token.id, s.id, task_id, omap,
                               int(cfg.get("timeout_sec") or AGENT_DEFAULT_TIMEOUT), ""))
            else:
                wieder.append(("aktion", s.instance_id, token.id, s.id, task_id, omap,
                               int(cfg.get("timeout_sec") or ACTION_DEFAULT_TIMEOUT),
                               str(res.get("context_key") or "action")))
        if wieder:
            await db.commit()
    for art, iid, tok_id, step_id, task_id, omap, timeout, ckey in wieder:
        log.info("Nachzügler: Ergebnis für %s doch noch da → Schritt %s wird verbucht",
                 task_id, step_id)
        if art == "agent":
            _spawn(_await_agent(iid, tok_id, step_id, task_id, omap, timeout))
        else:
            _spawn(_await_action(iid, tok_id, step_id, task_id, timeout, ckey, omap))


async def tote_laeufe_schliessen() -> int:
    """Läufe abschließen, hinter denen niemand mehr steht.

    Ein Lauf endet normalerweise selbst — außer der Prozess, der ihn führt, stirbt an einer
    Stelle, an der er nicht mehr schreiben kann (Lauf 753 am 2026-08-07: Deadlock beim
    Schreiben der Schrittzeile, die Sitzung war danach unbrauchbar, also blieb die Zeile
    für immer auf „läuft"). Die Aufräumung beim Worker-Start greift erst beim nächsten
    Neustart — bis dahin zählt der Lauf als lebend und hält über die Board-Regel („wer
    arbeitet, steht auf In Arbeit") ein Ticket in der Arbeit, das in Wahrheit wartet.

    Geprüft wird das echte Lebenszeichen, nicht die Uhr: Puls, Warteschlange,
    Verarbeitungsliste. Erst wenn keine dieser Quellen den Auftrag kennt UND die Gnadenfrist
    abgelaufen ist, wird geschlossen — ein Agent darf Stunden brauchen.
    """
    import datetime as _dt

    from ..core.redis import GNADENFRIST, lauf_lebt
    from ..models.agents import Run

    grenze = _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=GNADENFRIST)
    geschlossen = 0
    async with SessionLocal() as db:
        offen = (await db.execute(select(Run).where(
            Run.status == "running", Run.finished_at.is_(None),
            Run.started_at < grenze))).scalars().all()
        for run in offen:
            if await lauf_lebt(run.task_id):
                continue
            run.status = "failed"
            run.finished_at = _dt.datetime.now(_dt.UTC)
            run.error = ((run.error or "") +
                         "Kein Lebenszeichen mehr: der Lauf wurde abgebrochen, ohne sich "
                         "abmelden zu können (z. B. Absturz beim Schreiben).").strip()
            log.warning("Lauf %s (%s, Auftrag %s) ohne Lebenszeichen geschlossen",
                        run.id, run.agent, run.task_id)
            geschlossen += 1
        if geschlossen:
            await db.commit()
    return geschlossen


async def _engine_tick() -> None:
    # Zuerst die Toten schließen, dann abgleichen: sonst hält ein Lauf, der nur noch auf dem
    # Papier läuft, sein Ticket über die Board-Regel in der Arbeit fest.
    try:
        await tote_laeufe_schliessen()
    except Exception:  # noqa: BLE001 — darf den Tick nie blockieren
        log.exception("Schließen toter Läufe fehlgeschlagen")

    # Verlorene Wächter wieder anbinden. Ein Wächter lebt im Backend-Prozess; geht er
    # verloren (Reload, Ausnahme, hängende Verbindung), wartet niemand mehr auf das
    # Ergebnis — und das Ticket steht, ohne dass es jemandem auffällt.
    try:
        await recover_workflow_agents()
    except Exception:  # noqa: BLE001 — darf den Tick nie blockieren
        log.exception("Wiederanbinden der Wächter fehlgeschlagen")

    # Artefakt-Zeilen angleichen: `agent_status` wird an vielen Stellen gesetzt (Endpunkte,
    # Bot, PM-Chat, Worker) — der Abgleich holt das binnen eines Ticks nach, statt jede
    # dieser Stellen einzeln pflegen zu müssen.
    try:
        from .artifacts import reconcile
        async with SessionLocal() as db:
            await reconcile(db)
    except Exception:  # noqa: BLE001 — der Abgleich darf den Tick nie blockieren
        log.exception("Artefakt-Abgleich fehlgeschlagen")

    try:
        await nachzuegler_einsammeln()
    except Exception:  # noqa: BLE001 — darf den Tick nie blockieren
        log.exception("Nachzügler-Abholung fehlgeschlagen")

    # Tickets ohne Prozess-Instanz einsammeln. Das lief bisher NUR beim Backend-Start, und
    # damit war ein Ticket, das zwischendurch verwaiste, bis zum nächsten Neustart tot —
    # die unangenehmste Sorte Fehler, weil der Neustart ihn behebt und die Ursache verdeckt
    # (ABC-32 am 2026-08-07: vom Assistenten zugewiesen, nie gestartet).
    try:
        from .lifecycle_flow import adopt_orphans
        async with SessionLocal() as db:
            n = await adopt_orphans(db)
        if n:
            log.info("Tick: %d verwaiste(s) Ticket(s) in den Lebenszyklus geholt", n)
    except Exception:  # noqa: BLE001 — darf den Tick nie blockieren
        log.exception("Einsammeln verwaister Tickets fehlgeschlagen")

    gated = await _retry_gated()
    async with SessionLocal() as db:
        ids = (
            await db.execute(
                select(WorkflowInstance.id)
                .join(WorkflowToken, WorkflowToken.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.status.in_([IStatus.running, IStatus.waiting]),
                    WorkflowInstance.advancing.is_(False),
                    WorkflowToken.state == TState.active,
                )
                .distinct()
                .limit(50)
            )
        ).scalars().all()
    for iid in dict.fromkeys([*gated, *ids]):
        try:
            await advance(iid)
        except Exception:  # noqa: BLE001
            log.exception("workflow advance failed für Instanz %s", iid)


async def recover_workflow_agents() -> None:
    """Laufende agent_task- und Aktions-Schritte wieder an ihren Redis-Lauf anbinden.

    `_await_agent`/`_await_action` greifen ein bereits vorliegendes Ergebnis via wait_result
    sofort ab, sonst warten sie weiter — sonst hinge ein Ticket nach einem simplen
    Backend-Reload für immer im „läuft"-Zustand.

    Läuft beim Start UND in jedem Tick. Nur beim Start reichte nicht: ein Wächter kann auch
    im laufenden Betrieb verloren gehen — am 2026-08-07 hing einer in einer halb toten
    Redis-Verbindung fest, das fertige Ergebnis für ABC-31 lag ab 19:54 unabgeholt in Redis,
    und das Ticket stand eine Stunde still, ohne dass irgendetwas es bemerkte. Wer schon
    einen Wächter hat, bekommt keinen zweiten (`_WAECHTER`).
    """
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.status == SStatus.running,
                       WorkflowStepRun.node_type.in_([NType.agent_task, NType.auto_action]))
            )
        ).scalars().all()
        agents, actions = [], []
        for s in rows:
            task_id = (s.result or {}).get("task_id")
            if not task_id or s.token_id is None or s.id in _WAECHTER:
                continue      # es wartet schon einer — kein zweiter auf dasselbe Ergebnis
            inst = await db.get(WorkflowInstance, s.instance_id)
            if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
                continue
            version = await db.get(WorkflowVersion, inst.version_id)
            node = _node_by_id(version.graph or {}, s.node_id) if version else None
            cfg = node_config(node) if node else {}
            omap = dict(cfg.get("outcomes_map") or {})
            if s.node_type == NType.agent_task:
                agents.append((s.instance_id, s.token_id, s.id, task_id, omap,
                               int(cfg.get("timeout_sec") or AGENT_DEFAULT_TIMEOUT)))
            else:
                actions.append((s.instance_id, s.token_id, s.id, task_id,
                                int(cfg.get("timeout_sec") or ACTION_DEFAULT_TIMEOUT), omap,
                                str((s.result or {}).get("context_key") or "action")))
    for instance_id, token_id, step_id, task_id, omap, timeout in agents:
        log.info("workflow reattach: agent-Schritt %s (task %s)", step_id, task_id)
        _spawn(_await_agent(instance_id, token_id, step_id, task_id, omap, timeout))
    for instance_id, token_id, step_id, task_id, timeout, omap, ckey in actions:
        log.info("workflow reattach: Aktions-Schritt %s (task %s)", step_id, task_id)
        _spawn(_await_action(instance_id, token_id, step_id, task_id, timeout,
                                          ckey, omap))


async def run_workflow_engine() -> None:
    """30s-Loop: findet hängengebliebene running-Instanzen mit aktivem Token und schaltet
    sie weiter (Sicherheitsnetz nach Crash/Neustart; Normalbetrieb läuft synchron)."""
    log.info("workflow-engine gestartet (tick=%ss)", TICK_SECONDS)
    try:
        await recover_workflow_agents()
    except Exception:  # noqa: BLE001
        log.exception("recover_workflow_agents fehlgeschlagen")
    await asyncio.sleep(7)
    while True:
        try:
            await _engine_tick()
        except Exception:  # noqa: BLE001
            log.exception("workflow-engine tick failed")
        await asyncio.sleep(TICK_SECONDS)
