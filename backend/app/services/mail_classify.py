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

from ..config import settings
from ..models.nexus import AppSetting
from ..worker.providers.base import ProviderError
from ..worker.providers.openai import OpenAIProvider
from ..worker.secrets import resolve_provider_base_url, resolve_provider_token

log = logging.getLogger("traccoon.mail")


async def classify_config(db: AsyncSession) -> tuple[str, str, str]:
    """(provider, model, token_name) für die Vorklassifizierung — DB-Override (AppSetting,
    UI-editierbar) vor env-Default. So bleibt qwen für den Webhook, ohne Redeploy änderbar."""
    async def _s(key: str, dflt: str) -> str:
        row = await db.get(AppSetting, f"mail_classify_{key}")
        return (row.value if row and row.value else "") or dflt
    return (
        await _s("provider", settings.mail_classify_provider or "openai"),
        await _s("model", settings.mail_classify_model),
        await _s("token_name", settings.mail_classify_token_name or "local"),
    )


async def set_classify_config(db: AsyncSession, provider: str, model: str, token_name: str) -> None:
    for key, val in (("provider", provider), ("model", model), ("token_name", token_name)):
        row = await db.get(AppSetting, f"mail_classify_{key}")
        if row is None:
            db.add(AppSetting(key=f"mail_classify_{key}", value=val or ""))
        else:
            row.value = val or ""
    await db.commit()

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
                         sender: str, subject: str, body: str) -> dict:
    """→ {category, priority, sensitive, redacted_summary}. Bei jedem Fehler ein sicherer
    Fallback (sensitive=True, leere Summary) — im Zweifel NICHTS nach außen geben."""
    fallback = {"category": "sonstiges", "priority": "normal",
                "sensitive": True, "redacted_summary": ""}

    # Laufzeit-Konfig: DB-Override (AppSetting, in der UI editierbar) → env-Default.
    provider, model, token_name = await classify_config(db)
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
