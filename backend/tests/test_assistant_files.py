"""Files handed to the assistant along with a chat message.

A person on a phone shares a photo of a receipt or a PDF and says "file this". The server
keeps the bytes, the message carries the ids, and the assistant gets two tools: one that
shows it what a file is, one that hands the bytes on to a filing tool on the server without
the model ever carrying a megabyte of base64 through its context.

What is tested here: a file belongs to the one who uploaded it and to nobody else; the
message it goes out with binds it; the reading tool answers with text; the forwarding tool
fills exactly the named argument and substitutes the file's own name.
"""
import base64

import pytest
from app.models.assistant import AssistantFile
from app.worker.tools_files import call_file_tool

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


async def _upload(client, user, name="notiz.txt", data=b"Milch, Eier, Brot", mime="text/plain") -> dict:
    r = await client.post("/assistant/files", files={"file": (name, data, mime)}, headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_file_is_kept_and_goes_out_with_the_message(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna)
    assert up["filename"] == "notiz.txt" and up["size"] == 17

    r = await client.post("/assistant/chat", json={"text": "Was steht da?", "file_ids": [up["id"]]},
                          headers=auth(anna))
    assert r.status_code == 200, r.text
    msg = r.json()
    assert [f["filename"] for f in msg["files"]] == ["notiz.txt"]

    row = await db.get(AssistantFile, up["id"])
    assert row.task_id == msg["id"], "the message binds the file, so a run can find it"

    page = (await client.get("/assistant/chat?limit=5", headers=auth(anna))).json()
    assert page["messages"][-1]["files"][0]["id"] == up["id"], "the list carries them as well"


async def test_files_alone_are_a_message(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna, name="beleg.pdf", data=b"%PDF-1.4", mime="application/pdf")
    r = await client.post("/assistant/chat", json={"text": "", "file_ids": [up["id"]]}, headers=auth(anna))
    assert r.status_code == 200, r.text
    assert "beleg.pdf" in r.json()["text"], "without words the file names are the message"

    r = await client.post("/assistant/chat", json={"text": "", "file_ids": []}, headers=auth(anna))
    assert r.status_code == 400, "nothing at all is still nothing"


async def test_a_foreign_file_is_not_there(db, client):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    up = await _upload(client, anna)

    r = await client.post("/assistant/chat", json={"text": "meins?", "file_ids": [up["id"]]}, headers=auth(bert))
    assert r.status_code == 404

    assert (await client.get(f"/assistant/files/{up['id']}", headers=auth(bert))).status_code == 404
    mine = await client.get(f"/assistant/files/{up['id']}", headers=auth(anna))
    assert mine.status_code == 200 and mine.content == b"Milch, Eier, Brot"


async def test_a_message_takes_only_unbound_files(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna)
    first = await client.post("/assistant/chat", json={"text": "eins", "file_ids": [up["id"]]}, headers=auth(anna))
    assert first.status_code == 200
    again = await client.post("/assistant/chat", json={"text": "zwei", "file_ids": [up["id"]]}, headers=auth(anna))
    assert again.status_code == 404, "a file goes out once; the second message does not get it"


async def test_the_reading_tool_shows_text_and_refuses_foreign_files(db, client):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    up = await _upload(client, anna)

    out = await call_file_tool(db, None, anna.id, "traccoon_file_read", {"file_id": up["id"]})
    assert "notiz.txt" in out and "Milch, Eier, Brot" in out

    out = await call_file_tool(db, None, bert.id, "traccoon_file_read", {"file_id": up["id"]})
    assert out.startswith("ERROR")


async def test_the_reading_tool_hands_an_image_to_the_eyes(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna, name="foto.png", data=b"\x89PNG\r\n", mime="image/png")
    out = await call_file_tool(db, None, anna.id, "traccoon_file_read", {"file_id": up["id"]})
    assert isinstance(out, list) and out[0]["type"] == "image"
    assert out[0]["source"]["data"] == base64.b64encode(b"\x89PNG\r\n").decode()


class _Mcp:
    def __init__(self):
        self.calls = []

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "stored as #7"


async def test_forwarding_fills_the_named_field_and_the_file_name(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna, name="rechnung.pdf", data=b"%PDF-1.4 x", mime="application/pdf")
    mcp = _Mcp()

    out = await call_file_tool(db, mcp, anna.id, "traccoon_file_forward", {
        "file_id": up["id"], "tool": "paperless__post_document", "field": "file",
        "arguments": {"filename": "$filename", "title": "Rechnung Strom"},
    }, allowed=lambda name: name.startswith("paperless__"))
    assert "stored as #7" in out
    name, args = mcp.calls[0]
    assert name == "paperless__post_document"
    assert args["file"] == base64.b64encode(b"%PDF-1.4 x").decode()
    assert args["filename"] == "rechnung.pdf" and args["title"] == "Rechnung Strom"


async def test_forwarding_respects_the_allowlist_and_the_naming(db, client):
    anna = await make_user(db, "anna")
    up = await _upload(client, anna)
    mcp = _Mcp()

    out = await call_file_tool(db, mcp, anna.id, "traccoon_file_forward",
                               {"file_id": up["id"], "tool": "vault__notes_attach", "field": "data"},
                               allowed=lambda name: False)
    assert out.startswith("ERROR") and not mcp.calls

    out = await call_file_tool(db, mcp, anna.id, "traccoon_file_forward",
                               {"file_id": up["id"], "tool": "notes_attach", "field": "data"})
    assert out.startswith("ERROR") and not mcp.calls, "a tool is named with its server"
