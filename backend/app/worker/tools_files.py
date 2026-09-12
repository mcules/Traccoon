"""The files a person hands the assistant along with a chat message.

Two tools, and a deliberate split between them. `traccoon_file_read` shows the model what a
file says: an image as a vision block, text as text, a PDF as its text where one can be
pulled out. `traccoon_file_forward` hands the bytes on to another tool, on the server, without
them ever passing through the model: the model names the tool and the argument that takes
the content, the worker fills the base64 in. A scanned invoice is a megabyte of base64, and a
model that had to carry that from one tool answer into the next tool call would spend its
whole context on it and still get a byte wrong.

What is done with a file is the model's decision, not this module's: it knows the tools of
its person's MCP group (a document filing, a vault, whatever is connected) and the person's
learned rules. This module knows neither, only how to read a row and how to fill an argument.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantFile

log = logging.getLogger("traccoon.tools.files")

_IMG_MEDIA = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_TEXT_MIME = {"application/json", "application/xml", "application/x-yaml", "application/gpx+xml",
              "application/vnd.google-earth.kml+xml"}
_TEXT_EXT = (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".ini", ".cfg",
             ".gpx", ".kml", ".ics", ".eml")
MAX_TEXT = 16_000
MAX_ANSWER = 4_000


def _def(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


FILE_TOOLS = [
    _def("traccoon_file_read",
         "Read a file your person attached to the chat (the message lists them with their "
         "ids): an image comes as a picture, text and PDF as text. Read before you decide "
         "what a file is and whether it needs filing at all.",
         {"file_id": {"type": "integer"}}, ["file_id"]),
    _def("traccoon_file_forward",
         "Hand an attached file to another tool WITHOUT reading it yourself: the file's "
         "content goes as base64 into the argument you name in `field`, everything else in "
         "`arguments` is passed as given. Use it to file a document (for example "
         "`paperless__post_document` with field `file`, plus `filename` and `title`) or to put "
         "it into the vault (`vault__notes_attach` with field `data`, plus `name` and `note` "
         "or `folder`). The value \"$filename\" anywhere in `arguments` is replaced by the "
         "file's own name. Whether a file belongs anywhere is your call: a receipt or a letter "
         "is a document, a screenshot for a question needs no filing.",
         {"file_id": {"type": "integer"},
          "tool": {"type": "string", "description": "The tool, as <server>__<tool>."},
          "field": {"type": "string", "description": "The argument that takes the base64 content."},
          "arguments": {"type": "object", "description": "The other arguments of that tool."}},
         ["file_id", "tool", "field"]),
]
FILE_TOOL_NAMES = {t["function"]["name"] for t in FILE_TOOLS}


async def _file(db: AsyncSession, owner_id: int | None, args: dict) -> AssistantFile | str:
    try:
        fid = int(args.get("file_id"))
    except (TypeError, ValueError):
        return "ERROR: file_id is missing."
    row = await db.get(AssistantFile, fid)
    # Only the files of the person the assistant serves: a foreign id is "not there", the same
    # answer a wrong id gets, so nothing about other people's files can be probed.
    if row is None or owner_id is None or row.owner_user_id != owner_id:
        return f"ERROR: file #{fid} not found among your person's attachments."
    return row


def _pdf_text(data: bytes) -> str | None:
    """The text of a PDF, when a reader is at hand. None when there is none installed."""
    try:
        from io import BytesIO
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    try:
        reader = PdfReader(BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages[:40]]
        return "\n\n".join(t for t in pages if t.strip())
    except Exception as exc:  # noqa: BLE001
        log.info("pdf text failed: %s", exc)
        return None


async def call_file_tool(db: AsyncSession, mcp: Any, owner_id: int | None, name: str, args: dict,
                         allowed: Callable[[str], bool] | None = None) -> Any:
    row = await _file(db, owner_id, args)
    if isinstance(row, str):
        return row
    mime = (row.mime_type or "application/octet-stream").lower()
    data = row.data or b""
    head = f"File #{row.id} „{row.filename}“ ({mime}, {len(data)} bytes)"

    if name == "traccoon_file_read":
        if mime.startswith("image/"):
            media = mime if mime in _IMG_MEDIA else "image/png"
            return [
                {"type": "image", "source": {"type": "base64", "media_type": media,
                                             "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": head + "."},
            ]
        if mime == "application/pdf" or row.filename.lower().endswith(".pdf"):
            text = _pdf_text(data)
            if text is None:
                return (head + ": a PDF, and no text reader is installed on this worker. "
                        "Decide by name and context, or forward it as it is.")
            if not text.strip():
                return head + ": a PDF without a text layer (a scan). Forward it as it is."
            return f"{head}:\n\n{text[:MAX_TEXT]}"
        if mime.startswith("text/") or mime in _TEXT_MIME or row.filename.lower().endswith(_TEXT_EXT):
            return f"{head}:\n\n{data.decode('utf-8', errors='replace')[:MAX_TEXT]}"
        return head + ": a binary file, not readable as text or image. Forward it if it belongs somewhere."

    if name == "traccoon_file_forward":
        tool = (args.get("tool") or "").strip()
        field = (args.get("field") or "").strip()
        if "__" not in tool:
            return "ERROR: name the tool as <server>__<tool>, for example paperless__post_document."
        if not field:
            return "ERROR: `field` names the argument that takes the file content."
        if allowed is not None and not allowed(tool):
            return f"ERROR: tool '{tool}' is not allowed for this agent."
        if mcp is None:
            return "ERROR: no tool connection in this run."
        arguments = dict(args.get("arguments") or {})
        arguments = {k: (row.filename if v == "$filename" else v) for k, v in arguments.items()}
        arguments[field] = base64.b64encode(data).decode()
        try:
            out = await mcp.call(tool, arguments)
        except Exception as exc:  # noqa: BLE001
            return f"TOOL-ERROR: {exc}"
        text = out if isinstance(out, str) else str(out)
        return f"{tool} took {head}:\n{text[:MAX_ANSWER]}"

    return f"ERROR: unknown file tool {name}."
