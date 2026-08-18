"""Vorlagen: was zum Kopieren angeboten wird, muss auch laufen.

Eine Vorlage, die beim Veröffentlichen an der Prüfung scheitert, ist schlimmer als keine —
sie schickt jemanden mit einem kaputten Graphen los und lässt ihn den Fehler suchen, den
er nicht gemacht hat. Deshalb geht jede Vorlage hier durch dieselbe Prüfung wie ein
selbst gebauter Ablauf.
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
        assert "build" not in v          # der Graph gehört nicht in die Übersicht
        assert v["key"] and v["name"] and v["description"] and v["hinweis"]


@pytest.mark.parametrize("key", [v["key"] for v in vorlagen.VORLAGEN])
def test_vorlage_ist_gueltig(key):
    v = vorlagen.vorlage(key)
    graph = vorlagen.graph(key)
    fehler = validate_graph(v["subject_kind"], graph)
    assert fehler == [], f"{key}: {fehler}"


@pytest.mark.parametrize("key", [v["key"] for v in vorlagen.VORLAGEN])
def test_graph_ist_frisch(key):
    """Zwei Aufrufe dürfen sich nicht dasselbe dict teilen — sonst färbt ein Umbau ab."""
    a, b = vorlagen.graph(key), vorlagen.graph(key)
    assert a == b and a is not b
    a["nodes"][0]["data"]["config"]["label"] = "verbogen"
    assert vorlagen.graph(key)["nodes"][0]["data"]["config"]["label"] != "verbogen"


def test_unbekannte_vorlage():
    assert vorlagen.graph("gibt-es-nicht") is None
    assert vorlagen.vorlage("gibt-es-nicht") is None


def test_alle_standalone():
    """Ohne Ticket im Rücken: die Vorlagen sollen auch projektlos anlegbar sein."""
    assert all(v["subject_kind"] == WorkflowSubjectKind.standalone for v in vorlagen.VORLAGEN)


# ── Aus einer Vorlage anlegen (API) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_anlegen_aus_vorlage_bringt_den_ganzen_ablauf(client, db):
    """Wer eine Vorlage nimmt, bekommt keinen leeren Entwurf, sondern den Graphen."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "meldungen", "name": "Meldungen",
        "template": "meldung-von-aussen"})
    assert r.status_code == 201, r.text
    d = r.json()
    # Beschreibung kommt aus der Vorlage, wenn keine angegeben wurde.
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
