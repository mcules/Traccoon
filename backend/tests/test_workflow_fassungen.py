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
from app.services import workflow_graph as graf
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


async def _fassungen(db, def_id: int) -> list[WorkflowVersion]:
    return list((await db.execute(select(WorkflowVersion).where(
        WorkflowVersion.definition_id == def_id).order_by(WorkflowVersion.version))).scalars().all())


# ── Der Schnitt: Inhalt gegen Anordnung ──────────────────────────────────────

async def test_move_ist_keine_aenderung():
    """Drei Zentimeter nach links sind keine Aussage über das Verhalten."""
    a = _graph(x=0)
    b = _graph(x=750)
    assert graf.gleicher_inhalt(a, b)
    assert graf.unterschiede(a, b)["identical"] is True


async def test_ein_anderer_parameter_ist_eine_aenderung():
    assert not graf.gleicher_inhalt(_graph(tool="a"), _graph(tool="b"))


async def test_reihenfolge_der_node_zaehlt_nicht():
    """Der Editor liefert Knoten in wechselnder Reihenfolge; das ist kein Unterschied."""
    a = _graph()
    b = {"nodes": list(reversed(a["nodes"])), "edges": list(reversed(a["edges"]))}
    assert graf.gleicher_inhalt(a, b)


async def test_unterschiede_nennen_das_field_nicht_den_klotz():
    """„Die Aktion hat sich geändert" ist keine Antwort, wenn die Aktion zwei Seiten JSON ist."""
    u = graf.unterschiede(_graph(tool="alt"), _graph(tool="neu"))
    (node,) = u["nodes_changed"]
    assert node["id"] == "tun"
    fields = {f["field"] for f in node["fields"]}
    assert fields == {"action.params.wert"}, fields


async def test_new_und_entfernte_node_und_edges():
    a = _graph()
    b = _graph()
    b["nodes"] = [n for n in b["nodes"] if n["id"] != "ende"]
    b["edges"] = [e for e in b["edges"] if e["target"] != "ende"]
    u = graf.unterschiede(a, b)
    assert [k["id"] for k in u["nodes_removed"]] == ["ende"]
    assert u["edges_removed"] == ["tun → ende"]
    assert u["nodes_added"] == [] and u["edges_added"] == []


# ── Was der Editor daraus macht ──────────────────────────────────────────────

async def test_ansehen_legt_nichts_an(db, client):
    """Der eigentliche Anlass: Hinsehen darf keine Fassung kosten."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    r = await client.get(f"/workflows/{d.id}/editable", headers=auth(anna))

    assert r.status_code == 200
    assert r.json()["status"] == "published", "geliefert wird die Live-Fassung, kein Klon"
    assert len(await _fassungen(db, d.id)) == 1


async def test_anordnen_speichert_ohne_new_fassung(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    r = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                         json={"graph": _graph(x=900)})

    assert r.status_code == 200 and r.json()["result"] == "layout"
    fassungen = await _fassungen(db, d.id)
    assert len(fassungen) == 1 and fassungen[0].status == WorkflowVersionStatus.published
    # Die Anordnung ist trotzdem da, sonst wäre das Speichern eine Lüge.
    await db.refresh(fassungen[0])
    assert graf.positionen(fassungen[0].graph)["start"]["x"] == 900


async def test_inhaltliche_aenderung_legt_genau_einen_entwurf_an(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)

    erst = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                            json={"graph": _graph(tool="neu")})
    assert erst.json()["result"] == "neuer_entwurf"

    # Zweites Speichern schreibt in denselben Entwurf, statt Nummern zu verbrauchen.
    zweit = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                             json={"graph": _graph(tool="noch neuer")})
    assert zweit.json()["result"] == "entwurf"
    assert zweit.json()["version"]["id"] == erst.json()["version"]["id"]

    fassungen = await _fassungen(db, d.id)
    assert [f.status for f in fassungen] == [WorkflowVersionStatus.published,
                                             WorkflowVersionStatus.draft]


async def test_anordnen_im_entwurf_bleibt_im_entwurf(db, client):
    """Wer schon einen Entwurf offen hat, ordnet darin an und nicht in der Live-Fassung."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                         json={"graph": _graph(x=500, tool="neu")})

    assert r.json()["result"] == "layout"
    fassungen = await _fassungen(db, d.id)
    assert len(fassungen) == 2
    assert graf.positionen(fassungen[0].graph)["start"]["x"] == 0, "die Live-Fassung bleibt"


async def test_editor_bekommt_den_offenen_entwurf(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.get(f"/workflows/{d.id}/editable", headers=auth(anna))
    assert r.json()["status"] == "draft"


async def test_entwurf_verwerfen_laesst_nichts_zurueck(db, client):
    """Ohne diesen Weg musste man einen verfahrenen Graphen von Hand zurückbauen."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    await client.put(f"/workflows/{d.id}/graph", headers=auth(anna),
                     json={"graph": _graph(tool="neu")})

    r = await client.delete(f"/workflows/{d.id}/draft", headers=auth(anna))

    assert r.status_code == 204
    fassungen = await _fassungen(db, d.id)
    assert len(fassungen) == 1 and fassungen[0].status == WorkflowVersionStatus.published
    # Und der Editor zeigt danach wieder die Live-Fassung.
    assert (await client.get(f"/workflows/{d.id}/editable",
                             headers=auth(anna))).json()["status"] == "published"


async def test_verwerfen_ohne_entwurf_ist_kein_error(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    assert (await client.delete(f"/workflows/{d.id}/draft", headers=auth(anna))).status_code == 204


# ── Vergleichen und zurückrollen ─────────────────────────────────────────────

async def test_diff_vergleicht_ohne_angabe_mit_dem_vorgaenger(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    zweite = WorkflowVersion(definition_id=d.id, version=2, graph=_graph(tool="neu"),
                             status=WorkflowVersionStatus.published, created_by=anna.id)
    db.add(zweite)
    await db.commit()

    r = await client.get(f"/workflows/{d.id}/versions/{zweite.id}/diff", headers=auth(anna))

    daten = r.json()
    assert daten["from_version"] == 1 and daten["to_version"] == 2 and daten["identical"] is False
    assert daten["nodes_changed"][0]["fields"][0]["field"] == "action.params.wert"


async def test_diff_gegen_eine_bestimmte_fassung(db, client):
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    v1 = (await _fassungen(db, d.id))[0]

    r = await client.get(f"/workflows/{d.id}/versions/{v1.id}/diff?against={v1.id}",
                         headers=auth(anna))
    assert r.json()["identical"] is True


async def test_zurueckrollen_legt_eine_new_fassung_an(db, client):
    """Nicht den Zeiger biegen: laufende Instanzen hängen an ihrer Fassung, und die
    Geschichte soll zeigen, dass zurückgerollt wurde."""
    anna = await make_user(db, "anna")
    d = await _flow(db, anna)
    v1 = (await _fassungen(db, d.id))[0]
    zweite = WorkflowVersion(definition_id=d.id, version=2, graph=_graph(tool="neu"),
                             status=WorkflowVersionStatus.published, created_by=anna.id)
    db.add(zweite)
    await db.flush()
    d.current_version_id = zweite.id
    await db.commit()

    r = await client.post(f"/workflows/{d.id}/versions/{v1.id}/rollback", headers=auth(anna))

    assert r.status_code == 200
    fassungen = await _fassungen(db, d.id)
    assert len(fassungen) == 3, "die alte Fassung bleibt stehen"
    await db.refresh(d)
    assert d.current_version_id == fassungen[-1].id
    assert graf.gleicher_inhalt(fassungen[-1].graph, _graph())
