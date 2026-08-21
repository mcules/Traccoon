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
from app.services.notify import deliver
from app.services.workflow_actions import run_action
from sqlalchemy import select

from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _lines(db) -> list[Notification]:
    return list((await db.execute(select(Notification).order_by(Notification.id)))
                .scalars().all())


async def test_a_second_notice_within_the_window_stays_away(db):
    anna = await make_user(db, "anna")
    first = await deliver(db, user=anna, kind="test", title="Alarm",
                            throttle_key="shelter.diebstahl", throttle_minutes=15)
    second = await deliver(db, user=anna, kind="test", title="Alarm",
                             throttle_key="shelter.diebstahl", throttle_minutes=15)
    await db.commit()
    assert first["channel"] != "throttled"
    assert second["suppressed"] is True and second["open_again_at"]
    assert len(await _lines(db)) == 1, "no bell row either, otherwise the noise only moves elsewhere"


async def test_after_the_window_another_one_goes_out(db):
    anna = await make_user(db, "anna")
    await deliver(db, user=anna, kind="test", title="Alarm",
                    throttle_key="k", throttle_minutes=15)
    await db.commit()
    (old,) = await _lines(db)
    old.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=16)
    await db.commit()

    path = await deliver(db, user=anna, kind="test", title="Alarm",
                          throttle_key="k", throttle_minutes=15)
    await db.commit()
    assert path.get("suppressed") is not True
    assert len(await _lines(db)) == 2


async def test_different_keys_do_not_disturb_each_other(db):
    anna = await make_user(db, "anna")
    await deliver(db, user=anna, kind="test", title="A", throttle_key="a", throttle_minutes=60)
    await deliver(db, user=anna, kind="test", title="B", throttle_key="b", throttle_minutes=60)
    await db.commit()
    assert len(await _lines(db)) == 2


async def test_two_people_do_not_mute_each_other(db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await deliver(db, user=anna, kind="test", title="A", throttle_key="gleich",
                    throttle_minutes=60)
    path = await deliver(db, user=bert, kind="test", title="A", throttle_key="gleich",
                          throttle_minutes=60)
    await db.commit()
    assert path.get("suppressed") is not True
    assert len(await _lines(db)) == 2


async def test_without_throttling_everything_stays_as_before(db):
    """Regression protection: every existing notification goes through unchanged."""
    anna = await make_user(db, "anna")
    for _ in range(3):
        await deliver(db, user=anna, kind="test", title="Immer wieder")
    await db.commit()
    assert len(await _lines(db)) == 3


async def _instance(db, anna) -> WorkflowInstance:
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


def _node(params: dict) -> dict:
    return {"id": "melden", "type": "auto_action",
            "data": {"config": {"action": {"action": "notify", "params": params}}}}


async def test_a_node_throttles_itself(db):
    """A number should be enough; nobody would think up the key otherwise."""
    anna = await make_user(db, "anna")
    inst = await _instance(db, anna)
    p = {"to": {"mode": "user", "user_id": anna.id}, "title": "Alarm", "throttle_minutes": 15}
    first = await run_action(db, inst, _node(p))
    second = await run_action(db, inst, _node(p))
    await db.commit()
    assert first.get("suppressed") is not True
    assert second["suppressed"] is True
    assert len(await _lines(db)) == 1


async def test_a_key_from_the_context_separates_the_cases(db):
    """Two kinds of alarm on the same node must not swallow each other."""
    anna = await make_user(db, "anna")
    inst = await _instance(db, anna)

    def p(kind):
        return {"to": {"mode": "user", "user_id": anna.id}, "title": kind,
                "throttle_key": "{{ geraet }}." + kind, "throttle_minutes": 60}

    await run_action(db, inst, _node(p("vibration")))
    second_kind = await run_action(db, inst, _node(p("lowBattery")))
    repeat = await run_action(db, inst, _node(p("vibration")))
    await db.commit()
    assert second_kind.get("suppressed") is not True
    assert repeat["suppressed"] is True
    lines = await _lines(db)
    assert len(lines) == 2
    assert {z.throttle_key for z in lines} == {"shelter.vibration", "shelter.lowBattery"}
