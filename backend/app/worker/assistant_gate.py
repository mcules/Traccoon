"""Tool-Gate des persönlichen Assistenten (projektlos, owner-scoped).

Externe mutierende Aktionen (Mail senden, Dateien/Notizen schreiben, Kalender …) brauchen
eine Freigabe, bis der Mensch „immer" sagt. Der Assistent lernt so, was er darf. Entscheidung
kommt per Telegram (einmal|immer|nie); die Karte erzeugt dieses Modul.
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
    """→ 'allow' | 'deny' | 'ask'. Bei 'ask' wird das Item auf awaiting gesetzt und eine
    Telegram-Freigabekarte erzeugt (Run wird danach vom Aufrufer geblockt)."""
    # Einmal-Freigabe aus einer vorherigen Runde konsumieren.
    if task.grant_tool and perms._match_pat(task.grant_tool, tool) \
            and perms._match_pat(task.grant_resource or "*", resource):
        task.grant_tool = None
        task.grant_resource = None
        await db.commit()
        return "allow"

    decision = perms.evaluate(await _owner_rules(db, owner_id), tool, resource)
    if decision != "ask":
        return decision

    # Freigabe nötig → Item parken + Telegram-Karte (falls eingerichtet).
    task.status = "awaiting"
    task.pending_tool = tool
    task.pending_resource = (resource or "*")[:500]
    owner = await db.get(User, owner_id) if owner_id else None
    if owner and owner.telegram_chat_id:
        res = f" auf `{resource}`" if resource else ""
        db.add(Notification(
            user_id=owner_id, assistant_task_id=task.id, kind="assistant_perm",
            chat_id=owner.telegram_chat_id, title="🔐 Freigabe nötig",
            body=f"Der Assistent möchte `{tool}`{res} ausführen.\n({task.title})"[:1000]))
    await db.commit()
    return "ask"


async def apply_perm_decision(db: AsyncSession, task: AssistantTask, decision: str) -> None:
    """Gate-Entscheidung anwenden (geteilt von Telegram-Bot und Web): 'once' = Einmal-Grant,
    'always'/'never' = tool-weite Regel; danach den Lauf neu anstoßen."""
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
