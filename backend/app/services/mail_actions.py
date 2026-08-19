"""What the mail inbox can do: the steps of the slot `mail_intake` as actions.

The way of an incoming mail used to stand in one piece in `mail_intake.intake_mail`:
classifying, assessing, asking, clearing away or passing on. The order could therefore only
be read in the code and only be changed with a deploy. Here every step stands on its own;
their order is drawn by the graph (template `mail-eingang`, `workflow_templates`).

The context of an instance is the shared language of these steps:

    mail      raw payload of the watcher (from/to/subject/body/headers/account/folder/uid)
    eingang   settings of the trigger (classifying agent, assistant, prompt, owner)
    klasse    result of the local classification (category, urgency, short version)
    policy    learned rule about the sender (redaction, hint, auto approval)
    spam      verdict of the spam detection plus, later, the answer of the human
    task      the created assistant item

Nothing here decides what comes next; that is done by the branches in the graph.
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
    """Fill {placeholders} from the payload: EVERY payload field (mail OR paperless-linked:
    {document_id}/{url}/{note}/{hinweis}…). Missing ones stay empty. Safe replacement (no
    str.format, so that code examples with { } in the prompt do not break)."""
    import re

    out = tmpl
    for k, v in (payload or {}).items():
        out = out.replace("{" + k + "}", "" if v is None else str(v))
    out = out.replace("{body_text}", str(payload.get("body_text", payload.get("body", "")) or ""))
    return re.sub(r"\{[a-z_]+\}", "", out)


async def klassifizieren(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Classify the mail in house and look up the learned rule about the sender.

    Without a classifying agent it stays at passing through (as in the predecessor): the
    agent reads the mail itself over IMAP later. The technical findings of the rules go into
    the model as hints; it should assess the text, not read headers it does not see anyway.
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
                  "redacted_summary": "", "spam_score": 0.0, "spam_reason": "",
                  "betrug": False, "merkmale": []}

    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=klasse["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve
    # Redaction protects against raw text leaving the house. If a model on the own endpoint
    # processes the mail, nothing leaves the house, and then the redaction is no longer a
    # protection but only a loss of information (and a detour over the IMAP tools that
    # fetches the same text back anyway).
    agent = str(eingang.get("agent") or "assistent")
    if await agent_laeuft_lokal(db, owner_id, agent):
        redaction = "unredacted"
    if eingang.get("auto_run"):  # the trigger enforces a chatless immediate run.
        auto = True

    inst.context = {**ctx, "klasse": klasse,
                    "policy": {"redaction": redaction, "action_hint": action_hint or "",
                               "auto": bool(auto), "id": policy.id if policy else None}}
    return {"action": "mail_classify", "category": klasse["category"],
            "priority": klasse["priority"], "auto": bool(auto),
            "classified": bool(classify_agent)}


async def spam_beurteilen(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Pull rules, local model and memory together into one verdict.

    The rules are evaluated again here instead of being passed through the context: they are
    pure computation without access to the outside, and a findings object does not survive
    the way through a JSON column unharmed. What happens afterwards is decided by the branch
    in the graph; this verdict only says what was established.
    """
    from .spam_review import beurteilen

    urteil = await beurteilen(db, _owner(inst, ctx), _mail(ctx),
                              cls=(ctx.get("klasse") or {}))
    inst.context = {**ctx, "spam": {**((ctx.get("spam") or {})), **urteil}}
    return {"action": "spam_evaluate", "score": urteil["score"],
            "geklaert": urteil["geklaert_urteil"] or "nein",
            "aktiv": urteil["aktiv"]}


async def spam_karte(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Create the question: a verdict row (work stock plus learning material) and the card.

    `vorentschieden` reports a case the memory has already settled; it goes out as a card
    regardless, only without a question. `rueckholbar` reports a mail that lay above the auto
    threshold and therefore goes away without a question; its card carries the way back.
    Both always go out immediately: whoever moves silently never notices an error that crept
    in, and in a digest card of tomorrow morning the objection would be too late.

    Below the immediate threshold NO card of its own is deliberately sent: those cases are
    collected by the scheduler into the digest card (`spam_review.digest_faellig`). The flow
    waits at its approval node meanwhile.

    With `melden=false` this step only prepares: the verdict comes into being, the text
    stands in the context (`spam.karte_titel`, `spam.karte_text`, `spam.karte_art`,
    `spam.karte_faellig`), and the sending is done by a notify node behind it. That way
    switching off the message does not switch off the sorting.
    """
    from .spam_review import anlegen, karte, melden

    owner_id = _owner(inst, ctx)
    urteil = dict(ctx.get("spam") or {})
    task = ctx.get("task") or {}
    vorentschieden = bool(params.get("vorentschieden"))
    rueckholbar = bool(params.get("rueckholbar"))
    # Ob gemeldet wird, entscheidet der Ablauf. `melden=false` heißt: Urteil anlegen, Text
    # bereitstellen — verschickt wird es von einem Melde-Knoten dahinter, den man abschalten
    # kann, ohne die Aussortierung mit abzuschalten. Vorgabe bleibt `true`, damit
    # veröffentlichte Fassungen ohne solchen Knoten weiter melden.
    selbst_melden = params.get("melden")
    selbst_melden = True if selbst_melden is None else bool(selbst_melden)
    verdict = await anlegen(db, owner_id, urteil,
                            task_id=task.get("id") if isinstance(task, dict) else None,
                            instance_id=inst.id)
    sofort = vorentschieden or rueckholbar or float(urteil.get("score") or 0.0) >= float(
        urteil.get("sofort_ab") or 0.9)
    if sofort and selbst_melden:
        await melden(db, owner_id, verdict, sofort=True, vorentschieden=vorentschieden,
                     rueckholbar=rueckholbar)
    art = "rueckholbar" if rueckholbar else ("sofort" if sofort else "sammel")
    # Der Text gehört zum Spam-Wissen, nicht in den Graphen: welche Gründe genannt werden und
    # wie ein Rückholhinweis lautet, ändert sich mit der Erkennung. Der Melde-Knoten nimmt
    # ihn als `{{ spam.karte_titel }}` / `{{ spam.karte_text }}` und bleibt selbst allgemein.
    titel, text = karte(verdict, vorentschieden=vorentschieden, rueckholbar=rueckholbar)
    inst.context = {**ctx, "spam": {**urteil, "verdict_id": verdict.id, "karte": art,
                                    "karte_titel": titel, "karte_text": text,
                                    "karte_faellig": bool(sofort),
                                    "karte_art": "spam_auto" if rueckholbar else "spam_review"}}
    return {"action": "spam_card", "verdict_id": verdict.id, "karte": art,
            "vorentschieden": vorentschieden, "selbst_gemeldet": bool(sofort and selbst_melden)}


async def spam_ausfuehren(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Commit the verdict, learn from it and move the mail.

    The order is deliberate: first learn, then move. If moving fails (mail already cleared
    away, IMAP briefly gone), the decision still stays in the memory, because it was right,
    only not executable. That is why the step does not fail either; the result stands as text
    on the verdict and in the context.
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
    """Turn the mail into an assistant item (the thing the human then approves).

    A double delivery creates no second item: the key is the same as with the trigger
    (configured field, otherwise account:UID).
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
    log.info("Mail item #%s (%s, prio=%s, auto=%s) over flow %s",
             task.id, task.category, task.priority, policy.get("auto"), inst.id)
    return {"action": "assistant_task", "task_id": task.id, "status": task.status}


async def assistent_karte(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """The approval card for the assistant item (Telegram, bell)."""
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
    """Let the assistant run immediately (auto approval through a learned rule)."""
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
