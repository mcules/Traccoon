"""Speicher: benannte Orte, an denen Datenreihen liegen.

Was ein **Ziel** (`models/destination.Destination`) fuer ausgehende Aufrufe ist, ist ein
**Speicher** fuer Daten: Der Name steht in der Reihe, die Zugangsdaten stehen an genau einer
Stelle, verschluesselt, und tauchen weder in Ablaeufen noch in Protokollen auf.

Der Sinn ist die Wahl: Ein paar tausend Messwerte gehoeren in diese Datenbank — zusammen mit
den Freigaben, an denen sie haengen. Millionen Punkte im Sekundentakt gehoeren in etwas, das
dafuer gebaut ist. Womit ein Mensch rechnet, sagt er beim Anlegen der Reihe; welcher Speicher
dazu passt, schlaegt `services/stores/wahl.py` vor.

Der Geltungsbereich ist der der Ziele: `project_id` gesetzt heisst nur dieses Projekt,
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

# Der eingebaute Speicher — diese Datenbank. Er wird beim Start angelegt, falls er fehlt, und
# laesst sich nicht loeschen: Ohne ihn haette eine frische Anlage nirgends hin.
INTERN = "intern"


class DataStore(TimestampMixin, Base):
    __tablename__ = "data_stores"
    __table_args__ = (
        # Ein Name je Geltungsbereich. NULL-Spalten gelten in Postgres als verschieden,
        # deshalb Teilindizes statt einer UniqueConstraint — wie bei den Zielen.
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
    # Was der Anschluss sonst braucht: {"org": …, "bucket": …, "schema": …}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Welche Datenarten er traegt. Eine reine Messwert-Datenbank traegt keinen Fliesstext.
    kinds: Mapped[list] = mapped_column(JSON, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ergebnis des letzten Verbindungstests — damit die Liste zeigen kann, was laeuft, ohne
    # bei jedem Blick alle Anschluesse anzufassen.
    last_check_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_msg: Mapped[str] = mapped_column(Text, default="")
