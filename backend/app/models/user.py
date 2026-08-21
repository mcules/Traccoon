import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin
from .enums import GlobalRole, UserStatus, pg_enum_values

SYSTEM_USER_ID = 1


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The e-mail is optional: login-less accounts (created by an admin without an e-mail)
    # are allowed. UNIQUE stays, because Postgres does not count several NULLs as a collision.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
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

    # Personal secrets and settings (stored encrypted)
    claude_oauth_token_enc: Mapped[str] = mapped_column(String, default="")
    codex_token_enc: Mapped[str] = mapped_column(String, default="")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which way this person gets messages when the sender names none. The way belongs to the
    # person, not to the message: whoever triggers a notification rarely knows whether the
    # recipient uses Telegram at all.
    notify_default: Mapped[str] = mapped_column(String(20), default="telegram")  # telegram|email|ziel
    # Different address for notifications; empty = the login address (`email`).
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Channel "destination": the call goes to this destination (base URL and login stand
    # there). That makes every service reachable that accepts a URL — ntfy, Matrix, Gotify, a
    # bot of one's own — without Traccoon having to know it. A new messenger is thereby an
    # entry under "destinations" and no longer a code change.
    notify_destination_id: Mapped[int | None] = mapped_column(
        ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True)
    # UI language. German is the source language of the shipped catalogs, everything else
    # is a translation, so an unknown value simply falls back to it.
    locale: Mapped[str] = mapped_column(String(10), default="en")
    # Timezone of this person (IANA, e.g. "Europe/Berlin"). It decides what "8 o'clock" means:
    # in the UI, in the night window and in the schedule of their jobs. Without it the server
    # computed in UTC and in a hard-wired zone — a cron job "0 8 * * *" then ran at 10, and
    # nobody saw why.
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Berlin")
    # Which mailbox was open last. Stored on the person and not in the browser: whoever logs in
    # at the other machine in the evening wants to carry on where they left off.
    mail_last_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="SET NULL"), nullable=True)
    # Token an agent uses this person's MCP access with (bearer). It stands here encrypted and
    # is shown exactly once on creation — afterwards it can only be set anew. Whoever has it
    # sees the released mailboxes of this person.
    mail_mcp_token_enc: Mapped[str] = mapped_column(String, default="")

    # MCP-Gateway (MCPJungle) pro User — harte serverseitige Tool-Trennung.
    mcp_group: Mapped[str] = mapped_column(String(120), default="")          # MCPJungle-Gruppe
    mcp_token_enc: Mapped[str] = mapped_column(String, default="")           # Fernet, Client-Token
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)            # erlaubte Server (Doku/UI)

    # Memory folder in the Obsidian vault (ABC-30): below it the agents file their learned
    # insights as notes (Mensch.md, Agent-<rolle>.md, Projekt-<KEY>.md) and read them again at
    # the beginning of every run. Access runs over the MCP group of THIS user, so the memory
    # is necessarily personal. Empty = no memory (the function is off).
    vault_memory_path: Mapped[str] = mapped_column(String(500), default="")

    max_runners: Mapped[int] = mapped_column(Integer, default=3)
    # When the personal assistant reports over Telegram or the bell:
    #   needed = only when it reports itself, on errors and in the chat (default)
    #   always = every finished item · errors = errors only · never = not at all
    assistant_notify: Mapped[str] = mapped_column(String(10), default="needed")
    # Own process set: applies to ALL projects in which this user has the owner role (as far
    # as the project references no set of its own). NULL = the global default.
    workflow_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_sets.id", ondelete="SET NULL"), nullable=True
    )
    onboarded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    default_project_view: Mapped[str] = mapped_column(String(10), default="board")
    # How a ticket is opened on a left click: popup (drawer) or page (full page).
    ticket_open_mode: Mapped[str] = mapped_column(String(10), default="popup")
    # User specific block arrangement of the full ticket page: {"left":[...keys], "right":[...keys]}.
    ticket_layout: Mapped[dict] = mapped_column(JSON, default=dict)
    # Presentation of the PM chat: bubbles or cli (terminal look like the Claude Code CLI).
    # Applies globally to the user across all projects (ABC-21).
    pm_chat_style: Mapped[str] = mapped_column(String(10), default="bubbles")

    # Night window (Europe/Berlin) for night_task tickets
    night_start_hour: Mapped[int] = mapped_column(Integer, default=22)
    night_end_hour: Mapped[int] = mapped_column(Integer, default=6)
    night_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    night_override: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def is_system(self) -> bool:
        return self.id == SYSTEM_USER_ID
