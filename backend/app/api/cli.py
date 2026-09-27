"""Claude CLI sessions of a project in CLI mode: status, start/stop, queue, terminal, tools.

Everything here is about the caller's OWN session. Nobody attaches to, types into or reorders
the session of somebody else, not even an owner of the project: the session runs on that
person's subscription and sees what they typed.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging

import websockets
from fastapi import APIRouter, Depends, Header, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import scopes
from ..core.error import Error
from ..db import SessionLocal, get_session
from ..models.cli import CliDelivery, CliSession
from ..models.project import Project
from ..models.ticket import Comment, Issue
from ..models.user import User
from ..services import api_tokens, cli_sessions
from .deps import Access, build_access, require_ai_assign

log = logging.getLogger("traccoon.cli")
router = APIRouter(tags=["cli"])


def _require_cli(access: Access) -> None:
    if not access.project.cli_mode:
        raise Error(status.HTTP_409_CONFLICT, "err.cli_mode_off",
                    "This project does not work in CLI mode")


async def _queue(db: AsyncSession, sess: CliSession) -> list[dict]:
    rows = (await db.execute(
        select(CliDelivery, Issue).join(Issue, Issue.id == CliDelivery.issue_id)
        .where(CliDelivery.session_id == sess.id,
               CliDelivery.state.in_(("waiting", "delivered")))
        .order_by(CliDelivery.state.desc(), CliDelivery.position))).all()
    return [{"id": d.id, "issue_key": i.key, "summary": i.summary, "state": d.state,
             "delivery": d.delivery, "context": d.context, "error": d.error,
             "delivered_at": d.delivered_at} for d, i in rows]


async def _out(db: AsyncSession, sess: CliSession | None) -> dict:
    if sess is None:
        return {"status": "stopped", "error": "", "container": "", "queue": []}
    return {"status": sess.status, "error": sess.error, "container": sess.container,
            "queue": await _queue(db, sess)}


@router.get("/projects/{project_id}/cli/session")
async def my_session(access: Access = Depends(require_ai_assign),
                     db: AsyncSession = Depends(get_session)):
    _require_cli(access)
    sess = await cli_sessions.get_session(db, access.project.id, access.user.id)
    if sess is not None:
        await cli_sessions.refresh_status(db, [sess])
        await db.commit()
    return await _out(db, sess)


@router.post("/projects/{project_id}/cli/session/start")
async def start_session(access: Access = Depends(require_ai_assign),
                        db: AsyncSession = Depends(get_session)):
    _require_cli(access)
    sess = await cli_sessions.get_session(db, access.project.id, access.user.id, create=True)
    await cli_sessions.refresh_status(db, [sess])
    if sess.status != "running":
        await cli_sessions.start(db, sess)
    await db.commit()
    cli_sessions.kick(sess.id)
    return await _out(db, sess)


@router.post("/projects/{project_id}/cli/session/stop")
async def stop_session(access: Access = Depends(require_ai_assign),
                       db: AsyncSession = Depends(get_session)):
    _require_cli(access)
    sess = await cli_sessions.get_session(db, access.project.id, access.user.id)
    if sess is not None:
        await cli_sessions.stop(db, sess)
    return await _out(db, sess)


class MoveIn(BaseModel):
    direction: str  # up | down


@router.post("/projects/{project_id}/cli/queue/{delivery_id}/move")
async def move_entry(delivery_id: int, body: MoveIn, access: Access = Depends(require_ai_assign),
                     db: AsyncSession = Depends(get_session)):
    """Swap a waiting entry with its neighbour. Only waiting ones: a delivered ticket is
    already in the conversation, its place in the list means nothing any more."""
    _require_cli(access)
    sess = await cli_sessions.get_session(db, access.project.id, access.user.id)
    if sess is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.cli_no_session", "No session")
    waiting = (await db.execute(select(CliDelivery).where(
        CliDelivery.session_id == sess.id, CliDelivery.state == "waiting")
        .order_by(CliDelivery.position))).scalars().all()
    idx = next((n for n, d in enumerate(waiting) if d.id == delivery_id), None)
    if idx is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.cli_no_entry", "No such waiting entry")
    other = idx - 1 if body.direction == "up" else idx + 1
    if 0 <= other < len(waiting):
        a, b = waiting[idx], waiting[other]
        a.position, b.position = b.position, a.position
        await db.commit()
    return await _out(db, sess)


# ── The terminal ────────────────────────────────────────────────────────────

@router.websocket("/projects/{project_id}/cli/ws")
async def terminal(websocket: WebSocket, project_id: int, token: str = ""):
    """Pass the browser's terminal through to ttyd in the caller's own session container.

    ttyd speaks its own small protocol over the socket (a JSON hello, then frames with a one
    byte command prefix); the browser speaks it too, this only checks who is asking and
    copies frames both ways.
    """
    async with SessionLocal() as db:
        result = await api_tokens.authenticate(db, token)
        if result.user is None:
            await websocket.close(code=4401)
            return
        if not scopes.allowed(result.scopes, "GET", "/projects/{project_id}/cli/ws"):
            await websocket.close(code=4403)
            return
        project = await db.get(Project, project_id)
        if project is None or not project.cli_mode:
            await websocket.close(code=4404)
            return
        access = await build_access(project, result.user, db)
        if not access.ai_assign:
            await websocket.close(code=4403)
            return
        sess = await cli_sessions.get_session(db, project_id, result.user.id)
        if sess is None or sess.status != "running":
            await websocket.close(code=4409)
            return
        upstream_url = cli_sessions.terminal_url(sess)
        sess.last_attach_at = cli_sessions._now()
        await db.commit()

    await websocket.accept(subprotocol="tty")
    try:
        async with websockets.connect(upstream_url, subprotocols=["tty"],
                                      max_size=None, open_timeout=10) as upstream:
            async def down():
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)

            async def up():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send(msg["text"])

            tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except (OSError, websockets.WebSocketException, WebSocketDisconnect) as exc:
        log.info("CLI terminal %s closed: %s", upstream_url, exc)
    try:
        await websocket.close()
    except RuntimeError:
        pass


# ── The session's way back: ticket tools over MCP ─────────────────────────

LOG = "2024-11-05"

TOOLS = [
    {"name": "ticket_report",
     "description": "Report a ticket delivered into this session as finished (done), stuck "
                    "on a question (blocked) or failed. Traccoon moves the ticket on only "
                    "after this call; uncommitted changes in the ticket worktree are "
                    "committed.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "Ticket key, e.g. ABC-12"},
         "status": {"type": "string", "enum": list(cli_sessions.REPORT_STATES)},
         "summary": {"type": "string", "description": "What was done, or the question"}},
         "required": ["key", "status", "summary"]}},
    {"name": "ticket_get",
     "description": "Read a ticket of this project: summary, description, state, comments.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}},
                     "required": ["key"]}},
    {"name": "ticket_comment",
     "description": "Add a comment to a ticket of this project.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string"}, "text": {"type": "string"}}, "required": ["key", "text"]}},
    {"name": "ticket_create",
     "description": "Create a ticket in this project. It is not released: the person decides "
                    "when it comes into a session.",
     "inputSchema": {"type": "object", "properties": {
         "summary": {"type": "string"}, "description": {"type": "string"}},
         "required": ["summary"]}},
    {"name": "queue_list",
     "description": "The tickets waiting for or delivered into this session.",
     "inputSchema": {"type": "object", "properties": {}}},
]


async def _session_by_token(db: AsyncSession, header: str | None) -> CliSession:
    raw = (header or "").removeprefix("Bearer ").strip()
    if not raw:
        raise PermissionError("no token")
    wanted = cli_sessions.token_hash(raw)
    sess = (await db.execute(select(CliSession).where(
        CliSession.mcp_token_hash == wanted))).scalar_one_or_none()
    if sess is None or not hmac.compare_digest(sess.mcp_token_hash, wanted):
        raise PermissionError("unknown token")
    return sess


async def _issue(db: AsyncSession, sess: CliSession, key: str) -> Issue:
    issue = (await db.execute(select(Issue).where(
        Issue.key == str(key).strip(), Issue.project_id == sess.project_id))).scalar_one_or_none()
    if issue is None:
        raise LookupError(f"no ticket {key} in this project")
    return issue


async def _call(db: AsyncSession, sess: CliSession, name: str, args: dict):
    user = await db.get(User, sess.user_id)
    label = f"Claude CLI ({(user.display_name or user.username) if user else sess.user_id})"
    if name == "ticket_report":
        return await cli_sessions.report(db, sess, str(args.get("key") or ""),
                                         str(args.get("status") or ""),
                                         str(args.get("summary") or ""))
    if name == "ticket_get":
        issue = await _issue(db, sess, args.get("key"))
        comments = (await db.execute(select(Comment).where(Comment.issue_id == issue.id)
                                     .order_by(Comment.id))).scalars().all()
        return {"key": issue.key, "summary": issue.summary, "description": issue.description,
                "agent_status": issue.agent_status, "branch": issue.branch_name,
                "comments": [{"by": c.author_label, "text": c.body, "at": c.created_at}
                             for c in comments[-30:]]}
    if name == "ticket_comment":
        issue = await _issue(db, sess, args.get("key"))
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("empty comment")
        db.add(Comment(issue_id=issue.id, author_id=None, author_label=label, body=text,
                       kind="agent"))
        await db.commit()
        return f"commented on {issue.key}"
    if name == "ticket_create":
        from ..services.issues import new_issue
        issue = await new_issue(db, project_id=sess.project_id,
                                summary=str(args.get("summary") or "").strip() or "(untitled)",
                                description=str(args.get("description") or ""),
                                reporter_id=sess.user_id, source="cli")
        return {"key": issue.key}
    if name == "queue_list":
        return await _queue(db, sess)
    raise LookupError(f"unknown tool {name}")


@router.post("/mcp/project")
async def mcp_project(request: Request, authorization: str | None = Header(default=None),
                      db: AsyncSession = Depends(get_session)):
    """One MCP call from a session container (JSON-RPC 2.0 over HTTP)."""
    try:
        message = await request.json()
    except Exception:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not valid JSON"}}
    method = str(message.get("method") or "")
    id_ = message.get("id")
    params = message.get("params") or {}
    if method.startswith("notifications/"):
        return {}

    def answer(result=None, error=None):
        if error is not None:
            return {"jsonrpc": "2.0", "id": id_, "error": error}
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    try:
        sess = await _session_by_token(db, authorization)
    except PermissionError as exc:
        return answer(error={"code": -32001, "message": str(exc)})
    if method == "initialize":
        return answer({"protocolVersion": LOG,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "traccoon", "version": "1"},
                       "instructions": "Tickets of this Traccoon project. Every delivered "
                                       "ticket ends with ticket_report."})
    if method == "tools/list":
        return answer({"tools": TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        try:
            result = await _call(db, sess, name, params.get("arguments") or {})
        except (LookupError, ValueError) as exc:
            return answer({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001
            log.exception("CLI tool %s failed", name)
            return answer({"content": [{"type": "text", "text": f"error: {exc}"}],
                           "isError": True})
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False,
                                                                  default=str)
        return answer({"content": [{"type": "text", "text": text}]})
    if method == "ping":
        return answer({})
    return answer(error={"code": -32601, "message": f"unknown method {method}"})
