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

NOW = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.timezone.utc)


async def _ticket(db, proj, user, agent_status: TicketAgentStatus) -> Issue:
    kind = IssueType(project_id=proj.id, name="Aufgabe", order=0)
    status = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([kind, status, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key=f"{proj.key}-1", type_id=kind.id,
                  status_id=status.id, summary="Test", rank="0001", reporter_id=user.id,
                  agent_status=agent_status)
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return issue


def _notice(user, kind: str, *, sent: bool, **kw) -> Notification:
    return Notification(user_id=user.id, kind=kind, title=kind,
                        chat_id="42" if sent else None,
                        notified_at=NOW if sent else None, **kw)


async def _title(client, user, all_rows: bool = False) -> list[str]:
    r = await client.get("/notifications" + ("?all=1" if all_rows else ""), headers=auth(user))
    assert r.status_code == 200
    return [n["title"] for n in r.json()]


async def test_sent_and_finished_items_disappear(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    done = await _ticket(db, proj, anna, TicketAgentStatus.done)
    db.add_all([
        _notice(anna, "plan_review", sent=True, issue_id=done.id),
        _notice(anna, "assistant", sent=True),
    ])
    await db.commit()

    assert await _title(client, anna) == []
    # Gone from the bell, not from the world.
    assert sorted(await _title(client, anna, all_rows=True)) == ["assistant", "plan_review"]
    r = await client.get("/notifications/unread-count", headers=auth(anna))
    assert r.json()["count"] == 0


async def test_without_sending_it_stays_visible(db, client):
    """A row that never went out anywhere is the message itself, not its echo."""
    anna = await make_user(db, "anna")
    db.add(_notice(anna, "assistant", sent=False))
    await db.commit()

    assert await _title(client, anna) == ["assistant"]
    r = await client.get("/notifications/unread-count", headers=auth(anna))
    assert r.json()["count"] == 1


async def test_a_waiting_ticket_stays_despite_sending(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    waits = await _ticket(db, proj, anna, TicketAgentStatus.plan_review)
    db.add(_notice(anna, "plan_review", sent=True, issue_id=waits.id))
    await db.commit()

    assert await _title(client, anna) == ["plan_review"]

    # Plan approved: the same row now counts as done, without anybody touching it.
    waits.agent_status = TicketAgentStatus.approved
    await db.commit()
    assert await _title(client, anna) == []


async def test_an_open_permission_question_remains(db, client):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "GLO", "Glocke")
    issue = await _ticket(db, proj, anna, TicketAgentStatus.hold)
    request = PermRequest(issue_id=issue.id, tool="bash", resource="*", status="pending")
    db.add_all([request, _notice(anna, "blocked", sent=True, issue_id=issue.id)])
    await db.commit()

    assert await _title(client, anna) == ["blocked"]

    request.status = "decided"
    await db.commit()
    assert await _title(client, anna) == []


async def test_assistant_and_spam_hang_on_their_own_state(db, client):
    anna = await make_user(db, "anna")
    open_ones = AssistantTask(source="mail", title="offen", status="new")
    done = AssistantTask(source="mail", title="erledigt", status="done")
    spam = SpamVerdict(status="pending")
    db.add_all([open_ones, done, spam])
    await db.commit()
    db.add_all([
        _notice(anna, "assistant_review", sent=True, assistant_task_id=open_ones.id),
        _notice(anna, "assistant_review", sent=True, assistant_task_id=done.id),
        _notice(anna, "spam_review", sent=True, spam_verdict_id=spam.id),
    ])
    await db.commit()

    assert sorted(await _title(client, anna)) == ["assistant_review", "spam_review"]
    assert len(await _title(client, anna, all_rows=True)) == 3


async def test_mark_all_read_also_clears_the_hidden_ones(db, client):
    """Otherwise a hidden unread row would keep a counter alive that no bell can reach."""
    anna = await make_user(db, "anna")
    db.add_all([_notice(anna, "assistant", sent=True),
                _notice(anna, "assistant", sent=False)])
    await db.commit()

    await client.post("/notifications/read-all", headers=auth(anna))
    r = await client.get("/notifications?all=1", headers=auth(anna))
    assert all(n["read"] for n in r.json())
