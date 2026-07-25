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


@pytest_asyncio.fixture
async def db():
    """Frische In-Memory-DB pro Test (StaticPool = alle Sessions auf derselben Verbindung)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.__test_factory__ = session_factory
        yield session
    await engine.dispose()


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
