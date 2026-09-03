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

import errno
import logging
from pathlib import Path, PurePosixPath

from fastapi import (APIRouter, Depends, File, Form, Query, Response,
                     UploadFile, status)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.security import encrypt_secret
from ..db import get_session
from ..models.notes import NotesCalendar
from ..models.user import User
from ..notes import paths
from ..notes.dv import tasks as dv_tasks
from ..notes.dv.bases import run as dv_bases
from ..notes.dv.dql import evaluate_inline, execute as run_query, file_object
from ..notes.query.run import run as run_search
from ..notes.registry import vault_of, workspace_of
from ..notes.settings import options as vault_options
from ..notes.vault import write as vault_write
from ..notes.vault.files import content_hash, is_text, mime_for
from ..notes.workspace import Conflict
from .deps import get_current_user

log = logging.getLogger("notes")

router = APIRouter(prefix="/notes-native", tags=["notes"])

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
    except FileNotFoundError:
        raise Error(status.HTTP_404_NOT_FOUND, "err.notes_not_found",
                    "No such note: {path}", path=rel) from None


@router.get("/files/")
async def tree(user: User = Depends(get_current_user)) -> dict:
    return vault_of(user).tree().as_json()


@router.get("/files/content")
async def content(path: str = Query(...), user: User = Depends(get_current_user)):
    """A note as text, or a file as bytes.

    The hash travels with the text: the editor keeps it as the identity of the
    version it is working on and hands it back when it saves, which is how a save
    tells "still the file I read" from "somebody got there first".
    """
    v = vault_of(user)
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
async def search(q: str = Query(""), user: User = Depends(get_current_user)) -> dict:
    ws = workspace_of(user)
    hits = run_search(ws.graph, q, ws.words)
    return {"query": q, "hits": [h.as_json() for h in hits]}


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
    return dv_bases.run(ws.pages, source, view)


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
