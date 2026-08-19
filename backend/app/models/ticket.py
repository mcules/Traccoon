import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import (
    BoardType, HoldReason, IssueTypeCategory, LinkType, Priority, SprintState,
    StatusCategory, TicketAgentStatus, pg_enum_values,
)


class IssueType(Base):
    __tablename__ = "issue_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icon: Mapped[str] = mapped_column(String(50), default="task")
    color: Mapped[str] = mapped_column(String(20), default="#4BADE8")
    category: Mapped[IssueTypeCategory] = mapped_column(
        SAEnum(IssueTypeCategory, name="issuetypecategory", values_callable=pg_enum_values),
        default=IssueTypeCategory.standard,
    )
    order: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowStatus(Base):
    __tablename__ = "workflow_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[StatusCategory] = mapped_column(
        SAEnum(StatusCategory, name="statuscategory", values_callable=pg_enum_values),
        default=StatusCategory.todo,
    )
    order: Mapped[int] = mapped_column(Integer, default=0)


class IssueCounter(Base):
    __tablename__ = "issue_counters"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[BoardType] = mapped_column(
        SAEnum(BoardType, name="boardtype", values_callable=pg_enum_values),
        default=BoardType.kanban,
    )


class BoardColumn(Base):
    __tablename__ = "board_columns"
    __table_args__ = (UniqueConstraint("board_id", "status_id", name="uq_board_column"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("boards.id", ondelete="CASCADE"), index=True)
    status_id: Mapped[int] = mapped_column(ForeignKey("workflow_statuses.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer, default=0)


class Sprint(TimestampMixin, Base):
    __tablename__ = "sprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("boards.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[SprintState] = mapped_column(
        SAEnum(SprintState, name="sprintstate", values_callable=pg_enum_values),
        default=SprintState.future,
    )
    start_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="#6B778C")


class IssueTag(Base):
    __tablename__ = "issue_tags"

    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Issue(TimestampMixin, Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Common artifact identity (title, project, state), the same construction as with the
    # hardware unit. This table stays the detail table of the ticket: board, sprint, plan,
    # agent, merge and test environment hang here.
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    type_id: Mapped[int] = mapped_column(ForeignKey("issue_types.id", ondelete="CASCADE"))
    status_id: Mapped[int] = mapped_column(ForeignKey("workflow_statuses.id", ondelete="CASCADE"), index=True)

    # AI lifecycle (orthogonal to the board column status_id)
    agent_status: Mapped[TicketAgentStatus | None] = mapped_column(
        SAEnum(TicketAgentStatus, name="ticketagentstatus", values_callable=pg_enum_values),
        nullable=True,
    )
    hold_reason: Mapped[HoldReason | None] = mapped_column(
        SAEnum(HoldReason, name="holdreason", values_callable=pg_enum_values), nullable=True,
    )

    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, name="priority", values_callable=pg_enum_values), default=Priority.medium,
    )
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    assignee_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Agent assignment (core feature): NULL = no AI access
    assigned_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), nullable=True
    )
    sprint_id: Mapped[int | None] = mapped_column(
        ForeignKey("sprints.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Hardware reference (TRA-25): the ticket hangs off a unit. A unit collects several
    # tickets over its lifetime (defect, maintenance, rebuild), hence n:1, not 1:1.
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("hardware_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    story_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[str] = mapped_column(String(64), default="")
    due_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    night_task: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Agent execution
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exec_agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    continuation_count: Mapped[int] = mapped_column(Integer, default=0)
    # Cap window: the maximum run id at the time of the last plan approval. The runaway brake
    # (dispatcher._process) counts only runs and tokens with Run.id greater than this value,
    # so that old failed attempts (429 aborts for instance) do not count against legitimate
    # new work. NULL = all runs count. Cost and statistics aggregation (cost.py) is unaffected.
    cap_baseline_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Running lifecycle instance (workflow_instances.id). Deliberately without an FK: the
    # instance points at the ticket in turn, and a real FK would give a table cycle. The
    # instance is the truth about the flow, `agent_status` only the projection for board,
    # Telegram and drawer.
    workflow_instance_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    agent_working: Mapped[bool] = mapped_column(Boolean, default=False)
    merge_status: Mapped[str] = mapped_column(String(20), default="")
    merge_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merged_into: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merge_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Counter for consecutive merge conflict rounds at the accept. Brakes the
    # accept-conflict-approved-redispatch loop: after too many rounds it goes to hold (human).
    merge_conflict_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Correction rounds of the review gate used up. Until now it stood only as a loop counter
    # in the worker process, and a restart in the middle of round 2 began at 1 again (TRA-32
    # on 2026-08-07: check, correct, restart, check, correct …, with no end in sight). A
    # counter that is meant to enforce a limit belongs on the ticket.
    review_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Splitting
    parent_ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), nullable=True
    )
    split_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Testenv
    testenv_status: Mapped[str] = mapped_column(String(20), default="")
    testenv_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    testenv_container: Mapped[str | None] = mapped_column(String(200), nullable=True)
    testenv_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    testenv_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    testenv_env_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet

    # Herkunft (Webhook-Idempotenz)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Archive: archived tickets appear neither on the board nor in the list.
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Blocker(Base):
    """The structured ask_human question, for the resume."""
    __tablename__ = "blockers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TicketFileChange(Base):
    """Files the agent changed (from the git diff)."""
    __tablename__ = "ticket_file_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="modified")  # added|modified|deleted
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    uploader_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueLink(Base):
    __tablename__ = "issue_links"
    __table_args__ = (UniqueConstraint("source_id", "target_id", "type", name="uq_issue_link"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    type: Mapped[LinkType] = mapped_column(
        SAEnum(LinkType, name="linktype", values_callable=pg_enum_values), default=LinkType.relates,
    )


class Comment(TimestampMixin, Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_label: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="agent")  # agent | internal


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SavedFilter(TimestampMixin, Base):
    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    query_json: Mapped[str] = mapped_column(Text, default="{}")
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
