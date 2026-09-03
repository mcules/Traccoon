"""What belongs to a person's note area but not into their vault.

The notes themselves are files, and that is the point of them: they sync, they
open on a phone, they can be read without this program. What cannot be a file
sits here.

A calendar is the clearest case. Its address is a subscription, its password is
a credential, and both belong to one person — writing them into a folder that
the file sync carries to every device would put a password on every device.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


# Who may write into a calendar. Plain strings rather than a database enum: the
# interface reads them as they are, and trying a fourth should not need a type
# to be migrated first.
WRITE_NONE = "none"
WRITE_MANUAL = "manual"
WRITE_AGENT = "agent"
WRITE_ACCESS = (WRITE_NONE, WRITE_MANUAL, WRITE_AGENT)


class NotesCalendar(TimestampMixin, Base):
    """One calendar this person's notes read from.

    Deliberately a table and not a field on the user: there are several, they
    have an order, and one of them carries a password that has to be encrypted
    on its own rather than inside a JSON blob nobody would think to protect.
    """

    __tablename__ = "notes_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # What it is called in the calendar view.
    name: Mapped[str] = mapped_column(String(120), default="")
    # The login this calendar belongs to, if any. With one it can be written to
    # and it is read with that login's credentials; without one it is a
    # subscription — readable, and nothing more.
    server_id: Mapped[int | None] = mapped_column(
        ForeignKey("notes_calendar_servers.id", ondelete="SET NULL"),
        nullable=True, index=True)
    # Which collection on that server this is. The server's own name for it,
    # asked for over the protocol rather than guessed from the address.
    caldav_id: Mapped[str] = mapped_column(String(255), default="")
    # Who may write appointments here. Three states, not a flag: being allowed
    # to write and letting something write on your behalf are different
    # decisions, and the one somebody makes for their own calendar is not the
    # one they make for the family's. `none` = read it and nothing more,
    # `manual` = this person in the interface, `agent` = that and the assistant.
    write_access: Mapped[str] = mapped_column(String(16), default=WRITE_NONE)
    # What the server itself said the last time it was asked. Kept because a
    # permission the server does not grant is one nobody should be offered, and
    # because saying so beats a save that fails.
    server_read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where the events are read from: an ICS subscription or a collection that
    # hands its events out the same way.
    url: Mapped[str] = mapped_column(String(1000), default="")
    # The note the calendar's name links to, if any. Empty = it links nowhere.
    link_target: Mapped[str] = mapped_column(String(500), default="")
    auth_user: Mapped[str] = mapped_column(String(255), default="")
    # Fernet. The password never leaves this server and is never sent back out;
    # the interface only learns whether one is set.
    auth_password_enc: Mapped[str] = mapped_column(String, default="")
    # The order they are shown in. Written by the interface when they are moved.
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Off keeps the entry but stops it being fetched — which is what somebody
    # wants for a calendar that is temporarily noisy, rather than deleting it
    # and typing the address in again next week.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
