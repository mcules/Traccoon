import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.notification import Notification
from ..models.user import User
from .deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _q_own(user: User):
    return or_(Notification.user_id == user.id, Notification.user_id.is_(None))


@router.get("")
async def list_notifications(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (
        await db.execute(select(Notification).where(_q_own(user))
                         .order_by(Notification.id.desc()).limit(50))
    ).scalars().all()
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
             "issue_id": n.issue_id, "project_id": n.project_id,
             "read": n.read_at is not None, "created_at": n.created_at} for n in rows]


@router.get("/unread-count")
async def unread_count(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    c = (await db.execute(
        select(func.count()).select_from(Notification)
        .where(_q_own(user), Notification.read_at.is_(None)))).scalar_one()
    return {"count": c}


@router.post("/{nid}/read", status_code=204)
async def mark_read(nid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    n = await db.get(Notification, nid)
    if n is not None:
        n.read_at = dt.datetime.now(tz=dt.timezone.utc)
        await db.commit()


@router.post("/read-all", status_code=204)
async def read_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    await db.execute(update(Notification).where(_q_own(user), Notification.read_at.is_(None))
                     .values(read_at=dt.datetime.now(tz=dt.timezone.utc)))
    await db.commit()
