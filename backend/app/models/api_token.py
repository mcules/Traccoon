"""Personal access tokens: named, scoped credentials a human pastes into a client.

A JWT is right for a browser and wrong for a long lived client: it expires after twelve
hours, renewing it needs the password, and the only way to take one back is
`password_changed_at`, which kills EVERY session of that user everywhere. A token here is
the opposite of all three: it lives until it is revoked or expires, it needs nothing but
itself, and revoking one leaves the others alone.

**Two halves.** The token reads `trc_<prefix>_<secret>`. The prefix is the public lookup
handle and stands here in plain text; the secret is only ever kept as an Argon2 hash. Both
are needed because a hash cannot be searched for: without a prefix the server would have to
run Argon2 against every row on every request, which is a denial of service against
ourselves.

**The full token is shown exactly once**, in the answer to the create call. Afterwards only
the prefix is readable, the same rule the destinations already follow (`has_secret`). A row
is never deleted either, only stamped `revoked_at`: a deleted row is a lost record of what
once had access.
"""
import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The human this token acts as. It carries no rights of its own: what the token may do is
    # what its owner may do, narrowed by `scopes`.
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    # Public half, what the row is looked up by. Unique, because the lookup has to hit at
    # most one row without a further filter.
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    # Argon2 hash of the secret half. The secret itself exists nowhere on this server.
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Comma separated, see `core/scopes.py`. A string and not a JSON list: it is read on
    # every single request, and a short string beats a parsed document there.
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Written at most once a minute (see `services/api_tokens.py`): every request would
    # otherwise be a write per read, and the value is only ever read by a human deciding
    # whether a token is still in use.
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
