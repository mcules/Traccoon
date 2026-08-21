"""The mail inbox as a flow: report, assess, ask, clear away.

What is checked is the mechanics, not the accuracy of the detection: that a mail starts a
run, that a suspicion waits at the approval node instead of moving secretly, that the answer
from Telegram advances exactly this run, and that an inconspicuous mail goes its usual way to
the assistant.
"""
import pytest
from app.models.assistant import AssistantTask, SpamVerdict
from app.models.enums import WorkflowInstanceStatus, WorkflowStepStatus
from app.models.notification import Notification
from app.models.workflow import WorkflowInstance, WorkflowStepRun
from app.services import spam_learn, spam_review
from app.services.appsettings import set_setting
from app.services import workflow_templates
from sqlalchemy import select

from conftest import make_webhook, report, make_user


@pytest.fixture
async def owner(db):
    user = await make_user(db, "dennis")
    user.telegram_chat_id = "4242"
    # The mail intake is NOT a shipped flow any more but one out of the template — it belongs
    # to the person who created it. So the test creates it that way too.
    await workflow_templates.create(db, "mail-eingang", owner_id=user.id)
    await db.commit()
    # So that the question comes as a card of its own immediately in the test instead of
    # waiting for the digest beat: what is checked is the path, not the height of the threshold.
    await set_setting(db, spam_review.IMMEDIATE_FROM_KEY, "0.5")
    return user


@pytest.fixture
def imap_stub(monkeypatch):
    """Replace `imap-mcp` by a transcript."""
    calls = []

    async def fake_call_tool(url, tool, arguments, **kw):
        calls.append((tool, arguments))
        return {"content": [{"type": "text", "text": "verschoben nach Spam"}]}

    monkeypatch.setattr(spam_review, "call_tool", fake_call_tool)
    return calls


def _mail(**over) -> dict:
    payload = {
        "account": "privat", "folder": "INBOX", "uid": 4711,
        "from": [{"name": "Shop", "addr": "info@shop.de"}],
        "to": [{"name": "", "addr": "ich@meine-domain.de"}],
        "subject": "Ihre Bestellung", "message_id": "<abc123@shop.de>",
        "date": "2026-08-17T10:00:00+02:00", "timestamp": "2026-08-17T10:00:05+02:00",
        "body_text": "Danke für Ihre Bestellung.", "links": [], "attachments": [],
        "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                    "Return-Path": "<bounce@shop.de>", "Received-Count": 3},
    }
    payload.update(over)
    return payload


def _suspicious(uid: int = 5001) -> dict:
    """A mail that tears every threshold: an invented sender, a redirected reply, and nothing
    passes the authenticity check."""
    return _mail(uid=uid, **{
        "from": [{"name": "", "addr": "x@4t7k.xyz"}],
        "reply_to": [{"name": "", "addr": "kasse@anders.ru"}],
        "subject": "Sie haben GEWONNEN!!!",
        "headers": {"Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
                    "Return-Path": "<b@anders.ru>", "Received-Count": 1}})


async def _report(db, owner, payload, *, classify_agent: str = "") -> WorkflowInstance:
    """Take the path production takes: a webhook in, an event out, the flow runs.

    The mail intake hangs on no special path any more — it is a trigger like any other, and
    what the steps need to know about it the webhook builds into the context.
    """
    sub = await make_webhook(db, owner, "mail-test", mode="assistant", agent="assistent",
                             classify_agent=classify_agent)
    ids = await report(db, sub, payload)
    assert len(ids) == 1, "exactly one shipped mail inbox should start"
    return await db.get(WorkflowInstance, ids[0])


async def test_an_unremarkable_mail_goes_to_the_assistant(db, owner):
    inst = await _report(db, owner, _mail())

    assert inst.status == WorkflowInstanceStatus.completed
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.title == "Ihre Bestellung"
    # No spam verdict, but the usual approval card.
    assert (await db.execute(select(SpamVerdict))).scalars().all() == []
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "assistant_review" and karte.assistant_task_id == task.id


async def test_a_suspicion_waits_for_the_answer(db, owner, imap_stub):
    """Nothing is moved before a human has answered; that is the guard rail."""
    inst = await _report(db, owner, _suspicious())

    assert inst.status == WorkflowInstanceStatus.waiting
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id,
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().one()
    assert step.node_id == "rueckfrage"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "pending" and verdict.workflow_instance_id == inst.id
    assert imap_stub == []
    # The card with the buttons comes from the spam detection, not as a second workflow
    # message; otherwise two messages about the same mail would stand in the chat.
    maps = (await db.execute(select(Notification))).scalars().all()
    assert [k.kind for k in maps] == ["spam_review"]


async def test_an_answer_from_telegram_advances_the_flow(db, owner, imap_stub):
    inst = await _report(db, owner, _suspicious())
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    result = await spam_review.decide(db, verdict, True)

    assert imap_stub == [("mark_spam", {"account": "privat", "folder": "INBOX", "uid": 5001})]
    assert "verschoben" in result
    await db.refresh(verdict)
    assert verdict.status == "spam" and verdict.decided_by == "telegram"
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed
    # And learning happens as well: the same address stands out faster next time.
    score, _, _ = await spam_learn.rate(db, owner.id, ["from:x@4t7k.xyz"])
    assert score > 0.5


async def test_not_spam_leads_the_mail_to_the_assistant(db, owner, imap_stub):
    """"Not spam" is no waste bin: the mail should be handled completely normally afterwards."""
    inst = await _report(db, owner, _suspicious())
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    await spam_review.decide(db, verdict, False)

    assert imap_stub[0][0] == "mark_not_spam"
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.title == "Sie haben GEWONNEN!!!"


async def test_a_settled_sender_is_filed_away_without_asking(db, owner, imap_stub):
    """After three unanimous "is spam" the memory decides alone; reporting happens regardless,
    because otherwise an error that crept in would never stand out."""
    for i in range(3):
        v = SpamVerdict(owner_user_id=owner.id, sender_email="werber@versand.example",
                        features=["from:werber@versand.example"], status="spam")
        db.add(v)
        await db.flush()
        await spam_learn.remember(db, v, True)
    await db.commit()

    inst = await _report(db, owner, _mail(
        uid=6001, **{"from": [{"name": "Versand", "addr": "werber@versand.example"}],
                     "message_id": "<x@versand.example>",
                     "headers": {"Authentication-Results":
                                 "mx; spf=pass; dkim=pass; dmarc=pass",
                                 "Return-Path": "<bounce@versand.example>",
                                 "Received-Count": 3}}))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    fresh = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.workflow_instance_id == inst.id))).scalars().one()
    assert fresh.status == "spam" and fresh.decided_by == "auto"
    karte = (await db.execute(select(Notification).where(
        Notification.spam_verdict_id == fresh.id))).scalars().one()
    assert "gelernt" in karte.title.lower()


# ── Stage 2: moving without asking, but contestably ──────────────────────────

async def test_the_auto_threshold_moves_it_without_asking(db, owner, imap_stub):
    """Above the auto threshold nothing is asked any more; the card carries the way back."""
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.5")
    inst = await _report(db, owner, _suspicious(uid=8001))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam" and verdict.decided_by == "auto"
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto"
    assert "rückgängig" in karte.body.lower() or "zurück" in karte.body.lower()


async def test_recovering_learns_the_sender_as_wanted(db, owner, imap_stub):
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.5")
    await _report(db, owner, _suspicious(uid=8002))
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    result = await spam_review.reclaim(db, verdict)

    assert imap_stub[-1][0] == "mark_not_spam"
    assert "verschoben" in result or result
    await db.refresh(verdict)
    assert verdict.status == "ham"
    # And the sender is remembered: the same error does not happen again.
    score, _, _ = await spam_learn.rate(db, owner.id, ["from:x@4t7k.xyz"])
    assert score < 0.5
    from app.models.assistant import AssistantPolicy
    rule = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.match_value == "x@4t7k.xyz"))).scalar_one()
    assert rule.match_kind == "sender"


async def test_auto_is_off_by_default(db, owner, imap_stub):
    """Without an explicit decision of the human it stays with the question."""
    inst = await _report(db, owner, _suspicious(uid=8003))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []


async def test_the_server_verdict_files_it_away_without_asking(db, owner, imap_stub):
    """The case from 2026-08-18: four mails with `***SPAM***` in the subject, rated with 13
    points by the own server, and still an overall verdict of only ~0.55. Without the special
    path every auto threshold would stay ineffective."""
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.95")
    inst = await _report(db, owner, _mail(uid=9001, **{
        "from": [{"name": "Dr. Beispiel Person", "addr": "support@suchmaschine.example"}],
        "subject": "***SPAM*** Löwen-Deal: 8,3 kg Fettverlust pro Monat",
        "headers": {"Authentication-Results": "mx; spf=pass", "X-Spam-Flag": "YES",
                    "X-Spam-Level": "*************", "Received-Count": 2}}))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam" and verdict.decided_by == "auto"
    # The score alone would NOT have made it over the threshold; the server verdict did.
    assert verdict.score < 0.95
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto"


async def test_the_server_verdict_stays_quiet_while_auto_is_off(db, owner, imap_stub):
    """Without a set auto threshold the server verdict stays a question as well."""
    inst = await _report(db, owner, _mail(uid=9002, **{
        "from": [{"name": "", "addr": "wer@zufall.top"}],
        "subject": "***SPAM*** Angebot",
        "headers": {"X-Spam-Flag": "YES", "Received-Count": 1}}))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []


# ── Reporting is a step, not a side effect ──────────────────────────────────

async def _report_node_disable(db, node_id: str) -> None:
    """Switch the report step off — the same thing the checkbox in the editor does."""
    import copy

    from app.models.enums import WorkflowVersionStatus
    from app.models.workflow import WorkflowDefinition, WorkflowVersion

    d = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.key == "mail-eingang"))).scalars().first()
    old = await db.get(WorkflowVersion, d.current_version_id)
    graph = copy.deepcopy(old.graph)
    hits = [n for n in graph["nodes"] if n["id"] == node_id]
    assert hits, f"Knoten {node_id} steht nicht im ausgelieferten Ablauf"
    hits[0]["data"]["config"].update(deaktiviert=True, deaktiviert_modus="ueberspringen")
    new = WorkflowVersion(definition_id=d.id, version=old.version + 1, graph=graph,
                          status=WorkflowVersionStatus.published)
    db.add(new)
    await db.flush()
    d.current_version_id = new.id
    await db.commit()


async def test_reporting_can_be_switched_off_without_the_filing(db, owner, imap_stub):
    """The actual point of the separation: no sound, but the mail goes away all the same.

    Before, the card hung in the same action as the verdict. Whoever wanted to get rid of the
    message would have had to switch the step off — and with it the verdict that moving and
    learning hang on. Now the switch hits the message only.
    """
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.5")
    await _report_node_disable(db, "melde_auto")

    inst = await _report(db, owner, _suspicious(uid=8100))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam", "die Mail wird weiterhin weggeräumt"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam", "das Urteil entsteht und wird gelernt"
    assert (await db.execute(select(Notification))).scalars().all() == [], "kein Ton"


async def test_the_notify_node_attaches_the_card_to_the_verdict(db, owner, imap_stub):
    """The card has to stay actionable: the button "recall" needs the reference."""
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.5")
    inst = await _report(db, owner, _suspicious(uid=8101))

    assert inst.status == WorkflowInstanceStatus.completed
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto" and karte.spam_verdict_id == verdict.id
    assert karte.user_id == owner.id and karte.chat_id == owner.telegram_chat_id


async def test_the_setting_switches_off_the_notice_not_the_filing(db, owner, imap_stub):
    """The switch for everyday use: without a copy of the flow, only through the setting.

    A process set of its own would be the wrong way here — it is a full copy and would run
    next to the shipped flow, so twice per mail. That is why the decision in front of the
    report step reads the value out of the verdict.
    """
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.5")
    await set_setting(db, spam_review.AUTO_REPORT_KEY, "0")

    inst = await _report(db, owner, _suspicious(uid=8200))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam", "die Mail wird weiterhin weggeräumt"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam", "das Urteil steht in der Übersicht und wird gelernt"
    assert (await db.execute(select(Notification))).scalars().all() == [], "kein Ton"


async def test_the_question_remains_despite_auto_reporting_being_off(db, owner, imap_stub):
    """Whoever wants to be asked is asked: the switch applies only to what is cleared away
    WITHOUT a question."""
    await set_setting(db, spam_review.AUTO_REPORT_KEY, "0")

    inst = await _report(db, owner, _suspicious(uid=8201))

    assert inst.status == WorkflowInstanceStatus.waiting, "die Frage steht noch offen"
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_review"


# ── Das Urteil des lokalen Modells im Ablauf ─────────────────────────────────

@pytest.fixture
def model_stub(monkeypatch):
    """Replace the local classification. `mail_actions` imports it inside the function, so
    patching the module the function reads from is what takes hold."""
    from app.services import mail_classify

    def answer(**over):
        async def fake(*a, **kw):
            return {"category": "sonstiges", "priority": "normal", "sensitive": False,
                    "redacted_summary": "", "spam_score": 0.95, "spam_reason": "Phishing",
                    "betrug": True, "merkmale": [{"kennung": "marke_fremde_domain",
                                                  "text": "gibt sich als Bank aus"}],
                    **over}
        monkeypatch.setattr(mail_classify, "classify_email", fake)
    return answer


def _n26(uid: int = 9101) -> dict:
    """Technically flawless, and still a forgery: brand name, foreign domain."""
    return _mail(uid=uid, **{
        "from": [{"name": "Bank", "addr": "kundensicherheitscenter@fremde-firma.example"}],
        "subject": "Neue Mitteilung",
        "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                    "Return-Path": "<b@fremde-firma.example>", "Received-Count": 3}})


async def _report_classified(db, owner, payload) -> WorkflowInstance:
    return await _report(db, owner, payload, classify_agent="mail_classifier")


async def test_the_model_verdict_files_it_away_without_asking(db, owner, imap_stub, model_stub):
    """The case of 2026-08-19: rules 0.4, model 0.95, and the mixture landed at 0.731."""
    model_stub()
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.95")
    inst = await _report_classified(db, owner, _n26())

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam" and verdict.decided_by == "auto"
    assert verdict.rule_score < 0.5, "die Regeln allein hätten es nie getragen"
    assert verdict.kind == "phishing"
    assert any(b["quelle"] == "modell" for b in verdict.findings)
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto"


async def test_the_model_verdict_stays_quiet_while_auto_is_off(db, owner, imap_stub, model_stub):
    """Auto off is a deliberate decision of a human; the model does not overrule it."""
    model_stub()
    inst = await _report_classified(db, owner, _n26(uid=9102))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []


async def test_without_a_fraud_verdict_everything_stays_as_it_was(db, owner, imap_stub, model_stub):
    """Advertising the model is sure about is still not fraud, and stays a question."""
    model_stub(betrug=False, category="werbung", spam_reason="unerwünschte Werbung")
    await set_setting(db, spam_review.AUTO_FROM_KEY, "0.95")
    inst = await _report_classified(db, owner, _n26(uid=9103))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []
