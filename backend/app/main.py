import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import models  # noqa: F401  (Metadata für create_all füllen)
from .api import (
    admin, agents, auth, config, cost, dashboard, files, hardware, invitations, issues, lifecycle,
    mail, me, notifications, ops, permissions, plugins, projects, repo, runs, secrets, skills,
    users, workflows, ws,
)
from .config import settings
from .db import Base, SessionLocal, engine
from .seed import seed
from .services.dispatcher import recover_on_start, run_dispatcher
from .services.scheduler import run_scheduler
from .services.workflow_engine import run_workflow_engine
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
                # Person-Zuweisung (TRA-20): Platzhalter-Konten ohne Login. create_all/ADD
                # COLUMN zieht keine Enum-Werte nach → ADD VALUE explizit (PG 12+ erlaubt das
                # in-Tx, solange der Wert nicht in derselben Tx genutzt wird).
                "ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'placeholder'",
                # Lern-Policy des Assistenten: Schwärzung/Rohtext/gelernte Aktion pro Item.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS redaction VARCHAR(20) DEFAULT 'redacted' NOT NULL",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS raw_body TEXT",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS action_hint TEXT DEFAULT '' NOT NULL",
                # Telegram-Freigabekarte für projektlose Assistent-Items.
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS assistant_task_id INTEGER "
                "REFERENCES assistant_tasks(id) ON DELETE CASCADE",
                # Tool-Gate des Assistenten: wartende Freigabe + Einmal-Grant je Item.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS pending_tool VARCHAR(150)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS pending_resource VARCHAR(500)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS grant_tool VARCHAR(150)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS grant_resource VARCHAR(500)",
                # Mail-Webhook als normaler WebhookSub (Modus assistant): Klassifizier-Agent.
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS classify_agent VARCHAR(100)",
                # Mail-Task-Prompt (Verarbeitungs-Wissen) je Webhook — portiert aus dem Vorläufer.
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS prompt_tmpl TEXT",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS auto_run BOOLEAN DEFAULT FALSE NOT NULL",
                # Workflow-Trigger: Webhook/Job starten eine Workflow-Instanz (Etappe 3).
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS workflow_definition_id INTEGER "
                "REFERENCES workflow_definitions(id) ON DELETE SET NULL",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS context_map JSON DEFAULT '{}'::json NOT NULL",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workflow_definition_id INTEGER "
                "REFERENCES workflow_definitions(id) ON DELETE SET NULL",
                # E-Mail optional (login-lose Konten): NOT NULL entfernen (UNIQUE bleibt).
                "ALTER TABLE users ALTER COLUMN email DROP NOT NULL",
                # Ticket-Öffnen-Modus je Nutzer (popup|page).
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ticket_open_mode VARCHAR(10) "
                "DEFAULT 'popup' NOT NULL",
                # Nutzerspezifische Block-Anordnung der Ticket-Seite.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ticket_layout JSON DEFAULT '{}'::json NOT NULL",
                # PM-Chat-Darstellung je Nutzer (TRA-21).
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pm_chat_style VARCHAR(10) "
                "DEFAULT 'bubbles' NOT NULL",
                # Sub-Projekte (TRA-8/22): Vererbungs-Schalter + optionaler Projektbezug am Ort.
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS inherit_members BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                "ALTER TABLE locations ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL",
                # Agentenläufe folgen dem Ticket ins Archiv (TRA-29).
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE NOT NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
                # Bestandsdaten nachziehen: Läufe bereits archivierter Tickets mitarchivieren.
                "UPDATE runs SET archived = TRUE, archived_at = COALESCE(issues.archived_at, now()) "
                "FROM issues WHERE runs.issue_id = issues.id AND issues.archived "
                "AND NOT runs.archived",
                # Zuständige je Beschaffungsschritt (TRA-26).
                "ALTER TABLE hardware_workflow_steps ADD COLUMN IF NOT EXISTS assignee "
                "JSON DEFAULT '{}'::json NOT NULL",
                # Ticket an Hardware-Exemplar hängen (TRA-25).
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS asset_id INTEGER "
                "REFERENCES hardware_assets(id) ON DELETE SET NULL",
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
        asyncio.create_task(run_workflow_engine()),
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
api.include_router(workflows.router)
api.include_router(ops.router)
api.include_router(mail.router)
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
    from .models.ops import JobRun
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
