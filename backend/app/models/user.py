import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import GlobalRole, UserStatus, pg_enum_values

SYSTEM_USER_ID = 1


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#0052CC")
    theme: Mapped[str] = mapped_column(String(10), default="dark")

    global_role: Mapped[GlobalRole] = mapped_column(
        SAEnum(GlobalRole, name="globalrole", values_callable=pg_enum_values),
        default=GlobalRole.user, nullable=False,
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="userstatus", values_callable=pg_enum_values),
        default=UserStatus.pending, nullable=False,
    )

    # Persönliche Secrets/Settings (verschlüsselt gespeichert)
    claude_oauth_token_enc: Mapped[str] = mapped_column(String, default="")
    codex_token_enc: Mapped[str] = mapped_column(String, default="")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # MCP-Gateway (MCPJungle) pro User — harte serverseitige Tool-Trennung.
    mcp_group: Mapped[str] = mapped_column(String(120), default="")          # MCPJungle-Gruppe
    mcp_token_enc: Mapped[str] = mapped_column(String, default="")           # Fernet, Client-Token
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)            # erlaubte Server (Doku/UI)

    max_runners: Mapped[int] = mapped_column(Integer, default=3)
    onboarded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    default_project_view: Mapped[str] = mapped_column(String(10), default="board")

    # Nacht-Fenster (Europe/Berlin) für night_task-Tickets
    night_start_hour: Mapped[int] = mapped_column(Integer, default=22)
    night_end_hour: Mapped[int] = mapped_column(Integer, default=6)
    night_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    night_override: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def is_system(self) -> bool:
        return self.id == SYSTEM_USER_ID
