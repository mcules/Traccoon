"""Der Mail-Eingang als Ablauf: melden → beurteilen → fragen → wegräumen.

Geprüft wird die Mechanik, nicht die Treffsicherheit der Erkennung: dass eine Mail einen
Lauf startet, dass ein Verdacht am Genehmigungs-Knoten wartet statt heimlich zu verschieben,
dass die Antwort aus Telegram genau diesen Lauf weiterschaltet — und dass eine unauffällige
Mail ihren gewohnten Weg zum Assistenten geht.
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
    # Damit die Rückfrage im Test sofort als eigene Karte kommt statt auf den
    # Sammel-Takt zu warten — geprüft wird der Weg, nicht die Höhe der Schwelle.
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
    """Eine Mail, die jede Schwelle reißt — Absender erfunden, Antwort umgeleitet, nichts
    besteht die Echtheitsprüfung."""
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
    # Kein Spam-Urteil, dafür die gewohnte Freigabekarte.
    assert (await db.execute(select(SpamVerdict))).scalars().all() == []
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "assistant_review" and karte.assistant_task_id == task.id


async def test_verdacht_wartet_auf_die_antwort(db, owner, imap_stub):
    """Nichts wird verschoben, bevor ein Mensch geantwortet hat — das ist die Leitplanke."""
    inst = await _melden(db, owner, _verdaechtig())

    assert inst.status == WorkflowInstanceStatus.waiting
    schritt = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id,
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().one()
    assert schritt.node_id == "rueckfrage"
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()
    assert verdict.status == "pending" and verdict.workflow_instance_id == inst.id
    assert imap_stub == []
    # Die Karte mit den Knöpfen kommt aus der Spam-Erkennung, nicht als zweite
    # Workflow-Meldung — sonst stünden zwei Nachrichten zur selben Mail im Chat.
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
    # Und gelernt wird auch: dieselbe Adresse fällt beim nächsten Mal schneller auf.
    score, _, _ = await spam_learn.bewerten(db, owner.id, ["from:x@4t7k.xyz"])
    assert score > 0.5


async def test_kein_spam_fuehrt_die_mail_zum_assistenten(db, owner, imap_stub):
    """„Kein Spam" ist kein Papierkorb: die Mail soll danach ganz normal bearbeitet werden."""
    inst = await _melden(db, owner, _verdaechtig())
    verdict = (await db.execute(select(SpamVerdict))).scalars().one()

    await spam_review.entscheiden(db, verdict, False)

    assert imap_stub[0][0] == "mark_not_spam"
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.title == "Sie haben GEWONNEN!!!"


async def test_geklaerter_absender_wird_ohne_rueckfrage_weggeraeumt(db, owner, imap_stub):
    """Nach drei einhelligen „ist Spam" entscheidet das Gedächtnis allein — gemeldet wird
    trotzdem, sonst fiele ein eingeschlichener Irrtum nie auf."""
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


# ── Stufe 2: verschieben ohne zu fragen, aber widersprechlich ────────────────

async def test_auto_schwelle_verschiebt_ohne_rueckfrage(db, owner, imap_stub):
    """Über der Auto-Schwelle wird nicht mehr gefragt — die Karte trägt den Rückweg."""
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
    # Und der Absender ist gemerkt: derselbe Irrtum passiert nicht noch einmal.
    score, _, _ = await spam_learn.bewerten(db, owner.id, ["from:x@4t7k.xyz"])
    assert score < 0.5
    from app.models.assistant import AssistantPolicy
    regel = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.match_value == "x@4t7k.xyz"))).scalar_one()
    assert regel.match_kind == "sender"


async def test_auto_ist_ab_werk_aus(db, owner, imap_stub):
    """Ohne ausdrückliche Entscheidung des Menschen bleibt es bei der Rückfrage."""
    inst = await _melden(db, owner, _verdaechtig(uid=8003))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []


async def test_serverurteil_raeumt_ohne_rueckfrage_weg(db, owner, imap_stub):
    """Der Fall vom 2026-08-18: vier Mails mit `***SPAM***` im Betreff, vom eigenen Server
    mit 13 Punkten bewertet — und trotzdem ein Gesamturteil von nur ~0.55. Ohne den
    Sonderweg bliebe jede Auto-Schwelle wirkungslos."""
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
    # Die Punktzahl allein hätte es NICHT über die Schwelle geschafft — das Serverurteil hat.
    assert verdict.score < 0.95
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.kind == "spam_auto"


async def test_serverurteil_schweigt_solange_auto_aus_ist(db, owner, imap_stub):
    """Ohne gesetzte Auto-Schwelle bleibt auch das Serverurteil eine Frage."""
    inst = await _melden(db, owner, _mail(uid=9002, **{
        "from": [{"name": "", "addr": "wer@zufall.top"}],
        "subject": "***SPAM*** Angebot",
        "headers": {"X-Spam-Flag": "YES", "Received-Count": 1}}))
    assert inst.status == WorkflowInstanceStatus.waiting
    assert imap_stub == []
