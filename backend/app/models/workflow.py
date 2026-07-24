import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import (
    WorkflowInstanceStatus, WorkflowNodeType, WorkflowStepStatus, WorkflowSubjectKind,
    WorkflowTokenState, WorkflowVersionStatus, pg_enum_values,
)


class WorkflowDefinition(TimestampMixin, Base):
    """Logischer Workflow (Container über Versionen). project_id NULL = globale Vorlage."""
    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_workflow_def_project_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)  # slug, eindeutig pro Projekt
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    subject_kind: Mapped[WorkflowSubjectKind] = mapped_column(
        SAEnum(WorkflowSubjectKind, name="workflowsubjectkind", values_callable=pg_enum_values),
        default=WorkflowSubjectKind.standalone,
    )
    # Veröffentlichte Version, auf die neue Instanzen zeigen. NULL = noch kein Publish.
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WorkflowVersion(Base):
    """Unveränderlicher Graph-Snapshot. Veröffentlichte Versionen dürfen NICHT editiert werden."""
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("definition_id", "version", name="uq_workflow_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    # React-Flow-nativ: {"nodes": [...], "edges": [...]} inkl. Positionen + node.data.config
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[WorkflowVersionStatus] = mapped_column(
        SAEnum(WorkflowVersionStatus, name="workflowversionstatus", values_callable=pg_enum_values),
        default=WorkflowVersionStatus.draft,
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowInstance(TimestampMixin, Base):
    """Laufende Ausführung eines Workflows. version_id gepinnt → Edits brechen Instanzen nicht."""
    __tablename__ = "workflow_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT")
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject_kind: Mapped[WorkflowSubjectKind] = mapped_column(
        SAEnum(WorkflowSubjectKind, name="workflowsubjectkind", values_callable=pg_enum_values),
        default=WorkflowSubjectKind.standalone,
    )
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hardware_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("hardware_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[WorkflowInstanceStatus] = mapped_column(
        SAEnum(WorkflowInstanceStatus, name="workflowinstancestatus", values_callable=pg_enum_values),
        default=WorkflowInstanceStatus.running, index=True,
    )
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # Variablen-Kontext (Guards lesen hier)
    # Atomarer Claim gegen Doppel-Advance (Tick ↔ Event), analog Issue.agent_working.
    advancing: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)       # webhook | job | manual
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Idempotenz
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowToken(Base):
    """Ausführungsposition. v1: genau 1 aktives Token je Instanz; Schema erlaubt N (AND-Split später)."""
    __tablename__ = "workflow_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_instances.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(80), nullable=False)  # = graph node id
    state: Mapped[WorkflowTokenState] = mapped_column(
        SAEnum(WorkflowTokenState, name="workflowtokenstate", values_callable=pg_enum_values),
        default=WorkflowTokenState.active, index=True,
    )
    waiting_for: Mapped[str | None] = mapped_column(String(30), nullable=True)  # human_task|approval|agent|timer
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkflowStepRun(Base):
    """Append-only Historie + Persistenz von Zuständigen/Formular/Genehmigung/Agent-Ergebnis."""
    __tablename__ = "workflow_step_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_instances.id", ondelete="CASCADE"), index=True
    )
    token_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_tokens.id", ondelete="SET NULL"), nullable=True
    )
    node_id: Mapped[str] = mapped_column(String(80), nullable=False)
    node_type: Mapped[WorkflowNodeType] = mapped_column(
        SAEnum(WorkflowNodeType, name="workflownodetype", values_callable=pg_enum_values)
    )
    status: Mapped[WorkflowStepStatus] = mapped_column(
        SAEnum(WorkflowStepStatus, name="workflowstepstatus", values_callable=pg_enum_values),
        default=WorkflowStepStatus.pending, index=True,
    )
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    form_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # Formular-Eingaben
    decision: Mapped[str | None] = mapped_column(String(60), nullable=True)  # gewählter Ausgang (Handle)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # Agent-/Auto-Action-Ergebnis
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
