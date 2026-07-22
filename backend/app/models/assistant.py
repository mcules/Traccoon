import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
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

    # new = wartet auf Freigabe (nichts läuft); approved = freigegeben → Worker;
    # running → done | error.
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
