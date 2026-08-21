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
    "privat, werbung, phishing, spam, sonstiges)\n"
    '  "priority": eines von low|normal|high|urgent\n'
    '  "sensitive": true, wenn die Mail sensible/private Daten enthält, sonst false\n'
    '  "redacted_summary": 1–3 Sätze, geschwärzt, ohne PII/Geheimnisse\n'
    '  "spam_score": Zahl 0.0–1.0 — wie sicher ist das unerwünschte Massen-/Betrugspost?\n'
    '  "spam_reason": max. 12 Wörter, warum (leer lassen, wenn unverdächtig)\n'
    '  "betrug": true, wenn jemand unter fremdem Namen auftritt (Bank, Anbieter, Behörde, '
    "Kollege) oder zu Zahlung, Zugangsdaten, Anruf oder Code-Eingabe gedrängt wird, "
    "sonst false\n"
    '  "merkmale": höchstens 5 Befunde, je {"kennung": "kurz_in_schlangenschrift", '
    '"text": "ein knapper Satz"}, woran du es erkannt hast\n'
    "\n"
    "Zum spam_score: Ein BESTELLTER Newsletter oder Werbung eines Anbieters, bei dem der "
    "Empfänger Kunde ist, ist KEIN Spam (höchstens 0.3) — Bestellbestätigungen, Rechnungen "
    "und Terminmails erst recht nicht. Hoch (ab 0.8) nur bei Betrugsmustern: erfundene "
    "Paket-/Konto-/Mahnungsvorwände, Drohung mit Sperrung, Aufforderung zu Zahlung oder "
    "Zugangsdaten, Gewinnversprechen. Im Zweifel niedriger — ein Fehlalarm kostet mehr als "
    "ein durchgerutschter Werbebrief.\n"
    "Zum betrug: das ist keine Geschmacksfrage, sondern der harte Fall. Jemand gibt sich als "
    "ein anderer aus oder will an Geld, Zugangsdaten oder einen Rückruf. Setze dann auch "
    "spam_score auf mindestens 0.95, beides gehört zusammen. Aufdringliche Werbung, ein "
    "ungewollter Newsletter oder die echte Mahnung des echten Anbieters sind KEIN Betrug: "
    "dort bleibt betrug false, auch wenn der spam_score hoch ist. Achte darauf, WER wirklich "
    "absendet: ein bekannter Markenname im Anzeigenamen bei fremder Absenderdomain ist das "
    "häufigste Muster.\n"
    "Zu den merkmalen: je Befund eine kurze Kennung, die sich wiedererkennen lässt "
    "(marke_fremde_domain, rueckruf_statt_link, gefaelschter_code, drohung_sperrung), und "
    "ein Satz im Klartext dazu. Die Kennung wird gezählt, der Satz gelesen. Nenne nur, was "
    "wirklich in der Mail steht."
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
                # memory decide alone instead of inheriting an acquittal. And not an
                # accusation either: `betrug` false, so nothing is cleared away on the
                # strength of a call that never came back.
                "spam_score": 0.0, "spam_reason": "", "betrug": False, "merkmale": []}

    cfg = await resolve_classify_from_agent(db, owner_id, classify_agent)
    if cfg is None:
        log.warning("Classifying agent '%s' not found, falling back (nothing goes outside)", classify_agent)
        return fallback
    provider, model, token_name = cfg
    token = await resolve_provider_token(db, owner_id, provider, token_name)
    base_url = await resolve_provider_base_url(db, owner_id, provider, token_name)
    if not token or not base_url or not model:
        log.warning("Mail classification not configured (token/base_url/model missing), falling back")
        return fallback

    impl = OpenAIProvider(base_url=base_url)
    # Limit the raw text defensively (the local context need not be the whole mail).
    parts = [f"Konto: {account}", f"Von: {sender}", f"Betreff: {subject}"]
    if spam_hints:
        parts.append("\nTechnische Befunde zu dieser Mail:\n"
                     + "\n".join(f"- {h}" for h in spam_hints[:8]))
    if spam_beispiele:
        parts.append("\nFrühere Entscheidungen des Empfängers (daran ausrichten):\n"
                     + "\n".join(spam_beispiele[:6]))
    parts.append(f"\n--- Mailtext ---\n{(body or '')[:8000]}")
    user_msg = "\n".join(parts)
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
        log.warning("Mail classification failed: %s, falling back", exc)
        return fallback

    data = _parse_json(resp.text)
    if not data:
        log.warning("Mail classification: unparsable answer, falling back")
        return fallback

    prio = str(data.get("priority", "normal")).lower().strip()
    return {
        "category": str(data.get("category", "sonstiges")).strip()[:80] or "sonstiges",
        "priority": prio if prio in _ALLOWED_PRIORITY else "normal",
        "sensitive": bool(data.get("sensitive", True)),
        "redacted_summary": str(data.get("redacted_summary", "")).strip()[:2000],
        "spam_score": _spam_score(data.get("spam_score")),
        "spam_reason": str(data.get("spam_reason", "") or "").strip()[:200],
        "betrug": ja(data.get("betrug")),
        "merkmale": merkmale(data.get("merkmale")),
    }


def ja(roh) -> bool:
    """A yes out of a model answer.

    Deliberately not `bool(...)`: `bool("false")` is True, and small models answer with the
    strings "false", "nein" or "0" as readily as with a real boolean. Everything that is not
    clearly a yes counts as a no, because this flag carries a mail over the auto threshold.
    """
    if isinstance(roh, bool):
        return roh
    if isinstance(roh, (int, float)):
        return roh >= 1
    return str(roh or "").strip().lower() in ("true", "1", "ja", "yes", "y", "wahr")


def merkmale(roh) -> list[dict]:
    """The findings of the model, in the shape the rules already use: key plus plain text.

    Normalised on the way in, because the key is counted later (statistics, memory) and an
    invented spelling would become a category of its own. Capped at five: whoever names ten
    reasons has stopped looking and started listing.
    """
    if not isinstance(roh, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in roh:
        if not isinstance(entry, dict):
            continue
        kennung = re.sub(r"[^a-z0-9_]+", "_", str(entry.get("kennung") or "").strip().lower())
        kennung = kennung.strip("_")[:40]
        text = str(entry.get("text") or "").strip()[:160]
        if not kennung or kennung in seen:
            continue
        seen.add(kennung)
        out.append({"kennung": kennung, "text": text})
        if len(out) >= 5:
            break
    return out


def _spam_score(roh) -> float:
    """0.0 to 1.0 from a model statement. Small models like to deliver '80' or '0,8' instead
    of 0.8; both are caught instead of silently becoming a 0."""
    if roh is None or roh == "":
        return 0.0
    try:
        value = float(str(roh).replace(",", ".").strip().rstrip("%"))
    except ValueError:
        return 0.0
    if value > 1.0:
        value = value / 100.0
    return round(min(1.0, max(0.0, value)), 3)
