"""`POST /auth/refresh`: it may extend, not grant.

The endpoint is the precondition for a tab staying open longer than `jwt_expire_minutes`
(720); the kiosk wall screen of the office is the first that needs it. Exactly for that
reason it is a security surface as well, and this file nails it down:

* what is **no longer** valid is not revived (expired, password changed, account
  deactivated),
* and what is valid is extended **unchanged**: the same subject, the same role, no
  additional claims in the token.

The last point is the actual reason for this file. A refresh that adds something while
reissuing is an escalation of rights with a timer.
"""
import datetime as dt

import jwt
import pytest

from app.config import settings
from app.core.security import create_access_token
from app.models.enums import GlobalRole, UserStatus
from conftest import make_user

pytestmark = pytest.mark.asyncio


def token_mit(user_id: int, *, iat: dt.datetime, exp: dt.datetime) -> str:
    """A token with freely chosen timestamps, the same shape as `create_access_token`."""
    return jwt.encode(
        {"sub": str(user_id), "iat": int(iat.timestamp()), "exp": int(exp.timestamp())},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


def kopf(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_gueltiges_token_wird_verlaengert(client, db):
    """Der Normalfall: neues Token, und das neue Token trägt."""
    user = await make_user(db, "wandschirm")
    alt = create_access_token(user.id)

    r = await client.post("/auth/refresh", headers=kopf(alt))
    assert r.status_code == 200, r.text
    neu = r.json()["access_token"]
    assert r.json()["token_type"] == "bearer"

    # The proof that it is a real token is not its shape but a call with it.
    me = await client.get("/auth/me", headers=kopf(neu))
    assert me.status_code == 200
    assert me.json()["id"] == user.id


async def test_abgelaufenes_token_wird_abgelehnt(client, db):
    """A refresh extends a living session; it does not wake a dead one."""
    user = await make_user(db, "spaet")
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    tot = token_mit(user.id, iat=jetzt - dt.timedelta(days=2), exp=jetzt - dt.timedelta(hours=1))

    r = await client.post("/auth/refresh", headers=kopf(tot))
    assert r.status_code == 401


async def test_token_von_vor_dem_passwortwechsel(client, db):
    """Session invalidation: whoever changed their password ended all old tokens.

    A refresh that ignored that would be a back door for exactly the case the check was built
    for: a foreign tab that keeps running.
    """
    user = await make_user(db, "passwortwechsler")
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    # Two days of distance: under SQLite `password_changed_at` comes back naive and is read
    # as local time in `deps.py`. The test should hang off the check, not off the time zone
    # of the container.
    alt = token_mit(user.id, iat=jetzt - dt.timedelta(days=2), exp=jetzt + dt.timedelta(hours=1))
    user.password_changed_at = jetzt
    await db.commit()

    r = await client.post("/auth/refresh", headers=kopf(alt))
    assert r.status_code == 401


async def test_deaktiviertes_konto(client, db):
    """Deactivated means deactivated, even for a token that would still be valid for twelve hours.

    403 is the answer of the house here (`deps.get_current_user`: "Account not active"); the
    test accepts 401 as well, so that it does not break on a tightening.
    """
    user = await make_user(db, "gesperrt")
    token = create_access_token(user.id)
    user.status = UserStatus.disabled
    await db.commit()

    r = await client.post("/auth/refresh", headers=kopf(token))
    assert r.status_code in (401, 403)


async def test_ohne_token(client):
    r = await client.post("/auth/refresh")
    assert r.status_code == 401


async def test_kein_rechtezuwachs(client, db):
    """The new token is the same token, only later, and in particular with the same role.

    Checked on both levels: the claims in the token (there are exactly three, and none of them
    is a role) and the actual effect (`/auth/me` still says `user`).
    """
    user = await make_user(db, "einfach")          # global_role = user, no admin
    assert user.global_role == GlobalRole.user

    r = await client.post("/auth/refresh", headers=kopf(create_access_token(user.id)))
    assert r.status_code == 200
    neu = r.json()["access_token"]

    payload = jwt.decode(neu, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(user.id)
    # No additional claims: roles come from the database on every call, never from the token.
    # If this set grows, the attack surface grows.
    assert set(payload) == {"sub", "iat", "exp"}

    me = await client.get("/auth/me", headers=kopf(neu))
    assert me.status_code == 200
    assert me.json()["global_role"] == GlobalRole.user.value

    # And the admin area stays closed, which is the actual point behind the set of claims.
    admin = await client.get("/admin/run-retention", headers=kopf(neu))
    assert admin.status_code == 403
