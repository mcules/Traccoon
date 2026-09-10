"""Retention: what goes, what stays, and what nobody is allowed to delete.

The rule this replaces looked like it worked and did almost nothing: it deleted only
ARCHIVED agent runs, and a run is archived only when its ticket is archived — so every
run from a job or a flow, which is the vast majority, was never touched. The tests below
therefore care less about "it deletes" than about the two edges: a run without a ticket
must go, and a run that is still waiting for somebody must not.
"""
import datetime as dt

from sqlalchemy import select

from app.models.agents import Run
from app.models.notification import Notification
from app.services import retention
from app.services.appsettings import set_setting
from conftest import auth, make_user


def _ago(days: int) -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)


async def _run(db, *, status: str, days: int, agent: str = "tester") -> Run:
    r = Run(task_id=f"t-{status}-{days}", agent=agent, phase="execute", provider="claude_code",
            model="m", status=status, started_at=_ago(days))
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def test_finished_runs_without_a_ticket_go(db):
    """The case the old rule missed entirely: no ticket, therefore never archived."""
    old = await _run(db, status="success", days=60)
    fresh = await _run(db, status="success", days=1)
    assert old.issue_id is None

    rule = retention.rule_by_key("run_retention_days")
    removed = await retention.sweep_rule(db, rule)

    assert removed == 1
    left = {r.id for r in (await db.execute(select(Run))).scalars()}
    assert left == {fresh.id}


async def test_blocked_runs_stay_however_old(db):
    """A blocked run waits for an answer. Deleting it takes the question away from the person."""
    blocked = await _run(db, status="blocked", days=400)
    await _run(db, status="failed", days=400)

    rule = retention.rule_by_key("run_retention_days")
    removed = await retention.sweep_rule(db, rule)

    assert removed == 1
    rows = (await db.execute(select(Run))).scalars().all()
    assert [r.id for r in rows] == [blocked.id]


async def test_zero_days_never_deletes(db):
    """0 is the off switch, not "delete everything at once"."""
    await _run(db, status="success", days=999)
    await set_setting(db, "run_retention_days", "0")

    rule = retention.rule_by_key("run_retention_days")
    assert await retention.sweep_rule(db, rule) == 0
    assert await retention.pending(db, rule, 0) == 0


async def test_unread_notifications_survive(db):
    """An unread notification is the last trace of something nobody has looked at yet."""
    seen = Notification(kind="done", title="seen", created_at=_ago(365), read_at=_ago(365))
    unseen = Notification(kind="done", title="unseen", created_at=_ago(365))
    db.add_all([seen, unseen])
    await db.commit()

    rule = retention.rule_by_key("notification_retention_days")
    removed = await retention.sweep_rule(db, rule)

    assert removed == 1
    rows = (await db.execute(select(Notification))).scalars().all()
    assert [n.title for n in rows] == ["unseen"]


async def test_chunking_works_off_more_than_one_chunk(db, monkeypatch):
    """The first sweep after an upgrade faces months at once and must not do it in one DELETE."""
    monkeypatch.setattr(retention, "CHUNK", 3)
    for i in range(7):
        db.add(Run(task_id=f"bulk-{i}", agent="a", phase="execute", provider="p", model="m",
                   status="success", started_at=_ago(90)))
    await db.commit()

    removed = await retention.sweep_rule(db, retention.rule_by_key("run_retention_days"))
    assert removed == 7


async def test_overview_counts_rows_and_due(db):
    await _run(db, status="success", days=90)
    await _run(db, status="success", days=1)

    rules = {r["key"]: r for r in await retention.overview(db)}
    runs = rules["run_retention_days"]
    assert runs["rows"] == 2
    assert runs["pending"] == 1
    # Every source is named, otherwise one of them would silently have no period.
    assert set(rules) == {
        "run_retention_days", "workflow_retention_days", "job_run_retention_days",
        "notification_retention_days", "inbound_retention_days",
    }


async def test_admin_can_read_and_set_a_period(client, db):
    admin = await make_user(db, "chief", admin=True)

    r = await client.get("/admin/retention", headers=auth(admin))
    assert r.status_code == 200
    assert any(x["key"] == "inbound_retention_days" for x in r.json()["rules"])

    r = await client.put("/admin/retention",
                         json={"key": "inbound_retention_days", "days": 3}, headers=auth(admin))
    assert r.status_code == 200
    rule = retention.rule_by_key("inbound_retention_days")
    assert await retention.days_for(db, rule) == 3


async def test_an_invented_rule_is_refused(client, db):
    """Otherwise any setting key could be written over this route."""
    admin = await make_user(db, "chief2", admin=True)
    r = await client.put("/admin/retention",
                         json={"key": "jwt_secret", "days": 0}, headers=auth(admin))
    assert r.status_code == 400


async def test_a_normal_user_gets_nothing(client, db):
    user = await make_user(db, "someone")
    assert (await client.get("/admin/retention", headers=auth(user))).status_code == 403
    assert (await client.get("/admin/logs", headers=auth(user))).status_code == 403
