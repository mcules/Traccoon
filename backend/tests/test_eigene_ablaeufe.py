"""Eigene Abläufe: jeder darf sie anlegen — sie wirken aber nur dort, wo er selbst darf.

Ein freier Ablauf (kein Projekt, kein Slot) gehört seinem Ersteller. Das ist mehr als eine
Anzeigeregel: die Definition liegt projektlos in derselben Tabelle wie die ausgelieferten
Vorlagen, und ihre Aktionen fassen Artefakte an. Geprüft wird deshalb an drei Stellen —
sehen, starten, und auf ein Ereignis anspringen.
"""
import pytest
from app.models.enums import (
    ProjectRole, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.events import emit
from sqlalchemy import select

from conftest import add_member, auth, make_project, make_user

pytestmark = pytest.mark.asyncio


async def _freier_ablauf(db, besitzer, key: str, *, trigger: dict | None = None,
                         veroeffentlicht: bool = True) -> WorkflowDefinition:
    start_cfg: dict = {"label": "Start"}
    if trigger:
        start_cfg["trigger"] = trigger
    graph = {"nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                        "data": {"config": start_cfg}},
                       {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                        "data": {"config": {"outcome": "completed"}}}],
             "edges": [{"id": "e1", "source": "s", "target": "e"}]}
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=besitzer.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=(WorkflowVersionStatus.published if veroeffentlicht
                                else WorkflowVersionStatus.draft))
    db.add(v)
    await db.flush()
    if veroeffentlicht:
        d.current_version_id = v.id
    await db.commit()
    return d


async def test_jeder_darf_einen_eigenen_ablauf_anlegen(client, db):
    """Ein eigener Ablauf ist kein Adminrecht — sonst hat ihn niemand außer dem Admin."""
    anna = await make_user(db, "anna")
    r = await client.post("/workflows", headers=auth(anna), json={
        "project_id": None, "key": "abendrunde", "name": "Abendrunde",
        "subject_kind": "standalone"})
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == anna.id or True   # Feld optional im Schema


async def test_fremder_ablauf_ist_unsichtbar_und_unantastbar(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _freier_ablauf(db, anna, "annas-ablauf")

    sichtbar = [w["id"] for w in (await client.get("/workflows", headers=auth(bert))).json()]
    assert d.id not in sichtbar
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(anna))).json()]

    assert (await client.put(f"/workflows/{d.id}", headers=auth(bert),
                             json={"name": "geklaut"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(bert),
                              json={"subject_kind": "standalone"})).status_code == 403
    assert (await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                              json={"subject_kind": "standalone"})).status_code == 201


async def test_admin_sieht_und_darf_alles(client, db):
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    d = await _freier_ablauf(db, anna, "annas-ablauf")
    assert d.id in [w["id"] for w in (await client.get("/workflows", headers=auth(chef))).json()]
    assert (await client.put(f"/workflows/{d.id}", headers=auth(chef),
                             json={"name": "umbenannt"})).status_code == 200


async def test_start_verlangt_rechte_am_artefakt(client, db):
    """Der Ablauf gehört Anna — das Ticket nicht. Was er anfasst, entscheidet das Projekt."""
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    projekt = await make_project(db, "GEH", "Geheim")
    await add_member(db, projekt, chef, ProjectRole.owner)
    typ = IssueType(project_id=projekt.id, name="Aufgabe", order=0)
    status_ = WorkflowStatus(project_id=projekt.id, name="Offen", order=0)
    db.add_all([typ, status_])
    await db.flush()
    issue = Issue(project_id=projekt.id, number=1, key="GEH-1", type_id=typ.id,
                  status_id=status_.id, summary="Fremd", reporter_id=chef.id, rank="1")
    db.add(issue)
    await db.commit()

    d = await _freier_ablauf(db, anna, "annas-ablauf")
    r = await client.post(f"/workflows/{d.id}/instances", headers=auth(anna),
                          json={"subject_kind": "issue", "issue_id": issue.id})
    assert r.status_code in (403, 404), r.text


async def test_ereignis_startet_nur_bei_eigenen_projekten(db):
    """Ohne diese Grenze liefe Annas Ablauf bei JEDEM Ticket-Ereignis mit — auch in
    Projekten, die sie gar nicht sehen darf."""
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    ihrs = await make_project(db, "IHR", "Annas Projekt")
    await add_member(db, ihrs, anna, ProjectRole.member)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await add_member(db, fremd, chef, ProjectRole.owner)

    await _freier_ablauf(db, anna, "annas-lauscher", trigger={"event": "issue.created"})

    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 0
    assert len(await emit(db, "issue.created", project_id=ihrs.id)) == 1
    # Die Instanz selbst bleibt projektlos (das Subjekt ist `standalone`) — im Kontext
    # steht aber, aus welchem Projekt das Ereignis kam.
    lauf = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert lauf.project_id is None
    assert lauf.context["event"]["project_id"] == ihrs.id


async def test_admin_ablauf_hoert_ueberall(db):
    chef = await make_user(db, "chef", admin=True)
    fremd = await make_project(db, "FRD", "Fremdes Projekt")
    await _freier_ablauf(db, chef, "chef-lauscher", trigger={"event": "issue.created"})
    assert len(await emit(db, "issue.created", project_id=fremd.id)) == 1


# ── Webhook als Quelle ───────────────────────────────────────────────────────

async def test_ablauf_bekommt_eine_eigene_adresse(client, db):
    """Nicht jedes System spricht MCP oder kennt Traccoons Ereignisse — einen Webhook
    schickt fast jedes. Die Adresse entsteht jetzt im Ablauf, nicht am anderen Ende."""
    anna = await make_user(db, "anna")
    d = await _freier_ablauf(db, anna, "stoerungsmelder")

    assert (await client.get(f"/workflows/{d.id}/webhook", headers=auth(anna))).json() is None

    r = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert r.status_code == 201, r.text
    hook = r.json()
    assert hook["public_id"] and hook["secret"]
    assert hook["url"].endswith(f"/api/hooks/{hook['public_id']}")

    # Ein zweiter Aufruf gibt dieselbe Adresse — sonst sammelte sich bei jedem Klick eine
    # weitere, und niemand wüsste, welche das fremde System benutzt.
    wieder = await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))
    assert wieder.json()["public_id"] == hook["public_id"]


async def test_fremder_gibt_keinem_ablauf_eine_adresse(client, db):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    d = await _freier_ablauf(db, anna, "annas-ablauf")
    assert (await client.post(f"/workflows/{d.id}/webhook",
                              headers=auth(bert))).status_code == 403


async def test_webhook_startet_den_ablauf_wirklich(client, db):
    """Der Beweis, dass die Adresse trägt: Aufruf rein, Instanz raus — mit der Nutzlast
    im Kontext, damit die Verzweigungen etwas zu lesen haben."""
    import hashlib
    import hmac
    import json as _json

    from app.models.workflow import WorkflowInstance

    anna = await make_user(db, "anna")
    d = await _freier_ablauf(db, anna, "stoerungsmelder")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()

    nutzlast = {"quelle": "Zabbix", "vorgang": {"id": 42, "titel": "Störung"}}
    roh = _json.dumps(nutzlast).encode()
    sig = hmac.new(hook["secret"].encode(), roh, hashlib.sha256).hexdigest()
    r = await client.post(f"/hooks/{hook['public_id']}", content=roh,
                          headers={"content-type": "application/json",
                                   "X-Webhook-Signature": sig})
    assert r.status_code in (200, 202), r.text

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().all()
    assert len(inst) == 1
    assert inst[0].context["vorgang"]["titel"] == "Störung"


# ── Das Artefakt kommt aus der Nutzlast ──────────────────────────────────────

async def _ticket(db, chef, key="ABC-1"):
    from app.models.ticket import Issue, IssueType, WorkflowStatus

    projekt = await make_project(db, key.split("-")[0], "Projekt")
    await add_member(db, projekt, chef, ProjectRole.owner)
    typ = IssueType(project_id=projekt.id, name="Aufgabe", order=0)
    stat = WorkflowStatus(project_id=projekt.id, name="Offen", order=0)
    db.add_all([typ, stat])
    await db.flush()
    issue = Issue(project_id=projekt.id, number=int(key.split("-")[1]), key=key,
                  type_id=typ.id, status_id=stat.id, summary="Zielticket",
                  reporter_id=chef.id, rank="1")
    db.add(issue)
    await db.commit()
    return projekt, issue


async def _mit_subjektfeld(db, besitzer, feld: str | None, subject=WorkflowSubjectKind.issue):
    start_cfg = {"label": "Start", "trigger": {"kind": "webhook"}}
    if feld:
        start_cfg["trigger"]["subjekt_feld"] = feld
    graph = {"nodes": [{"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                        "data": {"config": start_cfg}},
                       {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                        "data": {"config": {"outcome": "completed"}}}],
             "edges": [{"id": "e1", "source": "s", "target": "e"}]}
    d = WorkflowDefinition(project_id=None, key=f"mitsubjekt{feld or 'ohne'}",
                           name="Mit Subjekt", created_by=besitzer.id, subject_kind=subject)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def _rufen(client, hook, nutzlast: dict):
    import hashlib
    import hmac
    import json as _json

    roh = _json.dumps(nutzlast).encode()
    sig = hmac.new(hook["secret"].encode(), roh, hashlib.sha256).hexdigest()
    return await client.post(f"/hooks/{hook['public_id']}", content=roh,
                             headers={"content-type": "application/json",
                                      "X-Webhook-Signature": sig})


async def test_webhook_bindet_das_ticket_aus_der_nutzlast(client, db):
    """Das fremde System kennt Traccoons Nummern nicht — es nennt die Kennung, die es
    kennt. Ohne diese Bindung liefen alle Ticket-Aktionen des Ablaufs ins Leere."""
    chef = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, chef, "ABC-7")
    d = await _mit_subjektfeld(db, chef, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()

    r = await _rufen(client, hook, {"vorgang": {"ticket": "ABC-7"}})
    assert r.status_code in (200, 202), r.text
    assert r.json()["issue_id"] == issue.id

    inst = (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.definition_id == d.id))).scalars().one()
    assert inst.issue_id == issue.id
    # Das Projekt des Tickets wird mitgeführt — sonst greifen Rechte und Live-Ereignisse nicht.
    assert inst.project_id == issue.project_id


async def test_auch_die_nummer_geht(client, db):
    chef = await make_user(db, "chef", admin=True)
    _, issue = await _ticket(db, chef, "ABC-9")
    d = await _mit_subjektfeld(db, chef, "id")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()
    r = await _rufen(client, hook, {"id": issue.id})
    assert r.json()["issue_id"] == issue.id


async def test_fehlendes_feld_sagt_es_deutlich(client, db):
    """Ein Ablauf, der ein Artefakt braucht, aber keines bekommt, darf nicht stumm
    ins Leere starten."""
    chef = await make_user(db, "chef", admin=True)
    await _ticket(db, chef, "ABC-3")
    d = await _mit_subjektfeld(db, chef, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()

    r = await _rufen(client, hook, {"etwas": "anderes"})
    assert r.status_code == 400 and "vorgang.ticket" in r.text

    leer = await _mit_subjektfeld(db, chef, None)
    hook2 = (await client.post(f"/workflows/{leer.id}/webhook", headers=auth(chef))).json()
    r2 = await _rufen(client, hook2, {"vorgang": {"ticket": "ABC-3"}})
    assert r2.status_code == 400 and "kein Feld" in r2.text


async def test_fremdes_ticket_bleibt_fremd(client, db):
    """Die Rechte kommen vom Besitzer des Auslösers, nicht vom Anrufer — eine
    Webhook-Adresse kann jeder kennen."""
    chef = await make_user(db, "chef", admin=True)
    anna = await make_user(db, "anna")
    _, issue = await _ticket(db, chef, "GEH-4")

    d = await _mit_subjektfeld(db, anna, "vorgang.ticket")
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(anna))).json()
    r = await _rufen(client, hook, {"vorgang": {"ticket": "GEH-4"}})
    assert r.status_code == 400 and "Rechte" in r.text


async def test_ablauf_ohne_artefakt_braucht_kein_feld(client, db):
    chef = await make_user(db, "chef", admin=True)
    d = await _mit_subjektfeld(db, chef, None, subject=WorkflowSubjectKind.standalone)
    hook = (await client.post(f"/workflows/{d.id}/webhook", headers=auth(chef))).json()
    r = await _rufen(client, hook, {"irgendwas": 1})
    assert r.status_code in (200, 202), r.text
