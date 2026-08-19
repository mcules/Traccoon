"""Switching a step off: skip it, or stop the flow right there.

Two different needs behind the same switch. While building you take a step out of the way
and everything after it should keep running. In an emergency you pull the handbrake, and a
flow that silently continued past the switched-off step would be the dangerous outcome. So
the mode is explicit and the engine never guesses.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import (
    WorkflowDefinition, WorkflowInstance, WorkflowStepRun, WorkflowVersion,
)
from app.services.workflow_engine import start_workflow, validate_graph
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(cfg: dict) -> dict:
    """start -> action (configurable) -> action (marker) -> end."""
    return {"nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
        {"id": "tut_was", "type": "auto_action", "position": {"x": 0, "y": 1},
         "data": {"config": {"label": "Kontext setzen",
                             "action": {"action": "set_context", "params": {"a": "1"}},
                             **cfg}}},
        {"id": "danach", "type": "auto_action", "position": {"x": 0, "y": 2},
         "data": {"config": {"action": {"action": "set_context", "params": {"b": "2"}}}}},
        {"id": "e", "type": "end", "position": {"x": 0, "y": 3},
         "data": {"config": {"outcome": "completed"}}}],
        "edges": [{"id": "k1", "source": "s", "target": "tut_was"},
                  {"id": "k2", "source": "tut_was", "target": "danach"},
                  {"id": "k3", "source": "danach", "target": "e"}]}


async def _lauf(db, cfg: dict) -> WorkflowInstance:
    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="schalter", name="Schalter",
                           created_by=anna.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=_graph(cfg),
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=anna.id, source="test")
    return await db.get(WorkflowInstance, inst.id)


async def _schritte(db, inst) -> dict:
    rows = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id))).scalars().all()
    return {r.node_id: r for r in rows}


async def test_aktiver_schritt_wirkt_wie_bisher(db):
    inst = await _lauf(db, {})
    assert inst.status.value == "completed"
    assert inst.context["a"] == "1" and inst.context["b"] == "2"


async def test_uebersprungen_laesst_den_rest_laufen(db):
    inst = await _lauf(db, {"deaktiviert": True, "deaktiviert_modus": "ueberspringen"})
    assert inst.status.value == "completed"
    assert "a" not in inst.context, "the disabled action must not have done anything"
    assert inst.context["b"] == "2", "it has to continue after that"
    schritte = await _schritte(db, inst)
    assert schritte["tut_was"].status.value == "skipped"
    assert schritte["tut_was"].result["deaktiviert"] is True


async def test_abbrechen_beendet_den_lauf(db):
    inst = await _lauf(db, {"deaktiviert": True, "deaktiviert_modus": "abbrechen"})
    assert inst.status.value == "cancelled"
    assert "abgeschaltet" in (inst.error or "")
    assert "a" not in inst.context and "b" not in inst.context
    schritte = await _schritte(db, inst)
    assert "danach" not in schritte, "nothing may run after the abort"


async def test_ohne_modus_wird_uebersprungen(db):
    """The harmless case is the default; the dangerous one has to be named."""
    inst = await _lauf(db, {"deaktiviert": True})
    assert inst.status.value == "completed" and inst.context["b"] == "2"


async def test_pruefung_bleibt_zufrieden(db):
    """Ein abgeschalteter Schritt ist kein Fehler im Graphen."""
    assert validate_graph(WorkflowSubjectKind.standalone,
                          _graph({"deaktiviert": True})) == []
