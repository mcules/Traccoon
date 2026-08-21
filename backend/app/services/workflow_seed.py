"""The shipped default set: the previous behaviour, drawn as a graph.

These graphs are the *starting line-up*, not the truth: every user may copy and rebuild
them. They map one to one what used to be wired firmly into the dispatcher, into
`lifecycle.py` and into the procurement module:

    planning → plan approval → implementation → (acceptance) → merge/deploy,
    continuation with stuck detection, questions as a waiting point on the comment,
    splitting into sub-tasks, procurement chain, ticket inbox, mail inbox.

`ensure_builtin_set` runs at backend start and is idempotent: a new version is published
only when a graph has actually changed. Running instances stay pinned to their version.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import (
    WorkflowSetScope, WorkflowSlot, WorkflowSubjectKind, WorkflowVersionStatus,
)
from ..models.workflow import WorkflowDefinition, WorkflowSet, WorkflowVersion
from .workflow_sets import BUILTIN_SET_KEY, SLOT_META
from .workflow_terms import migrate_graph

log = logging.getLogger("workflow_seed")

# Rises as soon as a shipped graph changes (only for traceability; publishing happens on a
# real graph difference anyway).
BUILTIN_REVISION = 14

# This is how often an agent may continue on the same thing after a limit (iterations,
# time, tokens) has ended it. Implementation may stay on it longer than planning: it leaves
# work behind in the worktree, while a planning that has no plan after ten attempts needs a
# human, not the eleventh attempt.
EXEC_CONTINUATIONS = 30
PLAN_CONTINUATIONS = 10

_COL = 260   # column spacing for branches
_ROW = 130   # Zeilenabstand


def _n(node_id: str, ntype: str, col: int, row: int, config: dict) -> dict:
    return {"id": node_id, "type": ntype,
            "position": {"x": col * _COL, "y": row * _ROW},
            "data": {"config": config}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def _action(name: str, label: str, **params) -> dict:
    """auto_action configuration in the shape the editor writes as well."""
    return {"label": label, "action": {"action": name, "params": params}}


# ── Slot: KI-Ticket-Lebenszyklus ─────────────────────────────────────────────

def build_ticket_lifecycle() -> dict:
    """The flow that used to sit in the dispatcher.

    Three entries over `context.entry`: `plan` (the normal case), `exec` (sub-task with a
    finished plan) and `accept` (collective ticket whose parts are all done).

    Deliberately kept terse: per phase ONE waiting point for all disturbances (question,
    error, stuck, rejected approval) instead of a pair per case. The state the ticket takes
    on comes from the run itself (`agent.hold_status`/`hold_reason`), so the same messages
    as before, only without five almost identical node pairs. Every node carries its
    `group` so that the interface can show phases as bands.
    """
    def _p(node_id, ntype, col, row, config, group):
        return _n(node_id, ntype, col, row, {**config, "group": group})

    nodes = [
        _p("start", "start", 0, 0, {"label": "Ticket zugewiesen"}, "start"),
        _p("entry", "decision", 0, 1, {
            "label": "Einstieg",
            "branches": [
                {"handle": "exec", "label": "a plan exists",
                 "guard": {"==": [{"var": "entry"}, "exec"]}},
                {"handle": "accept", "label": "acceptance only",
                 "guard": {"==": [{"var": "entry"}, "accept"]}},
                {"handle": "plan", "label": "plan it"},     # without a condition = catch-all
            ],
            "default_handle": "plan",
        }, "start"),

        # ── Planung ──────────────────────────────────────────────────────────
        _p("st_planning", "auto_action", 0, 2,
           _action("set_status", "Status: planning", status="planning"), "planung"),
        _p("plan", "agent_task", 0, 3, {
            "label": "Planning by the architect",
            "agent_role": "plan_agent", "phase": "planning",
        }, "planung"),
        # The same brake as in the implementation, it was simply missing here. The back edge
        # "keep planning" led back to `plan` unbraked: on 2026-08-07 TRA-31 hit the limit
        # every time after 20 iterations (~90 s) and immediately started the next run
        # without anything counting along. The only thing braking that was the gatekeeper.
        # Planning is cheaper than implementation but not free: a lower cap (10 instead of
        # 30 continuations).
        _p("may_plan_continue", "decision", 1, 4, {
            "label": "Weiterplanen?",
            "branches": [
                {"handle": "stop", "label": "anhalten", "guard": {"or": [
                    {"==": [{"var": "project.auto_continue"}, False]},
                    {">=": [{"var": "continuation"}, PLAN_CONTINUATIONS]},
                ]}},
                {"handle": "continue", "label": "weiter"},
            ],
            "default_handle": "continue",
        }, "planung"),
        _p("st_plan_review", "auto_action", 0, 4,
           _action("set_status", "Status: plan approval", status="plan_review",
                   reason="{{agent.hold_hint}}"), "planung"),
        _p("approve_plan", "approval", 0, 5, {
            "label": "Approve the plan", "gate": "ai_assign",
            "instructions": "Check the plan and approve it — after that the agent starts working.",
        }, "planung"),
        _p("is_split", "decision", 0, 6, {
            "label": "Aufteilung vorgeschlagen?",
            "branches": [
                {"handle": "split", "label": "Teilaufgaben",
                 "guard": {"==": [{"var": "agent.has_subtickets"}, True]}},
                {"handle": "single", "label": "in one go"},
            ],
            "default_handle": "single",
        }, "planung"),
        _p("do_split", "auto_action", -1, 7,
           _action("split_tickets", "Teilaufgaben anlegen"), "aufteilung"),
        _p("end_split", "end", -1, 8, {"label": "Aufgeteilt", "outcome": "completed"},
           "aufteilung"),

        # Disturbances of the planning: one state, one waiting point.
        _p("st_plan_stop", "auto_action", -2, 4,
           _action("set_status", "Planung angehalten",
                   status="{{agent.hold_status}}", reason="{{agent.hold_reason}}"),
           "stoerung"),
        _p("wait_plan", "wait_event", -2, 5, {
            "label": "Feedback on the planning", "events": ["comment", "manual", "answer"],
        }, "stoerung"),

        # ── Umsetzung ────────────────────────────────────────────────────────
        _p("cap_baseline", "auto_action", 0, 7,
           _action("set_cap_baseline", "Reset the cost window"), "umsetzung"),
        # Read once per approval round; both exits of the implementation access it.
        _p("facts", "auto_action", 0, 8,
           _action("refresh_facts", "Projekt-Einstellungen lesen"), "umsetzung"),
        # Deliberately sits right behind the approval and NOT before `exec`: the acceptance
        # branch leads there as well (`entry --abnehmen--> facts`), and an accepted ticket
        # must not fall back to "approved".
        #
        # Time can pass between approval and start: the run waits at the gatekeeper (user
        # limit, night window, cap). Without this node the ticket would meanwhile stay on
        # `plan_review`: the interface would keep offering "approve plan", and the endpoint
        # would rightly answer "no approval is waiting right now". Only the actual start
        # (`workflow_engine._start_agent_task`) switches to `in_progress`.
        _p("st_approved", "auto_action", 0, 7,
           _action("set_status", "Status: approved", status="approved"), "umsetzung"),
        _p("exec", "agent_task", 0, 9, {
            "label": "Umsetzung", "agent_role": "exec_agent", "phase": "execution",
        }, "umsetzung"),
        _p("may_continue", "decision", 1, 10, {
            "label": "Weiterarbeiten?",
            "branches": [
                {"handle": "stop", "label": "anhalten", "guard": {"or": [
                    {"==": [{"var": "agent.stalled"}, True]},
                    {"==": [{"var": "project.auto_continue"}, False]},
                    {">=": [{"var": "continuation"}, EXEC_CONTINUATIONS]},
                ]}},
                {"handle": "continue", "label": "weiter"},
            ],
            "default_handle": "continue",
        }, "umsetzung"),

        # Disturbances of the implementation: one state, one waiting point.
        _p("st_exec_stop", "auto_action", 2, 10,
           _action("set_status", "Umsetzung angehalten",
                   status="{{agent.hold_status}}", reason="{{agent.hold_reason}}"),
           "stoerung"),
        _p("wait_exec", "wait_event", 2, 11, {
            "label": "Feedback on the implementation", "events": ["comment", "manual", "answer"],
        }, "stoerung"),

        # ── Abnahme ──────────────────────────────────────────────────────────
        _p("needs_test", "decision", 0, 10, {
            "label": "Acceptance needed?",
            "branches": [
                {"handle": "review", "label": "yes",
                 "guard": {"==": [{"var": "project.needs_acceptance"}, True]}},
                {"handle": "direct", "label": "no, deliver directly"},
            ],
            "default_handle": "direct",
        }, "abnahme"),
        _p("st_to_test", "auto_action", 0, 11,
           _action("set_status", "Status: to test", status="to_test"), "abnahme"),
        _p("testenv", "auto_action", 0, 12,
           _action("start_testenv", "Testumgebung starten"), "abnahme"),
        _p("approve_result", "approval", 0, 13, {
            "label": "Abnahme", "gate": "ai_assign",
            "instructions": "Check the result. Approving merges the branch or opens the PR.",
        }, "abnahme"),
        _p("accept", "subflow", 0, 14, {
            "label": "Abnahme & Auslieferung", "slot": WorkflowSlot.acceptance.value,
        }, "abnahme"),
        _p("st_done", "auto_action", 0, 15,
           _action("set_status", "Status: done", status="done"), "abnahme"),
        _p("end_ok", "end", 0, 16, {"label": "Done", "outcome": "completed"}, "abnahme"),
        _p("st_merge_hold", "auto_action", 2, 16,
           _action("set_status", "Merge blocked", status="hold",
                   reason="merge"), "stoerung"),
        _p("merge_escalate", "decision", 1, 15, {
            "label": "Konflikt eskalieren?",
            "branches": [
                {"handle": "human", "label": "to the person",
                 "guard": {"==": [{"var": "merge.escalate"}, True]}},
                {"handle": "retry", "label": "the agent resolves it"},
            ],
            "default_handle": "retry",
        }, "abnahme"),
    ]

    edges = [
        _e("start", "entry"),
        _e("entry", "st_planning", "plan", "plan it"),
        _e("entry", "exec", "exec", "umsetzen"),
        _e("entry", "facts", "accept", "abnehmen"),

        _e("st_planning", "plan"),
        _e("plan", "st_plan_review", "planned", "plan is there"),
        _e("plan", "may_plan_continue", "loop_exhausted", "limit reached"),
        _e("may_plan_continue", "st_planning", "continue", "weiter planen"),
        _e("may_plan_continue", "st_plan_stop", "stop", "anhalten"),
        _e("plan", "st_plan_stop", "blocked", "a question"),
        _e("plan", "st_plan_stop", "failed", "an error"),
        # Safety net for unknown run results (default mapping to "err").
        _e("plan", "st_plan_stop", "err"),
        _e("st_plan_review", "approve_plan"),
        _e("approve_plan", "is_split", "approved", "freigegeben"),
        _e("approve_plan", "st_plan_stop", "rejected", "abgelehnt"),
        _e("is_split", "do_split", "split"),
        _e("is_split", "st_approved", "single"),
        _e("st_approved", "cap_baseline"),
        _e("do_split", "end_split"),
        _e("st_plan_stop", "wait_plan"),
        _e("wait_plan", "st_planning"),

        _e("cap_baseline", "facts"),
        _e("facts", "exec"),
        _e("exec", "needs_test", "done", "fertig"),
        _e("exec", "may_continue", "loop_exhausted", "Limit erreicht"),
        _e("exec", "st_exec_stop", "blocked", "a question"),
        _e("exec", "st_exec_stop", "failed", "Fehler"),
        _e("exec", "st_exec_stop", "err"),
        _e("may_continue", "exec", "continue", "weiter"),
        _e("may_continue", "st_exec_stop", "stop", "anhalten"),
        _e("st_exec_stop", "wait_exec"),
        _e("wait_exec", "exec"),

        _e("needs_test", "st_to_test", "review"),
        _e("needs_test", "accept", "direct"),
        _e("st_to_test", "testenv"),
        _e("testenv", "approve_result"),
        _e("approve_result", "accept", "approved", "abnehmen"),
        _e("approve_result", "st_exec_stop", "rejected", "nachbessern"),
        _e("accept", "st_done", "completed", "geliefert"),
        _e("accept", "merge_escalate", "failed", "Merge offen"),
        _e("st_done", "end_ok"),
        _e("merge_escalate", "st_merge_hold", "human"),
        _e("st_merge_hold", "wait_exec"),
        _e("merge_escalate", "exec", "retry", "the agent resolves it"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Abnahme & Auslieferung ─────────────────────────────────────────────

def build_acceptance() -> dict:
    """What `/issues/{key}/complete` used to do: clear the test environment, merge, deploy.

    The order is binding (TRA-18): first the test environment goes (container, volumes,
    worktree, port), then the merge, and only on a clean merge does it continue.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Abnahme"}),
        _n("stop_testenv", "auto_action", 0, 1,
           _action("stop_testenv", "Clear the test environment")),
        _n("merge", "auto_action", 0, 2,
           _action("accept_merge", "Merge the branch / open the PR")),
        _n("deploy", "auto_action", 0, 3, _action("deploy", "Deployment einreihen")),
        _n("end_ok", "end", 0, 4, {"label": "Geliefert", "outcome": "completed"}),
        _n("end_fail", "end", 1, 4, {"label": "Merge offen", "outcome": "failed"}),
    ]
    edges = [
        _e("start", "stop_testenv"),
        _e("stop_testenv", "merge"),
        _e("merge", "deploy", "merged", "gemerged"),
        _e("merge", "end_ok", "pr_open", "PR offen"),
        _e("merge", "end_ok", "no_git", "no git"),
        _e("merge", "end_ok", "out"),
        _e("merge", "end_fail", "conflict", "Konflikt"),
        _e("merge", "end_fail", "push_failed", "Push fehlgeschlagen"),
        _e("merge", "end_fail", "pr_failed"),
        _e("merge", "end_fail", "gone"),
        _e("deploy", "end_ok"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Ticket-Eingang ─────────────────────────────────────────────────────

def build_ticket_intake() -> dict:
    """Incoming report (webhook, mail) turned into a ticket. The context comes from the
    trigger: `title`, `body`, optionally `agent` (then it is assigned right away) and `ignore`."""
    nodes = [
        _n("start", "start", 0, 0, {"label": "Meldung eingegangen"}),
        _n("relevant", "decision", 0, 1, {
            "label": "Relevant?",
            "branches": [
                {"handle": "skip", "label": "verwerfen",
                 "guard": {"==": [{"var": "ignore"}, True]}},
                {"handle": "keep", "label": "adopt it"},
            ],
            "default_handle": "keep",
        }),
        _n("create", "auto_action", 0, 2, _action(
            "create_ticket", "Ticket anlegen",
            summary="{{title}}", description="{{body}}", assigned_agent="{{agent}}")),
        _n("end_ok", "end", 0, 3, {"label": "Angelegt", "outcome": "completed"}),
        _n("end_skip", "end", 1, 2, {"label": "Verworfen", "outcome": "completed"}),
    ]
    edges = [
        _e("start", "relevant"),
        _e("relevant", "create", "keep"),
        _e("relevant", "end_skip", "skip"),
        _e("create", "end_ok"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Mail-Eingang ───────────────────────────────────────────────────────

def build_hardware_procurement() -> dict:
    from .hardware_workflow import DEFAULT_STEPS, build_hardware_graph
    return build_hardware_graph(list(DEFAULT_STEPS))


BUILDERS = {
    WorkflowSlot.ticket_lifecycle.value: build_ticket_lifecycle,
    WorkflowSlot.acceptance.value: build_acceptance,
    WorkflowSlot.hardware_procurement.value: build_hardware_procurement,
    WorkflowSlot.ticket_intake.value: build_ticket_intake,
}


# ── Seed ─────────────────────────────────────────────────────────────────────

async def ensure_builtin_set(db: AsyncSession) -> WorkflowSet:
    """Create respectively bring up to date the global default set (idempotent).

    New versions are published ONLY on a real graph difference; otherwise every backend
    start would inflate the version history.
    """
    s = (await db.execute(select(WorkflowSet).where(WorkflowSet.is_builtin.is_(True))
                          .order_by(WorkflowSet.id))).scalars().first()
    if s is None:
        s = WorkflowSet(
            scope=WorkflowSetScope.global_, user_id=None, key=BUILTIN_SET_KEY,
            name="Traccoon default",
            description="The shipped flows. Copyable as a personal set or a project set.",
            is_builtin=True, builtin_revision=0,
        )
        db.add(s)
        await db.flush()

    changed = 0
    for slot, build in BUILDERS.items():
        # The terms pass rewrites every stored version onto the English words and leaves its
        # mark on it. Whoever compares here without that mark compares apples to a marked
        # apple: the graphs are the same, the comparison fails, and every start publishes
        # another identical version. That is where the several hundred duplicates came from.
        graph, _ = migrate_graph(build())
        meta = SLOT_META[slot]
        d = (await db.execute(select(WorkflowDefinition).where(
            WorkflowDefinition.set_id == s.id, WorkflowDefinition.slot == slot,
            WorkflowDefinition.archived_at.is_(None)))).scalar_one_or_none()
        if d is None:
            d = WorkflowDefinition(
                project_id=None, set_id=s.id, slot=slot, key=slot, name=meta["name"],
                description=meta["description"],
                subject_kind=WorkflowSubjectKind(meta["subject_kind"]),
            )
            db.add(d)
            await db.flush()
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is not None and current.graph == graph:
            continue
        last = (await db.execute(select(WorkflowVersion)
                                 .where(WorkflowVersion.definition_id == d.id)
                                 .order_by(WorkflowVersion.version.desc()))).scalars().first()
        version = WorkflowVersion(
            definition_id=d.id, version=(last.version + 1) if last else 1, graph=graph,
            status=WorkflowVersionStatus.published,
            published_at=dt.datetime.now(tz=dt.timezone.utc),
            notes=f"Ausgelieferter Standard (Revision {BUILTIN_REVISION})",
        )
        db.add(version)
        await db.flush()
        d.current_version_id = version.id
        changed += 1

    if changed:
        s.builtin_revision = BUILTIN_REVISION
        log.info("Default set updated: %d flow(s) republished", changed)
    await db.commit()
    await db.refresh(s)
    return s
