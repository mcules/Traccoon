"""The configuration audit: what a scan over the agent configurations found.

This used to live in the generic plugin tables — a finding was a JSON row in `plugin_data`,
a run another one, and the breakdown per configuration a JSON string inside that. It worked,
and it was half a feature: the collector sat in a stack of its own outside the repository,
the numbers could not be queried (only fetched whole and taken apart in the browser), and
nothing here knew what a severity is.

So: three tables. A run, what it found per configuration, and the findings themselves. A
finding stands exactly once and carries its history in `first_seen`/`last_seen` — one row per
run and finding would be four figures after a month, and the list is read whole.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

# Worst first. The order is the one the interface reads in, and the one a sort follows.
SEVERITIES = ("critical", "high", "medium", "low", "info")

# What a person can do to a finding. `fixed` is not a decision but an observation: the scan
# no longer sees it.
STATUSES = ("open", "ignored", "fixed")


class ShieldRun(Base):
    """One pass of the scanner over all configurations."""
    __tablename__ = "agentshield_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who asked for it: `job`, `hand` (somebody pressed the button), or the name of a flow.
    trigger: Mapped[str] = mapped_column(String(40), default="job")
    configs: Mapped[int] = mapped_column(Integer, default=0)
    findings: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    fixed_count: Mapped[int] = mapped_column(Integer, default=0)
    critical: Mapped[int] = mapped_column(Integer, default=0)
    high: Mapped[int] = mapped_column(Integer, default=0)
    medium: Mapped[int] = mapped_column(Integer, default=0)
    low: Mapped[int] = mapped_column(Integer, default=0)
    info: Mapped[int] = mapped_column(Integer, default=0)


class ShieldRunConfig(Base):
    """What one configuration looked like in one run.

    A row per run and configuration, not a JSON blob on the run: this is the history the
    chart is drawn from, and "how did this stack move" has to be a query, not a hundred
    strings taken apart in the browser.
    """
    __tablename__ = "agentshield_run_configs"
    __table_args__ = (UniqueConstraint("run_id", "config", name="uq_shield_run_config"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agentshield_runs.id", ondelete="CASCADE"), index=True)
    config: Mapped[str] = mapped_column(String(200), index=True)
    grade: Mapped[str] = mapped_column(String(4), default="?")
    # Set when the scan of this one configuration broke. It then carries no counts, and that
    # is not the same as a clean configuration — hence the field and not a zero.
    error: Mapped[str] = mapped_column(String(300), default="")
    critical: Mapped[int] = mapped_column(Integer, default=0)
    high: Mapped[int] = mapped_column(Integer, default=0)
    medium: Mapped[int] = mapped_column(Integer, default=0)
    low: Mapped[int] = mapped_column(Integer, default=0)
    info: Mapped[int] = mapped_column(Integer, default=0)


class ShieldFinding(Base):
    """One finding, over all the runs that saw it.

    `key` is what makes a finding the same one across runs: configuration, rule, file and
    title — deliberately without the line and without an excerpt of the text. Both move as
    soon as somebody inserts a line above, and the finding would be new every day although
    nothing changed.
    """
    __tablename__ = "agentshield_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    config: Mapped[str] = mapped_column(String(200), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    file: Mapped[str] = mapped_column(String(300), default="")
    rule: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    first_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
