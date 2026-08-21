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
from app.core.redis import lauf_lebt as echtes_lauf_lebt
from app.core.redis import wait_result as echtes_wait_result
from app.models.enums import TicketAgentStatus, WorkflowStepStatus
from app.models.ticket import Comment
from app.models.workflow import WorkflowStepRun
from sqlalchemy import select
import app.services.workflow_engine as enginemod
from test_lifecycle_process import _projekt_mit_ticket


class FakeRedis:
    """Only as much Redis as `wait_result` and `lauf_lebt` touch."""

    def __init__(self, result_ab=None, puls=False, queue=None):
        self.values = {}
        self.result_ab = result_ab      # from which fetch on a result lies there
        self.abrufe = 0
        self.puls = puls
        self.queue = queue or []

    async def get(self, key):
        if key.endswith("alive:t1"):
            return "1" if self.puls else None
        self.abrufe += 1
        if self.result_ab is not None and self.abrufe >= self.result_ab:
            return '{"status": "done", "output": "fertig"}'
        return None

    async def delete(self, *keys):
        return None

    async def lrange(self, listing, a, b):
        return self.queue

    async def hvals(self, key):
        return []


async def test_lebender_lauf_wird_nicht_abgeschnitten(monkeypatch):
    """As long as the run lives it is waited for: the old 30 minute limit does not exist any more."""
    fake = FakeRedis(result_ab=25, puls=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    res = await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_verschwundener_lauf_wird_aufgegeben(monkeypatch):
    """No pulse, not in the queue: after the grace period it stops."""
    fake = FakeRedis(result_ab=None, puls=False)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    assert await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0) is None


async def test_task_in_der_warteschlange_zaehlt_as_lebend(monkeypatch):
    """Not started yet is not the same as disappeared."""
    fake = FakeRedis(result_ab=25, puls=False, queue=['{"task_id": "t1"}'])
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    res = await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_harter_deckel_greift_wenn_gesetzt(monkeypatch):
    """Whoever deliberately sets a limit for a node gets it."""
    fake = FakeRedis(result_ab=None, puls=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    assert await echtes_wait_result("t1", timeout=0.05, poll=0.001) is None


async def test_verlorener_lauf_wird_beim_namen_genannt(db, seeded, redis_stub):
    """Without a result the ticket says WHAT was going on, not "unknown error"."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
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

    texte = [c.body for c in (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id))).scalars().all()]
    assert any("verschwunden" in t for t in texte)
    assert not any("unbekannter Fehler" in t for t in texte)


async def test_nachzuegler_wird_verbucht(db, seeded, redis_stub):
    """If the result arrives later after all, the process runs on instead of standing still."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
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
    await enginemod.nachzuegler_einsammeln()
    await enginemod.drain()

    await db.refresh(issue)
    assert issue.plan == "Der Plan."
    assert issue.agent_status == TicketAgentStatus.plan_review
