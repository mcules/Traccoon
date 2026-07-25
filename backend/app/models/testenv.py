"""Testumgebung für einen beliebigen Branch (ABC-18).

Ticket-Testumgebungen leben in Feldern am `Issue` (testenv_status/url/container/port/error);
für frei gewählte Branches gibt es keinen Träger — daher diese Tabelle. Sie hält nur
aktive bzw. fehlerhafte Umgebungen: beim Stoppen wird die Zeile gelöscht.
"""
import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class BranchTestenv(TimestampMixin, Base):
    __tablename__ = "branch_testenvs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="starting")  # starting|running|error
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    container: Mapped[str | None] = mapped_column(String(200), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
