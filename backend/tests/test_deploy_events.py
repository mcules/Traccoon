"""Deployments im Büro-Ereignisstrom — Watcher, Anker, geliehene `seq`.

Drei Dinge werden hier festgenagelt, weil sie sonst still kaputtgehen:

1. **Idempotenz** liegt in einer Spalte (`announced_status`), nicht im Prozess. Zweimal
   hinsehen darf nicht zweimal erzählen — auch nicht nach einem Neustart.
2. **Wartungs-Updates bekommen kein Bühnen-Ereignis.** Das sieht wie eine Lücke aus und
   ist eine Entscheidung: ein Self-Deploy recreated den Container, der die Bühne
   beliefert. Ohne diesen Test „repariert" das irgendwann jemand.
3. **Slot 3 hat zwei Bewerber.** Die synthetisierte `run_end`-Grenze gewinnt, das
   Bestands-Deployment weicht auf die Vorgängerzeile aus. Kollidierten sie, verlöre der
   Recorder eines von beiden — er entdoppelt ausschließlich über `seq`.
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
def kein_redis(monkeypatch):
    """Kein echter Redis im Test — und was gesendet wurde, ist prüfbar."""
    import app.core.redis as redismod
    gesendet: list[tuple[str, str]] = []

    class _R:
        async def publish(self, kanal, daten):
            gesendet.append((kanal, daten))

    monkeypatch.setattr(redismod, "get_redis", lambda: _R())
    return gesendet


# ── Testdaten ────────────────────────────────────────────────────────────────

async def ticket(db, projekt, nummer: int = 1) -> Issue:
    typ = IssueType(project_id=projekt.id, name="Aufgabe")
    status = WorkflowStatus(project_id=projekt.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=projekt.id, last_number=0)])
    await db.commit()
    i = Issue(project_id=projekt.id, number=nummer, key=f"{projekt.key}-{nummer}",
              type_id=typ.id, status_id=status.id, summary="Tu was", reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


async def lauf(db, *, issue=None, projekt=None, status="success", agent="developer") -> Run:
    r = Run(issue_id=issue.id if issue else None,
            project_id=projekt.id if projekt else (issue.project_id if issue else None),
            agent=agent, phase="execute", provider="claude_code", model="sonnet",
            status=status, started_at=NOW - dt.timedelta(minutes=5),
            finished_at=None if status == "running" else NOW - dt.timedelta(minutes=1))
    db.add(r)
    await db.commit()
    return r


async def schritte(db, run: Run, anzahl: int, *, ab_sekunden: int = 300) -> list[RunStep]:
    """Altzeilen (`kind=''`) mit aufsteigendem Zeitstempel — je Zeile ein Ereignis."""
    rows = [RunStep(run_id=run.id, seq=i + 1, role="assistant", content=f"Schritt {i + 1}",
                    created_at=NOW - dt.timedelta(seconds=ab_sekunden - i * 10))
            for i in range(anzahl)]
    db.add_all(rows)
    await db.commit()
    return rows


async def deployment(db, **kw) -> Deployment:
    dep = Deployment(**{"stack_dir": "/opt/docker/stacks/traccoon", "status": "building",
                        "source": "agent", **kw})
    db.add(dep)
    await db.commit()
    return dep


async def deploy_schritte(db, run_id: int) -> list[RunStep]:
    rows = (await db.execute(
        select(RunStep).where(RunStep.run_id == run_id, RunStep.kind == "deploy")
        .order_by(RunStep.id))).scalars().all()
    return list(rows)


# ── Idempotenz ───────────────────────────────────────────────────────────────

async def test_zweimal_watchen_erzaehlt_einmal(db):
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue, status="running")
    dep = await deployment(db, issue_id=issue.id, project_id=projekt.id, status="building")

    assert await dw.tick(db) == 1
    assert await dw.tick(db) == 0
    await db.refresh(dep)
    assert dep.announced_status == "building"
    assert [s.content for s in await deploy_schritte(db, run.id)] == [
        '{"deployment_id": %d, "state": "start", "log_head": ""}' % dep.id]

    # Der Ausgang ist ein neuer Zustand — und auch er wird nur einmal erzählt.
    dep.status = "ok"
    dep.log = "fertig gebaut"
    await db.commit()
    assert await dw.tick(db) == 1
    assert await dw.tick(db) == 0
    schritte_ = await deploy_schritte(db, run.id)
    assert [s.ok for s in schritte_] == [None, True]
    assert "fertig gebaut" in schritte_[-1].content


@pytest.mark.parametrize("announced,status,erwartet", [
    ("", "building", ["start"]),
    ("building", "ok", ["ok"]),
    ("building", "failed", ["fail"]),
    ("building", "rolledback", ["back"]),
    # Zwischen zwei Takten komplett durchgelaufen: der Auftakt wird nachgeholt, sonst
    # leuchtete das Rack auf, ohne dass je jemand hingelaufen ist.
    ("", "ok", ["start", "ok"]),
    ("pending", "failed", ["start", "fail"]),
    # Nichts zu zeigen: eine Warteschlange ist kein Vorgang, und `cancelled` schreibt
    # kein Codepfad (handgeschriebene Aufräumaktion, siehe `models/ops.Deployment`).
    ("", "pending", []),
    ("pending", "cancelled", []),
])
def test_states_for(announced, status, erwartet):
    assert dw.states_for(announced, status) == erwartet


# ── Ankerwahl ────────────────────────────────────────────────────────────────

async def test_anker_agentenwerkzeug_ist_der_wartende_lauf(db):
    """`worktree <> ''` heißt: ein Agent hat `deploy` gerufen und wartet inline. Die Zeile
    gehört SEINEM Lauf, nicht dem jüngsten — der kann längst ein Review sein."""
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    wartend = await lauf(db, issue=issue, status="running")
    juenger = await lauf(db, issue=issue, status="success", agent="reviewer")

    await deployment(db, issue_id=issue.id, project_id=projekt.id,
                     worktree="/workspace/tra-1", status="building")
    await dw.tick(db)
    assert len(await deploy_schritte(db, wartend.id)) == 1
    assert await deploy_schritte(db, juenger.id) == []


async def test_anker_merge_ist_der_juengste_lauf(db):
    """Ohne Worktree hat kein Agent gewartet (Merge/Workflow) — dann erzählt der jüngste
    Lauf des Tickets, weil er der ist, den der Raum gerade zeigt."""
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    aelter = await lauf(db, issue=issue, status="running")
    juenger = await lauf(db, issue=issue, status="success", agent="reviewer")

    await deployment(db, issue_id=issue.id, project_id=projekt.id, worktree="",
                     source="merge", status="building")
    await dw.tick(db)
    assert len(await deploy_schritte(db, juenger.id)) == 1
    assert await deploy_schritte(db, aelter.id) == []


async def test_wartungsupdate_erzeugt_kein_ereignis(db, kein_redis):
    """**Entscheidung, kein Versehen.** Ein Self-Deploy recreated den Backend-Container,
    der die Bühne beliefert: der WebSocket fällt mitten in der Animation, der Prozess, der
    sie zeichnen ließe, stirbt an ihr. Einen Vorgang zu animieren, der den Animierenden
    tötet, ist ein Kategorienfehler — diese Zeilen leben in der Liste, nicht im Raum."""
    projekt = await make_project(db, "TRA", "Traccoon")
    run = await lauf(db, projekt=projekt)           # es GÄBE einen Lauf zum Anhängen
    dep = await deployment(db, project_id=projekt.id, self_deploy=True, stack_dir="",
                           source="maintenance", status="building")

    assert await dw.tick(db) == 0
    assert await deploy_schritte(db, run.id) == []
    assert kein_redis == []
    # Quittiert wird trotzdem — sonst läge die Zeile bei jedem Takt wieder auf dem Tisch.
    await db.refresh(dep)
    assert dep.announced_status == "building"


async def test_ohne_lauf_kein_ereignis(db):
    """Ein Ticket ohne einen einzigen Lauf hat keinen Anker. Lieber eine Lücke als eine
    Zeile in einem Lauf, der mit dem Deploy nichts zu tun hat."""
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    await deployment(db, issue_id=issue.id, project_id=projekt.id, status="ok")
    assert await dw.tick(db) == 0


async def test_altbestand_bleibt_stumm(db):
    """Die 186 Bestandszeilen haben `announced_status=''` und wären sonst alle „neu" — der
    erste Takt erzählte drei Monate Historie, als wäre sie eben passiert."""
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue)
    await deployment(db, issue_id=issue.id, project_id=projekt.id, status="ok",
                     created_at=NOW - dt.timedelta(days=12))
    assert await dw.tick(db) == 0
    assert await deploy_schritte(db, run.id) == []


async def test_angefangene_geschichte_wird_zu_ende_erzaehlt(db):
    """Auftakt erzählt, Ausgang erst nach einem langen Backend-Ausfall: das Fenster darf
    die Zeile jetzt nicht fallen lassen, sonst bliebe das Rack für immer am Bauen."""
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=projekt.id, status="ok",
                     announced_status="building",
                     created_at=NOW - dt.timedelta(days=2))
    assert await dw.tick(db) == 1
    assert [s.ok for s in await deploy_schritte(db, run.id)] == [True]


# ── Freier Anschluss: deployment.finished ────────────────────────────────────

async def test_deployment_finished_feuert_einmal(db, monkeypatch):
    """Der Triggername steht seit jeher in `BUILTIN_EVENTS` und hat nie gefeuert."""
    import app.services.events as eventsmod
    gesehen: list[tuple[str, dict]] = []

    async def fake_emit(_db, event, **kw):
        gesehen.append((event, kw))
        return []

    monkeypatch.setattr(eventsmod, "emit", fake_emit)

    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    await lauf(db, issue=issue, status="running")
    dep = await deployment(db, issue_id=issue.id, project_id=projekt.id, status="building")

    await dw.tick(db)
    assert gesehen == []                       # `building` ist kein Abschluss

    dep.status = "ok"
    await db.commit()
    await dw.tick(db)
    assert [e for e, _ in gesehen] == ["deployment.finished"]
    kw = gesehen[0][1]
    assert kw["issue_id"] == issue.id and kw["source_ref"] == f"deployment:{dep.id}"
    assert kw["payload"]["deployment"]["ok"] is True

    await dw.tick(db)
    assert len(gesehen) == 1                   # quittiert ist quittiert


async def test_deployment_finished_auch_ohne_buehne(db, monkeypatch):
    """Das Wartungs-Update bekommt kein Bühnen-Ereignis — der Prozess-Auslöser hängt aber
    nicht an der Bühne, sondern am Abschluss."""
    import app.services.events as eventsmod
    gesehen: list[str] = []

    async def fake_emit(_db, event, **kw):
        gesehen.append(event)
        return []

    monkeypatch.setattr(eventsmod, "emit", fake_emit)

    projekt = await make_project(db, "TRA", "Traccoon")
    await deployment(db, project_id=projekt.id, self_deploy=True, stack_dir="",
                     source="maintenance", status="ok")
    await dw.tick(db)
    assert gesehen == ["deployment.finished"]


# ── Geliehene `seq` (Bestand) ────────────────────────────────────────────────

class _Zeile:
    """Minimale `run_steps`-Attrappe — `deploy_anchor_step_id` liest nur zwei Felder."""

    def __init__(self, step_id: int, sekunden: int):
        self.id = step_id
        self.created_at = NOW - dt.timedelta(seconds=sekunden)


class _Dep:
    def __init__(self, **kw):
        self.__dict__.update({"id": 42, "status": "ok", "stack_dir": "/stacks/tra",
                              "worktree": "", "log": "alles gut",
                              "created_at": NOW - dt.timedelta(seconds=25),
                              "started_at": None, "finished_at": None, **kw})


def ctx() -> RunCtx:
    return RunCtx(run_id=8871, project_id=27, owner_id=3, sid="issue:412", agent="dev")


def test_bestand_haengt_an_der_letzten_zeile_davor():
    zeilen = [_Zeile(100, 60), _Zeile(101, 40), _Zeile(102, 10)]
    anker = deploy_anchor_step_id(zeilen, _Dep().created_at)
    assert anker == 101
    ev = deployment_events(_Dep(), ctx(), anchor_step_id=anker)[0]
    assert ev["kind"] == "deploy" and ev["seq"] == 101 * SEQ_SLOTS + 3
    assert ev["deployment_id"] == 42 and ev["state"] == "ok"
    assert ev["target"] == "/stacks/tra" and ev["log_head"] == "alles gut"


def test_slot3_kollision_weicht_auf_die_vorgaengerzeile():
    """Die `run_end`-Grenze sitzt auf `last*4+3` — genau dem Platz, den das Deployment
    leihen wollte. Sie hat Vorrang (sie beendet einen Lauf, das Deployment illustriert
    ihn), das Deployment rutscht eine Zeile zurück."""
    zeilen = [_Zeile(100, 60), _Zeile(101, 40), _Zeile(102, 10)]
    anker = deploy_anchor_step_id(zeilen, _Dep().created_at, blocked={101})
    assert anker == 100
    ev = deployment_events(_Dep(), ctx(), anchor_step_id=anker)[0]
    assert ev["seq"] == 100 * SEQ_SLOTS + 3
    # Und wenn auch die Vorgängerzeile belegt ist, wird weiter zurückgegangen.
    assert deploy_anchor_step_id(zeilen, _Dep().created_at, blocked={100, 101}) is None


def test_bestand_ohne_zeile_davor_bekommt_nichts():
    """Ein Deploy, der vor der ersten geladenen Zeile liegt, hat keinen ehrlichen Platz —
    vorn eingehängt stünde er vor seinem eigenen Auslöser."""
    zeilen = [_Zeile(100, 5)]
    assert deploy_anchor_step_id(zeilen, _Dep().created_at) is None
    assert deployment_events(_Dep(), ctx(), anchor_step_id=None) == []


@pytest.mark.parametrize("status", ["pending", "pending-check", "cancelled", ""])
def test_bestand_ohne_zeigbaren_status_bleibt_stumm(status):
    assert deployment_events(_Dep(status=status), ctx(), anchor_step_id=100) == []


# ── Lesepfad ─────────────────────────────────────────────────────────────────

async def test_api_zeigt_bestandsdeployment_an_seiner_stelle(client, db):
    user = await make_user(db, "anna", admin=True)
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue)
    zeilen = await schritte(db, run, 3)
    dep = await deployment(db, issue_id=issue.id, project_id=projekt.id, status="failed",
                           log="❌ Wächter: Tests rot",
                           created_at=zeilen[1].created_at + dt.timedelta(seconds=2))

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(user))
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    deploys = [e for e in events if e["kind"] == "deploy"]
    assert len(deploys) == 1
    assert deploys[0]["deployment_id"] == dep.id and deploys[0]["state"] == "fail"
    # Zwischen der zweiten und der dritten Zeile — und die Reihenfolge bleibt monoton.
    assert deploys[0]["seq"] == zeilen[1].id * SEQ_SLOTS + 3
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    # Die synthetisierte `run_end`-Grenze steht weiter hinter allem.
    ende = [e for e in events if e["kind"] == "run_end"][0]
    assert ende["seq"] > deploys[0]["seq"]


async def test_api_erzaehlt_nicht_doppelt(client, db):
    """Was der Watcher als echte Zeile geschrieben hat, wird nicht noch einmal geliehen."""
    user = await make_user(db, "anna", admin=True)
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue, status="running")
    await schritte(db, run, 2)
    dep = await deployment(db, issue_id=issue.id, project_id=projekt.id, status="ok")
    await dw.tick(db)   # schreibt start + ok als echte Zeilen

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(user))
    deploys = [e for e in r.json()["events"] if e["kind"] == "deploy"]
    assert [e["state"] for e in deploys] == ["start", "ok"]
    assert {e["deployment_id"] for e in deploys} == {dep.id}


# ── Der Live-Weg ─────────────────────────────────────────────────────────────

async def test_watcher_sendet_in_den_kanal(db, kein_redis):
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    await lauf(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=projekt.id, status="building")
    await dw.tick(db)
    assert len(kein_redis) == 1 and '"kind": "deploy"' in kein_redis[0][1]


async def test_publish_step_schluckt_redis_ausfall(db, monkeypatch):
    """Die Ansicht ist ein Zuschauer, kein Beteiligter: ein toter Redis darf einen
    Deploy nicht zum Fehler machen."""
    import app.core.redis as redismod

    class _Tot:
        async def publish(self, *a, **k):
            raise RuntimeError("Redis weg")

    monkeypatch.setattr(redismod, "get_redis", lambda: _Tot())
    projekt = await make_project(db, "TRA", "Traccoon")
    issue = await ticket(db, projekt)
    run = await lauf(db, issue=issue, status="running")
    await deployment(db, issue_id=issue.id, project_id=projekt.id, status="building")

    assert await dw.tick(db) == 1               # kein Fehler nach oben
    assert len(await deploy_schritte(db, run.id)) == 1   # und die Zeile steht

    step = (await deploy_schritte(db, run.id))[0]
    await publish_step(RunCtx(run_id=run.id), step)      # auch direkt gerufen: still
