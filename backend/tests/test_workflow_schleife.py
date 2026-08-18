"""Schleifen: durch die Daten hindurch, nicht nur an sie heran.

Bis hierhin führte ein Ablauf jeden Schritt genau einmal aus. Geprüft wird das, was dabei
leicht schiefgeht: dass der Zähler einen Wartepunkt überlebt, dass eine leere Liste nicht
in den Körper läuft, dass zwei Schleifen nacheinander wieder von vorn beginnen — und dass
eine Liste ohne Ende trotzdem eines hat.
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


def _graph(*, liste="posten", max_=None, sammle=None) -> dict:
    """start → Schleife → (je Element: Kontext setzen) → zurück; fertig → Ende."""
    cfg = {"label": "Für jedes", "liste": liste, "element": "posten_eins", "index": "nr"}
    if max_ is not None:
        cfg["max"] = max_
    if sammle:
        cfg["sammle"] = sammle
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


async def _lauf(db, graph: dict, kontext: dict) -> WorkflowInstance:
    user = await make_user(db, f"u{abs(hash(str(kontext))) % 10000}")
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
                                context=kontext, actor_id=user.id)


async def test_jedes_element_kommt_einmal_dran(db):
    inst = await _lauf(db, _graph(), {"posten": ["eins", "zwei", "drei"]})
    assert inst.status == WorkflowInstanceStatus.completed
    # Der Körper hat zuletzt das dritte Element gesehen …
    assert inst.context["gesehen"] == "drei"
    # … und der Zähler ist danach aufgeräumt, nicht als Rest im Kontext liegengeblieben.
    assert inst.context.get("_schleifen") == {}
    assert "posten_eins" not in inst.context


async def test_leere_liste_geht_gar_nicht_erst_hinein(db):
    inst = await _lauf(db, _graph(), {"posten": []})
    assert inst.status == WorkflowInstanceStatus.completed
    assert "gesehen" not in inst.context


async def test_fehlende_liste_ist_kein_absturz(db):
    """Ein Pfad, den es nicht gibt, ist im Betrieb der Normalfall (Gegenstelle liefert
    nichts) — er darf den Lauf nicht kippen."""
    inst = await _lauf(db, _graph(liste="gibts.nicht"), {"posten": ["x"]})
    assert inst.status == WorkflowInstanceStatus.completed
    assert "gesehen" not in inst.context


async def test_einzelwert_wird_wie_eine_liste_mit_einem_element_behandelt(db):
    """Viele Gegenstellen liefern bei genau einem Treffer kein Array — das ist kein Fehler
    des Menschen, der den Ablauf gebaut hat."""
    inst = await _lauf(db, _graph(), {"posten": "allein"})
    assert inst.context["gesehen"] == "allein"


async def test_sammeln_haelt_die_ergebnisse_fest(db):
    inst = await _lauf(db, _graph(sammle="gesehen"), {"posten": ["a", "b"]})
    assert inst.context["ergebnisse"] == ["a", "b"]
    assert inst.context["nr_gesamt"] == 2


async def test_lange_liste_wird_gedeckelt(db):
    """Gegen die Liste, die aus Versehen 100 000 Zeilen hat: der Knoten hat sein eigenes
    Maß, unabhängig von der Zyklus-Bremse der Engine."""
    inst = await _lauf(db, _graph(max_=3), {"posten": list("abcdefgh")})
    assert inst.status == WorkflowInstanceStatus.completed
    assert inst.context["gesehen"] == "c"          # nach dem dritten ist Schluss
    assert inst.context["nr_gesamt"] == 8          # gemeldet wird die wahre Länge


async def test_zwei_durchlaeufe_beginnen_wieder_von_vorn(db):
    """Läuft derselbe Ablauf ein zweites Mal (oder eine äußere Schleife), darf kein Zähler
    von gestern übrig sein."""
    graph = _graph()
    erste = await _lauf(db, graph, {"posten": ["a", "b"]})
    assert erste.context["gesehen"] == "b"

    d = await db.get(WorkflowDefinition, erste.definition_id)
    zweite = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                  context={"posten": ["x"]}, actor_id=erste.started_by)
    assert zweite.context["gesehen"] == "x"
    assert zweite.context.get("_schleifen") == {}


async def test_validierung_verlangt_beide_ausgaenge_und_eine_liste():
    graph = _graph()
    assert validate_graph("standalone", graph) == []

    ohne_fertig = _graph()
    ohne_fertig["edges"] = [e for e in ohne_fertig["edges"] if e.get("sourceHandle") != "fertig"]
    assert any("fertig" in f for f in validate_graph("standalone", ohne_fertig))

    ohne_liste = _graph()
    del ohne_liste["nodes"][1]["data"]["config"]["liste"]
    assert any("keine Liste" in f for f in validate_graph("standalone", ohne_liste))
