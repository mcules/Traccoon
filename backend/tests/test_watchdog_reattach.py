"""A lost watcher has to be reattached, not only at the next restart.

The watcher waiting for the result of an agent run lives in the backend process. If it is lost, nobody waits any more: on 2026-08-07 one hung in a half dead Redis connection (the
er verloren, wartet niemand mehr: am 2026-08-07 hing einer in einer halb toten
client had neither keepalive nor socket_timeout, and the worker warns about that in its own
code). The finished result for ABC-31 lay unfetched in Redis from 19:54, the ticket stood
still for an hour, and from the outside it looked as if the agent were still working.

Reattaching used to happen only at the backend start. Now it happens in every tick, and at most once per step.
"""
from app.models.enums import WorkflowInstanceStatus as IStatus
from app.models.enums import WorkflowStepStatus as SStatus
from app.models.workflow import WorkflowStepRun
from app.services import workflow_engine as we


async def _step(db, inst, token, *, task_id="wf-1-1-exec-a") -> WorkflowStepRun:
    step = WorkflowStepRun(instance_id=inst.id, token_id=token.id, node_id="exec",
                           node_type=we.NType.agent_task, status=SStatus.running,
                           result={"task_id": task_id})
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def test_an_orphaned_step_gets_a_watchdog_again(db, monkeypatch, flow):
    inst, token = flow
    step = await _step(db, inst, token)
    started: list[int] = []

    async def fake_await_agent(instance_id, token_id, step_id, task_id, omap, timeout):
        started.append(step_id)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WATCHDOG.clear()

    await we.recover_workflow_agents()
    await we.drain()

    assert started == [step.id]


async def test_no_second_watchdog_on_the_same_result(db, monkeypatch, flow):
    """Two watchers on one result would both switch, and the step would run twice."""
    inst, token = flow
    step = await _step(db, inst, token)
    started: list[int] = []

    async def fake_await_agent(instance_id, token_id, step_id, task_id, omap, timeout):
        started.append(step_id)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WATCHDOG.clear()
    we._WATCHDOG.add(step.id)          # one is already waiting here
    try:
        await we.recover_workflow_agents()
        await we.drain()
        assert started == []
    finally:
        we._WATCHDOG.clear()


async def test_a_finished_step_gets_none(db, monkeypatch, flow):
    inst, token = flow
    step = await _step(db, inst, token)
    step.status = SStatus.done
    await db.commit()
    started: list[int] = []

    async def fake_await_agent(*a, **k):
        started.append(1)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WATCHDOG.clear()

    await we.recover_workflow_agents()
    await we.drain()

    assert started == []


import pytest  # noqa: E402


@pytest.fixture
async def flow(db):
    """Minimal running instance with a token: reattaching needs no more."""
    from app.models.workflow import (WorkflowDefinition, WorkflowInstance, WorkflowToken,
                                     WorkflowVersion)
    from app.models.enums import WorkflowTokenState as TState

    d = WorkflowDefinition(key="test-waechter", name="T")
    db.add(d)
    await db.commit()
    await db.refresh(d)
    from app.models.enums import WorkflowVersionStatus
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id, status=IStatus.waiting)
    db.add(inst)
    await db.commit()
    await db.refresh(inst)
    token = WorkflowToken(instance_id=inst.id, node_id="exec", state=TState.waiting,
                          waiting_for="agent")
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return inst, token
