"""The read API of the deployments: who may see it, and what it says about the existing data.

Two focal points, as with the office. **Visibility**: a new reading surface on a table that
nobody could read for 186 rows must not reveal the existence of foreign projects: 404, never
403. And **honesty**: `ok` is three valued, durations are `None` when the timestamp is
missing, and the full log text does not leave the list.

The test data deliberately reproduce the real stock: all seven status values including
`cancelled` (69 rows no code path writes), rows without `started_at`/`finished_at` (71 of
186) and the always identical 124 character guard text in every `failed`.
"""
import datetime as dt

import pytest

from app.api.deployments import LIMIT_MAX, LOG_HEAD_CHARS, SINCE_HOURS_MAX
from app.models.enums import ProjectRole, StatusCategory
from app.models.ops import Deployment
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)

# The seven status values that can occur in this table: six from the model plus `cancelled`,
# which only the existing data knows. Per row: the expected `phase` and `ok`.
STATUS_ERWARTUNG = [
    ("pending", "queued", None),
    ("pending-check", "queued", None),
    ("building", "running", None),
    ("ok", "done", True),
    ("failed", "done", False),
    ("rolledback", "done", False),
    ("cancelled", "aborted", None),
]


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


async def deploy(db, *, projekt=None, issue=None, status="ok", log="",
                 alter_stunden: int = 1, wartet_sekunden: float | None = 3.0,
                 dauer_sekunden: float | None = 12.5, self_deploy=False, check_only=False,
                 source="", stack_dir="/opt/docker/stacks/traccoon") -> Deployment:
    """One deployment row. `wartet_sekunden=None` means "never picked up" (no `started_at`),
    `dauer_sekunden=None` means "never finished" (no `finished_at`), exactly the two holes
    the existing data has."""
    created = NOW - dt.timedelta(hours=alter_stunden)
    started = None if wartet_sekunden is None else created + dt.timedelta(seconds=wartet_sekunden)
    finished = (None if (dauer_sekunden is None or started is None)
                else started + dt.timedelta(seconds=dauer_sekunden))
    d = Deployment(
        project_id=projekt.id if projekt else None,
        issue_id=issue.id if issue else None,
        stack_dir=stack_dir, status=status, log=log, source=source,
        self_deploy=self_deploy, check_only=check_only,
        created_at=created, started_at=started, finished_at=finished,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return d


# ── Visibility: 404 instead of 403 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_fremdes_projekt_ist_404_nicht_403(db, client):
    """A non-member gets a 404 on the project list. A 403 would be the statement "this
    project exists, you are only not allowed", exactly the statement a deployment list owes nobody.
    Deployment-Liste niemandem schuldet."""
    besitzer = await make_user(db, "besitzer")
    fremder = await make_user(db, "fremder")
    projekt = await make_project(db, "TRA", "Traccoon", inherit_members=False)
    await add_member(db, projekt, besitzer, ProjectRole.owner)
    await deploy(db, projekt=projekt)

    r = await client.get(f"/projects/{projekt.id}/deployments", headers=auth(fremder))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_viewer_genuegt(db, client):
    """Whoever merged wants to know whether it is out there, and is not necessarily a
    `maintainer`. The lowest role is enough for the list and the detail."""
    seher = await make_user(db, "seher")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, seher, ProjectRole.viewer)
    d = await deploy(db, projekt=projekt, log="fertig")

    liste = await client.get(f"/projects/{projekt.id}/deployments", headers=auth(seher))
    assert liste.status_code == 200
    assert liste.json()["count"] == 1

    detail = await client.get(f"/deployments/{d.id}", headers=auth(seher))
    assert detail.status_code == 200
    assert detail.json()["log"] == "fertig"


@pytest.mark.asyncio
async def test_detail_fuer_nichtmitglied_ist_404(db, client):
    """The detail route carries no project id in the path; the permission comes from the
    loaded row. A non-member must not be able to read off the 404 whether the row exists:
    "is not yours" and "does not exist" answer identically."""
    besitzer = await make_user(db, "besitzer")
    fremder = await make_user(db, "fremder")
    projekt = await make_project(db, "TRA", "Traccoon", inherit_members=False)
    await add_member(db, projekt, besitzer, ProjectRole.owner)
    d = await deploy(db, projekt=projekt)

    vorhanden = await client.get(f"/deployments/{d.id}", headers=auth(fremder))
    erfunden = await client.get(f"/deployments/{d.id + 999}", headers=auth(fremder))
    assert vorhanden.status_code == 404
    assert erfunden.status_code == 404
    assert vorhanden.json() == erfunden.json()


@pytest.mark.asyncio
async def test_projektlose_zeile_nur_fuer_admin(db, client):
    """`project_id IS NULL` is an admin matter. With a run one could anchor the visibility
    on the `owner_id`; the deployment has no such field (`requested_by` is filled on 0 of 186
    rows). An ownerless deployment therefore belongs to nobody."""
    admin = await make_user(db, "admin", admin=True)
    mitglied = await make_user(db, "mitglied")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, mitglied, ProjectRole.owner)
    eigen = await deploy(db, projekt=projekt)
    herrenlos = await deploy(db, projekt=None, stack_dir="")

    fuer_admin = (await client.get("/deployments", headers=auth(admin))).json()
    assert {i["id"] for i in fuer_admin["items"]} == {eigen.id, herrenlos.id}

    fuer_mitglied = (await client.get("/deployments", headers=auth(mitglied))).json()
    assert {i["id"] for i in fuer_mitglied["items"]} == {eigen.id}

    assert (await client.get(f"/deployments/{herrenlos.id}",
                             headers=auth(mitglied))).status_code == 404
    assert (await client.get(f"/deployments/{herrenlos.id}",
                             headers=auth(admin))).status_code == 200


@pytest.mark.asyncio
async def test_project_id_filter_verengt_und_autorisiert_nicht(db, client):
    """`?project_id=` stands as an additional AND beside the visibility condition, not in its
    place. Entering a foreign project yields an empty list: no access and explicitly no 403,
    which would be a proof of existence."""
    nutzer = await make_user(db, "nutzer")
    meins = await make_project(db, "TRA", "Traccoon")
    fremd = await make_project(db, "UNI", "GameProj", inherit_members=False)
    await add_member(db, meins, nutzer, ProjectRole.owner)
    eigen = await deploy(db, projekt=meins)
    await deploy(db, projekt=fremd)

    ohne = (await client.get("/deployments", headers=auth(nutzer))).json()
    assert [i["id"] for i in ohne["items"]] == [eigen.id]

    verengt = await client.get(f"/deployments?project_id={meins.id}", headers=auth(nutzer))
    assert [i["id"] for i in verengt.json()["items"]] == [eigen.id]

    fremdgefiltert = await client.get(f"/deployments?project_id={fremd.id}",
                                      headers=auth(nutzer))
    assert fremdgefiltert.status_code == 200
    assert fremdgefiltert.json()["items"] == []
    assert fremdgefiltert.json()["by_status"] == {}


# ── Log: the head in the list, the full text only in the detail ─────────────

@pytest.mark.asyncio
async def test_log_nur_im_detail_kopf_exakt_gekappt(db, client):
    """All 56 `failed` of the existing data carry the same guard text; a list without
    `log_head` would show 56 different failures where there is one. The full text stays
    outside regardless: an `ok` log is around 1 kB, and with 200 rows the list would be
    twenty times as large for no reason."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    langer_log = "x" * 1000
    d = await deploy(db, projekt=projekt, status="failed", log=langer_log)

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert "log" not in zeile
    assert len(zeile["log_head"]) == LOG_HEAD_CHARS
    assert zeile["log_head"] == langer_log[:LOG_HEAD_CHARS]
    assert zeile["log_bytes"] == 1000

    detail = (await client.get(f"/deployments/{d.id}", headers=auth(nutzer))).json()
    assert detail["log"] == langer_log
    assert detail["log_head"] == zeile["log_head"]
    assert detail["log_bytes"] == zeile["log_bytes"]


@pytest.mark.asyncio
async def test_kurzer_log_wird_nicht_aufgefuellt(db, client):
    """The head is a truncation, not a fixed width: shorter than 240 stays shorter."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    await deploy(db, projekt=projekt, status="ok", log="kurz")
    await deploy(db, projekt=projekt, status="cancelled", log="",
                 wartet_sekunden=None, dauer_sekunden=None)

    items = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"]
    kopf = {i["status"]: (i["log_head"], i["log_bytes"]) for i in items}
    assert kopf["ok"] == ("kurz", 4)
    assert kopf["cancelled"] == ("", 0)


# ── `ok` is three valued ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status,phase,ok", STATUS_ERWARTUNG)
@pytest.mark.asyncio
async def test_ok_ist_dreiwertig(db, client, status, phase, ok):
    """The same rule as `services/office.tool_ok`: **never a guessed result**. Open and
    aborted are both `None`, but for different reasons, and the `phase` separates them.
    `cancelled` is the most important case here: it stands on 69 existing rows no code path
    wrote, and counting as `done` would mean claiming something had come to an end.
    """
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    await deploy(db, projekt=projekt, status=status)

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert zeile["status"] == status, "the raw status passes through unembellished"
    assert zeile["phase"] == phase
    assert zeile["ok"] is ok


@pytest.mark.asyncio
async def test_unbekannter_status_gilt_als_abgebrochen(db, client):
    """A status this file does not know is not a finished deploy but one about which nothing
    is known: `aborted`, not `done`."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    await deploy(db, projekt=projekt, status="wasauchimmer")

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert zeile["phase"] == "aborted"
    assert zeile["ok"] is None


# ── Durations: `None` instead of a computed zero ────────────────────────────

@pytest.mark.asyncio
async def test_dauern_sind_none_ohne_zeitstempel(db, client):
    """71 of the 186 existing rows have no `finished_at`, 58 no `started_at`. A computed 0
    would claim a deploy that took no time instead of one whose time nobody wrote down.
    """
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    ganz = await deploy(db, projekt=projekt, status="ok",
                        wartet_sekunden=3.0, dauer_sekunden=12.5)
    ohne_ende = await deploy(db, projekt=projekt, status="building",
                             wartet_sekunden=3.0, dauer_sekunden=None)
    nie_gestartet = await deploy(db, projekt=projekt, status="cancelled",
                                 wartet_sekunden=None, dauer_sekunden=None)

    items = {i["id"]: i for i in (await client.get(
        f"/projects/{projekt.id}/deployments", headers=auth(nutzer))).json()["items"]}

    assert items[ganz.id]["wait_ms"] == 3000
    assert items[ganz.id]["duration_ms"] == 12500
    assert items[ganz.id]["finished_at"] is not None

    assert items[ohne_ende.id]["wait_ms"] == 3000
    assert items[ohne_ende.id]["duration_ms"] is None

    assert items[nie_gestartet.id]["wait_ms"] is None
    assert items[nie_gestartet.id]["duration_ms"] is None
    assert items[nie_gestartet.id]["started_at"] is None


# ── Zeilenform ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zeilenform_und_leere_herkunft(db, client):
    """The row carries the project and ticket key along (the view should not have to ask per
    row), `source` is honestly `unbekannt` without an entry instead of guessed, and
    `requested_by`/`chat_id` turn up nowhere: they are filled on 0 of 186 rows.
    """
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    t = await ticket(db, projekt, 7)
    await deploy(db, projekt=projekt, issue=t, status="ok")

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert set(zeile) == {
        "id", "project_id", "project_key", "issue_id", "issue_key", "status", "phase",
        "ok", "source", "kind", "stack_dir", "created_at", "started_at", "finished_at",
        "wait_ms", "duration_ms", "log_bytes", "log_head",
    }
    assert zeile["project_key"] == "TRA"
    assert zeile["issue_key"] == "ABC-7"
    assert zeile["source"] == "unbekannt"


@pytest.mark.asyncio
async def test_kind_unterscheidet_self_check_stack(db, client):
    """`self` beats `check`: a self-deploy is never a mere check, and `check_only` alone says
    nothing about whose stack is meant."""
    admin = await make_user(db, "admin", admin=True)
    projekt = await make_project(db, "TRA", "Traccoon")
    await deploy(db, projekt=projekt, self_deploy=True, status="ok")
    await deploy(db, projekt=projekt, check_only=True, status="ok")
    await deploy(db, projekt=projekt, status="ok")

    items = (await client.get("/deployments", headers=auth(admin))).json()["items"]
    assert [i["kind"] for i in items] == ["stack", "check", "self"]  # neueste zuerst


@pytest.mark.asyncio
async def test_issue_filter_verengt(db, client):
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    t = await ticket(db, projekt, 7)
    mit = await deploy(db, projekt=projekt, issue=t, status="ok")
    await deploy(db, projekt=projekt, status="ok")

    r = await client.get(f"/projects/{projekt.id}/deployments?issue_id={t.id}",
                         headers=auth(nutzer))
    assert [i["id"] for i in r.json()["items"]] == [mit.id]


# ── Umschlag: Kappung, Fenster, `by_status` ─────────────────────────────────

@pytest.mark.asyncio
async def test_limit_geklemmt_und_truncated_gemeldet(db, client):
    """Truncation happens at the newest end (`id DESC`), and the truncation is reported; a
    silent truncation would let the view believe it saw everything."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    ids = [(await deploy(db, projekt=projekt, status="ok")).id for _ in range(5)]

    zwei = (await client.get(f"/projects/{projekt.id}/deployments?limit=2",
                             headers=auth(nutzer))).json()
    assert [i["id"] for i in zwei["items"]] == ids[::-1][:2]
    assert zwei["count"] == 2 and zwei["truncated"] is True

    alle = (await client.get(f"/projects/{projekt.id}/deployments?limit=5",
                             headers=auth(nutzer))).json()
    assert alle["count"] == 5 and alle["truncated"] is False

    # Below 1 it is clamped to 1, not to "everything" or "nothing".
    null = (await client.get(f"/projects/{projekt.id}/deployments?limit=0",
                             headers=auth(nutzer))).json()
    assert null["count"] == 1 and null["truncated"] is True

    # Beyond the upper bound it is clamped instead of rejected.
    viel = await client.get(f"/projects/{projekt.id}/deployments?limit={LIMIT_MAX * 10}",
                            headers=auth(nutzer))
    assert viel.status_code == 200 and viel.json()["count"] == 5


@pytest.mark.asyncio
async def test_since_hours_geklemmt(db, client):
    """The window goes over `created_at`, not over `finished_at`; otherwise every row without
    an end (69 of 186) would fall out of every window and be reachable over no route any
    more. The upper bound is one year, even when more is requested."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    frisch = await deploy(db, projekt=projekt, status="ok", alter_stunden=1)
    halbjahr = await deploy(db, projekt=projekt, status="ok", alter_stunden=24 * 180)
    uralt = await deploy(db, projekt=projekt, status="cancelled", alter_stunden=24 * 500,
                         wartet_sekunden=None, dauer_sekunden=None)

    voreinstellung = (await client.get(f"/projects/{projekt.id}/deployments",
                                       headers=auth(nutzer))).json()
    assert [i["id"] for i in voreinstellung["items"]] == [frisch.id]

    weit = (await client.get(
        f"/projects/{projekt.id}/deployments?since_hours={SINCE_HOURS_MAX * 10}",
        headers=auth(nutzer))).json()
    # Clamped to one year: the half year comes along, the 500 days stay outside.
    assert {i["id"] for i in weit["items"]} == {frisch.id, halbjahr.id}
    assert uralt.id not in {i["id"] for i in weit["items"]}


@pytest.mark.asyncio
async def test_by_status_zaehlt_das_fenster_nicht_die_liste(db, client):
    """`by_status` is the only place where the aborted rows can be explained honestly without
    poisoning the list. It therefore counts against the **window**, not against the filtered
    list; otherwise it would be a tautology with `?status=ok`."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    for _ in range(3):
        await deploy(db, projekt=projekt, status="ok")
    await deploy(db, projekt=projekt, status="failed", log="Abgelehnt: …")
    for _ in range(2):
        await deploy(db, projekt=projekt, status="cancelled",
                     wartet_sekunden=None, dauer_sekunden=None)

    alles = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()
    assert alles["by_status"] == {"ok": 3, "cancelled": 2, "failed": 1}
    # Descending by count: the view can take the order over.
    assert list(alles["by_status"]) == ["ok", "cancelled", "failed"]

    nur_ok = (await client.get(f"/projects/{projekt.id}/deployments?status=ok",
                               headers=auth(nutzer))).json()
    assert nur_ok["count"] == 3
    assert nur_ok["by_status"] == alles["by_status"]


@pytest.mark.asyncio
async def test_statusfilter(db, client):
    """`running` means "not decided yet" and takes the queue along: whoever wants to know
    whether something is under way right now does not care whether the sidecar has already
    picked the row up. `other` is the rest, today exactly the aborted ones.
    """
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    for status, _phase, _ok in STATUS_ERWARTUNG:
        await deploy(db, projekt=projekt, status=status)

    async def stati(filter_: str) -> set[str]:
        r = await client.get(f"/projects/{projekt.id}/deployments?status={filter_}",
                             headers=auth(nutzer))
        assert r.status_code == 200
        return {i["status"] for i in r.json()["items"]}

    assert await stati("all") == {s for s, _p, _o in STATUS_ERWARTUNG}
    assert await stati("running") == {"pending", "pending-check", "building"}
    assert await stati("ok") == {"ok"}
    assert await stati("failed") == {"failed", "rolledback"}
    assert await stati("other") == {"cancelled"}

    kaputt = await client.get(f"/projects/{projekt.id}/deployments?status=quatsch",
                              headers=auth(nutzer))
    assert kaputt.status_code == 400


# ── The button: queueing by hand ────────────────────────────────────────────

STACK = "/opt/docker/stacks/gameproj"


async def mit_stack(db, projekt, pfad: str = STACK):
    """Add the stack directory: `make_project` does not know it, and without it the button
    rightly refuses."""
    projekt.workspace_dir = pfad
    await db.commit()
    await db.refresh(projekt)
    return projekt


@pytest.mark.asyncio
async def test_knopf_braucht_maintainer(db, client):
    """Reading is allowed for every member ("is my merge out there?"), triggering is not: the
    button rebuilds and restarts a running stack. `viewer`/`member` get a 403, because they
    already know the project, so a 404 would be no discretion here but a lie. Only the
    **stranger** gets a 404, as everywhere in this file."""
    fremder = await make_user(db, "fremder")
    seher = await make_user(db, "seher")
    mitglied = await make_user(db, "mitglied")
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon", inherit_members=False)
    await mit_stack(db, projekt)
    await add_member(db, projekt, seher, ProjectRole.viewer)
    await add_member(db, projekt, mitglied, ProjectRole.member)
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)
    pfad = f"/projects/{projekt.id}/deployments"

    assert (await client.post(pfad, json={}, headers=auth(fremder))).status_code == 404
    assert (await client.post(pfad, json={}, headers=auth(seher))).status_code == 403
    assert (await client.post(pfad, json={}, headers=auth(mitglied))).status_code == 403

    # And the read route stays open for the viewer: the two rights are separate.
    assert (await client.get(pfad, headers=auth(seher))).status_code == 200

    r = await client.post(pfad, json={}, headers=auth(pfleger))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ohne_stack_verzeichnis_400(db, client):
    """An empty `workspace_dir` aims at the host and maintenance project itself. The deployer
    rejects that anyway, but the row would come into being regardless, and in the auto-deploy
    path exactly that was a deploy storm once (ABC-19). The button must not lead there in the
    first place: **no row**, a 400, and the message says where to enter the directory."""
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)

    r = await client.post(f"/projects/{projekt.id}/deployments", json={},
                          headers=auth(pfleger))
    assert r.status_code == 400
    assert "stack directory" in r.json()["detail"]

    liste = await client.get(f"/projects/{projekt.id}/deployments", headers=auth(pfleger))
    assert liste.json()["items"] == [], "a rejected request leaves no row"


@pytest.mark.asyncio
async def test_zweiter_deploy_bei_offenem_ist_409(db, client):
    """Two `docker compose up` in the same directory are a data race. The lock is against the
    **open** statuses, not against "last built": a failure from earlier must not block the
    next attempt, because otherwise the button is dead after the first problem.
    """
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await mit_stack(db, projekt)
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)
    pfad = f"/projects/{projekt.id}/deployments"

    erste = await client.post(pfad, json={}, headers=auth(pfleger))
    assert erste.status_code == 200
    erste_id = erste.json()["id"]

    zweite = await client.post(pfad, json={}, headers=auth(pfleger))
    assert zweite.status_code == 409
    assert f"#{erste_id}" in zweite.json()["detail"], "the running row is named"

    # Only one row has come into being.
    assert (await client.get(pfad, headers=auth(pfleger))).json()["count"] == 1

    # Finished (failed as well) lifts the lock.
    lauf = await db.get(Deployment, erste_id)
    lauf.status = "failed"
    await db.commit()
    dritte = await client.post(pfad, json={}, headers=auth(pfleger))
    assert dritte.status_code == 200

    # An open deploy of **another** project does not lock along.
    anderes = await make_project(db, "UNI", "GameProj")
    await mit_stack(db, anderes, "/opt/docker/stacks/anderes")
    await add_member(db, anderes, pfleger, ProjectRole.maintainer)
    r = await client.post(f"/projects/{anderes.id}/deployments", json={}, headers=auth(pfleger))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_eingereihte_zeile_ist_pending_und_manual(db, client):
    """What lands in the row is the whole point of the route: `pending` (otherwise the sidecar
    never picks it up), `manual` as the fifth origin (not `agent`: the history should be able
    to tell the human from the automation) and the stack directory **from the project**, not
    from the body. The answer has the shape of the list, so that the frontend can sort it in
    without a second fetch."""
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await mit_stack(db, projekt)
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)

    r = await client.post(f"/projects/{projekt.id}/deployments", json={},
                          headers=auth(pfleger))
    assert r.status_code == 200
    zeile = r.json()

    assert set(zeile) == {
        "id", "project_id", "project_key", "issue_id", "issue_key", "status", "phase",
        "ok", "source", "kind", "stack_dir", "created_at", "started_at", "finished_at",
        "wait_ms", "duration_ms", "log_bytes", "log_head",
    }
    assert zeile["status"] == "pending"
    assert zeile["phase"] == "queued" and zeile["ok"] is None
    assert zeile["source"] == "manual"
    assert zeile["kind"] == "stack", "no self deploy and no mere check"
    assert zeile["stack_dir"] == STACK
    assert zeile["project_key"] == "TRA"
    assert zeile["issue_id"] is None and zeile["issue_key"] == ""
    assert zeile["started_at"] is None and zeile["finished_at"] is None

    gespeichert = await db.get(Deployment, zeile["id"])
    assert gespeichert.status == "pending"
    assert gespeichert.source == "manual"
    assert gespeichert.stack_dir == STACK
    assert gespeichert.self_deploy is False and gespeichert.check_only is False

    # And afterwards it stands in the same list the view reads from.
    liste = await client.get(f"/projects/{projekt.id}/deployments?status=running",
                             headers=auth(pfleger))
    assert [i["id"] for i in liste.json()["items"]] == [zeile["id"]]


@pytest.mark.asyncio
async def test_issue_id_wird_uebernommen_fremdes_ticket_404(db, client):
    """With a ticket the deploy hangs off the process (as with the auto-deploy after a merge),
    without one it stays project wide. A ticket from **another** project is rejected;
    otherwise a row would stand in the list whose `issue_key` points at a project where it
    has no business."""
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await mit_stack(db, projekt)
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)
    t = await ticket(db, projekt, 7)

    r = await client.post(f"/projects/{projekt.id}/deployments", json={"issue_id": t.id},
                          headers=auth(pfleger))
    assert r.status_code == 200
    assert r.json()["issue_id"] == t.id
    assert r.json()["issue_key"] == "ABC-7"
    assert (await db.get(Deployment, r.json()["id"])).issue_id == t.id

    # Clean up, so that the 409 lock does not overlay the next call.
    fertig = await db.get(Deployment, r.json()["id"])
    fertig.status = "ok"
    await db.commit()

    fremdes = await make_project(db, "UNI", "GameProj")
    ft = await ticket(db, fremdes, 1)
    falsch = await client.post(f"/projects/{projekt.id}/deployments",
                               json={"issue_id": ft.id}, headers=auth(pfleger))
    assert falsch.status_code == 404
    erfunden = await client.post(f"/projects/{projekt.id}/deployments",
                                 json={"issue_id": ft.id + 999}, headers=auth(pfleger))
    assert erfunden.status_code == 404
    assert falsch.json() == erfunden.json()
