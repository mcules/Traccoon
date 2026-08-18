"""Execution engine for processes (token based, node graph).

Everything Traccoon runs as a flow goes through here, including the AI ticket lifecycle
that used to be wired into the dispatcher. A process is a node graph
(`version.graph = {"nodes":[...], "edges":[...]}`), and an instance carries exactly ONE
active token that moves from node to node until it waits (person, approval, agent run,
event, subprocess) or reaches an end node.

Safeguards:
- atomic `advancing` claim (UPDATE ... WHERE advancing=false RETURNING) against a double
  advance (tick against request event),
- `MAX_STEPS` brake against cyclic auto advance,
- `routed_at` per step: a finished wait node is translated into an edge exactly ONCE.
  Without it a back edge (continuation) spun in circles forever,
- gatekeeper before every agent run (`services/agent_gate.py`): time windows, runner limit
  and runaway brake apply no matter how the graph is drawn,
- its own database session (SessionLocal), never the request session.
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

# Cycle brake per advance pass. Higher than the old 50 since loops exist: a `loop` over 120
# rows is not a spinning instance but work, and would otherwise only progress in 30 second
# waves of the tick. Against real endless loops there is additionally the
# `max` am Schleifen-Knoten selbst.
MAX_STEPS = 200
MAX_DRIVE_ROUNDS = 5    # follow up when a result arrived during the pass
TICK_SECONDS = 30       # Sicherheitsnetz-Loop (Crash-Recovery)
MAX_SUBFLOW_DEPTH = 3   # nesting brake for subflow nodes

# Nodes that wait for an external event. On re-entry (token active again) do NOT execute
# them again, take the edge according to the stored decision instead, and only ONCE per
# pass (`routed_at`), otherwise a back edge spins in circles forever.
WAIT_NODES = ("human_task", "approval", "agent_task", "wait_event", "subflow", "timer")

# Default mapping from worker result to outlet. Only used when neither `outcomes_map` nor an
# outlet of the same name (for example "loop_exhausted") is wired on the node.
_DEFAULT_AGENT_MAP = {
    "planned": "ok", "done": "ok", "failed": "err",
    "blocked": "blocked", "loop_exhausted": "blocked",
}
# Hard cap for waiting on an agent run: NONE by default.
#
# Until 2026-08-05 this was 1800 s and the watcher gave up after 30 minutes although the run
# kept working. An exec step covers implementation AND review rounds in ONE job, which
# regularly takes longer. The result was a ticket reading "failed: unknown error" while the
# agent committed cleanly moments later (ABC-2, ABC-6). Waiting now happens on the sign of
# life of the run (`wait_result`), not on the clock. Whoever wants a limit for a single node
# sets `timeout_sec` in its config, AGENT_WAIT_LIMIT_SEC is the global emergency brake
# (0 = off).
AGENT_DEFAULT_TIMEOUT = int(os.getenv("AGENT_WAIT_LIMIT_SEC", "0"))
# Cap for asynchronous auto actions (merge, test environment). Those are short and bounded,
# so a clock still makes sense here, but a generous one: a preview build takes its time.
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
    """Edge matches an outlet. "out" and None are interchangeable (the default outlet)."""
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


# Watcher tasks (agent run, asynchronous action) need a strong reference, otherwise the
# garbage collector may take them mid-wait and the process would silently stall. Tests wait
# for them through `drain()`.
_BACKGROUND: set[asyncio.Task] = set()


# Step ids a watcher in THIS process is currently waiting for. Without that knowledge
# reattaching (see `recover_workflow_agents`) could put a second watcher on the same result,
# and both would advance.
_WAECHTER: set[int] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def drain(timeout: float = 5.0) -> None:
    """Wait for all running watchers (tests only, not needed in production)."""
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


# -- step and assignee helpers ------------------------------------------------

async def _latest_step(db, instance_id: int, node_id: str) -> WorkflowStepRun | None:
    return (
        await db.execute(
            select(WorkflowStepRun)
            .where(WorkflowStepRun.instance_id == instance_id, WorkflowStepRun.node_id == node_id)
            .order_by(WorkflowStepRun.id.desc())
        )
    ).scalars().first()


async def _resolve_assignee(db, inst: WorkflowInstance, cfg: dict) -> int | None:
    """Resolve who is responsible for a human_task or approval.

    config["assignee"] = {mode, ...}:
      user     -> {user_id}
      role     -> {role}         (first project member with that role, else project lead)
      context  -> {path}         (user id from instance.context by dot path)
      reporter -> reporter of the bound issue
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
    """Notify the responsible person and add a ticket note when there is an issue.

    `config.notify = false` turns it off, for wait points whose question was already asked
    another way (the spam question brings its own card with buttons, a second message
    without buttons about the same mail would be noise).
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
    """Create a waiting StepRun (idempotent) and notify the responsible person."""
    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.waiting:
        return  # already waiting, do not create or notify twice
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


# -- dry run -----------------------------------------------------------------
# A flow could be built but not tried out: whether an expression is right, whether the
# decision leads into the intended branch, showed only on the first real run, with all its
# effects on the outside world. In a dry run the graph runs through completely, but every
# action only reports what it WOULD do.
PROBE_KEY = "_probe"
# What may run in a dry run as well: both stay inside the context of this run. Without them
# the trial would be worthless, later steps would have no data and decisions would test air.
PROBE_ERLAUBT = ("set_context", "refresh_facts", "noop")


def _ist_probe(inst) -> bool:
    return bool((inst.context or {}).get(PROBE_KEY))


def _probe_schritt(db, inst, node, token, ntype: str, text: str, decision: str | None = None):
    db.add(WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"], node_type=NType(ntype),
        status=SStatus.done, completed_at=_now(), decision=decision,
        result={"probe": text}))


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
        if _ist_probe(inst):
            _probe_schritt(db, inst, node, token, ntype, "würde auf einen Menschen warten")
            return Outcome(handle="out")
        await _ensure_wait_step(db, inst, node, ntype, token, "human_task")
        return Outcome(wait=True, waiting_for="human_task")

    if ntype == "approval":
        if _ist_probe(inst):
            # The dry run takes the approved path, which is the one you want to see. The
            # rejected one deserves a trial of its own and is not checked in secret.
            _probe_schritt(db, inst, node, token, ntype,
                           "würde auf eine Freigabe warten (Probe nimmt „genehmigt\")",
                           decision="approved")
            return Outcome(handle="approved")
        await _ensure_wait_step(db, inst, node, ntype, token, "approval")
        return Outcome(wait=True, waiting_for="approval")

    if ntype == "auto_action":
        from .workflow_actions import run_action
        if _ist_probe(inst):
            from .workflow_actions import _normalize_action
            name, params = _normalize_action(cfg)
            if name not in PROBE_ERLAUBT:
                ziel = params.get("tool") or params.get("destination") or params.get("status") \
                    or params.get("agent") or ""
                _probe_schritt(db, inst, node, token, "auto_action",
                               f"würde ausführen: {name}" + (f" ({ziel})" if ziel else ""))
                return Outcome(handle="out")
        # Idempotency: an asynchronous action (a merge, say) is already running, do not restart.
        running = await _latest_step(db, inst.id, node["id"])
        if running is not None and running.status == SStatus.running:
            return Outcome(wait=True, waiting_for="action")
        try:
            result = await run_action(db, inst, node)
            wait_spec = result.pop("_wait", None) if isinstance(result, dict) else None
            if wait_spec:
                # Asynchronous action: the step stays running, a watcher advances it.
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
            # Retry first, then branch, then give up. A failure towards the outside is
            # usually not one of the matter but of the moment: far side briefly gone,
            # network briefly gone. Without a delay the retry would be pointless (same
            # second, same error), so it waits on the same alarm clock a timer uses.
            versuche = int(cfg.get("wiederholungen") or 0)
            if versuche > 0:
                zaehler = dict((inst.context or {}).get("_versuche") or {})
                bisher = int(zaehler.get(node["id"], 0))
                if bisher < versuche:
                    zaehler[node["id"]] = bisher + 1
                    inst.context = {**(inst.context or {}), "_versuche": zaehler}
                    warte = float(cfg.get("warte_sek") or 30)
                    db.add(WorkflowStepRun(
                        instance_id=inst.id, token_id=token.id, node_id=node["id"],
                        node_type=NType.timer, status=SStatus.waiting,
                        result={"faellig": (_now() + dt.timedelta(seconds=warte)).isoformat(),
                                "versuch": bisher + 1, "von": versuche}))
                    await db.flush()
                    log.info("Instanz %s: %s scheitert (%d/%d) — neuer Versuch in %.0fs",
                             inst.id, node["id"], bisher + 1, versuche, warte)
                    return Outcome(wait=True, waiting_for="timer")
                # Used up: the counter has to go, otherwise the next attempt (loop,
                # restart) would continue from the old count.
                zaehler.pop(node["id"], None)
                inst.context = {**(inst.context or {}), "_versuche": zaehler}
            if next_node(edges, node["id"], "error") is not None:
                return Outcome(handle="error")
            return Outcome(terminal=True, instance_status="failed",
                           error=f"auto_action '{node['id']}' fehlgeschlagen: {e}")

    if ntype == "agent_task":
        if _ist_probe(inst):
            _probe_schritt(db, inst, node, token, ntype,
                           f"würde den Agenten „{cfg.get('agent_role') or '?'}\" starten",
                           decision="done")
            return Outcome(handle="done")
        return await _start_agent_task(db, inst, node, token, cfg, spawn_after)

    if ntype == "wait_event":
        if _ist_probe(inst):
            _probe_schritt(db, inst, node, token, ntype,
                           f"würde warten auf: {', '.join(_accepted_events(cfg))}")
            return Outcome(handle="out")
        return await _wait_for_event(db, inst, node, token, cfg)

    if ntype == "subflow":
        if _ist_probe(inst):
            _probe_schritt(db, inst, node, token, ntype,
                           f"würde den Ablauf „{cfg.get('slot') or '?'}\" aufrufen",
                           decision="completed")
            return Outcome(handle="completed")
        return await _start_subflow(db, inst, node, token, cfg)

    if ntype == "loop":
        return await _schleife(db, inst, node, token, cfg)

    if ntype == "timer":
        if _ist_probe(inst):
            bis = cfg.get("bis") or f"{cfg.get('dauer', '?')} {cfg.get('einheit', 'm')}"
            _probe_schritt(db, inst, node, token, ntype, f"würde warten: {bis}")
            return Outcome(handle="out")
        return await _timer(db, inst, node, token, cfg)

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
    """Hold the run until `resume_on_event` reports a matching event.

    That way questions, rejected plans and failed attempts in the ticket lifecycle hang off
    a person's comment instead of restarting through a hidden status jump.
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


# ── timer: Zeit vergehen lassen ──────────────────────────────────────────────

async def _timer(db, inst, node, token, cfg) -> Outcome:
    """Wait for a while, without anybody having to report anything.

    `wait_event` waits for an event, here the run waits for the clock. That is needed in
    two places: "look again later" (the far side only delivers in an hour) and as the delay
    between two attempts. Without it every retry would be an immediate retry: same far
    side, same second, same error.

    The wake-up happens in the engine's 30 second tick (`_faellige_timer`), not in a
    sleeping task: a backend restart must not forget a waiting run.
    """
    vorhanden = await _latest_step(db, inst.id, node["id"])
    if vorhanden is not None and vorhanden.status == SStatus.waiting:
        return Outcome(wait=True, waiting_for="timer")

    faellig = _faellig_ab(cfg, inst.context or {})
    db.add(WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType.timer, status=SStatus.waiting,
        result={"faellig": faellig.isoformat()}))
    await db.flush()
    log.info("Instanz %s wartet bis %s (%s)", inst.id, faellig.isoformat(), node["id"])
    return Outcome(wait=True, waiting_for="timer")


def _faellig_ab(cfg: dict, ctx: dict) -> dt.datetime:
    """When the run continues: a duration from now or a fixed point in time.

    The point in time may come from the context (`{{…}}` is already filled here when the
    editor writes it that way). If it lies in the past the run continues at once instead of
    never.
    """
    jetzt = _now()
    bis = str(cfg.get("bis") or "").strip()
    if bis:
        from .workflow_expr import fuellen
        roh = fuellen(bis, ctx) if "{{" in bis else bis
        try:
            ziel = dt.datetime.fromisoformat(roh.replace("Z", "+00:00"))
            if ziel.tzinfo is None:
                ziel = ziel.replace(tzinfo=dt.timezone.utc)
            return max(ziel, jetzt)
        except ValueError:
            log.warning("Timer: %r ist kein Zeitpunkt — es wird nicht gewartet", roh)
            return jetzt
    menge = float(cfg.get("dauer") or 0)
    einheit = str(cfg.get("einheit") or "m")[:1].lower()
    delta = {"s": dt.timedelta(seconds=menge), "m": dt.timedelta(minutes=menge),
             "h": dt.timedelta(hours=menge), "t": dt.timedelta(days=menge)}.get(
                 einheit, dt.timedelta(minutes=menge))
    # Capped at the top: a flow that sleeps for two years is almost always a typo.
    return jetzt + min(delta, dt.timedelta(days=90))


async def faellige_timer() -> int:
    """Wake expired timers, returns the number of runs woken (called from the tick)."""
    jetzt = _now()
    geweckt = 0
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(WorkflowStepRun, WorkflowToken)
            .join(WorkflowToken, WorkflowToken.id == WorkflowStepRun.token_id)
            .where(WorkflowStepRun.node_type == NType.timer,
                   WorkflowStepRun.status == SStatus.waiting,
                   WorkflowToken.state == TState.waiting))).all()
        faellig: list[int] = []
        for step, tok in rows:
            wann = (step.result or {}).get("faellig")
            try:
                if wann and dt.datetime.fromisoformat(wann) > jetzt:
                    continue
            except ValueError:
                pass                      # unreadable stamp, better wake than hang
            step.status = SStatus.done
            step.completed_at = jetzt
            tok.state = TState.active
            tok.waiting_for = None
            faellig.append(step.instance_id)
        if faellig:
            await db.execute(update(WorkflowInstance)
                             .where(WorkflowInstance.id.in_(faellig))
                             .values(status=IStatus.running))
            await db.commit()
            geweckt = len(faellig)
    for iid in set(faellig):
        await advance(iid)
    return geweckt


# -- loop: a list item by item -----------------------------------------------

# Where the counter of a loop lives. In the context, because it has to outlive the pass: a
# loop may cross a wait point (approval per item) and continue days later.
SCHLEIFEN_KEY = "_schleifen"
LOOP_MAX = 500


async def _schleife(db, inst, node, token, cfg) -> Outcome:
    """One pass through a list, sequential, over a back edge.

    The node is entered again on every pass (synchronous nodes are re-executed on re-entry,
    see `_drive`). It counts on, puts the next item into the context and takes the outlet
    `element`. When the list is exhausted it takes `fertig`.

    Sequential on purpose, not a fan of parallel tokens: the engine carries one token per
    instance, and the steps of a loop almost always depend on each other (same far side,
    same file, same account). Parallel would be faster and wrong in exactly those places.
    """
    ctx = dict(inst.context or {})
    stand = dict(ctx.get(SCHLEIFEN_KEY) or {})
    meins = dict(stand.get(node["id"]) or {})

    pfad = str(cfg.get("liste") or cfg.get("list") or "").strip()
    element_key = str(cfg.get("element") or "element")
    index_key = str(cfg.get("index") or "i")
    sammel_pfad = str(cfg.get("sammle") or "").strip()
    ergebnis_key = str(cfg.get("ergebnisse") or "ergebnisse")
    grenze = min(int(cfg.get("max") or LOOP_MAX), LOOP_MAX)

    if not meins:
        roh = _dig_ctx(ctx, pfad) if pfad else None
        liste = roh if isinstance(roh, list) else ([] if roh is None else [roh])
        meins = {"i": 0, "gesamt": len(liste), "werte": liste[:grenze], "ergebnisse": []}
    else:
        # Back from the loop body: collect first, then count on.
        if sammel_pfad:
            meins["ergebnisse"] = [*meins.get("ergebnisse", []), _dig_ctx(ctx, sammel_pfad)]
        meins["i"] = int(meins.get("i", 0)) + 1

    werte = meins.get("werte") or []
    i = int(meins.get("i", 0))
    if i >= len(werte):
        # Done: drop the counter (an outer loop would otherwise not restart the same
        # inner one), collected results stay.
        stand.pop(node["id"], None)
        ctx[SCHLEIFEN_KEY] = stand
        ctx.pop(element_key, None)
        ctx[ergebnis_key] = meins.get("ergebnisse", [])
        ctx[f"{index_key}_gesamt"] = meins.get("gesamt", 0)
        inst.context = ctx
        db.add(WorkflowStepRun(
            instance_id=inst.id, token_id=token.id, node_id=node["id"],
            node_type=NType.loop, status=SStatus.done, completed_at=_now(),
            result={"durchgaenge": len(werte), "gesamt": meins.get("gesamt", 0)}))
        return Outcome(handle="fertig")

    ctx[element_key] = werte[i]
    ctx[index_key] = i
    stand[node["id"]] = meins
    ctx[SCHLEIFEN_KEY] = stand
    inst.context = ctx
    return Outcome(handle="element")


def _dig_ctx(data, pfad: str):
    """Resolve a path in the context, including list indexes (`tool.json.items.0`)."""
    cur = data
    for teil in str(pfad).split("."):
        if isinstance(cur, dict) and teil in cur:
            cur = cur[teil]
        elif isinstance(cur, list) and teil.isdigit() and int(teil) < len(cur):
            cur = cur[int(teil)]
        else:
            return None
    return cur


# -- subflow: run another flow as a child instance ----------------------------

async def _instance_depth(db, inst: WorkflowInstance) -> int:
    depth, cur = 0, inst
    while cur is not None and cur.parent_instance_id and depth <= MAX_SUBFLOW_DEPTH:
        cur = await db.get(WorkflowInstance, cur.parent_instance_id)
        depth += 1
    return depth


async def _start_subflow(db, inst, node, token, cfg) -> Outcome:
    """Start the definition resolved for the slot as a child instance and wait for it.

    This is how the ticket lifecycle calls the separate review flow without duplicating it,
    and a customized review process takes effect everywhere it is called.
    """
    from .workflow_sets import resolve_definition

    existing = await _latest_step(db, inst.id, node["id"])
    if existing is not None and existing.status == SStatus.running:
        return Outcome(wait=True, waiting_for="subflow")

    slot = cfg.get("slot") or cfg.get("workflow_slot")
    def_id = cfg.get("definition_id")
    if not slot and not def_id:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"subflow-Knoten '{node['id']}' ohne Ablauf")
    if await _instance_depth(db, inst) >= MAX_SUBFLOW_DEPTH:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"subflow zu tief verschachtelt (> {MAX_SUBFLOW_DEPTH})")

    # A subprocess follows the issue type of the ticket it hangs off as well.
    vorgangsart = None
    if inst.issue_id:
        from ..models.ticket import Issue
        issue = await db.get(Issue, inst.issue_id)
        vorgangsart = issue.type_id if issue else None
    # A slot is resolved per project (own customization beats set beats default), an
    # explicitly named flow is exactly that one, including a free-standing one. Without
    # this second way "other flow" would only be usable for the five shipped slots, and
    # custom flows could not be nested into each other.
    if def_id:
        definition = await db.get(WorkflowDefinition, int(def_id))
        wofuer = f"Ablauf #{def_id}"
    else:
        definition = await resolve_definition(db, inst.project_id, slot, vorgangsart)
        wofuer = f"Slot '{slot}'"
    if definition is None or definition.current_version_id is None:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"Kein veröffentlichter Ablauf für {wofuer}")
    if definition.id == inst.definition_id:
        return Outcome(terminal=True, instance_status="failed",
                       error=f"subflow-Knoten '{node['id']}' ruft sich selbst auf")

    bezug = {"slot": slot} if slot else {"definition_id": definition.id}
    step = WorkflowStepRun(
        instance_id=inst.id, token_id=token.id, node_id=node["id"],
        node_type=NType.subflow, status=SStatus.running, result=bezug,
    )
    db.add(step)
    await db.flush()

    ctx = dict(inst.context or {}) if cfg.get("inherit_context", True) else {}
    child = await start_workflow(
        db, definition, subject_kind=definition.subject_kind, issue_id=inst.issue_id,
        hardware_asset_id=inst.hardware_asset_id, context=ctx, actor_id=inst.started_by,
        source=f"subflow:{inst.id}", parent_instance_id=inst.id, parent_node_id=node["id"],
    )
    step.result = {**bezug, "child_instance_id": child.id}
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
        # The child context (a merge result, for example) flows back up.
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


# -- agent_task: bridge to the existing agent queue (worker) ------------------

async def _resolve_agent_role(db, issue, cfg: dict) -> str:
    """Role of the run. Symbolic values bind the graph to the project settings instead of
    baking in a fixed staffing (`plan_agent`, `exec_agent`, `review_agent`, `assigned`),
    anything else counts as a concrete role name.

    Matches `dispatcher._plan_role` and `_exec_role`: planning is always done by the
    architect (even when the PM was assigned, the PM never plans itself), execution by the
    execution agent.
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
    # exec_agent: on a PM assignment deliberately NOT the PM but the execution agent.
    if issue.assigned_agent == "project_manager":
        return exec_default
    return issue.assigned_agent or exec_default


async def _park_on_gate(db, inst, issue, node, token, verdict) -> Outcome:
    """Postpone a run because a gate is closed. The token stays on the node
    (`waiting_for="gate"`), the engine tick retries it periodically.

    With the runaway brake (`hold`) the ticket is held as well: it only moves on when a
    person steps in (a fresh plan approval resets the cap window, for example).
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
    """Start an agent run through the queue and wait for the result asynchronously (the
    token waits, `_await_agent` moves it on).

    Before queueing, `services/agent_gate` decides: time windows, per-user limit and the
    runaway brake apply no matter how the process is drawn.

    Precondition (enforced by validate): subject_kind=issue with issue_id set.
    """
    import uuid

    # Idempotency: an agent is already running for this node, do not queue twice.
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

    # -- gatekeeper (policy, not graph) --------------------------------------
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

    # A waiting gate step of the same node is reused so a run postponed several times
    # does not bloat the step history.
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
    step.result = {"task_id": task_id}  # task_id stored for reattaching
    await db.flush()

    # The same mark the monitor and the per-user limit use.
    issue.agent_working = True
    # And the visible state: here, at the actual queueing, the agent really works. The
    # graph only sets "approved" before, because the gatekeeper may sit in between for any
    # length of time. Execution only, planning has its own state that `st_planning` already
    # set.
    if phase == "execution":
        from ..models.enums import TicketAgentStatus
        from .artifacts import set_ticket_status
        # `hold` and `failed` belong in here explicitly: once an agent runs again, the
        # old reason is obsolete. Without it ABC-31 showed "hold, merge" for hours while
        # the developer had long been working again. The label lied, not the process.
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
    # Start the result watcher only AFTER the commit: it reads step and token in a session
    # of its own and would not see them before the commit (and a commit of this pass coming
    # later would overwrite its changes).
    spawn_after.append(_await_agent(inst.id, token.id, step.id, task_id,
                                    dict(outcomes_map), timeout))
    return Outcome(wait=True, waiting_for="agent")


async def _warte(task_id: str, timeout: int, was: str) -> tuple[dict | None, dict]:
    """Wait for a worker result and, on failure, name WHAT went wrong.

    Without a result a substitute result comes back carrying the reason ("run vanished"
    against "time limit") and marked for the late pickup (`verloren`). This used to say
    "no result (timeout)", which read in the ticket as "failed: unknown error" although
    nothing had failed at all.
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
    """Watcher for asynchronous auto actions (merge and friends): waits for the worker
    result, stores it under `context.<context_key>` and continues through the matching
    outlet.

    Same shape as `_await_agent`. Only this keeps the engine responsive during a merge that
    runs for minutes instead of blocking the session and the advance claim.
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
    """Is the agent stuck? True when the worktree is unchanged since the PREVIOUS run
    (fingerprint comparison), the same stall detection the old dispatcher had."""
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
    """Outlet of an agent_task: explicit mapping, then an outlet of the same name, then
    the default.

    The middle step is what makes graphs readable: draw an edge called "loop_exhausted" and
    you get it, without maintaining an outcomes_map.
    """
    mapped = outcomes_map.get(status)
    if mapped:
        return mapped
    if next_node(edges, node_id, status) is not None:
        return status
    return _DEFAULT_AGENT_MAP.get(status, "err")


async def _agent_note(db, issue_id: int, status: str, summary: str, stalled: bool) -> None:
    """A short trace of every finished run in the ticket history (who did what).

    "blocked" is written by the worker itself (question, permission), so do not duplicate
    it here.
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
        # `kind` separates work state from incident log. The ticket history shows both,
        # the prompt of the next agent only the work state: a message about a worker
        # restart or a deadlock is nothing it could build on, but that is exactly how it
        # read them. On 2026-08-07 an agent turned "claude: answer truncated at max_tokens,
        # raise max_tokens" into its task and wrote an escalation into the provider router
        # for it, while the ticket was about a failing job.
        art = "agent_fail" if status == "failed" else "agent"
        db.add(Comment(issue_id=issue_id, author_id=None, author_label="Agent",
                       body=note[:1500], kind=art))


async def _await_agent(instance_id: int, token_id: int, step_id: int, task_id: str,
                       outcomes_map: dict, timeout: int) -> None:
    """Wait for the agent result, mark the step done with its decision (outcome handle),
    reactivate the token and continue. The agent_task node ALWAYS completes (done), the
    agent outcome only picks the branch.

    The result also lands under `context.agent` so decision nodes can check it (status,
    summary, kind of blocker, stall detection, merge state).
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
        # Without a result write down the REASON, not just "agent status: failed". This
        # very line sent the investigation of ABC-2 and ABC-6 down the wrong path.
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
                        # The plan is the artifact of the run, not a process decision.
                        # It is always written, no matter how the graph is drawn.
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
                # Pre-chewed values for decision nodes and status templates so graphs
                # get by without text analysis:
                "has_subtickets": has_subtickets,
                "hold_hint": "plan_split" if has_subtickets else "plan_review",
                "stuck_reason": "stuck" if stalled else "cap",
                "blocker_reason": {"permission": "permission", "review": "review"}.get(
                    blocker or "", "question"),
                # For the shared failure node per phase: which state the ticket takes
                # and why. A failure is "failed" (without a reason), everything else is a
                # "hold" with a fitting reason. Same display as with the earlier separate
                # nodes, just decided in one place.
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
    """The single entry point: creates instance and start token, then advances once.

    Creation uses the request session that was passed in, `advance` afterwards runs in a
    session of its own. `inst` is reloaded at the end.
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
    # Templates from a set are project-less, but the instance still belongs to the project
    # of its subject, otherwise permissions, live events and assignee resolution miss.
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
    # The topmost run of a ticket is its lifecycle: events (comment, stop) and the UI find
    # it through issues.workflow_instance_id. Child runs (subflow) do not count.
    if issue_id is not None and parent_instance_id is None:
        from ..models.ticket import Issue
        subj = await db.get(Issue, issue_id)
        if subj is not None and definition.slot == "ticket_lifecycle":
            subj.workflow_instance_id = inst.id
    await db.commit()
    inst_id = inst.id
    if not advance_now:
        # The instance is ready but does not start yet, for migrating existing tickets
        # whose token is placed on a wait node on purpose.
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
    """Decide the waiting approval step of an instance, without HTTP.

    For paths that do not come through the API (a chat card, native tools in the worker).
    Returns False when there is nothing to decide right now, so the caller can say that
    instead of reporting success.

    It does NOT commit and does NOT advance: `advance` belongs in the backend process, and
    the caller has to commit first, otherwise the engine's own session would not see the
    decision yet.
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
    """Move the active token forward synchronously until it waits or ends.

    An atomic `advancing` claim guards against a double advance, the actual execution runs
    in its own session (`_drive`). `advancing` is reset in every case.
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
        return  # another advance is already running OR the instance is terminal
    try:
        # Drive again while a token is active: an agent or action result can arrive WHILE
        # this pass still holds the claim (fast queue, reattached run). Without this loop
        # the instance would stand still until the next 30 second tick although everything
        # is ready.
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
        # Result watchers start only after the commit, otherwise they might read a step
        # this session has not written yet.
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

            # Re-entry: a step that is finished and carries a decision (approval, agent
            # outcome, event, subprocess, asynchronous action) determines the edge, the
            # node is NOT executed again. `routed_at` stamps that; without the stamp a back
            # edge onto the same node (continuation loop) would route again on the next
            # pass instead of executing. Synchronous actions carry no decision and run
            # again inside a loop on purpose.
            last = await _latest_step(db, inst.id, token.node_id)
            if (last is not None and last.status == SStatus.done and last.routed_at is None
                    and (last.decision or ntype in WAIT_NODES)):
                # Outlet according to the decision, otherwise fall back to "out" (the
                # editor often draws only an out outlet).
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
    """Check a graph. Returns a list of error messages, possibly empty. The messages are
    German because they are shown in the editor."""
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
            # The default branch MUST be one of the branches. Otherwise the node shows an
            # outlet the config does not know, which would be gone on the next edit and
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
            if not slot and not cfg.get("definition_id"):
                errors.append(f"Subflow-Knoten '{nid}': kein Ablauf gewählt")
            elif slot:
                from ..models.enums import WorkflowSlot
                if slot not in {s.value for s in WorkflowSlot}:
                    errors.append(f"Subflow-Knoten '{nid}': unbekannter Slot '{slot}'")

        if ntype == "timer":
            cfg = node_config(n)
            if not (cfg.get("dauer") or cfg.get("bis")):
                errors.append(f"Timer-Knoten '{nid}': weder Dauer noch Zeitpunkt angegeben")

        if ntype == "loop":
            handles = _outgoing_handles(edges, nid)
            for req in ("element", "fertig"):
                if req not in handles:
                    errors.append(f"Schleifen-Knoten '{nid}': Kante für Ausgang '{req}' fehlt")
            if not node_config(n).get("liste"):
                errors.append(f"Schleifen-Knoten '{nid}': keine Liste angegeben")

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
    """Arm postponed agent runs (`waiting_for="gate"`) again.

    This replaces the old dispatcher pickup: tickets that could not start because of the
    night window, after-hours, the runner limit or the cap come back to the gate here.
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
    """Collect results that arrive AFTER the watcher gave up.

    The watcher only gives up when a run has demonstrably vanished, but "gone" does not
    mean "gone forever": a restarted worker pulls its job back from the processing list and
    delivers a result hours later. Without this collector the work would be lost, with the
    process sitting on the failure branch and the ticket on "failed" while the branch
    carries the finished work.

    Instead of copying the bookkeeping, the step is set back to running, the token is
    returned to its node and the same watcher is attached again. It finds the result in
    Redis right away and continues as if nothing had happened.
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
            # If something runs for this instance again, the new run wins, not the latecomer.
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
            # Undo the detour over the failure branch: drop waiting steps and arm the
            # agent step again.
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
    """Close runs nobody stands behind any more.

    A run normally ends on its own, unless the process driving it dies where it can no
    longer write (run 753 on 2026-08-07: a deadlock while writing the step row left the
    session unusable, so the row stayed on running forever). The cleanup at worker start
    only helps on the next restart, and until then the run counts as alive and keeps a
    ticket in progress through the board rule ("whoever works, sits in In Progress") when
    it is really waiting.

    What gets checked is the actual sign of life, not the clock: heartbeat, queue,
    processing list. Only when none of those sources knows the job AND the grace period has
    passed is it closed, because an agent may take hours.
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
    # Close the dead ones first, then reconcile: otherwise a run that only exists on paper
    # keeps its ticket in progress through the board rule.
    try:
        await tote_laeufe_schliessen()
    except Exception:  # noqa: BLE001, must never block the tick
        log.exception("Schließen toter Läufe fehlgeschlagen")

    # Reattach lost watchers. A watcher lives in the backend process, and when it is lost
    # (reload, exception, hanging connection) nobody waits for the result any more, and the
    # ticket stands still without anyone noticing.
    try:
        await recover_workflow_agents()
    except Exception:  # noqa: BLE001, must never block the tick
        log.exception("Wiederanbinden der Wächter fehlgeschlagen")

    # Reconcile the artifact rows: `agent_status` is set in many places (endpoints, bot, PM
    # chat, worker), and reconciling catches up within one tick instead of maintaining every
    # one of those places separately.
    try:
        from .artifacts import reconcile
        async with SessionLocal() as db:
            await reconcile(db)
    except Exception:  # noqa: BLE001, reconciling must never block the tick
        log.exception("Artefakt-Abgleich fehlgeschlagen")

    # Wake expired timers before the latecomers run: a woken run is not a latecomer, and a
    # waiting timer should not count as stuck either.
    try:
        await faellige_timer()
    except Exception:  # noqa: BLE001, must never block the tick
        log.exception("Wecken fälliger Timer fehlgeschlagen")

    try:
        await nachzuegler_einsammeln()
    except Exception:  # noqa: BLE001, must never block the tick
        log.exception("Nachzügler-Abholung fehlgeschlagen")

    # Pick up tickets without a process instance. This used to run ONLY at backend start,
    # so a ticket orphaned in between was dead until the next restart, the nastiest kind of
    # bug because the restart fixes it and hides the cause (ABC-32 on 2026-08-07: assigned
    # by the assistant, never started).
    try:
        from .lifecycle_flow import adopt_orphans
        async with SessionLocal() as db:
            n = await adopt_orphans(db)
        if n:
            log.info("Tick: %d verwaiste(s) Ticket(s) in den Lebenszyklus geholt", n)
    except Exception:  # noqa: BLE001, must never block the tick
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
    """Reattach running agent_task and action steps to their run in Redis.

    `_await_agent` and `_await_action` pick up an already present result through
    wait_result, otherwise they keep waiting. Without this a ticket would hang in the
    running state forever after a plain backend reload.

    Runs at start AND in every tick. At start alone was not enough: a watcher can get lost
    during operation too. On 2026-08-07 one was stuck in a half dead Redis connection, the
    finished result for ABC-31 sat unclaimed in Redis from 19:54 on, and the ticket stood
    still for an hour without anything noticing. Whoever already has a watcher does not get
    a second one (`_WAECHTER`).
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
                continue      # somebody waits already, no second one on the same result
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
    """30 second loop: finds stuck running instances with an active token and advances
    them. A safety net after a crash or restart, normal operation runs synchronously."""
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
