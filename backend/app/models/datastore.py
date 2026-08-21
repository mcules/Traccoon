"""Speicher: benannte Orte, an denen Datenreihen liegen.

What a **destination** (`models/destination.Destination`) is for outgoing calls, a **storage**
is for data: the name stands on the series, the credentials stand in exactly one place,
encrypted, and turn up neither in flows nor in logs.

The point is the choice: a few thousand measurements belong in this database — together with
the grants they hang on. Millions of points at a one-second beat belong in something built for
that. What a person reckons with they say when creating the series; which storage
dazu passt, schlaegt `services/stores/wahl.py` vor.

The scope is that of the destinations: `project_id` set means this project only,
`user_id` gesetzt heisst alle Projekte dieses Menschen, beides NULL heisst systemweit.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text as sa_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin

# The built-in storage — this database. It is created at startup if it is missing and cannot be
# deleted: without it a fresh installation would have nowhere to put anything.
INTERN = "intern"


class DataStore(TimestampMixin, Base):
    __tablename__ = "data_stores"
    __table_args__ = (
        # Ein Name je Geltungsbereich. NULL-Spalten gelten in Postgres als verschieden,
        # hence partial indexes instead of a UniqueConstraint — as with the destinations.
        Index("uq_datastore_global", "name", unique=True,
              postgresql_where=sa_text("user_id IS NULL AND project_id IS NULL"),
              sqlite_where=sa_text("user_id IS NULL AND project_id IS NULL")),
        Index("uq_datastore_user", "user_id", "name", unique=True,
              postgresql_where=sa_text("user_id IS NOT NULL AND project_id IS NULL"),
              sqlite_where=sa_text("user_id IS NOT NULL AND project_id IS NULL")),
        Index("uq_datastore_project", "project_id", "name", unique=True,
              postgresql_where=sa_text("project_id IS NOT NULL"),
              sqlite_where=sa_text("project_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)

    # intern | postgres | timescale | influx2
    kind: Mapped[str] = mapped_column(String(20), default=INTERN)
    url: Mapped[str] = mapped_column(String(1000), default="")
    # Fernet: Passwort einer fremden Datenbank bzw. Token einer Influx.
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    # What the connection otherwise needs: {"org": …, "bucket": …, "schema": …}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Welche Datenarten er traegt. Eine reine Messwert-Datenbank traegt keinen Fliesstext.
    kinds: Mapped[list] = mapped_column(JSON, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Result of the last connection test — so the list can show what runs without
    # bei jedem Blick alle Anschluesse anzufassen.
    last_check_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_msg: Mapped[str] = mapped_column(Text, default="")
