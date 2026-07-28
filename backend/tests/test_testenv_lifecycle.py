"""Lebenszyklus der Testumgebungen (ABC-18) — die Teile, die ohne Docker prüfbar sind:
Board-Spalte „Testen", 409-Sperre gegen den direkten Sprung auf „Fertig", und dass
`/complete` ohne sauberen Merge kein „Fertig" setzt.
"""
import app.services.workflow_engine as enginemod
from app.models.enums import ProjectRole, StatusCategory, TicketAgentStatus
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from conftest import add_member, auth, make_project, make_user


async def _seed_board(db, project):
    """Minimaler Projekt-Satz: Typen + Status inkl. „Testen", Zähler."""
    t = IssueType(project_id=project.id, name="Aufgabe")
    db.add(t)
    stats = {}
    for i, (name, cat) in enumerate([
        ("To Do", StatusCategory.todo), ("In Arbeit", StatusCategory.in_progress),
        ("Warten", StatusCategory.in_progress), ("Testen", StatusCategory.in_progress),
        ("Fertig", StatusCategory.done),
    ]):
        s = WorkflowStatus(project_id=project.id, name=name, category=cat, order=i)
        db.add(s)
        stats[name] = s
    db.add(IssueCounter(project_id=project.id, last_number=0))
    await db.commit()
    for s in stats.values():
        await db.refresh(s)
    await db.refresh(t)
    return t, stats


async def _make_issue(db, project, type_id, status_id, agent_status=None):
    i = Issue(project_id=project.id, number=1, key=f"{project.key}-1", type_id=type_id,
              status_id=status_id, summary="Test", reporter_id=1, rank="0001",
              agent_status=agent_status)
    db.add(i)
    await db.commit()
    await db.refresh(i)
    return i


async def test_direkter_sprung_auf_fertig_wird_abgewiesen(client, db):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    t, stats = await _seed_board(db, proj)
    issue = await _make_issue(db, proj, t.id, stats["Testen"].id, TicketAgentStatus.to_test)

    r = await client.put(f"/issues/{issue.key}/move",
                         json={"status_id": stats["Fertig"].id, "position": 0},
                         headers=auth(owner))
    assert r.status_code == 409
    assert "Auf Fertig setzen" in r.json()["detail"]


async def test_move_in_andere_spalten_bleibt_erlaubt(client, db):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    await add_member(db, proj, owner, ProjectRole.owner)
    t, stats = await _seed_board(db, proj)
    issue = await _make_issue(db, proj, t.id, stats["Testen"].id, TicketAgentStatus.to_test)

    r = await client.put(f"/issues/{issue.key}/move",
                         json={"status_id": stats["Warten"].id, "position": 0},
                         headers=auth(owner))
    assert r.status_code == 200


async def test_ohne_testenv_flow_ist_fertig_frei(client, db):
    """Projekt-Toggle aus → altes Verhalten, der Board-Zug nach „Fertig" bleibt offen."""
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    proj.testenv_enabled = False
    await db.commit()
    await add_member(db, proj, owner, ProjectRole.owner)
    t, stats = await _seed_board(db, proj)
    issue = await _make_issue(db, proj, t.id, stats["Testen"].id, TicketAgentStatus.to_test)

    r = await client.put(f"/issues/{issue.key}/move",
                         json={"status_id": stats["Fertig"].id, "position": 0},
                         headers=auth(owner))
    assert r.status_code == 200


async def _bis_zur_abnahme(db, issue, merge_result, redis_stub):
    """Ticket in den Lebenszyklus übernehmen (steht dann an der Abnahme) und das
    Merge-Ergebnis des Workers vorgeben."""
    from app.services.lifecycle_flow import adopt_orphans
    redis_stub["*"] = merge_result
    await adopt_orphans(db)
    await db.refresh(issue)
    assert issue.workflow_instance_id, "Ticket wurde nicht in den Prozess übernommen"


async def test_complete_setzt_kein_fertig_bei_merge_konflikt(client, db, seeded, redis_stub):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    m = await add_member(db, proj, owner, ProjectRole.owner)
    m.ai_assign = True          # /complete verlangt das KI-Recht
    await db.commit()
    t, stats = await _seed_board(db, proj)
    issue = await _make_issue(db, proj, t.id, stats["Testen"].id, TicketAgentStatus.to_test)
    issue.assigned_agent = "developer"
    issue.assigned_by_user_id = owner.id
    await db.commit()

    await _bis_zur_abnahme(db, issue, {"status": "conflict", "error": "Merge-Konflikt: app.py",
                                       "escalate": True}, redis_stub)

    r = await client.post(f"/issues/{issue.key}/complete", headers=auth(owner))
    assert r.status_code == 200, r.text
    await enginemod.drain()          # Merge läuft asynchron im Abnahme-Prozess
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.resolved_at is None
    assert issue.status_id != stats["Fertig"].id   # kein stilles „Fertig"


async def test_complete_setzt_fertig_bei_sauberem_merge(client, db, seeded, redis_stub):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    m = await add_member(db, proj, owner, ProjectRole.owner)
    m.ai_assign = True
    await db.commit()
    t, stats = await _seed_board(db, proj)
    issue = await _make_issue(db, proj, t.id, stats["Testen"].id, TicketAgentStatus.to_test)
    issue.assigned_agent = "developer"
    issue.assigned_by_user_id = owner.id
    await db.commit()

    await _bis_zur_abnahme(db, issue, {"status": "merged"}, redis_stub)

    r = await client.post(f"/issues/{issue.key}/complete", headers=auth(owner))
    assert r.status_code == 200, r.text
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.done
    assert issue.resolved_at is not None
    assert issue.status_id == stats["Fertig"].id


async def test_neues_projekt_hat_testen_spalte(client, db):
    owner = await make_user(db, "owner", admin=True)
    r = await client.post("/projects", json={"name": "Frisch"}, headers=auth(owner))
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    meta = await client.get(f"/projects/{pid}/meta", headers=auth(owner))
    names = [s["name"] for s in meta.json()["statuses"]]
    assert "Testen" in names
    assert names.index("Testen") < names.index("Fertig")
