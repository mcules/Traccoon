"""Der Ticket-Lebenszyklus als Prozess: Schleife, Torwächter, Ereignisse.

Diese Tests sichern die drei Stellen ab, an denen die Migration schiefgehen konnte:
Fortsetzungs-Schleifen (Rückkante auf denselben Knoten), die Runaway-Bremse (darf nicht
per Graph abschaltbar sein) und das Fortsetzen per Kommentar/Antwort.
"""
from app.models.enums import (
    HoldReason, ProjectRole, StatusCategory, TicketAgentStatus, WorkflowStepStatus,
    WorkflowTokenState,
)
from app.models.agents import Run
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.models.workflow import WorkflowStepRun, WorkflowToken
from sqlalchemy import select
import app.services.workflow_engine as enginemod
from conftest import add_member, auth, make_project, make_user


async def _projekt_mit_ticket(db, agent_status=TicketAgentStatus.planning):
    owner = await make_user(db, "owner")
    proj = await make_project(db, "TST", "Test")
    m = await add_member(db, proj, owner, ProjectRole.owner)
    m.ai_assign = True
    t = IssueType(project_id=proj.id, name="Aufgabe")
    stats = {}
    for i, (name, cat) in enumerate([("To Do", StatusCategory.todo),
                                     ("In Arbeit", StatusCategory.in_progress),
                                     ("Warten", StatusCategory.in_progress),
                                     ("Testen", StatusCategory.in_progress),
                                     ("Fertig", StatusCategory.done)]):
        s = WorkflowStatus(project_id=proj.id, name=name, category=cat, order=i)
        db.add(s)
        stats[name] = s
    db.add_all([t, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id,
                  status_id=stats["To Do"].id, summary="Test", reporter_id=owner.id,
                  rank="0001", assigned_agent="developer", assigned_by_user_id=owner.id,
                  agent_status=agent_status)
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    return owner, proj, issue, stats


async def _schritte(db, node_id: str) -> list[WorkflowStepRun]:
    return list((await db.execute(select(WorkflowStepRun)
                                  .where(WorkflowStepRun.node_id == node_id))).scalars().all())


async def test_planung_laeuft_bis_zur_freigabe(db, seeded, redis_stub):
    """Zuweisen → Agent plant → Ticket wartet auf die Plan-Freigabe (Menschenhoheit)."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "planned", "output": "Der Plan.", "summary": "Plan"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)

    assert issue.plan == "Der Plan."
    assert issue.agent_status == TicketAgentStatus.plan_review
    assert issue.hold_reason == HoldReason.plan_review
    wartend = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().all()
    assert [s.node_id for s in wartend] == ["approve_plan"]


async def test_aufteilung_wird_als_solche_markiert(db, seeded, redis_stub):
    """Plan mit <subtickets> → andere Freigabe (plan_split), sonst identischer Weg."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "planned", "summary": "Plan",
                       "output": '<subtickets>[{"summary":"Teil A"}]</subtickets>'}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.hold_reason == HoldReason.plan_split


async def test_fortsetzung_laeuft_nicht_im_kreis(db, seeded, redis_stub):
    """`loop_exhausted` führt über eine Rückkante auf denselben Agenten-Knoten.

    Ohne den `routed_at`-Stempel würde die Engine den erledigten Schritt immer wieder in
    eine Kante übersetzen, statt neu auszuführen — der Lauf drehte sich, ohne je einen
    Agenten zu starten. Hier muss jede Runde einen NEUEN Schritt erzeugen.
    """
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    await db.commit()
    # Erst ein Zwischenstand (Fortsetzung), dann fertig — sonst drehte der Prozess bis zum Cap.
    redis_stub["*"] = [{"status": "loop_exhausted", "summary": "Zwischenstand",
                        "worktree_fingerprint": "aaa"},
                       {"status": "done", "summary": "fertig", "worktree_fingerprint": "bbb"}]

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()

    exec_schritte = await _schritte(db, "exec")
    assert len(exec_schritte) >= 2, "keine echte Fortsetzung — Knoten wurde nicht erneut ausgeführt"
    assert all(s.routed_at is not None for s in exec_schritte[:-1])
    await db.refresh(issue)
    assert issue.continuation_count >= 1


async def test_feststecker_haelt_an_statt_weiterzulaufen(db, seeded, redis_stub):
    """Gleicher Worktree-Fingerabdruck wie zuvor → Feststecker: anhalten, nicht fortsetzen."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution",
               worktree_fingerprint="gleich"))
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution",
               worktree_fingerprint="gleich"))
    await db.commit()
    redis_stub["*"] = {"status": "loop_exhausted", "summary": "hängt",
                       "worktree_fingerprint": "gleich"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()
    await db.refresh(issue)

    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason == HoldReason.stuck


async def test_runaway_bremse_greift_auch_im_graphen(db, seeded, redis_stub):
    """Über dem Cap darf KEIN Agentenlauf mehr starten — egal was der Prozess zeichnet."""
    from app.services import agent_gate

    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    for _ in range(agent_gate.MAX_RUNS_PER_TICKET):
        db.add(Run(issue_id=issue.id, agent="developer", phase="execution"))
    await db.commit()
    redis_stub["*"] = {"status": "done", "summary": "fertig"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()
    await db.refresh(issue)

    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason == HoldReason.cap
    assert issue.agent_working is False
    token = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.state == WorkflowTokenState.waiting))).scalars().first()
    assert token is not None and token.waiting_for == "gate"
    # Kein Schritt ist je gelaufen — der Lauf wurde vor dem Einreihen gestoppt.
    assert all(s.status == WorkflowStepStatus.pending for s in await _schritte(db, "exec"))


async def test_kommentar_setzt_wartenden_prozess_fort(client, db, seeded, redis_stub):
    """Rückfrage des Agenten → Ticket wartet; ein Kommentar nimmt den Prozess wieder auf."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    await db.commit()
    redis_stub["*"] = {"status": "blocked", "summary": "Wie soll X aussehen?",
                       "blocker": {"kind": "question"}}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason == HoldReason.question

    # Antwort als Kommentar → der Ereignis-Knoten nimmt sie an, der Agent läuft weiter.
    redis_stub["*"] = {"status": "done", "summary": "umgesetzt"}
    r = await client.post(f"/issues/{issue.key}/comments",
                          json={"body": "So wie im Entwurf."}, headers=auth(owner))
    assert r.status_code in (200, 201), r.text
    await enginemod.drain()

    ereignis = await _schritte(db, "wait_exec")
    assert ereignis and ereignis[0].decision == "comment"
    assert len(await _schritte(db, "exec")) >= 2


async def test_freigabe_bleibt_dem_menschen_vorbehalten(client, db, seeded, redis_stub):
    """Ein Kommentar darf eine wartende Freigabe NICHT überspringen."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "planned", "output": "Plan", "summary": "Plan"}
    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()

    r = await client.post(f"/issues/{issue.key}/comments",
                          json={"body": "Noch eine Anmerkung."}, headers=auth(owner))
    assert r.status_code in (200, 201)
    await enginemod.drain()
    await db.refresh(issue)

    assert issue.agent_status == TicketAgentStatus.plan_review   # unverändert wartend
    offen = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().all()
    assert [s.node_id for s in offen] == ["approve_plan"]


async def test_planung_laeuft_nicht_endlos_im_kreis(db, seeded, redis_stub):
    """Auch die Planung hat ein Fortsetzungs-Budget.

    Die Rückkante „weiter planen" führte ungebremst auf `plan` zurück: ein Architekt, der
    jedes Mal sein Iterations-Limit reißt, startete im Neunzig-Sekunden-Takt den nächsten
    Lauf, und gezählt hat das niemand (ABC-31 am 2026-08-07). Nach `PLAN_FORTSETZUNGEN`
    Anläufen ist Schluss — dann braucht es einen Menschen, nicht den elften Versuch.
    """
    from app.services.workflow_seed import PLAN_FORTSETZUNGEN

    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "loop_exhausted", "summary": "komme nicht weiter"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)

    plan_schritte = await _schritte(db, "plan")
    assert len(plan_schritte) <= PLAN_FORTSETZUNGEN + 1, "Planung dreht sich ungebremst"
    assert issue.continuation_count >= PLAN_FORTSETZUNGEN
    assert issue.agent_status == TicketAgentStatus.hold      # wartet auf einen Menschen
    wartend = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().all()
    assert [s.node_id for s in wartend] == ["wait_plan"]


async def test_freigabe_setzt_die_fortsetzungs_zaehlung_zurueck(db, seeded, redis_stub):
    """Planung und Umsetzung teilen sich einen Zähler — eine zähe Planung darf der
    Umsetzung nicht ihr Budget wegessen, bevor sie die erste Zeile geschrieben hat."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    issue.continuation_count = 7
    await db.commit()
    redis_stub["*"] = {"status": "done", "summary": "fertig", "worktree_fingerprint": "bbb"}

    from app.services.lifecycle_flow import start_lifecycle
    from app.services.workflow_actions import run_action
    inst = await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()

    await run_action(db, inst, {"id": "cap_baseline", "type": "auto_action", "data": {
        "config": {"action": {"action": "set_cap_baseline", "params": {}}}}})
    await db.commit()
    await db.refresh(issue)
    assert issue.continuation_count == 0
    assert (inst.context or {}).get("continuation") == 0
