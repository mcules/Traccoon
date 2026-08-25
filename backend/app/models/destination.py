"""Destinations: named external counterparts with a stored login.

Thought of like the destinations in SAP BTP: the **destination** knows the base URL and the
authentication, and the **call** (process action, job, agent tool) only names the name plus
method, path addition, query, headers and body. Credentials therefore stand in exactly one
place, encrypted, and appear neither in processes nor in logs.

The scope is as with the process sets: `project_id` set means this project only; `user_id`
set means all projects of the user; both NULL means system wide. Resolution happens by name
in exactly this order (see `services/destinations.resolve`).
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
        # One name per scope. NULL columns count as different in Postgres, which is why
        # there are partial indexes instead of a single UniqueConstraint.
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
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # slug for calls
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
    # Fernet encrypted: password · bearer token · API key · HMAC secret · client secret
    secret_enc: Mapped[str] = mapped_column(Text, default="")

    # api_key: name and place of the key (header or query parameter)
    api_key_name: Mapped[str] = mapped_column(String(120), default="X-API-Key")
    api_key_in: Mapped[str] = mapped_column(String(10), default="header")  # header | query

    # hmac: signature over the sent body
    hmac_header: Mapped[str] = mapped_column(String(120), default="X-Webhook-Signature")
    hmac_algo: Mapped[str] = mapped_column(String(20), default="sha256")
    # Prefix before the hex digest. Deliberately EMPTY as the default: some counterparts
    # (some of them) reject a `sha256=` prefix.
    hmac_prefix: Mapped[str] = mapped_column(String(20), default="")

    # oauth2_cc (Client Credentials)
    oauth_token_url: Mapped[str] = mapped_column(String(1000), default="")
    oauth_client_id: Mapped[str] = mapped_column(String(300), default="")
    oauth_scope: Mapped[str] = mapped_column(String(500), default="")
    oauth_audience: Mapped[str] = mapped_column(String(500), default="")
    # Cached access token (encrypted) plus expiry; saves a round trip
    # Token-Roundtrip je Aufruf.
    oauth_token_enc: Mapped[str] = mapped_column(Text, default="")
    oauth_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Sent along with every call (a call may override individual ones).
    default_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_sec: Mapped[int] = mapped_column(Integer, default=30)
    # How much of the answer a caller (an AI agent above all) gets to see at most.
    # Deliberately per destination: the default protects the context, but a counterpart that
    # deliberately delivers its whole state in ONE call (a game bot API) would be
    # unusable with 4000 characters, and the agent would plan on truncated JSON.
    max_response_chars: Mapped[int] = mapped_column(Integer, default=4000)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # May an AI agent use this destination over `http_call`? Default: no.
    allow_agents: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
