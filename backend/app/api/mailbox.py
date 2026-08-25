"""The mail client: accounts, identities, mailbox, sending — and actions as flows.

Separate from `api/mail.py`: that is where the assistant intake lives (what the assistant
makes of a mail), here the mailbox itself (what a person does with it).

Actions are deliberately not a fixed catalog. A button on a mail or on an attachment starts a
flow and puts account, folder, UID and — if chosen — the attachment into its context.
"Attachment to Paperless" is thereby a flow with a tool call, and the next feature comes into
being in the editor instead of in a development run.
"""
import base64

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error import Error
from ..core.security import encrypt_secret
from ..db import get_session
from ..models.mail import MailAccount, MailIdentity, MailImageRule
from ..models.user import User
from ..services import mailbox
from ..services import mailbox_cache as cache
from .deps import get_current_user

router = APIRouter(prefix="/mailbox", tags=["mailbox"])


# ── Konten ──────────────────────────────────────────────────────────────────

class AccountIn(BaseModel):
    name: str
    enabled: bool = True
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str = ""
    imap_password: str = ""          # empty = leave unchanged
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""          # empty = leave unchanged
    folder_sent: str = "Sent"
    folder_drafts: str = "Drafts"
    folder_trash: str = "Trash"
    folder_junk: str = "Junk"
    folder_archive: str = "Archive"
    archive_mode: str = "folder"          # folder | pattern
    archive_pattern: str = "Archive/{year}"
    mcp_enabled: bool = False
    mcp_ignore_folders: list[str] = []
    mcp_tools: list[str] = []
    mcp_instructions: str = ""


class AccountOut(BaseModel):
    id: int; name: str; enabled: bool
    imap_host: str; imap_port: int; imap_ssl: bool; imap_user: str
    smtp_host: str; smtp_port: int; smtp_security: str; smtp_user: str
    folder_sent: str; folder_drafts: str; folder_trash: str; folder_junk: str
    folder_archive: str; archive_mode: str; archive_pattern: str
    mcp_enabled: bool; mcp_ignore_folders: list[str]; mcp_tools: list[str]
    mcp_instructions: str
    auth_type: str
    # The password never comes back; the UI only needs to know whether one is stored.
    imap_password_set: bool = False
    smtp_password_set: bool = False


def _account_out(a: MailAccount) -> AccountOut:
    return AccountOut(
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


async def _account(db: AsyncSession, kid: int, user: User) -> MailAccount:
    a = await db.get(MailAccount, kid)
    if a is None or a.owner_user_id != user.id:
        raise Error(404, "err.mail_account_not_found", "Mail account not found")
    return a


@router.get("/accounts", response_model=list[AccountOut])
async def accounts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(MailAccount)
                             .where(MailAccount.owner_user_id == user.id)
                             .order_by(MailAccount.name))).scalars().all()
    return [_account_out(a) for a in rows]


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def account_create(data: AccountIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    values = data.model_dump()
    imap_pw, smtp_pw = values.pop("imap_password"), values.pop("smtp_password")
    a = MailAccount(**values, owner_user_id=user.id,
                    imap_password_enc=encrypt_secret(imap_pw) if imap_pw else "",
                    smtp_password_enc=encrypt_secret(smtp_pw) if smtp_pw else "")
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _account_out(a)


@router.put("/accounts/{kid}", response_model=AccountOut)
async def account_update(kid: int, data: AccountIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    a = await _account(db, kid, user)
    values = data.model_dump()
    imap_pw, smtp_pw = values.pop("imap_password"), values.pop("smtp_password")
    for field, value in values.items():
        setattr(a, field, value)
    # An empty field means "unchanged": the UI never gets to see the password and could
    # otherwise delete it by accident while saving the folder names.
    if imap_pw:
        a.imap_password_enc = encrypt_secret(imap_pw)
    if smtp_pw:
        a.smtp_password_enc = encrypt_secret(smtp_pw)
    await db.commit()
    await db.refresh(a)
    # Changed credentials or folder names: what still lies around belongs to the old state.
    mailbox.pool_empty(a.id)
    await cache.invalidate(a.id)
    return _account_out(a)


@router.delete("/accounts/{kid}", status_code=204)
async def account_delete(kid: int, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    a = await _account(db, kid, user)
    await db.delete(a)
    await db.commit()


@router.get("/unread")
async def unread(user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """Unread per mailbox — so one can see where something waits without going in.

    A mailbox that is unreachable at the moment returns `null` instead of a zero: "no new
    mail" and "I do not know" are two different pieces of information.
    """
    import asyncio

    rows = (await db.execute(select(MailAccount).where(
        MailAccount.owner_user_id == user.id,
        MailAccount.enabled.is_(True)).order_by(MailAccount.name))).scalars().all()

    async def one(account: MailAccount) -> dict:
        try:
            return {"account_id": account.id, "name": account.name,
                    "unseen": await cache.cached(account.id, "unread", cache.TTL_UNREAD,
                                                  lambda: mailbox.unread(account))}
        except Exception:  # noqa: BLE001 — a silent server must not blow the overview up
            return {"account_id": account.id, "name": account.name, "unseen": None}

    # Side by side instead of one after another: with three mailboxes that is the difference
    # zwischen einer und drei Wartezeiten.
    result = await asyncio.gather(*(one(k) for k in rows))
    return {"accounts": list(result),
            "total": sum(e["unseen"] or 0 for e in result)}


@router.get("/mcp-tools")
async def tools(_: User = Depends(get_current_user)):
    """The catalog: what a mailbox CAN release to agents, with the kind of permission."""
    from ..services.mail_mcp import TOOLS

    return [{"name": w["name"], "kind": w["art"], "description": w["description"],
             "always": bool(w.get("immer"))} for w in TOOLS]


@router.post("/accounts/{kid}/last", status_code=204)
async def latest_opened(kid: int, user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_session)):
    """Remembers which mailbox was open last — for the next visit."""
    await _account(db, kid, user)
    user.mail_last_account_id = kid
    await db.commit()


@router.post("/accounts/{kid}/test")
async def account_check(kid: int, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    return await mailbox.check(await _account(db, kid, user))


# ── Identities ──────────────────────────────────────────────────────────────

class IdentityIn(BaseModel):
    display_name: str = ""
    email: str
    reply_to: str = ""
    signature: str = ""
    is_default: bool = False


class IdentityOut(IdentityIn):
    id: int
    account_id: int


@router.get("/accounts/{kid}/identities", response_model=list[IdentityOut])
async def identities(kid: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    await _account(db, kid, user)
    rows = (await db.execute(select(MailIdentity).where(MailIdentity.account_id == kid)
                             .order_by(MailIdentity.id))).scalars().all()
    return rows


@router.post("/accounts/{kid}/identities", response_model=IdentityOut, status_code=201)
async def identity_create(kid: int, data: IdentityIn,
                             user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    await _account(db, kid, user)
    ident = MailIdentity(account_id=kid, **data.model_dump())
    db.add(ident)
    await _only_one_default(db, kid, ident)
    await db.commit()
    await db.refresh(ident)
    return ident


@router.put("/identities/{iid}", response_model=IdentityOut)
async def identity_update(iid: int, data: IdentityIn,
                             user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_session)):
    ident = await db.get(MailIdentity, iid)
    if ident is None:
        raise Error(404, "err.identity_not_found", "Identity not found")
    await _account(db, ident.account_id, user)
    for field, value in data.model_dump().items():
        setattr(ident, field, value)
    await _only_one_default(db, ident.account_id, ident)
    await db.commit()
    await db.refresh(ident)
    return ident


@router.delete("/identities/{iid}", status_code=204)
async def identity_delete(iid: int, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    ident = await db.get(MailIdentity, iid)
    if ident is None:
        return
    await _account(db, ident.account_id, user)
    await db.delete(ident)
    await db.commit()


async def _only_one_default(db: AsyncSession, kid: int, ident: MailIdentity) -> None:
    """Two defaults would be none. Whoever sets one takes it from the others."""
    if not ident.is_default:
        return
    rows = (await db.execute(select(MailIdentity).where(
        MailIdentity.account_id == kid))).scalars().all()
    for different in rows:
        if different is not ident:
            different.is_default = False


# ── Postfach ────────────────────────────────────────────────────────────────

@router.get("/accounts/{kid}/folders")
async def folder(kid: int, counts: bool = False, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    """The folder tree. `counts=true` additionally asks for the unread counts — that is one
    call per folder and therefore nothing that should run along with every click.

    With counters that is around 900 ms for 33 folders; which is why the answer comes from the
    cache as long as nothing has happened on the account (`services/mailbox_cache`).
    """
    account = await _account(db, kid, user)
    return await cache.cached(account.id, f"folders:{int(counts)}", cache.TTL_FOLDER,
                               lambda: mailbox.folder(account, counts))


class FolderIn(BaseModel):
    folder: str


def _check_deletable(account, folder: str) -> None:
    """Folders that must not be deleted or renamed.

    The inbox is not deletable (the server refuses it anyway), and the special folders hang on
    the buttons of the UI: whoever deletes their trash afterwards has a delete that no longer
    works. Renaming is the same case: on IMAP the name IS the folder, so a renamed trash is a
    gone trash as far as the account is concerned.
    """
    if folder.upper() == "INBOX":
        raise Error(400, "err.inbox_not_deletable", "The inbox cannot be deleted")
    roles = {account.folder_sent: "sent", account.folder_drafts: "drafts",
              account.folder_trash: "trash", account.folder_junk: "junk",
              account.folder_archive: "archive"}
    if folder and folder in roles:
        raise Error(400, "err.folder_has_role",
                     "'{folder}' is the {role} folder of this account. Change that first.",
                     folder=folder, role=roles[folder])


async def _delimiter(account: MailAccount) -> str:
    """How this server nests folders. A dot with Courier, a slash with Dovecot, and guessing
    it would mean creating `Archive/2026` as a folder with a slash in its name."""
    tree = await cache.cached(account.id, "folders:0", cache.TTL_FOLDER,
                               lambda: mailbox.folder(account, False))
    return next((o["delimiter"] for o in tree if o.get("delimiter")), "/")


@router.post("/accounts/{kid}/folders/read-all")
async def all_read(kid: int, data: FolderIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Set everything unread in the folder to read. Returns how many there were."""
    account = await _account(db, kid, user)
    count = await mailbox.all_read(account, data.folder)
    await cache.invalidate(account.id)
    return {"marked": count}


@router.post("/accounts/{kid}/folders/delete", status_code=204)
async def folder_delete(kid: int, data: FolderIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Delete a folder including its content. Special folders are protected."""
    account = await _account(db, kid, user)
    _check_deletable(account, data.folder)
    await mailbox.folder_delete(account, data.folder)
    await cache.invalidate(account.id)


@router.post("/accounts/{kid}/folders/empty")
async def folder_empty(kid: int, data: FolderIn, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_session)):
    """Empty the folder: the mail goes, the folder stays.

    Where it goes is the same question as with a single message: into the trash, unless one
    already stands in the trash (or the account has none). The answer says which of the two it
    was, because "127 into the trash" and "127 gone" are two different pieces of news.
    """
    account = await _account(db, kid, user)
    result = await mailbox.folder_empty(account, data.folder, account.folder_trash or "")
    await cache.invalidate(account.id)
    return result


class FolderCreateIn(BaseModel):
    name: str
    parent: str = ""


@router.post("/accounts/{kid}/folders/create")
async def folder_create(kid: int, data: FolderCreateIn,
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """A new folder, optionally below an existing one."""
    account = await _account(db, kid, user)
    delimiter = await _delimiter(account)
    name = data.name.strip().strip(delimiter)
    if not name:
        raise Error(400, "err.folder_name_missing", "The folder needs a name")
    if delimiter in name:
        raise Error(400, "err.folder_name_separator",
                     "'{separator}' separates folders on this server and cannot be part of a name",
                     separator=delimiter)
    full = f"{data.parent}{delimiter}{name}" if data.parent else name
    await mailbox.folder_create(account, full)
    await cache.invalidate(account.id)
    return {"folder": full}


class FolderRenameIn(BaseModel):
    folder: str
    name: str
    parent: str | None = None      # None = keep where it is


@router.post("/accounts/{kid}/folders/rename")
async def folder_rename(kid: int, data: FolderRenameIn,
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Rename a folder, and with `parent` hang it somewhere else at the same time.

    On IMAP both are the same command: the name is the path. Special folders are protected
    for the same reason as with deleting.
    """
    account = await _account(db, kid, user)
    _check_deletable(account, data.folder)
    delimiter = await _delimiter(account)
    name = data.name.strip().strip(delimiter)
    if not name:
        raise Error(400, "err.folder_name_missing", "The folder needs a name")
    if delimiter in name:
        raise Error(400, "err.folder_name_separator",
                     "'{separator}' separates folders on this server and cannot be part of a name",
                     separator=delimiter)
    parts = data.folder.split(delimiter)
    parent = data.parent if data.parent is not None else delimiter.join(parts[:-1])
    full = f"{parent}{delimiter}{name}" if parent else name
    if full == data.folder:
        return {"folder": full}
    # Into itself would be a folder that swallows its own path, and the server answers that
    # with an error nobody can read.
    if parent == data.folder or parent.startswith(data.folder + delimiter):
        raise Error(400, "err.folder_into_itself", "A folder cannot move into itself")
    await mailbox.folder_rename(account, data.folder, full)
    await cache.invalidate(account.id)
    return {"folder": full}


@router.get("/accounts/{kid}/messages")
async def messages(kid: int, folder: str = "INBOX", q: str = "", scope: str = "folder",
                      offset: int = 0, limit: int = 50,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """The messages of a folder, or the hits of a search.

    `scope=all` searches the whole mailbox instead of the open folder. That is one SELECT and
    one SEARCH per folder and therefore nothing that happens by itself: whoever wants it says
    so, and the answer says whether it had to stop at the cap.
    """
    account = await _account(db, kid, user)
    capped = max(1, min(limit, 200))
    # The search stays uncached: it is rarely the same one twice, and a hit that has actually
    # been moved already would be particularly annoying in a search.
    if q and scope == "all":
        return await mailbox.search_all(account, q, offset, capped)
    if q:
        return await mailbox.listing(account, folder, q, offset, capped)
    return await cache.cached(account.id, f"list:{folder}:{offset}:{capped}", cache.TTL_LISTING,
                               lambda: mailbox.listing(account, folder, "", offset, capped))


# ── Pictures from foreign servers ───────────────────────────────────────────

class ImageRuleIn(BaseModel):
    kind: str            # sender | domain | all
    value: str = ""


class ImageRuleOut(BaseModel):
    id: int
    kind: str
    value: str


async def _image_rules(db: AsyncSession, user: User) -> list[MailImageRule]:
    return list((await db.execute(select(MailImageRule).where(
        MailImageRule.owner_user_id == user.id)
        .order_by(MailImageRule.kind, MailImageRule.value))).scalars().all())


def _images_allowed(rules: list[MailImageRule], sender: str) -> bool:
    """Does an answer already exist for this sender?

    Three reaches, and the widest wins: whoever said "always" is not asked about a domain any
    more. A sender without an address (a broken header) falls through to the general rule,
    which is the careful direction: it says nothing about a house one cannot name.
    """
    sender = (sender or "").strip().lower()
    domain = sender.rpartition("@")[2]
    for rule in rules:
        if rule.kind == "all":
            return True
        if rule.kind == "sender" and sender and rule.value.lower() == sender:
            return True
        if rule.kind == "domain" and domain and rule.value.lower().lstrip("@") == domain:
            return True
    return False


@router.get("/image-rules", response_model=list[ImageRuleOut])
async def image_rules(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    return await _image_rules(db, user)


@router.post("/image-rules", response_model=ImageRuleOut, status_code=201)
async def image_rule_create(data: ImageRuleIn, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    if data.kind not in ("sender", "domain", "all"):
        raise Error(400, "err.unknown_reach", "Unknown reach '{name}'", name=data.kind)
    value = "" if data.kind == "all" else data.value.strip().lower().lstrip("@")
    if data.kind != "all" and not value:
        raise Error(400, "err.rule_without_sender", "The rule needs a sender or a domain")
    exists = (await db.execute(select(MailImageRule).where(
        MailImageRule.owner_user_id == user.id, MailImageRule.kind == data.kind,
        MailImageRule.value == value))).scalar_one_or_none()
    if exists is not None:
        return exists
    rule = MailImageRule(owner_user_id=user.id, kind=data.kind, value=value)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/image-rules/{rid}", status_code=204)
async def image_rule_delete(rid: int, user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    rule = await db.get(MailImageRule, rid)
    if rule is None or rule.owner_user_id != user.id:
        return
    await db.delete(rule)
    await db.commit()


@router.get("/accounts/{kid}/messages/{uid}")
async def message(kid: int, uid: int, folder: str = "INBOX",
                    user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    try:
        found = await mailbox.message(await _account(db, kid, user), folder, uid)
    except LookupError:
        raise Error(404, "err.mail_not_found", "Message not found")
    # Whether the pictures of THIS sender may be fetched without asking again. The decision
    # was made once, here it is only looked up.
    sender = (found.get("from") or [{}])[0].get("addr", "") if found.get("from") else ""
    found["images_allowed"] = _images_allowed(await _image_rules(db, user), sender)
    return found


@router.get("/accounts/{kid}/messages/{uid}/attachments/{index}")
async def attachment(kid: int, uid: int, index: int, folder: str = "INBOX",
                 user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    try:
        name, kind, data = await mailbox.attachment(await _account(db, kid, user), folder, uid, index)
    except LookupError:
        raise Error(404, "err.attachment_not_found", "Attachment not found")
    return Response(content=data, media_type=kind,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class FlagIn(BaseModel):
    folder: str = "INBOX"
    flag: str = "\\Seen"
    on: bool = True


@router.post("/accounts/{kid}/messages/{uid}/flag", status_code=204)
async def flag_set(kid: int, uid: int, data: FlagIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    account = await _account(db, kid, user)
    await mailbox.flag(account, data.folder, uid, data.flag, data.on)
    await cache.invalidate(account.id)


class MoveIn(BaseModel):
    folder: str = "INBOX"
    target: str


@router.post("/accounts/{kid}/messages/{uid}/move", status_code=204)
async def move(kid: int, uid: int, data: MoveIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    account = await _account(db, kid, user)
    await mailbox.move(account, data.folder, uid, data.target)
    await cache.invalidate(account.id)


class HandgripIn(BaseModel):
    folder: str = "INBOX"


@router.post("/accounts/{kid}/messages/{uid}/archive")
async def archive(kid: int, uid: int, data: HandgripIn,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_session)):
    """Into the archive. Where exactly is up to the account: a fixed folder or a pattern built
    from the date OF THE MAIL (`Archive/{year}`). Missing folders are created along the way."""
    account = await _account(db, kid, user)
    if account.archive_mode != "pattern" and not account.folder_archive:
        raise Error(400, "err.no_archive_folder",
                     "This account has no archive folder configured")
    target = await mailbox.archive(account, data.folder, uid)
    await cache.invalidate(account.id)
    # The target folder is worth reporting: with a pattern one sees only here where the
    # Mail wirklich gelandet ist.
    return {"folder": target}


class PatternIn(BaseModel):
    archive_pattern: str
    date: str = ""       # Beispieldatum (RFC-2822 oder ISO); leer = heute
    sender: str = ""


@router.post("/accounts/{kid}/archive-preview")
async def pattern_preview(kid: int, data: PatternIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_session)):
    """Shows which folder name a pattern produces — while typing, not only when
    ersten Klick auf „Archivieren"."""
    import datetime as dt

    account = await _account(db, kid, user)
    probe = MailAccount(archive_mode="pattern", archive_pattern=data.archive_pattern,
                        folder_archive=account.folder_archive)
    when = data.date or dt.datetime.now(dt.timezone.utc)
    return {"folder": mailbox.archive_target(probe, when, data.sender or "name@example.org")}


@router.post("/accounts/{kid}/messages/{uid}/spam", status_code=204)
async def as_spam(kid: int, uid: int, data: HandgripIn,
                   user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """Into the spam folder of the account. The verdict is the person's; learning from it
    happens in the assistant's spam detection, not here."""
    account = await _account(db, kid, user)
    if not account.folder_junk:
        raise Error(400, "err.no_junk_folder", "This account has no junk folder configured")
    await mailbox.move(account, data.folder, uid, account.folder_junk)
    await cache.invalidate(account.id)


@router.post("/accounts/{kid}/messages/{uid}/not-spam")
async def no_spam(kid: int, uid: int, data: HandgripIn,
                    user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """Back into the inbox — and the detection learns from it.

    In the spam folder "mark as spam" is not an offer but a repetition. What is missing there
    is the contradiction: this mail does not belong here.

    If there is a verdict of the spam detection for the mail, the contradiction is entered
    there (`spam_review.zurueckholen`) — then the memory notes the sender as wanted instead of
    making the same mistake tomorrow. Without a verdict (filed by hand, or the mail is older
    than the detection) it is simply pushed back.
    """
    from ..models.assistant import SpamVerdict
    from ..services import spam_review

    account = await _account(db, kid, user)
    # Looking the verdict up by the UID goes wrong: when moved into the spam folder the mail
    # gets a NEW number there, while the verdict holds the one from the inbox. That is why the
    # mail is read and matched through sender and subject — those are the details that
    # survived the move.
    header = await mailbox.message(account, data.folder, uid)
    sender = (header.get("from") or [{}])[0].get("addr", "") if header.get("from") else ""
    conditions = [SpamVerdict.owner_user_id == user.id, SpamVerdict.account == account.name]
    if sender:
        conditions.append(SpamVerdict.sender_email == sender)
    if header.get("subject"):
        conditions.append(SpamVerdict.subject == header["subject"][:500])
    verdict = (await db.execute(select(SpamVerdict).where(*conditions)
                               .order_by(SpamVerdict.id.desc()))).scalars().first()
    if verdict is None:
        # A second attempt through the number — for verdicts from the time when the mail still
        # lay in the inbox and was decided there.
        verdict = (await db.execute(select(SpamVerdict).where(
            SpamVerdict.owner_user_id == user.id, SpamVerdict.account == account.name,
            SpamVerdict.uid == uid).order_by(SpamVerdict.id.desc()))).scalars().first()
    if verdict is not None and verdict.status in ("spam", "pending"):
        result = await spam_review.reclaim(db, verdict, decided_by="mailbox")
        await cache.invalidate(account.id)
        return {"moved": True, "learned": True, "result": result}
    await mailbox.move(account, data.folder, uid, "INBOX")
    await cache.invalidate(account.id)
    return {"moved": True, "learned": False}


@router.post("/accounts/{kid}/messages/{uid}/delete", status_code=204)
async def delete(kid: int, uid: int, data: HandgripIn,
                   user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """Deleting means moving — unless one already stands in the trash, then it means gone.

    That is exactly how one knows it from every mail program, and it is the only version in
    which a misgrab is no loss.
    """
    account = await _account(db, kid, user)
    if not account.folder_trash or data.folder == account.folder_trash:
        await mailbox.final_delete(account, data.folder, uid)
    else:
        await mailbox.move(account, data.folder, uid, account.folder_trash)
    await cache.invalidate(account.id)


class BulkIn(BaseModel):
    folder: str = "INBOX"
    uids: list[int] = []
    action: str                       # flag | archive | move | delete
    target: str = ""                  # bei move
    flag: str = "\\Seen"               # bei flag
    on: bool = True


@router.post("/accounts/{kid}/messages/bulk")
async def bulk(kid: int, data: BulkIn, user: User = Depends(get_current_user),
                db: AsyncSession = Depends(get_session)):
    """The same handle over several messages at once.

    A selection of thirty used to be thirty requests. The answer says how many it was and
    where they went, because with a pattern archive that is not one folder but several, and
    "127 into the trash" is different news from "127 gone".
    """
    account = await _account(db, kid, user)
    if data.action not in ("flag", "archive", "move", "delete"):
        raise Error(400, "err.unknown_action", "Unknown action '{name}'", name=data.action)
    if not data.uids:
        return {"done": 0, "action": data.action}
    if data.action == "move" and not data.target:
        raise Error(400, "err.no_target_folder", "No target folder")
    if data.action == "archive" and account.archive_mode != "pattern" \
            and not account.folder_archive:
        raise Error(400, "err.no_archive_folder",
                     "This account has no archive folder configured")
    result = await mailbox.bulk(account, data.folder, data.uids, data.action,
                                 target=data.target, flag=data.flag, on=data.on)
    await cache.invalidate(account.id)
    return result


# ── Newsletters ─────────────────────────────────────────────────────────────

@router.get("/accounts/{kid}/newsletters")
async def newsletters(kid: int, folders: str = "", user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Which subscriptions arrive in this mailbox.

    Not a guess: a newsletter says of itself that it is one (`List-Unsubscribe`, RFC 2369).
    Whoever sends without that header does not turn up here, and that is honest, because for
    those the way out is not a button but the junk folder.

    Which folders are looked at is up to the caller; without a word it is the inbox. Every
    folder costs its own pass over up to eight hundred mails.
    """
    from ..services import newsletters as service

    account = await _account(db, kid, user)
    wanted = [f.strip() for f in folders.split(",") if f.strip()] or ["INBOX"]
    return {"newsletters": await cache.cached(
        account.id, f"newsletters:{','.join(wanted)}", 300,
        lambda: service.scan(account, wanted))}


class UnsubscribeIn(BaseModel):
    http: str = ""
    mailto: str = ""
    one_click: bool = False
    identity_id: int | None = None
    name: str = ""


@router.post("/accounts/{kid}/newsletters/unsubscribe")
async def unsubscribe(kid: int, data: UnsubscribeIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Out of a subscription, the way the sender named themselves.

    Two ways, and the order is not arbitrary. `one_click` (RFC 8058) is a POST and done, no
    page, no login. Without it a mail goes out, which every list understands but which takes
    its time.

    A plain HTTP address without one-click is NOT called here: it is a page meant for a human,
    often with a confirmation on it, and a POST from us into it would at best do nothing and
    at worst confirm something nobody read. It goes back to the caller as a link.
    """
    from ..services import newsletters as service

    account = await _account(db, kid, user)
    if data.one_click and data.http:
        ok, said = await service.one_click(data.http)
        return {"done": ok, "way": "one_click", "detail": said}

    if data.mailto:
        ident = await db.get(MailIdentity, data.identity_id) if data.identity_id else None
        if ident is None:
            ident = (await db.execute(select(MailIdentity).where(
                MailIdentity.account_id == account.id)
                .order_by(MailIdentity.is_default.desc(), MailIdentity.id))).scalars().first()
        if ident is None:
            raise Error(400, "err.account_without_identity",
                         "This account has no identity, so no mail can go out")
        # Everything after the question mark is what the list wants to read: mostly a subject
        # and now and then a body. Taken as it stands, because it is their key, not our text.
        address, _, query = data.mailto[len("mailto:"):].partition("?")
        from urllib.parse import parse_qs, unquote

        fields = parse_qs(query)
        await mailbox.send(account, ident, {
            "to": [unquote(address)], "cc": [], "bcc": [],
            "subject": (fields.get("subject") or ["unsubscribe"])[0],
            "text": (fields.get("body") or ["unsubscribe"])[0],
            "in_reply_to": "", "attachments": [],
        })
        return {"done": True, "way": "mail", "detail": unquote(address)}

    if data.http:
        # A page for a person. We do not click it for them.
        return {"done": False, "way": "link", "detail": data.http}
    raise Error(400, "err.no_unsubscribe_way", "This subscription names no way out")


# ── Sending and drafts ──────────────────────────────────────────────────────

class AttachmentIn(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    data_base64: str


class SendIn(BaseModel):
    identity_id: int
    to: list[str] = []
    cc: list[str] = []
    bcc: list[str] = []
    subject: str = ""
    text: str = ""
    in_reply_to: str = ""
    attachments: list[AttachmentIn] = []


async def _fields(db: AsyncSession, kid: int, data: SendIn, user: User):
    account = await _account(db, kid, user)
    ident = await db.get(MailIdentity, data.identity_id)
    if ident is None or ident.account_id != account.id:
        raise Error(400, "err.identity_not_of_account",
                     "The identity does not belong to this account")
    fields = data.model_dump(exclude={"identity_id", "attachments"})
    fields["attachments"] = [
        {"filename": a.filename, "content_type": a.content_type,
         "data": base64.b64decode(a.data_base64)} for a in data.attachments]
    return account, ident, fields


@router.post("/accounts/{kid}/send", status_code=204)
async def send(kid: int, data: SendIn, user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    account, ident, fields = await _fields(db, kid, data, user)
    if not fields.get("to"):
        raise Error(400, "err.no_recipient", "No recipient")
    await mailbox.send(account, ident, fields)
    await cache.invalidate(account.id)


@router.post("/accounts/{kid}/draft", status_code=204)
async def draft(kid: int, data: SendIn, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_session)):
    account, ident, fields = await _fields(db, kid, data, user)
    await mailbox.draft_save(account, ident, fields)
    await cache.invalidate(account.id)


# ── Actions (flows) ─────────────────────────────────────────────────────────

def _start_trigger(graph: dict) -> dict:
    """The trigger entry on the start node, read raw.

    `events.trigger_of` exists already but returns only event triggers (it checks for
    `event`). A mail trigger carries `kind` instead, and pulling both into one function would
    mean taking a condition away from the event path where it is right.
    """
    for n in (graph or {}).get("nodes") or []:
        if (n.get("type") or (n.get("data") or {}).get("type")) == "start":
            cfg = (n.get("data") or {}).get("config") or n.get("config") or {}
            t = cfg.get("trigger")
            return t if isinstance(t, dict) else {}
    return {}


@router.get("/actions")
async def actions(user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_session)):
    """Own flows whose start node waits for mails (`trigger.kind = "mail_action"`)."""
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
                    # A flow that processes an attachment belongs on the attachment and not on
                    # the mail — it says so itself through its trigger.
                    "scope": t.get("scope") or "message"})
    return out


class ActionIn(BaseModel):
    definition_id: int
    folder: str = "INBOX"
    attachment: int | None = None
    # Several attachments at once: one run per file, so that a flow stays what it is, the way
    # of ONE attachment. `all` takes whatever hangs on the mail, without the UI having to
    # count them first.
    attachments: list[int] | None = None
    all: bool = False


@router.post("/accounts/{kid}/messages/{uid}/action")
async def action_start(kid: int, uid: int, data: ActionIn,
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_session)):
    """Starts the flow with everything it needs to know about the mail."""
    from ..models.enums import WorkflowSubjectKind
    from ..models.workflow import WorkflowDefinition
    from ..services.workflow_engine import start_workflow

    account = await _account(db, kid, user)
    definition = await db.get(WorkflowDefinition, data.definition_id)
    if definition is None or definition.current_version_id is None:
        raise Error(400, "err.workflow_definition_missing_not",
                     "The workflow definition is missing or not published")
    header = await mailbox.message(account, data.folder, uid)
    attachments = header.get("attachments") or []

    # Which files this call is about. Nothing chosen means the mail itself: the flow then
    # runs once, without an attachment in its context.
    if data.all:
        wanted = [a["index"] for a in attachments]
        if not wanted:
            raise Error(400, "err.no_attachment", "This message has no attachments")
    elif data.attachments is not None:
        wanted = list(data.attachments)
    elif data.attachment is not None:
        wanted = [data.attachment]
    else:
        wanted = []

    chosen = []
    for index in wanted:
        hit = next((a for a in attachments if a["index"] == index), None)
        if hit is None:
            raise Error(404, "err.attachment_not_found", "Attachment not found")
        chosen.append(hit)

    mail = {
        "account": account.name, "account_id": account.id, "folder": data.folder, "uid": uid,
        # Who pressed the button. A flow that reports afterwards has a recipient that way
        # (`mail.owner_id`) instead of shouting into the room.
        "owner_id": user.id,
        "subject": header.get("subject", ""),
        "from": (header.get("from") or [{}])[0].get("addr", ""),
        "date": header.get("date", ""),
        "message_id": header.get("message_id", ""),
        "text": (header.get("text") or "")[:20000],
        "attachments": attachments,
        # A flow that assigns the assistant should know the house rules of the mailbox as
        # well — otherwise they would only apply through MCP.
        "instructions": account.mcp_instructions,
    }

    async def start(one: dict | None):
        return await start_workflow(
            db, definition, subject_kind=WorkflowSubjectKind.standalone,
            context={"mail": mail, "attachment": one or {}},
            actor_id=user.id, source=f"mail:{account.name}",
            source_ref=f"{data.folder}:{uid}" + (f":{one['index']}" if one else ""))

    runs = [await start(one) for one in chosen] if chosen else [await start(None)]
    return {"instance_id": runs[0].id, "status": runs[0].status.value,
            "runs": [{"instance_id": r.id, "status": r.status.value,
                       "attachment": (one or {}).get("filename", "")}
                      for r, one in zip(runs, chosen or [None])]}
