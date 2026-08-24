"""Minimal MCP gateway client (streamable-http). Optional: when MCP_GATEWAY_URL is not set,
the session delivers no tools (the agent then uses only built-in tools)."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "")
MCP_GATEWAY_TOKEN = os.getenv("MCP_GATEWAY_TOKEN", "")


@dataclass
class McpTool:
    name: str
    description: str
    schema: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.schema or {"type": "object", "properties": {}}}}


class McpSession:
    def __init__(self, base: str, token: str):
        self._base = base.rstrip("/")
        self._token = token
        self._client: httpx.AsyncClient | None = None
        self._extra_headers: dict[str, str] = {}
        self._session_id: str | None = None   # streamable HTTP: Mcp-Session-Id from initialize
        self._id = 0

    def _headers(self) -> dict[str, str]:
        # Streamable-HTTP-Server (z. B. MCPJungle) verlangen text/event-stream im Accept
        # and the session id from the initialize answer on all following requests.
        h = {"content-type": "application/json",
             "accept": "application/json, text/event-stream", **self._extra_headers}
        if self._token:
            h["authorization"] = f"Bearer {self._token}"
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    @staticmethod
    def _parse_body(r: httpx.Response) -> dict:
        # The answer is JSON or SSE (data: lines); tolerate both.
        ct = r.headers.get("content-type", "")
        if "text/event-stream" in ct:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            return {}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    async def _notify(self, method: str) -> None:
        assert self._client is not None
        try:
            await self._client.post(self._base, headers=self._headers(),
                                    json={"jsonrpc": "2.0", "method": method})
        except Exception:  # noqa: BLE001
            pass

    async def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        assert self._client is not None
        r = await self._client.post(self._base, headers=self._headers(),
                                    json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        # Remember the session id from the initialize answer (for all following requests).
        if method == "initialize":
            sid = r.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
        r.raise_for_status()
        return self._parse_body(r).get("result", {})

    async def list_tools(self) -> list[McpTool]:
        if not self._base:
            return []
        try:
            res = await self._rpc("tools/list", {})
        except Exception:  # noqa: BLE001
            return []
        return [McpTool(t["name"], t.get("description", ""), t.get("inputSchema") or {})
                for t in res.get("tools", [])]

    async def call(self, name: str, arguments: dict) -> str:
        text, _ = await self.call_ex(name, arguments)
        return text

    async def call_ex(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Like `call`, but says whether the server flagged the result as an error.

        `call` throws the `isError` flag away, so its callers can only guess from the text
        whether something went wrong — and guessing by substring reads an error out of a note
        that merely QUOTES one ("Section target not found"). Whoever needs the difference
        (tools_memory) asks here.
        """
        res = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
        return ("\n".join(parts) or "(kein Output)", bool(res.get("isError")))


class McpNotAvailable(RuntimeError):
    """The tool cannot be reached at all: no server carries its prefix, and no gateway.

    Deliberately an exception and not a sentence in the return value. This used to answer
    with "(no MCP configured — the tool … is not available)" as TEXT, and whoever got text
    booked the call as a success: on 2026-08-19 a flow wrote its note over a tool name
    without a server prefix, reported green and left the note empty. Whoever needs a sentence
    for a model builds it in the `except`, which every caller in the worker does anyway.
    """


class MultiMcpSession:
    """Bundles the global gateway plus project-owned MCP servers behind one session.

    Tools of the additional servers get a `<servername>__` prefix, which separates ones with
    the same name and says where the call belongs on invocation.
    """

    def __init__(self) -> None:
        self._gateway: McpSession | None = None
        self._servers: dict[str, McpSession] = {}

    async def list_tools(self) -> list[McpTool]:
        tools: list[McpTool] = []
        if self._gateway is not None:
            tools += await self._gateway.list_tools()
        for name, sess in self._servers.items():
            for t in await sess.list_tools():
                tools.append(McpTool(f"{name}__{t.name}", t.description, t.schema))
        return tools

    async def call(self, name: str, arguments: dict) -> str:
        text, _ = await self.call_ex(name, arguments)
        return text

    async def call_ex(self, name: str, arguments: dict) -> tuple[str, bool]:
        """`call` plus the server's `isError` flag; see `McpSession.call_ex`."""
        for server, sess in self._servers.items():
            if name.startswith(f"{server}__"):
                return await sess.call_ex(name[len(server) + 2:], arguments)
        if self._gateway is None:
            raise McpNotAvailable(
                f"no MCP server for {name!r}: no server of that name is registered and "
                f"kein Gateway eingerichtet")
        return await self._gateway.call_ex(name, arguments)


@asynccontextmanager
async def mcp_session(agent_name: str = "", servers: list[dict] | None = None,
                      gateway_url: str | None = None, gateway_token: str | None = None):
    """servers: [{name, url, headers}], HTTP/SSE servers from the MCP registry.

    gateway_url/gateway_token: the owner's own MCPJungle group endpoint (hard
    per-user separation). Falls back to the global MCP_GATEWAY_URL/TOKEN when it is not set.
    stdio servers are not served here.
    """
    gw_url = gateway_url if gateway_url is not None else MCP_GATEWAY_URL
    gw_token = gateway_token if gateway_token is not None else MCP_GATEWAY_TOKEN
    multi = MultiMcpSession()
    async with httpx.AsyncClient(timeout=60) as client:
        if gw_url:
            g = McpSession(gw_url, gw_token or "")
            g._client = client
            await _init(g)
            multi._gateway = g
        for spec in servers or []:
            url = (spec.get("url") or "").strip()
            if not url:
                continue
            s = McpSession(url, "")
            s._client = client
            s._extra_headers = spec.get("headers") or {}
            await _init(s)
            multi._servers[spec["name"]] = s
        yield multi


async def _init(session: McpSession) -> None:
    try:
        await session._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "traccoon", "version": "0.1"}})
        # Streamable HTTP handshake: the initialized notification after initialize.
        await session._notify("notifications/initialized")
    except Exception:  # noqa: BLE001
        pass
