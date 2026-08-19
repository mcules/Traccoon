"""Anthropic / Claude over an OAuth subscription (sk-ant-oat setup token).

Ported from the predecessor. The token comes exclusively over `auth_token` (secret vault);
there is no file AuthStore. The OAuth details are mandatory for the subscription token.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .base import ChatResponse, Provider, ProviderError, ToolCall

log = logging.getLogger("traccoon.providers.claude")


class _Abgeschnitten(ProviderError):
    """The answer ran into `max_tokens` (the thinking ate the budget). Internal, so that the
    rescue attempt can tell it apart from real provider errors. To the outside it stays an
    ordinary (retryable) ProviderError."""


IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_BETAS = "oauth-2025-04-20,claude-code-20250219"
_ANTHROPIC_VERSION = "2023-06-01"
# Web search: web_search_20250305 is the basic variant, where the server searches itself and
# returns `web_search_tool_result`. The newer type web_search_20260209 (dynamic filtering) on
# the other hand runs IN the code execution container (`await web_search(...)` in Python);
# with the OAuth subscription token the container reliably answers `too_many_requests`, so
# there is not a single search result and the turns fizzle out (job 3, "AI and tech news",
# died of that several times with loop_exhausted). Hence the basic variant by default;
# whoever wants to test the new type sets ANTHROPIC_WEB_SEARCH_TYPE.
_WS_DEFAULT_TYPE = "web_search_20250305"
_WS_MAX_USES = int(os.getenv("ANTHROPIC_WEB_SEARCH_MAX_USES", "8"))


def _web_search_tool(model: str) -> dict:
    ws_type = os.getenv("ANTHROPIC_WEB_SEARCH_TYPE", "") or _WS_DEFAULT_TYPE
    return {"type": ws_type, "name": "web_search", "max_uses": _WS_MAX_USES}


def _wire(name: str) -> str:
    if name.startswith("mcp__"):
        return name
    if name.startswith("mcp_"):
        return "mcp__" + name[4:]
    return "mcp__" + name


def _unwire(name: str) -> str:
    return name[5:] if name.startswith("mcp__") else name


def _clean_schema(node: Any) -> Any:
    """Remove invalid `enum` (null or empty), for instance from `enum: targets or None`."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "enum" and not (isinstance(v, list) and v):
                continue
            out[k] = _clean_schema(v)
        return out
    if isinstance(node, list):
        return [_clean_schema(x) for x in node]
    return node


def _translate(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None):
    system_blocks: list[dict[str, Any]] = [{"type": "text", "text": IDENTITY}]
    a_msgs: list[dict[str, Any]] = []
    tool_buf: list[dict[str, Any]] = []

    def flush() -> None:
        if tool_buf:
            a_msgs.append({"role": "user", "content": list(tool_buf)})
            tool_buf.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            tool_buf.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": m.get("content") or "(no output)",
            })
            continue
        flush()
        if role == "system":
            txt = m.get("content") or ""
            if txt:
                system_blocks.append({"type": "text", "text": txt})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tc.get("id"),
                               "name": _wire(fn.get("name", "")), "input": inp})
            a_msgs.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(empty)"}]})
        else:  # user (content can be a string or a multimodal list of blocks)
            a_msgs.append({"role": "user", "content": m.get("content") or ""})
    flush()

    a_tools = None
    if tools:
        a_tools = [{
            "name": _wire(t["function"]["name"]),
            "description": t["function"].get("description", ""),
            "input_schema": _clean_schema(t["function"].get("parameters") or {"type": "object", "properties": {}}),
        } for t in tools]

    if a_tools:
        a_tools[-1]["cache_control"] = {"type": "ephemeral"}
    system_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    # Cache the (growing) message history as well: THE lever against the quadratic
    # consumption. A breakpoint on the last block of the last message lets every following
    # iteration read the complete prefix (system plus tools plus previous turns) as a cache
    # hit (~0.1x) instead of paying for it in full. String content is turned into a text
    # block (cache_control only works on structured blocks).
    if a_msgs:
        _last = a_msgs[-1]
        _c = _last.get("content")
        if isinstance(_c, list) and _c:
            _c[-1]["cache_control"] = {"type": "ephemeral"}
        elif isinstance(_c, str):
            _last["content"] = [{"type": "text", "text": _c or "(leer)",
                                 "cache_control": {"type": "ephemeral"}}]
    return system_blocks, a_msgs, a_tools


class AnthropicProvider(Provider):
    name = "claude"

    # 300 s as with codex and openai. The earlier 180 s were the outlier and tore runs off as
    # soon as ONE model turn took longer, which is reachable with a large state and a turn
    # that formulates 40 build assignments. The run then died with "connection error", which
    # points at a network problem instead of at the time limit.
    def __init__(self, model: str = "", claude_code_version: str = "2.1.74",
                 timeout: float = float(os.getenv("ANTHROPIC_TIMEOUT_SEC", "300"))):
        self.model = model
        self._ver = claude_code_version
        self._timeout = timeout

    def _headers(self, token: str | None) -> dict[str, str]:
        if not token:
            raise ProviderError("claude: no setup token (the secret vault is empty). "
                                "Store the token under Settings -> secrets.")
        return {
            "Authorization": f"Bearer {token}",
            "anthropic-version": _ANTHROPIC_VERSION,
            "anthropic-beta": _BETAS,
            "user-agent": f"claude-cli/{self._ver} (external, cli)",
            "x-app": "cli",
            "content-type": "application/json",
        }

    async def chat(self, *, model: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   web_search: bool = False, auth_token: str | None = None,
                   effort: str = "") -> ChatResponse:
        system_blocks, a_msgs, a_tools = _translate(messages, tools)
        body: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": a_msgs,
        }
        # Thinking depth. Without this field sonnet-5/opus-5 thinks with the default level
        # `high`, and the thinking shares `max_tokens` with the visible answer. On exactly
        # that, runs 744 (reviewer) and 752 (developer) died: budget used up in thinking, the
        # answer truncated. A lower level on the agent is the clean lever.
        if effort:
            body["output_config"] = {"effort": effort}
        all_tools = list(a_tools or [])
        if web_search:
            all_tools.append(_web_search_tool(model or self.model))
        if all_tools:
            body["tools"] = all_tools
            body["tool_choice"] = {"type": "auto"}

        data = await self._post(body, auth_token)
        try:
            return self._parse(data)
        except _Abgeschnitten:
            # Rescue attempt: the same budget but without thinking, so that it goes fully
            # into the visible answer. Better an answer without a thinking step than a run
            # that dies of a format error after 41 iterations.
            zweiter = dict(body)                  # a body of its own: the first stays unchanged
            zweiter["thinking"] = {"type": "disabled"}
            if effort in ("xhigh", "max"):
                zweiter.pop("output_config", None)   # "disabled" is a 400 above `high`
            log.warning("claude: the answer was cut off at max_tokens (%d), second attempt without thinking",
                        max_tokens)
            return self._parse(await self._post(zweiter, auth_token), gerettet=True)

    async def _post(self, body: dict[str, Any], auth_token: str | None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                                         headers=self._headers(auth_token), json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"claude: Verbindungsfehler: {exc}", retryable=True) from exc

        if resp.status_code != 200:
            retry_after = None
            hv = resp.headers.get("retry-after")
            if hv:
                try:
                    retry_after = float(hv)
                except ValueError:
                    retry_after = None
            retryable = resp.status_code in (429, 500, 502, 503, 504, 529)
            raise ProviderError(f"claude: HTTP {resp.status_code}: {resp.text[:400]}",
                                status=resp.status_code, retryable=retryable, retry_after=retry_after)
        return resp.json()

    def _parse(self, data: dict[str, Any], *, gerettet: bool = False) -> ChatResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        oai_calls: list[dict[str, Any]] = []
        blocks = data.get("content", [])
        last_server = max((i for i, b in enumerate(blocks)
                           if b.get("type") in ("server_tool_use", "web_search_tool_result")), default=-1)
        for i, block in enumerate(blocks):
            if block.get("type") == "text":
                if last_server >= 0 and i < last_server:
                    continue
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                orig = _unwire(block.get("name", ""))
                inp = block.get("input") or {}
                calls.append(ToolCall(id=block.get("id", ""), name=orig, arguments=inp))
                oai_calls.append({"id": block.get("id", ""), "type": "function",
                                  "function": {"name": orig, "arguments": json.dumps(inp, ensure_ascii=False)}})
        text = "".join(text_parts)
        # max_tokens-Abbruch: entweder mitten im Tool-Argument (calls vorhanden, JSON
        # incomplete) OR the budget was used up by the (server side) thinking before any text
        # or tool use came at all, so an empty payload. Neither case is a valid "empty
        # answer" but a truncated one, so report it clearly (retryable) instead of passing it
        # through silently as idling (which would misdiagnose as "empty model answer").
        if data.get("stop_reason") == "max_tokens" and (calls or not text.strip()):
            raise _Abgeschnitten(
                "claude: the answer was cut off at max_tokens and is incomplete "
                "(tool arguments or completely empty, the budget went into thinking)"
                + (" Even without thinking. Raise max_tokens or cut the task smaller."
                   if gerettet else "."), retryable=True)
        raw_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if oai_calls:
            raw_msg["tool_calls"] = oai_calls
        usage = data.get("usage") or {}
        # Prompt-Caching-Anteile: cache_read = gecachter Prefix (~0,1x berechnet),
        # cache_creation = the share newly written into the cache (informative). Make both
        # explicitly graspable so that the runtime can take cache_read into the cost and the
        # CostEntry.
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        return ChatResponse(text=text, tool_calls=calls,
                            raw={"choices": [{"message": raw_msg}]}, usage=usage,
                            cache_read_tokens=cache_read)
