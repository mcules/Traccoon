"""What the supervision reads out of the agent runs.

The heart of it is the classification. Of 104 failed runs in the 30 days before this module
was written, 44 were provider rate limits and 21 leftovers of a worker restart — a supervision
that files a ticket for each of those buries the three that really were a defect. The texts
below are taken verbatim from that stock.
"""
import datetime as dt

from app.models.agents import Run, RunStep
from app.models.enums import StatusCategory
from app.services.run_health import (
    MIN_TOOL_CALLS, classify, health, signature,
)
from test_lifecycle_process import _project_with_ticket


async def _run(db, *, agent="developer", status="success", error=None, project_id=None,
               age_min=10, iterations=5, task="t") -> Run:
    now = dt.datetime.now(dt.UTC)
    run = Run(issue_id=None, project_id=project_id, task_id=f"{task}-{status}-{age_min}",
              agent=agent, phase="execution", provider="claude_code", model="m",
              status=status, error=error, iterations=iterations,
              started_at=now - dt.timedelta(minutes=age_min),
              finished_at=now - dt.timedelta(minutes=age_min - 1))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


# ── Classification ──────────────────────────────────────────────────────────

def test_the_provider_is_not_the_agents_fault():
    """A rate limit passes on its own and no ticket makes it pass faster."""
    for text in (
        'claude: HTTP 429: {"type":"error","error":{"type":"rate_limit_error","message":"…"}}',
        'claude: HTTP 529: {"type":"error","error":{"type":"overloaded_error"}}',
        "claude: Verbindungsfehler: All connection attempts failed",
        "claude: HTTP 503: upstream connect error or disconnect/reset before headers",
        "claude: Antwort bei max_tokens abgeschnitten – unvollständig",
    ):
        assert classify("failed", text) == "provider", text


def test_our_own_interruptions_are_not_the_agents_fault_either():
    """A run the house itself killed says nothing about how the agent worked."""
    for text in (
        "Worker-Neustart: der Lauf war beim Abbruch nicht zu Ende und wird nicht fortgesetzt.",
        "Worker restart: the run was not finished when it was aborted and is not continued.",
        "Abgebrochen: Stopp über den Kill-Kanal (Knopf, Prozess-Schritt oder Wartungs-Update).",
        "Altlast: Lauf ohne Abschluss (vor Einfuehrung der Neustart-Aufraeumung 2026-07-31).",
        "Abgebrochen: derselbe Auftrag wurde neu gestartet (Worker-Neustart).",
    ):
        assert classify("failed", text) == "infra", text


def test_a_limit_without_a_text_still_lands_on_the_agent():
    """`loop_exhausted` IS the message; those runs carry no error text at all."""
    assert classify("loop_exhausted", None) == "agent"
    assert classify("loop_exhausted", "") == "agent"
    assert classify("failed", "Zeitlimit erreicht (1800s, Grenze 1800s).") == "agent"
    assert classify("failed", "Leere Modell-Antwort.") == "agent"


def test_an_exception_that_got_out_is_a_defect():
    """Whatever is left with a Python signature is ours until somebody proves otherwise."""
    assert classify("failed", "'AgentDef' object has no attribute 'max_context_tokens'") == "bug"
    assert classify("failed", "'utf-8' codec can't encode character '\\udf82'") == "bug"


def test_delivering_and_waiting_are_no_problems():
    """A planning run that delivered a plan is finished, and a question is not a defect."""
    assert classify("success", None) == "ok"
    assert classify("planned", None) == "ok"
    assert classify("blocked", None) == "blocked"


def test_a_signature_does_not_change_from_day_to_day():
    """It must not carry a run id or a date, otherwise every day looks like a new problem."""
    assert signature("agent", "developer") == "agent/developer"
    assert signature("tool", "developer", "codegraph") == "tool/developer/codegraph"
    # No character that would break the marker in the ticket title.
    assert "]" not in signature("bug", "a]b", "c/d")


# ── The window ──────────────────────────────────────────────────────────────

async def test_the_window_counts_and_separates(db):
    """Delivered, waiting and aborted stay apart, and the classes are not mixed."""
    await _run(db, status="success", task="a")
    await _run(db, status="planned", task="b")
    await _run(db, status="blocked", task="c")
    await _run(db, status="loop_exhausted", task="d")
    await _run(db, status="failed", error="claude: HTTP 429: rate_limit_error", task="e")

    data = await health(db, since_hours=24)
    assert data["runs"] == 5
    assert (data["delivered"], data["waiting"], data["aborted"]) == (2, 1, 2)

    worth = [p for p in data["problems"] if p["ticket_worthy"]]
    assert [p["signature"] for p in worth] == ["agent/developer"]
    assert {p["kind"] for p in data["problems"] if not p["ticket_worthy"]} == {"blocked", "provider"}


async def test_runs_outside_the_window_stay_outside(db):
    """The window is the whole point: yesterday's problem is not today's."""
    await _run(db, status="loop_exhausted", age_min=10, task="jung")
    await _run(db, status="loop_exhausted", age_min=60 * 40, task="alt")
    assert (await health(db, since_hours=24))["runs"] == 1
    assert (await health(db, since_hours=24 * 7))["runs"] == 2


async def test_a_running_run_is_not_judged(db):
    """Whoever is still working has not failed yet."""
    now = dt.datetime.now(dt.UTC)
    db.add(Run(issue_id=None, task_id="laeuft", agent="developer", phase="execution",
               provider="claude_code", model="m", status="running", started_at=now))
    await db.commit()
    data = await health(db, since_hours=24)
    assert data["runs"] == 0 and data["problems"] == []


# ── Tools ───────────────────────────────────────────────────────────────────

async def _tool_calls(db, run, tool, *, n, failed):
    for i in range(n):
        db.add(RunStep(run_id=run.id, seq=i, role="tool", tool_name=tool, content="",
                       ok=i >= failed))
    await db.commit()


async def test_a_tool_that_fails_often_is_a_finding(db):
    """No run status shows it: the run carries on and ends in success."""
    run = await _run(db, status="success", task="werkzeug")
    await _tool_calls(db, run, "codegraph", n=20, failed=8)
    tools = (await health(db, since_hours=24))["tools"]
    assert len(tools) == 1
    assert tools[0]["signature"] == "tool/developer/codegraph"
    assert tools[0]["share"] == 0.4 and tools[0]["ticket_worthy"] is True


async def test_a_few_unlucky_calls_are_no_finding(db):
    """Below the minimum a single failure would look like a defect."""
    run = await _run(db, status="success", task="wenig")
    await _tool_calls(db, run, "fs_read", n=MIN_TOOL_CALLS - 1, failed=MIN_TOOL_CALLS - 1)
    assert (await health(db, since_hours=24))["tools"] == []


async def test_a_tool_that_mostly_works_is_no_finding(db):
    """Some failure is normal: a file that is not there is an answer, not a defect."""
    run = await _run(db, status="success", task="okay")
    await _tool_calls(db, run, "fs_read", n=100, failed=5)
    assert (await health(db, since_hours=24))["tools"] == []


# ── Not reporting the same thing twice ──────────────────────────────────────

async def _ticket(db, proj, owner, stats, key, summary, *, done=False):
    from app.models.ticket import Issue, IssueType
    from sqlalchemy import select
    t = (await db.execute(select(IssueType).where(IssueType.project_id == proj.id))).scalars().first()
    status = stats["Fertig"] if done else stats["To Do"]
    iss = Issue(project_id=proj.id, number=abs(hash(key)) % 9999, key=key, type_id=t.id,
                status_id=status.id, summary=summary, reporter_id=owner.id, rank="9999")
    db.add(iss)
    await db.commit()
    return iss


async def test_an_open_ticket_suppresses_the_repeat(db):
    """The marker in the title is the whole duplicate protection — no table, no setting."""
    owner, proj, _, stats = await _project_with_ticket(db)
    await _run(db, status="loop_exhausted", task="wieder")
    await _ticket(db, proj, owner, stats, "TST-90",
                  "[Aufsicht:agent/developer] developer läuft ins Zeitlimit")

    worth = [p for p in (await health(db, since_hours=24))["problems"] if p["ticket_worthy"]]
    assert worth[0]["open_ticket"] == "TST-90"


async def test_a_closed_ticket_does_not_suppress_it(db):
    """If the class comes back after the fix, that is news again."""
    owner, proj, _, stats = await _project_with_ticket(db)
    await _run(db, status="loop_exhausted", task="rueckfall")
    await _ticket(db, proj, owner, stats, "TST-91",
                  "[Aufsicht:agent/developer] war mal behoben", done=True)

    worth = [p for p in (await health(db, since_hours=24))["problems"] if p["ticket_worthy"]]
    assert worth[0]["open_ticket"] == ""


async def test_a_foreign_marker_does_not_suppress_anything(db):
    """A ticket about a different class is not this class's report."""
    owner, proj, _, stats = await _project_with_ticket(db)
    await _run(db, status="loop_exhausted", task="fremd")
    await _ticket(db, proj, owner, stats, "TST-92", "[Aufsicht:agent/code_reviewer] etwas anderes")

    worth = [p for p in (await health(db, since_hours=24))["problems"] if p["ticket_worthy"]]
    assert worth[0]["open_ticket"] == ""
