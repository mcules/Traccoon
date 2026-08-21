"""Waiting and retrying: the two things a flow has to be able to do when the world outside
does not answer immediately.

What is checked is the mechanics behind it: that a waiting run survives a restart (the alarm
sits in the tick, not in a sleeping task), that an expired timer really wakes, and that a
retry keeps a distance instead of producing the same error in the same second.
"""
import datetime as dt

import pytest
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowTokenState, WorkflowVersionStatus,
)
from app.models.workflow import (
    WorkflowDefinition, WorkflowStepRun, WorkflowToken, WorkflowVersion,
)
from app.services import workflow_actions, workflow_engine
from app.services.workflow_engine import due_timer, start_workflow, validate_graph
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(timer: dict | None = None, action: dict | None = None) -> dict:
    node = [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
               "data": {"config": {"label": "Start"}}}]
    edges = []
    before = "s"
    if timer is not None:
        node.append({"id": "warten", "type": "timer", "position": {"x": 0, "y": 1},
                       "data": {"config": timer}})
        edges.append({"id": "e1", "source": before, "target": "warten"})
        before = "warten"
    if action is not None:
        node.append({"id": "tun", "type": "auto_action", "position": {"x": 0, "y": 2},
                       "data": {"config": action}})
        edges.append({"id": "e2", "source": before, "target": "tun"})
        before = "tun"
    node.append({"id": "ende", "type": "end", "position": {"x": 0, "y": 3},
                   "data": {"config": {"outcome": "completed"}}})
    edges.append({"id": "e3", "source": before, "target": "ende"})
    return {"nodes": node, "edges": edges}


async def _run(db, graph: dict, name: str):
    user = await make_user(db, name)
    d = WorkflowDefinition(project_id=None, key=name, name=name, created_by=user.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=user.id)


async def test_the_timer_halts_the_run(db):
    inst = await _run(db, _graph(timer={"dauer": 30, "einheit": "m"}), "wartet")
    assert inst.status == WorkflowInstanceStatus.waiting
    token = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == inst.id))).scalars().one()
    assert token.state == WorkflowTokenState.waiting and token.waiting_for == "timer"
    # The due time stands on the step, not in a task that would not have survived a
    # restart.
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id))).scalars().one()
    due = dt.datetime.fromisoformat(step.result["faellig"])
    assert dt.timedelta(minutes=29) < due - dt.datetime.now(dt.timezone.utc) \
        <= dt.timedelta(minutes=30)


async def test_a_due_timer_wakes_and_carries_on(db):
    inst = await _run(db, _graph(timer={"dauer": 30, "einheit": "m"}), "geweckt")
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id))).scalars().one()

    # Not due means nothing happens. That is half the value: the alarm must not collect
    assert await due_timer() == 0

    step.result = {"faellig": (dt.datetime.now(dt.timezone.utc)
                               - dt.timedelta(seconds=1)).isoformat()}
    await db.commit()
    assert await due_timer() == 1

    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed


async def test_a_moment_in_the_past_does_not_wait(db):
    """A point in time that has already passed means "now", not "never"."""
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    inst = await _run(db, _graph(timer={"bis": yesterday}), "vergangen")
    assert await due_timer() == 1
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed


async def test_a_repeat_keeps_its_distance_and_then_gives_up(db, monkeypatch):
    """A failure to the outside is mostly one of the moment. So: wait, try again, but not
    endlessly."""
    attempts = {"n": 0}

    async def broken(db_, inst_, node_):
        attempts["n"] += 1
        raise ValueError("Gegenstelle weg")

    monkeypatch.setattr(workflow_actions, "run_action", broken)
    inst = await _run(db, _graph(action={
        "action": {"action": "notify", "params": {}}, "wiederholungen": 2, "warte_sek": 1,
    }), "wiederholt")

    assert attempts["n"] == 1
    assert inst.status == WorkflowInstanceStatus.waiting     # waits for the second attempt
    assert inst.context["_versuche"]["tun"] == 1

    async def due_spots():
        for st in (await db.execute(select(WorkflowStepRun).where(
                WorkflowStepRun.instance_id == inst.id,
                WorkflowStepRun.status == "waiting"))).scalars().all():
            st.result = {"faellig": (dt.datetime.now(dt.timezone.utc)
                                     - dt.timedelta(seconds=1)).isoformat()}
        await db.commit()

    await due_spots()
    await due_timer()
    assert attempts["n"] == 2
    await due_spots()
    await due_timer()
    assert attempts["n"] == 3          # the third is the last (two retries)

    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.failed
    # The counter is gone; otherwise the next attempt would count on from the old state.
    assert inst.context.get("_versuche", {}).get("tun") is None


async def test_the_error_branch_catches_the_failure(db, monkeypatch):
    """Whoever wires an `error` exit wants to handle the error instead of losing the run."""
    async def broken(db_, inst_, node_):
        raise ValueError("kaputt")

    monkeypatch.setattr(workflow_actions, "run_action", broken)
    graph = _graph(action={"action": {"action": "notify", "params": {}}})
    graph["nodes"].append({"id": "aufgefangen", "type": "end", "position": {"x": 1, "y": 3},
                           "data": {"config": {"outcome": "completed"}}})
    graph["edges"].append({"id": "e9", "source": "tun", "target": "aufgefangen",
                           "sourceHandle": "error"})

    inst = await _run(db, graph, "fehlerzweig")
    assert inst.status == WorkflowInstanceStatus.completed


async def test_validation_demands_a_duration_or_a_moment():
    assert validate_graph("standalone", _graph(timer={"dauer": 5, "einheit": "m"})) == []
    error = validate_graph("standalone", _graph(timer={}))
    assert any("neither a duration nor a moment" in f for f in error)


async def test_a_long_wait_is_capped():
    """A flow that sleeps for two years is almost always a typo."""
    due = workflow_engine._due_from({"dauer": 900, "einheit": "t"}, {})
    assert due - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(days=90)
