"""Deployments in the office event stream: watcher, anchor, borrowed `seq`.

Three things are nailed down here because they would otherwise break silently:

1. **Idempotency** lies in a column (`announced_status`), not in the process. Looking twice
   must not tell twice, not even after a restart.
2. **Maintenance updates get no stage event.** That looks like a gap and is a decision: a
   self-deploy recreates the container that supplies the stage. Without this test somebody
   would "repair" that eventually.
3. **Slot 3 has two applicants.** The synthesised `run_end` boundary wins, and the existing
   deployment moves to the preceding row. If they collided, the recorder would lose one of
   the two: it deduplicates exclusively over `seq`.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.models.agents import Run, RunStep
from app.models.enums import StatusCategory
from app.models.ops import Deployment
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import deploy_watch as dw
from app.services.office import (
    RunCtx, SEQ_SLOTS, deploy_anchor_step_id, deployment_events, publish_step,
)
from conftest import auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def no_redis(monkeypatch):
    """No real Redis in the test, and what was sent is checkable."""
    import app.core.redis as redismod
    sent: list[tuple[str, str]] = []

    class _R:
        async def publish(self, channel, data):
            sent.append((channel, data))

    monkeypatch.setattr(redismod, "get_redis", lambda: _R())
    return sent


# ── Testdaten ────────────────────────────────────────────────────────────────

async def ticket(db, project, number: int = 1) -> Issue:
    kind = IssueType(project_id=project.id, name="Aufgabe")
    status = WorkflowStatus(project_id=project.id, name="To Do", category=StatusCategory.todo)
    db.add_all([kind, status, IssueCounter(project_id=project.id, last_number=0)])
    await db.commit()
    i = Issue(project_id=project.id, number=number, key=f"{project.key}-{number}",
              type_id=kind.id, status_id=status.id, summary="Tu was", reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


async def make_run(db, *, issue=None, project=None, status="success", agent="developer") -> Run:
    r = Run(issue_id=issue.id if issue else None,
            project_id=project.id if project else (issue.project_id if issue else None),
            agent=agent, phase="execute", provider="claude_code", model="sonnet",
            status=status, started_at=NOW - dt.timedelta(minutes=5),
            finished_at=None if status == "running" else NOW - dt.timedelta(minutes=1))
    db.add(r)
    await db.commit()
    return r


async def steps(db, run: Run, count: int, *, from_seconds: int = 300) -> list[RunStep]:
    """Legacy rows (`kind=''`) with an ascending timestamp: one event per row."""
    rows = [RunStep(run_id=run.id, seq=i + 1, role="assistant", content=f"Schritt {i + 1}",
                    created_at=NOW - dt.timedelta(seconds=from_seconds - i * 10))
            for i in range(count)]
    db.add_all(rows)
    await db.commit()
    return rows


async def deployment(db, **kw) -> Deployment:
    dep = Deployment(**{"stack_dir": "/opt/docker/stacks/traccoon", "status": "building",
                        "source": "agent", **kw})
    db.add(dep)
    await db.commit()
    return dep


async def deploy_steps(db, run_id: int) -> list[RunStep]:
    rows = (await db.execute(
        select(RunStep).where(RunStep.run_id == run_id, RunStep.kind == "deploy")
        .order_by(RunStep.id))).scalars().all()
    return list(rows)


# ── Idempotenz ───────────────────────────────────────────────────────────────

async def test_watching_twice_tells_it_once(db):
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue, status="running")
    dep = await deployment(db, issue_id=issue.id, project_id=project.id, status="building")

    assert await dw.tick(db) == 1
    assert await dw.tick(db) == 0
    await db.refresh(dep)
    assert dep.announced_status == "building"
    assert [s.content for s in await deploy_steps(db, run.id)] == [
        '{"deployment_id": %d, "state": "start", "log_head": ""}' % dep.id]

    # The outcome is a new state, and it is told only once as well.
    dep.status = "ok"
    dep.log = "fertig gebaut"
    await db.commit()
    assert await dw.tick(db) == 1
    assert await dw.tick(db) == 0
    steps_ = await deploy_steps(db, run.id)
    assert [s.ok for s in steps_] == [None, True]
    assert "fertig gebaut" in steps_[-1].content


@pytest.mark.parametrize("announced, status, expected", [
    ("", "building", ["start"]),
    ("building", "ok", ["ok"]),
    ("building", "failed", ["fail"]),
    ("building", "rolledback", ["back"]),
    # Run through completely between two beats: the opening is caught up, because otherwise
    # the rack would light up without anybody ever having walked over.
    ("", "ok", ["start", "ok"]),
    ("pending", "failed", ["start", "fail"]),
    # Nothing to show: a queue is not a process, and `cancelled` is written by no code path
    # (a hand written clean-up, see `models/ops.Deployment`).
    ("", "pending", []),
    ("pending", "cancelled", []),
])
def test_states_for(announced, status, expected):
    assert dw.states_for(announced, status) == expected


# ── Ankerwahl ────────────────────────────────────────────────────────────────

async def test_the_anchor_for_an_agent_tool_is_the_waiting_run(db):
    """`worktree <> ''` means: an agent called `deploy` and is waiting inline. The row
    belongs to ITS run, not to the most recent one, which may long be a review."""
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    waiting = await make_run(db, issue=issue, status="running")
    younger = await make_run(db, issue=issue, status="success", agent="reviewer")

    await deployment(db, issue_id=issue.id, project_id=project.id,
                     worktree="/workspace/tra-1", status="building")
    await dw.tick(db)
    assert len(await deploy_steps(db, waiting.id)) == 1
    assert await deploy_steps(db, younger.id) == []


async def test_the_anchor_for_a_merge_is_the_newest_run(db):
    """Without a worktree no agent waited (merge, workflow), and then the most recent run of
    the ticket tells it, because it is the one the room is showing right now."""
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    older = await make_run(db, issue=issue, status="running")
    younger = await make_run(db, issue=issue, status="success", agent="reviewer")

    await deployment(db, issue_id=issue.id, project_id=project.id, worktree="",
                     source="merge", status="building")
    await dw.tick(db)
    assert len(await deploy_steps(db, younger.id)) == 1
    assert await deploy_steps(db, older.id) == []


async def test_a_maintenance_update_raises_no_event(db, no_redis):
    """**A decision, not an oversight.** A self-deploy recreates the backend container that
    supplies the stage: the WebSocket falls in the middle of the animation, and the process
    that would draw it dies of it. Animating a process that kills the animator is a category
    error; these rows live in the list, not in the room."""
    project = await make_project(db, "TRA", "Traccoon")
    run = await make_run(db, project=project)           # there WOULD be a run to hang it off
    dep = await deployment(db, project_id=project.id, self_deploy=True, stack_dir="",
                           source="maintenance", status="building")

    assert await dw.tick(db) == 0
    assert await deploy_steps(db, run.id) == []
    assert no_redis == []
    # Acknowledging happens regardless; otherwise the row would lie on the table every beat.
    await db.refresh(dep)
    assert dep.announced_status == "building"


async def test_no_event_without_a_run(db):
    """A ticket without a single run has no anchor. Better a gap than a row in a run that has
    nothing to do with the deploy."""
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    await deployment(db, issue_id=issue.id, project_id=project.id, status="ok")
    assert await dw.tick(db) == 0


async def test_existing_stock_stays_silent(db):
    """The 186 existing rows have `announced_status=''` and would otherwise all be "new": the
    first beat would tell three months of history as if it had just happened."""
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue)
    await deployment(db, issue_id=issue.id, project_id=project.id, status="ok",
                     created_at=NOW - dt.timedelta(days=12))
    assert await dw.tick(db) == 0
    assert await deploy_steps(db, run.id) == []


async def test_a_started_story_is_told_to_its_end(db):
    """The opening was told, the outcome only after a long backend outage: the window must
    not drop the row now, because otherwise the rack would stay building forever."""
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=project.id, status="ok",
                     announced_status="building",
                     created_at=NOW - dt.timedelta(days=2))
    assert await dw.tick(db) == 1
    assert [s.ok for s in await deploy_steps(db, run.id)] == [True]


# ── Freier Anschluss: deployment.finished ────────────────────────────────────

async def test_deployment_finished_fires_once(db, monkeypatch):
    """The trigger name has been in `BUILTIN_EVENTS` all along and has never fired."""
    import app.services.events as eventsmod
    seen: list[tuple[str, dict]] = []

    async def fake_emit(_db, event, **kw):
        seen.append((event, kw))
        return []

    monkeypatch.setattr(eventsmod, "emit", fake_emit)

    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    await make_run(db, issue=issue, status="running")
    dep = await deployment(db, issue_id=issue.id, project_id=project.id, status="building")

    await dw.tick(db)
    assert seen == []                       # `building` is no conclusion

    dep.status = "ok"
    await db.commit()
    await dw.tick(db)
    assert [e for e, _ in seen] == ["deployment.finished"]
    kw = seen[0][1]
    assert kw["issue_id"] == issue.id and kw["source_ref"] == f"deployment:{dep.id}"
    assert kw["payload"]["deployment"]["ok"] is True

    await dw.tick(db)
    assert len(seen) == 1                   # acknowledged is acknowledged


async def test_deployment_finished_even_without_a_stage(db, monkeypatch):
    """The maintenance update gets no stage event, but the process trigger hangs off the
    conclusion, not off the stage."""
    import app.services.events as eventsmod
    seen: list[str] = []

    async def fake_emit(_db, event, **kw):
        seen.append(event)
        return []

    monkeypatch.setattr(eventsmod, "emit", fake_emit)

    project = await make_project(db, "TRA", "Traccoon")
    await deployment(db, project_id=project.id, self_deploy=True, stack_dir="",
                     source="maintenance", status="ok")
    await dw.tick(db)
    assert seen == ["deployment.finished"]


# ── Geliehene `seq` (Bestand) ────────────────────────────────────────────────

class _Line:
    """Minimal `run_steps` dummy: `deploy_anchor_step_id` reads only two fields."""

    def __init__(self, step_id: int, seconds: int):
        self.id = step_id
        self.created_at = NOW - dt.timedelta(seconds=seconds)


class _Dep:
    def __init__(self, **kw):
        self.__dict__.update({"id": 42, "status": "ok", "stack_dir": "/stacks/tra",
                              "worktree": "", "log": "alles gut",
                              "created_at": NOW - dt.timedelta(seconds=25),
                              "started_at": None, "finished_at": None, **kw})


def ctx() -> RunCtx:
    return RunCtx(run_id=8871, project_id=27, owner_id=3, sid="issue:412", agent="dev")


def test_existing_stock_hangs_on_the_last_line_before_it():
    lines = [_Line(100, 60), _Line(101, 40), _Line(102, 10)]
    anchor = deploy_anchor_step_id(lines, _Dep().created_at)
    assert anchor == 101
    ev = deployment_events(_Dep(), ctx(), anchor_step_id=anchor)[0]
    assert ev["kind"] == "deploy" and ev["seq"] == 101 * SEQ_SLOTS + 3
    assert ev["deployment_id"] == 42 and ev["state"] == "ok"
    assert ev["target"] == "/stacks/tra" and ev["log_head"] == "alles gut"


def test_a_slot3_collision_moves_to_the_preceding_line():
    """The `run_end` boundary sits on `last*4+3`, exactly the place the deployment wanted to
    borrow. It has precedence (it ends a run, while the deployment illustrates one), so the
    deployment slips back one row."""
    lines = [_Line(100, 60), _Line(101, 40), _Line(102, 10)]
    anchor = deploy_anchor_step_id(lines, _Dep().created_at, blocked={101})
    assert anchor == 100
    ev = deployment_events(_Dep(), ctx(), anchor_step_id=anchor)[0]
    assert ev["seq"] == 100 * SEQ_SLOTS + 3
    # And when the preceding row is taken as well, it goes further back.
    assert deploy_anchor_step_id(lines, _Dep().created_at, blocked={100, 101}) is None


def test_stock_without_a_line_before_it_gets_nothing():
    """A deploy lying before the first loaded row has no honest place: hung in at the front
    it would stand before its own trigger."""
    lines = [_Line(100, 5)]
    assert deploy_anchor_step_id(lines, _Dep().created_at) is None
    assert deployment_events(_Dep(), ctx(), anchor_step_id=None) == []


@pytest.mark.parametrize("status", ["pending", "pending-check", "cancelled", ""])
def test_stock_without_a_showable_status_stays_silent(status):
    assert deployment_events(_Dep(status=status), ctx(), anchor_step_id=100) == []


# ── Lesepfad ─────────────────────────────────────────────────────────────────

async def test_the_api_shows_a_stock_deployment_in_its_place(client, db):
    user = await make_user(db, "anna", admin=True)
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue)
    lines = await steps(db, run, 3)
    dep = await deployment(db, issue_id=issue.id, project_id=project.id, status="failed",
                           log="❌ Wächter: Tests rot",
                           created_at=lines[1].created_at + dt.timedelta(seconds=2))

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(user))
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    deploys = [e for e in events if e["kind"] == "deploy"]
    assert len(deploys) == 1
    assert deploys[0]["deployment_id"] == dep.id and deploys[0]["state"] == "fail"
    # Between the second and the third row, and the order stays monotonic.
    assert deploys[0]["seq"] == lines[1].id * SEQ_SLOTS + 3
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    # The synthesised `run_end` boundary still stands behind everything.
    end = [e for e in events if e["kind"] == "run_end"][0]
    assert end["seq"] > deploys[0]["seq"]


async def test_the_api_does_not_tell_it_twice(client, db):
    """What the watcher wrote as a real row is not borrowed a second time."""
    user = await make_user(db, "anna", admin=True)
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue, status="running")
    await steps(db, run, 2)
    dep = await deployment(db, issue_id=issue.id, project_id=project.id, status="ok")
    await dw.tick(db)   # writes start plus ok as real rows

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(user))
    deploys = [e for e in r.json()["events"] if e["kind"] == "deploy"]
    assert [e["state"] for e in deploys] == ["start", "ok"]
    assert {e["deployment_id"] for e in deploys} == {dep.id}


# ── The live path ────────────────────────────────────────────────────────────

async def test_the_watcher_sends_into_the_channel(db, no_redis):
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    await make_run(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=project.id, status="building")
    await dw.tick(db)
    assert len(no_redis) == 1 and '"kind": "deploy"' in no_redis[0][1]


async def test_the_publish_step_swallows_a_redis_outage(db, monkeypatch):
    """The view is a spectator, not a participant: a dead Redis must not turn a deploy into
    an error."""
    import app.core.redis as redismod

    class _Dead:
        async def publish(self, *a, **k):
            raise RuntimeError("Redis weg")

    monkeypatch.setattr(redismod, "get_redis", lambda: _Dead())
    project = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, project)
    run = await make_run(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=project.id, status="building")

    assert await dw.tick(db) == 1               # no error upwards
    assert len(await deploy_steps(db, run.id)) == 1   # and the row stands

    step = (await deploy_steps(db, run.id))[0]
    await publish_step(RunCtx(run_id=run.id), step)      # called directly as well: silent
