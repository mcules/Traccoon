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

import logging
import os
import re
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from .mcp_client import McpError, call_tool, ergebnis_json
from .spam_rules import evaluate, ist_faelschungsverdacht, mail_text
from .spam_review import geschaeftsfreie_domains, meine_adressen
from .vault_contacts import bekannte_domains, kontakt_treffer

log = logging.getLogger("traccoon.spam")

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")


@dataclass
class Messung:
    """Result of a review: the scores per class plus the recommendation."""
    spam: list[float] = field(default_factory=list)
    ham: list[float] = field(default_factory=list)
    freispruch: int = 0          # known senders that are not assessed in the first place
    fehler: list[str] = field(default_factory=list)
    # The most conspicuous messages from the inbox: they decide the threshold all by
    # themselves and should be looked at before being believed, because often it is not a
    # false positive but rubbish that was never sorted in.
    ausreisser: list[dict] = field(default_factory=list)

    def bericht(self) -> dict:
        spam = sorted(self.spam)
        ham = sorted(self.ham)
        hoechstes_ham = ham[-1] if ham else 0.0
        # No false positives: the threshold has to lie above the highest real mail.
        vorschlag = round(min(0.95, hoechstes_ham + 0.05), 2) if ham else 0.45
        erwischt = sum(1 for s in spam if s >= vorschlag)
        return {
            "spam_geprueft": len(spam),
            "ham_geprueft": len(ham),
            "freispruch_bekannt": self.freispruch,
            "spam_mittel": round(sum(spam) / len(spam), 3) if spam else None,
            "ham_mittel": round(sum(ham) / len(ham), 3) if ham else None,
            "spam_spanne": [spam[0], spam[-1]] if spam else None,
            "ham_spanne": [ham[0], ham[-1]] if ham else None,
            "hoechstes_ham": hoechstes_ham,
            "vorschlag_frage_ab": vorschlag,
            "ausreisser": sorted(self.ausreisser, key=lambda a: -a["score"])[:5],
            "davon_erkannt": erwischt,
            "trefferquote": round(erwischt / len(spam), 2) if spam else None,
            "fehler": self.fehler[:5],
        }


async def _mail_holen(account: str, folder: str, uid: int) -> dict | None:
    """Prepare a message the way the watcher would deliver it."""
    try:
        antwort = await call_tool(IMAP_MCP_URL, "get_email", {
            "account": account, "folder": folder, "uid": uid, "body_format": "both",
            "max_body_bytes": 20000})
    except McpError as exc:
        log.debug("Review: %s/%s/%s not readable (%s)", account, folder, uid, exc)
        return None
    daten = ergebnis_json(antwort) or {}
    kopf = daten.get("headers") or {}
    text = ((daten.get("body") or {}).get("text") or {}).get("content") or ""
    html = ((daten.get("body") or {}).get("html") or {}).get("content") or ""
    # The watcher delivers the links along; whoever assesses afterwards has to get them out
    # of the HTML themselves; otherwise the review sees a mail without links where there are links.
    links = [{"href": href, "text": re.sub(r"<[^>]+>", " ", innen).strip()[:200]}
             for href, innen in re.findall(
                 r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html,
                 re.IGNORECASE | re.DOTALL)]
    return {
        "account": account, "folder": folder, "uid": uid,
        "from": kopf.get("from") or [], "to": kopf.get("to") or [],
        "cc": kopf.get("cc") or [], "reply_to": kopf.get("reply_to") or [],
        "subject": kopf.get("subject") or "", "message_id": kopf.get("message_id") or "",
        "date": kopf.get("date") or "",
        "headers": kopf.get("spam") or {},
        "body_text": text, "body_html": html, "links": links,
        "attachments": daten.get("attachments") or [],
    }


async def rueckschau(db: AsyncSession, owner_id: int | None, *, stichprobe: int = 40,
                     konten: list[dict] | None = None) -> Messung:
    """Assess a sample from the spam folder and the inbox. Returns the measurement."""
    from .spam_bootstrap import konten as alle_konten

    messung = Messung()
    meine = await meine_adressen(db)
    domains = await bekannte_domains(db, owner_id)
    ohne_geschaeft = await geschaeftsfreie_domains(db)

    for konto in (konten if konten is not None else await alle_konten(db)):
        alias = konto["alias"]
        aufgaben = [(konto.get("spam_folder"), True),
                    (konto.get("inbox_folder") or "INBOX", False)]
        for folder, ist_spam in aufgaben:
            if not folder:
                continue
            try:
                antwort = await call_tool(IMAP_MCP_URL, "search_emails", {
                    "account": alias, "folder": folder, "limit": stichprobe})
            except McpError as exc:
                messung.fehler.append(f"{alias}/{folder}: {exc}")
                continue
            for treffer in ((ergebnis_json(antwort) or {}).get("results") or []):
                payload = await _mail_holen(alias, folder, int(treffer["uid"]))
                if payload is None:
                    continue
                regel = evaluate(payload, meine_adressen=meine, bekannte_domains=domains,
                                 geschaeftsfreie_domains=ohne_geschaeft,
                                 body=mail_text(payload))
                # Known senders are not assessed at all in live operation: they do not belong
                # in the distribution but are counted separately.
                if not ist_spam:
                    treffer_art = await kontakt_treffer(
                        db, owner_id, regel.sender_email, regel.sender_domain)
                    if treffer_art in ("frontmatter", "sent") and not ist_faelschungsverdacht(
                            regel.signals):
                        messung.freispruch += 1
                        continue
                (messung.spam if ist_spam else messung.ham).append(regel.score)
                if not ist_spam and regel.score >= 0.45:
                    messung.ausreisser.append({
                        "score": regel.score, "konto": alias,
                        "von": regel.sender_email, "betreff": payload["subject"][:70],
                        "gruende": regel.reasons[:3]})
    return messung


async def bilanz(db: AsyncSession, owner_id: int | None) -> dict:
    """What actually happened in operation: asked, decided, learned."""
    from sqlalchemy import func, select

    from ..models.assistant import SpamFeatureStat, SpamVerdict

    zeilen = (await db.execute(
        select(SpamVerdict.status, SpamVerdict.decided_by, func.count())
        .where(SpamVerdict.owner_user_id == owner_id)
        .group_by(SpamVerdict.status, SpamVerdict.decided_by))).all()
    geklaert = (await db.execute(
        select(func.count()).select_from(SpamFeatureStat).where(
            SpamFeatureStat.owner_user_id == owner_id,
            SpamFeatureStat.feature.like("from:%"),
            ((SpamFeatureStat.spam_count >= 3) & (SpamFeatureStat.ham_count == 0))
            | ((SpamFeatureStat.ham_count >= 3) & (SpamFeatureStat.spam_count == 0))))).scalar()
    return {
        "urteile": {f"{st}/{by or '—'}": n for st, by, n in zeilen},
        "geklaerte_absender": int(geklaert or 0),
    }
