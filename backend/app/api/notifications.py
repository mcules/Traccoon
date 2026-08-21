"""The bell: what still wants something from the person.

Whatever went out over the messenger has been read there. Showing it under the bell a
second time makes the bell a second, permanently full inbox and its counter wallpaper,
which is how the 231 assistant cards of this installation ended up sitting behind a red
dot that nobody could clear meaningfully.

So the bell keeps two sorts of row:

* nothing went out (`notified_at IS NULL`): bell only, or a mail that did not make it, so
  this row is the only trace of the message;
* something is still open: the card went out, but its subject is still waiting for a
  decision (a plan in review, a pending permission request, an inbox item nobody
  released).

Everything else is history and reachable over `?all=1`, because "where was that message
again" needs a place to look.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.assistant import AssistantTask, SpamVerdict
from ..models.enums import TicketAgentStatus
from ..models.notification import Notification
from ..models.ops import PermRequest
from ..models.ticket import Issue
from ..models.user import User
from .deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Ticket cards: the kind names the AI state the ticket has to be in for the card to still
# be waiting. Moves the ticket on, the card is done, no matter who moved it or where.
_TICKET_STATE = {
    "plan_review": TicketAgentStatus.plan_review,
    "to_test": TicketAgentStatus.to_test,
    "failed": TicketAgentStatus.failed,
}


def _offen():
    """Condition: this notification still waits for a decision.

    Deliberately asked over the subject and not over a flag on the notification itself. A
    decision falls in three places (browser, messenger button, an agent that carries on),
    and only two of them would ever remember to tick a flag here.
    """
    ticket = [
        and_(Notification.kind == kind,
             select(Issue.id).where(Issue.id == Notification.issue_id,
                                    Issue.agent_status == state).exists())
        for kind, state in _TICKET_STATE.items()
    ]
    return or_(
        *ticket,
        # Permission request: the run hangs until somebody decides.
        and_(Notification.kind.in_(("blocked", "permission")),
             select(PermRequest.id).where(PermRequest.issue_id == Notification.issue_id,
                                          PermRequest.status == "pending").exists()),
        and_(Notification.kind == "assistant_review",
             select(AssistantTask.id).where(AssistantTask.id == Notification.assistant_task_id,
                                            AssistantTask.status == "new").exists()),
        and_(Notification.kind == "assistant_perm",
             select(AssistantTask.id).where(AssistantTask.id == Notification.assistant_task_id,
                                            AssistantTask.status == "awaiting").exists()),
        and_(Notification.kind.in_(("spam_review", "spam_digest")),
             select(SpamVerdict.id).where(SpamVerdict.id == Notification.spam_verdict_id,
                                          SpamVerdict.status == "pending").exists()),
    )


def _q_own(user: User):
    return or_(Notification.user_id == user.id, Notification.user_id.is_(None))


def _q_visible(user: User, alle: bool = False):
    if alle:
        return _q_own(user)
    return and_(_q_own(user), or_(Notification.notified_at.is_(None), _offen()))


@router.get("")
async def list_notifications(all: bool = False, user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    rows = (
        await db.execute(select(Notification).where(_q_visible(user, all))
                         .order_by(Notification.id.desc()).limit(50))
    ).scalars().all()
    # Ticket key and project key alongside, so a click on the card can lead somewhere. The
    # bare `issue_id` is of no use to the browser: the ticket page runs on keys.
    ids = {n.issue_id for n in rows if n.issue_id}
    targets: dict[int, tuple[str, str]] = {}
    if ids:
        from ..models.project import Project
        for issue_key, projekt_key, iid in (await db.execute(
                select(Issue.key, Project.key, Issue.id)
                .join(Project, Project.id == Issue.project_id).where(Issue.id.in_(ids)))).all():
            targets[iid] = (issue_key, projekt_key)
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
             "issue_id": n.issue_id, "project_id": n.project_id,
             "issue_key": targets.get(n.issue_id or 0, ("", ""))[0],
             "project_key": targets.get(n.issue_id or 0, ("", ""))[1],
             "assistant_task_id": n.assistant_task_id,
             # `gesendet` says whether the message also went out somewhere else. In the
             # unfiltered list that is the difference between "still open" and "history".
             "sent": n.notified_at is not None,
             "read": n.read_at is not None, "created_at": n.created_at} for n in rows]


@router.get("/unread-count")
async def unread_count(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Counts only what the bell shows. A counter over hidden rows could never be cleared."""
    c = (await db.execute(
        select(func.count()).select_from(Notification)
        .where(_q_visible(user), Notification.read_at.is_(None)))).scalar_one()
    return {"count": c}


@router.post("/{nid}/read", status_code=204)
async def mark_read(nid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    n = await db.get(Notification, nid)
    if n is not None:
        n.read_at = dt.datetime.now(tz=dt.timezone.utc)
        await db.commit()


@router.post("/read-all", status_code=204)
async def read_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    # Marks the hidden rows as well: whoever clears the bell means the whole backlog, and a
    # row that reappears later (`?all=1`) as unread would be a counter without a bell.
    await db.execute(update(Notification).where(_q_own(user), Notification.read_at.is_(None))
                     .values(read_at=dt.datetime.now(tz=dt.timezone.utc)))
    await db.commit()
