"""Self-Service-Provisionierung der MCP-Reichweite eines Users über die MCPJungle-Admin-API.

Legt/aktualisiert die Tool-Gruppe `traccoon-<uid>` (included_servers) + einen gescopeten
mcp-client-Token (allow_list — NUR dieses Feld scopet!) und schreibt mcp_group/mcp_servers/
mcp_token_enc. Ersetzt das Host-Skript provision_mcp.py für den UI-Weg. Backend muss am
mcp-backends-Netz hängen; Admin-Token aus MCPJUNGLE_ADMIN_TOKEN.
"""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.security import encrypt_secret
from ..models.user import User


class McpProvisionError(RuntimeError):
    pass


def _base() -> str:
    return (settings.mcpjungle_base or "http://mcpjungle:8080").rstrip("/")


def _headers() -> dict[str, str]:
    if not settings.mcpjungle_admin_token:
        raise McpProvisionError("MCPJUNGLE_ADMIN_TOKEN nicht gesetzt — Provisionierung nicht möglich.")
    return {"Authorization": f"Bearer {settings.mcpjungle_admin_token}"}


async def list_available_servers() -> list[str]:
    """Alle in MCPJungle registrierten Server (Namen), aus denen der User wählen kann."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{_base()}/api/v0/servers", headers=_headers())
    r.raise_for_status()
    return sorted(s.get("name") for s in (r.json() or []) if s.get("name"))


async def provision_user_mcp(db: AsyncSession, user: User, servers: list[str]) -> None:
    """Gruppe + gescopeten Token (neu) anlegen und beim User speichern. Nur real existierende
    Server werden übernommen. Leere Liste → Reichweite entziehen (Gruppe/Token gelöscht)."""
    group = f"traccoon-{user.id}"
    available = set(await list_available_servers())
    chosen = [s for s in dict.fromkeys(servers) if s in available]  # dedupe + nur echte

    hdr = _headers()
    async with httpx.AsyncClient(timeout=15) as c:
        # Alten Stand wegräumen (Rotation): erst Client, dann Gruppe.
        await c.delete(f"{_base()}/api/v0/clients/{group}", headers=hdr)
        await c.delete(f"{_base()}/api/v0/tool-groups/{group}", headers=hdr)

        if not chosen:
            user.mcp_group = ""
            user.mcp_servers = []
            user.mcp_token_enc = ""
            await db.commit()
            return

        rg = await c.post(f"{_base()}/api/v0/tool-groups", headers=hdr, json={
            "name": group, "description": f"Traccoon-User {user.username}", "included_servers": chosen})
        if rg.status_code >= 400:
            raise McpProvisionError(f"Gruppe anlegen fehlgeschlagen: {rg.status_code} {rg.text[:200]}")

        # allow_list ist das EINZIGE Feld, das den Token scopet (verifiziert).
        rc = await c.post(f"{_base()}/api/v0/clients", headers=hdr, json={
            "name": group, "description": f"Traccoon-User {user.username}", "allow_list": chosen})
        if rc.status_code >= 400:
            raise McpProvisionError(f"Client anlegen fehlgeschlagen: {rc.status_code} {rc.text[:200]}")
        token = (rc.json() or {}).get("access_token")
        if not token:
            raise McpProvisionError("Kein Access-Token von MCPJungle erhalten.")

    user.mcp_group = group
    user.mcp_servers = chosen
    user.mcp_token_enc = encrypt_secret(token)
    await db.commit()
