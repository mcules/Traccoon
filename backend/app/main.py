import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import models  # noqa: F401  (fills the metadata for create_all)
from .api import (
    admin, agents, artifacts as artifacts_api, auth, config, cost, dashboard, deployments,
    destinations, files, hardware, invitations,
    documents as documents_api,
    series as series_api, i18n as i18n_api, issues, lifecycle, mail, mailbox, mcp_server, metrics as metrics_api, me, notifications, ops, permissions, plugins, processes,
    projects, repo, office,
    runs, secrets, skills, testenv, users, workflows, ws,
)
from .config import settings
from .core.fehler import Fehler, fehler_handler
from .db import Base, SessionLocal, engine
from .seed import seed
from .services.dispatcher import recover_on_start, run_dispatcher
from .services.deploy_watch import run_deploy_watch
from .services.scheduler import run_scheduler
from .services.workflow_engine import run_workflow_engine
from .api.ws import event_bridge
from .api.office_ws import office_bridge, router as office_ws_router

VERSION = "0.1.0"
log = logging.getLogger("traccoon.start")


async def _fehlt_noch(conn, ddl: str) -> bool:
    """Does this `ADD COLUMN IF NOT EXISTS` need to run at all?

    `IF NOT EXISTS` prevents the error, not the lock: for every ALTER, Postgres first takes
    an AccessExclusiveLock on the table and ONLY THEN looks whether the column is already
    there. With 83 statements per backend start that means 83 exclusive locks on tables an
    agent is writing to next door. On 2026-08-07 at 18:00 exactly that killed run 753
    (TRA-31, 37 turns): the lock on `run_steps` against the running INSERT, Postgres broke
    the deadlock, and the victim was the agent.

    Looking first costs a cheap catalog read and, in the normal case (the column has long
    been there), takes no lock at all.
    """
    if m := re.match(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)\b", ddl, re.I):
        da = await conn.scalar(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"), {"t": m.group(1), "c": m.group(2)})
        return da is None
    # `CREATE INDEX IF NOT EXISTS` takes a ShareLock and thereby blocks every writer on the
    # table, so on `run_steps` the running agent. The same applies here: look first whether
    # the index still needs creating at all.
    if m := re.match(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)\b", ddl, re.I):
        return await conn.scalar(text("SELECT to_regclass(:n)"), {"n": m.group(1)}) is None
    # Would otherwise repeat on every start: the column has long been nullable, the lock is
    # requested anyway (and on 2026-08-07 promptly ran into the fresh lock_timeout).
    if m := re.match(r"ALTER TABLE (\w+) ALTER COLUMN (\w+) DROP NOT NULL", ddl, re.I):
        nullable = await conn.scalar(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"), {"t": m.group(1), "c": m.group(2)})
        return nullable == "NO"
    return True                           # everything else (ENUM values, UPDATE) runs as before


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.dev_create_all:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # And should something need catching up after all: give up after 3 seconds
            # instead of queueing in front of a running write load. A waiting
            # AccessExclusiveLock blocks every following writer in turn, so the
            # self-healing attempt would paralyse exactly what it is meant to repair.
            await conn.execute(text("SET lock_timeout = '3s'"))
            # Add additive columns idempotently (create_all only creates them on FRESH
            # tables, not on existing ones). Order and style as with ADD COLUMN IF NOT EXISTS.
            for _ddl in (
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS cap_baseline_run_id INTEGER",
                # Correction rounds spent at the review gate: on the ticket instead of in
                # the worker process, so that a restart does not reset the limit.
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS review_rounds INTEGER "
                "DEFAULT 0 NOT NULL",
                # Model catalog: context window plus approximate output speed. With local
                # models that is exactly the reason to choose one, the price being 0 there.
                "ALTER TABLE provider_models ADD COLUMN IF NOT EXISTS context_tokens INTEGER",
                "ALTER TABLE provider_models ADD COLUMN IF NOT EXISTS speed_tps DOUBLE PRECISION",
                # Eigene Base-URL je Provider-Token (OpenAI-kompatibler Endpoint, z. B. litellm).
                "ALTER TABLE provider_tokens ADD COLUMN IF NOT EXISTS base_url VARCHAR(500)",
                # Person assignment (TRA-20): placeholder accounts without a login.
                # create_all/ADD COLUMN does not add enum values, so ADD VALUE explicitly
                # (PG 12+ allows that in a transaction as long as the value is not used in it).
                "ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'placeholder'",
                # Learning policy of the assistant: redaction, raw text, learned action per item.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS redaction VARCHAR(20) DEFAULT 'redacted' NOT NULL",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS raw_body TEXT",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS action_hint TEXT DEFAULT '' NOT NULL",
                # Telegram approval card for project-less assistant items.
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS assistant_task_id INTEGER "
                "REFERENCES assistant_tasks(id) ON DELETE CASCADE",
                # Tool gate of the assistant: pending approval plus one-shot grant per item.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS pending_tool VARCHAR(150)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS pending_resource VARCHAR(500)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS grant_tool VARCHAR(150)",
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS grant_resource VARCHAR(500)",
                # Mail webhook as a normal WebhookSub (mode assistant): classifying agent.
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS classify_agent VARCHAR(100)",
                # Mail task prompt (processing knowledge) per webhook, ported from the predecessor.
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS prompt_tmpl TEXT",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS auto_run BOOLEAN DEFAULT FALSE NOT NULL",
                # Workflow trigger: webhook or job starts a workflow instance.
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS workflow_definition_id INTEGER "
                "REFERENCES workflow_definitions(id) ON DELETE SET NULL",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS context_map JSON DEFAULT '{}'::json NOT NULL",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workflow_definition_id INTEGER "
                "REFERENCES workflow_definitions(id) ON DELETE SET NULL",
                # E-Mail optional (login-lose Konten): NOT NULL entfernen (UNIQUE bleibt).
                "ALTER TABLE users ALTER COLUMN email DROP NOT NULL",
                # Ticket opening mode per user (popup|page).
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ticket_open_mode VARCHAR(10) "
                "DEFAULT 'popup' NOT NULL",
                # User specific block arrangement of the ticket page.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ticket_layout JSON DEFAULT '{}'::json NOT NULL",
                # PM-Chat-Darstellung je Nutzer (TRA-21).
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pm_chat_style VARCHAR(10) "
                "DEFAULT 'bubbles' NOT NULL",
                # Sub-Projekte (TRA-8/22): Vererbungs-Schalter + optionaler Projektbezug am Ort.
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS inherit_members BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                "ALTER TABLE locations ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL",
                # Agent runs follow the ticket into the archive (TRA-29).
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE NOT NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
                # Catch up existing data: archive runs of already archived tickets too.
                "UPDATE runs SET archived = TRUE, archived_at = COALESCE(issues.archived_at, now()) "
                "FROM issues WHERE runs.issue_id = issues.id AND issues.archived "
                "AND NOT runs.archived",
                # Responsible person per procurement step (TRA-26).
                "ALTER TABLE hardware_workflow_steps ADD COLUMN IF NOT EXISTS assignee "
                "JSON DEFAULT '{}'::json NOT NULL",
                # Testumgebungs-Lebenszyklus je Projekt (TRA-18).
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_enabled BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_compose_file "
                "VARCHAR(255) DEFAULT 'compose.preview.yml' NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_dockerfile "
                "VARCHAR(255) DEFAULT 'Dockerfile' NOT NULL",
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS testenv_url_template "
                "VARCHAR(255) DEFAULT 'http://{host}:{port}' NOT NULL",
                # Create the "testing" column for existing projects and sort it before "done".
                # Both statements are idempotent (NOT EXISTS, respectively only when done follows).
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
                # Align the column order with the status, otherwise the new "testing" column
                # collides with the old place of "done".
                "UPDATE board_columns c SET \"order\" = s.\"order\" FROM workflow_statuses s "
                "WHERE s.id = c.status_id AND c.\"order\" <> s.\"order\"",
                # Attach a ticket to a hardware unit (TRA-25).
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS asset_id INTEGER "
                "REFERENCES hardware_assets(id) ON DELETE SET NULL",
                # Process sets: slot and archive on the definitions, set reference on
                # project or user, routing stamp on the step, instance on the ticket.
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
                # Assign existing procurement definitions to the slot so that they are
                # recognised as a project adjustment (instead of standing beside the set).
                "UPDATE workflow_definitions SET slot = 'hardware_procurement' "
                "WHERE key = 'hardware-beschaffung' AND slot IS NULL AND project_id IS NOT NULL",
                # The webhook reports an event instead of starting a fixed flow.
                # Hardware gets a shared artifact identity; processes bind to an artifact in
                # general instead of to a single unit.
                "ALTER TABLE hardware_assets ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE CASCADE",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_hardware_asset_artifact ON hardware_assets "
                "(artifact_id) WHERE artifact_id IS NOT NULL",
                "ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE SET NULL",
                "CREATE INDEX IF NOT EXISTS ix_workflow_instances_artifact ON workflow_instances "
                "(artifact_id)",
                # Tickets get the same shared artifact identity as the hardware.
                "ALTER TABLE issues ADD COLUMN IF NOT EXISTS artifact_id INTEGER "
                "REFERENCES artifacts(id) ON DELETE SET NULL",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_issue_artifact ON issues "
                "(artifact_id) WHERE artifact_id IS NOT NULL",
                # The two JSON placeholders are superseded by the real field model
                # (`artifact_fields`/`artifact_values`). They were never filled but stand in
                # the table as NOT NULL without a default, so every INSERT would fail without a DROP.
                "ALTER TABLE artifact_types DROP COLUMN IF EXISTS fields",
                "ALTER TABLE artifacts DROP COLUMN IF EXISTS data",
                # Fields carry their origin (real column) and may be built in; values carry
                # a category and "waiting", both of which migrated here from the earlier
                # status model.
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
                # A flow may be bound to a kind of item (bug is not task).
                # The unique index is rebuilt for that: COALESCE, because NULLs would
                # otherwise count as different and any number of generic copies could appear.
                "ALTER TABLE workflow_definitions ADD COLUMN IF NOT EXISTS issue_type_id "
                "INTEGER REFERENCES issue_types(id) ON DELETE CASCADE",
                "CREATE INDEX IF NOT EXISTS ix_workflow_definitions_issue_type "
                "ON workflow_definitions (issue_type_id)",
                "DROP INDEX IF EXISTS uq_workflow_def_project_slot",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_def_project_slot "
                "ON workflow_definitions (project_id, slot, COALESCE(issue_type_id, 0)) "
                "WHERE archived_at IS NULL",
                # A project may extend its artifacts with fields of its own.
                "ALTER TABLE artifact_fields ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE CASCADE",
                "CREATE INDEX IF NOT EXISTS ix_artifact_fields_project "
                "ON artifact_fields (project_id)",
                "ALTER TABLE artifact_fields DROP CONSTRAINT IF EXISTS uq_artifact_field",
                "DROP INDEX IF EXISTS uq_artifact_field",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_field "
                "ON artifact_fields (type_id, COALESCE(project_id, 0), key)",
                # The ordering level "artifact type" is gone again: ticket and hardware are
                # both simply artifacts, their meaning comes from the fields.
                "ALTER TABLE artifact_types DROP COLUMN IF EXISTS group_id",
                "DROP TABLE IF EXISTS artifact_groups",
                "ALTER TABLE webhook_subs ADD COLUMN IF NOT EXISTS event_name VARCHAR(120)",
                # The assistant only speaks up when it is necessary.
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
                # Loop node: walks a list element by element.
                "ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'loop'",
                # Timer node: waits a while without anyone having to report anything.
                "ALTER TYPE workflownodetype ADD VALUE IF NOT EXISTS 'timer'",
                # Memory in the vault (TRA-30): folder on the user, learning switch on the agent.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS vault_memory_path VARCHAR(500) "
                "DEFAULT '' NOT NULL",
                "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS learns BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                # Response limit per destination (TRA-31): counterparts that deliberately
                # deliver their whole state in one call need more than the flat 4000 characters.
                "ALTER TABLE destinations ADD COLUMN IF NOT EXISTS max_response_chars "
                "INTEGER DEFAULT 4000 NOT NULL",
                # Office: project and owner move onto the run so that the live bridge can
                # authorise every event without a database round trip, and so that
                # project-less runs (assistant, job) have an affiliation at all.
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS owner_id INTEGER "
                "REFERENCES users(id) ON DELETE SET NULL",
                # Parent to child link: at the `delegate` tool start the child run id is
                # still unknown, the tool id already known. The child brings it along.
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS parent_tool_use_id VARCHAR(64)",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS spawn_depth SMALLINT DEFAULT 0 NOT NULL",
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS blocker_kind VARCHAR(24)",
                # Existing runs get their project from the ticket; without that the view
                # starts empty, because no old run would be authorisable.
                "UPDATE runs SET project_id = i.project_id FROM issues i "
                "WHERE runs.issue_id = i.id AND runs.project_id IS NULL",
                "CREATE INDEX IF NOT EXISTS ix_runs_project_started ON runs (project_id, started_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_runs_owner_started ON runs (owner_id, started_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_runs_parent_run_id ON runs (parent_run_id)",
                "CREATE INDEX IF NOT EXISTS ix_runs_issue_started ON runs (issue_id, started_at)",
                # Personnel file (`/office/agents`): each of its five queries groups by
                # `runs.agent` within a time window, and the tool table even joins
                # `run_steps` against `runs` for it. Without this index that is a seq scan
                # over meanwhile 13 000 run rows, on every opening of the tab.
                "CREATE INDEX IF NOT EXISTS ix_runs_agent_started ON runs (agent, started_at DESC)",
                # The step carries its event itself: kind, tool id, target, success, duration
                # and the tokens of the single model turn. `ok` is three valued
                # (NULL=unknown), `provider`/`model` record WHO answered, which on a
                # fallback is not the provider on the run.
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
                # Load bearing, not cosmetic: the view always reads "one run, in arrival
                # order". Without this index Postgres sorts up to 20 000 rows per fetch. It
                # runs in engine.begin(), so without CONCURRENTLY: on an already large table
                # create it by hand beforehand, then this here is a no-op.
                "CREATE INDEX IF NOT EXISTS ix_run_steps_run_id_id ON run_steps (run_id, id)",
                # Three valued: NULL=old row, False=no catalog entry (the 0.00 is merely a
                # gap), True=priced. Without it every catalog gap reads as "free".
                "ALTER TABLE cost_entries ADD COLUMN IF NOT EXISTS priced BOOLEAN",
                # Deployments (`api/deployments.py`): 186 rows nobody could read so far.
                # `source` answers the question `requested_by`/`chat_id` never answered
                # (filled on 0 of 186 rows), deliberately WITHOUT a backfill: the origin of
                # the existing rows stays empty instead of guessed.
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS source VARCHAR(20) "
                "DEFAULT '' NOT NULL",
                # Note of the stage watcher: which status has already been reported. A
                # column instead of process memory means restart proof and duplicate free.
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS announced_status VARCHAR(20) "
                "DEFAULT '' NOT NULL",
                "CREATE INDEX IF NOT EXISTS ix_deployments_project_created ON deployments "
                "(project_id, created_at DESC)",
                "CREATE INDEX IF NOT EXISTS ix_deployments_issue ON deployments (issue_id)",
                # The partial index has nothing to do with the new interface and belongs
                # here anyway: the `deployer` sidecar looks for the next open row every 3
                # seconds and turns that into a seq scan over the whole table today.
                # The same index serves the coming stage watcher.
                "CREATE INDEX IF NOT EXISTS ix_deployments_open ON deployments (id) "
                "WHERE status IN ('pending','pending-check','building')",
                # Media output of the notification: the backend container lacks
                # `TELEGRAM_BOT_TOKEN` entirely (only `TELEGRAM_OWNER_CHAT` is set), only the
                # telegram-bot process talks to Telegram. Whoever wants to send a file along
                # puts the path here; both columns are nullable so that nothing changes for
                # existing rows.
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS media_path VARCHAR(500)",
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS media_kind VARCHAR(20)",
                # Mail inbox as a process: the spam question knows its flow so that the
                # answer from Telegram advances it instead of moving past it.
                "ALTER TABLE spam_verdicts ADD COLUMN IF NOT EXISTS workflow_instance_id "
                "INTEGER REFERENCES workflow_instances(id) ON DELETE SET NULL",
                "CREATE INDEX IF NOT EXISTS ix_spam_verdicts_workflow_instance_id "
                "ON spam_verdicts (workflow_instance_id)",
                # The notification channel belongs to the person: whoever triggers a message
                # rarely knows whether the recipient uses Telegram at all. The sender may
                # prescribe a channel but need not, and then this one applies.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_default VARCHAR(20) "
                "DEFAULT 'telegram' NOT NULL",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_email VARCHAR(255)",
                # Drossel je Nachrichten-Art: „höchstens alle N Minuten dasselbe".
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS drossel_key VARCHAR(160)",
                "CREATE INDEX IF NOT EXISTS ix_notifications_drossel "
                "ON notifications (drossel_key, created_at)",
                # Stille-Marke einer Messreihe (einmal je Stille-Phase melden).
                "ALTER TABLE metric_series ADD COLUMN IF NOT EXISTS still_at "
                "TIMESTAMP WITH TIME ZONE",
                # UI language per person, and the translation overrides an admin edits.
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS locale VARCHAR(10) "
                "DEFAULT 'de' NOT NULL",
                # Languages an admin has created, renamed or switched off.
                "ALTER TABLE ui_locales ADD COLUMN IF NOT EXISTS enabled BOOLEAN "
                "DEFAULT TRUE NOT NULL",
                # Metric series (create_all creates the tables; the index it does not).
                "CREATE INDEX IF NOT EXISTS ix_metric_points_series_ts "
                "ON metric_points (series_id, ts DESC)",
                # Assistant: archive instead of delete. The chat window used to keep
                # everything forever, and the inbox had no way to put a finished item away.
                "ALTER TABLE assistant_tasks ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
                # What a mail was classified as, and the findings behind it. The kind is what
                # the statistics group by, the findings are what the knowledge note is fed
                # from; both used to exist only inside one log line.
                "ALTER TABLE spam_verdicts ADD COLUMN IF NOT EXISTS art VARCHAR(40) "
                "DEFAULT '' NOT NULL",
                "CREATE INDEX IF NOT EXISTS ix_spam_verdicts_art ON spam_verdicts (art)",
                "ALTER TABLE spam_verdicts ADD COLUMN IF NOT EXISTS befunde JSON "
                "DEFAULT '[]'::json NOT NULL",
                "CREATE INDEX IF NOT EXISTS ix_assistant_tasks_archived_at "
                "ON assistant_tasks (archived_at)",
                # Plugins: was sie an Traccoon-Daten lesen wollen, was davon freigegeben ist
                # und welche fremden Quellen ihre Seite laden darf. Ohne diese drei Spalten
                # bleibt die Bruecke zum Wirt geschlossen, denn sie fragt genau danach.
                "ALTER TABLE plugins ADD COLUMN IF NOT EXISTS liest JSON "
                "DEFAULT '[]'::json NOT NULL",
                "ALTER TABLE plugins ADD COLUMN IF NOT EXISTS liest_erlaubt JSON "
                "DEFAULT '[]'::json NOT NULL",
                "ALTER TABLE plugins ADD COLUMN IF NOT EXISTS csp JSON "
                "DEFAULT '{}'::json NOT NULL",
                # Datenreihen: create_all legt die Tabellen an, den zusammengesetzten Index
                # nicht. Er ist der einzige, der bei einer Million Standortpunkten zaehlt —
                # jede Abfrage lautet "diese Reihe, dieser Zeitraum".
                "CREATE INDEX IF NOT EXISTS ix_series_points_series_ts "
                "ON series_points (series_id, ts DESC)",
                # Das Aufnahme-Token wird bei jedem einzelnen Punkt nachgeschlagen.
                "CREATE INDEX IF NOT EXISTS ix_series_token_hash "
                "ON series (token_hash)",
            ):
                if not await _fehlt_noch(conn, _ddl):
                    continue
                try:
                    await conn.execute(text(_ddl))
                except Exception as exc:  # noqa: BLE001 - a lock conflict must not topple the start
                    log.warning("Schema update skipped (%s): %s", _ddl[:60], exc)
    async with SessionLocal() as db:
        await seed(db)
        # Add the shipped flows (ticket lifecycle, acceptance, procurement, inbox) as the
        # global default set: idempotent, published only on a change.
        from .services.workflow_seed import ensure_builtin_set
        await ensure_builtin_set(db)
        # Webhooks der alten Modi (Ticket, Meldung, Assistent) laufen ab jetzt über Abläufe.
        # Einmalig und idempotent — wer schon umgestellt ist, wird nicht angefasst.
        from .services.webhook_modes import umstellen as webhooks_umstellen
        anzahl = await webhooks_umstellen(db)
        if anzahl:
            log.info("%s Webhook(s) auf Abläufe umgestellt", anzahl)
        # Dasselbe für die Job-Arten: Prompt, Skript und HTTP sind Knoten im Ablauf.
        from .services.job_modes import umstellen as jobs_umstellen
        anzahl = await jobs_umstellen(db)
        if anzahl:
            log.info("%s Job(s) auf Abläufe umgestellt", anzahl)
        # Lift the automatically created procurement chains of the projects to the same shape.
        from .services.hardware_workflow import refresh_generated_definitions
        await refresh_generated_definitions(db)
        # Abläufe sprechen englisch: Aktionsnamen, Parameter und Kontextfelder. Erst NACH den
        # Erzeugern, sonst schriebe der nächste Seed-Lauf seine frische Fassung ohne Marke
        # daneben und die Umstellung liefe bei jedem Start wieder an.
        from .services.workflow_terms import migriere_alle as begriffe_umstellen
        anzahl = await begriffe_umstellen(db)
        if anzahl:
            log.info("%s Ablauf-Fassung(en) auf englische Begriffe umgeschrieben", anzahl)
        # Artifact register (ticket, hardware): maintainable in the admin area, missing
        # states are added, existing labels stay.
        from .services.artifacts import backfill_hardware_artifacts, ensure_builtin_types
        # First take over the labels from the earlier status model; afterwards
        # `ensure_builtin_types` creates the built-in fields without overwriting them.
        from .services.artifact_fields import uebernimm_alte_zustaende
        await uebernimm_alte_zustaende(db)
        await ensure_builtin_types(db)
        # Only now does the old status model fall; before this the takeover would have had
        # nothing left to read.
        await db.execute(text("DROP TABLE IF EXISTS artifact_statuses"))
        await db.commit()
        # Existing units get their artifact row (idempotent).
        await backfill_hardware_artifacts(db)
        # Tickets likewise, and everything that has drifted apart is aligned.
        from .services.artifacts import reconcile
        await reconcile(db)
    await recover_on_start()
    # Collect tickets without a process instance (switch to the engine, idempotent).
    async with SessionLocal() as db:
        from .services.lifecycle_flow import adopt_orphans
        await adopt_orphans(db)
    # Clear away previews from a crashed earlier life (does not block the start).
    from .services.testenv import cleanup_orphan_previews
    tasks = [
        asyncio.create_task(cleanup_orphan_previews()),
        asyncio.create_task(run_dispatcher()),
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_workflow_engine()),
        asyncio.create_task(event_bridge()),
        # A channel of its own for the office view: one user socket instead of N project
        # sockets, because project-less runs (job, assistant) have no project room at all.
        asyncio.create_task(office_bridge()),
        # Deployments into the office event stream: a 3 s beat of its own, because the
        # operations tick (30 s) is longer than an average deploy, so the opening would
        # regularly be over before anyone looks.
        asyncio.create_task(run_deploy_watch()),
    ]
    # Postfächer, die sich von selbst melden (IMAP IDLE). Läuft nur, solange jemand
    # zuschaut — siehe `mail_watch`.
    from .services import mail_watch
    await mail_watch.starten()

    yield
    await mail_watch.stoppen()
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
# Error texts carry their key along, so a browser can show them in its own language.
api.add_exception_handler(Fehler, fehler_handler)
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
api.include_router(metrics_api.router)
api.include_router(documents_api.router)
api.include_router(series_api.router)
api.include_router(i18n_api.router)
api.include_router(artifacts_api.router)
api.include_router(mail.router)
api.include_router(mailbox.router)
api.include_router(mcp_server.router)
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


# All API paths under /api
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
