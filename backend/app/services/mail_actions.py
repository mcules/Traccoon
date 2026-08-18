"""Was der Mail-Eingang tun kann — die Schritte des Slots `mail_intake` als Aktionen.

Der Weg einer eingegangenen Mail stand früher am Stück in `mail_intake.intake_mail`:
einordnen, beurteilen, nachfragen, wegräumen oder weiterreichen. Die Reihenfolge war damit
nur im Code nachlesbar und nur mit einem Deploy änderbar. Hier steht jeder Schritt für
sich; ihre Reihenfolge zeichnet der Graph (`workflow_seed.build_mail_intake`).

Der Kontext einer Instanz ist die gemeinsame Sprache dieser Schritte:

    mail      Rohpayload des Watchers (from/to/subject/body/headers/account/folder/uid)
    eingang   Einstellungen des Auslösers (Klassifizier-Agent, Assistent, Prompt, Besitzer)
    klasse    Ergebnis der lokalen Klassifizierung (Kategorie, Dringlichkeit, Kurzfassung)
    policy    gelernte Regel zum Absender (Schwärzung, Hinweis, Auto-Freigabe)
    spam      Urteil der Spam-Erkennung + später die Antwort des Menschen
    task      angelegtes Assistent-Item

Nichts hier entscheidet, was als Nächstes kommt — das tun die Weichen im Graphen.
"""
from __future__ import annotations

import logging

from ..models.assistant import AssistantTask
from ..models.notification import Notification
from ..models.user import User
from ..models.workflow import WorkflowInstance
from .i18n import tr

log = logging.getLogger("traccoon.mail")


def _mail(ctx: dict) -> dict:
    m = ctx.get("mail")
    return m if isinstance(m, dict) else {}


def _eingang(ctx: dict) -> dict:
    e = ctx.get("eingang")
    return e if isinstance(e, dict) else {}


def _owner(inst: WorkflowInstance, ctx: dict) -> int | None:
    roh = _eingang(ctx).get("owner_id")
    try:
        return int(roh) if roh is not None else inst.started_by
    except (TypeError, ValueError):
        return inst.started_by


def _mail_koerper(payload: dict) -> str:
    from .spam_rules import mail_text
    return mail_text(payload)


def _fill_prompt(tmpl: str, payload: dict) -> str:
    """{platzhalter} aus dem Payload füllen — JEDES Payload-Feld (mail ODER paperless-linked:
    {document_id}/{url}/{note}/{hinweis}…). Fehlende bleiben leer. Sichere Ersetzung (kein
    str.format, damit Code-Beispiele mit { } im Prompt nicht brechen)."""
    import re

    out = tmpl
    for k, v in (payload or {}).items():
        out = out.replace("{" + k + "}", "" if v is None else str(v))
    out = out.replace("{body_text}", str(payload.get("body_text", payload.get("body", "")) or ""))
    return re.sub(r"\{[a-z_]+\}", "", out)


async def klassifizieren(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Die Mail im Haus einordnen und die gelernte Regel zum Absender heraussuchen.

    Ohne Klassifizier-Agenten bleibt es beim Durchreichen (wie im Vorläufer): der Agent
    liest die Mail später selbst per IMAP. Die technischen Befunde der Regeln gehen als
    Hinweise ins Modell — es soll den Text beurteilen, nicht Kopfzeilen nachlesen, die es
    ohnehin nicht sieht.
    """
    from . import spam_learn
    from .assistant_policy import agent_laeuft_lokal, match_policy, note_hit, parse_sender
    from .mail_classify import classify_email
    from .spam_rules import evaluate, mail_text
    from .spam_review import geschaeftsfreie_domains, meine_adressen
    from .vault_contacts import bekannte_domains

    payload = _mail(ctx)
    eingang = _eingang(ctx)
    owner_id = _owner(inst, ctx)
    account = str(payload.get("account") or "")
    sender = str(payload.get("from") or "")
    subject = str(payload.get("subject") or "")
    body = mail_text(payload)

    classify_agent = str(params.get("classify_agent") or eingang.get("classify_agent") or "")
    if classify_agent:
        regel = evaluate(payload, meine_adressen=await meine_adressen(db),
                         bekannte_domains=await bekannte_domains(db, owner_id),
                         geschaeftsfreie_domains=await geschaeftsfreie_domains(db),
                         body=body)
        klasse = await classify_email(db, owner_id, account=account, sender=sender,
                                      subject=subject, body=body,
                                      classify_agent=classify_agent,
                                      spam_hints=regel.reasons,
                                      spam_beispiele=await spam_learn.beispiele(db, owner_id))
    else:
        klasse = {"category": "", "priority": "normal", "sensitive": False,
                  "redacted_summary": "", "spam_score": 0.0, "spam_reason": ""}

    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=klasse["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve
    # Schwärzen schützt davor, dass Rohtext das Haus verlässt. Bearbeitet die Mail ein
    # Modell auf dem eigenen Endpoint, verlässt nichts das Haus — dann ist die Schwärzung
    # kein Schutz mehr, sondern nur noch Informationsverlust (und ein Umweg über die
    # IMAP-Tools, der denselben Text ohnehin wieder heranholt).
    agent = str(eingang.get("agent") or "assistent")
    if await agent_laeuft_lokal(db, owner_id, agent):
        redaction = "unredacted"
    if eingang.get("auto_run"):  # Auslöser erzwingt chatlosen Sofortlauf.
        auto = True

    inst.context = {**ctx, "klasse": klasse,
                    "policy": {"redaction": redaction, "action_hint": action_hint or "",
                               "auto": bool(auto), "id": policy.id if policy else None}}
    return {"action": "mail_classify", "category": klasse["category"],
            "priority": klasse["priority"], "auto": bool(auto),
            "classified": bool(classify_agent)}


async def spam_beurteilen(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Regeln, lokales Modell und Gedächtnis zu einem Urteil zusammenziehen.

    Die Regeln werden hier erneut ausgewertet statt aus dem Kontext gereicht: sie sind
    reine Rechnung ohne Zugriff nach außen, und ein Befund-Objekt überlebt den Weg durch
    eine JSON-Spalte nicht unbeschadet. Was danach passiert, entscheidet die Weiche im
    Graphen — dieses Urteil sagt nur, was festgestellt wurde.
    """
    from .spam_review import beurteilen

    urteil = await beurteilen(db, _owner(inst, ctx), _mail(ctx),
                              cls=(ctx.get("klasse") or {}))
    inst.context = {**ctx, "spam": {**((ctx.get("spam") or {})), **urteil}}
    return {"action": "spam_evaluate", "score": urteil["score"],
            "geklaert": urteil["geklaert_urteil"] or "nein",
            "aktiv": urteil["aktiv"]}


async def spam_karte(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Die Rückfrage anlegen: eine Urteils-Zeile (Arbeitsvorrat + Lehrstoff) und die Karte.

    `vorentschieden` meldet einen Fall, den das Gedächtnis schon geklärt hat — er geht
    trotzdem als Karte raus, nur ohne Frage. `rueckholbar` meldet eine Mail, die über der
    Auto-Schwelle lag und deshalb ohne Rückfrage wegkommt; ihre Karte trägt den Rückweg.
    Beide gehen immer sofort raus: wer stillschweigend verschiebt, merkt einen
    eingeschlichenen Irrtum nie, und in einer Sammel-Karte von morgen früh wäre der
    Widerspruch zu spät.

    Unterhalb der Sofort-Schwelle wird bewusst KEINE eigene Karte verschickt: diese Fälle
    sammelt der Scheduler zur Sammel-Karte (`spam_review.digest_faellig`). Der Ablauf
    wartet derweil an seinem Genehmigungs-Knoten.
    """
    from .spam_review import anlegen, melden

    owner_id = _owner(inst, ctx)
    urteil = dict(ctx.get("spam") or {})
    task = ctx.get("task") or {}
    vorentschieden = bool(params.get("vorentschieden"))
    rueckholbar = bool(params.get("rueckholbar"))
    verdict = await anlegen(db, owner_id, urteil,
                            task_id=task.get("id") if isinstance(task, dict) else None,
                            instance_id=inst.id)
    sofort = vorentschieden or rueckholbar or float(urteil.get("score") or 0.0) >= float(
        urteil.get("sofort_ab") or 0.9)
    if sofort:
        await melden(db, owner_id, verdict, sofort=True, vorentschieden=vorentschieden,
                     rueckholbar=rueckholbar)
    art = "rueckholbar" if rueckholbar else ("sofort" if sofort else "sammel")
    inst.context = {**ctx, "spam": {**urteil, "verdict_id": verdict.id, "karte": art}}
    return {"action": "spam_card", "verdict_id": verdict.id, "karte": art,
            "vorentschieden": vorentschieden}


async def spam_ausfuehren(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Das Urteil festschreiben, daraus lernen und die Mail bewegen.

    Reihenfolge mit Absicht: erst lernen, dann verschieben. Scheitert das Verschieben (Mail
    schon weggeräumt, IMAP kurz weg), bleibt die Entscheidung trotzdem im Gedächtnis — sie
    war ja richtig, nur nicht ausführbar. Der Schritt schlägt deshalb auch nicht fehl; das
    Ergebnis steht als Text am Urteil und im Kontext.
    """
    from ..models.assistant import SpamVerdict
    from .spam_review import festschreiben, imap_aktion

    urteil = dict(ctx.get("spam") or {})
    wert = str(params.get("entscheidung") or urteil.get("entschieden") or "spam").strip().lower()
    ist_spam = wert not in ("ham", "kein_spam", "no", "false")
    vid = urteil.get("verdict_id")
    verdict = await db.get(SpamVerdict, int(vid)) if vid else None
    if verdict is None:
        return {"action": "spam_apply", "applied": False, "reason": "kein Urteil hinterlegt"}

    await festschreiben(db, verdict, ist_spam,
                        decided_by=str(params.get("decided_by")
                                       or urteil.get("entschieden_von") or "auto"))
    ergebnis = await imap_aktion(verdict, ist_spam)
    verdict.action_result = ergebnis[:2000]
    inst.context = {**ctx, "spam": {**urteil, "entschieden": "spam" if ist_spam else "ham",
                                    "ergebnis": ergebnis}}
    return {"action": "spam_apply", "verdict_id": verdict.id,
            "entscheidung": "spam" if ist_spam else "ham", "ergebnis": ergebnis}


async def assistent_item(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Aus der Mail ein Assistent-Item machen (das, was der Mensch dann freigibt).

    Doppelte Zustellung erzeugt kein zweites Item: der Schlüssel ist derselbe wie beim
    Auslöser (konfiguriertes Feld, sonst Konto:UID).
    """
    from sqlalchemy import select

    payload = _mail(ctx)
    eingang = _eingang(ctx)
    owner_id = _owner(inst, ctx)
    klasse = dict(ctx.get("klasse") or {})
    policy = dict(ctx.get("policy") or {})
    quelle = str(eingang.get("source") or "workflow")
    src_ref = eingang.get("source_ref") or None

    if src_ref:
        dup = (await db.execute(select(AssistantTask).where(
            AssistantTask.source == quelle,
            AssistantTask.source_ref == str(src_ref)))).scalar_one_or_none()
        if dup is not None:
            inst.context = {**ctx, "task": {"id": dup.id, "status": dup.status, "auto": False}}
            return {"action": "assistant_task", "task_id": dup.id, "duplicate": True}

    redaction = str(policy.get("redaction") or "redacted")
    prompt_tmpl = str(eingang.get("prompt_tmpl") or "")
    task = AssistantTask(
        owner_user_id=owner_id, kind="email", source=quelle,
        source_ref=str(src_ref) if src_ref else None,
        title=(str(payload.get("subject") or "") or "(kein Betreff)")[:500],
        category=klasse.get("category", ""), priority=klasse.get("priority", "normal"),
        redacted_summary=klasse.get("redacted_summary", ""),
        meta={"account": str(payload.get("account") or ""), "uid": payload.get("uid"),
              "from": str(payload.get("from") or ""),
              "subject": str(payload.get("subject") or ""),
              "sensitive": klasse.get("sensitive", False),
              "agent": str(eingang.get("agent") or "assistent"),
              **({"prompt": _fill_prompt(prompt_tmpl, payload)} if prompt_tmpl else {})},
        redaction=redaction, action_hint=str(policy.get("action_hint") or ""),
        raw_body=(_mail_koerper(payload) if redaction == "unredacted" else None),
        status=("approved" if policy.get("auto") else "new"),
    )
    db.add(task)
    await db.flush()
    inst.context = {**ctx, "task": {"id": task.id, "status": task.status,
                                    "auto": bool(policy.get("auto"))}}
    log.info("Mail-Item #%s (%s, prio=%s, auto=%s) über Ablauf %s",
             task.id, task.category, task.priority, policy.get("auto"), inst.id)
    return {"action": "assistant_task", "task_id": task.id, "status": task.status}


async def assistent_karte(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Freigabekarte für das Assistent-Item (Telegram/Glocke)."""
    owner_id = _owner(inst, ctx)
    task = dict(ctx.get("task") or {})
    klasse = dict(ctx.get("klasse") or {})
    if not (owner_id and task.get("id")):
        return {"action": "assistant_card", "sent": False, "reason": "kein Item/Besitzer"}
    owner = await db.get(User, owner_id)
    if owner is None or not owner.telegram_chat_id:
        return {"action": "assistant_card", "sent": False, "reason": "kein Telegram-Ziel"}
    titel = str(_mail(ctx).get("subject") or "(kein Betreff)")
    db.add(Notification(
        user_id=owner_id, assistant_task_id=int(task["id"]), kind="assistant_review",
        chat_id=owner.telegram_chat_id,
        title=(await tr(db, "server.notify.mail_eingang", owner.locale, titel=titel))[:200],
        body=(await tr(db, "server.notify.mail_von", owner.locale,
                       absender=_mail(ctx).get("from") or "?")
              + "\n" + str(klasse.get("redacted_summary", "")))[:1000]))
    return {"action": "assistant_card", "sent": True, "task_id": task["id"]}


async def assistent_lauf(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Den Assistenten sofort loslaufen lassen (Auto-Freigabe durch gelernte Regel)."""
    from ..core.redis import enqueue_task

    task = dict(ctx.get("task") or {})
    if not task.get("id"):
        return {"action": "assistant_run", "queued": False, "reason": "kein Item"}
    await enqueue_task({"kind": "assistant", "task_id": f"assistant-{task['id']}",
                        "assistant_task_id": int(task["id"])})
    return {"action": "assistant_run", "queued": True, "task_id": task["id"]}


HANDLER = {
    "mail_classify": klassifizieren,
    "spam_evaluate": spam_beurteilen,
    "spam_card": spam_karte,
    "spam_apply": spam_ausfuehren,
    "assistant_task": assistent_item,
    "assistant_card": assistent_karte,
    "assistant_run": assistent_lauf,
}
