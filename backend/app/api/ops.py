import datetime as dt
import hashlib
import hmac
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..db import get_session
from ..models.enums import ProjectRole, TicketAgentStatus
from ..models.ops import Job, JobRun, PermAction, Permission, WebhookCoalesce, WebhookSub
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from ..models.user import User
from .deps import (
    Access, build_access, get_current_user, is_owner_or_admin, owned_or_global,
    require_role,
)

router = APIRouter(tags=["ops"])


# ================= Webhooks =================

class WebhookIn(BaseModel):
    route: str
    secret: str = ""
    mode: str = "task"
    project_id: int | None = None
    agent: str | None = None
    classify_agent: str | None = None
    prompt_tmpl: str | None = None
    auto_run: bool = False
    title_template: str = "{title}"
    body_template: str = "{body}"
    silent: bool = False
    # Filter / Idempotenz / Coalescing / Alerts
    event_header: str | None = None
    event_filter: str | None = None
    event_key_header: str | None = None
    event_cooldowns: dict = {}
    alert_events: list = []
    ref_field: str | None = None
    notify_chat: str | None = None
    # mode=workflow: which definition is started and how the payload is mapped
    # ({context_key: payload.dot.path}; empty = the complete payload as context).
    workflow_definition_id: int | None = None
    context_map: dict = {}
    # mode=event: name of the reported event (empty = webhook.<route>).
    event_name: str | None = None
    # mode=workflow: hold the request open until the flow answers (seconds, 0 = off), and
    # which fields the answer carries ({field: context.path}; empty = context.antwort).
    response_timeout: int = 0
    response_map: dict = {}


class WebhookOut(BaseModel):
    id: int; public_id: str; route: str; mode: str; project_id: int | None
    owner_user_id: int | None; agent: str | None; classify_agent: str | None
    prompt_tmpl: str | None = None; auto_run: bool = False
    # The templates belong in the response: the frontend fills its form from them and
    # otherwise falls back to "{title}"/"{body}", so every save of an existing webhook
    # silently reset the entered texts.
    title_template: str = "{title}"; body_template: str = "{body}"
    silent: bool; enabled: bool; secret_set: bool
    event_header: str | None = None; event_filter: str | None = None
    event_key_header: str | None = None; event_cooldowns: dict = {}
    alert_events: list = []; ref_field: str | None = None; notify_chat: str | None = None
    workflow_definition_id: int | None = None; context_map: dict = {}
    event_name: str | None = None
    response_timeout: int = 0; response_map: dict = {}


def _wh_out(w: WebhookSub) -> WebhookOut:
    return WebhookOut(
        id=w.id, public_id=w.public_id, route=w.route, mode=w.mode, project_id=w.project_id,
        owner_user_id=w.owner_user_id, agent=w.agent, classify_agent=w.classify_agent,
        prompt_tmpl=w.prompt_tmpl, auto_run=w.auto_run,
        title_template=w.title_template, body_template=w.body_template,
        silent=w.silent, enabled=w.enabled, secret_set=bool(w.secret),
        event_name=w.event_name,
        event_header=w.event_header, event_filter=w.event_filter,
        event_key_header=w.event_key_header, event_cooldowns=w.event_cooldowns or {},
        alert_events=w.alert_events or [], ref_field=w.ref_field, notify_chat=w.notify_chat,
        workflow_definition_id=w.workflow_definition_id, context_map=w.context_map or {},
        response_timeout=w.response_timeout or 0, response_map=w.response_map or {})


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # Own plus global ones (admin: all). Owner filter via deps.owned_or_global.
    rows = (await db.execute(select(WebhookSub)
                             .where(owned_or_global(WebhookSub.owner_user_id, user))
                             .order_by(WebhookSub.route))).scalars().all()
    return [_wh_out(w) for w in rows]


async def _check_webhook_project(project_id: int | None, user: User, db: AsyncSession) -> None:
    """A task webhook creates tickets in the project, so the creator must be a member there (ai_assign)."""
    if project_id is None:
        return
    from ..models.project import Project
    proj = await db.get(Project, project_id)
    if proj is None:
        raise Fehler(400, "err.target_project_does_not_exist", "The target project does not exist")
    if not (await build_access(proj, user, db)).ai_assign:
        raise Fehler(403, "err.ai_right_target_project_required",
                     "The AI right in the target project is required")


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(data: WebhookIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    await _check_webhook_project(data.project_id, user, db)
    w = WebhookSub(**data.model_dump(), owner_user_id=user.id, public_id=str(uuid.uuid4()))
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return _wh_out(w)


@router.put("/webhooks/{wid}", response_model=WebhookOut)
async def update_webhook(wid: int, data: WebhookIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    w = await db.get(WebhookSub, wid)
    if w is None or not is_owner_or_admin(w.owner_user_id, user):
        raise Fehler(404, "err.webhook_not_found", "Webhook not found")
    await _check_webhook_project(data.project_id, user, db)
    for field, value in data.model_dump().items():
        if field == "secret" and not value:
            continue  # an empty field leaves the existing secret untouched
        setattr(w, field, value)
    await db.commit()
    await db.refresh(w)
    return _wh_out(w)


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/webhooks/{wid}/enabled", response_model=WebhookOut)
async def set_webhook_enabled(wid: int, data: EnabledIn, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    """On/off without deleting: deactivated, the inbound endpoint rejects (404)."""
    w = await db.get(WebhookSub, wid)
    if w is None or not is_owner_or_admin(w.owner_user_id, user):
        raise Fehler(404, "err.webhook_not_found", "Webhook not found")
    w.enabled = data.enabled
    await db.commit()
    await db.refresh(w)
    return _wh_out(w)


@router.delete("/webhooks/{wid}", status_code=204)
async def delete_webhook(wid: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    w = await db.get(WebhookSub, wid)
    if w and is_owner_or_admin(w.owner_user_id, user):
        await db.delete(w)
        await db.commit()


def _dig_payload(data, pfad: str):
    """Resolve a dot path in the payload (`event.attributes.alarm`, `posten.0.name`)."""
    cur = data
    for teil in str(pfad).split("."):
        if isinstance(cur, dict) and teil in cur:
            cur = cur[teil]
        elif isinstance(cur, list) and teil.isdigit() and int(teil) < len(cur):
            cur = cur[int(teil)]
        else:
            return None
    return cur


# Hard ceiling for a held-open request. Whoever waits here occupies a connection, and a
# sender that runs into ITS own timeout meanwhile gets nothing out of a longer wait.
ANTWORT_MAX_SEK = 120


async def _warte_auf_antwort(instance_id: int, sub: WebhookSub) -> dict:
    """Hold the answer of a flow open for the caller (mode 'workflow').

    A webhook is a trigger, and a trigger may have an answer: the flow writes it (action
    `antwort` → `context.antwort`), this reads it. Without `response_map` exactly that key
    goes back, with one the named fields are dug out of the context.

    The waiting is polling and not a subscription on purpose: a flow ends in many places
    (end node, error, cancellation, timeout of a step), and none of them should have to know
    that somebody is listening here.
    """
    import asyncio

    from ..db import SessionLocal
    from ..models.enums import WorkflowInstanceStatus as IStatus
    from ..models.workflow import WorkflowInstance

    grenze = max(0, min(int(sub.response_timeout or 0), ANTWORT_MAX_SEK))
    uhr = asyncio.get_running_loop().time
    ende = uhr() + grenze
    karte = sub.response_map or {}
    ctx: dict = {}
    status, fertig = "weg", False
    while True:
        # A session of its own per look: the answer is written by another task in another
        # transaction, and a session that keeps its snapshot would never see it.
        async with SessionLocal() as s:
            inst = await s.get(WorkflowInstance, instance_id)
            if inst is None:
                break
            ctx = dict(inst.context or {})
            status = inst.status.value
            fertig = inst.status not in (IStatus.running, IStatus.waiting)
        # An answer that already stands does not need the end of the flow: answering first
        # and tidying up afterwards is a perfectly good order.
        if karte:
            bereit = fertig or all(_dig_payload(ctx, pfad) is not None for pfad in karte.values())
        else:
            bereit = fertig or "antwort" in ctx
        if bereit or uhr() >= ende:
            break
        await asyncio.sleep(0.4)

    if karte:
        antwort = {schluessel: _dig_payload(ctx, pfad) for schluessel, pfad in karte.items()}
    else:
        antwort = ctx.get("antwort")
    return {"instance_id": instance_id, "status": status, "fertig": fertig,
            "antwort": antwort}


@router.post("/hooks/{public_id}", status_code=202)
async def inbound_webhook(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Public inbound endpoint by GUID. HMAC check (X-Webhook-Signature, hex, without a prefix)."""
    sub = (await db.execute(
        select(WebhookSub).where(WebhookSub.public_id == public_id))).scalar_one_or_none()
    if sub is None or not sub.enabled:
        raise Fehler(404, "err.unknown_route", "Unknown route")
    route = sub.route  # label for coalescing, notify and the idempotency source
    raw = await request.body()
    if sub.secret:
        sig = request.headers.get("X-Webhook-Signature", "")
        expected = hmac.new(sub.secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise Fehler(401, "err.invalid_signature", "Invalid signature")
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    def fill(tpl: str) -> str:
        """Fill placeholders: {field} for the top level, {a.b.c} deeper down.

        Nested payloads are the normal case as soon as the sender is not Traccoon:
        `{position.address}` or `{event.attributes.alarm}` could not be addressed before,
        and the message then contained the placeholder in plain text.
        """
        out = tpl
        for treffer in set(re.findall(r"\{([A-Za-z0-9_.]+)\}", tpl)):
            wert = _dig_payload(payload, treffer)
            if wert is not None:
                out = out.replace("{" + treffer + "}", str(wert))
        return out

    from ..models.notification import Notification

    # Where the event type comes from: from a header (X-GitHub-Event) or, when the sender
    # sets none, from the payload itself (`payload:event.type`). Without the second way
    # everything that sends headers could be filtered and nothing else; Traccar for instance
    # reports every ignition and every alarm over the same URL, without a header.
    event = ""
    if sub.event_header:
        if sub.event_header.startswith("payload:"):
            event = str(_dig_payload(payload, sub.event_header[len("payload:"):]) or "")
        else:
            event = request.headers.get(sub.event_header, "")
    if sub.event_filter:
        allowed = [e.strip() for e in sub.event_filter.split(",") if e.strip()]
        if event not in allowed:
            return {"accepted": True, "ignored": True, "event": event}

    # Alert events bypass the coalescing and report immediately.
    if event and event in (sub.alert_events or []):
        db.add(Notification(kind="webhook_alert", title=f"🚨 {route}: {event}",
                            body=(fill(sub.body_template) or fill(sub.title_template))[:4000],
                            chat_id=sub.notify_chat))
        await db.commit()
        # mode=assistant: alert AND agent run (for instance a gameproj attack, autopilot reacts).
        # Only notify/task: finished here (a pure immediate report).
        if sub.mode != "assistant":
            return {"accepted": True, "alert": True, "event": event}

    # Coalescing: within the cooldown window only collect, the scheduler summarises.
    cooldown = int((sub.event_cooldowns or {}).get(event, 0)) if event else 0
    if cooldown > 0:
        now = dt.datetime.now(tz=dt.timezone.utc)
        ekey = (request.headers.get(sub.event_key_header, "") if sub.event_key_header else "") or event
        open_win = (await db.execute(select(WebhookCoalesce).where(
            WebhookCoalesce.route == route, WebhookCoalesce.event_key == ekey,
            WebhookCoalesce.flushed.is_(False), WebhookCoalesce.window_until > now,
        ).with_for_update())).scalars().first()
        if open_win is not None:
            # JSON column: assign a new list, otherwise SQLAlchemy does not detect the change.
            open_win.payloads = [*open_win.payloads, payload]
            await db.commit()
            return {"accepted": True, "coalesced": True, "event": event}
        # The first delivery runs through normally but opens the window for follow-up events.
        db.add(WebhookCoalesce(route=route, event_key=ekey,
                               window_until=now + dt.timedelta(seconds=cooldown), payloads=[]))

    if sub.mode == "assistant":
        # E-mail becomes the event `mail.received`. What comes of it (classifying, spam,
        # assistant) stands in the flow of the slot `mail_intake`, including the creation of
        # the item and the start of the assistant. That is why nothing is queued here any more.
        from ..services.mail_intake import intake_mail
        ids = await intake_mail(
            db, sub.owner_user_id, payload if isinstance(payload, dict) else {},
            source=f"webhook:{route}", classify_agent=sub.classify_agent or "",
            agent=sub.agent or "assistent", prompt_tmpl=sub.prompt_tmpl or "",
            ref_field=sub.ref_field or "", auto_run=sub.auto_run)
        if not ids:
            return {"accepted": True, "ignored": True}
        return {"accepted": True, "mode": "assistant", "instances": ids}

    if sub.mode == "notify":
        # Rendered template as a notification (the Telegram bot delivers it).
        titel = f"Webhook: {route}"
        if sub.ref_field:
            # Idempotency: not the same message twice. Senders repeat the call when the
            # answer fails to arrive, and with the alarm of a motion detector the second
            # message is not information but noise. The reference stands in the title so that
            # the comparison gets by without a new column.
            ref = str(_dig_payload(payload, sub.ref_field) or "")
            if ref:
                titel = f"{titel} #{ref}"
                seit = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=24)
                schon = (await db.execute(select(Notification).where(
                    Notification.kind == "webhook", Notification.title == titel,
                    Notification.created_at >= seit))).scalars().first()
                if schon is not None:
                    return {"accepted": True, "mode": "notify", "duplicate": True, "ref": ref}
        db.add(Notification(kind="webhook", title=titel[:500],
                            body=(fill(sub.body_template) or fill(sub.title_template))[:4000],
                            chat_id=sub.notify_chat))
        await db.commit()
        return {"accepted": True, "mode": "notify"}

    if sub.mode == "event":
        # Reports an event; who listens for it is decided by the flows themselves over the
        # trigger on their start node. `event_name` on the webhook or `event` in the payload.
        from ..services.events import emit
        name = (sub.event_name or (payload.get("event") if isinstance(payload, dict) else "")
                or f"webhook.{route}")
        ref = None
        if sub.ref_field and isinstance(payload, dict):
            ref = str(payload.get(sub.ref_field) or "") or None
        ids = await emit(db, str(name), project_id=sub.project_id,
                         payload=payload if isinstance(payload, dict) else {"payload": payload},
                         actor_id=sub.owner_user_id, source_ref=ref)
        return {"accepted": True, "mode": "event", "event": name, "instances": ids}

    if sub.mode == "workflow":
        # Starts a workflow instance. context_map = {context_key: payload_path} (dot paths);
        # without a mapping the complete payload is taken over as the context.
        from ..models.workflow import WorkflowDefinition
        from ..services.jsonlogic import _dig
        from ..services.workflow_engine import start_workflow
        if sub.workflow_definition_id is None:
            raise Fehler(400, "err.webhook_without_workflow_definition_id",
                         "Webhook without workflow_definition_id")
        definition = await db.get(WorkflowDefinition, sub.workflow_definition_id)
        if definition is None or definition.current_version_id is None:
            raise Fehler(400, "err.workflow_definition_missing_not",
                         "The workflow definition is missing or not published")
        # Idempotenz via ref_field → source_ref.
        src_ref = None
        if sub.ref_field and isinstance(payload, dict):
            # A dot path as everywhere else: the reference of a foreign system almost never
            # sits on the top level (`event.id`), and without it every repetition of the
            # sender would run as a second flow.
            src_ref = str(_dig_payload(payload, sub.ref_field) or "") or None
            if src_ref:
                from ..models.workflow import WorkflowInstance
                dup = (await db.execute(select(WorkflowInstance).where(
                    WorkflowInstance.source == f"webhook:{route}",
                    WorkflowInstance.source_ref == src_ref))).scalar_one_or_none()
                if dup is not None:
                    return {"accepted": True, "duplicate": True, "instance_id": dup.id}
        cmap = sub.context_map or {}
        if cmap and isinstance(payload, dict):
            ctx = {k: _dig(payload, path) for k, path in cmap.items()}
        else:
            ctx = payload if isinstance(payload, dict) else {"payload": payload}
        # What the run hangs off is said by the payload: the start node names the field the
        # artifact stands in (ticket key, ticket id, unit id). Without this binding a flow
        # with a ticket subject would run into nothing: its actions (setting a state,
        # commenting, assigning) would find nothing to act on.
        from ..services.workflow_subject import subjekt_aus_nutzlast
        issue_id, asset_id, fehler = await subjekt_aus_nutzlast(
            db, definition, payload if isinstance(payload, dict) else {}, ctx,
            besitzer_id=sub.owner_user_id)
        if fehler:
            raise HTTPException(400, fehler)
        inst = await start_workflow(
            db, definition, subject_kind=definition.subject_kind, context=ctx,
            issue_id=issue_id, hardware_asset_id=asset_id,
            actor_id=sub.owner_user_id, source=f"webhook:{route}", source_ref=src_ref,
        )
        if int(sub.response_timeout or 0) > 0:
            # The caller wants the answer of the flow, not only the receipt. What goes back
            # is what the flow wrote: a dict answer IS the body (so the far side sees exactly
            # the fields it was promised), anything else is wrapped.
            from fastapi.responses import JSONResponse
            ergebnis = await _warte_auf_antwort(inst.id, sub)
            antwort = ergebnis["antwort"]
            if isinstance(antwort, dict):
                rumpf = antwort
            elif antwort is None:
                rumpf = {"accepted": True, "mode": "workflow", "antwort": None,
                         "instance_id": inst.id, "status": ergebnis["status"],
                         "fertig": ergebnis["fertig"]}
            else:
                rumpf = {"antwort": antwort}
            return JSONResponse(rumpf, status_code=200 if antwort is not None else 202)
        return {"accepted": True, "mode": "workflow", "instance_id": inst.id,
                "status": inst.status.value,
                **({"issue_id": issue_id} if issue_id else {}),
                **({"hardware_asset_id": asset_id} if asset_id else {})}

    # Idempotency: ref_field to source_ref; a double delivery creates no second ticket.
    src_ref = None
    if sub.ref_field and isinstance(payload, dict):
        src_ref = str(payload.get(sub.ref_field) or "") or None
        if src_ref:
            dup = (await db.execute(select(Issue).where(
                Issue.source == f"webhook:{route}", Issue.source_ref == src_ref))).scalar_one_or_none()
            if dup is not None:
                return {"accepted": True, "duplicate": True, "issue_key": dup.key}

    # mode == task: Ticket anlegen
    if sub.project_id is None:
        raise Fehler(400, "err.webhook_without_project",
                     "A webhook without project_id cannot create a ticket")
    t = (await db.execute(select(IssueType).where(IssueType.project_id == sub.project_id).order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == sub.project_id).order_by(WorkflowStatus.order))).scalars().first()
    if t is None or s is None:
        raise Fehler(400, "err.project_without_type_status", "Project without a type or status")
    from ..models.project import Project
    project = await db.get(Project, sub.project_id)
    counter = (await db.execute(select(IssueCounter).where(
        IssueCounter.project_id == sub.project_id).with_for_update())).scalar_one_or_none()
    if project is None or counter is None:
        raise Fehler(400, "err.target_project_no_longer_available",
                     "The target project is no longer available")
    counter.last_number += 1
    n = counter.last_number
    from ..models.user import SYSTEM_USER_ID
    # The owner of the webhook is the reporter, so the agent runs with their token plus MCP.
    owner = sub.owner_user_id or SYSTEM_USER_ID
    issue = Issue(
        project_id=sub.project_id, number=n, key=f"{project.key}-{n}"[:50],
        type_id=t.id, status_id=s.id, summary=fill(sub.title_template)[:500] or f"Webhook {sub.route}",
        description=fill(sub.body_template), reporter_id=owner, rank=f"{n:08d}",
        source=f"webhook:{sub.route}", source_ref=src_ref,
    )
    if sub.agent:
        issue.assigned_agent = sub.agent
        issue.assigned_by_user_id = sub.owner_user_id  # owner token resolution in the worker
        issue.assigned_at = dt.datetime.now(tz=dt.timezone.utc)
        from ..services.artifacts import set_ticket_status
        await set_ticket_status(db, issue, TicketAgentStatus.planning)
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return {"accepted": True, "issue_key": issue.key}


# ================= Jobs =================

class JobIn(BaseModel):
    name: str
    type: str = "interval"
    schedule: str = "60"
    # prompt | script | workflow | http | film. Deliberately a free `str` without validation:
    # branching happens in exactly ONE place (`services/scheduler.run_job_kind`), and a second
    # enumeration here would be a second truth about which kinds exist.
    # `film` = the after-work film of the office; its options stand in `args`
    # ({tz, sekunden, fps, grade, kapitel, behalten_tage}).
    kind: str = "prompt"
    workflow_definition_id: int | None = None   # only kind=workflow
    # only kind=http: destination plus call ({method, path, query, headers, body})
    destination_id: int | None = None
    http_request: dict = {}
    agent: str | None = None
    prompt: str = ""
    command: str = ""
    # A list = arguments of a script job (as before). An object = parameter set of a prompt
    # job; its values fill the `{{placeholders}}` in the prompt (services/job_params).
    args: list | dict = []
    project_id: int | None = None
    notify_mode: str = "on_output"
    notify_chat: str | None = None
    result_html: bool = False
    pause_on_success: bool = False
    run_timeout: int = 600


class JobOut(BaseModel):
    id: int; name: str; type: str; schedule: str; kind: str; agent: str | None
    workflow_definition_id: int | None = None
    destination_id: int | None = None
    http_request: dict = {}
    prompt: str = ""; command: str = ""; args: list | dict = []; project_id: int | None = None
    notify_mode: str; notify_chat: str | None = None; result_html: bool
    pause_on_success: bool = False; run_timeout: int = 600
    enabled: bool; paused: bool; last_run_at: dt.datetime | None
    model_config = {"from_attributes": True}


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Job).where(owned_or_global(Job.user_id, user))
                             .order_by(Job.id))).scalars().all()
    return list(rows)


@router.get("/jobs/templates")
async def job_templates(user: User = Depends(get_current_user)):
    """Templates for new jobs. They prefill the form; a created job then carries its own
    fields and parameters, with no binding to the template."""
    from ..services.job_templates import liste
    return liste()


@router.post("/jobs", response_model=JobOut, status_code=201)
async def create_job(data: JobIn, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    job = Job(**data.model_dump(), user_id=user.id)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.put("/jobs/{jid}", response_model=JobOut)
async def update_job(jid: int, data: JobIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Fehler(404, "err.job_not_found", "Job not found")
    for field, value in data.model_dump().items():
        setattr(job, field, value)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/run", response_model=JobOut)
async def run_job_now(jid: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    from ..core.redis import enqueue_task
    from ..services.scheduler import run_job_kind
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Fehler(404, "err.job_not_found", "Job not found")
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    job.last_run_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.flush()
    # script/workflow/http run directly here (as in the scheduler); only prompt jobs need the
    # worker. Without the kind branching a workflow job would land at the agent as a prompt.
    if await run_job_kind(db, job, jr):
        await db.commit()
    else:
        # Commit FIRST, queue AFTERWARDS. The other way round the assignment lies in Redis
        # before the JobRun exists in the database; a free worker grabs it within
        # milliseconds, finds `jr is None` and returns SILENTLY (worker/__main__.py:494). The
        # job run then stays on "running" forever, without an error and without a run.
        await db.commit()
        await enqueue_task({"kind": "job", "task_id": f"job-{jr.id}", "job_id": job.id,
                            "job_run_id": jr.id})
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/enabled", response_model=JobOut)
async def set_job_enabled(jid: int, data: EnabledIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """On/off without deleting: the scheduler skips deactivated jobs.
    Reactivating also lifts a pause_on_success `paused` again."""
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Fehler(404, "err.job_not_found", "Job not found")
    job.enabled = data.enabled
    if data.enabled:
        job.paused = False
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/jobs/{jid}/runs")
async def job_runs(jid: int, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Fehler(404, "err.job_not_found", "Job not found")
    rows = (await db.execute(select(JobRun).where(JobRun.job_id == jid).order_by(JobRun.id.desc()))).scalars().all()
    return [{"id": r.id, "status": r.status, "output": r.output, "started_at": r.started_at} for r in rows]


@router.delete("/jobs/{jid}", status_code=204)
async def delete_job(jid: int, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, jid)
    if job and is_owner_or_admin(job.user_id, user):
        await db.delete(job)
        await db.commit()


# ================= Permission-Regeln (pro Projekt) =================

class PermIn(BaseModel):
    tool: str
    resource: str = "*"
    action: PermAction = PermAction.ask


class PermOut(BaseModel):
    id: int; tool: str; resource: str; action: PermAction
    model_config = {"from_attributes": True}


@router.get("/projects/{project_id}/permissions", response_model=list[PermOut])
async def list_perms(access: Access = Depends(require_role(ProjectRole.maintainer)), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Permission).where(Permission.project_id == access.project.id))).scalars().all()
    return list(rows)


@router.post("/projects/{project_id}/permissions", response_model=PermOut, status_code=201)
async def add_perm(data: PermIn, access: Access = Depends(require_role(ProjectRole.maintainer)), db: AsyncSession = Depends(get_session)):
    p = Permission(project_id=access.project.id, tool=data.tool, resource=data.resource, action=data.action)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/projects/{project_id}/permissions/{perm_id}", status_code=204)
async def delete_perm(perm_id: int, access: Access = Depends(require_role(ProjectRole.maintainer)), db: AsyncSession = Depends(get_session)):
    p = await db.get(Permission, perm_id)
    if p and p.project_id == access.project.id:
        await db.delete(p)
        await db.commit()
