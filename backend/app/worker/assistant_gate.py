"""Tool gate of the personal assistant (project-less, owner-scoped).

External mutating actions (sending mail, writing files or notes, calendar …) need
an approval until the human says "always". That way the assistant learns what it may do. The
decision comes over Telegram (once|always|never); the card is produced by this module.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantPermission, AssistantTask
from ..models.notification import Notification
from ..models.user import User
from . import perms


async def _owner_rules(db: AsyncSession, owner_id: int | None) -> list[dict]:
    if not owner_id:
        return []
    rows = (await db.execute(select(AssistantPermission).where(
        AssistantPermission.owner_user_id == owner_id))).scalars().all()
    return [{"tool": r.tool, "resource": r.resource, "action": r.action} for r in rows]


async def gate_check(db: AsyncSession, task: AssistantTask, owner_id: int | None,
                     tool: str, resource: str) -> str:
    """Returns 'allow' | 'deny' | 'ask'. With 'ask' the item is set to awaiting and a Telegram
    approval card is produced (the run is blocked by the caller afterwards)."""
    # Consume a one-off approval from a previous round.
    if task.grant_tool and perms._match_pat(task.grant_tool, tool) \
            and perms._match_pat(task.grant_resource or "*", resource):
        task.grant_tool = None
        task.grant_resource = None
        await db.commit()
        return "allow"

    decision = perms.evaluate(await _owner_rules(db, owner_id), tool, resource)
    if decision != "ask":
        return decision

    # An approval is needed, so park the item and send the Telegram card (when set up).
    task.status = "awaiting"
    task.pending_tool = tool
    task.pending_resource = (resource or "*")[:500]
    owner = await db.get(User, owner_id) if owner_id else None
    if owner and owner.telegram_chat_id:
        res = f" auf `{resource}`" if resource else ""
        db.add(Notification(
            user_id=owner_id, assistant_task_id=task.id, kind="assistant_perm",
            chat_id=owner.telegram_chat_id, title="🔐 An approval is needed",
            body=f"The assistant wants to run `{tool}`{res}.\n({task.title})"[:1000]))
    await db.commit()
    return "ask"


async def apply_perm_decision(db: AsyncSession, task: AssistantTask, decision: str) -> None:
    """Apply the gate decision (shared by the Telegram bot and the web): 'once' = a one-off
    grant, 'always'/'never' = a tool wide rule; afterwards the run is set off again."""
    tool, res = task.pending_tool or "*", task.pending_resource or "*"
    if decision == "once":
        task.grant_tool, task.grant_resource = tool, res
    elif decision == "always":
        await learn_permission(db, task.owner_user_id, tool, "*", "allow")
    elif decision == "never":
        await learn_permission(db, task.owner_user_id, tool, "*", "deny")
    task.pending_tool = task.pending_resource = None
    task.status = "approved"
    await db.commit()
    from ..core.redis import enqueue_task
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task.id}",
                        "assistant_task_id": task.id})


async def learn_permission(db: AsyncSession, owner_id: int | None, tool: str,
                           resource: str, action: str) -> None:
    """'immer' (allow) / 'nie' (deny) dauerhaft merken (upsert je owner+tool+resource)."""
    resource = resource or "*"
    existing = (await db.execute(select(AssistantPermission).where(
        AssistantPermission.owner_user_id == owner_id, AssistantPermission.tool == tool,
        AssistantPermission.resource == resource))).scalar_one_or_none()
    if existing:
        existing.action = action
    else:
        db.add(AssistantPermission(owner_user_id=owner_id, tool=tool, resource=resource, action=action))
    await db.commit()
