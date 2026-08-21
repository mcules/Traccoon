"""Trial run: playing the flow through without anything happening.

The core of these tests is the promise the trial run gives: **nothing happens**. Everything
else (branches take hold, expressions compute, the run reaches the end) is there so that the
trial says anything at all.
"""
import pytest
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.ticket import Issue
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import workflow_tools
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


def _graph() -> dict:
    """Trigger, call a tool, branch, (create a ticket | wait), end."""
    return {
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"config": {"label": "Start", "trigger": {"kind": "webhook"}}}},
            {"id": "werkzeug", "type": "auto_action", "position": {"x": 0, "y": 1},
             "data": {"config": {"action": {"action": "tool_call", "params": {
                 "tool": "obsidian__obsidian_append_to_note",
                 "arguments": {"path": "{{ vorgang.titel | kurz:20 }}.md"}}}}}},
            {"id": "weiche", "type": "decision", "position": {"x": 0, "y": 2},
             "data": {"config": {"label": "Wichtig?", "branches": [
                 {"handle": "ja", "label": "wichtig",
                  "guard": {">=": [{"var": "vorgang.stufe"}, 3]}},
                 {"handle": "nein", "label": "egal"}], "default_handle": "nein"}}},
            {"id": "ticket", "type": "auto_action", "position": {"x": -1, "y": 3},
             "data": {"config": {"action": {"action": "create_ticket", "params": {
                 "project_id": 1, "summary": "{{ vorgang.titel }}"}}}}},
            {"id": "warten", "type": "timer", "position": {"x": 1, "y": 3},
             "data": {"config": {"dauer": 2, "einheit": "h"}}},
            {"id": "ende", "type": "end", "position": {"x": 0, "y": 4},
             "data": {"config": {"outcome": "completed"}}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "werkzeug"},
            {"id": "e2", "source": "werkzeug", "target": "weiche"},
            {"id": "e3", "source": "weiche", "target": "ticket", "sourceHandle": "ja"},
            {"id": "e4", "source": "weiche", "target": "warten", "sourceHandle": "nein"},
            {"id": "e5", "source": "ticket", "target": "ende"},
            {"id": "e6", "source": "warten", "target": "ende"},
        ],
    }


async def _flow(db, owner, graph=None, published=False):
    d = WorkflowDefinition(project_id=None, key=f"probe{owner.id}", name="Probe",
                           created_by=owner.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph or _graph(),
                        status=(WorkflowVersionStatus.published if published
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if published:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_the_dry_run_runs_through_and_shows_what_it_would_do(client, db, monkeypatch):
    called = []
    monkeypatch.setattr(workflow_tools, "call",
                        lambda *a, **k: called.append(a) or {"ok": True, "text": ""})

    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "Störung in der Halle",
                                                        "stufe": 5}}})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == WorkflowInstanceStatus.completed.value

    steps = {s["node_id"]: s for s in data["steps"]}
    # The tool was NOT called: it only says what it would do.
    assert called == []
    assert "would run: tool_call" in steps["werkzeug"]["result"]["probe"]
    assert "obsidian__obsidian_append_to_note" in steps["werkzeug"]["result"]["probe"]
    # The branch really computed: level 5 >= 3, so the important path.
    assert "ticket" in steps and "warten" not in steps
    assert "would run: create_ticket" in steps["ticket"]["result"]["probe"]
    # And no ticket has come into being.
    assert (await db.execute(select(Issue))).scalars().all() == []


async def test_the_other_side_of_the_decision_can_be_checked_the_same_way(client, db):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "Kleinkram", "stufe": 1}}})
    steps = {s["node_id"]: s for s in r.json()["steps"]}
    assert "warten" in steps and "ticket" not in steps
    # The timer does not stop the trial run; otherwise one would never see the end.
    assert "would wait: 2 h" in steps["warten"]["result"]["probe"]
    assert r.json()["status"] == WorkflowInstanceStatus.completed.value


async def test_the_dry_run_takes_the_draft_not_the_published_version(client, db):
    """What gets checked is what you just built."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna, published=True)
    draft = _graph()
    draft["nodes"][1]["data"]["config"]["action"]["params"]["tool"] = "neues__werkzeug"
    db.add(WorkflowVersion(definition_id=d.id, version=2, graph=draft,
                           status=WorkflowVersionStatus.draft))
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "x", "stufe": 9}}})
    steps = {s["node_id"]: s for s in r.json()["steps"]}
    assert "neues__werkzeug" in steps["werkzeug"]["result"]["probe"]
    # The published version stays the one real runs point at.
    await db.refresh(d)
    assert (await db.get(WorkflowVersion, d.current_version_id)).version == 1


async def test_an_inconsistent_flow_is_not_played_through(client, db):
    """A trial run over a broken graph only creates confusion; better to say what is missing."""
    anna = await make_user(db, "anna")
    broken = _graph()
    broken["edges"] = [e for e in broken["edges"] if e.get("sourceHandle") != "ja"]
    d = await _flow(db, anna, graph=broken)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna), json={})
    assert r.status_code == 422 and "ja" in r.text


async def test_a_stranger_may_not_rehearse(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _flow(db, anna)
    assert (await client.post(f"/workflows/{d.id}/dry-run", headers=auth(bert),
                              json={})).status_code == 403


async def test_the_dry_run_checks_the_state_from_the_editor(client, db):
    """While building one changes things constantly without saving: what should be checked is
    what one sees in front of one, not what landed in the database last."""
    from app.models.workflow import WorkflowInstance

    anna = await make_user(db, "anna")
    d = await _flow(db, anna, published=True)

    editor = _graph()
    editor["nodes"][1]["data"]["config"]["action"]["params"]["tool"] = "gerade__gebaut"
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "x", "stufe": 9}},
                                "graph": editor})
    assert r.status_code == 201, r.text
    steps = {s["node_id"]: s for s in r.json()["steps"]}
    assert "gerade__gebaut" in steps["werkzeug"]["result"]["probe"]

    # The trial leaves nothing behind: no instance, no additional version.
    assert (await db.execute(select(WorkflowInstance))).scalars().all() == []
    versions = (await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == d.id))).scalars().all()
    assert [f.version for f in versions] == [1]
