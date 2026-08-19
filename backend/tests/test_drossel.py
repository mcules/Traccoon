"""Throttle: the same message at most every N minutes.

The occasion is a device that does not summarise itself: as long as its alarm bit is set it
reports again with every position, every second. Ten minutes of shaking would give around
120 identical messages. Idempotency over an event id does not help there, because every one
of these reports is an event of its own.

What matters is the dividing line: what is throttled is the **message**, not the processing.
The flow runs on and measurements keep being written; only the messenger stays silent.
"""
import datetime as dt

import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.notification import Notification
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.notify import zustellen
from app.services.workflow_actions import run_action
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _zeilen(db) -> list[Notification]:
    return list((await db.execute(select(Notification).order_by(Notification.id)))
                .scalars().all())


async def test_zweite_meldung_im_fenster_bleibt_aus(db):
    anna = await make_user(db, "anna")
    erste = await zustellen(db, user=anna, kind="test", title="Alarm",
                            drossel_key="shelter.diebstahl", drossel_minuten=15)
    zweite = await zustellen(db, user=anna, kind="test", title="Alarm",
                             drossel_key="shelter.diebstahl", drossel_minuten=15)
    await db.commit()
    assert erste["kanal"] != "gedrosselt"
    assert zweite["unterdrueckt"] is True and zweite["wieder_ab"]
    assert len(await _zeilen(db)) == 1, "auch keine Glocken-Zeile — sonst nur Lärm woanders"


async def test_nach_dem_fenster_geht_wieder_eine_raus(db):
    anna = await make_user(db, "anna")
    await zustellen(db, user=anna, kind="test", title="Alarm",
                    drossel_key="k", drossel_minuten=15)
    await db.commit()
    (alt,) = await _zeilen(db)
    alt.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=16)
    await db.commit()

    weg = await zustellen(db, user=anna, kind="test", title="Alarm",
                          drossel_key="k", drossel_minuten=15)
    await db.commit()
    assert weg.get("unterdrueckt") is not True
    assert len(await _zeilen(db)) == 2


async def test_verschiedene_schluessel_stoeren_sich_nicht(db):
    anna = await make_user(db, "anna")
    await zustellen(db, user=anna, kind="test", title="A", drossel_key="a", drossel_minuten=60)
    await zustellen(db, user=anna, kind="test", title="B", drossel_key="b", drossel_minuten=60)
    await db.commit()
    assert len(await _zeilen(db)) == 2


async def test_zwei_menschen_schalten_sich_nicht_gegenseitig_stumm(db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await zustellen(db, user=anna, kind="test", title="A", drossel_key="gleich",
                    drossel_minuten=60)
    weg = await zustellen(db, user=bert, kind="test", title="A", drossel_key="gleich",
                          drossel_minuten=60)
    await db.commit()
    assert weg.get("unterdrueckt") is not True
    assert len(await _zeilen(db)) == 2


async def test_ohne_drossel_bleibt_alles_wie_bisher(db):
    """Regression protection: every existing notification goes through unchanged."""
    anna = await make_user(db, "anna")
    for _ in range(3):
        await zustellen(db, user=anna, kind="test", title="Immer wieder")
    await db.commit()
    assert len(await _zeilen(db)) == 3


async def _instanz(db, anna) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="dros", name="Dros", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context={"geraet": "shelter"}, started_by=anna.id)
    db.add(inst)
    await db.flush()
    return inst


def _knoten(params: dict) -> dict:
    return {"id": "melden", "type": "auto_action",
            "data": {"config": {"action": {"action": "notify", "params": params}}}}


async def test_knoten_drosselt_sich_selbst(db):
    """A number should be enough; nobody would think up the key otherwise."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    p = {"to": {"mode": "user", "user_id": anna.id}, "title": "Alarm", "drossel_minuten": 15}
    erste = await run_action(db, inst, _knoten(p))
    zweite = await run_action(db, inst, _knoten(p))
    await db.commit()
    assert erste.get("unterdrueckt") is not True
    assert zweite["unterdrueckt"] is True
    assert len(await _zeilen(db)) == 1


async def test_schluessel_aus_dem_kontext_trennt_die_faelle(db):
    """Two kinds of alarm on the same node must not swallow each other."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)

    def p(art):
        return {"to": {"mode": "user", "user_id": anna.id}, "title": art,
                "drossel_key": "{{ geraet }}." + art, "drossel_minuten": 60}

    await run_action(db, inst, _knoten(p("vibration")))
    zweite_art = await run_action(db, inst, _knoten(p("lowBattery")))
    wiederholung = await run_action(db, inst, _knoten(p("vibration")))
    await db.commit()
    assert zweite_art.get("unterdrueckt") is not True
    assert wiederholung["unterdrueckt"] is True
    zeilen = await _zeilen(db)
    assert len(zeilen) == 2
    assert {z.drossel_key for z in zeilen} == {"shelter.vibration", "shelter.lowBattery"}
