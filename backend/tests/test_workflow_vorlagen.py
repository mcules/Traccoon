"""Templates: what is offered for copying has to run as well.

A template that fails the validation on publishing is worse than none: it sends somebody off
with a broken graph and lets them look for the error they did not make. That is why every
template goes through the same validation here as a self-built flow.
"""
import pytest

from conftest import auth, make_user
from app.models.enums import WorkflowSubjectKind
from app.services import workflow_templates as vorlagen
from app.services.workflow_engine import validate_graph


def test_liste_ohne_graphen():
    liste = vorlagen.liste()
    assert len(liste) >= 4
    for v in liste:
        assert "build" not in v      # the graph does not belong in the overview
        assert v["key"] and v["name"] and v["description"] and v["hinweis"]


@pytest.mark.parametrize("key", [v["key"] for v in vorlagen.VORLAGEN])
def test_vorlage_ist_gueltig(key):
    v = vorlagen.vorlage(key)
    graph = vorlagen.graph(key)
    fehler = validate_graph(v["subject_kind"], graph)
    assert fehler == [], f"{key}: {fehler}"


@pytest.mark.parametrize("key", [v["key"] for v in vorlagen.VORLAGEN])
def test_graph_ist_frisch(key):
    """Two calls must not share the same dict; otherwise a rebuild bleeds through."""
    a, b = vorlagen.graph(key), vorlagen.graph(key)
    assert a == b and a is not b
    a["nodes"][0]["data"]["config"]["label"] = "verbogen"
    assert vorlagen.graph(key)["nodes"][0]["data"]["config"]["label"] != "verbogen"


def test_unbekannte_vorlage():
    assert vorlagen.graph("gibt-es-nicht") is None
    assert vorlagen.vorlage("gibt-es-nicht") is None


def test_alle_standalone():
    """Without a ticket behind it: the templates should be creatable project-less as well."""
    assert all(v["subject_kind"] == WorkflowSubjectKind.standalone for v in vorlagen.VORLAGEN)


# ── Creating from a template (API) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_anlegen_aus_vorlage_bringt_den_ganzen_ablauf(client, db):
    """Whoever takes a template gets not an empty draft but the graph."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "meldungen", "name": "Meldungen",
        "template": "meldung-von-aussen"})
    assert r.status_code == 201, r.text
    d = r.json()
    # The description comes from the template when none was given.
    assert "Webhook" in d["description"]

    versionen = (await client.get(f"/workflows/{d['id']}/versions", headers=auth(anna))).json()
    graph = versionen[0]["graph"]
    typen = sorted({n["type"] for n in graph["nodes"]})
    assert typen == ["auto_action", "decision", "end", "start"]
    assert len(graph["edges"]) == 4


@pytest.mark.asyncio
async def test_ohne_vorlage_bleibt_es_beim_geruest(client, db):
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "leer", "name": "Leer"})
    graph = (await client.get(f"/workflows/{r.json()['id']}/versions",
                              headers=auth(anna))).json()[0]["graph"]
    assert sorted(n["type"] for n in graph["nodes"]) == ["end", "start"]


@pytest.mark.asyncio
async def test_unbekannte_vorlage_wird_abgewiesen(client, db):
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "quatsch", "name": "Quatsch", "template": "gibt-es-nicht"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_uebersicht_wird_ausgeliefert(client, db):
    anna = await make_user(db, "anna")
    r = await client.get("/workflow-templates", headers=auth(anna))
    assert r.status_code == 200
    keys = [v["key"] for v in r.json()]
    assert "liste-abarbeiten" in keys
