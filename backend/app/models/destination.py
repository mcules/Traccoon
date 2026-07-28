"""Ziele: benannte externe Gegenstellen mit hinterlegter Anmeldung.

Gedacht wie die Destinations in der SAP BTP: das **Ziel** kennt Basis-URL und
Authentifizierung, der **Aufruf** (Prozess-Aktion, Job, Agenten-Werkzeug) nennt nur noch
den Namen plus Methode, Pfad-Ergänzung, Query, Header und Body. Zugangsdaten stehen damit
an genau einer Stelle, verschlüsselt, und tauchen weder in Prozessen noch in Logs auf.

Geltungsbereich wie bei den Prozess-Sätzen: `project_id` gesetzt → nur dieses Projekt;
`user_id` gesetzt → alle Projekte des Nutzers; beides NULL → systemweit. Aufgelöst wird
nach Namen in genau dieser Reihenfolge (siehe `services/destinations.resolve`).
"""
import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text as sa_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class Destination(TimestampMixin, Base):
    __tablename__ = "destinations"
    __table_args__ = (
        # Ein Name je Geltungsbereich. NULL-Spalten gelten in Postgres als verschieden,
        # deshalb partielle Indizes statt einer einzelnen UniqueConstraint.
        Index("uq_destination_global", "name", unique=True,
              postgresql_where=sa_text("user_id IS NULL AND project_id IS NULL"),
              sqlite_where=sa_text("user_id IS NULL AND project_id IS NULL")),
        Index("uq_destination_user", "user_id", "name", unique=True,
              postgresql_where=sa_text("user_id IS NOT NULL AND project_id IS NULL"),
              sqlite_where=sa_text("user_id IS NOT NULL AND project_id IS NULL")),
        Index("uq_destination_project", "project_id", "name", unique=True,
              postgresql_where=sa_text("project_id IS NOT NULL"),
              sqlite_where=sa_text("project_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # Slug für Aufrufe
    label: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)

    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # none | basic | bearer | api_key | hmac | oauth2_cc
    auth_type: Mapped[str] = mapped_column(String(20), default="none")

    # basic
    username: Mapped[str] = mapped_column(String(200), default="")
    # Fernet-verschlüsselt: Passwort · Bearer-Token · API-Key · HMAC-Secret · Client-Secret
    secret_enc: Mapped[str] = mapped_column(Text, default="")

    # api_key: Name und Ort des Schlüssels (Header oder Query-Parameter)
    api_key_name: Mapped[str] = mapped_column(String(120), default="X-API-Key")
    api_key_in: Mapped[str] = mapped_column(String(10), default="header")  # header | query

    # hmac: Signatur über den gesendeten Body
    hmac_header: Mapped[str] = mapped_column(String(120), default="X-Webhook-Signature")
    hmac_algo: Mapped[str] = mapped_column(String(20), default="sha256")
    # Präfix vor dem Hex-Digest. Bewusst LEER als Default — manche Gegenstellen (z. B.
    # Hermes) weisen ein „sha256="-Präfix ab.
    hmac_prefix: Mapped[str] = mapped_column(String(20), default="")

    # oauth2_cc (Client Credentials)
    oauth_token_url: Mapped[str] = mapped_column(String(1000), default="")
    oauth_client_id: Mapped[str] = mapped_column(String(300), default="")
    oauth_scope: Mapped[str] = mapped_column(String(500), default="")
    oauth_audience: Mapped[str] = mapped_column(String(500), default="")
    # Zwischengespeichertes Zugriffstoken (verschlüsselt) + Ablauf — spart einen
    # Token-Roundtrip je Aufruf.
    oauth_token_enc: Mapped[str] = mapped_column(Text, default="")
    oauth_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Bei jedem Aufruf mitgesendet (der Aufruf darf einzelne überschreiben).
    default_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=30)
    # Wie viel der Antwort ein Aufrufer (v. a. ein KI-Agent) höchstens zu sehen bekommt.
    # Bewusst je Ziel: der Standard schützt den Kontext, aber eine Gegenstelle, die ihre
    # Lage absichtlich in EINEM Abruf liefert (UniWar-Bot-API), wäre mit 4000 Zeichen
    # unbrauchbar — der Agent plante dann auf abgeschnittenem JSON.
    max_response_chars: Mapped[int] = mapped_column(Integer, default=4000)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Darf ein KI-Agent dieses Ziel über `http_call` nutzen? Standard: nein.
    allow_agents: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
