"""When the personal assistant speaks up, and when it stays silent.

The occasion: a finished "nothing to do" (an AliExpress receipt confirmation) arrived as a
Telegram message although the assistant wrote in the text itself that it was not reporting.
The reason was the unconditional closing report. Since then the rule is: the run itself is
no message; reporting happens over `traccoon_notify_human`, on mishaps and in the chat.
"""
import pytest
from app.models.assistant import AssistantTask
from app.models.notification import Notification
from app.models.user import User
from app.worker import __main__ as worker
from conftest import make_user
from sqlalchemy import select


async def _run(db, monkeypatch, *, owner: User, kind: str = "email",
                status: str = "done", reports: bool = False, title: str = "Bestellung 123",
                blocker_kind: str | None = None, source="web"):
    """Let an assistant item run through; `meldet` = the agent calls notify_human."""
    t = AssistantTask(owner_user_id=owner.id, kind=kind, title=title, status="approved",
                      source=source,
                      redacted_summary="Zusammenfassung", meta={"chat_text": "Wie spät?"})
    db.add(t)
    await db.commit()
    await db.refresh(t)

    class Result:
        def __init__(self):
            self.status = {"done": "done", "error": "failed"}.get(status, status)
            # ask_human delivers the question as `text`, without a summary.
            self.text = ("Ticket oder API-Freigabe?" if self.status == "blocked"
                         else "Erledigt. Kein Statuswechsel, keine Telegram-Nachricht.")
            self.summary = "" if self.status == "blocked" else self.text
            self.run_id = None
            self.blocker_kind = blocker_kind

    async def fake_run_agent(**kwargs):
        if reports:
            # This is how `traccoon_notify_human` would act: its own message plus a note on the item.
            db.add(Notification(user_id=owner.id, kind="assistant", title="Frist am Freitag",
                                body="Rechnung 240 € fällig", chat_id=owner.telegram_chat_id))
            obj = await db.get(AssistantTask, t.id)
            obj.notified = True
            await db.commit()
        return Result()

    async def fake_load_agent(*a, **kw):
        class A:
            role = "assistent"
            name = "assistent"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "run_agent", fake_run_agent, raising=False)
    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)
    # run_agent is imported in the body, so replace it there as well.
    import app.worker.runtime as rt
    monkeypatch.setattr(rt, "run_agent", fake_run_agent, raising=False)

    await worker._handle_assistant_task(
        {"assistant_task_id": t.id, "task_id": f"assistant-{t.id}"}, None)
    return t


async def _messages(db) -> list[Notification]:
    return list((await db.execute(select(Notification))).scalars().all())


@pytest.fixture
async def owner(db):
    u = await make_user(db, "anna")
    u.telegram_chat_id = "123"
    await db.commit()
    return u


async def test_finished_work_without_action_needed_stays_quiet(db, owner, monkeypatch):
    """The case from practice: filed, nothing to do, so no message."""
    await _run(db, monkeypatch, owner=owner)
    assert await _messages(db) == []


async def test_an_explicit_notice_arrives_and_only_once(db, owner, monkeypatch):
    """If the assistant reports itself, EXACTLY its message goes out, not the closing report
    in addition (because otherwise the same thing would come twice)."""
    await _run(db, monkeypatch, owner=owner, reports=True)
    n = await _messages(db)
    assert len(n) == 1
    assert n[0].title == "Frist am Freitag"


async def test_a_breakdown_always_reports_itself(db, owner, monkeypatch):
    await _run(db, monkeypatch, owner=owner, status="error")
    n = await _messages(db)
    assert len(n) == 1 and "Fehler" in n[0].title


async def test_chat_is_always_answered(db, owner, monkeypatch):
    """A question asked is a question one wants answered, even with "not at all"."""
    owner.assistant_notify = "never"
    await db.commit()
    await _run(db, monkeypatch, owner=owner, kind="chat")
    n = await _messages(db)
    assert len(n) == 1 and n[0].title.startswith("🤖 Assistent")


async def test_mode_always_also_reports_finished_work(db, owner, monkeypatch):
    owner.assistant_notify = "always"
    await db.commit()
    await _run(db, monkeypatch, owner=owner)
    assert len(await _messages(db)) == 1


async def test_mode_never_stays_quiet_even_on_breakdowns(db, owner, monkeypatch):
    owner.assistant_notify = "never"
    await db.commit()
    await _run(db, monkeypatch, owner=owner, status="error")
    assert await _messages(db) == []


async def test_callback_in_the_chat_arrives(db, owner, monkeypatch):
    """The occasion: `ask_human` ended as 'blocked' and was misread as a tool gate; the
    question disappeared silently and the human saw only an eternal 'running'."""
    t = await _run(db, monkeypatch, owner=owner, kind="chat", status="blocked",
                    blocker_kind="ask_human")
    n = await _messages(db)
    assert len(n) == 1 and "Ticket oder API-Freigabe?" in n[0].body
    await db.refresh(t)
    # Finished, not 'running'; otherwise the exchange is missing in the history later.
    assert t.status == "done" and t.result == "Ticket oder API-Freigabe?"


async def test_callback_without_chat_reports_despite_the_mode(db, owner, monkeypatch):
    """Outside the chat as well (mail inbox): a question without a recipient is pointless."""
    owner.assistant_notify = "needed"
    await db.commit()
    await _run(db, monkeypatch, owner=owner, status="blocked", blocker_kind="ask_human")
    n = await _messages(db)
    assert len(n) == 1 and "a question" in n[0].title


async def test_tool_grant_stays_quiet_and_open(db, owner, monkeypatch):
    """The counterpart: with the tool gate the item waits for the approval card; it must be
    neither finalised nor reported twice."""
    t = await _run(db, monkeypatch, owner=owner, status="blocked",
                    blocker_kind="assistant_perm")
    assert await _messages(db) == []
    await db.refresh(t)
    assert t.status == "running"


async def test_the_notify_tool_needs_no_grant(db, owner):
    """Without this exception a missing allowlist approval would mean "never reports", and
    then important things would stay mute as well."""
    from app.worker.runtime import _ALWAYS_ALLOWED
    assert "traccoon_notify_human" in _ALWAYS_ALLOWED


# ── Wohin eine Antwort geht ──────────────────────────────────────────────────

async def test_a_chat_from_the_browser_does_not_land_in_telegram(db, owner, monkeypatch):
    """An answer goes back where the question was asked.

    The Telegram conversation is its own. Pushing the answer to something typed
    in the browser into that thread drops it into a conversation that never
    asked, and the person reads the same answer twice — once in the panel that
    is already showing it, once on their phone.
    """
    await _run(db, monkeypatch, owner=owner, kind="chat", source="web")
    n = await _messages(db)
    assert len(n) == 1
    assert n[0].chat_id is None, "went to Telegram although it was asked in the browser"
    # The bell still gets it: whoever asked and walked away should learn that the
    # answer is there.
    assert n[0].user_id == owner.id


async def test_a_chat_from_telegram_is_answered_in_telegram(db, owner, monkeypatch):
    await _run(db, monkeypatch, owner=owner, kind="chat", source="telegram")
    n = await _messages(db)
    assert len(n) == 1 and n[0].chat_id == "123"


async def test_a_report_of_its_own_keeps_the_messenger(db, owner, monkeypatch):
    """The one case with no place it came from: the assistant working through a
    mail and finding something worth knowing. That is what the notify mode is
    for, and it reaches the person wherever they are."""
    owner.assistant_notify = "always"
    await db.commit()
    await _run(db, monkeypatch, owner=owner, kind="email", source="ablauf:15")
    n = await _messages(db)
    assert len(n) == 1 and n[0].chat_id == "123"


async def test_an_answer_belongs_to_the_person_who_asked(db, owner, monkeypatch):
    """`user_id IS NULL` is the "for everybody" case in the bell, so a chat
    answer without an owner would be readable by the next person."""
    await _run(db, monkeypatch, owner=owner, kind="chat", source="web")
    n = await _messages(db)
    assert n[0].user_id == owner.id
    assert n[0].assistant_task_id is not None


# ── Der zweite Weg nach draussen: das Melde-Werkzeug des Assistenten ─────────

async def _notify(db, owner, *, kind="chat", source="web", title="Frist am Freitag"):
    """`traccoon_notify_human`, wie der Assistent es aufruft."""
    from app.worker.tools_traccoon import call_traccoon_tool

    t = AssistantTask(owner_user_id=owner.id, kind=kind, source=source, status="running",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    out = await call_traccoon_tool(db, owner.id, "traccoon_notify_human",
                                   {"title": title, "text": "Rechnung 240 € fällig"},
                                   assistant_task_id=t.id)
    assert out == "Gemeldet."
    return t


async def test_the_assistant_reporting_inside_a_chat_stays_in_the_house(db, owner):
    """The second way out, and the one that survived the first fix.

    `traccoon_notify_human` is the assistant's own "you have to know this". In a
    conversation the person is sitting in front of the answer, so the messenger
    would hand them the same thing a second time in a thread that never asked.
    """
    await _notify(db, owner, kind="chat", source="web")
    n = await _messages(db)
    assert len(n) == 1 and n[0].chat_id is None
    assert n[0].user_id == owner.id and n[0].assistant_task_id is not None


async def test_it_keeps_the_messenger_for_a_chat_from_telegram(db, owner):
    await _notify(db, owner, kind="chat", source="telegram")
    n = await _messages(db)
    assert len(n) == 1 and n[0].chat_id == "123"


async def test_it_keeps_the_messenger_for_background_work(db, owner):
    """Working through a mail has no place it came from — there the messenger IS
    the way, and that is the whole point of the tool."""
    await _notify(db, owner, kind="email", source="ablauf:15")
    n = await _messages(db)
    assert len(n) == 1 and n[0].chat_id == "123"


async def test_reporting_marks_the_item_so_the_closing_report_stays_silent(db, owner):
    """Otherwise the person gets the same thing twice: once because the
    assistant said it, once because the run ended."""
    t = await _notify(db, owner, kind="email", source="ablauf:15")
    await db.refresh(t)
    assert t.notified is True
