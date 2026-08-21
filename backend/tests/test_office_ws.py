"""Live transport of the office: one definition of "may see", and a socket that is not
weaker than a request.

The most valuable test here is the **equality** `compute_acl(db, user)` with the set from
`GET /projects`. It is the reason why there can be no second, slowly drifting notion of which
projects a user may see. If it falls, the live stream shows either too little (annoying) or
too much (a leak).

No new test dependency: what is checked are the pure functions and the fan-out against a fake
socket. The endpoints are called directly; for the auth check no real WebSocket handshake is
needed, only an object that records `close()`.
"""
import asyncio
import datetime as dt
import time

import pytest
from fastapi import WebSocketDisconnect

from app.api.office_ws import (
    ACL_TTL_S, CLOSE_FORBIDDEN, CLOSE_TOO_SLOW, CLOSE_UNAUTHENTICATED, QUEUE_MAX,
    UserConnectionManager, _Conn, compute_acl, in_scope, parse_scopes, office_ws,
    visible,
)
from app.api.ws import project_ws
from app.core.security import create_access_token
from app.models.enums import ProjectRole, UserStatus
from conftest import add_member, auth, make_project, make_user


# ── Fake-Socket ──────────────────────────────────────────────────────────────

class FakeWS:
    """What the endpoint really uses of a WebSocket; no more is needed."""

    def __init__(self, incoming: list[str] | None = None) -> None:
        self.accepted = False
        self.closed: int | None = None
        self.sent: list[dict] = []
        self._incoming = list(incoming or [])

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_text(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect()


def conn(user_id: int = 1, *, is_admin: bool = False, allowed: set[int] | None = None,
         scope: set[int] | None = None) -> _Conn:
    return _Conn(ws=FakeWS(), user_id=user_id, is_admin=is_admin,
                 allowed=set(allowed or ()), acl_at=time.monotonic(), scope=scope)


def ev(project_id: int | None, owner_id: int | None = None, **kw) -> dict:
    """An office event, shortened to the fields that decide the visibility."""
    return {"v": 1, "seq": 41, "kind": "tool_start",
            "project_id": project_id, "owner_id": owner_id, **kw}


def use_test_db(monkeypatch, db) -> None:
    """The WS endpoints fetch a session of their own (no `Depends`), and they bound
    `SessionLocal` at import time, so the conftest patch on `app.db` does not reach them."""
    import app.api.office_ws as rtws
    import app.api.ws as wsmod
    factory = db.__test_factory__
    monkeypatch.setattr(rtws, "SessionLocal", factory)
    monkeypatch.setattr(wsmod, "SessionLocal", factory)


# ── Gleichheit: compute_acl ≡ GET /projects ──────────────────────────────────

async def projects_from_api(client, user) -> set[int]:
    r = await client.get("/projects", headers=auth(user))
    assert r.status_code == 200, r.text
    return {p["id"] for p in r.json()}


async def test_acl_equals_projects_for_a_member(client, db):
    """Direct member: exactly their projects, and none of the foreign ones."""
    user = await make_user(db, "mitglied")
    my = await make_project(db, "MEI", "Meins")
    await make_project(db, "FRE", "Fremdes")
    await add_member(db, my, user, ProjectRole.member)

    acl = await compute_acl(db, user)
    assert acl == await projects_from_api(client, user) == {my.id}


async def test_acl_equals_projects_for_an_inherited_subproject(client, db):
    """Member in the parent project: the sub-project comes along over the inheritance, and
    exactly there sits the branching a second ACL definition mapped wrongly."""
    user = await make_user(db, "erbe")
    parent = await make_project(db, "ELT", "Eltern")
    kind = await make_project(db, "KIN", "Kind", parent_id=parent.id, inherit_members=True)
    dense = await make_project(db, "DIC", "Abgeriegelt", parent_id=parent.id,
                               inherit_members=False)
    await add_member(db, parent, user, ProjectRole.member)

    acl = await compute_acl(db, user)
    assert acl == await projects_from_api(client, user) == {parent.id, kind.id}
    assert dense.id not in acl


async def test_acl_equals_projects_for_an_admin(client, db):
    """The admin sees everything, without a special branch in `compute_acl`; otherwise that
    would be exactly the place where the two definitions drifted apart."""
    admin = await make_user(db, "chef", admin=True)
    a = await make_project(db, "AAA", "A")
    b = await make_project(db, "BBB", "B")

    acl = await compute_acl(db, admin)
    assert acl == await projects_from_api(client, admin) == {a.id, b.id}


# ── visible(): the matrix ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("who", "what", "expected"),
    [
        # Mitglied (user 7, Projekt 27)
        ("mitglied", "projekt", True),
        ("mitglied", "eigenes_projektlos", True),
        ("mitglied", "fremdes_projektlos", False),
        # Non-member (user 8)
        ("fremd", "projekt", False),
        ("fremd", "eigenes_projektlos", False),   # belongs to user 7, not to them
        ("fremd", "fremdes_projektlos", False),
        # Admin
        ("admin", "projekt", True),
        ("admin", "eigenes_projektlos", True),
        ("admin", "fremdes_projektlos", True),
    ],
)
def test_the_visibility_matrix(who, what, expected):
    people = {
        "mitglied": conn(7, allowed={27}),
        "fremd": conn(8, allowed=set()),
        "admin": conn(9, is_admin=True),
    }
    events = {
        "projekt": ev(27, 3),
        "eigenes_projektlos": ev(None, 7),
        "fremdes_projektlos": ev(None, 99),
    }
    assert visible(events[what], people[who]) is expected


def test_without_project_and_owner_it_is_nobodys_event():
    assert visible(ev(None, None), conn(7, allowed={27})) is False


# ── The scope only narrows ───────────────────────────────────────────────────

def test_a_scope_on_a_foreign_project_yields_silence():
    """`scope={99}` on a project the user is not in: no event arrives. The narrowing of the
    client cannot open the server set up."""
    c = conn(7, allowed={27}, scope={99})
    foreign = ev(99, 3)
    assert visible(foreign, c) is False
    assert (visible(foreign, c) and in_scope(foreign, c)) is False
    # And the own project now falls out of the scope: narrowed stays narrowed.
    own = ev(27, 3)
    assert visible(own, c) is True
    assert in_scope(own, c) is False


def test_scope_none_is_everything_permitted():
    c = conn(7, allowed={27})
    assert in_scope(ev(27, 3), c) is True
    assert in_scope(ev(None, 7), c) is True


def test_a_project_scope_excludes_project_less_ones():
    """A project subscription (the tab in the project) does not want the global job runs."""
    c = conn(7, allowed={27}, scope={27})
    assert in_scope(ev(27, 3), c) is True
    assert in_scope(ev(None, 7), c) is False


def test_parsing_scopes_errs_on_the_narrow_side():
    assert parse_scopes({"type": "subscribe"}) is None                       # without it: global
    assert parse_scopes({"scopes": [{"kind": "global"}]}) is None
    assert parse_scopes({"scopes": [{"kind": "project", "id": 27}]}) == {27}
    # Global wins when both are in there: it is the set they may see anyway.
    assert parse_scopes({"scopes": [{"kind": "project", "id": 27}, {"kind": "global"}]}) is None
    # Unreadable becomes silence, NOT "everything".
    assert parse_scopes({"scopes": [{"kind": "project"}, "quatsch"]}) == set()
    assert parse_scopes({"scopes": []}) == set()


# ── Fan-out ──────────────────────────────────────────────────────────────────

async def test_the_fan_out_reaches_only_the_entitled():
    m = UserConnectionManager()
    member = conn(7, allowed={27})
    foreign = conn(8, allowed={99})
    admin = conn(9, is_admin=True)
    for c in (member, foreign, admin):
        m.add(c)

    event = ev(27, 3)
    await m.dispatch(event)

    assert member.queue.get_nowait() == {"type": "office_ev", "ev": event}
    assert admin.queue.get_nowait()["ev"] is event
    assert foreign.queue.empty()


async def test_a_project_less_fan_out_goes_only_to_the_owner():
    m = UserConnectionManager()
    own = conn(7, allowed=set())
    different = conn(8, allowed={27})
    m.add(own)
    m.add(different)

    await m.dispatch(ev(None, 7))
    assert not own.queue.empty()
    assert different.queue.empty()


async def test_a_slow_client_is_dropped_instead_of_slowing_the_bridge():
    """A full queue means the connection flies out. It then has a gap and has to come back
    over the snapshot; sending on would fake a gapless stream."""
    m = UserConnectionManager()
    c = conn(7, allowed={27})
    m.add(c)
    for i in range(QUEUE_MAX):
        c.queue.put_nowait({"füll": i})

    await m.dispatch(ev(27, 3))

    assert c not in m.conns
    await asyncio.sleep(0)  # give the closing task a turn
    assert c.ws.closed == CLOSE_TOO_SLOW


async def test_the_sweeper_refreshes_only_expired_acls(db, monkeypatch):
    """The ACL hangs off the connection and is refreshed by the sweeper, never per event. A
    fresh connection it does not touch at all."""
    use_test_db(monkeypatch, db)
    user = await make_user(db, "spaet")
    proj = await make_project(db, "SPT", "Später")
    await add_member(db, proj, user, ProjectRole.member)

    m = UserConnectionManager()
    old = _Conn(ws=FakeWS(), user_id=user.id, is_admin=False, allowed=set(),
                acl_at=time.monotonic() - ACL_TTL_S - 1)
    fresh = _Conn(ws=FakeWS(), user_id=user.id, is_admin=False, allowed=set(),
                   acl_at=time.monotonic())
    m.add(old)
    m.add(fresh)

    assert await m.refresh_stale() == 1
    assert old.allowed == {proj.id}
    assert fresh.allowed == set()


async def test_the_sweeper_throws_out_disabled_users(db, monkeypatch):
    use_test_db(monkeypatch, db)
    user = await make_user(db, "gesperrt")
    m = UserConnectionManager()
    c = _Conn(ws=FakeWS(), user_id=user.id, is_admin=False, allowed=set(),
              acl_at=time.monotonic() - ACL_TTL_S - 1)
    m.add(c)

    user.status = UserStatus.disabled
    await db.commit()

    await m.refresh_stale()
    assert c not in m.conns
    await asyncio.sleep(0)
    assert c.ws.closed == CLOSE_FORBIDDEN


# ── Auth ─────────────────────────────────────────────────────────────────────

async def test_the_socket_rejects_a_broken_token(db, monkeypatch):
    use_test_db(monkeypatch, db)
    ws = FakeWS()
    await office_ws(ws, token="kein.echtes.token")
    assert ws.closed == CLOSE_UNAUTHENTICATED
    assert ws.accepted is False


async def test_the_socket_rejects_a_token_from_before_the_password_change(db, monkeypatch):
    """A token issued before the last password change is dead; otherwise a stolen token would
    stay live despite the password change."""
    use_test_db(monkeypatch, db)
    user = await make_user(db, "wechsler")
    token = create_access_token(user.id)
    # Clearly in the future, so that the test stays independent of the time zone of the
    # database (SQLite returns the timestamp naively).
    user.password_changed_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    await db.commit()

    ws = FakeWS()
    await office_ws(ws, token=token)
    assert ws.closed == CLOSE_FORBIDDEN
    assert ws.accepted is False


async def test_the_project_socket_rejects_a_token_from_before_the_password_change(db, monkeypatch):
    """Regression for `/api/projects/{id}/ws`: the same claim on the old socket. It did not
    do the check until this wave, and a revoked token got through."""
    use_test_db(monkeypatch, db)
    user = await make_user(db, "wechsler2")
    proj = await make_project(db, "WEC", "Wechsel")
    await add_member(db, proj, user, ProjectRole.member)
    token = create_access_token(user.id)
    user.password_changed_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    await db.commit()

    ws = FakeWS()
    await project_ws(ws, proj.id, token=token)
    assert ws.closed == 4403
    assert ws.accepted is False


async def test_the_socket_rejects_an_inactive_user(db, monkeypatch):
    use_test_db(monkeypatch, db)
    user = await make_user(db, "inaktiv")
    token = create_access_token(user.id)
    user.status = UserStatus.disabled
    await db.commit()

    ws = FakeWS()
    await office_ws(ws, token=token)
    assert ws.closed == CLOSE_FORBIDDEN


async def test_the_project_socket_rejects_an_inactive_user(db, monkeypatch):
    use_test_db(monkeypatch, db)
    user = await make_user(db, "inaktiv2")
    proj = await make_project(db, "INA", "Inaktiv")
    await add_member(db, proj, user, ProjectRole.member)
    token = create_access_token(user.id)
    user.status = UserStatus.disabled
    await db.commit()

    ws = FakeWS()
    await project_ws(ws, proj.id, token=token)
    assert ws.closed == 4403


# ── Protokoll ────────────────────────────────────────────────────────────────

async def test_hello_and_subscribe(db, monkeypatch):
    """Connect, then `hello` with the server set; `subscribe` narrows and is confirmed. The
    confirmation runs through the same queue as the events: on a socket only the pump
    writes."""
    use_test_db(monkeypatch, db)
    import app.api.office_ws as rtws
    user = await make_user(db, "hallo")
    proj = await make_project(db, "HAL", "Hallo")
    await add_member(db, proj, user, ProjectRole.member)
    token = create_access_token(user.id)

    ws = FakeWS(['{"type":"subscribe","scopes":[{"kind":"project","id":%d}]}' % proj.id])
    seen: list[_Conn] = []
    real_add = rtws.manager.add
    monkeypatch.setattr(rtws.manager, "add",
                        lambda c: (seen.append(c), real_add(c))[1])

    await office_ws(ws, token=token)   # ends with WebSocketDisconnect

    assert ws.accepted is True
    assert ws.sent[0]["type"] == "hello"
    assert ws.sent[0]["projects"] == [proj.id]
    assert seen and seen[0].scope == {proj.id}
    # The confirmation lies in the queue (the pump was stopped while tearing down).
    assert seen[0].queue.get_nowait() == {"type": "subscribed", "scope": [proj.id]}
    assert seen[0] not in rtws.manager.conns   # sauber abgemeldet
