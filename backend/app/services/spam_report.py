"""Wie gut trennt die Erkennung wirklich? — Messung statt Gefühl.

Schwellen zu setzen, ohne die Verteilung zu kennen, ist Raten. Diese Rückschau holt eine
Stichprobe aus zwei Ordnern, deren Wahrheit feststeht — Spam-Ordner = Müll, Posteingang =
erwünscht — und beurteilt sie mit denselben Regeln wie den Livebetrieb. Heraus kommt, wo
die beiden Verteilungen liegen und welche Schwelle sie trennt, ohne echte Post zu treffen.

**Ohne Gedächtnis und ohne Modell.** Das Gedächtnis kennt genau diese Mails (es wurde aus
ihnen angelernt) — es mitzumessen wäre ein Zirkelschluss. Das Modell kostet je Mail einen
Durchlauf. Was hier steht, ist also die *untere* Schranke: die Regeln allein.

Die Empfehlung folgt der Leitplanke der Erkennung: **kein Fehlalarm.** Gesucht wird die
niedrigste Schwelle, unter der keine einzige echte Mail liegt — was darunter durchrutscht,
ist der Preis dafür.
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
    """Ergebnis einer Rückschau — je Klasse die Punktzahlen, dazu die Empfehlung."""
    spam: list[float] = field(default_factory=list)
    ham: list[float] = field(default_factory=list)
    freispruch: int = 0          # bekannte Absender, die gar nicht erst beurteilt werden
    fehler: list[str] = field(default_factory=list)
    # Die auffälligsten Nachrichten aus dem Posteingang — sie entscheiden die Schwelle
    # ganz allein und gehören angesehen, bevor man ihnen glaubt: oft ist es kein
    # Fehlalarm, sondern Müll, der nie einsortiert wurde.
    ausreisser: list[dict] = field(default_factory=list)

    def bericht(self) -> dict:
        spam = sorted(self.spam)
        ham = sorted(self.ham)
        hoechstes_ham = ham[-1] if ham else 0.0
        # Kein Fehlalarm: die Schwelle muss über der höchsten echten Mail liegen.
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
    """Eine Nachricht so herrichten, wie der Watcher sie liefern würde."""
    try:
        antwort = await call_tool(IMAP_MCP_URL, "get_email", {
            "account": account, "folder": folder, "uid": uid, "body_format": "both",
            "max_body_bytes": 20000})
    except McpError as exc:
        log.debug("Rückschau: %s/%s/%s nicht lesbar (%s)", account, folder, uid, exc)
        return None
    daten = ergebnis_json(antwort) or {}
    kopf = daten.get("headers") or {}
    text = ((daten.get("body") or {}).get("text") or {}).get("content") or ""
    html = ((daten.get("body") or {}).get("html") or {}).get("content") or ""
    # Der Watcher liefert die Links mit; wer nachträglich beurteilt, muss sie selbst aus
    # dem HTML holen — sonst sieht die Rückschau eine Mail ohne Links, wo Links sind.
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
    """Stichprobe aus Spam-Ordner und Posteingang beurteilen. → Messung."""
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
                # Bekannte Absender werden im Livebetrieb gar nicht erst beurteilt —
                # sie gehören nicht in die Verteilung, sondern gesondert gezählt.
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
    """Was im Betrieb tatsächlich passiert ist — gefragt, entschieden, gelernt."""
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
