"""Die Personalakte: Kennzahlen je **Rolle**, nicht je Lauf.

Der wichtigste Test dieser Datei ist `test_project_manager_steht_nicht_bei_null`. Eine
Erfolgsquote `success/runs` wiese den `project_manager` mit 0 % aus — er hat in der
laufenden Instanz 0 `success` und 7 `planned` — und den `architect` mit 6 % statt 78 %.
`office/engine.ts::verdictOf` behandelt `planned` dagegen **schon heute** als „ok". Eine
Zahl, die dem eigenen Code widerspricht, ist keine Kennzahl, sondern eine Verleumdung.
Deshalb rechnet der Server drei disjunkte Mengen aus und liefert sie fertig: `delivered`,
`waiting`, `aborted`. Sobald irgendwo im Frontend wieder `success/total` stünde, wäre die
Lüge zurück — diese Tests nageln die Semantik im Backend fest.

Die zweite Klammer ist die Ehrlichkeit der Verteilungen: Dauer als Median/p90/max plus
Histogramm (nie als Mittelwert — ein Lauf dauerte 36,5 Stunden), `iterations` und Schritte
getrennt, und jede Kostensumme als Untergrenze, solange `priced` NULL ist.
"""
import datetime as dt

import pytest

from app.models.agents import CostEntry, Run, RunStep
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def buehne(db, *, rolle=ProjectRole.viewer):
    """Nutzer (standardmäßig nur Viewer!), Projekt, Ticket."""
    user = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, user, rolle)
    typ = IssueType(project_id=proj.id, name="Aufgabe")
    status = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="AAA-1", type_id=typ.id,
                  status_id=status.id, summary="Tu was", reporter_id=user.id, rank="1")
    db.add(issue)
    await db.commit()
    return user, proj, issue


async def lauf(db, issue, *, agent="developer", status="success", dauer_s=60,
              iterations=0, alter_h=1) -> Run:
    start = NOW - dt.timedelta(hours=alter_h)
    r = Run(issue_id=issue.id, project_id=issue.project_id, agent=agent, phase="execute",
            provider="claude_code", model="sonnet", status=status, iterations=iterations,
            started_at=start,
            finished_at=None if status == "running" else start + dt.timedelta(seconds=dauer_s))
    db.add(r)
    await db.commit()
    return r


async def schritt(db, run, *, tool=None, ok=None, seq=1):
    db.add(RunStep(run_id=run.id, seq=seq, role="tool" if tool else "assistant",
                   kind="tool_call" if tool else "agent_text", tool_name=tool,
                   content="…", ok=ok, created_at=NOW))
    await db.commit()


async def posten(db, run, *, cost=1.0, priced=None, ein=0, aus=0, cache=0):
    db.add(CostEntry(run_id=run.id, project_id=run.project_id, issue_id=run.issue_id,
                     agent=run.agent, provider="claude_code", model="sonnet",
                     input_tokens=ein, output_tokens=aus, cache_read_tokens=cache,
                     cost_usd=cost, priced=priced, created_at=NOW))
    await db.commit()


def rolle(payload: dict, name: str) -> dict:
    for row in payload["agents"]:
        if row["agent"] == name:
            return row
    raise AssertionError(f"Rolle {name!r} fehlt in {[r['agent'] for r in payload['agents']]}")


# ── Die drei Balken ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_project_manager_steht_nicht_bei_null(client, db):
    """Der Test, um den es geht: 0 `success`, 7 `planned` — und trotzdem 7 von 11 geliefert.

    Nachgebaut ist die echte Verteilung des `project_manager` in der laufenden Instanz
    (7 planned, 2 blocked, 2 failed). `success/runs` ergäbe 0 %; richtig sind 7/11 = 64 %.
    """
    user, proj, issue = await buehne(db)
    for _ in range(7):
        await lauf(db, issue, agent="project_manager", status="planned")
    for _ in range(2):
        await lauf(db, issue, agent="project_manager", status="blocked")
    for _ in range(2):
        await lauf(db, issue, agent="project_manager", status="failed")

    r = await client.get("/office/agents", headers=auth(user))
    assert r.status_code == 200
    pm = rolle(r.json(), "project_manager")
    assert pm["runs"] == 11
    assert pm["by_status"] == {"planned": 7, "blocked": 2, "failed": 2}
    assert pm["delivered"] == 7      # NICHT 0
    assert pm["waiting"] == 2
    assert pm["aborted"] == 2
    # Die drei Mengen sind disjunkt und vollständig — sonst verschwände ein Lauf lautlos.
    assert pm["delivered"] + pm["waiting"] + pm["aborted"] == pm["runs"]


@pytest.mark.asyncio
async def test_architekt_78_prozent(client, db):
    """`architect` in echt: 3 success + 36 planned + 5 blocked + 6 failed = 39/50."""
    user, proj, issue = await buehne(db)
    for status, n in (("success", 3), ("planned", 36), ("blocked", 5), ("failed", 6)):
        for _ in range(n):
            await lauf(db, issue, agent="architect", status=status)

    r = await client.get("/office/agents", headers=auth(user))
    a = rolle(r.json(), "architect")
    assert (a["runs"], a["delivered"], a["waiting"], a["aborted"]) == (50, 39, 5, 6)


@pytest.mark.asyncio
async def test_status_zuordnung(client, db):
    """`planned` → abgeliefert, `blocked` → wartend, `loop_exhausted` → abgebrochen.

    `loop_exhausted` ist der Fall, den eine naive Zuordnung übersieht: der Lauf hat sein
    Rundenlimit aufgebraucht, ohne fertig zu werden — das ist ein Abbruch, kein Warten.
    """
    user, proj, issue = await buehne(db)
    await lauf(db, issue, agent="developer", status="success")
    await lauf(db, issue, agent="developer", status="planned")
    await lauf(db, issue, agent="developer", status="blocked")
    await lauf(db, issue, agent="developer", status="failed")
    await lauf(db, issue, agent="developer", status="loop_exhausted")
    await lauf(db, issue, agent="developer", status="running")

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["runs"] == 6
    assert d["delivered"] == 2
    assert d["waiting"] == 1
    assert d["aborted"] == 2
    assert d["running"] == 1
    # Der laufende Lauf ist in KEINEM der drei Balken — er hat noch nichts entschieden.
    assert d["delivered"] + d["waiting"] + d["aborted"] + d["running"] == d["runs"]


# ── Sichtbarkeit ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fremdes_projekt_ist_404(client, db):
    """Nicht-Mitglied bekommt 404, nie 403 — eine 403 verriete die Existenz des Projekts."""
    user, proj, issue = await buehne(db)
    fremder = await make_user(db, "bob")
    await lauf(db, issue, agent="developer")

    r = await client.get(f"/projects/{proj.id}/office/agents", headers=auth(fremder))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_viewer_genuegt(client, db):
    """Ein Viewer darf die Akte lesen. `/costs/global` ist `require_admin` — das Büro
    ausdrücklich nicht, sonst stünde dort für die meisten Nutzer ein leerer Reiter."""
    user, proj, issue = await buehne(db, rolle=ProjectRole.viewer)
    await lauf(db, issue, agent="developer")

    r = await client.get(f"/projects/{proj.id}/office/agents", headers=auth(user))
    assert r.status_code == 200
    assert rolle(r.json(), "developer")["runs"] == 1


@pytest.mark.asyncio
async def test_global_zeigt_fremdes_nicht(client, db):
    """Global ist keine 404-Fläche (es gibt keinen Pfad, dessen Existenz verraten würde),
    sondern schlicht leer — der Fremde sieht keine fremden Rollen."""
    user, proj, issue = await buehne(db)
    fremder = await make_user(db, "bob")
    await lauf(db, issue, agent="developer")

    r = await client.get("/office/agents", headers=auth(fremder))
    assert r.status_code == 200
    assert r.json()["agents"] == []


@pytest.mark.asyncio
async def test_admin_sieht_alles(client, db):
    user, proj, issue = await buehne(db)
    chef = await make_user(db, "chef", admin=True)
    await lauf(db, issue, agent="developer")
    await posten(db, (await lauf(db, issue, agent="assistent")), cost=2.0)

    r = await client.get("/office/agents", headers=auth(chef))
    assert r.status_code == 200
    assert {a["agent"] for a in r.json()["agents"]} == {"developer", "assistent"}
    assert rolle(r.json(), "assistent")["cost_usd"] == 2.0


# ── Parameter ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_filter_verengt(client, db):
    user, proj, issue = await buehne(db)
    await lauf(db, issue, agent="developer")
    await lauf(db, issue, agent="architect", status="planned")

    r = await client.get("/office/agents?agent=architect", headers=auth(user))
    assert [a["agent"] for a in r.json()["agents"]] == ["architect"]


@pytest.mark.asyncio
async def test_fenster_klemmt_und_steht_in_der_antwort(client, db):
    """`since_hours` wird geklemmt und mitgeliefert — die Ansicht soll „der letzten N
    Stunden" sagen können, weil `run_retention_days` ältere Läufe löscht."""
    user, proj, issue = await buehne(db)
    await lauf(db, issue, agent="developer", alter_h=1)
    await lauf(db, issue, agent="developer", alter_h=1000)

    r = await client.get("/office/agents?since_hours=24", headers=auth(user))
    assert r.json()["since_hours"] == 24
    assert rolle(r.json(), "developer")["runs"] == 1

    # 0 klemmt auf 1, Unsinn nach oben auf ein Jahr.
    assert (await client.get("/office/agents?since_hours=0",
                             headers=auth(user))).json()["since_hours"] == 1
    weit = (await client.get("/office/agents?since_hours=999999", headers=auth(user))).json()
    assert weit["since_hours"] == 24 * 365
    assert rolle(weit, "developer")["runs"] == 2


# ── Dauer: Verteilung statt Mittelwert ───────────────────────────────────────

@pytest.mark.asyncio
async def test_perzentile_und_histogramm(client, db):
    """Handgerechnete Verteilung: 12 s · 25 s · 100 s · 480 s · 1700 s.

    p50 ist der 3. Wert (100 s) → er liegt im Leiter-Eimer bis 120 s, also `p50_ms=120000`.
    p90 ist der 5. Wert (1700 s) → Eimer bis 1 800 000 ms, geklemmt auf das gemessene
    Maximum → `p90_ms=1 700 000`. Die Klemmung ist der Grund, warum die Obergrenze eines
    Eimers nie mehr behauptet, als gemessen wurde.

    Ein Mittelwert läge bei 463 s und beschriebe keinen einzigen dieser Läufe — genau der
    Fehler, den die 36,5-Stunden-Sitzung in der echten Instanz erzwingt.
    """
    user, proj, issue = await buehne(db)
    for s in (12, 25, 100, 480, 1700):
        await lauf(db, issue, agent="developer", dauer_s=s, alter_h=2)

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(),
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
async def test_laufender_lauf_hat_keine_dauer(client, db):
    """Ein noch laufender Lauf zählt in `running`, aber in keinen Dauer-Eimer: „bis jetzt"
    ist keine Dauer, sondern eine Zahl, die sich beim nächsten Abruf ändert."""
    user, proj, issue = await buehne(db)
    await lauf(db, issue, agent="developer", status="running")

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["running"] == 1
    assert d["duration"]["max_ms"] is None
    assert d["duration"]["p50_ms"] is None
    assert sum(b["n"] for b in d["duration"]["buckets"]) == 0


# ── Runden und Schritte sind zwei Dinge ──────────────────────────────────────

@pytest.mark.asyncio
async def test_runden_und_schritte_getrennt(client, db):
    """`iterations` (Runden) und `run_steps` (Schritte) haben eigene Felder — in echt
    stehen sie bei Ø 6,9 gegen Ø 21,5, ein gemeinsames Feld hätte beide unlesbar gemacht."""
    user, proj, issue = await buehne(db)
    a = await lauf(db, issue, agent="developer", iterations=2)
    b = await lauf(db, issue, agent="developer", iterations=8)
    for i in range(3):
        await schritt(db, a, seq=i)
    for i in range(7):
        await schritt(db, b, seq=i)

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["iterations_avg"] == 5.0 and d["iterations_max"] == 8
    assert d["steps_avg"] == 5.0 and d["steps_max"] == 7


@pytest.mark.asyncio
async def test_schrittschnitt_zaehlt_nur_laeufe_mit_schritten(client, db):
    """Ein Lauf, dessen Schritte die Aufbewahrungsfrist gelöscht hat, hatte nicht
    „0 Schritte" — er zieht den Schnitt deshalb nicht nach unten."""
    user, proj, issue = await buehne(db)
    a = await lauf(db, issue, agent="developer")
    await lauf(db, issue, agent="developer")          # geräumt: keine Schrittzeilen mehr
    for i in range(10):
        await schritt(db, a, seq=i)

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["runs"] == 2
    assert d["steps_avg"] == 10.0                      # nicht 5.0


# ── Kosten: immer eine Untergrenze ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_cost_partial_solange_priced_null(client, db):
    """`priced IS NULL` heißt „nie festgehalten, ob es einen Katalogeintrag gab" — in der
    laufenden Instanz gilt das für ALLE 411 Posten. Jede Summe ist damit eine Untergrenze."""
    user, proj, issue = await buehne(db)
    r1 = await lauf(db, issue, agent="developer")
    await posten(db, r1, cost=2.5, priced=None, ein=100, aus=20, cache=7)

    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_usd"] == 2.5
    assert d["cost_partial"] is True
    assert (d["in_tokens"], d["out_tokens"], d["cache_read_tokens"]) == (100, 20, 7)


@pytest.mark.asyncio
async def test_cost_partial_falsch_wenn_alles_bepreist(client, db):
    user, proj, issue = await buehne(db)
    r1 = await lauf(db, issue, agent="developer")
    await posten(db, r1, cost=1.0, priced=True)
    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_partial"] is False

    # Ein einziger unbepreister Posten kippt die ganze Rolle auf „mindestens".
    await posten(db, r1, cost=0.0, priced=False)
    d = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert d["cost_partial"] is True


@pytest.mark.asyncio
async def test_kosten_ueberleben_den_lauf(client, db):
    """Gruppiert wird nach `cost_entries.agent`, nicht nach `runs.agent`: `run_id` ist
    `SET NULL`, ein Posten überlebt die Lauflöschung. Über `runs.agent` gerechnet
    verschwände die Rechnung mit dem Lauf."""
    user, proj, issue = await buehne(db)
    r1 = await lauf(db, issue, agent="gameproj-operator")
    await posten(db, r1, cost=3.0)
    await db.delete(r1)
    await db.commit()

    row = rolle((await client.get("/office/agents", headers=auth(user))).json(),
                "gameproj-operator")
    assert row["runs"] == 0
    assert row["cost_usd"] == 3.0
    assert row["cost_partial"] is True


# ── Werkzeugtabelle ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_werkzeuge_reihenfolge_und_kappung(client, db):
    user, proj, issue = await buehne(db)
    r1 = await lauf(db, issue, agent="developer")
    seq = 0
    for tool, n in (("fs_read", 5), ("codegraph", 3), ("fs_list", 2), ("check", 1)):
        for _ in range(n):
            seq += 1
            await schritt(db, r1, tool=tool, ok=(tool != "check"), seq=seq)
    # Ein Schritt ohne Werkzeug (Modellzug) gehört nicht in die Tabelle.
    await schritt(db, r1, seq=99)

    voll = rolle((await client.get("/office/agents", headers=auth(user))).json(), "developer")
    assert [t["tool"] for t in voll["tools"]] == ["fs_read", "codegraph", "fs_list", "check"]
    assert voll["tools"][0] == {"tool": "fs_read", "n": 5, "ok": 5, "failed": 0}
    assert voll["tools"][3] == {"tool": "check", "n": 1, "ok": 0, "failed": 1}

    kurz = rolle((await client.get("/office/agents?tool_limit=2",
                                   headers=auth(user))).json(), "developer")
    assert [t["tool"] for t in kurz["tools"]] == ["fs_read", "codegraph"]


# ── Projekt- gegen globale Akte ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_projektakte_zeigt_nur_das_projekt(client, db):
    user, proj, issue = await buehne(db)
    zweit = await make_project(db, "BBB", "Beta")
    await add_member(db, zweit, user, ProjectRole.viewer)
    typ = IssueType(project_id=zweit.id, name="Aufgabe")
    status = WorkflowStatus(project_id=zweit.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=zweit.id, last_number=0)])
    await db.commit()
    issue2 = Issue(project_id=zweit.id, number=1, key="BBB-1", type_id=typ.id,
                   status_id=status.id, summary="Anderswo", reporter_id=user.id, rank="1")
    db.add(issue2)
    await db.commit()

    await lauf(db, issue, agent="developer")
    await lauf(db, issue2, agent="news", status="failed")

    eins = (await client.get(f"/projects/{proj.id}/office/agents", headers=auth(user))).json()
    assert [a["agent"] for a in eins["agents"]] == ["developer"]

    global_ = (await client.get("/office/agents", headers=auth(user))).json()
    assert {a["agent"] for a in global_["agents"]} == {"developer", "news"}
