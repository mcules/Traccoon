"""Claude CLI sessions: release into the session, delivery order, the report back.

Docker and Redis are replaced: `_deployer` records what would be typed into the container,
and a small in-memory Redis holds the alive set and the results the engine waits on.
"""
import json

import pytest
from sqlalchemy import select

import app.services.cli_sessions as cli
from app.models.cli import CliDelivery, CliSession
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.models.workflow import WorkflowStepRun
from conftest import add_member, make_project, make_user


class FakeRedis:
    def __init__(self):
        self.sets: dict[str, set] = {}
        self.values: dict[str, str] = {}

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key, *members):
        self.sets.setdefault(key, set()).difference_update(members)

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def publish(self, channel, message):
        return 0


@pytest.fixture
def fake(monkeypatch):
    redis = FakeRedis()
    calls: list[tuple[str, dict]] = []

    async def deployer(path, body, timeout=120):
        calls.append((path, body))
        if path == "/cli/status":
            return {n: "running" for n in body.get("names", [])}
        return {"ok": True, "log": ""}

    monkeypatch.setattr(cli, "get_redis", lambda: redis)
    monkeypatch.setattr(cli, "_deployer", deployer)
    monkeypatch.setattr(cli, "kick", lambda session_id: None)
    return redis, calls


async def _cli_project(db, n_tickets=1):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "CLI", "Cli")
    proj.cli_mode = True
    m = await add_member(db, proj, owner, ProjectRole.owner)
    m.ai_assign = True
    t = IssueType(project_id=proj.id, name="Aufgabe")
    s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=n_tickets)])
    await db.commit()
    issues = []
    for n in range(1, n_tickets + 1):
        issue = Issue(project_id=proj.id, number=n, key=f"CLI-{n}", type_id=t.id,
                      status_id=s.id, summary=f"Ticket {n}", description="Do it.",
                      reporter_id=owner.id, rank=f"{n:04d}", assigned_agent="cli_session",
                      assigned_by_user_id=owner.id)
        db.add(issue)
        issues.append(issue)
    await db.commit()
    return owner, proj, issues


def _sent(calls):
    return [b for p, b in calls if p == "/cli/send"]


async def test_a_release_goes_into_the_session_without_planning(db, seeded, redis_stub, fake):
    owner, proj, (issue,) = await _cli_project(db)
    redis, _ = fake
    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await db.commit()

    steps = (await db.execute(select(WorkflowStepRun.node_id))).scalars().all()
    assert "plan" not in steps and "approve_plan" not in steps
    d = (await db.execute(select(CliDelivery))).scalar_one()
    sess = await db.get(CliSession, d.session_id)
    assert (sess.user_id, d.issue_id, d.state) == (owner.id, issue.id, "waiting")
    assert d.task_id in redis.sets[cli.CLI_TASKS]


async def test_queue_waits_and_now_goes_straight_in(db, fake):
    owner, proj, (a, b, c) = await _cli_project(db, 3)
    _, calls = fake
    c.cli_delivery = "now"
    c.cli_context = "clear"
    for n, issue in enumerate((a, b, c)):
        await cli.enqueue(db, issue, f"task-{n}")
    await db.commit()
    sess = (await db.execute(select(CliSession))).scalar_one()

    await cli.dispatch(sess.id)
    sent = _sent(calls)
    # The `now` ticket and the first queued one; the second queued one waits.
    assert [s["text"].splitlines()[0] for s in sent] == [
        "[Traccoon ticket CLI-3] Ticket 3", "[Traccoon ticket CLI-1] Ticket 1"]
    assert [s["clear"] for s in sent] == [True, False]

    calls.clear()
    await cli.dispatch(sess.id)
    assert _sent(calls) == []

    msg = await cli.report(db, sess, "CLI-1", "done", "finished")
    assert "reported" in msg
    await cli.dispatch(sess.id)
    assert [s["text"].splitlines()[0] for s in _sent(calls)] == ["[Traccoon ticket CLI-2] Ticket 2"]


async def test_the_report_answers_the_waiting_workflow(db, fake):
    owner, proj, (issue,) = await _cli_project(db)
    redis, _ = fake
    await cli.enqueue(db, issue, "task-x")
    await db.commit()
    sess = (await db.execute(select(CliSession))).scalar_one()
    await cli.dispatch(sess.id)

    assert "must be one of" in await cli.report(db, sess, "CLI-1", "planned", "?")
    await cli.report(db, sess, "CLI-1", "blocked", "Which colour?")
    result = json.loads(redis.values["traccoon:result:task-x"])
    assert result["status"] == "blocked" and result["blocker"] == {"kind": "question"}
    assert "task-x" not in redis.sets[cli.CLI_TASKS]
    d = (await db.execute(select(CliDelivery))).scalar_one()
    assert d.state == "reported"


async def test_a_ticket_released_again_replaces_its_old_entry(db, fake):
    owner, proj, (issue,) = await _cli_project(db)
    redis, _ = fake
    await cli.enqueue(db, issue, "first")
    await cli.enqueue(db, issue, "second")
    await db.commit()
    states = dict((await db.execute(select(CliDelivery.task_id, CliDelivery.state))).all())
    assert states == {"first": "cancelled", "second": "waiting"}
    assert redis.sets[cli.CLI_TASKS] == {"second"}


async def test_the_tools_only_open_with_the_session_token(db, client, fake):
    owner, proj, (issue,) = await _cli_project(db)
    await cli.enqueue(db, issue, "task-m")
    sess = (await db.execute(select(CliSession))).scalar_one()
    sess.mcp_token_hash = cli.token_hash("secret")
    await db.commit()
    await cli.dispatch(sess.id)

    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = await client.post("/mcp/project", json=call, headers={"Authorization": "Bearer nope"})
    assert r.json()["error"]["code"] == -32001
    r = await client.post("/mcp/project", json=call, headers={"Authorization": "Bearer secret"})
    assert "ticket_report" in [t["name"] for t in r.json()["result"]["tools"]]

    report = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "ticket_report",
        "arguments": {"key": "CLI-1", "status": "done", "summary": "ok"}}}
    r = await client.post("/mcp/project", json=report,
                          headers={"Authorization": "Bearer secret"})
    assert "reported as done" in r.json()["result"]["content"][0]["text"]
