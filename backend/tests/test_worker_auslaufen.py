"""Ein Deploy darf keinen denkenden Agenten erschlagen.

Docker schickt beim Neustart SIGTERM und tötet nach der Gnadenfrist. Ohne Handler starb der
Worker sofort — mitsamt jedem Lauf, der gerade arbeitete. Die Wiedervorlage rettet den
Auftrag, nicht das Gespräch: ABC-31 verlor am 2026-08-07 zweimal knapp 40 Züge, beide Male
durch einen Deploy von Hand.

Vollständig lösen lässt sich das nicht — ein Lauf darf Stunden dauern, ein Deploy nicht
Stunden warten. Die Auslaufzeit deckt den laufenden Modellzug samt Werkzeug ab, und damit
stehen die Schrittzeilen, aus denen der Nachfolger seine Übergabe baut.
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
    """Die Frist ist eine Frist: ein stundenlanger Lauf hält den Deploy nicht auf. Er wird
    NICHT abgebrochen — er steht noch in PROCESSING und wird neu eingereiht."""
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
    await asyncio.wait_for(worker._auslaufen(), timeout=1)     # darf nicht die Frist absitzen


async def test_signalhandler_setzt_das_beenden_flag():
    worker._beenden.clear()
    worker._signale_annehmen()
    try:
        asyncio.get_running_loop().call_soon(worker._beenden.set)
        await asyncio.wait_for(worker._beenden.wait(), timeout=1)
    finally:
        worker._beenden.clear()
