import datetime as dt

from pydantic import BaseModel, Field, field_validator

from ..models.enums import Priority, TicketAgentStatus


class IssueCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = None
    type_id: int | None = None      # None = the default type of the project
    status_id: int | None = None    # None = erster Status
    priority: Priority = Priority.medium
    # Person assignment NOT here: only over POST /issues/{key}/assignee (set_assignee),
    # because there the membership is validated respectively a placeholder is created. A
    # field here would bypass that protection.
    parent_id: int | None = None
    sprint_id: int | None = None
    story_points: int | None = None
    asset_id: int | None = None     # Hardware-Bezug (ABC-25)


class IssueUpdate(BaseModel):
    summary: str | None = None
    description: str | None = None
    type_id: int | None = None
    status_id: int | None = None
    priority: Priority | None = None
    # Person assignment NOT here: only over POST/DELETE /issues/{key}/assignee.
    parent_id: int | None = None
    sprint_id: int | None = None
    story_points: int | None = None
    asset_id: int | None = None     # Hardware-Bezug (ABC-25)


class AssignAgentIn(BaseModel):
    agent: str = "project_manager"


class AssigneeIn(BaseModel):
    """Person assignment: either an existing user (user_id) OR a new person by name
    (display_name); for the latter a placeholder account is created."""
    user_id: int | None = None
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("display_name must not be empty")
        return v


class IssueOut(BaseModel):
    id: int
    key: str
    number: int
    project_id: int
    type_id: int
    status_id: int
    agent_status: TicketAgentStatus | None
    hold_reason: str | None
    priority: Priority
    summary: str
    description: str | None
    reporter_id: int
    assignee_user_id: int | None
    assigned_agent: str | None
    assigned_by_user_id: int | None
    assigned_at: dt.datetime | None
    plan: str | None
    parent_id: int | None
    parent_ticket_id: int | None = None
    split_order: int | None = None
    sprint_id: int | None
    story_points: int | None
    asset_id: int | None = None   # hardware reference; the label is resolved by the frontend
    rank: str
    agent_working: bool
    # Running lifecycle process (the truth about the flow; agent_status is the projection of
    # it). NULL = none is running for this ticket right now.
    workflow_instance_id: int | None = None
    # Common artifact identity; the free fields hang off it.
    artifact_id: int | None = None
    archived: bool = False
    archived_at: dt.datetime | None = None
    testenv_status: str | None = None
    testenv_url: str | None = None
    testenv_error: str | None = None
    resolved_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class MoveIn(BaseModel):
    status_id: int
    position: int = 0  # zero based position within the target column


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = "#6B778C"


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    kind: str = "agent"  # agent | internal


class CommentOut(BaseModel):
    id: int
    issue_id: int
    author_id: int | None
    author_label: str
    body: str
    kind: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}
