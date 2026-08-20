import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class Skill(Base):
    """Versioned prompt building block. A new version is a new row; `active` marks the used one."""
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(150), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(20), default="global")  # global | project
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    autostart: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class McpServer(TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_mcp_server"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), default="")
    transport: Mapped[str] = mapped_column(String(20), default="stdio")  # stdio | http | sse
    command: Mapped[str] = mapped_column(String(500), default="")
    args: Mapped[list] = mapped_column(JSON, default=list)
    env_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet-JSON
    url: Mapped[str] = mapped_column(String(500), default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    tools: Mapped[list] = mapped_column(JSON, default=list)          # discovered
    disabled_tools: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Variable schema: named slots the instances fill in (an auth header for instance).
    variables: Mapped[list] = mapped_column(JSON, default=list)      # [{key,label,secret,required}]


class McpInstance(Base):
    """Agent-owned, filled-in instance of an McpServer (variables to values).

    values_enc = Fernet-JSON {var_key: value}; nie im API-Response ausgeliefert.
    """
    __tablename__ = "mcp_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent_definitions.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    values_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plugin(TimestampMixin, Base):
    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="")
    entry: Mapped[str] = mapped_column(String(200), default="index.html")
    table_schema: Mapped[dict] = mapped_column(JSON, default=dict)   # {table: {col: type}} — Whitelist
    allowed_hosts: Mapped[list] = mapped_column(JSON, default=list)  # SSRF-Allowlist
    contributions: Mapped[list] = mapped_column(JSON, default=list)
    # Welche Traccoon-Daten das Plugin lesen will (aus dem Manifest) und was ein Admin davon
    # freigegeben hat. Deny-default wie bei den Werkzeugen der Agenten: Das Manifest fordert,
    # ein Mensch erlaubt. Ohne Haken antwortet die Bruecke mit einer Sperre.
    liest: Mapped[list] = mapped_column(JSON, default=list)
    liest_erlaubt: Mapped[list] = mapped_column(JSON, default=list)
    # Zusaetzliche CSP-Quellen aus dem Manifest, etwa der Kachelserver einer Karte. Ohne das
    # laedt ein Plugin nur eigene Dateien, denn die Vorgabe verbietet jede fremde Herkunft.
    csp: Mapped[dict] = mapped_column(JSON, default=dict)
    service_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    all_users: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_user_ids: Mapped[list] = mapped_column(JSON, default=list)


class PluginData(Base):
    """Generic plugin data row (table CRUD without dynamic DDL).

    row = JSON, validiert gegen Plugin.table_schema. user_id gesetzt = privat.
    """
    __tablename__ = "plugin_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"), index=True)
    table_name: Mapped[str] = mapped_column(String(120), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    row: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PluginFile(Base):
    __tablename__ = "plugin_files"
    __table_args__ = (UniqueConstraint("plugin_id", "path", name="uq_plugin_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer, default=0)
