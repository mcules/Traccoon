"""The note workspace, answered here rather than passed on.

This is the port growing in next to the bridge, not replacing it. `api/notes.py`
keeps serving `/api/notes/*` from the old service and the interface keeps using
it; this router answers the same questions under `/api/notes-native/*` so the two
can be asked the same thing and their answers compared, route by route, against
the real vault.

Nothing points at it yet. When a route here matches the old one on every case
that was recorded, the bridge stops forwarding that one, and when the last one
has moved the bridge and this prefix both go and what is left is `/api/notes`.

Read only, on purpose. The vault is mounted read only in this service, and that
was a deliberate decision back when the other program still wrote into it. It
stays until the writing side here has been through review: an untested writer on
somebody's notes is not a thing to switch on quietly.
"""
from __future__ import annotations

import datetime as _dt
import json
import errno
import logging
import re
from pathlib import Path, PurePosixPath

from fastapi import (APIRouter, Depends, File, Form, Header, Query, Request,
                     Response, UploadFile, WebSocket, WebSocketDisconnect, status)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.security import decrypt_secret, encrypt_secret
from ..db import SessionLocal, get_session
from ..models.enums import UserStatus
from ..models.notes import NotesCalendar
from ..models.user import User
from ..notes import live, paths, tickets
from ..core.timezones import zone_of
from ..config import settings
from ..notes import appearance as note_appearance
from ..notes import drawings as note_drawings
from ..notes import graph as note_graph
from ..notes.model import note as note_model
from ..notes import properties as note_properties
from ..notes import history as note_history
from ..notes import templates as note_templates
from ..notes.calendar import caldav as cal_dav
from ..notes.calendar import daily as cal_daily
from ..notes.calendar import fetch as cal_fetch
from ..notes.calendar import store as cal_store
from ..notes.dv import settings as note_settings
from ..notes.dv import tasks as dv_tasks
from ..notes.dv import tasktoggle as dv_toggle
from ..notes.dv.bases import run as dv_bases
from ..notes.dv.dql import evaluate_inline, execute as run_query, file_object
from ..notes.query import matches as note_matches
from ..notes.query.run import run as run_search
from ..notes.registry import vault_of, workspace_of
from ..core import scopes as scopes_mod
from ..services import api_tokens
from ..notes.settings import options as vault_options
from ..notes.vault import write as vault_write
from ..notes.vault.files import content_hash, is_text, mime_for
from ..notes.workspace import Conflict
from .deps import get_current_user

log = logging.getLogger("notes")

router = APIRouter(prefix="/notes-native", tags=["notes"])


async def browser_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Who is asking, for the requests a browser makes on its own.

    A picture is an `<img src>` and a compiled template arrives through
    `import()`; neither carries an `Authorization` header, because the browser
    decides what goes on such a request and it sends cookies. So a GET may also
    identify itself with the reading ticket — which is worth nothing but reading
    this one person's notes, and nothing at all for a POST.
    """
    if authorization:
        return await get_current_user(request, authorization, db)
    if request.method == "GET":
        uid = tickets.holder(request.cookies.get(tickets.COOKIE) or "")
        if uid is not None:
            user = await db.get(User, uid)
            if user is not None and user.status == UserStatus.active:
                # The ticket carries no scopes: it opens reading and nothing more.
                request.state.scopes = None
                return user
    raise Error(status.HTTP_401_UNAUTHORIZED, "err.not_authenticated",
                "Not authenticated")

# The link graph, per vault. Reading six thousand notes on every request is not
# a cache decision, it is the difference between an answer and a timeout.
#
# Built when a vault is first asked about and followed from then on. Building it
# on first use rather than at startup keeps a vault that nobody opens out of the
# way; the watcher next to it is what stops the copy from drifting away from the
# disk, which is a failure that says nothing while it happens.
def _guard(fn, rel: str):
    try:
        return fn()
    except paths.OutsideVault:
        # Deliberately the same answer as for a file that is not there. Telling
        # the two apart would say whether a path exists outside the vault, which
        # is exactly what somebody probing would want to learn.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        # A folder asked for as a note is the same answer as a note that is not
        # there. It used to be a 500, which says "something here is broken" for
        # what is only somebody asking for the wrong thing.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None


@router.get("/files/")
async def tree(user: User = Depends(get_current_user)) -> dict:
    return vault_of(user).tree().as_json()


@router.get("/files/content")
async def content(path: str = Query(...), user: User = Depends(browser_user)):
    """A note as text, or a file as bytes.

    The hash travels with the text: the editor keeps it as the identity of the
    version it is working on and hands it back when it saves, which is how a save
    tells "still the file I read" from "somebody got there first".

    A path that is not there is tried once more as a bare name. An embed is
    written `![[picture.jpg]]` without saying which folder it lives in, and
    without this every one of them would be a broken image.
    """
    ws = workspace_of(user)
    v = ws.vault
    if not vault_write.exists(v, path):
        found = ws.vault.by_basename().get(PurePosixPath(path).name.lower())
        if found:
            path = found[0]
    if is_text(path):
        text = _guard(lambda: v.read_text(path), path)
        return {"path": path, "content": text, "encoding": "utf8",
                "hash": content_hash(text)}
    data = _guard(lambda: v.read_bytes(path), path)
    return Response(content=data, media_type=mime_for(path))


@router.get("/files/stat")
async def stat(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    v = vault_of(user)
    return {"path": path, **_guard(lambda: v.stat(path), path)}


@router.get("/tags")
async def tags(user: User = Depends(get_current_user)) -> dict:
    return {"tags": workspace_of(user).graph.all_tags()}


@router.get("/backlinks")
async def backlinks(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    return {"path": path, "backlinks": workspace_of(user).graph.backlinks(path)}


@router.get("/resolve")
async def resolve_link(target: str = Query(...),
                       user: User = Depends(get_current_user)) -> dict:
    """Turn a link into a file, or say that it points at nothing yet.

    A link with an extension that is not a note (`[[Foo.canvas]]`) is not in the
    graph, which only holds notes, so the file index answers for those. A bare
    `[[Foo]]` deliberately does not fall through to it: it should stay unresolved
    so the interface can offer to create the note.
    """
    ws = workspace_of(user)
    found = ws.graph.resolve(target)
    if found is None and "." in PurePosixPath(target).name:
        suffix = PurePosixPath(target).suffix.lower()
        if suffix and suffix not in (".md", ".markdown"):
            hits = ws.vault.by_basename().get(PurePosixPath(target).name.lower())
            found = hits[0] if hits else None
    return {"target": target, "path": found}


@router.get("/search")
async def search(q: str = Query(""), limit: int | None = Query(None),
                 user: User = Depends(get_current_user)) -> dict:
    """Search the vault. Without a limit every match comes back — the panel
    renders them a screenful at a time and a cut here would hide the tail."""
    ws = workspace_of(user)
    hits = run_search(ws.graph, q, ws.words)
    total = len(hits)
    if limit and limit > 0:
        hits = hits[:limit]
    return {"query": q, "total": total, "hits": [h.as_json() for h in hits]}


@router.get("/health", response_class=PlainTextResponse)
async def health(user: User = Depends(get_current_user)) -> str:
    """Enough to see that the vault is reachable and how much is in it."""
    v = vault_of(user)
    count = len(paths.walk(v.root))
    return f"{v.name}: {count} files"


# ---------------------------------------------------------- the three little
# ------------------------------------------------------------------ languages

class QueryIn(BaseModel):
    query: str = ""
    # The note the block sits in. A query says `this.file` and means the note
    # around it, so the same text in two notes is two questions.
    path: str | None = None


class TasksIn(BaseModel):
    query: str = ""


class InlineOne(BaseModel):
    expr: str = ""
    path: str | None = None


class InlineIn(BaseModel):
    expr: str = ""
    path: str | None = None
    items: list[InlineOne] | None = None


@router.post("/dataview/query")
async def query(body: QueryIn, user: User = Depends(get_current_user)) -> dict:
    """Run one query block. A broken query answers with its own error rather
    than a 500: it is the person who wrote it who can fix it."""
    return run_query(workspace_of(user).pages, body.query, body.path)


@router.post("/dataview/tasks")
async def task_query(body: TasksIn, user: User = Depends(get_current_user)) -> dict:
    return dv_tasks.execute(workspace_of(user).pages, body.query)


@router.post("/dataview/inline")
async def inline(body: InlineIn, user: User = Depends(get_current_user)) -> dict:
    """Evaluate `= expression` against the note it sits in.

    Takes one or a list of them: a note with twenty should cost one round trip
    and not twenty.
    """
    index = workspace_of(user).pages
    if body.items is not None:
        return {"results": [evaluate_inline(index, it.expr, it.path)
                            for it in body.items[:500]]}
    return evaluate_inline(index, body.expr, body.path)


@router.get("/dataview/settings")
async def language_settings(user: User = Depends(get_current_user)) -> dict:
    """How the two languages are configured, so the interface shows a value the
    way the notes were written to expect it."""
    vault_of(user)
    return {"query": note_settings.query_settings(),
            "tasks": note_settings.task_settings(),
            "statuses": note_settings.statuses()}


@router.get("/dataview/pages")
async def pages(source: str = Query(""), user: User = Depends(get_current_user)) -> dict:
    """The notes themselves. `source` narrows to a folder (`"03 Areas"`) or a
    tag, so a block scoped to one folder does not drag the vault over the wire."""
    index = workspace_of(user).pages
    listed = index.all()
    text = source.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        folder = text[1:-1].rstrip("/")
        if folder:
            listed = [p for p in listed
                      if p.path == folder or p.path.startswith(folder + "/")]
    elif text.startswith("#"):
        want = text.lower()
        listed = [p for p in listed if any(t.lower() == want
                                           or t.lower().startswith(want + "/")
                                           for t in p.tags)]
    return {"pages": [{"file": file_object(index, p), "fields": p.fields}
                      for p in listed],
            "total": len(index.pages)}


@router.get("/dataview/page")
async def one_page(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    index = workspace_of(user).pages
    p = index.get(path) or index.by_link(path)
    if p is None:
        return {"page": None}
    return {"page": {"file": file_object(index, p), "fields": p.fields}}


@router.get("/dataview/meta")
async def page_meta(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    """Headings with their line numbers, which is what lets a link to a heading
    land on the right line."""
    index = workspace_of(user).pages
    p = index.get(path) or index.by_link(path)
    if p is None:
        return {"path": None}
    return {"path": p.path, "headings": p.headings, "tags": p.tags,
            "frontmatter": p.fields}


@router.get("/bases/view")
async def base_view(path: str = Query(...), view: str | None = Query(None),
                    user: User = Depends(get_current_user)) -> dict:
    """A table file, run over the same notes the query language reads."""
    ws = workspace_of(user)
    source = _guard(lambda: ws.vault.read_text(path), path)
    return dv_bases.run(ws.pages, source, view, user.locale or "en")


# ------------------------------------------------------------------- writing
#
# The vault is mounted read only in this service and stays that way until the
# writing side here has been through review. That is not caution for its own
# sake: these are somebody's notes, the folder has several writers, and a
# mistake here is not a wrong answer but a lost sentence. Until the mount
# changes every route below answers with the same refusal from the file system,
# which is the correct behaviour for a switch nobody has thrown yet.


class SaveIn(BaseModel):
    path: str
    content: str
    # The version the caller last read. Without it a save overwrites whatever
    # arrived in between — from another device, an agent, a job — and nothing
    # anywhere says that it did.
    baseHash: str | None = None


class PathIn(BaseModel):
    path: str


class RenameIn(BaseModel):
    from_: str = Field(alias="from")
    to: str
    dryRun: bool = False

    model_config = {"populate_by_name": True}


class CopyIn(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class SnapshotIn(BaseModel):
    path: str
    ts: int


def _writing(fn, rel: str):
    """Turn what the file system says into an answer a person can read."""
    try:
        return fn()
    except Conflict as clash:
        # The text that is there now travels with the refusal, so the editor can
        # merge instead of asking again and guessing. It rides in `values`,
        # which the house's error shape already carries to the browser — a
        # deliberate use of it for something larger than a placeholder, because
        # a second round trip here means the note has moved on again by the time
        # the answer arrives.
        raise Error(status.HTTP_409_CONFLICT, "err.notes_changed_on_disk",
                    "The note changed on disk: {path}", path=rel,
                    current=clash.current, hash=clash.hash) from None
    except paths.OutsideVault:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None
    except FileNotFoundError:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None
    except FileExistsError:
        raise Error(status.HTTP_409_CONFLICT, "err.notes_exists",
                    "There is already something there: {path}", path=rel) from None
    except vault_write.NotInTrash:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_not_in_trash",
                    "That is not in the trash: {path}", path=rel) from None
    except PermissionError:
        raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.notes_read_only",
                    "The vault is mounted read only here") from None
    except OSError as err:
        if err.errno == errno.EROFS:
            raise Error(status.HTTP_503_SERVICE_UNAVAILABLE, "err.notes_read_only",
                        "The vault is mounted read only here") from None
        raise


@router.put("/files/content")
async def save(body: SaveIn, user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(lambda: ws.save(body.path, body.content, body.baseHash), body.path)


@router.post("/files/folder")
async def make_folder(body: PathIn, user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(lambda: ws.create_folder(body.path), body.path)


@router.post("/files/upload")
async def upload(file: UploadFile = File(...), dir: str | None = Form(None),
                 note: str = Form(""),
                 user: User = Depends(get_current_user)) -> dict:
    """Put a file into the vault, where the vault keeps its attachments."""
    ws = workspace_of(user)
    data = await file.read()
    name = file.filename or "datei"
    return _writing(lambda: ws.upload(name, data, folder=dir, note=note), name)


@router.patch("/files/rename")
async def rename(body: RenameIn, user: User = Depends(get_current_user)) -> dict:
    """Move a note or a folder, and take every link that points at it along.

    With `dryRun` nothing is written and the caller gets the list of what would
    change — which is how a change of this size should be looked at first.
    """
    ws = workspace_of(user)
    return _writing(lambda: ws.rename(body.from_, body.to, dry_run=body.dryRun),
                    body.from_)


@router.post("/files/copy")
async def copy(body: CopyIn, user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(lambda: ws.copy(body.from_, body.to), body.to)


@router.delete("/files/")
async def delete(path: str = Query(...), user: User = Depends(get_current_user)) -> dict:
    """Delete, which by default means moving into the trash."""
    ws = workspace_of(user)
    return _writing(lambda: ws.delete(path), path)


@router.get("/files/trash")
async def trash_list(user: User = Depends(get_current_user)) -> dict:
    return {"items": workspace_of(user).trash_items()}


@router.post("/files/trash/restore")
async def trash_restore(body: PathIn, user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(lambda: ws.restore(body.path), body.path)


@router.delete("/files/trash/item")
async def trash_delete(path: str = Query(...),
                       user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(lambda: ws.delete_from_trash(path), path)


@router.delete("/files/trash")
async def trash_empty(user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    return _writing(ws.empty_trash, "")


@router.get("/files/recovery")
async def recovery_list(path: str = Query(...),
                        user: User = Depends(get_current_user)) -> dict:
    return {"snapshots": workspace_of(user).snapshots(path)}


@router.get("/files/recovery/content")
async def recovery_content(path: str = Query(...), ts: int = Query(...),
                           user: User = Depends(get_current_user)) -> dict:
    text = workspace_of(user).snapshot_text(path, ts)
    if text is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_snapshot",
                    "No kept version of {path} from then", path=path)
    return {"content": text}


@router.post("/files/recovery/restore")
async def recovery_restore(body: SnapshotIn,
                           user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    out = _writing(lambda: ws.restore_snapshot(body.path, body.ts), body.path)
    if out is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_snapshot",
                    "No kept version of {path} from then", path=body.path)
    return out


# ---------------------------------------------------------------- settings
#
# The note area has no settings page of its own. What a person can decide sits
# in their account with everything else personal, and what the house decides
# sits in the house settings. A second settings world beside those two is how
# somebody ends up looking for the same switch in three places.


class PrefsIn(BaseModel):
    trash: str | None = None
    delete_mode: str | None = None
    default_view: str | None = None
    search_fuzzy: float | None = None
    search_prefix: bool | None = None
    folder_colours: str | None = None
    folder_colour_opacity: float | None = None


@router.get("/prefs")
async def prefs(user: User = Depends(get_current_user)) -> dict:
    """What this person set, with the defaults filled in for what they did not."""
    out = vault_options.defaults()
    out.update({k: v for k, v in (user.notes_prefs or {}).items() if k in out})
    return out


@router.put("/prefs")
async def save_prefs(body: PrefsIn, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_session)) -> dict:
    """Change some of them. What the call does not name is left alone.

    A value that is not one of the allowed ones is dropped rather than refused:
    it comes back as the default on the next read, and a malformed preference
    must not be able to stop somebody reading their notes.
    """
    kept = dict(user.notes_prefs or {})
    for name, value in body.model_dump(exclude_none=True).items():
        kept[name] = value
    clean = vault_options.from_user(kept, Path(user.vault_path or "/"), "")
    user.notes_prefs = {name: getattr(clean, name) for name in vault_options.PREF_FIELDS}
    await db.commit()
    return user.notes_prefs


@router.get("/uistate")
async def ui_state(user: User = Depends(get_current_user)) -> dict:
    """The workspace: what was open, which panel, what was unfolded.

    On the person and not in the browser, so whoever logs in at the other
    machine in the evening carries on where they left off.
    """
    return user.notes_ui_state or {}


@router.put("/uistate")
async def save_ui_state(body: dict, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)) -> dict:
    user.notes_ui_state = body if isinstance(body, dict) else {}
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- calendars


class CalendarIn(BaseModel):
    name: str = ""
    url: str = ""
    link_target: str = ""
    auth_user: str = ""
    # Absent leaves the stored one alone; an empty string clears it. Those are
    # two different wishes and a single field cannot carry both.
    auth_password: str | None = None
    enabled: bool = True
    position: int = 0


def _calendar_json(row: NotesCalendar) -> dict:
    """A calendar as it goes out. The password never does — only whether one is
    set, which is all an interface needs to show."""
    return {"id": row.id, "name": row.name, "url": row.url,
            "link_target": row.link_target, "auth_user": row.auth_user,
            "has_password": bool(row.auth_password_enc),
            "enabled": row.enabled, "position": row.position}


async def _own_calendar(db: AsyncSession, user: User, cid: int) -> NotesCalendar:
    row = (await db.execute(select(NotesCalendar).where(
        NotesCalendar.id == cid,
        NotesCalendar.owner_user_id == user.id))).scalar_one_or_none()
    if row is None:
        # The same answer for somebody else's calendar as for one that is not
        # there: telling the two apart would say that it exists.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_calendar_not_found",
                    "No such calendar")
    return row


@router.get("/calendars")
async def calendars(user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)) -> dict:
    rows = (await db.execute(
        select(NotesCalendar).where(NotesCalendar.owner_user_id == user.id)
        .order_by(NotesCalendar.position, NotesCalendar.id))).scalars().all()
    return {"calendars": [_calendar_json(r) for r in rows]}


@router.post("/calendars", status_code=status.HTTP_201_CREATED)
async def add_calendar(body: CalendarIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)) -> dict:
    if not body.url.strip():
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_calendar_no_url",
                    "A calendar needs an address")
    row = NotesCalendar(
        owner_user_id=user.id, name=body.name.strip(), url=body.url.strip(),
        link_target=body.link_target.strip(), auth_user=body.auth_user.strip(),
        auth_password_enc=encrypt_secret(body.auth_password) if body.auth_password else "",
        enabled=body.enabled, position=body.position)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _calendar_json(row)


@router.patch("/calendars/{cid}")
async def change_calendar(cid: int, body: CalendarIn,
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)) -> dict:
    row = await _own_calendar(db, user, cid)
    given = body.model_dump(exclude_unset=True)
    for name in ("name", "url", "link_target", "auth_user"):
        if name in given:
            setattr(row, name, str(given[name]).strip())
    for name in ("enabled", "position"):
        if name in given:
            setattr(row, name, given[name])
    if "auth_password" in given:
        row.auth_password_enc = (encrypt_secret(given["auth_password"])
                                 if given["auth_password"] else "")
    await db.commit()
    return _calendar_json(row)


@router.delete("/calendars/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_calendar(cid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)) -> Response:
    row = await _own_calendar(db, user, cid)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- live channel


@router.websocket("/ws")
async def live_channel(websocket: WebSocket, token: str = "") -> None:
    """What changed in the vault, while a window is open.

    Same entrance as every request, through `api_tokens.authenticate` — a socket
    is neither a weaker nor a stronger way in. The token rides in the address
    because a browser cannot put a header on a socket; that is also why the
    bridge needed a cookie for it.
    """
    async with SessionLocal() as db:
        result = await api_tokens.authenticate(db, token)
        if result.user is None:
            await websocket.close(code=4401 if result.error in (
                api_tokens.BAD_TOKEN, api_tokens.BAD_UNKNOWN_USER) else 4403)
            return
        # Deny by default here too. No scope names this route, so a token made
        # for the tool server cannot listen in on somebody's window — only a
        # session, or a token that was deliberately given everything.
        if not scopes_mod.allowed(result.scopes, "GET", "/notes-native/ws"):
            await websocket.close(code=4403)
            return
        try:
            v = vault_of(result.user)
        except Error:
            await websocket.close(code=4404)
            return
        key = str(v.root)
        # Asking for the workspace is what starts the watcher, so a window that
        # is only listening still gets an index that follows the disk.
        workspace_of(result.user)

    await websocket.accept()
    live.join(key, websocket)
    try:
        while True:
            # One way. Receiving happens only so a disconnect is noticed.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        live.leave(key, websocket)


# ----------------------------------------------------------------- calendar
#
# The reading half. Writing an appointment back and carrying a day into its
# daily note follow; both need a way in to the calendar itself rather than to
# its feed, which is a different protocol and a different piece of work.


async def _calendar_sources(db: AsyncSession, user: User) -> list[cal_fetch.Source]:
    """This person's calendars, as the fetcher wants them.

    The password is decrypted here and nowhere else — it exists as plain text
    for the length of one fetch and never leaves this process.
    """
    rows = (await db.execute(
        select(NotesCalendar).where(NotesCalendar.owner_user_id == user.id,
                                    NotesCalendar.enabled.is_(True))
        .order_by(NotesCalendar.position, NotesCalendar.id))).scalars().all()
    out = []
    for row in rows:
        password = ""
        if row.auth_password_enc:
            try:
                password = decrypt_secret(row.auth_password_enc)
            except Exception:                     # noqa: BLE001
                log.warning("notes: the password of calendar %s cannot be read", row.id)
        out.append(cal_fetch.Source(name=row.name or f"Kalender {row.id}", url=row.url,
                                    link_target=row.link_target,
                                    auth_user=row.auth_user, auth_password=password))
    return out


@router.get("/calendar")
async def calendar(from_: str = Query("", alias="from"), to: str = Query(""),
                   user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)) -> dict:
    """Everything in the fetched window, for the calendar view."""
    sources = await _calendar_sources(db, user)
    snapshot = await cal_store.ensure(user.id, sources, zone=zone_of(user))
    events = [e for e in snapshot.events
              if (not from_ or e.date >= from_) and (not to or e.date <= to)]
    return {"events": [e.as_json() for e in events],
            "errors": snapshot.errors,
            "fetchedAt": snapshot.fetched_at,
            "calendars": [{"name": s.name, "linkTarget": s.link_target}
                          for s in sources]}


@router.get("/calendar/day")
async def calendar_day(date: str = Query(""), user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)) -> dict:
    sources = await _calendar_sources(db, user)
    snapshot = await cal_store.ensure(user.id, sources, zone=zone_of(user))
    return {"events": [e.as_json() for e in cal_fetch.on_day(snapshot, date)]}


@router.post("/calendar/refresh")
async def calendar_refresh(user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_session)) -> dict:
    """Fetch again now. What is held is otherwise a quarter of an hour old at
    most, which is right for a subscription and wrong for the moment somebody
    knows they have just changed something."""
    sources = await _calendar_sources(db, user)
    snapshot = await cal_store.ensure(user.id, sources, force=True, zone=zone_of(user))
    return {"count": len(snapshot.events), "errors": snapshot.errors,
            "fetchedAt": snapshot.fetched_at}


class TestSourceIn(BaseModel):
    url: str
    auth_user: str = ""
    auth_password: str = ""
    name: str = "Test"


@router.post("/calendar/test-source")
async def calendar_test(body: TestSourceIn,
                        user: User = Depends(get_current_user)) -> dict:
    """Read one feed once and say what came back, without saving anything.

    What somebody wants to know before adding a calendar is whether the address
    works, and a count of appointments answers that better than a status code.
    """
    source = cal_fetch.Source(name=body.name, url=body.url,
                              auth_user=body.auth_user, auth_password=body.auth_password)
    try:
        text = await cal_fetch.read_feed(source)
    except Exception as err:                      # noqa: BLE001
        return {"ok": False, "message": str(err)}
    try:
        import datetime as _dt
        zone = zone_of(user)
        today = _dt.datetime.now(zone).date()
        found = cal_fetch.expand(text, body.name, today - _dt.timedelta(days=30),
                                 today + _dt.timedelta(days=90), zone)
    except Exception as err:                      # noqa: BLE001
        return {"ok": False, "message": f"not a calendar: {err}"}
    return {"ok": True, "count": len(found),
            "sample": [e.title for e in found[:3]]}


class SyncDayIn(BaseModel):
    date: str
    # Nothing is written and the caller learns what would change. A change of
    # this shape — the server editing somebody's note — should be lookable at
    # before it happens.
    dryRun: bool = False


def _event_templates(ws, options) -> list[cal_daily.Template]:
    """The agendas that belong under a recurring appointment, read from the vault.

    A template that is not there is simply left out: a mapping can outlive the
    note it points at, and an appointment without its agenda is better than a
    refused sync.
    """
    out = []
    for pair in options.calendar_templates:
        rel = pair["template"]
        if not rel.lower().endswith((".md", ".markdown")):
            rel += ".md"
        try:
            lines = cal_daily.template_lines(ws.vault.read_text(rel))
        except (OSError, paths.OutsideVault):
            continue
        if lines:
            out.append(cal_daily.Template(match=pair["match"], lines=lines))
    return out


@router.post("/calendar/sync-day")
async def calendar_sync_day(body: SyncDayIn, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)) -> dict:
    """Write one day's appointments into its daily note.

    Only into a note that is already there. Creating one for every day in the
    window would fill the vault with empty notes nobody asked for, and a day
    without a note is a day nobody wrote about.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.date):
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_bad_date",
                    "A date is written YYYY-MM-DD: {given}", given=body.date)
    ws = workspace_of(user)
    options = ws.options
    rel = cal_daily.daily_note_path(_dt.date.fromisoformat(body.date),
                                    options.daily_folder, options.daily_format)
    if not vault_write.exists(ws.vault, rel):
        return {"path": rel, "added": 0, "updated": 0, "cancelled": 0, "written": False}

    sources = await _calendar_sources(db, user)
    snapshot = await cal_store.ensure(user.id, sources, zone=zone_of(user))
    events = cal_fetch.on_day(snapshot, body.date)

    content = _guard(lambda: ws.vault.read_text(rel), rel)
    # The old long form is folded down first, and this is not tidiness for its
    # own sake: a legacy line is not recognised as the appointment it describes,
    # so writing without folding puts the same appointment in the note a second
    # time. The side this replaces does exactly that, which is why some days
    # carry every appointment twice.
    folded = cal_daily.tidy_legacy_lines(content, {s.name for s in sources})
    result = cal_daily.apply_lines(
        folded.text, events, templates=_event_templates(ws, options),
        keep_strikes=body.date < _dt.datetime.now(zone_of(user)).date().isoformat())
    changed = result.text != content
    if changed and not body.dryRun:
        _writing(lambda: ws.save(rel, result.text), rel)
    return {"path": rel, "added": result.added, "updated": result.updated,
            "cancelled": result.cancelled, "folded": folded.changed,
            "written": changed and not body.dryRun}


class TidyIn(BaseModel):
    # Looking before writing is the default here, the other way round from
    # everything else: this one goes over every daily note at once.
    dryRun: bool = True


@router.post("/calendar/tidy")
async def calendar_tidy(body: TidyIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)) -> dict:
    """Fold the old long appointment form down, in every daily note.

    One pass, meant to be run once. Only notes below the daily-note folder are
    looked at, and only those that have an appointment section.
    """
    ws = workspace_of(user)
    names = {s.name for s in await _calendar_sources(db, user)}
    folder = ws.options.daily_folder
    prefix = f"{folder}/" if folder else ""
    files: list[dict] = []
    total = 0
    for rel in sorted(ws.graph.docs):
        if prefix and not rel.startswith(prefix):
            continue
        try:
            content = ws.vault.read_text(rel)
        except (OSError, paths.OutsideVault):
            continue
        # The cheap way past a note that cannot hold an appointment line. Not
        # "does it carry a control comment", which is what the side this
        # replaces asks: the previous folding left lines behind that have no
        # comment any more, and those are exactly the ones still to fold.
        if cal_daily.HEADING not in content:
            continue
        folded = cal_daily.tidy_legacy_lines(content, names)
        if folded.text == content:
            continue
        files.append({"path": rel, "changed": folded.changed})
        total += folded.changed
        if not body.dryRun:
            _writing(lambda t=folded.text, r=rel: ws.save(r, t), rel)
    return {"files": files, "total": total, "dryRun": body.dryRun}


# ------------------------------------------------------ the reading ticket


def _over_https(request: Request) -> bool:
    """Did this reach us over https? The proxy in front is what knows."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return (forwarded or request.url.scheme) == "https"


@router.post("/session")
async def open_session(request: Request, response: Response,
                       user: User = Depends(get_current_user)) -> dict:
    """Hand out the reading ticket. The note area asks for this when it opens.

    It is what carries the requests a browser makes on its own — a picture, a
    stylesheet, a compiled block — none of which take a header of ours.
    """
    vault_of(user)                      # refuses here if this account has none
    ticket = tickets.issue(user.id)
    for path in tickets.PATHS:
        response.set_cookie(
            tickets.COOKIE, ticket, max_age=tickets.TTL, httponly=True,
            samesite="lax",
            # `Secure` only where it can be honoured. A browser silently throws
            # a secure cookie away on a plain connection, and the whole area
            # then looks broken for a reason nothing reports.
            secure=_over_https(request),
            path=path)
    return {"ok": True}


@router.post("/session/end")
async def close_session(response: Response,
                        _user: User = Depends(get_current_user)) -> dict:
    for path in tickets.PATHS:
        response.delete_cookie(tickets.COOKIE, path=path)
    return {"ok": True}


# ------------------------------------------- ticking a task off, and the blocks


class ToggleIn(BaseModel):
    path: str
    line: int
    # What the caller believes stands there. A result can be a minute old, and
    # the note may have been written since.
    text: str = ""
    checked: bool = False
    # "tasks" writes a done date and brings the next instance of a recurring
    # task with it; "dataview" only flips the box, because that language's own
    # completion tracking is off.
    mode: str = "dataview"


@router.post("/dataview/task")
async def toggle_task(body: ToggleIn, user: User = Depends(get_current_user)) -> dict:
    """Tick a task off in the note it lives in."""
    if not body.path.strip() or body.line < 0:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_path_required",
                    "Which note?")
    ws = workspace_of(user)
    raw = _guard(lambda: ws.vault.read_text(body.path), body.path)
    lines = raw.split("\n")
    if body.line >= len(lines):
        raise Error(status.HTTP_409_CONFLICT, "err.notes_task_moved",
                    "That task is no longer where it was — open the note again")
    # Windows line endings stay: the carriage return is taken off for matching
    # and put back for writing, so ticking one task off does not rewrite every
    # line of the file for everything syncing it.
    written = lines[body.line]
    cr = "\r" if written.endswith("\r") else ""
    current = written[:-1] if cr else written

    said = dv_toggle.text_of(current)
    if said is None or (body.text and said.strip() != body.text.strip()):
        raise Error(status.HTTP_409_CONFLICT, "err.notes_task_moved",
                    "That task is no longer where it was — open the note again")
    out = dv_toggle.toggled(current, body.checked, body.mode)
    if out is None:
        raise Error(status.HTTP_409_CONFLICT, "err.notes_task_moved",
                    "That task is no longer where it was — open the note again")
    lines[body.line:body.line + 1] = [f"{l}{cr}" for l in out]
    _writing(lambda: ws.save(body.path, "\n".join(lines)), body.path)
    return {"ok": True, "recurred": len(out) > 1}


class ToggleLinesIn(BaseModel):
    line: str
    checked: bool = False
    mode: str = "tasks"


@router.post("/dataview/task/lines")
async def toggle_task_lines(body: ToggleLinesIn,
                            user: User = Depends(get_current_user)) -> dict:
    """What a tick would produce — worked out, never written.

    When the task sits in the note somebody has open, the change has to go
    through the editor's own document: writing the file behind its back leaves
    a stale buffer that saves the tick away again a moment later.
    """
    out = dv_toggle.toggled(body.line, body.checked, body.mode)
    if out is None:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_not_a_task",
                    "That line is not a task")
    return {"lines": out}


# The blocks of the query language that are program rather than query. The page
# may not build a function from a string — its content policy forbids it — so
# the code is registered here and handed back as a real module from this origin.
# What is held is only ever code an authenticated session just sent, and a
# session that can register code can equally well write it into a note.
_scripts = note_templates.Modules()
LONGEST_SCRIPT = 200_000


class ScriptIn(BaseModel):
    code: str


@router.post("/dataview/script")
async def register_script(body: ScriptIn, user: User = Depends(get_current_user)) -> dict:
    if len(body.code) > LONGEST_SCRIPT:
        raise Error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "err.notes_script_too_long",
                    "That block is too long to run")
    wrapped = ("export default async function block(dv, app, input, container) {\n"
               f"{body.code}\n}}\n")
    return {"id": _scripts.put(note_templates.Compiled(code=wrapped, interactive=False))}


@router.get("/dataview/script/{script_id}.mjs")
async def serve_script(script_id: str, user: User = Depends(browser_user)):
    code = _scripts.get(script_id)
    if code is None:
        return PlainTextResponse(
            'export default async function () {\n'
            '  throw new Error("This block is no longer held — open the note again.");\n'
            '}\n',
            status_code=status.HTTP_404_NOT_FOUND, media_type="text/javascript")
    return PlainTextResponse(code, media_type="text/javascript",
                             headers={"Cache-Control": "private, max-age=300"})


# ---------------------------------------------------- what the vault decides
#
# Not settings of this program: these are the vault's own, and they are read
# rather than owned. A vault opened somewhere else has to behave the same way,
# which is why the tab width, the attachment folder and the key bindings come
# out of files in the vault and not out of a table here.

HOTKEYS_FILE = "hotkeys.json"


@router.get("/vault-config")
async def vault_config(user: User = Depends(get_current_user)) -> dict:
    """How the vault says its notes are written and read."""
    ws = workspace_of(user)
    o = ws.options
    hotkeys: dict = {}
    if settings.notes_config_dir:
        try:
            found = json.loads(ws.vault.read_text(
                f"{settings.notes_config_dir}/{HOTKEYS_FILE}"))
            hotkeys = found if isinstance(found, dict) else {}
        except (OSError, ValueError, paths.OutsideVault):
            hotkeys = {}
    return {
        "app": {"attachmentFolderPath": o.attachment_folder,
                "alwaysUpdateLinks": o.follow_links_on_rename,
                "readableLineLength": o.readable_line_length,
                "mobileToolbarCommands": o.mobile_toolbar,
                "useTab": o.use_tab,
                "tabSize": o.tab_size,
                "showUnsupportedFiles": o.show_unsupported_files,
                "showInlineTitle": o.show_inline_title},
        "dailyNotes": {"folder": o.daily_folder, "format": o.daily_format,
                       "template": o.daily_template},
        "templates": {"folder": o.templates_folder,
                      "dateFormat": o.template_date_format,
                      "timeFormat": o.template_time_format},
        "hotkeys": hotkeys,
    }


class HotkeysIn(BaseModel):
    hotkeys: dict


@router.put("/vault-config/hotkeys")
async def save_hotkeys(body: HotkeysIn, user: User = Depends(get_current_user)) -> dict:
    """Put the key bindings back into the vault.

    Into the vault and not into this database, because they belong to the notes
    rather than to this program: whatever else opens the vault reads the same
    file, and a binding set here is set there.
    """
    if not settings.notes_config_dir:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_no_vault_config",
                    "This vault has no configuration folder")
    rel = f"{settings.notes_config_dir}/{HOTKEYS_FILE}"
    text = json.dumps(body.hotkeys, ensure_ascii=False, indent=2) + "\n"
    _writing(lambda: vault_write.write_text(vault_of(user), rel, text), rel)
    return {"ok": True}


@router.get("/appearance")
async def appearance(user: User = Depends(get_current_user)) -> dict:
    """The CSS the vault carries, and how the tree is coloured."""
    ws = workspace_of(user)
    return note_appearance.info(Path(ws.vault.root), settings.notes_config_dir,
                                settings.notes_style_settings_dir,
                                chosen_style=ws.options.folder_colours,
                                chosen_opacity=ws.options.folder_colour_opacity)


@router.get("/appearance/snippet/{name}.css")
async def appearance_snippet(name: str, user: User = Depends(browser_user)):
    """One snippet, as CSS, so a `<link>` can pull it in.

    A `<link>` carries no header of ours, hence the reading ticket. The name is
    checked against what a name may be and never used to walk a filesystem.
    """
    css = note_appearance.snippet(Path(vault_of(user).root),
                                  settings.notes_config_dir, name)
    if css is None:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=f"{name}.css")
    return PlainTextResponse(css, media_type="text/css",
                             headers={"Cache-Control": "private, max-age=60"})


class DailyIn(BaseModel):
    # Days from today, so yesterday is -1 and tomorrow 1.
    offset: int = 0


@router.post("/files/daily")
async def daily_note(body: DailyIn, user: User = Depends(get_current_user)) -> dict:
    """The daily note of a day, made from its template if it is not there yet.

    Where it goes and what it is called come from the vault: that folder is
    already full of notes with those names, and a second opinion here would put
    tomorrow's note somewhere nobody looks.
    """
    ws = workspace_of(user)
    o = ws.options
    day = _dt.datetime.now(zone_of(user)) + _dt.timedelta(days=body.offset)
    rel = cal_daily.daily_note_path(day.date(), o.daily_folder, o.daily_format)
    if vault_write.exists(ws.vault, rel):
        return {"path": rel, "created": False, "unresolved": []}

    title = PurePosixPath(rel).name.rsplit(".", 1)[0]
    text, unresolved = "", []
    if o.daily_template:
        template = o.daily_template
        if not template.lower().endswith((".md", ".markdown")):
            template += ".md"
        try:
            filled = note_templates.fill(ws.vault.read_text(template), title=title,
                                         now=day.replace(tzinfo=None),
                                         date_format=o.template_date_format,
                                         time_format=o.template_time_format)
            text, unresolved = filled.text, filled.unresolved
        except (OSError, paths.OutsideVault):
            # A template that is gone is no reason to refuse the note: an empty
            # one is still the note somebody asked for.
            text = ""
    _writing(lambda: ws.save(rel, text), rel)
    return {"path": rel, "created": True, "unresolved": unresolved}


# ------------------------------------------------- what a search found, where


class MatchesIn(BaseModel):
    query: str = ""
    paths: list[str] = []
    matchCase: bool = False
    # One needle rather than one per word: an unlinked mention looks for the
    # note's whole title, not for each word of it separately.
    phrase: bool = False


# How many notes one call will read. The interface asks in batches as it draws
# them, so this is a ceiling against a caller asking for the whole vault.
MOST_PATHS = 80


@router.post("/search/matches")
async def search_matches(body: MatchesIn, user: User = Depends(get_current_user)) -> dict:
    """Where in each of these notes the search words stand.

    Asked for separately from the result list because it means reading the notes
    again — a search that did that for every hit before showing anything would
    show nothing for a while.
    """
    terms = ([body.query.strip()] if body.phrase and len(body.query.strip()) >= 2
             else note_matches.terms_of_query(body.query))
    ws = workspace_of(user)
    out = []
    for rel in body.paths[:MOST_PATHS]:
        # Read afresh rather than out of the index: what is marked is the body,
        # and the index keeps the raw text because a search looks into the
        # properties block as well. Reading here is also why this is a call of
        # its own — it happens for the notes actually drawn, not for every hit.
        try:
            note = note_model.parse(rel, ws.vault.read_text(rel))
        except (OSError, paths.OutsideVault):
            out.append({"path": rel, "count": 0, "contexts": []})
            continue
        count, contexts = note_matches.in_body(note.body, terms,
                                               case_sensitive=body.matchCase)
        out.append({"path": rel, "count": count,
                    "contexts": [c.as_json() for c in contexts]})
    return {"matches": out}


@router.get("/properties")
async def properties(user: User = Depends(get_current_user)) -> dict:
    """Every property the notes carry, with the type most of them use."""
    ws = workspace_of(user)
    return {"properties": note_properties.in_use(
        [doc.frontmatter for doc in ws.graph.docs.values()])}


@router.get("/property-types")
async def property_types(user: User = Depends(get_current_user)) -> dict:
    """The types set in the vault, as the vault itself stores them."""
    return {"types": note_properties.assigned(Path(vault_of(user).root),
                                              settings.notes_config_dir)}


class PropertyTypeIn(BaseModel):
    key: str
    type: str


@router.post("/property-types")
async def set_property_type(body: PropertyTypeIn,
                            user: User = Depends(get_current_user)) -> dict:
    if not body.key.strip() or not body.type.strip():
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_property_incomplete",
                    "A property type needs a name and a kind")
    return {"types": _writing(
        lambda: note_properties.assign(Path(vault_of(user).root),
                                       settings.notes_config_dir,
                                       body.key.strip(), body.type.strip()),
        body.key)}


@router.get("/graph")
async def graph(user: User = Depends(get_current_user)) -> dict:
    """The vault as a picture: notes, attachments, and links pointing nowhere."""
    return note_graph.build(workspace_of(user).graph)


@router.post("/reindex")
async def reindex(user: User = Depends(get_current_user)) -> dict:
    """Read the whole vault again.

    Not something the normal way of working needs — the watcher keeps the
    indexes in step. It is for the case where somebody has reason to believe
    they are not, and wants to stop wondering.
    """
    ws = workspace_of(user)
    ws.rebuild()
    return {"ok": True, "notes": len(ws.graph.docs)}


# ------------------------------------------------------------------- drawings


def _drawing(rel: str) -> None:
    if not note_drawings.is_drawing(rel):
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_not_a_drawing",
                    "That is not a drawing: {path}", path=rel)


@router.get("/drawing")
async def drawing(path: str = Query(""), user: User = Depends(get_current_user)) -> dict:
    """The scene inside a drawing file, whichever of the two shapes it has."""
    _drawing(path)
    ws = workspace_of(user)
    source = _guard(lambda: ws.vault.read_text(path), path)
    return {"path": path, "hash": content_hash(source),
            "scene": note_drawings.read(source)}


class DrawingIn(BaseModel):
    path: str
    scene: dict
    # What the editor read. Without it a drawing changed elsewhere in the
    # meantime is overwritten by a canvas that was opened before it changed.
    baseHash: str = ""


@router.put("/drawing")
async def save_drawing(body: DrawingIn, user: User = Depends(get_current_user)) -> dict:
    _drawing(body.path)
    ws = workspace_of(user)
    original = _guard(lambda: ws.vault.read_text(body.path), body.path)
    current = content_hash(original)
    if body.baseHash and body.baseHash != current:
        raise Error(status.HTTP_409_CONFLICT, "err.notes_changed_on_disk",
                    "The note changed on disk: {path}", path=body.path,
                    current=note_drawings.read(original), hash=current)
    text = note_drawings.write(original, body.scene)
    _writing(lambda: ws.save(body.path, text), body.path)
    return {"path": body.path, "hash": content_hash(text)}


# ------------------------------------------------------------------ templates
#
# A template is compiled here and fetched back as a module. The page's content
# policy forbids building a function from a string in the browser, so generated
# code has to arrive as a real module from this origin — and it runs there
# rather than here because half of what these templates do is ask questions.

_modules = note_templates.Modules()


@router.get("/templates")
async def templates_list(user: User = Depends(get_current_user)) -> dict:
    """The notes in the template folder, as things to insert."""
    ws = workspace_of(user)
    return {"folder": ws.options.templates_folder,
            "templates": note_templates.in_folder(sorted(ws.graph.docs),
                                                  ws.options.templates_folder)}


@router.get("/templates/folders")
async def template_folders(user: User = Depends(get_current_user)) -> dict:
    """Which folder gets which form, so a new note arrives with the right one."""
    return {"folderTemplates": workspace_of(user).options.folder_templates}


class CompileIn(BaseModel):
    path: str


@router.post("/templates/compile")
async def compile_template(body: CompileIn, user: User = Depends(get_current_user)) -> dict:
    if not body.path.strip():
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_path_required",
                    "Which note?")
    ws = workspace_of(user)
    source = _guard(lambda: ws.vault.read_text(body.path), body.path)
    compiled = note_templates.compile_template(source)
    return {"id": _modules.put(compiled), "interactive": compiled.interactive}


class FillIn(BaseModel):
    path: str
    # What `{{title}}` and `tp.file.title` mean: the name of the note being made.
    title: str = ""


@router.post("/templates/fill")
async def fill_template(body: FillIn, user: User = Depends(get_current_user)) -> dict:
    """A template with the plain substitutions done, here on the server.

    The other route compiles a template into something the browser runs, because
    it may ask questions. This one is for a note being created with nobody
    watching: what can be answered is answered, and what cannot is reported
    rather than guessed at.
    """
    ws = workspace_of(user)
    raw = _guard(lambda: ws.vault.read_text(body.path), body.path)
    filled = note_templates.fill(raw, title=body.title,
                                 date_format=ws.options.template_date_format,
                                 time_format=ws.options.template_time_format)
    return {"text": filled.text, "unresolved": filled.unresolved}


@router.get("/templates/module/{module_id}.mjs")
async def template_module(module_id: str, user: User = Depends(browser_user)):
    """The compiled template, as a module the page can import.

    Reached by `import()`, which carries no header of ours — hence the reading
    ticket. What comes back is only ever code this server generated a moment ago
    from a template in this vault.
    """
    code = _modules.get(module_id)
    if code is None:
        # A module the browser asks for after it has been forgotten. Answering
        # with a module that explains itself puts the sentence where somebody
        # will see it; a 404 here surfaces as an unreadable import error.
        return PlainTextResponse(
            'export default async function () {\n'
            '  throw new Error("This template is no longer held — insert it again.");\n'
            '}\n',
            status_code=status.HTTP_404_NOT_FOUND, media_type="text/javascript")
    return PlainTextResponse(code, media_type="text/javascript",
                             headers={"Cache-Control": "private, max-age=300"})


# ---------------------------------------------------------------- the versions
#
# Reading only. The six routes of the side this replaces that write — init,
# clone, pull, commit, push, sync — are not ported: the setting that drove them
# was off, and the one time one of them ran it put a 246 MB repository inside
# the vault, which the synchronisation then carried to five devices.


def _history() -> note_history.History:
    return note_history.open_history(settings.notes_history_dir)


@router.get("/history")
async def history_info(user: User = Depends(get_current_user)) -> dict:
    """Whether this vault has older versions, and how fresh they are."""
    try:
        return _history().info()
    except note_history.NoHistory:
        return {"has": False, "last": None}


@router.get("/history/log")
async def history_log(path: str = Query(""), limit: int = Query(50),
                      user: User = Depends(get_current_user)) -> dict:
    """The versions of one note, newest first."""
    if not path.strip():
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_path_required",
                    "Which note?")
    try:
        return {"commits": [c.as_json() for c in _history().log(path, limit)]}
    except paths.OutsideVault:
        # The same answer as for a note that is not there, as everywhere else
        # here: telling the two apart says whether a path exists outside.
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=path) from None
    except note_history.NoHistory:
        return {"commits": []}


@router.get("/history/show")
async def history_show(hash: str = Query(""), path: str = Query(""),
                       user: User = Depends(get_current_user)) -> dict:
    """One note as it stood in one version."""
    if not hash.strip() or not path.strip():
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_version_incomplete",
                    "A version needs a commit and a note")
    try:
        return {"content": _history().show(hash, path)}
    except paths.OutsideVault:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=path) from None
    except note_history.NoHistory as err:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_version_not_found",
                    "This version of the note is not there: {why}",
                    why=str(err)) from None


# ------------------------------------------------------------------- CalDAV


def _caldav_account(user: User) -> cal_dav.Account:
    password = ""
    if user.notes_caldav_password_enc:
        try:
            password = decrypt_secret(user.notes_caldav_password_enc)
        except Exception:                         # noqa: BLE001
            log.warning("notes: the calendar account password of %s cannot be read", user.id)
    return cal_dav.Account(url=user.notes_caldav_url or "",
                           user=user.notes_caldav_user or "", password=password)


class CalDavAccountIn(BaseModel):
    url: str = ""
    user: str = ""
    # Absent leaves the stored one alone; empty clears it. Two different wishes,
    # and one field cannot carry both.
    password: str | None = None


@router.get("/calendar/account")
async def caldav_account(user: User = Depends(get_current_user)) -> dict:
    """Whether an appointment can be written, and through which account.

    The password never comes back out — only whether one is set, which is all an
    interface needs in order to show the difference.
    """
    account = _caldav_account(user)
    return {"url": account.url, "user": account.user,
            "has_password": bool(user.notes_caldav_password_enc),
            "writable": account.configured}


@router.put("/calendar/account")
async def save_caldav_account(body: CalDavAccountIn,
                              user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)) -> dict:
    given = body.model_dump(exclude_unset=True)
    if "url" in given:
        user.notes_caldav_url = given["url"].strip().rstrip("/")
    if "user" in given:
        user.notes_caldav_user = given["user"].strip()
    if "password" in given:
        user.notes_caldav_password_enc = (encrypt_secret(given["password"])
                                          if given["password"] else "")
    await db.commit()
    return await caldav_account(user)


@router.get("/calendar/writable")
async def caldav_calendars(user: User = Depends(get_current_user)) -> dict:
    """The calendars this account can see, and which of them it may write to."""
    account = _caldav_account(user)
    if not account.configured:
        return {"configured": False, "calendars": []}
    try:
        found = await cal_dav.calendars(account)
    except cal_dav.CalDavError as err:
        return {"configured": True, "calendars": [], "error": str(err)}
    return {"configured": True,
            "calendars": [{"id": c.id, "name": c.name, "readOnly": c.read_only}
                          for c in found]}


class EventIn(BaseModel):
    calendar: str
    title: str
    # `YYYY-MM-DD` for a whole day, otherwise `YYYY-MM-DDTHH:MM`.
    start: str
    end: str
    uid: str = ""
    allDay: bool = False
    location: str = ""
    description: str = ""


@router.post("/calendar/event")
async def write_event(body: EventIn, user: User = Depends(get_current_user)) -> dict:
    account = _caldav_account(user)
    if not account.configured:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_no_calendar_account",
                    "No account to write appointments through")
    try:
        return await cal_dav.save_event(
            account, body.calendar, timezone=user.timezone or "Europe/Berlin",
            uid=body.uid, title=body.title, start=body.start, end=body.end,
            all_day=body.allDay, location=body.location, description=body.description)
    except LookupError:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_calendar_not_found",
                    "No such calendar") from None
    except PermissionError:
        raise Error(status.HTTP_403_FORBIDDEN, "err.notes_calendar_read_only",
                    "That calendar can only be read") from None
    except cal_dav.CalDavError as err:
        raise Error(status.HTTP_502_BAD_GATEWAY, "err.notes_calendar_refused",
                    "The calendar refused it: {why}", why=str(err)) from None


@router.delete("/calendar/event")
async def drop_event(calendar: str = Query(...), uid: str = Query(...),
                     user: User = Depends(get_current_user)) -> dict:
    account = _caldav_account(user)
    if not account.configured:
        raise Error(status.HTTP_400_BAD_REQUEST, "err.notes_no_calendar_account",
                    "No account to write appointments through")
    try:
        await cal_dav.delete_event(account, calendar, uid)
    except LookupError:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_calendar_not_found",
                    "No such calendar") from None
    except PermissionError:
        raise Error(status.HTTP_403_FORBIDDEN, "err.notes_calendar_read_only",
                    "That calendar can only be read") from None
    except cal_dav.CalDavError as err:
        raise Error(status.HTTP_502_BAD_GATEWAY, "err.notes_calendar_refused",
                    "The calendar refused it: {why}", why=str(err)) from None
    return {"ok": True}
