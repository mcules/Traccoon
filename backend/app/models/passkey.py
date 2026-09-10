"""A passkey somebody logs in with.

What is stored is the PUBLIC half and nothing else. The private key never leaves the device
that made it — that is the whole point of the thing: a database that leaks here leaks
nothing anybody could log in with, unlike a table of password hashes.

One person can have several: the phone, the laptop, a hardware key in a drawer. Which is
why each row carries a name and the day it was last used — without those, taking one out
again means guessing which of three lines is the phone that got lost.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class Passkey(TimestampMixin, Base):
    __tablename__ = "passkeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # The id the authenticator gave this key, base64url as the browser sends it. Unique
    # across everybody: it is what an incoming login is looked up by.
    credential_id: Mapped[str] = mapped_column(String(400), unique=True, nullable=False,
                                               index=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The counter of the authenticator. It only ever grows; a value that does not is the
    # sign of a cloned key, and then the login is refused (see `services/passkeys.py`).
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    # What the person calls this key. Filled by them, because "unknown authenticator" in a
    # list of three tells nobody which one is lying in the drawer.
    label: Mapped[str] = mapped_column(String(80), default="")
    # Where the browser said this key lives ("platform" = built into this device,
    # "cross-platform" = a stick one carries). A hint for the list, nothing hangs on it.
    kind: Mapped[str] = mapped_column(String(20), default="")
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
