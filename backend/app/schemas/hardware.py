import datetime as dt

from pydantic import BaseModel, Field

from ..models.enums import LocationType, PurchaseStatus


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: LocationType = LocationType.other
    parent_id: int | None = None
    project_id: int | None = None
    notes: str | None = None


class LocationOut(BaseModel):
    id: int
    name: str
    type: LocationType
    parent_id: int | None
    project_id: int | None
    full_path: str
    notes: str | None
    # Common artifact identity; the free fields hang off it.
    artifact_id: int | None = None
    model_config = {"from_attributes": True}


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    notes: str | None = None


class ModelOut(BaseModel):
    id: int
    name: str
    category: str | None
    manufacturer: str | None
    model: str | None
    notes: str | None
    model_config = {"from_attributes": True}


class AssetIn(BaseModel):
    model_id: int
    project_id: int | None = None
    issue_id: int | None = None
    serial_number: str | None = None
    purchase_status: PurchaseStatus = PurchaseStatus.planned
    location_id: int | None = None
    cost: float | None = None
    currency: str = "EUR"
    vendor: str | None = None
    notes: str | None = None


class AssetUpdate(BaseModel):
    project_id: int | None = None
    serial_number: str | None = None
    purchase_status: PurchaseStatus | None = None
    location_id: int | None = None
    cost: float | None = None
    vendor: str | None = None
    notes: str | None = None


class AssetOut(BaseModel):
    id: int
    model_id: int
    project_id: int | None
    issue_id: int | None
    serial_number: str | None
    purchase_status: PurchaseStatus
    location_id: int | None
    cost: float | None
    currency: str
    vendor: str | None
    notes: str | None
    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: int
    name: str
    order: int
    assignee_id: int | None
    status: str
    note: str | None
    completed_at: dt.datetime | None
    completed_by_id: int | None
    model_config = {"from_attributes": True}


class WorkflowStepIn(BaseModel):
    name: str
    order: int = 0
    # AssigneeSpec of the workflow engine: {"mode": user|role|context|reporter, ...}
    assignee: dict = {}


class WorkflowStepOut(BaseModel):
    id: int
    name: str
    order: int
    assignee: dict = {}
    model_config = {"from_attributes": True}


class WorkflowIn(BaseModel):
    steps: list[WorkflowStepIn]


class StepComplete(BaseModel):
    next_assignee: int | None = None
    note: str | None = None
