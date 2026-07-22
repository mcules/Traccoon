import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import models  # noqa: F401  (Metadata für create_all füllen)
from .api import (
    admin, agents, auth, config, cost, dashboard, files, hardware, invitations, issues, lifecycle,
    me, predecessor, notifications, permissions, plugins, projects, repo, runs, secrets, skills, users, ws,
)
from .config import settings
from .db import Base, SessionLocal, engine
from .seed import seed
from .services.dispatcher import recover_on_start, run_dispatcher
from .services.scheduler import run_scheduler
from .api.ws import event_bridge

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.dev_create_all:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Additive Spalten idempotent nachziehen (create_all legt sie nur auf FRISCHEN
            # Tabellen an, nicht auf bestehenden). Reihenfolge/Stil wie ADD COLUMN IF NOT EXISTS.
            for _ddl in (
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS cap_baseline_run_id INTEGER",
                # Eigene Base-URL je Provider-Token (OpenAI-kompatibler Endpoint, z. B. litellm).
                "ALTER TABLE provider_tokens ADD COLUMN IF NOT EXISTS base_url VARCHAR(500)",
                # Person-Zuweisung (ABC-20): Platzhalter-Konten ohne Login. create_all/ADD
                # COLUMN zieht keine Enum-Werte nach → ADD VALUE explizit (PG 12+ erlaubt das
                # in-Tx, solange der Wert nicht in derselben Tx genutzt wird).
                "ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'placeholder'",
            ):
                await conn.execute(text(_ddl))
    async with SessionLocal() as db:
        await seed(db)
    await recover_on_start()
    # Previews aus einem abgestürzten Vorleben abräumen (blockiert den Start nicht).
    from .services.testenv import cleanup_orphan_previews
    tasks = [
        asyncio.create_task(cleanup_orphan_previews()),
        asyncio.create_task(run_dispatcher()),
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(event_bridge()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Traccoon API", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = FastAPI(title="Traccoon API", version=VERSION)
api.include_router(auth.router)
api.include_router(me.router)
api.include_router(users.router)
api.include_router(projects.router)
api.include_router(invitations.router)
api.include_router(config.router)
api.include_router(issues.router)
api.include_router(lifecycle.router)
api.include_router(hardware.router)
api.include_router(predecessor.router)
api.include_router(secrets.router)
api.include_router(permissions.router)
api.include_router(notifications.router)
api.include_router(cost.router)
api.include_router(skills.router)
api.include_router(plugins.router)
api.include_router(agents.router)
api.include_router(runs.router)
api.include_router(dashboard.router)
api.include_router(files.router)
api.include_router(repo.router)
api.include_router(admin.router)
api.include_router(ws.router)


@api.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": VERSION, "auth_enabled": True}


# Alle API-Pfade unter /api
app.mount("/api", api)


@app.get("/health", tags=["health"])
async def root_health():
    return {"status": "ok", "version": VERSION}


@app.get("/digest/{run_id}")
async def digest(run_id: int):
    from fastapi.responses import HTMLResponse
    from .db import SessionLocal
    from .models.predecessor import JobRun
    async with SessionLocal() as db:
        jr = await db.get(JobRun, run_id)
    if jr is None:
        return HTMLResponse("<h1>404</h1>", status_code=404)
    import html as _html
    body = _html.escape(jr.output or "")
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>Digest #{run_id}</title>"
            "<style>body{max-width:800px;margin:2rem auto;padding:0 1rem;font-family:system-ui;"
            "line-height:1.6;color:#172b4d}pre{white-space:pre-wrap;word-wrap:break-word}</style></head>"
            f"<body><pre>{body}</pre></body></html>")
    return HTMLResponse(page)
