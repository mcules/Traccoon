"""Nebenaufgaben laufen nicht auf dem Modell, das die eigentliche Arbeit macht.

Zusammenfassen, Betiteln, Aufräumen — das ist Fleißarbeit ohne Urteilsvermögen, und sie auf
demselben Sonnet/Opus laufen zu lassen, das gerade denkt, kostet Geld und Wartezeit. Der
Vorläufer Hermes hatte dafür einen `auxiliary:`-Block mit eigenem Modell **je Aufgabe**
(compression, title_generation, triage …); Default `auto` = Haupt-Provider. Genau das hier,
mit Traccoons Bausteinen: benannte Provider-Tokens aus dem Tresor, Router mit Fallback.

Bewusste Eigenschaften:

* **Ohne Konfiguration ändert sich nichts.** Kein Eintrag → `auto` → derselbe Provider und
  dasselbe Modell wie der aufrufende Agent. Wer nichts einstellt, merkt nichts.
* **Eine Nebenaufgabe darf den Hauptlauf niemals reißen.** Jeder Aufruf ist gekapselt;
  Fehler und Zeitüberschreitungen liefern `None`, und der Aufrufer arbeitet ohne das
  Ergebnis weiter (bei der Kompaktierung heißt das: lieber hart kürzen als abbrechen).
* **Keine Tools, kein Gedächtnis, kein Cache-Anfassen.** Ein Aux-Aufruf ist ein
  Einweg-Gespräch; er darf den sorgfältig aufgebauten Prompt-Cache des Hauptlaufs nicht
  anfassen (Traccoons Haupthebel gegen Token-Verbrennung).
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..services.appsettings import get_setting
from .providers.base import ProviderError
from .providers.router import router
from .secrets import resolve_provider_base_url, resolve_provider_token

log = logging.getLogger("traccoon.aux")

# Die Nebenaufgaben, für die eine eigene Einstellung existiert. Bewusst wenige — jede weitere
# will begründet sein, sonst zerfasert die Konfiguration in Einstellungen, die niemand pflegt.
AUX_TASKS = {
    "compression": "Langen Gesprächsverlauf zusammenfassen (Kontext-Kompaktierung)",
    "title": "Kurzen Titel für einen Eingang oder ein Gespräch bilden",
    "curator": "Gelerntes Gedächtnis sichten und aufräumen",
}

# Schlüssel in app_settings, z. B. `aux.compression`. Wert ist JSON:
#   {"provider": "openai", "model": "qwen3.6-35b-q3", "token_name": "aux", "timeout": 300}
# Leer oder fehlend = `auto`.
def setting_key(task: str) -> str:
    return f"aux.{task}"


async def aux_config(db: AsyncSession, task: str) -> dict:
    """Eingestelltes Modell für eine Nebenaufgabe — leeres dict heißt `auto`."""
    raw = (await get_setting(db, setting_key(task), "")).strip()
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
    except ValueError:
        log.warning("aux.%s: Einstellung ist kein gültiges JSON — nutze den Haupt-Provider", task)
        return {}
    return cfg if isinstance(cfg, dict) and cfg.get("provider") else {}


async def aux_chat(db: AsyncSession, *, owner_id: int | None, task: str, messages: list[dict],
                   agent=None, tokens: dict | None = None, base_urls: dict | None = None,
                   max_tokens: int = 2048, temperature: float = 0.2) -> str | None:
    """Eine Nebenaufgabe an das dafür eingestellte Modell geben. `None` = hat nicht geklappt.

    `agent`/`tokens`/`base_urls` sind der Kontext des laufenden Agenten; sie tragen den
    `auto`-Fall (kein eigenes Modell eingestellt) ohne zusätzlichen Tresor-Zugriff.
    """
    cfg = await aux_config(db, task)
    if cfg:
        provider = str(cfg["provider"])
        model = str(cfg.get("model") or "")
        name = str(cfg.get("token_name") or "")
        token = await resolve_provider_token(db, owner_id, provider, name)
        base_url = cfg.get("base_url") or await resolve_provider_base_url(db, owner_id, provider, name)
        use_tokens = {provider: token}
        use_base_urls = {provider: base_url}
        timeout = float(cfg.get("timeout") or 120)
        # Endpoint-eigene Felder. Denkende Modelle (qwen3.6 & Co.) verbrauchen sonst ihr
        # ganzes Ausgabe-Budget im Reasoning und liefern leeren Text — für Fleißarbeit wie
        # Zusammenfassen ist das Denken ohnehin verschenkt. Voreinstellung deshalb: aus.
        extra = cfg.get("extra_body")
        if extra is None:
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
    elif agent is not None:
        # `auto`: derselbe Weg wie der Hauptlauf — inklusive dessen Fallback-Kette.
        provider, model = agent.provider, agent.model
        use_tokens, use_base_urls = tokens or {}, base_urls or {}
        timeout = 120.0
        extra = None            # Haupt-Provider: nichts dazutun, der Lauf kennt seine Felder
    else:
        return None

    try:
        # Zeitdeckel: eine Nebenaufgabe darf den Hauptlauf nicht aufhalten. Hermes lief hier
        # regelmäßig in 120s-Timeouts, weil die Kompaktierung auf dem großen Modell hing —
        # deshalb ist die Frist konfigurierbar und der Fehlschlag folgenlos.
        resp = await asyncio.wait_for(
            router.chat(provider=provider, model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens,
                        tokens=use_tokens, base_urls=use_base_urls, extra_body=extra),
            timeout=timeout)
    except (ProviderError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.warning("aux.%s fehlgeschlagen (%s) — Aufrufer arbeitet ohne das Ergebnis weiter", task, exc)
        return None
    text = (resp.text or "").strip()
    return text or None
