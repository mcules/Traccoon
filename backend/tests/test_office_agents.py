"""The personnel file: key figures per **role**, not per run.

The most important test of this file is `test_the_project_manager_does_not_start_at_zero`. A
success rate of `success/runs` would report the `project_manager` at 0 % (it has 0 `success`
and 7 `planned` in the running instance) and the `architect` at 6 % instead of 78 %.
`office/engine.ts::verdictOf` on the other hand treats `planned` as "ok" **today already**. A
number that contradicts one's own code is not a key figure but a slander. That is why the
server computes three disjoint sets and delivers them ready: `delivered`, `waiting`,
`aborted`. As soon as `success/total` stood anywhere in the frontend again, the lie would be
back, and these tests nail the semantics down in the backend.

The second bracket is the honesty of the distributions: duration as median, p90 and maximum
plus a histogram (never as an average, because one run took 36.5 hours), `iterations` and
steps separated, and every cost sum as a lower bound as long as `priced` is NULL.
"""
import datetime as dt

import pytest

from app.models.agents import CostEntry, Run, RunStep
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def stage(db, *, role_of=ProjectRole.viewer):
    """User (a viewer by default!), project, ticket."""
    user = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, user, role_of)
    kind = IssueType(project_id=proj.id, name="Aufgabe")
    status = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo)
    db.add_all([kind, status, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="AAA-1", type_id=kind.id,
                  status_id=status.id, summary="Tu was", reporter_id=user.id, rank="1")
    db.add(issue)
    await db.commit()
    return user, proj, issue


async def make_run(db, issue, *, agent="developer", status="success", duration_s=60,
              iterations=0, age_h=1) -> Run:
    start = NOW - dt.timedelta(hours=age_h)
    r = Run(issue_id=issue.id, project_id=issue.project_id, agent=agent, phase="execute",
            provider="claude_code", model="sonnet", status=status, iterations=iterations,
            started_at=start,
            finished_at=None if status == "running" else start + dt.timedelta(seconds=duration_s))
    db.add(r)
    await db.commit()
    return r


async def step(db, run, *, tool=None, ok=None, seq=1):
    db.add(RunStep(run_id=run.id, seq=seq, role="tool" if tool else "assistant",
                   kind="tool_call" if tool else "agent_text", tool_name=tool,
                   content="…", ok=ok, created_at=NOW))
    await db.commit()


async def item(db, run, *, cost=1.0, priced=None, ein=0, aus=0, cache=0):
    db.add(CostEntry(run_id=run.id, project_id=run.project_id, issue_id=run.issue_id,
                     agent=run.agent, provider="claude_code", model="sonnet",
                     input_tokens=ein, output_tokens=aus, cache_read_tokens=cache,
                     cost_usd=cost, priced=priced, created_at=NOW))
    await db.commit()


def role_of(payload: dict, name: str) -> dict:
    for row in payload["agents"]:
        if row["agent"] == name:
            return row
    raise AssertionError(f"Role {name!r} is missing in {[r['agent'] for r in payload['agents']]}")


# ── The three bars ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_project_manager_does_not_start_at_zero(client, db):
    """The test it is all about: 0 `success`, 7 `planned`, and 7 of 11 delivered regardless.

    What is reproduced is the real distribution of the `project_manager` in the running
    instance (7 planned, 2 blocked, 2 failed). `success/runs` would give 0 %; right is 7/11 = 64 %.
    """
    user, proj, issue = await stage(db)
    for _ in range(7):
        await make_run(db, issue, agent="project_manager", status="planned")
    for _ in range(2):
        await make_run(db, issue, agent="project_manager", status="blocked")
    for _ in range(2):
        await make_run(db, issue, agent="project_manager", status="failed")

    r = await client.get("/office/agents", headers=auth(user))
    assert r.status_code == 200
    pm = role_of(r.json(), "project_manager")
    assert pm["runs"] == 11
    assert pm["by_status"] == {"planned": 7, "blocked": 2, "failed": 2}
    assert pm["delivered"] == 7      # NOT 0
    assert pm["waiting"] == 2
    assert pm["aborted"] == 2
    # The three sets are disjoint and complete; otherwise a run would disappear silently.
    assert pm["delivered"] + pm["waiting"] + pm["aborted"] == pm["runs"]


@pytest.mark.asyncio
async def test_architect_at_78_percent(client, db):
    """`architect` in echt: 3 success + 36 planned + 5 blocked + 6 failed = 39/50."""
    user, proj, issue = await stage(db)
    for status, n in (("success", 3), ("planned", 36), ("blocked", 5), ("failed", 6)):
        for _ in range(n):
            await make_run(db, issue, agent="architect", status=status)

    r = await client.get("/office/agents", headers=auth(user))
    a = role_of(r.json(), "architect")
    assert (a["runs"], a["delivered"], a["waiting"], a["aborted"]) == (50, 39, 5, 6)


@pytest.mark.asyncio
async def test_status_mapping(client, db):
    """`planned` means delivered, `blocked` waiting, `loop_exhausted` aborted.

    `loop_exhausted` is the case a naive assignment overlooks: the run used up its round
    limit without finishing, and that is an abort, not waiting.
    """
    user, proj, issue = await stage(db)
    await make_run(db, issue, agent="developer", status="success")
    await make_run(db, issue, agent="developer", status="planned")
    await make_run(db, issue, agent="developer", status="blocked")
    await make_run(db, issue, agent="developer", status="failed")
    await make_run(db, issue, agent="developer", status="loop_exhausted")
    await make_run(db, issue, agent="developer", status="running")

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["runs"] == 6
    assert d["delivered"] == 2
    assert d["waiting"] == 1
    assert d["aborted"] == 2
    assert d["running"] == 1
    # The running run is in NONE of the three bars: it has decided nothing yet.
    assert d["delivered"] + d["waiting"] + d["aborted"] + d["running"] == d["runs"]


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_foreign_project_is_404(client, db):
    """A non-member gets a 404, never a 403; a 403 would reveal the existence of the project."""
    user, proj, issue = await stage(db)
    foreign = await make_user(db, "bob")
    await make_run(db, issue, agent="developer")

    r = await client.get(f"/projects/{proj.id}/office/agents", headers=auth(foreign))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_viewer_is_enough(client, db):
    """A viewer may read the file. `/costs/global` is `require_admin`; the office explicitly
    is not, because otherwise an empty tab would stand there for most users."""
    user, proj, issue = await stage(db, role_of=ProjectRole.viewer)
    await make_run(db, issue, agent="developer")

    r = await client.get(f"/projects/{proj.id}/office/agents", headers=auth(user))
    assert r.status_code == 200
    assert role_of(r.json(), "developer")["runs"] == 1


@pytest.mark.asyncio
async def test_the_global_view_does_not_show_foreign_ones(client, db):
    """Global is no 404 surface (there is no path whose existence would be revealed) but
    simply empty: the stranger sees no foreign roles."""
    user, proj, issue = await stage(db)
    foreign = await make_user(db, "bob")
    await make_run(db, issue, agent="developer")

    r = await client.get("/office/agents", headers=auth(foreign))
    assert r.status_code == 200
    assert r.json()["agents"] == []


@pytest.mark.asyncio
async def test_an_admin_sees_everything(client, db):
    user, proj, issue = await stage(db)
    boss = await make_user(db, "chef", admin=True)
    await make_run(db, issue, agent="developer")
    await item(db, (await make_run(db, issue, agent="assistent")), cost=2.0)

    r = await client.get("/office/agents", headers=auth(boss))
    assert r.status_code == 200
    assert {a["agent"] for a in r.json()["agents"]} == {"developer", "assistent"}
    assert role_of(r.json(), "assistent")["cost_usd"] == 2.0


# ── Parameter ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_agent_filter_narrows(client, db):
    user, proj, issue = await stage(db)
    await make_run(db, issue, agent="developer")
    await make_run(db, issue, agent="architect", status="planned")

    r = await client.get("/office/agents?agent=architect", headers=auth(user))
    assert [a["agent"] for a in r.json()["agents"]] == ["architect"]


@pytest.mark.asyncio
async def test_the_window_is_clamped_and_named_in_the_answer(client, db):
    """`since_hours` is clamped and delivered along: the view should be able to say "of the
    last N hours", because `run_retention_days` deletes older runs."""
    user, proj, issue = await stage(db)
    await make_run(db, issue, agent="developer", age_h=1)
    await make_run(db, issue, agent="developer", age_h=1000)

    r = await client.get("/office/agents?since_hours=24", headers=auth(user))
    assert r.json()["since_hours"] == 24
    assert role_of(r.json(), "developer")["runs"] == 1

    # 0 clamps to 1, and nonsense upwards to one year.
    assert (await client.get("/office/agents?since_hours=0",
                             headers=auth(user))).json()["since_hours"] == 1
    wide = (await client.get("/office/agents?since_hours=999999", headers=auth(user))).json()
    assert wide["since_hours"] == 24 * 365
    assert role_of(wide, "developer")["runs"] == 2


# ── Duration: a distribution instead of an average ───────────────────────────

@pytest.mark.asyncio
async def test_percentiles_and_histogram(client, db):
    """A distribution computed by hand: 12 s · 25 s · 100 s · 480 s · 1700 s.

    p50 is the 3rd value (100 s), so it lies in the ladder bucket up to 120 s, giving `p50_ms=120000`.
    p90 is the 5th value (1700 s), so the bucket up to 1 800 000 ms, clamped to the measured
    maximum, giving `p90_ms=1 700 000`. The clamping is the reason why the upper bound of a
    bucket never claims more than was measured.

    An average would lie at 463 s and describe not a single one of these runs, exactly the
    error the 36.5 hour session forces in the real instance.
    """
    user, proj, issue = await stage(db)
    for s in (12, 25, 100, 480, 1700):
        await make_run(db, issue, agent="developer", duration_s=s, age_h=2)

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(),
              "developer")["duration"]
    assert d["p50_ms"] == 120_000
    assert abs(d["p90_ms"] - 1_700_000) <= 5
    assert abs(d["max_ms"] - 1_700_000) <= 5
    assert d["buckets"] == [
        {"lt_ms": 60_000, "n": 2},          # 12 s, 25 s
        {"lt_ms": 300_000, "n": 1},         # 100 s
        {"lt_ms": 1_200_000, "n": 1},       # 480 s
        {"lt_ms": 4_800_000, "n": 1},       # 1700 s
        {"lt_ms": None, "n": 0},
    ]


@pytest.mark.asyncio
async def test_a_running_run_has_no_duration(client, db):
    """A still running run counts in `running` but in no duration bucket: "until now" is not
    a duration but a number that changes on the next fetch."""
    user, proj, issue = await stage(db)
    await make_run(db, issue, agent="developer", status="running")

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["running"] == 1
    assert d["duration"]["max_ms"] is None
    assert d["duration"]["p50_ms"] is None
    assert sum(b["n"] for b in d["duration"]["buckets"]) == 0


# ── Rounds and steps are two things ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_rounds_and_steps_kept_apart(client, db):
    """`iterations` (rounds) and `run_steps` (steps) have fields of their own: in reality they
    stand at an average of 6.9 against 21.5, and a common field would have made both unreadable."""
    user, proj, issue = await stage(db)
    a = await make_run(db, issue, agent="developer", iterations=2)
    b = await make_run(db, issue, agent="developer", iterations=8)
    for i in range(3):
        await step(db, a, seq=i)
    for i in range(7):
        await step(db, b, seq=i)

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["iterations_avg"] == 5.0 and d["iterations_max"] == 8
    assert d["steps_avg"] == 5.0 and d["steps_max"] == 7


@pytest.mark.asyncio
async def test_the_step_average_counts_only_runs_with_steps(client, db):
    """A run whose steps the retention deleted did not have "0 steps", so it does not pull the
    average down."""
    user, proj, issue = await stage(db)
    a = await make_run(db, issue, agent="developer")
    await make_run(db, issue, agent="developer")          # cleared: no step rows any more
    for i in range(10):
        await step(db, a, seq=i)

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["runs"] == 2
    assert d["steps_avg"] == 10.0                      # not 5.0


# ── Costs: always a lower bound ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cost_is_partial_while_priced_is_null(client, db):
    """`priced IS NULL` means "never recorded whether there was a catalog entry", and in the
    running instance that applies to ALL 411 entries. Every sum is therefore a lower bound."""
    user, proj, issue = await stage(db)
    r1 = await make_run(db, issue, agent="developer")
    await item(db, r1, cost=2.5, priced=None, ein=100, aus=20, cache=7)

    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_usd"] == 2.5
    assert d["cost_partial"] is True
    assert (d["in_tokens"], d["out_tokens"], d["cache_read_tokens"]) == (100, 20, 7)


@pytest.mark.asyncio
async def test_cost_partial_is_false_when_everything_is_priced(client, db):
    user, proj, issue = await stage(db)
    r1 = await make_run(db, issue, agent="developer")
    await item(db, r1, cost=1.0, priced=True)
    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_partial"] is False

    # A single unpriced entry tips the whole role to "at least".
    await item(db, r1, cost=0.0, priced=False)
    d = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_partial"] is True


@pytest.mark.asyncio
async def test_costs_survive_the_run(client, db):
    """Grouping happens by `cost_entries.agent`, not by `runs.agent`: `run_id` is `SET NULL`,
    and an entry survives the deletion of the run. Computed over `runs.agent` the bill would
    disappear with the run."""
    user, proj, issue = await stage(db)
    r1 = await make_run(db, issue, agent="game-operator")
    await item(db, r1, cost=3.0)
    await db.delete(r1)
    await db.commit()

    row = role_of((await client.get("/office/agents", headers=auth(user))).json(),
                "game-operator")
    assert row["runs"] == 0
    assert row["cost_usd"] == 3.0
    assert row["cost_partial"] is True


# ── Werkzeugtabelle ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_order_and_capping(client, db):
    user, proj, issue = await stage(db)
    r1 = await make_run(db, issue, agent="developer")
    seq = 0
    for tool, n in (("fs_read", 5), ("codegraph", 3), ("fs_list", 2), ("check", 1)):
        for _ in range(n):
            seq += 1
            await step(db, r1, tool=tool, ok=(tool != "check"), seq=seq)
    # A step without a tool (a model turn) does not belong in the table.
    await step(db, r1, seq=99)

    full = role_of((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert [t["tool"] for t in full["tools"]] == ["fs_read", "codegraph", "fs_list", "check"]
    assert full["tools"][0] == {"tool": "fs_read", "n": 5, "ok": 5, "failed": 0}
    assert full["tools"][3] == {"tool": "check", "n": 1, "ok": 0, "failed": 1}

    short = role_of((await client.get("/office/agents?tool_limit=2",
                                   headers=auth(user))).json(), "developer")
    assert [t["tool"] for t in short["tools"]] == ["fs_read", "codegraph"]


# ── Projekt- gegen globale Akte ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_project_file_shows_only_that_project(client, db):
    user, proj, issue = await stage(db)
    second = await make_project(db, "BBB", "Beta")
    await add_member(db, second, user, ProjectRole.viewer)
    kind = IssueType(project_id=second.id, name="Aufgabe")
    status = WorkflowStatus(project_id=second.id, name="To Do", category=StatusCategory.todo)
    db.add_all([kind, status, IssueCounter(project_id=second.id, last_number=0)])
    await db.commit()
    issue2 = Issue(project_id=second.id, number=1, key="BBB-1", type_id=kind.id,
                   status_id=status.id, summary="Anderswo", reporter_id=user.id, rank="1")
    db.add(issue2)
    await db.commit()

    await make_run(db, issue, agent="developer")
    await make_run(db, issue2, agent="news", status="failed")

    one = (await client.get(f"/projects/{proj.id}/office/agents", headers=auth(user))).json()
    assert [a["agent"] for a in one["agents"]] == ["developer"]

    global_ = (await client.get("/office/agents", headers=auth(user))).json()
    assert {a["agent"] for a in global_["agents"]} == {"developer", "news"}
