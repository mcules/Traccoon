"""Versions are about behaviour, not about the picture.

A flow used to get a new draft version the moment somebody looked at it, and moving a box
marked it as "differs from the published one". The history of `schnee-winterreifen` collected
two versions on its first day in which nothing had happened, and a stale draft from such a
look would have silently overwritten two later corrections on the next publish.

What is checked here is the dividing line: an arrangement is saved without a version, a
changed content produces exactly one draft, and a discarded draft leaves nothing behind.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import workflow_graph as wgraph
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


def _graph(x: int = 0, label: str = "Start", tool: str = "a") -> dict:
    return {
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": x, "y": 0},
             "data": {"config": {"label": label}}},
            {"id": "tun", "type": "auto_action", "position": {"x": x + 100, "y": 0},
             "data": {"config": {"label": "Tun", "action": {
                 "action": "set_context", "params": {"wert": tool}}}}},
            {"id": "ende", "type": "end", "position": {"x": x + 200, "y": 0},
             "data": {"config": {"label": "Ende", "outcome": "completed"}}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "tun"},
            {"id": "e2", "source": "tun", "target": "ende"},
        ],
    }


async def _flow(db, user, graph=None) -> WorkflowDefinition:
    d = WorkflowDefinition(project_id=None, key="probe", name="Probe", created_by=user.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph or _graph(),
                        status=WorkflowVersionStatus.published, created_by=user.id)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _versions(db, def_id: int) -> list[WorkflowVersion]:
    return list((await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == def_id).order_by(WorkflowVersion.version))).scalars().all())


# ── Der Schnitt: Inhalt gegen Anordnung ──────────────────────────────────────

async def test_moving_is_not_a_change():
    """Three centimetres to the left are no statement about behaviour."""
    a = _graph(x=0)
    b = _graph(x=750)
    assert wgraph.same_content(a, b)
    assert wgraph.differences(a, b)["identical"] is True


async def test_a_different_parameter_is_a_change():
    assert not wgraph.same_content(_graph(tool="a"), _graph(tool="b"))


async def test_the_order_of_the_nodes_does_not_count():
    """The editor delivers nodes in changing order; that is no difference."""
    a = _graph()
    b = {"nodes": list(reversed(a["nodes"])), "edges": list(reversed(a["edges"]))}
    assert wgraph.same_content(a, b)


async def test_differences_name_the_field_not_the_lump():
    """"The action has changed" is no answer when the action is two pages of JSON."""
    u = wgraph.differences(_graph(tool="alt"), _graph(tool="neu"))
    (node,) = u["nodes_changed"]
    assert node["id"] == "tun"
    fields = {f["field"] for f in node["fields"]}
    assert fields == {"action.params.wert"}, fields


async def test_new_and_removed_nodes_and_edges():
    a = _graph()
    b = _graph()
    b["nodes"] = [n for n in b["nodes"] if n["id"] != "ende"]
    b["edges"] = [e for e in b["edges"] if e["target"] != "ende"]
    u = wgraph.differences(a, b)
    assert [k["id"] for k in u["nodes_removed"]] == ["ende"]
    assert u["edges_removed"] == ["tun → ende"]
    assert u["nodes_added"] == [] and u["edges_added"] == []


# ── What the editor makes of it ─────────────────────────────────────────────

async def test_viewing_creates_nothing(db, client):
    """The actual occasion: looking must not cost a version."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    r = await client.get(f"/workflows/{d.id}/editable", headers=auth(anna))

    assert r.status_code == 200
    assert r.json()["status"] == "published", "geliefert wird die Live-Fassung, kein Klon"
    assert len(await _versions(db, d.id)) == 1


async def test_rearranging_saves_without_a_new_version(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    r = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                         json={"graph": _graph(x=900)})

    assert r.status_code == 200 and r.json()["result"] == "layout"
    versions = await _versions(db, d.id)
    assert len(versions) == 1 and versions[0].status == WorkflowVersionStatus.published
    # The arrangement is there all the same, otherwise saving would be a lie.
    await db.refresh(versions[0])
    assert wgraph.positions(versions[0].graph)["start"]["x"] == 900


async def test_a_substantive_change_creates_exactly_one_draft(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    first = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                            json={"graph": _graph(tool="neu")})
    assert first.json()["result"] == "neuer_entwurf"

    # Zweites Speichern schreibt in denselben Entwurf, statt Nummern zu verbrauchen.
    second = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                             json={"graph": _graph(tool="noch neuer")})
    assert second.json()["result"] == "entwurf"
    assert second.json()["version"]["id"] == first.json()["version"]["id"]

    versions = await _versions(db, d.id)
    assert [f.status for f in versions] == [WorkflowVersionStatus.published,
                                             WorkflowVersionStatus.draft]


async def test_rearranging_in_the_draft_stays_in_the_draft(db, client):
    """Whoever already has a draft open arranges in it and not in the live version."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                         json={"graph": _graph(x=500, tool="neu")})

    assert r.json()["result"] == "layout"
    versions = await _versions(db, d.id)
    assert len(versions) == 2
    assert wgraph.positions(versions[0].graph)["start"]["x"] == 0, "die Live-Fassung bleibt"


async def test_the_editor_receives_the_open_draft(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.get(f"/workflows/{d.id}/editable", headers=auth(anna))
    assert r.json()["status"] == "draft"


async def test_discarding_the_draft_leaves_nothing_behind(db, client):
    """Without this way one had to unpick a stuck graph by hand."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.delete(f"/workflows/{d.id}/draft", headers=auth(anna))

    assert r.status_code == 204
    versions = await _versions(db, d.id)
    assert len(versions) == 1 and versions[0].status == WorkflowVersionStatus.published
    # And afterwards the editor shows the live version again.
    assert (await client.get(f"/workflows/{d.id}/editable",
                             headers=auth(anna))).json()["status"] == "published"


async def test_discarding_without_a_draft_is_not_an_error(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    assert (await client.delete(f"/workflows/{d.id}/draft", headers=auth(anna))).status_code == 204


# ── Comparing and rolling back ──────────────────────────────────────────────

async def test_the_diff_compares_with_the_predecessor_by_default(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    second = WorkflowVersion(definition_id=d.id, version=2, graph=_graph(tool="neu"),
                             status=WorkflowVersionStatus.published, created_by=anna.id)
    db.add(second)
    await db.commit()

    r = await client.get(f"/workflows/{d.id}/versions/{second.id}/diff", headers=auth(anna))

    data = r.json()
    assert data["from_version"] == 1 and data["to_version"] == 2 and data["identical"] is False
    assert data["nodes_changed"][0]["fields"][0]["field"] == "action.params.wert"


async def test_a_diff_against_a_particular_version(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    v1 = (await _versions(db, d.id))[0]

    r = await client.get(f"/workflows/{d.id}/versions/{v1.id}/diff?against={v1.id}",
                         headers=auth(anna))
    assert r.json()["identical"] is True


async def test_rolling_back_creates_a_new_version(db, client):
    """Do not bend the pointer: running instances hang on their version, and the history
    should show that a rollback happened."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    v1 = (await _versions(db, d.id))[0]
    second = WorkflowVersion(definition_id=d.id, version=2, graph=_graph(tool="neu"),
                             status=WorkflowVersionStatus.published, created_by=anna.id)
    db.add(second)
    await db.flush()
    d.current_version_id = second.id
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/versions/{v1.id}/rollback", headers=auth(anna))

    assert r.status_code == 200
    versions = await _versions(db, d.id)
    assert len(versions) == 3, "die alte Fassung bleibt stehen"
    await db.refresh(d)
    assert d.current_version_id == versions[-1].id
    assert wgraph.same_content(versions[-1].graph, _graph())
