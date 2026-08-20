"""Mail-Konten und Identitäten einer Person.

Der Unterschied zum `imap-mcp`: Dort stehen die Konten in der Umgebung des Servers, gelten
für alle und können nur lesen. Was hier steht, gehört einer Person, wird von ihr gepflegt und
trägt beide Wege — IMAP zum Lesen, SMTP zum Senden.

Passwörter liegen verschlüsselt (`core.security.encrypt_secret`), wie Provider-Tokens und der
Tresor. `auth_type` ist heute immer `password`; die Spalten für OAuth stehen schon da, damit
Gmail und Microsoft später ohne Wanderung der Daten nachrüstbar sind.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class MailAccount(TimestampMixin, Base):
    __tablename__ = "mail_accounts"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_mail_account_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Kurzname in der Oberfläche und im Kontext eines Ablaufs ("privat", "vorstand").
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    imap_host: Mapped[str] = mapped_column(String(255), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    imap_user: Mapped[str] = mapped_column(String(255), default="")
    imap_password_enc: Mapped[str] = mapped_column(Text, default="")

    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    # ssl = von Anfang an verschlüsselt (465), starttls = Aufrüsten (587), none = nur im Haus.
    smtp_security: Mapped[str] = mapped_column(String(10), default="starttls")
    smtp_user: Mapped[str] = mapped_column(String(255), default="")
    smtp_password_enc: Mapped[str] = mapped_column(Text, default="")

    auth_type: Mapped[str] = mapped_column(String(20), default="password")  # password | oauth2
    oauth_token_enc: Mapped[str] = mapped_column(Text, default="")

    # Wohin Gesendetes, Entwürfe und Gelöschtes gehören. Die Namen unterscheiden sich je
    # Anbieter ("Sent" · "Gesendet" · "INBOX.Sent"), deshalb gehören sie zum Konto und nicht
    # in eine Liste im Code.
    folder_sent: Mapped[str] = mapped_column(String(255), default="Sent")
    folder_drafts: Mapped[str] = mapped_column(String(255), default="Drafts")
    folder_trash: Mapped[str] = mapped_column(String(255), default="Trash")
    folder_junk: Mapped[str] = mapped_column(String(255), default="Junk")
    folder_archive: Mapped[str] = mapped_column(String(255), default="Archive")
    # Wie das Archiv aufgeteilt wird: `folder` = alles in `folder_archive`, `pattern` = ein
    # Name aus Platzhaltern, gebildet aus dem Datum DER MAIL (nicht von heute — sonst landet
    # eine alte Rechnung im laufenden Jahr).
    archive_mode: Mapped[str] = mapped_column(String(10), default="folder")  # folder | pattern
    archive_pattern: Mapped[str] = mapped_column(String(255), default="Archive/{jahr}")

    # ── Was Agenten von diesem Postfach sehen dürfen ────────────────────────
    # Ein Postfach ist die Post eines Menschen, kein Datenbestand. Deshalb ist hier alles
    # ausgeschaltet, bis es jemand einschaltet — und zwar je Werkzeug einzeln, nicht als
    # „Zugriff ja/nein".
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ordner, die für Werkzeuge nicht existieren (Muster mit `*`, wie im alten imap-mcp).
    # Was hier steht, taucht weder in der Ordnerliste noch in einer Suche auf.
    mcp_ignore_folders: Mapped[list] = mapped_column(JSON, default=list)
    # Freigegebene Werkzeuge, mit ihrem Namen (`mail_search`, `mail_send` …). Leer = keins.
    mcp_tools: Mapped[list] = mapped_column(JSON, default=list)
    # Was ein Agent über dieses Postfach wissen muss, bevor er es anfasst: Tonfall, Zuständige,
    # Hausregeln („nie ohne Rückfrage senden"). Steht beim Konto und nicht im Agenten-Prompt,
    # weil es sich mit dem Postfach ändert und nicht mit dem Agenten.
    mcp_instructions: Mapped[str] = mapped_column(Text, default="")


class MailIdentity(TimestampMixin, Base):
    """Wer als Absender auftritt. Ein Konto kann mehrere Identitäten haben (Rolle im Verein,
    eigener Name, Alias einer Domain) — die Adresse muss der Server nur senden dürfen."""
    __tablename__ = "mail_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
