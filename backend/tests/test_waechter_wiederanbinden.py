"""Ein verlorener Wächter muss wieder angebunden werden — nicht erst beim nächsten Neustart.

Der Wächter, der auf das Ergebnis eines Agentenlaufs wartet, lebt im Backend-Prozess. Geht
er verloren, wartet niemand mehr: am 2026-08-07 hing einer in einer halb toten
Redis-Verbindung (der Client hatte weder keepalive noch socket_timeout — der Worker warnt
im eigenen Code davor). Das fertige Ergebnis für ABC-31 lag ab 19:54 unabgeholt in Redis,
das Ticket stand eine Stunde still, und von außen sah es aus, als arbeite der Agent noch.

Angebunden wurde bisher nur beim Backend-Start. Jetzt in jedem Tick — und höchstens einmal
je Schritt.
"""
from app.models.enums import WorkflowInstanceStatus as IStatus
from app.models.enums import WorkflowStepStatus as SStatus
from app.models.workflow import WorkflowStepRun
from app.services import workflow_engine as we


async def _schritt(db, inst, token, *, task_id="wf-1-1-exec-a") -> WorkflowStepRun:
    step = WorkflowStepRun(instance_id=inst.id, token_id=token.id, node_id="exec",
                           node_type=we.NType.agent_task, status=SStatus.running,
                           result={"task_id": task_id})
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def test_verwaister_schritt_bekommt_wieder_einen_waechter(db, monkeypatch, prozess):
    inst, token = prozess
    step = await _schritt(db, inst, token)
    gestartet: list[int] = []

    async def fake_await_agent(instance_id, token_id, step_id, task_id, omap, timeout):
        gestartet.append(step_id)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WAECHTER.clear()

    await we.recover_workflow_agents()
    await we.drain()

    assert gestartet == [step.id]


async def test_kein_zweiter_waechter_auf_dasselbe_ergebnis(db, monkeypatch, prozess):
    """Zwei Wächter auf einem Ergebnis würden beide schalten — der Schritt liefe doppelt."""
    inst, token = prozess
    step = await _schritt(db, inst, token)
    gestartet: list[int] = []

    async def fake_await_agent(instance_id, token_id, step_id, task_id, omap, timeout):
        gestartet.append(step_id)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WAECHTER.clear()
    we._WAECHTER.add(step.id)          # hier wartet bereits einer
    try:
        await we.recover_workflow_agents()
        await we.drain()
        assert gestartet == []
    finally:
        we._WAECHTER.clear()


async def test_beendeter_schritt_bekommt_keinen(db, monkeypatch, prozess):
    inst, token = prozess
    step = await _schritt(db, inst, token)
    step.status = SStatus.done
    await db.commit()
    gestartet: list[int] = []

    async def fake_await_agent(*a, **k):
        gestartet.append(1)

    monkeypatch.setattr(we, "_await_agent", fake_await_agent)
    we._WAECHTER.clear()

    await we.recover_workflow_agents()
    await we.drain()

    assert gestartet == []


import pytest  # noqa: E402


@pytest.fixture
async def prozess(db):
    """Minimale laufende Instanz mit Token — mehr braucht das Wiederanbinden nicht."""
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
