"""Pydantic schemas of the workflow engine (definitions, versions, instances).

The graph is a freely shaped dict (React Flow native), deliberately NO rigid schema, so that
the editor can carry arbitrary node.data and positions.
"""
import datetime as dt

from pydantic import BaseModel, Field

from ..models.enums import (
    WorkflowInstanceStatus, WorkflowNodeType, WorkflowSetScope, WorkflowStepStatus,
    WorkflowSubjectKind, WorkflowTokenState, WorkflowVersionStatus,
)


# ── Definitionen ─────────────────────────────────────────────────────────────

class WorkflowDefinitionCreate(BaseModel):
    project_id: int | None = None          # None = globale Vorlage
    key: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    subject_kind: WorkflowSubjectKind = WorkflowSubjectKind.standalone
    # Instead of start plus end, a finished flow to rebuild right away (services/workflow_templates).
    template: str | None = None


class WorkflowDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    enabled: bool | None = None


class WorkflowDefinitionOut(BaseModel):
    id: int
    project_id: int | None
    set_id: int | None = None
    slot: str | None = None
    # Set when this flow applies only to one issue type (bug is not task).
    issue_type_id: int | None = None
    archived_at: dt.datetime | None = None
    key: str
    name: str
    description: str
    subject_kind: WorkflowSubjectKind
    current_version_id: int | None
    enabled: bool
    created_by: int | None
    created_at: dt.datetime
    updated_at: dt.datetime
    model_config = {"from_attributes": True}


# ── Process sets ─────────────────────────────────────────────────────────────

class WorkflowSetOut(BaseModel):
    id: int
    scope: WorkflowSetScope
    user_id: int | None
    key: str
    name: str
    description: str
    is_builtin: bool
    model_config = {"from_attributes": True}


class WorkflowSetCreate(BaseModel):
    """A new set, always as a copy of a template (default: the global standard)."""
    name: str = Field(default="", max_length=200)
    source_set_id: int | None = None


class SlotOut(BaseModel):
    """One flow slot of a project including the origin of the applicable graph."""
    slot: str
    name: str
    description: str
    subject_kind: str
    origin: str                     # project | user | global | builtin | none
    set_id: int | None = None
    set_name: str | None = None
    definition_id: int | None = None
    definition_name: str | None = None
    published: bool = False
    customizable: bool = True
    # Flows that apply to one issue type only (bug is not task).
    per_issue_type: list[dict] = []


# ── Versionen ────────────────────────────────────────────────────────────────

class WorkflowVersionUpdate(BaseModel):
    graph: dict
    notes: str | None = None


class WorkflowVersionOut(BaseModel):
    id: int
    definition_id: int
    version: int
    graph: dict
    status: WorkflowVersionStatus
    notes: str
    created_by: int | None
    created_at: dt.datetime
    published_at: dt.datetime | None
    model_config = {"from_attributes": True}


class ValidateOut(BaseModel):
    ok: bool
    errors: list[str] = []


# ── Instanzen ────────────────────────────────────────────────────────────────

class InstanceCreate(BaseModel):
    subject_kind: WorkflowSubjectKind = WorkflowSubjectKind.standalone
    issue_id: int | None = None
    hardware_asset_id: int | None = None
    context: dict | None = None


class StepCompleteIn(BaseModel):
    form_data: dict | None = None
    next_assignee: int | None = None


class ApproveIn(BaseModel):
    reason: str | None = None


class RejectIn(BaseModel):
    reason: str = Field(min_length=1)


class TokenLite(BaseModel):
    id: int
    node_id: str
    state: WorkflowTokenState
    waiting_for: str | None = None
    model_config = {"from_attributes": True}


class StepRunOut(BaseModel):
    id: int
    instance_id: int
    node_id: str
    node_type: WorkflowNodeType
    status: WorkflowStepStatus
    assignee_user_id: int | None
    form_data: dict | None
    decision: str | None
    result: dict | None
    error: str | None
    entered_at: dt.datetime
    completed_at: dt.datetime | None
    completed_by: int | None
    model_config = {"from_attributes": True}


class InstanceOut(BaseModel):
    id: int
    definition_id: int
    version_id: int
    project_id: int | None
    subject_kind: WorkflowSubjectKind
    issue_id: int | None
    hardware_asset_id: int | None
    status: WorkflowInstanceStatus
    context: dict
    error: str | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    tokens: list[TokenLite] = []
    steps: list[StepRunOut] = []
    graph: dict = {}


class WorkflowTaskLite(BaseModel):
    step_id: int
    instance_id: int
    definition_name: str
    node_id: str
    node_type: WorkflowNodeType
    node_config: dict
    project_id: int | None
    project_key: str | None
    subject_kind: WorkflowSubjectKind
    issue_key: str | None
    entered_at: dt.datetime
