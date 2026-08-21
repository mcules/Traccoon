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
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import SpamVerdict
from ..models.notification import Notification
from ..models.user import User
from . import spam_learn
from .appsettings import get_setting
from .i18n import tr
from .mail_classify import yes
from .mcp_client import McpError, call_tool, result_text
from .spam_rules import (
    FREEMAIL_DOMAINS, evaluate, features, is_forgery_suspicion, mail_text,
)
from .vault_contacts import known_domains, contact_hits, named_collision

log = logging.getLogger("traccoon.spam")

# --- Adjustable settings (AppSetting, so they can be changed without a restart) ------
ACTIVE_KEY = "spam_aktiv"                     # '1'/'0'
QUESTION_FROM_KEY = "spam_frage_ab"               # from here on anything is asked at all
IMMEDIATE_FROM_KEY = "spam_sofort_ab"             # from here on singly instead of a digest card
AUTO_FROM_KEY = "spam_auto_ab"                 # from here on away without asking (recall window)
DIGEST_MIN_KEY = "spam_digest_minuten"       # beat of the digest card
# Whether a mail cleared away WITHOUT a question is worth a message. The sorting itself
# does not hang off this: the verdict comes into being, is learnt from and stands in the
# overview either way, only the card stays away. The branch for it stands in the flow,
# this is merely the value it reads.
AUTO_REPORT_KEY = "spam_auto_melden"         # 1 = melden (Vorgabe), 0 = still
MY_ADDRESSES_KEY = "spam_meine_adressen"   # eigene Empfangsadressen, Komma-getrennt
# Domains over which demonstrably no contractual business runs. There, every "invoice" is a
# claim, regardless of what the address in front of it is called.
NO_GESCHAEFTSDOMAINS_KEY = "spam_keine_geschaeftsdomains"

_DEFAULT = {
    ACTIVE_KEY: "1",
    QUESTION_FROM_KEY: "0.45",
    IMMEDIATE_FROM_KEY: "0.9",
    # Above 1.0 = off. Auto-moving is not a default but a decision a human takes after
    # their own measurement (see `spam_report.rueckschau`).
    AUTO_FROM_KEY: "1.01",
    DIGEST_MIN_KEY: "120",
    AUTO_REPORT_KEY: "1",
    MY_ADDRESSES_KEY: "",
    NO_GESCHAEFTSDOMAINS_KEY: "",
}

IMAP_MCP_URL = os.getenv("IMAP_MCP_URL", "http://imap-mcp:3010/mcp")


async def _number(db: AsyncSession, key: str) -> float:
    try:
        return float(await get_setting(db, key, _DEFAULT[key]))
    except ValueError:
        return float(_DEFAULT[key])


async def my_addresses(db: AsyncSession) -> frozenset[str]:
    """Own receiving addresses and aliases. Entries may read `*@my-domain.de`."""
    raw = await get_setting(db, MY_ADDRESSES_KEY, "")
    return frozenset(t.strip().lower() for t in raw.replace(";", ",").split(",") if t.strip())


async def nonbusiness_domains(db: AsyncSession) -> frozenset[str]:
    """Domains without contractual business (setting). `@` and case are forgiven."""
    raw = await get_setting(db, NO_GESCHAEFTSDOMAINS_KEY, "")
    return frozenset(t.strip().lstrip("@").lower()
                     for t in raw.replace(";", ",").split(",") if t.strip())


def _mix(rule: float, model: float, learned: float | None) -> float:
    """Three partial verdicts into one overall verdict.

    Weighted instead of maximum: the maximum would let every single voice decide on its own,
    and each of them errs in its own way, the rules on cleanly set up fraud, the model on
    soberly written advertising, the memory on everything new.

    Without observations the memory drops out instead of pulling towards the middle with 0.5.
    """
    if learned is None:
        return round(0.55 * rule + 0.45 * model, 3)
    return round(0.4 * rule + 0.3 * model + 0.3 * learned, 3)


# What a fraud named by the model is worth at least. The weighted mixture cannot carry it:
# with a single rule signal (0.4) even a model at 0.95 reaches 0.76 at most, and every
# sensible auto threshold stays out of reach. Case of 2026-08-19 (verdict #42): a "N26" mail
# from fremde-firma.example, SPF, DKIM and DMARC all passing, model 0.95 with "Phishing-Versuch
# with a forged sender", overall verdict 0.731, and so it stayed in the inbox.
_FRAUD_SCORE = 0.95
# From here on the wording of the reason counts as a statement of its own. The floor keeps a
# negation ("no attempted fraud, just advertising") from turning into the opposite verdict, and
# it keeps the technical findings out that the flow passes into the prompt (they contain the
# word "forgery" themselves).
_FRAUD_TEXT_FROM = 0.8
# When model and memory are in dispute: above the asking threshold, below any sensible auto
# threshold. So the mail is shown but not touched.
_DISPUTE_SCORE = 0.6
# And once a person has contradicted for this sender: let it through. Below any asking
# threshold, because the question has been answered.
_DECIDED_SCORE = 0.2
# The words a local model writes into its reason. German and English, because the model
# answers in the language of the mail, not in the language of the house.
_FRAUD_WORDS = re.compile(
    r"phish|betrug|fraud|scam|f[äa]lsch|forge|identit[äa]tsdiebstahl|identity theft", re.I)


def _model_fraud(cls: dict, model: float) -> bool:
    """Does the local model name a fraud, not merely bulk mail?

    Three ways in, because the field is younger than some of the models that answer here: the
    explicit flag, the category, and as a last resort the wording of the reason. The last one
    only above `_BETRUG_TEXT_AB`, so that a model mentioning the word in order to rule it out
    does not produce the opposite verdict.
    """
    if yes(cls.get("betrug")):
        return True
    if str(cls.get("category") or "").strip().lower() in ("phishing", "betrug"):
        return True
    reason = str(cls.get("spam_reason") or "")
    return model >= _FRAUD_TEXT_FROM and bool(_FRAUD_WORDS.search(reason))


async def judge(db: AsyncSession, owner_id: int | None, payload: dict, *,
                     cls: dict, rule=None) -> dict:
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
    active = (await get_setting(db, ACTIVE_KEY, _DEFAULT[ACTIVE_KEY])) == "1"

    if rule is None:
        rule = evaluate(payload, my_addresses=await my_addresses(db),
                         known_domains=await known_domains(db, owner_id),
                         nonbusiness_domains=await nonbusiness_domains(db),
                         body=mail_text(payload))
    subject = str(payload.get("subject") or "")

    # The verdict of the local model, pulled forward: both the contact acquittal below and
    # the memory need to know whether a fraud was named, and both stand before the place
    # where the score is put together.
    model = float(cls.get("spam_score") or 0.0)
    if str(cls.get("category") or "").lower() in ("spam", "phishing", "werbung"):
        model = max(model, 0.6)
    fraud = _model_fraud(cls, model)

    # Boss scam: the display name is a known contact but the address is not theirs.
    # Technically there is nothing wrong with such a mail; only the contact list gives it
    # away, which is why this check stands here and not in the rule based part.
    if rule.sender_name:
        victim = await named_collision(db, owner_id, rule.sender_name, rule.sender_email)
        if victim:
            rule.hits("namens_kollision",
                          f"it passes itself off as \u201c{victim}\u201d but writes from "
                          f"{rule.sender_email or 'an unknown address'}")
            rule.score = min(1.0, rule.score)

    # --- A known sender -------------------------------------------------------
    # The address is always checked, the domain only when it says anything at all: a contact
    # at gmx.de does not exonerate all other gmx addresses.
    hits = await contact_hits(
        db, owner_id, rule.sender_email,
        "" if rule.sender_domain in FREEMAIL_DOMAINS else rule.sender_domain)
    # A known sender only exonerates as long as the technical side is right. The known name
    # in particular is the rewarding target: a forged mail "from the house bank" is more
    # dangerous than any advertising, and it is noticed exactly here.
    forgery_suspicion = is_forgery_suspicion(rule.signals)
    # `sent` counts like a vault entry: whoever I wrote to myself I demonstrably know, and
    # that is the strongest "wanted" statement a mailbox can produce.
    # A named fraud lifts the acquittal as well: the graph checks the `kontakt` branch BEFORE
    # the automatic one, so a boss scam from a taken over contact address would otherwise walk
    # past everything below.
    known_contact = (hits in ("frontmatter", "sent")
                         and not forgery_suspicion and not fraud)
    if known_contact:
        log.debug("Mail from the known contact %s, no spam suspicion", rule.sender_email)
    if hits and forgery_suspicion:
        rule.reasons.append("a known sender, but the authenticity check failed "
                             "(a forgery is suspected)")
        rule.signals.append("kontakt_gefaelscht")
        rule.score = min(1.0, rule.score + 0.2)
    elif hits in ("body", "domain"):
        # Weaker degree of familiarity: a deduction, not an acquittal.
        rule.score = max(0.0, rule.score - 0.15)

    feature_list = features(rule, subject, contact_hits=hits)
    # The findings of both sources in one shape. The rules have carried key plus plain text
    # since `RuleResult.treffer()`; the model now delivers the same, and only the origin
    # tells them apart. Whoever reads the card, writes the note or counts the statistics
    # reads from here instead of taking each source apart again.
    model_features = list(cls.get("merkmale") or [])
    findings = [{"quelle": "regel", "kennung": sig, "text": text}
               for sig, text in zip(rule.signals, rule.reasons)]
    findings += [{"quelle": "modell", "kennung": str(m.get("kennung") or ""),
                 "text": str(m.get("text") or "")}
                for m in model_features if m.get("kennung")]
    # The model keys join the memory with their own namespace: the format is prefixed anyway
    # (`from:`, `dom:`, `sig:`, `wort:`), so nothing in the learning has to change.
    feature_list += [f"llm:{m['kennung']}" for m in model_features if m.get("kennung")]

    # --- Memory ---------------------------------------------------------------
    learned_score, learned_reasons, safe = await spam_learn.rate(db, owner_id, feature_list)
    has_memory = bool(learned_reasons) or safe
    # How well does the mailbox know this sender? Not the score but the experience: "wanted
    # 286 times, never spam" is something other than "seen three times".
    trusted = await spam_learn.sender_trusted(db, owner_id, rule.sender_email)
    # And whether somebody has already contradicted explicitly. That weighs more than any
    # statistic: whoever says "not spam" twice and is asked again the third time is right to
    # consider the detection broken.
    contradicted = await spam_learn.already_contradicted(db, owner_id, rule.sender_email)

    score = _mix(rule.score, model, learned_score if has_memory else None)

    # A subscribed newsletter is not spam. Without this brake, order confirmations and
    # invoices would wander into the spam folder along with the advertising.
    if rule.is_newsletter and not forgery_suspicion:
        score = min(score, 0.4)

    # Here the model wins, against the mixture and against the newsletter brake. Whoever asks
    # a model whether this is fraud and then averages its answer away with two calmer voices
    # need not have asked. Deliberately AFTER the brake: a phish that hangs a List-Unsubscribe
    # under its footer counts as a "newsletter" to the rules, and the cap of 0.4 would take
    # back exactly the verdict this is about.
    if fraud:
        score = round(max(score, model, _FRAUD_SCORE), 3)

    # Contradiction between model and memory: ask, do not clear away.
    #
    # The model errs differently from the memory. With a sender this mailbox knows a hundred
    # times over as wanted and never as spam, "brand abuse" is the less likely explanation —
    # but certain it is not either. So nobody decides alone: the mail goes into the question.
    #
    # The case of 2026-08-20: a real PayPal receipt (`service@paypal.de`, wanted 282 times)
    # was rated by the model as brand phishing and cleared away automatically — and because a
    # moved mail gets a new number when recalled, the game started over with every recall.
    dispute = bool(fraud and trusted and not forgery_suspicion)
    reasons_dispute = ""
    if dispute and contradicted:
        # Decided is decided. Only the authenticity check still overrides that, and it stands
        # in `forgery_suspicion` — otherwise a sender released once would be a free pass for
        # anyone using their name.
        score = min(score, _DECIDED_SCORE)
        reasons_dispute = (f"{rule.sender_email} was once explicitly decided here to be "
                          f"no spam")
    elif dispute:
        score = min(score, _DISPUTE_SCORE)
        reasons_dispute = (f"the model takes it for fraud, but this mailbox knows "
                          f"{rule.sender_email} as wanted \u2014 hence the question")

    reasons = list(rule.reasons)
    if reasons_dispute:
        reasons.append(reasons_dispute)
    if fraud:
        reasons.append("the local model recognises an attempted fraud")
    if cls.get("spam_reason"):
        reasons.append(str(cls["spam_reason"])[:200])
    reasons.extend(learned_reasons)

    # The memory may decide alone when it agrees about the sender, which is exactly what the
    # learning is for. Otherwise the same question would stand forever. The consequence is
    # drawn by the graph; here stands only THAT the matter is settled and how.
    settled = bool(safe and not forgery_suspicion)
    # A named fraud lifts the "wanted" of the memory, not its "spam": what is dangerous here
    # is the acquittal. Whoever wrote to me three times harmlessly can have their account
    # taken over on the fourth, and the memory would wave exactly that mail through. The
    # condition keeps the `geklaert_spam` path intact, which clears away regardless of the
    # auto threshold.
    if settled and fraud and learned_score < 0.5:
        settled = False
    # …unless the mailbox really knows the sender. Then it stays at the dispute (a question)
    # instead of the silent clearing away — see above.
    if dispute:
        settled = False
    # The verdict of one's own mail server stands for itself. In the weighted mixture it
    # would go under: with rule = 1.0 and a silent model, even a mail the own server gives 13
    # spam points to lands at ~0.55, which would put an auto threshold out of reach. Whoever
    # asks their own infrastructure and then does not believe it need not have asked; the
    # recall card remains the safety net.
    serververdict = any(str(sig).startswith("server_spam") or sig == "betreff_spam_markiert"
                       for sig in rule.signals)
    recipient = rule.recipients[0] if rule.recipients else ""
    verdict_row = {
        "aktiv": active,
        "score": score,
        "rule_score": rule.score,
        "model_score": model,
        "learned_score": learned_score if has_memory else 0.0,
        "settled": settled,
        "serverurteil": serververdict,
        "modellurteil": fraud,
        # What the mail was classified as. Comes from the model, and a named fraud is at
        # least "phishing" even when the model called it something vaguer. This is what the
        # statistics group by, so it is deliberately a value and not a flag: a new kind
        # appears there without a line of code.
        "art": ("phishing" if fraud and str(cls.get("category") or "").lower()
                not in ("phishing", "betrug") else
                str(cls.get("category") or "").strip().lower() or "unbekannt"),
        "befunde": findings,
        # Ready made line for a note or a card: the plain texts, in reading order.
        "findings_text": " · ".join(b["text"] for b in findings if b["text"])[:600],
        "settled_verdict": ("spam" if learned_score >= 0.5 else "ham") if settled else "",
        "bekannter_kontakt": known_contact,
        "faelschungsverdacht": forgery_suspicion,
        "reasons": reasons[:12],
        "features": feature_list,
        "sender_email": rule.sender_email[:320],
        "sender_domain": rule.sender_domain[:255],
        "recipient": recipient[:320],
        "subject": subject[:500],
        "account": str(payload.get("account") or ""),
        "folder": str(payload.get("folder") or ""),
        "uid": payload.get("uid") if isinstance(payload.get("uid"), int) else None,
        # The thresholds travel into the verdict: the branch stands in the graph, and it
        # should be able to check against the setting of NOW without reading the database.
        "frage_ab": await _number(db, QUESTION_FROM_KEY),
        "sofort_ab": await _number(db, IMMEDIATE_FROM_KEY),
        "auto_ab": await _number(db, AUTO_FROM_KEY),
        # Same reason as the thresholds: the branch in front of the reporting step reads
        # it out of the context instead of asking the database itself.
        "auto_melden": str(await get_setting(
            db, AUTO_REPORT_KEY, _DEFAULT[AUTO_REPORT_KEY])).strip().lower()
            not in ("0", "false", "nein", "aus"),
    }
    log.info("Spam verdict (%.2f: rule=%.2f model=%.2f learnt=%.2f, resolved=%s, fraud=%s, "
             "kind=%s) from %s",
             score, rule.score, model, learned_score, verdict_row["settled_verdict"] or "nein",
             fraud, verdict_row["art"], rule.sender_email)
    return verdict_row


async def create(db: AsyncSession, owner_id: int | None, verdict_row: dict, *,
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
        account=str(verdict_row.get("account") or ""),
        folder=str(verdict_row.get("folder") or ""),
        uid=verdict_row.get("uid") if isinstance(verdict_row.get("uid"), int) else None,
        sender_email=str(verdict_row.get("sender_email") or "")[:320],
        sender_domain=str(verdict_row.get("sender_domain") or "")[:255],
        recipient=str(verdict_row.get("recipient") or "")[:320],
        subject=str(verdict_row.get("subject") or "")[:500],
        rule_score=float(verdict_row.get("rule_score") or 0.0),
        model_score=float(verdict_row.get("model_score") or 0.0),
        learned_score=float(verdict_row.get("learned_score") or 0.0),
        score=float(verdict_row.get("score") or 0.0),
        reasons=list(verdict_row.get("reasons") or [])[:12],
        features=list(verdict_row.get("features") or []),
        kind=str(verdict_row.get("art") or "")[:40],
        findings=list(verdict_row.get("befunde") or [])[:20],
        status="pending")
    db.add(verdict)
    await db.flush()
    return verdict


# --- Karten ------------------------------------------------------------------------

def karte(verdict: SpamVerdict, *, predecided: bool = False,
          recoverable: bool = False) -> tuple[str, str]:
    """(Title, text) of the single card.

    Three shapes: the question, the learned case (already decided) and the automatically
    moved one (already happened, with a way back). The third is the only one where a human
    objects afterwards, which is why it says first what HAS happened.
    """
    if recoverable:
        header = "🗑 Sorted out automatically"
    else:
        header = "🚩 Suspected spam" if not predecided else "🚩 Spam (learned)"
    title = f"{header} ({verdict.score:.2f})"
    lines = [
        f"From:    {verdict.sender_email or '?'}",
        f"To:      {verdict.recipient or '?'}",
        f"Subject: {verdict.subject or '(no subject)'}",
    ]
    if verdict.reasons:
        lines.append("")
        lines.append("Reason:  " + "\n         · ".join(verdict.reasons[:5]))
    lines.append("")
    if recoverable:
        lines.append("Moved without asking \u2014 the score is above the automatic threshold. "
                      "One press brings it back and remembers the sender.")
    elif predecided:
        lines.append("Moved \u2014 the sender counts as settled.")
    else:
        lines.append("Suggestion: → move it to the spam folder")
    return title, "\n".join(lines)


async def report(db: AsyncSession, owner_id: int | None, verdict: SpamVerdict, *,
                 immediate: bool, predecided: bool = False,
                 recoverable: bool = False) -> None:
    """Put a single card into the notifications (the bot delivers it and attaches the
    buttons)."""
    if not owner_id:
        return
    owner = await db.get(User, owner_id)
    if owner is None or not owner.telegram_chat_id:
        return
    title, text = karte(verdict, predecided=predecided, recoverable=recoverable)
    # The kind decides which buttons the bot attaches: a question gets two, an already
    # executed sorting exactly one, the way back.
    db.add(Notification(
        user_id=owner_id, spam_verdict_id=verdict.id,
        kind="spam_auto" if recoverable else "spam_review",
        chat_id=owner.telegram_chat_id, title=title[:200], body=text[:4000]))
    if not immediate:
        log.debug("Verdict #%s is waiting for the collection card", verdict.id)


async def digest_due(db: AsyncSession) -> int:
    """Bundle open suspected cases below the immediate threshold into ONE card.

    Driven by the scheduler. Without bundling, half the day would consist of Telegram
    messages at any notable spam volume, and whoever is asked every three minutes eventually
    presses a button at random.
    """
    beat = int(await _number(db, DIGEST_MIN_KEY))
    if beat <= 0:
        return 0
    limit = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=beat)
    open_ones = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.status == "pending", SpamVerdict.digest_batch.is_(None),
        SpamVerdict.created_at <= limit).order_by(SpamVerdict.id).limit(50))).scalars().all()
    # Cases reported immediately already hang off a card of their own; they must not be
    # asked about twice.
    already_reported = set((await db.execute(select(Notification.spam_verdict_id).where(
        Notification.spam_verdict_id.in_([v.id for v in open_ones] or [0])))).scalars().all())
    open_ones = [v for v in open_ones if v.id not in already_reported]
    if not open_ones:
        return 0

    by_owner: dict[int | None, list[SpamVerdict]] = {}
    for v in open_ones:
        by_owner.setdefault(v.owner_user_id, []).append(v)

    sent = 0
    for owner_id, cases in by_owner.items():
        if not owner_id:
            continue
        owner = await db.get(User, owner_id)
        if owner is None or not owner.telegram_chat_id:
            continue
        batch = uuid.uuid4().hex[:12]
        lines = []
        for i, v in enumerate(cases, 1):
            reason = v.reasons[0] if v.reasons else "odd"
            lines.append(f"{i}. {v.sender_email or '?'} ({v.score:.2f})\n"
                          f"   \u201c{(v.subject or '(no subject)')[:70]}\u201d\n"
                          f"   {reason}")
            v.digest_batch = batch
        db.add(Notification(
            # The reference points at the first case of the collection; through its
            # `digest_batch` the bot finds the whole set again. The identifier itself does
            # not fit into the callback of a button when an action already stands there.
            user_id=owner_id, spam_verdict_id=cases[0].id, kind="spam_digest",
            chat_id=owner.telegram_chat_id,
            title=(await tr(db, "server.notify.spam_suspicion", owner.locale,
                            count=len(cases)))[:200],
            body="\n".join(lines)[:4000]))
        sent += 1
    await db.commit()
    return sent


# --- Decision and execution ---------------------------------------------------------

async def decide(db: AsyncSession, verdict: SpamVerdict, is_spam: bool, *,
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
        return await _an_flow_report(db, verdict, is_spam, decided_by=decided_by)
    await commit(db, verdict, is_spam, decided_by=decided_by)
    result = await imap_action(verdict, is_spam)
    verdict.action_result = result[:2000]
    await db.commit()
    return result


async def commit(db: AsyncSession, verdict: SpamVerdict, is_spam: bool, *,
                        decided_by: str = "telegram") -> None:
    """Record the verdict and learn from it (without IMAP). Does NOT commit."""
    before = verdict.status if verdict.status in ("spam", "ham") else ""
    verdict.status = "spam" if is_spam else "ham"
    verdict.decided_by = decided_by
    verdict.decided_at = dt.datetime.now(tz=dt.timezone.utc)
    await spam_learn.remember(db, verdict, is_spam, before=before)

    if not is_spam:
        # "Not spam" is more than a no: the sender should not even stand out in future. The
        # learned rule takes hold before the assessment already.
        from .assistant_policy import upsert_policy
        if verdict.sender_email and verdict.sender_domain not in FREEMAIL_DOMAINS:
            await upsert_policy(db, verdict.owner_user_id, match_kind="sender",
                                match_value=verdict.sender_email, auto_approve=False)


async def _an_flow_report(db: AsyncSession, verdict: SpamVerdict, is_spam: bool, *,
                            decided_by: str) -> str:
    """Give the answer to the waiting flow and advance it.

    The decision then stands in the context (`spam.entschieden`), and the graph reads it at
    its branch and executes the IMAP action. If no approval step is waiting (flow aborted,
    instance gone), the answer is executed the direct way: an answered question must not run
    into nothing.
    """
    from ..models.workflow import WorkflowInstance
    from .workflow_engine import advance, decide_approval

    inst = await db.get(WorkflowInstance, verdict.workflow_instance_id)
    decided = await decide_approval(
        db, inst, "approved" if is_spam else "rejected", actor_id=None,
        context={"spam": {**((inst.context or {}).get("spam") or {}),
                          "entschieden": "spam" if is_spam else "ham",
                          "entschieden_von": decided_by}},
    ) if inst is not None else False
    if not decided:
        log.warning("Verdict #%s: no waiting flow (instance %s), executed directly",
                    verdict.id, verdict.workflow_instance_id)
        await commit(db, verdict, is_spam, decided_by=decided_by)
        result = await imap_action(verdict, is_spam)
        verdict.action_result = result[:2000]
        await db.commit()
        return result

    await db.commit()
    await advance(inst.id)
    # The flow has meanwhile written in a session of its own; for the reply to the human its
    # result counts, not the state from before.
    await db.refresh(verdict)
    return verdict.action_result or "handed over to the flow"


async def imap_action(verdict: SpamVerdict, is_spam: bool) -> str:
    """Move mail through `imap-mcp`. Errors are reported, not raised: a mail that cannot be
    moved must not undo the decision."""
    if not (verdict.account and verdict.folder and verdict.uid):
        return "keine Mailkennung hinterlegt — nichts verschoben"
    tool = "mark_spam" if is_spam else "mark_not_spam"
    try:
        result = await call_tool(IMAP_MCP_URL, tool, {
            "account": verdict.account, "folder": verdict.folder, "uid": verdict.uid})
    except McpError as exc:
        log.warning("%s failed for verdict #%s: %s", tool, verdict.id, exc)
        return f"nicht verschoben: {exc}"
    text = result_text(result) or "verschoben"
    log.info("%s for verdict #%s: %s", tool, verdict.id, text)
    return text


async def reclaim(db: AsyncSession, verdict: SpamVerdict, *,
                       decided_by: str = "telegram") -> str:
    """An automatically sorted out mail back into the inbox. Returns a plain text result.

    The objection to auto-moving, and it is more than a retreat: the sender is learned as
    wanted and gets a rule so that the same error does not happen again tomorrow. Without
    that, stage 2 would be a machine that makes the same mistake any number of
    times.
    """
    if verdict.status not in ("spam", "pending"):
        return f"schon erledigt ({verdict.status})"
    await commit(db, verdict, False, decided_by=decided_by)
    result = await imap_action(verdict, False)
    verdict.action_result = result[:2000]
    await db.commit()
    log.info("Verdict #%s taken back: %s", verdict.id, result)
    return result


async def decide_batch(db: AsyncSession, batch: str, is_spam: bool, *,
                           decided_by: str = "telegram") -> tuple[int, int]:
    """Decide all open cases of a digest card at once. Returns (done, errors)."""
    cases = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.digest_batch == batch, SpamVerdict.status == "pending"))).scalars().all()
    error = 0
    for v in cases:
        result = await decide(db, v, is_spam, decided_by=decided_by)
        if result.startswith("nicht verschoben"):
            error += 1
    return len(cases), error
