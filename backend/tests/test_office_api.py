"""Die Lese-API des Büros: was eine Session ist, wer sie sehen darf, was sie liefert.

Der Schwerpunkt liegt auf den beiden Stellen, an denen eine neue Lesefläche gefährlich
wird: **Sichtbarkeit** (fremde Projekte dürfen nicht einmal ihre Existenz verraten — 404,
nie 403) und **Reihenfolge** (die Ansicht spult nach `seq` zurück; eine falsch sortierte
oder still gekappte Antwort zeigt einen Raum, den es so nie gab).

Alle Läufe hier tragen `kind=''`-Schritte, also den ALTDATEN-Pfad. Das ist Absicht: die
API muss am ersten Tag auf dem Bestand funktionieren, und was schon auf Altzeilen läuft,
läuft auf den instrumentierten Zeilen von Welle B erst recht.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.api import office as rt_api
from app.main import api
from app.models.agents import Run, RunStep
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def router_registriert():
    """Welle C hängt ihren Router nicht selbst in `main.py` ein (zwei Wellen arbeiten
    parallel an der Datei). Für die Tests wird er hier registriert — idempotent, damit
    er nach der Registrierung in `main.py` nicht ein zweites Mal landet."""
    if not any(getattr(r, "path", "") == "/office/sessions" for r in api.routes):
        api.include_router(rt_api.router)


# ── Testdaten ────────────────────────────────────────────────────────────────

async def ticket(db, projekt, nummer: int = 1, summary: str = "Tu was") -> Issue:
    typ = IssueType(project_id=projekt.id, name="Aufgabe")
    status = WorkflowStatus(project_id=projekt.id, name="To Do", category=StatusCategory.todo)
    db.add_all([typ, status, IssueCounter(project_id=projekt.id, last_number=0)])
    await db.commit()
    i = Issue(project_id=projekt.id, number=nummer, key=f"{projekt.key}-{nummer}",
              type_id=typ.id, status_id=status.id, summary=summary, reporter_id=1, rank="1")
    db.add(i)
    await db.commit()
    return i


async def lauf(db, *, issue=None, projekt=None, owner=None, agent="developer",
               status="success", parent=None, spawn_depth=0, minuten=5) -> Run:
    r = Run(
        issue_id=issue.id if issue else None,
        project_id=projekt.id if projekt else (issue.project_id if issue else None),
        owner_id=owner.id if owner else None,
        agent=agent, phase="execute", provider="claude_code", model="sonnet",
        status=status, parent_run_id=parent.id if parent else None, spawn_depth=spawn_depth,
        started_at=NOW - dt.timedelta(minutes=minuten),
        finished_at=None if status == "running" else NOW - dt.timedelta(minutes=minuten - 1),
    )
    db.add(r)
    await db.commit()
    return r


async def schritte(db, run: Run, anzahl: int) -> list[RunStep]:
    """`anzahl` Altzeilen (kind='') — je Zeile genau EIN Ereignis."""
    rows = [RunStep(run_id=run.id, seq=i + 1, role="assistant", content=f"Schritt {i + 1}",
                    created_at=NOW - dt.timedelta(minutes=4, seconds=anzahl - i))
            for i in range(anzahl)]
    db.add_all(rows)
    await db.commit()
    return rows


def sids(payload: dict) -> set[str]:
    return {s["sid"] for s in payload["sessions"]}


# ── Sessionliste je Projekt ──────────────────────────────────────────────────

async def test_projektliste_zeigt_nur_dieses_projekt(client, db):
    """Der Reiter eines Projekts ist der Raum dieses Projekts — auch für jemanden, der
    das Nachbarprojekt genauso sehen dürfte. Die Liste filtert nach Zugehörigkeit, nicht
    nach Berechtigung."""
    user = await make_user(db, "anna")
    a = await make_project(db, "AAA", "Alpha")
    b = await make_project(db, "BBB", "Beta")
    await add_member(db, a, user, ProjectRole.member)
    await add_member(db, b, user, ProjectRole.member)
    ia, ib = await ticket(db, a), await ticket(db, b)
    ra, rb = await lauf(db, issue=ia), await lauf(db, issue=ib)
    await schritte(db, ra, 3)
    await schritte(db, rb, 3)

    r = await client.get(f"/projects/{a.id}/office/sessions", headers=auth(user))
    assert r.status_code == 200, r.text
    assert sids(r.json()) == {f"issue:{ia.id}"}
    assert f"issue:{ib.id}" not in sids(r.json())

    session = r.json()["sessions"][0]
    assert session["kind"] == "issue" and session["ref"] == ia.id
    assert session["project_key"] == "AAA" and session["issue_key"] == "AAA-1"
    assert session["runs"] == 1 and session["events"] == 3 and session["purged"] is False
    assert r.json()["live_window_ms"] == 90_000


async def test_nichtmitglied_bekommt_404_statt_403(client, db):
    """Ein fremdes Projekt existiert für den Fremden nicht. Ein 403 wäre die Auskunft
    „das Projekt gibt es" — genau die, die `deps.build_access` überall verweigert."""
    owner = await make_user(db, "owner")
    fremd = await make_user(db, "fremd")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, owner, ProjectRole.owner)

    r = await client.get(f"/projects/{proj.id}/office/sessions", headers=auth(fremd))
    assert r.status_code == 404


# ── Globale Sessionliste ─────────────────────────────────────────────────────

async def test_globale_liste_zeigt_eigene_projekte_und_eigene_projektlose_laeufe(client, db):
    """Die Vollbildseite zeigt beides: was über ein Projekt sichtbar ist UND die eigenen
    Läufe ohne Projekt (Assistent, Job). Der projektlose Lauf eines anderen bleibt außen —
    für ihn gibt es keinen Projektraum, über den er je sichtbar würde."""
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    eigen = await lauf(db, issue=issue)
    meins = await lauf(db, owner=anna, agent="assistant")
    fremd = await lauf(db, owner=bert, agent="assistant")
    for run in (eigen, meins, fremd):
        await schritte(db, run, 2)

    r = await client.get("/office/sessions", headers=auth(anna))
    assert r.status_code == 200, r.text
    assert sids(r.json()) == {f"issue:{issue.id}", f"run:{meins.id}"}
    assert f"run:{fremd.id}" not in sids(r.json())


async def test_admin_sieht_alles(client, db):
    admin = await make_user(db, "chef", admin=True)
    bert = await make_user(db, "bert")
    proj = await make_project(db, "AAA", "Alpha")
    issue = await ticket(db, proj)
    a = await lauf(db, issue=issue)
    b = await lauf(db, owner=bert, agent="assistant")
    await schritte(db, a, 2)
    await schritte(db, b, 2)

    r = await client.get("/office/sessions", headers=auth(admin))
    assert sids(r.json()) == {f"issue:{issue.id}", f"run:{b.id}"}


async def test_project_id_verengt_und_autorisiert_nicht(client, db):
    """`?project_id=` ist ein Filter, kein Schlüssel: ein fremdes Projekt einzutragen
    liefert Stille, keinen Zugang."""
    anna = await make_user(db, "anna")
    meins = await make_project(db, "AAA", "Alpha")
    fremd = await make_project(db, "BBB", "Beta")
    await add_member(db, meins, anna, ProjectRole.member)
    i1, i2 = await ticket(db, meins), await ticket(db, fremd)
    await schritte(db, await lauf(db, issue=i1), 2)
    await schritte(db, await lauf(db, issue=i2), 2)

    r = await client.get(f"/office/sessions?project_id={fremd.id}", headers=auth(anna))
    assert r.status_code == 200 and r.json()["sessions"] == []
    r = await client.get(f"/office/sessions?project_id={meins.id}", headers=auth(anna))
    assert sids(r.json()) == {f"issue:{i1.id}"}


# ── Ereignisse ───────────────────────────────────────────────────────────────

async def test_ereignisse_streng_nach_seq_und_after_seq_schliesst_aus(client, db):
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    run = await lauf(db, issue=issue)
    rows = await schritte(db, run, 4)

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(anna))
    assert r.status_code == 200, r.text
    body = r.json()
    seqs = [e["seq"] for e in body["events"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert body["truncated"] is False and body["purged"] is False
    assert body["seq_from"] == seqs[0] and body["seq_to"] == seqs[-1]
    # Altlauf ohne eigene Grenzzeilen: die Grenzen kommen aus `run_boundary_events`.
    kinds = [e["kind"] for e in body["events"]]
    assert kinds[0] == "session_seen" and "run_start" in kinds and kinds[-1] == "run_end"

    grenze = rows[1].id * 4 + 1     # das Hauptereignis der zweiten Zeile
    r = await client.get(
        f"/office/sessions/issue/{issue.id}/events?after_seq={grenze}", headers=auth(anna))
    weiter = [e["seq"] for e in r.json()["events"]]
    assert weiter and min(weiter) > grenze
    assert weiter == sorted(weiter)
    # Die Kopfzeile kommt nur beim Vollabruf — sonst käme sie beim Nachfassen mit neuer
    # `seq` ein zweites Mal in den Recorder.
    assert "session_seen" not in [e["kind"] for e in r.json()["events"]]


async def test_kappung_meldet_truncated_und_behaelt_den_roster(client, db):
    """Gekappt wird vom ÄLTESTEN Ende — der Raum soll die Gegenwart zeigen. Damit fallen
    zuerst die `run_start`-Ereignisse weg; dass trotzdem alle Agenten im Raum stehen,
    ist genau die Aufgabe von `agents[]`."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    eltern = await lauf(db, issue=issue, agent="developer")
    kind = await lauf(db, issue=issue, agent="reviewer", parent=eltern, spawn_depth=1)
    alt = await schritte(db, eltern, 3)
    await schritte(db, kind, 3)

    voll = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(anna))
    aeltestes = min(e["seq"] for e in voll.json()["events"])

    r = await client.get(f"/office/sessions/issue/{issue.id}/events?limit=2",
                         headers=auth(anna))
    body = r.json()
    assert body["truncated"] is True
    assert body["seq_from"] > aeltestes
    assert all(e["seq"] >= body["seq_from"] for e in body["events"])
    # Der abgeschnittene Elternlauf ist im Log nicht mehr zu sehen (die Kopfzeile trägt
    # seine `run_id`, weil sie am Wurzellauf hängt — sie ist kein Schritt des Laufs).
    assert eltern.id not in {e["run_id"] for e in body["events"] if e["kind"] != "session_seen"}
    assert alt[0].id * 4 + 1 < body["seq_from"]
    # … steht aber vollständig im Roster.
    assert {a["run_id"] for a in body["agents"]} == {eltern.id, kind.id}
    assert {a["agent"] for a in body["agents"]} == {"developer", "reviewer"}


async def test_run_session_eigentuemer_fremder_admin(client, db):
    """Ein projektloser Lauf gehört seinem Eigentümer und dem Admin — sonst niemandem,
    und für sonst niemanden existiert er."""
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    chef = await make_user(db, "chef", admin=True)
    run = await lauf(db, owner=anna, agent="assistant")
    await schritte(db, run, 2)

    pfad = f"/office/sessions/run/{run.id}/events"
    assert (await client.get(pfad, headers=auth(anna))).status_code == 200
    assert (await client.get(pfad, headers=auth(bert))).status_code == 404
    assert (await client.get(pfad, headers=auth(chef))).status_code == 200


async def test_kindlauf_ist_nicht_selbst_adressierbar(client, db):
    """Nur Wurzeln sind eine `run:`-Adresse. Hätte ein Kindlauf eine eigene, gäbe es zwei
    Räume für denselben Baum und der Link entschiede, was man sieht."""
    anna = await make_user(db, "anna")
    wurzel = await lauf(db, owner=anna, agent="assistant")
    kind = await lauf(db, owner=anna, agent="reviewer", parent=wurzel, spawn_depth=1)
    await schritte(db, kind, 2)

    r = await client.get(f"/office/sessions/run/{kind.id}/events", headers=auth(anna))
    assert r.status_code == 404
    r = await client.get(f"/office/sessions/run/{wurzel.id}/events", headers=auth(anna))
    assert {a["run_id"] for a in r.json()["agents"]} == {wurzel.id, kind.id}


async def test_aufgeraeumte_session_meldet_purged(client, db):
    """Ticket steht, Läufe sind der Aufbewahrung zum Opfer gefallen. Eine 404 wäre hier
    eine Lüge — den Raum gab es, und die UI soll das sagen dürfen."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(anna))
    assert r.status_code == 200, r.text
    assert r.json()["purged"] is True
    assert r.json()["events"] == [] and r.json()["agents"] == []
    assert (await db.execute(select(Run))).scalars().all() == []


async def test_issue_sid_enthaelt_delegierte_kindlaeufe(client, db):
    """Der Raum eines Tickets ist der ganze Laufbaum: Planung, Ausführung UND jeder
    delegierte Unteragent. Ein eigenes Büro je Unteragent wäre die falsche Einheit."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    plan = await lauf(db, issue=issue, agent="planner", minuten=9)
    exe = await lauf(db, issue=issue, agent="developer", minuten=7)
    sub = await lauf(db, issue=issue, agent="reviewer", parent=exe, spawn_depth=1, minuten=6)
    for run in (plan, exe, sub):
        await schritte(db, run, 2)

    r = await client.get(f"/office/sessions/issue/{issue.id}/events", headers=auth(anna))
    roster = {a["agent"]: a for a in r.json()["agents"]}
    assert set(roster) == {"planner", "developer", "reviewer"}
    assert roster["reviewer"]["spawn_depth"] == 1
    assert roster["reviewer"]["parent_run_id"] == exe.id

    liste = await client.get(f"/projects/{proj.id}/office/sessions", headers=auth(anna))
    session = liste.json()["sessions"][0]
    assert session["runs"] == 3 and session["agents"] == 3 and session["events"] == 6


# ── Ereignisse ALLER Sitzungen (`GET /office/events`) ─────────────────────────

async def test_alle_ereignisse_mischen_sitzungen_in_ein_log(client, db):
    """Der Raum der globalen Seite: mehrere Sitzungen, EIN Log, streng nach `seq`.

    Das trägt nur, weil `seq` aus `run_steps.id` kommt — einer SERIAL-Spalte, die über
    Läufe und Projekte hinweg monoton ist. Dass die Folge streng aufsteigend und
    doppelfrei ist, ist deshalb keine Kosmetik: `Recorder.push` entdoppelt genau über
    diese Zahl und verwürfe sonst still ein Ereignis.
    """
    anna = await make_user(db, "anna")
    a = await make_project(db, "AAA", "Alpha")
    b = await make_project(db, "BBB", "Beta")
    await add_member(db, a, anna, ProjectRole.member)
    await add_member(db, b, anna, ProjectRole.member)
    ia, ib = await ticket(db, a), await ticket(db, b)
    ra = await lauf(db, issue=ia, agent="developer")
    rb = await lauf(db, issue=ib, agent="architect")
    eigen = await lauf(db, owner=anna, agent="assistant")
    for run in (ra, rb, eigen):
        await schritte(db, run, 3)

    r = await client.get("/office/events", headers=auth(anna))
    assert r.status_code == 200, r.text
    body = r.json()

    seqs = [e["seq"] for e in body["events"]]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    assert {e["sid"] for e in body["events"]} == {
        f"issue:{ia.id}", f"issue:{ib.id}", f"run:{eigen.id}"}
    assert body["sessions"] == 3 and body["runs"] == 3
    assert body["seq_from"] == seqs[0] and body["seq_to"] == seqs[-1]
    assert body["count"] == len(seqs) and body["truncated"] is False
    # Statt einer `sid` trägt die Antwort das Fenster.
    assert "sid" not in body
    assert body["since_hours"] == rt_api.EVENTS_SINCE_HOURS_DEFAULT
    assert body["window_from"] < body["window_to"]
    # Keine Kopfzeile: vierzehn Titel für einen Raum wären vierzehn Widersprüche.
    assert "session_seen" not in {e["kind"] for e in body["events"]}
    # Jede Figur kommt herein und geht wieder — über alle drei Sitzungen hinweg.
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_start"} == {
        ra.id, rb.id, eigen.id}
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_end"} == {
        ra.id, rb.id, eigen.id}

    roster = {a_["run_id"]: a_ for a_ in body["agents"]}
    assert set(roster) == {ra.id, rb.id, eigen.id}
    # Ohne `project_key`/`issue_key` fiele in der Kopfzeile jede Figur in „(ohne Projekt)"
    # und die Sitzungsreiter blieben unsichtbar.
    assert roster[ra.id]["project_key"] == "AAA" and roster[ra.id]["issue_key"] == "AAA-1"
    assert roster[rb.id]["project_key"] == "BBB"
    assert roster[eigen.id]["project_key"] == "" and roster[eigen.id]["project_id"] is None


async def test_alle_ereignisse_zeigen_nur_erlaubtes(client, db):
    """Dieselbe Sichtbarkeitsmenge wie `/office/sessions` — es gibt genau eine Definition
    von „darf sehen" (`_visible_runs`). Der projektlose Lauf eines anderen bleibt draußen,
    der Admin sieht beides."""
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    chef = await make_user(db, "chef", admin=True)
    meins = await make_project(db, "AAA", "Alpha")
    fremd = await make_project(db, "BBB", "Beta")
    await add_member(db, meins, anna, ProjectRole.member)
    i1, i2 = await ticket(db, meins), await ticket(db, fremd)
    r1 = await lauf(db, issue=i1)
    r2 = await lauf(db, issue=i2)
    r3 = await lauf(db, owner=bert, agent="assistant")
    for run in (r1, r2, r3):
        await schritte(db, run, 2)

    body = (await client.get("/office/events", headers=auth(anna))).json()
    assert {e["run_id"] for e in body["events"]} == {r1.id}
    assert {a_["run_id"] for a_ in body["agents"]} == {r1.id}

    alles = (await client.get("/office/events", headers=auth(chef))).json()
    assert {a_["run_id"] for a_ in alles["agents"]} == {r1.id, r2.id, r3.id}


async def test_alle_ereignisse_project_id_verengt_und_autorisiert_nicht(client, db):
    """`?project_id=` ist ein Filter, kein Schlüssel. Ein fremdes Projekt liefert Stille —
    keinen Zugang und auch keine 403, die dessen Existenz verriete."""
    anna = await make_user(db, "anna")
    meins = await make_project(db, "AAA", "Alpha")
    fremd = await make_project(db, "BBB", "Beta")
    await add_member(db, meins, anna, ProjectRole.member)
    await add_member(db, fremd, anna, ProjectRole.member)
    i1, i2 = await ticket(db, meins), await ticket(db, fremd)
    r1, r2 = await lauf(db, issue=i1), await lauf(db, issue=i2)
    await schritte(db, r1, 2)
    await schritte(db, r2, 2)

    beide = (await client.get("/office/events", headers=auth(anna))).json()
    assert {a_["run_id"] for a_ in beide["agents"]} == {r1.id, r2.id}

    eng = (await client.get(f"/office/events?project_id={meins.id}", headers=auth(anna))).json()
    assert {a_["run_id"] for a_ in eng["agents"]} == {r1.id}

    bert = await make_user(db, "bert")
    stille = await client.get(f"/office/events?project_id={meins.id}", headers=auth(bert))
    assert stille.status_code == 200
    assert stille.json()["events"] == [] and stille.json()["agents"] == []


async def test_alle_ereignisse_klemmen_das_run_start_auf_den_fensteranfang(client, db):
    """Ein Lauf, der VOR dem Fenster begann, bekommt seine `run_start`-Grenze mit
    `run.started_at` — also mit einem Zeitstempel von gestern. Ungeklemmt zöge die
    Zeitleiste den ganzen Raum dorthin auf, und das sähe aus wie ein Engine-Fehler."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    # Startet vor 30 Stunden, arbeitet aber noch — die Schritte liegen im Fenster.
    alt = await lauf(db, issue=issue, minuten=30 * 60)
    await schritte(db, alt, 2)

    body = (await client.get("/office/events?since_hours=12", headers=auth(anna))).json()
    start = next(e for e in body["events"] if e["kind"] == "run_start")
    geklemmt = dt.datetime.fromisoformat(start["ts"].replace("Z", "+00:00"))
    assert geklemmt >= NOW - dt.timedelta(hours=12, minutes=2)
    assert geklemmt < NOW - dt.timedelta(hours=11)


async def test_alle_ereignisse_ohne_seq_kollision_am_laufuebergang(client, db):
    """Zwei Läufe mit benachbarten Zeilen-IDs: das `run_end` des einen (`letzte*4+3`) und
    das `run_start` des nächsten (`erste*4-1`) sind DIESELBE Zahl. Über Sitzungen hinweg
    ist das der Normalfall, nicht der Ausreißer — und `Recorder.push` verwürfe das zweite
    Ereignis still. Also muss die Antwort die Kollision selbst auflösen."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    a = await make_project(db, "BBB", "Beta")
    await add_member(db, a, anna, ProjectRole.member)
    i1, i2 = await ticket(db, proj), await ticket(db, a)
    erst = await lauf(db, issue=i1, agent="developer")
    zeilen_erst = await schritte(db, erst, 2)
    zweit = await lauf(db, issue=i2, agent="architect")
    zeilen_zweit = await schritte(db, zweit, 2)
    # Der Aufbau muss die Kollision überhaupt erzeugen, sonst prüft der Test nichts.
    assert zeilen_zweit[0].id == zeilen_erst[-1].id + 1

    body = (await client.get("/office/events", headers=auth(anna))).json()
    seqs = [e["seq"] for e in body["events"]]
    assert len(seqs) == len(set(seqs)) and seqs == sorted(seqs)
    grenzen = [(e["kind"], e["run_id"]) for e in body["events"]
               if e["kind"] in ("run_start", "run_end")]
    assert ("run_end", erst.id) in grenzen and ("run_start", zweit.id) in grenzen
    # Das Ende geht dem nächsten Anfang voraus: erst geht jemand, dann kommt der Nächste.
    assert grenzen.index(("run_end", erst.id)) < grenzen.index(("run_start", zweit.id))


async def test_alle_ereignisse_kappen_vom_aeltesten_ende_und_behalten_den_roster(client, db):
    """Gekappt wird vom ÄLTESTEN Ende — der Raum zeigt die Gegenwart.

    Damit fällt zuerst das `run_start` weg, und ohne Gegenmaßnahme fehlte die Figur im
    Raum, obwohl sie noch arbeitet. Zwei Dinge fangen das: die Fenstergrenzen werden aus
    den GELADENEN Zeilen gerechnet (der Lauf bekommt also ein frisches `run_start` an
    seinem ersten sichtbaren Schritt), und `agents[]` kommt aus `runs`, nicht aus den
    Ereignissen.

    Wer gar keinen sichtbaren Schritt mehr hat, steht auch nicht im Roster — anders als
    bei `session_events` ist der Roster hier die Besetzung des **gezeigten** Fensters, und
    die Kopfzeile soll die Summe über das zählen, was im Raum steht.
    """
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    zwei = await make_project(db, "BBB", "Beta")
    drei = await make_project(db, "CCC", "Gamma")
    await add_member(db, zwei, anna, ProjectRole.member)
    await add_member(db, drei, anna, ProjectRole.member)
    i1, i2, i3 = await ticket(db, proj), await ticket(db, zwei), await ticket(db, drei)
    ganz_raus = await lauf(db, issue=i1, agent="planner")
    halb = await lauf(db, issue=i2, agent="developer")
    neu_ = await lauf(db, issue=i3, agent="architect")
    await schritte(db, ganz_raus, 3)
    zeilen_halb = await schritte(db, halb, 3)
    await schritte(db, neu_, 3)

    voll = (await client.get("/office/events", headers=auth(anna))).json()
    aeltestes = min(e["seq"] for e in voll["events"])

    # Vier Zeilen: der letzte Schritt von `halb` plus die drei von `neu_`.
    body = (await client.get("/office/events?limit=4", headers=auth(anna))).json()
    assert body["truncated"] is True
    assert body["seq_from"] > aeltestes
    assert all(e["seq"] >= body["seq_from"] for e in body["events"])
    assert zeilen_halb[0].id * 4 + 1 < body["seq_from"]

    # `halb` hat noch einen sichtbaren Schritt — also kommt die Figur herein, obwohl ihr
    # echtes `run_start` unter der Kappung liegt.
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_start"} == {
        halb.id, neu_.id}
    assert {a_["run_id"] for a_ in body["agents"]} == {halb.id, neu_.id}
    assert ganz_raus.id not in {e["run_id"] for e in body["events"]}


async def test_alle_ereignisse_halten_sich_ans_fenster(client, db):
    """`since_hours` ist die ganze Aussage: was älter ist, gehört nicht in den Raum."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    run = await lauf(db, issue=issue)
    zeilen = await schritte(db, run, 2)
    for zeile in zeilen:
        zeile.created_at = NOW - dt.timedelta(hours=30)
        db.add(zeile)
    await db.commit()

    eng = (await client.get("/office/events?since_hours=12", headers=auth(anna))).json()
    assert eng["events"] == [] and eng["agents"] == [] and eng["sessions"] == 0

    weit = (await client.get("/office/events?since_hours=48", headers=auth(anna))).json()
    assert {a_["run_id"] for a_ in weit["agents"]} == {run.id}
    assert weit["since_hours"] == 48
