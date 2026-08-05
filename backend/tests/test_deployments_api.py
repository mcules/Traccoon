"""Die Lese-API der Deployments: wer sie sehen darf, und was sie über den Bestand sagt.

Zwei Schwerpunkte, wie beim Büro. **Sichtbarkeit**: eine neue Lesefläche auf einer
Tabelle, die 186 Zeilen lang niemand lesen konnte, darf die Existenz fremder Projekte
nicht verraten — 404, nie 403. Und **Ehrlichkeit**: `ok` ist dreiwertig, Dauern sind
`None`, wenn der Zeitstempel fehlt, und der Log-Volltext verlässt die Liste nicht.

Die Testdaten bilden bewusst den echten Bestand nach: alle sieben Statuswerte inklusive
`cancelled` (69 Zeilen, die kein Codepfad schreibt), Zeilen ohne `started_at`/`finished_at`
(71 von 186) und der immer gleiche 124-Zeichen-Wächtertext in jedem `failed`.
"""
import datetime as dt

import pytest

from app.api.deployments import LIMIT_MAX, LOG_HEAD_CHARS, SINCE_HOURS_MAX
from app.models.enums import ProjectRole, StatusCategory
from app.models.ops import Deployment
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user

NOW = dt.datetime.now(dt.timezone.utc)

# Die sieben Statuswerte, die in dieser Tabelle vorkommen können — sechs aus dem Modell
# plus `cancelled`, das nur der Bestand kennt. Je Zeile: erwartete `phase` und `ok`.
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
    """Eine Deployment-Zeile. `wartet_sekunden=None` heißt „nie aufgegriffen"
    (kein `started_at`), `dauer_sekunden=None` heißt „nie beendet" (kein `finished_at`) —
    genau die beiden Löcher, die der Bestand hat."""
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


# ── Sichtbarkeit: 404 statt 403 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fremdes_projekt_ist_404_nicht_403(db, client):
    """Ein Nichtmitglied bekommt auf die Projektliste 404. Eine 403 wäre die Auskunft
    „dieses Projekt gibt es, du darfst nur nicht" — genau die Auskunft, die eine
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
    """Wer gemergt hat, will wissen, ob es draußen ist — und ist dafür nicht zwangsläufig
    `maintainer`. Die niedrigste Rolle reicht für Liste und Detail."""
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
    """Die Detailroute trägt keine Projekt-ID im Pfad — die Berechtigung kommt aus der
    geladenen Zeile. Ein Nichtmitglied darf am 404 nicht ablesen können, ob es die Zeile
    gibt: „gehört dir nicht" und „gibt es nicht" antworten identisch."""
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
    """`project_id IS NULL` ist Admin-Sache. Beim Lauf könnte man Sichtbarkeit am
    `owner_id` festmachen; das Deployment hat kein solches Feld (`requested_by` ist bei 0
    von 186 Zeilen gefüllt). Ein herrenloses Deployment gehört deshalb niemandem."""
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
    """`?project_id=` steht als zusätzliches UND neben der Sichtbarkeitsbedingung, nicht
    an ihrer Stelle. Ein fremdes Projekt einzutragen liefert eine leere Liste — keinen
    Zugang und ausdrücklich keine 403, die wäre ein Existenzbeweis."""
    nutzer = await make_user(db, "nutzer")
    meins = await make_project(db, "TRA", "Traccoon")
    fremd = await make_project(db, "UNI", "Uniwar", inherit_members=False)
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


# ── Log: Kopf in der Liste, Volltext nur im Detail ──────────────────────────

@pytest.mark.asyncio
async def test_log_nur_im_detail_kopf_exakt_gekappt(db, client):
    """Alle 56 `failed` des Bestands tragen denselben Wächtertext — eine Liste ohne
    `log_head` zeigte 56 verschiedene Fehlschläge, wo einer steht. Der Volltext bleibt
    trotzdem draußen: ein `ok`-Log ist rund 1 kB, bei 200 Zeilen wäre die Liste ohne Not
    zwanzigmal so groß."""
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
    """Der Kopf ist eine Kappung, keine feste Breite: kürzer als 240 bleibt kürzer."""
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


# ── `ok` ist dreiwertig ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status,phase,ok", STATUS_ERWARTUNG)
@pytest.mark.asyncio
async def test_ok_ist_dreiwertig(db, client, status, phase, ok):
    """Dieselbe Regel wie `services/office.tool_ok`: **nie ein geratenes Ergebnis**.
    Offen und abgebrochen sind beide `None` — aber aus verschiedenen Gründen, und die
    `phase` trennt sie. `cancelled` ist hier der wichtigste Fall: es steht auf 69
    Bestandszeilen, die kein Codepfad geschrieben hat; als `done` zu gelten hieße zu
    behaupten, da sei etwas zu Ende gegangen."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    await deploy(db, projekt=projekt, status=status)

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert zeile["status"] == status, "der rohe Status geht ungeschönt durch"
    assert zeile["phase"] == phase
    assert zeile["ok"] is ok


@pytest.mark.asyncio
async def test_unbekannter_status_gilt_als_abgebrochen(db, client):
    """Ein Status, den diese Datei nicht kennt, ist kein abgeschlossener Deploy, sondern
    einer, über den nichts bekannt ist — `aborted`, nicht `done`."""
    nutzer = await make_user(db, "nutzer")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, nutzer, ProjectRole.owner)
    await deploy(db, projekt=projekt, status="wasauchimmer")

    zeile = (await client.get(f"/projects/{projekt.id}/deployments",
                              headers=auth(nutzer))).json()["items"][0]
    assert zeile["phase"] == "aborted"
    assert zeile["ok"] is None


# ── Dauern: `None` statt gerechneter Null ───────────────────────────────────

@pytest.mark.asyncio
async def test_dauern_sind_none_ohne_zeitstempel(db, client):
    """71 der 186 Bestandszeilen haben kein `finished_at`, 58 kein `started_at`. Eine
    gerechnete 0 behauptete einen Deploy, der keine Zeit gebraucht hat, statt einen,
    dessen Zeit niemand aufgeschrieben hat."""
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
    """Die Zeile trägt Projekt- und Ticketschlüssel mit (die Ansicht soll nicht je Zeile
    nachfragen müssen), `source` ist ohne Eintrag ehrlich `unbekannt` statt geraten, und
    `requested_by`/`chat_id` tauchen nirgends auf — sie sind bei 0 von 186 Zeilen
    gefüllt."""
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
    assert zeile["issue_key"] == "TRA-7"
    assert zeile["source"] == "unbekannt"


@pytest.mark.asyncio
async def test_kind_unterscheidet_self_check_stack(db, client):
    """`self` schlägt `check`: ein Self-Deploy ist nie ein bloßer Check, und `check_only`
    allein sagt nichts darüber, wessen Stack gemeint ist."""
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
    """Gekappt wird am neuesten Ende (`id DESC`), und die Kappung wird gemeldet — eine
    stille Kappung ließe die Ansicht glauben, sie sähe alles."""
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

    # Unter 1 wird auf 1 geklemmt, nicht auf „alles" oder „nichts".
    null = (await client.get(f"/projects/{projekt.id}/deployments?limit=0",
                             headers=auth(nutzer))).json()
    assert null["count"] == 1 and null["truncated"] is True

    # Über die Obergrenze hinaus wird geklemmt statt abgelehnt.
    viel = await client.get(f"/projects/{projekt.id}/deployments?limit={LIMIT_MAX * 10}",
                            headers=auth(nutzer))
    assert viel.status_code == 200 and viel.json()["count"] == 5


@pytest.mark.asyncio
async def test_since_hours_geklemmt(db, client):
    """Das Fenster geht über `created_at`, nicht über `finished_at` — sonst fiele jede
    Zeile ohne Ende (69 von 186) aus jedem Fenster und wäre über keine Route mehr
    erreichbar. Die Obergrenze ist ein Jahr, auch wenn mehr angefragt wird."""
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
    # Geklemmt auf ein Jahr: das Halbjahr kommt mit, die 500 Tage bleiben draußen.
    assert {i["id"] for i in weit["items"]} == {frisch.id, halbjahr.id}
    assert uralt.id not in {i["id"] for i in weit["items"]}


@pytest.mark.asyncio
async def test_by_status_zaehlt_das_fenster_nicht_die_liste(db, client):
    """`by_status` ist die einzige Stelle, an der die abgebrochenen Zeilen ehrlich erklärt
    werden können, ohne die Liste zu vergiften. Es zählt deshalb gegen das **Fenster**,
    nicht gegen die gefilterte Liste — sonst wäre es bei `?status=ok` eine Tautologie."""
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
    # Absteigend nach Anzahl — die Ansicht kann die Reihenfolge übernehmen.
    assert list(alles["by_status"]) == ["ok", "cancelled", "failed"]

    nur_ok = (await client.get(f"/projects/{projekt.id}/deployments?status=ok",
                               headers=auth(nutzer))).json()
    assert nur_ok["count"] == 3
    assert nur_ok["by_status"] == alles["by_status"]


@pytest.mark.asyncio
async def test_statusfilter(db, client):
    """`running` meint „noch nicht entschieden" und nimmt die Warteschlange mit: wer
    wissen will, ob gerade etwas unterwegs ist, interessiert sich nicht dafür, ob der
    Sidecar die Zeile schon aufgegriffen hat. `other` ist der Rest — heute genau die
    abgebrochenen."""
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


# ── Der Knopf: von Hand einreihen ───────────────────────────────────────────

STACK = "/opt/docker/stacks/uniwar"


async def mit_stack(db, projekt, pfad: str = STACK):
    """Das Stack-Verzeichnis nachtragen — `make_project` kennt es nicht, und ohne es
    lehnt der Knopf zu Recht ab."""
    projekt.workspace_dir = pfad
    await db.commit()
    await db.refresh(projekt)
    return projekt


@pytest.mark.asyncio
async def test_knopf_braucht_maintainer(db, client):
    """Lesen darf jedes Mitglied („ist mein Merge draußen?"), Auslösen nicht: der Knopf
    baut und startet einen laufenden Stack neu. `viewer`/`member` bekommen 403 — sie
    kennen das Projekt ja bereits, eine 404 wäre hier keine Verschwiegenheit, sondern eine
    Lüge. Nur der **Fremde** bekommt 404, wie überall in dieser Datei."""
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

    # Und die Leseroute bleibt für den Viewer offen — die beiden Rechte sind getrennt.
    assert (await client.get(pfad, headers=auth(seher))).status_code == 200

    r = await client.post(pfad, json={}, headers=auth(pfleger))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ohne_stack_verzeichnis_400(db, client):
    """Ein leerer `workspace_dir` zielt auf das Host-/Wartungsprojekt selbst. Der Deployer
    lehnt das ohnehin ab, die Zeile entstünde aber trotzdem — im Auto-Deploy-Pfad war
    genau das einmal ein Deploy-Sturm (TRA-19). Der Knopf darf da nicht erst hinführen:
    **keine Zeile**, 400, und die Meldung sagt, wo man das Verzeichnis einträgt."""
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)

    r = await client.post(f"/projects/{projekt.id}/deployments", json={},
                          headers=auth(pfleger))
    assert r.status_code == 400
    assert "Stack-Verzeichnis" in r.json()["detail"]

    liste = await client.get(f"/projects/{projekt.id}/deployments", headers=auth(pfleger))
    assert liste.json()["items"] == [], "eine abgelehnte Anfrage hinterlässt keine Zeile"


@pytest.mark.asyncio
async def test_zweiter_deploy_bei_offenem_ist_409(db, client):
    """Zwei `docker compose up` im selben Verzeichnis sind ein Datenrennen. Gesperrt wird
    gegen die **offenen** Status, nicht gegen „zuletzt gebaut" — ein Fehlschlag von vorhin
    darf den nächsten Versuch nicht blockieren, sonst ist der Knopf nach dem ersten
    Problem tot."""
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
    assert f"#{erste_id}" in zweite.json()["detail"], "die laufende Zeile wird benannt"

    # Nur eine Zeile ist entstanden.
    assert (await client.get(pfad, headers=auth(pfleger))).json()["count"] == 1

    # Fertig (auch fehlgeschlagen) hebt die Sperre auf.
    lauf = await db.get(Deployment, erste_id)
    lauf.status = "failed"
    await db.commit()
    dritte = await client.post(pfad, json={}, headers=auth(pfleger))
    assert dritte.status_code == 200

    # Ein offener Deploy eines **anderen** Projekts sperrt nicht mit.
    anderes = await make_project(db, "UNI", "Uniwar")
    await mit_stack(db, anderes, "/opt/docker/stacks/anderes")
    await add_member(db, anderes, pfleger, ProjectRole.maintainer)
    r = await client.post(f"/projects/{anderes.id}/deployments", json={}, headers=auth(pfleger))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_eingereihte_zeile_ist_pending_und_manual(db, client):
    """Was in der Zeile landet, ist der ganze Punkt der Route: `pending` (sonst greift der
    Sidecar sie nie auf), `manual` als fünfte Herkunft (nicht `agent` — die Historie soll
    den Menschen vom Automatismus unterscheiden können) und das Stack-Verzeichnis **aus
    dem Projekt**, nicht aus dem Rumpf. Die Antwort hat die Form der Liste, damit das
    Frontend sie ohne zweiten Abruf einsortieren kann."""
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
    assert zeile["kind"] == "stack", "kein Self-Deploy und keine bloße Prüfung"
    assert zeile["stack_dir"] == STACK
    assert zeile["project_key"] == "TRA"
    assert zeile["issue_id"] is None and zeile["issue_key"] == ""
    assert zeile["started_at"] is None and zeile["finished_at"] is None

    gespeichert = await db.get(Deployment, zeile["id"])
    assert gespeichert.status == "pending"
    assert gespeichert.source == "manual"
    assert gespeichert.stack_dir == STACK
    assert gespeichert.self_deploy is False and gespeichert.check_only is False

    # Und sie steht danach in derselben Liste, aus der die Ansicht liest.
    liste = await client.get(f"/projects/{projekt.id}/deployments?status=running",
                             headers=auth(pfleger))
    assert [i["id"] for i in liste.json()["items"]] == [zeile["id"]]


@pytest.mark.asyncio
async def test_issue_id_wird_uebernommen_fremdes_ticket_404(db, client):
    """Mit Ticket hängt der Deploy am Vorgang (wie beim Auto-Deploy nach Merge), ohne
    bleibt er projektweit. Ein Ticket aus einem **anderen** Projekt wird abgelehnt — sonst
    stünde in der Liste eine Zeile, deren `issue_key` auf ein Projekt zeigt, in dem sie
    nichts zu suchen hat."""
    pfleger = await make_user(db, "pfleger")
    projekt = await make_project(db, "TRA", "Traccoon")
    await mit_stack(db, projekt)
    await add_member(db, projekt, pfleger, ProjectRole.maintainer)
    t = await ticket(db, projekt, 7)

    r = await client.post(f"/projects/{projekt.id}/deployments", json={"issue_id": t.id},
                          headers=auth(pfleger))
    assert r.status_code == 200
    assert r.json()["issue_id"] == t.id
    assert r.json()["issue_key"] == "TRA-7"
    assert (await db.get(Deployment, r.json()["id"])).issue_id == t.id

    # Aufräumen, damit die 409-Sperre den nächsten Aufruf nicht überlagert.
    fertig = await db.get(Deployment, r.json()["id"])
    fertig.status = "ok"
    await db.commit()

    fremdes = await make_project(db, "UNI", "Uniwar")
    ft = await ticket(db, fremdes, 1)
    falsch = await client.post(f"/projects/{projekt.id}/deployments",
                               json={"issue_id": ft.id}, headers=auth(pfleger))
    assert falsch.status_code == 404
    erfunden = await client.post(f"/projects/{projekt.id}/deployments",
                                 json={"issue_id": ft.id + 999}, headers=auth(pfleger))
    assert erfunden.status_code == 404
    assert falsch.json() == erfunden.json()
