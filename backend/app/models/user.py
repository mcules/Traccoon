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
    # E-Mail optional: login-lose Konten (Admin-Anlage ohne E-Mail) sind erlaubt.
    # UNIQUE bleibt — Postgres wertet mehrere NULL nicht als Kollision.
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

    # Persönliche Secrets/Settings (verschlüsselt gespeichert)
    claude_oauth_token_enc: Mapped[str] = mapped_column(String, default="")
    codex_token_enc: Mapped[str] = mapped_column(String, default="")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Auf welchem Weg dieser Mensch Nachrichten bekommt, wenn der Absender keinen nennt.
    # Der Weg gehört zur Person, nicht zur Nachricht: wer eine Benachrichtigung auslöst,
    # weiß selten, ob der Empfänger Telegram überhaupt benutzt.
    notify_default: Mapped[str] = mapped_column(String(20), default="telegram")  # telegram|email
    # Abweichende Adresse für Benachrichtigungen; leer = die Anmelde-Adresse (`email`).
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # UI language. German is the source language of the shipped catalogs, everything else
    # is a translation, so an unknown value simply falls back to it.
    locale: Mapped[str] = mapped_column(String(10), default="de")

    # MCP-Gateway (MCPJungle) pro User — harte serverseitige Tool-Trennung.
    mcp_group: Mapped[str] = mapped_column(String(120), default="")          # MCPJungle-Gruppe
    mcp_token_enc: Mapped[str] = mapped_column(String, default="")           # Fernet, Client-Token
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)            # erlaubte Server (Doku/UI)

    # Gedächtnis-Ordner im Obsidian-Vault (ABC-30): darunter legen die Agenten ihre gelernten
    # Erkenntnisse als Notizen ab (Mensch.md, Agent-<rolle>.md, Projekt-<KEY>.md) und lesen sie
    # zu Beginn jedes Laufs wieder. Der Zugriff läuft über die MCP-Gruppe DIESES Nutzers, das
    # Gedächtnis ist also zwingend persönlich. Leer = kein Gedächtnis (Funktion aus).
    vault_memory_path: Mapped[str] = mapped_column(String(500), default="")

    max_runners: Mapped[int] = mapped_column(Integer, default=3)
    # Wann der persönliche Assistent per Telegram/Glocke meldet:
    #   needed = nur wenn er selbst meldet, bei Fehlern und im Chat (Default)
    #   always = jedes erledigte Item · errors = nur Fehler · never = gar nicht
    assistant_notify: Mapped[str] = mapped_column(String(10), default="needed")
    # Eigener Prozess-Satz: gilt für ALLE Projekte, in denen dieser Nutzer die Owner-Rolle
    # hat (sofern das Projekt keinen eigenen Satz referenziert). NULL = globaler Standard.
    workflow_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_sets.id", ondelete="SET NULL"), nullable=True
    )
    onboarded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    default_project_view: Mapped[str] = mapped_column(String(10), default="board")
    # Wie ein Ticket per Linksklick geöffnet wird: popup (Drawer) oder page (volle Seite).
    ticket_open_mode: Mapped[str] = mapped_column(String(10), default="popup")
    # Nutzerspezifische Block-Anordnung der vollen Ticket-Seite: {"left":[...keys], "right":[...keys]}.
    ticket_layout: Mapped[dict] = mapped_column(JSON, default=dict)
    # Darstellung des PM-Chats: bubbles (Sprechblasen) oder cli (Terminal-Look wie die
    # Claude-Code-CLI). Gilt global für den Nutzer über alle Projekte (ABC-21).
    pm_chat_style: Mapped[str] = mapped_column(String(10), default="bubbles")

    # Nacht-Fenster (Europe/Berlin) für night_task-Tickets
    night_start_hour: Mapped[int] = mapped_column(Integer, default=22)
    night_end_hour: Mapped[int] = mapped_column(Integer, default=6)
    night_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    night_override: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def is_system(self) -> bool:
        return self.id == SYSTEM_USER_ID
