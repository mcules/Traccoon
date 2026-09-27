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
from ..models.cli import CliDelivery, CliSession, Release
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
# A running session with nothing delivered, waiting or attached for this long is stopped.
# It costs memory while it idles, and it starts again by itself with the next ticket.
IDLE_HOURS = float(os.getenv("CLI_IDLE_HOURS", "8"))

_locks: dict[int, asyncio.Lock] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


_DIR = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")


def extra_dirs(project: Project) -> list[str]:
    """The project's additional workspace folders (projects.cli_extra_dirs), checked."""
    names = re.split(r"[\s,]+", project.cli_extra_dirs or "")
    return [n for n in names if _DIR.match(n) and n != project.key.lower()]


def session_dirs(project: Project) -> list[str]:
    """Every folder of /workspace a session of this project sees."""
    return [project.key.lower(), *extra_dirs(project)]


async def build(sess: CliSession, directory: str, only: list[str] | None = None) -> tuple[bool, str]:
    """Build the programs of one of the session's folders on this host (deployer, steps from
    traccoon-build.json in that folder). Allowed for CLI sessions by the owner of the house."""
    async with SessionLocal() as db:
        project = await db.get(Project, sess.project_id)
    if project is None or directory not in session_dirs(project):
        return False, f"{directory} is not a folder of this session"
    try:
        res = await _deployer("/cli/build", {"repo": f"{WORKSPACE_HOST_PATH}/{directory}",
                                             "only": only or []}, 3700)
    except httpx.HTTPError as exc:
        return False, f"deployer unreachable: {exc}"
    return bool(res.get("ok")), str(res.get("log") or "")


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


async def start(db: AsyncSession, sess: CliSession, login: bool = False) -> bool:
    """Start the container (idempotent). A fresh MCP token on every start.

    Login: the person's own claude.ai login (/login in the terminal) wins over the
    subscription token from Traccoon once it exists; only that login shows the plan's limits.
    `login` starts without the token so that /login can be done at all. Without a token and
    without a login the session starts too, and waits for /login.
    """
    from ..worker.secrets import resolve_provider_token

    project = await db.get(Project, sess.project_id)
    user = await db.get(User, sess.user_id)
    if project is None or user is None:
        return False
    token_name = project.default_token_name if project.default_provider == "claude_code" else ""
    oauth = await resolve_provider_token(db, user.id, "claude_code", token_name)
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
        *[{"host": f"{WORKSPACE_HOST_PATH}/{d}", "target": f"/workspace/{d}"}
          for d in extra_dirs(project)],
        # One config directory per person, shared by all their sessions: one /login is
        # enough, and it survives every restart.
        {"host": f"{CLI_DATA_HOST_PATH}/u{user.id}", "target": "/cfg"},
    ]
    # Without ticket worktrees everything happens in the project checkout: start there, so
    # claude finds the project's CLAUDE.md and `--continue` picks up the right conversation.
    workdir = "/workspace" if (project.git_enabled and project.work_in_branches) \
        else f"/workspace/{pkey}"
    env = {
        "SESSION_WORKDIR": workdir,
        "TRACCOON_MCP_URL": CLI_MCP_URL,
        # The same token opens the session's own endpoints (build) for scripts.
        "TRACCOON_API_URL": CLI_MCP_URL.rsplit("/mcp/", 1)[0],
        "TRACCOON_MCP_TOKEN": raw,
        "GIT_NAME": user.display_name or user.username,
        "GIT_EMAIL": user.email or f"{user.username}@traccoon.local",
    }
    if oauth:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth
    if project.cli_ssh_key_enc:
        # Base64: the env file the deployer writes has one line per variable.
        import base64
        from ..core.security import decrypt_secret
        env["SSH_PRIVATE_KEY_B64"] = base64.b64encode(
            decrypt_secret(project.cli_ssh_key_enc).encode()).decode()
    # The deployer only sees a running container as "keep it", so a restart with a new token
    # has to take the old one down first.
    await _deployer("/cli/stop", {"name": sess.container}, 60)
    sess.status = "starting"
    await db.commit()
    try:
        res = await _deployer("/cli/start", {
            "name": sess.container, "mounts": mounts, "env": env,
            "labels": {"project": pkey, "user": str(user.id)},
            "token_env": "CLAUDE_CODE_OAUTH_TOKEN", "login": login,
            "credentials": f"{CLI_DATA_HOST_PATH}/u{user.id}/claude/.credentials.json",
        }, 180)
    except httpx.HTTPError as exc:
        res = {"ok": False, "log": f"deployer unreachable: {exc}"}
    sess.status = "running" if res.get("ok") else "failed"
    sess.error = "" if res.get("ok") else str(res.get("log") or "")[-2000:]
    if res.get("ok") and res.get("log") in ("login", "login-pending", "token"):
        sess.auth = res["log"]
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


async def run(sess: CliSession, command: str, workdir: str,
              timeout: int = 300) -> tuple[bool, str]:
    """Run a shell command inside the session container (not in the claude conversation)."""
    try:
        res = await _deployer("/cli/exec", {"name": sess.container, "cmd": command,
                                            "workdir": workdir, "timeout": timeout},
                              timeout + 30)
    except httpx.HTTPError as exc:
        return False, f"deployer unreachable: {exc}"
    return bool(res.get("ok")), str(res.get("log") or "")


async def screen(sess: CliSession, lines: int = 200) -> str:
    try:
        res = await _deployer("/cli/capture", {"name": sess.container, "lines": lines}, 30)
    except httpx.HTTPError:
        return ""
    return str(res.get("text") or "")


def login_url(text: str) -> str:
    """The newest login link on the screen, put back together.

    claude wraps a long link itself at the terminal's width (on a phone every 40 columns), so
    it stands on the screen as a block of lines without spaces. They are joined until the first
    line that is not part of it any more.
    """
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        pos = lines[i].find("https://claude.ai/oauth/authorize")
        if pos < 0:
            pos = lines[i].find("https://claude.com/cai/oauth/authorize")
        if pos < 0:
            continue
        url = lines[i][pos:].strip()
        for nxt in lines[i + 1:]:
            part = nxt.strip()
            if not part or " " in part:
                break
            url += part
        return url
    return ""


async def logs(sess: CliSession, tail: int = 300) -> str:
    try:
        res = await _deployer("/cli/logs", {"name": sess.container, "tail": tail}, 30)
    except httpx.HTTPError as exc:
        return f"deployer unreachable: {exc}"
    return str(res.get("log") or "")


async def image(build: bool = False, latest: bool = False) -> dict:
    """Version of the session image; with `build` (re)build it first (admins only)."""
    try:
        return await _deployer("/cli/image", {"build": build, "latest": latest},
                               1300 if build else 90)
    except httpx.HTTPError as exc:
        return {"ok": False, "version": "", "log": f"deployer unreachable: {exc}"}


async def stop_project(db: AsyncSession, project_id: int) -> None:
    """Take down every session of a project (it is being deleted)."""
    for sess in (await db.execute(select(CliSession).where(
            CliSession.project_id == project_id))).scalars().all():
        await stop(db, sess)


async def _stop_idle() -> None:
    cutoff = _now() - dt.timedelta(hours=IDLE_HOURS)
    async with SessionLocal() as db:
        running = (await db.execute(select(CliSession).where(
            CliSession.status == "running"))).scalars().all()
        for sess in running:
            busy = (await db.execute(select(func.count()).select_from(CliDelivery).where(
                CliDelivery.session_id == sess.id,
                CliDelivery.state.in_(("waiting", "delivered"))))).scalar() or 0
            last = max(t for t in (sess.last_attach_at, sess.updated_at) if t is not None)
            if last.tzinfo is None:
                last = last.replace(tzinfo=dt.timezone.utc)
            if not busy and last < cutoff:
                log.info("CLI session %s idle since %s, stopping", sess.container, last)
                await stop(db, sess)


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
    # The ticket ships with the open release. One that comes back after its release was
    # deployed (rework) belongs to the next one.
    current = await db.get(Release, issue.release_id) if issue.release_id else None
    if current is None or current.state != "open":
        project = await db.get(Project, issue.project_id)
        rel = await open_release(db, project)
        issue.release_id = rel.id if rel else None
    row = CliDelivery(session_id=sess.id, issue_id=issue.id, task_id=task_id,
                      delivery=issue.cli_delivery or "queue", context=issue.cli_context or "keep",
                      position=last + 1, state="waiting")
    db.add(row)
    await db.flush()
    await get_redis().sadd(CLI_TASKS, task_id)
    return row


# ── Releases ────────────────────────────────────────────────────────────────

# A ticket in these states is finished work as far as deploying goes: acceptance can come
# after the deploy (that is what one tests against).
FINISHED = ("done", "testing", "to_test")


async def open_release(db: AsyncSession, project: Project, *,
                       force_new: bool = False) -> Release | None:
    """The project's open release; a new one when there is none and the project opens them
    by itself (or it never had one, or `force_new`)."""
    rel = (await db.execute(select(Release).where(
        Release.project_id == project.id, Release.state == "open")
        .order_by(Release.id.desc()))).scalars().first()
    if rel is not None:
        return rel
    last = (await db.execute(select(func.max(Release.number)).where(
        Release.project_id == project.id))).scalar()
    if not (force_new or project.release_auto_new or last is None):
        return None
    number = (last or 0) + 1
    rel = Release(project_id=project.id, number=number, name=f"Release {number}", state="open")
    db.add(rel)
    await db.flush()
    return rel


async def release_tickets(db: AsyncSession, release_id: int) -> list[Issue]:
    return list((await db.execute(select(Issue).where(Issue.release_id == release_id)
                                  .order_by(Issue.number))).scalars().all())


def _status(issue: Issue) -> str:
    v = issue.agent_status
    return (v.value if hasattr(v, "value") else v) or "open"


async def deploy_release(db: AsyncSession, rel: Release, user_id: int,
                         force: bool = False) -> CliDelivery:
    """Hand the deploy of a release to the caller's session (behind what it works on).

    Unfinished tickets block it; with `force` they move on to the next release instead and
    are not part of this one. The next release is opened right away (if the project does
    that), so tickets released from now on already land there.
    """
    if rel.state not in ("open", "failed"):
        raise ValueError(f"{rel.name} is {rel.state}")
    tickets = await release_tickets(db, rel.id)
    unfinished = [i for i in tickets if _status(i) not in FINISHED]
    if unfinished and not force:
        raise ValueError("unfinished: " + ", ".join(i.key for i in unfinished))
    if not [i for i in tickets if _status(i) in FINISHED]:
        raise ValueError(f"{rel.name} has no finished ticket")
    project = await db.get(Project, rel.project_id)
    rel.state = "deploying"
    rel.deploy_started_at = _now()
    rel.deployed_by_user_id = user_id
    await db.flush()
    nxt = await open_release(db, project)
    for i in unfinished:
        i.release_id = nxt.id if nxt else None
    import uuid
    sess = await get_session(db, rel.project_id, user_id, create=True)
    last = (await db.execute(select(func.max(CliDelivery.position)).where(
        CliDelivery.session_id == sess.id))).scalar() or 0
    row = CliDelivery(session_id=sess.id, issue_id=None, release_id=rel.id,
                      task_id=f"release-{rel.id}-{uuid.uuid4().hex[:8]}",
                      delivery="queue", context="keep", position=last + 1, state="waiting")
    db.add(row)
    await db.flush()
    return row


async def release_report(db: AsyncSession, sess: CliSession, release_id: int, status: str,
                         summary: str) -> str:
    """The session says how the deploy went."""
    if status not in ("done", "failed"):
        return "status must be done or failed"
    rel = await db.get(Release, release_id)
    if rel is None or rel.project_id != sess.project_id:
        return f"no release {release_id} in this project"
    d = (await db.execute(select(CliDelivery).where(
        CliDelivery.session_id == sess.id, CliDelivery.release_id == rel.id,
        CliDelivery.state.in_(("delivered", "waiting"))))).scalars().first()
    if d is None:
        return f"{rel.name} is not waiting for a report from this session"
    d.state = "reported"
    d.reported_at = _now()
    rel.state = "deployed" if status == "done" else "failed"
    rel.summary = summary
    if status == "done":
        rel.deployed_at = _now()
    from .comments import add_system_comment
    text = (f"🚀 Deployed with {rel.name}" if status == "done"
            else f"⚠️ Deploy of {rel.name} failed: {summary[:500]}")
    for i in await release_tickets(db, rel.id):
        await add_system_comment(db, i.id, text, author_label="Release")
    await db.commit()
    await publish_event(sess.project_id, {"type": "cli_queue", "session_id": sess.id})
    kick(sess.id)
    return f"{rel.name} reported as {status}"


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
    if d.release_id is not None:
        return await _deliver_release(db, sess, d)
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
    before = ""
    if project.cli_before_ticket.strip():
        ok, before = await run(sess, project.cli_before_ticket, workdir)
        before = before.strip()[-3000:]
        from .comments import add_system_comment
        if not ok:
            # The ticket must not start on a stale state: it goes on hold with the reason,
            # and whoever fixes the cause releases it again.
            await add_system_comment(
                db, issue.id, f"⚠️ Check before the work failed, the ticket was not handed "
                f"to the session:\n```\n{before}\n```", author_label="Workflow")
            await db.commit()
            await report(db, sess, issue.key, "blocked",
                         "The check before the work failed (see comment).")
            return
        if before:
            await add_system_comment(db, issue.id, f"🔄 Check before the work:\n```\n{before}\n```",
                                     author_label="Workflow")
    text = await _ticket_text(db, issue, workdir)
    if before:
        text += "\n\nCheck before the work (already run):\n" + before[-1500:]
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


async def _deliver_release(db: AsyncSession, sess: CliSession, d: CliDelivery) -> None:
    rel = await db.get(Release, d.release_id)
    project = await db.get(Project, sess.project_id)
    if rel is None or project is None:
        d.state = "cancelled"
        await db.commit()
        return
    tickets = [i for i in await release_tickets(db, rel.id)]
    how = (f"Deploy with: {project.cli_deploy_command.strip()}"
           if project.cli_deploy_command.strip()
           else "Deploy as the project's instructions (CLAUDE.md) describe.")
    text = "\n".join([
        f"[Traccoon release {rel.id}] Deploy {rel.name} of {project.name}",
        "Tickets in this release:",
        *[f"- {i.key}: {i.summary}" for i in tickets],
        "",
        how,
        "First make sure the work of these tickets is committed. Deploy, check that it "
        "worked, then call the MCP tool traccoon release_report with release "
        f"{rel.id}, status done|failed and a short summary.",
    ])
    ok, out = await send(sess, text)
    if not ok:
        d.error = out[-2000:]
        await db.commit()
        return
    d.state = "delivered"
    d.delivered_at = _now()
    d.error = ""
    await db.commit()
    await publish_event(project.id, {"type": "cli_queue", "session_id": sess.id})


async def _field_lines(db: AsyncSession, issue: Issue) -> list[str]:
    """The ticket's own fields (not the built-in ones), readable: for a choice its label and,
    when the option has one, its description. That is how a person tells the session which
    part of a project a ticket is about (an area field with where its code lives)."""
    if not issue.artifact_id:
        return []
    from ..models.artifact import Artifact
    from . import artifact_fields as fields
    artifact = await db.get(Artifact, issue.artifact_id)
    if artifact is None:
        return []
    values = await fields.values_of(db, artifact.id)
    lines: list[str] = []
    for f in await fields.fields_of(db, artifact.type_id, artifact.project_id):
        if f.source or not values.get(f.key):
            continue
        if f.kind != "select":
            lines.append(f"- {f.label}: {', '.join(str(v) for v in values[f.key])}")
            continue
        options = {o.value: o for o in await fields.options_of(db, f.id, only_active=False)}
        for v in values[f.key]:
            o = options.get(str(v))
            lines.append(f"- {f.label}: {(o.label or o.value) if o else v}")
            if o is not None and o.description.strip():
                lines += [f"    {line}" for line in o.description.strip().splitlines()]
    return lines


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
    extra = await _field_lines(db, issue)
    if extra:
        lines += ["", "Fields of the ticket:", *extra]
    if comments:
        lines += ["", "Latest comments (newest first):"]
        lines += [f"- {c.author_label or 'comment'}: {c.body.strip()[:1500]}" for c in comments]
    where = (f"Work in {workdir} or the folders the fields above name, and commit in the "
             "repository you changed." if extra else f"Work in {workdir} and commit there.")
    lines += [
        "",
        f"{where} When finished or stuck, call the MCP tool "
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
    if IDLE_HOURS > 0:
        await _stop_idle()
