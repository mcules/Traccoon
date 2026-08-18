"""Warten und Wiederholen: die zwei Dinge, die ein Ablauf können muss, wenn die Welt
draußen nicht sofort antwortet.

Geprüft wird die Mechanik dahinter: dass ein wartender Lauf einen Neustart überlebt (der
Wecker sitzt im Tick, nicht in einem schlafenden Task), dass ein abgelaufener Timer wirklich
weckt — und dass eine Wiederholung Abstand hält, statt in derselben Sekunde denselben Fehler
zu erzeugen.
"""
import datetime as dt

import pytest
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowTokenState, WorkflowVersionStatus,
)
from app.models.workflow import (
    WorkflowDefinition, WorkflowStepRun, WorkflowToken, WorkflowVersion,
)
from app.services import workflow_actions, workflow_engine
from app.services.workflow_engine import faellige_timer, start_workflow, validate_graph
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _graph(timer: dict | None = None, aktion: dict | None = None) -> dict:
    knoten = [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
               "data": {"config": {"label": "Start"}}}]
    kanten = []
    vorher = "s"
    if timer is not None:
        knoten.append({"id": "warten", "type": "timer", "position": {"x": 0, "y": 1},
                       "data": {"config": timer}})
        kanten.append({"id": "e1", "source": vorher, "target": "warten"})
        vorher = "warten"
    if aktion is not None:
        knoten.append({"id": "tun", "type": "auto_action", "position": {"x": 0, "y": 2},
                       "data": {"config": aktion}})
        kanten.append({"id": "e2", "source": vorher, "target": "tun"})
        vorher = "tun"
    knoten.append({"id": "ende", "type": "end", "position": {"x": 0, "y": 3},
                   "data": {"config": {"outcome": "completed"}}})
    kanten.append({"id": "e3", "source": vorher, "target": "ende"})
    return {"nodes": knoten, "edges": kanten}


async def _lauf(db, graph: dict, name: str):
    user = await make_user(db, name)
    d = WorkflowDefinition(project_id=None, key=name, name=name, created_by=user.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=user.id)


async def test_timer_haelt_den_lauf_an(db):
    inst = await _lauf(db, _graph(timer={"dauer": 30, "einheit": "m"}), "wartet")
    assert inst.status == WorkflowInstanceStatus.waiting
    token = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == inst.id))).scalars().one()
    assert token.state == WorkflowTokenState.waiting and token.waiting_for == "timer"
    # Die Fälligkeit steht am Schritt — nicht in einem Task, der einen Neustart nicht
    # überlebt hätte.
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id))).scalars().one()
    faellig = dt.datetime.fromisoformat(step.result["faellig"])
    assert dt.timedelta(minutes=29) < faellig - dt.datetime.now(dt.timezone.utc) \
        <= dt.timedelta(minutes=30)


async def test_faelliger_timer_weckt_und_laeuft_weiter(db):
    inst = await _lauf(db, _graph(timer={"dauer": 30, "einheit": "m"}), "geweckt")
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.instance_id == inst.id))).scalars().one()

    # Nicht fällig → nichts passiert. Das ist die halbe Miete: der Wecker darf nicht
    # jeden wartenden Lauf sofort einsammeln.
    assert await faellige_timer() == 0

    step.result = {"faellig": (dt.datetime.now(dt.timezone.utc)
                               - dt.timedelta(seconds=1)).isoformat()}
    await db.commit()
    assert await faellige_timer() == 1

    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed


async def test_zeitpunkt_in_der_vergangenheit_wartet_nicht(db):
    """Ein Zeitpunkt, der schon vorbei ist, heißt „jetzt" — nicht „nie"."""
    gestern = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    inst = await _lauf(db, _graph(timer={"bis": gestern}), "vergangen")
    assert await faellige_timer() == 1
    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.completed


async def test_wiederholung_haelt_abstand_und_gibt_dann_auf(db, monkeypatch):
    """Ein Fehlschlag nach außen ist meist einer des Augenblicks. Also: warten, erneut
    versuchen — aber nicht endlos."""
    versuche = {"n": 0}

    async def kaputt(db_, inst_, node_):
        versuche["n"] += 1
        raise ValueError("Gegenstelle weg")

    monkeypatch.setattr(workflow_actions, "run_action", kaputt)
    inst = await _lauf(db, _graph(aktion={
        "action": {"action": "notify", "params": {}}, "wiederholungen": 2, "warte_sek": 1,
    }), "wiederholt")

    assert versuche["n"] == 1
    assert inst.status == WorkflowInstanceStatus.waiting     # wartet auf den zweiten Versuch
    assert inst.context["_versuche"]["tun"] == 1

    async def faellig_stellen():
        for st in (await db.execute(select(WorkflowStepRun).where(
                WorkflowStepRun.instance_id == inst.id,
                WorkflowStepRun.status == "waiting"))).scalars().all():
            st.result = {"faellig": (dt.datetime.now(dt.timezone.utc)
                                     - dt.timedelta(seconds=1)).isoformat()}
        await db.commit()

    await faellig_stellen()
    await faellige_timer()
    assert versuche["n"] == 2
    await faellig_stellen()
    await faellige_timer()
    assert versuche["n"] == 3          # der dritte ist der letzte (zwei Wiederholungen)

    await db.refresh(inst)
    assert inst.status == WorkflowInstanceStatus.failed
    # Der Zähler ist weg — sonst zählte der nächste Anlauf beim alten Stand weiter.
    assert inst.context.get("_versuche", {}).get("tun") is None


async def test_fehlerzweig_faengt_den_fehlschlag_auf(db, monkeypatch):
    """Wer einen `error`-Ausgang verdrahtet, will den Fehler behandeln statt den Lauf
    zu verlieren."""
    async def kaputt(db_, inst_, node_):
        raise ValueError("kaputt")

    monkeypatch.setattr(workflow_actions, "run_action", kaputt)
    graph = _graph(aktion={"action": {"action": "notify", "params": {}}})
    graph["nodes"].append({"id": "aufgefangen", "type": "end", "position": {"x": 1, "y": 3},
                           "data": {"config": {"outcome": "completed"}}})
    graph["edges"].append({"id": "e9", "source": "tun", "target": "aufgefangen",
                           "sourceHandle": "error"})

    inst = await _lauf(db, graph, "fehlerzweig")
    assert inst.status == WorkflowInstanceStatus.completed


async def test_validierung_verlangt_dauer_oder_zeitpunkt():
    assert validate_graph("standalone", _graph(timer={"dauer": 5, "einheit": "m"})) == []
    fehler = validate_graph("standalone", _graph(timer={}))
    assert any("weder Dauer noch Zeitpunkt" in f for f in fehler)


async def test_lange_wartezeit_wird_gedeckelt():
    """Ein Ablauf, der zwei Jahre schläft, ist fast immer ein Vertipper."""
    faellig = workflow_engine._faellig_ab({"dauer": 900, "einheit": "t"}, {})
    assert faellig - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(days=90)
