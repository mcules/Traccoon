"""The memory of the spam detection: learning from decided cases.

Without this module the detection would be as clever with every mail as on the first day,
and the human would answer the same question about the same sender endlessly. Every
confirmation ("is spam") and every rejection ("not spam") raises counters per feature here,
and **every** following mail is held against these counters before anybody is asked.

Method: naive Bayes over features (sender, domain, addressed alias, technical signals,
subject words) with Laplace smoothing. Deliberately no trained model:

* **traceable**: one can look up which feature was decided how often and how;
* **effective immediately**: the next mail profits, with no training run in between;
* **correctable**: a changed decision counts back cleanly instead of lingering.

The counters are owner-scoped: what is learned is what *this* person considers spam. That
is exactly the point: a newsletter one person subscribed to and another never wanted cannot
be decided objectively.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import SpamFeatureStat, SpamVerdict

log = logging.getLogger("traccoon.spam")

# Smoothing: a single hit should not be a verdict right away.
_ALPHA = 1.0
# From how many observations a feature may have a say, separated by kind.
#
# A single decision about a concrete address is a statement: "I want nothing from that one".
# It has to take effect from the next mail on, because otherwise the human answers the same
# question about the same sender a second time, and exactly that should stop.
#
# A single subject word on the other hand is not a statement but chance: "invoice" stands in
# real post as in fraud mails. Such features need repetition, because otherwise the first
# random word of a week skews every later assessment.
_MIN_EVIDENZ_STARK = 1
_MIN_EVIDENZ_SCHWACH = 2
# Cap per feature in log odds. Without it a single often seen word ("rechnung") pulls the
# overall verdict to itself.
_MAX_GEWICHT = 1.6
# From this many unanimous observations on, a sender counts as settled, and then the memory
# decides alone and nobody is asked again.
_SICHER_AB = 3

# Kinds of feature that may carry on their own. A subject word never may.
_STARKE_ARTEN = ("from:", "to:", "dom:")


def _mindestens(feature: str) -> int:
    """How many observations this feature needs in order to have a say."""
    return (_MIN_EVIDENZ_STARK if (feature or "").startswith(_STARKE_ARTEN)
            else _MIN_EVIDENZ_SCHWACH)


def _logodds(spam: int, ham: int, basis: float) -> float:
    """The weight of a feature in log odds, smoothed and capped."""
    p = (spam + _ALPHA * basis) / (spam + ham + _ALPHA)
    p = min(max(p, 1e-4), 1 - 1e-4)
    return max(-_MAX_GEWICHT, min(_MAX_GEWICHT, math.log(p / (1 - p)) - math.log(basis / (1 - basis))))


async def _basisrate(db: AsyncSession, owner_id: int | None) -> float:
    """Share of spam among all decided cases, the starting point without any feature.

    Capped to [0.1, 0.9]: whoever has confirmed nothing but spam for two weeks should not end
    up in a world in which every mail is guilty from the outset.
    """
    rows = (await db.execute(select(SpamVerdict.status).where(
        SpamVerdict.owner_user_id == owner_id,
        SpamVerdict.status.in_(("spam", "ham"))))).scalars().all()
    if len(rows) < 10:
        return 0.5
    spam = sum(1 for r in rows if r == "spam")
    return min(0.9, max(0.1, spam / len(rows)))


async def bewerten(db: AsyncSession, owner_id: int | None,
                   merkmale: list[str]) -> tuple[float, list[str], bool]:
    """(score, reasons, certain) from what has been learned.

    `score` 0..1 as with the other partial verdicts. `certain` means: a strong feature
    (sender, alias, domain) has been decided unanimously often enough, and then no question
    is needed any more, which is the actual purpose of the learning.

    Without observations (base rate, [], False) comes back, so no opinion, not "innocent".
    The caller weights that accordingly.
    """
    if not merkmale:
        return 0.5, [], False
    basis = await _basisrate(db, owner_id)
    rows = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.owner_user_id == owner_id,
        SpamFeatureStat.feature.in_(merkmale)))).scalars().all()
    if not rows:
        return basis, [], False

    summe = math.log(basis / (1 - basis))
    beitraege: list[tuple[float, str]] = []
    sicher = False
    for row in rows:
        gesamt = (row.spam_count or 0) + (row.ham_count or 0)
        if gesamt < _mindestens(row.feature):
            continue
        gewicht = _logodds(row.spam_count or 0, row.ham_count or 0, basis)
        summe += gewicht
        if abs(gewicht) > 0.2:
            beitraege.append((gewicht, _erklaerung(row)))
        # Unanimous verdict about a strong feature means settled.
        if row.feature.startswith(_STARKE_ARTEN) and gesamt >= _SICHER_AB:
            if row.ham_count == 0 or row.spam_count == 0:
                sicher = True

    score = 1 / (1 + math.exp(-summe))
    beitraege.sort(key=lambda b: abs(b[0]), reverse=True)
    return round(score, 3), [text for _, text in beitraege[:4]], sicher


def _erklaerung(row: SpamFeatureStat) -> str:
    """Feature counters turned into a sentence that can stand in the Telegram card."""
    art, _, wert = (row.feature or "").partition(":")
    label = {
        "from": f"Absender {wert}", "dom": f"Domain {wert}", "to": f"Alias {wert}",
        "sig": f"Merkmal {wert}", "wort": f"Betreff-Wort „{wert}“",
    }.get(art, row.feature)
    s, h = row.spam_count or 0, row.ham_count or 0
    if s and not h:
        return f"{label}: bisher {s}× Spam"
    if h and not s:
        return f"{label}: bisher {h}× erwünscht"
    return f"{label}: {s}× Spam / {h}× erwünscht"


async def merkmale_zaehlen(db: AsyncSession, owner_id: int | None, merkmale: list[str],
                           ist_spam: bool, *, vorher: str = "") -> int:
    """Take features into the counters. Does NOT commit. Returns the number of counted features.

    Separated from `merken`, because not every piece of learning material comes from a
    question: what lies in the spam folder or has stood in the inbox for years is a decision
    of a human as well, only one that never went through Traccoon (see `spam_bootstrap`).
    """
    merkmale = [m for m in merkmale if isinstance(m, str) and m]
    if not merkmale:
        return 0
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    vorhanden = {
        row.feature: row for row in (await db.execute(select(SpamFeatureStat).where(
            SpamFeatureStat.owner_user_id == owner_id,
            SpamFeatureStat.feature.in_(merkmale)))).scalars().all()
    }
    for merkmal in merkmale:
        row = vorhanden.get(merkmal)
        if row is None:
            row = SpamFeatureStat(owner_user_id=owner_id, feature=merkmal)
            db.add(row)
            vorhanden[merkmal] = row
        if vorher == "spam":
            row.spam_count = max(0, (row.spam_count or 0) - 1)
        elif vorher == "ham":
            row.ham_count = max(0, (row.ham_count or 0) - 1)
        if ist_spam:
            row.spam_count = (row.spam_count or 0) + 1
        else:
            row.ham_count = (row.ham_count or 0) + 1
        row.last_seen_at = jetzt
    return len(merkmale)


async def merken(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool,
                 *, vorher: str = "") -> None:
    """Take one decision into the counters. Does NOT commit.

    `vorher` is the previous status of the same row ('spam'/'ham'/''). If the human changes
    their mind, the old counting is taken back; otherwise the error would stay in the memory
    forever and have a say with every future mail.
    """
    anzahl = await merkmale_zaehlen(db, verdict.owner_user_id, list(verdict.features or []),
                                    ist_spam, vorher=vorher)
    if anzahl:
        log.info("Spam memory: %d features from verdict #%s (%s)",
                 anzahl, verdict.id, "spam" if ist_spam else "ham")


async def beispiele(db: AsyncSession, owner_id: int | None, limit: int = 6) -> list[str]:
    """Short example lines of the last decisions for the prompt of the local model.

    The counters above act on the features; the model sees nothing of them. So that its
    assessment moves along as well, it gets the most recent verdicts as examples, kept short
    (sender, subject, decision) so that the prompt does not grow and contains no more
    personal data than necessary.
    """
    rows = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.owner_user_id == owner_id,
        SpamVerdict.status.in_(("spam", "ham")),
        SpamVerdict.decided_by != "auto",
    ).order_by(SpamVerdict.decided_at.desc()).limit(limit))).scalars().all()
    out: list[str] = []
    for r in rows:
        urteil = "SPAM" if r.status == "spam" else "KEIN SPAM"
        out.append(f"- von {r.sender_email or '?'} · Betreff „{(r.subject or '')[:60]}“ → {urteil}")
    return out
