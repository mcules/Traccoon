"""Die Vorgangsart wählt den Prozess.

Bis hierher fuhr jedes Ticket eines Projekts denselben Lebenszyklus. Jetzt darf ein Bug
einen eigenen haben, während Aufgabe und Anforderung weiter dem Satz folgen — die Kopie
hängt dafür an der Vorgangsart.

Auflösung: Vorgangsart → projekteigen (allgemein) → Satz → Owner-Satz → Standard.
"""
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import workflow_sets as sets
from app.services.workflow_seed import ensure_builtin_set
from conftest import add_member, auth, make_project, make_user
from sqlalchemy import select

SLOT = "ticket_lifecycle"


async def _projekt_mit_arten(db, key="VGA"):
    proj = await make_project(db, key, "Vorgangsarten")
    aufgabe = IssueType(project_id=proj.id, name="Aufgabe", order=0)
    bug = IssueType(project_id=proj.id, name="Bug", order=1)
    db.add_all([aufgabe, bug, IssueCounter(project_id=proj.id, last_number=0),
                WorkflowStatus(project_id=proj.id, name="To Do",
                               category=StatusCategory.todo, order=0)])
    await db.commit()
    return proj, aufgabe, bug


async def test_ohne_eigene_kopie_gilt_fuer_alle_dasselbe(db):
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_arten(db)

    fuer_aufgabe = await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)
    fuer_bug = await sets.resolve_definition(db, proj.id, SLOT, bug.id)
    assert fuer_aufgabe is not None
    assert fuer_aufgabe.id == fuer_bug.id      # beide folgen dem Standard


async def test_kopie_fuer_eine_vorgangsart_gilt_nur_dort(db):
    """Der Kern: ein eigener Ablauf für Bugs lässt alle anderen unberührt."""
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_arten(db)
    standard = await sets.resolve_definition(db, proj.id, SLOT)

    eigen = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    assert eigen.issue_type_id == bug.id

    assert (await sets.resolve_definition(db, proj.id, SLOT, bug.id)).id == eigen.id
    assert (await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)).id == standard.id
    # Und ohne Angabe der Vorgangsart bleibt es beim Standard.
    assert (await sets.resolve_definition(db, proj.id, SLOT)).id == standard.id


async def test_allgemeine_kopie_greift_wo_keine_besondere_steht(db):
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_arten(db)
    allgemein = await sets.customize(db, proj, SLOT, actor_id=None)
    fuer_bug = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)

    assert (await sets.resolve_definition(db, proj.id, SLOT, bug.id)).id == fuer_bug.id
    assert (await sets.resolve_definition(db, proj.id, SLOT, aufgabe.id)).id == allgemein.id


async def test_zweimal_anpassen_liefert_dieselbe_kopie(db):
    """Sonst entstünden stille Doppel — der Index verbietet sie ohnehin."""
    await ensure_builtin_set(db)
    proj, _, bug = await _projekt_mit_arten(db)
    a = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    b = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)
    assert a.id == b.id


async def test_lebenszyklus_startet_den_ablauf_der_vorgangsart(db):
    """Nicht nur die Auflösung — der echte Start muss die Vorgangsart berücksichtigen."""
    from app.services.lifecycle_flow import start_lifecycle
    await ensure_builtin_set(db)
    proj, aufgabe, bug = await _projekt_mit_arten(db)
    eigen = await sets.customize(db, proj, SLOT, actor_id=None, issue_type_id=bug.id)

    spalte = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == proj.id))).scalars().first()
    ticket = Issue(project_id=proj.id, number=1, key="VGA-1", type_id=bug.id,
                   status_id=spalte.id, summary="Ein Bug", reporter_id=1, rank="0001",
                   assigned_agent="dev")
    db.add(ticket)
    await db.commit()

    inst = await start_lifecycle(db, ticket, None, entry="plan", advance_now=False)
    await db.commit()
    assert inst is not None and inst.definition_id == eigen.id


async def test_api_legt_kopie_je_vorgangsart_an(client, db):
    await ensure_builtin_set(db)
    chef = await make_user(db, "chef", admin=True)
    proj, aufgabe, bug = await _projekt_mit_arten(db)
    await add_member(db, proj, chef, ProjectRole.owner)
    await db.commit()
    pid, bug_id, aufgabe_id = proj.id, bug.id, aufgabe.id

    r = await client.post(
        f"/projects/{pid}/workflow-slots/{SLOT}/customize?issue_type_id={bug_id}",
        headers=auth(chef))
    assert r.status_code == 201, r.text
    assert r.json()["issue_type_id"] == bug_id

    # Die Aufgabe folgt weiterhin dem Satz.
    fuer_aufgabe = await sets.resolve_definition(db, pid, SLOT, aufgabe_id)
    assert fuer_aufgabe.id != r.json()["id"]
