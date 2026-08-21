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
_MAX_WEIGHT = 1.6
# From this many unanimous observations on, a sender counts as settled, and then the memory
# decides alone and nobody is asked again.
_SAFE_FROM = 3

# Kinds of feature that may carry on their own. A subject word never may.
#
# `to:` steht hier bewusst NICHT mehr — die eigene Adresse ist kein Merkmal der Mail,
# sondern eines des Postfachs: Sie steht in jeder erwünschten genauso wie in jedem Spam.
# Weil man aber fast nur Spam ausdrücklich entscheidet, sammelt sie einseitig Spam-Zähler
# und wird zur selbsterfüllenden Regel. Fall vom 2026-08-20: Ein PayPal-Beleg an einen
# frischen Alias (`to:` 4:0 Spam) wurde weggeräumt, obwohl derselbe Absender 282-mal als
# erwünscht gelernt war — `to:` löste „sicher" aus und übersprang alles andere.
_STARKE_KINDS = ("from:", "dom:")

# Als Merkmal darf die Empfängeradresse mitzählen (ein Wegwerf-Alias, der wirklich nur
# Werbung bekommt, sagt etwas), aber sie entscheidet nichts allein.
_SCHWACHE_STARKE = ("to:",)


def _atleast(feature: str) -> int:
    """How many observations this feature needs in order to have a say.

    Die eigene Empfängeradresse braucht mehr als ein Absender: Sie steht in JEDER Mail an
    dieses Postfach, und weil fast nur Spam ausdrücklich entschieden wird, sammelt sie
    einseitig Zähler, ohne etwas über die einzelne Mail zu sagen.
    """
    if (feature or "").startswith(_SCHWACHE_STARKE):
        return _MIN_EVIDENZ_STARK * 4
    return (_MIN_EVIDENZ_STARK if (feature or "").startswith(_STARKE_KINDS)
            else _MIN_EVIDENZ_SCHWACH)


def _logodds(spam: int, ham: int, basis: float) -> float:
    """The weight of a feature in log odds, smoothed and capped."""
    p = (spam + _ALPHA * basis) / (spam + ham + _ALPHA)
    p = min(max(p, 1e-4), 1 - 1e-4)
    return max(-_MAX_WEIGHT, min(_MAX_WEIGHT, math.log(p / (1 - p)) - math.log(basis / (1 - basis))))


async def _baserate(db: AsyncSession, owner_id: int | None) -> float:
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


async def rate(db: AsyncSession, owner_id: int | None,
                   features: list[str]) -> tuple[float, list[str], bool]:
    """(score, reasons, certain) from what has been learned.

    `score` 0..1 as with the other partial verdicts. `certain` means: a strong feature
    (sender, alias, domain) has been decided unanimously often enough, and then no question
    is needed any more, which is the actual purpose of the learning.

    Without observations (base rate, [], False) comes back, so no opinion, not "innocent".
    The caller weights that accordingly.
    """
    if not features:
        return 0.5, [], False
    basis = await _baserate(db, owner_id)
    rows = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.owner_user_id == owner_id,
        SpamFeatureStat.feature.in_(features)))).scalars().all()
    if not rows:
        return basis, [], False

    sum_total = math.log(basis / (1 - basis))
    contributions: list[tuple[float, str]] = []
    safe = False
    agreed: list[SpamFeatureStat] = []
    for row in rows:
        total = (row.spam_count or 0) + (row.ham_count or 0)
        if total < _atleast(row.feature):
            continue
        weight = _logodds(row.spam_count or 0, row.ham_count or 0, basis)
        sum_total += weight
        if abs(weight) > 0.2:
            contributions.append((weight, _explanation(row)))
        # Unanimous verdict about a strong feature means settled.
        if row.feature.startswith(_STARKE_KINDS) and total >= _SAFE_FROM:
            if row.ham_count == 0 or row.spam_count == 0:
                safe = True
                agreed.append(row)

    # „Sicher" heißt einig — und einig ist man nur, wenn niemand widerspricht. Zeigt ein
    # zweites starkes Merkmal in die Gegenrichtung (derselbe Absender 282-mal erwünscht,
    # während ein anderes Merkmal dreimal auf Spam steht), ist die Sache eben nicht klar.
    if safe and len(agreed) > 1:
        directions = {r.spam_count == 0 for r in agreed}
        if len(directions) > 1:
            safe = False
    score = 1 / (1 + math.exp(-sum_total))
    contributions.sort(key=lambda b: abs(b[0]), reverse=True)
    return round(score, 3), [text for _, text in contributions[:4]], safe


def _explanation(row: SpamFeatureStat) -> str:
    """Feature counters turned into a sentence that can stand in the Telegram card."""
    kind, _, value = (row.feature or "").partition(":")
    label = {
        "from": f"Absender {value}", "dom": f"Domain {value}", "to": f"Alias {value}",
        "sig": f"Merkmal {value}", "wort": f"Betreff-Wort „{value}“",
    }.get(kind, row.feature)
    s, h = row.spam_count or 0, row.ham_count or 0
    if s and not h:
        return f"{label}: bisher {s}× Spam"
    if h and not s:
        return f"{label}: bisher {h}× erwünscht"
    return f"{label}: {s}× Spam / {h}× erwünscht"


async def sender_trusted(db: AsyncSession, owner_id: int | None, sender: str,
                            *, ab: int = 20, share: float = 0.95) -> bool:
    """Kennt dieses Postfach den Absender als erwünscht — deutlich und über lange Zeit?

    Nicht der gelernte Score, sondern die Erfahrung dahinter: „286-mal erwünscht" ist etwas
    anderes als „dreimal gesehen und für gut befunden". Nur bei dieser Deutlichkeit darf das
    Gedächtnis dem Modell widersprechen.

    Gefragt ist das Verhältnis, nicht die Makellosigkeit: Ein einziger Fehlgriff — ein
    versehentliches „ist Spam", eine gefälschte Mail unter dem Namen — darf nicht 286 gute
    Beobachtungen aufheben. Genau daran scheiterte die erste Fassung dieser Bremse.
    """
    if not sender:
        return False
    row = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.owner_user_id == owner_id,
        SpamFeatureStat.feature == f"from:{sender}"))).scalars().first()
    if row is None:
        return False
    ham, spam = row.ham_count or 0, row.spam_count or 0
    return ham >= ab and ham >= share * (ham + spam)


async def already_contradicted(db: AsyncSession, owner_id: int | None, sender: str) -> bool:
    """Hat ein Mensch für diesen Absender schon einmal ausdrücklich „kein Spam" gesagt?

    Das ist die stärkste Auskunft, die es gibt — stärker als jede Statistik und stärker als
    das Modell. Wer zweimal widerspricht und beim dritten Mal wieder gefragt wird, hat recht,
    wenn er die Erkennung für kaputt hält.

    Gezählt werden nur menschliche Entscheidungen: `auto` ist die Maschine, die sich selbst
    bestätigt.
    """
    from ..models.assistant import SpamVerdict

    if not sender:
        return False
    row = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.owner_user_id == owner_id,
        SpamVerdict.sender_email == sender,
        SpamVerdict.status == "ham",
        SpamVerdict.decided_by.notin_(("auto", ""))).limit(1))).scalars().first()
    return row is not None


async def features_count(db: AsyncSession, owner_id: int | None, features: list[str],
                           is_spam: bool, *, before: str = "") -> int:
    """Take features into the counters. Does NOT commit. Returns the number of counted features.

    Separated from `merken`, because not every piece of learning material comes from a
    question: what lies in the spam folder or has stood in the inbox for years is a decision
    of a human as well, only one that never went through Traccoon (see `spam_bootstrap`).
    """
    features = [m for m in features if isinstance(m, str) and m]
    if not features:
        return 0
    now = dt.datetime.now(tz=dt.timezone.utc)
    existing = {
        row.feature: row for row in (await db.execute(select(SpamFeatureStat).where(
            SpamFeatureStat.owner_user_id == owner_id,
            SpamFeatureStat.feature.in_(features)))).scalars().all()
    }
    for feature in features:
        row = existing.get(feature)
        if row is None:
            row = SpamFeatureStat(owner_user_id=owner_id, feature=feature)
            db.add(row)
            existing[feature] = row
        if before == "spam":
            row.spam_count = max(0, (row.spam_count or 0) - 1)
        elif before == "ham":
            row.ham_count = max(0, (row.ham_count or 0) - 1)
        if is_spam:
            row.spam_count = (row.spam_count or 0) + 1
        else:
            row.ham_count = (row.ham_count or 0) + 1
        row.last_seen_at = now
    return len(features)


async def remember(db: AsyncSession, verdict: SpamVerdict, is_spam: bool,
                 *, before: str = "") -> None:
    """Take one decision into the counters. Does NOT commit.

    `vorher` is the previous status of the same row ('spam'/'ham'/''). If the human changes
    their mind, the old counting is taken back; otherwise the error would stay in the memory
    forever and have a say with every future mail.
    """
    count = await features_count(db, verdict.owner_user_id, list(verdict.features or []),
                                    is_spam, before=before)
    if count:
        log.info("Spam memory: %d features from verdict #%s (%s)",
                 count, verdict.id, "spam" if is_spam else "ham")


async def examples(db: AsyncSession, owner_id: int | None, limit: int = 6) -> list[str]:
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
        verdict = "SPAM" if r.status == "spam" else "KEIN SPAM"
        out.append(f"- von {r.sender_email or '?'} · Betreff „{(r.subject or '')[:60]}“ → {verdict}")
    return out
