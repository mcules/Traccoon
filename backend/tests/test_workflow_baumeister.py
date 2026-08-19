"""The builder: a graph out of a description, and what happens when it slips.

What is checked is not the model (nobody can test that) but everything around it: that a
clean graph comes out of a crooked answer, that the validation takes hold, that a faulty
drawing gets exactly ONE correction and that a draft stores nothing.
"""
import json

import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import workflow_author as autor
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


GUT = {
    "erklaerung": "Meldet eingehende Störungen weiter.",
    "nodes": [
        {"id": "start", "type": "start", "data": {"config": {"label": "Start"}}},
        {"id": "melden", "type": "auto_action", "data": {"config": {
            "label": "Melden", "action": {"action": "notify", "params": {"title": "Hallo"}}}}},
        {"id": "ende", "type": "end", "data": {"config": {"outcome": "completed"}}},
    ],
    "edges": [{"id": "e1", "source": "start", "target": "melden"},
              {"id": "e2", "source": "melden", "target": "ende"}],
}

KAPUTT = {
    "erklaerung": "Halb fertig.",
    "nodes": [
        {"id": "start", "type": "start", "data": {"config": {}}},
        {"id": "weiche", "type": "decision", "data": {"config": {
            "branches": [{"handle": "ja"}, {"handle": "nein"}], "default_handle": "nein"}}},
        {"id": "ende", "type": "end", "data": {"config": {"outcome": "completed"}}},
    ],
    # The branch "ja" has no edge, and exactly that the validation reports.
    "edges": [{"id": "e1", "source": "start", "target": "weiche"},
              {"id": "e2", "source": "weiche", "target": "ende", "sourceHandle": "nein"}],
}


class _Antwort:
    def __init__(self, text): self.text = text


def _modell(monkeypatch, *antworten):
    """Replaces the LLM call by fixed answers; counts the rounds."""
    zaehler = {"n": 0}

    async def chat(**kwargs):
        i = min(zaehler["n"], len(antworten) - 1)
        zaehler["n"] += 1
        chat.letzte = kwargs
        return _Antwort(antworten[i])

    chat.letzte = {}
    monkeypatch.setattr(autor.llm_router, "chat", chat)
    monkeypatch.setattr(autor, "resolve_provider_token",
                        lambda *a, **k: _fertig("tok"))
    monkeypatch.setattr(autor, "_werkzeugliste", lambda *a, **k: _fertig(""))
    return zaehler, chat


def _fertig(wert):
    async def _f(): return wert
    return _f()


async def test_sauberer_entwurf(db, monkeypatch):
    anna = await make_user(db, "anna")
    _modell(monkeypatch, json.dumps(GUT))
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="melde Störungen",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert r["fehler"] == []
    assert r["erklaerung"].startswith("Meldet")
    assert [n["id"] for n in r["graph"]["nodes"]] == ["start", "melden", "ende"]
    # Positions come from the server, not from the model; otherwise everything would lie on top of each other.
    assert {n["position"]["y"] for n in r["graph"]["nodes"]} == {0, 130, 260}


async def test_konfiguration_wird_auch_flach_gefunden(db, monkeypatch):
    """Models like to put the configuration somewhere else; it must never arrive empty."""
    anna = await make_user(db, "anna")
    flach = {"erklaerung": "x", "edges": GUT["edges"], "nodes": [
        {"id": "start", "type": "start", "config": {"label": "Start"}},
        {"id": "melden", "type": "auto_action",
         "data": {"label": "Melden", "action": {"action": "notify", "params": {}}}},
        {"id": "ende", "type": "end", "data": {"config": {"outcome": "completed"}}}]}
    _modell(monkeypatch, json.dumps(flach))
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    cfgs = {n["id"]: n["data"]["config"] for n in r["graph"]["nodes"]}
    assert cfgs["start"]["label"] == "Start"
    assert cfgs["melden"]["action"]["action"] == "notify"


async def test_vergessener_standardzweig_wird_gesetzt(db, monkeypatch):
    """A branch without a default path demanded an edge called 'default', which nobody draws.
    We close that ourselves instead of sacrificing a model round for it."""
    anna = await make_user(db, "anna")
    ohne = {"erklaerung": "x", "nodes": [
        {"id": "start", "type": "start", "data": {"config": {}}},
        {"id": "w", "type": "decision", "data": {"config": {"branches": [
            {"handle": "ja", "guard": {"==": [{"var": "a"}, 1]}}, {"handle": "nein"}]}}},
        {"id": "e1", "type": "end", "data": {"config": {}}},
        {"id": "e2", "type": "end", "data": {"config": {}}}],
        "edges": [{"id": "k1", "source": "start", "target": "w"},
                  {"id": "k2", "source": "w", "target": "e1", "sourceHandle": "ja"},
                  {"id": "k3", "source": "w", "target": "e2", "sourceHandle": "nein"}]}
    zaehler, _ = _modell(monkeypatch, json.dumps(ohne))
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert r["fehler"] == [] and zaehler["n"] == 1, "keine Nachbesserungsrunde nötig"
    weiche = next(n for n in r["graph"]["nodes"] if n["id"] == "w")
    assert weiche["data"]["config"]["default_handle"] == "nein"
    assert all(n["data"]["config"]["outcome"] == "completed"
               for n in r["graph"]["nodes"] if n["type"] == "end")


async def test_code_zaeune_und_vorrede_stoeren_nicht(db, monkeypatch):
    anna = await make_user(db, "anna")
    _modell(monkeypatch, "Klar, hier:\n```json\n" + json.dumps(GUT) + "\n```\nViel Spaß!")
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert r["fehler"] == [] and len(r["graph"]["nodes"]) == 3


async def test_fehler_werden_zurueckgegeben_und_einmal_nachgebessert(db, monkeypatch):
    anna = await make_user(db, "anna")
    zaehler, chat = _modell(monkeypatch, json.dumps(KAPUTT), json.dumps(GUT))
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert zaehler["n"] == 2, "genau eine Nachbesserung"
    assert r["fehler"] == []
    # The correction gets the real validation sentences to see.
    nachricht = chat.letzte["messages"][-1]["content"]
    assert "Kante für Ausgang 'ja' fehlt" in nachricht


async def test_hartnaeckig_kaputt_kommt_trotzdem_an(db, monkeypatch):
    """An almost finished graph is worth more than an error message."""
    anna = await make_user(db, "anna")
    zaehler, _ = _modell(monkeypatch, json.dumps(KAPUTT))
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert zaehler["n"] == 2
    assert r["graph"]["nodes"] and r["fehler"]


async def test_kein_json_kein_absturz(db, monkeypatch):
    anna = await make_user(db, "anna")
    _modell(monkeypatch, "Tut mir leid, das kann ich nicht.")
    r = await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                              subject_kind=WorkflowSubjectKind.standalone)
    assert r["graph"] == {"nodes": [], "edges": []}
    assert r["fehler"]


async def test_umbau_bekommt_den_bestand_mit(db, monkeypatch):
    anna = await make_user(db, "anna")
    _, chat = _modell(monkeypatch, json.dumps(GUT))
    await autor.entwerfen(db, owner_id=anna.id, beschreibung="häng eine Freigabe davor",
                          subject_kind=WorkflowSubjectKind.standalone,
                          vorhanden={"nodes": GUT["nodes"], "edges": GUT["edges"]})
    auftrag = chat.letzte["messages"][-1]["content"]
    assert "bestehende Ablauf" in auftrag and "melden" in auftrag


async def test_ohne_ticket_werden_ticket_aktionen_ausgeschlossen(db, monkeypatch):
    anna = await make_user(db, "anna")
    _, chat = _modell(monkeypatch, json.dumps(GUT))
    await autor.entwerfen(db, owner_id=anna.id, beschreibung="x",
                          subject_kind=WorkflowSubjectKind.standalone)
    system = chat.letzte["messages"][0]["content"]
    assert "agent_task" in system and "NICHT zur Verfügung" in system


async def test_endpunkt_speichert_nichts(client, db, monkeypatch):
    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="entwurf", name="Entwurf", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.draft,
                        graph={"nodes": [], "edges": []})
    db.add(v)
    await db.commit()

    _modell(monkeypatch, json.dumps(GUT))
    r = await client.post(f"/workflows/{d.id}/entwurf", headers=auth(anna),
                          json={"beschreibung": "melde Störungen"})
    assert r.status_code == 200, r.text
    assert len(r.json()["graph"]["nodes"]) == 3

    await db.refresh(v)
    assert v.graph == {"nodes": [], "edges": []}, "der Entwurf darf nichts überschreiben"


async def test_fremder_darf_nicht_bauen(client, db, monkeypatch):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = WorkflowDefinition(project_id=None, key="privat", name="Privat", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.commit()
    _modell(monkeypatch, json.dumps(GUT))
    r = await client.post(f"/workflows/{d.id}/entwurf", headers=auth(bert),
                          json={"beschreibung": "irgendwas"})
    assert r.status_code == 403


async def test_ohne_zugang_klare_ansage(client, db, monkeypatch):
    anna = await make_user(db, "anna")
    d = WorkflowDefinition(project_id=None, key="ohnezugang", name="Ohne", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.commit()
    monkeypatch.setattr(autor, "resolve_provider_token", lambda *a, **k: _fertig(""))
    r = await client.post(f"/workflows/{d.id}/entwurf", headers=auth(anna),
                          json={"beschreibung": "melde Störungen"})
    assert r.status_code == 409
    assert "Claude-Zugang" in r.json()["detail"]
