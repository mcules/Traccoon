"""What the mail inbox can do: the steps of the slot `mail_intake` as actions.

The way of an incoming mail used to stand in one piece in `mail_intake.intake_mail`:
classifying, assessing, asking, clearing away or passing on. The order could therefore only
be read in the code and only be changed with a deploy. Here every step stands on its own;
their order is drawn by the graph (template `mail-eingang`, `workflow_templates`).

The context of an instance is the shared language of these steps:

    mail      raw payload of the watcher (from/to/subject/body/headers/account/folder/uid)
    intake    settings of the trigger (classifying agent, assistant, prompt, owner)
    classification  result of the local classification (category, urgency, short version)
    policy    learned rule about the sender (redaction, hint, auto approval)
    spam      verdict of the spam detection plus, later, the answer of the human
    task      the created assistant item

Nothing here decides what comes next; that is done by the branches in the graph.
"""
from __future__ import annotations

import logging

from ..models.workflow import WorkflowInstance
from .i18n import tr

log = logging.getLogger("traccoon.mail")


def _mail(ctx: dict) -> dict:
    m = ctx.get("mail")
    return m if isinstance(m, dict) else {}


def _eingang(ctx: dict) -> dict:
    e = ctx.get("intake")
    return e if isinstance(e, dict) else {}


def _owner(inst: WorkflowInstance, ctx: dict) -> int | None:
    roh = _eingang(ctx).get("owner_id")
    try:
        return int(roh) if roh is not None else inst.started_by
    except (TypeError, ValueError):
        return inst.started_by




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

    inst.context = {**ctx, "classification": klasse,
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
                              cls=(ctx.get("classification") or {}))
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


# ── Assistent: nur noch die Übersetzung, nicht mehr die Arbeit ───────────────
# Den Eingang anlegen, ihn starten, die Freigabekarte schicken — das konnte einmal nur der
# Mail-Weg, mit drei eigenen Aktionen. Es sind aber keine Mail-Sachen: `assistent_auftrag`
# legt den Eingang an (und startet ihn, wenn keine Freigabe nötig ist), `notify` schickt die
# Karte. Was der Mail-Eingang beisteuert, sind seine Werte — und die stehen hier als
# Parametersatz, damit Vorlage und Altnamen dieselben benutzen.
AUFTRAG_PARAMS: dict = {
    "kind": "email",
    "source": "{{ intake.source }}",
    "reference": "{{ intake.source_ref }}",
    "agent": "{{ intake.agent }}",
    "title": "{{ mail.subject | default:\"(kein Betreff)\" }}",
    "task": "{{ intake.prompt_tmpl }}",
    "category": "{{ classification.category }}",
    "priority": "{{ classification.priority | default:\"normal\" }}",
    "summary": "{{ classification.redacted_summary }}",
    "hint": "{{ policy.action_hint }}",
    "redaction": "{{ policy.redaction | default:\"redacted\" }}",
    # Welches Feld den Text trägt, hängt am Absender der Mail, nicht am Ablauf.
    "full_text": "{{ mail.body_text | default:mail.body | default:mail.body_html_as_text }}",
    "meta": {"account": "{{ mail.account }}", "uid": "{{ mail.uid }}",
             "from": "{{ mail.from }}", "subject": "{{ mail.subject }}",
             "sensitive": "{{ classification.sensitive }}"},
    # Eine gelernte Regel gibt frei, alles andere fragt.
    "approval": {"!": {"var": "policy.auto"}},
}

# Die Karte zum Freigeben: eine gewöhnliche Nachricht mit Art und Bezug.
KARTE_PARAMS: dict = {
    "kind": "assistant_review",
    "ref": {"assistant_task_id": "{{ task.id }}"},
    "title": "📥 {{ mail.subject | default:\"(kein Betreff)\" }}",
    # `from` kommt je nach Melder als Text oder als Liste von {name, addr}; die Filterkette
    # holt in beiden Fällen die Adresse heraus, statt eine Python-Liste hinzuschreiben.
    "text": "Von {{ mail.from | field:\"addr\" | join:\", \" | default:\"?\" }}\n"
            "{{ classification.redacted_summary }}",
}


async def assistent_item(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Altname `assistant_task`, umgeleitet auf den allgemeinen Knoten.

    Er steht in veröffentlichten Fassungen, und die sind unveränderlich — laufende Instanzen
    hängen daran. Deshalb umgeleitet statt zweimal gepflegt, wie bei `_ALT_AKTIONEN`.
    """
    from .workflow_actions import _assistent_auftrag
    return await _assistent_auftrag(db, inst, {**AUFTRAG_PARAMS, **params}, ctx, "item")


async def assistent_karte(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Altname `assistant_card`: die Freigabekarte ist eine Nachricht wie jede andere."""
    from .workflow_actions import run_action
    return await run_action(db, inst, {"id": "freigabe_karte", "data": {"config": {
        "action": {"action": "notify", "params": {**KARTE_PARAMS, **params}}}}})


async def assistent_lauf(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Altname `assistant_run`: der Eingang startet heute schon beim Anlegen.

    Ein zweiter Anstoß wäre ein zweiter Lauf derselben Sache — deshalb wird hier nur
    berichtet, was ohnehin geschehen ist.
    """
    task = dict(ctx.get("task") or {})
    return {"action": "assistant_run", "queued": False, "task_id": task.get("id"),
            "grund": "läuft bereits mit dem Anlegen"}


HANDLER = {
    "mail_classify": klassifizieren,
    "spam_evaluate": spam_beurteilen,
    "spam_card": spam_karte,
    "spam_apply": spam_ausfuehren,
    # Die Altnamen des Mail-Wegs. `assistant_task` heißt heute der ALLGEMEINE Auftrag —
    # deshalb tragen die alten hier ein `mail_`-Vorzeichen, und die einmalige Umstellung
    # (`workflow_terms.EINMALIG`) schreibt sie in den gespeicherten Fassungen darauf um.
    "mail_assistant_task": assistent_item,
    "mail_assistant_card": assistent_karte,
    "mail_assistant_run": assistent_lauf,
}
