import datetime as dt
import hashlib
import hmac
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    # mode=workflow: welche Definition wird gestartet und wie wird der Payload gemappt
    # ({context_key: payload.dot.pfad}; leer = kompletter Payload als Kontext).
    workflow_definition_id: int | None = None
    context_map: dict = {}
    # mode=event: Name des gemeldeten Ereignisses (leer = webhook.<route>).
    event_name: str | None = None


class WebhookOut(BaseModel):
    id: int; public_id: str; route: str; mode: str; project_id: int | None
    owner_user_id: int | None; agent: str | None; classify_agent: str | None
    prompt_tmpl: str | None = None; auto_run: bool = False
    silent: bool; enabled: bool; secret_set: bool
    event_header: str | None = None; event_filter: str | None = None
    event_key_header: str | None = None; event_cooldowns: dict = {}
    alert_events: list = []; ref_field: str | None = None; notify_chat: str | None = None
    workflow_definition_id: int | None = None; context_map: dict = {}
    event_name: str | None = None


def _wh_out(w: WebhookSub) -> WebhookOut:
    return WebhookOut(
        id=w.id, public_id=w.public_id, route=w.route, mode=w.mode, project_id=w.project_id,
        owner_user_id=w.owner_user_id, agent=w.agent, classify_agent=w.classify_agent,
        prompt_tmpl=w.prompt_tmpl, auto_run=w.auto_run,
        silent=w.silent, enabled=w.enabled, secret_set=bool(w.secret),
        event_header=w.event_header, event_filter=w.event_filter,
        event_key_header=w.event_key_header, event_cooldowns=w.event_cooldowns or {},
        alert_events=w.alert_events or [], ref_field=w.ref_field, notify_chat=w.notify_chat,
        workflow_definition_id=w.workflow_definition_id, context_map=w.context_map or {})


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


class EnabledIn(BaseModel):
    enabled: bool


@router.post("/webhooks/{wid}/enabled", response_model=WebhookOut)
async def set_webhook_enabled(wid: int, data: EnabledIn, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    """An/Aus ohne Löschen — deaktiviert weist der Inbound-Endpoint ab (404)."""
    w = await db.get(WebhookSub, wid)
    if w is None or not is_owner_or_admin(w.owner_user_id, user):
        raise HTTPException(404, "Webhook nicht gefunden")
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
    """Punktpfad in der Nutzlast auflösen (`event.attributes.alarm`, `posten.0.name`)."""
    cur = data
    for teil in str(pfad).split("."):
        if isinstance(cur, dict) and teil in cur:
            cur = cur[teil]
        elif isinstance(cur, list) and teil.isdigit() and int(teil) < len(cur):
            cur = cur[int(teil)]
        else:
            return None
    return cur


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
        """Platzhalter füllen — {feld} für die oberste Ebene, {a.b.c} tiefer.

        Verschachtelte Nutzlasten sind der Normalfall, sobald der Absender kein Traccoon
        ist: `{position.address}` oder `{event.attributes.alarm}` ließen sich vorher nicht
        anschreiben, die Meldung enthielt dann den Platzhalter im Klartext.
        """
        out = tpl
        for treffer in set(re.findall(r"\{([A-Za-z0-9_.]+)\}", tpl)):
            wert = _dig_payload(payload, treffer)
            if wert is not None:
                out = out.replace("{" + treffer + "}", str(wert))
        return out

    from ..models.notification import Notification

    # Woher der Ereignis-Typ kommt: aus einer Kopfzeile (X-GitHub-Event) oder — wenn der
    # Absender keine setzt — aus der Nutzlast selbst (`payload:event.type`). Ohne den
    # zweiten Weg ließe sich alles filtern, was Kopfzeilen schickt, und nichts anderes;
    # Traccar etwa meldet jede Zündung und jeden Alarm über dieselbe URL, ohne Kopfzeile.
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

    # Alert-Events umgehen Coalescing und melden sofort.
    if event and event in (sub.alert_events or []):
        db.add(Notification(kind="webhook_alert", title=f"🚨 {route}: {event}",
                            body=(fill(sub.body_template) or fill(sub.title_template))[:4000],
                            chat_id=sub.notify_chat))
        await db.commit()
        # mode=assistant: Alert UND Agent-Lauf (z. B. uniwar-Angriff → Autopilot reagiert).
        # Nur notify/task: hier fertig (reine Sofort-Meldung).
        if sub.mode != "assistant":
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
        # E-Mail → Ereignis `mail.received`. Was daraus wird (einordnen, Spam, Assistent),
        # steht im Ablauf des Slots `mail_intake` — auch das Anlegen des Items und der
        # Start des Assistenten. Deshalb wird hier nichts mehr eingereiht.
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
        # Gerendertes Template als Notification (Telegram-Bot liefert aus).
        titel = f"Webhook: {route}"
        if sub.ref_field:
            # Idempotenz: dieselbe Meldung nicht zweimal. Absender wiederholen den Aufruf,
            # wenn die Antwort ausbleibt — beim Alarm eines Bewegungsmelders ist die zweite
            # Nachricht keine Information, sondern Lärm. Der Bezug steht im Titel, damit
            # der Abgleich ohne neue Spalte auskommt.
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
        # Meldet ein Ereignis; wer darauf hört, entscheiden die Abläufe selbst über den
        # Trigger an ihrem Start-Knoten. `event_name` am Webhook oder `event` im Payload.
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
        # Startet eine Workflow-Instanz. context_map = {context_key: payload_pfad} (dot-Pfade);
        # ohne Mapping wird der komplette Payload als Kontext übernommen.
        from ..models.workflow import WorkflowDefinition
        from ..services.jsonlogic import _dig
        from ..services.workflow_engine import start_workflow
        if sub.workflow_definition_id is None:
            raise HTTPException(400, "Webhook ohne workflow_definition_id")
        definition = await db.get(WorkflowDefinition, sub.workflow_definition_id)
        if definition is None or definition.current_version_id is None:
            raise HTTPException(400, "Workflow-Definition fehlt oder ist nicht veröffentlicht")
        # Idempotenz via ref_field → source_ref.
        src_ref = None
        if sub.ref_field and isinstance(payload, dict):
            # Punktpfad wie überall sonst: der Bezug eines Fremdsystems steckt fast nie
            # auf der obersten Ebene (`event.id`), und ohne ihn liefe jede Wiederholung
            # des Absenders als zweiter Ablauf.
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
        # Woran der Lauf hängt, sagt die Nutzlast: der Start-Knoten benennt das Feld, in dem
        # das Artefakt steht (Ticket-Kennung, Ticket-ID, Exemplar-ID). Ohne diese Bindung
        # liefe ein Ablauf mit Ticket-Subjekt ins Leere — seine Aktionen (Zustand setzen,
        # kommentieren, zuweisen) fänden nichts, worauf sie wirken könnten.
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
        return {"accepted": True, "mode": "workflow", "instance_id": inst.id,
                "status": inst.status.value,
                **({"issue_id": issue_id} if issue_id else {}),
                **({"hardware_asset_id": asset_id} if asset_id else {})}

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
    # prompt | script | workflow | http | film. Bewusst ein freies `str` ohne Validierung:
    # verzweigt wird an genau EINER Stelle (`services/scheduler.run_job_kind`), und eine
    # zweite Aufzählung hier wäre eine zweite Wahrheit darüber, welche Arten es gibt.
    # `film` = Feierabend-Film des Büros; seine Optionen stehen in `args`
    # ({tz, sekunden, fps, grade, kapitel, behalten_tage}).
    kind: str = "prompt"
    workflow_definition_id: int | None = None   # nur kind=workflow
    # nur kind=http: Ziel + Aufruf ({method, path, query, headers, body})
    destination_id: int | None = None
    http_request: dict = {}
    agent: str | None = None
    prompt: str = ""
    command: str = ""
    # Liste = Argumente eines script-Jobs (wie bisher). Objekt = Parametersatz eines
    # prompt-Jobs; seine Werte füllen die `{{platzhalter}}` im Prompt (services/job_params).
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
    """Vorlagen für neue Jobs. Sie füllen das Formular vor — ein angelegter Job trägt danach
    seine eigenen Felder und Parameter, keine Bindung an die Vorlage."""
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
        raise HTTPException(404, "Job nicht gefunden")
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
        raise HTTPException(404, "Job nicht gefunden")
    jr = JobRun(job_id=job.id, status="running")
    db.add(jr)
    job.last_run_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.flush()
    # script/workflow/http laufen hier direkt (wie im Scheduler); nur Prompt-Jobs brauchen
    # den Worker. Ohne die kind-Verzweigung landete ein Workflow-Job als Prompt beim Agenten.
    if await run_job_kind(db, job, jr):
        await db.commit()
    else:
        # ERST committen, DANN einreihen. Andersherum liegt der Auftrag in Redis, bevor es
        # den JobRun in der Datenbank gibt — ein freier Worker greift ihn in Millisekunden,
        # findet `jr is None` und kehrt STILL zurück (worker/__main__.py:494). Der Job-Run
        # bleibt dann für immer auf „running", ohne Fehler und ohne Lauf.
        await db.commit()
        await enqueue_task({"kind": "job", "task_id": f"job-{jr.id}", "job_id": job.id,
                            "job_run_id": jr.id})
    await db.refresh(job)
    return job


@router.post("/jobs/{jid}/enabled", response_model=JobOut)
async def set_job_enabled(jid: int, data: EnabledIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """An/Aus ohne Löschen — deaktivierte Jobs überspringt der Scheduler.
    Reaktivieren hebt zugleich ein pause_on_success-`paused` wieder auf."""
    job = await db.get(Job, jid)
    if job is None or not is_owner_or_admin(job.user_id, user):
        raise HTTPException(404, "Job nicht gefunden")
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
