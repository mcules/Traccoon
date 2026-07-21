"""Berechtigungs-Gate (Port aus nexus/perms.py) + DB-Grant/Request-Helfer.

Regeln kommen aus der Permission-Tabelle (als Liste [{tool,resource,action}]).
Präzedenz deny > allow > ask; Default ask für mutierende Tools.
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.nexus import PermGrant, PermRequest

_MUTATING = (
    "write", "edit", "delete", "create", "update", "patch", "append",
    "replace", "remove", "move", "post", "put", "set_", "exec", "commit",
    "push", "send", "upload", "rename", "manage", "deploy", "build", "shell",
    "save", "mark",
)

_RESOURCE_KEYS = ("path", "file_path", "file", "filename", "target_path",
                  "dest", "destination", "url", "note", "id")


def is_gated(tool: str) -> bool:
    t = (tool or "").lower()
    return any(m in t for m in _MUTATING)


def resource_of(tool: str, args: dict[str, Any] | None) -> str:
    args = args or {}
    tgt = args.get("target")
    if isinstance(tgt, dict) and isinstance(tgt.get("path"), str):
        return tgt["path"]
    for k in _RESOURCE_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _match_pat(pattern: str, value: str) -> bool:
    pattern = (pattern or "").strip()
    if pattern in ("", "*"):
        return True
    value = value or ""
    return fnmatch(value, pattern) or value.endswith(pattern)


def _rule_matches(rule: dict[str, Any], tool: str, resource: str) -> bool:
    return (_match_pat(str(rule.get("tool", "*")), tool)
            and _match_pat(str(rule.get("resource", "*")), resource))


def evaluate(rules: list[dict[str, Any]], tool: str, resource: str) -> str:
    if not is_gated(tool):
        return "allow"
    actions = {str(r.get("action", "ask")).lower()
               for r in (rules or []) if isinstance(r, dict) and _rule_matches(r, tool, resource)}
    if "deny" in actions:
        return "deny"
    if "allow" in actions:
        return "allow"
    return "ask"


async def take_grant(db: AsyncSession, issue_id: int, tool: str, resource: str) -> bool:
    """Einmal-Freigabe konsumieren (atomar). True wenn eine passende Grant vorhanden war."""
    rows = (
        await db.execute(
            select(PermGrant).where(
                PermGrant.issue_id == issue_id, PermGrant.consumed.is_(False)
            ).with_for_update()
        )
    ).scalars().all()
    for g in rows:
        if _match_pat(g.tool, tool) and _match_pat(g.resource or "*", resource):
            g.consumed = True
            await db.commit()
            return True
    return False


async def create_perm_request(db: AsyncSession, issue_id: int, run_id: int | None,
                              tool: str, resource: str) -> PermRequest:
    existing = (
        await db.execute(
            select(PermRequest).where(
                PermRequest.issue_id == issue_id, PermRequest.tool == tool,
                PermRequest.resource == (resource or "*"), PermRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    pr = PermRequest(issue_id=issue_id, run_id=run_id, tool=tool, resource=resource or "*")
    db.add(pr)
    await db.commit()
    await db.refresh(pr)
    return pr
