"""Lern-Policy des persönlichen Assistenten: passende Regel für einen Eingang finden
und Regeln per Freigabe („immer …") anlegen/aktualisieren.

Generisch — der Inhalt (Absender, Aktionen) ist owner-scoped und bleibt in der DB.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantPolicy

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ALLOWED_REDACTION = {"redacted", "unredacted"}
_ALLOWED_KIND = {"sender", "domain", "category"}


def parse_sender(from_header: str) -> tuple[str, str]:
    """('news@verband.de', 'verband.de') aus einem From-Header wie 'Name <news@verband.de>'.
    Leerstrings, wenn keine Adresse gefunden."""
    m = _EMAIL_RE.search(from_header or "")
    if not m:
        return "", ""
    email = m.group(0).lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    return email, domain


async def match_policy(db: AsyncSession, owner_id: int | None, *, sender_email: str,
                       domain: str, category: str) -> AssistantPolicy | None:
    """Beste passende, aktive Regel — Priorität Absender > Domain > Kategorie."""
    if not owner_id:
        return None
    rows = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.owner_user_id == owner_id,
        AssistantPolicy.enabled.is_(True)))).scalars().all()
    by_kind: dict[str, dict[str, AssistantPolicy]] = {"sender": {}, "domain": {}, "category": {}}
    for p in rows:
        by_kind.get(p.match_kind, {})[(p.match_value or "").lower()] = p
    for kind, key in (("sender", sender_email), ("domain", domain), ("category", (category or "").lower())):
        if key and key in by_kind[kind]:
            return by_kind[kind][key]
    return None


async def note_hit(db: AsyncSession, policy: AssistantPolicy) -> None:
    policy.hit_count = (policy.hit_count or 0) + 1
    policy.last_used_at = dt.datetime.now(tz=dt.timezone.utc)


async def upsert_policy(db: AsyncSession, owner_id: int | None, *, match_kind: str,
                        match_value: str, auto_approve: bool = True,
                        redaction: str = "redacted", action_hint: str = "") -> AssistantPolicy:
    """Regel für (owner, kind, value) anlegen oder aktualisieren."""
    if match_kind not in _ALLOWED_KIND:
        match_kind = "sender"
    if redaction not in _ALLOWED_REDACTION:
        redaction = "redacted"
    value = (match_value or "").strip().lower()
    existing = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.owner_user_id == owner_id,
        AssistantPolicy.match_kind == match_kind,
        AssistantPolicy.match_value == value))).scalar_one_or_none()
    if existing:
        existing.auto_approve = auto_approve
        existing.redaction = redaction
        if action_hint:
            existing.action_hint = action_hint
        existing.enabled = True
        return existing
    p = AssistantPolicy(owner_user_id=owner_id, match_kind=match_kind, match_value=value,
                        auto_approve=auto_approve, redaction=redaction, action_hint=action_hint)
    db.add(p)
    return p


async def agent_laeuft_lokal(db: AsyncSession, owner_id: int | None, role: str) -> bool:
    """Läuft dieser Agent auf einem Modell im eigenen Haus?

    Merkmal ist nicht der Provider-Name, sondern die eigene Endpoint-URL des Tokens: `openai`
    heißt hier meist nicht OpenAI, sondern ein eigener OpenAI-kompatibler Endpoint (LiteLLM &
    Co.). Ohne Base-URL geht der Aufruf zum Anbieter — dann verlässt der Text das Haus, und
    die Schwärzung ist genau dafür da.
    """
    from sqlalchemy import or_, select

    from ..models.agents import AgentDefinition
    from ..worker.secrets import resolve_provider_base_url

    row = (await db.execute(
        select(AgentDefinition).where(
            AgentDefinition.role == role, AgentDefinition.project_id.is_(None),
            or_(AgentDefinition.user_id == owner_id, AgentDefinition.user_id.is_(None)))
        .order_by(AgentDefinition.user_id.is_(None)))).scalars().first()
    if row is None or row.provider in ("claude_code", "claude", "anthropic", "codex"):
        return False       # Subscriptions laufen immer auswärts
    base = await resolve_provider_base_url(db, owner_id, row.provider, row.token_name or "")
    return bool(base)
