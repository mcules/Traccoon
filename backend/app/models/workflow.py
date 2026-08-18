import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint, func, text as sa_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import (
    WorkflowInstanceStatus, WorkflowNodeType, WorkflowSetScope, WorkflowStepStatus,
    WorkflowSubjectKind, WorkflowTokenState, WorkflowVersionStatus, pg_enum_values,
)


class WorkflowSet(TimestampMixin, Base):
    """Named set of process templates (at most one definition per slot).

    Resolution chain for a project (services/workflow_sets.resolve_definition): the project's
    own copy, then the set of the project, then the set of a project owner, then the global
    builtin set. Projects only reference a set; a copy comes into being on adjustment
    (copy-on-write) so that changes to the set take hold everywhere immediately.
    """
    __tablename__ = "workflow_sets"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_workflow_set_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[WorkflowSetScope] = mapped_column(
        SAEnum(WorkflowSetScope, name="workflowsetscope", values_callable=pg_enum_values),
        default=WorkflowSetScope.user, index=True,
    )
    # scope=user means owner; scope=global means NULL
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # The one shipped default set. It is updated idempotently at start
    # (services/workflow_seed.ensure_builtin_set) and must not be deleted.
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Revision of the shipped graphs; rises when the seed publishes new versions.
    builtin_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WorkflowDefinition(TimestampMixin, Base):
    """Logical workflow (container over versions). project_id NULL = global template."""
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_workflow_def_project_key"),
        # At most ONE active definition per slot per set respectively per project. Archived
        # (reset) copies stay so that running instances remain intact.
        Index("uq_workflow_def_set_slot", "set_id", "slot", unique=True,
              postgresql_where=sa_text("archived_at IS NULL"),
              sqlite_where=sa_text("archived_at IS NULL")),
        # ONE copy per project and slot, and in addition one per issue type. `COALESCE`,
        # because NULL values in a unique index would otherwise count as different and any
        # number of generic copies could appear.
        Index("uq_workflow_def_project_slot", "project_id", "slot",
              sa_text("COALESCE(issue_type_id, 0)"), unique=True,
              postgresql_where=sa_text("archived_at IS NULL"),
              sqlite_where=sa_text("archived_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The template set it belongs to (NULL = free or project-owned workflow).
    set_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_sets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Occupied slot (WorkflowSlot value); NULL with freely created workflows.
    slot: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # Reset project copies are archived instead of deleted (instances hang off them).
    # Does this flow apply only to one issue type? NULL = to all tickets of the project.
    # That lets a bug run a different lifecycle from a task.
    issue_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("issue_types.id", ondelete="CASCADE"), nullable=True, index=True)
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)  # slug, eindeutig pro Projekt
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    subject_kind: Mapped[WorkflowSubjectKind] = mapped_column(
        SAEnum(WorkflowSubjectKind, name="workflowsubjectkind", values_callable=pg_enum_values),
        default=WorkflowSubjectKind.standalone,
    )
    # Published version new instances point at. NULL = not published yet.
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WorkflowVersion(Base):
    """Immutable graph snapshot. Published versions must NOT be edited."""
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
    """Running execution of a workflow. version_id is pinned, so edits do not break instances."""
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
    # General artifact binding (supersedes hardware_asset_id; tickets follow later).
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[WorkflowInstanceStatus] = mapped_column(
        SAEnum(WorkflowInstanceStatus, name="workflowinstancestatus", values_callable=pg_enum_values),
        default=WorkflowInstanceStatus.running, index=True,
    )
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # variable context (guards read here)
    # Atomarer Claim gegen Doppel-Advance (Tick ↔ Event), analog Issue.agent_working.
    advancing: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)       # webhook | job | manual
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Idempotenz
    # subflow: back reference to the calling run. When this instance ends, it wakes the
    # waiting subflow step of the parent instance (found over parent_node_id) and delivers
    # its exit as a handle. Deliberately NO FK on workflow_step_runs: that would give a cycle
    # between the tables and break create_all.
    parent_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_instances.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowToken(Base):
    """Execution position. v1: exactly 1 active token per instance; the schema allows N (AND split later)."""
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
    """Append-only history plus persistence of responsible people, form, approval and agent result."""
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
    decision: Mapped[str | None] = mapped_column(String(60), nullable=True)  # chosen exit (handle)
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
    # When the engine translated this (finished) step into an edge. Without this stamp a back
    # edge onto the same waiting node (the continuation loop in the ticket lifecycle) would
    # route again immediately on re-entry instead of executing the node anew, and the run
    # would go round in circles forever without ever starting an agent.
    routed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
