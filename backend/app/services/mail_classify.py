"""Local pre-classification of e-mail over a model in house (qwen via litellm).

The aim: sensitive content stays in the house. The raw text goes ONLY to the local model;
to the outside (towards the Claude assistant) exclusively `redacted_summary` is passed on, a
cleaned summary without PII or secrets.
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
    """(provider, model, token_name) from the AgentDefinition with this role (one's own
    before the global one, project-less). None when the role does not exist, and then the
    caller uses the env fallback."""
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
    '  "redacted_summary": 1–3 Sätze, geschwärzt, ohne PII/Geheimnisse\n'
    '  "spam_score": Zahl 0.0–1.0 — wie sicher ist das unerwünschte Massen-/Betrugspost?\n'
    '  "spam_reason": max. 12 Wörter, warum (leer lassen, wenn unverdächtig)\n'
    "\n"
    "Zum spam_score: Ein BESTELLTER Newsletter oder Werbung eines Anbieters, bei dem der "
    "Empfänger Kunde ist, ist KEIN Spam (höchstens 0.3) — Bestellbestätigungen, Rechnungen "
    "und Terminmails erst recht nicht. Hoch (ab 0.8) nur bei Betrugsmustern: erfundene "
    "Paket-/Konto-/Mahnungsvorwände, Drohung mit Sperrung, Aufforderung zu Zahlung oder "
    "Zugangsdaten, Gewinnversprechen. Im Zweifel niedriger — ein Fehlalarm kostet mehr als "
    "ein durchgerutschter Werbebrief."
)


def _parse_json(text: str) -> dict:
    """Pull robust JSON out of a model answer (tolerating code fences and text around it)."""
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
                         classify_agent: str = "", spam_hints: list[str] | None = None,
                         spam_beispiele: list[str] | None = None) -> dict:
    """Returns {category, priority, sensitive, redacted_summary, spam_score, spam_reason}. On
    every error a safe fallback (sensitive=True, empty summary): in case of doubt give
    NOTHING to the outside. Provider, model and token come from the classifying agent (when
    set), otherwise the env fallback.

    `spam_hints` are the technical findings of the rules (SPF failed, return path deviating
    and so on). The model should assess the text, not read headers it does not see anyway;
    with the findings in the prompt it judges the same mail as the rules. `spam_beispiele`
    are the most recent decisions of the human, so that the assessment of the model moves
    along as well, not only the statistics.
    """
    fallback = {"category": "sonstiges", "priority": "normal",
                "sensitive": True, "redacted_summary": "",
                # No verdict is NOT "inconspicuous": with a failed classification, rules and
                # memory decide alone instead of inheriting an acquittal.
                "spam_score": 0.0, "spam_reason": ""}

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
    # Limit the raw text defensively (the local context need not be the whole mail).
    teile = [f"Konto: {account}", f"Von: {sender}", f"Betreff: {subject}"]
    if spam_hints:
        teile.append("\nTechnische Befunde zu dieser Mail:\n"
                     + "\n".join(f"- {h}" for h in spam_hints[:8]))
    if spam_beispiele:
        teile.append("\nFrühere Entscheidungen des Empfängers (daran ausrichten):\n"
                     + "\n".join(spam_beispiele[:6]))
    teile.append(f"\n--- Mailtext ---\n{(body or '')[:8000]}")
    user_msg = "\n".join(teile)
    try:
        resp = await impl.chat(
            model=model,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=1500, auth_token=token,
            # Thinking models (qwen3.6 and company) otherwise use the whole output budget on
            # reasoning and deliver empty text, so the classification fell back on the
            # emergency default EVERY time (sensitive=True, no summary) without anybody
            # noticing. For triage the thinking is wasted anyway.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
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
        "spam_score": _spam_score(data.get("spam_score")),
        "spam_reason": str(data.get("spam_reason", "") or "").strip()[:200],
    }


def _spam_score(roh) -> float:
    """0.0 to 1.0 from a model statement. Small models like to deliver '80' or '0,8' instead
    of 0.8; both are caught instead of silently becoming a 0."""
    if roh is None or roh == "":
        return 0.0
    try:
        wert = float(str(roh).replace(",", ".").strip().rstrip("%"))
    except ValueError:
        return 0.0
    if wert > 1.0:
        wert = wert / 100.0
    return round(min(1.0, max(0.0, wert)), 3)
