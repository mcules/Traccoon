"""Generische Workflow-Ausführungs-Engine (Token-basiert, React-Flow-Graph).

Läuft NEBEN dem KI-Ticket-Lifecycle (dispatcher.py). Ein Workflow ist ein Node-Graph
(`version.graph = {"nodes":[...], "edges":[...]}`); eine Instanz trägt genau EIN aktives
Token, das synchron von Knoten zu Knoten geschaltet wird, bis es wartet (human_task/
approval) oder ein end-Knoten erreicht ist.

Absicherungen analog dispatcher.py:
- atomarer `advancing`-Claim (UPDATE … WHERE advancing=false RETURNING) gegen Doppel-Advance
  (Tick ↔ Request-Event),
- `MAX_STEPS`-Bremse gegen zyklische Auto-Advance,
- eigene DB-Session (SessionLocal), niemals die Request-Session.

agent_task ist in v1 bewusst NICHT ausführbar (Etappe 3): der Handler markiert den Schritt
als fehlgeschlagen und beendet die Instanz, damit v1 lauffähig bleibt.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
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
from ..core.redis import enqueue_task, publish_event, wait_result
from .jsonlogic import ALLOWED_OPS, JsonLogicError, collect_operators, safe_eval

log = logging.getLogger("workflow_engine")

MAX_STEPS = 50          # Zyklus-Bremse pro advance-Durchlauf
TICK_SECONDS = 30       # Sicherheitsnetz-Loop (Crash-Recovery)

# Knoten, die auf ein externes Ereignis warten. Beim Wiedereintritt (Token wieder aktiv)
# NICHT erneut ausführen, sondern die Kante gemäß hinterlegter decision nehmen.
WAIT_NODES = ("human_task", "approval", "agent_task")

# Standard-Abbildung Worker-Ergebnis → Ausgang (falls node.config.outcomes_map nichts sagt).
_DEFAULT_AGENT_MAP = {
    "planned": "ok", "done": "ok", "failed": "err",
    "blocked": "blocked", "loop_exhausted": "blocked",
}
AGENT_DEFAULT_TIMEOUT = 1800


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
    """In-App/Telegram-Benachrichtigung an den Zuständigen + Ticket-Notiz (falls Issue)."""
    cfg = node_config(node)
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

async def _run_node(db, inst, node, ntype, token, edges) -> Outcome:
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
        try:
            result = await run_action(db, inst, node)
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
        return await _start_agent_task(db, inst, node, token, cfg)

    return Outcome(terminal=True, instance_status="failed",
                   error=f"Unbekannter Knotentyp '{ntype}'")


# ── agent_task: Brücke zur bestehenden Agent-Queue (Worker) ──────────────────

async def _start_agent_task(db, inst, node, token, cfg) -> Outcome:
    """Startet einen KI-Agenten-Lauf über dieselbe Redis-Queue wie der Dispatcher und
    wartet asynchron auf das Ergebnis (Token wartet, `_await_agent` schaltet weiter).

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

    role = cfg.get("agent_role") or "developer"
    phase = "planning" if cfg.get("phase") == "planning" else "execution"
    timeout = int(cfg.get("timeout_sec") or AGENT_DEFAULT_TIMEOUT)
    outcomes_map = cfg.get("outcomes_map") or {}
    task_id = f"wf-{inst.id}-{token.id}-{node['id']}-{uuid.uuid4().hex[:8]}"

    step = WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType.agent_task, status=SStatus.running,
        assignee_user_id=None, result={"task_id": task_id},  # task_id für Reattach hinterlegt
    )
    db.add(step)
    await db.flush()

    payload = {
        "task_id": task_id, "issue_id": issue.id, "issue_key": issue.key,
        "project_id": inst.project_id, "role": role, "phase": phase,
        "continuation_index": 0, "continuation_hint": "",
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
    # Ergebnis-Wächter als eigene Task (eigene Session). Bei Timeout/Neustart übernimmt der Tick.
    asyncio.create_task(_await_agent(inst.id, token.id, step.id, task_id, dict(outcomes_map), timeout))
    return Outcome(wait=True, waiting_for="agent")


async def _lookup_run_id(db, task_id: str) -> int | None:
    from ..models.agents import Run
    r = (
        await db.execute(select(Run).where(Run.task_id == task_id).order_by(Run.id.desc()))
    ).scalars().first()
    return r.id if r else None


async def _await_agent(instance_id: int, token_id: int, step_id: int, task_id: str,
                       outcomes_map: dict, timeout: int) -> None:
    """Wartet auf das Agent-Ergebnis, markiert den Schritt done + decision (Outcome-Handle),
    reaktiviert das Token und schaltet weiter. Der agent_task-Knoten schließt IMMER ab
    (done) — der Agent-Ausgang bestimmt nur den Zweig."""
    try:
        result = await wait_result(task_id, timeout=timeout)
    except Exception:  # noqa: BLE001
        log.exception("wait_result für %s fehlgeschlagen", task_id)
        result = None
    status = (result or {}).get("status", "failed")
    handle = outcomes_map.get(status) or _DEFAULT_AGENT_MAP.get(status, "err")

    async with SessionLocal() as db:
        step = await db.get(WorkflowStepRun, step_id)
        if step is None or step.status != SStatus.running:
            return  # schon anderweitig finalisiert (Reattach-Doppelung)
        step.status = SStatus.done
        step.decision = handle
        step.result = result or {"status": "failed", "output": "kein Ergebnis (Timeout)"}
        step.error = None if status in ("done", "planned") else f"Agent-Status: {status}"
        step.completed_at = _now()
        step.agent_run_id = await _lookup_run_id(db, task_id)
        tok = await db.get(WorkflowToken, token_id)
        if tok is not None and tok.state == TState.waiting:
            tok.state = TState.active
            tok.waiting_for = None
        inst = await db.get(WorkflowInstance, instance_id)
        if inst is not None and inst.status == IStatus.waiting:
            inst.status = IStatus.running
        await db.commit()
    await advance(instance_id)


# ── Kern: advance ────────────────────────────────────────────────────────────

async def start_workflow(
    db, definition: WorkflowDefinition, *, subject_kind, issue_id: int | None = None,
    hardware_asset_id: int | None = None, context: dict | None = None,
    actor_id: int | None = None, source: str = "manual", source_ref: str | None = None,
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
    inst = WorkflowInstance(
        definition_id=definition.id, version_id=version.id, project_id=definition.project_id,
        subject_kind=sk, issue_id=issue_id, hardware_asset_id=hardware_asset_id,
        status=IStatus.running, context=dict(context or {}),
        source=source, source_ref=source_ref, started_by=actor_id,
    )
    db.add(inst)
    await db.flush()
    db.add(WorkflowToken(instance_id=inst.id, node_id=start["id"], state=TState.active))
    await db.commit()
    inst_id = inst.id
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
        await _drive(instance_id)
    finally:
        async with SessionLocal() as db:
            await db.execute(
                update(WorkflowInstance).where(WorkflowInstance.id == instance_id)
                .values(advancing=False)
            )
            await db.commit()


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

            # Wiedereintritt: erledigtes human_task/approval → Kante gemäß decision nehmen,
            # OHNE den Knoten erneut auszuführen (kein neuer Wait).
            if ntype in WAIT_NODES:
                last = await _latest_step(db, inst.id, token.node_id)
                if last is not None and last.status == SStatus.done:
                    if ntype == "human_task":
                        handle = "out"
                    elif ntype == "agent_task":
                        # Outcome-Handle falls verdrahtet, sonst Fallback auf „out"
                        # (v1-Editor zeichnet oft nur einen out-Ausgang).
                        dec = last.decision or "out"
                        handle = dec if next_node(edges, token.node_id, dec) is not None else "out"
                    else:  # approval
                        handle = last.decision or "out"
                    if not await _move(db, inst, token, edges, node, handle):
                        break
                    steps_taken += 1
                    continue

            outcome = await _run_node(db, inst, node, ntype, token, edges)

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
        await publish_event(inst.project_id or 0, {
            "type": "workflow_update", "instance_id": inst.id, "status": inst.status.value,
        })


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

async def _engine_tick() -> None:
    async with SessionLocal() as db:
        ids = (
            await db.execute(
                select(WorkflowInstance.id)
                .join(WorkflowToken, WorkflowToken.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.status == IStatus.running,
                    WorkflowInstance.advancing.is_(False),
                    WorkflowToken.state == TState.active,
                )
                .distinct()
                .limit(50)
            )
        ).scalars().all()
    for iid in ids:
        try:
            await advance(iid)
        except Exception:  # noqa: BLE001
            log.exception("workflow advance failed für Instanz %s", iid)


async def recover_workflow_agents() -> None:
    """Nach Backend-Neustart: wartende agent_task-Schritte (StepRun.running) wieder an ihren
    Redis-Lauf anbinden. `_await_agent` greift ein bereits vorliegendes Ergebnis via
    wait_result sofort ab, sonst wartet es weiter — 1:1 die recover_on_start-Philosophie
    des Dispatchers."""
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(WorkflowStepRun)
                .where(WorkflowStepRun.status == SStatus.running,
                       WorkflowStepRun.node_type == NType.agent_task)
            )
        ).scalars().all()
        pending = []
        for s in rows:
            task_id = (s.result or {}).get("task_id")
            if not task_id or s.token_id is None:
                continue
            inst = await db.get(WorkflowInstance, s.instance_id)
            if inst is None or inst.status not in (IStatus.running, IStatus.waiting):
                continue
            version = await db.get(WorkflowVersion, inst.version_id)
            node = _node_by_id(version.graph or {}, s.node_id) if version else None
            cfg = node_config(node) if node else {}
            pending.append((s.instance_id, s.token_id, s.id, task_id,
                            dict(cfg.get("outcomes_map") or {}),
                            int(cfg.get("timeout_sec") or AGENT_DEFAULT_TIMEOUT)))
    for instance_id, token_id, step_id, task_id, omap, timeout in pending:
        log.info("workflow reattach: agent-Schritt %s (task %s)", step_id, task_id)
        asyncio.create_task(_await_agent(instance_id, token_id, step_id, task_id, omap, timeout))


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
