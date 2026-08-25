"""The mail client: accounts, identities and actions.

What is NOT checked here is IMAP itself — that would need a real server, and `imapclient` is
not the place where our mistakes come into being. What is checked is what belongs to us: that
a password never comes back, that it does not get lost while saving other fields, that there
is only one default identity and that a mail action starts the flow with everything it needs
to know about the mail.
"""
import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.mail import MailAccount, MailIdentity
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import mailbox
from sqlalchemy import select

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


def _account(**over) -> dict:
    values = {"name": "privat", "imap_host": "imap.example.org", "imap_user": "ich",
             "imap_password": "geheim", "smtp_host": "smtp.example.org",
             "smtp_user": "ich", "smtp_password": "auch geheim"}
    values.update(over)
    return values


async def test_the_password_never_comes_back(db, client):
    anna = await make_user(db, "anna")
    r = await client.post("/mailbox/accounts", headers=auth(anna), json=_account())
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["imap_password_set"] is True and data["smtp_password_set"] is True
    assert "geheim" not in r.text, "das Kennwort darf die Oberfläche nie erreichen"

    # And in the database it does not stand in the clear.
    account = (await db.execute(select(MailAccount))).scalars().one()
    assert account.imap_password_enc and account.imap_password_enc != "geheim"


async def test_an_empty_password_means_unchanged(db, client):
    """The most common operating mistake would otherwise be: change folder names and unlock the account."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    before = (await db.execute(select(MailAccount))).scalars().one().imap_password_enc

    r = await client.put(f"/mailbox/accounts/{kid}", headers=auth(anna),
                         json=_account(imap_password="", smtp_password="", folder_sent="Gesendet"))
    assert r.status_code == 200
    db.expire_all()
    account = (await db.execute(select(MailAccount))).scalars().one()
    assert account.folder_sent == "Gesendet"
    assert account.imap_password_enc == before, "unverändert, nicht gelöscht"


async def test_foreign_accounts_stay_foreign(db, client):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    assert (await client.get(f"/mailbox/accounts/{kid}/identities",
                             headers=auth(bert))).status_code == 404
    assert (await client.get("/mailbox/accounts", headers=auth(bert))).json() == []


async def test_only_one_default_identity(db, client):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    for address in ("ich@example.org", "vorstand@example.org"):
        r = await client.post(f"/mailbox/accounts/{kid}/identities", headers=auth(anna),
                              json={"email": address, "is_default": True})
        assert r.status_code == 201

    db.expire_all()
    identities = (await db.execute(select(MailIdentity))).scalars().all()
    defaults = [i for i in identities if i.is_default]
    assert len(identities) == 2 and len(defaults) == 1
    assert defaults[0].email == "vorstand@example.org", "die zuletzt gesetzte gilt"


async def test_the_action_starts_a_flow_with_the_mail_in_the_context(db, client, monkeypatch):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def fake_message(account, folder, uid):
        return {"subject": "Rechnung 2026-08", "from": [{"addr": "shop@example.org"}],
                "date": "2026-08-19", "message_id": "<abc@example.org>", "text": "Anbei.",
                "attachments": [{"index": 3, "filename": "rechnung.pdf",
                                 "content_type": "application/pdf", "size": 1234}]}
    monkeypatch.setattr(mailbox, "message", fake_message)

    d = WorkflowDefinition(project_id=None, key="anhang-paperless", name="Anhang nach Paperless",
                           created_by=anna.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    graph = {"nodes": [
        {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
         "data": {"config": {"trigger": {"kind": "mail_action", "scope": "attachment"}}}},
        {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
         "data": {"config": {"outcome": "completed"}}}],
        "edges": [{"id": "k", "source": "s", "target": "e"}]}
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    # The UI asks which flows fit a mail.
    offer = (await client.get("/mailbox/actions", headers=auth(anna))).json()
    assert [a["key"] for a in offer] == ["anhang-paperless"]
    assert offer[0]["scope"] == "attachment"

    r = await client.post(f"/mailbox/accounts/{kid}/messages/42/action", headers=auth(anna),
                          json={"definition_id": d.id, "folder": "INBOX", "attachment": 3})
    assert r.status_code == 200, r.text

    inst = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert inst.context["mail"]["uid"] == 42
    assert inst.context["mail"]["subject"] == "Rechnung 2026-08"
    assert inst.context["attachment"]["filename"] == "rechnung.pdf"
    assert inst.source_ref == "INBOX:42:3", "zweimal dieselbe Aktion ist derselbe Vorgang"


async def test_the_action_does_not_know_the_attachment(db, client, monkeypatch):
    """An attachment that does not exist is an error and not an empty flow."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def fake_message(account, folder, uid):
        return {"subject": "ohne", "from": [], "attachments": []}
    monkeypatch.setattr(mailbox, "message", fake_message)

    d = WorkflowDefinition(project_id=None, key="leer", name="leer", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {"trigger": {"kind": "mail_action"}}}},
                            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "s", "target": "e"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    r = await client.post(f"/mailbox/accounts/{kid}/messages/7/action", headers=auth(anna),
                          json={"definition_id": d.id, "attachment": 99})
    assert r.status_code == 404


# ── HTML einer fremden Mail ─────────────────────────────────────────────────

async def test_html_is_cleaned_and_remote_images_stay():
    """Three things at once: no script, no form, no silent image fetch."""
    from app.services.mailbox import clean

    clean, fern = clean(
        '<p onclick="alert(1)">Hallo<script>alert(2)</script>'
        '<img src="https://tracker.example/pixel.gif">'
        '<img src="data:image/png;base64,AAA">'
        '<form action="https://phish"><input name="pw"></form>'
        '<a href="javascript:evil()">klick</a></p>')

    assert "script" not in clean and "onclick" not in clean
    assert "<form" not in clean and "<input" not in clean
    assert "javascript:" not in clean
    # The remote image stays visible but loads nothing.
    assert 'data-fern="https://tracker.example/pixel.gif"' in clean
    assert fern is True
    # An embedded image is no remote image and stays as it is.
    assert 'src="data:image/png;base64,AAA"' in clean


async def test_no_warning_without_remote_images():
    from app.services.mailbox import clean

    clean, fern = clean('<p>Nur <b>Text</b> und <img src="data:image/gif;base64,AA">.</p>')
    assert fern is False and "<b>Text</b>" in clean


# ── Ordnerbaum ──────────────────────────────────────────────────────────────

def _o(name: str, parent: str = "", special: str = "", separator: str = ".") -> dict:
    return {"name": name, "display": name.split(separator)[-1], "parent": parent,
            "delimiter": separator, "special": special, "level": 0, "unseen": 0, "total": 0}


async def test_subfolders_sit_under_their_parent():
    """The mistake one sees: `Archives` comes alphabetically before `INBOX.Aliexpress`, so the
    subfolders of the inbox slid below the archive — indented, which makes the
    falschen Eindruck perfekt machte."""
    from app.services.mailbox import tree_sort

    tree = tree_sort([
        _o("Archives"),
        _o("Archives.2024", "Archives"),
        _o("INBOX", special="inbox"),
        _o("INBOX.Aliexpress", "INBOX"),
        _o("INBOX.Bewerbung", "INBOX"),
        _o("Sent", special="sent"),
    ])
    order = [(e["name"], e["level"]) for e in tree]

    assert order == [
        ("INBOX", 0),
        ("INBOX.Aliexpress", 1),
        ("INBOX.Bewerbung", 1),
        ("Sent", 0),
        ("Archives", 0),
        ("Archives.2024", 1),
    ], "Sonderordner zuerst, Kinder direkt unter ihrem Elternteil"


async def test_a_folder_without_a_parent_is_a_root():
    """Some servers list `Archives/2024` without `Archives`. Pointing indented into the void
    would be worse than one level less."""
    from app.services.mailbox import tree_sort

    tree = tree_sort([_o("INBOX", special="inbox"),
                           _o("Archives.2024", "Archives")])
    assert [(e["name"], e["level"]) for e in tree] == [("INBOX", 0), ("Archives.2024", 0)]


async def test_special_folders_appear_in_the_usual_order():
    from app.services.mailbox import tree_sort

    tree = tree_sort([
        _o("Trash", special="trash"), _o("Zeug"), _o("Drafts", special="drafts"),
        _o("INBOX", special="inbox"), _o("Sent", special="sent"), _o("Abrechnung"),
    ])
    assert [e["name"] for e in tree] == [
        "INBOX", "Drafts", "Sent", "Trash", "Abrechnung", "Zeug"]


# ── Archiv nach Muster ──────────────────────────────────────────────────────

def _account_pattern(pattern: str = "Archive/{jahr}") -> MailAccount:
    return MailAccount(name="p", archive_mode="pattern", archive_pattern=pattern,
                       folder_archive="Archive")


async def test_the_pattern_takes_the_date_of_the_mail_not_of_today():
    """The actual point: an invoice from 2023 still belongs in the year 2023 in 2026."""
    import datetime as dt

    from app.services.mailbox import archive_target

    old = dt.datetime(2023, 3, 7, 9, 0, tzinfo=dt.timezone.utc)
    assert archive_target(_account_pattern(), old) == "Archive/2023"
    assert archive_target(_account_pattern("Archive/{jahr}/{monat}"), old) == "Archive/2023/03"
    assert archive_target(_account_pattern("Archiv/{jahr}-{quartal}"), old) == "Archiv/2023-Q1"


async def test_the_separator_of_the_server_is_used():
    """The same pattern on Courier (a dot) and Dovecot (a slash) — the person should not have
    to know how their server nests folders."""
    import datetime as dt

    from app.services.mailbox import archive_target

    when = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)
    assert archive_target(_account_pattern("Archive/{jahr}"), when, separator=".") == "Archive.2026"
    assert archive_target(_account_pattern("Archive/{jahr}"), when, separator="/") == "Archive/2026"


async def test_the_sender_in_the_pattern():
    import datetime as dt

    from app.services.mailbox import archive_target

    target = archive_target(_account_pattern("Archive/{absender_domain}/{jahr}"),
                       dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc), "support@haendler.example")
    assert target == "Archive/haendler.example/2026"


async def test_a_typo_in_the_pattern_creates_no_bracket_folder():
    """`{jhar}` is a typo. It should stand out — but not produce a folder with curly braces in
    its name that one clears away by hand afterwards."""
    import datetime as dt

    from app.services.mailbox import archive_target

    target = archive_target(_account_pattern("Archive/{jhar}"),
                       dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))
    assert "{" not in target and target == "Archive"


async def test_a_fixed_folder_stays_a_fixed_folder():
    import datetime as dt

    from app.services.mailbox import archive_target

    account = MailAccount(name="p", archive_mode="folder", folder_archive="Archiv",
                        archive_pattern="Archive/{jahr}")
    assert archive_target(account, dt.datetime(2023, 5, 5, tzinfo=dt.timezone.utc)) == "Archiv"


# ── The paperclip in the list ───────────────────────────────────────────────

async def test_an_attachment_is_recognised_without_loading_the_mail():
    """The paperclip comes out of the BODYSTRUCTURE — a list of fifty messages must not drag
    fifty attachments across the network."""
    from app.services.mailbox import _has_attachment

    mit = ((b"text", b"plain", (b"charset", b"utf-8"), None, None, b"7bit", 12, 1),
           (b"application", b"pdf", (b"name", b"r.pdf"), None, None, b"base64", 9000,
            (b"attachment", (b"filename", b"r.pdf"))), b"mixed")
    without = ((b"text", b"plain", (b"charset", b"utf-8"), None, None, b"7bit", 12, 1),
            (b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", 40, 2),
            b"alternative")

    assert _has_attachment(mit) is True
    assert _has_attachment(without) is False
    assert _has_attachment(None) is False


async def test_an_embedded_logo_is_not_an_attachment():
    """Otherwise every advertising mail would carry a paperclip, and it would be no information any more."""
    from app.services.mailbox import _has_attachment

    inline = ((b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", 40, 2),
              (b"image", b"png", (b"name", b"logo.png"), b"<logo>", None, b"base64", 500,
               (b"inline", (b"filename", b"logo.png"))), b"related")
    assert _has_attachment(inline) is False


# ── Mailboxes as MCP ────────────────────────────────────────────────────────

async def _mcp_account(db, user, **over) -> MailAccount:
    values = {"name": "privat", "owner_user_id": user.id, "enabled": True,
             "mcp_enabled": True, "mcp_tools": ["mail_folders", "mail_search"],
             "mcp_ignore_folders": ["Junk", "Privat*"]}
    values.update(over)
    k = MailAccount(**values)
    db.add(k)
    await db.commit()
    return k


async def test_only_released_tools_appear_in_the_catalog(db):
    """The default is nothing. What stands in the listing somebody switched on."""
    from app.services.mail_mcp import toollist

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna)

    names = {w["name"] for w in await toollist(db, anna)}
    assert names == {"mail_accounts", "mail_folders", "mail_search"}
    assert "mail_send" not in names


async def test_a_disabled_mailbox_does_not_exist(db):
    from app.services.mail_mcp import execute, toollist

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna, mcp_enabled=False)

    assert [w["name"] for w in await toollist(db, anna)] == ["mail_accounts"]
    with pytest.raises(LookupError):
        await execute(db, anna, "mail_folders", {"account": "privat"})


async def test_a_blocked_tool_is_rejected(db):
    from app.services.mail_mcp import execute

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna)
    with pytest.raises(PermissionError):
        await execute(db, anna, "mail_get", {"account": "privat", "uid": 1})


async def test_foreign_mailboxes_stay_invisible(db):
    from app.services.mail_mcp import execute

    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await _mcp_account(db, anna)
    with pytest.raises(LookupError):
        await execute(db, bert, "mail_folders", {"account": "privat"})


async def test_ignored_folders_are_not_a_screen_but_a_block(db, monkeypatch):
    """A hidden folder must be no target either — otherwise one could push mail behind the
    Sichtschutz schieben."""
    from app.services import mail_mcp

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna, mcp_tools=["mail_folders", "mail_move"])

    async def fake_folder(account, count=False):
        return [{"name": "INBOX"}, {"name": "Junk"}, {"name": "Privat.Familie"}]
    monkeypatch.setattr(mail_mcp.mailbox, "folder", fake_folder)

    visible = await mail_mcp.execute(db, anna, "mail_folders", {"account": "privat"})
    assert [o["name"] for o in visible] == ["INBOX"]

    with pytest.raises(PermissionError):
        await mail_mcp.execute(db, anna, "mail_move",
                                  {"account": "privat", "uid": 1, "target": "Privat.Familie"})
    with pytest.raises(PermissionError):
        await mail_mcp.execute(db, anna, "mail_move",
                                  {"account": "privat", "folder": "Junk", "uid": 1,
                                   "target": "INBOX"})


async def test_the_pattern_of_the_ignore_list():
    from app.services.mail_mcp import ignores

    assert ignores("Junk", ["Junk"]) is True
    assert ignores("Privat.Familie", ["Privat*"]) is True
    assert ignores("INBOX", ["Privat*", "Junk"]) is False


async def test_instructions_appear_on_connect_and_on_the_account(db):
    """House rules belong where an agent gets to know the mailbox — not into a
    Datei, die er vielleicht liest."""
    from app.services.mail_mcp import instructions, execute

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna, name="vorstand",
                     mcp_instructions="Sachlich und in Sie-Form. Nichts ohne Rückfrage senden.")

    text = await instructions(db, anna)
    assert "vorstand" in text and "Sie-Form" in text

    accounts = await execute(db, anna, "mail_accounts", {})
    assert accounts[0]["instructions"].startswith("Sachlich")


async def test_no_empty_field_without_an_instruction(db):
    """An empty hint is worse than none: it looks like a rule."""
    from app.services.mail_mcp import instructions, execute

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna)
    assert await instructions(db, anna) == ""
    assert "instructions" not in (await execute(db, anna, "mail_accounts", {}))[0]


# ── Handgriffe am ganzen Ordner ─────────────────────────────────────────────

async def test_special_folders_are_protected_from_deletion(db, client):
    """Whoever deletes their trash afterwards has a delete that no longer works."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account(folder_trash="Trash"))).json()["id"]

    for folder in ("INBOX", "Trash"):
        r = await client.post(f"/mailbox/accounts/{kid}/folders/delete", headers=auth(anna),
                              json={"folder": folder})
        assert r.status_code == 400, f"{folder} muss geschützt sein"


async def test_an_ordinary_folder_may_be_deleted(db, client, monkeypatch):
    from app.services import mailbox as mb

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    deleted = []

    async def fake_delete(account, folder):
        deleted.append(folder)
    monkeypatch.setattr(mb, "folder_delete", fake_delete)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/delete", headers=auth(anna),
                          json={"folder": "Alte Newsletter"})
    assert r.status_code == 204 and deleted == ["Alte Newsletter"]


# ── What a flow can do with a mail ──────────────────────────────────────────

async def test_a_flow_marks_a_mail_as_read(db, monkeypatch):
    """The most common handgrip of all — and until just now the only one a flow could not do:
    whoever has filed a mail wants it marked as read afterwards."""
    from app.services.workflow_actions import _mail_flag

    anna = await make_user(db, "anna")
    account = MailAccount(owner_user_id=anna.id, name="privat", imap_host="imap.example.org")
    db.add(account)
    await db.commit()

    marked = []

    async def flag(k, folder, uid, kind, an):
        marked.append((k.id, folder, uid, kind, an))

    monkeypatch.setattr(mailbox, "flag", flag)
    inst = type("Inst", (), {"context": {}, "started_by": anna.id,
                             "definition_id": 1, "id": 1})()
    result = await _mail_flag(db, inst, {}, {"mail": {"account_id": account.id,
                                                        "folder": "INBOX", "uid": 7}})

    assert result["set"] is True and result["flag"] == "seen" and result["on"] is True
    assert marked == [(account.id, "INBOX", 7, "seen", True)]


async def test_without_a_mail_in_the_context_the_node_says_why(db):
    """A flow that comes from a job has no mail — that is no crash."""
    from app.services.workflow_actions import _mail_flag

    inst = type("Inst", (), {"context": {}, "started_by": None, "definition_id": 1, "id": 1})()
    result = await _mail_flag(db, inst, {}, {})
    assert result["set"] is False and "no mail" in result["reason"]


async def test_a_move_without_a_target_goes_to_the_archive(db, monkeypatch):
    """That way a flow can say "done, away with it" without knowing the folder name."""
    from app.services.workflow_actions import _mail_move

    anna = await make_user(db, "anna")
    account = MailAccount(owner_user_id=anna.id, name="privat", imap_host="imap.example.org",
                        folder_archive="Archive")
    db.add(account)
    await db.commit()

    async def archive(k, folder, uid):
        return "Archive/2026"

    monkeypatch.setattr(mailbox, "archive", archive)
    inst = type("Inst", (), {"context": {}, "started_by": anna.id,
                             "definition_id": 1, "id": 1})()
    result = await _mail_move(db, inst, {}, {"mail": {"account_id": account.id,
                                                        "folder": "INBOX", "uid": 9}})
    assert result == {"action": "mail_move", "moved": True,
                        "target": "Archive/2026", "uid": 9}


# ── Ordner: leeren, anlegen, umbenennen ─────────────────────────────────────

class _FakeIMAP:
    """As much IMAP as the folder handles touch. It remembers what was asked of it."""

    def __init__(self, uids: list[int], move: bool = True):
        self.uids, self._move = uids, move
        self.selected = ""
        self.moved: list[tuple[list[int], str]] = []
        self.flagged: list[tuple[list[int], list]] = []
        self.expunged = False
        self.created: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.subscribed: list[str] = []

    def select_folder(self, name, readonly=False):
        self.selected = name

    def search(self, criteria):
        return list(self.uids)

    def has_capability(self, name):
        return self._move and name == "MOVE"

    def move(self, uids, target):
        self.moved.append((list(uids), target))

    def copy(self, uids, target):
        self.moved.append((list(uids), target))

    def add_flags(self, uids, flags):
        self.flagged.append((list(uids), list(flags)))

    def expunge(self):
        self.expunged = True

    def create_folder(self, name):
        self.created.append(name)

    def rename_folder(self, name, target):
        self.renamed.append((name, target))

    def subscribe_folder(self, name):
        self.subscribed.append(name)


def _fake_imap(monkeypatch, client) -> None:
    import contextlib

    @contextlib.contextmanager
    def borrow(account):
        yield client
    monkeypatch.setattr(mailbox, "_imap", borrow)


async def test_emptying_moves_into_the_trash(monkeypatch):
    """Emptying is what deleting a single message is: a movement, not a loss."""
    fake = _FakeIMAP([1, 2, 3])
    _fake_imap(monkeypatch, fake)
    account = MailAccount(name="privat", folder_trash="Papierkorb")

    result = mailbox._folder_empty_sync(account, "Newsletter", "Papierkorb")

    assert result == {"deleted": 3, "target": "Papierkorb"}
    assert fake.moved == [([1, 2, 3], "Papierkorb")]
    assert not fake.flagged, "verschoben wird verschoben, nicht zusätzlich gelöscht"


async def test_emptying_the_trash_is_final(monkeypatch):
    """In the trash there is nothing left to move to. It would be a move onto itself, and
    the folder would stay full."""
    fake = _FakeIMAP([7, 8])
    _fake_imap(monkeypatch, fake)
    account = MailAccount(name="privat", folder_trash="Papierkorb")

    result = mailbox._folder_empty_sync(account, "Papierkorb", "Papierkorb")

    assert result == {"deleted": 2, "target": ""}
    assert not fake.moved
    assert fake.flagged == [([7, 8], [b"\\Deleted"])] and fake.expunged


async def test_emptying_asks_in_blocks(monkeypatch):
    """A folder with ten thousand mails must not become a single command."""
    fake = _FakeIMAP(list(range(1, 1201)))
    _fake_imap(monkeypatch, fake)

    mailbox._folder_empty_sync(MailAccount(name="p"), "Alt", "Papierkorb")

    assert [len(block) for block, _ in fake.moved] == [500, 500, 200]


async def test_an_empty_folder_stays_untouched(monkeypatch):
    fake = _FakeIMAP([])
    _fake_imap(monkeypatch, fake)

    assert mailbox._folder_empty_sync(MailAccount(name="p"), "Leer", "Papierkorb") == {
        "deleted": 0, "target": ""}
    assert not fake.moved and not fake.expunged


async def _folders(monkeypatch, separator: str = ".") -> None:
    async def fake_folder(account, count=False):
        return [{"name": "INBOX", "display": "INBOX", "level": 0, "parent": "",
                 "delimiter": separator, "special": "inbox", "unseen": 0, "total": 0},
                {"name": f"INBOX{separator}Archiv", "display": "Archiv", "level": 1,
                 "parent": "INBOX", "delimiter": separator, "special": "", "unseen": 0,
                 "total": 0}]
    monkeypatch.setattr(mailbox, "folder", fake_folder)


async def test_a_new_folder_takes_the_separator_of_the_server(db, client, monkeypatch):
    """A slash in the name is a folder called "a/b" on a server that nests with dots."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    await _folders(monkeypatch)
    created = []

    async def fake_create(account, name):
        created.append(name)
    monkeypatch.setattr(mailbox, "folder_create", fake_create)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/create", headers=auth(anna),
                          json={"name": "2026", "parent": "INBOX.Archiv"})
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == "INBOX.Archiv.2026"
    assert created == ["INBOX.Archiv.2026"]

    # And a name that carries the separator is a mistake, not a second level.
    r = await client.post(f"/mailbox/accounts/{kid}/folders/create", headers=auth(anna),
                          json={"name": "Archiv.2026"})
    assert r.status_code == 400
    assert created == ["INBOX.Archiv.2026"]


async def test_renaming_keeps_the_folder_where_it_hangs(db, client, monkeypatch):
    """Without `parent` only the name changes, the path around it stays."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    await _folders(monkeypatch)
    renamed = []

    async def fake_rename(account, folder, target):
        renamed.append((folder, target))
    monkeypatch.setattr(mailbox, "folder_rename", fake_rename)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/rename", headers=auth(anna),
                          json={"folder": "INBOX.Archiv", "name": "Ablage"})
    assert r.status_code == 200, r.text
    assert renamed == [("INBOX.Archiv", "INBOX.Ablage")]

    # Mit `parent` ist dasselbe Kommando auch der Umzug.
    r = await client.post(f"/mailbox/accounts/{kid}/folders/rename", headers=auth(anna),
                          json={"folder": "INBOX.Archiv", "name": "Archiv", "parent": ""})
    assert r.status_code == 200, r.text
    assert renamed[-1] == ("INBOX.Archiv", "Archiv")


async def test_a_folder_does_not_move_into_itself(db, client, monkeypatch):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    await _folders(monkeypatch)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/rename", headers=auth(anna),
                          json={"folder": "INBOX.Archiv", "name": "Archiv",
                                "parent": "INBOX.Archiv"})
    assert r.status_code == 400


async def test_a_special_folder_is_not_renamed(db, client, monkeypatch):
    """The trash hangs on the delete button. Renamed it would be a button that does nothing."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account(folder_trash="Papierkorb"))).json()["id"]
    await _folders(monkeypatch)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/rename", headers=auth(anna),
                          json={"folder": "Papierkorb", "name": "Muell"})
    assert r.status_code == 400
    assert "Papierkorb" in r.text


async def test_all_attachments_are_one_run_each(db, client, monkeypatch):
    """A flow is the way of ONE attachment. Three files are therefore three runs, not one
    that has to loop by itself."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def fake_message(account, folder, uid):
        return {"subject": "Belege", "from": [{"addr": "shop@example.org"}], "attachments": [
            {"index": 1, "filename": "a.pdf", "content_type": "application/pdf", "size": 1},
            {"index": 2, "filename": "b.pdf", "content_type": "application/pdf", "size": 2}]}
    monkeypatch.setattr(mailbox, "message", fake_message)

    d = WorkflowDefinition(project_id=None, key="paperless", name="Nach Paperless",
                           created_by=anna.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                        graph={"nodes": [
                            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                             "data": {"config": {"trigger": {"kind": "mail_action",
                                                              "scope": "attachment"}}}},
                            {"id": "e", "type": "end", "position": {"x": 0, "y": 1},
                             "data": {"config": {"outcome": "completed"}}}],
                            "edges": [{"id": "k", "source": "s", "target": "e"}]})
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    r = await client.post(f"/mailbox/accounts/{kid}/messages/5/action", headers=auth(anna),
                          json={"definition_id": d.id, "all": True})
    assert r.status_code == 200, r.text
    assert [run["attachment"] for run in r.json()["runs"]] == ["a.pdf", "b.pdf"]

    rows = (await db.execute(select(WorkflowInstance))).scalars().all()
    assert sorted(i.source_ref for i in rows) == ["INBOX:5:1", "INBOX:5:2"]
    assert sorted(i.context["attachment"]["filename"] for i in rows) == ["a.pdf", "b.pdf"]


# ── Dateityp eines Anhangs ──────────────────────────────────────────────────

def _with_attachment(kind: str, name: str):
    """A mail with exactly one attachment of that declared type."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "mit Anhang"
    msg.set_content("siehe Anhang")
    main, _, sub = kind.partition("/")
    msg.add_attachment(b"%PDF-1.4 ...", maintype=main, subtype=sub, filename=name)
    return msg


async def test_a_pdf_declared_as_nothing_is_still_a_pdf():
    """Plenty of senders declare every attachment as `application/octet-stream`. The preview
    then had nothing to go on and offered none, for a file called `rechnung.pdf`."""
    from app.services.mailbox import _attachments

    found = _attachments(_with_attachment("application/octet-stream", "rechnung.pdf"))
    assert [(a["filename"], a["content_type"]) for a in found] == [
        ("rechnung.pdf", "application/pdf")]


async def test_a_declared_type_keeps_the_last_word():
    """Only the generic types are a guess. What a sender really says stands."""
    from app.services.mailbox import _attachments

    found = _attachments(_with_attachment("image/png", "bild.jpg"))
    assert found[0]["content_type"] == "image/png"


async def test_without_an_extension_nothing_is_invented():
    from app.services.mailbox import _attachments

    found = _attachments(_with_attachment("application/octet-stream", "datei"))
    assert found[0]["content_type"] == "application/octet-stream"
