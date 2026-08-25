"""Bug reports: the way from a stranger's form to a ticket.

The interesting part is not the storing, it is who may do what. The reporting program has a
token and may file exactly one thing; everybody else needs a login; and a report becomes work
only when a person says so.
"""
import pytest

from app.models.bugs import BugSource
from app.models.enums import ProjectRole
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import bugs as svc


async def make_source(db, key="yaesuprog", project=None, limit=20):
    source = BugSource(key=key, name=key.title(), hourly_limit=limit,
                       project_id=project.id if project else None)
    token = svc.new_token()
    svc.set_token(source, token)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source, token


async def make_board(db, project):
    """A project can only carry a ticket with a type, a state and a counter."""
    db.add(IssueType(project_id=project.id, name="Bug"))
    db.add(WorkflowStatus(project_id=project.id, name="To do"))
    db.add(IssueCounter(project_id=project.id, last_number=0))
    await db.commit()


REPORT = {"title": "Channel 1 and 2 got FM-W with 96 MHz",
          "details": "Everything else fits.", "contact": "DN9MAK",
          "version": "0.1.0", "environment": "FTX-1, Chrome",
          "technical": "> MR00001;\n< MR00001145337500+000000412000;"}


@pytest.mark.asyncio
async def test_a_program_with_a_token_may_report(client, db):
    _, token = await make_source(db)

    answer = await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})

    assert answer.status_code == 201, answer.text
    assert answer.json()["number"].startswith("BUG-")


@pytest.mark.asyncio
async def test_without_a_valid_token_nothing_arrives(client, db):
    await make_source(db)

    answer = await client.post("/bugs/report", json=REPORT,
                               headers={"X-Bug-Token": "made-up"})

    assert answer.status_code == 401
    assert answer.json()["key"] == "err.bug_token_unknown"


@pytest.mark.asyncio
async def test_the_report_keeps_what_the_program_sent(client, db, helpers):
    _, token = await make_source(db)
    await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})
    person = await helpers.make_user(db, "hans")

    liste = await client.get("/bugs", headers=helpers.auth(person))

    assert liste.status_code == 200, liste.text
    bug = liste.json()[0]
    assert bug["title"] == REPORT["title"]
    assert bug["contact"] == "DN9MAK"
    assert bug["app"] == "yaesuprog"
    assert bug["status"] == "new"
    # The attachment is the reason these reports are worth anything.
    assert "MR00001" in bug["technical"]


@pytest.mark.asyncio
async def test_reading_reports_needs_a_login(client, db):
    _, token = await make_source(db)
    await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})

    assert (await client.get("/bugs")).status_code == 401


@pytest.mark.asyncio
async def test_the_hourly_ceiling_holds(client, db):
    _, token = await make_source(db, limit=2)

    codes = [(await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).status_code for _ in range(3)]

    assert codes == [201, 201, 429]


@pytest.mark.asyncio
async def test_a_report_becomes_a_ticket_and_says_so(client, db, helpers):
    project = await helpers.make_project(db, "ABC", "A project")
    await make_board(db, project)
    person = await helpers.make_user(db, "chef", admin=True)
    _, token = await make_source(db, project=project)
    await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    answer = await client.post(f"/bugs/{bug_id}/ticket", json={"project_id": project.id},
                               headers=helpers.auth(person))

    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "ticket"
    assert answer.json()["ticket"] == "ABC-1"
    # The ticket carries the report including the attachment: whoever works on it should not
    # have to go looking for where it came from.
    issue = (await db.execute(__import__("sqlalchemy").select(Issue))).scalars().first()
    assert "MR00001" in (issue.description or "")
    assert "DN9MAK" in (issue.description or "")


@pytest.mark.asyncio
async def test_without_rights_no_ticket_in_a_foreign_project(client, db, helpers):
    """A project one has no part in does not even exist: that is the house rule of
    `build_access` (strict isolation, a 404 instead of a 403), and a report must not become a
    keyhole into it."""
    project = await helpers.make_project(db, "ABC", "A project")
    await make_board(db, project)
    stranger = await helpers.make_user(db, "fremd")
    _, token = await make_source(db, project=project)
    await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})
    bug_id = (await client.get("/bugs", headers=helpers.auth(stranger))).json()[0]["id"]

    answer = await client.post(f"/bugs/{bug_id}/ticket", json={"project_id": project.id},
                               headers=helpers.auth(stranger))

    assert answer.status_code == 404


@pytest.mark.asyncio
async def test_a_judged_report_leaves_the_open_list(client, db, helpers):
    _, token = await make_source(db)
    await client.post("/bugs/report", json=REPORT, headers={"X-Bug-Token": token})
    person = await helpers.make_user(db, "hans")
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    await client.post(f"/bugs/{bug_id}/status", json={"status": "rejected"},
                      headers=helpers.auth(person))

    offen = await client.get("/bugs?state=open", headers=helpers.auth(person))
    assert offen.json() == []


@pytest.mark.asyncio
async def test_a_wish_is_not_a_fault(client, db, helpers):
    """Three kinds share the list: bug, wish, question. The words come from gameproj."""
    _, token = await make_source(db)
    for art, titel in (("feature", "A button for the CAT log"),
                       ("question", "Which baud rate does the FT-991 want?")):
        antwort = await client.post("/bugs/report", headers={"X-Bug-Token": token},
                                    json={**REPORT, "title": titel, "kind": art})
        assert antwort.status_code == 201, antwort.text
    person = await helpers.make_user(db, "hans")

    alle = (await client.get("/bugs", headers=helpers.auth(person))).json()
    wuensche = (await client.get("/bugs?kind=feature", headers=helpers.auth(person))).json()

    assert {b["kind"] for b in alle} == {"feature", "question"}
    assert [b["title"] for b in wuensche] == ["A button for the CAT log"]


@pytest.mark.asyncio
async def test_an_unknown_kind_is_still_a_report(client, db, helpers):
    """A program that sends nonsense must not lose the report."""
    _, token = await make_source(db)
    await client.post("/bugs/report", headers={"X-Bug-Token": token},
                      json={**REPORT, "kind": "erfundenes"})
    person = await helpers.make_user(db, "hans")

    assert (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["kind"] == "bug"


# ── The conversation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reporter_and_team_talk_to_each_other(client, db, helpers):
    """The point of the whole thing: an answer here arrives over there, and the other way round."""
    _, token = await make_source(db)
    app_kopf = {"X-Bug-Token": token}
    await client.post("/bugs/report", headers=app_kopf,
                      json={**REPORT, "external_ref": "spieler-7", "contact": "Uwe"})
    person = await helpers.make_user(db, "chef", admin=True)
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    hier = await client.post(f"/bugs/{bug_id}/posts", headers=helpers.auth(person),
                             json={"body": "Welche Firmware hast du?"})
    assert hier.status_code == 201, hier.text

    # Der Melder sieht die Antwort in seinem Programm ...
    faden = await client.get(f"/bugs/app/reports/{bug_id}?external_ref=spieler-7",
                             headers=app_kopf)
    assert faden.status_code == 200, faden.text
    assert [p["body"] for p in faden.json()["posts"]] == ["Welche Firmware hast du?"]

    # ... und antwortet selbst.
    zurueck = await client.post(f"/bugs/app/reports/{bug_id}/posts", headers=app_kopf,
                                json={"body": "1.08", "external_ref": "spieler-7", "author": "Uwe"})
    assert zurueck.status_code == 201, zurueck.text

    alles = await client.get(f"/bugs/{bug_id}/posts", headers=helpers.auth(person))
    assert [p["body"] for p in alles.json()] == ["Welche Firmware hast du?", "1.08"]
    assert [p["author"] for p in alles.json()] == ["Chef", "Uwe"]


@pytest.mark.asyncio
async def test_an_internal_note_never_leaves_the_house(client, db, helpers):
    """Der Grund, warum die Sichtbarkeit ein Parameter ist und kein nachtraeglicher Filter."""
    _, token = await make_source(db)
    app_kopf = {"X-Bug-Token": token}
    await client.post("/bugs/report", headers=app_kopf, json={**REPORT, "external_ref": "spieler-7"})
    person = await helpers.make_user(db, "chef", admin=True)
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    await client.post(f"/bugs/{bug_id}/posts", headers=helpers.auth(person),
                      json={"body": "Der hat das Profil nie gehabt.", "internal": True})
    await client.post(f"/bugs/{bug_id}/posts", headers=helpers.auth(person),
                      json={"body": "Schau bitte ins Menue 031.", "internal": False})

    draussen = await client.get(f"/bugs/app/reports/{bug_id}?external_ref=spieler-7",
                                headers=app_kopf)
    drinnen = await client.get(f"/bugs/{bug_id}/posts", headers=helpers.auth(person))

    assert [p["body"] for p in draussen.json()["posts"]] == ["Schau bitte ins Menue 031."]
    assert len(drinnen.json()) == 2


@pytest.mark.asyncio
async def test_a_program_sees_only_its_own_users(client, db, helpers):
    _, token = await make_source(db)
    app_kopf = {"X-Bug-Token": token}
    await client.post("/bugs/report", headers=app_kopf, json={**REPORT, "external_ref": "spieler-7"})
    person = await helpers.make_user(db, "chef", admin=True)
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    fremd = await client.get(f"/bugs/app/reports/{bug_id}?external_ref=spieler-8",
                             headers=app_kopf)

    assert fremd.status_code == 404          # wie ein Platz, den es nicht gibt
    meine = await client.get("/bugs/app/reports?external_ref=spieler-7", headers=app_kopf)
    assert [t["id"] for t in meine.json()] == [bug_id]
    assert (await client.get("/bugs/app/reports?external_ref=spieler-8",
                             headers=app_kopf)).json() == []


@pytest.mark.asyncio
async def test_an_answer_moves_the_report_off_new(client, db, helpers):
    _, token = await make_source(db)
    await client.post("/bugs/report", headers={"X-Bug-Token": token}, json=REPORT)
    person = await helpers.make_user(db, "chef", admin=True)
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]

    await client.post(f"/bugs/{bug_id}/posts", headers=helpers.auth(person),
                      json={"body": "Danke, schaue ich mir an."})

    bug = (await client.get(f"/bugs/{bug_id}", headers=helpers.auth(person))).json()
    assert bug["status"] == "seen"


@pytest.mark.asyncio
async def test_a_picture_belongs_to_its_entry(client, db, helpers):
    _, token = await make_source(db)
    app_kopf = {"X-Bug-Token": token}
    await client.post("/bugs/report", headers=app_kopf, json={**REPORT, "external_ref": "spieler-7"})
    person = await helpers.make_user(db, "chef", admin=True)
    bug_id = (await client.get("/bugs", headers=helpers.auth(person))).json()[0]["id"]
    post = (await client.post(f"/bugs/app/reports/{bug_id}/posts", headers=app_kopf,
                              json={"body": "So sieht es aus", "external_ref": "spieler-7"})).json()

    bild = b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 100
    hoch = await client.post(f"/bugs/app/posts/{post['id']}/images", headers=app_kopf,
                             files={"file": ("schirm.png", bild, "image/png")})

    assert hoch.status_code == 201, hoch.text
    faden = (await client.get(f"/bugs/app/reports/{bug_id}?external_ref=spieler-7",
                              headers=app_kopf)).json()
    assert faden["posts"][0]["images"][0]["filename"] == "schirm.png"
    runter = await client.get(f"/bugs/images/{hoch.json()['id']}", headers=helpers.auth(person))
    assert runter.content == bild
