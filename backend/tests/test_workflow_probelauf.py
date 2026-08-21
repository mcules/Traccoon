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


async def _flow(db, owner, graph=None, veroeffentlicht=False):
    d = WorkflowDefinition(project_id=None, key=f"probe{owner.id}", name="Probe",
                           created_by=owner.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph or _graph(),
                        status=(WorkflowVersionStatus.published if veroeffentlicht
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if veroeffentlicht:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_probelauf_running_durch_und_zeigt_was_er_taete(client, db, monkeypatch):
    gerufen = []
    monkeypatch.setattr(workflow_tools, "call",
                        lambda *a, **k: gerufen.append(a) or {"ok": True, "text": ""})

    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "Störung in der Halle",
                                                        "stufe": 5}}})
    assert r.status_code == 201, r.text
    daten = r.json()
    assert daten["status"] == WorkflowInstanceStatus.completed.value

    steps = {s["node_id"]: s for s in daten["steps"]}
    # The tool was NOT called: it only says what it would do.
    assert gerufen == []
    assert "würde ausführen: tool_call" in steps["werkzeug"]["result"]["probe"]
    assert "obsidian__obsidian_append_to_note" in steps["werkzeug"]["result"]["probe"]
    # The branch really computed: level 5 >= 3, so the important path.
    assert "ticket" in steps and "warten" not in steps
    assert "würde ausführen: create_ticket" in steps["ticket"]["result"]["probe"]
    # And no ticket has come into being.
    assert (await db.execute(select(Issue))).scalars().all() == []


async def test_die_andere_page_der_weiche_laesst_sich_genauso_check(client, db):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "Kleinkram", "stufe": 1}}})
    steps = {s["node_id"]: s for s in r.json()["steps"]}
    assert "warten" in steps and "ticket" not in steps
    # The timer does not stop the trial run; otherwise one would never see the end.
    assert "würde warten: 2 h" in steps["warten"]["result"]["probe"]
    assert r.json()["status"] == WorkflowInstanceStatus.completed.value


async def test_probelauf_nimmt_den_entwurf_nicht_das_veroeffentlichte(client, db):
    """What gets checked is what you just built."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna, veroeffentlicht=True)
    entwurf = _graph()
    entwurf["nodes"][1]["data"]["config"]["action"]["params"]["tool"] = "neues__werkzeug"
    db.add(WorkflowVersion(definition_id=d.id, version=2, graph=entwurf,
                           status=WorkflowVersionStatus.draft))
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna),
                          json={"context": {"vorgang": {"titel": "x", "stufe": 9}}})
    steps = {s["node_id"]: s for s in r.json()["steps"]}
    assert "neues__werkzeug" in steps["werkzeug"]["result"]["probe"]
    # The published version stays the one real runs point at.
    await db.refresh(d)
    assert (await db.get(WorkflowVersion, d.current_version_id)).version == 1


async def test_unschluessiger_flow_wird_nicht_durchgespielt(client, db):
    """A trial run over a broken graph only creates confusion; better to say what is missing."""
    anna = await make_user(db, "anna")
    kaputt = _graph()
    kaputt["edges"] = [e for e in kaputt["edges"] if e.get("sourceHandle") != "ja"]
    d = await _flow(db, anna, graph=kaputt)
    r = await client.post(f"/workflows/{d.id}/dry-run", headers=auth(anna), json={})
    assert r.status_code == 422 and "ja" in r.text


async def test_fremder_may_nicht_proben(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _flow(db, anna)
    assert (await client.post(f"/workflows/{d.id}/dry-run", headers=auth(bert),
                              json={})).status_code == 403


async def test_probelauf_prueft_den_state_aus_dem_editor(client, db):
    """While building one changes things constantly without saving: what should be checked is
    what one sees in front of one, not what landed in the database last."""
    from app.models.workflow import WorkflowInstance

    anna = await make_user(db, "anna")
    d = await _flow(db, anna, veroeffentlicht=True)

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
    fassungen = (await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == d.id))).scalars().all()
    assert [f.version for f in fassungen] == [1]
