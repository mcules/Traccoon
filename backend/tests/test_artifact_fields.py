"""Fields and values on artifacts, following the ALMEX example (Artifacts → Fields → Values).

An artifact (ticket, hardware, own type) carries typed fields; a choice field has a
maintained value list, and the field says whether a unit may carry one or several values
from it. These tests record the promises data hangs on: cardinality, value list, fields
created afterwards, and that nothing disappears silently.
"""
import pytest
from app.models.artifact import ArtifactField, ArtifactFieldOption
from app.models.enums import ProjectRole, StatusCategory, TicketAgentStatus
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import artifact_fields as felder
from app.services import artifacts as art
from conftest import add_member, auth, make_asset, make_project, make_user
from sqlalchemy import select


@pytest.fixture
async def register(db):
    await art.ensure_builtin_types(db)


async def _ticket(db, proj, nummer=1) -> Issue:
    t = (await db.execute(select(IssueType).where(IssueType.project_id == proj.id))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == proj.id))).scalars().first()
    if t is None:
        t = IssueType(project_id=proj.id, name="Aufgabe")
        s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
        db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=0)])
        await db.commit()
    i = Issue(project_id=proj.id, number=nummer, key=f"{proj.key}-{nummer}", type_id=t.id,
              status_id=s.id, summary="Ein Ticket", reporter_id=1, rank=f"{nummer:04d}")
    db.add(i)
    await db.commit()
    return i


async def _feld(db, type_key: str, key: str, *, kind="text", multi=False, required=False,
                werte: list[str] | None = None) -> ArtifactField:
    typ = await art.type_by_key(db, type_key)
    f = ArtifactField(type_id=typ.id, key=key, label=key.capitalize(), kind=kind,
                      multi=multi, required=required)
    db.add(f)
    await db.flush()
    for i, w in enumerate(werte or []):
        db.add(ArtifactFieldOption(field_id=f.id, value=w, order=i))
    await db.commit()
    return f


# ── Kern: Feld anlegen, Werte zuordnen ───────────────────────────────────────

async def test_ticket_traegt_mehrere_werte_eines_feldes(client, db, register):
    """The core case: a choice field with multiple selection on a real ticket."""
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    await _feld(db, "ticket", "komponente", kind="select", multi=True,
                werte=["Backend", "Frontend", "DB"])

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"komponente": ["Backend", "DB"]}})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["komponente"] == ["Backend", "DB"]

    gelesen = await client.get(f"/artifacts/{aid}/values", headers=auth(user))
    assert gelesen.json()["values"]["komponente"] == ["Backend", "DB"]
    # The field definitions come along: the built-in ones of the ticket and the free one.
    keys = [f["key"] for f in gelesen.json()["fields"]]
    assert "komponente" in keys and "status" in keys and "prioritaet" in keys


async def test_einzelwert_feld_lehnt_zwei_werte_ab(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    await _feld(db, "ticket", "prio", kind="select", multi=False, werte=["hoch", "niedrig"])

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"prio": ["hoch", "niedrig"]}})
    assert r.status_code == 400
    assert "nur einen Wert" in r.json()["detail"]
    # After the rejected attempt nothing may be half written.
    leer = await client.get(f"/artifacts/{aid}/values", headers=auth(user))
    assert "prio" not in leer.json()["values"]


async def test_wert_ausserhalb_der_liste_wird_abgelehnt(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    await _feld(db, "ticket", "komponente", kind="select", werte=["Backend"])

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"komponente": ["Kaffeemaschine"]}})
    assert r.status_code == 400
    # The message names what would be allowed; otherwise the user guesses.
    assert "Backend" in r.json()["detail"]


async def test_feld_darf_jederzeit_dazukommen(client, db, register):
    """The "at any time" case: a new field does not break existing artifacts."""
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    alt = await _ticket(db, proj, 1)
    a_alt = await art.ensure_for_issue(db, alt)
    await db.commit()
    aid = a_alt.id

    # Only now does the field come into being; the old ticket does not know it.
    await _feld(db, "ticket", "kunde", kind="text")

    gelesen = await client.get(f"/artifacts/{aid}/values", headers=auth(user))
    assert gelesen.status_code == 200
    assert "kunde" not in gelesen.json()["values"]        # still without a value
    assert "kunde" in [f["key"] for f in gelesen.json()["fields"]]

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"kunde": ["Vostura"]}})
    assert r.status_code == 200
    assert r.json()["values"]["kunde"] == ["Vostura"]


# ── Typen ────────────────────────────────────────────────────────────────────

async def test_zahl_und_datum_werden_geprueft(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    await _feld(db, "ticket", "aufwand", kind="number")
    await _feld(db, "ticket", "termin", kind="date")
    await _feld(db, "ticket", "extern", kind="boolean")

    ok = await client.put(f"/artifacts/{aid}/values", headers=auth(user), json={"values": {
        "aufwand": [3], "termin": ["2026-08-01"], "extern": [True]}})
    assert ok.status_code == 200, ok.text
    werte = ok.json()["values"]
    assert werte["aufwand"] == [3] and werte["extern"] == [True]

    for feld, mist in (("aufwand", "viel"), ("termin", "irgendwann"), ("extern", "vielleicht")):
        r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                             json={"values": {feld: [mist]}})
        assert r.status_code == 400, f"{feld}={mist} hätte abgelehnt werden müssen"


# ── Nichts verschwindet still ────────────────────────────────────────────────

async def test_benutzter_listenwert_laesst_sich_nicht_loeschen(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    f = await _feld(db, "ticket", "komponente", kind="select", werte=["DB"])
    option = (await db.execute(select(ArtifactFieldOption).where(
        ArtifactFieldOption.field_id == f.id))).scalars().first()
    await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                     json={"values": {"komponente": ["DB"]}})

    r = await client.delete(f"/artifact-field-options/{option.id}", headers=auth(user))
    assert r.status_code == 409
    assert "1 Artefakt" in r.json()["detail"]


async def test_benutztes_feld_braucht_force(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    f = await _feld(db, "ticket", "kunde", kind="text")
    await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                     json={"values": {"kunde": ["Vostura"]}})

    assert (await client.delete(f"/artifact-fields/{f.id}", headers=auth(user))).status_code == 409
    assert (await client.delete(f"/artifact-fields/{f.id}?force=true",
                                headers=auth(user))).status_code == 204


async def test_mehrfach_zurueckstellen_nur_wenn_es_passt(client, db, register):
    """Going from "several" to "one" must not throw values away silently."""
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "FLD", "Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    f = await _feld(db, "ticket", "komponente", kind="select", multi=True, werte=["A", "B"])
    await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                     json={"values": {"komponente": ["A", "B"]}})

    r = await client.put(f"/artifact-fields/{f.id}", headers=auth(user), json={"multi": False})
    assert r.status_code == 409
    assert "mehrere Werte" in r.json()["detail"]

    # Back to one value, and then it works.
    await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                     json={"values": {"komponente": ["A"]}})
    assert (await client.put(f"/artifact-fields/{f.id}", headers=auth(user),
                             json={"multi": False})).status_code == 200


# ── Rechte ───────────────────────────────────────────────────────────────────

async def test_fremdes_projekt_bleibt_verschlossen(client, db, register):
    besitzer = await make_user(db, "eigner", admin=True)
    fremder = await make_user(db, "fremd")
    proj = await make_project(db, "GEH", "Geheim", inherit_members=False)
    await add_member(db, proj, besitzer, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id   # after a rollback in the endpoint a.id would no longer be readable
    await _feld(db, "ticket", "kunde", kind="text")

    assert (await client.get(f"/artifacts/{aid}/values",
                             headers=auth(fremder))).status_code in (403, 404)
    assert (await client.put(f"/artifacts/{aid}/values", headers=auth(fremder),
                             json={"values": {"kunde": ["X"]}})).status_code in (403, 404)


async def test_nur_admin_pflegt_das_register(client, db, register):
    niemand = await make_user(db, "gast")
    typ = await art.type_by_key(db, "ticket")
    r = await client.post(f"/artifact-types/{typ.id}/fields", headers=auth(niemand),
                          json={"key": "x", "label": "X"})
    assert r.status_code == 403


# ── Hardware carries fields the same way ─────────────────────────────────────

async def test_hardware_traegt_felder_genauso(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "HWF", "HW-Felder")
    await add_member(db, proj, user, ProjectRole.owner)
    asset = await make_asset(db, "Switch", project=proj)
    a = await art.ensure_for_asset(db, asset)
    await db.commit()
    aid = a.id
    await _feld(db, "hardware", "inventarnummer", kind="text")

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"inventarnummer": ["SN-4711"]}})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["inventarnummer"] == ["SN-4711"]


# ── Dienst-Ebene ─────────────────────────────────────────────────────────────

async def test_sammelabfrage_liefert_je_artefakt(db, register):
    """`values_for` is the shortcut for lists: one query instead of one per row."""
    proj = await make_project(db, "SAM", "Sammel")
    a1 = await _ticket(db, proj, 1)
    a2 = await _ticket(db, proj, 2)
    art1 = await art.ensure_for_issue(db, a1)
    art2 = await art.ensure_for_issue(db, a2)
    await db.commit()
    f = await _feld(db, "ticket", "kunde", kind="text")
    await felder.set_values(db, art1.id, f, ["Vostura"])
    await db.commit()

    alle = await felder.values_for(db, [art1.id, art2.id])
    assert alle[art1.id]["kunde"] == ["Vostura"]
    # The second ticket has set no free field, only its built-in columns.
    assert "kunde" not in alle.get(art2.id, {})


# ── Felder im Prozess setzen ─────────────────────────────────────────────────

async def _instanz(db, proj, issue):
    """Minimal instance with a ticket binding: the action needs no more."""
    from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
    from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
    d = WorkflowDefinition(project_id=proj.id, key="f", name="Felder",
                           subject_kind=WorkflowSubjectKind.issue)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id, project_id=proj.id,
                            subject_kind=WorkflowSubjectKind.issue, issue_id=issue.id,
                            context={})
    db.add(inst)
    await db.flush()
    return inst


def _knoten(**params):
    return {"type": "auto_action",
            "data": {"config": {"action": {"action": "set_field", "params": params}}}}


async def test_prozess_setzt_ergaenzt_und_entfernt(db, register):
    """The flow should be able to maintain fields: replace, add, remove."""
    from app.services.workflow_actions import run_action
    proj = await make_project(db, "PRZ", "Prozess")
    issue = await _ticket(db, proj)
    await art.ensure_for_issue(db, issue)
    await db.commit()
    await _feld(db, "ticket", "komponente", kind="select", multi=True,
                werte=["Backend", "Frontend", "DB"])
    inst = await _instanz(db, proj, issue)

    r = await run_action(db, inst, _knoten(field="komponente", values="Backend"))
    assert r["values"] == ["Backend"]
    # The set values stand in the context immediately, so conditions afterwards can read them.
    assert inst.context["fields"]["komponente"] == ["Backend"]

    r = await run_action(db, inst, _knoten(field="komponente", values="DB", mode="add"))
    assert r["values"] == ["Backend", "DB"]
    r = await run_action(db, inst, _knoten(field="komponente", values="Backend", mode="remove"))
    assert r["values"] == ["DB"]


async def test_prozess_versteht_komma_und_vorlagen(db, register):
    from app.services.workflow_actions import run_action
    proj = await make_project(db, "PRZ", "Prozess")
    issue = await _ticket(db, proj)
    await art.ensure_for_issue(db, issue)
    await db.commit()
    await _feld(db, "ticket", "komponente", kind="select", multi=True, werte=["Backend", "DB"])
    inst = await _instanz(db, proj, issue)
    inst.context = {"agent": {"bereich": "DB"}}

    r = await run_action(db, inst, _knoten(field="komponente", values="Backend, {{agent.bereich}}"))
    assert r["values"] == ["Backend", "DB"]


async def test_prozess_meldet_unbekanntes_feld(db, register):
    """Ein Tippfehler im Graphen darf nicht still ins Leere laufen."""
    from app.services.workflow_actions import run_action
    proj = await make_project(db, "PRZ", "Prozess")
    issue = await _ticket(db, proj)
    await art.ensure_for_issue(db, issue)
    await db.commit()
    inst = await _instanz(db, proj, issue)

    with pytest.raises(ValueError, match="gibt es an diesem Artefakt nicht"):
        await run_action(db, inst, _knoten(field="gibtsnicht", values="x"))


# ── Built-in fields: the register shows what a ticket really has ────────────

async def test_ticket_hat_seine_echten_felder(db, register):
    """Priority, issue type, sprint and company are no second truth any more."""
    typ = await art.type_by_key(db, "ticket")
    keys = {f.key: f for f in await felder.fields_of(db, typ.id)}
    for erwartet in ("status", "vorgangsart", "board", "prioritaet", "zustaendig",
                     "sprint", "story_points", "faellig"):
        assert erwartet in keys, erwartet
    # They write into the real columns and are protected against renaming.
    assert keys["prioritaet"].source == "priority" and keys["prioritaet"].builtin
    assert keys["status"].source == "agent_status"


async def test_zustand_ist_nur_ein_feld(db, register):
    """The state has no model of its own any more: it is the value list of `status`."""
    typ = await art.type_by_key(db, "ticket")
    feld = await felder.status_field(db, typ.id)
    assert feld is not None and feld.kind == "select" and feld.builtin
    werte = {o.value for o in await felder.options_of(db, feld.id)}
    assert werte == {s.value for s in TicketAgentStatus}
    # The category and "waiting" hang off the value, not off a special model.
    wartend = {o.value for o in await felder.options_of(db, feld.id) if o.waiting}
    assert "plan_review" in wartend and "done" not in wartend


async def test_eingebautes_feld_schreibt_in_die_echte_spalte(client, db, register):
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "SPA", "Spalten")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid, iid = a.id, issue.id

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"prioritaet": ["high"], "story_points": [5]}})
    assert r.status_code == 200, r.text
    frisch = await db.get(Issue, iid)
    await db.refresh(frisch)
    assert frisch.priority.value == "high"
    assert frisch.story_points == 5
    # And the way back: reading happens from the column, not from the value table.
    gelesen = await client.get(f"/artifacts/{aid}/values", headers=auth(user))
    assert gelesen.json()["values"]["prioritaet"] == ["high"]


async def test_zustand_ueber_das_feld_zieht_die_board_spalte_nach(client, db, register):
    """The state field has to have the same consequences as the earlier special path."""
    from app.models.enums import StatusCategory
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "BRD", "Board")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    db.add(WorkflowStatus(project_id=proj.id, name="Warten",
                          category=StatusCategory.in_progress, order=1))
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid, iid = a.id, issue.id

    r = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                         json={"values": {"status": ["hold"]}})
    assert r.status_code == 200, r.text
    frisch = await db.get(Issue, iid)
    await db.refresh(frisch)
    assert frisch.agent_status.value == "hold"
    spalte = await db.get(WorkflowStatus, frisch.status_id)
    assert spalte.name == "Warten"        # the board came along


async def test_projektabhaengige_auswahl_wird_geprueft(client, db, register):
    """Issue type and sprint take their values from the project; nonsense flies out."""
    user = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "DYN", "Dynamisch")
    await add_member(db, proj, user, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid = a.id
    art_id = (await db.execute(select(IssueType).where(
        IssueType.project_id == proj.id))).scalars().first().id

    ok = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                          json={"values": {"vorgangsart": [str(art_id)]}})
    assert ok.status_code == 200, ok.text
    schlecht = await client.put(f"/artifacts/{aid}/values", headers=auth(user),
                                json={"values": {"vorgangsart": ["999999"]}})
    assert schlecht.status_code == 400


async def test_prozess_setzt_auch_eingebaute_felder(db, register):
    """"Set a field" must not stop at the free fields."""
    from app.services.workflow_actions import run_action
    proj = await make_project(db, "PRZ", "Prozess")
    issue = await _ticket(db, proj)
    await art.ensure_for_issue(db, issue)
    await db.commit()
    iid = issue.id
    inst = await _instanz(db, proj, issue)

    r = await run_action(db, inst, _knoten(field="prioritaet", values="highest"))
    assert r["values"] == ["highest"]
    frisch = await db.get(Issue, iid)
    await db.refresh(frisch)
    assert frisch.priority.value == "highest"


# ── A project extends its artifacts itself ───────────────────────────────────

async def test_projekt_feld_gilt_nur_dort(client, db, register):
    """The core: the owner extends THEIR tickets, not those of everybody else."""
    chef = await make_user(db, "chef", admin=True)
    meins = await make_project(db, "MEI", "Meins", inherit_members=False)
    fremd = await make_project(db, "FRE", "Fremd", inherit_members=False)
    await add_member(db, meins, chef, ProjectRole.owner)
    await add_member(db, fremd, chef, ProjectRole.owner)
    await db.commit()
    typ = await art.type_by_key(db, "ticket")
    tid, mid, fid = typ.id, meins.id, fremd.id

    r = await client.post(f"/artifact-types/{tid}/fields?project_id={mid}", headers=auth(chef),
                          json={"key": "kunde", "label": "Kunde", "kind": "text"})
    assert r.status_code == 201, r.text

    meine = [f.key for f in await felder.fields_of(db, tid, mid)]
    fremde = [f.key for f in await felder.fields_of(db, tid, fid)]
    allgemein = [f.key for f in await felder.fields_of(db, tid)]
    assert "kunde" in meine
    assert "kunde" not in fremde        # another project does not see it
    assert "kunde" not in allgemein     # and it is not valid everywhere
    # The shipped fields are all there regardless.
    assert "status" in meine and "prioritaet" in meine


async def test_projekt_feld_erscheint_am_ticket(client, db, register):
    """„Erweiterte Felder müssen in der Ticketansicht erscheinen."""
    chef = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "TKT", "Ticketansicht")
    await add_member(db, proj, chef, ProjectRole.owner)
    issue = await _ticket(db, proj)
    a = await art.ensure_for_issue(db, issue)
    await db.commit()
    aid, tid, pid = a.id, (await art.type_by_key(db, "ticket")).id, proj.id

    await client.post(f"/artifact-types/{tid}/fields?project_id={pid}", headers=auth(chef),
                      json={"key": "kunde", "label": "Kunde", "kind": "text"})

    sicht = await client.get(f"/artifacts/{aid}/values", headers=auth(chef))
    assert "kunde" in [f["key"] for f in sicht.json()["fields"]]
    r = await client.put(f"/artifacts/{aid}/values", headers=auth(chef),
                         json={"values": {"kunde": ["Vostura"]}})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["kunde"] == ["Vostura"]


async def test_fremder_darf_kein_feld_anlegen(client, db, register):
    chef = await make_user(db, "chef", admin=True)
    fremder = await make_user(db, "fremd")
    proj = await make_project(db, "ZU", "Zu", inherit_members=False)
    await add_member(db, proj, chef, ProjectRole.owner)
    await db.commit()
    tid, pid = (await art.type_by_key(db, "ticket")).id, proj.id

    r = await client.post(f"/artifact-types/{tid}/fields?project_id={pid}", headers=auth(fremder),
                          json={"key": "x", "label": "X"})
    assert r.status_code in (403, 404)


async def test_ohne_projekt_bleibt_es_admin_sache(client, db, register):
    """A field without a project applies everywhere; only an admin changes that."""
    owner = await make_user(db, "owner")
    proj = await make_project(db, "OW", "Owner")
    await add_member(db, proj, owner, ProjectRole.owner)
    await db.commit()
    tid = (await art.type_by_key(db, "ticket")).id

    r = await client.post(f"/artifact-types/{tid}/fields", headers=auth(owner),
                          json={"key": "global", "label": "Global"})
    assert r.status_code == 403


async def test_ausgeliefertes_feld_laesst_sich_nicht_entfernen(client, db, register):
    """„Die bisher notwendigen Felder können nicht entfernt werden."""
    chef = await make_user(db, "chef", admin=True)
    typ = await art.type_by_key(db, "ticket")
    status = await felder.status_field(db, typ.id)

    r = await client.delete(f"/artifact-fields/{status.id}?force=true", headers=auth(chef))
    assert r.status_code == 409
    assert "nicht löschen" in r.json()["detail"]
    # Switching off stays possible.
    assert (await client.put(f"/artifact-fields/{status.id}", headers=auth(chef),
                             json={"enabled": False})).status_code == 200


async def test_projekt_feld_darf_ausgeliefertes_nicht_verdecken(client, db, register):
    """Two fields with the same key: nobody would know which one is meant any more."""
    chef = await make_user(db, "chef", admin=True)
    proj = await make_project(db, "VD", "Verdecken")
    await add_member(db, proj, chef, ProjectRole.owner)
    await db.commit()
    tid, pid = (await art.type_by_key(db, "ticket")).id, proj.id

    r = await client.post(f"/artifact-types/{tid}/fields?project_id={pid}", headers=auth(chef),
                          json={"key": "status", "label": "Mein Status"})
    assert r.status_code == 409
