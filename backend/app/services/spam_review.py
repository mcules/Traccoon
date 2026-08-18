"""Spam-Beurteilung eingehender Mails — Urteil bilden, nachfragen, ausführen, lernen.

Drei Stimmen entscheiden gemeinsam, keine allein:

1. **Regeln** (`spam_rules`) — technische Tatsachen aus Adressen und Kopfzeilen.
2. **Lokales Modell** (`mail_classify`) — der Text, im Haus beurteilt.
3. **Gedächtnis** (`spam_learn`) — was der Mensch in vergleichbaren Fällen entschieden hat.

Die dritte Stimme ist der Grund, warum die Erkennung mit der Zeit besser wird statt gleich
schlecht zu bleiben: sie wächst mit jeder Bestätigung. Ist sie sich über einen Absender
einig genug, entscheidet sie allein — dann wird nicht mehr gefragt.

Leitplanke über allem: **ein Fehlalarm kostet mehr als ein durchgerutschter Werbebrief.**
Es wird nie gelöscht, nur in den Spam-Ordner verschoben, und im jetzigen Ausbaustand nie
ohne Bestätigung eines Menschen.
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

# --- Stellschrauben (AppSetting, damit sie ohne Neustart änderbar sind) ------------
AKTIV_KEY = "spam_aktiv"                     # '1'/'0'
FRAGE_AB_KEY = "spam_frage_ab"               # ab hier wird überhaupt nachgefragt
SOFORT_AB_KEY = "spam_sofort_ab"             # ab hier sofort einzeln statt Sammel-Karte
AUTO_AB_KEY = "spam_auto_ab"                 # ab hier ohne Rückfrage weg (Rückholfenster)
DIGEST_MIN_KEY = "spam_digest_minuten"       # Takt der Sammel-Karte
MEINE_ADRESSEN_KEY = "spam_meine_adressen"   # eigene Empfangsadressen, Komma-getrennt
# Domains, über die nachweislich kein Vertragswesen läuft. Dort ist jede „Rechnung" eine
# Behauptung — und zwar unabhängig davon, wie die Adresse davor heißt.
KEINE_GESCHAEFTSDOMAINS_KEY = "spam_keine_geschaeftsdomains"

_VORGABE = {
    AKTIV_KEY: "1",
    FRAGE_AB_KEY: "0.45",
    SOFORT_AB_KEY: "0.9",
    # Über 1.0 = aus. Auto-Verschieben ist kein Standard, sondern eine Entscheidung, die
    # ein Mensch nach eigener Messung trifft (siehe `spam_report.rueckschau`).
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
    """Eigene Empfangsadressen/Aliase. Einträge dürfen `*@meine-domain.de` lauten."""
    roh = await get_setting(db, MEINE_ADRESSEN_KEY, "")
    return frozenset(t.strip().lower() for t in roh.replace(";", ",").split(",") if t.strip())


async def geschaeftsfreie_domains(db: AsyncSession) -> frozenset[str]:
    """Domains ohne Vertragswesen (Einstellung). `@` und Groß/Klein werden verziehen."""
    roh = await get_setting(db, KEINE_GESCHAEFTSDOMAINS_KEY, "")
    return frozenset(t.strip().lstrip("@").lower()
                     for t in roh.replace(";", ",").split(",") if t.strip())


def _mischen(regel: float, modell: float, gelernt: float | None) -> float:
    """Drei Teilurteile → ein Gesamturteil.

    Gewichtet statt Maximum: das Maximum ließe jede einzelne Stimme allein durchentscheiden,
    und jede von ihnen irrt auf ihre Weise — die Regeln bei sauber aufgesetztem Betrug, das
    Modell bei nüchtern geschriebener Werbung, das Gedächtnis bei allem Neuen.

    Ohne Beobachtungen fällt das Gedächtnis heraus, statt mit 0.5 zur Mitte zu ziehen.
    """
    if gelernt is None:
        return round(0.55 * regel + 0.45 * modell, 3)
    return round(0.4 * regel + 0.3 * modell + 0.3 * gelernt, 3)


async def beurteilen(db: AsyncSession, owner_id: int | None, payload: dict, *,
                     cls: dict, regel=None) -> dict:
    """Eine eingegangene Mail beurteilen — reines Urteil, ohne Nebenwirkung.

    Schreibt nichts und fragt niemanden: das Ergebnis ist ein serialisierbares dict, das
    in den Kontext einer Prozess-Instanz passt. Was daraus folgt (nachfragen, verschieben,
    durchlassen), entscheidet der Ablauf im Graphen — hier steht nur, was *festgestellt*
    wurde. Vorher war beides in einer Funktion verwoben, und damit war die Reihenfolge der
    Behandlung nur im Code nachlesbar, nicht im Prozess.

    `regel` kann fertig hereingereicht werden — der Aufrufer braucht die Befunde meist
    schon vorher, um sie dem lokalen Modell mitzugeben; zweimal auswerten liefert dasselbe
    und würde nur die Deutung der Befunde auf zwei Stellen verteilen.
    """
    aktiv = (await get_setting(db, AKTIV_KEY, _VORGABE[AKTIV_KEY])) == "1"

    if regel is None:
        regel = evaluate(payload, meine_adressen=await meine_adressen(db),
                         bekannte_domains=await bekannte_domains(db, owner_id),
                         geschaeftsfreie_domains=await geschaeftsfreie_domains(db),
                         body=mail_text(payload))
    subject = str(payload.get("subject") or "")

    # Chef-Masche: der Anzeigename ist ein bekannter Kontakt, die Adresse aber nicht seine.
    # Technisch ist an so einer Mail nichts auszusetzen — nur der Kontaktbestand verrät sie,
    # weshalb die Prüfung hier steht und nicht im regelbasierten Teil.
    if regel.sender_name:
        opfer = await namens_kollision(db, owner_id, regel.sender_name, regel.sender_email)
        if opfer:
            regel.treffer("namens_kollision",
                          f"gibt sich als „{opfer}“ aus, schreibt aber von "
                          f"{regel.sender_email or 'unbekannt'}")
            regel.score = min(1.0, regel.score)

    # --- Bekannter Absender ---------------------------------------------------
    # Die Adresse wird immer geprüft, die Domain nur, wenn sie überhaupt etwas aussagt:
    # ein Kontakt bei gmx.de spricht nicht alle anderen gmx-Adressen frei.
    treffer = await kontakt_treffer(
        db, owner_id, regel.sender_email,
        "" if regel.sender_domain in FREEMAIL_DOMAINS else regel.sender_domain)
    # Ein bekannter Absender spricht nur frei, solange die Technik stimmt. Gerade der
    # bekannte Name ist das lohnende Ziel: eine gefälschte Mail „von der Hausbank" ist
    # gefährlicher als jede Werbung, und sie fällt genau hier auf.
    faelschungsverdacht = ist_faelschungsverdacht(regel.signals)
    # `sent` zählt wie ein Vault-Eintrag: wem ich selbst geschrieben habe, den kenne ich
    # nachweislich — das ist die stärkste Aussage „erwünscht", die ein Postfach hergibt.
    bekannter_kontakt = treffer in ("frontmatter", "sent") and not faelschungsverdacht
    if bekannter_kontakt:
        log.debug("Mail von bekanntem Kontakt %s — kein Spam-Verdacht", regel.sender_email)
    if treffer and faelschungsverdacht:
        regel.reasons.append("bekannter Absender, aber Echtheitsprüfung fehlgeschlagen "
                             "(Fälschungsverdacht)")
        regel.signals.append("kontakt_gefaelscht")
        regel.score = min(1.0, regel.score + 0.2)
    elif treffer in ("body", "domain"):
        # Schwächerer Bekanntheitsgrad: Abschlag, kein Freispruch.
        regel.score = max(0.0, regel.score - 0.15)

    merkmale = features(regel, subject, kontakt_treffer=treffer)

    # --- Gedächtnis -----------------------------------------------------------
    gelernt_score, gelernt_gruende, sicher = await spam_learn.bewerten(db, owner_id, merkmale)
    hat_gedaechtnis = bool(gelernt_gruende) or sicher

    modell = float(cls.get("spam_score") or 0.0)
    if str(cls.get("category") or "").lower() in ("spam", "phishing", "werbung"):
        modell = max(modell, 0.6)

    score = _mischen(regel.score, modell, gelernt_score if hat_gedaechtnis else None)

    # Bestellter Newsletter ist kein Spam. Ohne diese Bremse wandern
    # Bestellbestätigungen und Rechnungen mit der Werbung in den Spam-Ordner.
    if regel.ist_newsletter and not faelschungsverdacht:
        score = min(score, 0.4)

    gruende = list(regel.reasons)
    if cls.get("spam_reason"):
        gruende.append(str(cls["spam_reason"])[:200])
    gruende.extend(gelernt_gruende)

    # Das Gedächtnis darf allein entscheiden, wenn es über den Absender einig ist — genau
    # dafür wird gelernt. Sonst bliebe die immer gleiche Frage ewig stehen. Die Folge
    # daraus zieht der Graph; hier steht nur, DASS die Sache geklärt ist und wie.
    geklaert = bool(sicher and not faelschungsverdacht)
    # Das Urteil des eigenen Mailservers steht für sich. In der gewichteten Mischung geht
    # es unter: mit Regel = 1.0 und schweigendem Modell landet selbst eine Mail, der der
    # eigene Server 13 Spam-Punkte gibt, bei ~0.55 — eine Auto-Schwelle wäre damit
    # unerreichbar. Wer die eigene Infrastruktur befragt und ihr dann nicht glaubt, hätte
    # sie nicht fragen müssen; die Rückholkarte bleibt das Sicherheitsnetz.
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
        # Die Schwellen wandern mit ins Urteil: die Weiche steht im Graphen, und sie soll
        # gegen die Einstellung von JETZT prüfen können, ohne selbst die Datenbank zu lesen.
        "frage_ab": await _zahl(db, FRAGE_AB_KEY),
        "sofort_ab": await _zahl(db, SOFORT_AB_KEY),
        "auto_ab": await _zahl(db, AUTO_AB_KEY),
    }
    log.info("Spam-Urteil (%.2f: regel=%.2f modell=%.2f gelernt=%.2f, geklärt=%s) von %s",
             score, regel.score, modell, gelernt_score, urteil["geklaert_urteil"] or "nein",
             regel.sender_email)
    return urteil


async def anlegen(db: AsyncSession, owner_id: int | None, urteil: dict, *,
                  task_id: int | None = None, instance_id: int | None = None) -> SpamVerdict:
    """Aus einem Urteil eine Zeile machen — Arbeitsvorrat und späterer Lehrstoff.

    `instance_id` bindet die Zeile an den Ablauf, der sie erzeugt hat: der Telegram-Knopf
    entscheidet damit nicht mehr an der Engine vorbei, sondern schaltet den Ablauf weiter
    (siehe `entscheiden`).
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
    """(Titel, Text) der Einzelkarte.

    Drei Bauformen: die Frage, der gelernte Fall (schon entschieden) und der automatisch
    verschobene (schon geschehen, mit Rückweg). Die dritte ist die einzige, bei der ein
    Mensch nachträglich widerspricht — deshalb sagt sie zuerst, was passiert IST.
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
    """Einzelkarte in die Benachrichtigungen legen (der Bot stellt zu und hängt die
    Knöpfe an)."""
    if not owner_id:
        return
    owner = await db.get(User, owner_id)
    if owner is None or not owner.telegram_chat_id:
        return
    titel, text = karte(verdict, vorentschieden=vorentschieden, rueckholbar=rueckholbar)
    # Die Art entscheidet, welche Knöpfe der Bot anhängt: eine Frage bekommt zwei, eine
    # bereits ausgeführte Aussortierung genau einen — den Rückweg.
    db.add(Notification(
        user_id=owner_id, spam_verdict_id=verdict.id,
        kind="spam_auto" if rueckholbar else "spam_review",
        chat_id=owner.telegram_chat_id, title=titel[:200], body=text[:4000]))
    if not sofort:
        log.debug("Urteil #%s wartet auf die Sammel-Karte", verdict.id)


async def digest_faellig(db: AsyncSession) -> int:
    """Offene Verdachtsfälle unterhalb der Sofort-Schwelle zu EINER Karte bündeln.

    Wird vom Scheduler getaktet. Ohne Bündelung würde bei nennenswertem Spam-Aufkommen
    der halbe Tag aus Telegram-Nachrichten bestehen — und wer alle drei Minuten gefragt
    wird, drückt irgendwann auf gut Glück.
    """
    takt = int(await _zahl(db, DIGEST_MIN_KEY))
    if takt <= 0:
        return 0
    grenze = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=takt)
    offen = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.status == "pending", SpamVerdict.digest_batch.is_(None),
        SpamVerdict.created_at <= grenze).order_by(SpamVerdict.id).limit(50))).scalars().all()
    # Sofort gemeldete Fälle hängen schon an einer eigenen Karte — sie dürfen nicht
    # doppelt gefragt werden.
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
            # Der Bezug zeigt auf den ersten Fall der Sammlung; über dessen `digest_batch`
            # findet der Bot die ganze Menge wieder. Die Kennung selbst passt nicht in die
            # Rückmeldung eines Knopfes, wenn dort schon eine Aktion steht.
            user_id=owner_id, spam_verdict_id=faelle[0].id, kind="spam_digest",
            chat_id=owner.telegram_chat_id,
            title=(await tr(db, "server.notify.spam_verdacht", owner.locale,
                            anzahl=len(faelle)))[:200],
            body="\n".join(zeilen)[:4000]))
        gesendet += 1
    await db.commit()
    return gesendet


# --- Entscheidung + Ausführung ------------------------------------------------------

async def entscheiden(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool, *,
                      decided_by: str = "telegram") -> str:
    """Die Antwort des Menschen entgegennehmen. → Klartext-Ergebnis für die Rückmeldung.

    Hängt an der Zeile ein Ablauf (Normalfall seit dem Mail-Prozess), wird hier NICHT mehr
    selbst verschoben: die Antwort schaltet den Genehmigungs-Knoten weiter, und was danach
    geschieht — lernen, Absender merken, Mail bewegen — steht im Graphen. Sonst liefe der
    Ablauf am Knopf vorbei und stünde ewig an seinem Wartepunkt.

    Ohne Ablauf (Altbestand aus der Zeit vor dem Prozess) bleibt der direkte Weg: erst
    lernen, dann verschieben. Scheitert das Verschieben (Mail schon weggeräumt, IMAP kurz
    weg), bleibt die Entscheidung trotzdem im Gedächtnis — sie war ja richtig, nur nicht
    ausführbar.
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
    """Urteil festhalten und daraus lernen (ohne IMAP). Committet NICHT."""
    vorher = verdict.status if verdict.status in ("spam", "ham") else ""
    verdict.status = "spam" if ist_spam else "ham"
    verdict.decided_by = decided_by
    verdict.decided_at = dt.datetime.now(tz=dt.timezone.utc)
    await spam_learn.merken(db, verdict, ist_spam, vorher=vorher)

    if not ist_spam:
        # „Kein Spam" ist mehr als ein Nein: der Absender soll künftig gar nicht erst
        # auffallen. Die gelernte Regel greift schon vor der Beurteilung.
        from .assistant_policy import upsert_policy
        if verdict.sender_email and verdict.sender_domain not in FREEMAIL_DOMAINS:
            await upsert_policy(db, verdict.owner_user_id, match_kind="sender",
                                match_value=verdict.sender_email, auto_approve=False)


async def _an_ablauf_melden(db: AsyncSession, verdict: SpamVerdict, ist_spam: bool, *,
                            decided_by: str) -> str:
    """Antwort in den wartenden Ablauf geben und ihn weiterschalten.

    Die Entscheidung steht danach im Kontext (`spam.entschieden`) — der Graph liest sie an
    seiner Weiche und führt die IMAP-Aktion aus. Wartet gerade kein Genehmigungs-Schritt
    (Ablauf abgebrochen, Instanz weg), wird die Antwort auf dem direkten Weg ausgeführt:
    eine beantwortete Frage darf nicht ins Leere laufen.
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
        log.warning("Urteil #%s: kein wartender Ablauf (Instanz %s) — direkt ausgeführt",
                    verdict.id, verdict.workflow_instance_id)
        await festschreiben(db, verdict, ist_spam, decided_by=decided_by)
        ergebnis = await imap_aktion(verdict, ist_spam)
        verdict.action_result = ergebnis[:2000]
        await db.commit()
        return ergebnis

    await db.commit()
    await advance(inst.id)
    # Der Ablauf hat inzwischen in einer eigenen Sitzung geschrieben — für die Rückmeldung
    # an den Menschen zählt sein Ergebnis, nicht der Stand von vorhin.
    await db.refresh(verdict)
    return verdict.action_result or "an den Ablauf übergeben"


async def imap_aktion(verdict: SpamVerdict, ist_spam: bool) -> str:
    """Mail über `imap-mcp` verschieben. Fehler werden gemeldet, nicht geworfen —
    eine nicht verschiebbare Mail darf die Entscheidung nicht rückgängig machen."""
    if not (verdict.account and verdict.folder and verdict.uid):
        return "keine Mailkennung hinterlegt — nichts verschoben"
    werkzeug = "mark_spam" if ist_spam else "mark_not_spam"
    try:
        ergebnis = await call_tool(IMAP_MCP_URL, werkzeug, {
            "account": verdict.account, "folder": verdict.folder, "uid": verdict.uid})
    except McpError as exc:
        log.warning("%s für Urteil #%s fehlgeschlagen: %s", werkzeug, verdict.id, exc)
        return f"nicht verschoben: {exc}"
    text = ergebnis_text(ergebnis) or "verschoben"
    log.info("%s für Urteil #%s: %s", werkzeug, verdict.id, text)
    return text


async def zurueckholen(db: AsyncSession, verdict: SpamVerdict, *,
                       decided_by: str = "telegram") -> str:
    """Eine automatisch aussortierte Mail zurück in den Posteingang. → Klartext-Ergebnis.

    Der Widerspruch zum Auto-Verschieben, und er ist mehr als ein Rückzug: der Absender
    wird als erwünscht gelernt und bekommt eine Regel, damit derselbe Irrtum nicht morgen
    wieder passiert. Ohne das wäre Stufe 2 ein Automat, der denselben Fehler beliebig oft
    macht.
    """
    if verdict.status not in ("spam", "pending"):
        return f"schon erledigt ({verdict.status})"
    await festschreiben(db, verdict, False, decided_by=decided_by)
    ergebnis = await imap_aktion(verdict, False)
    verdict.action_result = ergebnis[:2000]
    await db.commit()
    log.info("Urteil #%s zurückgeholt: %s", verdict.id, ergebnis)
    return ergebnis


async def entscheide_batch(db: AsyncSession, batch: str, ist_spam: bool, *,
                           decided_by: str = "telegram") -> tuple[int, int]:
    """Alle offenen Fälle einer Sammel-Karte auf einmal entscheiden. → (erledigt, Fehler)."""
    faelle = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.digest_batch == batch, SpamVerdict.status == "pending"))).scalars().all()
    fehler = 0
    for v in faelle:
        ergebnis = await entscheiden(db, v, ist_spam, decided_by=decided_by)
        if ergebnis.startswith("nicht verschoben"):
            fehler += 1
    return len(faelle), fehler
