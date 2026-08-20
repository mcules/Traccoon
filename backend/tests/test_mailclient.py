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


def _konto(**over) -> dict:
    werte = {"name": "privat", "imap_host": "imap.example.org", "imap_user": "ich",
             "imap_password": "geheim", "smtp_host": "smtp.example.org",
             "smtp_user": "ich", "smtp_password": "auch geheim"}
    werte.update(over)
    return werte


async def test_kennwort_kommt_nie_zurueck(db, client):
    anna = await make_user(db, "anna")
    r = await client.post("/mailbox/accounts", headers=auth(anna), json=_konto())
    assert r.status_code == 201, r.text
    daten = r.json()
    assert daten["imap_password_set"] is True and daten["smtp_password_set"] is True
    assert "geheim" not in r.text, "das Kennwort darf die Oberfläche nie erreichen"

    # Und in der Datenbank steht es nicht im Klartext.
    konto = (await db.execute(select(MailAccount))).scalars().one()
    assert konto.imap_password_enc and konto.imap_password_enc != "geheim"


async def test_leeres_kennwort_heisst_unveraendert(db, client):
    """Der häufigste Bedienfehler wäre sonst: Ordnernamen ändern und das Konto entsperren."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]
    vorher = (await db.execute(select(MailAccount))).scalars().one().imap_password_enc

    r = await client.put(f"/mailbox/accounts/{kid}", headers=auth(anna),
                         json=_konto(imap_password="", smtp_password="", folder_sent="Gesendet"))
    assert r.status_code == 200
    db.expire_all()
    konto = (await db.execute(select(MailAccount))).scalars().one()
    assert konto.folder_sent == "Gesendet"
    assert konto.imap_password_enc == vorher, "unverändert, nicht gelöscht"


async def test_fremde_konten_bleiben_fremd(db, client):
    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]

    assert (await client.get(f"/mailbox/accounts/{kid}/identities",
                             headers=auth(bert))).status_code == 404
    assert (await client.get("/mailbox/accounts", headers=auth(bert))).json() == []


async def test_nur_eine_vorgabe_identitaet(db, client):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]
    for adresse in ("ich@example.org", "vorstand@example.org"):
        r = await client.post(f"/mailbox/accounts/{kid}/identities", headers=auth(anna),
                              json={"email": adresse, "is_default": True})
        assert r.status_code == 201

    db.expire_all()
    identitaeten = (await db.execute(select(MailIdentity))).scalars().all()
    vorgaben = [i for i in identitaeten if i.is_default]
    assert len(identitaeten) == 2 and len(vorgaben) == 1
    assert vorgaben[0].email == "vorstand@example.org", "die zuletzt gesetzte gilt"


async def test_aktion_startet_ablauf_mit_der_mail_im_kontext(db, client, monkeypatch):
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]

    async def fake_nachricht(konto, ordner, uid):
        return {"subject": "Rechnung 2026-08", "from": [{"addr": "shop@example.org"}],
                "date": "2026-08-19", "message_id": "<abc@example.org>", "text": "Anbei.",
                "attachments": [{"index": 3, "filename": "rechnung.pdf",
                                 "content_type": "application/pdf", "size": 1234}]}
    monkeypatch.setattr(mailbox, "nachricht", fake_nachricht)

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
    angebot = (await client.get("/mailbox/actions", headers=auth(anna))).json()
    assert [a["key"] for a in angebot] == ["anhang-paperless"]
    assert angebot[0]["scope"] == "attachment"

    r = await client.post(f"/mailbox/accounts/{kid}/messages/42/action", headers=auth(anna),
                          json={"definition_id": d.id, "folder": "INBOX", "attachment": 3})
    assert r.status_code == 200, r.text

    inst = (await db.execute(select(WorkflowInstance))).scalars().one()
    assert inst.context["mail"]["uid"] == 42
    assert inst.context["mail"]["subject"] == "Rechnung 2026-08"
    assert inst.context["attachment"]["filename"] == "rechnung.pdf"
    assert inst.source_ref == "INBOX:42:3", "zweimal dieselbe Aktion ist derselbe Vorgang"


async def test_aktion_kennt_den_anhang_nicht(db, client, monkeypatch):
    """Ein Anhang, den es nicht gibt, ist ein Fehler und kein leerer Ablauf."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]

    async def fake_nachricht(konto, ordner, uid):
        return {"subject": "ohne", "from": [], "attachments": []}
    monkeypatch.setattr(mailbox, "nachricht", fake_nachricht)

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

async def test_html_wird_gesaeubert_und_fernbilder_bleiben_stehen():
    """Drei Dinge auf einmal: kein Skript, kein Formular, kein stiller Bildabruf."""
    from app.services.mailbox import saeubern

    sauber, fern = saeubern(
        '<p onclick="alert(1)">Hallo<script>alert(2)</script>'
        '<img src="https://tracker.example/pixel.gif">'
        '<img src="data:image/png;base64,AAA">'
        '<form action="https://phish"><input name="pw"></form>'
        '<a href="javascript:evil()">klick</a></p>')

    assert "script" not in sauber and "onclick" not in sauber
    assert "<form" not in sauber and "<input" not in sauber
    assert "javascript:" not in sauber
    # Das Fernbild bleibt sichtbar, lädt aber nichts nach.
    assert 'data-fern="https://tracker.example/pixel.gif"' in sauber
    assert fern is True
    # Ein eingebettetes Bild ist kein Fernbild und bleibt, wie es ist.
    assert 'src="data:image/png;base64,AAA"' in sauber


async def test_ohne_fernbilder_keine_warnung():
    from app.services.mailbox import saeubern

    sauber, fern = saeubern('<p>Nur <b>Text</b> und <img src="data:image/gif;base64,AA">.</p>')
    assert fern is False and "<b>Text</b>" in sauber


# ── Ordnerbaum ──────────────────────────────────────────────────────────────

def _o(name: str, parent: str = "", special: str = "", trenner: str = ".") -> dict:
    return {"name": name, "display": name.split(trenner)[-1], "parent": parent,
            "delimiter": trenner, "special": special, "level": 0, "unseen": 0, "total": 0}


async def test_unterordner_stehen_unter_ihrem_elternteil():
    """Der Fehler, den man sieht: `Archives` steht alphabetisch vor `INBOX.Aliexpress`, also
    rutschten die Unterordner des Posteingangs unter das Archiv — eingerückt, was den
    falschen Eindruck perfekt machte."""
    from app.services.mailbox import baum_sortieren

    baum = baum_sortieren([
        _o("Archives"),
        _o("Archives.2024", "Archives"),
        _o("INBOX", special="inbox"),
        _o("INBOX.Aliexpress", "INBOX"),
        _o("INBOX.Bewerbung", "INBOX"),
        _o("Sent", special="sent"),
    ])
    reihenfolge = [(e["name"], e["level"]) for e in baum]

    assert reihenfolge == [
        ("INBOX", 0),
        ("INBOX.Aliexpress", 1),
        ("INBOX.Bewerbung", 1),
        ("Sent", 0),
        ("Archives", 0),
        ("Archives.2024", 1),
    ], "Sonderordner zuerst, Kinder direkt unter ihrem Elternteil"


async def test_ordner_ohne_elternteil_ist_eine_wurzel():
    """Manche Server listen `Archives/2024` ohne `Archives`. Eingerückt ins Leere zu zeigen
    wäre schlimmer als eine Ebene weniger."""
    from app.services.mailbox import baum_sortieren

    baum = baum_sortieren([_o("INBOX", special="inbox"),
                           _o("Archives.2024", "Archives")])
    assert [(e["name"], e["level"]) for e in baum] == [("INBOX", 0), ("Archives.2024", 0)]


async def test_sonderordner_stehen_in_gewohnter_reihenfolge():
    from app.services.mailbox import baum_sortieren

    baum = baum_sortieren([
        _o("Trash", special="trash"), _o("Zeug"), _o("Drafts", special="drafts"),
        _o("INBOX", special="inbox"), _o("Sent", special="sent"), _o("Abrechnung"),
    ])
    assert [e["name"] for e in baum] == [
        "INBOX", "Drafts", "Sent", "Trash", "Abrechnung", "Zeug"]


# ── Archiv nach Muster ──────────────────────────────────────────────────────

def _konto_muster(muster: str = "Archive/{jahr}") -> MailAccount:
    return MailAccount(name="p", archive_mode="pattern", archive_pattern=muster,
                       folder_archive="Archive")


async def test_muster_nimmt_das_datum_der_mail_nicht_von_heute():
    """Der eigentliche Zweck: eine Rechnung von 2023 gehört auch 2026 noch ins Jahr 2023."""
    import datetime as dt

    from app.services.mailbox import archiv_ziel

    alt = dt.datetime(2023, 3, 7, 9, 0, tzinfo=dt.timezone.utc)
    assert archiv_ziel(_konto_muster(), alt) == "Archive/2023"
    assert archiv_ziel(_konto_muster("Archive/{jahr}/{monat}"), alt) == "Archive/2023/03"
    assert archiv_ziel(_konto_muster("Archiv/{jahr}-{quartal}"), alt) == "Archiv/2023-Q1"


async def test_trenner_des_servers_wird_eingesetzt():
    """Dasselbe Muster auf Courier (Punkt) und Dovecot (Schrägstrich) — der Mensch soll
    nicht wissen müssen, wie sein Server Ordner schachtelt."""
    import datetime as dt

    from app.services.mailbox import archiv_ziel

    wann = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)
    assert archiv_ziel(_konto_muster("Archive/{jahr}"), wann, trenner=".") == "Archive.2026"
    assert archiv_ziel(_konto_muster("Archive/{jahr}"), wann, trenner="/") == "Archive/2026"


async def test_absender_im_muster():
    import datetime as dt

    from app.services.mailbox import archiv_ziel

    ziel = archiv_ziel(_konto_muster("Archive/{absender_domain}/{jahr}"),
                       dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc), "support@haendler.example")
    assert ziel == "Archive/haendler.example/2026"


async def test_tippfehler_im_muster_legt_keinen_klammer_ordner_an():
    """`{jhar}` ist ein Tippfehler. Er soll auffallen — aber keinen Ordner mit geschweiften
    Klammern im Namen erzeugen, den man hinterher von Hand wegräumt."""
    import datetime as dt

    from app.services.mailbox import archiv_ziel

    ziel = archiv_ziel(_konto_muster("Archive/{jhar}"),
                       dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))
    assert "{" not in ziel and ziel == "Archive"


async def test_fester_ordner_bleibt_fester_ordner():
    import datetime as dt

    from app.services.mailbox import archiv_ziel

    konto = MailAccount(name="p", archive_mode="folder", folder_archive="Archiv",
                        archive_pattern="Archive/{jahr}")
    assert archiv_ziel(konto, dt.datetime(2023, 5, 5, tzinfo=dt.timezone.utc)) == "Archiv"


# ── Büroklammer in der Liste ────────────────────────────────────────────────

async def test_anhang_wird_ohne_laden_der_mail_erkannt():
    """Die Büroklammer entsteht aus der BODYSTRUCTURE — eine Liste von fünfzig Nachrichten
    soll keine fünfzig Anhänge durchs Netz ziehen."""
    from app.services.mailbox import _hat_anhang

    mit = ((b"text", b"plain", (b"charset", b"utf-8"), None, None, b"7bit", 12, 1),
           (b"application", b"pdf", (b"name", b"r.pdf"), None, None, b"base64", 9000,
            (b"attachment", (b"filename", b"r.pdf"))), b"mixed")
    ohne = ((b"text", b"plain", (b"charset", b"utf-8"), None, None, b"7bit", 12, 1),
            (b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", 40, 2),
            b"alternative")

    assert _hat_anhang(mit) is True
    assert _hat_anhang(ohne) is False
    assert _hat_anhang(None) is False


async def test_eingebettetes_logo_ist_kein_anhang():
    """Sonst trüge jede Werbemail eine Büroklammer, und die wäre keine Auskunft mehr."""
    from app.services.mailbox import _hat_anhang

    inline = ((b"text", b"html", (b"charset", b"utf-8"), None, None, b"7bit", 40, 2),
              (b"image", b"png", (b"name", b"logo.png"), b"<logo>", None, b"base64", 500,
               (b"inline", (b"filename", b"logo.png"))), b"related")
    assert _hat_anhang(inline) is False


# ── Postfächer als MCP ──────────────────────────────────────────────────────

async def _mcp_konto(db, user, **over) -> MailAccount:
    werte = {"name": "privat", "owner_user_id": user.id, "enabled": True,
             "mcp_enabled": True, "mcp_tools": ["mail_folders", "mail_search"],
             "mcp_ignore_folders": ["Junk", "Privat*"]}
    werte.update(over)
    k = MailAccount(**werte)
    db.add(k)
    await db.commit()
    return k


async def test_nur_freigegebene_werkzeuge_stehen_im_verzeichnis(db):
    """Voreinstellung ist nichts. Was im Verzeichnis steht, hat jemand eingeschaltet."""
    from app.services.mail_mcp import werkzeugliste

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna)

    namen = {w["name"] for w in await werkzeugliste(db, anna)}
    assert namen == {"mail_accounts", "mail_folders", "mail_search"}
    assert "mail_send" not in namen


async def test_abgeschaltetes_postfach_gibt_es_nicht(db):
    from app.services.mail_mcp import ausfuehren, werkzeugliste

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna, mcp_enabled=False)

    assert [w["name"] for w in await werkzeugliste(db, anna)] == ["mail_accounts"]
    with pytest.raises(LookupError):
        await ausfuehren(db, anna, "mail_folders", {"account": "privat"})


async def test_gesperrtes_werkzeug_wird_abgelehnt(db):
    from app.services.mail_mcp import ausfuehren

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna)
    with pytest.raises(PermissionError):
        await ausfuehren(db, anna, "mail_get", {"account": "privat", "uid": 1})


async def test_fremde_postfaecher_bleiben_unsichtbar(db):
    from app.services.mail_mcp import ausfuehren

    anna = await make_user(db, "anna")
    bert = await make_user(db, "bert")
    await _mcp_konto(db, anna)
    with pytest.raises(LookupError):
        await ausfuehren(db, bert, "mail_folders", {"account": "privat"})


async def test_ignorierte_ordner_sind_kein_sichtschutz_sondern_eine_sperre(db, monkeypatch):
    """Ein ausgeblendeter Ordner darf auch kein Ziel sein — sonst könnte man Post hinter den
    Sichtschutz schieben."""
    from app.services import mail_mcp

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna, mcp_tools=["mail_folders", "mail_move"])

    async def fake_ordner(konto, zaehlen=False):
        return [{"name": "INBOX"}, {"name": "Junk"}, {"name": "Privat.Familie"}]
    monkeypatch.setattr(mail_mcp.mailbox, "ordner", fake_ordner)

    sichtbar = await mail_mcp.ausfuehren(db, anna, "mail_folders", {"account": "privat"})
    assert [o["name"] for o in sichtbar] == ["INBOX"]

    with pytest.raises(PermissionError):
        await mail_mcp.ausfuehren(db, anna, "mail_move",
                                  {"account": "privat", "uid": 1, "target": "Privat.Familie"})
    with pytest.raises(PermissionError):
        await mail_mcp.ausfuehren(db, anna, "mail_move",
                                  {"account": "privat", "folder": "Junk", "uid": 1,
                                   "target": "INBOX"})


async def test_muster_der_ignorierliste():
    from app.services.mail_mcp import ignoriert

    assert ignoriert("Junk", ["Junk"]) is True
    assert ignoriert("Privat.Familie", ["Privat*"]) is True
    assert ignoriert("INBOX", ["Privat*", "Junk"]) is False


async def test_anweisungen_stehen_beim_verbinden_und_am_konto(db):
    """Hausregeln gehören dorthin, wo ein Agent das Postfach kennenlernt — nicht in eine
    Datei, die er vielleicht liest."""
    from app.services.mail_mcp import anweisungen, ausfuehren

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna, name="vorstand",
                     mcp_instructions="Sachlich und in Sie-Form. Nichts ohne Rückfrage senden.")

    text = await anweisungen(db, anna)
    assert "vorstand" in text and "Sie-Form" in text

    konten = await ausfuehren(db, anna, "mail_accounts", {})
    assert konten[0]["instructions"].startswith("Sachlich")


async def test_ohne_anweisung_kein_leeres_feld(db):
    """Ein leerer Hinweis ist schlechter als keiner: er sieht aus wie eine Regel."""
    from app.services.mail_mcp import anweisungen, ausfuehren

    anna = await make_user(db, "anna")
    await _mcp_konto(db, anna)
    assert await anweisungen(db, anna) == ""
    assert "instructions" not in (await ausfuehren(db, anna, "mail_accounts", {}))[0]


# ── Handgriffe am ganzen Ordner ─────────────────────────────────────────────

async def test_sonderordner_sind_vor_dem_loeschen_geschuetzt(db, client):
    """Wer seinen Papierkorb löscht, hat danach ein Löschen, das nicht mehr funktioniert."""
    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto(folder_trash="Trash"))).json()["id"]

    for ordner in ("INBOX", "Trash"):
        r = await client.post(f"/mailbox/accounts/{kid}/folders/delete", headers=auth(anna),
                              json={"folder": ordner})
        assert r.status_code == 400, f"{ordner} muss geschützt sein"


async def test_gewoehnlicher_ordner_darf_geloescht_werden(db, client, monkeypatch):
    from app.services import mailbox as mb

    anna = await make_user(db, "anna")
    kid = (await client.post("/mailbox/accounts", headers=auth(anna),
                             json=_konto())).json()["id"]

    geloescht = []

    async def fake_loeschen(konto, ordner):
        geloescht.append(ordner)
    monkeypatch.setattr(mb, "ordner_loeschen", fake_loeschen)

    r = await client.post(f"/mailbox/accounts/{kid}/folders/delete", headers=auth(anna),
                          json={"folder": "Alte Newsletter"})
    assert r.status_code == 204 and geloescht == ["Alte Newsletter"]


# ── Was ein Ablauf mit einer Mail tun kann ───────────────────────────────────

async def test_ablauf_markiert_eine_mail_als_gelesen(db, monkeypatch):
    """Der häufigste Handgriff überhaupt — und bis eben der einzige, den ein Ablauf nicht
    tun konnte: Wer eine Mail einsortiert hat, will sie danach als gelesen wissen."""
    from app.services.workflow_actions import _mail_flag

    anna = await make_user(db, "anna")
    konto = MailAccount(owner_user_id=anna.id, name="privat", imap_host="imap.example.org")
    db.add(konto)
    await db.commit()

    gesetzt = []

    async def flag(k, ordner, uid, art, an):
        gesetzt.append((k.id, ordner, uid, art, an))

    monkeypatch.setattr(mailbox, "flag", flag)
    inst = type("Inst", (), {"context": {}, "started_by": anna.id,
                             "definition_id": 1, "id": 1})()
    ergebnis = await _mail_flag(db, inst, {}, {"mail": {"account_id": konto.id,
                                                        "folder": "INBOX", "uid": 7}})

    assert ergebnis["set"] is True and ergebnis["flag"] == "seen" and ergebnis["on"] is True
    assert gesetzt == [(konto.id, "INBOX", 7, "seen", True)]


async def test_ohne_mail_im_kontext_sagt_der_knoten_warum(db):
    """Ein Ablauf, der von einem Job kommt, hat keine Mail — das ist kein Absturz."""
    from app.services.workflow_actions import _mail_flag

    inst = type("Inst", (), {"context": {}, "started_by": None, "definition_id": 1, "id": 1})()
    ergebnis = await _mail_flag(db, inst, {}, {})
    assert ergebnis["set"] is False and "keine Mail" in ergebnis["reason"]


async def test_verschieben_ohne_ziel_geht_ins_archiv(db, monkeypatch):
    """So kann ein Ablauf „erledigt, weg damit" sagen, ohne den Ordnernamen zu kennen."""
    from app.services.workflow_actions import _mail_move

    anna = await make_user(db, "anna")
    konto = MailAccount(owner_user_id=anna.id, name="privat", imap_host="imap.example.org",
                        folder_archive="Archive")
    db.add(konto)
    await db.commit()

    async def archivieren(k, ordner, uid):
        return "Archive/2026"

    monkeypatch.setattr(mailbox, "archivieren", archivieren)
    inst = type("Inst", (), {"context": {}, "started_by": anna.id,
                             "definition_id": 1, "id": 1})()
    ergebnis = await _mail_move(db, inst, {}, {"mail": {"account_id": konto.id,
                                                        "folder": "INBOX", "uid": 9}})
    assert ergebnis == {"action": "mail_move", "moved": True,
                        "target": "Archive/2026", "uid": 9}
