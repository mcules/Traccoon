"""What the agent runs of a time window say about the agents themselves.

The numbers of the personnel file (`api/office.py`) answer "how much did which role do".
This module answers a different question: **what went wrong, and is it worth a ticket?**

The distinction is the whole point. Of 104 failed runs in the 30 days before this was written,
44 were provider rate limits and 21 were leftovers of a worker restart. A supervision that
filed a ticket for each of those would produce noise and nothing else, and the three runs that
really were a bug in the house would drown in it. So every failure gets a class first, and
only two of those classes are worth a person's attention.

The second source is `run_steps.ok`: a tool that fails in a quarter of its calls is a real
defect, and no run status shows it. The run carries on and ends in `success`.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agents import CostEntry, Run, RunStep
from ..models.enums import StatusCategory
from ..models.ticket import Issue, WorkflowStatus

# Every run whose status is one of these delivered, whatever it is called. `planned` is a
# finished planning run, not a failure — the same reading as `office.DELIVERED_STATUS`.
DELIVERED = ("success", "planned")
WAITING = ("blocked",)                       # waiting for a person, not broken
ABORTED = ("failed", "loop_exhausted")

# The marker a supervision ticket carries in its title. It is what makes a second run
# recognise its own earlier report instead of filing it again, and a person reading the ticket
# sees straight away where it came from.
MARK = "[Aufsicht:{signature}]"
MARK_RE = re.compile(r"\[Aufsicht:([^\]]+)\]")

# Only from this many calls on does a tool failure rate mean anything. Below it a single
# unlucky call would look like a defect.
MIN_TOOL_CALLS = 10
# And only from this share on is it worth reporting at all.
TOOL_FAIL_SHARE = 0.15


# ── Classification ──────────────────────────────────────────────────────────

# Order matters: the first matching row wins. The infrastructure rows stand before the agent
# rows on purpose — a run killed by a worker restart says nothing about the agent, however it
# ended.
_CLASSES: list[tuple[str, tuple[str, ...]]] = [
    # The provider said no. Nothing in this house can fix it, and it passes on its own.
    ("provider", ("rate_limit_error", "overloaded_error", "Verbindungsfehler",
                  "connection attempts failed", "upstream connect error", "<!DOCTYPE html>",
                  "bei max_tokens abgeschnitten", "truncated at max_tokens")),
    # Traccoon itself interrupted the run. Also not the agent's doing.
    ("infra", ("Worker-Neustart", "Worker restart", "Kill-Kanal", "kill channel", "Altlast:",
               "derselbe Auftrag wurde neu gestartet", "Wächter war bereits weg")),
    # The agent ran out of room: time, iterations, tokens, build gate. THIS is worth looking at.
    ("agent", ("Zeitlimit", "Iterations-Limit", "Token-Budget", "FINISHING BLOCKED",
               "Leere Modell-Antwort", "empty model answer")),
]

# What a class means for the supervision. Only two of them justify pulling a person in.
TICKET_WORTHY = ("agent", "bug")


def classify(status: str, error: str | None) -> str:
    """The class of a finished run: provider | infra | blocked | agent | bug | ok."""
    if status in DELIVERED:
        return "ok"
    if status in WAITING:
        return "blocked"
    text = (error or "").strip()
    for name, markers in _CLASSES:
        if any(m.lower() in text.lower() for m in markers):
            return name
    # A run that hit a limit carries no error text at all — `loop_exhausted` IS the message.
    if status == "loop_exhausted":
        return "agent"
    # Whatever is left with a text is an exception that reached the outside: a defect in this
    # house until somebody proves otherwise.
    return "bug" if text else "agent"


def signature(kind: str, agent: str, detail: str = "") -> str:
    """The stable name of a problem class, used to recognise it again tomorrow.

    Deliberately coarse: it must not contain a run id, a date or a line number, otherwise
    every run would look like a new problem and the supervision would file a ticket a day.
    """
    parts = [kind, agent or "?"]
    if detail:
        parts.append(detail)
    return "/".join(p.strip().replace("/", "-").replace("]", "") for p in parts)


# ── The numbers ─────────────────────────────────────────────────────────────

def _duration_ms(db: AsyncSession):
    """`finished_at - started_at` in milliseconds, dialect dependent.

    Postgres can `extract(epoch from a - b)`, SQLite knows neither intervals nor `extract`;
    there `julianday()` counts in days. Same reasoning as `api/office._duration_ms_expr`.
    """
    if db.get_bind().dialect.name == "sqlite":
        return (func.julianday(Run.finished_at) - func.julianday(Run.started_at)) * 86_400_000.0
    return func.extract("epoch", Run.finished_at - Run.started_at) * 1000.0


def _sum_if(condition) -> object:
    return func.sum(case((condition, 1), else_=0))


async def _per_agent(db: AsyncSession, since: dt.datetime, project_id: int | None) -> list[dict]:
    """One row per role: how many runs, how they ended, how long, how expensive."""
    dur = _duration_ms(db)
    q = (select(Run.agent,
                func.count().label("runs"),
                _sum_if(Run.status.in_(DELIVERED)).label("delivered"),
                _sum_if(Run.status.in_(WAITING)).label("waiting"),
                _sum_if(Run.status.in_(ABORTED)).label("aborted"),
                func.avg(Run.iterations).label("iterations_avg"),
                func.max(Run.iterations).label("iterations_max"),
                func.avg(dur).label("duration_avg_ms"),
                func.max(dur).label("duration_max_ms"))
         .where(Run.started_at >= since, Run.status != "running")
         .group_by(Run.agent).order_by(func.count().desc()))
    if project_id is not None:
        q = q.where(Run.project_id == project_id)
    rows = (await db.execute(q)).all()

    # Costs come from `cost_entries`, not from `runs`: they outlive the retention of the runs,
    # and that is the reason the personnel file groups them separately as well.
    cq = (select(CostEntry.agent, func.sum(CostEntry.cost_usd))
          .where(CostEntry.created_at >= since).group_by(CostEntry.agent))
    if project_id is not None:
        cq = cq.where(CostEntry.project_id == project_id)
    costs = {a: float(c or 0.0) for a, c in (await db.execute(cq)).all()}

    out = []
    for r in rows:
        out.append({
            "agent": r.agent, "runs": int(r.runs or 0),
            "delivered": int(r.delivered or 0), "waiting": int(r.waiting or 0),
            "aborted": int(r.aborted or 0),
            "iterations_avg": round(float(r.iterations_avg or 0.0), 1),
            "iterations_max": int(r.iterations_max or 0),
            "duration_avg_s": int((r.duration_avg_ms or 0) / 1000),
            "duration_max_s": int((r.duration_max_ms or 0) / 1000),
            "cost_usd": round(costs.get(r.agent, 0.0), 2),
        })
    return out


async def _problems(db: AsyncSession, since: dt.datetime, project_id: int | None) -> list[dict]:
    """The failed runs of the window, grouped by class and role."""
    q = (select(Run.id, Run.agent, Run.status, Run.error)
         .where(Run.started_at >= since, Run.status.notin_(DELIVERED + ("running",)))
         .order_by(Run.started_at.desc()))
    if project_id is not None:
        q = q.where(Run.project_id == project_id)

    groups: dict[tuple[str, str], dict] = {}
    for run_id, agent, status, error in (await db.execute(q)).all():
        kind = classify(status, error)
        slot = groups.setdefault((kind, agent or "?"), {
            "kind": kind, "agent": agent or "?", "n": 0, "runs": [], "examples": [],
            "statuses": {}, "signature": signature(kind, agent or "?"),
            "ticket_worthy": kind in TICKET_WORTHY,
        })
        slot["n"] += 1
        # A run that ran into a limit carries no error text, so without the status the group
        # would read as a bare number and say nothing about what actually happened.
        slot["statuses"][status] = slot["statuses"].get(status, 0) + 1
        if len(slot["runs"]) < 8:                      # enough to look them up, not a dump
            slot["runs"].append(int(run_id))
        text = " ".join((error or "").split())[:200]
        if text and text not in slot["examples"] and len(slot["examples"]) < 3:
            slot["examples"].append(text)
    return sorted(groups.values(), key=lambda g: (not g["ticket_worthy"], -g["n"]))


async def _tools(db: AsyncSession, since: dt.datetime, project_id: int | None) -> list[dict]:
    """Tools that fail noticeably often — the defect no run status shows."""
    q = (select(RunStep.tool_name, Run.agent,
                func.count().label("n"),
                func.sum(case((RunStep.ok.is_(False), 1), else_=0)).label("failed"))
         .join(Run, Run.id == RunStep.run_id)
         .where(Run.started_at >= since, RunStep.tool_name.isnot(None),
                RunStep.ok.isnot(None))
         .group_by(RunStep.tool_name, Run.agent)
         .having(func.count() >= MIN_TOOL_CALLS))
    if project_id is not None:
        q = q.where(Run.project_id == project_id)

    out = []
    for tool, agent, n, failed in (await db.execute(q)).all():
        n, failed = int(n or 0), int(failed or 0)
        share = failed / n if n else 0.0
        if share < TOOL_FAIL_SHARE:
            continue
        out.append({"kind": "tool", "agent": agent or "?", "tool": tool, "n": n,
                    "failed": failed, "share": round(share, 2), "ticket_worthy": True,
                    "signature": signature("tool", agent or "?", tool)})
    return sorted(out, key=lambda t: -t["share"])


async def _already_open(db: AsyncSession, signatures: list[str]) -> dict[str, str]:
    """Which of these problems already has an OPEN ticket, by its marker in the title.

    A closed ticket does not count: if the same class comes back after the fix, that is news
    again. The lookup runs over the marker and not over a table of its own — the ticket IS the
    record, and a person can see the marker.
    """
    if not signatures:
        return {}
    rows = (await db.execute(
        select(Issue.key, Issue.summary)
        .join(WorkflowStatus, WorkflowStatus.id == Issue.status_id)
        .where(WorkflowStatus.category != StatusCategory.done,
               Issue.summary.contains("[Aufsicht:")))).all()
    wanted = set(signatures)
    found: dict[str, str] = {}
    for key, summary in rows:
        hit = MARK_RE.search(summary or "")
        if hit and hit.group(1) in wanted:
            found.setdefault(hit.group(1), key)
    return found


async def health(db: AsyncSession, *, since_hours: int = 24, project_id: int | None = None,
                 agent: str = "") -> dict:
    """Everything the supervision needs about one time window."""
    since_hours = max(1, min(int(since_hours or 24), 24 * 90))
    now = dt.datetime.now(tz=dt.timezone.utc)
    since = now - dt.timedelta(hours=since_hours)

    agents = await _per_agent(db, since, project_id)
    problems = await _problems(db, since, project_id)
    tools = await _tools(db, since, project_id)
    if agent:
        agents = [a for a in agents if a["agent"] == agent]
        problems = [p for p in problems if p["agent"] == agent]
        tools = [t for t in tools if t["agent"] == agent]

    open_tickets = await _already_open(
        db, [p["signature"] for p in problems + tools if p["ticket_worthy"]])
    for row in problems + tools:
        row["open_ticket"] = open_tickets.get(row["signature"], "")

    return {
        "since": since.isoformat(timespec="minutes"),
        "until": now.isoformat(timespec="minutes"),
        "hours": since_hours,
        "runs": sum(a["runs"] for a in agents),
        "delivered": sum(a["delivered"] for a in agents),
        "waiting": sum(a["waiting"] for a in agents),
        "aborted": sum(a["aborted"] for a in agents),
        "cost_usd": round(sum(a["cost_usd"] for a in agents), 2),
        "agents": agents,
        "problems": problems,
        "tools": tools,
    }
