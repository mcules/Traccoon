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


def _intake(ctx: dict) -> dict:
    e = ctx.get("intake")
    return e if isinstance(e, dict) else {}


def _owner(inst: WorkflowInstance, ctx: dict) -> int | None:
    raw = _intake(ctx).get("owner_id")
    try:
        return int(raw) if raw is not None else inst.started_by
    except (TypeError, ValueError):
        return inst.started_by




async def classify(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Classify the mail in house and look up the learned rule about the sender.

    Without a classifying agent it stays at passing through (as in the predecessor): the
    agent reads the mail itself over IMAP later. The technical findings of the rules go into
    the model as hints; it should assess the text, not read headers it does not see anyway.
    """
    from . import spam_learn
    from .assistant_policy import agent_running_local, match_policy, note_hit, parse_sender
    from .mail_classify import classify_email
    from .spam_rules import evaluate, mail_text
    from .spam_review import nonbusiness_domains, my_addresses
    from .vault_contacts import known_domains

    payload = _mail(ctx)
    intake = _intake(ctx)
    owner_id = _owner(inst, ctx)
    account = str(payload.get("account") or "")
    sender = str(payload.get("from") or "")
    subject = str(payload.get("subject") or "")
    body = mail_text(payload)

    classify_agent = str(params.get("classify_agent") or intake.get("classify_agent") or "")
    if classify_agent:
        rule = evaluate(payload, my_addresses=await my_addresses(db),
                         known_domains=await known_domains(db, owner_id),
                         nonbusiness_domains=await nonbusiness_domains(db),
                         body=body)
        classification = await classify_email(db, owner_id, account=account, sender=sender,
                                      subject=subject, body=body,
                                      classify_agent=classify_agent,
                                      spam_hints=rule.reasons,
                                      spam_examples=await spam_learn.examples(db, owner_id))
    else:
        classification = {"category": "", "priority": "normal", "sensitive": False,
                  "redacted_summary": "", "spam_score": 0.0, "spam_reason": "",
                  "betrug": False, "merkmale": []}

    sender_email, domain = parse_sender(sender)
    policy = await match_policy(db, owner_id, sender_email=sender_email, domain=domain,
                                category=classification["category"])
    redaction, action_hint, auto = "redacted", "", False
    if policy is not None:
        await note_hit(db, policy)
        redaction, action_hint, auto = policy.redaction, policy.action_hint, policy.auto_approve
    # Redaction protects against raw text leaving the house. If a model on the own endpoint
    # processes the mail, nothing leaves the house, and then the redaction is no longer a
    # protection but only a loss of information (and a detour over the IMAP tools that
    # fetches the same text back anyway).
    agent = str(intake.get("agent") or "assistent")
    if await agent_running_local(db, owner_id, agent):
        redaction = "unredacted"
    if intake.get("auto_run"):  # the trigger enforces a chatless immediate run.
        auto = True

    inst.context = {**ctx, "classification": classification,
                    "policy": {"redaction": redaction, "action_hint": action_hint or "",
                               "auto": bool(auto), "id": policy.id if policy else None}}
    return {"action": "mail_classify", "category": classification["category"],
            "priority": classification["priority"], "auto": bool(auto),
            "classified": bool(classify_agent)}


async def spam_judge(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Pull rules, local model and memory together into one verdict.

    The rules are evaluated again here instead of being passed through the context: they are
    pure computation without access to the outside, and a findings object does not survive
    the way through a JSON column unharmed. What happens afterwards is decided by the branch
    in the graph; this verdict only says what was established.
    """
    from .spam_review import judge

    verdict_row = await judge(db, _owner(inst, ctx), _mail(ctx),
                              cls=(ctx.get("classification") or {}))
    inst.context = {**ctx, "spam": {**((ctx.get("spam") or {})), **verdict_row}}
    return {"action": "spam_evaluate", "score": verdict_row["score"],
            "settled": verdict_row["settled_verdict"] or "nein",
            "aktiv": verdict_row["aktiv"]}


async def spam_card(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
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
    stands in the context (`spam.card_title`, `spam.card_text`, `spam.card_kind`,
    `spam.card_due`), and the sending is done by a notify node behind it. That way
    switching off the message does not switch off the sorting.
    """
    from .spam_review import create, karte, report

    owner_id = _owner(inst, ctx)
    verdict_row = dict(ctx.get("spam") or {})
    task = ctx.get("task") or {}
    predecided = bool(params.get("predecided"))
    recoverable = bool(params.get("recoverable"))
    # Whether it is reported the flow decides. `report=false` means: create the verdict,
    # provide the text — sending it is done by a report node behind it, which one can switch
    # off without switching off the filing as well. The default stays `true` so that published
    # versions without such a node keep reporting.
    selbst_report = params.get("report")
    selbst_report = True if selbst_report is None else bool(selbst_report)
    verdict = await create(db, owner_id, verdict_row,
                            task_id=task.get("id") if isinstance(task, dict) else None,
                            instance_id=inst.id)
    immediate = predecided or recoverable or float(verdict_row.get("score") or 0.0) >= float(
        verdict_row.get("sofort_ab") or 0.9)
    if immediate and selbst_report:
        await report(db, owner_id, verdict, immediate=True, predecided=predecided,
                     recoverable=recoverable)
    kind = "rueckholbar" if recoverable else ("sofort" if immediate else "sammel")
    # The text belongs to the spam knowledge, not into the graph: which reasons are named and
    # how a recall hint reads changes with the detection. The report node takes
    # ihn als `{{ spam.card_title }}` / `{{ spam.card_text }}` und bleibt selbst allgemein.
    title, text = karte(verdict, predecided=predecided, recoverable=recoverable)
    inst.context = {**ctx, "spam": {**verdict_row, "verdict_id": verdict.id, "karte": kind,
                                    "card_title": title, "card_text": text,
                                    "card_due": bool(immediate),
                                    "card_kind": "spam_auto" if recoverable else "spam_review"}}
    return {"action": "spam_card", "verdict_id": verdict.id, "karte": kind,
            "vorentschieden": predecided, "selbst_gemeldet": bool(immediate and selbst_report)}


async def spam_execute(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Commit the verdict, learn from it and move the mail.

    The order is deliberate: first learn, then move. If moving fails (mail already cleared
    away, IMAP briefly gone), the decision still stays in the memory, because it was right,
    only not executable. That is why the step does not fail either; the result stands as text
    on the verdict and in the context.
    """
    from ..models.assistant import SpamVerdict
    from .spam_review import commit, imap_action

    verdict_row = dict(ctx.get("spam") or {})
    value = str(params.get("entscheidung") or verdict_row.get("entschieden") or "spam").strip().lower()
    is_spam = value not in ("ham", "kein_spam", "no", "false")
    vid = verdict_row.get("verdict_id")
    verdict = await db.get(SpamVerdict, int(vid)) if vid else None
    if verdict is None:
        return {"action": "spam_apply", "applied": False, "reason": "kein Urteil hinterlegt"}

    await commit(db, verdict, is_spam,
                        decided_by=str(params.get("decided_by")
                                       or verdict_row.get("entschieden_von") or "auto"))
    result = await imap_action(verdict, is_spam)
    verdict.action_result = result[:2000]
    inst.context = {**ctx, "spam": {**verdict_row, "entschieden": "spam" if is_spam else "ham",
                                    "ergebnis": result}}
    return {"action": "spam_apply", "verdict_id": verdict.id,
            "entscheidung": "spam" if is_spam else "ham", "ergebnis": result}


# ── Assistant: only the translation any more, no longer the work ─────────────
# Creating the intake, starting it, sending the approval card — that could once only be done
# by the mail path, with three actions of its own. But they are not mail matters:
# `assistent_auftrag` creates the intake (and starts it when no approval is needed), `notify`
# sends the card. What the mail intake contributes are its values — and those stand here as a
# parameter set so that the template and the legacy names use the same ones.
TASK_PARAMS: dict = {
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
    # Which field carries the text depends on the sender of the mail, not on the flow.
    "full_text": "{{ mail.body_text | default:mail.body | default:mail.body_html_as_text }}",
    "meta": {"account": "{{ mail.account }}", "uid": "{{ mail.uid }}",
             "from": "{{ mail.from }}", "subject": "{{ mail.subject }}",
             "sensitive": "{{ classification.sensitive }}"},
    # A learned rule releases it, everything else asks.
    "approval": {"!": {"var": "policy.auto"}},
}

# The card for approving: an ordinary message with a kind and a reference.
MAP_PARAMS: dict = {
    "kind": "assistant_review",
    "ref": {"assistant_task_id": "{{ task.id }}"},
    "title": "📥 {{ mail.subject | default:\"(kein Betreff)\" }}",
    # `from` arrives as text or as a list of {name, addr} depending on the reporter; the filter
    # chain pulls the address out in both cases instead of writing a Python list here.
    "text": "Von {{ mail.from | field:\"addr\" | join:\", \" | default:\"?\" }}\n"
            "{{ classification.redacted_summary }}",
}


async def assistant_item(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Altname `assistant_task`, umgeleitet auf den allgemeinen Knoten.

    It stands in published versions, and those are immutable — running instances hang on them.
    That is why it is redirected instead of maintained twice, as with `_OLD_ACTIONS`.
    """
    from .workflow_actions import _assistant_task
    return await _assistant_task(db, inst, {**TASK_PARAMS, **params}, ctx, "item")


async def assistant_card(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Legacy name `assistant_card`: the approval card is a message like any other."""
    from .workflow_actions import run_action
    return await run_action(db, inst, {"id": "freigabe_karte", "data": {"config": {
        "action": {"action": "notify", "params": {**MAP_PARAMS, **params}}}}})


async def assistant_run(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Legacy name `assistant_run`: the intake already starts on creation today.

    A second push would be a second run of the same matter — which is why only what has
    happened anyway is reported here.
    """
    task = dict(ctx.get("task") or {})
    return {"action": "assistant_run", "queued": False, "task_id": task.get("id"),
            "grund": "läuft bereits mit dem Anlegen"}


HANDLER = {
    "mail_classify": classify,
    "spam_evaluate": spam_judge,
    "spam_card": spam_card,
    "spam_apply": spam_execute,
    # The legacy names of the mail path. `assistant_task` today means the GENERAL assignment —
    # which is why the old ones carry a `mail_` prefix here, and the one-off conversion
    # (`workflow_terms.ONCE`) rewrites them onto that in the stored versions.
    "mail_assistant_task": assistant_item,
    "mail_assistant_card": assistant_card,
    "mail_assistant_run": assistant_run,
}
