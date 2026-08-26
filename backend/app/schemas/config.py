import datetime as dt

from pydantic import BaseModel, Field

from ..models.enums import IssueTypeCategory, SprintState, StatusCategory


class IssueTypeOut(BaseModel):
    id: int
    name: str
    icon: str
    color: str
    category: IssueTypeCategory
    order: int
    model_config = {"from_attributes": True}


class IssueTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str = "task"
    color: str = "#4BADE8"
    category: IssueTypeCategory = IssueTypeCategory.standard


class StatusOut(BaseModel):
    id: int
    name: str
    category: StatusCategory
    order: int
    model_config = {"from_attributes": True}


class StatusIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: StatusCategory = StatusCategory.todo


class ColumnOut(BaseModel):
    status_id: int
    order: int
    model_config = {"from_attributes": True}


class BoardOut(BaseModel):
    id: int
    name: str
    columns: list[ColumnOut]


class SprintOut(BaseModel):
    id: int
    board_id: int
    name: str
    goal: str | None
    state: SprintState
    start_date: dt.datetime | None
    end_date: dt.datetime | None
    model_config = {"from_attributes": True}


class SprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal: str | None = None
    state: SprintState = SprintState.future
    start_date: dt.datetime | None = None
    end_date: dt.datetime | None = None


class TagOut(BaseModel):
    id: int
    name: str
    color: str
    model_config = {"from_attributes": True}


class MemberLite(BaseModel):
    user_id: int
    username: str
    # The name of this person in this project (alias before account name).
    display_name: str
    role: str
    ai_assign: bool
    status: str


class ProjectMeta(BaseModel):
    types: list[IssueTypeOut]
    statuses: list[StatusOut]
    boards: list[BoardOut]
    sprints: list[SprintOut]
    members: list[MemberLite]
    my_ai_assign: bool
    my_role: str
