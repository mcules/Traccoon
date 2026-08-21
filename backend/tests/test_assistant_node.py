"""Assistent als Knoten — und die Antwort eines Ablaufs an seinen Auslöser.

Bis hierher konnte nur der Mail-Eingang dem Assistenten Arbeit geben; sein Auftrag stand in
einer Mail. Geprüft wird die Mechanik des allgemeinen Weges: dass ein Knoten einen Auftrag
anlegt und einreiht, dass „warten" das Ergebnis in den Kontext holt, und dass ein Webhook die
Antwort zurückbekommt, die der Ablauf selbst geschrieben hat.
"""
import pytest
from app.models.assistant import AssistantTask
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.ops import WebhookSub
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_engine import start_workflow
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(task_params: dict, with_answer: bool = False) -> dict:
    node = [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"label": "Start", "trigger": {"kind": "webhook"}}}},
        {"id": "beauftragen", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"label": "Assistent",
                             "action": {"action": "assistant_task",
                                        "params": task_params}}}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "beauftragen"}]
    before = "beauftragen"
    if with_answer:
        node.append({"id": "answer", "type": "auto_action", "position": {"x": 0, "y": 2},
                       "data": {"config": {"label": "Antwort",
                                           "action": {"action": "answer", "params": {
                                               "fields": {"ergebnis": "{{ assistant.output }}",
                                                          "source": "{{ doc_id }}"}}}}}})
        edges.append({"id": "e2", "source": before, "target": "answer"})
        before = "answer"
    node.append({"id": "ende", "type": "end", "position": {"x": 0, "y": 3},
                   "data": {"config": {"outcome": "completed"}}})
    edges.append({"id": "e3", "source": before, "target": "ende"})
    return {"nodes": node, "edges": edges}


async def _wait_to_done(db, instance_id: int, seconds: float = 3.0):
    """Dem Wächter Zeit geben: er wartet auf den Lauf und schaltet dann selbst weiter.

    Ohne dieses Zusehen prüfte der Test den Zustand, bevor der Hintergrund-Schritt überhaupt
    an der Reihe war — und hätte „unfertig" gemeldet, wo nur „noch nicht dran" stand.
    """
    import asyncio

    for _ in range(int(seconds / 0.02)):
        await asyncio.sleep(0.02)
        db.expire_all()
        inst = await db.get(WorkflowInstance, instance_id)
        if inst is not None and inst.status not in (WorkflowInstanceStatus.running,
                                                    WorkflowInstanceStatus.waiting):
            return inst
    db.expire_all()
    return await db.get(WorkflowInstance, instance_id)


async def _definition(db, name: str, graph: dict, owner):
    d = WorkflowDefinition(project_id=None, key=name, name=name, created_by=owner.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def test_node_creates_the_task_and_queues_it(db):
    """Ohne Mail und ohne Ticket: der Auftrag steht im Knoten und wird gerendert."""
    user = await make_user(db, "chefin")
    d = await _definition(db, "task", _graph({
        "task": "Lies Dokument {{ doc_id }} und halte Wissenswertes fest.",
        "title": "Dokument {{ doc_id }}"}), user)
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"doc_id": "3464"}, actor_id=user.id)

    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.kind == "task" and task.status == "approved"
    assert task.owner_user_id == user.id
    assert task.title == "Dokument 3464"
    # Der Auftrag ist der Prompt — der Worker nimmt ihn aus `meta.prompt`.
    assert task.meta["prompt"] == "Lies Dokument 3464 und halte Wissenswertes fest."
    assert task.meta["agent"] == "assistent"
    # Ohne „warten" läuft der Ablauf weiter, statt am Assistenten zu hängen.
    assert inst.status == WorkflowInstanceStatus.completed


async def test_a_pending_grant_keeps_the_task_in_the_inbox(db):
    """Mit Freigabe wartet der Auftrag auf den Menschen, statt sofort zu laufen."""
    user = await make_user(db, "vorsichtig")
    d = await _definition(db, "approval", _graph({
        "task": "Mach etwas", "approval": True}), user)
    await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                         context={}, actor_id=user.id)
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.status == "new"


async def test_wait_pulls_the_result_into_the_context(db, redis_stub):
    """Mit „warten" steht die Antwort des Assistenten im Kontext — und damit im Ablauf."""
    redis_stub["*"] = {"status": "done", "output": "nichts wissenswertes",
                       "summary": "nichts wissenswertes"}
    user = await make_user(db, "geduldig")
    d = await _definition(db, "wait", _graph(
        {"task": "Lies {{ doc_id }}", "wait": True}, with_answer=True), user)
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"doc_id": "77"}, actor_id=user.id)

    fresh = await _wait_to_done(db, inst.id)
    assert fresh.context["assistant"]["output"] == "nichts wissenswertes"
    # Die Antwort des Ablaufs ist gerendert, nicht die Vorlage.
    assert fresh.context["answer"] == {"ergebnis": "nichts wissenswertes", "source": "77"}
    assert fresh.status == WorkflowInstanceStatus.completed


def _only_answer() -> dict:
    """Ablauf, der ohne Umweg antwortet — dafür braucht es keinen Agenten."""
    return {"nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"trigger": {"kind": "webhook"}}}},
        {"id": "answer", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"action": {"action": "answer", "params": {
             "felder": {"ergebnis": "{{ text }}", "wer": "Traccoon"}}}}}},
        {"id": "ende", "type": "end", "position": {"x": 0, "y": 2},
         "data": {"config": {"outcome": "completed"}}},
    ], "edges": [{"id": "e1", "source": "s", "target": "answer"},
                 {"id": "e2", "source": "answer", "target": "ende"}]}


async def test_webhook_receives_the_answer_of_the_flow(db, client):
    """Ein Webhook ist ein Auslöser — und darf eine Antwort haben."""
    user = await make_user(db, "aufrufer")
    d = await _definition(db, "rueckkanal", _only_answer(), user)
    hook = WebhookSub(public_id="test-hook-1", owner_user_id=user.id, route="rueckkanal",
                      mode="workflow", workflow_definition_id=d.id, response_timeout=5)
    db.add(hook)
    await db.commit()

    answer = await client.post("/hooks/test-hook-1", json={"text": "fertig"})
    assert answer.status_code == 200
    # Der Rumpf IST die Antwort des Ablaufs, ohne Hülle drumherum.
    assert answer.json() == {"ergebnis": "fertig", "wer": "Traccoon"}


async def test_webhook_without_an_answer_acknowledges_and_says_so(db, client):
    """Läuft der Ablauf noch, kommt keine erfundene Antwort, sondern eine Auskunft."""
    user = await make_user(db, "ungeduldig")
    # Ein Ablauf, der auf den Assistenten wartet und nie ein Ergebnis bekommt
    # (redis_stub ohne Eintrag): die Zeitgrenze greift.
    d = await _definition(db, "haengt", _graph({"task": "Tu was", "wait": True}), user)
    hook = WebhookSub(public_id="test-hook-2", owner_user_id=user.id, route="haengt",
                      mode="workflow", workflow_definition_id=d.id, response_timeout=1)
    db.add(hook)
    await db.commit()

    answer = await client.post("/hooks/test-hook-2", json={})
    assert answer.status_code == 202
    base = answer.json()
    assert base["answer"] is None and base["accepted"] is True
