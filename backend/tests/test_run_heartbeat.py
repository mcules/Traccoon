"""Waiting on the sign of life instead of on the clock.

On 2026-08-05 UNI-2 and UNI-6 stood on "failed: unknown error" although both agents
finished cleanly and committed: the watcher in the backend had given up after 30 minutes
while the exec step (implementation plus two review rounds in ONE assignment) was still
running. These tests record the three promises:

* A living run is not cut off, not even after hours.
* A disappeared run is named as such, not as an "unknown error".
* If its result arrives later after all, it is added instead of thrown away.
"""
import app.core.redis as redismod
# Reach the real functions BEFORE the autouse stub (which only replaces the module attributes).
from app.core.redis import run_alive as real_run_alive
from app.core.redis import wait_result as real_wait_result
from app.models.enums import TicketAgentStatus, WorkflowStepStatus
from app.models.ticket import Comment
from app.models.workflow import WorkflowStepRun
from sqlalchemy import select
import app.services.workflow_engine as enginemod
from test_lifecycle_process import _project_with_ticket


class FakeRedis:
    """Only as much Redis as `wait_result` and `lauf_lebt` touch."""

    def __init__(self, result_from=None, pulse=False, queue=None):
        self.values = {}
        self.result_from = result_from      # from which fetch on a result lies there
        self.calls = 0
        self.pulse = pulse
        self.queue = queue or []

    async def get(self, key):
        if key.endswith("alive:t1"):
            return "1" if self.pulse else None
        self.calls += 1
        if self.result_from is not None and self.calls >= self.result_from:
            return '{"status": "done", "output": "fertig"}'
        return None

    async def delete(self, *keys):
        return None

    async def lrange(self, listing, a, b):
        return self.queue

    async def hvals(self, key):
        return []


async def test_a_live_run_is_not_cut_off(monkeypatch):
    """As long as the run lives it is waited for: the old 30 minute limit does not exist any more."""
    fake = FakeRedis(result_from=25, pulse=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "run_alive", real_run_alive)
    monkeypatch.setattr(redismod, "CHECK_BEAT", 0.0)

    res = await real_wait_result("t1", poll=0.001, grace=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_a_vanished_run_is_given_up(monkeypatch):
    """No pulse, not in the queue: after the grace period it stops."""
    fake = FakeRedis(result_from=None, pulse=False)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "run_alive", real_run_alive)
    monkeypatch.setattr(redismod, "CHECK_BEAT", 0.0)

    assert await real_wait_result("t1", poll=0.001, grace=0.0) is None


async def test_a_task_in_the_queue_counts_as_alive(monkeypatch):
    """Not started yet is not the same as disappeared."""
    fake = FakeRedis(result_from=25, pulse=False, queue=['{"task_id": "t1"}'])
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "run_alive", real_run_alive)
    monkeypatch.setattr(redismod, "CHECK_BEAT", 0.0)

    res = await real_wait_result("t1", poll=0.001, grace=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_the_hard_cap_applies_when_set(monkeypatch):
    """Whoever deliberately sets a limit for a node gets it."""
    fake = FakeRedis(result_from=None, pulse=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "run_alive", real_run_alive)
    monkeypatch.setattr(redismod, "CHECK_BEAT", 0.0)

    assert await real_wait_result("t1", timeout=0.05, poll=0.001) is None


async def test_a_lost_run_is_called_by_its_name(db, seeded, redis_stub):
    """Without a result the ticket says WHAT was going on, not "unknown error"."""
    owner, proj, issue, _ = await _project_with_ticket(db)
    redis_stub["*"] = None      # the watcher gets nothing

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()

    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.node_id == "plan"))).scalars().first()
    assert step.status == WorkflowStepStatus.done
    assert step.result["verloren"] is True
    assert step.result["task_id"]
    assert "verschwunden" in step.result["output"]

    texts = [c.body for c in (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id))).scalars().all()]
    assert any("verschwunden" in t for t in texts)
    assert not any("unbekannter Fehler" in t for t in texts)


async def test_a_straggler_is_booked(db, seeded, redis_stub):
    """If the result arrives later after all, the process runs on instead of standing still."""
    owner, proj, issue, _ = await _project_with_ticket(db)
    redis_stub["*"] = None
    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.failed

    # The worker was only away, not dead: its result now lies in Redis.
    step = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.node_id == "plan"))).scalars().first()
    redis_stub[step.result["task_id"]] = {"status": "planned", "output": "Der Plan.",
                                             "summary": "Plan"}
    await enginemod.stragglers_collect()
    await enginemod.drain()

    await db.refresh(issue)
    assert issue.plan == "Der Plan."
    assert issue.agent_status == TicketAgentStatus.plan_review
