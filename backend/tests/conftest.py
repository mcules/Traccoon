"""Test fixtures: the app against an in-memory SQLite instead of Postgres.

Why SQLite carries: all models use `SAEnum(..., values_callable=pg_enum_values)` with lower
case values, and under SQLite those become CHECK constraint strings. The access helpers in
`deps.py`/`hardware.py` are pure ORM queries without PG specifics. The schema comes over
`Base.metadata.create_all` (not Alembic), so that the tests run independently of the
migration state.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Mandatory settings, before app.config is read.
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


# Modules that fetch a session of their own (engine, tick, test environments). So that they
# see the same in-memory database in the test as the HTTP client, their `SessionLocal` is
# rehung; otherwise the process engine would run against an empty second database.
_SESSION_MODULES = (
    "app.db", "app.services.workflow_engine", "app.services.dispatcher",
    "app.services.scheduler", "app.services.testenv", "app.worker.__main__",
)


@pytest_asyncio.fixture
async def db(monkeypatch):
    """A fresh in-memory database per test (StaticPool = all sessions on the same connection)."""
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
    # End the watcher tasks of the engine before the database disappears; otherwise they
    # wait in the next test on a long closed connection and the run hangs.
    import app.services.workflow_engine as enginemod
    for task in list(enginemod._BACKGROUND):
        task.cancel()
    enginemod._BACKGROUND.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """Database with the shipped default set (ticket lifecycle, acceptance, …)."""
    from app.services.workflow_seed import ensure_builtin_set
    return await ensure_builtin_set(db)


@pytest.fixture(autouse=True)
def no_mcp(monkeypatch):
    """No test talks to a real MCP service.

    The mail inbox calls `imap-mcp` at the end and thereby moves real post. In the test that
    ran against the running service; the mail id from the test data does point at no existing
    message, but nothing may rely on that. Whoever wants to check the call replaces
    `call_tool` themselves (see `imap_stub` in the spam tests).
    """
    import app.services.mcp_client as mcpmod

    async def forbidden(url, tool, arguments=None, **kw):
        raise AssertionError(
            f"Test wollte {tool!r} an {url!r} rufen — im Test bitte ersetzen (monkeypatch).")

    monkeypatch.setattr(mcpmod, "call_tool", forbidden)
    import importlib
    for modname in ("app.services.spam_review",):
        mod = importlib.import_module(modname)
        if hasattr(mod, "call_tool"):
            monkeypatch.setattr(mod, "call_tool", forbidden)


@pytest.fixture(autouse=True)
def redis_stub(monkeypatch):
    """Redis and worker replacement for all tests.

    Without it, process steps (sending an event, queueing an agent run) would run into a real
    Redis and hang in timeouts for minutes. `results` is filled by the test:
    `results["*"] = {...}` answers every run, `results["<task_id>"]` a particular one. A LIST
    is worked off in order and the last entry stays, which lets "first an interim state, then
    finished" be reproduced without a process turning in a continuation loop up to the cap.
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

    async def wait_result(task_id, timeout=None, poll=0.4, grace=300.0):
        return _next(task_id)

    async def run_alive(task_id):
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
        "lauf_lebt": run_alive,
        "get_user_flag": get_user_flag, "set_flag": set_flag, "publish_kill": publish_kill,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(redismod, name, fn, raising=False)
    # Modules that bound the functions at import time need the same replacement.
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
    """HTTP client against the API; `get_session` points at the test database. The routes hang
    off the sub-app `api` mounted under /api, whose dependency_overrides are separate from
    those of the outer app. The lifespan deliberately does not run (no dispatcher or scheduler
    in the test)."""
    async def _override():
        yield db
    api.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as c:
        yield c
    api.dependency_overrides.clear()


# ── Helpers for creating test data ───────────────────────────────────────────

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


async def make_webhook(db, owner: User, route: str, **fields) -> "WebhookSub":
    """Create a webhook and convert it right away if it still carries an old mode.

    Tests that need a trigger from outside thereby take the same path production takes: what
    used to be a mode of its own (ticket, report, assistant) is a flow today, and
    `webhook_modes.convert` is the place that produces that.
    """
    import uuid as _uuid

    from app.models.ops import WebhookSub
    from app.services.webhook_modes import convert

    sub = WebhookSub(public_id=str(_uuid.uuid4()), route=route,
                     owner_user_id=owner.id, **fields)
    db.add(sub)
    await db.commit()
    await convert(db)
    await db.refresh(sub)
    return sub


async def report(db, sub, payload: dict) -> list[int]:
    """Delivery of a webhook without HTTP — context and reference as in `api/ops`."""
    from app.api.ops import _context, _reference
    from app.models.workflow import WorkflowDefinition
    from app.services.events import emit
    from app.services.workflow_engine import start_workflow

    ctx, ref = _context(sub, payload), _reference(sub, payload)
    if sub.mode == "event":
        ids = await emit(db, str(sub.event_name), project_id=sub.project_id, payload=ctx,
                         actor_id=sub.owner_user_id, source_ref=ref)
        await db.commit()
        return ids
    definition = await db.get(WorkflowDefinition, sub.workflow_definition_id)
    inst = await start_workflow(db, definition, subject_kind=definition.subject_kind,
                                context=ctx, actor_id=sub.owner_user_id,
                                source=f"webhook:{sub.route}", source_ref=ref)
    await db.commit()
    return [inst.id]


@pytest.fixture
def redis_stub_real(monkeypatch):
    """A Redis that really keeps something — in memory, for the test only.

    The big `redis_stub` replaces the queue; here it is about the cache, and that needs a
    counterpart which remembers what it was given.
    """
    store: dict[str, str] = {}

    class Wrong:
        async def get(self, key):
            return store.get(key)

        async def set(self, key, value, ex=None):
            store[key] = value

        async def incr(self, key):
            store[key] = str(int(store.get(key, 0)) + 1)
            return int(store[key])

    monkeypatch.setattr("app.services.mailbox_cache.get_redis", lambda: Wrong())
    return store


@pytest.fixture
def helpers():
    """Bundle of the creation helpers, so that tests have to import only one fixture."""
    return type("H", (), {
        "make_user": staticmethod(make_user), "auth": staticmethod(auth),
        "make_project": staticmethod(make_project), "add_member": staticmethod(add_member),
        "make_location": staticmethod(make_location), "make_asset": staticmethod(make_asset),
        "make_webhook": staticmethod(make_webhook), "melde": staticmethod(report),
    })


@pytest.fixture(autouse=True)
def _i18n_fresh():
    """Translations come from a process wide cache (30 s), so that not every notification
    costs a query. Between two tests that is a bug: the text one test overwrites would keep
    applying in the next."""
    from app.services.i18n import discard
    discard()
    yield
    discard()
