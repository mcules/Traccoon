"""The bell shows what is still open, not what already went out over the messenger.

The occasion is a real installation: of around 420 notifications 400 had gone out over
Telegram, been read there and settled. Under the bell they all stood again, so the bell
became a second inbox that could not be worked off. The rule now: a row belongs to the
bell as long as nothing went out about it (then the bell is the only trace), or as long
as its subject is still waiting for a decision.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantTask, SpamVerdict
from app.models.enums import StatusCategory, TicketAgentStatus
from app.models.notification import Notification
from app.models.ops import PermRequest
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus

from conftest import auth, make_project, make_user

pytestmark = pytest.mark.asyncio

JETZT = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.timezone.utc)


async def _ticket(db, proj, user, agent_status: TicketAgentStatus) -> Issue:
    typ = IssueType(project_id=proj.id, name="Aufgabe", order=0)
    status = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([typ, status, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key=f"{proj.key}-1", type_id=typ.id,
                  status_id=status.id, summary="Test", rank="0001", reporter_id=user.id,
                  agent_status=agent_status)
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


def _meldung(user, kind: str, *, gesendet: bool, **kw) -> Notification:
    return Notification(user_id=user.id, kind=kind, title=kind,
                        chat_id="42" if gesendet else None,
                        notified_at=JETZT if gesendet else None, **kw)


async def _titel(client, user, alle: bool = False) -> list[str]:
    r = await client.get("/notifications" + ("?alle=1" if alle else ""), headers=auth(user))
    assert r.status_code == 200
    return [n["title"] for n in r.json()]


async def test_gesendetes_und_erledigtes_verschwindet(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    fertig = await _ticket(db, proj, anna, TicketAgentStatus.done)
    db.add_all([
        _meldung(anna, "plan_review", gesendet=True, issue_id=fertig.id),
        _meldung(anna, "assistant", gesendet=True),
    ])
    await db.commit()

    assert await _titel(client, anna) == []
    # Gone from the bell, not from the world.
    assert sorted(await _titel(client, anna, alle=True)) == ["assistant", "plan_review"]
    r = await client.get("/notifications/unread-count", headers=auth(anna))
    assert r.json()["count"] == 0


async def test_ohne_versand_bleibt_sichtbar(db, client):
    """A row that never went out anywhere is the message itself, not its echo."""
    anna = await make_user(db, "anna")
    db.add(_meldung(anna, "assistant", gesendet=False))
    await db.commit()

    assert await _titel(client, anna) == ["assistant"]
    r = await client.get("/notifications/unread-count", headers=auth(anna))
    assert r.json()["count"] == 1


async def test_wartendes_ticket_bleibt_trotz_versand(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    wartet = await _ticket(db, proj, anna, TicketAgentStatus.plan_review)
    db.add(_meldung(anna, "plan_review", gesendet=True, issue_id=wartet.id))
    await db.commit()

    assert await _titel(client, anna) == ["plan_review"]

    # Plan approved: the same row now counts as done, without anybody touching it.
    wartet.agent_status = TicketAgentStatus.approved
    await db.commit()
    assert await _titel(client, anna) == []


async def test_offene_rechtefrage_bleibt(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    issue = await _ticket(db, proj, anna, TicketAgentStatus.hold)
    anfrage = PermRequest(issue_id=issue.id, tool="bash", resource="*", status="pending")
    db.add_all([anfrage, _meldung(anna, "blocked", gesendet=True, issue_id=issue.id)])
    await db.commit()

    assert await _titel(client, anna) == ["blocked"]

    anfrage.status = "decided"
    await db.commit()
    assert await _titel(client, anna) == []


async def test_assistent_und_spam_haengen_am_eigenen_zustand(db, client):
    anna = await make_user(db, "anna")
    offen = AssistantTask(source="mail", title="offen", status="new")
    erledigt = AssistantTask(source="mail", title="erledigt", status="done")
    spam = SpamVerdict(status="pending")
    db.add_all([offen, erledigt, spam])
    await db.commit()
    db.add_all([
        _meldung(anna, "assistant_review", gesendet=True, assistant_task_id=offen.id),
        _meldung(anna, "assistant_review", gesendet=True, assistant_task_id=erledigt.id),
        _meldung(anna, "spam_review", gesendet=True, spam_verdict_id=spam.id),
    ])
    await db.commit()

    assert sorted(await _titel(client, anna)) == ["assistant_review", "spam_review"]
    assert len(await _titel(client, anna, alle=True)) == 3


async def test_alle_gelesen_raeumt_auch_das_verborgene(db, client):
    """Otherwise a hidden unread row would keep a counter alive that no bell can reach."""
    anna = await make_user(db, "anna")
    db.add_all([_meldung(anna, "assistant", gesendet=True),
                _meldung(anna, "assistant", gesendet=False)])
    await db.commit()

    await client.post("/notifications/read-all", headers=auth(anna))
    r = await client.get("/notifications?alle=1", headers=auth(anna))
    assert all(n["read"] for n in r.json())
