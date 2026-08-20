"""Handlers for the auto_action nodes of the workflow engine.

An auto_action node carries an `action` type plus parameters in its `config`. `run_action`
performs the side effect and returns a result dict (persisted in StepRun.result). Only what
is explicitly meant for it reaches the outside: `http_request` and `webhook` call foreign
systems, everything else stays inside Traccoon.

Supported actions:
  set_context         {set:{key:val,...}}       write variables into instance.context
  set_status          {status, reason?, notify?} set the state of the bound artifact
                                                (ticket: plus board column, message, event)
  set_field           {field, values, mode?}    set a custom field of the artifact
                                                (mode: set | add | remove)
  set_board_status    {status|category}         set the board column of the bound ticket
  create_ticket       {summary, ...}            create a ticket (like the inbound webhook)
  tool_call           {tool, arguments, context_key?, fail_on_error?}  call an MCP tool
  http_request        {destination, method, path, query, headers, body}  call a destination
  webhook             {url, method, headers, payload, secret}  outgoing call to a free URL
  comment             {text}                    system comment on the bound issue
  notify              {to:{mode,...}, title, text}  notification (bell plus channel)
  assistent_auftrag   {auftrag, agent?, warten?, ...}  hand a free assignment to the assistant
  mail_anhang         {index?, context_key?, max_mb?}  fetch a mail attachment as base64
  antwort             {text | fielder}           the answer of this run (a waiting webhook reads it)
  noop                (default)                 nothing, a placeholder

Ticket lifecycle (the graph is the truth, `agent_status` is the projection):
  refresh_facts       {}                        project and ticket facts into the context
  assign_agent        {agent}                   assign an agent (sets assigned_by and at)
  set_cap_baseline    {}                        move the cap window to "from now on"
  start_testenv       {}                        start the test environment of the ticket
  stop_testenv        {}                        tear the test environment down (before merge)
  accept_merge        {timeout_sec?}            merge the branch or open a PR (async, worker)
  deploy              {force?}                  queue a deployment
  split_tickets       {}                        create <subtickets> from the plan as children
  stop_agent          {}                        abort a running agent run

Mail intake (slot `mail_intake`, handlers in `services/mail_actions.py`):
  mail_classify       {classify_agent?}         classify the mail and learn the sender rule
  spam_evaluate       {}                        rules, model and memory into one verdict
  spam_card           {vorentschieden?}         verdict row plus a question in the messenger
  spam_apply          {entscheidung?, decided_by?}  commit it, learn, move the mail
  assistant_task      {}                        create an assistant item from the mail
  assistant_card      {}                        approval card for that item
  assistant_run       {}                        queue an assistant run (auto approval)

Actions accept both config shapes (the editor nests {action:{action,params}}, flat
{action:"name",...} works too) through _normalize_action. Text and value fields support
`{{var.path}}` templating from the context.
"""
from __future__ import annotations

import os
import re

from ..models.workflow import WorkflowInstance
from . import workflow_terms as terms

def _config(node: dict) -> dict:
    data = node.get("data") or {}
    cfg = data.get("config")
    if isinstance(cfg, dict):
        return cfg
    return node.get("config") if isinstance(node.get("config"), dict) else {}


def _dig(data, path: str):
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _interp(value, ctx: dict):
    """Replace `{{…}}` inside strings. Non-strings are left alone.

    Since 2026-08-18 the braces hold more than a path: a chain of filters
    (`{{ mail.subject | truncate:40 }}`, see `workflow_expr`). A plain path behaves as before,
    so every existing template stays valid.
    """
    if not isinstance(value, str):
        return value
    from .workflow_expr import fuellen
    return fuellen(value, ctx)


def _normalize_action(cfg: dict) -> tuple[str, dict]:
    """Vereinheitlicht beide Config-Formen:
      - verschachtelt (Editor):  {"action": {"action": "name", "params": {...}}}
      - flach (Seed/Handschrift): {"action": "name", <param>: <wert>, ...}
    Liefert (action_name, params)."""
    raw = cfg.get("action")
    if isinstance(raw, dict):
        return (raw.get("action") or raw.get("kind") or "noop"), dict(raw.get("params") or {})
    action = raw or cfg.get("kind") or "noop"
    params = {k: v for k, v in cfg.items() if k not in ("action", "kind", "label")}
    return action, params


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _create_ticket(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Create a ticket (like the inbound webhook in mode=task). The new ticket is stored
    under context[context_key] (default 'created_ticket') as {id,key} for later nodes."""
    from sqlalchemy import select

    from ..models.enums import TicketAgentStatus
    from ..models.project import Project
    from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from ..models.user import SYSTEM_USER_ID

    pid = _as_int(_interp(params.get("project_id"), ctx)) if params.get("project_id") is not None else None
    fremdes_ziel = pid is not None and pid != inst.project_id
    pid = pid or inst.project_id
    if pid is None:
        raise ValueError("create_ticket: no project_id (neither a parameter nor the instance project)")
    # A flow may only create where the person behind it could create as well. Without this
    # check a free-standing flow would be a way into foreign projects: the definition
    # belongs to its creator, the target project does not.
    if fremdes_ziel and inst.started_by is not None:
        from ..api.deps import build_access
        from ..models.enums import GlobalRole, ProjectRole
        from ..models.user import User as _User
        starter = await db.get(_User, inst.started_by)
        ziel = await db.get(Project, pid)
        if starter is None or ziel is None:
            raise ValueError("create_ticket: target project or trigger unknown")
        if starter.global_role != GlobalRole.admin:
            zugriff = await build_access(ziel, starter, db)
            if not zugriff.has_role(ProjectRole.member):
                raise ValueError(
                    f"create_ticket: no rights on the project {ziel.key}")
    t = (await db.execute(select(IssueType).where(IssueType.project_id == pid)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == pid)
                          .order_by(WorkflowStatus.order))).scalars().first()
    project = await db.get(Project, pid)
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == pid)
                                .with_for_update())).scalar_one_or_none()
    if not (t and s and project and counter):
        raise ValueError("create_ticket: target project without type, status or counter")
    counter.last_number += 1
    n = counter.last_number
    reporter = inst.started_by or SYSTEM_USER_ID
    summary = (_interp(params.get("summary") or params.get("summary_tpl") or "", ctx)
               or f"Workflow #{inst.id}")[:500]
    description = _interp(params.get("description") or params.get("body_tpl") or "", ctx)
    issue = Issue(
        project_id=pid, number=n, key=f"{project.key}-{n}"[:50], type_id=t.id, status_id=s.id,
        summary=summary, description=description, reporter_id=reporter, rank=f"{n:08d}",
        source=f"workflow:{inst.id}",
    )
    agent = params.get("assigned_agent") or params.get("agent")
    if agent:
        import datetime as _dt
        issue.assigned_agent = str(agent)
        issue.assigned_by_user_id = reporter
        issue.assigned_at = _dt.datetime.now(tz=_dt.timezone.utc)
        sa = str(params.get("start_agent_status") or "planning")
        try:
            issue.agent_status = TicketAgentStatus(sa)
        except ValueError:
            issue.agent_status = TicketAgentStatus.planning
    db.add(issue)
    await db.flush()
    # A fresh ticket is an artifact right away, not only at the next reconcile.
    from .artifacts import ensure_for_issue
    await ensure_for_issue(db, issue)
    key_out = params.get("context_key") or "created_ticket"
    inst.context = {**ctx, key_out: {"id": issue.id, "key": issue.key}}
    return {"action": "create_ticket", "issue_id": issue.id, "issue_key": issue.key}


async def _set_board_status(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Sets the board column (status_id) of the bound ticket. The parameter is `status` (the
    column name) or `category` (todo|in_progress|done)."""
    if inst.issue_id is None:
        return {"action": "set_board_status", "applied": False, "reason": "keine Ticket-Bindung"}
    from sqlalchemy import select

    from ..models.ticket import Issue, WorkflowStatus

    issue = await db.get(Issue, inst.issue_id)
    if issue is None:
        return {"action": "set_board_status", "applied": False, "reason": "Ticket fehlt"}
    name = _interp(params.get("status") or params.get("name") or "", ctx).strip()
    category = str(params.get("category") or "").strip()
    rows = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == issue.project_id)
                             .order_by(WorkflowStatus.order))).scalars().all()
    target = None
    if name:
        target = next((w for w in rows if (w.name or "").lower() == name.lower()), None)
    if target is None and category:
        target = next((w for w in rows if getattr(w.category, "value", str(w.category)) == category), None)
    if target is None:
        raise ValueError(f"set_board_status: no status '{name or category}' in the project")
    issue.status_id = target.id
    return {"action": "set_board_status", "status_id": target.id, "status": target.name}


def _interp_deep(value, ctx: dict):
    """Recursive {{var}} templating over strings in dicts and lists, non-strings stay."""
    if isinstance(value, str):
        return _interp(value, ctx)
    if isinstance(value, dict):
        return {k: _interp_deep(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_interp_deep(v, ctx) for v in value]
    return value


async def _webhook(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Outgoing HTTP call. Parameters:
      url (required), method (GET|POST|PUT|PATCH|DELETE, default POST), headers {..},
      payload {..} or text, secret (vault name, available as {{secret}} in url, headers and
      payload, never logged), timeout_sec.
    Definitions are authorized by project maintainers, like the existing job and webhook
    infrastructure.
    """
    import httpx

    from ..worker.secrets import resolve_ref

    # Resolve the secret and expose it ONLY for templating, never in context or result.
    tctx = dict(ctx)
    sref = params.get("secret") or params.get("secret_ref")
    if sref:
        ref = sref if str(sref).startswith("secret:") else f"secret:{sref}"
        tctx["secret"] = await resolve_ref(db, ref, inst.started_by)

    url = _interp(params.get("url") or "", tctx).strip()
    if not url:
        raise ValueError("webhook: 'url' is required")
    method = str(params.get("method") or "POST").upper()
    timeout = float(params.get("timeout_sec") or 10)
    headers = {k: _interp(str(v), tctx) for k, v in (params.get("headers") or {}).items()}
    payload = params.get("payload")
    if payload is None:
        payload = params.get("body")

    kwargs: dict = {"headers": headers, "timeout": timeout}
    if method not in ("GET", "HEAD", "DELETE") and payload is not None:
        p = _interp_deep(payload, tctx)
        if isinstance(p, (dict, list)):
            kwargs["json"] = p
        else:
            kwargs["content"] = str(p)

    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, **kwargs)
    body = (resp.text or "")[:500]
    ok = 200 <= resp.status_code < 300
    return {"action": "webhook", "url": url, "method": method,
            "status_code": resp.status_code, "ok": ok, "response": body}


# ── Ticket-Lebenszyklus ──────────────────────────────────────────────────────

# Before the artifact registry there was one action per subject. Both are `set_status`
# today, but the old names still sit in published versions, and those are immutable
# (running instances hang off them). So they are redirected instead of maintained twice.
_ALT_AKTIONEN = {"set_agent_status", "set_purchase_status"}

# Which agent status triggers which notification, identical to the earlier post-processing
# in the dispatcher so messenger and bell behave unchanged.
_NOTIFY_ON = {
    "plan_review": ("plan_review", "Plan bereit"),
    "to_test": ("to_test", "bereit zur Abnahme"),
    "failed": ("failed", "fehlgeschlagen"),
    "hold": ("blocked", "blockiert"),
}


async def _issue_of(db, inst: WorkflowInstance):
    if inst.issue_id is None:
        return None
    from ..models.ticket import Issue
    return await db.get(Issue, inst.issue_id)


async def _artefakt_von(db, inst: WorkflowInstance):
    """The shared artifact row of the flow, which the custom fields hang off.

    Prefers the binding on the instance. Older instances (from before the shared identity)
    do not have it yet and are resolved through the ticket or the hardware item.
    """
    from ..models.artifact import Artifact
    from . import artifacts as art

    if inst.artifact_id:
        return await db.get(Artifact, inst.artifact_id)
    if inst.issue_id:
        issue = await _issue_of(db, inst)
        return await art.ensure_for_issue(db, issue) if issue else None
    if inst.hardware_asset_id:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, inst.hardware_asset_id)
        return await art.ensure_for_asset(db, asset) if asset else None
    return None


async def _set_field(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Set a custom field of the bound artifact (Administration, artifacts).

    `mode` decides what happens to existing values: `set` replaces them, `add` appends,
    `remove` takes away. On a single-select field only `set` makes sense, and the registry
    check rejects anything else anyway.

    The new values also land in the context under `fields.<key>` so later conditions in the
    same pass can read them.
    """
    from . import artifact_fields as af

    key = str(_interp(params.get("field") or params.get("key") or "", ctx)).strip()
    if not key:
        return {"action": "set_field", "applied": False, "reason": "kein Feld angegeben"}
    artefakt = await _artefakt_von(db, inst)
    if artefakt is None:
        return {"action": "set_field", "applied": False, "reason": "kein Artefakt an diesem Ablauf"}

    feld = next((f for f in await af.fields_of(db, artefakt.type_id, artefakt.project_id)
                 if f.key == key), None)
    if feld is None:
        raise ValueError(f"The field '{key}' does not exist on this artifact")

    # Values may arrive as a list, as a single value or comma separated.
    roh = params.get("values", params.get("value"))
    if roh is None:
        neue = []
    elif isinstance(roh, list):
        neue = [_interp(v, ctx) for v in roh]
    else:
        text = str(_interp(roh, ctx))
        neue = [t.strip() for t in text.split(",")] if "," in text else [text]
    neue = [v for v in neue if str(v).strip() != ""]

    modus = str(params.get("mode") or "set").lower()
    if modus in ("add", "remove"):
        vorhanden = [str(v) for v in (await af.values_of(db, artefakt.id)).get(key, [])]
        if modus == "add":
            neue = vorhanden + [str(v) for v in neue if str(v) not in vorhanden]
        else:
            weg = {str(v) for v in neue}
            neue = [v for v in vorhanden if v not in weg]

    gesetzt = await af.set_values(db, artefakt.id, feld, neue)
    inst.context = {**ctx, "fields": {**(ctx.get("fields") or {}), key: gesetzt}}
    return {"action": "set_field", "field": key, "mode": modus, "values": gesetzt}


async def _set_status(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Set the state of the bound artifact: ticket, hardware or a custom type.

    The ONE way inside the graph. Which values are allowed comes from the artifact registry
    (Administration, artifacts), and the editor only shows the states of the subject the
    flow hangs off. The old names `set_agent_status` and `set_purchase_status` end up here
    through `_ALT_AKTIONEN` as well, because they still sit in published versions.
    """
    from . import artifacts as art
    from ..models.hardware import HardwareAsset

    wert = str(_interp(params.get("status") or params.get("value") or "", ctx)).strip()
    if not wert:
        import logging
        logging.getLogger("workflow_actions").warning(
            "set_status without a state (template %r empty), going to hold", params.get("status"))
        wert = "hold"
    issue = await _issue_of(db, inst)
    asset = (await db.get(HardwareAsset, inst.hardware_asset_id)
             if inst.hardware_asset_id else None)
    ergebnis = await art.apply_status(
        db, subject_kind=inst.subject_kind, issue=issue, asset=asset, status_key=wert,
        reason=str(_interp(params.get("reason") or params.get("hold_reason") or "", ctx)).strip(),
    )
    # Messages stay on the ticket (board, messenger and bell read agent_status).
    if issue is not None and params.get("notify", True) and wert in _NOTIFY_ON:
        kind, label = _NOTIFY_ON[wert]
        from .notify import notify_issue
        detail = (ctx.get("agent") or {}).get("summary") or issue.summary
        if wert == "hold" and issue.hold_reason:
            label = f"blockiert ({issue.hold_reason.value})"
        await notify_issue(db, issue, kind, f"{issue.key}: {label}", str(detail)[:400])
        ergebnis["notified"] = True
    if issue is not None:
        from .events import emit
        await emit(db, "issue.done" if wert == "done" else "issue.agent_status_changed",
                   project_id=issue.project_id, issue_id=issue.id,
                   payload={"issue": {
                       "key": issue.key, "agent_status": wert,
                       "hold_reason": issue.hold_reason.value if issue.hold_reason else None}})
    return {"action": "set_status", **ergebnis}


async def _refresh_facts(db, inst: WorkflowInstance, ctx: dict) -> dict:
    """Write current project and ticket facts into the context as input for decisions.

    Without this node guards would test values frozen at instance start, while settings
    (test environment, auto deploy, continuation) should apply at the moment of the
    decision.
    """
    from ..models.project import Project
    issue = await _issue_of(db, inst)
    pid = issue.project_id if issue else inst.project_id
    project = await db.get(Project, pid) if pid else None
    facts: dict = {}
    if project is not None:
        facts["project"] = {
            "testenv_enabled": bool(project.testenv_enabled),
            "managed": bool(project.managed),
            "verify_command": bool(project.verify_command),
            "needs_acceptance": bool(project.testenv_enabled or project.managed
                                     or project.verify_command),
            "auto_deploy": bool(project.auto_deploy),
            "auto_continue": bool(project.auto_continue),
            "review_enabled": bool(project.review_enabled),
            "use_pull_request": bool(project.use_pull_request),
            "git_enabled": bool(project.git_enabled),
        }
    if issue is not None:
        facts["issue"] = {
            "has_parent": issue.parent_ticket_id is not None,
            "has_plan": bool(issue.plan),
            "merge_status": issue.merge_status or "",
            "continuation_count": issue.continuation_count,
            "testenv_status": issue.testenv_status or "",
            "assigned_agent": issue.assigned_agent or "",
        }
    inst.context = {**ctx, **facts}
    return {"action": "refresh_facts", "keys": list(facts.keys())}


async def _assign_agent(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    import datetime as _dt
    issue = await _issue_of(db, inst)
    agent = str(_interp(params.get("agent") or params.get("role") or "", ctx)).strip()
    if issue is None or not agent:
        return {"action": "assign_agent", "applied": False}
    issue.assigned_agent = agent
    issue.assigned_by_user_id = issue.assigned_by_user_id or inst.started_by
    issue.assigned_at = _dt.datetime.now(tz=_dt.timezone.utc)
    return {"action": "assign_agent", "agent": agent}


async def _set_cap_baseline(db, inst: WorkflowInstance) -> dict:
    """Reset the cap window: from here the runaway brake only counts fresh runs.

    Belongs at every human approval. Old failed attempts (rate limit aborts, for example)
    must not push legitimate new work straight into the cap.

    For the same reason the continuation count starts over here: planning and execution
    share one counter, and a stubborn planning phase would otherwise eat the budget of the
    execution before it wrote its first line.
    """
    from sqlalchemy import func, select

    from ..models.agents import Run
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "set_cap_baseline", "applied": False}
    issue.cap_baseline_run_id = (
        await db.execute(select(func.max(Run.id)).where(Run.issue_id == issue.id))).scalar()
    issue.continuation_count = 0
    # The review rounds belong to the same section: a new attempt (rework after a
    # question, a fresh assignment) starts with a fresh correction budget. Otherwise a
    # ticket that once needed two rounds would stall again on the next finding.
    issue.review_rounds = 0
    inst.context = {**(inst.context or {}), "continuation": 0, "continuation_hint": ""}
    return {"action": "set_cap_baseline", "baseline": issue.cap_baseline_run_id,
            "continuation": 0}


async def _testenv(db, inst: WorkflowInstance, start: bool) -> dict:
    from ..models.project import Project
    from .testenv import start_testenv, stop_testenv
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "testenv", "applied": False, "reason": "keine Ticket-Bindung"}
    project = await db.get(Project, issue.project_id)
    if project is None:
        return {"action": "testenv", "applied": False, "reason": "Projekt fehlt"}
    if start:
        if not project.testenv_enabled:
            return {"action": "start_testenv", "applied": False, "reason": "am Projekt aus"}
        res = await start_testenv(db, issue, project.key)
        return {"action": "start_testenv", "applied": True, "result": res}
    if not issue.testenv_status:
        return {"action": "stop_testenv", "applied": False, "reason": "keine Testumgebung"}
    await stop_testenv(db, issue, project.key)
    return {"action": "stop_testenv", "applied": True}


async def _accept_merge(db, inst: WorkflowInstance, params: dict) -> dict:
    """Queue the review merge on the worker, asynchronously.

    Returns `_wait`: the engine parks the step and only continues once the result is there
    (merged, conflict, pr_open, no_git and so on). Without that the merge would block the
    engine session for minutes.
    """
    import uuid

    from ..core.redis import enqueue_task
    issue = await _issue_of(db, inst)
    if issue is None:
        raise ValueError("accept_merge requires a bound ticket")
    task_id = f"accept-{issue.key}-{uuid.uuid4().hex[:8]}"
    await enqueue_task({"kind": "accept", "task_id": task_id,
                        "issue_id": issue.id, "project_id": issue.project_id})
    return {"action": "accept_merge",
            "_wait": {"task_id": task_id, "timeout": int(params.get("timeout_sec") or 0),
                      "context_key": "merge"}}


async def _deploy(db, inst: WorkflowInstance, params: dict) -> dict:
    """Queue a deployment. Without `force` only when the project has auto deploy enabled
    and a real stack directory (never the maintenance or host project itself, TRA-19)."""
    from ..models.ops import Deployment
    from ..models.project import Project
    issue = await _issue_of(db, inst)
    pid = issue.project_id if issue else inst.project_id
    project = await db.get(Project, pid) if pid else None
    if project is None:
        return {"action": "deploy", "queued": False, "reason": "kein Projekt"}
    if not params.get("force") and not project.auto_deploy:
        return {"action": "deploy", "queued": False, "reason": "Auto-Deploy aus"}
    if not project.workspace_dir:
        return {"action": "deploy", "queued": False, "reason": "Self-/Host-Projekt"}
    db.add(Deployment(project_id=project.id, issue_id=issue.id if issue else None,
                      stack_dir=project.workspace_dir, status="pending", source="workflow"))
    return {"action": "deploy", "queued": True}


async def _split_tickets(db, inst: WorkflowInstance, params: dict) -> dict:
    """Create the subtasks proposed in the plan as child tickets (part 1 starts, the rest
    is parked). Triggered from the lifecycle graph after a person approved the split."""
    import datetime as _dt
    import json
    import re

    from sqlalchemy import select

    from ..models.enums import TicketAgentStatus
    from ..models.project import Project
    from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus

    umbrella = await _issue_of(db, inst)
    if umbrella is None:
        raise ValueError("split_tickets requires a bound ticket")
    m = re.search(r"<subtickets>\s*(\[.*?\])\s*</subtickets>", umbrella.plan or "", re.DOTALL)
    if not m:
        return {"action": "split_tickets", "created": 0, "reason": "kein <subtickets>-Block"}
    subs = json.loads(m.group(1))
    project = await db.get(Project, umbrella.project_id)
    exec_agent = umbrella.exec_agent or project.exec_agent or "developer"
    t = (await db.execute(select(IssueType).where(IssueType.project_id == project.id)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == project.id)
                          .order_by(WorkflowStatus.order))).scalars().first()
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == project.id)
                                .with_for_update())).scalar_one()
    keys = []
    for order, sub in enumerate(subs):
        counter.last_number += 1
        n = counter.last_number
        child = Issue(
            project_id=project.id, number=n, key=f"{project.key}-{n}", type_id=t.id,
            status_id=s.id, summary=(sub.get("summary") or f"Teil {order + 1}")[:500],
            description=sub.get("description", ""), reporter_id=umbrella.reporter_id,
            rank=f"{n:08d}", parent_ticket_id=umbrella.id, split_order=order,
            plan=sub.get("plan", ""), assigned_agent=exec_agent,
            assigned_by_user_id=umbrella.assigned_by_user_id or umbrella.reporter_id,
            assigned_at=_dt.datetime.now(tz=_dt.timezone.utc),
            # Child 0 starts right away, the rest waits for its predecessor.
            agent_status=(TicketAgentStatus.approved if order == 0 else None),
        )
        db.add(child)
        keys.append(child.key)
    from .artifacts import set_ticket_status
    # The parent ticket waits for its parts: no state, the board stays put.
    await set_ticket_status(db, umbrella, None, board=False)
    from .comments import add_system_comment
    await add_system_comment(
        db, umbrella.id, f"✅ Aufteilung übernommen — {len(keys)} Teilaufgaben angelegt.",
        author_label="Workflow")
    return {"action": "split_tickets", "created": len(keys), "keys": keys}


async def _stop_agent(db, inst: WorkflowInstance) -> dict:
    from ..core.redis import publish_kill
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "stop_agent", "applied": False}
    await publish_kill(issue.key)
    issue.agent_working = False
    return {"action": "stop_agent", "applied": True}


async def _besitzer(db, inst: WorkflowInstance) -> int | None:
    """On whose behalf the run reaches outside.

    The starter, otherwise the person behind the ticket. The MCP group and the destinations
    hang off that: a flow gets nowhere its owner is not allowed to go.
    """
    if inst.started_by is not None:
        return inst.started_by
    if inst.issue_id:
        issue = await _issue_of(db, inst)
        if issue is not None:
            return issue.assigned_by_user_id or issue.reporter_id
    return None


async def _tool_call(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Call an MCP tool, the direct way to everything Traccoon has connected.

    Parameters:
      tool           tool name as in the picker (for example `obsidian_append_to_note`)
      arguments      {key: value}, values may contain `{{path}}` from the context
      context_key    where the result is written (default `tool`)
      fail_on_error  true means a failed call fails the step

    Without `fail_on_error` the flow decides for itself: `tool.ok` sits in the context and
    can be tested at a decision.
    """
    from .workflow_tools import aufrufen

    name = str(_interp(params.get("tool") or params.get("name") or "", ctx)).strip()
    argumente = _interp_deep(params.get("arguments") or params.get("args") or {}, ctx)
    if not isinstance(argumente, dict):
        argumente = {}
    ergebnis = await aufrufen(db, await _besitzer(db, inst), name, argumente)
    key = str(params.get("context_key") or "tool")
    inst.context = {**ctx, key: ergebnis}
    if params.get("fail_on_error") and not ergebnis["ok"]:
        raise ValueError(f"Tool {name!r}: {ergebnis.get('error', 'failed')}")
    return {"action": "tool_call", "tool": name, "ok": ergebnis["ok"],
            "error": ergebnis.get("error")}


async def _notiz_anhaengen(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Append a line to a note in the vault, and create the note when it is missing.

    Parameters, all with `{{path}}` templating from the context:
      pfad         path of the note, for example `04 Wissen/Erkennung/{{ spam.art }}.md`
      text         what is appended (one line, or several separated by newlines)
      ueberschrift optional section the text is put under (created when absent)
      werkzeug     MCP tool, default `obsidian__obsidian_append_to_note`
      context_key  where the result is written (default `notiz`)

    A shortcut over `tool_call`, and not a redundant one: the address form of the obsidian
    server is an `oneOf` (`{"type": "path", "path": …}`), and whoever writes it out by hand
    in the arguments of every flow gets it wrong once and then wonders why the note stays
    empty. The knowledge sits in exactly one place now, the same as in `tools_memory`.
    """
    from .workflow_tools import aufrufen

    pfad = str(_interp(params.get("path") or params.get("path") or "", ctx)).strip()
    text = str(_interp(params.get("text") or "", ctx)).strip()
    ueberschrift = str(_interp(params.get("heading") or params.get("heading") or "",
                               ctx)).strip()
    key = str(params.get("context_key") or "notiz")
    if not pfad or not text:
        # Not an error: a flow that has nothing to write should not fail because of it.
        inst.context = {**ctx, key: {"ok": False, "error": "kein Pfad oder kein Text"}}
        return {"action": "note_append", "ok": False, "reason": "leer"}

    argumente: dict = {"target": {"type": "path", "path": pfad}, "content": text}
    if ueberschrift:
        # A section is addressed as an object, and it has to be created when it is missing:
        # otherwise the call fails on a note that does not carry that heading yet. Without
        # this the line lands at the end of the file, which is where nobody looks for a task.
        argumente["section"] = {"type": "heading", "target": ueberschrift}
        argumente["createTargetIfMissing"] = True
    # With the server prefix: without it the session finds no tool and answers with a HINT
    # AS TEXT, and `aufrufen` reports ok because it got an answer. The note stays empty and
    # nobody notices. The name may be overridden, for a vault hanging off another server.
    werkzeug = str(params.get("tool") or "obsidian__obsidian_append_to_note").strip()
    ergebnis = await aufrufen(db, await _besitzer(db, inst), werkzeug, argumente)
    inst.context = {**ctx, key: ergebnis}
    # Der Rueckgabewert nennt die Aktion so, wie sie heisst. Er landet im Schritt-Protokoll
    # und nicht im Kontext — der Kontext-Schluessel `notiz` bleibt deshalb, wie er ist:
    # Der steht in gespeicherten Graphen.
    return {"action": "note_append", "path": pfad, "ok": ergebnis["ok"],
            "error": ergebnis.get("error")}


async def _http_request(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Call a configured destination (base URL and authentication live there).

    Parameters, all with `{{path}}` templating from the context:
      destination  name of the destination (resolved project, then user, then system wide)
      method       GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS (default POST)
      path         appended to the base URL, for example "/api/v2/orders"
      query        {key: value}, appended to the URL
      headers      {key: value}, on top of the standard headers of the destination
      body         dict or list (sent as JSON) or text, ignored on GET, HEAD and DELETE
      context_key  where the result is written in the context (default "http")
      fail_on_error  true means a 4xx or 5xx fails the step (default false: the process
                     decides for itself using `status_code` and `ok`)
    """
    from . import destinations

    name = str(_interp(params.get("destination") or params.get("target") or "", ctx)).strip()
    if not name:
        raise ValueError("http_request: no destination given")
    owner = inst.started_by
    if owner is None and inst.issue_id:
        issue = await _issue_of(db, inst)
        owner = (issue.assigned_by_user_id or issue.reporter_id) if issue else None
    dest = await destinations.resolve(db, name, project_id=inst.project_id, owner_id=owner)
    if dest is None:
        raise ValueError(f"http_request: unknown or disabled destination '{name}'")

    result = await destinations.call(
        db, dest,
        method=str(_interp(params.get("method") or "POST", ctx)),
        path=str(_interp(params.get("path") or "", ctx)),
        query={k: _interp_deep(v, ctx) for k, v in (params.get("query") or {}).items()},
        headers={k: str(_interp(v, ctx)) for k, v in (params.get("headers") or {}).items()},
        body=_interp_deep(params.get("body", params.get("payload")), ctx),
        timeout=int(params["timeout_sec"]) if params.get("timeout_sec") else None,
    )
    key = str(params.get("context_key") or "http")
    inst.context = {**ctx, key: result}
    if params.get("fail_on_error") and not result["ok"]:
        raise ValueError(f"Destination '{name}' answered with {result['status_code']}: "
                         f"{result.get('error', '')[:200]}")
    return {"action": "http_request", **{k: result[k] for k in ("destination", "method", "url",
                                                                "status_code", "ok")}}


def _zahl(params: dict, ctx: dict, *namen, default: float = 0.0) -> float:
    """A numeric parameter that may also come from the context.

    Numbers used to sit literally in the node. As soon as the same flow is used more than
    once (another job, another series, another threshold) they come from outside and read
    as `{{ still_stunden }}`. Without substitution the step fails on a text that was meant
    as a number.
    """
    for name in namen:
        if name in params and params[name] not in (None, ""):
            roh = _interp(params[name], ctx)
            if isinstance(roh, str):
                roh = roh.strip().replace(",", ".")
            try:
                return float(roh)
            except (TypeError, ValueError):
                raise ValueError(f"'{name}': '{roh}' is not a number")
    return default


def _kein_messwert(inst: WorkflowInstance, ctx: dict, params: dict, key: str,
                   grund: str, roh=None, letzter=None, einheit: str = "") -> dict:
    """Nothing recorded, but nothing broken either.

    Two cases end up here: the value is missing (an event without a reading) or it is not
    credible (the device reports 127 % when it does not know the charge). In both cases the
    last credible reading stays in the context, otherwise the next node writes the nonsense
    into the message.
    """
    key_ctx = str(params.get("context_key") or "metric")
    vorher = ctx.get(key_ctx) if isinstance(ctx.get(key_ctx), dict) else {}
    inst.context = {**ctx, key_ctx: {**vorher, "series": key, "ignored": True,
                                     "warn": False, "value": letzter, "unit": einheit,
                                     "raw": roh}}
    return {"action": "metric_record", "series": key, "ignored": True, "skipped": True,
            "reason": grund, "last_good": letzter}


async def _messwert(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Record a number, and read off where the series is heading.

    A flow used to see only the moment: "battery 25 %" became a message and was gone. Only
    the series of the last weeks answers the question you really have, which is how long
    this lasts and when you have to act.

    Parameters:
      reihe          key of the series (for example `akku.shelter`)
      wert           the number, `{{ … }}` allowed, commas and percent signs tolerated
      name/einheit   only needed when the series is created
      min/max        valid range, values outside are NOT recorded
      pflicht        true means a missing value is an error (default: skip)
      ziel           value the series runs towards (default 0, meaning empty)
      vorwarn_tage   how early to warn (default 7), 0 turns the warning off
      fenster_tage   how far back the trend looks (default 30)

    Afterwards the context holds `messreihe.*`: value, change per day, days left, the date
    of the zero point, the quality of the line and `warnen`. The next decision in the flow
    uses that to judge whether anybody should hear about it.
    """
    from . import metrics

    key = str(_interp(params.get("series") or "", ctx)).strip()
    if not key:
        raise ValueError("messwert: no series given")
    roh = _interp(params.get("value"), ctx)
    if isinstance(roh, str):
        roh = roh.strip().replace("%", "").replace(",", ".")
    # A missing value is usually not a defect but the nature of the thing: a tracker
    # reports "I am online" or "I am down" without a position, and therefore without a
    # charge level. The step then has nothing to do and should not look as if something
    # broke. Whoever needs the value sets `pflicht`.
    fehlt = roh is None or (isinstance(roh, str) and roh.lower() in ("", "none", "null"))
    if fehlt and not params.get("required"):
        # The last known reading stays in the context: on a failure notice it is the
        # most interesting number still available.
        bekannt = await metrics.reihe(db, await _besitzer(db, inst), key)
        return _kein_messwert(inst, ctx, params, key, "kein Wert in der Nutzlast",
                              letzter=bekannt.last_value if bekannt else None,
                              einheit=bekannt.unit if bekannt else "")
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        raise ValueError(f"messwert: '{roh}' is not a number")

    # Devices report nonsense when they do not know something: the tracker sends
    # `batteryLevel: 127` as soon as the charge is unknown. A single point like that bends
    # the line from "empty in two weeks" into "rising slightly", so a value outside the
    # bounds never enters the series.
    hat_unten = params.get("min", params.get("minimum")) not in (None, "")
    hat_oben = params.get("max", params.get("maximum")) not in (None, "")
    unten = _zahl(params, ctx, "min", "minimum") if hat_unten else None
    oben = _zahl(params, ctx, "max", "maximum") if hat_oben else None
    ausserhalb = ((unten is not None and wert < unten)
                  or (oben is not None and wert > oben))
    if ausserhalb:
        # During alarms the tracker reported "231 %", and without this bound that went out.
        vorhanden = await metrics.reihe(db, await _besitzer(db, inst), key)
        return _kein_messwert(inst, ctx, params, key, f"außerhalb {unten}…{oben}", roh=wert,
                              letzter=vorhanden.last_value if vorhanden else None,
                              einheit=vorhanden.unit if vorhanden else "")

    ziel = _zahl(params, ctx, "target")
    vorwarn = _zahl(params, ctx, "warn_days", default=7.0)
    fenster = int(_zahl(params, ctx, "window_days",
                        default=float(metrics.FENSTER_TAGE)))

    owner = await _besitzer(db, inst)
    reihe, _punkt = await metrics.erfassen(
        db, owner, key, wert,
        name=str(_interp(params.get("name") or "", ctx)),
        einheit=str(params.get("unit") or ""),
        kontext={"instanz": inst.id, "definition": inst.definition_id})
    stand = await metrics.trend(db, reihe, ziel=ziel, fenster_tage=fenster)
    warnen = metrics.vorwarnen(reihe, stand["days_left"], vorwarn) if vorwarn > 0 else False
    stand = {**stand, "series": key, "target": ziel, "warn_days": vorwarn, "warn": warnen}
    key_ctx = str(params.get("context_key") or "metric")
    inst.context = {**ctx, key_ctx: stand}
    return {"action": "metric_record", "series": key, "value": wert,
            "per_day": stand["per_day"], "days_left": stand["days_left"], "warn": warnen}


async def _messreihe_lesen(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Look at a series without feeding it, for flows that come from the clock.

    `messwert` answers "where do we stand after this value", here the question is "where do
    we stand at all, and is anything still coming?". That is the counterpart to the
    forecast: a device that fails says nothing any more, not even about the fault. Silence
    then looks like a quiet day, which is exactly why somebody has to look.

    Parameters:
      reihe          key of the series (`{{ … }}` allowed, from job parameters for example)
      ziel           target value for the forecast (default 0)
      fenster_tage   trend window (default 30)
      still_stunden  when it counts as quiet (default 0, meaning do not check)
      context_key    where in the context (default `messreihe`)

    Afterwards the context holds everything from `messwert` plus `alter_stunden`, `still`
    (the state) and `still_melden` (the decision, exactly once per phase of silence). Two
    fields instead of one, because a display is something else than a trigger.
    """
    from . import metrics

    key = str(_interp(params.get("series") or "", ctx)).strip()
    if not key:
        raise ValueError("messreihe_lesen: no series given")
    still_ab = _zahl(params, ctx, "silence_hours", "silent_from")
    key_ctx = str(params.get("context_key") or "metric")

    owner = await _besitzer(db, inst)
    reihe = await metrics.reihe(db, owner, key)
    if reihe is None:
        # Not an error: a typo in the key would otherwise be a red run every hour. The
        # flow decides for itself whether it cares (`gefunden`).
        inst.context = {**ctx, key_ctx: {"series": key, "found": False, "silent": False,
                                         "report_silence": False, "value": None}}
        return {"action": "metric_read", "series": key, "found": False}

    stand = await metrics.trend(
        db, reihe, ziel=_zahl(params, ctx, "target"),
        fenster_tage=int(_zahl(params, ctx, "window_days",
                               default=float(metrics.FENSTER_TAGE))))
    alter = stand.get("age_hours")
    still = bool(still_ab > 0 and alter is not None and alter >= still_ab)
    melden = metrics.stille_melden(reihe, alter, still_ab)
    stand = {**stand, "series": key, "found": True, "silence_hours": still_ab,
             "silent": still, "report_silence": melden}
    inst.context = {**ctx, key_ctx: stand}
    return {"action": "metric_read", "series": key, "value": stand["value"],
            "age_hours": alter, "silent": still, "report_silence": melden}


async def _mail_konto(db, params: dict, ctx: dict):
    """Konto, Ordner und Nummer der Mail, um die es geht.

    Vorgabe ist die Mail aus dem Auslöser (`mail` im Kontext) — das ist der Normalfall, wenn
    ein Knopf am Postfach oder der Mail-Eingang den Ablauf gestartet hat. Wer eine andere
    meint, sagt es im Knoten.
    """
    from ..models.mail import MailAccount

    mail = dict(ctx.get("mail") or {})
    konto_id = params.get("account_id") or mail.get("account_id")
    uid = params.get("uid") or mail.get("uid")
    ordner = str(_interp(params.get("folder") or "", ctx)).strip() \
        or str(mail.get("folder") or "INBOX")
    if konto_id is None or uid in (None, ""):
        return None, ordner, None
    konto = await db.get(MailAccount, int(konto_id))
    return konto, ordner, int(uid)


async def _cache_leeren(konto_id: int) -> None:
    """Was der Ablauf am Postfach ändert, darf die Oberfläche nicht aus dem Cache lesen."""
    from .mailbox_cache import entwerten

    await entwerten(konto_id)


async def _mail_flag(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Eine Mail markieren: gelesen, wichtig, beantwortet.

    Der häufigste Handgriff überhaupt und bis jetzt der einzige, den ein Ablauf nicht tun
    konnte: Wer eine Mail einsortiert oder beantwortet hat, will sie danach als gelesen
    wissen, ohne sie noch einmal anzufassen.

    Parameter: `flag` (seen | flagged | answered, Vorgabe `seen`), `on` (Vorgabe an),
    `folder`/`uid`/`account_id` (Vorgabe: die Mail aus dem Auslöser).
    """
    from . import mailbox

    konto, ordner, uid = await _mail_konto(db, params, ctx)
    if konto is None or uid is None:
        return {"action": "mail_flag", "set": False,
                "reason": "keine Mail im Kontext (Auslöser ist keine Mail-Aktion?)"}
    flag = str(_interp(params.get("flag") or "", ctx)).strip().lower() or "seen"
    an = _ja(params.get("on"), ctx) if params.get("on") is not None else True
    await mailbox.flag(konto, ordner, uid, flag, an)
    await _cache_leeren(konto.id)
    return {"action": "mail_flag", "set": True, "flag": flag, "on": an, "uid": uid}


async def _mail_move(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Eine Mail in einen Ordner verschieben — oder ins Archiv, wenn keiner genannt ist.

    Ohne Ziel gilt, was am Konto als Archiv eingetragen ist, samt Muster: So kann ein Ablauf
    „erledigt, weg damit" sagen, ohne den Ordnernamen zu kennen.
    """
    from . import mailbox

    konto, ordner, uid = await _mail_konto(db, params, ctx)
    if konto is None or uid is None:
        return {"action": "mail_move", "moved": False,
                "reason": "keine Mail im Kontext (Auslöser ist keine Mail-Aktion?)"}
    ziel = str(_interp(params.get("target") or "", ctx)).strip()
    if ziel:
        await mailbox.verschieben(konto, ordner, uid, ziel)
    elif konto.folder_archive:
        ziel = await mailbox.archivieren(konto, ordner, uid)
    else:
        return {"action": "mail_move", "moved": False,
                "reason": "kein Ziel genannt und kein Archiv am Konto"}
    await _cache_leeren(konto.id)
    return {"action": "mail_move", "moved": True, "target": ziel, "uid": uid}


async def _mail_anhang(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Holt den Anhang einer Mail in den Kontext — als Base64, wie ihn Werkzeuge erwarten.

    Der Auslöser einer Mail-Aktion legt nur die Beschreibung des Anhangs in den Kontext
    (Name, Typ, Größe): eine Liste von zwanzig Mails soll keine zwanzig PDF durchs Netz
    ziehen. Wer den Inhalt wirklich braucht, holt ihn hier — und zwar genau einmal, an der
    Stelle im Ablauf, an der er gebraucht wird.

    Parameter: `index` (Vorgabe: der Anhang aus dem Auslöser), `context_key` (Vorgabe
    `attachment`), `max_mb` (Vorgabe 25).
    """
    import base64

    from ..models.mail import MailAccount
    from . import mailbox

    mail = dict(ctx.get("mail") or {})
    anhang = dict(ctx.get("anhang") or {})
    konto_id = mail.get("account_id")
    index = params.get("index")
    index = int(index) if index not in (None, "") else anhang.get("index")
    if konto_id is None or mail.get("uid") is None or index is None:
        return {"action": "mail_attachment", "fetched": False,
                "grund": "kein Anhang im Kontext (Auslöser ist keine Mail-Aktion?)"}
    konto = await db.get(MailAccount, int(konto_id))
    if konto is None:
        return {"action": "mail_attachment", "fetched": False, "grund": "Konto gibt es nicht mehr"}

    name, typ, daten = await mailbox.anhang(konto, str(mail.get("folder") or "INBOX"),
                                            int(mail["uid"]), int(index))
    grenze = int(float(params.get("max_mb") or 25) * 1024 * 1024)
    if len(daten) > grenze:
        # Lieber eine klare Ansage als ein Kontext, der die Datenbankzeile sprengt.
        raise ValueError(f"Anhang {name} ist {len(daten) // 1024 // 1024} MB groß "
                         f"(Grenze {grenze // 1024 // 1024} MB)")
    key = str(params.get("context_key") or "attachment")
    inst.context = {**ctx, key: {"filename": name, "content_type": typ, "size": len(daten),
                                 "base64": base64.b64encode(daten).decode()}}
    return {"action": "mail_attachment", "fetched": True, "filename": name, "size": len(daten),
            "context_key": key}


def _ja(wert, ctx: dict) -> bool:
    """Ein Schalter, der auch aus dem Lauf kommen darf.

    Fest (`true`), aus dem Kontext (`"{{ policy.auto }}"`) oder als Bedingung in derselben
    Sprache, die die Weichen sprechen (`{"!": {"var": "policy.auto"}}`). Ohne das müsste ein
    Ablauf für „mal so, mal so“ zwei Knoten führen, die sich nur in einem Häkchen
    unterscheiden — und beide gepflegt werden wollen.
    """
    if isinstance(wert, bool):
        return wert
    if isinstance(wert, dict):
        from .jsonlogic import JsonLogicError, safe_eval
        try:
            return bool(safe_eval(wert, ctx))
        except JsonLogicError:
            return False
    if isinstance(wert, str):
        text = str(_interp(wert, ctx)).strip().lower()
        return text in ("true", "1", "ja", "yes", "an")
    return bool(wert)


async def _assistent_auftrag(db, inst: WorkflowInstance, params: dict, ctx: dict,
                             node_id: str) -> dict:
    """Give the personal assistant a free assignment — no mail, no ticket, no project.

    The generic counterpart to the mail path (`assistant_task` + `assistant_run`), which can
    only read its assignment out of an incoming mail. Here the assignment stands in the node,
    so ANY flow can hand work to the assistant.

    Parameters:
      auftrag      the assignment itself ({{…}} out of the context), required
      titel        heading in the inbox (default: first line of the assignment)
      agent        role of the agent (default `assistent`)
      freigabe     true = the item waits for the person, false (default) = it runs at once.
                   Darf auch aus dem Lauf kommen (`{{ … }}` oder eine Bedingung, siehe `_ja`)
      warten       true = the step waits for the run and puts its answer into the context
      context_key  where the answer lands (default `assistent`)
      timeout_sek  limit for the wait (0 = the engine default)

    Und was der Mail-Weg mitbrachte, bis es ihn nicht mehr gibt — dieselben Angaben, nur
    ohne Mail: `art` (wonach der Eingang sortiert, Vorgabe `auftrag`), `quelle`/`referenz`
    (der Schlüssel gegen Doppelanlage; ohne sie Ablauf und Knoten), `zusammenfassung`,
    `volltext` samt `schwaerzen` (der Volltext wird nur bei `unredacted` überhaupt
    gespeichert), `hinweis` und `meta` für alles Weitere.

    Idempotent per node and instance: a repeated pass (restart, retry) picks up the existing
    item instead of commissioning the work a second time.
    """
    from sqlalchemy import select

    from ..core.redis import enqueue_task
    from ..models.assistant import AssistantTask

    auftrag = str(_interp(params.get("task") or params.get("prompt") or "", ctx)).strip()
    rolle = str(_interp(params.get("agent") or "", ctx)).strip() or "assistent"
    titel = str(_interp(params.get("title") or "", ctx)).strip() \
        or (auftrag.splitlines()[0][:200] if auftrag else "")
    if not (auftrag or titel):
        # Ein Auftragstext ist der Normalfall, aber nicht die Bedingung: Ein Eingang, der
        # seine Sache mitbringt (Betreff, Zusammenfassung, Volltext), ist auch ohne Prompt
        # bearbeitbar — der Assistent hat dann seinen eigenen.
        return {"action": "assistant_task", "started": False, "reason": "kein Auftrag"}
    besitzer = params.get("owner_id") or inst.started_by or _dig(ctx, "eingang.owner_id")
    try:
        besitzer = int(besitzer) if besitzer is not None else None
    except (TypeError, ValueError):
        besitzer = None
    if besitzer is None:
        # Without an owner there is no token and no MCP group, so the run would start and
        # immediately have nothing to work with. Say so instead of failing later.
        return {"action": "assistant_task", "started": False,
                "reason": "kein Besitzer (weder am Ablauf noch am Knoten)"}

    # Woran die Doppelanlage erkannt wird. Ohne eigene Angabe ist es der Knoten selbst (ein
    # Neustart soll nicht zweimal beauftragen); mit eigener Angabe die Sache, um die es geht
    # — dieselbe Mail über zwei Wege bleibt ein Eingang.
    quelle = str(_interp(params.get("source") or "", ctx)).strip() or f"ablauf:{inst.definition_id}"
    quelle_ref = str(_interp(params.get("reference") or "", ctx)).strip() or f"{inst.id}:{node_id}"
    task = (await db.execute(select(AssistantTask).where(
        AssistantTask.source == quelle,
        AssistantTask.source_ref == quelle_ref))).scalar_one_or_none()
    neu_angelegt = task is None
    if task is None:
        freigabe = _ja(params.get("approval"), ctx)
        schwaerzen = str(_interp(params.get("redaction") or "", ctx)).strip() or "redacted"
        volltext = str(_interp(params.get("full_text") or "", ctx))
        zusatz = _interp_deep(params.get("meta"), ctx) if isinstance(params.get("meta"), dict) else {}
        task = AssistantTask(
            owner_user_id=besitzer,
            kind=str(_interp(params.get("kind") or "", ctx)).strip() or "task",
            source=quelle, source_ref=quelle_ref,
            title=titel[:500],
            category=str(_interp(params.get("category") or "", ctx))[:80],
            priority=str(_interp(params.get("priority")
                                 or params.get("priority") or "normal", ctx)) or "normal",
            redacted_summary=str(_interp(params.get("summary") or "", ctx)),
            redaction=schwaerzen,
            action_hint=str(_interp(params.get("hint") or "", ctx))[:500],
            # Der Volltext wird nur gespeichert, wenn nicht geschwärzt werden soll. Sonst
            # läge das, wovor die Schwärzung schützt, gleich daneben.
            raw_body=volltext if (volltext and schwaerzen == "unredacted") else None,
            # The assignment IS the prompt; the worker takes it out of `meta.prompt`, the
            # same way it takes the ported mail prompt of a webhook.
            meta={**zusatz, "agent": rolle, "prompt": auftrag,
                  "ablauf": {"instanz": inst.id, "knoten": node_id,
                             "definition": inst.definition_id}},
            status="new" if freigabe else "approved",
        )
        db.add(task)
        await db.flush()

    task_id = f"assistant-{task.id}"
    warten = _ja(params.get("wait"), ctx)
    if neu_angelegt and task.status == "approved":
        await enqueue_task({"kind": "assistant", "task_id": task_id,
                            "assistant_task_id": int(task.id)})
    ergebnis = {"action": "assistant_task", "task_id": task.id, "agent": rolle,
                "status": task.status, "started": task.status == "approved",
                "reused": not neu_angelegt}
    if warten and task.status == "approved":
        ergebnis["_wait"] = {"task_id": task_id,
                             "timeout": int(params.get("timeout_sec") or 0),
                             "context_key": str(params.get("context_key") or "assistant")}
    # `auftrag` und `task` tragen dasselbe: Der Mail-Weg hat immer `task` gelesen, und die
    # Karte danach soll den Eingang finden, ohne zu wissen, welcher Knoten ihn angelegt hat.
    inst.context = {**ctx, "task": {"task_id": task.id, "status": task.status,
                                    "agent": rolle},
                    "task": {"id": task.id, "status": task.status,
                             "auto": task.status == "approved"}}
    return ergebnis


async def _agent_lauf(db, inst: WorkflowInstance, params: dict, ctx: dict,
                     node_id: str) -> dict:
    """Einen Agenten arbeiten lassen und auf sein Ergebnis warten — ohne Ticket, ohne Eingang.

    Der dritte Weg, einen Agenten zu starten, und bis jetzt der einzige, den ein Ablauf nicht
    gehen konnte: `agent_task` verlangt ein Ticket, `assistent_auftrag` legt einen Eingang an,
    und der freie Lauf steckte in der Job-Art `prompt` fest. Hier ist er ein Knoten — damit
    lässt sich „fragen, prüfen, melden“ bauen, statt drei Jobs hintereinanderzuhängen.

    Parameter:
      auftrag      was der Agent tun soll ({{…}} aus dem Kontext), nötig
      agent        Rolle (Vorgabe `assistent`)
      titel        Überschrift des Laufs (Vorgabe: erste Zeile des Auftrags)
      context_key  wohin das Ergebnis kommt (Vorgabe `lauf`) — `.output` ist der Text
      timeout_sek  Zeitgrenze (0 = Vorgabe der Engine)
      warten       aus: nur anstoßen, nicht auf das Ergebnis warten (Vorgabe: an)
    """
    from ..core.redis import enqueue_task

    auftrag = str(_interp(params.get("task") or params.get("prompt") or "", ctx)).strip()
    if not auftrag:
        return {"action": "agent_run", "started": False, "reason": "kein Auftrag"}
    rolle = str(_interp(params.get("agent") or "", ctx)).strip() or "assistent"
    titel = str(_interp(params.get("title") or "", ctx)).strip() or auftrag.splitlines()[0][:200]
    besitzer = params.get("owner_id") or inst.started_by
    if besitzer is None:
        # Ohne Besitzer kein Token und keine Werkzeuge — der Lauf startete und stünde sofort
        # mit leeren Händen da.
        return {"action": "agent_run", "started": False, "reason": "kein Besitzer"}

    task_id = f"lauf-{inst.id}-{node_id}"
    await enqueue_task({"kind": "agent_frei", "task_id": task_id, "agent": rolle,
                        "prompt": auftrag, "name": titel, "owner_id": int(besitzer)})
    ergebnis = {"action": "agent_run", "started": True, "agent": rolle, "task_id": task_id}
    if params.get("wait") is None or _ja(params.get("wait"), ctx):
        ergebnis["_wait"] = {"task_id": task_id,
                             "timeout": int(params.get("timeout_sec") or 0),
                             "context_key": str(params.get("context_key") or "run")}
    return ergebnis


async def _reihe_schreiben(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Einen Punkt in eine Datenreihe schreiben — gleich welcher Art.

    Es gibt genau eine Sorte Reihe im Kern, mit einer Art daran (Zahl, Standort, Text). Also
    gibt es auch genau eine Aktion, die hineinschreibt; welche Felder gelten, entscheidet die
    Art. Drei Aktionen fuer "haenge einen Punkt an" waeren dieselbe Dreifachstruktur, die es
    in den Tabellen nicht mehr gibt.

    Gedacht ist das fuer Quellen, die ihre Daten nebenbei mitschicken: Traccar schickt zu
    jedem Alarm die ganze Position samt Akkustand mit. Wer selbst und laufend meldet, nimmt
    besser den Aufnahmepfad (`/api/ingest/<token>`) — der kostet keine Ablauf-Instanz je Punkt.

    Parameter (`reihe`, `art`, `wert`, `quelle` und `pflicht` gehen auch deutsch, weil
    `workflow_terms.PARAMS` sie abbildet):
      series       Schluessel der Reihe (`tracker.shelter`), noetig
      kind         number | location | text — nur noetig, wenn die Reihe neu entsteht
      value        bei `number`: die Zahl
      lat · lon    bei `location`: die Koordinaten
      title · body bei `text`: Ueberschrift und Text
      ts           Zeitstempel (Vorgabe: jetzt) — Unix-Sekunden oder ISO
      accuracy · altitude · speed · course · battery   was ein Geraet sonst mitschickt
      name · color Anzeigename und Farbe, wenn die Reihe neu entsteht
      source       woher der Punkt kam (Vorgabe `flow`)
      required     ohne Wert abbrechen statt uebergehen
      context_key  wo das Ergebnis landet (Vorgabe `series`)
    """
    from . import series as reihen
    from . import series_formats

    schluessel = str(_interp(params.get("series") or "", ctx)).strip()
    if not schluessel:
        raise ValueError("reihe: keine Reihe genannt")
    schluessel_ctx = str(params.get("context_key") or "series")
    besitzer = await _besitzer(db, inst)

    # Die Art steht an der Reihe, sobald es sie gibt. Der Parameter zaehlt nur beim Anlegen —
    # sonst koennte ein Ablauf die Art einer bestehenden Reihe unter sich wegziehen.
    vorhanden = await reihen.reihe(db, besitzer, schluessel)
    art = vorhanden.kind if vorhanden else str(params.get("kind") or "number").strip()
    if art not in ("number", "location", "text"):
        raise ValueError(f"reihe: unbekannte Art '{art}'")

    eintrag, fehlt = _eintrag_bauen(art, params, ctx, series_formats)
    if fehlt:
        if params.get("required"):
            raise ValueError(f"reihe: {fehlt}")
        # Der Normalfall, nicht der Fehler: Ein Geraet meldet auch seinen Zustand, wenn es
        # gerade keinen Fix hat oder einen Wert nicht kennt.
        inst.context = {**ctx, schluessel_ctx: {"series": schluessel, "kind": art,
                                                "stored": False, "reason": fehlt}}
        return {"action": "series_record", "series": schluessel, "kind": art,
                "stored": False, "ignored": True, "skipped": True, "reason": fehlt}

    reihe = vorhanden or await reihen.reihe(
        db, besitzer, schluessel, kind=art, anlegen=True,
        name=str(_interp(params.get("name") or "", ctx)).strip(),
        farbe=str(params.get("color") or ""))

    ergebnis = await reihen.aufnehmen(db, reihe, [eintrag])
    stand = reihe.state or {}
    aus = {"series": schluessel, "kind": art, "stored": ergebnis["accepted"] > 0,
           "skipped": ergebnis["skipped"], "points": reihe.points,
           **{n: stand.get(n) for n in ("value", "lat", "lon", "battery", "accuracy", "title")
              if stand.get(n) is not None},
           "places": stand.get("places") or [],
           "entered": ergebnis["betreten"], "left": ergebnis["verlassen"]}
    inst.context = {**ctx, schluessel_ctx: aus}
    return {"action": "series_record", **aus}


def _eintrag_bauen(art: str, params: dict, ctx: dict, formate) -> tuple[dict, str]:
    """Aus den Parametern einen Punkt machen. Zweiter Rueckgabewert nennt, was fehlt."""
    ts = formate.zeitpunkt(_interp(params.get("ts"), ctx))
    quelle = str(params.get("source") or "flow")

    if art == "location":
        # Ueber dieselbe Formatschicht wie der Aufnahmepfad: Dann gelten hier dieselben
        # Regeln fuer Komma-Zahlen, Zeitstempel und den Akku als Bruchteil oder Prozent.
        punkte = formate.normalisiere({
            "lat": _interp(params.get("lat"), ctx), "lon": _interp(params.get("lon"), ctx),
            "ts": _interp(params.get("ts"), ctx),
            "accuracy": _interp(params.get("accuracy"), ctx),
            "altitude": _interp(params.get("altitude"), ctx),
            "speed": _interp(params.get("speed"), ctx),
            "course": _interp(params.get("course"), ctx),
            "battery": _interp(params.get("battery"), ctx),
            "source": quelle,
        })
        return (punkte[0], "") if punkte else ({}, "keine Position in der Nutzlast")

    if art == "text":
        text = str(_interp(params.get("body") or params.get("text") or "", ctx))
        if not text.strip():
            return {}, "kein Text in der Nutzlast"
        titel = str(_interp(params.get("title") or "", ctx)).strip()
        if not titel:
            titel = next((z.strip("# ").strip() for z in text.splitlines() if z.strip()), "")
        return {"title": titel[:200], "body": text, "ts": ts, "source": quelle,
                "format": str(params.get("format") or "markdown")}, ""

    roh = _interp(params.get("value"), ctx)
    if isinstance(roh, str):
        roh = roh.strip().replace("%", "").replace(",", ".")
    if roh is None or (isinstance(roh, str) and roh.lower() in ("", "none", "null")):
        return {}, "kein Wert in der Nutzlast"
    try:
        return {"value": float(roh), "ts": ts, "source": quelle}, ""
    except (TypeError, ValueError):
        return {}, f"'{roh}' ist keine Zahl"


async def _dokument(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Einen Text in einer Ablage hinlegen — das Gegenstück zum Messwert.

    Ein Ablauf konnte seinen Text nirgends lassen: Der Rückblick, den ein Agent jeden Morgen
    schreibt, landete im Ausgabefeld eines Job-Laufs, abgeschnitten und ohne Ansicht. Eine
    Ablage ist ein Name und eine Folge von Fassungen; was drinsteht, weiß der Ablauf.

    Parameter:
      ablage       Schlüssel der Ablage (`ki-tech-news`), nötig
      text         der Text selbst ({{…}} aus dem Kontext), nötig
      titel        Überschrift dieser Fassung (Vorgabe: erste Überschrift oder Zeile)
      name         Anzeigename der Ablage, wenn sie neu entsteht
      format       markdown (Vorgabe) oder text
      behalten     wie viele Fassungen aufgehoben werden (Vorgabe 60)
      context_key  wo der Verweis landet (Vorgabe `dokument`) — `.url` ist der Link
    """
    from ..config import settings
    from . import documents

    schluessel = str(_interp(params.get("storage") or params.get("key") or "", ctx)).strip()
    text = str(_interp(params.get("text") or "", ctx))
    if not schluessel:
        return {"action": "document", "stored": False, "reason": "keine Ablage genannt"}
    if not text.strip():
        # Nichts hinlegen ist besser als eine leere Fassung: Sie verdrängte im Verlauf eine
        # echte und stünde als „Stand von heute" da, obwohl nichts erarbeitet wurde.
        return {"action": "document", "stored": False, "reason": "kein Text"}
    besitzer = params.get("owner_id") or inst.started_by
    titel = str(_interp(params.get("title") or "", ctx)).strip()
    if not titel:
        erste = next((z.strip("# ").strip() for z in text.splitlines() if z.strip()), "")
        titel = erste[:200]
    eintrag = await documents.hinlegen(
        db, int(besitzer) if besitzer is not None else None, schluessel,
        titel=titel, text=text,
        format=str(params.get("format") or "markdown"),
        name=str(_interp(params.get("name") or "", ctx)).strip(),
        behalten=int(params.get("keep") or 0),
        context={"ablauf": inst.definition_id, "instanz": inst.id,
                 **({"job": (inst.context or {}).get("job")} if (inst.context or {}).get("job") else {})})
    schluessel_ctx = str(params.get("context_key") or "document")
    inst.context = {**ctx, schluessel_ctx: {
        "id": eintrag.id, "storage": schluessel, "title": titel,
        # Der Link, der in eine Meldung gehört. Ohne Basis-Adresse bleibt er relativ — dann
        # ist er in der Oberfläche richtig und im Messenger unbrauchbar, was besser ist als
        # ein Link auf „localhost".
        "url": f"{settings.app_base_url.rstrip('/')}/documents/{schluessel}"
               if settings.app_base_url else f"/documents/{schluessel}"}}
    return {"action": "document", "stored": True, "entry_id": eintrag.id,
            "storage": schluessel, "context_key": schluessel_ctx}


async def _dokument_lesen(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Die letzte Fassung einer Ablage in den Kontext holen — für Abläufe, die auf dem
    aufbauen, was beim letzten Mal herauskam."""
    from . import documents

    schluessel = str(_interp(params.get("storage") or params.get("key") or "", ctx)).strip()
    besitzer = params.get("owner_id") or inst.started_by
    eintrag = await documents.letzte(
        db, int(besitzer) if besitzer is not None else None, schluessel) if schluessel else None
    schluessel_ctx = str(params.get("context_key") or "document")
    inst.context = {**ctx, schluessel_ctx: {
        "found": eintrag is not None,
        "title": eintrag.title if eintrag else "",
        "text": eintrag.body if eintrag else "",
        "ts": (eintrag.ts.isoformat() if eintrag and eintrag.ts else "")}}
    return {"action": "document_read", "found": eintrag is not None, "storage": schluessel}


async def _job_pausieren(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Den Zeitplan anhalten, aus dem dieser Lauf kommt.

    Ein Skript-Job konnte sich mit einem eigenen Rückgabewert selbst stilllegen („erledigt,
    weck mich nicht wieder“). Das steckte in der Job-Art fest; als Knoten kann es jeder
    Ablauf — die Erinnerung, die aufhört, wenn die Sache getan ist.
    """
    from ..models.ops import Job

    job_id = _as_int(_interp(params.get("job_id"), ctx)) if params.get("job_id") is not None \
        else _as_int(_dig(ctx, "job.id"))
    if job_id is None:
        return {"action": "job_pause", "paused": False, "reason": "kein Job im Kontext"}
    job = await db.get(Job, int(job_id))
    if job is None:
        return {"action": "job_pause", "paused": False, "reason": "Job nicht gefunden"}
    job.paused = not _ja(params.get("resume"), ctx)
    return {"action": "job_pause", "paused": job.paused, "job_id": job.id}


async def _skript(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Ein hinterlegtes Skript ausführen — dieselbe Prüfung wie beim Skript-Job.

    Nur was im erlaubten Verzeichnis liegt, läuft; der Pfad kommt aus dem Ablauf, nicht aus
    der Nutzlast eines Fremden.
    """
    import asyncio

    from .scheduler import _resolve_script

    befehl = str(_interp(params.get("command") or "", ctx)).strip()
    skript = _resolve_script(befehl)
    if not skript:
        return {"action": "script", "ok": False,
                "error": f"Skript nicht im erlaubten Verzeichnis: {befehl}"}
    argumente = _interp_deep(params.get("args") or [], ctx)
    grenze = int(params.get("timeout_sec") or 600)
    try:
        p = await asyncio.create_subprocess_exec(
            skript, *[str(a) for a in argumente],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        aus, _ = await asyncio.wait_for(p.communicate(), timeout=grenze)
        rc = p.returncode or 0
        text = aus.decode("utf-8", "replace")[:20000]
    except asyncio.TimeoutError:
        return {"action": "script", "ok": False, "error": "Zeitgrenze überschritten"}
    schluessel = str(params.get("context_key") or "script")
    inst.context = {**ctx, schluessel: {"output": text, "exit_code": rc, "ok": rc == 0}}
    return {"action": "script", "ok": rc == 0, "exit_code": rc, "context_key": schluessel}



def _antwort(inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Set the answer this run gives back to whoever started it.

    A webhook that waits (`response_timeout`) reads exactly this: `context.antwort`. Free
    text goes into `text`, a structured answer into the remaining fields — both go through
    the same `{{…}}` templating as everywhere else.
    """
    if params.get("text") is not None:
        antwort = _interp(params.get("text") or "", ctx)
    else:
        felder = params.get("fields") if isinstance(params.get("fields"), dict) else \
            {k: v for k, v in params.items() if k not in ("felder", "text")}
        antwort = _interp_deep(felder, ctx)
    schluessel = str(params.get("context_key") or "answer")
    inst.context = {**ctx, schluessel: antwort}
    return {"action": "answer", "context_key": schluessel,
            "fields": list(antwort) if isinstance(antwort, dict) else "text"}


async def run_action(db, inst: WorkflowInstance, node: dict) -> dict:
    cfg = _config(node)
    action, params = _normalize_action(cfg)
    # Englisch ist die Vorgabe; die deutschen Namen bleiben lesbar, weil sie in
    # veröffentlichten Fassungen stehen (`services/workflow_terms.py` schreibt sie um,
    # aber eine Instanz kann an einer alten Fassung hängen).
    action = terms.normalisiere_aktion(action)
    params = terms.normalisiere_params(params)
    ctx = dict(inst.context or {})

    if action == "set_context":
        # The editor delivers the assignments directly as params ({key:val}), an explicit
        # {set:{...}} is supported as well.
        raw = params.get("set") if isinstance(params.get("set"), dict) else params
        updates = {k: v for k, v in raw.items() if k != "set"}
        applied = {k: _interp(v, ctx) for k, v in updates.items()}
        # Assign a new dict so SQLAlchemy notices the JSON column changed.
        inst.context = {**ctx, **applied}
        return {"action": "set_context", "keys": list(applied.keys())}

    if action == "comment":
        text = _interp(params.get("text") or params.get("message") or "", ctx)
        if inst.issue_id and text:
            from .comments import add_system_comment
            await add_system_comment(db, inst.issue_id, text, author_label="Workflow")
        return {"action": "comment", "text": text, "written": bool(inst.issue_id and text)}

    if action == "create_ticket":
        return await _create_ticket(db, inst, params, ctx)

    if action == "set_board_status":
        return await _set_board_status(db, inst, params, ctx)

    if action == "webhook":
        # A destination in the parameter set wins: then it is a normal destination call.
        if params.get("destination") or params.get("target"):
            return await _http_request(db, inst, params, ctx)
        return await _webhook(db, inst, params, ctx)

    if action == "http_request":
        return await _http_request(db, inst, params, ctx)

    if action == "tool_call":
        return await _tool_call(db, inst, params, ctx)

    if action in _ALT_AKTIONEN or action == "set_status":
        return await _set_status(db, inst, params, ctx)

    if action == "set_field":
        return await _set_field(db, inst, params, ctx)

    if action == "refresh_facts":
        return await _refresh_facts(db, inst, ctx)

    if action == "assign_agent":
        return await _assign_agent(db, inst, params, ctx)

    if action == "set_cap_baseline":
        return await _set_cap_baseline(db, inst)

    if action == "start_testenv":
        return await _testenv(db, inst, start=True)

    if action == "stop_testenv":
        return await _testenv(db, inst, start=False)

    if action == "accept_merge":
        return await _accept_merge(db, inst, params)

    if action == "deploy":
        return await _deploy(db, inst, params)

    if action == "split_tickets":
        return await _split_tickets(db, inst, params)

    if action == "stop_agent":
        return await _stop_agent(db, inst)

    # Mail intake (slot `mail_intake`): classify, judge, ask back, file away or hand to the
    # assistant. It lives in a module of its own because that is where all the mail
    # knowledge comes together, here we only record that these steps exist.
    from .mail_actions import HANDLER as MAIL_HANDLER
    if action in MAIL_HANDLER:
        return await MAIL_HANDLER[action](db, inst, params, ctx)

    if action == "mail_flag":
        return await _mail_flag(db, inst, params, ctx)

    if action == "mail_move":
        return await _mail_move(db, inst, params, ctx)

    if action == "mail_attachment":
        return await _mail_anhang(db, inst, params, ctx)

    if action == "agent_run":
        return await _agent_lauf(db, inst, params, ctx, str(node.get("id") or ""))

    if action == "script":
        return await _skript(db, inst, params, ctx)

    if action == "document":
        return await _dokument(db, inst, params, ctx)

    if action == "document_read":
        return await _dokument_lesen(db, inst, params, ctx)

    if action == "job_pause":
        return await _job_pausieren(db, inst, params, ctx)

    if action == "assistant_task":
        return await _assistent_auftrag(db, inst, params, ctx, str(node.get("id") or ""))

    if action == "answer":
        return _antwort(inst, params, ctx)

    if action == "note_append":
        return await _notiz_anhaengen(db, inst, params, ctx)

    if action == "series_record":
        return await _reihe_schreiben(db, inst, params, ctx)
    if action == "metric_record":
        return await _messwert(db, inst, params, ctx)

    if action == "metric_read":
        return await _messreihe_lesen(db, inst, params, ctx)

    if action == "notify":
        target = await _resolve_target(db, inst, params.get("to") or {})
        title = _interp(params.get("title") or "Workflow-Benachrichtigung", ctx)
        body = _interp(params.get("text") or params.get("message") or "", ctx)
        from ..models.user import User
        from .notify import zustellen
        # The channel is optional: without one the person decides how they are reached. A
        # flow often learns its recipient only at runtime and cannot know which messenger
        # they use.
        kanal = str(_interp(params.get("channel") or "", ctx)).strip()
        empfaenger = await db.get(User, target) if target is not None else None
        # Throttle: "at most the same thing every N minutes". Without a key of its own the
        # node throttles itself, because for the normal case a number should be enough
        # without inventing a name. A key with `{{ … }}` separates by device or kind of
        # alarm so two different incidents do not mute each other.
        drossel = float(params.get("throttle_minutes") or 0)
        drossel_key = str(_interp(params.get("throttle_key") or "", ctx)).strip()
        if drossel > 0 and not drossel_key:
            drossel_key = f"ablauf:{inst.definition_id}:{node.get('id')}"
        # Art und Bezug machen aus der Nachricht eine Karte, auf der man handeln kann: der
        # Bot hängt seine Knöpfe an der Art auf und findet über den Bezug die Sache, um die
        # es geht (ein Spam-Urteil zum Zurückholen, ein Eingang zum Freigeben). Ohne beides
        # bleibt es eine gewöhnliche Meldung — der Normalfall.
        art = str(_interp(params.get("kind") or "", ctx)).strip() \
            or "workflow_notify"
        roh_bezug = params.get("ref") if isinstance(params.get("ref"), dict) else {}
        bezug: dict[str, int] = {}
        for feld, wert in roh_bezug.items():
            wert = _interp(wert, ctx)
            zahl = _as_int(wert)
            if zahl is not None:
                bezug[str(feld)] = zahl
        weg = await zustellen(db, user=empfaenger, kind=art, title=title,
                              body=body, kanal=kanal, project_id=inst.project_id,
                              issue_id=inst.issue_id, bezug=bezug or None,
                              drossel_key=drossel_key, drossel_minuten=drossel)
        return {"action": "notify", "user_id": target, "kind": art,
                **({"bezug": bezug} if bezug else {}), **weg}

    # Unknown or deliberate noop action: not an error, so the workflow keeps running.
    return {"action": "noop", "requested": action}


async def _resolve_target(db, inst: WorkflowInstance, to: dict) -> int | None:
    """Target user of a notify action (like the assignee resolution, kept minimal)."""
    mode = to.get("mode", "user")
    if mode == "user":
        uid = to.get("user_id")
        return int(uid) if uid is not None else None
    if mode == "reporter" and inst.issue_id:
        from ..models.ticket import Issue
        issue = await db.get(Issue, inst.issue_id)
        return issue.reporter_id if issue else None
    if mode == "context":
        val = _dig(inst.context or {}, to.get("path") or "")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    if mode == "role" and inst.project_id and to.get("role"):
        from ..models.enums import ProjectRole
        from ..models.project import ProjectMember
        from sqlalchemy import select
        try:
            prole = ProjectRole(to["role"])
        except ValueError:
            return None
        m = (
            await db.execute(
                select(ProjectMember)
                .where(ProjectMember.project_id == inst.project_id, ProjectMember.role == prole)
                .order_by(ProjectMember.id)
            )
        ).scalars().first()
        return m.user_id if m else None
    return None
