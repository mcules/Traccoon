import datetime as dt
import enum

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, JSON, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import pg_enum_values


class PermAction(str, enum.Enum):
    allow = "allow"
    ask = "ask"
    deny = "deny"


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(150), nullable=False)
    resource: Mapped[str] = mapped_column(String(500), default="*")
    action: Mapped[PermAction] = mapped_column(
        SAEnum(PermAction, name="permaction", values_callable=pg_enum_values), default=PermAction.ask
    )


class PermRequest(Base):
    __tablename__ = "perm_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool: Mapped[str] = mapped_column(String(150), nullable=False)
    resource: Mapped[str] = mapped_column(String(500), default="*")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | decided
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)  # once | always | never
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PermGrant(Base):
    __tablename__ = "perm_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(150), nullable=False)
    resource: Mapped[str] = mapped_column(String(500), default="*")
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookSub(TimestampMixin, Base):
    __tablename__ = "webhook_subs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # public_id ist die GUID in der Inbound-URL (/api/hooks/<public_id>); route ist nur ein Label.
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default="")
    # Eigentümer: Inbound-Ticket läuft mit dessen Token + MCP-Servern.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    route: Mapped[str] = mapped_column(String(120), nullable=False)  # Label (nicht mehr global eindeutig)
    secret: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), default="task")  # task | notify | assistant | workflow
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # Modus 'workflow': startet eine Instanz dieser Definition; context_map bildet Payload→Kontext ab.
    workflow_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"), nullable=True)
    context_map: Mapped[dict] = mapped_column(JSON, default=dict)  # {context_key: payload_path}
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Modus 'assistant' (E-Mail): projektlose AssistantTask + lokale Vorklassifizierung durch
    # diesen Agenten (dessen Provider/Modell/Token). Leer = keine Klassifizierung (Passthrough).
    classify_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # mode='event': unter welchem Namen das Ereignis gemeldet wird (leer = webhook.<route>).
    event_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Vollständiger Task-Prompt (Mail-Verarbeitungs-Wissen: Kategorien, Ablage-Regeln …).
    # Platzhalter {account}/{uid}/{from}/{subject}/{body_text}… werden aus dem Payload gefüllt.
    # Leer = eingebauter Standard-Prompt. Portiert aus dem Vorläufer (webhook_subs.prompt_tmpl).
    prompt_tmpl: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Chatloser Sofortlauf ohne Freigabe (z. B. paperless-linked Link-back). Default: Review-Gate.
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status_new: Mapped[str] = mapped_column(String(20), default="planning")
    title_template: Mapped[str] = mapped_column(String(500), default="{title}")
    body_template: Mapped[str] = mapped_column(Text, default="{body}")
    silent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Filter / Idempotenz / Coalescing / Alerts
    event_header: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_key_header: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_cooldowns: Mapped[dict] = mapped_column(JSON, default=dict)   # {event: seconds}
    alert_events: Mapped[list] = mapped_column(JSON, default=list)
    ref_field: Mapped[str | None] = mapped_column(String(120), nullable=True)  # → source_ref (Idempotenz)
    notify_chat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permissions_json: Mapped[list] = mapped_column(JSON, default=list)  # Policy für projektlose Tasks
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookCoalesce(Base):
    __tablename__ = "webhook_coalesce"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route: Mapped[str] = mapped_column(String(120), index=True)
    event_key: Mapped[str] = mapped_column(String(255), default="")
    window_until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    payloads: Mapped[list] = mapped_column(JSON, default=list)
    flushed: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Eigentümer: läuft mit dessen Token + MCP-Servern. NULL = System (Alt-/Admin-Jobs).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="interval")   # cron | interval | once
    schedule: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(20), default="prompt")  # prompt|script|workflow|http
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # kind 'workflow': startet bei Fälligkeit eine Instanz dieser Definition (subject standalone).
    workflow_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"), nullable=True)
    # kind 'http': ruft bei Fälligkeit dieses Ziel auf. `http_request` trägt
    # {method, path, query, headers, body} — dieselbe Form wie die Prozess-Aktion.
    destination_id: Mapped[int | None] = mapped_column(
        ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True)
    http_request: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt: Mapped[str] = mapped_column(Text, default="")
    command: Mapped[str] = mapped_column(String(500), default="")       # script-Pfad
    args: Mapped[list] = mapped_column(JSON, default=list)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    notify_mode: Mapped[str] = mapped_column(String(20), default="on_output")  # always|on_output|on_error|never
    notify_chat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_html: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_on_success: Mapped[bool] = mapped_column(Boolean, default=False)
    run_timeout: Mapped[int] = mapped_column(Integer, default=600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error
    output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Globaler Key-Value-Store für App-weite Admin-Einstellungen (z. B. Wartungsprojekt)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)
    stack_dir: Mapped[str] = mapped_column(String(500), default="")
    worktree: Mapped[str] = mapped_column(String(500), default="")
    check_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Self-Deploy (Host-Stack recreaten) NUR wenn explizit gesetzt — nie implizit durch
    # leeren stack_dir. Agenten/Auto-Deploy erzeugen immer self_deploy=False.
    self_deploy: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|pending-check|building|ok|failed|rolledback
    log: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderModel(Base):
    __tablename__ = "provider_models"
    __table_args__ = (UniqueConstraint("provider", "model", name="uq_provider_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), default="")
    price_input: Mapped[float] = mapped_column(Float, default=0.0)         # USD / 1M
    price_output: Mapped[float] = mapped_column(Float, default=0.0)
    price_cache_read: Mapped[float] = mapped_column(Float, default=0.0)
    # Damit sichtbar ist, WOFÜR ein Modell taugt, nicht nur was es kostet: das Kontextfenster
    # (Tokens) und die ungefähre Ausgabegeschwindigkeit (Tokens/s). Bei lokalen Modellen ist
    # die Geschwindigkeit gemessen und der eigentliche Auswahlgrund — Preis ist dort 0.
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speed_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
