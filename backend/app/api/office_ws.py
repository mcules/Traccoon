"""Live transport of the office: ONE socket per user instead of N sockets per project.

The socket is `GET /api/ws?token=`; it carries exclusively office events out of the one
Redis channel `traccoon:office` (`services.office.CHANNEL`). Why not simply reuse the
existing project sockets:

1. **Project-less runs have no project room at all.** Job and assistant runs carry
   `project_id = None`; over `traccoon:events:{pid}` they are simply not addressable.
   The global page would be blind for precisely those runs that only exist globally.
2. **With N sockets the authorisation would lie with the client.** The server would accept
   what it is told. Here the server computes the permitted set itself, and the `subscribe`
   of the client can only **narrow** it, never widen it.
3. **Cost.** A user in 40 projects would otherwise open 40 sockets and 40 `build_access`
   rounds during page load alone.
4. **Separated traffic.** `traccoon:events:{pid}` carries `pm_chat` and `issue_update`;
   feeding step events in there would flood `PmChat.tsx` and `ProjectView.tsx` with
   traffic they can only throw away.

Office events are therefore **not** mirrored onto `traccoon:events:{pid}`, and
`/api/projects/{id}/ws` stays untouched in content (only the auth hardening below arrives
there as well).

**Protocol**

    client connects    → server: {"type":"hello", …}
    client:  {"type":"subscribe","scopes":[{"kind":"project","id":27}]}
             {"type":"subscribe","scopes":[{"kind":"global"}]}
    server:  {"type":"office_ev","ev":{…}}

**Reconnect protocol** (critical for determinism, therefore recorded here):

    connect → subscribe → BUFFER incoming events
    → fetch the snapshot over GET /api/office/sessions/{kind}/{ref}/events
    → discard buffered events with seq <= seq_to → go live

The client must **never** poll incrementally with `after_seq`: `seq` comes from a `SERIAL`
column, and that is assigned **before** the commit. Two parallel workers can therefore
make their rows visible in reverse order, and a poller that remembers its high water mark
would skip the row that lands later with a smaller id. `after_seq` is exclusively the gap
filling directly after a reconnect, where the buffer covers the gap anyway.
Puffer die Lücke ohnehin deckt.

**Authorisation.** The permitted project set is computed ONCE on connect, over exactly the
same pattern as `GET /projects` (`api/projects.py:74-91`: two queries plus
`build_access_bulk`, no round per project), so that there is only one definition of "may
see"; a test secures the equality. It holds for 60 s, and a single background sweeper
refreshes it, **never** the hot path. The filter per event needs no database at all:
`project_id`/`owner_id` hang off every event, so the decision cannot drift past a stale
join.

**Auth.** The socket does what `deps.get_current_user` does: decode the token, load the
user, reject `status != active` and reject tokens issued before `password_changed_at`.
Close codes: 4401 (token broken or unknown user), 4403 (inactive or revoked).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import decode_access_token
from ..db import SessionLocal
from ..models.enums import GlobalRole, UserStatus
from ..models.project import Project, ProjectMember
from ..models.user import User
from ..services.office import CHANNEL, EVENT_VERSION
from .deps import build_access_bulk

log = logging.getLogger("office.ws")
router = APIRouter()

# How long the once computed project set is valid. 60 s is the compromise: a freshly
# withdrawn membership takes effect noticeably fast without anyone reaching into the
# database per event.
ACL_TTL_S = 60.0
# Beat of the sweeper. Smaller than the TTL so that an expired ACL does not stand around
# until twice the TTL.
ACL_SWEEP_S = 15.0
# Queue per connection. A slow client must not slow the bridge task down; if it overflows,
# the connection falls (see `_Conn`).
QUEUE_MAX = 512

CLOSE_UNAUTHENTICATED = 4401   # Token unlesbar / Nutzer unbekannt
CLOSE_FORBIDDEN = 4403         # account inactive or token revoked by a password change
CLOSE_TOO_SLOW = 1013          # "try again later": the client is lagging behind
CLOSE_INTERNAL = 1011


# ── Verbindung ──────────────────────────────────────────────────────────────

# `eq=False`: a connection is itself, not its content. Two tabs of the same user with the
# same ACL are two connections, and only that way do both lie in the set.
@dataclass(eq=False)
class _Conn:
    """One open user connection together with its view of the world.

    `allowed` is the set computed by the SERVER (what the user may see), `scope` the
    narrowing wished for by the CLIENT (what they want to see right now). Both are
    intersected, never united: a subscription to a foreign project yields silence.
    """

    ws: WebSocket
    user_id: int
    is_admin: bool
    allowed: set[int]                 # project ids this user may see
    acl_at: float                     # monotonic clock, the age of `allowed`
    scope: set[int] | None = None     # None = global; otherwise the narrowing of the client
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))


def visible(ev: dict, c: _Conn) -> bool:
    """May this connection see this event? Without a database, without a clock.

    Every event carries `project_id`/`owner_id` itself, which is why the hot path costs
    nothing and cannot drift past a stale join.
    """
    if c.is_admin:
        return True
    project_id = ev.get("project_id")
    if project_id is not None:
        return project_id in c.allowed
    # Project-less (job, assistant): only the owner of the run sees it. An event without
    # both is nobody's event and is not delivered.
    owner_id = ev.get("owner_id")
    return owner_id is not None and owner_id == c.user_id


def in_scope(ev: dict, c: _Conn) -> bool:
    """The narrowing of the client. `None` = everything they may see anyway."""
    if c.scope is None:
        return True
    return ev.get("project_id") in c.scope


def parse_scopes(data: dict) -> set[int] | None:
    """A `subscribe` message turned into a narrowing. What is meant wrongly becomes narrow, never wide.

    Without a `scopes` key, global applies (None). If `{"kind":"global"}` stands anywhere,
    global applies. Otherwise the readable project ids count, and if none is left that is an
    EMPTY set and therefore silence, not "everything": an incomprehensible message must not
    tear the stream open.
    """
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        return None
    ids: set[int] = set()
    for entry in scopes:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == "global":
            return None
        if kind == "project":
            try:
                ids.add(int(entry["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return ids


# ── Fan-out ─────────────────────────────────────────────────────────────────

class UserConnectionManager:
    """All open user sockets. One room, not a room per project: the separation is done by
    `visible`, not by the delivery."""

    def __init__(self) -> None:
        self.conns: set[_Conn] = set()

    def add(self, conn: _Conn) -> None:
        self.conns.add(conn)

    def remove(self, conn: _Conn) -> None:
        self.conns.discard(conn)

    def send(self, conn: _Conn, message: dict) -> bool:
        """Put into the queue. If it overflows the connection falls: the client then has a
        gap anyway and has to come back over the snapshot (see the reconnect protocol in
        the module docstring). Sending on would fake a gapless stream for them that they
        did not get."""
        try:
            conn.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.remove(conn)
            # Not awaited: the bridge task must not wait on a hanging client.
            asyncio.create_task(_close_quiet(conn.ws, CLOSE_TOO_SLOW))
            return False

    async def dispatch(self, ev: dict) -> None:
        """One office event to everybody who may see it AND wants to see it."""
        message = {"type": "office_ev", "ev": ev}
        for conn in list(self.conns):
            if visible(ev, conn) and in_scope(ev, conn):
                self.send(conn, message)

    async def refresh_stale(self, now: float | None = None) -> int:
        """Refresh expired ACLs, ONE pass for all connections.

        Per user it is computed at most once (several tabs are several connections of the
        same user). Whoever has been deactivated meanwhile loses the stream here; otherwise
        a locked account would keep listening until the next reload.
        """
        now = time.monotonic() if now is None else now
        stale = [c for c in self.conns if (now - c.acl_at) > ACL_TTL_S]
        if not stale:
            return 0
        cache: dict[int, tuple[bool, set[int]] | None] = {}
        async with SessionLocal() as db:
            for conn in stale:
                if conn.user_id not in cache:
                    user = await db.get(User, conn.user_id)
                    if user is None or user.status != UserStatus.active:
                        cache[conn.user_id] = None
                    else:
                        cache[conn.user_id] = (
                            user.global_role == GlobalRole.admin, await compute_acl(db, user),
                        )
                entry = cache[conn.user_id]
                if entry is None:
                    self.remove(conn)
                    asyncio.create_task(_close_quiet(conn.ws, CLOSE_FORBIDDEN))
                    continue
                conn.is_admin, conn.allowed = entry
                conn.acl_at = now
        return len(stale)


manager = UserConnectionManager()


async def _close_quiet(ws: WebSocket, code: int) -> None:
    """Close without breaking anything: an already dead socket raises on close, and that is
    not an event anybody is interested in."""
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001
        pass


async def _pump(conn: _Conn) -> None:
    """The ONLY writer on a socket. The answers of the protocol run through here too,
    because two tasks sending on the same socket at once could interleave their frames."""
    try:
        while True:
            message = await conn.queue.get()
            await conn.ws.send_json(message)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        # Sending raises = the connection is gone. Drop it and close; that wakes the
        # receive loop, which then cleans up.
        manager.remove(conn)
        await _close_quiet(conn.ws, CLOSE_INTERNAL)


# ── Autorisierung ───────────────────────────────────────────────────────────

async def compute_acl(db: AsyncSession, user: User) -> set[int]:
    """The projects this user may see, exactly the set from `GET /projects`.

    The same pattern as `api/projects.py:74-91`: all projects and all memberships of the
    user in ONE query each, then `build_access_bulk` without further database access
    (otherwise the parent_id tree would run into N+1). The admin needs no special branch:
    `build_access_bulk` gives them an `Access` view for every project, exactly like the
    short circuit line in `list_projects`. A test holds both sets against each other.
    """
    all_projects = (await db.execute(select(Project).order_by(Project.id))).scalars().all()
    projects_by_id = {p.id: p for p in all_projects}
    memberships = (
        await db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id))
    ).scalars().all()
    members_by_project = {m.project_id: m for m in memberships}
    return {
        p.id for p in all_projects
        if build_access_bulk(p, user, members_by_project, projects_by_id) is not None
    }


def token_revoked(payload: dict, user: User) -> bool:
    """Was this token devalued by a password change? (like `get_current_user`)"""
    if user.password_changed_at is None:
        return False
    return int(payload.get("iat", 0) or 0) < int(user.password_changed_at.timestamp())


async def authenticate(token: str, db: AsyncSession) -> tuple[User | None, int]:
    """(User, close code). Exactly the checks from `deps.get_current_user`: a socket is not
    a weaker entrance than a request."""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return None, CLOSE_UNAUTHENTICATED
    user = await db.get(User, int(payload.get("sub", 0) or 0))
    if user is None:
        return None, CLOSE_UNAUTHENTICATED
    if user.status != UserStatus.active or token_revoked(payload, user):
        return None, CLOSE_FORBIDDEN
    return user, 0


# ── The socket ──────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def office_ws(websocket: WebSocket, token: str = "") -> None:
    async with SessionLocal() as db:
        user, code = await authenticate(token, db)
        if user is None:
            await websocket.close(code=code)
            return
        conn = _Conn(
            ws=websocket, user_id=user.id,
            is_admin=(user.global_role == GlobalRole.admin),
            allowed=await compute_acl(db, user), acl_at=time.monotonic(),
        )

    await websocket.accept()
    # The `hello` still goes out by hand: the pump only runs afterwards, so there is no
    # second writer yet.
    await websocket.send_json({
        "type": "hello", "v": EVENT_VERSION, "user_id": conn.user_id,
        "is_admin": conn.is_admin, "projects": sorted(conn.allowed),
        "acl_ttl_s": ACL_TTL_S,
    })
    manager.add(conn)
    pump = asyncio.create_task(_pump(conn))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") == "subscribe":
                conn.scope = parse_scopes(data)
                manager.send(conn, {
                    "type": "subscribed",
                    "scope": None if conn.scope is None else sorted(conn.scope),
                })
            elif data.get("type") == "ping":
                # Against middleboxes that cut silent connections after a few minutes.
                manager.send(conn, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("Büro-Socket beendet", exc_info=True)
    finally:
        manager.remove(conn)
        pump.cancel()


# ── Bridge and sweeper ──────────────────────────────────────────────────────

async def office_bridge() -> None:
    """Subscribes to `traccoon:office` and distributes ACL filtered to the user sockets.

    The ACL sweeper hangs off this so that the lifespan knows only ONE task and the sweeper
    is guaranteed to live and die with the bridge. The Redis access is only imported in the
    body: at the module head it would have nailed down the test replacement, and every test
    that merely imports this module would run into a real connection.
    """
    from ..core.redis import get_redis

    sweeper = asyncio.create_task(acl_sweeper())
    try:
        pubsub = get_redis().pubsub()
        await pubsub.subscribe(CHANNEL)
        log.info("büro-bridge aktiv (%s)", CHANNEL)
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                ev = json.loads(msg["data"])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(ev, dict):
                await manager.dispatch(ev)
    finally:
        sweeper.cancel()


async def acl_sweeper() -> None:
    """Refreshes expired project sets, the ONE place where that happens.
    In the hot path it would be one query per event and per connection."""
    while True:
        await asyncio.sleep(ACL_SWEEP_S)
        try:
            await manager.refresh_stale()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Büro: ACL-Auffrischung fehlgeschlagen", exc_info=True)
