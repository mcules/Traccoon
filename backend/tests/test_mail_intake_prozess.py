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
from app.services import mail_intake, spam_learn, spam_review
from app.services.appsettings import set_setting
from app.services.workflow_seed import ensure_builtin_set
from sqlalchemy import select

from conftest import make_user


@pytest.fixture
async def owner(db):
    await ensure_builtin_set(db)
    user = await make_user(db, "dennis")
    user.telegram_chat_id = "4242"
    await db.commit()
    # So that the question comes as a card of its own immediately in the test instead of
    # waiting for the digest beat: what is checked is the path, not the height of the threshold.
    await set_setting(db, spam_review.SOFORT_AB_KEY, "0.5")
    return user


@pytest.fixture
def imap_stub(monkeypatch):
    """`imap-mcp` durch einen Mitschrieb ersetzen."""
    aufrufe = []

    async def fake_call_tool(url, tool, arguments, **kw):
        aufrufe.append((tool, arguments))
        return {"content": [{"type": "text", "text": "verschoben nach Spam"}]}

    monkeypatch.setattr(spam_review, "call_tool", fake_call_tool)
    return aufrufe


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


def _verdaechtig(uid: int = 5001) -> dict:
    """A mail that tears every threshold: an invented sender, a redirected reply, and nothing
    passes the authenticity check."""
    return _mail(uid=uid, **{
        "from": [{"name": "", "addr": "x@4t7k.xyz"}],
        "reply_to": [{"name": "", "addr": "kasse@anders.ru"}],
        "subject": "Sie haben GEWONNEN!!!",
        "headers": {"Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
                    "Return-Path": "<b@anders.ru>", "Received-Count": 1}})


async def _melden(db, owner, payload) -> WorkflowInstance:
    ids = await mail_intake.intake_mail(db, owner.id, payload, source="mail",
                                        agent="assistent")
    assert len(ids) == 1, "genau ein ausgelieferter Mail-Eingang soll anlaufen"
    return await db.get(WorkflowInstance, ids[0])


async def test_unauffaellige_mail_geht_zum_assistenten(db, owner):
    inst = await _melden(db, owner, _mail())

    assert inst.status == WorkflowInstanceStatus.completed
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.title == "Ihre Bestellung"
    # No spam verdict, but the usual approval card.
    assert (await db.execute(select(SpamVerdict))).scalars().all() == []
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "assistant_review" and karte.assistant_task_id == task.id


async def test_verdacht_wartet_auf_die_antwort(db, owner, imap_stub):
    """Nothing is moved before a human has answered; that is the guard rail."""
    inst = await _melden(db, owner, _verdaechtig())

    assert inst.status == WorkflowInstanceStatus.waiting
    schritt = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id,
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().one()
    assert schritt.node_id == "rueckfrage"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "pending" and verdict.workflow_instance_id == inst.id
    assert imap_stub == []
    # The card with the buttons comes from the spam detection, not as a second workflow
    # message; otherwise two messages about the same mail would stand in the chat.
    karten = (await db.execute(select(Notification))).scalars().all()
    assert [k.kind for k in karten] == ["spam_review"]


async def test_antwort_aus_telegram_schaltet_den_ablauf_weiter(db, owner, imap_stub):
    inst = await _melden(db, owner, _verdaechtig())
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    ergebnis = await spam_review.entscheiden(db, verdict, True)

    assert imap_stub == [("mark_spam", {"account": "privat", "folder": "INBOX", "uid": 5001})]
    assert "verschoben" in ergebnis
    await db.refresh(verdict)
    assert verdict.status == "spam" and verdict.decided_by == "telegram"
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed
    # And learning happens as well: the same address stands out faster next time.
    score, _, _ = await spam_learn.bewerten(db, owner.id, ["from:x@4t7k.xyz"])
    assert score > 0.5


async def test_kein_spam_fuehrt_die_mail_zum_assistenten(db, owner, imap_stub):
    """"Not spam" is no waste bin: the mail should be handled completely normally afterwards."""
    inst = await _melden(db, owner, _verdaechtig())
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    await spam_review.entscheiden(db, verdict, False)

    assert imap_stub[0][0] == "mark_not_spam"
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.title == "Sie haben GEWONNEN!!!"


async def test_geklaerter_absender_wird_ohne_rueckfrage_weggeraeumt(db, owner, imap_stub):
    """After three unanimous "is spam" the memory decides alone; reporting happens regardless,
    because otherwise an error that crept in would never stand out."""
    for i in range(3):
        v = SpamVerdict(owner_user_id=owner.id, sender_email="werber@versand.example",
                        features=["from:werber@versand.example"], status="spam")
        db.add(v)
        await db.flush()
        await spam_learn.merken(db, v, True)
    await db.commit()

    inst = await _melden(db, owner, _mail(
        uid=6001, **{"from": [{"name": "Versand", "addr": "werber@versand.example"}],
                     "message_id": "<x@versand.example>",
                     "headers": {"Authentication-Results":
                                 "mx; spf=pass; dkim=pass; dmarc=pass",
                                 "Return-Path": "<bounce@versand.example>",
                                 "Received-Count": 3}}))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    frisch = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.workflow_instance_id == inst.id))).scalars().one()
    assert frisch.status == "spam" and frisch.decided_by == "auto"
    karte = (await db.execute(select(Notification).where(
        Notification.spam_verdict_id == frisch.id))).scalars().one()
    assert "gelernt" in karte.title.lower()


# ── Stage 2: moving without asking, but contestably ──────────────────────────

async def test_auto_schwelle_verschiebt_ohne_rueckfrage(db, owner, imap_stub):
    """Above the auto threshold nothing is asked any more; the card carries the way back."""
    await set_setting(db, spam_review.AUTO_AB_KEY, "0.5")
    inst = await _melden(db, owner, _verdaechtig(uid=8001))

    assert inst.status == WorkflowInstanceStatus.completed
    assert imap_stub[0][0] == "mark_spam"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "spam" and verdict.decided_by == "auto"
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto"
    assert "rückgängig" in karte.body.lower() or "zurück" in karte.body.lower()


async def test_rueckholen_lernt_den_absender_als_erwuenscht(db, owner, imap_stub):
    await set_setting(db, spam_review.AUTO_AB_KEY, "0.5")
    await _melden(db, owner, _verdaechtig(uid=8002))
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    ergebnis = await spam_review.zurueckholen(db, verdict)

    assert imap_stub[-1][0] == "mark_not_spam"
    assert "verschoben" in ergebnis or ergebnis
    await db.refresh(verdict)
    assert verdict.status == "ham"
    # And the sender is remembered: the same error does not happen again.
    score, _, _ = await spam_learn.bewerten(db, owner.id, ["from:x@4t7k.xyz"])
    assert score < 0.5
    from app.models.assistant import AssistantPolicy
    regel = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.match_value == "x@4t7k.xyz"))).scalar_one()
    assert regel.match_kind == "sender"


async def test_auto_ist_ab_werk_aus(db, owner, imap_stub):
    """Without an explicit decision of the human it stays with the question."""
    inst = await _melden(db, owner, _verdaechtig(uid=8003))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []


async def test_serverurteil_raeumt_ohne_rueckfrage_weg(db, owner, imap_stub):
    """The case from 2026-08-18: four mails with `***SPAM***` in the subject, rated with 13
    points by the own server, and still an overall verdict of only ~0.55. Without the special
    path every auto threshold would stay ineffective."""
    await set_setting(db, spam_review.AUTO_AB_KEY, "0.95")
    inst = await _melden(db, owner, _mail(uid=9001, **{
        "from": [{"name": "Dr. Sarah Bergmann", "addr": "support@google.com"}],
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


async def test_serverurteil_schweigt_solange_auto_aus_ist(db, owner, imap_stub):
    """Without a set auto threshold the server verdict stays a question as well."""
    inst = await _melden(db, owner, _mail(uid=9002, **{
        "from": [{"name": "", "addr": "wer@zufall.top"}],
        "subject": "***SPAM*** Angebot",
        "headers": {"X-Spam-Flag": "YES", "Received-Count": 1}}))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []
