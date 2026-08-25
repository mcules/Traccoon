"""Mail accounts and identities of a person.

The difference to `imap-mcp`: there the accounts stand in the environment of the server, apply
to everyone and can only read. What stands here belongs to a person, is maintained by them and
carries both ways — IMAP for reading, SMTP for sending.

Passwords are stored encrypted (`core.security.encrypt_secret`), like provider tokens and the
vault. `auth_type` is always `password` today; the columns for OAuth are already there so that
Gmail and Microsoft can be added later without a migration of the data.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class MailAccount(TimestampMixin, Base):
    __tablename__ = "mail_accounts"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_mail_account_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Short name in the UI and in the context of a flow ("private", "board").
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    imap_host: Mapped[str] = mapped_column(String(255), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    imap_user: Mapped[str] = mapped_column(String(255), default="")
    imap_password_enc: Mapped[str] = mapped_column(Text, default="")

    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    # ssl = encrypted from the start (465), starttls = upgrade (587), none = in-house only.
    smtp_security: Mapped[str] = mapped_column(String(10), default="starttls")
    smtp_user: Mapped[str] = mapped_column(String(255), default="")
    smtp_password_enc: Mapped[str] = mapped_column(Text, default="")

    auth_type: Mapped[str] = mapped_column(String(20), default="password")  # password | oauth2
    oauth_token_enc: Mapped[str] = mapped_column(Text, default="")

    # Where sent, draft and deleted mail belong. The names differ per provider ("Sent" ·
    # "Gesendet" · "INBOX.Sent"), which is why they belong to the account and not
    # in eine Liste im Code.
    folder_sent: Mapped[str] = mapped_column(String(255), default="Sent")
    folder_drafts: Mapped[str] = mapped_column(String(255), default="Drafts")
    folder_trash: Mapped[str] = mapped_column(String(255), default="Trash")
    folder_junk: Mapped[str] = mapped_column(String(255), default="Junk")
    folder_archive: Mapped[str] = mapped_column(String(255), default="Archive")
    # How the archive is split up: `folder` = everything into `folder_archive`, `pattern` = a
    # name from placeholders, built from the date OF THE MAIL (not from today — otherwise
    # eine alte Rechnung im laufenden Jahr).
    archive_mode: Mapped[str] = mapped_column(String(10), default="folder")  # folder | pattern
    archive_pattern: Mapped[str] = mapped_column(String(255), default="Archive/{year}")

    # ── What agents may see of this mailbox ─────────────────────────────────
    # A mailbox is the mail of a person, no data store. That is why everything here is off
    # until somebody switches it on — per tool individually, not as a
    # „Zugriff ja/nein".
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Folders that do not exist for tools (patterns with `*`, as in the old imap-mcp).
    # What stands here turns up neither in the folder list nor in a search.
    mcp_ignore_folders: Mapped[list] = mapped_column(JSON, default=list)
    # Freigegebene Werkzeuge, mit ihrem Namen (`mail_search`, `mail_send` …). Leer = keins.
    mcp_tools: Mapped[list] = mapped_column(JSON, default=list)
    # What an agent needs to know about this mailbox before it touches it: tone, who is
    # responsible, house rules ("never send without asking"). Stored on the account and not in
    # the agent prompt, because it changes with the mailbox and not with the agent.
    mcp_instructions: Mapped[str] = mapped_column(Text, default="")


class MailIdentity(TimestampMixin, Base):
    """Who appears as the sender. An account can have several identities (a role in the club,
    one's own name, an alias of a domain) — the server only has to be allowed to send it."""
    __tablename__ = "mail_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    signature: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class MailImageRule(TimestampMixin, Base):
    """Whose pictures may be fetched without asking again.

    Loading a picture from a foreign server tells the sender that the mail was read, which is
    why nothing is fetched by itself. But the question "may this one?" has the same answer
    every time for a newsletter one reads daily, and a question one answers the same way
    twenty times is not a question, it is a toll.

    Three reaches, from narrow to wide: one sender, one domain, everything. Whoever trusts
    `news@example.org` does not thereby trust every shop, and whoever trusts `@example.org`
    says something about a house, not about the world.

    Deliberately not on the account but on the person: mail from the same sender arrives in
    both mailboxes, and the answer would be the same in both.
    """
    __tablename__ = "mail_image_rules"
    __table_args__ = (UniqueConstraint("owner_user_id", "kind", "value",
                                        name="uq_mail_image_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # sender | domain | all. With `all` the value stays empty.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str] = mapped_column(String(320), default="")


class MailUnsubscribe(TimestampMixin, Base):
    """A subscription one has got out of, and how.

    Two reasons for writing it down. The first is the list: a newsletter one has unsubscribed
    from keeps its old mails in the folder, so it would go on turning up in the overview for
    months as if nothing had happened. The second is the receipt: unsubscribing is a request,
    not a switch. Whoever still gets mail from them four weeks later wants to be able to say
    when they asked and by which way, and against a list that keeps sending, that date is the
    whole argument.
    """
    __tablename__ = "mail_unsubscribes"
    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_mail_unsubscribe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("mail_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    # What groups the subscription: the list id where there is one, otherwise the sender.
    key: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(320), default="")
    sender: Mapped[str] = mapped_column(String(320), default="")
    list_id: Mapped[str] = mapped_column(String(320), default="")
    # one_click | mail — the way that was actually taken, not the ones on offer.
    way: Mapped[str] = mapped_column(String(20), default="")
    # What the other side said. `HTTP 200`, or the address the mail went to.
    detail: Mapped[str] = mapped_column(String(500), default="")
