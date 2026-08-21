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
    # Project-less assistant approvals (Telegram card): reference to the inbox item instead of the issue.
    assistant_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"), nullable=True)
    # Spam question (single card): reference to the verdict instead of the inbox item; a
    # verdict can come into being without an inbox item as well (passthrough webhooks create no task).
    spam_verdict_id: Mapped[int | None] = mapped_column(
        ForeignKey("spam_verdicts.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="")   # done|failed|plan_review|to_test|blocked|permission|assistant_review|spam_review|spam_digest
    title: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Media output: the notifier is the ONLY way to Telegram, because the backend container
    # lacks `TELEGRAM_BOT_TOKEN` entirely (it only has `TELEGRAM_OWNER_CHAT`) and only the
    # telegram-bot process talks to Telegram. Whoever wants to send a file along puts it at a
    # path visible to BOTH services and writes it here; a second output would be the
    # duplication this repository otherwise argues against throughout.
    # Both columns are nullable and without a default: existing rows do not change behaviour.
    media_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)   # animation|photo|document
    # Throttle: when a message of this kind last went out. Traccar explicitly does NOT
    # deduplicate alarms; as long as the vibration bit is set, one event comes per incoming
    # position, every few seconds in guard mode. Ten minutes of shaking would be around 120
    # identical messages. What counts as "the same message" is decided by the flow over the
    # key (device, class, whatever), not by Traccoon.
    throttle_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Telegram gesendet
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_notifications_drossel", "throttle_key", "created_at"),)
