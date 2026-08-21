"""WebSocket pro Projekt + Redis-Event-Bridge (Runner/Dispatcher → Clients)."""
from __future__ import annotations

import asyncio
import json
import logging

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.redis import PREFIX, get_redis
from ..core.security import decode_access_token
from ..db import SessionLocal
from ..models.enums import UserStatus
from ..models.project import Project
from ..models.user import User
from .deps import build_access

log = logging.getLogger("ws")
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[int, set[WebSocket]] = {}

    async def connect(self, project_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self.rooms.setdefault(project_id, set()).add(ws)

    def disconnect(self, project_id: int, ws: WebSocket) -> None:
        self.rooms.get(project_id, set()).discard(ws)

    async def broadcast(self, project_id: int, message: dict) -> None:
        dead = []
        for ws in list(self.rooms.get(project_id, set())):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)


manager = ConnectionManager()


class PersonsChannel:
    """Ein Kanal je Person statt je Projekt.

    The project rooms carry what concerns a project. Mail belongs to no project but to a
    person — and it should arrive no matter which page they are on (the counter in the bar is
    everywhere).
    """

    def __init__(self) -> None:
        self.offen: dict[int, set[WebSocket]] = {}

    async def join(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self.offen.setdefault(user_id, set()).add(ws)

    def separate(self, user_id: int, ws: WebSocket) -> None:
        self.offen.get(user_id, set()).discard(ws)

    def somebody_there(self, user_id: int) -> bool:
        return bool(self.offen.get(user_id))

    async def send(self, user_id: int, message: dict) -> None:
        dead = []
        for ws in list(self.offen.get(user_id, set())):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.separate(user_id, ws)


persons = PersonsChannel()


@router.websocket("/ws/me")
async def persons_ws(websocket: WebSocket, token: str = ""):
    """The personal channel: what concerns the person arrives here (new mail)."""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return
    async with SessionLocal() as db:
        user = await db.get(User, int(payload.get("sub", 0)))
        if user is None or user.status != UserStatus.active:
            await websocket.close(code=4403)
            return
        revoke = (user.password_changed_at is not None
                      and int(payload.get("iat", 0) or 0)
                      < int(user.password_changed_at.timestamp()))
        if revoke:
            await websocket.close(code=4403)
            return
        user_id = user.id

    await persons.join(user_id, websocket)
    try:
        while True:
            # The channel is a one-way street; receiving happens only to notice the disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        persons.separate(user_id, websocket)


@router.websocket("/projects/{project_id}/ws")
async def project_ws(websocket: WebSocket, project_id: int, token: str = ""):
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return
    async with SessionLocal() as db:
        user = await db.get(User, int(payload.get("sub", 0)))
        project = await db.get(Project, project_id)
        if user is None or project is None:
            await websocket.close(code=4404)
            return
        # Fix: the socket used to be the only entrance without these two checks. A
        # deactivated account and a token revoked by a password change still got in here,
        # while `deps.get_current_user` rejected them on every request. A token alone is not
        # access as long as the account behind it is locked or the token devalued.
        revoked = (user.password_changed_at is not None
                   and int(payload.get("iat", 0) or 0) < int(user.password_changed_at.timestamp()))
        if user.status != UserStatus.active or revoked:
            await websocket.close(code=4403)
            return
        try:
            access = await build_access(project, user, db)
        except Exception:  # noqa: BLE001
            await websocket.close(code=4404)
            return
        ai_assign = access.ai_assign
        user_id = user.id

    await manager.connect(project_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "chat" and (data.get("content") or "").strip():
                if not ai_assign:
                    await websocket.send_json({"type": "error", "data": {"message": "The AI right is required"}})
                    continue
                asyncio.create_task(_run_pm(project_id, user_id, data["content"].strip()))
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)


async def _run_pm(project_id: int, user_id: int, text: str) -> None:
    from ..services.pm_orchestrator import run_pm_chat
    async with SessionLocal() as db:
        try:
            await run_pm_chat(db, project_id, user_id, text)
        except Exception:  # noqa: BLE001
            log.exception("PM chat error")


@router.get("/projects/{project_id}/messages")
async def list_messages(project_id: int, token: str = "", limit: int = 100):
    from sqlalchemy import select as _select
    from ..models.chat import Message
    from ..core.security import decode_access_token
    try:
        payload = decode_access_token(token) if token else None
    except jwt.PyJWTError:
        payload = None
    async with SessionLocal() as db:
        if payload is None:
            return []
        user = await db.get(User, int(payload.get("sub", 0)))
        project = await db.get(Project, project_id)
        if user is None or project is None:
            return []
        try:
            await build_access(project, user, db)
        except Exception:  # noqa: BLE001
            return []
        rows = (await db.execute(_select(Message).where(Message.project_id == project_id)
                                 .order_by(Message.id.desc()).limit(limit))).scalars().all()
        rows = list(reversed(rows))
        return [{"id": m.id, "role": m.role, "author": m.author_label, "content": m.content,
                 "created_at": m.created_at} for m in rows]


async def event_bridge() -> None:
    """Subscribes to traccoon:events:* and broadcasts to the matching project rooms."""
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.psubscribe(f"{PREFIX}events:*")
    log.info("event bridge active")
    async for msg in pubsub.listen():
        if msg.get("type") != "pmessage":
            continue
        channel = msg["channel"]
        try:
            project_id = int(channel.rsplit(":", 1)[1])
            data = json.loads(msg["data"])
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        await manager.broadcast(project_id, data)
