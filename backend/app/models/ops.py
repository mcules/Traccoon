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
    # public_id is the GUID in the inbound URL (/api/hooks/<public_id>); route is only a label.
    public_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default="")
    # Owner: the inbound ticket runs with their token plus MCP servers.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    route: Mapped[str] = mapped_column(String(120), nullable=False)  # label (no longer globally unique)
    secret: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), default="task")  # task | notify | assistant | workflow
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # Mode 'workflow': starts an instance of this definition; context_map maps payload to context.
    workflow_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"), nullable=True)
    context_map: Mapped[dict] = mapped_column(JSON, default=dict)  # {context_key: payload_path}
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Modus 'assistant' (E-Mail): projektlose AssistantTask + lokale Vorklassifizierung durch
    # this agent (its provider, model and token). Empty = no classification (passthrough).
    classify_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # mode='event': under which name the event is reported (empty = webhook.<route>).
    event_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Complete task prompt (mail processing knowledge: categories, filing rules …).
    # Placeholders {account}/{uid}/{from}/{subject}/{body_text}… are filled from the payload.
    # Empty = built-in default prompt. Ported from the predecessor (webhook_subs.prompt_tmpl).
    prompt_tmpl: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Chatless immediate run without approval (for instance paperless-linked link-back). Default: review gate.
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
    permissions_json: Mapped[list] = mapped_column(JSON, default=list)  # policy for project-less tasks
    # Answer back to the caller (mode 'workflow'): how many seconds the request may be held
    # open until the flow has produced its answer (0 = answer at once, as before). What is
    # sent back is what the flow itself wrote — `antwort` in the context (action `antwort`),
    # or, when a map stands here, exactly the fields it names ({field: context.path}).
    response_timeout: Mapped[int] = mapped_column(Integer, default=0)
    response_map: Mapped[dict] = mapped_column(JSON, default=dict)
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
    # Owner: runs with their token plus MCP servers. NULL = system (legacy or admin jobs).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="interval")   # cron | interval | once
    schedule: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(20), default="prompt")  # prompt|script|workflow|http
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # kind 'workflow': starts an instance of this definition when due (subject standalone).
    workflow_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"), nullable=True)
    # kind 'http': calls this destination when due. `http_request` carries
    # {method, path, query, headers, body}, the same shape as the process action.
    destination_id: Mapped[int | None] = mapped_column(
        ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True)
    http_request: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt: Mapped[str] = mapped_column(Text, default="")
    command: Mapped[str] = mapped_column(String(500), default="")       # script path
    # A list = arguments of a script job, an object = parameter set (prompt placeholders
    # respectively the start context of a flow job). The annotation used to tell only half the truth.
    args: Mapped[list | dict] = mapped_column(JSON, default=list)
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
    """Global key-value store for application-wide admin settings (maintenance project and the like)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Deployment(Base):
    """One queued deploy: the sidecar `deployer` fetches the row, builds and writes log and
    status back.

    **No code path writes `status='cancelled'`.** Checked so that the next reader saves the
    hour: `grep -rn cancelled` finds in the whole repository (backend, worker, `deployer/`,
    scripts, frontend) only `WorkflowInstanceStatus.cancelled`, none of which touches this
    table; `git log -S cancelled` shows no commit that ever did. The 69 rows with this status
    all come from **2026-07-21 between 19:08 and 19:31**: 23 minutes, an empty log on all 69,
    no `finished_at` on all 69, no `started_at` on 58 of them. That is a clean-up carried out
    no `finished_at` on all 69, no `started_at` on 58 of them. That is a clean-up carried
    out by hand on a stuck queue (an `UPDATE … WHERE status IN ('pending', 'building')`),
    not a function.

    The read API (`api/deployments.py`) therefore shows them but **does not canonicalise**
    them: their `phase` is `aborted` and their `ok` is `None`. Only when a
    `POST /deployments/{id}/cancel` exists does the status belong in the model.

    Two columns are **dead and stay that way**: `chat_id` and `requested_by` are filled on 0
    of 186 rows, and none of the four write sites (`services/dispatcher.py`,
    `services/workflow_actions.py`, `worker/__main__.py`, `worker/runtime.py`) sets them.
    They are not deleted (nothing hangs off them, but nothing is gained either) and appear in
    no response. Whoever wants to know the trigger reads `source`.
    """

    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    stack_dir: Mapped[str] = mapped_column(String(500), default="")
    worktree: Mapped[str] = mapped_column(String(500), default="")
    check_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Self-deploy (recreating the host stack) ONLY when explicitly set, never implicitly
    # through an empty stack_dir. Agents and auto-deploy always produce self_deploy=False.
    self_deploy: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|pending-check|building|ok|failed|rolledback
    log: Mapped[str] = mapped_column(Text, default="")
    # Who triggered the deploy: agent | merge | workflow | maintenance | manual. Empty =
    # unknown, and honestly so: the existing rows are NOT backfilled. The rule
    # `self_deploy → maintenance` would be correct today and would become wrong the moment
    # `merge`/`workflow` fire for the first time; a guessed origin in a history view is worse
    # than an empty field.
    # `manual` is the only value with a human behind it: the button under Settings →
    # Deployment (`api/deployments.create_deployment`). It deliberately does not count as
    # `agent`: "did somebody press it" is the question the column should answer when a state
    # is out there that no acceptance explains.
    source: Mapped[str] = mapped_column(String(20), default="")
    # Idempotency note of the stage watcher: which status has already been reported as an
    # event. Deliberately a column and not process memory, so restart proof and duplicate
    # free. Neither read nor delivered by the read API.
    announced_status: Mapped[str] = mapped_column(String(20), default="")
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
    # So that it is visible WHAT a model is good for, not only what it costs: the context
    # window (tokens) and the approximate output speed (tokens/s). With local models the
    # speed is measured and the actual reason to choose one, the price being 0 there.
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speed_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
