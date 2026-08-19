"""Metric series: numbers with a timestamp that a flow writes along.

A flow used to see only the moment. "Battery 25 %" is meaningless on its own; only the
series of the last weeks says whether the device stops in two days or in two months. Exactly
those conclusions were impossible: the value arrived with the webhook, was poured into a
message and was gone.

Deliberately kept general and not tailored to battery levels: a series is a name, a unit and
a sequence of (moment, value). What is read from it (consumption per day, remaining runtime,
early warning) stands in the flow, not here.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class MetricSeries(TimestampMixin, Base):
    """A named series: akku.shelter, temperatur.serverraum."""
    __tablename__ = "metric_series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_metric_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Series belong to a human: they come into being from their flows and contain operating
    # data of their devices.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Last state; saves the overview a look into the points.
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the last early warning happened. Without this mark the warning "empty in 7 days"
    # would come again every day, and with a device that reports daily that would be the kind
    # of notification one mutes after three days.
    warned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # When it was last reported that this series has gone silent. A mark of its own beside
    # `warned_at`, because it is another fact with another expiry condition: the remaining
    # runtime warning expires on refilling, the silence on the next value at all. Merged,
    # one message would swallow the other.
    still_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MetricPoint(Base):
    """One measuring point. `context` records where it came from (device, run, event)."""
    __tablename__ = "metric_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("metric_series.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                            index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
