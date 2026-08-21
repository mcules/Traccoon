"""Stores: texts a flow writes and that one wants to look at later.

A flow had nowhere to put its text. The review an agent writes every morning ended up in the
output field of a job run — truncated at 20,000 characters, without a heading, without a view,
and findable only if one knew which run it was. The report about it pointed at a page that
never existed.

Built like the metric series and for the same reason: a store is a name and a sequence of
versions. What stands in it (a review, a report, a log, a note) and what follows from it the
flow knows — here stands only that it is kept.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class DocSeries(TimestampMixin, Base):
    """Eine benannte Ablage: ki-tech-news, wochenbericht, serverraum-protokoll."""
    __tablename__ = "doc_series"
    __table_args__ = (UniqueConstraint("owner_user_id", "key", name="uq_doc_series_owner_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stores belong to a person: they come out of their flows and hold what the agents of
    # those flows wrote for them.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # How many versions are kept. A daily review would be 365 versions after a year, of which
    # nobody looks at more than the last few dozen.
    keep: Mapped[int] = mapped_column(Integer, default=60)
    # Latest state; saves the overview a look into the versions.
    last_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_title: Mapped[str] = mapped_column(String(300), default="")


class DocEntry(Base):
    """One version. `context` records where it came from (a flow, a run, a job)."""
    __tablename__ = "doc_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("doc_series.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                            index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # markdown | text. No HTML: what goes in here comes from a model, and HTML from a model
    # would be a foreign page inside our own UI.
    format: Mapped[str] = mapped_column(String(20), default="markdown")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
