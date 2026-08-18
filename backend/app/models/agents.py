import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class AgentDefinition(TimestampMixin, Base):
    __tablename__ = "agent_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(50), default="claude_code")
    model: Mapped[str] = mapped_column(String(150), default="")
    token_name: Mapped[str] = mapped_column(String(120), default="")  # welcher benannte Token (leer=Default)
    fallback: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Fallback-Provider
    fallback_model: Mapped[str] = mapped_column(String(150), default="")     # model of the fallback provider
    fallback_token_name: Mapped[str] = mapped_column(String(120), default="")
    effort: Mapped[str] = mapped_column(String(10), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    max_tokens: Mapped[int] = mapped_column(Integer, default=16384)
    max_context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_turns_planning: Mapped[int] = mapped_column(Integer, default=10)
    max_turns_execution: Mapped[int] = mapped_column(Integer, default=80)
    can_code: Mapped[bool] = mapped_column(Boolean, default=False)
    can_read_code: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delegate: Mapped[bool] = mapped_column(Boolean, default=False)
    web_search: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)   # Tool-Glob-Whitelist
    allowed_skills: Mapped[list] = mapped_column(JSON, default=list)  # available skill keys
    autoload_skills: Mapped[list] = mapped_column(JSON, default=list) # subset: always in the prompt
    delegate_to: Mapped[list] = mapped_column(JSON, default=list)     # Sub-Agenten-Rollen
    # Learns (ABC-30): reads the memory from the vault of the owner at the beginning and looks
    # back after a successful run in order to file what is permanently valid there. Needs a
    # set User.vault_memory_path; without it nothing happens.
    learns: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Link: which global agent it was copied from plus whether it has been edited since
    origin_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="SET NULL"), nullable=True)
    customized: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=True, index=True)
    # Project and owner lie redundantly on the run, because the office has to authorise every
    # event without a database round trip; the way over the ticket would be a JOIN per event,
    # and project-less runs (assistant, job) would have none at all. SET NULL, so that a
    # deleted project does not take the cost history of the run with it.
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[str] = mapped_column(String(200), default="")
    agent: Mapped[str] = mapped_column(String(100), default="")
    phase: Mapped[str] = mapped_column(String(20), default="")
    provider: Mapped[str] = mapped_column(String(50), default="")
    model: Mapped[str] = mapped_column(String(150), default="")
    claude_sub_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # running|success|failed|blocked|planned|loop_exhausted
    status: Mapped[str] = mapped_column(String(20), default="running")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    continuation_index: Mapped[int] = mapped_column(Integer, default=0)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The link key between parent and child run: at the tool start (`delegate`) the child run
    # id is still unknown while the tool id is already known. The child brings it along, and
    # the room can draw the spawn line and the handover without a backward search.
    parent_tool_use_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spawn_depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # What the run hangs on when status='blocked'; otherwise "blocked" would not be
    # distinguishable without reading the text: ask_human|permission|assistant_perm|question.
    blocker_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    worktree_fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Runs follow the ticket (ABC-29): archiving a ticket archives its runs. Archived runs
    # disappear from the monitor and are finally deleted after the retention period
    # (AppSetting run_retention_days).
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(20), default="")       # assistant|tool|system|user
    tool_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # Event kind of the step (services/office.KINDS). Empty = an old row from the time before
    # the instrumentation; that one is reconstructed from `role` plus `content` while reading,
    # so that the history does not start from zero.
    kind: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    tool_use_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # What the tool acts on (path, role, URL), the same label as in the permission dialog.
    # With user_message the source stands here instead.
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Three valued on purpose: True = proven successful, False = proven failed, NULL =
    # unknown. A guessed True would paint the view green where nobody looked.
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Tokens of the single model turn; the totals on the `Run` do not say WHEN they arose.
    in_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    out_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Who actually answered: on a fallback that is not the provider on the `Run`.
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)
    agent: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(50), default="")
    model: Mapped[str] = mapped_column(String(150), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # Three valued: True = there was a catalog entry (0.00 then means *free*, not *unknown*),
    # False = no entry, so the 0.00 arose merely for lack of a price, NULL = an old row that
    # never knew the distinction. Without it every gap in the catalog reads as "free".
    priced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
