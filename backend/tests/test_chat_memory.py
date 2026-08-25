"""The conversation thread of the assistant.

Before it was a pure time window: after 12 hours or 8 exchanges it knew nothing abruptly.
Now the most recent stays verbatim and the older part wanders into a growing summary; the
tests guard above all that nothing is silently lost in the process.
"""
import datetime as dt

import pytest
from app.models.assistant import AssistantTask, ChatSummary
from app.worker import __main__ as worker
from conftest import make_user
from sqlalchemy import select


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Mock away the agent and token resolution; every test sets the aux model itself."""
    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "assistent"
            provider, model = "claude_code", "sonnet"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)


async def _chat(db, anna, question: str, answer: str, *, days_old: int = 0,
                agent: str | None = None) -> AssistantTask:
    meta = {"chat_text": question}
    if agent:
        meta["agent"] = agent
    t = AssistantTask(owner_user_id=anna.id, kind="chat", title=question[:200], status="done",
                      result=answer, meta=meta)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    if days_old:
        t.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days_old)
        await db.commit()
    return t


def _mock_aux(monkeypatch, text):
    async def fake_aux(*a, **kw):
        fake_aux.seen = kw.get("messages", [{}])[0].get("content", "")
        return text
    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    return fake_aux


async def test_a_short_conversation_stays_verbatim(db, anna, monkeypatch):
    """Little said means nothing to summarise, no aux call, no cost."""
    aux = _mock_aux(monkeypatch, "sollte nicht gerufen werden")
    for i in range(3):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    new = await _chat(db, anna, "Und jetzt?", "")
    history = await worker._chat_history(db, new)
    assert [w["body"] for w in history if w["role"] == "user"] == ["Frage 0", "Frage 1", "Frage 2"]
    assert not hasattr(aux, "seen")
    assert (await db.execute(select(ChatSummary))).scalars().first() is None


async def test_older_parts_move_into_the_summary(db, anna, monkeypatch):
    _mock_aux(monkeypatch, "- Mensch mag knappe Antworten\n- Umzug des News-Jobs offen")
    for i in range(16):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    new = await _chat(db, anna, "Und jetzt?", "")
    history = await worker._chat_history(db, new)

    assert history[0]["label"] == "What you remember"
    assert "News-Jobs offen" in history[0]["body"]
    # The most recent 8 stand there verbatim, the oldest no longer.
    literal = [w["body"] for w in history if w["role"] == "user"]
    assert "Frage 15" in literal and "Frage 0" not in literal

    s = (await db.execute(select(ChatSummary))).scalars().one()
    assert s.agent == "assistent" and s.to_task_id > 0


async def test_the_summary_is_extended_not_replaced(db, anna, monkeypatch):
    """The second time, the existing memory has to go into the assignment as well; otherwise
    the assistant loses everything summarised before on every shift."""
    _mock_aux(monkeypatch, "- Runde eins")
    for i in range(16):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    await worker._chat_history(db, await _chat(db, anna, "x", ""))

    aux2 = _mock_aux(monkeypatch, "- Runde eins\n- Runde zwei")
    for i in range(16, 32):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    await worker._chat_history(db, await _chat(db, anna, "y", ""))

    assert "The memory so far" in aux2.seen and "Runde eins" in aux2.seen
    assert (await db.execute(select(ChatSummary))).scalars().one().text == "- Runde eins\n- Runde zwei"


async def test_only_new_material_is_summarised(db, anna, monkeypatch):
    """What already stands in the summary must not go through the model again; otherwise every
    message costs the whole history."""
    _mock_aux(monkeypatch, "- alles bekannt")
    for i in range(16):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    await worker._chat_history(db, await _chat(db, anna, "x", ""))

    aux2 = _mock_aux(monkeypatch, "- nichts Neues")
    await worker._chat_history(db, await _chat(db, anna, "y", ""))
    assert not hasattr(aux2, "seen")     # nothing shifted, so no call


async def test_without_aux_the_old_memory_remains(db, anna, monkeypatch):
    """Aux unreachable: better a slightly outdated memory than a torn thread."""
    _mock_aux(monkeypatch, "- Stand von gestern")
    for i in range(16):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    await worker._chat_history(db, await _chat(db, anna, "x", ""))

    async def broken(*a, **kw):
        return None
    monkeypatch.setattr("app.worker.aux.aux_chat", broken)
    for i in range(16, 32):
        await _chat(db, anna, f"Frage {i}", f"Antwort {i}")
    history = await worker._chat_history(db, await _chat(db, anna, "y", ""))
    assert "Stand von gestern" in history[0]["body"]


async def test_a_specialist_agent_has_its_own_conversation(db, anna, monkeypatch):
    _mock_aux(monkeypatch, "- egal")
    await _chat(db, anna, "Assistenten-Sache", "ok")
    await _chat(db, anna, "Game matter", "ok", agent="game-operator")
    new = await _chat(db, anna, "Weiter", "", agent="game-operator")
    history = await worker._chat_history(db, new)
    assert [w["body"] for w in history if w["role"] == "user"] == ["Game matter"]


async def test_very_old_conversations_no_longer_count(db, anna, monkeypatch):
    _mock_aux(monkeypatch, "- egal")
    await _chat(db, anna, "Uralt", "ok", days_old=30)
    await _chat(db, anna, "Neulich", "ok")
    history = await worker._chat_history(db, await _chat(db, anna, "Weiter", ""))
    assert [w["body"] for w in history if w["role"] == "user"] == ["Neulich"]
