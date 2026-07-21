import datetime as dt

from pydantic import BaseModel, Field

from ..models.enums import Priority, TicketAgentStatus


class IssueCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    description: str | None = None
    type_id: int | None = None      # None = Default-Typ des Projekts
    status_id: int | None = None    # None = erster Status
    priority: Priority = Priority.medium
    assignee_user_id: int | None = None
    parent_id: int | None = None
    sprint_id: int | None = None
    story_points: int | None = None


class IssueUpdate(BaseModel):
    summary: str | None = None
    description: str | None = None
    type_id: int | None = None
    status_id: int | None = None
    priority: Priority | None = None
    assignee_user_id: int | None = None
    parent_id: int | None = None
    sprint_id: int | None = None
    story_points: int | None = None


class AssignAgentIn(BaseModel):
    agent: str = "project_manager"


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
    rank: str
    agent_working: bool
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
    position: int = 0  # 0-basierte Position innerhalb der Zielspalte


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
