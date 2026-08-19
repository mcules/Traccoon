"""Spam assessment of incoming mail: forming a verdict, asking, executing, learning.

Three voices decide together, none of them alone:

1. **Rules** (`spam_rules`): technical facts from addresses and headers.
2. **Local model** (`mail_classify`): the text, judged in house.
3. **Memory** (`spam_learn`): what the human decided in comparable cases.

The third voice is the reason the detection gets better over time instead of staying
equally bad: it grows with every confirmation. Once it agrees about a sender clearly
enough it decides alone, and then nobody is asked any more.

The guard rail above all: **a false positive costs more than an advertising letter that
slipped through.** Nothing is ever deleted, only moved to the spam folder, and at the
current stage of development never without the confirmation of a human.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import SpamVerdict
from ..models.notification import Notification
from ..models.user import User
from . import spam_learn
from .appsettings import get_setting
from .i18n import tr
from .mcp_client import McpError, call_tool, ergebnis_text
from .spam_rules import (
    FREEMAIL_DOMAINS, evaluate, features, ist_faelschungsverdacht, mail_text,
)
from .vault_contacts import bekannte_domains, kontakt_treffer, namens_kollision

log = logging.getLogger("traccoon.spam")

# --- Adjustable settings (AppSetting, so they can be changed without a restart) ------
AKTIV_KEY = "spam_aktiv"                     # '1'/'0'
FRAGE_AB_KEY = "spam_frage_ab"               # from here on anything is asked at all
SOFORT_AB_KEY = "spam_sofort_ab"             # from here on singly instead of a digest card
AUTO_AB_KEY = "spam_auto_ab"                 # from here on away without asking (recall window)
DIGEST_MIN_KEY = "spam_digest_minuten"       # beat of the digest card
MEINE_ADRESSEN_KEY = "spam_meine_adressen"   # eigene Empfangsadressen, Komma-getrennt
# Domains over which demonstrably no contractual business runs. There, every "invoice" is a
# claim, regardless of what the address in front of it is called.
KEINE_GESCHAEFTSDOMAINS_KEY = "spam_keine_geschaeftsdomains"

_VORGABE = {
    AKTIV_KEY: "1",
    FRAGE_AB_KEY: "0.45",
    SOFORT_AB_KEY: "0.9",
    # Above 1.0 = off. Auto-moving is not a default but a decision a human takes after
    # their own measurement (see `spam_report.rueckschau`).
    AUTO_AB_KEY: "1.01",
    DIGEST_MIN_KEY: "120",
    MEINE_ADRESSEN_KEY: "",
    KEINE_GESCHAEFTSDOMAINS_KEY: "",
}

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")


async def _zahl(db: AsyncSession, key: str) -> float:
    try:
        return float(await get_setting(db, key, _VORGABE[key]))
    except ValueError:
        return float(_VORGABE[key])


async def meine_adressen(db: AsyncSession) -> frozenset[str]:
    """Own receiving addresses and aliases. Entries may read `*@my-domain.de`."""
    roh = await get_setting(db, MEINE_ADRESSEN_KEY, "")
    return frozenset(t.strip().lower() for t in roh.replace(";", ",").split(",") if t.strip())


async def geschaeftsfreie_domains(db: AsyncSession) -> frozenset[str]:
    """Domains without contractual business (setting). `@` and case are forgiven."""
    roh = await get_setting(db, KEINE_GESCHAEFTSDOMAINS_KEY, "")
    return frozenset(t.strip().lstrip("@").lower()
                     for t in roh.replace(";", ",").split(",") if t.strip())


def _mischen(regel: float, modell: float, gelernt: float | None) -> float:
    """Three partial verdicts into one overall verdict.

    Weighted instead of maximum: the maximum would let every single voice decide on its own,
    and each of them errs in its own way, the rules on cleanly set up fraud, the model on
    soberly written advertising, the memory on everything new.

    Without observations the memory drops out instead of pulling towards the middle with 0.5.
    """
    if gelernt is None:
        return round(0.55 * regel + 0.45 * modell, 3)
    return round(0.4 * regel + 0.3 * modell + 0.3 * gelernt, 3)


async def beurteilen(db: AsyncSession, owner_id: int | None, payload: dict, *,
                     cls: dict, regel=None) -> dict:
    """Assess an incoming mail: a pure verdict, without side effects.

    Writes nothing and asks nobody: the result is a serialisable dict that fits into the
    context of a process instance. What follows from it (asking, moving, letting through) is
    decided by the flow in the graph; here stands only what was *established*. Before, both
    were interwoven in one function, and the order of treatment could therefore only be read
    in the code, not in the process.

    `regel` can be handed in ready made: the caller usually needs the findings beforehand
    anyway to pass them to the local model, and evaluating twice yields the same thing and
    would only spread the interpretation of the findings over two places.
    """
    aktiv = (await get_setting(db, AKTIV_KEY, _VORGABE[AKTIV_KEY])) == "1"

    if regel is None:
        regel = evaluate(payload, meine_adressen=await meine_adressen(db),
                         bekannte_domains=await bekannte_domains(db, owner_id),
                         geschaeftsfreie_domains=await geschaeftsfreie_domains(db),
                         body=mail_text(payload))
    subject = str(payload.get("subject") or "")

    # Boss scam: the display name is a known contact but the address is not theirs.
    # Technically there is nothing wrong with such a mail; only the contact list gives it
    # away, which is why this check stands here and not in the rule based part.
    if regel.sender_name:
        opfer = await namens_kollision(db, owner_id, regel.sender_name, regel.sender_email)
        if opfer:
            regel.treffer("namens_kollision",
                          f"gibt sich als „{opfer}“ aus, schreibt aber von "
                          f"{regel.sender_email or 'unbekannt'}")
            regel.score = min(1.0, regel.score)

    # --- Bekannter Absender ---------------------------------------------------
    # The address is always checked, the domain only when it says anything at all: a contact
    # at gmx.de does not exonerate all other gmx addresses.
    treffer = await kontakt_treffer(
        db, owner_id, regel.sender_email,
        "" if regel.sender_domain in FREEMAIL_DOMAINS else regel.sender_domain)
    # A known sender only exonerates as long as the technical side is right. The known name
    # in particular is the rewarding target: a forged mail "from the house bank" is more
    # dangerous than any advertising, and it is noticed exactly here.
    faelschungsverdacht = ist_faelschungsverdacht(regel.signals)
    # `sent` counts like a vault entry: whoever I wrote to myself I demonstrably know, and
    # that is the strongest "wanted" statement a mailbox can produce.
    bekannter_kontakt = treffer in ("frontmatter", "sent") and not faelschungsverdacht
    if bekannter_kontakt:
        log.debug("Mail from the known contact %s, no spam suspicion", regel.sender_email)
    if treffer and faelschungsverdacht:
        regel.reasons.append("bekannter Absender, aber Echtheitsprüfung fehlgeschlagen "
                             "(Fälschungsverdacht)")
        regel.signals.append("kontakt_gefaelscht")
        regel.score = min(1.0, regel.score + 0.2)
    elif treffer in ("body", "domain"):
        # Weaker degree of familiarity: a deduction, not an acquittal.
        regel.score = max(0.0, regel.score - 0.15)

    merkmale = features(regel, subject, kontakt_treffer=treffer)

    # --- Memory ---------------------------------------------------------------
    gelernt_score, gelernt_gruende, sicher = await spam_learn.bewerten(db, owner_id, merkmale)
    hat_gedaechtnis = bool(gelernt_gruende) or sicher

    modell = float(cls.get("spam_score") or 0.0)
    if str(cls.get("category") or "").lower() in ("spam", "phishing", "werbung"):
        modell = max(modell, 0.6)

    score = _mischen(regel.score, modell, gelernt_score if hat_gedaechtnis else None)

    # A subscribed newsletter is not spam. Without this brake, order confirmations and
    # invoices would wander into the spam folder along with the advertising.
    if regel.ist_newsletter and not faelschungsverdacht:
        score = min(score, 0.4)

    gruende = list(regel.reasons)
    if cls.get("spam_reason"):
        gruende.append(str(cls["spam_reason"])[:200])
    gruende.extend(gelernt_gruende)

    # The memory may decide alone when it agrees about the sender, which is exactly what the
    # learning is for. Otherwise the same question would stand forever. The consequence is
    # drawn by the graph; here stands only THAT the matter is settled and how.
    geklaert = bool(sicher and not faelschungsverdacht)
    # The verdict of one's own mail server stands for itself. In the weighted mixture it
    # would go under: with rule = 1.0 and a silent model, even a mail the own server gives 13
    # spam points to lands at ~0.55, which would put an auto threshold out of reach. Whoever
    # asks their own infrastructure and then does not believe it need not have asked; the
    # recall card remains the safety net.
    serverurteil = any(str(sig).startswith("server_spam") or sig == "betreff_spam_markiert"
                       for sig in regel.signals)
    empfaenger = regel.recipients[0] if regel.recipients else ""
    urteil = {
        "aktiv": aktiv,
        "score": score,
        "rule_score": regel.score,
        "model_score": modell,
        "learned_score": gelernt_score if hat_gedaechtnis else 0.0,
        "geklaert": geklaert,
        "serverurteil": serverurteil,
        "geklaert_urteil": ("spam" if gelernt_score >= 0.5 else "ham") if geklaert else "",
        "bekannter_kontakt": bekannter_kontakt,
        "faelschungsverdacht": faelschungsverdacht,
        "reasons": gruende[:12],
        "features": merkmale,
        "sender_email": regel.sender_email[:320],
        "sender_domain": regel.sender_domain[:255],
        "recipient": empfaenger[:320],
        "subject": subject[:500],
        "account": str(payload.get("account") or ""),
        "folder": str(payload.get("folder") or ""),
        "uid": payload.get("uid") if isinstance(payload.get("uid"), int) else None,
        # The thresholds travel into the verdict: the branch stands in the graph, and it
        # should be able to check against the setting of NOW without reading the database.
        "frage_ab": await _zahl(db, FRAGE_AB_KEY),
        "sofort_ab": await _zahl(db, SOFORT_AB_KEY),
        "auto_ab": await _zahl(db, AUTO_AB_KEY),
    }
    log.info("Spam verdict (%.2f: rule=%.2f model=%.2f learnt=%.2f, resolved=%s) from %s",
             score, regel.score, modell, gelernt_score, urteil["geklaert_urteil"] or "nein",
             regel.sender_email)
    return urteil


async def anlegen(db: AsyncSession, owner_id: int | None, urteil: dict, *,
                  task_id: int | None = None, instance_id: int | None = None) -> SpamVerdict:
    """Turn a verdict into a row: work stock and later learning material.

    `instance_id` binds the row to the flow that produced it: the Telegram button therefore
    no longer decides past the engine but advances the flow
    (see `entscheiden`).
    """
    verdict = SpamVerdict(
        owner_user_id=owner_id,
        assistant_task_id=task_id,
        workflow_instance_id=instance_id,
        account=str(urteil.get("account") or ""),
        folder=str(urteil.get("folder") or ""),
        uid=urteil.get("uid") if isinstance(urteil.get("uid"), int) else None,
        sender_email=str(urteil.get("sender_email") or "")[:320],
        sender_domain=str(urteil.get("sender_domain") or "")[:255],
        recipient=str(urteil.get("recipient") or "")[:320],
        subject=str(urteil.get("subject") or "")[:500],
        rule_score=float(urteil.get("rule_score") or 0.0),
        model_score=float(urteil.get("model_score") or 0.0),
        learned_score=float(urteil.get("learned_score") or 0.0),
        score=float(urteil.get("score") or 0.0),
        reasons=list(urteil.get("reasons") or [])[:12],
        features=list(urteil.get("features") or []),
        status="pending")
    db.add(verdict)
    await db.flush()
    return verdict


# --- Karten ------------------------------------------------------------------------

def karte(verdict: SpamVerdict, *, vorentschieden: bool = False,
          rueckholbar: bool = False) -> tuple[str, str]:
    """(Title, text) of the single card.

    Three shapes: the question, the learned case (already decided) and the automatically
    moved one (already happened, with a way back). The third is the only one where a human
    objects afterwards, which is why it says first what HAS happened.
    """
    if rueckholbar:
        kopf = "🗑 Automatisch aussortiert"
    else:
        kopf = "🚩 Spam-Verdacht" if not vorentschieden else "🚩 Spam (gelernt)"
    titel = f"{kopf} ({verdict.score:.2f})"
    zeilen = [
        f"Von:     {verdict.sender_email or '?'}",
        f"An:      {verdict.recipient or '?'}",
        f"Betreff: {verdict.subject or '(kein Betreff)'}",
    ]
    if verdict.reasons:
        zeilen.append("")
        zeilen.append("Grund:   " + "\n         · ".join(verdict.reasons[:5]))
    zeilen.append("")
    if rueckholbar:
        zeilen.append("Verschoben, ohne zu fragen — Punktzahl über der Auto-Schwelle. "
                      "Ein Druck holt sie zurück und merkt sich den Absender.")
    elif vorentschieden:
        zeilen.append("Verschoben — der Absender gilt als geklärt.")
    else:
        zeilen.append("Vorschlag: → Ordner Spam verschieben")
    return titel, "\n".join(zeilen)


async def melden(db: AsyncSession, owner_id: int | None, verdict: SpamVerdict, *,
                 sofort: bool, vorentschieden: bool = False,
                 rueckholbar: bool = False) -> None:
    """Put a single card into the notifications (the bot delivers it and attaches the
    buttons)."""
    if not owner_id:
        return
    owner = await db.get(User, owner_id)
    if owner is None or not owner.telegram_chat_id:
        return
    titel, text = karte(verdict, vorentschieden=vorentschieden, rueckholbar=rueckholbar)
    # The kind decides which buttons the bot attaches: a question gets two, an already
    # executed sorting exactly one, the way back.
    db.add(Notification(
        user_id=owner_id, spam_verdict_id=verdict.id,
        kind="spam_auto" if rueckholbar else "spam_review",
        chat_id=owner.telegram_chat_id, title=titel[:200], body=text[:4000]))
    if not sofort:
        log.debug("Verdict #%s is waiting for the collection card", verdict.id)


async def digest_faellig(db: AsyncSession) -> int:
    """Bundle open suspected cases below the immediate threshold into ONE card.

    Driven by the scheduler. Without bundling, half the day would consist of Telegram
    messages at any notable spam volume, and whoever is asked every three minutes eventually
    presses a button at random.
    """
    takt = int(await _zahl(db, DIGEST_MIN_KEY))
    if takt <= 0:
        return 0
    grenze = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=takt)
    offen = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.status == "pending", SpamVerdict.digest_batch.is_(None),
        SpamVerdict.created_at <= grenze).order_by(SpamVerdict.id).limit(50))).scalars().all()
    # Cases reported immediately already hang off a card of their own; they must not be
    # asked about twice.
    schon_gemeldet = set((await db.execute(select(Notification.spam_verdict_id).where(
        Notification.spam_verdict_id.in_([v.id for v in offen] or [0])))).scalars().all())
    offen = [v for v in offen if v.id not in schon_gemeldet]
    if not offen:
        return 0

    nach_owner: dict[int | None, list[SpamVerdict]] = {}
    for v in offen:
        nach_owner.setdefault(v.owner_user_id, []).append(v)

    gesendet = 0
    for owner_id, faelle in nach_owner.items():
        if not owner_id:
            continue
        owner = await db.get(User, owner_id)
        if owner is None or not owner.telegram_chat_id:
            continue
        batch = uuid.uuid4().hex[:12]
        zeilen = []
        for i, v in enumerate(faelle, 1):
            grund = v.reasons[0] if v.reasons else "auffällig"
            zeilen.append(f"{i}. {v.sender_email or '?'} ({v.score:.2f})\n"
                          f"   „{(v.subject or '(kein Betreff)')[:70]}“\n"
                          f"   {grund}")
            v.digest_batch = batch
        db.add(Notification(
            # The reference points at the first case of the collection; through its
            # `digest_batch` the bot finds the whole set again. The identifier itself does
            # not fit into the callback of a button when an action already stands there.
            user_id=owner_id, spam_verdict_id=faelle[0].id, kind="spam_digest",
            chat_id=owner.telegram_chat_id,
            title=(await tr(db, "server.notify.spam_verdacht", owner.locale,
                            anzahl=len(faelle)))[:200],
            body="\n".join(zeilen)[:4000]))
        gesendet += 1
    await db.commit()
    return gesendet


# --- Decision and execution ---------------------------------------------------------

async def entscheiden(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool, *,
                      decided_by: str = "telegram") -> str:
    """Take the answer of the human. Returns a plain text result for the reply.

    If a flow hangs off the row (the normal case since the mail process), nothing is moved
    here any more: the answer advances the approval node, and what happens afterwards
    (learning, remembering the sender, moving the mail) stands in the graph. Otherwise the
    flow would run past the button and stand at its waiting point forever.

    Without a flow (legacy from the time before the process) the direct way remains: first
    learn, then move. If moving fails (mail already cleared away, IMAP briefly gone) the
    decision still stays in the memory, because it was right, only not
    executable.
    """
    if verdict.workflow_instance_id:
        return await _an_ablauf_melden(db, verdict, ist_spam, decided_by=decided_by)
    await festschreiben(db, verdict, ist_spam, decided_by=decided_by)
    ergebnis = await imap_aktion(verdict, ist_spam)
    verdict.action_result = ergebnis[:2000]
    await db.commit()
    return ergebnis


async def festschreiben(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool, *,
                        decided_by: str = "telegram") -> None:
    """Record the verdict and learn from it (without IMAP). Does NOT commit."""
    vorher = verdict.status if verdict.status in ("spam", "ham") else ""
    verdict.status = "spam" if ist_spam else "ham"
    verdict.decided_by = decided_by
    verdict.decided_at = dt.datetime.now(tz=dt.timezone.utc)
    await spam_learn.merken(db, verdict, ist_spam, vorher=vorher)

    if not ist_spam:
        # "Not spam" is more than a no: the sender should not even stand out in future. The
        # learned rule takes hold before the assessment already.
        from .assistant_policy import upsert_policy
        if verdict.sender_email and verdict.sender_domain not in FREEMAIL_DOMAINS:
            await upsert_policy(db, verdict.owner_user_id, match_kind="sender",
                                match_value=verdict.sender_email, auto_approve=False)


async def _an_ablauf_melden(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool, *,
                            decided_by: str) -> str:
    """Give the answer to the waiting flow and advance it.

    The decision then stands in the context (`spam.entschieden`), and the graph reads it at
    its branch and executes the IMAP action. If no approval step is waiting (flow aborted,
    instance gone), the answer is executed the direct way: an answered question must not run
    into nothing.
    """
    from ..models.workflow import WorkflowInstance
    from .workflow_engine import advance, entscheide_genehmigung

    inst = await db.get(WorkflowInstance, verdict.workflow_instance_id)
    entschieden = await entscheide_genehmigung(
        db, inst, "approved" if ist_spam else "rejected", actor_id=None,
        context={"spam": {**((inst.context or {}).get("spam") or {}),
                          "entschieden": "spam" if ist_spam else "ham",
                          "entschieden_von": decided_by}},
    ) if inst is not None else False
    if not entschieden:
        log.warning("Verdict #%s: no waiting flow (instance %s), executed directly",
                    verdict.id, verdict.workflow_instance_id)
        await festschreiben(db, verdict, ist_spam, decided_by=decided_by)
        ergebnis = await imap_aktion(verdict, ist_spam)
        verdict.action_result = ergebnis[:2000]
        await db.commit()
        return ergebnis

    await db.commit()
    await advance(inst.id)
    # The flow has meanwhile written in a session of its own; for the reply to the human its
    # result counts, not the state from before.
    await db.refresh(verdict)
    return verdict.action_result or "an den Ablauf übergeben"


async def imap_aktion(verdict: SpamVerdict, ist_spam: bool) -> str:
    """Move mail through `imap-mcp`. Errors are reported, not raised: a mail that cannot be
    moved must not undo the decision."""
    if not (verdict.account and verdict.folder and verdict.uid):
        return "keine Mailkennung hinterlegt — nichts verschoben"
    werkzeug = "mark_spam" if ist_spam else "mark_not_spam"
    try:
        ergebnis = await call_tool(IMAP_MCP_URL, werkzeug, {
            "account": verdict.account, "folder": verdict.folder, "uid": verdict.uid})
    except McpError as exc:
        log.warning("%s failed for verdict #%s: %s", werkzeug, verdict.id, exc)
        return f"nicht verschoben: {exc}"
    text = ergebnis_text(ergebnis) or "verschoben"
    log.info("%s for verdict #%s: %s", werkzeug, verdict.id, text)
    return text


async def zurueckholen(db: AsyncSession, verdict: SpamVerdict, *,
                       decided_by: str = "telegram") -> str:
    """An automatically sorted out mail back into the inbox. Returns a plain text result.

    The objection to auto-moving, and it is more than a retreat: the sender is learned as
    wanted and gets a rule so that the same error does not happen again tomorrow. Without
    that, stage 2 would be a machine that makes the same mistake any number of
    times.
    """
    if verdict.status not in ("spam", "pending"):
        return f"schon erledigt ({verdict.status})"
    await festschreiben(db, verdict, False, decided_by=decided_by)
    ergebnis = await imap_aktion(verdict, False)
    verdict.action_result = ergebnis[:2000]
    await db.commit()
    log.info("Verdict #%s taken back: %s", verdict.id, ergebnis)
    return ergebnis


async def entscheide_batch(db: AsyncSession, batch: str, ist_spam: bool, *,
                           decided_by: str = "telegram") -> tuple[int, int]:
    """Decide all open cases of a digest card at once. Returns (done, errors)."""
    faelle = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.digest_batch == batch, SpamVerdict.status == "pending"))).scalars().all()
    fehler = 0
    for v in faelle:
        ergebnis = await entscheiden(db, v, ist_spam, decided_by=decided_by)
        if ergebnis.startswith("nicht verschoben"):
            fehler += 1
    return len(faelle), fehler
