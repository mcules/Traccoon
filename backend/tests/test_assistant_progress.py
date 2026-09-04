"""What the assistant is doing, while it does it.

A running message used to be a spinner and a stopwatch. The run writes every
step down anyway — the same ones the console prints — so what is tested here is
that they reach the person waiting, and that the two things which made the first
attempt useless stay fixed: the run cannot be found by the task's `run_id`
(it only learns that at the end), and the arguments of a call are not JSON.
"""
from __future__ import annotations

import pytest
from conftest import auth, make_user

from app.api.mail import _short
from app.models.agents import Run, RunStep
from app.models.assistant import AssistantTask


async def a_run(db, task_id: str, *steps) -> Run:
    run = Run(task_id=task_id, status="running", agent="assistent", phase="execute")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    for seq, (kind, tool, content, ok, ms) in enumerate(steps, start=1):
        db.add(RunStep(run_id=run.id, seq=seq, role="tool" if tool else "assistant",
                       kind=kind, tool_name=tool, content=content, ok=ok, duration_ms=ms))
    await db.commit()
    return run


# ------------------------------------------------------------------ the label

def test_a_truncated_call_still_gives_a_label() -> None:
    """The run keeps the first 400 characters of a call, so a document path or a
    mail subject reaches the cut on its own. `json.loads` refuses every one of
    those fragments — which is why the label was empty for exactly the calls
    worth labelling."""
    cut = '{"path": "03 Bereiche/Firmen/Ein sehr langer Name GmbH/Datei.md", "find": "der Rest fehl'
    assert _short(cut) == "03 Bereiche/Firmen/Ein sehr langer Name GmbH/Datei.md"


def test_the_label_takes_the_first_key_it_knows() -> None:
    assert _short('{"query": "tag:#idee", "limit": 5}') == "tag:#idee"
    assert _short('{"limit": 5, "folder": "03 Bereiche"}') == "03 Bereiche"


def test_an_unreadable_call_gets_no_label_rather_than_raw_json() -> None:
    assert _short("nicht mal fast json") == ""
    assert _short('{"unbekannt": "wert"}') == ""
    assert _short("") == ""


def test_a_label_is_one_line_and_bounded() -> None:
    """It goes into a single line of a narrow panel."""
    long = '{"path": "' + "a" * 400
    out = _short(long)
    assert len(out) <= 120 and "\n" not in out
    assert _short('{"title": "zwei\\nzeilen"}') == "zwei zeilen"


# ------------------------------------------------------------------ the route

@pytest.mark.asyncio
async def test_the_steps_of_a_running_message_are_found_without_a_run_id(client, db) -> None:
    """The task learns its `run_id` when it finishes, which is exactly when
    nobody needs this. While it runs, the run is found by the name it carries."""
    user = await make_user(db, "fort1")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="running",
                      title="Was steht an?", meta={"chat_text": "Was steht an?"})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    assert t.run_id is None

    await a_run(db, f"assistant-{t.id}",
                ("agent_text", None, "Ich sehe in den Notizen nach.", None, None),
                ("tool_start", "vault__notes_read", '{"path": "Notiz.md"', None, None),
                ("tool_result", "vault__notes_read", "…", True, 42))

    r = await client.get(f"/assistant/chat/{t.id}/progress", headers=auth(user))
    assert r.status_code == 200
    out = r.json()
    assert out["running"] is True and out["run_id"] is not None
    kinds = [s["kind"] for s in out["steps"]]
    assert kinds == ["agent_text", "tool_start", "tool_result"]
    assert out["steps"][0]["text"] == "Ich sehe in den Notizen nach."
    assert out["steps"][1]["label"] == "Notiz.md"
    assert out["steps"][2]["ok"] is True and out["steps"][2]["ms"] == 42


@pytest.mark.asyncio
async def test_only_what_is_new_comes_back(client, db) -> None:
    """A message that runs for ten minutes collects hundreds of steps; re-sending
    all of them every two seconds would be the same list over and over."""
    user = await make_user(db, "fort2")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="running",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    await a_run(db, f"assistant-{t.id}",
                *[("tool_start", "vault__notes_read", '{"path": "a.md"', None, None)] * 5)

    r = await client.get(f"/assistant/chat/{t.id}/progress?after=3", headers=auth(user))
    assert [s["seq"] for s in r.json()["steps"]] == [4, 5]


@pytest.mark.asyncio
async def test_bookkeeping_steps_are_not_shown(client, db) -> None:
    """Token counts and the system prompt are not something to watch."""
    user = await make_user(db, "fort3")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="running",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    await a_run(db, f"assistant-{t.id}",
                ("system", None, "Du bist ein Agent.", None, None),
                ("usage", None, "1234", None, None),
                ("agent_text", None, "Los geht's.", None, None))
    out = (await client.get(f"/assistant/chat/{t.id}/progress", headers=auth(user))).json()
    assert [s["kind"] for s in out["steps"]] == ["agent_text"]


@pytest.mark.asyncio
async def test_a_tool_result_carries_no_text_into_the_bubble(client, db) -> None:
    """What a tool answered belongs in the note, not in a chat bubble — a single
    read can be forty thousand characters."""
    user = await make_user(db, "fort4")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="running",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    await a_run(db, f"assistant-{t.id}",
                ("tool_result", "vault__notes_read", "x" * 40_000, True, 3))
    out = (await client.get(f"/assistant/chat/{t.id}/progress", headers=auth(user))).json()
    assert out["steps"][0]["text"] == ""


@pytest.mark.asyncio
async def test_somebody_elses_message_is_not_there(client, db) -> None:
    mine = await make_user(db, "fort5")
    theirs = await make_user(db, "fort6")
    t = AssistantTask(owner_user_id=theirs.id, kind="chat", source="web", status="running",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    r = await client.get(f"/assistant/chat/{t.id}/progress", headers=auth(mine))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_message_without_a_run_yet_answers_empty(client, db) -> None:
    """Between sending and the worker picking it up there is nothing to show,
    and that is a state, not a failure."""
    user = await make_user(db, "fort7")
    t = AssistantTask(owner_user_id=user.id, kind="chat", source="web", status="new",
                      title="x", meta={})
    db.add(t)
    await db.commit()
    await db.refresh(t)
    out = (await client.get(f"/assistant/chat/{t.id}/progress", headers=auth(user))).json()
    assert out == {"run_id": None, "running": True, "steps": []}
