import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import models  # noqa: F401  (Metadata für create_all füllen)
from .api import (
    admin, agents, artifacts as artifacts_api, auth, config, cost, dashboard, deployments,
    destinations, files, hardware, invitations,
    issues, lifecycle, mail, me, notifications, ops, permissions, plugins, processes,
    projects, repo, office,
    runs, secrets, skills, testenv, users, workflows, ws,
)
from .config import settings
from .db import Base, SessionLocal, engine
from .seed import seed
from .services.dispatcher import recover_on_start, run_dispatcher
from .services.deploy_watch import run_deploy_watch
from .services.scheduler import run_scheduler
from .services.workflow_engine import run_workflow_engine
from .api.ws import event_bridge
from .api.office_ws import office_bridge, router as office_ws_router

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
                # Modellkatalog: Kontextfenster + ungefähre Ausgabegeschwindigkeit. Bei
                # lokalen Modellen ist genau das der Auswahlgrund — der Preis ist dort 0.
                "ALTER TABLE provider_models ADD COLUMN IF NOT EXISTS context_tokens INTEGER",
                "ALTER TABLE provider_models ADD COLUMN IF NOT EXISTS speed_tps DOUBLE PRECISION",
                # Eigene Base-URL je Provider-Token (OpenAI-kompatibler Endpoint, z. B. litellm).
                "ALTER TABLE provider_tokens ADD COLUMN IF NOT EXISTS base_url VARCHAR(500)",
                # Person-Zuweisung (ABC-20): Platzhalter-Konten ohne Login. create_all/ADD
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
                # PM-Chat-Darstellung je Nutzer (ABC-21).
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pm_chat_style VARCHAR(10) "
                "DEFAULT 'bubbles' NOT NULL",
                # Sub-Projekte (ABC-8/22): Vererbungs-Schalter + optionaler Projektbezug am Ort.
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS inherit_members BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                "ALTER TABLE locations ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL",
                # Agentenläufe folgen dem Ticket ins Archiv (ABC-29).
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE NOT NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
                # Bestandsdaten nachziehen: Läufe bereits archivierter Tickets mitarchivieren.
                "UPDATE runs SET archived = TRUE, archived_at = COALESCE(issues.archived_at, now()) "
                "FROM issues WHERE runs.issue_id = issues.id AND issues.archived "
                "AND NOT runs.archived",
                # Zuständige je Beschaffungsschritt (ABC-26).
                "ALTER TABLE hardware_workflow_steps ADD COLUMN IF NOT EXISTS assignee "
                "JSON DEFAULT '{}'::json NOT NULL",
                # Testumgebungs-Lebenszyklus je Projekt (ABC-18).
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_enabled BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_compose_file "
                "VARCHAR(255) DEFAULT 'compose.preview.yml' NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_dockerfile "
                "VARCHAR(255) DEFAULT 'Dockerfile' NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_url_template "
                "VARCHAR(255) DEFAULT 'http://{host}:{port}' NOT NULL",
                # „Testen"-Spalte für Bestandsprojekte anlegen und vor „Fertig" einsortieren.
                # Beide Statements sind idempotent (NOT EXISTS bzw. nur wenn done davor liegt).
                "INSERT INTO workflow_statuses (project_id, name, category, \"order\") "
                "SELECT p.id, 'Testen', 'in_progress', "
                "COALESCE((SELECT MIN(s.\"order\") FROM workflow_statuses s "
                "          WHERE s.project_id = p.id AND s.category = 'done'), 99) "
                "FROM projects p "
                "WHERE NOT EXISTS (SELECT 1 FROM workflow_statuses s "
                "                  WHERE s.project_id = p.id AND s.name = 'Testen')",
                "UPDATE workflow_statuses d SET \"order\" = t.\"order\" + 1 "
                "FROM workflow_statuses t "
                "WHERE t.project_id = d.project_id AND t.name = 'Testen' "
                "  AND d.category = 'done' AND d.\"order\" <= t.\"order\"",
                "INSERT INTO board_columns (board_id, status_id, \"order\") "
                "SELECT b.id, s.id, s.\"order\" FROM workflow_statuses s "
                "JOIN boards b ON b.project_id = s.project_id "
                "WHERE s.name = 'Testen' AND NOT EXISTS ("
                "  SELECT 1 FROM board_columns c WHERE c.board_id = b.id AND c.status_id = s.id)",
                # Spaltenreihenfolge am Status ausrichten — sonst kollidiert die neue
                # „Testen"-Spalte mit dem alten Platz von „Fertig".
                "UPDATE board_columns c SET \"order\" = s.\"order\" FROM workflow_statuses s "
                "WHERE s.id = c.status_id AND c.\"order\" <> s.\"order\"",
                # Ticket an Hardware-Exemplar hängen (ABC-25).
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS asset_id INTEGER "
                "REFERENCES hardware_assets(id) ON DELETE SET NULL",
                # Prozess-Sätze: Slot/Archiv an den Definitionen, Satz-Referenz an
                # Projekt/Nutzer, Routing-Stempel am Schritt, Instanz am Ticket.
                "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS set_id INTEGER "
                "REFERENCES workflow_sets(id) ON DELETE CASCADE",
                "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS slot VARCHAR(40)",
                "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_def_set_slot ON workflow_definitions "
                "(set_id, slot) WHERE archived_at IS NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_def_project_slot ON workflow_definitions "
                "(project_id, slot) WHERE archived_at IS NULL",
                "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS parent_instance_id INTEGER "
                "REFERENCES workflow_instances(id) ON DELETE SET NULL",
                "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS parent_node_id VARCHAR(80)",
                "ALTER TABLE workflow_step_runs ADD COLUMN IF NOT EXISTS routed_at TIMESTAMPTZ",
                "UPDATE workflow_step_runs SET routed_at = completed_at "
                "WHERE completed_at IS NOT NULL AND routed_at IS NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS workflow_set_id INTEGER "
                "REFERENCES workflow_sets(id) ON DELETE SET NULL",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS workflow_set_id INTEGER "
                "REFERENCES workflow_sets(id) ON DELETE SET NULL",
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS workflow_instance_id INTEGER",
                "CREATE INDEX IF NOT EXISTS ix_issues_workflow_instance_id ON issues "
                "(workflow_instance_id)",
                # Bestehende Beschaffungs-Definitionen dem Slot zuordnen, damit sie als
                # Projekt-Anpassung erkannt werden (statt neben dem Satz zu stehen).
                "UPDATE workflow_definitions SET slot = 'hardware_procurement' "
                "WHERE key = 'hardware-beschaffung' AND slot IS NULL AND project_id IS NOT NULL",
                # Webhook meldet ein Ereignis statt einen festen Ablauf zu starten.
                # Hardware bekommt eine gemeinsame Artefakt-Identität; Prozesse binden
                # allgemein an ein Artefakt statt an ein Exemplar.
                "ALTER TABLE hardware_assets ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE CASCADE",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_hardware_asset_artifact ON hardware_assets "
                "(artifact_id) WHERE artifact_id IS NOT NULL",
                "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE SET NULL",
                "CREATE INDEX IF NOT EXISTS ix_workflow_instances_artifact ON workflow_instances "
                "(artifact_id)",
                # Tickets bekommen dieselbe gemeinsame Artefakt-Identität wie die Hardware.
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE SET NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_issue_artifact ON issues "
                "(artifact_id) WHERE artifact_id IS NOT NULL",
                # Die beiden JSON-Platzhalter sind durch das echte Feld-Modell abgelöst
                # (`artifact_fields`/`artifact_values`). Sie waren nie befüllt, stehen aber
                # als NOT NULL ohne Vorgabe in der Tabelle — ohne DROP schlüge jedes INSERT fehl.
                "ALTER TABLE artifact_types DROP COLUMN IF EXISTS fields",
                "ALTER TABLE artifacts DROP COLUMN IF EXISTS data",
                # Felder tragen ihre Herkunft (echte Spalte) und sind ggf. eingebaut;
                # Werte tragen Kategorie und „wartet" — beides wanderte aus dem
                # früheren Zustands-Modell hierher.
                "ALTER TABLE artifact_fields ADD COLUMN IF NOT EXISTS source VARCHAR(40) "
                "DEFAULT '' NOT NULL",
                "ALTER TABLE artifact_fields ADD COLUMN IF NOT EXISTS options_source "
                "VARCHAR(30) DEFAULT '' NOT NULL",
                "ALTER TABLE artifact_fields ADD COLUMN IF NOT EXISTS builtin BOOLEAN "
                "DEFAULT FALSE NOT NULL",
                "ALTER TABLE artifact_field_options ADD COLUMN IF NOT EXISTS category "
                "VARCHAR(20) DEFAULT '' NOT NULL",
                "ALTER TABLE artifact_field_options ADD COLUMN IF NOT EXISTS waiting BOOLEAN "
                "DEFAULT FALSE NOT NULL",
                # Ein Ablauf darf an eine Vorgangsart gebunden sein (Bug ≠ Aufgabe).
                # Der Unique-Index wird dafür neu gezogen: COALESCE, weil NULLs sonst als
                # verschieden gelten und beliebig viele allgemeine Kopien entstünden.
                "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS issue_type_id "
                "INTEGER REFERENCES issue_types(id) ON DELETE CASCADE",
                "CREATE INDEX IF NOT EXISTS ix_workflow_definitions_issue_type "
                "ON workflow_definitions (issue_type_id)",
                "DROP INDEX IF EXISTS uq_workflow_def_project_slot",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_def_project_slot "
                "ON workflow_definitions (project_id, slot, COALESCE(issue_type_id, 0)) "
                "WHERE archived_at IS NULL",
                # Ein Projekt darf seine Artefakte um eigene Felder erweitern.
                "ALTER TABLE artifact_fields ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE CASCADE",
                "CREATE INDEX IF NOT EXISTS ix_artifact_fields_project "
                "ON artifact_fields (project_id)",
                "ALTER TABLE artifact_fields DROP CONSTRAINT IF EXISTS uq_artifact_field",
                "DROP INDEX IF EXISTS uq_artifact_field",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_field "
                "ON artifact_fields (type_id, COALESCE(project_id, 0), key)",
                # Die Ordnungsebene „Artefakt-Typ" ist wieder weg: Ticket und Hardware sind
                # beide einfach Artefakte, ihre Bedeutung kommt aus den Feldern.
                "ALTER TABLE artifact_types DROP COLUMN IF EXISTS group_id",
                "DROP TABLE IF EXISTS artifact_groups",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS event_name VARCHAR(120)",
                # Assistent meldet sich nur noch, wenn es nötig ist.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS notified BOOLEAN "
                "DEFAULT FALSE NOT NULL",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS assistant_notify VARCHAR(10) "
                "DEFAULT 'needed' NOT NULL",
                # Ziele (externe Gegenstellen) + Job-Art „http".
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_destination_global ON destinations "
                "(name) WHERE user_id IS NULL AND project_id IS NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_destination_user ON destinations "
                "(user_id, name) WHERE user_id IS NOT NULL AND project_id IS NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_destination_project ON destinations "
                "(project_id, name) WHERE project_id IS NOT NULL",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS destination_id INTEGER "
                "REFERENCES destinations(id) ON DELETE SET NULL",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS http_request JSON "
                "DEFAULT '{}'::json NOT NULL",
                "ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'wait_event'",
                "ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'subflow'",
                # Gedächtnis im Vault (ABC-30): Ordner am Nutzer, Lern-Schalter am Agenten.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS vault_memory_path VARCHAR(500) "
                "DEFAULT '' NOT NULL",
                "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS learns BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                # Antwortgrenze je Ziel (ABC-31): Gegenstellen, die ihre Lage bewusst in
                # einem Abruf liefern, brauchen mehr als die pauschalen 4000 Zeichen.
                "ALTER TABLE destinations ADD COLUMN IF NOT EXISTS max_response_chars "
                "INTEGER DEFAULT 4000 NOT NULL",
                # Büro (office): Projekt/Owner wandern an den Lauf, damit die Live-Brücke
                # jedes Ereignis ohne DB-Rückfrage autorisieren kann — und damit projektlose
                # Läufe (Assistent, Job) überhaupt eine Zugehörigkeit haben.
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner_id INTEGER "
                "REFERENCES users(id) ON DELETE SET NULL",
                # Verbund Eltern→Kind: beim `delegate`-Werkzeugstart ist die Kind-Lauf-ID noch
                # unbekannt, die Werkzeug-ID schon. Das Kind bringt sie mit.
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_tool_use_id VARCHAR(64)",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS spawn_depth SMALLINT DEFAULT 0 NOT NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS blocker_kind VARCHAR(24)",
                # Bestandsläufe bekommen ihr Projekt aus dem Ticket — ohne das startet die
                # Ansicht leer, weil kein Altlauf autorisierbar wäre.
                "UPDATE runs SET project_id = i.project_id FROM issues i "
                "WHERE runs.issue_id = i.id AND runs.project_id IS NULL",
                "CREATE INDEX IF NOT EXISTS ix_runs_project_started ON runs (project_id, started_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_runs_owner_started ON runs (owner_id, started_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_runs_parent_run_id ON runs (parent_run_id)",
                "CREATE INDEX IF NOT EXISTS ix_runs_issue_started ON runs (issue_id, started_at)",
                # Personalakte (`/office/agents`): jede ihrer fünf Abfragen gruppiert nach
                # `runs.agent` innerhalb eines Zeitfensters — die Werkzeugtabelle joint dafür
                # sogar `run_steps` gegen `runs`. Ohne diesen Index ist das ein Seq-Scan über
                # inzwischen 13 000 Laufzeilen, und zwar bei jedem Öffnen des Reiters.
                "CREATE INDEX IF NOT EXISTS ix_runs_agent_started ON runs (agent, started_at DESC)",
                # Der Schritt trägt sein Ereignis selbst: Art, Werkzeug-ID, Ziel, Erfolg, Dauer
                # und die Tokens des einzelnen Modellzugs. `ok` ist dreiwertig (NULL=unbekannt),
                # `provider`/`model` halten fest, WER geantwortet hat — bei Fallback ist das
                # nicht der Provider am Lauf.
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS kind VARCHAR(24) DEFAULT '' NOT NULL",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS tool_use_id VARCHAR(64)",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS target VARCHAR(500)",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS ok BOOLEAN",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS in_tokens INTEGER DEFAULT 0 NOT NULL",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS out_tokens INTEGER DEFAULT 0 NOT NULL",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER DEFAULT 0 NOT NULL",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS provider VARCHAR(50)",
                "ALTER TABLE run_steps ADD COLUMN IF NOT EXISTS model VARCHAR(150)",
                # Tragend, nicht kosmetisch: die Ansicht liest immer „ein Lauf, in
                # Ankunftsreihenfolge". Ohne diesen Index sortiert Postgres dafür bis zu
                # 20 000 Zeilen je Abruf. Läuft in engine.begin(), also ohne CONCURRENTLY —
                # bei bereits großer Tabelle vorher von Hand anlegen, dann ist das hier ein No-op.
                "CREATE INDEX IF NOT EXISTS ix_run_steps_run_id_id ON run_steps (run_id, id)",
                # Dreiwertig: NULL=Altzeile, False=kein Katalogeintrag (die 0,00 ist bloß eine
                # Lücke), True=bepreist. Ohne das liest sich jede Katalog-Lücke als „gratis".
                "ALTER TABLE cost_entries ADD COLUMN IF NOT EXISTS priced BOOLEAN",
                # Deployments (`api/deployments.py`): 186 Zeilen, die bisher niemand lesen
                # konnte. `source` beantwortet die Frage, die `requested_by`/`chat_id` nie
                # beantwortet haben (bei 0 von 186 Zeilen gefüllt) — bewusst OHNE Backfill,
                # die Herkunft der Bestandszeilen bleibt leer statt geraten.
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS source VARCHAR(20) "
                "DEFAULT '' NOT NULL",
                # Merkposten des Bühnen-Watchers (nächste Welle): welcher Status wurde schon
                # gemeldet. Spalte statt Prozessgedächtnis = neustartfest und doppelfrei.
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS announced_status VARCHAR(20) "
                "DEFAULT '' NOT NULL",
                "CREATE INDEX IF NOT EXISTS ix_deployments_project_created ON deployments "
                "(project_id, created_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_deployments_issue ON deployments (issue_id)",
                # Der Partialindex hat mit der neuen Oberfläche nichts zu tun und gehört
                # trotzdem hierher: der `deployer`-Sidecar sucht alle 3 Sekunden die nächste
                # offene Zeile und macht daraus heute einen Seq-Scan über die ganze Tabelle.
                # Derselbe Index bedient den kommenden Bühnen-Watcher.
                "CREATE INDEX IF NOT EXISTS ix_deployments_open ON deployments (id) "
                "WHERE status IN ('pending','pending-check','building')",
            ):
                await conn.execute(text(_ddl))
    async with SessionLocal() as db:
        await seed(db)
        # Ausgelieferte Abläufe (Ticket-Lebenszyklus, Abnahme, Beschaffung, Eingang) als
        # globalen Standard-Satz nachziehen — idempotent, veröffentlicht nur bei Änderung.
        from .services.workflow_seed import ensure_builtin_set
        await ensure_builtin_set(db)
        # Automatisch erzeugte Beschaffungs-Ketten der Projekte auf dieselbe Bauform heben.
        from .services.hardware_workflow import refresh_generated_definitions
        await refresh_generated_definitions(db)
        # Artefakt-Register (Ticket, Hardware) — im Admin pflegbar, fehlende Zustände
        # werden nachgetragen, bestehende Beschriftungen bleiben.
        from .services.artifacts import backfill_hardware_artifacts, ensure_builtin_types
        # Erst die Beschriftungen aus dem früheren Zustands-Modell übernehmen — danach legt
        # `ensure_builtin_types` die eingebauten Felder an, ohne sie zu überschreiben.
        from .services.artifact_fields import uebernimm_alte_zustaende
        await uebernimm_alte_zustaende(db)
        await ensure_builtin_types(db)
        # Erst jetzt fällt das alte Zustands-Modell — vorher hätte die Übernahme
        # nichts mehr zu lesen gehabt.
        await db.execute(text("DROP TABLE IF EXISTS artifact_statuses"))
        await db.commit()
        # Bestehende Exemplare bekommen ihre Artefakt-Zeile (idempotent).
        await backfill_hardware_artifacts(db)
        # Tickets ebenso — und alles, was auseinandergelaufen ist, wird angeglichen.
        from .services.artifacts import reconcile
        await reconcile(db)
    await recover_on_start()
    # Tickets ohne Prozess-Instanz einsammeln (Umstieg auf die Engine, idempotent).
    async with SessionLocal() as db:
        from .services.lifecycle_flow import adopt_orphans
        await adopt_orphans(db)
    # Previews aus einem abgestürzten Vorleben abräumen (blockiert den Start nicht).
    from .services.testenv import cleanup_orphan_previews
    tasks = [
        asyncio.create_task(cleanup_orphan_previews()),
        asyncio.create_task(run_dispatcher()),
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_workflow_engine()),
        asyncio.create_task(event_bridge()),
        # Eigener Kanal für die Büro-Ansicht: ein Nutzer-Socket statt N Projekt-Sockets,
        # weil projektlose Läufe (Job/Assistent) gar keinen Projektraum haben.
        asyncio.create_task(office_bridge()),
        # Deployments in den Büro-Ereignisstrom: eigener 3-s-Takt, weil der Betriebs-Tick
        # (30 s) länger ist als ein mittlerer Deploy — der Auftakt wäre sonst regelmäßig
        # vorbei, bevor jemand hinsieht.
        asyncio.create_task(run_deploy_watch()),
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
api.include_router(processes.router)
api.include_router(ops.router)
api.include_router(destinations.router)
api.include_router(artifacts_api.router)
api.include_router(mail.router)
api.include_router(secrets.router)
api.include_router(permissions.router)
api.include_router(notifications.router)
api.include_router(cost.router)
api.include_router(skills.router)
api.include_router(plugins.router)
api.include_router(agents.router)
api.include_router(office.router)
api.include_router(deployments.router)
api.include_router(runs.router)
api.include_router(testenv.router)
api.include_router(dashboard.router)
api.include_router(files.router)
api.include_router(repo.router)
api.include_router(admin.router)
api.include_router(ws.router)
api.include_router(office_ws_router)


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
