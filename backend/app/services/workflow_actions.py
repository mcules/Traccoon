"""Handlers for the auto_action nodes of the workflow engine.

An auto_action node carries an `action` type plus parameters in its `config`. `run_action`
performs the side effect and returns a result dict (persisted in StepRun.result). Only what
is explicitly meant for it reaches the outside: `http_request` and `webhook` call foreign
systems, everything else stays inside Traccoon.

Supported actions:
  series_read         {series, target?, window_days?, silence_hours?, context_key?}
                                                where a series stands and where it heads
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
  assistant_session   {op, ...}                 conversations of the assistant: create, close,
                                                delete (the ONLY way one is deleted)
  mail_anhang         {index?, context_key?, max_mb?}  fetch a mail attachment as base64
  mail_document       {filename, doc_id, doc_url, title?}  report back what an attachment became
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
    from .workflow_expr import fill
    return fill(value, ctx)


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
    foreign_target = pid is not None and pid != inst.project_id
    pid = pid or inst.project_id
    if pid is None:
        raise ValueError("create_ticket: no project_id (neither a parameter nor the instance project)")
    # A flow may only create where the person behind it could create as well. Without this
    # check a free-standing flow would be a way into foreign projects: the definition
    # belongs to its creator, the target project does not.
    if foreign_target and inst.started_by is not None:
        from ..api.deps import build_access
        from ..models.enums import GlobalRole, ProjectRole
        from ..models.user import User as _User
        starter = await db.get(_User, inst.started_by)
        target = await db.get(Project, pid)
        if starter is None or target is None:
            raise ValueError("create_ticket: target project or trigger unknown")
        if starter.global_role != GlobalRole.admin:
            access = await build_access(target, starter, db)
            if not access.has_role(ProjectRole.member):
                raise ValueError(
                    f"create_ticket: no rights on the project {target.key}")
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
_OLD_ACTIONS = {"set_agent_status", "set_purchase_status"}

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


async def _artifact_from(db, inst: WorkflowInstance):
    """The shared artifact row of the flow, which the custom fields hang off.

    Prefers the binding on the instance. Older instances (from before the shared identity)
    do not have it yet and are resolved through the ticket or the hardware item.
    """
    from ..models.artifact import Artifact
    from . import artifacts as svc

    if inst.artifact_id:
        return await db.get(Artifact, inst.artifact_id)
    if inst.issue_id:
        issue = await _issue_of(db, inst)
        return await svc.ensure_for_issue(db, issue) if issue else None
    if inst.hardware_asset_id:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, inst.hardware_asset_id)
        return await svc.ensure_for_asset(db, asset) if asset else None
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
    artifact = await _artifact_from(db, inst)
    if artifact is None:
        return {"action": "set_field", "applied": False, "reason": "no artifact on this flow"}

    field = next((f for f in await af.fields_of(db, artifact.type_id, artifact.project_id)
                 if f.key == key), None)
    if field is None:
        raise ValueError(f"The field '{key}' does not exist on this artifact")

    # Values may arrive as a list, as a single value or comma separated.
    raw = params.get("values", params.get("value"))
    if raw is None:
        new = []
    elif isinstance(raw, list):
        new = [_interp(v, ctx) for v in raw]
    else:
        text = str(_interp(raw, ctx))
        new = [t.strip() for t in text.split(",")] if "," in text else [text]
    new = [v for v in new if str(v).strip() != ""]

    mode = str(params.get("mode") or "set").lower()
    if mode in ("add", "remove"):
        existing = [str(v) for v in (await af.values_of(db, artifact.id)).get(key, [])]
        if mode == "add":
            new = existing + [str(v) for v in new if str(v) not in existing]
        else:
            path = {str(v) for v in new}
            new = [v for v in existing if v not in path]

    marked = await af.set_values(db, artifact.id, field, new)
    inst.context = {**ctx, "fields": {**(ctx.get("fields") or {}), key: marked}}
    return {"action": "set_field", "field": key, "mode": mode, "values": marked}


async def _set_status(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Set the state of the bound artifact: ticket, hardware or a custom type.

    The ONE way inside the graph. Which values are allowed comes from the artifact registry
    (Administration, artifacts), and the editor only shows the states of the subject the
    flow hangs off. The old names `set_agent_status` and `set_purchase_status` end up here
    through `_ALT_AKTIONEN` as well, because they still sit in published versions.
    """
    from . import artifacts as svc
    from ..models.hardware import HardwareAsset

    value = str(_interp(params.get("status") or params.get("value") or "", ctx)).strip()
    if not value:
        import logging
        logging.getLogger("workflow_actions").warning(
            "set_status without a state (template %r empty), going to hold", params.get("status"))
        value = "hold"
    issue = await _issue_of(db, inst)
    asset = (await db.get(HardwareAsset, inst.hardware_asset_id)
             if inst.hardware_asset_id else None)
    result = await svc.apply_status(
        db, subject_kind=inst.subject_kind, issue=issue, asset=asset, status_key=value,
        reason=str(_interp(params.get("reason") or params.get("hold_reason") or "", ctx)).strip(),
    )
    # Messages stay on the ticket (board, messenger and bell read agent_status).
    if issue is not None and params.get("notify", True) and value in _NOTIFY_ON:
        kind, label = _NOTIFY_ON[value]
        from .notify import notify_issue
        detail = (ctx.get("agent") or {}).get("summary") or issue.summary
        if value == "hold" and issue.hold_reason:
            label = f"blockiert ({issue.hold_reason.value})"
        await notify_issue(db, issue, kind, f"{issue.key}: {label}", str(detail)[:400])
        result["notified"] = True
    if issue is not None:
        from .events import emit
        await emit(db, "issue.done" if value == "done" else "issue.agent_status_changed",
                   project_id=issue.project_id, issue_id=issue.id,
                   payload={"issue": {
                       "key": issue.key, "agent_status": value,
                       "hold_reason": issue.hold_reason.value if issue.hold_reason else None}})
    return {"action": "set_status", **result}


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
    and a real stack directory (never the maintenance or host project itself)."""
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
        db, umbrella.id, f"✅ The split was taken over — {len(keys)} sub-tasks created.",
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


async def _owner(db, inst: WorkflowInstance) -> int | None:
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
    from .workflow_tools import call

    name = str(_interp(params.get("tool") or params.get("name") or "", ctx)).strip()
    arguments = _interp_deep(params.get("arguments") or params.get("args") or {}, ctx)
    if not isinstance(arguments, dict):
        arguments = {}
    result = await call(db, await _owner(db, inst), name, arguments)
    key = str(params.get("context_key") or "tool")
    inst.context = {**ctx, key: result}
    if params.get("fail_on_error") and not result["ok"]:
        raise ValueError(f"Tool {name!r}: {result.get('error', 'failed')}")
    return {"action": "tool_call", "tool": name, "ok": result["ok"],
            "error": result.get("error")}


async def _note_append(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
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
    from .workflow_tools import call

    path = str(_interp(params.get("path") or params.get("path") or "", ctx)).strip()
    text = str(_interp(params.get("text") or "", ctx)).strip()
    heading = str(_interp(params.get("heading") or params.get("heading") or "",
                               ctx)).strip()
    key = str(params.get("context_key") or "note")
    if not path or not text:
        # Not an error: a flow that has nothing to write should not fail because of it.
        inst.context = {**ctx, key: {"ok": False, "error": "no path or no text"}}
        return {"action": "note_append", "ok": False, "reason": "leer"}

    arguments: dict = {"target": {"type": "path", "path": path}, "content": text}
    if heading:
        # A section is addressed as an object, and it has to be created when it is missing:
        # otherwise the call fails on a note that does not carry that heading yet. Without
        # this the line lands at the end of the file, which is where nobody looks for a task.
        arguments["section"] = {"type": "heading", "target": heading}
        arguments["createTargetIfMissing"] = True
    # With the server prefix: without it the session finds no tool and answers with a HINT
    # AS TEXT, and `aufrufen` reports ok because it got an answer. The note stays empty and
    # nobody notices. The name may be overridden, for a vault hanging off another server.
    tool = str(params.get("tool") or "obsidian__obsidian_append_to_note").strip()
    result = await call(db, await _owner(db, inst), tool, arguments)
    inst.context = {**ctx, key: result}
    # The return value names the action the way it is called. It lands in the step log and
    # not in the context — the context key `notiz` therefore stays as it is: it appears in
    # stored graphs.
    return {"action": "note_append", "path": path, "ok": result["ok"],
            "error": result.get("error")}


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


def _number(params: dict, ctx: dict, *names, default: float = 0.0) -> float:
    """A numeric parameter that may also come from the context.

    Numbers used to sit literally in the node. As soon as the same flow is used more than
    once (another job, another series, another threshold) they come from outside and read
    as `{{ still_stunden }}`. Without substitution the step fails on a text that was meant
    as a number.
    """
    for name in names:
        if name in params and params[name] not in (None, ""):
            raw = _interp(params[name], ctx)
            if isinstance(raw, str):
                raw = raw.strip().replace(",", ".")
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"'{name}': '{raw}' is not a number")
    return default


def _no_measurement(inst: WorkflowInstance, ctx: dict, params: dict, key: str,
                   reason: str, raw=None, last=None, unit: str = "") -> dict:
    """Nothing recorded, but nothing broken either.

    Two cases end up here: the value is missing (an event without a reading) or it is not
    credible (the device reports 127 % when it does not know the charge). In both cases the
    last credible reading stays in the context, otherwise the next node writes the nonsense
    into the message.
    """
    key_ctx = str(params.get("context_key") or "metric")
    before = ctx.get(key_ctx) if isinstance(ctx.get(key_ctx), dict) else {}
    inst.context = {**ctx, key_ctx: {**before, "series": key, "ignored": True,
                                     "warn": False, "value": last, "unit": unit,
                                     "raw": raw}}
    return {"action": "metric_record", "series": key, "ignored": True, "skipped": True,
            "reason": reason, "last_good": last}


async def _measurement(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
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
    raw = _interp(params.get("value"), ctx)
    if isinstance(raw, str):
        raw = raw.strip().replace("%", "").replace(",", ".")
    # A missing value is usually not a defect but the nature of the thing: a tracker
    # reports "I am online" or "I am down" without a position, and therefore without a
    # charge level. The step then has nothing to do and should not look as if something
    # broke. Whoever needs the value sets `pflicht`.
    missing = raw is None or (isinstance(raw, str) and raw.lower() in ("", "none", "null"))
    if missing and not params.get("required"):
        # The last known reading stays in the context: on a failure notice it is the
        # most interesting number still available.
        known = await metrics.series(db, await _owner(db, inst), key)
        return _no_measurement(inst, ctx, params, key, "no value in the payload",
                              last=known.last_value if known else None,
                              unit=known.unit if known else "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"messwert: '{raw}' is not a number")

    # Devices report nonsense when they do not know something: the tracker sends
    # `batteryLevel: 127` as soon as the charge is unknown. A single point like that bends
    # the line from "empty in two weeks" into "rising slightly", so a value outside the
    # bounds never enters the series.
    has_below = params.get("min", params.get("minimum")) not in (None, "")
    has_above = params.get("max", params.get("maximum")) not in (None, "")
    below = _number(params, ctx, "min", "minimum") if has_below else None
    above = _number(params, ctx, "max", "maximum") if has_above else None
    outside = ((below is not None and value < below)
                  or (above is not None and value > above))
    if outside:
        # During alarms the tracker reported "231 %", and without this bound that went out.
        existing = await metrics.series(db, await _owner(db, inst), key)
        return _no_measurement(inst, ctx, params, key, f"outside {below}…{above}", raw=value,
                              last=existing.last_value if existing else None,
                              unit=existing.unit if existing else "")

    target = _number(params, ctx, "target")
    forewarn = _number(params, ctx, "warn_days", default=7.0)
    window = int(_number(params, ctx, "window_days",
                        default=float(metrics.WINDOW_DAYS)))

    owner = await _owner(db, inst)
    series_row, _point = await metrics.record(
        db, owner, key, value,
        name=str(_interp(params.get("name") or "", ctx)),
        unit=str(params.get("unit") or ""),
        context={"instanz": inst.id, "definition": inst.definition_id})
    state = await metrics.trend(db, series_row, target=target, window_days=window)
    warn = metrics.forewarn(series_row, state["days_left"], forewarn) if forewarn > 0 else False
    state = {**state, "series": key, "target": target, "warn_days": forewarn, "warn": warn}
    key_ctx = str(params.get("context_key") or "metric")
    inst.context = {**ctx, key_ctx: state}
    return {"action": "metric_record", "series": key, "value": value,
            "per_day": state["per_day"], "days_left": state["days_left"], "warn": warn}


async def _series_read(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
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
    still_from = _number(params, ctx, "silence_hours", "silent_from")
    key_ctx = str(params.get("context_key") or "metric")

    owner = await _owner(db, inst)
    series_row = await metrics.series(db, owner, key)
    if series_row is None:
        # Not an error: a typo in the key would otherwise be a red run every hour. The
        # flow decides for itself whether it cares (`gefunden`).
        inst.context = {**ctx, key_ctx: {"series": key, "found": False, "silent": False,
                                         "report_silence": False, "value": None}}
        return {"action": "metric_read", "series": key, "found": False}

    state = await metrics.trend(
        db, series_row, target=_number(params, ctx, "target"),
        window_days=int(_number(params, ctx, "window_days",
                               default=float(metrics.WINDOW_DAYS))))
    alter = state.get("age_hours")
    still = bool(still_from > 0 and alter is not None and alter >= still_from)
    report = metrics.silence_report(series_row, alter, still_from)
    state = {**state, "series": key, "found": True, "silence_hours": still_from,
             "silent": still, "report_silence": report}
    inst.context = {**ctx, key_ctx: state}
    return {"action": "metric_read", "series": key, "value": state["value"],
            "age_hours": alter, "silent": still, "report_silence": report}


async def _series_state(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Read a data series: where it stands, where it is heading, whether it went quiet.

    The counterpart of `series_record`, and the one thing the new model was missing: it could
    be written from a flow but not read, so every watchdog still hung off the old metric
    series. What it answers follows `metric_read` field for field, so a flow moving over
    changes its action and nothing else.

    Parameters (`reihe`, `ziel`, `still_ab` work in German too):
      series         key of the series, needed
      target         the value the forecast aims at (default 0)
      window_days    how far back the trend reads (default 30)
      silence_hours  report when nothing has arrived for this long
      context_key    where the answer goes (default `series`)

    A text series answers with its newest entry instead of a trend: a heading and a body have
    no direction, but "when did something last arrive" is the same question for both.
    """
    from . import series as series_service

    key = str(_interp(params.get("series") or "", ctx)).strip()
    if not key:
        raise ValueError("reihe_lesen: no series given")
    key_ctx = str(params.get("context_key") or "series")
    silence_from = _number(params, ctx, "silence_hours", "silent_from")

    owner = await _owner(db, inst)
    row = await series_service.series(db, owner, key)
    if row is None:
        # Not an error: a typo in the key would otherwise be a red run every hour. The flow
        # decides for itself whether it cares (`found`).
        answer = {"series": key, "found": False, "silent": False,
                  "report_silence": False, "value": None}
        inst.context = {**ctx, key_ctx: answer}
        return {"action": "series_read", **answer}

    if row.kind == "text":
        state = await series_service.latest(db, row)
        age = state.get("age_hours")
    else:
        state = await series_service.trend(
            db, row, target=_number(params, ctx, "target"),
            window_days=int(_number(params, ctx, "window_days",
                                    default=float(series_service.WINDOW_DAYS))))
        age = state.get("age_hours")

    silent = bool(silence_from > 0 and age is not None and age >= silence_from)
    report = series_service.silence_report(row, age, silence_from)
    state = {**state, "series": key, "kind": row.kind, "found": True,
             "age_hours": age, "silence_hours": silence_from,
             "silent": silent, "report_silence": report}
    inst.context = {**ctx, key_ctx: state}
    return {"action": "series_read", "series": key, "kind": row.kind,
            "value": state.get("value"), "age_hours": age,
            "silent": silent, "report_silence": report}


async def _mail_account(db, params: dict, ctx: dict):
    """Account, folder and number of the mail this is about.

    The default is the mail from the trigger (`mail` in the context) — the normal case when a
    button on the mailbox or the mail intake started the flow. Whoever means a different one
    says so in the node.
    """
    from ..models.mail import MailAccount

    mail = dict(ctx.get("mail") or {})
    account_id = params.get("account_id") or mail.get("account_id")
    uid = params.get("uid") or mail.get("uid")
    folder = str(_interp(params.get("folder") or "", ctx)).strip() \
        or str(mail.get("folder") or "INBOX")
    if account_id is None or uid in (None, ""):
        return None, folder, None
    account = await db.get(MailAccount, int(account_id))
    return account, folder, int(uid)


async def _cache_empty(account_id: int) -> None:
    """What the flow changes on the mailbox must not be read from the cache by the UI."""
    from .mailbox_cache import invalidate

    await invalidate(account_id)


async def _mail_flag(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Eine Mail markieren: gelesen, wichtig, beantwortet.

    The most common handgrip of all and until now the only one a flow could not do: whoever
    has filed or answered a mail wants it marked as read afterwards without touching it a
    second time.

    Parameter: `flag` (seen | flagged | answered, Vorgabe `seen`), `on` (Vorgabe an),
    `folder`/`uid`/`account_id` (default: the mail from the trigger).
    """
    from . import mailbox

    account, folder, uid = await _mail_account(db, params, ctx)
    if account is None or uid is None:
        return {"action": "mail_flag", "set": False,
                "reason": "no mail in the context (is the trigger not a mail action?)"}
    flag = str(_interp(params.get("flag") or "", ctx)).strip().lower() or "seen"
    an = _yes(params.get("on"), ctx) if params.get("on") is not None else True
    await mailbox.flag(account, folder, uid, flag, an)
    await _cache_empty(account.id)
    return {"action": "mail_flag", "set": True, "flag": flag, "on": an, "uid": uid}


async def _mail_move(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Move a mail into a folder — or into the archive if none is named.

    Without a target, whatever is entered as the archive on the account applies, pattern
    included: that way a flow can say "done, away with it" without knowing the folder name.
    """
    from . import mailbox

    account, folder, uid = await _mail_account(db, params, ctx)
    if account is None or uid is None:
        return {"action": "mail_move", "moved": False,
                "reason": "no mail in the context (is the trigger not a mail action?)"}
    target = str(_interp(params.get("target") or "", ctx)).strip()
    if target:
        await mailbox.move(account, folder, uid, target)
    elif account.folder_archive:
        target = await mailbox.archive(account, folder, uid)
    else:
        return {"action": "mail_move", "moved": False,
                "reason": "no target named and no archive on the account"}
    await _cache_empty(account.id)
    return {"action": "mail_move", "moved": True, "target": target, "uid": uid}


async def _mail_attachment(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Fetches the attachment of a mail into the context — as base64, the way tools expect it.

    The trigger of a mail action puts only the description of the attachment into the context
    (name, type, size): a list of twenty mails must not drag twenty PDFs across the network.
    Whoever really needs the content fetches it here — exactly once, at the point in the flow
    where it is needed.

    Parameters: `index` (default: the attachment from the trigger), `context_key` (default
    `attachment`), `max_mb` (Vorgabe 25).
    """
    import base64

    from ..models.mail import MailAccount
    from . import mailbox

    mail = dict(ctx.get("mail") or {})
    # The trigger writes `attachment`; `anhang` is the name from before the switch to English
    # and still stands in the context of instances that were started back then.
    attachment = dict(ctx.get("attachment") or ctx.get("anhang") or {})
    account_id = mail.get("account_id")
    index = params.get("index")
    index = int(index) if index not in (None, "") else attachment.get("index")
    if account_id is None or mail.get("uid") is None or index is None:
        return {"action": "mail_attachment", "fetched": False,
                "reason": "no attachment in the context (is the trigger not a mail action?)"}
    account = await db.get(MailAccount, int(account_id))
    if account is None:
        return {"action": "mail_attachment", "fetched": False, "reason": "the account does not exist any more"}

    name, kind, data = await mailbox.attachment(account, str(mail.get("folder") or "INBOX"),
                                            int(mail["uid"]), int(index))
    limit = int(float(params.get("max_mb") or 25) * 1024 * 1024)
    if len(data) > limit:
        # A clear word is better than a context that blows up the database row.
        raise ValueError(f"the attachment {name} is {len(data) // 1024 // 1024} MB "
                         f"(Grenze {limit // 1024 // 1024} MB)")
    key = str(params.get("context_key") or "attachment")
    # The index stays in. Without it nobody knows later which attachment this was, and an
    # answer coming back from the archive lands on the mail instead of on the file.
    inst.context = {**ctx, key: {"index": int(index), "filename": name, "content_type": kind,
                                 "size": len(data),
                                 "base64": base64.b64encode(data).decode()}}
    return {"action": "mail_attachment", "fetched": True, "filename": name, "size": len(data),
            "context_key": key}


async def _mail_document(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Report back which document grew out of a filed attachment.

    Filing is a one way street with a gap in the middle: the upload answers with a task
    number, and which document comes of it is decided minutes later by the other side. This
    action is where the answer arrives when it comes back, usually from a webhook the archive
    calls once it is done.

    Parameters, all with `{{path}}` from the context:
      filename     the name of the file that was filed. That is the connection.
      doc_id       the number over there
      doc_url      the address one can open it at
      title        optional, what the archive called it
      system       optional, which archive (default `paperless`)

    The attachment is found through the run that filed it: it carries the file in its context
    and knows which message it hung on. Two runs with the same file name are possible (an
    `invoice.pdf` arrives twice a month), so the most recent one without a document wins,
    which is the one just filed.
    """
    from sqlalchemy import select

    from ..models.mail import MailDocument
    from ..models.workflow import WorkflowInstance as Instance

    name = str(_interp(params.get("filename") or "", ctx)).strip()
    doc_id = str(_interp(params.get("doc_id") or "", ctx)).strip()
    doc_url = str(_interp(params.get("doc_url") or "", ctx)).strip()
    if not name or not (doc_id or doc_url):
        return {"action": "mail_document", "noted": False,
                "reason": "without a file name and a document there is nothing to connect"}

    system = str(_interp(params.get("system") or "paperless", ctx)).strip() or "paperless"
    rows = (await db.execute(select(Instance).where(
        Instance.source.like("mail:%"),
        Instance.source_ref.isnot(None))
        .order_by(Instance.id.desc()).limit(300))).scalars().all()

    for run in rows:
        attachment = (run.context or {}).get("attachment") or {}
        if str(attachment.get("filename") or "").strip() != name:
            continue
        mail = (run.context or {}).get("mail") or {}
        account_id, uid = mail.get("account_id"), mail.get("uid")
        if account_id is None or uid is None:
            continue
        folder = str(mail.get("folder") or "INBOX")
        # The start record is the more reliable source: it has carried the index since the
        # click, while the context can lose it on the way.
        teile = (run.source_ref or "").rsplit(":", 1)
        index = int(teile[1]) if len(teile) == 2 and teile[1].isdigit() \
            and teile[0] != folder else int(attachment.get("index", -1))
        exists = (await db.execute(select(MailDocument).where(
            MailDocument.account_id == int(account_id), MailDocument.folder == folder,
            MailDocument.uid == int(uid),
            MailDocument.attachment == index))).scalar_one_or_none()
        if exists is not None:
            # Already reported. The second message about the same file is a repetition, and
            # the first answer is the one that counts.
            continue
        db.add(MailDocument(account_id=int(account_id), folder=folder, uid=int(uid),
                             attachment=index, filename=name, system=system,
                             doc_id=doc_id[:80], doc_url=doc_url[:1000],
                             title=str(_interp(params.get("title") or "", ctx))[:500]))
        await db.flush()
        return {"action": "mail_document", "noted": True, "uid": int(uid),
                 "attachment": index, "doc_id": doc_id}

    return {"action": "mail_document", "noted": False,
            "reason": f"no filed attachment named {name!r} found"}


def _yes(value, ctx: dict) -> bool:
    """A switch that may also come from the run.

    Fixed (`true`), from the context (`"{{ policy.auto }}"`) or as a condition in the same
    language the decisions speak (`{"!": {"var": "policy.auto"}}`). Without that a flow would
    have to keep two nodes for "sometimes this way, sometimes that way" which differ in one
    checkbox only — and both want maintaining.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        from .jsonlogic import JsonLogicError, safe_eval
        try:
            return bool(safe_eval(value, ctx))
        except JsonLogicError:
            return False
    if isinstance(value, str):
        text = str(_interp(value, ctx)).strip().lower()
        return text in ("true", "1", "ja", "yes", "an")
    return bool(value)


async def _assistant_task(db, inst: WorkflowInstance, params: dict, ctx: dict,
                             node_id: str) -> dict:
    """Give the personal assistant a free assignment — no mail, no ticket, no project.

    The generic counterpart to the mail path (`assistant_task` + `assistant_run`), which can
    only read its assignment out of an incoming mail. Here the assignment stands in the node,
    so ANY flow can hand work to the assistant.

    Parameters:
      assignment      the assignment itself ({{…}} out of the context), required
      titel        heading in the inbox (default: first line of the assignment)
      agent        role of the agent (default `assistent`)
      freigabe     true = the item waits for the person, false (default) = it runs at once.
                   May also come from the run (`{{ … }}` or a condition, see `_ja`)
      warten       true = the step waits for the run and puts its answer into the context
      context_key  where the answer lands (default `assistent`)
      timeout_sek  limit for the wait (0 = the engine default)

    And what the mail path brought along, until it no longer exists — the same entries, only
    without the mail: `art` (what the intake sorts by, default `assignment`), `quelle`/`referenz`
    (the key against duplicate creation; without them, flow and node), `zusammenfassung`,
    `volltext` together with `schwaerzen` (the full text is stored only with `unredacted`),
    `hinweis` and `meta` for everything else.

    Idempotent per node and instance: a repeated pass (restart, retry) picks up the existing
    item instead of commissioning the work a second time.
    """
    from sqlalchemy import select

    from ..core.redis import enqueue_task
    from ..models.assistant import AssistantTask

    assignment = str(_interp(params.get("task") or params.get("prompt") or "", ctx)).strip()
    role = str(_interp(params.get("agent") or "", ctx)).strip() or "assistent"
    title = str(_interp(params.get("title") or "", ctx)).strip() \
        or (assignment.splitlines()[0][:200] if assignment else "")
    if not (assignment or title):
        # An assignment text is the normal case but not the condition: an intake that brings
        # its own matter along (subject, summary, full text) can be worked on without a prompt
        # as well — the assistant then has its own.
        return {"action": "assistant_task", "started": False, "reason": "no task"}
    owner = params.get("owner_id") or inst.started_by or _dig(ctx, "intake.owner_id")
    try:
        owner = int(owner) if owner is not None else None
    except (TypeError, ValueError):
        owner = None
    if owner is None:
        # Without an owner there is no token and no MCP group, so the run would start and
        # immediately have nothing to work with. Say so instead of failing later.
        return {"action": "assistant_task", "started": False,
                "reason": "no owner (neither on the flow nor on the node)"}

    # How duplicate creation is recognised. Without an entry of its own it is the node itself
    # (a restart must not assign twice); with an entry of its own it is the matter at hand
    # — the same mail arriving on two paths stays one intake.
    source = str(_interp(params.get("source") or "", ctx)).strip() or f"ablauf:{inst.definition_id}"
    source_ref = str(_interp(params.get("reference") or "", ctx)).strip() or f"{inst.id}:{node_id}"
    task = (await db.execute(select(AssistantTask).where(
        AssistantTask.source == source,
        AssistantTask.source_ref == source_ref))).scalar_one_or_none()
    new_created = task is None
    if task is None:
        grant = _yes(params.get("approval"), ctx)
        redact = str(_interp(params.get("redaction") or "", ctx)).strip() or "redacted"
        fulltext = str(_interp(params.get("full_text") or "", ctx))
        extra = _interp_deep(params.get("meta"), ctx) if isinstance(params.get("meta"), dict) else {}
        task = AssistantTask(
            owner_user_id=owner,
            kind=str(_interp(params.get("kind") or "", ctx)).strip() or "task",
            source=source, source_ref=source_ref,
            title=title[:500],
            category=str(_interp(params.get("category") or "", ctx))[:80],
            priority=str(_interp(params.get("priority")
                                 or params.get("priority") or "normal", ctx)) or "normal",
            redacted_summary=str(_interp(params.get("summary") or "", ctx)),
            redaction=redact,
            action_hint=str(_interp(params.get("hint") or "", ctx))[:500],
            # The full text is stored only when nothing is to be redacted. Otherwise what the
            # redaction protects against would lie right next to it.
            raw_body=fulltext if (fulltext and redact == "unredacted") else None,
            # The assignment IS the prompt; the worker takes it out of `meta.prompt`, the
            # same way it takes the ported mail prompt of a webhook.
            meta={**extra, "agent": role, "prompt": assignment,
                  "ablauf": {"instanz": inst.id, "knoten": node_id,
                             "definition": inst.definition_id}},
            status="new" if grant else "approved",
        )
        db.add(task)
        await db.flush()

    task_id = f"assistant-{task.id}"
    wait = _yes(params.get("wait"), ctx)
    if new_created and task.status == "approved":
        await enqueue_task({"kind": "assistant", "task_id": task_id,
                            "assistant_task_id": int(task.id)})
    result = {"action": "assistant_task", "task_id": task.id, "agent": role,
                "status": task.status, "started": task.status == "approved",
                "reused": not new_created}
    if wait and task.status == "approved":
        result["_wait"] = {"task_id": task_id,
                             "timeout": int(params.get("timeout_sec") or 0),
                             "context_key": str(params.get("context_key") or "assistant")}
    # `assignment` and `task` carry the same thing: the mail path always read `task`, and the
    # card afterwards must find the intake without knowing which node created it.
    inst.context = {**ctx, "task": {"task_id": task.id, "status": task.status,
                                    "agent": role},
                    "task": {"id": task.id, "status": task.status,
                             "auto": task.status == "approved"}}
    return result


async def _assistant_session(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Create, close or delete conversations of the personal assistant.

    Deleting a conversation is deliberately NOT a button anywhere in the interface. It is
    here, as an action, so that clearing out old conversations can be scheduled as a job —
    and so that the destructive path has exactly ONE place where its guard rails live.

      op=create   {title?, agent?}
                  The new id lands in the context under `session`, so a following node can
                  send into it.
      op=close    {session_id}  or a sweep: {older_than_days, agent?}
      op=delete   {session_id}  or a selector: {closed_only (default true), older_than_days,
                  agent, keep_last}
                  Returns {"deleted": n, "ids": [...]}.

    `context_key` (default `session_cleanup`) is where close and delete put their outcome, so
    a following decision can ask whether anything happened at all.

    Three guard rails, because this is the one path that destroys something:

    * A conversation with a running task is never deleted — not by any selector. That rule
      lives in `assistant_sessions.delete`, not here, so it cannot be walked around.
    * An OPEN conversation is deleted only when `session_id` names it explicitly. A sweep
      that eats the conversation somebody is sitting in would be unforgivable, and a selector
      is exactly the kind of thing that gets misconfigured once.
    * A selector without an owner does nothing at all. Without one it would reach across
      every person in the house.
    """
    import datetime as _dt

    from sqlalchemy import select

    from ..models.assistant import AssistantSession
    from . import assistant_sessions as sessions

    op = str(_interp(params.get("op") or "", ctx)).strip().lower() or "create"
    owner = params.get("owner_id") or inst.started_by or _dig(ctx, "intake.owner_id")
    owner = _as_int(_interp(owner, ctx)) if owner is not None else None
    agent = str(_interp(params.get("agent") or "", ctx)).strip()
    named = _as_int(_interp(params.get("session_id"), ctx)) if params.get("session_id") else None

    if op == "create":
        if owner is None:
            return {"action": "assistant_session", "op": op, "created": False,
                    "reason": "no owner (neither on the flow nor on the node)"}
        title = str(_interp(params.get("title") or "", ctx)).strip()
        s = await sessions.create(db, owner, title=title, agent=agent or "assistent",
                                  commit=False)
        inst.context = {**ctx, "session": {"id": s.id, "title": s.title, "agent": s.agent}}
        return {"action": "assistant_session", "op": op, "created": True, "session_id": s.id}

    # ── The set this call works on ───────────────────────────────────────────
    if named is not None:
        s = await db.get(AssistantSession, named)
        if s is None or (owner is not None and s.owner_user_id != owner):
            return {"action": "assistant_session", "op": op, "deleted": 0, "ids": [],
                    "reason": "the conversation was not found"}
        candidates = [s]
    else:
        if owner is None:
            # A selector without an owner would reach across everybody. Say so instead of
            # doing it.
            return {"action": "assistant_session", "op": op, "deleted": 0, "ids": [],
                    "reason": "a selector needs an owner"}
        q = select(AssistantSession).where(AssistantSession.owner_user_id == owner)
        if agent:
            q = q.where(AssistantSession.agent == agent)
        candidates = list((await db.execute(q)).scalars().all())

    from .assistant_sessions import recency as age_of

    days = _as_int(_interp(params.get("older_than_days"), ctx))
    if days and days > 0:
        cutoff = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=days)
        candidates = [s for s in candidates if age_of(s) < cutoff]

    key = str(_interp(params.get("context_key") or "", ctx)).strip() or "session_cleanup"

    if op == "close":
        closed = []
        for s in candidates:
            if s.closed_at is None:
                await sessions.close(db, s, commit=False)
                closed.append(s.id)
        inst.context = {**ctx, key: {"closed": len(closed), "deleted": 0, "ids": closed}}
        return {"action": "assistant_session", "op": op, "closed": len(closed), "ids": closed}

    if op != "delete":
        return {"action": "assistant_session", "op": op, "reason": "unknown op"}

    # `closed_only` defaults to TRUE and is only lifted for a session named by number. An
    # open conversation is one somebody may be sitting in right now.
    closed_only = _yes(params.get("closed_only"), ctx) \
        if params.get("closed_only") is not None else True
    if named is None or closed_only:
        candidates = [s for s in candidates if s.closed_at is not None]

    keep = _as_int(_interp(params.get("keep_last"), ctx)) or 0
    if keep > 0:
        # The N most recent of ALL the person's conversations (in this agent's scope), not
        # only of the ones the selector caught: "keep the last five" is meant as a floor
        # under the whole shelf, not under the part that happens to be old and closed.
        q = select(AssistantSession).where(AssistantSession.owner_user_id == owner)
        if agent:
            q = q.where(AssistantSession.agent == agent)
        everything = list((await db.execute(q)).scalars().all())
        everything.sort(key=age_of, reverse=True)
        protected = {s.id for s in everything[:keep]}
        candidates = [s for s in candidates if s.id not in protected]

    gone = await sessions.delete(db, candidates, commit=False)
    inst.context = {**ctx, key: {"deleted": len(gone), "closed": 0, "ids": gone}}
    return {"action": "assistant_session", "op": op, "deleted": len(gone), "ids": gone}


async def _agent_run(db, inst: WorkflowInstance, params: dict, ctx: dict,
                     node_id: str) -> dict:
    """Let an agent work and wait for its result — without a ticket, without an intake.

    The third way to start an agent and until now the only one a flow could not take:
    `agent_task` demands a ticket, `assistent_auftrag` creates an intake, and the free run was
    stuck in the job kind `prompt`. Here it is a node — which makes "ask, check, report"
    buildable instead of chaining three jobs one after another.

    Parameter:
      task         what the agent is to do ({{…}} from the context), required
      agent        the role (default `assistent`)
      title        the heading of the run (default: the first line of the task)
      context_key  where the result goes (default `lauf`) — `.output` is the text
      timeout_sek  time limit (0 = the engine default)
      wait         off: only kick it off, do not wait for the result (default: on)
    """
    from ..core.redis import enqueue_task

    task = str(_interp(params.get("task") or params.get("prompt") or "", ctx)).strip()
    if not task:
        return {"action": "agent_run", "started": False, "reason": "no task"}
    role = str(_interp(params.get("agent") or "", ctx)).strip() or "assistent"
    title = str(_interp(params.get("title") or "", ctx)).strip() or task.splitlines()[0][:200]
    owner = params.get("owner_id") or inst.started_by
    if owner is None:
        # Without an owner there is no token and there are no tools — the run would start and
        # stand there empty-handed at once.
        return {"action": "agent_run", "started": False, "reason": "kein Besitzer"}

    task_id = f"lauf-{inst.id}-{node_id}"
    await enqueue_task({"kind": "agent_frei", "task_id": task_id, "agent": role,
                        "prompt": task, "name": title, "owner_id": int(owner)})
    result = {"action": "agent_run", "started": True, "agent": role, "task_id": task_id}
    if params.get("wait") is None or _yes(params.get("wait"), ctx):
        result["_wait"] = {"task_id": task_id,
                             "timeout": int(params.get("timeout_sec") or 0),
                             "context_key": str(params.get("context_key") or "run")}
    return result


async def _series_write(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Write a point into a data series — whatever its kind.

    There is exactly one sort of series in the core, with a kind attached (number, location,
    text). So there is exactly one action that writes into it; which fields apply is decided
    by the kind. Three actions for "append a point" would be the same triple structure that no
    longer exists in the tables.

    This is meant for sources that send their data along in passing: with every alarm Traccar
    sends the whole position including the battery level. Whoever reports by themselves and
    continuously is better served by the ingest path (`/api/ingest/<token>`) — that one costs
    no flow instance per point.

    Parameters (`reihe`, `art`, `wert`, `quelle` and `pflicht` work in German too, because
    `workflow_terms.PARAMS` sie abbildet):
      series       Schluessel der Reihe (`tracker.shelter`), noetig
      kind         number | location | text — needed only when the series is newly created
      value        with `number`: the number
      lat · lon    with `location`: the coordinates
      title · body with `text`: heading and text
      ts           timestamp (default: now) — unix seconds or ISO
      accuracy · altitude · speed · course · battery   what a device otherwise sends along
      name · color display name and colour when the series is newly created
      context      extra fields for the point: a path into the context ("element.context")
                   or a dict of templates ({"pulse": "{{element.pulse}}"})
      source       where the point came from (default `flow`)
      required     abort without a value instead of passing over it
      context_key  where the result goes (default `series`)
    """
    from . import series as series
    from . import series_formats

    key = str(_interp(params.get("series") or "", ctx)).strip()
    if not key:
        raise ValueError("reihe: keine Reihe genannt")
    key_ctx = str(params.get("context_key") or "series")
    owner = await _owner(db, inst)

    # The kind sits on the series as soon as it exists. The parameter counts only on creation
    # — otherwise a flow could pull the kind of an existing series out from under it.
    existing = await series.series(db, owner, key)
    kind = existing.kind if existing else str(params.get("kind") or "number").strip()
    if kind not in ("number", "location", "text"):
        raise ValueError(f"reihe: unbekannte Art '{kind}'")

    entry, missing = _entry_build(kind, params, ctx, series_formats)
    if missing:
        if params.get("required"):
            raise ValueError(f"reihe: {missing}")
        # The normal case, not the error: a device reports its state as well when it has no
        # fix at the moment or does not know a value.
        inst.context = {**ctx, key_ctx: {"series": key, "kind": kind,
                                                "stored": False, "reason": missing}}
        return {"action": "series_record", "series": key, "kind": kind,
                "stored": False, "ignored": True, "skipped": True, "reason": missing}

    series_row = existing or await series.series(
        db, owner, key, kind=kind, create=True,
        name=str(_interp(params.get("name") or "", ctx)).strip(),
        color=str(params.get("color") or ""))

    result = await series.ingest(db, series_row, [entry])
    state = series_row.state or {}
    aus = {"series": key, "kind": kind, "stored": result["accepted"] > 0,
           "skipped": result["skipped"], "points": series_row.points,
           **{n: state.get(n) for n in ("value", "lat", "lon", "battery", "accuracy", "title")
              if state.get(n) is not None},
           "places": state.get("places") or [],
           "entered": result["betreten"], "left": result["verlassen"]}
    inst.context = {**ctx, key_ctx: aus}
    return {"action": "series_record", **aus}


# The width of `SeriesPoint.source`. Named here rather than imported: the models are loaded
# lazily inside the actions, and a constant that drifts is worse than one that is stated.
SOURCE_MAX = 30


def _point_context(params: dict, ctx: dict) -> dict:
    """The extra fields of a point: what a device sends along but nothing searches by.

    Two ways of naming it, because both already exist in the house. A **path** works like the
    list of a loop node (`context: "element.context"`) and hands the object over whole, which
    is what a sender that already groups its extras wants. A **dict** is templated field by
    field (`{"pulse": "{{element.pulse}}"}`) for a flow that assembles them itself.

    Not through `_interp`: that fills text and would turn a dict into its JSON as a string.
    """
    raw = params.get("context")
    if isinstance(raw, dict):
        return _interp_deep(raw, ctx)
    if isinstance(raw, str) and raw.strip():
        from .workflow_expr import evaluate
        value = evaluate(raw.strip().strip("{} "), ctx)
        return value if isinstance(value, dict) else {}
    return {}


def _entry_build(kind: str, params: dict, ctx: dict, formats) -> tuple[dict, str]:
    """Turn the parameters into a point. The second return value names what is missing."""
    ts = formats.moment(_interp(params.get("ts"), ctx))
    # Through `_interp` like every other text parameter. Without it a flow could only ever
    # name a fixed source, and a delivery that carries several origins would lose them.
    #
    # Clipped like the title next to it, and for a harder reason: `SeriesPoint.source` is
    # thirty characters wide, Postgres does not truncate but refuses the row, and the refusal
    # lands in the middle of the transaction. The instance then cannot even write down its own
    # failure and sits on `running` with no steps, which looks like a hang and not like a
    # rejected value. Health Connect names a phone's own records
    # `com.android.healthconnect.phone.<32 hex>`: sixty-two characters, and every flow that
    # passes an origin through would fall over it.
    source = str(_interp(params.get("source"), ctx) or "flow")[:SOURCE_MAX]
    context = _point_context(params, ctx)

    if kind == "location":
        # Through the same format layer as the ingest path: then the same rules apply here for
        # comma numbers, timestamps and the battery as a fraction or a percentage.
        points = formats.normalise({
            "lat": _interp(params.get("lat"), ctx), "lon": _interp(params.get("lon"), ctx),
            "ts": _interp(params.get("ts"), ctx),
            "accuracy": _interp(params.get("accuracy"), ctx),
            "altitude": _interp(params.get("altitude"), ctx),
            "speed": _interp(params.get("speed"), ctx),
            "course": _interp(params.get("course"), ctx),
            "battery": _interp(params.get("battery"), ctx),
            "source": source,
        })
        return (points[0], "") if points else ({}, "no position in the payload")

    if kind == "text":
        text = str(_interp(params.get("body") or params.get("text") or "", ctx))
        if not text.strip():
            return {}, "no text in the payload"
        title = str(_interp(params.get("title") or "", ctx)).strip()
        if not title:
            title = next((z.strip("# ").strip() for z in text.splitlines() if z.strip()), "")
        return {"title": title[:200], "body": text, "ts": ts, "source": source,
                "context": context,
                "format": str(params.get("format") or "markdown")}, ""

    raw = _interp(params.get("value"), ctx)
    if isinstance(raw, str):
        raw = raw.strip().replace("%", "").replace(",", ".")
    if raw is None or (isinstance(raw, str) and raw.lower() in ("", "none", "null")):
        return {}, "no value in the payload"
    try:
        return {"value": float(raw), "ts": ts, "source": source, "context": context}, ""
    except (TypeError, ValueError):
        return {}, f"'{raw}' is not a number"


async def _document(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Put a text down in a store — the counterpart to the measurement.

    A flow had nowhere to leave its text: the review an agent writes every morning ended up in
    the output field of a job run, truncated and without a view. A store is a name and a
    sequence of versions; what is in it is the flow's business.

    Parameter:
      ablage       key of the store (`ki-tech-news`), required
      text         the text itself ({{…}} from the context), required
      titel        heading of this version (default: first heading or line)
      name         display name of the store when it is newly created
      format       markdown (default) or text
      behalten     how many versions are kept (default 60)
      context_key  where the reference lands (default `dokument`) — `.url` is the link
    """
    from ..config import settings
    from . import documents

    key = str(_interp(params.get("storage") or params.get("key") or "", ctx)).strip()
    text = str(_interp(params.get("text") or "", ctx))
    if not key:
        return {"action": "document", "stored": False, "reason": "keine Ablage genannt"}
    if not text.strip():
        # Putting nothing down is better than an empty version: it would displace a real one
        # in the history and stand there as "today's state" although nothing was worked out.
        return {"action": "document", "stored": False, "reason": "kein Text"}
    owner = params.get("owner_id") or inst.started_by
    title = str(_interp(params.get("title") or "", ctx)).strip()
    if not title:
        first = next((z.strip("# ").strip() for z in text.splitlines() if z.strip()), "")
        title = first[:200]
    entry = await documents.put(
        db, int(owner) if owner is not None else None, key,
        title=title, text=text,
        format=str(params.get("format") or "markdown"),
        name=str(_interp(params.get("name") or "", ctx)).strip(),
        keep=int(params.get("keep") or 0),
        context={"ablauf": inst.definition_id, "instanz": inst.id,
                 **({"job": (inst.context or {}).get("job")} if (inst.context or {}).get("job") else {})})
    key_ctx = str(params.get("context_key") or "document")
    inst.context = {**ctx, key_ctx: {
        "id": entry.id, "storage": key, "title": title,
        # The link that belongs in a report. Without a base address it stays relative — then
        # it is right in the UI and useless in the messenger, which is better than
        # a link to "localhost".
        "url": f"{settings.app_base_url.rstrip('/')}/documents/{key}"
               if settings.app_base_url else f"/documents/{key}"}}
    return {"action": "document", "stored": True, "entry_id": entry.id,
            "storage": key, "context_key": key_ctx}


async def _document_read(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Fetch the latest version of a store into the context — for flows that build on what
    came out the last time."""
    from . import documents

    key = str(_interp(params.get("storage") or params.get("key") or "", ctx)).strip()
    owner = params.get("owner_id") or inst.started_by
    entry = await documents.last(
        db, int(owner) if owner is not None else None, key) if key else None
    key_ctx = str(params.get("context_key") or "document")
    inst.context = {**ctx, key_ctx: {
        "found": entry is not None,
        "title": entry.title if entry else "",
        "text": entry.body if entry else "",
        "ts": (entry.ts.isoformat() if entry and entry.ts else "")}}
    return {"action": "document_read", "found": entry is not None, "storage": key}


async def _job_pause(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Halt the schedule this run comes from.

    A script job could shut itself down with a return value of its own ("done, do not wake me
    again"). That was stuck in the job kind; as a node every flow can do it — the reminder
    that stops once the matter is settled.
    """
    from ..models.ops import Job

    job_id = _as_int(_interp(params.get("job_id"), ctx)) if params.get("job_id") is not None \
        else _as_int(_dig(ctx, "job.id"))
    if job_id is None:
        return {"action": "job_pause", "paused": False, "reason": "kein Job im Kontext"}
    job = await db.get(Job, int(job_id))
    if job is None:
        return {"action": "job_pause", "paused": False, "reason": "Job nicht gefunden"}
    job.paused = not _yes(params.get("resume"), ctx)
    return {"action": "job_pause", "paused": job.paused, "job_id": job.id}


async def _agentshield_scan(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Run the configuration audit and put its summary into the context.

    This used to be a `http_request` onto a destination — a flow that had to know a URL, a
    token and the shape of an answer for something that is a part of the house. As a node it
    is one word, and what follows it (report, open a ticket, stay quiet) is the flow's own
    business as with every other step.
    """
    from . import agentshield

    summary = await agentshield.scan(db, trigger=str(_interp(params.get("trigger"), ctx)
                                                     or "job")[:40])
    key = str(params.get("context_key") or "audit")
    inst.context = {**ctx, key: summary}
    return {"action": "agentshield_scan", **summary}


async def _script(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Run a stored script — the same check as with the script job.

    Only what lies in the allowed directory runs; the path comes from the flow, not from
    out of a stranger's payload.
    """
    import asyncio

    from .scheduler import _resolve_script

    command = str(_interp(params.get("command") or "", ctx)).strip()
    script = _resolve_script(command)
    if not script:
        return {"action": "script", "ok": False,
                "error": f"Skript nicht im erlaubten Verzeichnis: {command}"}
    arguments = _interp_deep(params.get("args") or [], ctx)
    limit = int(params.get("timeout_sec") or 600)
    try:
        p = await asyncio.create_subprocess_exec(
            script, *[str(a) for a in arguments],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        aus, _ = await asyncio.wait_for(p.communicate(), timeout=limit)
        rc = p.returncode or 0
        text = aus.decode("utf-8", "replace")[:20000]
    except asyncio.TimeoutError:
        return {"action": "script", "ok": False, "error": "the time limit was exceeded"}
    key = str(params.get("context_key") or "script")
    inst.context = {**ctx, key: {"output": text, "exit_code": rc, "ok": rc == 0}}
    return {"action": "script", "ok": rc == 0, "exit_code": rc, "context_key": key}



def _answer(inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Set the answer this run gives back to whoever started it.

    A webhook that waits (`response_timeout`) reads exactly this: `context.antwort`. Free
    text goes into `text`, a structured answer into the remaining fields — both go through
    the same `{{…}}` templating as everywhere else.
    """
    if params.get("text") is not None:
        answer = _interp(params.get("text") or "", ctx)
    else:
        fields = params.get("fields") if isinstance(params.get("fields"), dict) else \
            {k: v for k, v in params.items() if k not in ("felder", "text")}
        answer = _interp_deep(fields, ctx)
    key = str(params.get("context_key") or "answer")
    inst.context = {**ctx, key: answer}
    return {"action": "answer", "context_key": key,
            "fields": list(answer) if isinstance(answer, dict) else "text"}


async def run_action(db, inst: WorkflowInstance, node: dict) -> dict:
    cfg = _config(node)
    action, params = _normalize_action(cfg)
    # English is the default; the German names stay readable because they appear in published
    # versions (`services/workflow_terms.py` rewrites them, but an instance can still hang on
    # an old version).
    action = terms.normalise_action(action)
    params = terms.normalise_params(params)
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

    if action in _OLD_ACTIONS or action == "set_status":
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
        return await _mail_attachment(db, inst, params, ctx)

    if action == "mail_document":
        return await _mail_document(db, inst, params, ctx)

    if action == "agent_run":
        return await _agent_run(db, inst, params, ctx, str(node.get("id") or ""))

    if action == "script":
        return await _script(db, inst, params, ctx)

    if action == "document":
        return await _document(db, inst, params, ctx)

    if action == "document_read":
        return await _document_read(db, inst, params, ctx)

    if action == "job_pause":
        return await _job_pause(db, inst, params, ctx)

    if action == "agentshield_scan":
        return await _agentshield_scan(db, inst, params, ctx)

    if action == "assistant_task":
        return await _assistant_task(db, inst, params, ctx, str(node.get("id") or ""))

    if action == "assistant_session":
        return await _assistant_session(db, inst, params, ctx)

    if action == "answer":
        return _answer(inst, params, ctx)

    if action == "note_append":
        return await _note_append(db, inst, params, ctx)

    if action == "series_read":
        return await _series_state(db, inst, params, ctx)

    if action == "series_record":
        return await _series_write(db, inst, params, ctx)
    if action == "metric_record":
        return await _measurement(db, inst, params, ctx)

    if action == "metric_read":
        return await _series_read(db, inst, params, ctx)

    if action == "notify":
        target = await _resolve_target(db, inst, params.get("to") or {})
        title = _interp(params.get("title") or "Workflow notification", ctx)
        body = _interp(params.get("text") or params.get("message") or "", ctx)
        from ..models.user import User
        from .notify import deliver
        # The channel is optional: without one the person decides how they are reached. A
        # flow often learns its recipient only at runtime and cannot know which messenger
        # they use.
        channel = str(_interp(params.get("channel") or "", ctx)).strip()
        recipient = await db.get(User, target) if target is not None else None
        # Throttle: "at most the same thing every N minutes". Without a key of its own the
        # node throttles itself, because for the normal case a number should be enough
        # without inventing a name. A key with `{{ … }}` separates by device or kind of
        # alarm so two different incidents do not mute each other.
        throttle = float(params.get("throttle_minutes") or 0)
        throttle_key = str(_interp(params.get("throttle_key") or "", ctx)).strip()
        if throttle > 0 and not throttle_key:
            throttle_key = f"flow:{inst.definition_id}:{node.get('id')}"
        # Kind and reference turn the message into a card one can act on: the bot hangs its
        # buttons on the kind and finds through the reference the matter at hand (a spam
        # verdict to take back, an intake to approve). Without both it stays an ordinary
        # report — the normal case.
        kind = str(_interp(params.get("kind") or "", ctx)).strip() \
            or "workflow_notify"
        raw_reference = params.get("ref") if isinstance(params.get("ref"), dict) else {}
        reference: dict[str, int] = {}
        for field, value in raw_reference.items():
            value = _interp(value, ctx)
            number = _as_int(value)
            if number is not None:
                reference[str(field)] = number
        path = await deliver(db, user=recipient, kind=kind, title=title,
                              body=body, channel=channel, project_id=inst.project_id,
                              issue_id=inst.issue_id, reference=reference or None,
                              throttle_key=throttle_key, throttle_minutes=throttle)
        return {"action": "notify", "user_id": target, "kind": kind,
                **({"bezug": reference} if reference else {}), **path}

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
