"""Traccoon agent worker: Redis consumer of the task queue with a real tool loop.

Shares the backend image (`python -m app.worker`) and opens a SessionLocal of its own
(Postgres allows the concurrency).
"""
from __future__ import annotations

import asyncio
import faulthandler
import json
import logging
import os
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import or_, select

from ..config import settings
from ..core.redis import (ACTIVE, RESULT_TTL, PREFIX, PROCESSING, PULSE_BEAT, PULSE_TTL,
                          QUEUE, get_flag, pulse_key)
from ..db import SessionLocal
from ..models.agents import AgentDefinition
from ..models.enums import GlobalRole
from ..models.ops import Permission
from ..models.project import Project
from ..models.ticket import Comment, Issue
from ..models.user import User
from . import gitops
from .runtime import AgentDef, agent_def_from_row, run_agent
from .secrets import (
    resolve_git_token, resolve_provider_base_url,
    resolve_provider_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("traccoon.worker")

MAX_CONCURRENT = int(os.getenv("WORKER_CONCURRENCY", "3"))
# Grace period on shutdown: this long an agent already running may keep working before the
# process leaves. A run can take hours, so waiting for it completely is out, but the two
# minutes are enough for the model turn in flight including its tool, and for its step rows
# to be written. Exactly from those the successor builds its handover
# (`runtime._abbruch_uebergabe`). Has to stay below `stop_grace_period` in the compose file,
# otherwise Docker strikes first.
DRAIN_SEC = int(os.getenv("WORKER_DRAIN_SEC", "120"))
# Wait of BLMOVE (server side) and the Redis options against silent hangs.
BLOCK_TIMEOUT = 5
_REDIS_KW = {"decode_responses": True, "socket_keepalive": True,
             "health_check_interval": 30, "retry_on_timeout": True}
# How often a merge conflict goes back to the agent on accept before it escalates to a
# person (a brake against accept → conflict → approved → re-dispatch loops).
MAX_CONFLICT_ROUNDS = int(os.getenv("MAX_CONFLICT_ROUNDS", "3"))
DEFAULT_CLAUDE_MODEL = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-sonnet-4-5")
DEFAULT_CODEX_MODEL = os.getenv("DEFAULT_CODEX_MODEL", "gpt-5")

# Default role abilities when no AgentDefinition exists.
_DEFAULTS: dict[str, dict] = {
    "project_manager": {"can_delegate": True, "can_read_code": True, "mp": 20, "me": 40},
    "architect":       {"can_read_code": True, "mp": 20, "me": 80},
    "developer":       {"can_code": True, "mp": 10, "me": 80},
    "code_reviewer":   {"can_read_code": True, "mp": 8, "me": 30},
    "tester":          {"can_code": True, "mp": 8, "me": 40},
    "devops":          {"can_code": True, "mp": 8, "me": 40},
    # The supervision reads numbers and writes tickets. It touches no code, and it needs few
    # rounds: fetch the window, decide, file, done.
    "supervisor":      {"mp": 6, "me": 20},
}

_PROMPTS: dict[str, str] = {
    "project_manager": "You are the project manager. Understand → plan → delegate. Answer in the "
                       "language of the person.",
    "architect": "You are the architect. Plan carefully; the result is submit_plan with goals, steps, "
                 "affected files, acceptance criteria (checkable), a test plan and risks. If the task "
                 "is too large (>1 layer, several sub-features or >~5-8 files), add a dependency "
                 "ordered block to the plan: "
                 "<subtickets>[{\"summary\":\"...\",\"description\":\"...\",\"plan\":\"...\"}]</subtickets> "
                 "(every subticket with a full plan including acceptance criteria).",
    "developer": "You are the developer. Implement the plan completely, then verify with check. Edit "
                 "surgically; never delete large blocks to hide an error.",
    "code_reviewer": "You are the code reviewer. Check bugs, security and edge cases. Only findings "
                     "that force a correction.",
    "tester": "You are the tester. Write tests (happy path plus edge cases).",
    "devops": "You are devops. Build, dependencies, CI/CD, infrastructure.",
    # Deliberately about the WAY of working, not about the assignment: what exactly is to be
    # looked at stands in the start context of the job that calls this role.
    "supervisor": "You are the supervision of the agents. You look at finished runs, never at "
                  "a single ticket: what repeats is a finding, what happened once is not. "
                  "Provider trouble and interruptions of the house itself are none of your "
                  "business, they pass on their own. For a real finding you open exactly ONE "
                  "ticket and assign it to the role that can fix it. A pattern that is no "
                  "defect in the code becomes exactly ONE rule in the memory of the agent it "
                  "concerns (`memory_teach`), phrased so that it still helps next month. "
                  "Never report the same thing twice: a class that already has an open ticket "
                  "is settled for today.",
}


def _default_agent_def(role: str, provider: str, model: str, mode: str) -> AgentDef:
    d = _DEFAULTS.get(role, {"me": 40, "mp": 10})
    return AgentDef(
        id=None, name=role, role=role, system_prompt=_PROMPTS.get(role, f"You are {role}."),
        provider=provider, model=model, token_name="", fallback=None, fallback_model="",
        fallback_token_name="", temperature=0.3, max_tokens=8192,
        max_iterations=d.get("mp", 10) if mode == "plan" else d.get("me", 40),
        can_code=d.get("can_code", False), can_read_code=d.get("can_read_code", False),
        can_delegate=d.get("can_delegate", False), web_search=False,
        allowed_tools=[], allowed_skills=[], autoload_skills=[], delegate_to=[],
    )


async def _build_tokens(db, owner_id, agent, project=None) -> tuple[dict, dict]:
    """(tokens, base_urls) per provider from the agent's choice: primary (provider/token_name)
    plus fallback (fallback/fallback_token_name). The legacy keys claude_code/codex serve as
    the default. base_urls carries the optional endpoint of the chosen provider token (only
    relevant for openai), determined by the same eff_name choice as the tokens.

    The project default subscription (project.default_provider/-token_name) overrides the
    user's personal default, and only applies when the agent picks no token itself."""
    proj_provider = getattr(project, "default_provider", "") or ""
    proj_name = getattr(project, "default_token_name", "") or ""

    def eff_name(provider: str, agent_name: str) -> str:
        if agent_name:
            return agent_name                       # Agent-Wahl hat Vorrang
        if proj_name and proj_provider == provider:  # otherwise the project default
            return proj_name
        return ""                                    # otherwise the personal default

    tokens = {
        "claude_code": await resolve_provider_token(db, owner_id, "claude_code", eff_name("claude_code", "")),
        "codex": await resolve_provider_token(db, owner_id, "codex", eff_name("codex", "")),
    }
    base_urls: dict[str, str | None] = {}
    prim_name = eff_name(agent.provider, agent.token_name)
    tokens[agent.provider] = await resolve_provider_token(db, owner_id, agent.provider, prim_name)
    base_urls[agent.provider] = await resolve_provider_base_url(db, owner_id, agent.provider, prim_name)
    if agent.fallback and agent.fallback != agent.provider:
        fb_name = eff_name(agent.fallback, agent.fallback_token_name)
        tokens[agent.fallback] = await resolve_provider_token(db, owner_id, agent.fallback, fb_name)
        base_urls[agent.fallback] = await resolve_provider_base_url(db, owner_id, agent.fallback, fb_name)
    return tokens, base_urls


async def _none():
    return None


async def _load_agent(db, role: str, project_id: int, mode: str, owner_id: int | None = None) -> AgentDef:
    # Only the owner's own definition or a global one (user_id IS NULL), never that of a
    # different user. Precedence: own before global, project scoped before projectless.
    row = (
        await db.execute(
            select(AgentDefinition).where(
                AgentDefinition.role == role, AgentDefinition.active.is_(True),
                or_(AgentDefinition.user_id == owner_id, AgentDefinition.user_id.is_(None))
                if owner_id is not None else AgentDefinition.user_id.is_(None),
                # Only definitions scoped to THIS project or projectless ones, never those of
                # fremden Projekts.
                or_(AgentDefinition.project_id == project_id, AgentDefinition.project_id.is_(None)),
            )
            .order_by(AgentDefinition.user_id.is_(None),      # eigene zuerst
                      AgentDefinition.project_id.is_(None))   # projekt-scoped (dieses Projekt) zuerst
        )
    ).scalars().first()
    if row:
        d = agent_def_from_row(row, mode)
        if not d.model:
            d.model = DEFAULT_CLAUDE_MODEL if d.provider in ("claude_code", "claude") else DEFAULT_CODEX_MODEL
        return d
    prov = "claude_code"
    return _default_agent_def(role, prov, DEFAULT_CLAUDE_MODEL, mode)


async def handle(job: dict, redis: Redis) -> None:
    task_id = job["task_id"]
    kind = job.get("kind")
    if kind == "accept":
        # ALWAYS write the result to Redis: /complete waits for it and may only set a ticket
        # to done on a clean merge (ABC-18).
        try:
            res = await _handle_accept(job, redis)
        except Exception as exc:  # noqa: BLE001
            log.exception("accept failed")
            res = {"status": "failed", "error": str(exc)[:500]}
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(res or {"status": "failed"}), ex=RESULT_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        return
    if kind == "assistant":
        await _handle_assistant_task(job, redis)
        return
    if kind == "curator":
        await _handle_curator(job)
        return
    if kind == "agent_frei":
        await _handle_agent_free(job, redis)
        return
    if kind:  # infrastructure task (testenv_start and so on), later
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps({"status": "failed",
                        "output": f"the infrastructure task {kind} is not implemented yet"}), ex=RESULT_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        return

    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return
        mode = "plan" if job.get("phase") == "planning" else "execute"
        role = job["role"]
        # Whoever works stands on "in progress", and from now on, not only after the next
        # reconciliation. The process sets that when a step starts, but a task the reliable
        # queue re-presents after a restart does NOT come through the graph and would
        # otherwise run under a "waiting" label (2026-08-07).
        from ..models.enums import TicketAgentStatus as _TS
        from ..services.artifacts import set_ticket_status as _set_status
        _target = _TS.planning if mode == "plan" else _TS.in_progress
        if issue.agent_status not in (_TS.done, _target):
            await _set_status(db, issue, _target)
        issue.agent_working = True
        await db.commit()
        owner_id = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
        agent = await _load_agent(db, role, project.id, mode, owner_id)
        # Skill-Laden passiert jetzt zentral in run_agent (autoload + on-demand, beide Pfade).
        tokens, base_urls = await _build_tokens(db, owner_id, agent, project)

        # Git-Kontext / Workspace
        ws_root = None
        ctx = None
        if project.git_enabled:
            host = urlsplit(project.github_repo).hostname or ""
            token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
            wt = gitops.worktree_path(project.key, issue.key) if project.work_in_branches else None
            base_branch = project.merge_target or "main"
            # Subticket: base it on the branch of the umbrella ticket (and merge back there).
            if issue.parent_ticket_id:
                umbrella = await db.get(Issue, issue.parent_ticket_id)
                if umbrella:
                    umb_branch = umbrella.branch_name or gitops.issue_branch(umbrella.key)
                    ens = gitops.GitCtx(
                        workdir=gitops.project_workdir(project.key), branch=umb_branch,
                        remote=project.github_repo, token=token,
                        main=project.merge_target or "main", enabled=True)
                    log.info("git ensure-umbrella %s: %s", umbrella.key,
                             await gitops.ensure_branch(ens, umb_branch, project.merge_target or "main"))
                    if not umbrella.branch_name:
                        umbrella.branch_name = umb_branch
                        umbrella.base_branch = project.merge_target or "main"
                        await db.commit()
                    base_branch = umb_branch
            ctx = gitops.GitCtx(
                workdir=gitops.project_workdir(project.key), branch=gitops.issue_branch(issue.key),
                remote=project.github_repo, token=token, worktree=wt, main=base_branch,
                enabled=True)
            note = await gitops.prepare(ctx)
            log.info("git prepare %s: %s", issue.key, note)
            ws_root = ctx.worktree or ctx.workdir
            issue.branch_name = ctx.branch
            issue.base_branch = ctx.main
            issue.git_base_sha = ctx.base_commit
            await db.commit()
        elif project.managed:
            ws_root = gitops.project_workdir(project.key)

        # Permissions
        perms_rows = (
            await db.execute(select(Permission).where(Permission.project_id == project.id))
        ).scalars().all()
        permissions = [{"tool": p.tool, "resource": p.resource, "action": p.action.value} for p in perms_rows]
        gate_on = bool(project.managed or permissions)

        # Comment history (comments by people plus questions from the agent)
        crows = (
            await db.execute(select(Comment).where(Comment.issue_id == issue.id).order_by(Comment.created_at))
        ).scalars().all()
        # `agent_fail` stays out: those are mishap reports (worker restart, deadlock,
        # truncated answer), not work in progress. They remain on the ticket, but they have no
        # business in the prompt, because an agent reads it looking for its assignment. That is
        # exactly how the escalation in the provider router came about on 2026-08-07: read as
        # an assignment, implemented, committed to the branch, nothing to do with the ticket.
        comment_history = [
            {"label": c.author_label or ("User" if c.author_id else "Agent"),
             "role": "user" if c.author_id else "agent", "body": c.body}
            for c in crows if c.kind == "agent"
        ]

        result = await run_agent(
            db=db, agent=agent,
            issue={"id": issue.id, "key": issue.key, "summary": issue.summary,
                   "description": issue.description, "plan": issue.plan},
            project={"id": project.id, "key": project.key, "system_prompt": project.system_prompt,
                     "stack_dir": project.workspace_dir, "live_url": "",
                     "vault_moc_path": project.vault_moc_path},
            mode=mode, permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id,
            testenv_url=issue.testenv_url or "",
            continuation_index=job.get("continuation_index", 0),
            continuation_hint=job.get("continuation_hint", ""),
            comment_history=comment_history, task_id=task_id,
            delegate_loader=(lambda r: _load_agent(db, r, project.id, "execute", owner_id)
                             if r in _DEFAULTS else _none()),
        )

        # Review gate (at most 2 correction rounds) before finishing
        if (mode == "execute" and result.status == "done" and project.review_enabled
                and ctx is not None and ws_root):
            result = await _review_gate(db, project, issue, agent, ws_root, gate_on, tokens,
                                        permissions, result, ctx, owner_id, task_id=task_id,
                                        base_urls=base_urls)

        # ALWAYS commit the agent's changes (not only on 'done'), otherwise the work sits
        # uncommitted in the worktree on a review hold or a question and cannot be reviewed.
        merge_status = ""
        if mode == "execute" and ctx is not None:
            changes = await gitops.file_changes(ctx)
            cmsg = await gitops.commit(ctx, f"ticket {issue.key}: {issue.summary}")
            log.info("git commit %s: %s", issue.key, cmsg)
            # Make 0 changes visible, otherwise a ticket lands on to_test in silence.
            if not changes:
                db.add(Comment(
                    issue_id=issue.id, author_id=None, author_label="System", kind="internal",
                    body="⚠️ No code changes made. The agent implemented nothing "
                         "(the requirement may already be met, or was not recognised) — please check."))
            from ..models.ticket import TicketFileChange
            for o in (await db.execute(select(TicketFileChange).where(
                    TicketFileChange.issue_id == issue.id))).scalars().all():
                await db.delete(o)
            for c in changes:
                db.add(TicketFileChange(issue_id=issue.id, path=c["path"], status=c["status"],
                                        additions=c["additions"], deletions=c["deletions"]))
            await db.commit()

        fp = None
        try:
            fp = await gitops.worktree_fingerprint(ws_root) if ws_root else None
        except Exception:  # noqa: BLE001
            pass

        payload = {
            "status": result.status,
            "success": result.status == "done",
            "output": result.text, "summary": result.summary or result.text[:400],
            "run_id": result.run_id, "worktree_fingerprint": fp,
            "blocker": {"kind": result.blocker_kind} if result.blocker_kind else None,
            "merge_status": merge_status,
        }
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(payload), ex=RESULT_TTL)
        await redis.publish(f"{PREFIX}results", task_id)
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
        # Tidy the memory afterwards, as a task of its own so nobody waits for it. Until now
        # this only happened after assistant tasks, and those carry no project — so the
        # project notes were never tidied at all. `due()` still caps it at once per note and
        # day, so one nudge per ticket run costs nothing.
        if owner_id:
            from ..core.redis import enqueue_task
            await enqueue_task({"kind": "curator", "task_id": f"curator-{owner_id}-{task_id}",
                                "owner_id": owner_id, "agent_role": role,
                                "project_key": project.key})
    log.info("processed %s -> %s", task_id, result.status)


# Safety net for the correction rounds of the review gate, NOT the normal stop. What ends it
# is the matter itself: passing, or a correction that changes nothing in the code (a
# standstill). The earlier hard limit of 2 fetched a person while things were still moving,
# on ABC-32 (2026-08-07) even over a finding that came from the truncated diff display. A
# ticket should run through as long as it makes progress.
REVIEW_ROUNDS = int(os.getenv("REVIEW_MAX_RUNDEN", "6"))


async def _review_gate(db, project, issue, exec_agent, ws_root, gate_on, tokens, permissions,
                       result, ctx, owner_id=None, task_id="", base_urls=None):
    """The review agent checks the cumulative diff. <review-ok/> means it passed. Otherwise at
    most 2 correction rounds by the executing agent, then hold_review.

    The rounds used up are kept ON THE TICKET, not in this loop. A counter in the process is
    zero again after every worker restart, and ABC-32 ran straight into that on 2026-08-07:
    check, correct, restart, check, correct, and the limit that should fetch a person was
    never reached.
    """
    reviewer = await _load_agent(db, project.review_agent or "code_reviewer", project.id, "execute", owner_id)
    rev = None      # no review run this round (budget spent), so no finding text
    previous_diff: str | None = None
    for attempt in range(int(issue.review_rounds or 0), REVIEW_ROUNDS):
        diff = await gitops.diff_text(ctx)
        if not diff.strip():
            return result  # nothing changed, nothing to check
        # As long as something moves, work continues: the limit is a standstill, not a number
        # of rounds. If the last correction did not touch the diff, the next round brings
        # nothing, and then it fetches a person, with exactly that reason.
        if previous_diff is not None and diff == previous_diff:
            log.warning("review %s: round %d changed nothing, standstill",
                        issue.key, attempt)
            db.add(Comment(
                issue_id=issue.id, author_id=None, author_label="Reviewer", kind="internal",
                body=("🛑 Standstill in the review: the last correction changed nothing in the "
                      "code. Further rounds would only cost tokens.\n\nOpen "
                      "findings:\n\n" + (getattr(rev, "text", "") or "(no text)")[:4000])))
            await db.commit()
            from .runtime import RunResult
            return RunResult("blocked", "review gate: a correction without effect (standstill)",
                             run_id=result.run_id, blocker_kind="review")
        previous_diff = diff
        rev_prompt = (
            "Check the following diff strictly (bugs, security, edge cases). Answer EXACTLY `<review-ok/>` "
            "(nothing else) when there are no findings that force a correction. Otherwise number the "
            "findings (file/place/problem/expected correction) as a work order. Write no files.\n\n"
            f"# Diff for {issue.key}: {issue.summary}\n```diff\n{diff}\n```")
        rev = await run_agent(
            db=db, agent=reviewer,
            issue={"id": issue.id, "key": issue.key, "summary": f"Review {issue.key}",
                   "description": rev_prompt, "plan": None},
            project={"id": project.id, "key": project.key, "system_prompt": "", "stack_dir": "", "live_url": ""},
            mode="execute", permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command="", screenshot_enabled=False, owner_id=owner_id, task_id=task_id)
        if "<review-ok/>" in (rev.text or ""):
            log.info("review %s: passed (round %d)", issue.key, attempt + 1)
            return result
        # An ABORTED reviewer has no findings, it did not review at all. Without this
        # distinction its error message was passed on as an assignment: on 2026-08-07 ABC-31
        # sent the developer off to fix "claude: answer truncated at max_tokens … raise
        # max_tokens". That costs one of the two correction rounds, burns a full run and ends
        # in a review hold afterwards, over a finding that never existed.

        if rev.status != "done":
            log.warning("review %s: reviewer run %s (round %d), no findings, no assignment",
                        issue.key, rev.status, attempt + 1)
            db.add(Comment(
                issue_id=issue.id, author_id=None, author_label="System", kind="internal",
                body=(f"⚠️ The review run was aborted ({rev.status}): "
                      f"{(rev.text or '(ohne Meldung)')[:400]}\n\n"
                      "The diff is therefore UNCHECKED. The result goes on all the same — "
                      "an aborted check is no finding, and setting the developer onto "
                      "an error message would be an invented task.")))
            await db.commit()
            return result
        log.info("review %s: findings (round %d of %d), correcting",
                 issue.key, attempt + 1, REVIEW_ROUNDS)
        # The round is spent the moment it begins, and committed, so that it survives a
        # restart in the middle of the correction.
        issue.review_rounds = attempt + 1
        await db.commit()
        # Correction round by the executing agent
        result = await run_agent(
            db=db, agent=exec_agent,
            issue={"id": issue.id, "key": issue.key, "summary": issue.summary,
                   "description": issue.description, "plan": issue.plan},
            project={"id": project.id, "key": project.key, "system_prompt": project.system_prompt,
                     "stack_dir": project.workspace_dir, "live_url": ""},
            mode="execute", permissions=permissions, ws_root=ws_root, gate_on=gate_on, tokens=tokens,
            base_urls=base_urls,
            verify_command=project.verify_command, screenshot_enabled=project.screenshot_enabled,
            strict_success=await get_flag("strict_success"), owner_id=owner_id, task_id=task_id,
            continuation_index=99, continuation_hint="REVIEW-BEFUNDE (beheben):\n" + (rev.text or ""))
        if result.status != "done":
            return result
    # Rounds spent and findings still open, so a person takes over. WITH the findings: the
    # ticket used to carry only "hold: review", and whoever wanted to know why had to look
    # the run up in the database (ABC-32 on 2026-08-07). Somebody who has to decide needs
    # the reason in the same place as the decision.
    open_ones = (getattr(rev, "text", "") or "").strip()
    db.add(Comment(
        issue_id=issue.id, author_id=None, author_label="Reviewer", kind="internal",
        body=(f"🛑 After {REVIEW_ROUNDS} rounds of correction findings are still open — "
              "the ticket is waiting for you.\n\n" +
              (open_ones[:4000] if open_ones else
               "(the correction budget was used up before this round — the "
               "findings stand in the previous reviewer entry)") +
              "\n\nLet it work on: kick the ticket off again (the correction budget "
              "then starts over). Accept it: overrule the findings deliberately.")))
    await db.commit()
    from .runtime import RunResult
    return RunResult("blocked", f"Review-Gate: Befunde nach {REVIEW_ROUNDS} Runden offen",
                     run_id=result.run_id, blocker_kind="review")


async def _handle_accept(job: dict, redis: Redis) -> dict:
    """On acceptance: merge the ticket branch into main (and push), optionally queue an

    auto deploy. Returns the outcome as {"status": merged|conflict|push_failed|pr_open|
    pr_failed|no_git|gone, "error"?: str}, and `/complete` decides from it whether the ticket
    """
    from ..models.ops import Deployment
    async with SessionLocal() as db:
        issue = await db.get(Issue, job["issue_id"])
        project = await db.get(Project, job["project_id"])
        if issue is None or project is None:
            return {"status": "gone", "error": "The ticket or the project no longer exists"}
        # Idempotence: do NOT merge an already merged ticket again. This prevents duplicate or
        # late accept jobs (from queue recovery, for instance) from touching a cleanly merged
        # branch and running into a phantom conflict, which is a source of loops.
        if issue.merge_status == "merged":
            log.info("accept %s: already merged, skipped", job["issue_id"])
            return {"status": "merged"}
        if project.git_enabled and issue.branch_name:
            host = urlsplit(project.github_repo).hostname or ""
            owner_id = issue.assigned_by_user_id or issue.reporter_id or project.lead_user_id
            token = await resolve_git_token(db, project.git_token_enc, owner_id, host) or ""
            # A subticket merges into the umbrella branch, otherwise into the target branch.
            target = project.merge_target or "main"
            if issue.parent_ticket_id:
                umbrella = await db.get(Issue, issue.parent_ticket_id)
                if umbrella and umbrella.branch_name:
                    target = umbrella.branch_name
            ctx = gitops.GitCtx(
                workdir=gitops.project_workdir(project.key), branch=issue.branch_name,
                remote=project.github_repo, token=token,
                worktree=gitops.worktree_path(project.key, issue.key) if project.work_in_branches else None,
                base_commit=issue.git_base_sha, main=target, enabled=True)
            # Pre-merge gate: fresh main into the worktree, a conflict goes back to the agent
            pre = await gitops.precheck_merge(ctx)
            if pre and pre.conflict:
                issue.merge_status = "conflict"
                issue.merge_error = "Merge-Konflikt: " + ", ".join(pre.conflict_files[:8])
                issue.resolved_at = None
                issue.merge_conflict_rounds += 1
                # Loop brake: if the conflict does not converge it escalates instead of being
                # handed back to the agent forever. Whether that means "hold" or another
                # attempt is decided by the acceptance process; here only the finding is reported.
                escalate = issue.merge_conflict_rounds > MAX_CONFLICT_ROUNDS
                if escalate:
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="internal",
                                   body=f"⛔ Merge-Konflikt nach {issue.merge_conflict_rounds - 1} "
                                        f"attempts to resolve it — escalated to the person. "
                                        f"Konflikt in: {', '.join(pre.conflict_files[:8])}"))
                    log.info("accept %s: the conflict limit was reached, escalated", job["issue_id"])
                else:
                    # Put the conflict markers into the worktree so the agent can resolve them.
                    await gitops.setup_conflict_resolution(ctx)
                    log.info("accept %s: conflict (round %d), back to the agent",
                             job["issue_id"], issue.merge_conflict_rounds)
                await db.commit()
                await redis.publish(f"{PREFIX}events:{project.id}",
                                    json.dumps({"type": "issue_update", "issue_key": issue.key}))
                return {"status": "conflict", "error": issue.merge_error,
                        "escalate": escalate, "rounds": issue.merge_conflict_rounds}
            if project.use_pull_request and not issue.parent_ticket_id:
                # Instead of merging: push the branch, open a PR, the decision stays on GitHub.
                # (Subtickets always merge straight into the umbrella branch, no PR.)
                res = await gitops.open_pull_request(
                    ctx, title=f"{issue.key}: {issue.summary}",
                    body=(issue.plan or issue.description or "")[:60000])
                if res.startswith("pr:"):
                    url = res.split(":", 1)[1]
                    issue.merge_status = "pr_open"
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="agent",
                                   body=f"Pull request opened: {url}"))
                else:
                    issue.merge_status = "pr_failed"
                    issue.merge_error = res
                    db.add(Comment(issue_id=issue.id, author_id=None, kind="agent",
                                   body=f"The pull request could not be opened: {res}"))
                await db.commit()
                await redis.publish(f"{PREFIX}events:{project.id}",
                                    json.dumps({"type": "issue_update", "issue_key": issue.key}))
                log.info("accept %s -> %s", job["issue_id"], res.split(":", 1)[0])
                return ({"status": "pr_open"} if issue.merge_status == "pr_open"
                        else {"status": "pr_failed", "error": issue.merge_error})
            res = await gitops.accept(ctx)
            if res.startswith("merged:"):
                issue.merge_status = "merged"
                issue.merge_commit = res.split(":", 1)[1]
                issue.merged_into = ctx.main
                issue.merge_error = None
                issue.merge_conflict_rounds = 0   # came through cleanly, reset the counter
                await gitops.remove_worktree(ctx)
            elif res.startswith("conflict:"):
                issue.merge_status = "conflict"
                issue.merge_error = res
            elif res == "push_failed":
                # Merged locally, push to the remote failed: keep the worktree, do not deploy.
                issue.merge_status = "push_failed"
                issue.merge_error = "Push zum Remote fehlgeschlagen (Auth/Netz)."
            await db.commit()
        # Auto deploy ONLY on a real merge into the target branch (not for subtickets that
        # merge into the umbrella branch, and not on conflict/push_failed/pr).
        if project.auto_deploy and issue.merge_status == "merged" and not issue.parent_ticket_id:
            # Tickets must NOT deploy the host or maintenance project itself. An empty
            # (self targeting) stack_dir would be rejected by the deployer anyway and would
            # only produce a deploy storm on every pass through the loop (see ABC-19). The host
            # stack is recreated exclusively through the explicit, idle gated maintenance
            # update (dispatcher self_deploy, only when no agent is running).
            if project.workspace_dir:
                db.add(Deployment(project_id=project.id, issue_id=issue.id,
                                  stack_dir=project.workspace_dir, status="pending",
                                  source="merge"))
                await db.commit()
            else:
                log.info("accept %s: self or host project, no ticket deploy "
                         "(the host stack only over the maintenance update)", job["issue_id"])
        # Subticket merged: release the next parked sibling or finish the umbrella ticket
        # (only AFTER the merge, so that part n+1 builds on n).
        if issue.parent_ticket_id and issue.merge_status == "merged":
            from ..services.lifecycle_flow import promote_split
            await promote_split(db, issue)
            await db.commit()
        await redis.publish(f"{PREFIX}events:{project.id}",
                            json.dumps({"type": "issue_update", "issue_key": issue.key}))
    log.info("accept %s -> merge=%s deploy=%s", job["issue_id"], issue.merge_status, project.auto_deploy)
    if not (project.git_enabled and issue.branch_name):
        return {"status": "no_git"}   # project without git: nothing to merge, acceptance is free
    if issue.merge_status == "merged":
        return {"status": "merged"}
    return {"status": issue.merge_status or "failed", "error": issue.merge_error}


async def _handle_agent_free(job: dict, redis: Redis) -> None:
    """An agent run without anything: without a ticket, without a project, without an intake.

    Exactly that could until now only be done by a prompt job — the ability was stuck in the job
    kind and was not available out of a flow. Now it is a task like any other: who sets it (a
    job, a flow node) makes no difference from here, and the result goes back the same way as
    with all waiting steps.
    """
    from .runtime import run_agent
    task_id = job["task_id"]

    async def report(status: str, text: str) -> None:
        await redis.set(f"{PREFIX}result:{task_id}", json.dumps(
            {"status": status, "success": status == "done", "output": text[:20000],
             "summary": text[:2000]}), ex=RESULT_TTL)
        await redis.publish(f"{PREFIX}results", task_id)

    owner_id = job.get("owner_id")
    prompt = str(job.get("prompt") or "")
    name = str(job.get("name") or "Lauf")
    async with SessionLocal() as db:
        try:
            agent = await _load_agent(db, job.get("agent") or "assistent", 0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            result = await run_agent(
                db=db, agent=agent,
                issue={"id": None, "key": task_id, "summary": name,
                       "description": prompt, "plan": None},
                project={"id": None, "key": "", "system_prompt": "", "vault_moc_path": None},
                mode="execute", permissions=[], ws_root=None, gate_on=False, tokens=tokens,
                base_urls=base_urls, owner_id=owner_id, task_id=task_id)
            text = result.summary or result.text or ""
            await db.commit()
            await report("done" if result.status == "done" else "failed",
                        text if result.status == "done" else (result.text or result.status))
        except Exception as exc:  # noqa: BLE001 — whoever waits has to be told
            log.exception("free agent run %s failed", task_id)
            await report("failed", str(exc)[:2000])


# The prompt job no longer exists: a job is schedule plus flow, and the agent run in it is
# `agent_free` (above). With that the way through this queue falls away too — it was the only
# reason why a job could do exactly one thing.


# Conversation history in chat (ABC-30): a chat used to be a series of independent runs, so a
# person had to repeat themselves inside one conversation.
#
# The plain time window (8 exchanges / 12 h) cut the reference off ABRUPTLY: the person
# referred to yesterday, the assistant knew only the last hour. Now the most recent exchanges
# stay verbatim and everything older moves into a growing summary (`chat_summaries`), modelled
# on context compaction.
CHAT_HISTORY_MAX = 8
CHAT_HISTORY_HOURS = 12
# Only still for tasks WITHOUT a session (a mail item, a webhook run). For a conversation the
# session is the boundary: one that is picked up again after three weeks has to arrive whole,
# and how long it is stays the business of the summary block below, not of the calendar.
CHAT_MEMORY_DAYS = 14
# This many exchanges pile up above the verbatim window before a summary is made. Without
# this buffer an auxiliary run would happen on EVERY message from the ninth exchange on:
# waiting time for the person, without the memory changing noticeably.
CHAT_SUMMARY_BLOCK = 4

_SUMMARISE = (
    "You keep the memory of a personal assistant. Summarise the conversation so far in such a "
    "way that it can be carried on later without its person having to repeat themselves.\n\n"
    "Take in: what the person wants and has decided, their preferences and rules, open "
    "questions, agreed next steps, concrete facts (names, numbers, paths, ids). Leave out: "
    "politeness, repetitions, everything finished without an aftermath.\n\n"
    "Bullet points, in the language of the conversation, without a preamble. Keep it short, but "
    "lose no commitment."
)


async def _chat_history(db, t) -> list[dict]:
    """The thread of a conversation: a summary of the older part plus the recent exchanges verbatim.

    The SESSION is the cut. It used to be the calendar (`CHAT_MEMORY_DAYS`), which meant two
    things at once: a subject could not be begun without dragging yesterday's along, and a
    conversation picked up after three weeks arrived empty. A session that is loaded again
    must arrive whole — that is the point of loading it — and how long it gets is handled by
    the summary block, not by the calendar.

    A task without a session (a mail item, a webhook run) keeps the behaviour it has today:
    owner plus time window. Those never went through a conversation and must not start now.
    """
    import datetime as _dt

    from ..models.assistant import AssistantTask, ChatSummary
    agent_name = (t.meta or {}).get("agent") or "assistent"
    q = select(AssistantTask).where(
        AssistantTask.kind == "chat",
        AssistantTask.id != t.id,
        AssistantTask.status == "done",
    )
    if t.session_id:
        q = q.where(AssistantTask.session_id == t.session_id)
    else:
        since = _now_dt() - _dt.timedelta(days=CHAT_MEMORY_DAYS)
        q = q.where(AssistantTask.owner_user_id == t.owner_user_id,
                    AssistantTask.created_at >= since)
    all_rows = (await db.execute(q.order_by(AssistantTask.id))).scalars().all()
    # A session belongs to one agent, so this is a cheap consistency net rather than the
    # separation itself. Without a session it IS the separation: the GameProj operator has
    # nothing to do with the assistant.
    all_rows = [r for r in all_rows if ((r.meta or {}).get("agent") or "assistent") == agent_name]

    def exchange(r) -> list[dict]:
        meta = r.meta or {}
        out = []
        if (question := (meta.get("chat_text") or r.title or "").strip()):
            out.append({"label": "Dein Mensch", "role": "user", "body": question[:2000]})
        if (answer := (r.result or "").strip()):
            out.append({"label": "Du", "role": "agent", "body": answer[:2000]})
        return out

    # The memory of THIS session. Reading it by (owner, agent) alone was the one bug worth
    # naming: the compacted memory of one conversation would be read into the next, and
    # nothing about it is visible — the agent simply "remembers" something the human never
    # said in this conversation.
    summary = (await db.execute(select(ChatSummary).where(
        ChatSummary.owner_user_id == t.owner_user_id,
        ChatSummary.agent == agent_name,
        ChatSummary.session_id == t.session_id,
    ).order_by(ChatSummary.id))).scalars().first()

    # Not summarised yet means it stands verbatim in the history. Summarising happens in
    # blocks, not on every message moving up: otherwise an auxiliary run would happen on EVERY
    # message once the conversation passes eight exchanges.
    open_ones = [r for r in all_rows if r.id > (summary.to_task_id if summary else 0)]
    new_to_grasp: list = []
    if len(open_ones) > CHAT_HISTORY_MAX + CHAT_SUMMARY_BLOCK:
        new_to_grasp = open_ones[:-CHAT_HISTORY_MAX]
    young = open_ones[-CHAT_HISTORY_MAX:] if new_to_grasp else open_ones
    if new_to_grasp:
        sofar = (summary.text if summary else "").strip()
        raw = "\n".join(f"{w['label']}: {w['body']}" for r in new_to_grasp for w in exchange(r))
        task = (_SUMMARISE
                   + ("\n\n--- The memory so far (carry it on, lose nothing) ---\n" + sofar
                      if sofar else "")
                   + "\n\n--- Neue Wortwechsel ---\n" + raw)
        from .aux import aux_chat
        agent = await _load_agent(db, agent_name, 0, "execute", t.owner_user_id)
        tokens, base_urls = await _build_tokens(db, t.owner_user_id, agent)
        text = await aux_chat(db, owner_id=t.owner_user_id, task="compression",
                              messages=[{"role": "user", "content": task}],
                              agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1500)
        if text:
            if summary is None:
                summary = ChatSummary(owner_user_id=t.owner_user_id, agent=agent_name,
                                      session_id=t.session_id)
                db.add(summary)
            summary.text = text
            summary.to_task_id = new_to_grasp[-1].id
            await db.commit()
        # No result (the auxiliary run is unreachable): the old summary still applies. A
        # slightly stale memory is better than none, and the thread does not tear.

    history: list[dict] = []
    if summary and summary.text.strip():
        history.append({"label": "What you remember", "role": "agent",
                        "body": "# Earlier parts of this conversation\n" + summary.text.strip()})
    for r in young:
        history.extend(exchange(r))
    return history


# The rule for reporting: the run itself is not worth a message.
REPORT_RULE = (
    "WICHTIG — Melden: Deine Abschluss-Zusammenfassung geht NICHT an deinen Menschen, sie "
    "landet nur still im Posteingang. Soll er etwas erfahren (Frist, Geldbetrag, "
    "a decision, a fault, something they have to answer), call `traccoon_notify_human` with a "
    "short, concrete report. For things finished without anything to do (filed, noted, nothing "
    "to do) you do NOT report."
)


async def _reference_source(db, task_id) -> str:
    """Extra context for the quoted message: what was the original mail about?"""
    if not task_id:
        return ""
    from ..models.assistant import AssistantTask
    source = await db.get(AssistantTask, int(task_id))
    if source is None:
        return ""
    parts = [f"This message belongs to your case \"{source.title}\" "
             f"({source.kind}, Stand {source.status})."]
    if source.result:
        parts.append(f"Was du dort zuletzt berichtet hast:\n{source.result[:1500]}")
    return "\n".join(parts) + "\n\n"


async def _handle_assistant_task(job: dict, redis: Redis) -> None:
    """Work through an approved assistant item (a mail, for instance) with the owner's full

    tool loop. Projectless like `_handle_job`: run_agent with issue.id=None/project.id=None,
    the owner's token and tool group. The prompt carries the REDACTED summary plus the mail
    metadata; the assistant fetches the full text itself through the IMAP tools when it needs
    it (the approval by the person was at the same time the approval for that access).
    """
    from ..models.assistant import AssistantTask
    from ..models.notification import Notification
    from .runtime import run_agent
    tid = job["assistant_task_id"]

    async def _report(status: str, text: str) -> None:
        """Publish the result under the task id. A waiting process step reads exactly this.

        Silence would not be neutral here: whoever waits (`assistent_auftrag` with `warten`)
        would sit out the whole time limit and then read "run vanished" although the run had
        long finished.
        """
        try:
            await redis.set(f"{PREFIX}result:{job['task_id']}", json.dumps(
                {"status": status, "success": status == "done", "output": text[:20000],
                 "summary": text[:2000]}), ex=RESULT_TTL)
            await redis.publish(f"{PREFIX}results", job["task_id"])
        except Exception:  # noqa: BLE001
            log.exception("assistant task %s: result could not be published", tid)

    async with SessionLocal() as db:
        t = await db.get(AssistantTask, tid)
        if t is None:
            # Race against the commit of whoever ordered the work: the process step queues
            # the job and only commits its transaction afterwards, so for a moment the item
            # does not exist for anybody else. Dropping it here silently cost a whole run on
            # 2026-08-19 — the item stayed on `approved`, the worker said nothing.
            for _ in range(5):
                await asyncio.sleep(1)
                db.expire_all()
                t = await db.get(AssistantTask, tid)
                if t is not None:
                    break
        if t is None:
            log.warning("assistant task %s does not exist (also not after waiting) — dropped", tid)
            await _report("error", f"Assistent-Item {tid} nicht gefunden")
            return
        if t.status not in ("approved",):
            log.info("assistant task %s stands on '%s', not on 'approved' — no run", tid, t.status)
            await _report("error", f"the assistant item {tid} stands on '{t.status}'")
            return
        t.status = "running"
        await db.commit()

        owner_id = t.owner_user_id
        meta = t.meta or {}
        acc, uid = meta.get("account", ""), meta.get("uid", "")
        is_chat = t.kind == "chat"
        head = (f"Von: {meta.get('from', '')}\nBetreff: {meta.get('subject', '')}\n"
                f"Category: {t.category} · priority: {t.priority}\n\n")
        if t.redaction == "unredacted" and t.raw_body:
            # The source is marked trustworthy by a rule, so the full text goes in directly.
            content = f"Full text (released for this source):\n{t.raw_body}\n\n"
        elif t.redacted_summary:
            content = (f"Summary (redacted):\n{t.redacted_summary}\n\n"
                       f"The full text lies in the IMAP account '{acc}' under UID {uid}. Read it ONLY "
                       "through the imap tools if you really need it to act.\n\n")
        else:
            # Passthrough (no pre-classification, as in the predecessor): the agent reads the mail itself.
            content = (f"The mail lies in the IMAP account '{acc}' under UID {uid}. Read it through the "
                       "imap-Tools.\n\n")
        learned = (f"A learned rule of your person for such intakes: {t.action_hint}\n\n"
                   if t.action_hint else "")
        if is_chat:
            # Direct chat: the message IS the assignment. The system is operated through the
            # traccoon_* tools (with the rights of your person), personal things through your own tools.
            prompt = (meta.get("chat_text") or t.title) + (
                f"\n\n(Kontext: gelernte Vorgabe — {t.action_hint})" if t.action_hint else "")
            # A reply to one particular message: that message is the reference, not the
            # conversation in general. Without it "do that" would have no object, and the
            # earlier item (mail, approval) would only exist as a scrap of memory.
            if meta.get("bezug_text"):
                source = await _reference_source(db, meta.get("bezug_task_id"))
                prompt = (
                    "Your person is answering DIRECTLY to this message of yours:\n"
                    f"---\n{meta['bezug_text']}\n---\n"
                    + source +
                    "Their answer to it is your task — work on exactly that matter "
                    "instead of merely taking note of it:\n\n" + prompt
                )
        elif meta.get("prompt"):
            # The full task prompt from the webhook (ported knowledge about handling mail).
            prompt = meta["prompt"] + (learned if t.action_hint else "") + "\n\n" + REPORT_RULE
        else:
            prompt = (
                "An intake for your person (pre-classified locally).\n" + head + content + learned +
                "Decide on your own and in the spirit of your person what is to be done (note it in "
                "the vault, prepare a draft, create an appointment, file it …) and carry it "
                "out. Summarise briefly at the end what you did.\n\n" + REPORT_RULE
            )
        # In chat the run carries the conversation so far, otherwise a person would have to
        # repeat every reference in every message.
        history = await _chat_history(db, t) if is_chat else []
        out, status, err, run_id = "", "done", "", None
        question_open = False
        try:
            # The handling agent comes from the item (webhook config), default 'assistent'. No env.
            agent = await _load_agent(db, meta.get("agent") or "assistent",
                                      0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            result = await run_agent(
                db=db, agent=agent,
                issue={"id": None, "key": f"assistant-{t.id}", "summary": t.title,
                       "description": prompt, "plan": None},
                project={"id": None, "key": "", "system_prompt": "", "vault_moc_path": None},
                mode="execute", permissions=[], ws_root=None, gate_on=False, tokens=tokens,
                base_urls=base_urls, owner_id=owner_id, task_id=job["task_id"],
                comment_history=history,
                history_title="# The conversation so far (oldest message first)",
                assistant_task_id=t.id)
            if result.status == "blocked" and getattr(result, "blocker_kind", None) == "assistant_perm":
                # Tool gate: the item waits for approval (status awaiting, chat card set).
                # Do NOT finalise, the run is started again after the decision.
                log.info("assistant task %s waits for approval (%s)", tid, result.text)
                return
            out = result.summary or result.text or ""
            run_id = getattr(result, "run_id", None)
            if result.status == "blocked":
                # A question (ask_human): without a project there is no ticket it could hang
                # on. In a conversation the question IS the answer: report done so it reaches
                # the person and stays in the history (`_chat_history`).
                question_open = True
            elif result.status not in ("done",):
                status, err = "error", (result.text or result.status)
        except Exception as exc:  # noqa: BLE001
            status, err = "error", str(exc)

        t.status = status
        t.result = out[:20000]
        t.error = err[:2000]
        t.run_id = run_id
        t.finished_at = _now_dt()
        owner = await db.get(User, owner_id) if owner_id else None
        # When the assistant reports at all. The default "needed" means only when there is
        # something to know, because the run itself is not worth a message. Otherwise every
        # filed advertising mail would be a chat ping.
        mode = (owner.assistant_notify if owner else "needed") or "needed"
        if is_chat:
            report = True          # a question that was asked is always answered
        elif question_open:
            report = True          # the assistant asks back, otherwise it waits for nobody
        elif mode == "never":
            report = False
        elif status == "error":
            report = True          # a mishap has to be known
        elif mode == "always":
            report = True
        else:
            # needed/errors: the assistant reports on its own when there is something to know
            # (`traccoon_notify_human`), and the closing report stays silent, otherwise the
            # dieselbe Sache zweimal an.
            report = False
        if report:
            # Who answered belongs in the title, otherwise answers from the personal assistant
            # and from a specialised agent (gameproj-operator, say) cannot be told apart.
            label = "🤖 Assistent" if (meta.get("agent") or "assistent") == "assistent" \
                else f"🛰 {meta['agent']}"
            title = (label if is_chat else f"{label}: {t.title}") + (
                " — Fehler" if status == "error"
                else " — a question" if question_open and not is_chat else "")
            db.add(Notification(kind="assistant", title=title[:200],
                                body=(err if status == "error" else out)[:4000],
                                chat_id=owner.telegram_chat_id if owner else None))
        elif not t.notified:
            # Done quietly: the result stands in the assistant's inbox. As an unread bell
            # entry without a chat id it would be noise, so nothing at all.
            log.info("assistant task %s quietly done (mode %s)", tid, mode)
        await db.commit()
    await _report(status, (err if status == "error" else out) or "")
    log.info("assistant task %s -> %s", tid, status)
    # Trigger the memory upkeep, after the work is done and as a task of its own. `kuratiere`
    # decides for itself whether anything is due (at most once per day and note).
    if owner_id:
        from ..core.redis import enqueue_task
        await enqueue_task({"kind": "curator", "task_id": f"curator-{owner_id}-{tid}",
                            "owner_id": owner_id,
                            "agent_role": (meta.get("agent") or "assistent")})


async def _handle_curator(job: dict) -> None:
    """Tidy the memory, as a task of its own and not inside the conversation.

    The trigger here is the end of an assistant run, but the work itself runs separately so
    that nobody waits for it. If it fails that has no consequence: the memory simply stays as
    it was.
    """
    from .aux import aux_config
    from .curator import curate
    from .mcp_client import mcp_session
    from .runtime import _agent_mcp, _owner_gateway
    owner_id = job.get("owner_id")
    role = job.get("agent_role") or "assistent"
    # Empty for a projectless run — `note_path` then yields no project notes, which is exactly
    # right there.
    project_key = job.get("project_key") or ""
    if not owner_id:
        return
    async with SessionLocal() as db:
        # Without a model of its own for memory upkeep it stays OFF. Otherwise the working
        # model would run unnoticed in the background: expensive for busywork, and it would
        # write into the person's vault unasked. Compaction may use `auto` (the alternative
        # there is an aborted run), tidying may not.
        if not await aux_config(db, "curator"):
            return
        try:
            agent = await _load_agent(db, role, 0, "execute", owner_id)
            tokens, base_urls = await _build_tokens(db, owner_id, agent)
            gw_url, gw_token = await _owner_gateway(db, owner_id)
            async with mcp_session(agent.name, servers=await _agent_mcp(db, agent, owner_id),
                                   gateway_url=gw_url or "", gateway_token=gw_token or "") as mcp:
                reports = await curate(db, mcp, owner_id=owner_id, agent_role=role,
                                           project_key=project_key, agent=agent, tokens=tokens,
                                           base_urls=base_urls)
            for b in reports:
                log.info("Curator: %s", b)
        except Exception:  # noqa: BLE001
            log.exception("Curator failed (without consequences)")


def _now_dt():
    import datetime as _dt
    return _dt.datetime.now(tz=_dt.timezone.utc)


async def heartbeat(redis: Redis) -> None:
    while True:
        try:
            await redis.set(f"{PREFIX}runner:heartbeat", int(time.time() * 1000), ex=10)
        except Exception:  # noqa: BLE001
            pass
        _loop_tick()
        await asyncio.sleep(5)


async def _pulse(redis: Redis, task_id: str) -> None:
    """Sign of life for ONE task while it is being processed.

    The runner heartbeat only says "a worker is running", not whether THIS task still belongs
    to somebody. The watchdog in the backend waits along this pulse and may therefore wait
    arbitrarily long: an agent run with several review rounds does take hours, and it only
    disappears when the pulse stops.
    """
    if not task_id:
        return
    while True:
        try:
            await redis.set(pulse_key(task_id), int(time.time()), ex=PULSE_TTL)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(PULSE_BEAT)


# ── Watchdog over the event loop ────────────────────────────────────────────
# On 2026-07-30 the worker stood still for over an hour: no heartbeat, eleven tasks in the
# queue, none picked up, and NOT a line in the log. From the outside the container looked
# healthy, the assistant simply said nothing. Once the loop stands still no coroutine can
# report it any more, so a real thread watches here and writes the stacks of all threads into
# the log as soon as the loop stops ticking. That makes the next case diagnosable instead of
# leaving silence behind again.
_LAST_TICK = time.monotonic()
LOOP_STALL_SEC = float(os.getenv("WORKER_STALL_SEC", "60"))
# On 2026-07-31 the watchdog did its job and still helped nothing: stacks in the log, then
# eight hours of standstill at 100 % CPU (an endless loop in the compaction), no answer in
# chat. Reporting alone is not enough. If the loop stands that long it is dead, and then it
# is better to leave and let the container (restart: unless-stopped) start again. 0 switches
# the exit off.
LOOP_KILL_SEC = float(os.getenv("WORKER_STALL_KILL_SEC", "300"))


def _loop_tick() -> None:
    global _LAST_TICK
    _LAST_TICK = time.monotonic()


def watchdog_check(reported: bool) -> bool:
    """One pass of the watchdog. Returns whether the standstill is (still) reported."""
    stands_since = time.monotonic() - _LAST_TICK
    if stands_since > LOOP_STALL_SEC:
        if not reported:
            log.error("The event loop has not ticked for %.0fs, thread stacks follow", stands_since)
            faulthandler.dump_traceback()   # to stderr, so into the container log
        if LOOP_KILL_SEC and stands_since > LOOP_KILL_SEC:
            log.error("The event loop has stood for %.0fs, the worker ends itself for the restart", stands_since)
            faulthandler.dump_traceback()   # the last state before leaving
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(1)                     # no clean shutdown possible: the loop does not react
        return True
    if reported:
        log.warning("The event loop runs again (standstill %.0fs)", stands_since)
    return False


def start_loop_watchdog() -> None:
    def run() -> None:
        reported = False
        while True:
            time.sleep(5)
            reported = watchdog_check(reported)

    threading.Thread(target=run, name="loop-watchdog", daemon=True).start()


# ── Cleaning up after a hard exit ───────────────────────────────────────────
# Whoever was running during a crash is not running any more, only nobody knew that: runs
# stood on `running` for days, assistant tasks likewise. And because the handler only accepts
# `approved`, a task recovered from PROCESSING was silently dropped on restart. An outage
# thereby became an invisible outage.
#
# Assumption: exactly ONE worker container runs (the compose file knows no replicas). The
# grace period still protects the edge case that a run was just being created next door:
# whatever the watchdog kills has been standing for minutes anyway.
STALE_GRACE_SEC = 60


async def _run_finish(task_id: str, reason: str) -> None:
    """Close the run row of an aborted task, with the real cause.

    Whoever aborts knows why. If the row stays on "running", the watchdog for dead runs finds
    it later and has to guess: run 778 was buried on 2026-08-07 as "no sign of life … crash
    while writing", although somebody had pressed stop. A wrong cause is worse than none,
    because it ends the search.
    """
    if not task_id:
        return
    from ..models.agents import Run
    try:
        async with SessionLocal() as db:
            run = (await db.execute(select(Run).where(
                Run.task_id == task_id, Run.status == "running")
                .order_by(Run.id.desc()).limit(1))).scalars().first()
            if run is None:
                return
            run.status, run.finished_at = "failed", _now_dt()
            run.error = ((run.error or "") + reason).strip()
            await db.commit()
    except Exception:  # noqa: BLE001 — the cleanup must not make the abort worse
        log.exception("Run row not closed after the abort (task %s)", task_id)


async def sweep_corpses_and_report() -> None:
    """Once at startup: close orphaned runs and tasks and tell the people about it."""
    from ..models.agents import Run
    from ..models.assistant import AssistantTask
    from ..models.notification import Notification
    import datetime as _dt
    limit = _now_dt() - _dt.timedelta(seconds=STALE_GRACE_SEC)
    hint = "Worker restart: the run was not finished when it was aborted and is not continued."
    async with SessionLocal() as db:
        runs = (await db.execute(select(Run).where(
            Run.status == "running", Run.started_at < limit))).scalars().all()
        for r in runs:
            r.status, r.error, r.finished_at = "failed", (r.error or "") + hint, _now_dt()

        tasks = (await db.execute(select(AssistantTask).where(
            AssistantTask.status == "running", AssistantTask.updated_at < limit))).scalars().all()
        for t in tasks:
            t.status, t.error, t.finished_at = "error", (t.error or "") + hint, _now_dt()

        if not runs and not tasks:
            await db.commit()
            return

        # Recipients: whoever owns the tasks, plus the admins (runs carry no owner). Only
        # accounts with chat: an outage nobody reads is not one outage less, and burying
        # dormant admin accounts under unread bells helps nobody.
        recipient = {t.owner_user_id for t in tasks if t.owner_user_id}
        admins = (await db.execute(select(User).where(
            User.global_role == GlobalRole.admin))).scalars().all()
        recipient |= {u.id for u in admins}
        users = [u for u in (await db.execute(select(User).where(
            User.id.in_(recipient)))).scalars().all() if (u.telegram_chat_id or "").strip()]

        def _listing(ids: list[int]) -> str:
            return ", ".join(str(i) for i in ids[:10]) + (" …" if len(ids) > 10 else "")

        body = (f"The worker was restarted. Aborted: {len(runs)} run(s)"
                f"{' (' + _listing([r.id for r in runs]) + ')' if runs else ''}"
                f", {len(tasks)} assistant task(s)"
                f"{' (' + _listing([t.id for t in tasks]) + ')' if tasks else ''}.\n\n"
                "This work is NOT repeated automatically — if it is still needed, please "
                "send it again.")
        for u in users:
            db.add(Notification(user_id=u.id, kind="failed",
                                title="⚠️ The worker restarted after an abort",
                                body=body[:4000], chat_id=u.telegram_chat_id))
        await db.commit()
    log.warning("Clean-up after the restart: %d run(s) and %d assistant task(s) finished",
                len(runs), len(tasks))


PULL_INTERVAL = 60


async def pull_loop(redis: Redis) -> None:
    """Keeps the main checkouts of all git projects fresh (fetch plus fast forward).

    Once at startup: push leftovers out of PROCESSING (a job popped by blmove while the worker
    crashed before the ACK) back into QUEUE so that they are not lost.
    """
    recovered = 0
    duplicates = 0
    seen: set[str] = set()
    while True:
        raw = await redis.rpop(PROCESSING)
        if raw is None:
            break
        try:
            tid = json.loads(raw).get("task_id")
        except Exception:  # noqa: BLE001
            tid = None  # unparsable: treat like having no task_id, never drop as a duplicate
        # Deduplicate only real run jobs that carry a task_id. On repeated restarts the same
        # task_id can lie in PROCESSING several times, so only DISTINCT goes back into QUEUE
        # and further occurrences are dropped. Jobs without a task_id are always requeued.
        if tid and tid in seen:
            duplicates += 1
            continue
        if tid:
            seen.add(tid)
        await redis.lpush(QUEUE, raw)
        recovered += 1
    if recovered or duplicates:
        log.info("Recovery: %d job(s) from PROCESSING back into QUEUE, %d duplicate(s) discarded",
                 recovered, duplicates)
    while True:
        await asyncio.sleep(PULL_INTERVAL)
        try:
            async with SessionLocal() as db:
                projects = (await db.execute(
                    select(Project).where(Project.git_enabled.is_(True)))).scalars().all()
                for project in projects:
                    if not project.github_repo:
                        continue
                    host = urlsplit(project.github_repo).hostname or ""
                    token = await resolve_git_token(db, project.git_token_enc, None, host) or ""
                    ctx = gitops.GitCtx(
                        workdir=gitops.project_workdir(project.key), branch=project.merge_target or "main",
                        remote=project.github_repo, token=token, worktree=None,
                        main=project.merge_target or "main", enabled=True)
                    if not os.path.isdir(ctx.workdir):
                        continue  # never cloned: the first run takes care of that
                    note = await gitops.refresh_main(ctx)
                    if note not in ("main aktualisiert", "kein Remote"):
                        log.debug("pull %s: %s", project.key, note)
        except Exception:  # noqa: BLE001
            log.exception("pull loop error")


# Running runs per ticket key, for the kill channel (aborting from the interface)
RUNNING: dict[str, asyncio.Task] = {}
# Set as soon as SIGTERM/SIGINT arrived: accept no new tasks, let running ones finish.
# Without this every deploy tore the agents out mid turn: twice on ABC-31 on 2026-08-07,
# each time after nearly 40 turns of work.
_shutdown = asyncio.Event()
# The mirror in Redis (ACTIVE) comes from core.redis, and the backend checks the same key.

# In-flight deduplication: task_ids currently being processed. A restart storm can put the
# same job (same task_id) into the queue several times through the PROCESSING→QUEUE recovery;
# without this lock the worker (concurrency>1) would run it in parallel, causing a burst of
# requests per minute and HTTP 429. Access happens only from the one event loop, so no lock is
# needed as long as check and add happen without an await in between (atomically) before the start.
_inflight_task_ids: set[str] = set()


async def kill_listener(redis: Redis) -> None:
    """Abonniert traccoon:kill; Payload = Ticket-Key → laufenden Lauf abbrechen."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"{PREFIX}kill")
    async for msg in pubsub.listen():
        if msg.get("type") != "message":
            continue
        key = (msg.get("data") or "").strip()
        task = RUNNING.get(key)
        if task and not task.done():
            task.cancel()
            log.info("kill: run for %s aborted", key)


async def main() -> None:
    # socket_keepalive plus health_check_interval: without them the client waits forever for
    # an answer on a half dead connection, with no error, no timeout and no log line. The
    # blocking client additionally needs a socket limit ABOVE the BLMOVE wait, otherwise the
    # server side timeout does not cover the case at all.
    redis = Redis.from_url(settings.redis_url, **_REDIS_KW)
    blocking = Redis.from_url(settings.redis_url, socket_timeout=BLOCK_TIMEOUT + 10, **_REDIS_KW)
    killer = Redis.from_url(settings.redis_url, **_REDIS_KW)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    # Drop entries from a crashed previous life, otherwise the interface shows runs that do
    # not exist any more.
    await redis.delete(ACTIVE)
    # Before the first job: whatever still stands on `running` from the previous life is dead.
    # Clean up and report, otherwise an outage stays invisible (the handler drops `running`
    try:
        await sweep_corpses_and_report()
    except Exception:  # noqa: BLE001
        log.exception("Clean-up after the restart failed (the worker starts regardless)")
    asyncio.create_task(heartbeat(redis))
    asyncio.create_task(kill_listener(killer))
    asyncio.create_task(pull_loop(redis))
    start_loop_watchdog()
    _signals_accept()
    log.info("Traccoon worker started (concurrency=%d, drain time %ds)",
             MAX_CONCURRENT, DRAIN_SEC)

    async def _run(job: dict, raw: str) -> None:
        key = job.get("issue_key") or job.get("task_id", "")
        async with sem:
            RUNNING[key] = asyncio.current_task()
            await redis.hset(ACTIVE, key, json.dumps({
                "issue_key": key, "task_id": job.get("task_id", ""), "role": job.get("role", ""),
                "phase": job.get("phase", ""), "project_id": job.get("project_id"),
                "started_at": time.time()}))
            # Pulse: as long as this task is being processed here the backend knows it is
            # alive, whether it takes two minutes or five hours. The watchdog there waits
            # along it instead of giving up after a fixed time.
            pulse = asyncio.create_task(_pulse(redis, job.get("task_id", "")))
            try:
                await handle(job, redis)
                # Clean pass, so ACK: remove exactly the popped entry from PROCESSING.
                await redis.lrem(PROCESSING, 1, raw)
            except asyncio.CancelledError:
                log.info("Run %s aborted (kill)", key)
                await redis.set(f"{PREFIX}result:{job['task_id']}", json.dumps(
                    {"status": "failed", "success": False,
                     "output": "Abgebrochen (Stopp durch Nutzer)"}), ex=RESULT_TTL)
                await redis.publish(f"{PREFIX}results", job["task_id"])
                # Close the run row here. Without that it stayed on "running", and the
                # watchdog for dead runs later found a corpse whose cause it did not know:
                # run 778 was buried on 2026-08-07 as "no sign of life … crash while writing",
                # although somebody had simply pressed stop. Whoever knows the cause should
                # write it down.
                await _run_finish(job.get("task_id", ""),
                                         "Aborted: a stop over the kill channel (a button, "
                                         "Prozess-Schritt oder Wartungs-Update).")
                # No ACK: the entry stays in PROCESSING, so recovery brings it back into QUEUE
                # at the next worker start (no data loss on abort or crash).
            except Exception as exc:  # noqa: BLE001
                log.exception("handle error")
                # IMPORTANT: publish the result, otherwise the dispatcher hangs until the
                # 1800 s timeout and the ticket stays agent_working=True (blocking a slot).
                tid = job.get("task_id")
                if tid:
                    await redis.set(f"{PREFIX}result:{tid}", json.dumps(
                        {"status": "failed", "success": False,
                         "output": f"Interner Worker-Fehler: {exc}"[:500]}), ex=RESULT_TTL)
                    await redis.publish(f"{PREFIX}results", tid)
                # No ACK: the entry stays in PROCESSING, so recovery brings it back into QUEUE
                # at the next worker start instead of losing it silently here.
            finally:
                RUNNING.pop(key, None)
                pulse.cancel()
                await redis.hdel(ACTIVE, key)
                # Delete the pulse right away instead of letting it expire: the result is
                # already in Redis, and an echo would make the watchdog wait for nothing.
                await redis.delete(pulse_key(job.get("task_id", "")))
                # Release the in-flight lock reliably, including on an exception or an abort,
                # otherwise the task_id would stay marked as "already running" forever.
                _inflight_task_ids.discard(job.get("task_id"))

    while not _shutdown.is_set():
        try:
            # blmove instead of brpop: the job moves atomically from QUEUE to PROCESSING
            # instead of merely disappearing. If the worker dies before the ACK, recovery in
            # pull_loop() finds it again at the next start (a reliable queue).
            raw = await blocking.blmove(QUEUE, PROCESSING, timeout=BLOCK_TIMEOUT,
                                        src="RIGHT", dest="LEFT")
            _loop_tick()
            if not raw:
                continue
            if _shutdown.is_set():
                # The signal arrived between blmove and here: put the task BACK into the
                # queue instead of starting it in a dying process. It should be picked up by
                # the new worker right away, not only by its recovery out of PROCESSING.

                await redis.lrem(PROCESSING, 1, raw)
                await redis.rpush(QUEUE, raw)
                break
            job = json.loads(raw)
            # In-flight deduplication by task_id: if the same dispatch is already running (for
            # instance queued twice by the restart recovery), remove this job from PROCESSING
            # and do NOT process it. Check and add without an await in between, so atomic in
            # the event loop. Jobs without a task_id run unchanged and are never blocked.
            task_id = job.get("task_id")
            if task_id:
                if task_id in _inflight_task_ids:
                    await redis.lrem(PROCESSING, 1, raw)
                    log.warning("Duplicate job task_id=%s discarded (already running)", task_id)
                    continue
                _inflight_task_ids.add(task_id)
            asyncio.create_task(_run(job, raw))
        except RedisTimeoutError:
            continue      # the socket limit hit before the answer, nothing special
        except Exception:  # noqa: BLE001
            log.exception("loop error")
            await asyncio.sleep(1)

    await _drain()


def _signals_accept() -> None:
    """Stop letting SIGTERM/SIGINT strike in the middle of the work.

    On a restart Docker first sends SIGTERM and kills after the grace period. Without a
    handler the process died at once, together with every agent that was thinking. The
    requeue saves the task, not the conversation: on 2026-08-07 that cost ABC-31 nearly 40
    turns of work, twice.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown.set)
        except NotImplementedError:      # Windows or a test environment: then as before
            pass


async def _drain() -> None:
    """Finish running tasks, as far as the grace period reaches."""
    running = [t for t in RUNNING.values() if not t.done()]
    if not running:
        log.info("The worker shuts down, nothing is running any more")
        return
    log.info("The worker shuts down: %d run(s) active, waiting up to %d s "
             "(new assignments are no longer accepted)", len(running), DRAIN_SEC)
    _done, open_ones = await asyncio.wait(running, timeout=DRAIN_SEC)
    if open_ones:
        # No abort by hand: the tasks still stand in PROCESSING, the recovery of the next
        # worker fetches them back, and the successor builds its handover from the step rows.
        # Docker ends the process in a moment anyway.
        log.warning("Grace time over, %d run(s) unfinished: they are queued anew",
                    len(open_ones))
    else:
        log.info("All runs finished cleanly")


if __name__ == "__main__":
    asyncio.run(main())
