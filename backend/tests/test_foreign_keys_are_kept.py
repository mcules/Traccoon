"""The rules the database enforces, enforced in the tests too.

SQLite ignores foreign keys unless asked to. For as long as nobody asked, every
`ON DELETE CASCADE` and `ON DELETE SET NULL` in the models was a declaration no
test could measure, and a test could invent a row pointing at nothing — which is
exactly the row nobody notices is impossible until Postgres refuses it.

The switch is one line in `conftest`. These tests are here so that turning it
back off is a failure rather than a silence.
"""
from __future__ import annotations

import pytest
from conftest import make_user
from sqlalchemy import select, text

from app.models.api_token import ApiToken
from app.models.notes import NotesCalendar
from app.models.notes_servers import NotesCalendarServer


@pytest.mark.asyncio
async def test_the_switch_is_on(db) -> None:
    assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1


@pytest.mark.asyncio
async def test_a_row_pointing_at_nobody_is_refused(db) -> None:
    """The failure mode this catches: a test that says `reporter_id=1` and gets
    away with it, then asserts something about a person who does not exist."""
    from sqlalchemy.exc import IntegrityError

    db.add(ApiToken(owner_user_id=999_999, name="niemandes", prefix="x" * 12,
                    token_hash="x", scopes=""))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_a_cascade_actually_cascades(db) -> None:
    user = await make_user(db, "fk1")
    db.add(ApiToken(owner_user_id=user.id, name="seins", prefix="y" * 12,
                    token_hash="y", scopes=""))
    await db.commit()
    await db.delete(user)
    await db.commit()
    left = (await db.execute(select(ApiToken))).scalars().all()
    assert left == []


@pytest.mark.asyncio
async def test_set_null_actually_sets_null(db) -> None:
    """A calendar outlives the login it sat on, as a subscription. Measured on
    the database rather than on the declaration."""
    user = await make_user(db, "fk2")
    server = NotesCalendarServer(owner_user_id=user.id, label="Wolke",
                                 url="https://example.invalid/dav/")
    db.add(server)
    await db.commit()
    calendar = NotesCalendar(owner_user_id=user.id, name="Privat",
                             url="https://example.invalid/dav/privat/",
                             server_id=server.id, caldav_id="privat")
    db.add(calendar)
    await db.commit()
    await db.delete(server)
    await db.commit()
    await db.refresh(calendar)
    assert calendar.server_id is None
    assert calendar.caldav_id == "privat"   # what it was, not what it may do
