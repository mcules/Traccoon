"""How well does the detection really separate? Measurement instead of gut feeling.

Setting thresholds without knowing the distribution is guessing. This review takes a sample
from two folders whose truth is settled (spam folder = rubbish, inbox = wanted) and assesses
it with the same rules as live operation. Out comes where the two distributions lie and
which threshold separates them, without hitting real post.

**Without memory and without a model.** The memory knows exactly these mails (it was trained
from them), so measuring it as well would be circular. The model costs one pass per mail.
What stands here is therefore the *lower* bound: the rules alone.

The recommendation follows the guard rail of the detection: **no false positives.** What is
sought is the lowest threshold below which not a single real mail lies; what slips through
below it is the price for that.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from .mcp_client import McpError, call_tool, result_json
from .spam_rules import evaluate, is_forgery_suspicion, mail_text
from .spam_review import nonbusiness_domains, my_addresses
from .vault_contacts import known_domains, contact_hits

log = logging.getLogger("traccoon.spam")

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")


@dataclass
class Measurement:
    """Result of a review: the scores per class plus the recommendation."""
    spam: list[float] = field(default_factory=list)
    ham: list[float] = field(default_factory=list)
    acquittal: int = 0          # known senders that are not assessed in the first place
    error: list[str] = field(default_factory=list)
    # The most conspicuous messages from the inbox: they decide the threshold all by
    # themselves and should be looked at before being believed, because often it is not a
    # false positive but rubbish that was never sorted in.
    outlier: list[dict] = field(default_factory=list)

    def report(self) -> dict:
        spam = sorted(self.spam)
        ham = sorted(self.ham)
        highest_ham = ham[-1] if ham else 0.0
        # No false positives: the threshold has to lie above the highest real mail.
        suggestion = round(min(0.95, highest_ham + 0.05), 2) if ham else 0.45
        caught = sum(1 for s in spam if s >= suggestion)
        return {
            "spam_geprueft": len(spam),
            "ham_geprueft": len(ham),
            "freispruch_bekannt": self.acquittal,
            "spam_mittel": round(sum(spam) / len(spam), 3) if spam else None,
            "ham_mittel": round(sum(ham) / len(ham), 3) if ham else None,
            "spam_spanne": [spam[0], spam[-1]] if spam else None,
            "ham_spanne": [ham[0], ham[-1]] if ham else None,
            "hoechstes_ham": highest_ham,
            "vorschlag_frage_ab": suggestion,
            "ausreisser": sorted(self.outlier, key=lambda a: -a["score"])[:5],
            "davon_erkannt": caught,
            "trefferquote": round(caught / len(spam), 2) if spam else None,
            "fehler": self.error[:5],
        }


async def _mail_fetch(account: str, folder: str, uid: int) -> dict | None:
    """Prepare a message the way the watcher would deliver it."""
    try:
        answer = await call_tool(IMAP_MCP_URL, "get_email", {
            "account": account, "folder": folder, "uid": uid, "body_format": "both",
            "max_body_bytes": 20000})
    except McpError as exc:
        log.debug("Review: %s/%s/%s not readable (%s)", account, folder, uid, exc)
        return None
    data = result_json(answer) or {}
    header = data.get("headers") or {}
    text = ((data.get("body") or {}).get("text") or {}).get("content") or ""
    html = ((data.get("body") or {}).get("html") or {}).get("content") or ""
    # The watcher delivers the links along; whoever assesses afterwards has to get them out
    # of the HTML themselves; otherwise the review sees a mail without links where there are links.
    links = [{"href": href, "text": re.sub(r"<[^>]+>", " ", inside).strip()[:200]}
             for href, inside in re.findall(
                 r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html,
                 re.IGNORECASE | re.DOTALL)]
    return {
        "account": account, "folder": folder, "uid": uid,
        "from": header.get("from") or [], "to": header.get("to") or [],
        "cc": header.get("cc") or [], "reply_to": header.get("reply_to") or [],
        "subject": header.get("subject") or "", "message_id": header.get("message_id") or "",
        "date": header.get("date") or "",
        "headers": header.get("spam") or {},
        "body_text": text, "body_html": html, "links": links,
        "attachments": data.get("attachments") or [],
    }


async def review(db: AsyncSession, owner_id: int | None, *, sample: int = 40,
                     accounts: list[dict] | None = None) -> Measurement:
    """Assess a sample from the spam folder and the inbox. Returns the measurement."""
    from .spam_bootstrap import accounts as all_accounts

    measurement = Measurement()
    my = await my_addresses(db)
    domains = await known_domains(db, owner_id)
    without_business = await nonbusiness_domains(db)

    for account in (accounts if accounts is not None else await all_accounts(db)):
        alias = account["alias"]
        tasks = [(account.get("spam_folder"), True),
                    (account.get("inbox_folder") or "INBOX", False)]
        for folder, is_spam in tasks:
            if not folder:
                continue
            try:
                answer = await call_tool(IMAP_MCP_URL, "search_emails", {
                    "account": alias, "folder": folder, "limit": sample})
            except McpError as exc:
                measurement.error.append(f"{alias}/{folder}: {exc}")
                continue
            for hits in ((result_json(answer) or {}).get("results") or []):
                payload = await _mail_fetch(alias, folder, int(hits["uid"]))
                if payload is None:
                    continue
                rule = evaluate(payload, my_addresses=my, known_domains=domains,
                                 nonbusiness_domains=without_business,
                                 body=mail_text(payload))
                # Known senders are not assessed at all in live operation: they do not belong
                # in the distribution but are counted separately.
                if not is_spam:
                    hits_kind = await contact_hits(
                        db, owner_id, rule.sender_email, rule.sender_domain)
                    if hits_kind in ("frontmatter", "sent") and not is_forgery_suspicion(
                            rule.signals):
                        measurement.acquittal += 1
                        continue
                (measurement.spam if is_spam else measurement.ham).append(rule.score)
                if not is_spam and rule.score >= 0.45:
                    measurement.outlier.append({
                        "score": rule.score, "konto": alias,
                        "von": rule.sender_email, "betreff": payload["subject"][:70],
                        "gruende": rule.reasons[:3]})
    return measurement


async def classifications(db: AsyncSession, owner_id: int | None, *, days: int = 30) -> dict:
    """As what mail was classified, counted at query time.

    No second stock of data: the rows carry it already, so the answer covers everything that
    ever ran through, not only what has been measured since a counter was switched on.
    Grouped by the VALUE in the column, never by a list in the code: a kind the model names
    tomorrow for the first time appears here without a line being changed.

    Two pots, because a mail leaves two different traces: everything suspicious becomes a
    `SpamVerdict` (from the question threshold on), everything inconspicuous an
    `AssistantTask` with the category of the model. Whoever counts only one of them is
    counting half a mailbox.
    """
    from sqlalchemy import func, select

    from ..models.assistant import AssistantTask, SpamVerdict

    since = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=max(1, days))

    suspicions = (await db.execute(
        select(SpamVerdict.kind, SpamVerdict.status, func.count())
        .where(SpamVerdict.owner_user_id == owner_id, SpamVerdict.created_at >= since)
        .group_by(SpamVerdict.kind, SpamVerdict.status))).all()
    passed = (await db.execute(
        select(AssistantTask.category, func.count())
        .where(AssistantTask.owner_user_id == owner_id, AssistantTask.kind != "chat",
               AssistantTask.created_at >= since)
        .group_by(AssistantTask.category))).all()

    kinds: dict[str, dict] = {}
    for kind, status, n in suspicions:
        entry = kinds.setdefault(kind or "unknown",
                                   {"total": 0, "sortedout": 0, "passed": 0, "open": 0})
        entry["total"] += n
        if status == "spam":
            entry["sortedout"] += n
        elif status == "pending":
            entry["open"] += n
        else:
            entry["passed"] += n
    for category, n in passed:
        entry = kinds.setdefault(category or "unknown",
                                   {"total": 0, "sortedout": 0, "passed": 0, "open": 0})
        entry["total"] += n
        entry["passed"] += n

    # How well the model judged, measured against what the human decided. Only rows that were
    # decided count: a pending question says nothing about anybody being right.
    decided = (await db.execute(
        select(SpamVerdict.model_score, SpamVerdict.status)
        .where(SpamVerdict.owner_user_id == owner_id, SpamVerdict.created_at >= since,
               SpamVerdict.status.in_(("spam", "ham"))))).all()
    hits = sum(1 for score, status in decided
                  if (score >= 0.5) == (status == "spam"))
    return {
        "days": days,
        "kinds": dict(sorted(kinds.items(), key=lambda p: -p[1]["total"])),
        "model": {"decided": len(decided), "hits": hits,
                  "quote": round(hits / len(decided), 3) if decided else None},
    }


async def balance(db: AsyncSession, owner_id: int | None) -> dict:
    """What actually happened in operation: asked, decided, learned."""
    from sqlalchemy import func, select

    from ..models.assistant import SpamFeatureStat, SpamVerdict

    lines = (await db.execute(
        select(SpamVerdict.status, SpamVerdict.decided_by, func.count())
        .where(SpamVerdict.owner_user_id == owner_id)
        .group_by(SpamVerdict.status, SpamVerdict.decided_by))).all()
    settled = (await db.execute(
        select(func.count()).select_from(SpamFeatureStat).where(
            SpamFeatureStat.owner_user_id == owner_id,
            SpamFeatureStat.feature.like("from:%"),
            ((SpamFeatureStat.spam_count >= 3) & (SpamFeatureStat.ham_count == 0))
            | ((SpamFeatureStat.ham_count >= 3) & (SpamFeatureStat.spam_count == 0))))).scalar()
    return {
        "verdicts": {f"{st}/{by or '—'}": n for st, by, n in lines},
        "settled_senders": int(settled or 0),
    }
