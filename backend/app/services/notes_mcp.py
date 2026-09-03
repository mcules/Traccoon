"""The notes as tools, for whoever works with them but is not a person.

Until now an agent reached this vault through three stations: a registry, a
translating server, and a rebuilt interface behind it. Each of them could fail
on its own, and when one did the symptom was not an error but an absence — the
tools were simply not in the list, and the agent said it had no way to write a
note. That is the chain this replaces. Here the tools sit in the same process as
the workspace, so what an agent writes is in the index before the call returns.

The tools are the ones the work actually needs, and no more. Reading and
searching, writing and appending, replacing a piece of text, the properties at
the top of a note, its tags, moving it, deleting it, and running one of the
note languages. Every one of them goes through the same workspace the interface
uses, so the two cannot disagree about what is in the vault.

Deny by default holds here too. Whoever calls this needs a token with the note
scope and nothing else in Traccoon opens with it.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

from ..models.user import User
from ..notes.dv import tasks as dv_tasks
from ..notes.dv.dql import execute as run_query
from ..notes.model.frontmatter import split as split_frontmatter
from ..notes.query.run import run as run_search
from ..notes.registry import workspace_of
from ..notes.vault.files import content_hash, is_text
from ..notes.workspace import Conflict

log = logging.getLogger("notes.mcp")

# How much of a note goes into one answer by default. A note is read to be
# worked with, not to be poured into a prompt; the caller can ask for more.
DEFAULT_MAX_CHARS = 40_000
MAX_LIST = 500

STRING = {"type": "string"}


def _tool(name: str, description: str, properties: dict,
          required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": required or []}}


TOOLS: list[dict] = [
    _tool("notes_list",
          "List the notes of the vault. `folder` narrows to one folder and "
          "everything under it.",
          {"folder": STRING,
           "limit": {"type": "integer", "description": f"at most {MAX_LIST}"}}),
    _tool("notes_read",
          "Read one note: its text, the properties at the top, its tags, and the "
          "identity of this version. Hand that identity back when writing and the "
          "write is refused if somebody changed the note in between.",
          {"path": STRING,
           "max_chars": {"type": "integer",
                         "description": f"default {DEFAULT_MAX_CHARS}"}},
          ["path"]),
    _tool("notes_search",
          "Search the vault. The query language of the notes applies: `tag:#idea`, "
          "`path:folder`, `\"a phrase\"`, `-not this`, `a OR b`, `/regex/`.",
          {"query": STRING, "limit": {"type": "integer"}}, ["query"]),
    _tool("notes_query",
          "Run one block of the note query language (TABLE/LIST/TASK … FROM … "
          "WHERE …) and give back its answer. `path` is the note the block would "
          "sit in, which is what `this` means inside it.",
          {"query": STRING, "path": STRING}, ["query"]),
    _tool("notes_tasks",
          "Run one block of the task filter language: `not done`, `due before "
          "today`, `tag includes #x`, `sort by due`, `group by filename`.",
          {"query": STRING}, ["query"]),
    _tool("notes_write",
          "Write a note, creating it or replacing it whole. Give `base_hash` from "
          "a read to be told rather than to overwrite when it changed in between.",
          {"path": STRING, "content": STRING, "base_hash": STRING},
          ["path", "content"]),
    _tool("notes_append",
          "Add text to the end of a note, creating it if it is not there. The "
          "safe way to add something without holding the whole note.",
          {"path": STRING, "text": STRING,
           "heading": {"type": "string",
                       "description": "put it under this heading instead of at the end"}},
          ["path", "text"]),
    _tool("notes_replace",
          "Replace a piece of text in a note, literally. Says how often it "
          "matched; nothing is written when the count does not match `expect`.",
          {"path": STRING, "find": STRING, "replace": STRING,
           "expect": {"type": "integer",
                      "description": "how many matches are expected, if you know"}},
          ["path", "find", "replace"]),
    _tool("notes_properties",
          "Read or change the properties at the top of a note. `set` writes the "
          "keys it names and leaves the others alone; `remove` names keys to drop.",
          {"path": STRING, "set": {"type": "object"},
           "remove": {"type": "array", "items": STRING}},
          ["path"]),
    _tool("notes_tags",
          "The tags of a note, or of the whole vault when no path is given. "
          "`add` and `remove` change the ones in the properties at the top.",
          {"path": STRING, "add": {"type": "array", "items": STRING},
           "remove": {"type": "array", "items": STRING}}),
    _tool("notes_backlinks",
          "Which notes point at this one.", {"path": STRING}, ["path"]),
    _tool("notes_move",
          "Rename or move a note or a folder. Every link that points at it is "
          "rewritten. With `dry_run` nothing is written and you learn what would "
          "change.",
          {"from": STRING, "to": STRING, "dry_run": {"type": "boolean"}},
          ["from", "to"]),
    _tool("notes_delete",
          "Delete a note or a folder. It goes into the vault's trash, from where "
          "a person can bring it back.",
          {"path": STRING}, ["path"]),
    _tool("notes_attach",
          "Put a file into the vault, where the vault keeps its attachments. "
          "`data` is base64. `note` is the note it is going into, which decides "
          "the folder.",
          {"name": STRING, "data": STRING, "note": STRING, "folder": STRING},
          ["name", "data"]),
]

TOOL_NAMES = {t["name"] for t in TOOLS}

INSTRUCTIONS = """This is one person's note vault: a folder of markdown files that a
person also edits themselves, on this machine and on their phone.

Two things follow from that and they are not style:

* Read before you write. `notes_write` replaces the whole note. Pass the
  `base_hash` you got from `notes_read` and a change somebody else made in the
  meantime is reported instead of overwritten. To add something, use
  `notes_append` — it does not need the rest of the note at all.
* A link is `[[Note name]]`, a tag is `#tag`. Use `notes_move` to rename, never
  a write plus a delete: only `notes_move` carries the links along, and a link
  to a note that has gone looks exactly like a link to one that never existed.
"""


def toollist() -> list[dict]:
    return TOOLS


# ------------------------------------------------------------------ helpers

def _text_of(ws, path: str) -> str:
    """A note as text, or a sentence saying why not.

    A missing file has to read as "there is no such note" and not as a file
    system error: whoever gets this is a model deciding what to do next, and an
    errno tells it nothing it can act on.
    """
    if not path:
        raise ValueError("`path` must not be empty")
    if not is_text(path):
        raise ValueError(f"{path} is not a text file")
    try:
        return ws.vault.read_text(path)
    except FileNotFoundError:
        raise LookupError(f"there is no note at {path}") from None
    except OSError as err:
        raise ValueError(f"{path} cannot be read: {err.strerror}") from None


def _note_summary(ws, rel: str) -> dict:
    page = ws.pages.get(rel)
    out: dict[str, Any] = {"path": rel}
    if page is not None:
        out["title"] = page.name
        if page.tags:
            out["tags"] = page.tags
    return out


def _dump_properties(data: dict) -> str:
    """The properties block, written back.

    Written out here rather than handed to a YAML writer: a writer reflows the
    whole block, requotes strings and reorders nothing but changes everything,
    and the person who wrote the note by hand then sees their file rewritten.
    """
    import yaml
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).rstrip("\n")
    return f"---\n{body}\n---\n"


def _with_properties(raw: str, data: dict) -> str:
    _, body = split_frontmatter(raw)
    if not data:
        return body
    return _dump_properties(data) + body


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def _append_under(raw: str, heading: str, text: str) -> str:
    """Put text at the end of the section under a heading.

    At the end of the *section*, not right after the heading: an entry added to
    a list belongs after the entries that are already in it.
    """
    found = None
    for m in HEADING.finditer(raw):
        if m.group(2).strip().lower() == heading.strip().lower():
            found = m
            break
    if found is None:
        raise LookupError(f"no heading {heading!r} in this note")
    level = len(found.group(1))
    end = len(raw)
    for m in HEADING.finditer(raw, found.end()):
        if len(m.group(1)) <= level:
            end = m.start()
            break
    head, tail = raw[:end], raw[end:]
    if not head.endswith("\n"):
        head += "\n"
    return head + text.rstrip("\n") + "\n" + tail


# ------------------------------------------------------------------ the run

async def execute(user: User, name: str, args: dict) -> Any:
    """One tool call. Everything it raises reaches the caller as a sentence."""
    if name not in TOOL_NAMES:
        raise LookupError(f"no tool called {name!r}")
    ws = workspace_of(user)
    path = str(args.get("path") or "")

    if name == "notes_list":
        folder = str(args.get("folder") or "").strip("/")
        limit = min(int(args.get("limit") or MAX_LIST), MAX_LIST)
        rels = sorted(ws.pages.pages)
        if folder:
            rels = [r for r in rels if r == folder or r.startswith(folder + "/")]
        return {"total": len(rels),
                "notes": [_note_summary(ws, r) for r in rels[:limit]]}

    if name == "notes_read":
        raw = _text_of(ws, path)
        limit = int(args.get("max_chars") or DEFAULT_MAX_CHARS)
        page = ws.pages.get(path)
        return {
            "path": path,
            "content": raw[:limit],
            "truncated": len(raw) > limit,
            "hash": content_hash(raw),
            "properties": page.fields if page else {},
            "tags": page.tags if page else [],
        }

    if name == "notes_search":
        limit = min(int(args.get("limit") or 50), MAX_LIST)
        hits = run_search(ws.graph, str(args.get("query") or ""), ws.words)
        return {"hits": [h.as_json() for h in hits[:limit]], "total": len(hits)}

    if name == "notes_query":
        return run_query(ws.pages, str(args.get("query") or ""),
                         str(args.get("path") or "") or None)

    if name == "notes_tasks":
        return dv_tasks.execute(ws.pages, str(args.get("query") or ""))

    if name == "notes_write":
        try:
            return ws.save(path, str(args.get("content") or ""),
                           str(args.get("base_hash") or "") or None)
        except Conflict as clash:
            # Not an exception the caller has to parse out of a sentence: the
            # text that is there now comes back, so it can merge and write again.
            raise ValueError(
                f"the note changed since you read it. It now holds:\n\n{clash.current}"
            ) from None

    if name == "notes_append":
        text = str(args.get("text") or "")
        raw = _text_of(ws, path) if ws.pages.get(path) or _exists(ws, path) else ""
        heading = str(args.get("heading") or "")
        if heading:
            out = _append_under(raw, heading, text)
        else:
            out = raw + ("" if raw.endswith("\n") or not raw else "\n") + text.rstrip("\n") + "\n"
        return ws.save(path, out)

    if name == "notes_replace":
        raw = _text_of(ws, path)
        find = str(args.get("find") or "")
        if not find:
            raise ValueError("`find` must not be empty")
        count = raw.count(find)
        expect = args.get("expect")
        if expect is not None and int(expect) != count:
            raise ValueError(f"expected {int(expect)} matches, found {count} — "
                             "nothing was written")
        if count == 0:
            raise ValueError("that text is not in this note — nothing was written")
        return {**ws.save(path, raw.replace(find, str(args.get("replace") or ""))),
                "replaced": count}

    if name == "notes_properties":
        raw = _text_of(ws, path)
        data, _ = split_frontmatter(raw)
        changes = args.get("set") or {}
        drops = args.get("remove") or []
        if not changes and not drops:
            return {"path": path, "properties": data}
        data = dict(data)
        data.update(changes)
        for key in drops:
            data.pop(str(key), None)
        ws.save(path, _with_properties(raw, data))
        return {"path": path, "properties": data}

    if name == "notes_tags":
        add = [str(t).lstrip("#") for t in (args.get("add") or [])]
        drop = [str(t).lstrip("#") for t in (args.get("remove") or [])]
        if not path:
            if add or drop:
                raise ValueError("naming tags needs a note to put them on")
            return {"tags": ws.graph.all_tags()}
        if not add and not drop:
            page = ws.pages.get(path)
            return {"path": path, "tags": page.tags if page else []}
        raw = _text_of(ws, path)
        data, _ = split_frontmatter(raw)
        data = dict(data)
        have = data.get("tags")
        tags = [str(t) for t in have] if isinstance(have, list) else (
            [t for t in re.split(r"[,\s]+", have) if t] if isinstance(have, str) else [])
        for t in add:
            if t not in tags:
                tags.append(t)
        tags = [t for t in tags if t not in drop]
        if tags:
            data["tags"] = tags
        else:
            data.pop("tags", None)
        ws.save(path, _with_properties(raw, data))
        return {"path": path, "tags": tags}

    if name == "notes_backlinks":
        return {"path": path, "backlinks": ws.graph.backlinks(path)}

    if name == "notes_move":
        return ws.rename(str(args.get("from") or ""), str(args.get("to") or ""),
                         dry_run=bool(args.get("dry_run")))

    if name == "notes_delete":
        return ws.delete(path)

    if name == "notes_attach":
        try:
            data = base64.b64decode(str(args.get("data") or ""), validate=True)
        except (ValueError, TypeError):
            raise ValueError("`data` is not base64") from None
        folder = args.get("folder")
        return ws.upload(str(args.get("name") or "file"), data,
                         folder=str(folder) if folder is not None else None,
                         note=str(args.get("note") or ""))

    raise LookupError(f"no tool called {name!r}")


def _exists(ws, rel: str) -> bool:
    from ..notes.vault import write
    return write.exists(ws.vault, rel)
