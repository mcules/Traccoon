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


async def test_auslaufen_wartet_auf_den_laufenden_agenten(monkeypatch):
    monkeypatch.setattr(worker, "DRAIN_SEC", 5)
    fertig = asyncio.Event()

    async def agent():
        await asyncio.sleep(0.05)
        fertig.set()

    worker.RUNNING.clear()
    worker.RUNNING["TST-1"] = asyncio.create_task(agent())
    try:
        await worker._auslaufen()
        assert fertig.is_set(), "der laufende Agent wurde nicht zu Ende gebracht"
    finally:
        worker.RUNNING.clear()


async def test_auslaufen_gibt_nach_der_frist_auf(monkeypatch):
    """The deadline is a deadline: a run of hours does not hold the deploy up. It is NOT
    aborted; it still stands in PROCESSING and is queued anew."""
    monkeypatch.setattr(worker, "DRAIN_SEC", 0)

    async def zaeher_agent():
        await asyncio.sleep(30)

    task = asyncio.create_task(zaeher_agent())
    worker.RUNNING.clear()
    worker.RUNNING["TST-1"] = task
    try:
        await worker._auslaufen()
        assert not task.done()
        assert not task.cancelled(), "der Lauf darf nicht von Hand abgebrochen werden"
    finally:
        task.cancel()
        worker.RUNNING.clear()


async def test_ohne_laufende_agenten_sofort_fertig(monkeypatch):
    monkeypatch.setattr(worker, "DRAIN_SEC", 30)
    worker.RUNNING.clear()
    await asyncio.wait_for(worker._auslaufen(), timeout=1)     # must not sit out the deadline


async def test_signalhandler_setzt_das_beenden_flag():
    worker._beenden.clear()
    worker._signale_annehmen()
    try:
        asyncio.get_running_loop().call_soon(worker._beenden.set)
        await asyncio.wait_for(worker._beenden.wait(), timeout=1)
    finally:
        worker._beenden.clear()
