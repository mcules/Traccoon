"""Sub-flows: "another flow" may be an own one as well.

The node knew only the five shipped slots until now, and for everything of one's own it was
useless: one builds a flow, wants to call it out of a second one, and finds only the ticket
lifecycle in the dropdown. An explicitly named flow (`definition_id`) complements the slot
path without replacing it: a slot is resolved per project, while a definition is exactly
this one.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import workflow_engine as engine
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _flow(db, owner, key: str, graph: dict, *, published=True):
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=owner.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=(WorkflowVersionStatus.published if published
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if published:
        d.current_version_id = v.id
    await db.commit()
    return d


def _line_fit(*, extra_node=None, extra_edges=()):
    nodes = [{"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
             {"id": "e", "type": "end", "position": {"x": 0, "y": 2},
              "data": {"config": {"outcome": "completed"}}}]
    edges = [{"id": "e1", "source": "s", "target": "e"}]
    if extra_node:
        nodes.insert(1, extra_node)
        edges = list(extra_edges)
    return {"nodes": nodes, "edges": edges}


async def test_a_subflow_calls_a_named_flow(client, db):
    anna = await make_user(db, "anna")
    kind = await _flow(db, anna, "kind", _line_fit())
    parent = await _flow(db, anna, "eltern", _line_fit(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {"definition_id": kind.id}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e",
                      "sourceHandle": "completed"}]))

    r = await client.post(f"/workflows/{parent.id}/instances", headers=auth(anna),
                          json={"subject_kind": "standalone"})
    assert r.status_code in (200, 201), r.text

    children = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == kind.id))).scalars().all()
    assert len(children) == 1, "the named flow has to have run as a child instance"


async def test_without_a_flow_the_check_complains():
    graph = _line_fit(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e", "sourceHandle": "completed"}])
    error = engine.validate_graph(WorkflowSubjectKind.standalone, graph)
    assert any("no flow chosen" in f for f in error)


async def test_a_named_flow_satisfies_the_check():
    graph = _line_fit(
        extra_node={"id": "unter", "type": "subflow", "position": {"x": 0, "y": 1},
                    "data": {"config": {"definition_id": 7}}},
        extra_edges=[{"id": "a", "source": "s", "target": "unter"},
                     {"id": "b", "source": "unter", "target": "e", "sourceHandle": "completed"}])
    assert engine.validate_graph(WorkflowSubjectKind.standalone, graph) == []


async def test_calling_itself_is_refused(client, db):
    """A flow that enters itself as a sub-flow would run endlessly."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna, "ich", _line_fit())
    graph = _line_fit(
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
    assert "calls itself" in (inst[0].error or "")
