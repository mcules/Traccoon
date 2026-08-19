"""The read API of the office: what a session is, who may see it, what it delivers.

The focus lies on the two places where a new reading surface becomes dangerous:
**visibility** (foreign projects must not even reveal their existence: 404, never 403) and
**order** (the view rewinds by `seq`; a wrongly sorted or silently truncated answer shows a
room that never existed that way).

All runs here carry `kind=''` steps, so the LEGACY path. That is deliberate: the API has to
work on the existing data on the first day, and what already runs on old rows runs on the
instrumented rows all the more.
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
    """This wave does not hang its router into `main.py` itself (two waves work on the file
    in parallel). For the tests it is registered here, idempotently, so that it does not land
    a second time after the registration in `main.py`."""
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
    """The tab of a project is the room of this project, even for somebody who would be
    allowed to see the neighbouring project just as well. The list filters by affiliation,
    not by permission."""
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
    """A foreign project does not exist for the stranger. A 403 would be the statement "the
    project exists", exactly the one `deps.build_access` refuses everywhere."""
    owner = await make_user(db, "owner")
    fremd = await make_user(db, "fremd")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, owner, ProjectRole.owner)

    r = await client.get(f"/projects/{proj.id}/office/sessions", headers=auth(fremd))
    assert r.status_code == 404


# ── Globale Sessionliste ─────────────────────────────────────────────────────

async def test_globale_liste_zeigt_eigene_projekte_und_eigene_projektlose_laeufe(client, db):
    """The full screen page shows both: what is visible over a project AND one's own runs
    without a project (assistant, job). The project-less run of somebody else stays outside:
    for it there is no project room over which it would ever become visible."""
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
    """`?project_id=` is a filter, not a key: entering a foreign project yields silence, no access."""
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
    # Legacy run without boundary rows of its own: the boundaries come from `run_boundary_events`.
    kinds = [e["kind"] for e in body["events"]]
    assert kinds[0] == "session_seen" and "run_start" in kinds and kinds[-1] == "run_end"

    grenze = rows[1].id * 4 + 1     # the main event of the second row
    r = await client.get(
        f"/office/sessions/issue/{issue.id}/events?after_seq={grenze}", headers=auth(anna))
    weiter = [e["seq"] for e in r.json()["events"]]
    assert weiter and min(weiter) > grenze
    assert weiter == sorted(weiter)
    # The header comes only with the full fetch; otherwise it would come into the recorder a
    # second time with a new `seq` while following up.
    assert "session_seen" not in [e["kind"] for e in r.json()["events"]]


async def test_kappung_meldet_truncated_und_behaelt_den_roster(client, db):
    """Truncation happens from the OLDEST end: the room should show the present. With that
    the `run_start` events fall away first, and that all agents still stand in the room is
    exactly the job of `agents[]`."""
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
    # The truncated parent run can no longer be seen in the log (the header carries its
    # `run_id`, because it hangs off the root run: it is not a step of the run).
    assert eltern.id not in {e["run_id"] for e in body["events"] if e["kind"] != "session_seen"}
    assert alt[0].id * 4 + 1 < body["seq_from"]
    # … but stands completely in the roster.
    assert {a["run_id"] for a in body["agents"]} == {eltern.id, kind.id}
    assert {a["agent"] for a in body["agents"]} == {"developer", "reviewer"}


async def test_run_session_eigentuemer_fremder_admin(client, db):
    """A project-less run belongs to its owner and to the admin, to nobody else, and for
    nobody else does it exist."""
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
    """Only roots are a `run:` address. If a child run had one of its own, there would be two
    rooms for the same tree and the link would decide what one sees."""
    anna = await make_user(db, "anna")
    wurzel = await lauf(db, owner=anna, agent="assistant")
    kind = await lauf(db, owner=anna, agent="reviewer", parent=wurzel, spawn_depth=1)
    await schritte(db, kind, 2)

    r = await client.get(f"/office/sessions/run/{kind.id}/events", headers=auth(anna))
    assert r.status_code == 404
    r = await client.get(f"/office/sessions/run/{wurzel.id}/events", headers=auth(anna))
    assert {a["run_id"] for a in r.json()["agents"]} == {wurzel.id, kind.id}


async def test_aufgeraeumte_session_meldet_purged(client, db):
    """The ticket stands, the runs have fallen to the retention. A 404 would be a lie here:
    the room existed, and the UI should be allowed to say so."""
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
    """The room of a ticket is the whole run tree: planning, execution AND every delegated
    sub-agent. An office of its own per sub-agent would be the wrong unit."""
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
    """The room of the global page: several sessions, ONE log, strictly by `seq`.

    That only carries because `seq` comes from `run_steps.id`, a SERIAL column that is
    monotonic across runs and projects. That the sequence is strictly ascending and duplicate
    free is therefore not cosmetics: `Recorder.push` deduplicates over exactly this number
    and would otherwise silently discard an event.
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
    # Instead of a `sid` the answer carries the window.
    assert "sid" not in body
    assert body["since_hours"] == rt_api.EVENTS_SINCE_HOURS_DEFAULT
    assert body["window_from"] < body["window_to"]
    # No header: fourteen titles for one room would be fourteen contradictions.
    assert "session_seen" not in {e["kind"] for e in body["events"]}
    # Every figure comes in and leaves again, across all three sessions.
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_start"} == {
        ra.id, rb.id, eigen.id}
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_end"} == {
        ra.id, rb.id, eigen.id}

    roster = {a_["run_id"]: a_ for a_ in body["agents"]}
    assert set(roster) == {ra.id, rb.id, eigen.id}
    # Without `project_key`/`issue_key` every figure would fall into "(without a project)" in
    # the header and the session tabs would stay invisible.
    assert roster[ra.id]["project_key"] == "AAA" and roster[ra.id]["issue_key"] == "AAA-1"
    assert roster[rb.id]["project_key"] == "BBB"
    assert roster[eigen.id]["project_key"] == "" and roster[eigen.id]["project_id"] is None


async def test_alle_ereignisse_zeigen_nur_erlaubtes(client, db):
    """The same visibility set as `/office/sessions`: there is exactly one definition of "may
    see" (`_visible_runs`). The project-less run of somebody else stays outside, and the admin
    sees both."""
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
    """`?project_id=` is a filter, not a key. A foreign project yields silence: no access and
    no 403 either, which would reveal its existence."""
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
    """A run that began BEFORE the window gets its `run_start` boundary with `run.started_at`,
    so with a timestamp from yesterday. Unclamped, the timeline would pull the whole room
    there, and that would look like an engine bug."""
    anna = await make_user(db, "anna")
    proj = await make_project(db, "AAA", "Alpha")
    await add_member(db, proj, anna, ProjectRole.member)
    issue = await ticket(db, proj)
    # Starts 30 hours ago but is still working: the steps lie in the window.
    alt = await lauf(db, issue=issue, minuten=30 * 60)
    await schritte(db, alt, 2)

    body = (await client.get("/office/events?since_hours=12", headers=auth(anna))).json()
    start = next(e for e in body["events"] if e["kind"] == "run_start")
    geklemmt = dt.datetime.fromisoformat(start["ts"].replace("Z", "+00:00"))
    assert geklemmt >= NOW - dt.timedelta(hours=12, minutes=2)
    assert geklemmt < NOW - dt.timedelta(hours=11)


async def test_alle_ereignisse_ohne_seq_kollision_am_laufuebergang(client, db):
    """Two runs with neighbouring row ids: the `run_end` of one (`letzte*4+3`) and the
    `run_start` of the next (`erste*4-1`) are THE SAME number. Across sessions that is the
    normal case, not the outlier, and `Recorder.push` would silently discard the second event.
    So the answer has to resolve the collision itself."""
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
    # The setup has to produce the collision at all, because otherwise the test checks nothing.
    assert zeilen_zweit[0].id == zeilen_erst[-1].id + 1

    body = (await client.get("/office/events", headers=auth(anna))).json()
    seqs = [e["seq"] for e in body["events"]]
    assert len(seqs) == len(set(seqs)) and seqs == sorted(seqs)
    grenzen = [(e["kind"], e["run_id"]) for e in body["events"]
               if e["kind"] in ("run_start", "run_end")]
    assert ("run_end", erst.id) in grenzen and ("run_start", zweit.id) in grenzen
    # The end precedes the next start: first somebody leaves, then the next one comes.
    assert grenzen.index(("run_end", erst.id)) < grenzen.index(("run_start", zweit.id))


async def test_alle_ereignisse_kappen_vom_aeltesten_ende_und_behalten_den_roster(client, db):
    """Truncation happens from the OLDEST end: the room shows the present.

    With that the `run_start` falls away first, and without a countermeasure the figure would
    be missing in the room although it is still working. Two things catch that: the window
    boundaries are computed from the LOADED rows (so the run gets a fresh `run_start` at its
    first visible step), and `agents[]` comes from `runs`, not from the events.

    Whoever has no visible step left does not stand in the roster either: unlike with
    `session_events` the roster here is the cast of the **shown** window, and the header
    should count its sum over what stands in the room.
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

    # Four rows: the last step of `halb` plus the three of `neu_`.
    body = (await client.get("/office/events?limit=4", headers=auth(anna))).json()
    assert body["truncated"] is True
    assert body["seq_from"] > aeltestes
    assert all(e["seq"] >= body["seq_from"] for e in body["events"])
    assert zeilen_halb[0].id * 4 + 1 < body["seq_from"]

    # `halb` still has a visible step, so the figure comes in although its real `run_start`
    # lies below the truncation.
    assert {e["run_id"] for e in body["events"] if e["kind"] == "run_start"} == {
        halb.id, neu_.id}
    assert {a_["run_id"] for a_ in body["agents"]} == {halb.id, neu_.id}
    assert ganz_raus.id not in {e["run_id"] for e in body["events"]}


async def test_alle_ereignisse_halten_sich_ans_fenster(client, db):
    """`since_hours` is the whole statement: what is older does not belong in the room."""
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
