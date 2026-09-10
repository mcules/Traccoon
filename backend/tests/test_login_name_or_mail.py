"""What may stand in the first field of the login form.

An email address is the long way round for somebody who logs in every day, and a login that
is annoying ends in a password saved in the browser that nobody rotates. The username is the
same login, only shorter.

What must NOT change with it: an unknown name and a wrong password have to look identical
from the outside. Otherwise the form is a way to find out which accounts exist.
"""
import pytest
from app.core.security import hash_password
from app.models.enums import UserStatus
from app.models.user import User
from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _person(db, username: str, email: str, password: str = "geheim-genug") -> User:
    u = await make_user(db, username)
    u.email = email
    u.password_hash = hash_password(password)
    u.status = UserStatus.active
    await db.commit()
    return u


async def test_the_email_address_still_works(client, db):
    await _person(db, "anna", "anna@example.org")
    r = await client.post("/auth/login",
                          json={"email": "anna@example.org", "password": "geheim-genug"})
    assert r.status_code == 200 and r.json()["access_token"]


async def test_the_username_works_too(client, db):
    await _person(db, "anna", "anna@example.org")
    r = await client.post("/auth/login",
                          json={"email": "anna", "password": "geheim-genug"})
    assert r.status_code == 200 and r.json()["access_token"]


async def test_the_spelling_of_the_username_does_not_decide(client, db):
    """Typed in the morning with a capital, it is the same person."""
    await _person(db, "McUles", "mc@example.org")
    r = await client.post("/auth/login",
                          json={"email": "mcules", "password": "geheim-genug"})
    assert r.status_code == 200


async def test_two_spellings_side_by_side_let_nobody_in(client, db):
    """`username` is unique per exact string, so `anna` and `Anna` CAN both exist. Guessing
    between two accounts at a login is not a thing to do — the exact spelling decides."""
    await _person(db, "anna", "eins@example.org", password="eins-geheim")
    await _person(db, "Anna", "zwei@example.org", password="zwei-geheim")

    vague = await client.post("/auth/login",
                              json={"email": "ANNA", "password": "eins-geheim"})
    assert vague.status_code == 401

    exact = await client.post("/auth/login",
                              json={"email": "anna", "password": "eins-geheim"})
    assert exact.status_code == 200


async def test_a_wrong_password_and_an_unknown_name_look_the_same(client, db):
    """A login that says "this user does not exist" is a list of accounts for whoever asks
    patiently. Same code, same message."""
    await _person(db, "anna", "anna@example.org")
    wrong = await client.post("/auth/login",
                              json={"email": "anna", "password": "falsch"})
    stranger = await client.post("/auth/login",
                                 json={"email": "gibtsnicht", "password": "falsch"})
    assert wrong.status_code == stranger.status_code == 401
    assert wrong.json() == stranger.json()


async def test_an_empty_field_is_no_login(client, db):
    r = await client.post("/auth/login", json={"email": "   ", "password": "x"})
    assert r.status_code == 422
