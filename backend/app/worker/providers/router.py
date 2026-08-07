"""Provider-Router mit Cooldown/Circuit-Breaker (Port aus dem Vorläufer, self-contained).

Provider-Map ist schlank: claude_code→Anthropic, codex→Codex. Token pro Provider
kommen aus dem Secret-Tresor (im Worker aufgelöst, hier durchgereicht).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .anthropic import AnthropicProvider
from .base import ChatResponse, Provider, ProviderError
from .codex import CodexProvider
from .openai import OpenAIProvider

log = logging.getLogger("traccoon.providers")

_COOLDOWN_DEFAULT = float(os.getenv("PROVIDER_COOLDOWN", "300"))
_COOLDOWN_MAX = 3600.0
_RATE_LIMIT_WAIT = float(os.getenv("PROVIDER_RATE_LIMIT_WAIT", "60"))
_RATE_LIMIT_WAIT_MAX = 180.0
_MAX_ATTEMPTS = int(os.getenv("PROVIDER_MAX_ATTEMPTS", "4"))
# Deckel für die max_tokens-Eskalation (Anthropic `stop_reason=max_tokens`-Abbruch): ein
# Retry mit UNVERÄNDERTEM max_tokens würde denselben Fehler reproduzieren, darum wird das
# Budget vor dem nächsten Versuch verdoppelt — aber nicht grenzenlos, sonst kostet ein
# hartnäckiger Fall (z. B. Endlos-Thinking) beliebig viel pro Zug.
_MAX_TOKENS_ESCALATION_CEILING = int(os.getenv("PROVIDER_MAX_TOKENS_CEILING", "64000"))

# Traccoon-Provider-Namen (AgentDefinition.provider) → Impl.
# claude_code = Anthropic-OAuth-Subscription, codex = ChatGPT-Subscription,
# openai = echter OpenAI-API-Provider (sk-Key) — eigenständig, KEIN Codex-Alias mehr.
_ANTHROPIC = {"claude_code", "claude", "anthropic"}
_CODEX = {"codex"}
_OPENAI = {"openai"}


class Router:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {
            "claude_code": AnthropicProvider(),
            "codex": CodexProvider(),
            "openai": OpenAIProvider(),
        }
        # OpenAI-kompatible Provider mit eigener Base-URL, nach URL gecacht (der globale
        # Default-OpenAIProvider bleibt unangetastet → api.openai.com).
        self._openai_by_url: dict[str, OpenAIProvider] = {}
        self._cooldown: dict[str, float] = {}

    def _openai_for(self, base_url: str) -> OpenAIProvider:
        impl = self._openai_by_url.get(base_url)
        if impl is None:
            impl = OpenAIProvider(base_url=base_url)
            self._openai_by_url[base_url] = impl
        return impl

    def _impl(self, provider: str, base_url: str | None = None) -> Provider | None:
        if provider in _ANTHROPIC:
            return self._providers["claude_code"]
        if provider in _CODEX:
            return self._providers["codex"]
        if provider in _OPENAI:
            # Eigener Endpoint (lokales litellm o. Ä.) nur für die OpenAI-Familie.
            return self._openai_for(base_url) if base_url else self._providers["openai"]
        return None

    def _cooling(self, prov: str) -> bool:
        return self._cooldown.get(prov, 0.0) > time.monotonic()

    def _trip(self, prov: str, exc: ProviderError) -> None:
        cd = exc.retry_after if exc.retry_after else _COOLDOWN_DEFAULT
        cd = max(1.0, min(cd, _COOLDOWN_MAX))
        self._cooldown[prov] = time.monotonic() + cd
        log.warning("Provider '%s' rate-limited (HTTP %s) → Cooldown %ss", prov, exc.status, int(cd))

    def cooldown_status(self) -> dict[str, int]:
        now = time.monotonic()
        return {p: int(d - now) for p, d in self._cooldown.items() if d > now}

    async def chat(self, *, provider: str, model: str, messages, tools=None,
                   temperature: float = 0.3, max_tokens: int = 4096,
                   fallback: str | None = None, fallback_model: str = "",
                   web_search: bool = False,
                   tokens: dict[str, str | None] | None = None,
                   base_urls: dict[str, str | None] | None = None,
                   extra_body: dict | None = None) -> ChatResponse:
        tokens = tokens or {}
        base_urls = base_urls or {}
        raw_chain = [provider] + ([fallback] if fallback and fallback != provider else [])
        chain = [p for p in raw_chain if not self._cooling(p)] or raw_chain
        last_err: ProviderError | None = None
        for prov in chain:
            # Base-URL strikt per Provider; Legacy-Key-Rückfall analog zum Token unten.
            base_url = base_urls.get(prov)
            impl = self._impl(prov, base_url)
            if impl is None:
                last_err = ProviderError(f"Provider '{prov}' nicht verfügbar")
                continue
            # Jeder Provider hat eigene Modellnamen: Primär → model, Fallback → fallback_model
            # (leer → Provider-Default). Kein Übernehmen des Primär-Modells auf den Fallback.
            use_model = model if prov == provider else fallback_model
            # Token strikt per Provider; Legacy-Keys claude_code/codex nur als Default-Rückfall.
            token = tokens.get(prov)
            if token is None:
                legacy = "claude_code" if prov in _ANTHROPIC else "codex" if prov in _CODEX else prov
                token = tokens.get(legacy)
            # Eigene Kopie je Provider: die Eskalation (s. u.) darf das Budget des NÄCHSTEN
            # Providers in der Kette nicht mit hochziehen.
            cur_max_tokens = max_tokens
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    # `extra_body` kennt nur der OpenAI-kompatible Provider (endpoint-eigene
                    # Felder wie `chat_template_kwargs`). Die Subscription-Provider bekommen
                    # es NICHT — dort wäre es ein unbekanntes Feld und damit ein 400er.
                    zusatz = {"extra_body": extra_body} if extra_body and prov in _OPENAI else {}
                    resp = await impl.chat(model=use_model, messages=messages, tools=tools,
                                           temperature=temperature, max_tokens=cur_max_tokens,
                                           web_search=web_search, auth_token=token, **zusatz)
                    # Erst hier steht fest, WER geantwortet hat: nach einem Fallback ist das
                    # weder der eingestellte Provider noch dessen Modell. Ohne diese Zeilen
                    # bepreist der Aufrufer den ganzen Lauf mit dem Primärmodell.
                    resp.provider, resp.model = prov, use_model
                    return resp
                except ProviderError as exc:
                    last_err = exc
                    is_last = attempt >= _MAX_ATTEMPTS - 1
                    # Anlass: der KI-&-Tech-News-Job riss ab dem 03.08. jeden Tag an `stop_reason
                    # =max_tokens` — ein Retry mit UNVERÄNDERTEM Budget hätte denselben Fehler
                    # reproduziert (kein Verbindungsproblem, sondern ein zu knappes Budget für
                    # einen langen Recherche-Digest). Vor dem nächsten Versuch verdoppeln, bis
                    # zum Deckel — erst danach gilt der Fehler als endgültig.
                    if (exc.escalate_max_tokens and not is_last
                            and cur_max_tokens < _MAX_TOKENS_ESCALATION_CEILING):
                        cur_max_tokens = min(cur_max_tokens * 2, _MAX_TOKENS_ESCALATION_CEILING)
                        log.warning("Provider '%s': max_tokens-Abbruch → Eskalation auf %d",
                                    prov, cur_max_tokens)
                        continue
                    if exc.status == 529 and not is_last:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    if exc.status == 429 and not is_last:
                        wait = min(exc.retry_after or _RATE_LIMIT_WAIT, _RATE_LIMIT_WAIT_MAX)
                        self._trip(prov, exc)
                        await asyncio.sleep(wait)
                        continue
                    if exc.status in (429, 529):
                        self._trip(prov, exc)
                    elif not exc.retryable:
                        raise
                    break
        assert last_err is not None
        raise last_err


router = Router()
