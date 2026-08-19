"""Assistant inbox, learned rules (policy) and web chat.

The e-mail inbox itself runs over the NORMAL webhook (WebhookSub, mode 'assistant',
api/ops.py to services/mail_intake.py). Here stand only the UI and administration endpoints.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.assistant import AssistantPermission, AssistantPolicy, AssistantTask
from ..models.user import User
from ..services.assistant_inbox import approve_assistant_task, reject_assistant_task
from ..services.assistant_policy import upsert_policy
from .deps import get_current_user, is_owner_or_admin

log = logging.getLogger("traccoon.mail")
router = APIRouter(tags=["assistant"])


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
    # An admin sees everything; everybody else only their own.
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
    # once = this item only; sender|domain|category creates a rule "always from now on".
    scope: str = "once"
    redaction: str = "redacted"   # redacted | unredacted
    action_note: str = ""         # optionaler gelernter Handlungs-Hinweis


@router.post("/assistant/inbox/{tid}/approve")
async def approve_inbox(tid: int, data: ApproveIn | None = None,
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """The approval is at the same time the approval for full text access. Starts the assistant.
    `scope` != once learns an AssistantPolicy (always for the sender, domain or category)."""
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


# ================= Gelernte Tool-Freigaben (AssistantPermission) =================

class PermIn(BaseModel):
    tool: str
    resource: str = "*"
    action: str = "allow"   # allow | ask | deny


@router.get("/assistant/tool-permissions")
async def list_tool_perms(user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    q = select(AssistantPermission).order_by(AssistantPermission.tool)
    if user.global_role != "admin":
        q = q.where(AssistantPermission.owner_user_id == user.id)
    rows = (await db.execute(q)).scalars().all()
    return [{"id": p.id, "tool": p.tool, "resource": p.resource, "action": p.action} for p in rows]


@router.post("/assistant/tool-permissions")
async def upsert_tool_perm(data: PermIn, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_session)):
    from ..worker.assistant_gate import learn_permission
    action = data.action if data.action in ("allow", "ask", "deny") else "allow"
    await learn_permission(db, user.id, data.tool.strip(), (data.resource or "*").strip(), action)
    return {"ok": True}


@router.delete("/assistant/tool-permissions/{pid}", status_code=204)
async def delete_tool_perm(pid: int, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_session)):
    p = await db.get(AssistantPermission, pid)
    if p is None or not is_owner_or_admin(p.owner_user_id, user):
        raise HTTPException(404, "Nicht gefunden")
    await db.delete(p)
    await db.commit()


# ================= Chat with the assistant (web, in parallel to Telegram) =================

def _chat_out(t: AssistantTask) -> dict:
    return {
        "id": t.id, "text": (t.meta or {}).get("chat_text") or t.title,
        "status": t.status, "result": t.result, "error": t.error,
        "pending_tool": t.pending_tool, "created_at": t.created_at, "finished_at": t.finished_at,
    }


class ChatIn(BaseModel):
    text: str


class DecideIn(BaseModel):
    decision: str   # once | always | never


@router.get("/assistant/chat")
async def chat_history(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(AssistantTask).where(
        AssistantTask.owner_user_id == user.id, AssistantTask.kind == "chat")
        .order_by(AssistantTask.id.desc()).limit(50))).scalars().all()
    return [_chat_out(t) for t in reversed(rows)]  # oldest first (the conversation history)


@router.post("/assistant/chat")
async def chat_send(data: ChatIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    text = (data.text or "").strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="approved",
                      title=text[:200], meta={"chat_text": text})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{t.id}",
                        "assistant_task_id": t.id})
    return _chat_out(t)


@router.post("/assistant/chat/{tid}/decide")
async def chat_decide(tid: int, data: DecideIn, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    t = await _get_owned(tid, user, db)
    if t.status != "awaiting":
        raise HTTPException(409, "Keine offene Freigabe")
    if data.decision not in ("once", "always", "never"):
        raise HTTPException(400, "Ungültige Entscheidung")
    from ..worker.assistant_gate import apply_perm_decision
    await apply_perm_decision(db, t, data.decision)
    await db.refresh(t)
    return _chat_out(t)
