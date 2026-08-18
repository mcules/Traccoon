import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=True)
    # Projektlose Assistent-Freigaben (Telegram-Karte): Bezug aufs Inbox-Item statt aufs Issue.
    assistant_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"), nullable=True)
    # Spam-Rückfrage (Einzelkarte): Bezug aufs Urteil statt aufs Inbox-Item — ein Urteil
    # kann auch ohne Inbox-Item entstehen (Passthrough-Webhooks legen keinen Task an).
    spam_verdict_id: Mapped[int | None] = mapped_column(
        ForeignKey("spam_verdicts.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="")   # done|failed|plan_review|to_test|blocked|permission|assistant_review|spam_review|spam_digest
    title: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Medienausgang: der Notifier ist der EINZIGE Weg nach Telegram — dem backend-Container
    # fehlt `TELEGRAM_BOT_TOKEN` vollständig (er hat nur `TELEGRAM_OWNER_CHAT`), nur der
    # telegram-bot-Prozess spricht mit Telegram. Wer eine Datei mitschicken will, legt sie
    # an einen für BEIDE Dienste sichtbaren Pfad und schreibt ihn hierher; ein zweiter
    # Ausgang wäre die Doppelung, gegen die dieses Repo sonst durchgehend argumentiert.
    # Beide Spalten nullable und ohne Vorgabe: Bestandszeilen ändern ihr Verhalten nicht.
    media_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)   # animation|photo|document
    # Drossel: wann zuletzt eine Nachricht dieser Art hinausging. Traccar dedupliziert
    # Alarme ausdrücklich NICHT — solange das Erschütterungsbit steht, kommt ein Ereignis je
    # eingehender Position, im Wachbetrieb alle paar Sekunden. Zehn Minuten Rütteln wären
    # rund 120 gleichlautende Nachrichten. Was als „dieselbe Nachricht" gilt, entscheidet
    # der Ablauf über den Schlüssel (Gerät, Klasse, was auch immer) — nicht Traccoon.
    drossel_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Telegram gesendet
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_notifications_drossel", "drossel_key", "created_at"),)
