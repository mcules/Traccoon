"""A login to a calendar server.

Reading a calendar needs no login where the address is a share link. Writing
always does, and until now there was exactly one such login, on the person. That
is one server and one account — which does not survive contact with reality:
appointments live on more than one server, and one server holds more than one
account when a person has a private and a work login on the same machine.

So the login is a thing of its own, and a calendar names the one it belongs to.
A calendar without one is a subscription: it can be read and not written.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class NotesCalendarServer(TimestampMixin, Base):
    __tablename__ = "notes_calendar_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # What it is called in the settings. Two logins to the same machine are
    # told apart by this and by nothing else, so it is the one field that
    # carries the difference.
    label: Mapped[str] = mapped_column(String(120), default="")
    # The address of the server, not of one calendar: where its calendars are
    # is asked for over the protocol rather than built into a path here.
    url: Mapped[str] = mapped_column(String(500), default="")
    username: Mapped[str] = mapped_column(String(255), default="")
    # Fernet. It never leaves this server and never comes back out; the
    # interface only learns whether one is set.
    password_enc: Mapped[str] = mapped_column(String, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
