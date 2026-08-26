"""Learning policy of the personal assistant: find the matching rule for an inbox item and
create or update rules over an approval ("always …").

Generic; the content (sender, actions) is owner-scoped and stays in the database.
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
    """('news@verband.de', 'verband.de') from a From header like 'Name <news@verband.de>'.
    Empty strings when no address is found."""
    m = _EMAIL_RE.search(from_header or "")
    if not m:
        return "", ""
    email = m.group(0).lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    return email, domain


async def match_policy(db: AsyncSession, owner_id: int | None, *, sender_email: str,
                       domain: str, category: str) -> AssistantPolicy | None:
    """The rule that decides about this item.

    Two passes, and the order between them is the whole point: a BLOCK counts first, no matter
    how specific the allow beside it is. A domain is put on the block list precisely because
    one does not want to hunt down every single address behind it — an allow for one of those
    addresses must not punch a hole in that.

    Within a pass it stays sender before domain before category, the specific rule before the
    broad one.
    """
    if not owner_id:
        return None
    rows = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.owner_user_id == owner_id,
        AssistantPolicy.enabled.is_(True)))).scalars().all()
    keys = (("sender", sender_email), ("domain", domain), ("category", (category or "").lower()))

    def pick(blocked: bool) -> AssistantPolicy | None:
        by_kind: dict[str, dict[str, AssistantPolicy]] = {"sender": {}, "domain": {}, "category": {}}
        for p in rows:
            if bool(p.blocked) is blocked:
                by_kind.get(p.match_kind, {})[(p.match_value or "").lower()] = p
        for kind, key in keys:
            if key and key in by_kind[kind]:
                return by_kind[kind][key]
        return None

    return pick(True) or pick(False)


async def note_hit(db: AsyncSession, policy: AssistantPolicy) -> None:
    policy.hit_count = (policy.hit_count or 0) + 1
    policy.last_used_at = dt.datetime.now(tz=dt.timezone.utc)


async def upsert_policy(db: AsyncSession, owner_id: int | None, *, match_kind: str,
                        match_value: str, auto_approve: bool = True,
                        redaction: str = "redacted", action_hint: str = "",
                        blocked: bool = False, origin: str = "",
                        origin_task_id: int | None = None) -> AssistantPolicy:
    """Create or update the rule for (owner, kind, value)."""
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
        existing.auto_approve = auto_approve and not blocked
        existing.blocked = blocked
        existing.redaction = redaction
        if action_hint:
            existing.action_hint = action_hint
        # Where it came from is written only the first time: the origin of a rule is the
        # moment it was granted, not the last time somebody touched it.
        if origin and not existing.origin:
            existing.origin = origin[:300]
            existing.origin_task_id = origin_task_id
        existing.enabled = True
        return existing
    p = AssistantPolicy(owner_user_id=owner_id, match_kind=match_kind, match_value=value,
                        auto_approve=auto_approve and not blocked, blocked=blocked,
                        redaction=redaction, action_hint=action_hint,
                        origin=(origin or "")[:300], origin_task_id=origin_task_id)
    db.add(p)
    return p


async def revoke_policy(db: AsyncSession, owner_id: int | None, *, match_kind: str,
                        match_value: str) -> bool:
    """Take a rule back. True when there was one.

    A mistaken tap on "always this sender" has to be undoable without a developer, and by the
    assistant on behalf of its person — which is why this is a function and not three lines
    inside an endpoint.
    """
    if not owner_id:
        return False
    p = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.owner_user_id == owner_id,
        AssistantPolicy.match_kind == match_kind,
        AssistantPolicy.match_value == (match_value or "").strip().lower()))).scalar_one_or_none()
    if p is None:
        return False
    await db.delete(p)
    return True


async def agent_running_local(db: AsyncSession, owner_id: int | None, role: str) -> bool:
    """Does this agent run on a model in one's own house?

    The distinguishing feature is not the provider name but the endpoint URL of the token:
    `openai` mostly does not mean OpenAI here but an OpenAI-compatible endpoint of one's own
    (LiteLLM and company). Without a base URL the call goes to the vendor, and then the text
    leaves the house, which is exactly what the redaction is for.
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
        return False       # subscriptions always run outside
    base = await resolve_provider_base_url(db, owner_id, row.provider, row.token_name or "")
    return bool(base)
