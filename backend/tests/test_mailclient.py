"""Der Mail-Client: Konten, Identitäten und Aktionen.

Was hier NICHT geprüft wird, ist IMAP selbst — dafür bräuchte es einen echten Server, und
`imapclient` ist nicht die Stelle, an der unsere Fehler entstehen. Geprüft wird, was uns
gehört: dass ein Kennwort nie zurückkommt, dass es beim Speichern anderer Felder nicht
verlorengeht, dass es nur eine Vorgabe-Identität gibt und dass eine Mail-Aktion den Ablauf
mit allem startet, was er über die Mail wissen muss.
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

    # Und in der Datenbank steht es nicht im Klartext.
    account = (await db.execute(select(MailAccount))).scalars().one()
    assert account.imap_password_enc and account.imap_password_enc != "geheim"


async def test_an_empty_password_means_unchanged(db, client):
    """Der häufigste Bedienfehler wäre sonst: Ordnernamen ändern und das Konto entsperren."""
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

    # Die Oberfläche fragt, welche Abläufe an eine Mail passen.
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
    """Ein Anhang, den es nicht gibt, ist ein Fehler und kein leerer Ablauf."""
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
    """Drei Dinge auf einmal: kein Skript, kein Formular, kein stiller Bildabruf."""
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
    # Das Fernbild bleibt sichtbar, lädt aber nichts nach.
    assert 'data-fern="https://tracker.example/pixel.gif"' in clean
    assert fern is True
    # Ein eingebettetes Bild ist kein Fernbild und bleibt, wie es ist.
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
    """Der Fehler, den man sieht: `Archives` steht alphabetisch vor `INBOX.Aliexpress`, also
    rutschten die Unterordner des Posteingangs unter das Archiv — eingerückt, was den
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
    """Manche Server listen `Archives/2024` ohne `Archives`. Eingerückt ins Leere zu zeigen
    wäre schlimmer als eine Ebene weniger."""
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
    """Der eigentliche Zweck: eine Rechnung von 2023 gehört auch 2026 noch ins Jahr 2023."""
    import datetime as dt

    from app.services.mailbox import archive_target

    old = dt.datetime(2023, 3, 7, 9, 0, tzinfo=dt.timezone.utc)
    assert archive_target(_account_pattern(), old) == "Archive/2023"
    assert archive_target(_account_pattern("Archive/{jahr}/{monat}"), old) == "Archive/2023/03"
    assert archive_target(_account_pattern("Archiv/{jahr}-{quartal}"), old) == "Archiv/2023-Q1"


async def test_the_separator_of_the_server_is_used():
    """Dasselbe Muster auf Courier (Punkt) und Dovecot (Schrägstrich) — der Mensch soll
    nicht wissen müssen, wie sein Server Ordner schachtelt."""
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
    """`{jhar}` ist ein Tippfehler. Er soll auffallen — aber keinen Ordner mit geschweiften
    Klammern im Namen erzeugen, den man hinterher von Hand wegräumt."""
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


# ── Büroklammer in der Liste ────────────────────────────────────────────────

async def test_an_attachment_is_recognised_without_loading_the_mail():
    """Die Büroklammer entsteht aus der BODYSTRUCTURE — eine Liste von fünfzig Nachrichten
    soll keine fünfzig Anhänge durchs Netz ziehen."""
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
    """Sonst trüge jede Werbemail eine Büroklammer, und die wäre keine Auskunft mehr."""
    from app.services.mailbox import _has_attachment

    inline = ((b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", 40, 2),
              (b"image", b"png", (b"name", b"logo.png"), b"<logo>", None, b"base64", 500,
               (b"inline", (b"filename", b"logo.png"))), b"related")
    assert _has_attachment(inline) is False


# ── Postfächer als MCP ──────────────────────────────────────────────────────

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
    """Voreinstellung ist nichts. Was im Verzeichnis steht, hat jemand eingeschaltet."""
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
    """Ein ausgeblendeter Ordner darf auch kein Ziel sein — sonst könnte man Post hinter den
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
    """Hausregeln gehören dorthin, wo ein Agent das Postfach kennenlernt — nicht in eine
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
    """Ein leerer Hinweis ist schlechter als keiner: er sieht aus wie eine Regel."""
    from app.services.mail_mcp import instructions, execute

    anna = await make_user(db, "anna")
    await _mcp_account(db, anna)
    assert await instructions(db, anna) == ""
    assert "instructions" not in (await execute(db, anna, "mail_accounts", {}))[0]


# ── Handgriffe am ganzen Ordner ─────────────────────────────────────────────

async def test_special_folders_are_protected_from_deletion(db, client):
    """Wer seinen Papierkorb löscht, hat danach ein Löschen, das nicht mehr funktioniert."""
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


# ── Was ein Ablauf mit einer Mail tun kann ───────────────────────────────────

async def test_a_flow_marks_a_mail_as_read(db, monkeypatch):
    """Der häufigste Handgriff überhaupt — und bis eben der einzige, den ein Ablauf nicht
    tun konnte: Wer eine Mail einsortiert hat, will sie danach als gelesen wissen."""
    from app.services.workflow_actions import _mail_flag

    anna = await make_user(db, "anna")
    account = MailAccount(owner_user_id=anna.id, name="privat", imap_host="imap.example.org")
    db.add(account)
    await db.commit()

    gesetzt = []

    async def flag(k, folder, uid, kind, an):
        gesetzt.append((k.id, folder, uid, kind, an))

    monkeypatch.setattr(mailbox, "flag", flag)
    inst = type("Inst", (), {"context": {}, "started_by": anna.id,
                             "definition_id": 1, "id": 1})()
    result = await _mail_flag(db, inst, {}, {"mail": {"account_id": account.id,
                                                        "folder": "INBOX", "uid": 7}})

    assert result["set"] is True and result["flag"] == "seen" and result["on"] is True
    assert gesetzt == [(account.id, "INBOX", 7, "seen", True)]


async def test_without_a_mail_in_the_context_the_node_says_why(db):
    """Ein Ablauf, der von einem Job kommt, hat keine Mail — das ist kein Absturz."""
    from app.services.workflow_actions import _mail_flag

    inst = type("Inst", (), {"context": {}, "started_by": None, "definition_id": 1, "id": 1})()
    result = await _mail_flag(db, inst, {}, {})
    assert result["set"] is False and "keine Mail" in result["reason"]


async def test_a_move_without_a_target_goes_to_the_archive(db, monkeypatch):
    """So kann ein Ablauf „erledigt, weg damit" sagen, ohne den Ordnernamen zu kennen."""
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
