"""Warten am Lebenszeichen statt an der Uhr.

Am 2026-08-05 standen ABC-2 und ABC-6 auf „fehlgeschlagen: unbekannter Fehler", obwohl
beide Agenten sauber fertig wurden und committeten: der Wächter im Backend hatte nach
30 Minuten aufgegeben, während der exec-Schritt (Umsetzung + zwei Review-Runden in EINEM
Auftrag) noch lief. Diese Tests halten die drei Zusagen fest:

* Ein lebender Lauf wird nicht abgeschnitten — auch nicht nach Stunden.
* Ein verschwundener Lauf wird als solcher benannt, nicht als „unbekannter Fehler".
* Trifft sein Ergebnis später doch ein, wird es nachgetragen statt weggeworfen.
"""
import app.core.redis as redismod
# Die echten Funktionen VOR dem autouse-Stub greifen (der ersetzt nur die Modul-Attribute).
from app.core.redis import lauf_lebt as echtes_lauf_lebt
from app.core.redis import wait_result as echtes_wait_result
from app.models.enums import TicketAgentStatus, WorkflowStepStatus
from app.models.ticket import Comment
from app.models.workflow import WorkflowStepRun
from sqlalchemy import select
import app.services.workflow_engine as enginemod
from test_lifecycle_process import _projekt_mit_ticket


class FakeRedis:
    """Nur so viel Redis, wie `wait_result`/`lauf_lebt` anfassen."""

    def __init__(self, ergebnis_ab=None, puls=False, queue=None):
        self.werte = {}
        self.ergebnis_ab = ergebnis_ab      # ab dem wievielten Abruf liegt ein Ergebnis da
        self.abrufe = 0
        self.puls = puls
        self.queue = queue or []

    async def get(self, key):
        if key.endswith("alive:t1"):
            return "1" if self.puls else None
        self.abrufe += 1
        if self.ergebnis_ab is not None and self.abrufe >= self.ergebnis_ab:
            return '{"status": "done", "output": "fertig"}'
        return None

    async def delete(self, *keys):
        return None

    async def lrange(self, liste, a, b):
        return self.queue

    async def hvals(self, key):
        return []


async def test_lebender_lauf_wird_nicht_abgeschnitten(monkeypatch):
    """Solange der Lauf lebt, wird gewartet — die alte 30-Minuten-Grenze gibt es nicht mehr."""
    fake = FakeRedis(ergebnis_ab=25, puls=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    res = await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_verschwundener_lauf_wird_aufgegeben(monkeypatch):
    """Kein Puls, nicht in der Warteschlange → nach der Gnadenfrist ist Schluss."""
    fake = FakeRedis(ergebnis_ab=None, puls=False)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    assert await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0) is None


async def test_auftrag_in_der_warteschlange_zaehlt_als_lebend(monkeypatch):
    """Noch nicht drangekommen ist nicht dasselbe wie verschwunden."""
    fake = FakeRedis(ergebnis_ab=25, puls=False, queue=['{"task_id": "t1"}'])
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    res = await echtes_wait_result("t1", poll=0.001, gnadenfrist=0.0)
    assert res == {"status": "done", "output": "fertig"}


async def test_harter_deckel_greift_wenn_gesetzt(monkeypatch):
    """Wer für einen Knoten bewusst eine Grenze setzt, bekommt sie auch."""
    fake = FakeRedis(ergebnis_ab=None, puls=True)
    monkeypatch.setattr(redismod, "get_redis", lambda: fake)
    monkeypatch.setattr(redismod, "lauf_lebt", echtes_lauf_lebt)
    monkeypatch.setattr(redismod, "PRUEF_TAKT", 0.0)

    assert await echtes_wait_result("t1", timeout=0.05, poll=0.001) is None


async def test_verlorener_lauf_wird_beim_namen_genannt(db, seeded, redis_stub):
    """Ohne Ergebnis steht im Ticket, WAS los war — nicht „unbekannter Fehler"."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = None      # der Wächter bekommt nichts

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()

    schritt = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.node_id == "plan"))).scalars().first()
    assert schritt.status == WorkflowStepStatus.done
    assert schritt.result["verloren"] is True
    assert schritt.result["task_id"]
    assert "verschwunden" in schritt.result["output"]

    texte = [c.body for c in (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id))).scalars().all()]
    assert any("verschwunden" in t for t in texte)
    assert not any("unbekannter Fehler" in t for t in texte)


async def test_nachzuegler_wird_verbucht(db, seeded, redis_stub):
    """Trifft das Ergebnis später doch ein, läuft der Prozess weiter statt zu verharren."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = None
    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.failed

    # Der Worker war nur weg, nicht tot: sein Ergebnis liegt jetzt in Redis.
    schritt = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.node_id == "plan"))).scalars().first()
    redis_stub[schritt.result["task_id"]] = {"status": "planned", "output": "Der Plan.",
                                             "summary": "Plan"}
    await enginemod.nachzuegler_einsammeln()
    await enginemod.drain()

    await db.refresh(issue)
    assert issue.plan == "Der Plan."
    assert issue.agent_status == TicketAgentStatus.plan_review
