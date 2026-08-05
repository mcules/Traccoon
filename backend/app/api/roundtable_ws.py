"""Live-Transport des Büros — EIN Socket je Nutzer statt N Sockets je Projekt.

Der Socket ist `GET /api/ws?token=`; er trägt ausschließlich Büro-Ereignisse aus dem
einen Redis-Kanal `traccoon:roundtable` (`services.roundtable.CHANNEL`). Warum nicht
einfach die vorhandenen Projekt-Sockets mitbenutzen:

1. **Projektlose Läufe haben gar keinen Projektraum.** Job- und Assistentenläufe tragen
   `project_id = None`; über `traccoon:events:{pid}` sind sie schlicht nicht adressierbar.
   Die globale Seite wäre ausgerechnet für die Läufe blind, die es nur global gibt.
2. **Mit N Sockets läge die Autorisierung beim Client.** Der Server nähme entgegen, was
   man ihm sagt. Hier rechnet der Server die erlaubte Menge selbst aus, und das
   `subscribe` des Clients kann sie nur **verengen** — nie erweitern.
3. **Kosten.** Ein Nutzer in 40 Projekten öffnete sonst 40 Sockets und 40
   `build_access`-Runden allein beim Seitenaufbau.
4. **Getrennter Verkehr.** `traccoon:events:{pid}` trägt `pm_chat` und `issue_update`;
   Schritt-Ereignisse dort einzuspeisen würde `PmChat.tsx` und `ProjectView.tsx` mit
   Verkehr fluten, den sie nur wegwerfen können.

Büro-Ereignisse werden deshalb **nicht** auf `traccoon:events:{pid}` gespiegelt, und
`/api/projects/{id}/ws` bleibt inhaltlich unangetastet (nur die Auth-Härtung unten kommt
dort ebenfalls an).

**Protokoll**

    Client verbindet   → Server: {"type":"hello", …}
    Client:  {"type":"subscribe","scopes":[{"kind":"project","id":27}]}
             {"type":"subscribe","scopes":[{"kind":"global"}]}
    Server:  {"type":"roundtable_ev","ev":{…}}

**Wiederverbindungsprotokoll** (determinismuskritisch, deshalb hier festgehalten):

    verbinden → subscriben → eingehende Ereignisse PUFFERN
    → Snapshot über GET /api/roundtable/sessions/{kind}/{ref}/events holen
    → gepufferte Ereignisse mit seq <= seq_to verwerfen → live gehen

Der Client darf **nie** inkrementell mit `after_seq` pollen: `seq` stammt aus einer
`SERIAL`-Spalte, und die wird **vor** dem Commit vergeben. Zwei parallele Worker können
ihre Zeilen also in umgekehrter Reihenfolge sichtbar machen — ein Poller, der sich seinen
Höchststand merkt, überspränge die Zeile, die später mit kleinerer id landet. `after_seq`
ist ausschließlich die Lückenfüllung unmittelbar nach einer Wiederverbindung, wo der
Puffer die Lücke ohnehin deckt.

**Autorisierung.** Die erlaubte Projektmenge wird EINMAL beim Verbinden gerechnet — über
exakt dasselbe Muster wie `GET /projects` (`api/projects.py:74-91`: zwei Queries plus
`build_access_bulk`, keine Runde je Projekt), damit es nur eine Definition von „darf
sehen" gibt; ein Test sichert die Gleichheit ab. Sie hält 60 s, ein einzelner
Hintergrund-Sweeper frischt sie auf — **nie** der heiße Pfad. Der Filter je Ereignis
braucht überhaupt keine Datenbank: `project_id`/`owner_id` hängen an jedem Ereignis
(Welle A), und damit kann die Entscheidung auch nicht an einem veralteten Join
vorbeidriften.

**Auth.** Der Socket tut, was `deps.get_current_user` tut: Token dekodieren, Nutzer laden,
`status != active` ablehnen und Tokens ablehnen, die vor `password_changed_at` ausgestellt
wurden. Schließcodes: 4401 (Token kaputt/unbekannter Nutzer), 4403 (inaktiv/widerrufen).
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
from ..services.roundtable import CHANNEL, EVENT_VERSION
from .deps import build_access_bulk

log = logging.getLogger("roundtable.ws")
router = APIRouter()

# Wie lange die einmal gerechnete Projektmenge gilt. 60 s ist der Kompromiss: eine
# frisch entzogene Mitgliedschaft wirkt spürbar schnell, ohne dass jemand je Ereignis
# in die Datenbank fasst.
ACL_TTL_S = 60.0
# Takt des Sweepers. Kleiner als die TTL, damit eine abgelaufene ACL nicht bis zur
# doppelten TTL stehen bleibt.
ACL_SWEEP_S = 15.0
# Warteschlange je Verbindung. Ein langsamer Client darf den Brücken-Task nicht
# ausbremsen; läuft sie über, fällt die Verbindung (siehe `_Conn`).
QUEUE_MAX = 512

CLOSE_UNAUTHENTICATED = 4401   # Token unlesbar / Nutzer unbekannt
CLOSE_FORBIDDEN = 4403         # Konto inaktiv oder Token durch Passwortwechsel widerrufen
CLOSE_TOO_SLOW = 1013          # „try again later" — Client hängt hinterher
CLOSE_INTERNAL = 1011


# ── Verbindung ──────────────────────────────────────────────────────────────

# `eq=False`: eine Verbindung ist sie selbst, nicht ihr Inhalt — zwei Reiter desselben
# Nutzers mit derselben ACL sind zwei Verbindungen. Nur so liegen sie beide im Set.
@dataclass(eq=False)
class _Conn:
    """Eine offene Nutzer-Verbindung samt ihrer Sicht auf die Welt.

    `allowed` ist die vom SERVER gerechnete Menge (was der Nutzer sehen darf), `scope`
    die vom CLIENT gewünschte Verengung (was er gerade sehen will). Beide werden
    geschnitten, nie vereinigt — ein Abonnement auf ein fremdes Projekt liefert Stille.
    """

    ws: WebSocket
    user_id: int
    is_admin: bool
    allowed: set[int]                 # Projekt-IDs, die dieser Nutzer sehen darf
    acl_at: float                     # monotone Uhr — Alter von `allowed`
    scope: set[int] | None = None     # None = global; sonst die Verengung des Clients
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX))


def visible(ev: dict, c: _Conn) -> bool:
    """Darf diese Verbindung dieses Ereignis sehen? Ohne Datenbank, ohne Uhr.

    Jedes Ereignis trägt `project_id`/`owner_id` selbst (Welle A) — deshalb kostet der
    heiße Pfad nichts und kann nicht an einem veralteten Join vorbeidriften.
    """
    if c.is_admin:
        return True
    project_id = ev.get("project_id")
    if project_id is not None:
        return project_id in c.allowed
    # Projektlos (Job, Assistent): nur der Eigentümer des Laufs sieht ihn. Ein Ereignis
    # ohne beides ist niemandes Ereignis und wird nicht zugestellt.
    owner_id = ev.get("owner_id")
    return owner_id is not None and owner_id == c.user_id


def in_scope(ev: dict, c: _Conn) -> bool:
    """Die Verengung des Clients. `None` = alles, was er ohnehin sehen darf."""
    if c.scope is None:
        return True
    return ev.get("project_id") in c.scope


def parse_scopes(data: dict) -> set[int] | None:
    """`subscribe`-Nachricht → Verengung. Fehlerhaft Gemeintes wird eng, nie weit.

    Ohne `scopes`-Schlüssel gilt global (None). Steht irgendwo `{"kind":"global"}`, gilt
    global. Sonst zählen die lesbaren Projekt-IDs — bleibt keine übrig, ist das eine
    LEERE Menge und damit Stille, nicht etwa „alles": eine unverständliche Nachricht darf
    den Strom nicht aufreißen.
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
    """Alle offenen Nutzer-Sockets. Ein Raum, kein Raum je Projekt — die Trennung macht
    `visible`, nicht die Zustellung."""

    def __init__(self) -> None:
        self.conns: set[_Conn] = set()

    def add(self, conn: _Conn) -> None:
        self.conns.add(conn)

    def remove(self, conn: _Conn) -> None:
        self.conns.discard(conn)

    def send(self, conn: _Conn, message: dict) -> bool:
        """In die Warteschlange legen. Läuft sie über, fällt die Verbindung — der Client
        hat dann ohnehin eine Lücke und muss über den Snapshot wiederkommen (siehe
        Wiederverbindungsprotokoll im Modul-Docstring). Weiterschicken hieße, ihm einen
        lückenlosen Strom vorzuspielen, den er nicht bekommen hat."""
        try:
            conn.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.remove(conn)
            # Nicht awaiten: der Brücken-Task darf an einem hängenden Client nicht warten.
            asyncio.create_task(_close_quiet(conn.ws, CLOSE_TOO_SLOW))
            return False

    async def dispatch(self, ev: dict) -> None:
        """Ein Büro-Ereignis an alle, die es sehen dürfen UND sehen wollen."""
        message = {"type": "roundtable_ev", "ev": ev}
        for conn in list(self.conns):
            if visible(ev, conn) and in_scope(ev, conn):
                self.send(conn, message)

    async def refresh_stale(self, now: float | None = None) -> int:
        """Abgelaufene ACLs auffrischen — EIN Durchgang für alle Verbindungen.

        Je Nutzer wird höchstens einmal gerechnet (mehrere Reiter sind mehrere
        Verbindungen desselben Nutzers). Wer inzwischen deaktiviert wurde, verliert den
        Strom hier — sonst hinge ein gesperrtes Konto bis zum Reload weiter mit.
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
    """Schließen und dabei nichts kaputtmachen: ein schon toter Socket wirft beim
    Schließen, und das ist kein Ereignis, das jemanden interessiert."""
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001
        pass


async def _pump(conn: _Conn) -> None:
    """Der EINZIGE Schreiber auf einem Socket. Auch die Antworten des Protokolls laufen
    hier durch — zwei Tasks, die gleichzeitig auf denselben Socket senden, könnten ihre
    Rahmen verschränken."""
    try:
        while True:
            message = await conn.queue.get()
            await conn.ws.send_json(message)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        # Senden wirft = Verbindung ist weg. Fallenlassen und schließen; das weckt die
        # Empfangsschleife, die dann aufräumt.
        manager.remove(conn)
        await _close_quiet(conn.ws, CLOSE_INTERNAL)


# ── Autorisierung ───────────────────────────────────────────────────────────

async def compute_acl(db: AsyncSession, user: User) -> set[int]:
    """Die Projekte, die dieser Nutzer sehen darf — exakt die Menge aus `GET /projects`.

    Dasselbe Muster wie `api/projects.py:74-91`: alle Projekte und alle Mitgliedschaften
    des Nutzers in je EINER Query, danach `build_access_bulk` ohne weiteren DB-Zugriff
    (sonst liefe der parent_id-Baum in N+1). Der Admin braucht keinen Sonderzweig —
    `build_access_bulk` gibt ihm für jedes Projekt eine `Access`-Sicht, genau wie die
    Kurzschluss-Zeile in `list_projects`. Ein Test hält beide Mengen aneinander.
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
    """Wurde dieses Token durch einen Passwortwechsel entwertet? (wie `get_current_user`)"""
    if user.password_changed_at is None:
        return False
    return int(payload.get("iat", 0) or 0) < int(user.password_changed_at.timestamp())


async def authenticate(token: str, db: AsyncSession) -> tuple[User | None, int]:
    """(Nutzer, Schließcode). Genau die Prüfungen aus `deps.get_current_user` — ein
    Socket ist kein schwächerer Eingang als ein Request."""
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


# ── Der Socket ──────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def roundtable_ws(websocket: WebSocket, token: str = "") -> None:
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
    # Das `hello` geht noch von Hand raus — die Pumpe läuft erst danach, es gibt also
    # noch keinen zweiten Schreiber.
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
                # Gegen Zwischenboxen, die stille Verbindungen nach ein paar Minuten kappen.
                manager.send(conn, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.debug("Büro-Socket beendet", exc_info=True)
    finally:
        manager.remove(conn)
        pump.cancel()


# ── Brücke und Sweeper ──────────────────────────────────────────────────────

async def roundtable_bridge() -> None:
    """Abonniert `traccoon:roundtable` und verteilt ACL-gefiltert an die Nutzer-Sockets.

    Der ACL-Sweeper hängt hier mit dran, damit das Lifespan nur EINEN Task kennt und der
    Sweeper garantiert mit der Brücke lebt und stirbt. Der Redis-Zugriff wird erst im
    Rumpf importiert — am Modulkopf hätte er den Test-Ersatz festgenagelt und jeder Test,
    der dieses Modul auch nur importiert, liefe in eine echte Verbindung.
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
    """Frischt abgelaufene Projektmengen auf — der EINE Ort, an dem das passiert.
    Im heißen Pfad wäre es eine Query je Ereignis und je Verbindung."""
    while True:
        await asyncio.sleep(ACL_SWEEP_S)
        try:
            await manager.refresh_stale()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("Büro: ACL-Auffrischung fehlgeschlagen", exc_info=True)
