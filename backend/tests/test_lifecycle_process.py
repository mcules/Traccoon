"""The ticket lifecycle as a process: loop, gatekeeper, events.

These tests secure the three places where the migration could go wrong: continuation loops
(a back edge onto the same node), the runaway brake (which must not be switchable off over
the graph) and continuing over a comment or an answer.
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


async def _steps(db, node_id: str) -> list[WorkflowStepRun]:
    return list((await db.execute(select(WorkflowStepRun)
                                  .where(WorkflowStepRun.node_id == node_id))).scalars().all())


async def test_planung_running_bis_zur_grant(db, seeded, redis_stub):
    """Assign, the agent plans, the ticket waits for the plan approval (human sovereignty)."""
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


async def test_aufteilung_wird_as_solche_markiert(db, seeded, redis_stub):
    """A plan with <subtickets> gives another approval (plan_split), otherwise the same way."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "planned", "summary": "Plan",
                       "output": '<subtickets>[{"summary":"Teil A"}]</subtickets>'}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)
    assert issue.hold_reason == HoldReason.plan_split


async def test_fortsetzung_running_nicht_im_kreis(db, seeded, redis_stub):
    """`loop_exhausted` leads over a back edge onto the same agent node.

    Without the `routed_at` stamp the engine would translate the finished step into an edge
    again and again instead of executing it anew, and the run would turn without ever
    starting an agent. Here every round has to produce a NEW step.
    """
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.approved)
    issue.plan = "Plan"
    await db.commit()
    # First an interim state (continuation), then finished; otherwise the process would turn up to the cap.
    redis_stub["*"] = [{"status": "loop_exhausted", "summary": "Zwischenstand",
                        "worktree_fingerprint": "aaa"},
                       {"status": "done", "summary": "fertig", "worktree_fingerprint": "bbb"}]

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()

    exec_steps = await _steps(db, "exec")
    assert len(exec_steps) >= 2, "not a real continuation, the node was not executed again"
    assert all(s.routed_at is not None for s in exec_steps[:-1])
    await db.refresh(issue)
    assert issue.continuation_count >= 1


async def test_feststecker_haelt_an_statt_weiterzulaufen(db, seeded, redis_stub):
    """The same worktree fingerprint as before means stuck: halt, do not continue."""
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
    """Above the cap NO agent run may start any more, no matter what the process draws."""
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
    # No step has ever run: the run was stopped before it was queued.
    assert all(s.status == WorkflowStepStatus.pending for s in await _steps(db, "exec"))


async def test_kommentar_setzt_wartenden_prozess_fort(client, db, seeded, redis_stub):
    """A question from the agent makes the ticket wait; a comment picks the process up again."""
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

    # The answer as a comment: the event node accepts it and the agent runs on.
    redis_stub["*"] = {"status": "done", "summary": "umgesetzt"}
    r = await client.post(f"/issues/{issue.key}/comments",
                          json={"body": "So wie im Entwurf."}, headers=auth(owner))
    assert r.status_code in (200, 201), r.text
    await enginemod.drain()

    ereignis = await _steps(db, "wait_exec")
    assert ereignis and ereignis[0].decision == "comment"
    assert len(await _steps(db, "exec")) >= 2


async def test_grant_bleibt_dem_menschen_vorbehalten(client, db, seeded, redis_stub):
    """A comment must NOT skip a waiting approval."""
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

    assert issue.agent_status == TicketAgentStatus.plan_review   # unchanged, still waiting
    offen = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().all()
    assert [s.node_id for s in offen] == ["approve_plan"]


async def test_planung_running_nicht_endlos_im_kreis(db, seeded, redis_stub):
    """The planning has a continuation budget as well.

    The back edge "keep planning" led back to `plan` unbraked: an architect that tears its
    iteration limit every time started the next run every ninety seconds, and nobody counted
    that (TRA-31 on 2026-08-07). After `PLAN_FORTSETZUNGEN` attempts it stops, and then a
    human is needed, not the eleventh attempt.
    """
    from app.services.workflow_seed import PLAN_CONTINUATIONS

    owner, proj, issue, _ = await _projekt_mit_ticket(db)
    redis_stub["*"] = {"status": "loop_exhausted", "summary": "komme nicht weiter"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id)
    await enginemod.drain()
    await db.refresh(issue)

    plan_steps = await _steps(db, "plan")
    assert len(plan_steps) <= PLAN_CONTINUATIONS + 1, "the planning turns unbraked"
    assert issue.continuation_count >= PLAN_CONTINUATIONS
    assert issue.agent_status == TicketAgentStatus.hold      # waits for a human
    wartend = (await db.execute(select(WorkflowStepRun).where(
        WorkflowStepRun.status == WorkflowStepStatus.waiting))).scalars().all()
    assert [s.node_id for s in wartend] == ["wait_plan"]


async def test_grant_setzt_die_fortsetzungs_zaehlung_zurueck(db, seeded, redis_stub):
    """Planning and implementation share a counter: a tough planning must not eat the
    implementation's budget before it has written the first line."""
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


async def test_zuweisen_ueber_den_assistenten_startet_den_prozess(db, seeded, redis_stub):
    """The assistant operates Traccoon over native tools, not over the API. If it only set
    fields, the ticket would lie there with an agent and a status without a process running,
    and only a backend restart would ever catch it again (TRA-32 on 2026-08-07)."""
    from app.services.lifecycle_flow import live_instance, start_lifecycle

    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.planning)
    assert await live_instance(db, issue) is None

    inst = await start_lifecycle(db, issue, owner.id, advance_now=False, entry="plan")
    await db.commit()

    assert inst is not None
    assert await live_instance(db, issue) is not None
    # The token is ACTIVE, not waiting: exactly by that the 30 s tick of the backend
    # recognises that it has to continue here; advance deliberately does not run in the worker.
    tok = (await db.execute(select(WorkflowToken).where(
        WorkflowToken.instance_id == inst.id))).scalars().first()
    assert tok.state == WorkflowTokenState.active


async def test_verwaistes_ticket_wird_im_tick_eingesammelt(db, seeded, redis_stub):
    """The safety net: what stands there without an instance is fetched by the tick, not only by the restart."""
    from app.services.lifecycle_flow import adopt_orphans, live_instance

    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.planning)
    redis_stub["*"] = {"status": "planned", "output": "Plan", "summary": "Plan"}
    assert await live_instance(db, issue) is None

    n = await adopt_orphans(db)
    await enginemod.drain()

    assert n == 1
    assert await live_instance(db, issue) is not None


async def test_startender_agent_hebt_ueberholten_hold_auf(db, seeded, redis_stub):
    """If an agent is running again, the old hold reason is history; otherwise the board
    shows "hold - merge" while work is going on."""
    owner, proj, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.hold)
    issue.hold_reason = HoldReason.merge
    issue.plan = "Plan"
    await db.commit()
    redis_stub["*"] = {"status": "done", "summary": "fertig", "worktree_fingerprint": "x"}

    from app.services.lifecycle_flow import start_lifecycle
    await start_lifecycle(db, issue, owner.id, entry="exec")
    await enginemod.drain()
    await db.refresh(issue)

    assert issue.agent_status != TicketAgentStatus.hold
    assert issue.hold_reason is None
