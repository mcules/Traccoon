"""Lokale E-Mail-Vorklassifizierung über ein hausinternes Modell (qwen via litellm).

Ziel: sensible Inhalte bleiben im Haus. Der Rohtext geht NUR an das lokale Modell;
nach außen (Richtung Claude-Assistent) reicht ausschließlich `redacted_summary` weiter —
eine bereinigte Zusammenfassung ohne PII/Geheimnisse.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import or_, select

from ..models.agents import AgentDefinition
from ..worker.providers.base import ProviderError
from ..worker.providers.openai import OpenAIProvider
from ..worker.secrets import resolve_provider_base_url, resolve_provider_token

log = logging.getLogger("traccoon.mail")


async def resolve_classify_from_agent(db: AsyncSession, owner_id: int | None,
                                      role: str) -> tuple[str, str, str] | None:
    """(provider, model, token_name) aus der AgentDefinition mit dieser Rolle (eigene vor
    globaler, projektlos). None, wenn es die Rolle nicht gibt → Aufrufer nutzt env-Fallback."""
    if not role:
        return None
    q = select(AgentDefinition).where(
        AgentDefinition.role == role, AgentDefinition.project_id.is_(None),
        or_(AgentDefinition.user_id == owner_id, AgentDefinition.user_id.is_(None)))
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        return None
    row = next((r for r in rows if r.user_id == owner_id), rows[0])
    return (row.provider or "openai", row.model or "", row.token_name or "")

_ALLOWED_PRIORITY = {"low", "normal", "high", "urgent"}

_SYSTEM = (
    "Du bist ein lokaler E-Mail-Triage-Assistent. Du läufst im Haus; deine Ausgabe wird an "
    "einen EXTERNEN KI-Assistenten weitergereicht. Gib deshalb NIEMALS Rohinhalt, "
    "personenbezogene Daten, Zugangsdaten, Beträge, Konto-/Vertrags-/Aktenzeichen oder "
    "vollständige Namen/Adressen weiter. Fasse den Vorgang neutral und knapp zusammen, "
    "sodass ein Mensch entscheiden kann, ob und wie er handelt. Antworte AUSSCHLIESSLICH mit "
    "einem JSON-Objekt, kein Fließtext, keine Code-Zäune.\n"
    "Felder:\n"
    '  "category": kurzes Schlagwort (z. B. rechnung, termin, newsletter, behörde, '
    "privat, spam, sonstiges)\n"
    '  "priority": eines von low|normal|high|urgent\n'
    '  "sensitive": true, wenn die Mail sensible/private Daten enthält, sonst false\n'
    '  "redacted_summary": 1–3 Sätze, geschwärzt, ohne PII/Geheimnisse'
)


def _parse_json(text: str) -> dict:
    """Robustes JSON aus einer Modell-Antwort ziehen (Code-Zäune/Vor-/Nachtext tolerieren)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                pass
    return {}


async def classify_email(db: AsyncSession, owner_id: int | None, *, account: str,
                         sender: str, subject: str, body: str,
                         classify_agent: str = "") -> dict:
    """→ {category, priority, sensitive, redacted_summary}. Bei jedem Fehler ein sicherer
    Fallback (sensitive=True, leere Summary) — im Zweifel NICHTS nach außen geben.
    Provider/Modell/Token kommen aus dem Klassifizier-Agenten (falls gesetzt) → env-Fallback."""
    fallback = {"category": "sonstiges", "priority": "normal",
                "sensitive": True, "redacted_summary": ""}

    cfg = await resolve_classify_from_agent(db, owner_id, classify_agent)
    if cfg is None:
        log.warning("Klassifizier-Agent '%s' nicht gefunden → Fallback (nichts nach außen)", classify_agent)
        return fallback
    provider, model, token_name = cfg
    token = await resolve_provider_token(db, owner_id, provider, token_name)
    base_url = await resolve_provider_base_url(db, owner_id, provider, token_name)
    if not token or not base_url or not model:
        log.warning("Mail-Klassifizierung nicht konfiguriert (token/base_url/model fehlt) → Fallback")
        return fallback

    impl = OpenAIProvider(base_url=base_url)
    # Rohtext defensiv begrenzen (der lokale Kontext muss nicht die ganze Mail sein).
    user_msg = (f"Konto: {account}\nVon: {sender}\nBetreff: {subject}\n\n"
                f"--- Mailtext ---\n{(body or '')[:8000]}")
    try:
        resp = await impl.chat(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=1500, auth_token=token)
    except ProviderError as exc:
        log.warning("Mail-Klassifizierung fehlgeschlagen: %s → Fallback", exc)
        return fallback

    data = _parse_json(resp.text)
    if not data:
        log.warning("Mail-Klassifizierung: unparsbare Antwort → Fallback")
        return fallback

    prio = str(data.get("priority", "normal")).lower().strip()
    return {
        "category": str(data.get("category", "sonstiges")).strip()[:80] or "sonstiges",
        "priority": prio if prio in _ALLOWED_PRIORITY else "normal",
        "sensitive": bool(data.get("sensitive", True)),
        "redacted_summary": str(data.get("redacted_summary", "")).strip()[:2000],
    }
