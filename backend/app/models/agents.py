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
    fallback_model: Mapped[str] = mapped_column(String(150), default="")     # Modell des Fallback-Providers
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
    allowed_skills: Mapped[list] = mapped_column(JSON, default=list)  # verfügbare Skill-Keys
    autoload_skills: Mapped[list] = mapped_column(JSON, default=list) # Teilmenge: immer in den Prompt
    delegate_to: Mapped[list] = mapped_column(JSON, default=list)     # Sub-Agenten-Rollen
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Verknüpfung (Workstream B): aus welchem globalen Agenten kopiert + ob seither bearbeitet
    origin_agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="SET NULL"), nullable=True)
    customized: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[str] = mapped_column(String(200), default="")
    agent: Mapped[str] = mapped_column(String(100), default="")
    phase: Mapped[str] = mapped_column(String(20), default="")
    provider: Mapped[str] = mapped_column(String(50), default="")
    model: Mapped[str] = mapped_column(String(150), default="")
    claude_sub_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|success|failed|blocked|planned
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    continuation_index: Mapped[int] = mapped_column(Integer, default=0)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worktree_fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Läufe folgen dem Ticket (TRA-29): Ticket archivieren → Läufe archivieren.
    # Archivierte Läufe verschwinden aus dem Monitor und werden nach der
    # Aufbewahrungsfrist (AppSetting run_retention_days) endgültig gelöscht.
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(20), default="")       # assistant|tool|system
    tool_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
