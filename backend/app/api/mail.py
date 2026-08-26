"""Assistant inbox, learned rules (policy) and web chat.

The e-mail inbox itself runs over the NORMAL webhook (WebhookSub, mode 'assistant',
api/ops.py to services/mail_intake.py). Here stand only the UI and administration endpoints.
"""
import datetime as dt
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..db import get_session
from ..models.assistant import AssistantPermission, AssistantPolicy, AssistantTask
from ..models.user import User
from ..services.assistant_inbox import approve_assistant_task, reject_assistant_task
from ..services.assistant_policy import upsert_policy
from ..services import assistant_sessions as sessions
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
        raise Error(404, "err.not_found", "Not found")
    return t


@router.get("/assistant/inbox")
async def list_inbox(status_filter: str | None = None, archive: bool = False,
                     user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)):
    """Incoming items, newest first.

    Without `kind` the chat used to stand in here as well: 116 of 518 entries were messages
    somebody had typed into the chat window, in a list that says "incoming". The chat has
    its own view, so it does not belong in this one.
    """
    q = (select(AssistantTask)
         .where(AssistantTask.kind != "chat")
         .order_by(AssistantTask.id.desc()))
    q = q.where(AssistantTask.archived_at.isnot(None) if archive
                else AssistantTask.archived_at.is_(None))
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
        raise Error(409, "err.item_cannot_approved_status",
                     "The item cannot be approved (status {status})", status=t.status)
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


@router.get("/assistant/stats")
async def stats(days: int = 30, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """As what mail was classified, plus how well the local model judged.

    Counted at query time out of the rows that exist anyway (the house pattern, see
    `api/dashboard.py`). Nothing is kept twice, and the answer covers the whole stock
    instead of starting at zero with a counter.
    """
    from ..services.spam_report import balance, classifications

    data = await classifications(db, user.id, days=days)
    data["operation"] = await balance(db, user.id)
    return data


# ================= Learned rules (AssistantPolicy) =================

def _pol_out(p: AssistantPolicy) -> dict:
    return {
        "id": p.id, "match_kind": p.match_kind, "match_value": p.match_value,
        "auto_approve": p.auto_approve, "blocked": p.blocked, "redaction": p.redaction,
        "action_hint": p.action_hint, "enabled": p.enabled,
        "origin": p.origin, "origin_task_id": p.origin_task_id,
        "hit_count": p.hit_count, "last_used_at": p.last_used_at, "created_at": p.created_at,
    }


class PolicyIn(BaseModel):
    match_kind: str = "sender"       # sender | domain | category
    match_value: str
    auto_approve: bool = True
    blocked: bool = False            # never by itself; beats every allow
    redaction: str = "redacted"      # redacted | unredacted
    action_hint: str = ""
    enabled: bool = True


async def _pol_owned(pid: int, user: User, db: AsyncSession) -> AssistantPolicy:
    p = await db.get(AssistantPolicy, pid)
    if p is None or not is_owner_or_admin(p.owner_user_id, user):
        raise Error(404, "err.rule_not_found", "Rule not found")
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
                            action_hint=data.action_hint, blocked=data.blocked)
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
    p.blocked = data.blocked
    # A blocked rule cannot approve at the same time. Two fields, one truth: whoever sets the
    # block wins, so that no row can say yes and no about the same sender.
    p.auto_approve = data.auto_approve and not data.blocked
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
        raise Error(404, "err.not_found", "Not found")
    await db.delete(p)
    await db.commit()


# ================= Conversations (sessions) =================
#
# Everything under `/assistant/*`, so the `assistant` scope reaches it without a new scope
# having to be invented (see `core/scopes.py`). Deleting is deliberately NOT in here: it is a
# workflow action (`assistant_session`, op `delete`), so that clearing out old conversations
# can be scheduled as a job instead of hanging off a button nobody presses twice.


class SessionIn(BaseModel):
    title: str | None = None
    agent: str | None = None


class SessionPatch(BaseModel):
    title: str


async def _get_session_owned(sid: int, user: User, db: AsyncSession):
    s = await sessions.get_owned(db, sid, user.id)
    if s is None:
        raise Error(404, "err.not_found", "Not found")
    return s


@router.get("/assistant/sessions")
async def list_sessions(closed: bool = False, agent: str = "",
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """The conversations a switcher is drawn from, newest activity first.

    `running` says whether something is still going on in one. A switcher has to show that:
    otherwise a person switches away from the conversation whose answer they are waiting for
    and cannot see where it went.
    """
    rows = await sessions.listing(db, user.id, closed=closed, agent=agent.strip())
    return await sessions.out_many(db, rows)


@router.post("/assistant/sessions", status_code=201)
async def create_session(data: SessionIn, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    s = await sessions.create(db, user.id, title=(data.title or ""),
                              agent=(data.agent or "assistent"))
    return sessions.out(s)


@router.patch("/assistant/sessions/{sid}")
async def rename_session(sid: int, data: SessionPatch, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    s = await _get_session_owned(sid, user, db)
    title = (data.title or "").strip()
    if not title:
        raise Error(400, "err.empty_title", "Empty title")
    s.title = title[:200]
    await db.commit()
    return sessions.out(s)


@router.post("/assistant/sessions/{sid}/close")
async def close_session(sid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Out of the default list, not out of the world: it stays loadable and continuable."""
    s = await _get_session_owned(sid, user, db)
    await sessions.close(db, s)
    return sessions.out(s)


@router.post("/assistant/sessions/{sid}/reopen")
async def reopen_session(sid: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    s = await _get_session_owned(sid, user, db)
    await sessions.reopen(db, s)
    return sessions.out(s)


# ================= Chat with the assistant (web, in parallel to Telegram) =================

def _chat_out(t: AssistantTask) -> dict:
    return {
        "id": t.id, "text": (t.meta or {}).get("chat_text") or t.title,
        # Which conversation the message belongs to. A client that draws a switcher has to be
        # able to tell whether a message that came in belongs to the one it is showing.
        "session_id": t.session_id,
        "status": t.status, "result": t.result, "error": t.error,
        # Which run belongs to this message. Without it a client that wants to follow the
        # live events of its own chat message has to guess.
        "run_id": t.run_id,
        "pending_tool": t.pending_tool, "created_at": t.created_at, "finished_at": t.finished_at,
    }


class ChatIn(BaseModel):
    text: str
    # Optional so that a client which knows nothing of sessions can still send: without it the
    # channel pointer decides, and a conversation comes into being when there is none.
    session_id: int | None = None


class DecideIn(BaseModel):
    decision: str   # once | always | never


@router.get("/assistant/chat")
async def chat_history(before: int | None = None, limit: int = 20, archive: bool = False,
                       session_id: int | None = None,
                       user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """A page of ONE conversation, newest last.

    Formerly the last fifty came at once, and the browser scrolled through all of them down
    to the current one on every opening. Now the newest `limit` come, and whoever wants to
    read further back fetches the page before them (`before` = the id one is standing at).

    `more` says whether anything lies before that page, which the browser cannot tell from a
    full page alone.

    WITHOUT `session_id` everything of the owner comes back, exactly as before. That is not
    tidiness but compatibility: a client that predates the sessions must not fall silent
    because a parameter it does not know now exists.
    """
    n = max(1, min(limit, 100))
    q = (select(AssistantTask)
         .where(AssistantTask.owner_user_id == user.id, AssistantTask.kind == "chat")
         .order_by(AssistantTask.id.desc()))
    if session_id:
        q = q.where(AssistantTask.session_id == session_id)
    q = q.where(AssistantTask.archived_at.isnot(None) if archive
                else AssistantTask.archived_at.is_(None))
    if before:
        q = q.where(AssistantTask.id < before)
    # One row more than asked for: that is the answer to "is there anything older".
    rows = (await db.execute(q.limit(n + 1))).scalars().all()
    more = len(rows) > n
    page = rows[:n]
    return {"messages": [_chat_out(t) for t in reversed(page)], "more": more}


# Running messages stay: archiving something that is still being worked on would hide the
# answer one is waiting for.
_RUNNING = ("new", "approved", "running", "awaiting")


@router.post("/assistant/chat/archive-all")
async def chat_archive_all(user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_session)):
    """Clear the conversation: everything finished goes into the archive."""
    n = (await db.execute(
        update(AssistantTask)
        .where(AssistantTask.owner_user_id == user.id, AssistantTask.kind == "chat",
               AssistantTask.archived_at.is_(None),
               AssistantTask.status.not_in(_RUNNING))
        .values(archived_at=dt.datetime.now(tz=dt.timezone.utc)))).rowcount
    await db.commit()
    return {"archived": n or 0}


@router.post("/assistant/chat/{tid}/archive")
async def chat_archive(tid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    t = await _get_owned(tid, user, db)
    if t.status in _RUNNING:
        raise Error(409, "err.still_running", "The message is still being worked on")
    t.archived_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    return _chat_out(t)


@router.post("/assistant/chat/{tid}/unarchive")
async def chat_unarchive(tid: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    t = await _get_owned(tid, user, db)
    t.archived_at = None
    await db.commit()
    return _chat_out(t)


@router.post("/assistant/chat")
async def chat_send(data: ChatIn, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """Send a message into a conversation.

    Named explicitly it goes there — into a closed one as well, because whoever loaded it
    again wants to carry on in it. Without a name the `web` pointer decides, and when that
    points at nothing a conversation comes into being. Sending always writes the pointer, so
    that a person who was last in a session in the browser finds the same one after a reload.
    """
    text = (data.text or "").strip()
    if not text:
        raise Error(400, "err.empty_message", "Empty message")
    if data.session_id and await sessions.get_owned(db, data.session_id, user.id) is None:
        raise Error(404, "err.not_found", "Not found")
    s = await sessions.for_message(db, user.id, "web", text, session_id=data.session_id)
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="approved",
                      title=text[:200], meta={"chat_text": text}, session_id=s.id)
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
        raise Error(409, "err.no_open_approval", "No open approval")
    if data.decision not in ("once", "always", "never"):
        raise Error(400, "err.invalid_decision", "Invalid decision")
    from ..worker.assistant_gate import apply_perm_decision
    await apply_perm_decision(db, t, data.decision)
    await db.refresh(t)
    return _chat_out(t)
