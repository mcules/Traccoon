"""Unterabläufe: „Anderer Ablauf" darf auch ein eigener sein.

Der Knoten kannte bisher nur die fünf ausgelieferten Slots — für alles Eigene war er
nutzlos: man baut sich einen Ablauf, will ihn aus einem zweiten heraus aufrufen, und
findet im Dropdown nur den Ticket-Lebenszyklus. Ein ausdrücklich benannter Ablauf
(`definition_id`) ergänzt den Slot-Weg, ohne ihn zu ersetzen: ein Slot wird je Projekt
aufgelöst, eine Definition ist genau diese eine.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import workflow_engine as engine
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _ablauf(db, besitzer, key: str, graph: dict, *, veroeffentlicht=True):
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=besitzer.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=(WorkflowVersionStatus.published if veroeffentlicht
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if veroeffentlicht:
        d.current_version_id = v.id
    await db.commit()
    return d


def _gerade(*, extra_node=None, extra_edges=()):
    nodes = [{"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
             {"id": "e", "type": "end", "position": {"x": 0, "y": 2},
              "data": {"config": {"outcome": "completed"}}}]
    edges = [{"id": "e1", "source": "s", "target": "e"}]
    if extra_node:
        nodes.insert(1, extra_node)
        edges = list(extra_edges)
    return {"nodes": nodes, "edges": edges}


async def test_unterablauf_ruft_einen_benannten_ablauf(client, db):
    anna = await make_user(db, "anna")
    kind = await _ablauf(db, anna, "kind", _gerade())
    eltern = await _ablauf(db, anna, "eltern", _gerade(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {"definition_id": kind.id}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e",
                      "sourceHandle": "completed"}]))

    r = await client.post(f"/workflows/{eltern.id}/instances", headers=auth(anna),
                          json={"subject_kind": "standalone"})
    assert r.status_code in (200, 201), r.text

    kinder = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == kind.id))).scalars().all()
    assert len(kinder) == 1, "der benannte Ablauf muss als Kind-Instanz gelaufen sein"


async def test_ohne_ablauf_meckert_die_pruefung():
    graph = _gerade(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e", "sourceHandle": "completed"}])
    fehler = engine.validate_graph(WorkflowSubjectKind.standalone, graph)
    assert any("kein Ablauf gewählt" in f for f in fehler)


async def test_benannter_ablauf_genuegt_der_pruefung():
    graph = _gerade(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {"definition_id": 7}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e", "sourceHandle": "completed"}])
    assert engine.validate_graph(WorkflowSubjectKind.standalone, graph) == []


async def test_selbstaufruf_wird_verweigert(client, db):
    """Ein Ablauf, der sich selbst als Unterablauf einträgt, liefe endlos."""
    anna = await make_user(db, "anna")
    d = await _ablauf(db, anna, "ich", _gerade())
    graph = _gerade(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {"definition_id": d.id}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e", "sourceHandle": "completed"}])
    v = (await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == d.id))).scalars().first()
    v.graph = graph
    await db.commit()

    await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                      json={"subject_kind": "standalone"})
    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().all()
    assert len(inst) == 1
    assert inst[0].status.value == "failed"
    assert "selbst" in (inst[0].error or "")
