"""Test-Fixtures: App gegen eine In-Memory-SQLite statt Postgres.

Warum SQLite trägt: alle Modelle nutzen `SAEnum(..., values_callable=pg_enum_values)`
mit lowercase-Values — unter SQLite werden daraus CHECK-Constraint-Strings. Die
Zugriffs-Helper in `deps.py`/`hardware.py` sind reine ORM-Queries ohne PG-Spezifika.
Das Schema kommt über `Base.metadata.create_all` (nicht Alembic), damit die Tests
unabhängig vom Migrationsstand laufen.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Pflicht-Settings, bevor app.config gelesen wird.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal")
os.environ.setdefault("DEV_CREATE_ALL", "false")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import api, app  # noqa: E402
from app.models import (  # noqa: E402
    HardwareAsset, HardwareModel, Location, Project, ProjectMember, User,
)
from app.models.enums import (  # noqa: E402
    GlobalRole, LocationType, ProjectRole, UserStatus,
)


# Module, die sich ihre eigene Session holen (Engine, Tick, Testumgebungen). Damit sie im
# Test dieselbe In-Memory-DB sehen wie der HTTP-Client, wird ihr `SessionLocal` umgehängt —
# sonst liefe die Prozess-Engine gegen eine leere zweite Datenbank.
_SESSION_MODULES = (
    "app.db", "app.services.workflow_engine", "app.services.dispatcher",
    "app.services.scheduler", "app.services.testenv", "app.worker.__main__",
)


@pytest_asyncio.fixture
async def db(monkeypatch):
    """Frische In-Memory-DB pro Test (StaticPool = alle Sessions auf derselben Verbindung)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    import importlib
    for name in _SESSION_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", session_factory)
    async with session_factory() as session:
        session.__test_factory__ = session_factory
        yield session
    # Wächter-Tasks der Engine beenden, bevor die DB verschwindet — sonst warten sie im
    # nächsten Test auf eine längst geschlossene Verbindung und der Lauf hängt.
    import app.services.workflow_engine as enginemod
    for task in list(enginemod._BACKGROUND):
        task.cancel()
    enginemod._BACKGROUND.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """DB mit dem ausgelieferten Standard-Satz (Ticket-Lebenszyklus, Abnahme, …)."""
    from app.services.workflow_seed import ensure_builtin_set
    return await ensure_builtin_set(db)


@pytest.fixture(autouse=True)
def kein_mcp(monkeypatch):
    """Kein Test spricht mit einem echten MCP-Dienst.

    Der Mail-Eingang ruft am Ende `imap-mcp` und verschiebt damit echte Post. Im Test lief
    das gegen den laufenden Dienst — die Mailkennung aus den Testdaten zeigt zwar auf keine
    existierende Nachricht, aber darauf darf sich nichts verlassen. Wer den Aufruf prüfen
    will, ersetzt `call_tool` selbst (siehe `imap_stub` in den Spam-Tests).
    """
    import app.services.mcp_client as mcpmod

    async def verboten(url, tool, arguments=None, **kw):
        raise AssertionError(
            f"Test wollte {tool!r} an {url!r} rufen — im Test bitte ersetzen (monkeypatch).")

    monkeypatch.setattr(mcpmod, "call_tool", verboten)
    import importlib
    for modname in ("app.services.spam_review",):
        mod = importlib.import_module(modname)
        if hasattr(mod, "call_tool"):
            monkeypatch.setattr(mod, "call_tool", verboten)


@pytest.fixture(autouse=True)
def redis_stub(monkeypatch):
    """Redis/Worker-Ersatz für alle Tests.

    Ohne das würden Prozess-Schritte (Ereignis senden, Agentenlauf einreihen) in einen
    echten Redis laufen und minutenlang in Timeouts hängen. `results` füllt der Test:
    `results["*"] = {...}` beantwortet jeden Lauf, `results["<task_id>"]` einen bestimmten.
    Eine LISTE wird der Reihe nach abgearbeitet, der letzte Eintrag bleibt stehen — damit
    lässt sich „erst Zwischenstand, dann fertig" abbilden, ohne dass ein Prozess in einer
    Fortsetzungs-Schleife bis zum Cap dreht.
    """
    import app.core.redis as redismod
    results: dict[str, object] = {}

    def _next(key: str):
        val = results.get(key, results.get("*"))
        if isinstance(val, list) and val:
            return val.pop(0) if len(val) > 1 else val[0]
        return val

    async def publish_event(*a, **k):
        return None

    async def enqueue_task(payload):
        return None

    async def wait_result(task_id, timeout=None, poll=0.4, gnadenfrist=300.0):
        return _next(task_id)

    async def lauf_lebt(task_id):
        return False

    async def peek_result(task_id):
        return _next(task_id)

    async def get_flag(name):
        return False

    async def get_user_flag(name, user_id=None):
        return False

    async def set_flag(name, value):
        return None

    async def publish_kill(key):
        return None

    stubs = {
        "publish_event": publish_event, "enqueue_task": enqueue_task,
        "wait_result": wait_result, "peek_result": peek_result, "get_flag": get_flag,
        "lauf_lebt": lauf_lebt,
        "get_user_flag": get_user_flag, "set_flag": set_flag, "publish_kill": publish_kill,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(redismod, name, fn, raising=False)
    # Module, die die Funktionen beim Import gebunden haben, brauchen denselben Ersatz.
    import importlib
    for modname in ("app.services.workflow_engine", "app.services.dispatcher",
                    "app.services.scheduler"):
        mod = importlib.import_module(modname)
        for name, fn in stubs.items():
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
    return results


@pytest_asyncio.fixture
async def client(db):
    """HTTP-Client gegen die API; `get_session` zeigt auf die Test-DB.
    Die Routen hängen an der unter /api gemounteten Sub-App `api` — deren
    dependency_overrides sind von denen der äußeren App getrennt.
    Lifespan läuft bewusst nicht (kein Dispatcher/Scheduler im Test)."""
    async def _override():
        yield db
    api.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as c:
        yield c
    api.dependency_overrides.clear()


# ── Helfer zum Anlegen von Testdaten ─────────────────────────────────────────

async def make_user(db, username: str, admin: bool = False) -> User:
    u = User(
        username=username, email=f"{username}@test.local", display_name=username.title(),
        password_hash=hash_password("pw"), status=UserStatus.active,
        global_role=GlobalRole.admin if admin else GlobalRole.user,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def make_project(db, key: str, name: str, parent_id: int | None = None,
                       inherit_members: bool = True) -> Project:
    p = Project(key=key, name=name, parent_id=parent_id, inherit_members=inherit_members,
                has_hardware=True)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def add_member(db, project: Project, user: User, role: ProjectRole) -> ProjectMember:
    m = ProjectMember(project_id=project.id, user_id=user.id, role=role, ai_assign=False)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def make_location(db, name: str, project: Project | None = None,
                        parent: Location | None = None) -> Location:
    loc = Location(
        name=name, type=LocationType.other,
        parent_id=parent.id if parent else None,
        project_id=project.id if project else None,
        full_path=f"{parent.full_path} / {name}" if parent else name,
    )
    db.add(loc)
    await db.commit()
    await db.refresh(loc)
    return loc


async def make_asset(db, model_name: str, project: Project | None = None,
                     location: Location | None = None) -> HardwareAsset:
    m = HardwareModel(name=model_name)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    a = HardwareAsset(
        model_id=m.id,
        project_id=project.id if project else None,
        location_id=location.id if location else None,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@pytest.fixture
def helpers():
    """Bündel der Anlege-Helfer, damit Tests nur eine Fixture importieren müssen."""
    return type("H", (), {
        "make_user": staticmethod(make_user), "auth": staticmethod(auth),
        "make_project": staticmethod(make_project), "add_member": staticmethod(add_member),
        "make_location": staticmethod(make_location), "make_asset": staticmethod(make_asset),
    })
