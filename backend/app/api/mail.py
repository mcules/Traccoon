"""E-Mail-Webhook → persönlicher Assistent (projektlos).

Portiert das predecessor-Verhalten (POST /webhooks/new-email, roher Body, HMAC-SHA256 im
Header X-Webhook-Signature ohne Prefix, Idempotenz über account:uid), ERGÄNZT um eine
LOKALE Vorklassifizierung: der Rohtext geht nur an das hausinterne Modell, nach außen
(Claude) reicht nur die geschwärzte Zusammenfassung. Nichts läuft ohne Freigabe.
"""
import datetime as dt
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models.assistant import AssistantTask
from ..models.user import User
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

    task = AssistantTask(
        owner_user_id=owner_id, kind="email", source=source, source_ref=src_ref,
        title=(subject or "(kein Betreff)")[:500],
        category=cls["category"], priority=cls["priority"],
        redacted_summary=cls["redacted_summary"],
        # Nur Metadaten für den späteren IMAP-Volltextzugriff — KEIN Rohtext gespeichert.
        meta={"account": account, "uid": uid, "from": sender, "subject": subject,
              "sensitive": cls["sensitive"]},
        status="new",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    log.info("Mail-Item #%s angelegt (%s, prio=%s, sensitive=%s)",
             task.id, cls["category"], cls["priority"], cls["sensitive"])
    return {"accepted": True, "id": task.id, "status": task.status}


# ================= Assistent-Inbox (auth) =================

def _out(t: AssistantTask) -> dict:
    return {
        "id": t.id, "kind": t.kind, "source": t.source, "title": t.title,
        "category": t.category, "priority": t.priority,
        "sensitive": bool((t.meta or {}).get("sensitive")),
        "redacted_summary": t.redacted_summary, "status": t.status,
        "from": (t.meta or {}).get("from"), "subject": (t.meta or {}).get("subject"),
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


@router.post("/assistant/inbox/{tid}/approve")
async def approve_inbox(tid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Freigabe = zugleich die Freigabe für den Volltextzugriff. Startet den Assistenten."""
    t = await _get_owned(tid, user, db)
    if t.status not in ("new", "error"):
        raise HTTPException(409, f"Item ist nicht freigebbar (Status {t.status})")
    t.status = "approved"
    t.error = ""
    await db.commit()
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{t.id}",
                        "assistant_task_id": t.id})
    return _out(t)


@router.post("/assistant/inbox/{tid}/reject")
async def reject_inbox(tid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    t = await _get_owned(tid, user, db)
    t.status = "done"
    t.result = "(verworfen)"
    t.finished_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    return _out(t)
