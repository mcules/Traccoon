"""Personal access tokens: named, scoped, individually revocable credentials.

The four claims worth a test each are the ones a JWT cannot make: a token survives a password
change, it reaches only what its scopes name, it can be taken back alone, and its secret is
readable exactly once. Everything else here guards the machinery that makes those four true
(the two halves of the string, the deny-by-default table, the throttled `last_used_at`).
"""
import datetime as dt

import pytest

from app.api.office_ws import CLOSE_FORBIDDEN, CLOSE_UNAUTHENTICATED, office_ws
from app.core import scopes as scopes_mod
from app.core.security import create_access_token
from app.models.api_token import ApiToken
from app.models.enums import ProjectRole, StatusCategory
from app.models.ticket import IssueCounter, IssueType, WorkflowStatus
from app.services import api_tokens
from conftest import add_member, auth, make_project, make_user
from test_office_ws import FakeWS, use_test_db


# ── Helpers ──────────────────────────────────────────────────────────────────

async def mint(client, user, *, name="Obsidian", scopes=("assistant",), days=None):
    """Create a token over the API, the way a human does. Returns (full string, row json)."""
    body = {"name": name, "scopes": list(scopes)}
    if days is not None:
        body["expires_in_days"] = days
    r = await client.post("/me/tokens", json=body, headers=auth(user))
    assert r.status_code == 201, r.text
    row = r.json()
    return row["token"], row


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def ticket_project(db, user, key="TOK"):
    """A project this user owns, complete enough for `POST /projects/{id}/issues`."""
    proj = await make_project(db, key, "Token")
    await add_member(db, proj, user, ProjectRole.owner)
    db.add_all([
        IssueType(project_id=proj.id, name="Aufgabe"),
        WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0),
        IssueCounter(project_id=proj.id, last_number=0),
    ])
    await db.commit()
    return proj


# ── 1. The token authenticates, a wrong secret does not ──────────────────────

async def test_a_created_token_authenticates_a_normal_request(client, db):
    anna = await make_user(db, "anna")
    token, row = await mint(client, anna)

    r = await client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "anna"
    assert token.startswith("trc_") and token.startswith(f"trc_{row['prefix']}_")


async def test_the_right_prefix_with_a_wrong_secret_is_rejected(client, db):
    """The prefix is public (it stands in every listing). It must be worth nothing alone."""
    anna = await make_user(db, "anna")
    _token, row = await mint(client, anna)

    r = await client.get("/auth/me", headers=bearer(f"trc_{row['prefix']}_" + "x" * 32))
    assert r.status_code == 401
    assert r.json()["key"] == "err.invalid_token"


async def test_a_token_of_the_right_shape_without_a_row_is_rejected(client, db):
    await make_user(db, "anna")
    r = await client.get("/auth/me", headers=bearer("trc_" + "a" * 12 + "_" + "b" * 32))
    assert r.status_code == 401
    assert r.json()["key"] == "err.invalid_token"


async def test_the_two_halves_survive_an_underscore_in_either_of_them(db):
    """The url-safe alphabet contains `_`, so splitting on it would be ambiguous. The prefix
    has a fixed length, which is why the separator's place is known."""
    assert api_tokens.split("trc_" + "a_b" + "c" * 9 + "_" + "d_e_f") == ("a_b" + "c" * 9, "d_e_f")
    assert api_tokens.split("nottrc_x") is None
    assert api_tokens.split("trc_short") is None


# ── 2. Revoked and expired ───────────────────────────────────────────────────

async def test_a_revoked_token_is_dead_and_the_row_stays(client, db):
    anna = await make_user(db, "anna")
    token, row = await mint(client, anna)
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 200

    r = await client.delete(f"/me/tokens/{row['id']}", headers=auth(anna))
    assert r.status_code == 204
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 401

    # The row is a record of what once had access, so it is stamped and not deleted.
    listing = (await client.get("/me/tokens", headers=auth(anna))).json()
    assert [t["id"] for t in listing] == [row["id"]]
    assert listing[0]["revoked_at"] is not None


async def test_an_expired_token_is_dead(client, db):
    anna = await make_user(db, "anna")
    token, row = await mint(client, anna, days=1)
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 200

    stored = await db.get(ApiToken, row["id"])
    stored.expires_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=1)
    await db.commit()

    r = await client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401
    assert r.json()["key"] == "err.invalid_token"
    assert (await client.get("/me/tokens", headers=auth(anna))).json()[0]["expired"] is True


async def test_a_token_of_a_locked_account_is_dead(client, db):
    """Same 401 as every other way a token can fail: which of the five it was is information
    an attacker does not need."""
    from app.models.enums import UserStatus

    anna = await make_user(db, "anna")
    token, _row = await mint(client, anna)
    anna.status = UserStatus.disabled
    await db.commit()

    r = await client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401
    assert r.json()["key"] == "err.invalid_token"


# ── 3. Scopes ────────────────────────────────────────────────────────────────

async def test_assistant_reaches_the_chat_and_not_the_tickets(client, db):
    anna = await make_user(db, "anna")
    proj = await ticket_project(db, anna)
    token, _row = await mint(client, anna, scopes=("assistant",))

    assert (await client.get("/assistant/chat", headers=bearer(token))).status_code == 200
    r = await client.post(f"/projects/{proj.id}/issues", json={"summary": "x"},
                          headers=bearer(token))
    assert r.status_code == 403
    assert r.json()["key"] == "err.scope_missing"


async def test_full_reaches_both(client, db):
    anna = await make_user(db, "anna")
    proj = await ticket_project(db, anna)
    token, _row = await mint(client, anna, scopes=("full",))

    assert (await client.get("/assistant/chat", headers=bearer(token))).status_code == 200
    r = await client.post(f"/projects/{proj.id}/issues", json={"summary": "x"},
                          headers=bearer(token))
    assert r.status_code == 201, r.text


async def test_tickets_reaches_the_ticket_endpoints_and_not_the_assistant(client, db):
    anna = await make_user(db, "anna")
    proj = await ticket_project(db, anna)
    token, _row = await mint(client, anna, scopes=("tickets",))

    r = await client.post(f"/projects/{proj.id}/issues", json={"summary": "x"},
                          headers=bearer(token))
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    assert (await client.get("/projects", headers=bearer(token))).status_code == 200
    assert (await client.get(f"/issues/{key}", headers=bearer(token))).status_code == 200
    assert (await client.post(f"/issues/{key}/comments", json={"body": "hi"},
                              headers=bearer(token))).status_code == 201
    assert (await client.get("/assistant/chat", headers=bearer(token))).status_code == 403


async def test_assistant_also_reaches_the_notifications_and_the_personal_channel(client, db, monkeypatch):
    """The bell and the personal socket carry what concerns the person, like the chat does.
    A client that cannot read them has to poll the chat to notice anything."""
    from app.api.ws import persons_ws

    use_test_db(monkeypatch, db)
    anna = await make_user(db, "anna")
    token, _row = await mint(client, anna, scopes=("assistant",))

    assert (await client.get("/notifications", headers=bearer(token))).status_code == 200
    ws = FakeWS()
    await persons_ws(ws, token=token)
    assert ws.closed is None


async def test_the_project_socket_needs_full(client, db, monkeypatch):
    """No scope names it, so `assistant` does not reach it: the project chat can assign
    agents, and that is not what an assistant token is for."""
    from app.api.ws import project_ws

    use_test_db(monkeypatch, db)
    anna = await make_user(db, "anna")
    proj = await ticket_project(db, anna, key="PWS")
    narrow, _ = await mint(client, anna, name="eng", scopes=("assistant",))
    wide, _ = await mint(client, anna, name="weit", scopes=("full",))

    ws = FakeWS()
    await project_ws(ws, proj.id, token=narrow)
    assert ws.closed == 4403

    ws = FakeWS()
    await project_ws(ws, proj.id, token=wide)
    assert ws.closed is None


async def test_an_endpoint_no_scope_names_needs_full(client, db):
    """Deny by default. `PUT /me/theme` is named by nothing, so a scoped token cannot reach
    it, and a route added tomorrow is closed to every token issued today."""
    anna = await make_user(db, "anna")
    narrow, _ = await mint(client, anna, name="eng", scopes=("assistant", "tickets"))
    wide, _ = await mint(client, anna, name="weit", scopes=("full",))

    assert (await client.put("/me/theme", json={"value": "light"},
                             headers=bearer(narrow))).status_code == 403
    assert (await client.put("/me/theme", json={"value": "light"},
                             headers=bearer(wide))).status_code == 204


async def test_the_table_is_keyed_by_the_route_template_not_the_path():
    """`/issues/ABC-7` and `/issues/ABC-3` are one entry, so no clever path widens a token."""
    assert scopes_mod.allowed({"tickets"}, "GET", "/issues/{key}")
    assert not scopes_mod.allowed({"tickets"}, "GET", "/issues/ABC-7")
    assert not scopes_mod.allowed({"tickets"}, "DELETE", "/issues/{key}")
    # A JWT session is never measured against the table.
    assert scopes_mod.allowed(None, "DELETE", "/anything/at/all")


async def test_a_token_cannot_be_minted_without_a_scope_or_a_name(client, db):
    anna = await make_user(db, "anna")
    r = await client.post("/me/tokens", json={"name": "x", "scopes": []}, headers=auth(anna))
    assert r.status_code == 400 and r.json()["key"] == "err.token_scope_missing"
    r = await client.post("/me/tokens", json={"name": " ", "scopes": ["full"]},
                          headers=auth(anna))
    assert r.status_code == 400 and r.json()["key"] == "err.token_name_missing"
    # An unknown scope is dropped, and dropping the only one is the empty case above.
    r = await client.post("/me/tokens", json={"name": "x", "scopes": ["erfunden"]},
                          headers=auth(anna))
    assert r.status_code == 400 and r.json()["key"] == "err.token_scope_missing"


# ── 4. A password change kills the session, not the token ────────────────────

async def test_a_password_change_kills_the_jwt_and_leaves_the_token_alive(client, db):
    """The whole reason these tokens exist. A client that has to be re-pasted whenever the
    human changes their password is a JWT with extra steps."""
    anna = await make_user(db, "anna")
    session = create_access_token(anna.id)
    token, _row = await mint(client, anna)
    assert (await client.get("/auth/me", headers=bearer(session))).status_code == 200

    r = await client.post("/auth/me/password", headers=auth(anna),
                          json={"old_password": "pw", "new_password": "ein-neues-pw"})
    assert r.status_code == 204

    # `iat` counts in whole seconds and the check is `iat < password_changed_at`, so a JWT
    # minted in the same second as the change survives it. The test would otherwise pass or
    # fail depending on how fast the machine is, so the stamp is pushed clearly past it (the
    # same move as in `test_office_ws.py`).
    await db.refresh(anna)
    anna.password_changed_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    await db.commit()

    dead = await client.get("/auth/me", headers=bearer(session))
    assert dead.status_code == 401
    assert dead.json()["key"] == "err.token_expired_by_password_change"
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 200


# ── 5. The office websocket ──────────────────────────────────────────────────

async def test_the_office_socket_accepts_a_scoped_token(client, db, monkeypatch):
    use_test_db(monkeypatch, db)
    anna = await make_user(db, "anna")
    proj = await ticket_project(db, anna, key="WSA")
    token, _row = await mint(client, anna, scopes=("assistant",))

    ws = FakeWS()
    await office_ws(ws, token=token)   # ends on WebSocketDisconnect
    assert ws.accepted is True
    assert ws.sent[0]["type"] == "hello"
    assert ws.sent[0]["projects"] == [proj.id]


async def test_the_office_socket_refuses_a_token_without_the_assistant_scope(client, db, monkeypatch):
    use_test_db(monkeypatch, db)
    anna = await make_user(db, "anna")
    token, _row = await mint(client, anna, scopes=("tickets",))

    ws = FakeWS()
    await office_ws(ws, token=token)
    assert ws.closed == CLOSE_FORBIDDEN
    assert ws.accepted is False


async def test_the_office_socket_refuses_a_revoked_token(client, db, monkeypatch):
    use_test_db(monkeypatch, db)
    anna = await make_user(db, "anna")
    token, row = await mint(client, anna, scopes=("assistant",))
    await client.delete(f"/me/tokens/{row['id']}", headers=auth(anna))

    ws = FakeWS()
    await office_ws(ws, token=token)
    assert ws.closed == CLOSE_UNAUTHENTICATED
    assert ws.accepted is False


# ── 6. The secret is shown exactly once ──────────────────────────────────────

async def test_the_secret_stands_in_the_create_answer_and_nowhere_else(client, db):
    anna = await make_user(db, "anna")
    r = await client.post("/me/tokens", json={"name": "Obsidian", "scopes": ["assistant"]},
                          headers=auth(anna))
    token = r.json()["token"]
    secret = token.split("_", 2)[2]

    listing = await client.get("/me/tokens", headers=auth(anna))
    assert "token" not in listing.json()[0]
    assert secret not in listing.text
    # Not even the server keeps it: what stands in the row is an Argon2 hash.
    row = await db.get(ApiToken, r.json()["id"])
    assert secret not in row.token_hash
    assert row.token_hash.startswith("$argon2")


async def test_an_admin_sees_foreign_rows_but_no_secret(client, db):
    anna = await make_user(db, "anna")
    chef = await make_user(db, "chef", admin=True)
    token, row = await mint(client, anna)

    seen = await client.get(f"/me/tokens?user_id={anna.id}", headers=auth(chef))
    assert [t["id"] for t in seen.json()] == [row["id"]]
    assert token.split("_", 2)[2] not in seen.text
    # And a non-admin asking for a foreign list gets their own, not an error: whose tokens
    # these are is not a question they should be able to probe.
    berta = await make_user(db, "berta")
    assert (await client.get(f"/me/tokens?user_id={anna.id}",
                             headers=auth(berta))).json() == []


async def test_only_the_owner_and_an_admin_revoke(client, db):
    anna = await make_user(db, "anna")
    berta = await make_user(db, "berta")
    _token, row = await mint(client, anna)

    assert (await client.delete(f"/me/tokens/{row['id']}",
                                headers=auth(berta))).status_code == 404
    assert (await client.delete(f"/me/tokens/{row['id']}",
                                headers=auth(anna))).status_code == 204


# ── 7. last_used_at ──────────────────────────────────────────────────────────

async def test_last_used_is_written_but_not_on_every_request(client, db):
    """Every request would be a write per read, and the value is only ever read by a human
    deciding whether a token is still in use."""
    anna = await make_user(db, "anna")
    token, row = await mint(client, anna)
    assert (await client.get("/me/tokens", headers=auth(anna))).json()[0]["last_used_at"] is None

    await client.get("/auth/me", headers=bearer(token))
    first = (await client.get("/me/tokens", headers=auth(anna))).json()[0]["last_used_at"]
    assert first is not None

    await client.get("/auth/me", headers=bearer(token))
    await client.get("/auth/me", headers=bearer(token))
    assert (await client.get("/me/tokens", headers=auth(anna))).json()[0]["last_used_at"] == first

    # Only once the gap has passed does it move again.
    stored = await db.get(ApiToken, row["id"])
    stored.last_used_at = dt.datetime.now(tz=dt.timezone.utc) - 2 * api_tokens.LAST_USED_GAP
    await db.commit()
    await client.get("/auth/me", headers=bearer(token))
    assert (await client.get("/me/tokens", headers=auth(anna))).json()[0]["last_used_at"] != first
