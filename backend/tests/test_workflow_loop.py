"""Loops: through the data, not only up to it.

Until now a flow executed every step exactly once. What is checked is what easily goes wrong
there: that the counter survives a waiting point, that an empty list does not run into the
body, that two loops one after another begin from the front again, and that a list without
an end still has one.
"""
import pytest
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_engine import start_workflow, validate_graph
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(*, listing="posten", max_=None, collect=None) -> dict:
    """start, loop, (per element: set the context), back; when finished, end."""
    cfg = {"label": "Für jedes", "liste": listing, "element": "posten_eins", "index": "nr"}
    if max_ is not None:
        cfg["max"] = max_
    if collect:
        cfg["sammle"] = collect
    return {
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"config": {"label": "Start"}}},
            {"id": "schleife", "type": "loop", "position": {"x": 0, "y": 1},
             "data": {"config": cfg}},
            {"id": "koerper", "type": "auto_action", "position": {"x": 1, "y": 2},
             "data": {"config": {"action": {"action": "set_context", "params": {
                 "gesehen": "{{ posten_eins }}"}}}}},
            {"id": "ende", "type": "end", "position": {"x": 0, "y": 3},
             "data": {"config": {"outcome": "completed"}}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "schleife"},
            {"id": "e2", "source": "schleife", "target": "koerper", "sourceHandle": "element"},
            {"id": "e3", "source": "koerper", "target": "schleife"},
            {"id": "e4", "source": "schleife", "target": "ende", "sourceHandle": "fertig"},
        ],
    }


async def _run(db, graph: dict, context: dict) -> WorkflowInstance:
    user = await make_user(db, f"u{abs(hash(str(context))) % 10000}")
    d = WorkflowDefinition(project_id=None, key=f"schleife{abs(hash(str(graph))) % 10000}",
                           name="Schleife", subject_kind=WorkflowSubjectKind.standalone,
                           created_by=user.id)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context=context, actor_id=user.id)


async def test_every_element_gets_its_turn_once(db):
    inst = await _run(db, _graph(), {"posten": ["eins", "zwei", "drei"]})
    assert inst.status == WorkflowInstanceStatus.completed
    # The body last saw the third element …
    assert inst.context["gesehen"] == "drei"
    # … and the counter is cleaned up afterwards, not left lying in the context.
    assert inst.context.get("_schleifen") == {}
    assert "posten_eins" not in inst.context


async def test_an_empty_list_never_even_enters(db):
    inst = await _run(db, _graph(), {"posten": []})
    assert inst.status == WorkflowInstanceStatus.completed
    assert "gesehen" not in inst.context


async def test_a_missing_list_is_no_crash(db):
    """A path that does not exist is the normal case in operation (the counterpart delivers
    nothing), and it must not topple the run."""
    inst = await _run(db, _graph(listing="gibts.nicht"), {"posten": ["x"]})
    assert inst.status == WorkflowInstanceStatus.completed
    assert "gesehen" not in inst.context


async def test_a_single_value_is_treated_like_a_list_of_one(db):
    """Many counterparts deliver no array with exactly one hit, and that is no error of the
    human who built the flow."""
    inst = await _run(db, _graph(), {"posten": "allein"})
    assert inst.context["gesehen"] == "allein"


async def test_collecting_holds_on_to_the_results(db):
    inst = await _run(db, _graph(collect="gesehen"), {"posten": ["a", "b"]})
    assert inst.context["ergebnisse"] == ["a", "b"]
    assert inst.context["nr_gesamt"] == 2


async def test_a_long_list_is_capped(db):
    """Against the list that accidentally has 100 000 rows: the node has a measure of its own,
    independently of the cycle brake of the engine."""
    inst = await _run(db, _graph(max_=3), {"posten": list("abcdefgh")})
    assert inst.status == WorkflowInstanceStatus.completed
    assert inst.context["gesehen"] == "c"          # after the third it stops
    assert inst.context["nr_gesamt"] == 8          # what is reported is the true length


async def test_two_passes_start_over_again(db):
    """If the same flow runs a second time (or an outer loop), no counter from yesterday may
    be left."""
    graph = _graph()
    first = await _run(db, graph, {"posten": ["a", "b"]})
    assert first.context["gesehen"] == "b"

    d = await db.get(WorkflowDefinition, first.definition_id)
    second = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                  context={"posten": ["x"]}, actor_id=first.started_by)
    assert second.context["gesehen"] == "x"
    assert second.context.get("_schleifen") == {}


async def test_validation_demands_both_exits_and_a_list():
    graph = _graph()
    assert validate_graph("standalone", graph) == []

    without_done = _graph()
    without_done["edges"] = [e for e in without_done["edges"] if e.get("sourceHandle") != "fertig"]
    assert any("fertig" in f for f in validate_graph("standalone", without_done))

    without_listing = _graph()
    del without_listing["nodes"][1]["data"]["config"]["liste"]
    assert any("keine Liste" in f for f in validate_graph("standalone", without_listing))
