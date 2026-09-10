"""How long the protocols are kept, and who deletes them.

Traccoon writes several kinds of protocol, and until now exactly one of them was ever
cleaned up: archived agent runs. That rule missed almost everything it was meant to
catch, because a run is only archived when its TICKET is archived — and the runs that
actually pile up have no ticket at all. They come from jobs and flows (the hourly
operators, the watchers, the assistant). Measured before this module existed: 1495 of
1631 runs had no ticket, so the retention covered 8 % of them, `run_steps` had grown to
56 MB and the oldest run was still there from the first day.

So the rule here hangs on TIME and on a finished state, not on the archive flag:
whatever is done and older than its period goes. What is still running, waiting or
blocked stays, however old — a blocked run can still be answered.

Deliberately NOT swept:
  * `cost_entries` — that is accounting, not protocol. It has to stay evaluable in
    hindsight, and it survives a deleted run (the foreign key sets the reference to
    NULL instead of taking the row with it).
  * `assistant_tasks` — a finished task is still the context of the conversation that
    followed it.

Deleting happens in chunks and with an upper bound per sweep. The first sweep after an
upgrade faces months of backlog, and a single DELETE over ten thousand rows holds locks
that the running agents feel.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..db import SessionLocal
from .appsettings import get_setting

log = logging.getLogger("retention")

#: Rows per DELETE. Small enough that the transaction stays short.
CHUNK = 500

#: Upper bound per source and sweep. The sweep runs hourly, so a backlog is worked off
#: over a few hours instead of blocking the database once for minutes.
MAX_PER_SWEEP = 20_000

#: Runs in these states are done. Everything else (`running`, `blocked`) stays: a blocked
#: run waits for an answer and would silently vanish from under the person answering.
RUN_DONE = ("success", "failed", "loop_exhausted", "planned")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


@dataclass(frozen=True)
class Rule:
    """One protocol source and how long it is kept."""

    #: Key in `app_settings`. 0 means: never delete.
    key: str
    #: Name for the admin surface. Translated in the frontend, this is the fallback.
    label: str
    default_days: int
    #: Which table, which timestamp, which condition — filled in by `_rules()`.
    model: Any
    stamp: Any
    where: Any


def _rules() -> list[Rule]:
    """The sources. Imported inside the function so the module stays import-cheap."""
    from ..models.agents import Run
    from ..models.notification import Notification
    from ..models.ops import InboundDelivery, JobRun
    from ..models.workflow import WorkflowInstance

    return [
        Rule(
            key="run_retention_days", label="Agent runs", default_days=30,
            model=Run, stamp=Run.started_at,
            # `started_at` and not `finished_at`: a run that never finished properly has
            # no end stamp, and precisely those must not stay forever.
            where=Run.status.in_(RUN_DONE),
        ),
        Rule(
            key="workflow_retention_days", label="Flow runs", default_days=30,
            model=WorkflowInstance, stamp=WorkflowInstance.started_at,
            # Steps and tokens hang off it over ON DELETE CASCADE, `waiting` stays.
            where=WorkflowInstance.status.in_(("completed", "failed", "cancelled")),
        ),
        Rule(
            key="job_run_retention_days", label="Job runs", default_days=30,
            model=JobRun, stamp=JobRun.started_at,
            where=JobRun.status.in_(("ok", "error")),
        ),
        Rule(
            key="notification_retention_days", label="Notifications", default_days=60,
            model=Notification, stamp=Notification.created_at,
            # Only what somebody has actually seen. An unread notification stays, however
            # old — it is the last trace of something nobody has looked at yet.
            where=Notification.read_at.isnot(None),
        ),
        Rule(
            key="inbound_retention_days", label="Inbound deliveries", default_days=14,
            model=InboundDelivery, stamp=InboundDelivery.received_at,
            # Shorter than the rest on purpose: the raw body of every webhook lies in
            # here, including whatever the sender packed into it.
            where=InboundDelivery.status.in_(("done", "dropped")),
        ),
    ]


async def days_for(db: AsyncSession, rule: Rule) -> int:
    """The configured period, or the default if the setting is unusable."""
    raw = await get_setting(db, rule.key, str(rule.default_days))
    try:
        days = int(raw)
    except ValueError:
        return rule.default_days
    return days if days >= 0 else rule.default_days


def _condition(rule: Rule, cutoff: dt.datetime) -> ColumnElement[bool]:
    return rule.where & (rule.stamp < cutoff)


async def pending(db: AsyncSession, rule: Rule, days: int) -> int:
    """How many rows the next sweep would take. 0 days = never delete, so nothing."""
    if days <= 0:
        return 0
    stmt = select(func.count()).select_from(rule.model).where(_condition(rule, _now() - dt.timedelta(days=days)))
    return int((await db.execute(stmt)).scalar_one())


async def sweep_rule(db: AsyncSession, rule: Rule) -> int:
    """Delete what is due for one source. Returns the number of deleted rows."""
    days = await days_for(db, rule)
    if days <= 0:
        return 0
    cutoff = _now() - dt.timedelta(days=days)
    removed = 0
    while removed < MAX_PER_SWEEP:
        ids = (await db.execute(
            select(rule.model.id).where(_condition(rule, cutoff)).limit(CHUNK)
        )).scalars().all()
        if not ids:
            break
        res = await db.execute(delete(rule.model).where(rule.model.id.in_(ids)))
        await db.commit()
        removed += res.rowcount or 0
        # Fewer than a full chunk means the source is empty — asking again costs a query
        # for nothing.
        if len(ids) < CHUNK:
            break
    if removed:
        log.info("retention %s: %d rows older than %d days deleted", rule.key, removed, days)
    return removed


async def sweep() -> dict[str, int]:
    """One pass over all sources. Called hourly by the scheduler."""
    result: dict[str, int] = {}
    async with SessionLocal() as db:
        for rule in _rules():
            try:
                result[rule.key] = await sweep_rule(db, rule)
            except Exception:  # noqa: BLE001 - one broken source must not stop the rest
                await db.rollback()
                log.exception("retention %s failed", rule.key)
                result[rule.key] = 0
    return result


async def overview(db: AsyncSession) -> list[dict]:
    """State of all rules for the admin surface: period, total rows, rows due."""
    out = []
    for rule in _rules():
        days = await days_for(db, rule)
        total = int((await db.execute(select(func.count()).select_from(rule.model))).scalar_one())
        out.append({
            "key": rule.key,
            "label": rule.label,
            "days": days,
            "default_days": rule.default_days,
            "rows": total,
            "pending": await pending(db, rule, days),
        })
    return out


def rule_by_key(key: str) -> Rule | None:
    return next((r for r in _rules() if r.key == key), None)
