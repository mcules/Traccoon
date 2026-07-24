"""Pydantic-Schemas der Workflow-Engine (Definitionen, Versionen, Instanzen).

Der Graph ist ein frei geformtes dict (React-Flow-nativ) — bewusst KEIN starres Schema,
damit der Editor beliebige node.data/Positionen tragen kann.
"""
import datetime as dt

from pydantic import BaseModel, Field

from ..models.enums import (
    WorkflowInstanceStatus, WorkflowNodeType, WorkflowStepStatus, WorkflowSubjectKind,
    WorkflowTokenState, WorkflowVersionStatus,
)


# ── Definitionen ─────────────────────────────────────────────────────────────

class WorkflowDefinitionCreate(BaseModel):
    project_id: int | None = None          # None = globale Vorlage
    key: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    subject_kind: WorkflowSubjectKind = WorkflowSubjectKind.standalone


class WorkflowDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    enabled: bool | None = None


class WorkflowDefinitionOut(BaseModel):
    id: int
    project_id: int | None
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
