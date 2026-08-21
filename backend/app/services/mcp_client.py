"""Minimal MCP client (streamable HTTP) for calls out of the backend.

So far only the *agent* talked to MCP servers, over the CLI, which builds its own
connection. For the spam detection that is not enough: moving a confirmed mail is a
mechanical hand movement, not an assignment. Starting a Claude run for it would be
expensive, slow and unreliable; the bot needs a direct line to `imap-mcp`.

Hence the bare minimum of the protocol here: `initialize`, remember the session id,
`tools/call`. No tool directory, no resources, no resumption; what is missing is missing on purpose.

The servers in the `mcp-backends` network are internally unauthenticated (the rights check
sits in MCPJungle in front of them). This path deliberately bypasses MCPJungle: it carries no
user identity but executes a decision the human has just taken themselves.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger("traccoon.mcp")

_PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT = 30.0


class McpError(RuntimeError):
    """The server rejected the call or was unreachable."""


def _unpack(resp: httpx.Response) -> dict[str, Any]:
    """Response to a JSON-RPC object. Streamable HTTP may answer with `application/json` OR as
    an event stream; both occur, depending on the server and the call."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw:
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        raise McpError("Event stream without a usable answer")
    try:
        return resp.json()
    except ValueError as exc:
        raise McpError(f"unlesbare Antwort: {resp.text[:200]}") from exc


async def call_tool(url: str, tool: str, arguments: dict[str, Any], *,
                    headers: dict[str, str] | None = None,
                    timeout: float = _TIMEOUT) -> dict[str, Any]:
    """Call a tool on an MCP server. Returns the result content of the server.

    Raises `McpError` when the server reports an error, including `isError` in the result,
    because a "could not move" is the same for the caller as a transport error: the mail is
    still where it was.
    """
    basis = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            init = await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION,
                           "capabilities": {},
                           "clientInfo": {"name": "traccoon", "version": "1"}}})
            init.raise_for_status()
        except httpx.HTTPError as exc:
            raise McpError(f"{url} not reachable: {exc}") from exc
        _unpack(init)
        # The server assigns the session only here; all following calls have to carry it.
        session = init.headers.get("mcp-session-id")
        if session:
            basis["Mcp-Session-Id"] = session

        # Without this notification, strict servers reject every further call.
        try:
            await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "method": "notifications/initialized"})
        except httpx.HTTPError as exc:      # not fatal: some servers do not need it
            log.debug("notifications/initialized failed: %s", exc)

        try:
            resp = await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise McpError(f"{tool} failed: {exc}") from exc

    data = _unpack(resp)
    if "error" in data:
        error = data["error"]
        raise McpError(f"{tool}: {error.get('message') or error}")
    result = data.get("result") or {}
    if result.get("isError"):
        raise McpError(f"{tool}: {_text(result)}")
    return result


def _text(result: dict[str, Any]) -> str:
    """Pull the text parts of an MCP result together (for messages and the log)."""
    parts = [c.get("text", "") for c in (result.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return " ".join(t for t in parts if t).strip()


def result_text(result: dict[str, Any]) -> str:
    return _text(result)


def result_json(result: dict[str, Any]) -> dict | None:
    """Structured result of a tool (or None).

    Tools deliver their result twice: as `structuredContent` and as JSON text. Whoever wants
    to compute with it instead of only reporting it takes this path; taking the text apart by
    hand again would be the same work in a second place.
    """
    content = result.get("structuredContent")
    if isinstance(content, dict):
        return content
    text = _text(result)
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None
