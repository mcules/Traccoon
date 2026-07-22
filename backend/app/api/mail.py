"""E-Mail-Webhook → persönlicher Assistent (projektlos).

Portiert das nexus-Verhalten (POST /webhooks/new-email, roher Body, HMAC-SHA256 im
Header X-Webhook-Signature ohne Prefix, Idempotenz über account:uid), ERGÄNZT um eine
LOKALE Vorklassifizierung: der Rohtext geht nur an das hausinterne Modell, nach außen
(Claude) reicht nur die geschwärzte Zusammenfassung. Nichts läuft ohne Freigabe.
"""
import datetime as dt
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models.assistant import AssistantPolicy, AssistantTask
from ..models.notification import Notification
from ..models.user import User
from ..services.assistant_inbox import approve_assistant_task, reject_assistant_task
from ..services.assistant_policy import match_policy, note_hit, parse_sender, upsert_policy
from ..services.mail_classify import classify_email
from .deps import get_current_user, is_owner_or_admin

log = logging.getLogger("traccoon.mail")
router = APIRouter(tags=["assistant"])


# ================= Inbound-Webhook (öffentlich, HMAC-geschützt) =================

@router.post("/webhooks/new-email", status_code=202)
async def new_email(request: Request, db: AsyncSession = Depends(get_session)):
    raw = await request.body()
    secret = settings.mail_webhook_secret
    if not secret:
        raise HTTPException(503, "Mail-Webhook nicht konfiguriert")
    sig = request.headers.get("X-Webhook-Signature", "")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.strip(), expected):
        raise HTTPException(401, "Signatur ungültig")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    account = str(payload.get("account") or "")
    subject = str(payload.get("subject") or "")
    sender = str(payload.get("from") or "")
    uid = payload.get("uid")
    body = str(payload.get("body") or "")

    # Idempotenz: dieselbe Mail (account:uid) erzeugt kein zweites Item.
    source = "webhook:new-email"
    src_ref = f"{account}:{uid}" if uid is not None else None
    if src_ref:
        dup = (await db.execute(select(AssistantTask).where(
            AssistantTask.source == source,
            AssistantTask.source_ref == src_ref))).scalar_one_or_none()
        if dup is not None:
            return {"accepted": True, "duplicate": True, "id": dup.id}

    owner_id = settings.mail_assistant_owner_id or None

    # LOKAL vorklassifizieren + schwärzen (Rohtext verlässt das Haus nicht).
    cls = await classify_email(db, owner_id, account=account, sender=sender,
                               subject=subject, body=body)

    # Gelernte Regel suchen (Absender > Domain > Kategorie). Treffer kann automatisch
    # freigeben, die Schwärzung bestimmen und einen Handlungs-Hinweis mitgeben.
    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=cls["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve

    task = AssistantTask(
        owner_user_id=owner_id, kind="email", source=source, source_ref=src_ref,
        title=(subject or "(kein Betreff)")[:500],
        category=cls["category"], priority=cls["priority"],
        redacted_summary=cls["redacted_summary"],
        # Nur Metadaten für den späteren IMAP-Volltextzugriff — KEIN Rohtext gespeichert,
        # AUSSER eine Regel erlaubt ausdrücklich 'unredacted' (dann liegt der Volltext bereit).
        meta={"account": account, "uid": uid, "from": sender, "subject": subject,
              "sensitive": cls["sensitive"]},
        redaction=redaction, action_hint=action_hint or "",
        raw_body=(body if redaction == "unredacted" else None),
        status=("approved" if auto else "new"),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    log.info("Mail-Item #%s angelegt (%s, prio=%s, sensitive=%s, regel=%s, auto=%s)",
             task.id, cls["category"], cls["priority"], cls["sensitive"],
             policy.id if policy else None, auto)
    if auto:
        from ..core.redis import enqueue_task
        await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task.id}",
                            "assistant_task_id": task.id})
    elif owner_id:
        # Freigabe-Karte per Telegram (falls eingerichtet) — parallel zur Inbox-Seite.
        owner = await db.get(User, owner_id)
        if owner and owner.telegram_chat_id:
            db.add(Notification(
                user_id=owner_id, assistant_task_id=task.id, kind="assistant_review",
                chat_id=owner.telegram_chat_id, title=f"📥 {task.title}"[:200],
                body=(f"von {sender}\n{cls['redacted_summary']}")[:1000]))
            await db.commit()
    return {"accepted": True, "id": task.id, "status": task.status, "auto": auto}


# ================= Assistent-Inbox (auth) =================

def _out(t: AssistantTask) -> dict:
    return {
        "id": t.id, "kind": t.kind, "source": t.source, "title": t.title,
        "category": t.category, "priority": t.priority,
        "sensitive": bool((t.meta or {}).get("sensitive")),
        "redacted_summary": t.redacted_summary, "status": t.status,
        "from": (t.meta or {}).get("from"), "subject": (t.meta or {}).get("subject"),
        "redaction": t.redaction, "action_hint": t.action_hint,
        "result": t.result, "error": t.error,
        "created_at": t.created_at, "finished_at": t.finished_at,
    }


async def _get_owned(tid: int, user: User, db: AsyncSession) -> AssistantTask:
    t = await db.get(AssistantTask, tid)
    if t is None or not is_owner_or_admin(t.owner_user_id, user):
        raise HTTPException(404, "Nicht gefunden")
    return t


@router.get("/assistant/inbox")
async def list_inbox(status_filter: str | None = None,
                     user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    q = select(AssistantTask).order_by(AssistantTask.id.desc())
    # Admin sieht alles; sonst nur eigene.
    if user.global_role != "admin":
        q = q.where(AssistantTask.owner_user_id == user.id)
    if status_filter:
        q = q.where(AssistantTask.status == status_filter)
    rows = (await db.execute(q.limit(200))).scalars().all()
    return [_out(t) for t in rows]


@router.get("/assistant/inbox/{tid}")
async def get_inbox(tid: int, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    return _out(await _get_owned(tid, user, db))


class ApproveIn(BaseModel):
    # once = nur dieses Item; sender|domain|category = Regel „ab jetzt immer" anlegen.
    scope: str = "once"
    redaction: str = "redacted"   # redacted | unredacted
    action_note: str = ""         # optionaler gelernter Handlungs-Hinweis


@router.post("/assistant/inbox/{tid}/approve")
async def approve_inbox(tid: int, data: ApproveIn | None = None,
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Freigabe = zugleich die Freigabe für den Volltextzugriff. Startet den Assistenten.
    `scope` != once lernt eine AssistantPolicy (immer für Absender/Domain/Kategorie)."""
    d = data or ApproveIn()
    t = await _get_owned(tid, user, db)
    if t.status not in ("new", "error"):
        raise HTTPException(409, f"Item ist nicht freigebbar (Status {t.status})")
    await approve_assistant_task(db, t, scope=d.scope, redaction=d.redaction, action_note=d.action_note)
    await db.refresh(t)
    return _out(t)


@router.post("/assistant/inbox/{tid}/reject")
async def reject_inbox(tid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    t = await _get_owned(tid, user, db)
    await reject_assistant_task(db, t)
    await db.refresh(t)
    return _out(t)


# ================= Gelernte Regeln (AssistantPolicy) =================

def _pol_out(p: AssistantPolicy) -> dict:
    return {
        "id": p.id, "match_kind": p.match_kind, "match_value": p.match_value,
        "auto_approve": p.auto_approve, "redaction": p.redaction,
        "action_hint": p.action_hint, "enabled": p.enabled,
        "hit_count": p.hit_count, "last_used_at": p.last_used_at, "created_at": p.created_at,
    }


class PolicyIn(BaseModel):
    match_kind: str = "sender"       # sender | domain | category
    match_value: str
    auto_approve: bool = True
    redaction: str = "redacted"      # redacted | unredacted
    action_hint: str = ""
    enabled: bool = True


async def _pol_owned(pid: int, user: User, db: AsyncSession) -> AssistantPolicy:
    p = await db.get(AssistantPolicy, pid)
    if p is None or not is_owner_or_admin(p.owner_user_id, user):
        raise HTTPException(404, "Regel nicht gefunden")
    return p


@router.get("/assistant/policies")
async def list_policies(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    q = select(AssistantPolicy).order_by(AssistantPolicy.match_kind, AssistantPolicy.match_value)
    if user.global_role != "admin":
        q = q.where(AssistantPolicy.owner_user_id == user.id)
    rows = (await db.execute(q)).scalars().all()
    return [_pol_out(p) for p in rows]


@router.post("/assistant/policies")
async def create_policy(data: PolicyIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    p = await upsert_policy(db, user.id, match_kind=data.match_kind, match_value=data.match_value,
                            auto_approve=data.auto_approve, redaction=data.redaction,
                            action_hint=data.action_hint)
    p.enabled = data.enabled
    await db.commit()
    await db.refresh(p)
    return _pol_out(p)


@router.put("/assistant/policies/{pid}")
async def update_policy(pid: int, data: PolicyIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    p = await _pol_owned(pid, user, db)
    p.match_kind = data.match_kind if data.match_kind in ("sender", "domain", "category") else p.match_kind
    p.match_value = (data.match_value or "").strip().lower()
    p.auto_approve = data.auto_approve
    p.redaction = data.redaction if data.redaction in ("redacted", "unredacted") else "redacted"
    p.action_hint = data.action_hint
    p.enabled = data.enabled
    await db.commit()
    await db.refresh(p)
    return _pol_out(p)


@router.delete("/assistant/policies/{pid}", status_code=204)
async def delete_policy(pid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    p = await _pol_owned(pid, user, db)
    await db.delete(p)
    await db.commit()
