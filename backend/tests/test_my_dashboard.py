"""The start page reads from `/me/dashboard`: what runs, what fell over, what it cost.

The three blocks are new next to the ticket lists, and each of them has a way of quietly
turning up empty: a run without a ticket (assistant, job) falls out of a ticket join, a job
error older than a day does not belong in it, and costs hang off the run owner, not off a
project.
"""
import datetime as dt

import pytest

from app.models.agents import CostEntry, Run
from app.models.ops import Job, JobRun
from tests.conftest import auth, make_project, make_user


def _ago(hours: float) -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=hours)


@pytest.mark.asyncio
async def test_dashboard_shows_running_errors_and_costs(client, db):
    me = await make_user(db, "dash")
    other = await make_user(db, "other")
    project = await make_project(db, "DSH", "Dashboard")

    # A run without a ticket: the assistant works project-less and has to appear all the same.
    mine = Run(owner_id=me.id, project_id=project.id, agent="assistant", phase="execution",
               status="running", task_id="t-1", started_at=_ago(0.5))
    foreign = Run(owner_id=other.id, agent="coder", status="running", started_at=_ago(0.5))
    finished = Run(owner_id=me.id, agent="coder", status="success", started_at=_ago(2))
    db.add_all([mine, foreign, finished])
    await db.commit()
    await db.refresh(mine)

    db.add_all([
        CostEntry(run_id=mine.id, input_tokens=1000, output_tokens=200, cost_usd=0.5,
                  created_at=_ago(2)),
        # Older than a day: counts for the week, not for the day.
        CostEntry(run_id=mine.id, input_tokens=10, output_tokens=5, cost_usd=0.25,
                  created_at=_ago(48)),
    ])

    job = Job(user_id=me.id, name="nightly", kind="workflow")
    db.add(job)
    await db.commit()
    await db.refresh(job)
    db.add_all([
        JobRun(job_id=job.id, status="error", error="boom\nstack…", started_at=_ago(3)),
        JobRun(job_id=job.id, status="error", error="old", started_at=_ago(40)),
        JobRun(job_id=job.id, status="ok", started_at=_ago(1)),
    ])
    await db.commit()

    r = await client.get("/me/dashboard", headers=auth(me))
    assert r.status_code == 200
    data = r.json()

    assert [x["run_id"] for x in data["running"]] == [mine.id]
    assert data["running"][0]["project_key"] == "DSH"
    # The way into the office: a run without a ticket addresses through its own root.
    assert data["running"][0]["sid"] == f"run:{mine.id}"
    assert data["stats"]["job_errors"] == 1
    assert [e["error"] for e in data["job_errors"]] == ["boom"]
    assert data["costs"]["day"] == {"usd": 0.5, "tokens": 1200}
    assert data["costs"]["week"] == {"usd": 0.75, "tokens": 1215}
    # The brakes: the stub answers every flag with False and knows no runner heartbeat.
    assert data["state"] == {"runner": False, "paused": "", "shift_end": False}
