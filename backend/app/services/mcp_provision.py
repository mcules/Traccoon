"""Self-service provisioning of the MCP reach of a user over the MCPJungle admin API.

Creates and updates the tool group `traccoon-<uid>` (included_servers) plus a scoped
mcp-client token (allow_list is the ONLY field that scopes) and writes mcp_group,
mcp_servers and mcp_token_enc. Replaces the host script provision_mcp.py for the UI path.
The backend has to hang on the mcp-backends network; the admin token comes from MCPJUNGLE_ADMIN_TOKEN.
"""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from ..config import settings
from ..core.security import encrypt_secret
from ..models.plugins import McpServer
from ..models.user import User

# MCPJungle transport to McpServer transport (the worker serves http[streamable]/sse).
_TRANSPORT = {"streamable_http": "http", "http": "http", "sse": "sse"}


class McpProvisionError(RuntimeError):
    pass


def _base() -> str:
    return (settings.mcpjungle_base or "http://mcpjungle:8080").rstrip("/")


def _headers() -> dict[str, str]:
    if not settings.mcpjungle_admin_token:
        raise McpProvisionError("MCPJUNGLE_ADMIN_TOKEN is not set, provisioning is not possible.")
    return {"Authorization": f"Bearer {settings.mcpjungle_admin_token}"}


async def _jungle_servers() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{_base()}/api/v0/servers", headers=_headers())
    r.raise_for_status()
    return r.json() or []


async def import_registry_from_jungle(db: AsyncSession, user: User) -> dict:
    """Create the servers registered in MCPJungle as REAL McpServer registry entries of the
    user (name, transport, URL; editable like manually set up ones). The assistant then uses
    them directly. To avoid duplicate tools, the gateway group is switched off.
    `enabled` = whether the server is currently in the reach of the user (banking and the like stay off)."""
    active = set(user.mcp_servers or [])
    existing = {m.name: m for m in (await db.execute(select(McpServer).where(
        McpServer.user_id == user.id))).scalars().all()}
    created, updated = [], []
    for s in await _jungle_servers():
        name = s.get("name")
        if not name:
            continue
        transport = _TRANSPORT.get(s.get("transport", ""), "http")
        url = s.get("url") or ""
        enabled = (name in active) if active else True
        m = existing.get(name)
        if m is None:
            db.add(McpServer(user_id=user.id, name=name, display_name=name,
                             transport=transport, url=url, variables=[], enabled=enabled))
            created.append(name)
        else:
            m.transport, m.url = transport, url  # URL/Transport nachziehen, enabled belassen
            updated.append(name)
    await db.commit()
    # Switch the gateway group off (the registry replaces it) so that there are no duplicate tools.
    await provision_user_mcp(db, user, [])
    return {"created": created, "updated": updated}


async def list_available_servers() -> list[str]:
    """All servers registered in MCPJungle (names) the user can choose from."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{_base()}/api/v0/servers", headers=_headers())
    r.raise_for_status()
    return sorted(s.get("name") for s in (r.json() or []) if s.get("name"))


async def provision_user_mcp(db: AsyncSession, user: User, servers: list[str]) -> None:
    """Create the group plus a (new) scoped token and store it on the user. Only servers that
    really exist are taken over. An empty list withdraws the reach (group and token deleted)."""
    group = f"traccoon-{user.id}"
    available = set(await list_available_servers())
    chosen = [s for s in dict.fromkeys(servers) if s in available]  # dedupe plus real ones only

    hdr = _headers()
    async with httpx.AsyncClient(timeout=15) as c:
        # Clear the old state away (rotation): first the client, then the group.
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

        # allow_list is the ONLY field that scopes the token (verified).
        rc = await c.post(f"{_base()}/api/v0/clients", headers=hdr, json={
            "name": group, "description": f"Traccoon-User {user.username}", "allow_list": chosen})
        if rc.status_code >= 400:
            raise McpProvisionError(f"Client anlegen fehlgeschlagen: {rc.status_code} {rc.text[:200]}")
        token = (rc.json() or {}).get("access_token")
        if not token:
            raise McpProvisionError("No access token received from MCPJungle.")

    user.mcp_group = group
    user.mcp_servers = chosen
    user.mcp_token_enc = encrypt_secret(token)
    await db.commit()
