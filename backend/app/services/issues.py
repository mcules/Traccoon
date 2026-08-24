"""Creating a ticket from code, without an HTTP request behind it.

The route `POST /projects/{key}/issues` and the flow action `create_ticket` each carry their
own copy of these twenty lines (type, state, counter, key, artifact). This is the third
caller, and rather than a third copy it gets a function. The two older ones keep working;
whoever touches them next can move them over.

What this function deliberately does NOT do is decide anything: no permission check, no
default project, no assignment to an agent. The caller knows why it wants a ticket, this
only knows how one is built.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import Priority
from ..models.project import Project
from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus


class NoTargetProject(ValueError):
    """The project cannot carry a ticket: type, state or counter are missing."""


async def new_issue(db: AsyncSession, *, project_id: int, summary: str, description: str,
                    reporter_id: int, priority: Priority = Priority.medium,
                    source: str = "") -> Issue:
    """Build a ticket in this project and hand it back, committed and refreshed."""
    project = await db.get(Project, project_id)
    kind = (await db.execute(select(IssueType).where(IssueType.project_id == project_id)
                             .order_by(IssueType.order))).scalars().first()
    state = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == project_id)
                              .order_by(WorkflowStatus.order))).scalars().first()
    # The counter row is locked, not read: two reports arriving at the same second would
    # otherwise both get the same ticket number, and the key is unique.
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == project_id)
                                .with_for_update())).scalar_one_or_none()
    if not (project and kind and state and counter):
        raise NoTargetProject("The project has no issue type, state or counter")

    counter.last_number += 1
    number = counter.last_number
    issue = Issue(project_id=project_id, number=number, key=f"{project.key}-{number}"[:50],
                  type_id=kind.id, status_id=state.id, priority=priority,
                  summary=(summary or "")[:500], description=description,
                  reporter_id=reporter_id, rank=f"{number:08d}", source=source)
    db.add(issue)
    await db.flush()

    from .artifacts import ensure_for_issue
    await ensure_for_issue(db, issue)
    await db.commit()
    await db.refresh(issue)

    from .events import emit
    await emit(db, "issue.created", project_id=project_id, issue_id=issue.id,
               actor_id=reporter_id,
               payload={"issue": {"key": issue.key, "summary": issue.summary,
                                  "priority": issue.priority.value}})
    return issue
