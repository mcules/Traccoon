"""Answered Telegram questions carry no buttons any more.

The occasion: after a press the keyboard stayed. In the history it was then no longer visible
which question had already been answered and which was still open, and the old buttons kept
inviting a press.
"""
import pytest
from app.bot.__main__ import _done


class FakeMessage:
    def __init__(self, text="<b>Frage</b>\nInhalt", edit_text_error=False):
        self.html_text = text
        self.message_id = 42
        self.edit_text_error = edit_text_error
        self.processed = None
        self.markup_removed = False

    async def edit_text(self, text, **_kw):
        if self.edit_text_error:
            raise RuntimeError("message is not modified")
        self.processed = text

    async def edit_reply_markup(self, reply_markup=None):
        assert reply_markup is None
        self.markup_removed = True


class FakeCq:
    def __init__(self, message):
        self.message = message


async def test_a_note_replaces_the_keyboard():
    msg = FakeMessage()
    await _done(FakeCq(msg), "✅ Freigegeben")
    # edit_text without reply_markup removes the keyboard with it, and the outcome stands there.
    assert msg.processed.startswith("<b>Frage</b>\nInhalt")
    assert "✅ Freigegeben" in msg.processed
    assert "<i>" in msg.processed


async def test_an_old_message_at_least_loses_its_buttons():
    """Too old or unchanged means the text stays, but the buttons have to go."""
    msg = FakeMessage(edit_text_error=True)
    await _done(FakeCq(msg), "✅ Freigegeben")
    assert msg.markup_removed


async def test_no_crash_without_a_message():
    await _done(FakeCq(None), "✅ Freigegeben")


@pytest.mark.parametrize("raw, expected", [("<b>böse</b>", "&lt;b&gt;"), ("A & B", "&amp;")])
async def test_the_note_is_escaped(raw, expected):
    """The note goes out as HTML: unescaped markup takes the message apart."""
    msg = FakeMessage()
    await _done(FakeCq(msg), raw)
    assert expected in msg.processed


# ── Decided elsewhere: the buttons in the chat come down ────────────────────

from app.bot.__main__ import _settled_elsewhere  # noqa: E402
from app.models.enums import ProjectRole, TicketAgentStatus  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.ops import PermRequest  # noqa: E402
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus  # noqa: E402
from conftest import add_member, make_project, make_user  # noqa: E402


async def _ticket(db, agent_status):
    boss = await make_user(db, "chef")
    proj = await make_project(db, "TGB", "Buttons")
    await add_member(db, proj, boss, ProjectRole.owner)
    t = IssueType(project_id=proj.id, name="Task")
    st = WorkflowStatus(project_id=proj.id, name="To Do", category="todo", order=0)
    db.add_all([t, st, IssueCounter(project_id=proj.id, last_number=0)])
    await db.flush()
    iss = Issue(project_id=proj.id, number=1, key="TGB-1", type_id=t.id, status_id=st.id,
                summary="x", reporter_id=boss.id, rank="1", agent_status=agent_status)
    db.add(iss)
    await db.commit()
    return iss


def _note(iss, kind):
    return Notification(issue_id=iss.id, kind=kind, chat_id="1", tg_message_id=7, buttons_open=True)


async def test_plan_buttons_stand_while_the_plan_waits_and_fall_once_it_is_decided(db):
    iss = await _ticket(db, TicketAgentStatus.plan_review)
    n = _note(iss, "plan_review")
    assert await _settled_elsewhere(db, n) is False
    iss.agent_status = TicketAgentStatus.approved     # approved in the web interface
    await db.commit()
    assert await _settled_elsewhere(db, n) is True


async def test_permission_buttons_fall_when_no_request_is_pending_any_more(db):
    iss = await _ticket(db, TicketAgentStatus.hold)
    pr = PermRequest(issue_id=iss.id, tool="x", resource="*", status="pending")
    db.add(pr)
    await db.commit()
    n = _note(iss, "blocked")
    assert await _settled_elsewhere(db, n) is False
    pr.status = "decided"
    await db.commit()
    assert await _settled_elsewhere(db, n) is True


async def test_a_closed_ticket_takes_every_button_down(db):
    iss = await _ticket(db, TicketAgentStatus.to_test)
    n = _note(iss, "to_test")
    assert await _settled_elsewhere(db, n) is False
    iss.archived = True
    await db.commit()
    assert await _settled_elsewhere(db, n) is True
