import datetime as dt
import hashlib
import hmac
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.enums import ProjectRole
from ..models.ops import (
    InboundDelivery, Job, JobRun, PermAction, Permission, WebhookCoalesce, WebhookSub,
)
from ..models.user import User
from ..services import inbound
from ..services.inbound import (
    context_of as _context, dig_payload as _dig_payload, fill as _fill,
    reference_of as _reference, set_deep as _set_deep,
)
from .deps import (
    Access, build_access, get_current_user, is_owner_or_admin, owned_or_global,
    require_admin, require_role,
)

# The shaping of a payload into a context lives in the service, because that is where the
# work happens now. The names stay here so that whoever imported them from the router, the
# scheduler on its collection window, the tests on their way in, keeps finding them.
__all__ = ["router", "_context", "_reference", "_dig_payload", "_fill", "_set_deep"]

router = APIRouter(tags=["ops"])


# ================= Webhooks =================

class WebhookIn(BaseModel):
    route: str
    secret: str = ""
    # workflow = start a flow, event = report an event. A trigger needs no more than that:
    # ticket, report and assistant assignment are nodes INSIDE the flow.
    mode: str = "workflow"
    project_id: int | None = None
    # Filter / Idempotenz / Coalescing / Alarme
    event_header: str | None = None
    event_filter: str | None = None
    event_key_header: str | None = None
    event_cooldowns: dict = {}
    alert_events: list = []
    # A field of the payload OR a template out of several ({account}:{uid}), that becomes the
    # a key against a double delivery.
    ref_field: str | None = None
    # mode=workflow: welche Definition startet.
    workflow_definition_id: int | None = None
    # Context of the run: from the payload ({target: dotted.path}; an empty path = everything)
    # and fixed ({target: value}, `{field}` in it is filled from the payload). Dots in the
    # target nest. Without either, the payload is the context.
    context_map: dict = {}
    context_fixed: dict = {}
    # mode=event: name of the reported event (empty = webhook.<route>).
    event_name: str | None = None
    # mode=workflow: hold the request open until the flow answers (seconds, 0 = off), and
    # which fields the answer carries ({field: context.path}; empty = context.antwort).
    response_timeout: int = 0
    response_map: dict = {}


class WebhookOut(BaseModel):
    id: int; public_id: str; route: str; mode: str; project_id: int | None
    owner_user_id: int | None
    enabled: bool; secret_set: bool
    event_header: str | None = None; event_filter: str | None = None
    event_key_header: str | None = None; event_cooldowns: dict = {}
    alert_events: list = []; ref_field: str | None = None
    workflow_definition_id: int | None = None
    context_map: dict = {}; context_fixed: dict = {}
    event_name: str | None = None
    response_timeout: int = 0; response_map: dict = {}


def _wh_out(w: WebhookSub) -> WebhookOut:
    return WebhookOut(
        id=w.id, public_id=w.public_id, route=w.route, mode=w.mode, project_id=w.project_id,
        owner_user_id=w.owner_user_id, enabled=w.enabled, secret_set=bool(w.secret),
        event_name=w.event_name,
        event_header=w.event_header, event_filter=w.event_filter,
        event_key_header=w.event_key_header, event_cooldowns=w.event_cooldowns or {},
        alert_events=w.alert_events or [], ref_field=w.ref_field,
        workflow_definition_id=w.workflow_definition_id, context_map=w.context_map or {},
        context_fixed=w.context_fixed or {},
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
        raise Error(400, "err.target_project_does_not_exist", "The target project does not exist")
    if not (await build_access(proj, user, db)).ai_assign:
        raise Error(403, "err.ai_right_target_project_required",
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
        raise Error(404, "err.webhook_not_found", "Webhook not found")
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
        raise Error(404, "err.webhook_not_found", "Webhook not found")
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


# Hard ceiling for a held-open request. Whoever waits here occupies a connection, and a
# sender that runs into ITS own timeout meanwhile gets nothing out of a longer wait.
ANSWER_MAX_SEC = 120


async def _wait_on_answer(instance_id: int, sub: WebhookSub) -> dict:
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

    limit = max(0, min(int(sub.response_timeout or 0), ANSWER_MAX_SEC))
    clock = asyncio.get_running_loop().time
    end = clock() + limit
    karte = sub.response_map or {}
    ctx: dict = {}
    status, done = "weg", False
    while True:
        # A session of its own per look: the answer is written by another task in another
        # transaction, and a session that keeps its snapshot would never see it.
        async with SessionLocal() as s:
            inst = await s.get(WorkflowInstance, instance_id)
            if inst is None:
                break
            ctx = dict(inst.context or {})
            status = inst.status.value
            done = inst.status not in (IStatus.running, IStatus.waiting)
        # An answer that already stands does not need the end of the flow: answering first
        # and tidying up afterwards is a perfectly good order.
        if karte:
            ready = done or all(_dig_payload(ctx, path) is not None for path in karte.values())
        else:
            ready = done or "answer" in ctx
        if ready or clock() >= end:
            break
        await asyncio.sleep(0.4)

    if karte:
        answer = {key: _dig_payload(ctx, path) for key, path in karte.items()}
    else:
        answer = ctx.get("answer")
    return {"instance_id": instance_id, "status": status, "done": done,
            "answer": answer}


# ── The inbox, for looking at and for repeating ─────────────────────────────

def _delivery_out(row: InboundDelivery) -> dict:
    return {
        "id": row.id, "channel": row.channel, "target": row.target, "route": row.route,
        "status": row.status, "attempts": row.attempts, "last_error": row.last_error,
        "outcome": row.outcome, "received_at": row.received_at,
        "next_try_at": row.next_try_at, "finished_at": row.finished_at,
        "size": len(row.body or b""),
    }


@router.get("/inbound")
async def list_inbound(status: str = "", limit: int = 100,
                       _: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_session)):
    """What came in from outside. Newest first, because a delivery is asked about while it is
    still fresh, and a parked one is asked about the moment the message arrives."""
    q = select(InboundDelivery).order_by(InboundDelivery.id.desc()).limit(max(1, min(limit, 500)))
    if status:
        q = q.where(InboundDelivery.status.in_([s for s in status.split(",") if s]))
    rows = (await db.execute(q)).scalars().all()
    return [_delivery_out(r) for r in rows]


@router.get("/inbound/{did}/body")
async def inbound_body(did: int, _: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_session)):
    """The payload as it arrived. The reason for looking at a parked delivery at all is
    usually the question what actually stood in it."""
    row = await db.get(InboundDelivery, did)
    if row is None:
        raise Error(404, "err.delivery_not_found", "Delivery not found")
    return {"id": row.id, "headers": row.headers,
            "body": (row.body or b"").decode("utf-8", "replace")}


@router.post("/inbound/{did}/retry")
async def inbound_retry(did: int, _: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session)):
    """Try it again, right now. For whatever stood still because something else was broken."""
    row = await db.get(InboundDelivery, did)
    if row is None:
        raise Error(404, "err.delivery_not_found", "Delivery not found")
    row.status, row.attempts, row.next_try_at = "new", 0, None
    row.finished_at, row.last_error, row.outcome = None, "", ""
    await db.flush()
    await inbound.work_one(db, row)
    await db.commit()
    await db.refresh(row)
    return _delivery_out(row)


@router.post("/hooks/{public_id}", status_code=202)
async def inbound_webhook(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Public inbound endpoint by GUID. Takes the delivery in; the work happens afterwards.

    The answer is a receipt and not a result, and that is the whole change: whatever goes
    wrong later, a missing flow, a restart in the middle, a bug of ours, costs at most a
    repeat, never the payload. Nobody sending to us tries twice.

    The signature is deliberately NOT checked here. It is checked when the work is done, over
    exactly the bytes that were stored, which is what lets a small separate receiver stand at
    this door while the rest of the house is being rebuilt: it needs no secrets at all.

    The one exception is a caller that waits for the answer of the flow (`response_timeout`).
    That one cannot be served from a queue, it wants the answer, not a receipt, so it keeps
    running through synchronously.
    """
    sub = (await db.execute(
        select(WebhookSub).where(WebhookSub.public_id == public_id))).scalar_one_or_none()
    if sub is None or not sub.enabled:
        raise Error(404, "err.unknown_route", "Unknown route")
    raw = await request.body()
    headers = dict(request.headers)

    if int(sub.response_timeout or 0) > 0:
        return await _answering_webhook(db, sub, raw, headers)

    row = await inbound.store(db, channel="webhook", target=public_id, route=sub.route,
                              body=raw, headers=headers)
    await db.commit()
    return {"accepted": True, "delivery_id": row.id}


async def _answering_webhook(db: AsyncSession, sub: WebhookSub, raw: bytes, headers: dict):
    """The synchronous way, for a caller that wants the answer of the flow.

    Kept apart on purpose: everything in here happens while somebody is waiting, so it cannot
    be repeated and cannot be parked. If it throws, the caller sees it, which is the honest
    outcome when the promise was an answer.
    """
    from fastapi.responses import JSONResponse
    try:
        result = await inbound.deliver(db, sub, raw, headers)
    except inbound.Dropped as why:
        await db.commit()
        return JSONResponse({"accepted": True, "ignored": True, "reason": str(why)},
                            status_code=202)
    except inbound.Retry as why:
        raise Error(400, "err.delivery_not_possible",
                    "The delivery cannot be carried out: {why}", why=str(why))
    inst_id = result.get("instance_id")
    if inst_id is None:
        await db.commit()
        return JSONResponse(result, status_code=202)
    await db.commit()
    answer_result = await _wait_on_answer(inst_id, sub)
    answer = answer_result["answer"]
    if isinstance(answer, dict):
        base = answer
    elif answer is None:
        base = {"accepted": True, "mode": "workflow", "answer": None,
                "instance_id": inst_id, "status": answer_result["status"],
                "done": answer_result["done"]}
    else:
        base = {"answer": answer}
    return JSONResponse(base, status_code=200 if answer is not None else 202)


# ================= Jobs =================

class JobIn(BaseModel):
    name: str
    # The schedule: cron | interval | once. Unlike `kind` this is checked, and the reason
    # stands in `services/scheduler.SCHEDULE_KINDS`: an unknown value does not break the job
    # but silences it, it is simply never due, while the UI shows "enabled". Exactly that way
    # a job lay dead for 13 days, because `prompt` stood there, that is the kind of work
    # instead of the schedule.
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

    @field_validator("type")
    @classmethod
    def _schedule_check(cls, value: str) -> str:
        from ..services.scheduler import SCHEDULE_KINDS
        if value not in SCHEDULE_KINDS:
            raise ValueError(
                f"'{value}' is no schedule. Allowed: {', '.join(SCHEDULE_KINDS)}. "
                f"Die Art der Arbeit (prompt, workflow, film …) gehoert in `kind`.")
        return value


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
async def job_templates(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Templates for new jobs. They prefill the form; a created job then carries its own
    fields and parameters, with no binding to the template.

    The session is needed for the flow behind the research jobs: the template names it by
    key, the form needs its number.
    """
    from ..services.job_templates import listing_for
    return await listing_for(db)


@router.post("/jobs", response_model=JobOut, status_code=201)
async def create_job(data: JobIn, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    job = Job(**data.model_dump(), user_id=user.id)
    db.add(job)
    await db.flush()
    # Whoever still enters an old kind (a template, the agent tool, an old script) gets a flow
    # right away. Otherwise a path would stay open here that a later restart would have to
    # collect, and until then the job would run differently from what is shown.
    from ..services.job_modes import OLD_KINDS, as_flow
    if job.kind in OLD_KINDS:
        await as_flow(db, job)
    await db.commit()
    await db.refresh(job)
    return job


@router.put("/jobs/{jid}", response_model=JobOut)
async def update_job(jid: int, data: JobIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Error(404, "err.job_not_found", "Job not found")
    for field, value in data.model_dump().items():
        setattr(job, field, value)
    from ..services.job_modes import OLD_KINDS, as_flow
    if job.kind in OLD_KINDS:
        await as_flow(db, job)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/run", response_model=JobOut)
async def run_job_now(jid: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    from ..services.scheduler import run_job_kind
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Error(404, "err.job_not_found", "Job not found")
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    job.last_run_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.flush()
    # One way for all kinds (as in the schedule and in the agent tool). The work itself stands
    # in the flow; where it takes longer, the flow waits for it, not this call.
    await run_job_kind(db, job, jr)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/enabled", response_model=JobOut)
async def set_job_enabled(jid: int, data: EnabledIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """On/off without deleting: the scheduler skips deactivated jobs.
    Reactivating also lifts a pause_on_success `paused` again."""
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise Error(404, "err.job_not_found", "Job not found")
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
        raise Error(404, "err.job_not_found", "Job not found")
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
