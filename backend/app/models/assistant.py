import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class AssistantTask(TimestampMixin, Base):
    """Projektloses Arbeits-Item des persönlichen Assistenten (steht über den Projekten).

    Entsteht z. B. aus einer eingehenden E-Mail: ein LOKALES Modell (qwen via litellm)
    klassifiziert und schwärzt den Inhalt VORHER — nur `redacted_summary` (+ Metadaten)
    verlässt das Haus Richtung Claude. Der Rohtext bleibt lokal; Claude liest den Volltext
    erst nach ausdrücklicher Freigabe (Status `new` → `approved`) über die IMAP-Tools.
    """
    __tablename__ = "assistant_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Eigentümer = der Mensch, dem der Assistent dient; sein Token, seine MCP-Gruppe.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    kind: Mapped[str] = mapped_column(String(30), default="email")  # email | note | …
    source: Mapped[str] = mapped_column(String(120), default="")     # z. B. webhook:new-email
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)  # Idempotenz

    title: Mapped[str] = mapped_column(String(500), default="")
    # Ergebnis der LOKALEN Vorklassifizierung (qwen) — bleibt im Haus erzeugt.
    category: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low|normal|high|urgent
    # Bereinigte, an Claude weiterreichbare Zusammenfassung (KEIN Rohtext, keine PII).
    redacted_summary: Mapped[str] = mapped_column(Text, default="")
    # Metadaten für den späteren IMAP-Volltextzugriff (account/uid/from/subject) — kein Inhalt.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # Schwärzung dieses Items: 'redacted' = nur Summary an Claude (Default, sicher);
    # 'unredacted' = Volltext direkt (nur wenn eine AssistantPolicy das für die Quelle erlaubt).
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")
    # Rohtext NUR gespeichert, wenn eine Regel 'unredacted' erlaubt (sonst NULL → nie im Haus abgelegt).
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Gelernter Handlungs-Hinweis aus der greifenden AssistantPolicy (z. B. „in Paperless ablegen").
    action_hint: Mapped[str] = mapped_column(Text, default="")

    # new = wartet auf Freigabe (nichts läuft); approved = freigegeben → Worker;
    # running → done | error.
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Tool-Gate: worauf der Lauf gerade auf Freigabe wartet (status='awaiting').
    pending_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    pending_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Einmal-Freigabe für die nächste Wiederaufnahme (wird beim Gate konsumiert).
    grant_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    grant_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AssistantPermission(TimestampMixin, Base):
    """Gelernte Tool-Berechtigung des Assistenten (owner-scoped, projektlos): 'immer erlauben'
    / 'nie' pro Tool(+Ressource). Wird vom Freigabe-Gate gelesen (perms.evaluate). Inhalt
    persönlich → DB-only, nicht im git."""
    __tablename__ = "assistant_permissions"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "tool", "resource", name="uq_assistant_perm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(150), default="")     # glob
    resource: Mapped[str] = mapped_column(String(500), default="*")  # glob
    action: Mapped[str] = mapped_column(String(10), default="ask")  # allow | ask | deny


class AssistantPolicy(TimestampMixin, Base):
    """Gelernte Regel des persönlichen Assistenten für eingehende Items (v. a. Mail).

    Owner-scoped, projektlos. Inhalt (Absender, Aktionen …) ist persönlich und bleibt in der DB —
    NICHT im git. Wird per Freigabe „immer …" gefüllt (Inbox/Telegram) oder manuell gepflegt.
    Passt eine Regel auf einen Eingang, kann er automatisch (geschwärzt/ungeschwärzt) laufen und
    bekommt den gelernten Handlungs-Hinweis mit.
    """
    __tablename__ = "assistant_policies"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "match_kind", "match_value", name="uq_assistant_policy_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # Worauf die Regel matcht: 'sender' (news@verband.de) · 'domain' (verband.de) · 'category' (rechnung).
    match_kind: Mapped[str] = mapped_column(String(20), default="sender")
    match_value: Mapped[str] = mapped_column(String(300), default="")

    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True)   # überspringt Review
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")  # redacted | unredacted
    action_hint: Mapped[str] = mapped_column(Text, default="")         # gelernte Aktion
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
