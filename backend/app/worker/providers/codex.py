"""Codex / OpenAI over a ChatGPT subscription (the responses format).

Ported from the predecessor. The token (a JWT) comes over `auth_token` (secret vault). The
account id is read from the JWT claim. Image blocks become placeholders (Codex has no vision).
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from .base import ChatResponse, Provider, ProviderError, ToolCall


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def _account_id(token: str) -> str | None:
    claims = _decode_jwt_claims(token)
    auth = claims.get("https://api.openai.com/auth") or {}
    return auth.get("chatgpt_account_id") or claims.get("account_id")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text") or "")
            elif b.get("type") == "image":
                parts.append("[Screenshot/Bild – für dieses Modell nicht darstellbar]")
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _to_input_items(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            txt = _text_of(m.get("content"))
            if txt:
                instructions.append(txt)
        elif role == "user":
            items.append({"role": "user",
                          "content": [{"type": "input_text", "text": _text_of(m.get("content"))}]})
        elif role == "assistant":
            txt = _text_of(m.get("content"))
            if txt:
                items.append({"role": "assistant",
                              "content": [{"type": "output_text", "text": txt}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                items.append({"type": "function_call", "call_id": tc.get("id"),
                              "name": fn.get("name", ""), "arguments": fn.get("arguments") or "{}"})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"),
                          "output": _text_of(m.get("content")) or "(no output)"})
    return "\n\n".join(instructions), items


def _to_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [{
        "type": "function",
        "name": t["function"]["name"],
        "description": t["function"].get("description", ""),
        "strict": False,
        "parameters": t["function"].get("parameters") or {"type": "object", "properties": {}},
    } for t in tools]


class CodexProvider(Provider):
    name = "codex"

    def __init__(self, model: str = "gpt-5", base_url: str = "https://chatgpt.com/backend-api/codex",
                 timeout: float = 300.0):
        self.model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, token: str | None) -> dict[str, str]:
        if not token:
            raise ProviderError("codex: kein Access-Token (Secret-Tresor leer).")
        h = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "codex_cli_rs/0.0.0 (traccoon)",
            "originator": "codex_cli_rs",
            "content-type": "application/json",
        }
        acct = _account_id(token)
        if acct:
            h["ChatGPT-Account-ID"] = acct
        return h

    async def chat(self, *, model: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   web_search: bool = False, auth_token: str | None = None) -> ChatResponse:
        instructions, items = _to_input_items(messages)
        body: dict[str, Any] = {
            "model": model or self.model,
            "instructions": instructions,
            "input": items,
            "store": False,
            "stream": True,
        }
        ctools = _to_tools(tools)
        if ctools:
            body["tools"] = ctools
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = True

        headers = {**self._headers(auth_token), "Accept": "text/event-stream"}
        final: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self._base}/responses",
                                         headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        retryable = resp.status_code in (429, 500, 502, 503, 504)
                        retry_after = None
                        if resp.status_code == 429:
                            hv = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-requests")
                            try:
                                retry_after = float(hv) if hv else None
                            except ValueError:
                                retry_after = None
                        raise ProviderError(
                            f"codex: HTTP {resp.status_code}: {raw.decode('utf-8', 'replace')[:400]}",
                            status=resp.status_code, retryable=retryable, retry_after=retry_after)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            ev = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        etype = ev.get("type", "")
                        if etype == "response.completed":
                            final = ev.get("response")
                        elif etype in ("response.failed", "error"):
                            raise ProviderError(f"codex: Stream-Fehler: {str(ev)[:300]}", retryable=True)
        except httpx.HTTPError as exc:
            raise ProviderError(f"codex: Verbindungsfehler: {exc}", retryable=True) from exc

        if final is None:
            raise ProviderError("codex: kein response.completed-Event empfangen", retryable=True)
        return self._parse(final)

    def _parse(self, data: dict[str, Any]) -> ChatResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        oai_calls: list[dict[str, Any]] = []
        for item in data.get("output", []):
            itype = item.get("type")
            if itype == "message":
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        text_parts.append(c.get("text", ""))
            elif itype == "function_call":
                args_raw = item.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
                cid = item.get("call_id") or item.get("id", "")
                calls.append(ToolCall(id=cid, name=item.get("name", ""), arguments=args))
                oai_calls.append({"id": cid, "type": "function",
                                  "function": {"name": item.get("name", ""), "arguments": args_raw}})
        text = "".join(text_parts) or (data.get("output_text") or "")
        if not text and not calls:
            status = data.get("status")
            detail = data.get("incomplete_details") or data.get("error") or {}
            raise ProviderError(
                f"codex: leere Antwort (status={status}, {str(detail)[:200]})", retryable=True)
        raw_msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if oai_calls:
            raw_msg["tool_calls"] = oai_calls
        usage = data.get("usage") or {}
        return ChatResponse(text=text, tool_calls=calls,
                            raw={"choices": [{"message": raw_msg}]}, usage=usage)
