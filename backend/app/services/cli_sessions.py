"""Claude CLI sessions: one interactive Claude Code per (project, person).

A project in CLI mode (`projects.cli_mode`) has no PM chat and no agents. Instead every
member opens a session: a container of the `traccoon-cli` image (see cli/), started by the
deployer, with the person's own subscription token and config volume. The terminal reaches
the browser over api/cli.py.

Tickets: the lifecycle graph stays as it is. Where it would queue a worker run
(`workflow_engine._start_agent_task`), a project in CLI mode calls `enqueue` instead. The
workflow token then waits on the same result key it would wait on for a worker; the session
answers under it with the MCP tool `ticket_report` (`report` below), and the graph moves on
over its usual edges. The run counts as alive for as long as the key is in `CLI_TASKS`.

Delivery per ticket (`issues.cli_delivery`): `queue` waits until the session has reported
its current queued ticket, `now` is typed in straight away. `issues.cli_context=clear` sends
`/clear` first.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.redis import PREFIX, RESULT_TTL, get_redis, publish_event
from ..core.security import encrypt_secret
from ..db import SessionLocal
from ..models.cli import CliDelivery, CliSession
from ..models.project import Project
from ..models.ticket import Comment, Issue
from ..models.user import User

log = logging.getLogger("traccoon.cli")

DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://deployer:8661")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
WORKSPACE_HOST_PATH = os.getenv("WORKSPACE_HOST_PATH", "")
CLI_DATA_HOST_PATH = os.getenv("CLI_DATA_HOST_PATH", "")
CLI_MCP_URL = os.getenv("CLI_MCP_URL", "http://traccoon-backend:8800/api/mcp/project")
# The task ids of tickets that are in a session (waiting or delivered). `core.redis.run_alive`
# reads it: without it the engine would take a ticket nobody works on in a worker for a
# vanished run after five minutes.
CLI_TASKS = PREFIX + "cli:tasks"
# The port ttyd serves the tmux session on, inside the container.
TTYD_PORT = 7681
REPORT_STATES = ("done", "blocked", "failed")

_locks: dict[int, asyncio.Lock] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def container_name(project: Project, user_id: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project.key.lower()).strip("-") or f"p{project.id}"
    return f"traccoon-cli-{slug}-u{user_id}"


def terminal_url(sess: CliSession) -> str:
    """ttyd's WebSocket inside the session container (reachable on the `traccoon-cli` net)."""
    return f"ws://{sess.container}:{TTYD_PORT}/ws"


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _deployer(path: str, body: dict, timeout: float = 120) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{DEPLOYER_URL}{path}", json=body,
                              headers={"X-Traccoon-Internal": INTERNAL_TOKEN})
    try:
        data = r.json()
    except ValueError:
        data = {"ok": False, "log": r.text[:500]}
    if r.status_code >= 400 and "ok" not in data:
        data["ok"] = False
    return data


async def get_session(db: AsyncSession, project_id: int, user_id: int,
                      create: bool = False) -> CliSession | None:
    sess = (await db.execute(select(CliSession).where(
        CliSession.project_id == project_id, CliSession.user_id == user_id))).scalar_one_or_none()
    if sess is None and create:
        project = await db.get(Project, project_id)
        sess = CliSession(project_id=project_id, user_id=user_id,
                          container=container_name(project, user_id), status="stopped")
        db.add(sess)
        await db.flush()
    return sess


async def refresh_status(db: AsyncSession, sessions: list[CliSession]) -> None:
    """Ask Docker what really runs; the stored status is only the last thing seen."""
    if not sessions:
        return
    try:
        states = await _deployer("/cli/status", {"names": [s.container for s in sessions]}, 30)
    except httpx.HTTPError:
        log.warning("CLI status: the deployer is not reachable")
        return
    for s in sessions:
        docker_state = states.get(s.container)
        if docker_state == "running":
            s.status = "running"
        elif s.status != "failed":
            s.status = "stopped"


async def start(db: AsyncSession, sess: CliSession) -> bool:
    """Start the container (idempotent). A fresh MCP token on every start."""
    from ..worker.secrets import resolve_provider_token

    project = await db.get(Project, sess.project_id)
    user = await db.get(User, sess.user_id)
    if project is None or user is None:
        return False
    token_name = project.default_token_name if project.default_provider == "claude_code" else ""
    oauth = await resolve_provider_token(db, user.id, "claude_code", token_name)
    if not oauth:
        sess.status = "failed"
        sess.error = "No Claude subscription token for this person (Account → tokens)."
        return False
    if not WORKSPACE_HOST_PATH or not CLI_DATA_HOST_PATH:
        sess.status = "failed"
        sess.error = "WORKSPACE_HOST_PATH / CLI_DATA_HOST_PATH are not configured."
        return False
    raw = secrets.token_urlsafe(32)
    sess.mcp_token_enc = encrypt_secret(raw)
    sess.mcp_token_hash = token_hash(raw)
    sess.container = container_name(project, user.id)
    pkey = project.key.lower()
    # The project checkout and the ticket worktrees, at the same paths as in the worker:
    # git records worktrees with absolute paths, and those have to resolve in here too.
    mounts = [
        {"host": f"{WORKSPACE_HOST_PATH}/{pkey}", "target": f"/workspace/{pkey}"},
        {"host": f"{WORKSPACE_HOST_PATH}/.traccoon-worktrees/{pkey}",
         "target": f"/workspace/.traccoon-worktrees/{pkey}"},
        {"host": f"{CLI_DATA_HOST_PATH}/{pkey}/u{user.id}", "target": "/cfg"},
    ]
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": oauth,
        "SESSION_WORKDIR": "/workspace",
        "TRACCOON_MCP_URL": CLI_MCP_URL,
        "TRACCOON_MCP_TOKEN": raw,
        "GIT_NAME": user.display_name or user.username,
        "GIT_EMAIL": user.email or f"{user.username}@traccoon.local",
    }
    # The deployer only sees a running container as "keep it", so a restart with a new token
    # has to take the old one down first.
    await _deployer("/cli/stop", {"name": sess.container}, 60)
    sess.status = "starting"
    await db.commit()
    try:
        res = await _deployer("/cli/start", {
            "name": sess.container, "mounts": mounts, "env": env,
            "labels": {"project": pkey, "user": str(user.id)},
        }, 180)
    except httpx.HTTPError as exc:
        res = {"ok": False, "log": f"deployer unreachable: {exc}"}
    sess.status = "running" if res.get("ok") else "failed"
    sess.error = "" if res.get("ok") else str(res.get("log") or "")[-2000:]
    await db.commit()
    if res.get("ok"):
        # ttyd needs a moment before the first attach or paste succeeds.
        await asyncio.sleep(3)
    return bool(res.get("ok"))


async def stop(db: AsyncSession, sess: CliSession) -> None:
    try:
        await _deployer("/cli/stop", {"name": sess.container}, 60)
    except httpx.HTTPError:
        log.warning("CLI stop %s: deployer unreachable", sess.container)
    sess.status = "stopped"
    sess.mcp_token_enc = ""
    sess.mcp_token_hash = ""
    await db.commit()


async def send(sess: CliSession, text: str = "", *, clear: bool = False,
               interrupt: bool = False) -> tuple[bool, str]:
    try:
        res = await _deployer("/cli/send", {"name": sess.container, "text": text,
                                            "clear": clear, "interrupt": interrupt}, 60)
    except httpx.HTTPError as exc:
        return False, str(exc)
    return bool(res.get("ok")), str(res.get("log") or "")


# ── Tickets ─────────────────────────────────────────────────────────────────

async def enqueue(db: AsyncSession, issue: Issue, task_id: str) -> CliDelivery:
    """Put a released ticket into the session of the person who released it.

    Called by the engine instead of `enqueue_task`; the caller commits. Delivery itself runs
    after the commit (`kick`), because it types into a container and must not hold the
    engine's transaction.
    """
    owner = issue.assigned_by_user_id or issue.reporter_id
    sess = await get_session(db, issue.project_id, owner, create=True)
    # A ticket that comes back (an answered question, a rejected result) replaces its old
    # waiting entry instead of queueing twice.
    for old in (await db.execute(select(CliDelivery).where(
            CliDelivery.issue_id == issue.id,
            CliDelivery.state.in_(("waiting", "delivered"))))).scalars().all():
        old.state = "cancelled"
        await get_redis().srem(CLI_TASKS, old.task_id)
    last = (await db.execute(select(func.max(CliDelivery.position)).where(
        CliDelivery.session_id == sess.id))).scalar() or 0
    row = CliDelivery(session_id=sess.id, issue_id=issue.id, task_id=task_id,
                      delivery=issue.cli_delivery or "queue", context=issue.cli_context or "keep",
                      position=last + 1, state="waiting")
    db.add(row)
    await db.flush()
    await get_redis().sadd(CLI_TASKS, task_id)
    return row


def kick(session_id: int) -> None:
    """Deliver in the background (after the caller's commit)."""
    asyncio.get_running_loop().create_task(_dispatch_safe(session_id, retry_failed=True))


async def _dispatch_safe(session_id: int, retry_failed: bool = False) -> None:
    try:
        await dispatch(session_id, retry_failed=retry_failed)
    except Exception:  # noqa: BLE001 - the tick tries again
        log.exception("CLI dispatch for session %s failed", session_id)


async def dispatch(session_id: int, retry_failed: bool = False) -> None:
    """Type the next due tickets into the session.

    `now` entries always go in. `queue` entries go in one at a time: only while no other
    queued entry of this session is delivered and unreported.

    A session whose start failed is only started again on a new ticket or by hand
    (`retry_failed`), not on every engine tick: a missing token does not fix itself.
    """
    lock = _locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        async with SessionLocal() as db:
            sess = await db.get(CliSession, session_id)
            if sess is None:
                return
            waiting = (await db.execute(select(CliDelivery).where(
                CliDelivery.session_id == session_id, CliDelivery.state == "waiting")
                .order_by(CliDelivery.position))).scalars().all()
            if not waiting:
                return
            busy = (await db.execute(select(func.count()).select_from(CliDelivery).where(
                CliDelivery.session_id == session_id, CliDelivery.state == "delivered",
                CliDelivery.delivery == "queue"))).scalar() or 0
            due = [d for d in waiting if d.delivery == "now"]
            queued = [d for d in waiting if d.delivery != "now"]
            if not busy and queued:
                due.append(queued[0])
            if not due:
                return
            await refresh_status(db, [sess])
            if sess.status == "failed" and not retry_failed:
                return
            if sess.status != "running" and not await start(db, sess):
                for d in due:
                    d.error = sess.error
                await db.commit()
                return
            for d in due:
                await _deliver(db, sess, d)


async def _deliver(db: AsyncSession, sess: CliSession, d: CliDelivery) -> None:
    issue = await db.get(Issue, d.issue_id)
    project = await db.get(Project, sess.project_id)
    if issue is None or project is None:
        d.state = "cancelled"
        await db.commit()
        return
    workdir = f"/workspace/{project.key.lower()}"
    if project.git_enabled:
        from ..worker.issue_git import prepare_issue_git
        ctx = await prepare_issue_git(db, issue, project, sess.user_id)
        if ctx is not None and ctx.worktree:
            workdir = ctx.worktree
    text = await _ticket_text(db, issue, workdir)
    ok, out = await send(sess, text, clear=d.context == "clear")
    if not ok:
        d.error = out[-2000:]
        await db.commit()
        log.warning("CLI delivery of %s into %s failed: %s", issue.key, sess.container, out)
        return
    d.state = "delivered"
    d.delivered_at = _now()
    d.error = ""
    from .comments import add_system_comment
    await add_system_comment(db, issue.id, "💻 Delivered into the Claude CLI session",
                             author_label="Workflow")
    await db.commit()
    await publish_event(project.id, {"type": "cli_queue", "session_id": sess.id})


async def _ticket_text(db: AsyncSession, issue: Issue, workdir: str) -> str:
    """What is typed into the session: enough to start, the rest is one tool call away."""
    comments = (await db.execute(select(Comment).where(
        Comment.issue_id == issue.id, Comment.author_id.is_not(None))
        .order_by(Comment.id.desc()).limit(5))).scalars().all()
    lines = [
        f"[Traccoon ticket {issue.key}] {issue.summary}",
        f"Working directory: {workdir}"
        + (f" (branch {issue.branch_name})" if issue.branch_name else ""),
        "",
        (issue.description or "(no description)").strip(),
    ]
    if comments:
        lines += ["", "Latest comments (newest first):"]
        lines += [f"- {c.author_label or 'comment'}: {c.body.strip()[:1500]}" for c in comments]
    lines += [
        "",
        f"Work in {workdir} and commit there. When finished or stuck, call the MCP tool "
        f"traccoon ticket_report with key \"{issue.key}\", status done|blocked|failed and a "
        "short summary.",
    ]
    return "\n".join(lines)


async def report(db: AsyncSession, sess: CliSession, key: str, status: str,
                 summary: str) -> str:
    """The session says a ticket is finished (or stuck). Answers the waiting workflow."""
    from ..worker import gitops

    if status not in REPORT_STATES:
        return f"status must be one of {', '.join(REPORT_STATES)}"
    issue = (await db.execute(select(Issue).where(
        Issue.key == key, Issue.project_id == sess.project_id))).scalar_one_or_none()
    if issue is None:
        return f"no ticket {key} in this project"
    d = (await db.execute(select(CliDelivery).where(
        CliDelivery.session_id == sess.id, CliDelivery.issue_id == issue.id,
        CliDelivery.state.in_(("delivered", "waiting")))
        .order_by(CliDelivery.id.desc()))).scalars().first()
    if d is None:
        return f"{key} is not waiting for a report from this session"
    project = await db.get(Project, sess.project_id)
    fingerprint = None
    if project is not None and project.git_enabled:
        # Whatever the session left uncommitted belongs to the ticket too.
        from ..worker.issue_git import prepare_issue_git
        ctx = await prepare_issue_git(db, issue, project, sess.user_id)
        if ctx is not None:
            note = await gitops.commit(ctx, f"{issue.key}: {issue.summary}")
            log.info("CLI report %s: %s", issue.key, note)
            fingerprint = await gitops.worktree_fingerprint(ctx.worktree)
    result = {
        "status": status, "success": status == "done", "output": summary, "summary": summary,
        "task_id": d.task_id, "worktree_fingerprint": fingerprint,
    }
    if status == "blocked":
        result["blocker"] = {"kind": "question"}
    r = get_redis()
    await r.set(f"{PREFIX}result:{d.task_id}", json.dumps(result), ex=RESULT_TTL)
    await r.publish(f"{PREFIX}results", d.task_id)
    await r.srem(CLI_TASKS, d.task_id)
    d.state = "reported"
    d.reported_at = _now()
    await db.commit()
    await publish_event(sess.project_id, {"type": "cli_queue", "session_id": sess.id})
    kick(sess.id)
    return f"{key} reported as {status}"


async def cancel(db: AsyncSession, issue: Issue) -> None:
    """The ticket was stopped: take it out of the session and answer the waiting workflow."""
    rows = (await db.execute(select(CliDelivery).where(
        CliDelivery.issue_id == issue.id,
        CliDelivery.state.in_(("waiting", "delivered"))))).scalars().all()
    r = get_redis()
    for d in rows:
        if d.state == "delivered":
            sess = await db.get(CliSession, d.session_id)
            if sess is not None:
                await send(sess, interrupt=True)
        d.state = "cancelled"
        await r.srem(CLI_TASKS, d.task_id)
        stopped = {"status": "failed", "success": False, "output": "Stopped by a person.",
                   "summary": "Stopped by a person.", "task_id": d.task_id}
        await r.set(f"{PREFIX}result:{d.task_id}", json.dumps(stopped), ex=RESULT_TTL)
        await r.publish(f"{PREFIX}results", d.task_id)


async def tick() -> None:
    """From the engine tick: deliver what is due (after a restart, a failed start, ...)."""
    async with SessionLocal() as db:
        ids = (await db.execute(select(CliDelivery.session_id).where(
            CliDelivery.state == "waiting").distinct())).scalars().all()
        # A task id that fell out of the alive set (Redis flushed) would make the engine
        # give the ticket up; put every open one back.
        open_ids = (await db.execute(select(CliDelivery.task_id).where(
            CliDelivery.state.in_(("waiting", "delivered"))))).scalars().all()
    if open_ids:
        await get_redis().sadd(CLI_TASKS, *open_ids)
    for sid in ids:
        await _dispatch_safe(sid)
