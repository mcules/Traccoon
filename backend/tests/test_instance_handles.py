"""Two handles on a run that has ended: start it again, and remove it.

A failed run used to stay in the operations list for ever, and the only thing one could do
with it was to cancel it. So six identical failures of the same flow stood there, none of
them removable, and after fixing the cause there was no way to say "then do it again".

What is checked here is what those two handles must NOT do: restart a run that is still going
(there would be two of them on the same subject), delete one under the engine's feet, or leave
a ticket pointing at a run that is gone.
"""
import pytest
from app.models.enums import (
    WorkflowInstanceStatus, WorkflowSubjectKind, WorkflowVersionStatus,
)
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services.workflow_engine import start_workflow
from sqlalchemy import select

from conftest import add_member, auth, make_project, make_user

pytestmark = pytest.mark.asyncio

# A flow that stops at a human task: it does not run to the end by itself, so the test can
# put it into whatever state it wants to look at.
GRAPH = {
    "nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"label": "Start"}}},
        {"id": "frage", "type": "human_task", "position": {"x": 0, "y": 1},
         "data": {"config": {"label": "Rückmeldung", "assignee": {"kind": "starter"}}}},
        {"id": "ende", "type": "end", "position": {"x": 0, "y": 2},
         "data": {"config": {"outcome": "completed"}}},
    ],
    "edges": [{"id": "e1", "source": "s", "target": "frage"},
              {"id": "e2", "source": "frage", "target": "ende"}],
}


async def _flow(db, user, key: str) -> WorkflowDefinition:
    d = WorkflowDefinition(project_id=None, key=key, name=key, created_by=user.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=GRAPH,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()
    return d


async def test_restart_makes_a_new_run_and_retires_the_old_one(client, db):
    user = await make_user(db, "starter")
    d = await _flow(db, user, "again")
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"was": "damals"}, actor_id=user.id)
    inst.status = WorkflowInstanceStatus.failed
    inst.error = "No module named 'imapclient'"
    await db.commit()

    r = await client.post(f"/workflow-instances/{inst.id}/restart", headers=auth(user))
    assert r.status_code == 201
    fresh = r.json()
    assert fresh["id"] != inst.id
    # Same starting point as the run that failed — otherwise it is not a repetition.
    assert fresh["context"]["was"] == "damals"

    await db.refresh(inst)
    # The old one is dealt with and leaves the operations list; it is not deleted, its error
    # message is the reason one restarted at all.
    assert inst.status == WorkflowInstanceStatus.cancelled
    assert inst.error == "No module named 'imapclient'"


async def test_a_running_run_is_not_restarted(client, db):
    user = await make_user(db, "eager")
    d = await _flow(db, user, "runs")
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=user.id)

    r = await client.post(f"/workflow-instances/{inst.id}/restart", headers=auth(user))
    assert r.status_code == 409
    assert (await client.get("/processes/running", headers=auth(user))).json().__len__() == 1


async def test_delete_only_after_the_run_has_ended(client, db):
    user = await make_user(db, "tidy")
    d = await _flow(db, user, "gone")
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=user.id)

    # Still waiting for a human: deleting it would leave the engine with a token pointing
    # into nothing.
    assert (await client.delete(f"/workflow-instances/{inst.id}",
                                headers=auth(user))).status_code == 409

    inst.status = WorkflowInstanceStatus.failed
    await db.commit()
    assert (await client.delete(f"/workflow-instances/{inst.id}",
                                headers=auth(user))).status_code == 204
    assert (await db.execute(select(WorkflowInstance).where(
        WorkflowInstance.id == inst.id))).scalars().first() is None


async def test_a_foreign_free_flow_stays_out_of_reach(client, db):
    owner = await make_user(db, "owner")
    stranger = await make_user(db, "stranger")
    d = await _flow(db, owner, "private")
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={}, actor_id=owner.id)
    inst.status = WorkflowInstanceStatus.failed
    await db.commit()

    assert (await client.delete(f"/workflow-instances/{inst.id}",
                                headers=auth(stranger))).status_code == 403
    assert (await client.post(f"/workflow-instances/{inst.id}/restart",
                              headers=auth(stranger))).status_code == 403


async def test_deleting_frees_the_ticket_that_pointed_at_the_run(client, db):
    """The one reference without a foreign key.

    A ticket carries the id of its lifecycle run as a plain number — the two point at each
    other, and a real key would be a cycle between the tables. Nothing therefore clears that
    number by itself, and a ticket left pointing at a deleted run sends every reader of it
    (board, drawer, engine) looking for something that is not there.
    """
    from app.models.enums import ProjectRole, StatusCategory
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus

    user = await make_user(db, "keeper", admin=True)
    project = await make_project(db, "TCK", "Tickets")
    await add_member(db, project, user, ProjectRole.member)
    kind = IssueType(project_id=project.id, name="Aufgabe")
    state = WorkflowStatus(project_id=project.id, name="To Do",
                           category=StatusCategory.todo, order=0)
    db.add_all([kind, state, IssueCounter(project_id=project.id, last_number=0)])
    await db.commit()
    issue = Issue(project_id=project.id, number=1, key="TCK-1", type_id=kind.id,
                  status_id=state.id, summary="Erstes", reporter_id=user.id, rank="0001")
    db.add(issue)
    await db.commit()

    d = await _flow(db, user, "lifecycle")
    d.project_id = project.id
    await db.commit()
    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                issue_id=issue.id, context={}, actor_id=user.id)
    issue.workflow_instance_id = inst.id
    inst.status = WorkflowInstanceStatus.failed
    await db.commit()

    assert (await client.delete(f"/workflow-instances/{inst.id}",
                                headers=auth(user))).status_code == 204
    await db.refresh(issue)
    assert issue.workflow_instance_id is None
