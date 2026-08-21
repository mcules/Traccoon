"""MCP tools inside a flow.

Traccoon already runs a dozen MCP servers (mail, vault, documents, cloud storage, photos,
time tracking, home automation). Until now only agents could reach them: writing a note or
filing a document from a flow meant starting a language model run, which is expensive and
slow for something a tool does directly.

Permissions run through the owner of the run. Calls go through their own group endpoint,
not through a global account. Someone without access to a service does not gain it by
building a flow.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("workflow_tools")


async def _server_des_besitzers(db: AsyncSession, owner_id: int | None) -> list[dict]:
    """The MCP servers belonging to this person, plus the global ones.

    Exactly the same source the agent uses: the registry under Settings, MCP servers. That
    makes connecting a foreign system configuration instead of programming. Whoever
    registers a server has its tools available in the flow right away.

    Servers with a variable schema need a filled instance, which today hangs off the agent.
    Without an instance they stay out rather than failing with half-filled headers.
    """
    from sqlalchemy import or_, select

    from ..models.plugins import McpServer
    from ..worker.runtime import _server_spec

    q = select(McpServer).where(McpServer.enabled.is_(True))
    q = q.where(or_(McpServer.user_id.is_(None), McpServer.user_id == owner_id)
                if owner_id is not None else McpServer.user_id.is_(None))
    out = []
    for r in (await db.execute(q)).scalars().all():
        if r.variables:
            continue
        spec = _server_spec(r)
        if spec:
            out.append(spec)
    return out


async def _session(db: AsyncSession, owner_id: int | None):
    """Context manager for the owner's MCP session, or None when they have none.

    Two sources, both tied to the person behind the run: their registry servers and, when
    set up, their group endpoint.
    """
    from ..worker.mcp_client import mcp_session
    from ..worker.runtime import _owner_gateway

    url, token = await _owner_gateway(db, owner_id)
    server = await _server_des_besitzers(db, owner_id)
    if not url and not server:
        return None
    return mcp_session(gateway_url=url, gateway_token=token, servers=server)


async def tools(db: AsyncSession, owner_id: int | None) -> list[dict]:
    """Which tools this person has: name, description, required fields.

    Feeds the picker in the editor. When the gateway is down the list is empty instead of
    broken: a flow can still be built by typing the tool name.
    """
    session = await _session(db, owner_id)
    if session is None:
        return []
    try:
        async with session as mcp:
            roh = await mcp.list_tools()
    except Exception:  # noqa: BLE001, the tool list is convenience, not infrastructure
        log.warning("MCP tool list for user %s not fetchable", owner_id, exc_info=True)
        return []
    out = []
    for t in roh:
        schema = t.schema if isinstance(t.schema, dict) else {}
        fields = list((schema.get("properties") or {}).keys())
        out.append({
            "name": t.name,
            "server": t.name.split("__", 1)[0] if "__" in t.name else "",
            "beschreibung": (t.description or "").strip().split("\n")[0][:300],
            "felder": fields[:20],
            "pflicht": list(schema.get("required") or [])[:20],
        })
    return sorted(out, key=lambda w: w["name"])


async def call(db: AsyncSession, owner_id: int | None, name: str,
                   arguments: dict) -> dict:
    """Call a tool, returns {ok, text, json?, error?}.

    Errors are returned, not raised: the flow decides for itself whether a failed call ends
    it (`fail_on_error`) or whether it keeps going.
    """
    if not name:
        return {"ok": False, "text": "", "error": "kein Werkzeug angegeben"}
    session = await _session(db, owner_id)
    if session is None:
        return {"ok": False, "text": "",
                "error": "no MCP access for the owner of this flow"}
    # Unknown server in the name (`server__tool`) and no gateway to catch it: the session
    # then answers with a hint as TEXT instead of an error, and the flow would carry on as
    # if everything were fine. Better to look first.
    if "__" in name:
        server = name.split("__", 1)[0]
        from ..worker.runtime import _owner_gateway
        url, _ = await _owner_gateway(db, owner_id)
        if not url and server not in {
                s["name"] for s in await _server_des_besitzers(db, owner_id)}:
            return {"ok": False, "text": "",
                    "error": f"unknown MCP server {server!r}, register it in the settings "
                             f"or check the name"}
    try:
        async with session as mcp:
            text = await mcp.call(name, arguments or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("tool %s failed: %s", name, exc)
        return {"ok": False, "text": "", "error": str(exc)[:500]}

    text = text if isinstance(text, str) else str(text)
    result: dict = {"ok": True, "text": text[:20000]}
    # Anyone computing with the result needs it parsed, and most tools answer in JSON
    # anyway.
    try:
        daten = json.loads(text)
    except (ValueError, TypeError):
        daten = None
    if isinstance(daten, (dict, list)):
        result["json"] = daten
    # Tools often report their own failure inside the text. That is not a transport
    # error, but the flow should be able to branch on it.
    if isinstance(daten, dict) and daten.get("error"):
        result["ok"] = False
        result["error"] = str(daten["error"])[:500]
    return result
