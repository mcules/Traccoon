import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
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
    kind: Mapped[str] = mapped_column(String(40), default="")   # done|failed|plan_review|to_test|blocked|permission|assistant_review
    title: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Telegram gesendet
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
