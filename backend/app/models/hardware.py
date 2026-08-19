import datetime as dt

from sqlalchemy import (
    DateTime, Enum as SAEnum, ForeignKey, Integer, JSON, Numeric, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import LocationType, PurchaseStatus, pg_enum_values


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[LocationType] = mapped_column(
        SAEnum(LocationType, name="locationtype", values_callable=pg_enum_values),
        default=LocationType.other,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Optional: hang the location firmly off a project (NOT mandatory; many locations stay
    # project-less or global, and only the associated assets carry a project_id).
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_path: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HardwareModel(TimestampMixin, Base):
    __tablename__ = "hardware_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HardwareAsset(TimestampMixin, Base):
    __tablename__ = "hardware_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Common identity of all artifacts: state, project and title stand there, and processes,
    # references and grants point at it. This table holds only the hardware specific details.
    # (model, place, cost, serial number, warranty).
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True, unique=True, index=True
    )
    model_id: Mapped[int] = mapped_column(ForeignKey("hardware_models.id", ondelete="RESTRICT"), index=True)
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )  # NULL = Vorrat/Lager
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # State: authoritative is `artifacts.status_key`. This column is written along as long as
    # the interface and the filters run on it (changeover in stages).
    purchase_status: Mapped[PurchaseStatus] = mapped_column(
        SAEnum(PurchaseStatus, name="purchasestatus", values_callable=pg_enum_values),
        default=PurchaseStatus.planned, index=True,
    )
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    install_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warranty_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HardwareWorkflow(TimestampMixin, Base):
    __tablename__ = "hardware_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )


class HardwareWorkflowStep(Base):
    __tablename__ = "hardware_workflow_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("hardware_workflows.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    # Who is responsible for this step (ABC-26)? Same shape as the workflow AssigneeSpec.
    # {"mode": user|role|context|reporter, ...}. Empty = nobody prefilled.
    assignee: Mapped[dict] = mapped_column(JSON, default=dict)


class HardwareAssetStep(Base):
    __tablename__ = "hardware_asset_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("hardware_assets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | DONE
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
