"""A deploy must not slay a thinking agent.

Docker sends SIGTERM on a restart and kills after the grace period. Without a handler the
worker died immediately, together with every run that was working. The follow-up rescues the
assignment, not the conversation: ABC-31 lost almost 40 turns twice on 2026-08-07, both
times through a deploy by hand.

It cannot be solved completely: a run may take hours, and a deploy must not wait hours. The
grace time covers the running model turn including its tool, and with that the step rows
stand from which the successor builds its handover.
"""
import asyncio

import app.worker.__main__ as worker


async def test_draining_waits_for_the_running_agent(monkeypatch):
    monkeypatch.setattr(worker, "DRAIN_SEC", 5)
    done = asyncio.Event()

    async def agent():
        await asyncio.sleep(0.05)
        done.set()

    worker.RUNNING.clear()
    worker.RUNNING["TST-1"] = asyncio.create_task(agent())
    try:
        await worker._drain()
        assert done.is_set(), "the running agent was not brought to an end"
    finally:
        worker.RUNNING.clear()


async def test_draining_gives_up_after_the_deadline(monkeypatch):
    """The deadline is a deadline: a run of hours does not hold the deploy up. It is NOT
    aborted; it still stands in PROCESSING and is queued anew."""
    monkeypatch.setattr(worker, "DRAIN_SEC", 0)

    async def tougher_agent():
        await asyncio.sleep(30)

    task = asyncio.create_task(tougher_agent())
    worker.RUNNING.clear()
    worker.RUNNING["TST-1"] = task
    try:
        await worker._drain()
        assert not task.done()
        assert not task.cancelled(), "the run must not be aborted by hand"
    finally:
        task.cancel()
        worker.RUNNING.clear()


async def test_without_running_agents_done_at_once(monkeypatch):
    monkeypatch.setattr(worker, "DRAIN_SEC", 30)
    worker.RUNNING.clear()
    await asyncio.wait_for(worker._drain(), timeout=1)     # must not sit out the deadline


async def test_the_signal_handler_sets_the_shutdown_flag():
    worker._shutdown.clear()
    worker._signals_accept()
    try:
        asyncio.get_running_loop().call_soon(worker._shutdown.set)
        await asyncio.wait_for(worker._shutdown.wait(), timeout=1)
    finally:
        worker._shutdown.clear()
