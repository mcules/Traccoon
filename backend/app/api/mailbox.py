"""Der Mail-Client: Konten, Identitäten, Postfach, Senden — und Aktionen als Abläufe.

Getrennt von `api/mail.py`: dort liegt der Assistenten-Eingang (was der Assistent aus einer
Mail macht), hier das Postfach selbst (was ein Mensch damit macht).

Aktionen sind bewusst kein fester Katalog. Ein Knopf an einer Mail oder an einem Anhang
startet einen Ablauf und legt Konto, Ordner, UID und — falls gewählt — den Anhang in dessen
Kontext. „Anhang nach Paperless" ist damit ein Ablauf mit einem Werkzeugaufruf, und die
nächste Funktion entsteht im Editor statt in einem Entwicklungslauf.
"""
import base64

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..core.security import encrypt_secret
from ..db import get_session
from ..models.mail import MailAccount, MailIdentity
from ..models.user import User
from ..services import mailbox
from ..services import mailbox_cache as cache
from .deps import get_current_user

router = APIRouter(prefix="/mailbox", tags=["mailbox"])


# ── Konten ──────────────────────────────────────────────────────────────────

class KontoIn(BaseModel):
    name: str
    enabled: bool = True
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str = ""
    imap_password: str = ""          # leer = unverändert lassen
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""          # leer = unverändert lassen
    folder_sent: str = "Sent"
    folder_drafts: str = "Drafts"
    folder_trash: str = "Trash"
    folder_junk: str = "Junk"
    folder_archive: str = "Archive"
    archive_mode: str = "folder"          # folder | pattern
    archive_pattern: str = "Archive/{jahr}"
    mcp_enabled: bool = False
    mcp_ignore_folders: list[str] = []
    mcp_tools: list[str] = []
    mcp_instructions: str = ""


class KontoOut(BaseModel):
    id: int; name: str; enabled: bool
    imap_host: str; imap_port: int; imap_ssl: bool; imap_user: str
    smtp_host: str; smtp_port: int; smtp_security: str; smtp_user: str
    folder_sent: str; folder_drafts: str; folder_trash: str; folder_junk: str
    folder_archive: str; archive_mode: str; archive_pattern: str
    mcp_enabled: bool; mcp_ignore_folders: list[str]; mcp_tools: list[str]
    mcp_instructions: str
    auth_type: str
    # Das Kennwort kommt nie zurück; die Oberfläche muss nur wissen, ob eines hinterlegt ist.
    imap_password_set: bool = False
    smtp_password_set: bool = False


def _konto_out(a: MailAccount) -> KontoOut:
    return KontoOut(
        id=a.id, name=a.name, enabled=a.enabled,
        imap_host=a.imap_host, imap_port=a.imap_port, imap_ssl=a.imap_ssl,
        imap_user=a.imap_user, smtp_host=a.smtp_host, smtp_port=a.smtp_port,
        smtp_security=a.smtp_security, smtp_user=a.smtp_user,
        folder_sent=a.folder_sent, folder_drafts=a.folder_drafts,
        folder_trash=a.folder_trash, folder_junk=a.folder_junk,
        folder_archive=a.folder_archive, archive_mode=a.archive_mode,
        archive_pattern=a.archive_pattern, mcp_enabled=a.mcp_enabled,
        mcp_ignore_folders=list(a.mcp_ignore_folders or []),
        mcp_tools=list(a.mcp_tools or []), mcp_instructions=a.mcp_instructions,
        auth_type=a.auth_type,
        imap_password_set=bool(a.imap_password_enc), smtp_password_set=bool(a.smtp_password_enc))


async def _konto(db: AsyncSession, kid: int, user: User) -> MailAccount:
    a = await db.get(MailAccount, kid)
    if a is None or a.owner_user_id != user.id:
        raise Fehler(404, "err.mail_account_not_found", "Mail account not found")
    return a


@router.get("/accounts", response_model=list[KontoOut])
async def konten(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(MailAccount)
                             .where(MailAccount.owner_user_id == user.id)
                             .order_by(MailAccount.name))).scalars().all()
    return [_konto_out(a) for a in rows]


@router.post("/accounts", response_model=KontoOut, status_code=201)
async def konto_anlegen(data: KontoIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    werte = data.model_dump()
    imap_pw, smtp_pw = werte.pop("imap_password"), werte.pop("smtp_password")
    a = MailAccount(**werte, owner_user_id=user.id,
                    imap_password_enc=encrypt_secret(imap_pw) if imap_pw else "",
                    smtp_password_enc=encrypt_secret(smtp_pw) if smtp_pw else "")
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _konto_out(a)


@router.put("/accounts/{kid}", response_model=KontoOut)
async def konto_aendern(kid: int, data: KontoIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    a = await _konto(db, kid, user)
    werte = data.model_dump()
    imap_pw, smtp_pw = werte.pop("imap_password"), werte.pop("smtp_password")
    for feld, wert in werte.items():
        setattr(a, feld, wert)
    # Ein leeres Feld heißt „unverändert": die Oberfläche bekommt das Kennwort nie zu sehen
    # und könnte es sonst beim Speichern der Ordnernamen versehentlich löschen.
    if imap_pw:
        a.imap_password_enc = encrypt_secret(imap_pw)
    if smtp_pw:
        a.smtp_password_enc = encrypt_secret(smtp_pw)
    await db.commit()
    await db.refresh(a)
    # Geänderte Zugangsdaten oder Ordnernamen: Was noch offen liegt, gehört zum alten Stand.
    mailbox.pool_leeren(a.id)
    await cache.entwerten(a.id)
    return _konto_out(a)


@router.delete("/accounts/{kid}", status_code=204)
async def konto_loeschen(kid: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    a = await _konto(db, kid, user)
    await db.delete(a)
    await db.commit()


@router.get("/unread")
async def ungelesen(user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """Ungelesenes je Postfach — damit man sieht, wo etwas liegt, ohne hineinzugehen.

    Ein Postfach, das gerade nicht erreichbar ist, liefert `null` statt einer Null: „keine
    neue Post" und „ich weiß es nicht" sind zwei verschiedene Auskünfte.
    """
    import asyncio

    rows = (await db.execute(select(MailAccount).where(
        MailAccount.owner_user_id == user.id,
        MailAccount.enabled.is_(True)).order_by(MailAccount.name))).scalars().all()

    async def einer(konto: MailAccount) -> dict:
        try:
            return {"account_id": konto.id, "name": konto.name,
                    "unseen": await cache.gecacht(konto.id, "unread", cache.TTL_UNGELESEN,
                                                  lambda: mailbox.ungelesen(konto))}
        except Exception:  # noqa: BLE001 — ein stiller Server darf die Übersicht nicht sprengen
            return {"account_id": konto.id, "name": konto.name, "unseen": None}

    # Nebeneinander statt nacheinander: bei drei Postfächern ist das der Unterschied
    # zwischen einer und drei Wartezeiten.
    ergebnis = await asyncio.gather(*(einer(k) for k in rows))
    return {"accounts": list(ergebnis),
            "total": sum(e["unseen"] or 0 for e in ergebnis)}


@router.get("/mcp-tools")
async def werkzeuge(_: User = Depends(get_current_user)):
    """Der Katalog: was ein Postfach an Agenten freigeben KANN, mit Art der Berechtigung."""
    from ..services.mail_mcp import WERKZEUGE

    return [{"name": w["name"], "kind": w["art"], "description": w["beschreibung"],
             "always": bool(w.get("immer"))} for w in WERKZEUGE]


@router.post("/accounts/{kid}/last", status_code=204)
async def zuletzt_geoeffnet(kid: int, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)):
    """Merkt sich, welches Postfach zuletzt offen war — für den nächsten Besuch."""
    await _konto(db, kid, user)
    user.mail_last_account_id = kid
    await db.commit()


@router.post("/accounts/{kid}/test")
async def konto_pruefen(kid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    return await mailbox.pruefen(await _konto(db, kid, user))


# ── Identitäten ─────────────────────────────────────────────────────────────

class IdentitaetIn(BaseModel):
    display_name: str = ""
    email: str
    reply_to: str = ""
    signature: str = ""
    is_default: bool = False


class IdentitaetOut(IdentitaetIn):
    id: int
    account_id: int


@router.get("/accounts/{kid}/identities", response_model=list[IdentitaetOut])
async def identitaeten(kid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    await _konto(db, kid, user)
    rows = (await db.execute(select(MailIdentity).where(MailIdentity.account_id == kid)
                             .order_by(MailIdentity.id))).scalars().all()
    return rows


@router.post("/accounts/{kid}/identities", response_model=IdentitaetOut, status_code=201)
async def identitaet_anlegen(kid: int, data: IdentitaetIn,
                             user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    await _konto(db, kid, user)
    ident = MailIdentity(account_id=kid, **data.model_dump())
    db.add(ident)
    await _nur_eine_vorgabe(db, kid, ident)
    await db.commit()
    await db.refresh(ident)
    return ident


@router.put("/identities/{iid}", response_model=IdentitaetOut)
async def identitaet_aendern(iid: int, data: IdentitaetIn,
                             user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    ident = await db.get(MailIdentity, iid)
    if ident is None:
        raise Fehler(404, "err.identity_not_found", "Identity not found")
    await _konto(db, ident.account_id, user)
    for feld, wert in data.model_dump().items():
        setattr(ident, feld, wert)
    await _nur_eine_vorgabe(db, ident.account_id, ident)
    await db.commit()
    await db.refresh(ident)
    return ident


@router.delete("/identities/{iid}", status_code=204)
async def identitaet_loeschen(iid: int, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    ident = await db.get(MailIdentity, iid)
    if ident is None:
        return
    await _konto(db, ident.account_id, user)
    await db.delete(ident)
    await db.commit()


async def _nur_eine_vorgabe(db: AsyncSession, kid: int, ident: MailIdentity) -> None:
    """Zwei Vorgaben wären keine. Wer eine setzt, nimmt sie den anderen ab."""
    if not ident.is_default:
        return
    rows = (await db.execute(select(MailIdentity).where(
        MailIdentity.account_id == kid))).scalars().all()
    for andere in rows:
        if andere is not ident:
            andere.is_default = False


# ── Postfach ────────────────────────────────────────────────────────────────

@router.get("/accounts/{kid}/folders")
async def ordner(kid: int, counts: bool = False, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    """Der Ordnerbaum. `counts=true` fragt zusätzlich die Ungelesen-Zahlen ab — das ist ein
    Aufruf je Ordner und deshalb nichts, was bei jedem Klick mitlaufen sollte.

    Mit Zählern sind das bei 33 Ordnern rund 900 ms; deshalb kommt die Antwort aus dem Cache,
    solange sich am Konto nichts getan hat (`services/mailbox_cache`).
    """
    konto = await _konto(db, kid, user)
    return await cache.gecacht(konto.id, f"folders:{int(counts)}", cache.TTL_ORDNER,
                               lambda: mailbox.ordner(konto, counts))


class OrdnerIn(BaseModel):
    folder: str


def _pruefe_loeschbar(konto, ordner: str) -> None:
    """Ordner, die nicht gelöscht werden dürfen.

    Der Posteingang ist nicht löschbar (der Server verweigert es ohnehin), und die vier
    Sonderordner hängen an den Knöpfen der Oberfläche: Wer seinen Papierkorb löscht, hat
    danach ein Löschen, das nicht mehr funktioniert.
    """
    if ordner.upper() == "INBOX":
        raise Fehler(400, "err.inbox_not_deletable", "The inbox cannot be deleted")
    rollen = {konto.folder_sent: "sent", konto.folder_drafts: "drafts",
              konto.folder_trash: "trash", konto.folder_junk: "junk"}
    if ordner and ordner in rollen:
        raise Fehler(400, "err.folder_has_role",
                     "'{folder}' is the {role} folder of this account. Change that first.",
                     folder=ordner, role=rollen[ordner])


@router.post("/accounts/{kid}/folders/read-all")
async def alle_gelesen(kid: int, data: OrdnerIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Alles Ungelesene im Ordner auf gelesen setzen. Gibt zurück, wie viele es waren."""
    konto = await _konto(db, kid, user)
    anzahl = await mailbox.alle_gelesen(konto, data.folder)
    await cache.entwerten(konto.id)
    return {"marked": anzahl}


@router.post("/accounts/{kid}/folders/delete", status_code=204)
async def ordner_loeschen(kid: int, data: OrdnerIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Einen Ordner samt Inhalt löschen. Sonderordner sind geschützt."""
    konto = await _konto(db, kid, user)
    _pruefe_loeschbar(konto, data.folder)
    await mailbox.ordner_loeschen(konto, data.folder)
    await cache.entwerten(konto.id)


@router.get("/accounts/{kid}/messages")
async def nachrichten(kid: int, folder: str = "INBOX", q: str = "", offset: int = 0,
                      limit: int = 50, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    konto = await _konto(db, kid, user)
    grenze = max(1, min(limit, 200))
    # Die Suche bleibt ungecacht: Sie ist selten dieselbe zweimal, und ein Treffer, der
    # eigentlich schon verschoben ist, wäre in einer Suche besonders ärgerlich.
    if q:
        return await mailbox.liste(konto, folder, q, offset, grenze)
    return await cache.gecacht(konto.id, f"list:{folder}:{offset}:{grenze}", cache.TTL_LISTE,
                               lambda: mailbox.liste(konto, folder, "", offset, grenze))


@router.get("/accounts/{kid}/messages/{uid}")
async def nachricht(kid: int, uid: int, folder: str = "INBOX",
                    user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    try:
        return await mailbox.nachricht(await _konto(db, kid, user), folder, uid)
    except LookupError:
        raise Fehler(404, "err.mail_not_found", "Message not found")


@router.get("/accounts/{kid}/messages/{uid}/attachments/{index}")
async def anhang(kid: int, uid: int, index: int, folder: str = "INBOX",
                 user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    try:
        name, typ, daten = await mailbox.anhang(await _konto(db, kid, user), folder, uid, index)
    except LookupError:
        raise Fehler(404, "err.attachment_not_found", "Attachment not found")
    return Response(content=daten, media_type=typ,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class FlagIn(BaseModel):
    folder: str = "INBOX"
    flag: str = "\\Seen"
    on: bool = True


@router.post("/accounts/{kid}/messages/{uid}/flag", status_code=204)
async def flag_setzen(kid: int, uid: int, data: FlagIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    konto = await _konto(db, kid, user)
    await mailbox.flag(konto, data.folder, uid, data.flag, data.on)
    await cache.entwerten(konto.id)


class MoveIn(BaseModel):
    folder: str = "INBOX"
    target: str


@router.post("/accounts/{kid}/messages/{uid}/move", status_code=204)
async def verschieben(kid: int, uid: int, data: MoveIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    konto = await _konto(db, kid, user)
    await mailbox.verschieben(konto, data.folder, uid, data.target)
    await cache.entwerten(konto.id)


class HandgriffIn(BaseModel):
    folder: str = "INBOX"


@router.post("/accounts/{kid}/messages/{uid}/archive")
async def archivieren(kid: int, uid: int, data: HandgriffIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Ins Archiv. Wohin genau, sagt das Konto: ein fester Ordner oder ein Muster, das aus
    dem Datum DER MAIL gebildet wird (`Archive/{jahr}`). Fehlende Ordner entstehen dabei."""
    konto = await _konto(db, kid, user)
    if konto.archive_mode != "pattern" and not konto.folder_archive:
        raise Fehler(400, "err.no_archive_folder",
                     "This account has no archive folder configured")
    ziel = await mailbox.archivieren(konto, data.folder, uid)
    await cache.entwerten(konto.id)
    # Der Zielordner ist eine Auskunft wert: bei einem Muster sieht man erst hier, wo die
    # Mail wirklich gelandet ist.
    return {"folder": ziel}


class MusterIn(BaseModel):
    archive_pattern: str
    date: str = ""       # Beispieldatum (RFC-2822 oder ISO); leer = heute
    sender: str = ""


@router.post("/accounts/{kid}/archive-preview")
async def muster_vorschau(kid: int, data: MusterIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Zeigt, welcher Ordnername aus einem Muster entsteht — beim Tippen, nicht erst beim
    ersten Klick auf „Archivieren"."""
    import datetime as dt

    konto = await _konto(db, kid, user)
    probe = MailAccount(archive_mode="pattern", archive_pattern=data.archive_pattern,
                        folder_archive=konto.folder_archive)
    wann = data.date or dt.datetime.now(dt.timezone.utc)
    return {"folder": mailbox.archiv_ziel(probe, wann, data.sender or "name@example.org")}


@router.post("/accounts/{kid}/messages/{uid}/spam", status_code=204)
async def als_spam(kid: int, uid: int, data: HandgriffIn,
                   user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """In den Spam-Ordner des Kontos. Das Urteil trifft der Mensch; gelernt wird daraus in
    der Spam-Erkennung des Assistenten, nicht hier."""
    konto = await _konto(db, kid, user)
    if not konto.folder_junk:
        raise Fehler(400, "err.no_junk_folder", "This account has no junk folder configured")
    await mailbox.verschieben(konto, data.folder, uid, konto.folder_junk)
    await cache.entwerten(konto.id)


@router.post("/accounts/{kid}/messages/{uid}/delete", status_code=204)
async def loeschen(kid: int, uid: int, data: HandgriffIn,
                   user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """Löschen heißt verschieben — außer man steht schon im Papierkorb, dann heißt es weg.

    Genau so kennt man es aus jedem Mail-Programm, und es ist die einzige Fassung, bei der
    ein Fehlgriff kein Verlust ist.
    """
    konto = await _konto(db, kid, user)
    if not konto.folder_trash or data.folder == konto.folder_trash:
        await mailbox.endgueltig_loeschen(konto, data.folder, uid)
    else:
        await mailbox.verschieben(konto, data.folder, uid, konto.folder_trash)
    await cache.entwerten(konto.id)


# ── Senden und Entwürfe ─────────────────────────────────────────────────────

class AnhangIn(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    data_base64: str


class SendenIn(BaseModel):
    identity_id: int
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    subject: str = ""
    text: str = ""
    in_reply_to: str = ""
    attachments: list[AnhangIn] = []


async def _felder(db: AsyncSession, kid: int, data: SendenIn, user: User):
    konto = await _konto(db, kid, user)
    ident = await db.get(MailIdentity, data.identity_id)
    if ident is None or ident.account_id != konto.id:
        raise Fehler(400, "err.identity_not_of_account",
                     "The identity does not belong to this account")
    felder = data.model_dump(exclude={"identity_id", "attachments"})
    felder["attachments"] = [
        {"filename": a.filename, "content_type": a.content_type,
         "data": base64.b64decode(a.data_base64)} for a in data.attachments]
    return konto, ident, felder


@router.post("/accounts/{kid}/send", status_code=204)
async def senden(kid: int, data: SendenIn, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    konto, ident, felder = await _felder(db, kid, data, user)
    if not felder.get("to"):
        raise Fehler(400, "err.no_recipient", "No recipient")
    await mailbox.senden(konto, ident, felder)
    await cache.entwerten(konto.id)


@router.post("/accounts/{kid}/draft", status_code=204)
async def entwurf(kid: int, data: SendenIn, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_session)):
    konto, ident, felder = await _felder(db, kid, data, user)
    await mailbox.entwurf_speichern(konto, ident, felder)
    await cache.entwerten(konto.id)


# ── Aktionen (Abläufe) ──────────────────────────────────────────────────────

def _start_trigger(graph: dict) -> dict:
    """Die Auslöser-Angabe am Start-Knoten, roh gelesen.

    `events.trigger_of` gibt es schon, liefert aber nur Ereignis-Auslöser zurück (es prüft auf
    `event`). Ein Mail-Auslöser trägt stattdessen `kind`, und beides in einer Funktion
    zusammenzuziehen hieße, dem Ereignis-Weg eine Bedingung zu nehmen, die dort richtig ist.
    """
    for n in (graph or {}).get("nodes") or []:
        if (n.get("type") or (n.get("data") or {}).get("type")) == "start":
            cfg = (n.get("data") or {}).get("config") or n.get("config") or {}
            t = cfg.get("trigger")
            return t if isinstance(t, dict) else {}
    return {}


@router.get("/actions")
async def aktionen(user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """Eigene Abläufe, deren Start-Knoten auf Mails wartet (`trigger.kind = "mail_action"`)."""
    from ..models.workflow import WorkflowDefinition, WorkflowVersion

    rows = (await db.execute(select(WorkflowDefinition).where(
        WorkflowDefinition.enabled.is_(True),
        WorkflowDefinition.archived_at.is_(None),
        WorkflowDefinition.current_version_id.isnot(None),
        WorkflowDefinition.project_id.is_(None)))).scalars().all()
    out = []
    for d in rows:
        if d.created_by not in (None, user.id):
            continue
        version = await db.get(WorkflowVersion, d.current_version_id)
        t = _start_trigger(version.graph if version else {})
        if t.get("kind") != "mail_action":
            continue
        out.append({"definition_id": d.id, "key": d.key, "name": d.name,
                    "description": d.description,
                    # Ein Ablauf, der einen Anhang verarbeitet, gehört an den Anhang und
                    # nicht an die Mail — das sagt er selbst über seinen Auslöser.
                    "scope": t.get("scope") or "message"})
    return out


class AktionIn(BaseModel):
    definition_id: int
    folder: str = "INBOX"
    attachment: int | None = None


@router.post("/accounts/{kid}/messages/{uid}/action")
async def aktion_starten(kid: int, uid: int, data: AktionIn,
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """Startet den Ablauf mit allem, was er über die Mail wissen muss."""
    from ..models.enums import WorkflowSubjectKind
    from ..models.workflow import WorkflowDefinition
    from ..services.workflow_engine import start_workflow

    konto = await _konto(db, kid, user)
    definition = await db.get(WorkflowDefinition, data.definition_id)
    if definition is None or definition.current_version_id is None:
        raise Fehler(400, "err.workflow_definition_missing_not",
                     "The workflow definition is missing or not published")
    kopf = await mailbox.nachricht(konto, data.folder, uid)
    anhaenge = kopf.get("attachments") or []
    gewaehlt = None
    if data.attachment is not None:
        gewaehlt = next((a for a in anhaenge if a["index"] == data.attachment), None)
        if gewaehlt is None:
            raise Fehler(404, "err.attachment_not_found", "Attachment not found")
    kontext = {
        "mail": {
            "account": konto.name, "account_id": konto.id, "folder": data.folder, "uid": uid,
            "subject": kopf.get("subject", ""),
            "from": (kopf.get("from") or [{}])[0].get("addr", ""),
            "date": kopf.get("date", ""),
            "message_id": kopf.get("message_id", ""),
            "text": (kopf.get("text") or "")[:20000],
            "attachments": anhaenge,
            # Auch ein Ablauf, der den Assistenten beauftragt, soll die Hausregeln des
            # Postfachs kennen — sonst gälten sie nur über MCP.
            "instructions": konto.mcp_instructions,
        },
        "attachment": gewaehlt or {},
    }
    inst = await start_workflow(
        db, definition, subject_kind=WorkflowSubjectKind.standalone, context=kontext,
        actor_id=user.id, source=f"mail:{konto.name}",
        source_ref=f"{data.folder}:{uid}" + (f":{data.attachment}" if gewaehlt else ""))
    return {"instance_id": inst.id, "status": inst.status.value}
