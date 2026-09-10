"""Passkeys: what may open a door, and what must stay shut.

The ceremony itself (the browser signs a challenge) is proven in the browser probe
`tools/uitest/passkey.mjs` with a virtual authenticator. What is nailed down here is
everything around it — the part where a mistake would not show as a broken login but as an
open one.
"""
import pytest

from app.core.security import hash_password
from app.models.enums import UserStatus
from app.models.passkey import Passkey
from app.models.user import User
from app.services import passkeys
from conftest import make_user

pytestmark = pytest.mark.asyncio


async def _person(db, username: str, *, status=UserStatus.active) -> User:
    u = await make_user(db, username)
    u.email = f"{username}@example.org"
    u.password_hash = hash_password("egal-egal")
    u.status = status
    await db.commit()
    return u


async def _key(db, user: User, credential_id: str = "AAAA") -> Passkey:
    k = Passkey(user_id=user.id, credential_id=credential_id, public_key=b"nicht-echt",
                sign_count=0, label="Handy")
    db.add(k)
    await db.commit()
    return k


async def test_the_offer_looks_the_same_for_a_stranger(client, db, redis_stub, passkey_redis):
    """A login form that answers differently for a known and an unknown name is a list of
    accounts for whoever asks patiently."""
    user = await _person(db, "anna")
    await _key(db, user)

    known = await client.post("/auth/passkey/options", json={"email": "anna"})
    stranger = await client.post("/auth/passkey/options", json={"email": "gibtsnicht"})

    assert known.status_code == stranger.status_code == 200
    a, b = known.json(), stranger.json()
    assert a["rpId"] == b["rpId"]
    assert len(a["allowCredentials"]) == len(b["allowCredentials"]) == 1
    assert set(a) == set(b), "dieselben Felder, sonst ist die Form die Auskunft"


async def test_an_account_without_a_key_is_not_given_away_either(client, db, redis_stub, passkey_redis):
    await _person(db, "anna")
    answer = await client.post("/auth/passkey/options", json={"email": "anna"})
    assert answer.status_code == 200
    assert len(answer.json()["allowCredentials"]) == 1, "ein Angebot, das niemand beantworten kann"


async def test_a_signature_without_an_offer_gets_nowhere(client, db, redis_stub, passkey_redis):
    """The challenge is spent on reading. Without one there is nothing this signature could
    belong to, and a stored signature must not be usable a second time."""
    user = await _person(db, "anna")
    await _key(db, user)
    answer = await client.post("/auth/passkey/login",
                               json={"email": "anna", "credential": {"id": "AAAA"}})
    assert answer.status_code == 401


async def test_the_challenge_is_gone_after_one_read(db, redis_stub, passkey_redis):
    await passkeys._remember("login", "7", b"\x01\x02\x03")
    assert await passkeys._redeem("login", "7") == b"\x01\x02\x03"
    assert await passkeys._redeem("login", "7") is None


async def test_a_key_of_somebody_else_does_not_open_this_account(db, redis_stub, passkey_redis):
    anna = await _person(db, "anna")
    berta = await _person(db, "berta")
    await _key(db, berta, credential_id="BBBB")
    await passkeys._remember("login", str(anna.id), b"egal")

    with pytest.raises(passkeys.NoPasskey):
        await passkeys.take_login(db, anna, {"id": "BBBB"})


async def test_a_deactivated_account_stays_out(db):
    """The key may be fine, the account is not. The rule hangs on `_may_log_in`, the same one
    the password login uses — two doors, one rule."""
    from app.api.auth import _may_log_in
    from app.core.error import Error

    user = await _person(db, "anna", status=UserStatus.disabled)
    with pytest.raises(Error):
        _may_log_in(user)


async def test_a_locked_account_is_not_given_away_by_its_answer(client, db, redis_stub,
                                                                passkey_redis):
    """The state of the account is checked AFTER the signature. The other way round, a locked
    account would answer 403 to any old rubbish while an unknown name answers 401 — and that
    difference is a way to find out which accounts exist."""
    user = await _person(db, "anna", status=UserStatus.disabled)
    await _key(db, user)
    locked = await client.post("/auth/passkey/login",
                               json={"email": "anna", "credential": {"id": "AAAA"}})
    stranger = await client.post("/auth/passkey/login",
                                 json={"email": "gibtsnicht", "credential": {"id": "AAAA"}})
    assert locked.status_code == stranger.status_code == 401
    assert locked.json() == stranger.json()


async def test_the_house_comes_out_of_one_setting(monkeypatch):
    """`rp_id` and origin must not drift apart — a key made for one and asked by the other
    fails in the browser with a message nobody can act on."""
    from app.config import settings

    monkeypatch.setattr(settings, "app_base_url", "https://traccoon.example.org/")
    assert passkeys.house() == ("traccoon.example.org", "https://traccoon.example.org")


async def test_without_a_domain_passkeys_are_simply_off(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_base_url", "")
    assert passkeys.configured() is False
