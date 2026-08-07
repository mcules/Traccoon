"""Anthropic / Claude über OAuth-Subscription (sk-ant-oat setup-token).

Port aus dem Vorläufer. Token kommt ausschließlich über `auth_token` (Secret-Tresor);
kein Datei-AuthStore. Die OAuth-Details sind zwingend für den Subscription-Token.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base import ChatResponse, Provider, ProviderError, ToolCall

IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_BETAS = "oauth-2025-04-20,claude-code-20250219"
_ANTHROPIC_VERSION = "2023-06-01"
# Web-Suche: neuere Modelle nutzen web_search_20260209 (dynamic filtering); ältere die
# Basis-Variante web_search_20250305 (der neue Typ gäbe dort 400). Model-abhängig wählen.
_WS_NEW_MODELS = ("opus-4-8", "opus-4-7", "opus-4-6", "sonnet-5", "sonnet-4-6", "fable-5", "mythos-5")


def _web_search_tool(model: str) -> dict:
    ws_type = "web_search_20260209" if any(k in (model or "") for k in _WS_NEW_MODELS) else "web_search_20250305"
    return {"type": ws_type, "name": "web_search", "max_uses": 8}


def _wire(name: str) -> str:
    if name.startswith("mcp__"):
        return name
    if name.startswith("mcp_"):
        return "mcp__" + name[4:]
    return "mcp__" + name


def _unwire(name: str) -> str:
    return name[5:] if name.startswith("mcp__") else name


def _clean_schema(node: Any) -> Any:
    """Ungültige `enum` (null/leer) entfernen — z.B. aus `enum: targets or None`."""
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
        else:  # user (content kann String oder multimodale Block-Liste sein)
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
    # Auch die (wachsende) Message-History cachen — DER Hebel gegen den quadratischen
    # Verbrauch: Breakpoint auf den letzten Block der letzten Nachricht, damit jede
    # Folge-Iteration den kompletten Prefix (system + tools + bisherige Turns) als
    # Cache-Hit (~0,1x) liest statt ihn voll zu bezahlen. String-Content wird in einen
    # Text-Block gewandelt (cache_control geht nur auf strukturierte Blöcke).
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

    # 300 s wie bei codex und openai. Die früheren 180 s waren der Ausreißer und rissen
    # Läufe ab, sobald EIN Modellzug länger brauchte — bei einem großen Zustand und einem
    # Zug, der 40 Bauaufträge ausformuliert, ist das erreichbar. Der Lauf starb dann mit
    # „Verbindungsfehler", was auf ein Netzproblem zeigt statt auf das Zeitlimit.
    def __init__(self, model: str = "", claude_code_version: str = "2.1.74",
                 timeout: float = float(os.getenv("ANTHROPIC_TIMEOUT_SEC", "300"))):
        self.model = model
        self._ver = claude_code_version
        self._timeout = timeout

    def _headers(self, token: str | None) -> dict[str, str]:
        if not token:
            raise ProviderError("claude: kein Setup-Token (Secret-Tresor leer). "
                                "Token unter Einstellungen → Secrets hinterlegen.")
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
                   web_search: bool = False, auth_token: str | None = None) -> ChatResponse:
        system_blocks, a_msgs, a_tools = _translate(messages, tools)
        body: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": a_msgs,
        }
        all_tools = list(a_tools or [])
        if web_search:
            all_tools.append(_web_search_tool(model or self.model))
        if all_tools:
            body["tools"] = all_tools
            body["tool_choice"] = {"type": "auto"}

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
        return self._parse(resp.json())

    def _parse(self, data: dict[str, Any]) -> ChatResponse:
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
        # unvollständig) ODER das Budget wurde vom (server-seitigen) Thinking aufgebraucht,
        # bevor überhaupt Text/Tool-Use kam → leere Nutzlast. Beide Fälle sind KEINE gültige
        # „leere Antwort", sondern eine abgeschnittene → klar (retrybar) melden statt still
        # als Leerlauf durchzureichen (sonst Fehldiagnose „Leere Modell-Antwort").
        if data.get("stop_reason") == "max_tokens" and (calls or not text.strip()):
            raise ProviderError(
                "claude: Antwort bei max_tokens abgeschnitten – unvollständig "
                "(Tool-Argumente oder komplett leer, Budget im Thinking verbraucht). "
                "max_tokens erhöhen.", retryable=True, escalate_max_tokens=True)
        raw_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if oai_calls:
            raw_msg["tool_calls"] = oai_calls
        usage = data.get("usage") or {}
        # Prompt-Caching-Anteile: cache_read = gecachter Prefix (~0,1x berechnet),
        # cache_creation = neu in den Cache geschriebener Anteil (informativ). Beide
        # explizit greifbar machen, damit die Runtime cache_read in Kosten + CostEntry
        # aufnehmen kann.
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        return ChatResponse(text=text, tool_calls=calls,
                            raw={"choices": [{"message": raw_msg}]}, usage=usage,
                            cache_read_tokens=cache_read)
