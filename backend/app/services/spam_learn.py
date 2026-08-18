"""Das Gedächtnis der Spam-Erkennung: aus entschiedenen Fällen lernen.

Ohne dieses Modul wäre die Erkennung bei jeder Mail gleich schlau wie am ersten Tag — der
Mensch würde dieselbe Frage über denselben Absender endlos beantworten. Jede Bestätigung
(„ist Spam") und jede Ablehnung („kein Spam") erhöht hier Zähler je Merkmal, und **jede**
folgende Mail wird gegen diese Zähler gehalten, bevor irgendjemand gefragt wird.

Verfahren: naives Bayes über Merkmale (Absender, Domain, angeschriebener Alias, technische
Signale, Betreff-Wörter) mit Laplace-Glättung. Bewusst kein trainiertes Modell:

* **nachvollziehbar** — man kann nachsehen, welches Merkmal wie oft wie entschieden wurde;
* **sofort wirksam** — die nächste Mail profitiert, kein Trainingslauf dazwischen;
* **korrigierbar** — eine geänderte Entscheidung zählt sauber zurück statt nachzuwirken.

Die Zähler sind owner-scoped: gelernt wird, was *dieser* Mensch für Spam hält. Genau darum
geht es — ein Newsletter, den einer bestellt hat und der andere nie wollte, ist objektiv
nicht zu entscheiden.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import SpamFeatureStat, SpamVerdict

log = logging.getLogger("traccoon.spam")

# Glättung: ein einzelner Treffer soll nicht sofort ein Urteil sein.
_ALPHA = 1.0
# Ab wie vielen Beobachtungen ein Merkmal mitreden darf — nach Art getrennt.
#
# Eine einzelne Entscheidung über eine konkrete Adresse ist eine Aussage: „von dem will ich
# nichts". Sie muss ab der nächsten Mail wirken, sonst beantwortet der Mensch dieselbe Frage
# über denselben Absender ein zweites Mal, und genau das soll aufhören.
#
# Ein einzelnes Betreff-Wort ist dagegen keine Aussage, sondern Zufall — „Rechnung" steht in
# echter Post wie in Betrugsmails. Solche Merkmale brauchen Wiederholung, sonst zieht das
# erste zufällige Wort einer Woche jede spätere Beurteilung schief.
_MIN_EVIDENZ_STARK = 1
_MIN_EVIDENZ_SCHWACH = 2
# Deckel je Merkmal in Log-Odds. Ohne ihn reißt ein einziges, oft gesehenes Wort
# ("rechnung") das Gesamturteil an sich.
_MAX_GEWICHT = 1.6
# Ab so vielen einhelligen Beobachtungen gilt ein Absender als geklärt — dann entscheidet
# das Gedächtnis allein und es wird nicht erneut gefragt.
_SICHER_AB = 3

# Merkmalsarten, die für sich allein tragen dürfen. Ein Betreff-Wort darf das nie.
_STARKE_ARTEN = ("from:", "to:", "dom:")


def _mindestens(feature: str) -> int:
    """Wie viele Beobachtungen dieses Merkmal braucht, um mitzureden."""
    return (_MIN_EVIDENZ_STARK if (feature or "").startswith(_STARKE_ARTEN)
            else _MIN_EVIDENZ_SCHWACH)


def _logodds(spam: int, ham: int, basis: float) -> float:
    """Gewicht eines Merkmals in Log-Odds, geglättet und gedeckelt."""
    p = (spam + _ALPHA * basis) / (spam + ham + _ALPHA)
    p = min(max(p, 1e-4), 1 - 1e-4)
    return max(-_MAX_GEWICHT, min(_MAX_GEWICHT, math.log(p / (1 - p)) - math.log(basis / (1 - basis))))


async def _basisrate(db: AsyncSession, owner_id: int | None) -> float:
    """Anteil Spam an allen entschiedenen Fällen — der Ausgangspunkt ohne jedes Merkmal.

    Gedeckelt auf [0.1, 0.9]: wer zwei Wochen nur Spam bestätigt hat, soll nicht in eine
    Welt geraten, in der jede Mail von vornherein schuldig ist.
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
    """(score, gruende, sicher) aus dem Gelernten.

    `score` 0..1 wie bei den anderen Teilurteilen. `sicher` heißt: ein starkes Merkmal
    (Absender/Alias/Domain) ist oft genug einhellig entschieden worden — dann braucht es
    keine Rückfrage mehr, das ist der eigentliche Zweck des Lernens.

    Ohne Beobachtungen kommt (Basisrate, [], False) zurück — also keine Meinung, nicht
    „unschuldig". Der Aufrufer gewichtet das entsprechend.
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
        # Einhelliges Urteil über ein starkes Merkmal → geklärt.
        if row.feature.startswith(_STARKE_ARTEN) and gesamt >= _SICHER_AB:
            if row.ham_count == 0 or row.spam_count == 0:
                sicher = True

    score = 1 / (1 + math.exp(-summe))
    beitraege.sort(key=lambda b: abs(b[0]), reverse=True)
    return round(score, 3), [text for _, text in beitraege[:4]], sicher


def _erklaerung(row: SpamFeatureStat) -> str:
    """Merkmal-Zähler → ein Satz, der in der Telegram-Karte stehen kann."""
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
    """Merkmale in die Zähler übernehmen. Committet NICHT. → Anzahl gezählter Merkmale.

    Getrennt von `merken`, weil nicht jeder Lehrstoff aus einer Rückfrage stammt: was im
    Spam-Ordner liegt oder seit Jahren im Posteingang steht, ist ebenfalls eine Entscheidung
    eines Menschen — nur eine, die nie durch Traccoon lief (siehe `spam_bootstrap`).
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
    """Eine Entscheidung in die Zähler übernehmen. Committet NICHT.

    `vorher` ist der bisherige Status derselben Zeile ('spam'/'ham'/''). Ändert der Mensch
    seine Meinung, wird die alte Zählung zurückgenommen — sonst bliebe der Irrtum für
    immer im Gedächtnis stehen und würde bei jeder künftigen Mail mitreden.
    """
    anzahl = await merkmale_zaehlen(db, verdict.owner_user_id, list(verdict.features or []),
                                    ist_spam, vorher=vorher)
    if anzahl:
        log.info("Spam-Gedächtnis: %d Merkmale aus Urteil #%s (%s)",
                 anzahl, verdict.id, "spam" if ist_spam else "ham")


async def beispiele(db: AsyncSession, owner_id: int | None, limit: int = 6) -> list[str]:
    """Kurze Beispielzeilen der letzten Entscheidungen für den Prompt des lokalen Modells.

    Die Zähler oben wirken auf die Merkmale; das Modell sieht davon nichts. Damit auch
    seine Einschätzung mitwandert, bekommt es die jüngsten Urteile als Beispiele — knapp
    gehalten (Absender, Betreff, Entscheidung), damit der Prompt nicht wächst und nichts
    Persönliches mehr enthält als nötig.
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
