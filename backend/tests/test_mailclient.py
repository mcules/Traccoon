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
    assert "geheim" not in r.text, "the password must never reach the interface"

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

    d = WorkflowDefinition(project_id=None, key="anhang-paperless", name="Attachment to the archive",
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
    """Three things at once: no script, no form, no silent image fetch.

    The picture in here is an ordinary one on purpose. It used to be called `pixel.gif`, and
    that name means something now: it would be thrown out as a counter, and this test would
    then be checking the counting rule instead of the cleaning.
    """
    from app.services.mailbox import clean

    clean, fern, _ = clean(
        '<p onclick="alert(1)">Hallo<script>alert(2)</script>'
        '<img src="https://cdn.example/header.jpg" width="600">'
        '<img src="data:image/png;base64,AAA">'
        '<form action="https://phish"><input name="pw"></form>'
        '<a href="javascript:evil()">klick</a></p>')

    assert "script" not in clean and "onclick" not in clean
    assert "<form" not in clean and "<input" not in clean
    assert "javascript:" not in clean
    # The remote image stays visible but loads nothing.
    assert 'data-fern="https://cdn.example/header.jpg"' in clean
    assert fern is True
    # An embedded image is no remote image and stays as it is.
    assert 'src="data:image/png;base64,AAA"' in clean


async def test_no_warning_without_remote_images():
    from app.services.mailbox import clean

    clean, fern, _ = clean('<p>Nur <b>Text</b> und <img src="data:image/gif;base64,AA">.</p>')
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


async def test_a_mailbox_without_rules_adds_no_empty_line(db):
    """An empty hint is worse than none: it looks like a rule.

    What always stands there is the one rule that belongs to no mailbox in particular, how an
    answer is written. A mailbox that has nothing of its own to say adds nothing to it.
    """
    from app.services.mail_mcp import instructions, execute

    anna = await make_user(db, "anna")
    account = await _mcp_account(db, anna)
    text = await instructions(db, anna)
    assert "reply_uid" in text, "the standing rule stands there"
    assert account.name not in text, "and nothing about a mailbox that said nothing"
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
        self.unflagged: list[tuple[list[int], list]] = []
        self.there: set[str] = {"INBOX"}
        self.envelopes: dict[int, dict] = {}

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

    def remove_flags(self, uids, flags):
        self.unflagged.append((list(uids), list(flags)))

    def folder_exists(self, name):
        return name in self.there

    def list_folders(self):
        return [([], b".", "INBOX")]

    def fetch(self, uids, what):
        return {uid: self.envelopes.get(uid, {}) for uid in uids}


def _fake_imap_maker(client):
    """Derselbe Leih-Kontext, aber als Wert: `newsletters` hat sein eigenes `_imap`."""
    import contextlib

    @contextlib.contextmanager
    def borrow(account):
        yield client
    return borrow


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
    assert not fake.flagged, "moved is moved, not deleted on top of it"


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

    # With `parent` the same command is the move as well.
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


# ── Hat die Mail überhaupt einen HTML-Teil? ─────────────────────────────────

async def test_a_wrapper_without_content_is_no_html_part():
    """Plenty of senders hang a second part on the mail that is nothing but a wrapper and a
    tracking pixel. What is left after the cleaning is an empty white box, offered as
    "formatted" beside the text nobody gets to see."""
    from app.services.mailbox import has_content

    assert not has_content('<div><span>&nbsp;</span></div>')
    assert not has_content("")
    assert not has_content("<p></p><table><tr><td></td></tr></table>")


async def test_a_mail_that_is_one_picture_stays_html():
    """A newsletter that consists of a single graphic carries no text, and it is still a
    formatted mail."""
    from app.services.mailbox import has_content

    assert has_content('<div><img data-fern="https://example.org/a.png"></div>')
    assert has_content("<p>Guten Tag</p>")


# ── Mehrere Nachrichten auf einmal ──────────────────────────────────────────

async def test_a_selection_is_one_command_not_thirty(monkeypatch):
    """Thirty ticked mails used to be thirty requests, each with its own round trip."""
    fake = _FakeIMAP([])
    _fake_imap(monkeypatch, fake)
    account = MailAccount(name="p", folder_trash="Papierkorb")

    result = mailbox._bulk_sync(account, "INBOX", [1, 2, 3], "delete")

    assert result == {"done": 3, "action": "delete", "target": "Papierkorb"}
    assert fake.moved == [([1, 2, 3], "Papierkorb")]


async def test_deleting_in_the_trash_is_final_here_too(monkeypatch):
    fake = _FakeIMAP([])
    _fake_imap(monkeypatch, fake)
    account = MailAccount(name="p", folder_trash="Papierkorb")

    result = mailbox._bulk_sync(account, "Papierkorb", [4, 5], "delete")

    assert result["target"] == ""
    assert fake.flagged == [([4, 5], [b"\\Deleted"])] and fake.expunged


async def test_marking_many_read_and_unread(monkeypatch):
    fake = _FakeIMAP([])
    _fake_imap(monkeypatch, fake)

    mailbox._bulk_sync(MailAccount(name="p"), "INBOX", [1, 2], "flag", flag="\\Seen", on=True)
    mailbox._bulk_sync(MailAccount(name="p"), "INBOX", [3], "flag", flag="\\Seen", on=False)

    assert fake.flagged == [([1, 2], [b"\\Seen"])]
    assert fake.unflagged == [([3], [b"\\Seen"])]


async def test_archiving_many_groups_by_year(monkeypatch):
    """A pattern archive gives every message its own target. Thirty mails from three years
    are three MOVE commands, not thirty."""
    import datetime as dt

    fake = _FakeIMAP([])
    fake.envelopes = {
        1: {b"INTERNALDATE": dt.datetime(2024, 3, 1)},
        2: {b"INTERNALDATE": dt.datetime(2024, 7, 1)},
        3: {b"INTERNALDATE": dt.datetime(2026, 1, 5)},
    }
    _fake_imap(monkeypatch, fake)
    account = MailAccount(name="p", archive_mode="pattern", archive_pattern="Archiv/{year}",
                          folder_archive="Archiv")

    result = mailbox._bulk_sync(account, "INBOX", [1, 2, 3], "archive")

    assert result["targets"] == ["Archiv.2024", "Archiv.2026"]
    assert sorted(fake.moved) == [([1, 2], "Archiv.2024"), ([3], "Archiv.2026")]
    assert sorted(fake.created) == ["Archiv.2024", "Archiv.2026"]


async def test_an_empty_selection_asks_the_mailbox_nothing(db, client, monkeypatch):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def never(*a, **k):
        raise AssertionError("the mailbox had nothing to do here")
    monkeypatch.setattr(mailbox, "bulk", never)

    r = await client.post(f"/mailbox/accounts/{kid}/messages/bulk", headers=auth(anna),
                          json={"folder": "INBOX", "uids": [], "action": "delete"})
    assert r.status_code == 200 and r.json()["done"] == 0


async def test_an_unknown_action_is_refused(db, client):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    r = await client.post(f"/mailbox/accounts/{kid}/messages/bulk", headers=auth(anna),
                          json={"uids": [1], "action": "verbrennen"})
    assert r.status_code == 400


# ── Search across the whole mailbox ─────────────────────────────────────────

class _SearchIMAP(_FakeIMAP):
    """A mailbox with several folders, two of which find something."""

    def __init__(self, hits: dict[str, list[int]], dates: dict[int, object]):
        super().__init__([])
        self.hits, self.dates = hits, dates
        self.searched: list[str] = []

    def list_folders(self):
        return [([], b".", name) for name in ["INBOX", "Archiv", "Papierkorb"]] + [
            ([b"\\Noselect"], b".", "Container")]

    def search(self, criteria):
        self.searched.append(self.selected)
        return list(self.hits.get(self.selected, []))

    def fetch(self, uids, what):
        return {uid: {b"INTERNALDATE": self.dates[uid]} for uid in uids}


async def test_the_search_across_the_mailbox_asks_every_folder(monkeypatch):
    """Whoever files by year cannot search in a folder: they would have to guess the year
    before being allowed to look."""
    import datetime as dt

    fake = _SearchIMAP({"INBOX": [1], "Archiv": [2, 3]},
                        {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2024, 5, 1),
                         3: dt.datetime(2026, 8, 1)})
    _fake_imap(monkeypatch, fake)

    result = mailbox._search_all_sync(MailAccount(name="p"), "Rechnung", 0, 50)

    # The container folder is not one: \\Noselect is skipped.
    assert fake.searched == ["INBOX", "Archiv", "Papierkorb"]
    assert result["total"] == 3
    # Sorted by date across folders, and every hit knows where it lies.
    assert [(m["folder"], m["uid"]) for m in result["messages"]] == [
        ("Archiv", 3), ("INBOX", 1), ("Archiv", 2)]


async def test_the_search_says_when_it_had_to_stop(monkeypatch):
    """Five hundred hits are the cap. A cut-off result that looks complete would be the worse
    answer."""
    import datetime as dt

    viele = list(range(1, 700))
    fake = _SearchIMAP({"INBOX": viele},
                        {uid: dt.datetime(2026, 1, 1) for uid in viele})
    _fake_imap(monkeypatch, fake)

    result = mailbox._search_all_sync(MailAccount(name="p"), "a", 0, 50)

    assert result["capped"] is True
    assert result["total"] == mailbox.SEARCH_CAP


async def test_a_folder_that_refuses_does_not_end_the_search(monkeypatch):
    """One folder without permission must not swallow the hits of the other twenty."""
    import datetime as dt

    fake = _SearchIMAP({"INBOX": [1], "Archiv": [2]},
                        {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2026, 2, 1)})
    echt = fake.select_folder

    def zickig(name, readonly=False):
        if name == "Papierkorb":
            raise RuntimeError("kein Zugriff")
        echt(name, readonly)
    fake.select_folder = zickig
    _fake_imap(monkeypatch, fake)

    result = mailbox._search_all_sync(MailAccount(name="p"), "a", 0, 50)

    assert result["total"] == 2


# ── What is left of a foreign mail ──────────────────────────────────────────

async def test_a_style_block_survives_the_cleaning():
    """Half the senders put their table widths in a `<style>` block. Without it the table is
    squeezed into nothing, and "Status" arrives as a column of single letters.

    It cannot do any harm: the frame it is shown in forbids loading anything.
    """
    from app.services.mailbox import clean

    html, _, _ = clean('<style>th { width: 1% }</style><table><tr><th>Status</th></tr></table>')
    assert "<style>" in html and "width: 1%" in html


async def test_a_script_goes_with_its_content():
    """Without that the code would be gone and its text would stand in the mail."""
    from app.services.mailbox import clean

    html, _, _ = clean('<p>Hallo</p><script>alert("hallo")</script>')
    assert "alert" not in html and "Hallo" in html


async def test_a_picture_in_the_style_block_counts_as_a_remote_one():
    """A tracking pixel can hide in the CSS. The frame blocks it like any other, so the notice
    above the mail has to say so."""
    from app.services.mailbox import clean

    _, remote, _ = clean('<style>body { background: url(https://tracker.example.org/p.gif) }</style>')
    assert remote is True

    _, own, _ = clean('<style>body { background: url(data:image/gif;base64,AA) }</style>')
    assert own is False


async def test_die_mail_wird_immer_im_hellen_gezeigt():
    """Post steht hier auf weißem Grund - also gilt für sie hell, egal was das System sagt.

    Halbfertige Dunkelmodus-Regeln sind in Rundmails der Normalfall: der Text wird auf Weiß
    gesetzt, der Kasten darunter bleibt weiß. Gemessen an einer echten Rundmail waren im
    Dunkeln 13 von 23 Textstellen unlesbar (Kontrast 1,0), im Hellen keine einzige. Deshalb
    wird die Dunkelfrage falsch und die Hellfrage wahr - keine Regel wird weggeworfen, keine
    Farbe geraten.
    """
    from app.services.mailbox import clean

    html, _, _ = clean(
        "<style>@media (prefers-color-scheme: dark) { body { color: #fff } }"
        "@media (prefers-color-scheme:light) { body { color: #000 } }</style><p>Moin</p>")

    assert "prefers-color-scheme" not in html
    assert "(max-width: 0px) { body { color: #fff } }" in html
    assert "(min-width: 0px) { body { color: #000 } }" in html
    # Und der Text steht noch da: stillgelegt wird die Frage, nicht die Antwort.
    assert "Moin" in html


async def test_eine_mail_ohne_dunkelmodus_bleibt_unangetastet():
    from app.services.mailbox import clean

    html, _, _ = clean("<style>@media (max-width: 600px) { td { display: block } }</style>")

    assert "(max-width: 600px)" in html


# ── Bilder von fremden Servern ──────────────────────────────────────────────

async def test_a_counting_pixel_never_comes_back():
    """A picture one pixel across is not there to be seen. It goes before anybody is asked,
    and it stays gone even when somebody presses "load pictures": whoever wants to see the
    mail wants to see the mail, not report having read it."""
    from app.services.mailbox import clean

    html, remote, counted = clean(
        '<p>Hallo</p>'
        '<img src="https://shop.example.org/o/abc123.gif" width="1" height="1">'
        '<img src="https://shop.example.org/logo.png" width="200" height="80">')

    assert counted == 1
    assert "abc123" not in html, "the tracking pixel must not stand anywhere any more"
    assert 'data-fern="https://shop.example.org/logo.png"' in html
    assert remote is True


async def test_a_dispatch_house_tracker_is_recognised_by_its_address():
    """The big dispatch houses put their open-tracker on a fixed path. Size alone would not
    catch it: plenty of them ship it at 20 by 20."""
    from app.services.mailbox import clean

    _, _, counted = clean('<img src="https://x.list-manage.com/track/open.php?u=1&id=2" '
                           'width="20" height="20">')
    assert counted == 1


async def test_an_ordinary_picture_stays_an_ordinary_picture():
    """The recognition must not eat the logo. Wrong in this direction is the loud kind of
    wrong: a hole in the middle of the mail that nobody can explain."""
    from app.services.mailbox import clean

    html, remote, counted = clean(
        '<img src="https://cdn.example.org/newsletter/header.jpg" width="600" height="200">')
    assert counted == 0 and remote is True
    assert "header.jpg" in html


async def test_the_kept_answer_is_looked_up_by_sender_and_by_house(db, client):
    """Three reaches, and the widest wins."""
    from app.api.mailbox import _images_allowed
    from app.models.mail import MailImageRule

    only_sender = [MailImageRule(kind="sender", value="news@example.org")]
    assert _images_allowed(only_sender, "news@example.org") is True
    assert _images_allowed(only_sender, "billing@example.org") is False

    house = [MailImageRule(kind="domain", value="example.org")]
    assert _images_allowed(house, "billing@example.org") is True
    assert _images_allowed(house, "billing@example.net") is False

    everything = [MailImageRule(kind="all", value="")]
    assert _images_allowed(everything, "wer@auch.immer") is True
    # A broken header names no house. It falls through, which is the careful direction.
    assert _images_allowed(house, "") is False


async def test_a_rule_is_only_stored_once(db, client):
    anna = await make_user(db, "anna")

    for _ in range(2):
        r = await client.post("/mailbox/image-rules", headers=auth(anna),
                              json={"kind": "sender", "value": "Post@Beispiel.DE"})
        assert r.status_code in (200, 201), r.text

    rows = (await client.get("/mailbox/image-rules", headers=auth(anna))).json()
    # Stored in lower case: mail addresses are not case sensitive where it matters, and two
    # rules for the same sender would be one to delete twice.
    assert [(r["kind"], r["value"]) for r in rows] == [("sender", "post@beispiel.de")]


async def test_foreign_rules_stay_foreign(db, client):
    anna, bert = await make_user(db, "anna"), await make_user(db, "bert")
    made = (await client.post("/mailbox/image-rules", headers=auth(anna),
                              json={"kind": "all"})).json()

    r = await client.delete(f"/mailbox/image-rules/{made['id']}", headers=auth(bert))
    assert r.status_code == 204
    assert len((await client.get("/mailbox/image-rules", headers=auth(anna))).json()) == 1


# ── Newsletter-Abos ─────────────────────────────────────────────────────────

def _newsletter_head(sender: str, out: str, post: str = "", list_id: str = "") -> bytes:
    zeilen = [f"From: {sender}", f"List-Unsubscribe: {out}"]
    if post:
        zeilen.append(f"List-Unsubscribe-Post: {post}")
    if list_id:
        zeilen.append(f"List-Id: {list_id}")
    return ("\r\n".join(zeilen) + "\r\n\r\n").encode()


class _NewsIMAP(_FakeIMAP):
    """Ein Ordner voller Kopfzeilen, mehr braucht die Abo-Sicht nicht."""

    def __init__(self, mails: dict[int, bytes], dates: dict[int, object]):
        super().__init__(list(mails))
        self.mails, self.dates = mails, dates

    def fetch(self, uids, what):
        key = b"BODY[HEADER.FIELDS (FROM LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST LIST-ID SUBJECT)]"
        return {uid: {key: self.mails[uid], b"INTERNALDATE": self.dates[uid]} for uid in uids}


async def test_only_what_says_it_is_a_newsletter_turns_up(monkeypatch):
    """No guessing: a subscription declares itself (RFC 2369). Whoever sends without the
    header does not appear, and for those the way out is the junk folder, not a button."""
    import datetime as dt

    from app.services import newsletters

    fake = _NewsIMAP({
        1: _newsletter_head("Shop <news@shop.de>", "<https://shop.de/u?a=1>",
                             post="List-Unsubscribe=One-Click"),
        2: b"From: Kollege <kollege@firma.de>\r\n\r\n",
        3: _newsletter_head("Shop <News@Shop.de>", "<mailto:stop@shop.de>"),
    }, {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2026, 2, 1), 3: dt.datetime(2026, 3, 1)})
    monkeypatch.setattr(newsletters, "_imap", _fake_imap_maker(fake))

    listing = newsletters._scan_sync(MailAccount(name="p"), ["INBOX"])

    assert [e["key"] for e in listing] == ["news@shop.de"], "a colleague is not a subscription"
    only = listing[0]
    # Two mails from the same sender are a subscription, spelling differences included.
    assert only["count"] == 2
    # The way out comes from the NEWEST mail: an address from three years ago is dead.
    assert only["mailto"] == "mailto:stop@shop.de"


async def test_a_list_id_beats_the_sender(monkeypatch):
    """One house can run several lists from one address."""
    import datetime as dt

    from app.services import newsletters

    fake = _NewsIMAP({
        1: _newsletter_head("Verein <post@verein.de>", "<mailto:a@verein.de>",
                             list_id="<technik.verein.de>"),
        2: _newsletter_head("Verein <post@verein.de>", "<mailto:b@verein.de>",
                             list_id="<vorstand.verein.de>"),
    }, {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2026, 1, 2)})
    monkeypatch.setattr(newsletters, "_imap", _fake_imap_maker(fake))

    listing = newsletters._scan_sync(MailAccount(name="p"), ["INBOX"])
    assert sorted(e["key"] for e in listing) == ["technik.verein.de", "vorstand.verein.de"]


async def test_one_click_is_recognised(monkeypatch):
    import datetime as dt

    from app.services import newsletters

    fake = _NewsIMAP({
        1: _newsletter_head("A <a@x.de>", "<https://x.de/u>, <mailto:u@x.de>",
                             post="List-Unsubscribe=One-Click"),
        2: _newsletter_head("B <b@y.de>", "<https://y.de/seite>"),
    }, {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2026, 1, 1)})
    monkeypatch.setattr(newsletters, "_imap", _fake_imap_maker(fake))

    listing = {e["sender"]: e for e in newsletters._scan_sync(MailAccount(name="p"), ["INBOX"])}
    assert listing["a@x.de"]["one_click"] is True
    assert listing["a@x.de"]["http"] == "https://x.de/u"
    # Without the post line the address is a page for people, not a button for us.
    assert listing["b@y.de"]["one_click"] is False


async def test_a_page_is_not_clicked_for_anybody(db, client):
    """An unsubscribe page often carries a confirmation. Pressing that unread is not
    unsubscribing, it is guessing."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    r = await client.post(f"/mailbox/accounts/{kid}/newsletters/unsubscribe",
                          headers=auth(anna),
                          json={"http": "https://shop.de/abmelden", "one_click": False})
    assert r.status_code == 200
    assert r.json() == {"done": False, "way": "link", "detail": "https://shop.de/abmelden"}


# ── Unsubscribed, and what is left of it ────────────────────────────────────

async def test_an_unsubscribing_that_worked_is_written_down(db, client, monkeypatch):
    """Unsubscribing is a request, not a switch. Whoever still gets mail four weeks later
    wants to say when they asked and how."""
    from app.services import newsletters

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def worked(url):
        return True, "HTTP 200"
    monkeypatch.setattr(newsletters, "one_click", worked)

    r = await client.post(f"/mailbox/accounts/{kid}/newsletters/unsubscribe",
                          headers=auth(anna),
                          json={"http": "https://shop.de/u", "one_click": True,
                                "key": "news@shop.de", "name": "Shop",
                                "sender": "news@shop.de"})
    assert r.json()["done"] is True

    listing = (await client.get(f"/mailbox/accounts/{kid}/unsubscribes",
                                 headers=auth(anna))).json()
    assert len(listing) == 1
    assert listing[0]["way"] == "one_click" and listing[0]["detail"] == "HTTP 200"
    assert listing[0]["when"], "without a moment the entry is worthless as a record"


async def test_a_failed_attempt_is_no_unsubscribing(db, client, monkeypatch):
    """An entry for it would hide the subscription from the overview although it goes on
    sending, and that is the one mistake this must not make."""
    from app.services import newsletters

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    async def gonewrong(url):
        return False, "HTTP 500"
    monkeypatch.setattr(newsletters, "one_click", gonewrong)

    r = await client.post(f"/mailbox/accounts/{kid}/newsletters/unsubscribe",
                          headers=auth(anna),
                          json={"http": "https://shop.de/u", "one_click": True,
                                "key": "news@shop.de"})
    assert r.json()["done"] is False
    assert (await client.get(f"/mailbox/accounts/{kid}/unsubscribes",
                             headers=auth(anna))).json() == []


async def test_what_is_unsubscribed_leaves_the_overview(db, client, monkeypatch):
    import datetime as dt

    from app.services import newsletters

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    fake = _NewsIMAP({
        1: _newsletter_head("Shop <news@shop.de>", "<mailto:stop@shop.de>"),
        2: _newsletter_head("Verein <post@verein.de>", "<mailto:stop@verein.de>"),
    }, {1: dt.datetime(2026, 1, 1), 2: dt.datetime(2026, 1, 2)})
    monkeypatch.setattr(newsletters, "_imap", _fake_imap_maker(fake))

    before = (await client.get(f"/mailbox/accounts/{kid}/newsletters",
                                headers=auth(anna))).json()
    assert len(before["newsletters"]) == 2

    async def sent(account, ident, fields):
        return None
    monkeypatch.setattr(mailbox, "send", sent)
    await client.post(f"/mailbox/accounts/{kid}/identities", headers=auth(anna),
                      json={"email": "ich@example.org", "is_default": True})
    await client.post(f"/mailbox/accounts/{kid}/newsletters/unsubscribe", headers=auth(anna),
                      json={"mailto": "mailto:stop@shop.de", "key": "news@shop.de",
                            "name": "Shop"})

    after = (await client.get(f"/mailbox/accounts/{kid}/newsletters",
                               headers=auth(anna))).json()
    assert [n["key"] for n in after["newsletters"]] == ["post@verein.de"]
    assert after["unsubscribed"] == 1

    # Und zurück in die Übersicht, wenn die Liste weitersendet.
    entry = (await client.get(f"/mailbox/accounts/{kid}/unsubscribes",
                               headers=auth(anna))).json()[0]
    await client.delete(f"/mailbox/accounts/{kid}/unsubscribes/{entry['id']}",
                        headers=auth(anna))
    back = (await client.get(f"/mailbox/accounts/{kid}/newsletters",
                              headers=auth(anna))).json()
    assert len(back["newsletters"]) == 2


# ── Was ein Anhang ist ──────────────────────────────────────────────────────

def _mit_teilen(teile) -> object:
    """Eine mehrteilige Mail aus (maintype, subtype, name, cid, disposition)."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "Newsletter"
    msg.set_content("Text")
    for main, sub, name, cid, disposition in teile:
        msg.add_attachment(b"x" * 100, maintype=main, subtype=sub, filename=name,
                            disposition=disposition,
                            **({"cid": cid} if cid else {}))
    return msg


async def test_a_picture_the_mail_shows_itself_is_no_attachment():
    """A newsletter builds its layout out of a dozen of them. As attachments they are a wall
    in front of the mail, and the list beside it says "no paperclip" for the same message."""
    from app.services.mailbox import _attachments

    found = _attachments(_mit_teilen([
        ("image", "png", "mailingassets_2d386b.png", "<logo1>", "inline"),
        ("image", "png", "mailingassets_256810.png", "<logo2>", "inline"),
        ("application", "pdf", "rechnung.pdf", None, "attachment"),
    ]))
    assert [a["filename"] for a in found] == ["rechnung.pdf"]


async def test_an_invoice_marked_inline_stays_an_attachment():
    """Being wrong in this direction leaves a file lying around, in the other one loses it."""
    from app.services.mailbox import _attachments

    found = _attachments(_mit_teilen([
        ("application", "pdf", "rechnung.pdf", "<beleg>", "inline"),
    ]))
    assert [a["filename"] for a in found] == ["rechnung.pdf"]


async def test_a_picture_without_a_content_id_stays_an_attachment():
    """Whoever sends a photo sends a photo, whether their program writes `inline` or not."""
    from app.services.mailbox import _attachments

    found = _attachments(_mit_teilen([
        ("image", "jpeg", "urlaub.jpg", None, "inline"),
    ]))
    assert [a["filename"] for a in found] == ["urlaub.jpg"]


# ── Bilder, die die Mail selbst mitbringt ───────────────────────────────────

async def test_a_picture_the_mail_carries_along_is_laid_into_it():
    """`cid:` is only meaningful inside the message, no browser can fetch it. What arrived
    was a mail full of empty frames, and "load pictures" did not help either: there was
    nothing out there to load."""
    from email.message import EmailMessage

    from app.services.mailbox import _lay_in

    msg = EmailMessage()
    msg.set_content("Text")
    msg.add_attachment(b"\x89PNG-daten", maintype="image", subtype="png",
                        filename="logo.png", cid="<imgLogo>", disposition="inline")

    html = _lay_in('<p><img src="cid:imgLogo" alt=""></p>', msg)

    assert "cid:" not in html
    assert "data:image/png;base64," in html


async def test_a_picture_that_is_not_there_stays_as_it_is():
    """A reference into nothing is left alone: an empty frame says more than a broken one."""
    from email.message import EmailMessage

    from app.services.mailbox import _lay_in

    msg = EmailMessage()
    msg.set_content("Text")

    html = _lay_in('<img src="cid:fehlt">', msg)
    assert html == '<img src="cid:fehlt">'


async def test_a_carried_picture_is_no_remote_picture():
    """Nothing is fetched for it, so nobody is told anything, so nobody has to allow it."""
    from email.message import EmailMessage

    from app.services.mailbox import _lay_in, clean

    msg = EmailMessage()
    msg.set_content("Text")
    msg.add_attachment(b"bild", maintype="image", subtype="gif", filename="l.gif",
                        cid="<l>", disposition="inline")

    _, remote, _ = clean(_lay_in('<img src="cid:l">', msg))
    assert remote is False


# ── What became of an attachment ────────────────────────────────────────────

async def _leerer_ablauf(db, user) -> tuple[int, int]:
    """A published definition with one version, a run needs no more."""
    d = WorkflowDefinition(project_id=None, key=f"melder-{user.id}", name="Melder",
                            created_by=user.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, status=WorkflowVersionStatus.published,
                         graph={"nodes": [], "edges": []})
    db.add(v)
    await db.flush()
    return d.id, v.id


def _knoten(params: dict) -> dict:
    """A flow node with the action `mail_document`, the way the editor builds it."""
    return {"id": "melden", "type": "auto_action",
            "data": {"config": {"action": "mail_document", **params}}}

async def test_the_archive_reports_back_which_document_it_became(db, client, monkeypatch):
    """Filing is a one way street with a gap in the middle: the upload answers with a task
    number, the document number comes minutes later. This is where it arrives."""
    from app.models.mail import MailDocument
    from app.models.workflow import WorkflowInstance
    from app.services.workflow_actions import run_action

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]

    def_id, ver_id = await _leerer_ablauf(db, anna)
    lauf = WorkflowInstance(
        definition_id=def_id, version_id=ver_id, subject_kind=WorkflowSubjectKind.standalone,
        source="mail:privat", source_ref="INBOX:42:3", started_by=anna.id,
        context={"mail": {"account_id": kid, "folder": "INBOX", "uid": 42},
                  "attachment": {"index": 3, "filename": "rechnung.pdf"}})
    db.add(lauf)
    await db.flush()

    out = await run_action(db, lauf, _knoten({
        "filename": "rechnung.pdf", "doc_id": "3464",
        "doc_url": "https://paperless.example/documents/3464/"}))
    assert out["noted"] is True

    doc = (await db.execute(select(MailDocument))).scalars().one()
    assert (doc.uid, doc.attachment, doc.doc_id) == (42, 3, "3464")

    # And the message knows it without anybody having to ask.
    async def fake_message(account, folder, uid):
        return {"subject": "x", "from": [], "attachments": [
            {"index": 3, "filename": "rechnung.pdf", "content_type": "application/pdf",
             "size": 1}]}
    monkeypatch.setattr(mailbox, "message", fake_message)
    r = (await client.get(f"/mailbox/accounts/{kid}/messages/42?folder=INBOX",
                          headers=auth(anna))).json()
    assert r["documents"][0]["doc_url"].endswith("/3464/")


async def test_a_second_report_about_the_same_file_is_a_repetition(db, client):
    """The archive may call twice. The first answer is the one that counts, otherwise a
    tidied up document number would be overwritten by an older one."""
    from app.models.mail import MailDocument
    from app.models.workflow import WorkflowInstance
    from app.services.workflow_actions import run_action

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_account())).json()["id"]
    def_id, ver_id = await _leerer_ablauf(db, anna)
    lauf = WorkflowInstance(
        definition_id=def_id, version_id=ver_id, subject_kind=WorkflowSubjectKind.standalone,
        source="mail:privat", source_ref="INBOX:42:3", started_by=anna.id,
        context={"mail": {"account_id": kid, "folder": "INBOX", "uid": 42},
                  "attachment": {"index": 3, "filename": "rechnung.pdf"}})
    db.add(lauf)
    await db.flush()

    for nummer in ("3464", "9999"):
        await run_action(db, lauf, _knoten({
            "filename": "rechnung.pdf", "doc_id": nummer,
            "doc_url": f"https://paperless.example/documents/{nummer}/"}))

    rows = (await db.execute(select(MailDocument))).scalars().all()
    assert [d.doc_id for d in rows] == ["3464"]


async def test_a_report_about_an_unknown_file_connects_nothing(db, client):
    """Better an entry that is missing than one on the wrong mail: a document number on the
    wrong attachment is a wrong link nobody checks."""
    from app.models.mail import MailDocument
    from app.models.workflow import WorkflowInstance
    from app.services.workflow_actions import run_action

    anna = await make_user(db, "anna")
    def_id, ver_id = await _leerer_ablauf(db, anna)
    lauf = WorkflowInstance(
        definition_id=def_id, version_id=ver_id, subject_kind=WorkflowSubjectKind.standalone,
        source="mail:privat", source_ref="INBOX:1", started_by=anna.id, context={})
    db.add(lauf)
    await db.flush()

    out = await run_action(db, lauf, _knoten({"filename": "gibtsnicht.pdf", "doc_id": "1"}))
    assert out["noted"] is False
    assert (await db.execute(select(MailDocument))).scalars().all() == []
