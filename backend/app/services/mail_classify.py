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
    "You are a local e-mail triage assistant. You run in-house; your output goes to another "
    "model outside the house. NEVER pass on any personal data, credentials, amounts, account, "
    "contract or file numbers, or complete names and addresses. Summarise the matter neutrally "
    "and briefly, so that a person can decide whether and how to act. Answer ONLY with a JSON "
    "object, no prose, no code fences.\n"
    "Fields:\n"
    '  "category": a short keyword (rechnung, termin, newsletter, behörde, say)\n'
    '  "priority": low | normal | high\n'
    '  "sensitive": true when the mail holds sensitive or private data, otherwise false\n'
    '  "redacted_summary": 1-3 sentences, redacted, without PII or secrets\n'
    '  "spam_score": a number 0.0-1.0 — how certain is this unwanted bulk or fraudulent mail?\n'
    '  "spam_reason": at most 12 words on why (leave empty when unsuspicious)\n'
    '  "betrug": true when somebody appears under a foreign name (a bank, a provider, an '
    "authority, a colleague) or presses for a payment, credentials, a phone call or the entry "
    "of a code, otherwise false\n"
    '  "merkmale": at most 5 findings, each {"kennung": "short_in_snake_case", '
    '"text": "one sentence in plain words"}\n'
    "On spam_score: a newsletter or advertising that was ORDERED from a provider the recipient "
    "is a customer of is NOT spam (0.3 at most) — order confirmations, invoices and appointment "
    "mails even less so. High (from 0.8) only with fraud patterns: invented parcel, account or "
    "dunning pretexts, a threat of a block, a demand for a payment or credentials.\n"
    "On betrug: this is not a matter of taste but the hard case. Somebody pretends to be another "
    "or wants money, credentials or a call back. Then set spam_score to at least 0.95 as well, "
    "the two belong together. Pushy advertising, an unwanted newsletter or the real dunning "
    "letter of the real provider are NO fraud: there betrug stays false, even when the "
    "spam_score is high. Watch WHO really sends: a known brand name in the display name with a "
    "foreign sender domain is the most common pattern.\n"
    "On merkmale: one short identifier per finding that can be recognised again "
    "(marke_fremde_domain, drohung_sperrung, zahlungsaufforderung), plus one sentence in plain "
    "words. The identifier is counted, the sentence is read. Name only what you really see."
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
                         spam_examples: list[str] | None = None) -> dict:
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
    if spam_examples:
        parts.append("\nEarlier decisions of the recipient (align with them):\n"
                     + "\n".join(spam_examples[:6]))
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
        "betrug": yes(data.get("betrug")),
        "merkmale": features(data.get("merkmale")),
    }


def yes(raw) -> bool:
    """A yes out of a model answer.

    Deliberately not `bool(...)`: `bool("false")` is True, and small models answer with the
    strings "false", "nein" or "0" as readily as with a real boolean. Everything that is not
    clearly a yes counts as a no, because this flag carries a mail over the auto threshold.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw >= 1
    return str(raw or "").strip().lower() in ("true", "1", "ja", "yes", "y", "wahr")


def features(raw) -> list[dict]:
    """The findings of the model, in the shape the rules already use: key plus plain text.

    Normalised on the way in, because the key is counted later (statistics, memory) and an
    invented spelling would become a category of its own. Capped at five: whoever names ten
    reasons has stopped looking and started listing.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ident = re.sub(r"[^a-z0-9_]+", "_", str(entry.get("kennung") or "").strip().lower())
        ident = ident.strip("_")[:40]
        text = str(entry.get("text") or "").strip()[:160]
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append({"kennung": ident, "text": text})
        if len(out) >= 5:
            break
    return out


def _spam_score(raw) -> float:
    """0.0 to 1.0 from a model statement. Small models like to deliver '80' or '0,8' instead
    of 0.8; both are caught instead of silently becoming a 0."""
    if raw is None or raw == "":
        return 0.0
    try:
        value = float(str(raw).replace(",", ".").strip().rstrip("%"))
    except ValueError:
        return 0.0
    if value > 1.0:
        value = value / 100.0
    return round(min(1.0, max(0.0, value)), 3)
