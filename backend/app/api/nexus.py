import datetime as dt
import hashlib
import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.enums import ProjectRole, TicketAgentStatus
from ..models.predecessor import Job, JobRun, PermAction, Permission, WebhookCoalesce, WebhookSub
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from ..models.user import User
from .deps import (
    Access, build_access, get_current_user, is_owner_or_admin, owned_or_global,
    require_admin, require_role,
)

router = APIRouter(tags=["predecessor"])


# ================= Webhooks =================

class WebhookIn(BaseModel):
    route: str
    secret: str = ""
    mode: str = "task"
    project_id: int | None = None
    agent: str | None = None
    classify_agent: str | None = None
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


class WebhookOut(BaseModel):
    id: int; public_id: str; route: str; mode: str; project_id: int | None
    owner_user_id: int | None; agent: str | None; classify_agent: str | None
    silent: bool; enabled: bool; secret_set: bool
    event_header: str | None = None; event_filter: str | None = None
    event_key_header: str | None = None; event_cooldowns: dict = {}
    alert_events: list = []; ref_field: str | None = None; notify_chat: str | None = None


def _wh_out(w: WebhookSub) -> WebhookOut:
    return WebhookOut(
        id=w.id, public_id=w.public_id, route=w.route, mode=w.mode, project_id=w.project_id,
        owner_user_id=w.owner_user_id, agent=w.agent, classify_agent=w.classify_agent,
        silent=w.silent, enabled=w.enabled, secret_set=bool(w.secret),
        event_header=w.event_header, event_filter=w.event_filter,
        event_key_header=w.event_key_header, event_cooldowns=w.event_cooldowns or {},
        alert_events=w.alert_events or [], ref_field=w.ref_field, notify_chat=w.notify_chat)


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # Eigene + globale (Admin: alle). Owner-Filter via deps.owned_or_global.
    rows = (await db.execute(select(WebhookSub)
                             .where(owned_or_global(WebhookSub.owner_user_id, user))
                             .order_by(WebhookSub.route))).scalars().all()
    return [_wh_out(w) for w in rows]


async def _check_webhook_project(project_id: int | None, user: User, db: AsyncSession) -> None:
    """Ein task-Webhook legt Tickets im Projekt an → Ersteller muss dort Mitglied (ai_assign) sein."""
    if project_id is None:
        return
    from ..models.project import Project
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(400, "Zielprojekt existiert nicht")
    if not (await build_access(proj, user, db)).ai_assign:
        raise HTTPException(403, "KI-Recht im Zielprojekt erforderlich")


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
        raise HTTPException(404, "Webhook nicht gefunden")
    await _check_webhook_project(data.project_id, user, db)
    for field, value in data.model_dump().items():
        if field == "secret" and not value:
            continue  # leeres Feld lässt das bestehende Secret unangetastet
        setattr(w, field, value)
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


@router.post("/hooks/{public_id}", status_code=202)
async def inbound_webhook(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Öffentlicher Inbound-Endpoint per GUID. HMAC-Prüfung (X-Webhook-Signature, hex, ohne Prefix)."""
    sub = (await db.execute(
        select(WebhookSub).where(WebhookSub.public_id == public_id))).scalar_one_or_none()
    if sub is None or not sub.enabled:
        raise HTTPException(404, "Unbekannte Route")
    route = sub.route  # Label für Coalescing/Notify/Idempotenz-Quelle
    raw = await request.body()
    if sub.secret:
        sig = request.headers.get("X-Webhook-Signature", "")
        expected = hmac.new(sub.secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "Signatur ungültig")
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    def fill(tpl: str) -> str:
        out = tpl
        for k, v in (payload.items() if isinstance(payload, dict) else []):
            out = out.replace("{" + k + "}", str(v))
        return out

    from ..models.notification import Notification

    # Event-Typ aus Header (z. B. X-GitHub-Event) → Filter / Alert / Cooldown
    event = request.headers.get(sub.event_header, "") if sub.event_header else ""
    if sub.event_filter:
        allowed = [e.strip() for e in sub.event_filter.split(",") if e.strip()]
        if event not in allowed:
            return {"accepted": True, "ignored": True, "event": event}

    # Alert-Events umgehen Coalescing und melden sofort.
    if event and event in (sub.alert_events or []):
        db.add(Notification(kind="webhook_alert", title=f"🚨 {route}: {event}",
                            body=(fill(sub.body_template) or fill(sub.title_template))[:4000],
                            chat_id=sub.notify_chat))
        await db.commit()
        return {"accepted": True, "alert": True, "event": event}

    # Coalescing: innerhalb des Cooldown-Fensters nur sammeln, der Scheduler fasst zusammen.
    cooldown = int((sub.event_cooldowns or {}).get(event, 0)) if event else 0
    if cooldown > 0:
        now = dt.datetime.now(tz=dt.timezone.utc)
        ekey = (request.headers.get(sub.event_key_header, "") if sub.event_key_header else "") or event
        open_win = (await db.execute(select(WebhookCoalesce).where(
            WebhookCoalesce.route == route, WebhookCoalesce.event_key == ekey,
            WebhookCoalesce.flushed.is_(False), WebhookCoalesce.window_until > now,
        ).with_for_update())).scalars().first()
        if open_win is not None:
            # JSON-Spalte: neue Liste zuweisen, sonst erkennt SQLAlchemy die Änderung nicht.
            open_win.payloads = [*open_win.payloads, payload]
            await db.commit()
            return {"accepted": True, "coalesced": True, "event": event}
        # Erste Zustellung läuft normal durch, öffnet aber das Fenster für Folge-Events.
        db.add(WebhookCoalesce(route=route, event_key=ekey,
                               window_until=now + dt.timedelta(seconds=cooldown), payloads=[]))

    if sub.mode == "assistant":
        # E-Mail → projektlose AssistantTask (lokale Vorklassifizierung durch classify_agent).
        from ..services.mail_intake import intake_mail
        task, auto = await intake_mail(
            db, sub.owner_user_id, payload if isinstance(payload, dict) else {},
            source=f"webhook:{route}", classify_agent=sub.classify_agent or "")
        if task is None:
            return {"accepted": True, "ignored": True}
        if auto:
            from ..core.redis import enqueue_task
            await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task.id}",
                                "assistant_task_id": task.id})
        return {"accepted": True, "id": task.id, "status": task.status, "auto": auto}

    if sub.mode == "notify":
        # Gerendertes Template als Notification (Telegram-Bot liefert aus).
        db.add(Notification(kind="webhook", title=f"Webhook: {route}",
                            body=(fill(sub.body_template) or fill(sub.title_template))[:4000],
                            chat_id=sub.notify_chat))
        await db.commit()
        return {"accepted": True, "mode": "notify"}

    # Idempotenz: ref_field → source_ref; Doppel-Delivery erzeugt kein zweites Ticket.
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
        raise HTTPException(400, "Webhook ohne project_id kann kein Ticket anlegen")
    t = (await db.execute(select(IssueType).where(IssueType.project_id == sub.project_id).order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == sub.project_id).order_by(WorkflowStatus.order))).scalars().first()
    if t is None or s is None:
        raise HTTPException(400, "Projekt ohne Typ/Status")
    from ..models.project import Project
    project = await db.get(Project, sub.project_id)
    counter = (await db.execute(select(IssueCounter).where(
        IssueCounter.project_id == sub.project_id).with_for_update())).scalar_one_or_none()
    if project is None or counter is None:
        raise HTTPException(400, "Zielprojekt nicht mehr verfügbar")
    counter.last_number += 1
    n = counter.last_number
    from ..models.user import SYSTEM_USER_ID
    # Eigentümer des Webhooks ist Reporter → damit läuft der Agent mit dessen Token + MCP.
    owner = sub.owner_user_id or SYSTEM_USER_ID
    issue = Issue(
        project_id=sub.project_id, number=n, key=f"{project.key}-{n}"[:50],
        type_id=t.id, status_id=s.id, summary=fill(sub.title_template)[:500] or f"Webhook {sub.route}",
        description=fill(sub.body_template), reporter_id=owner, rank=f"{n:08d}",
        source=f"webhook:{sub.route}", source_ref=src_ref,
    )
    if sub.agent:
        issue.assigned_agent = sub.agent
        issue.assigned_by_user_id = sub.owner_user_id  # Owner-Token-Auflösung im Worker
        issue.assigned_at = dt.datetime.now(tz=dt.timezone.utc)
        issue.agent_status = TicketAgentStatus.planning
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return {"accepted": True, "issue_key": issue.key}


# ================= Jobs =================

class JobIn(BaseModel):
    name: str
    type: str = "interval"
    schedule: str = "60"
    kind: str = "prompt"          # prompt | script
    agent: str | None = None
    prompt: str = ""
    command: str = ""
    args: list = []
    project_id: int | None = None
    notify_mode: str = "on_output"
    notify_chat: str | None = None
    result_html: bool = False
    pause_on_success: bool = False
    run_timeout: int = 600


class JobOut(BaseModel):
    id: int; name: str; type: str; schedule: str; kind: str; agent: str | None
    notify_mode: str; result_html: bool
    enabled: bool; paused: bool; last_run_at: dt.datetime | None
    model_config = {"from_attributes": True}


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Job).where(owned_or_global(Job.user_id, user))
                             .order_by(Job.id))).scalars().all()
    return list(rows)


@router.post("/jobs", response_model=JobOut, status_code=201)
async def create_job(data: JobIn, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    job = Job(**data.model_dump(), user_id=user.id)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/run", response_model=JobOut)
async def run_job_now(jid: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    from ..core.redis import enqueue_task
    from ..services.scheduler import _run_script
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise HTTPException(404, "Job nicht gefunden")
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    job.last_run_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.flush()
    if job.kind == "script":
        await _run_script(db, job, jr)
    else:
        await enqueue_task({"kind": "job", "task_id": f"job-{jr.id}", "job_id": job.id, "job_run_id": jr.id})
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/jobs/{jid}/runs")
async def job_runs(jid: int, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise HTTPException(404, "Job nicht gefunden")
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
