import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class AssistantTask(TimestampMixin, Base):
    """Project-less work item of the personal assistant (stands above the projects).

    Comes into being for instance from an incoming e-mail: a LOCAL model (qwen via litellm)
    classifies and redacts the content BEFOREHAND, and only `redacted_summary` (plus
    metadata) leaves the house towards Claude. The raw text stays local; Claude reads the
    full text only after an explicit approval (status `new` to `approved`) over the IMAP tools.
    """
    __tablename__ = "assistant_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Owner = the human the assistant serves; their token, their MCP group.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    kind: Mapped[str] = mapped_column(String(30), default="email")  # email | note | …
    source: Mapped[str] = mapped_column(String(120), default="")     # z. B. webhook:new-email
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)  # Idempotenz

    title: Mapped[str] = mapped_column(String(500), default="")
    # Result of the LOCAL pre-classification (qwen), produced in house.
    category: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low|normal|high|urgent
    # Cleaned summary that can be passed on to Claude (NO raw text, no PII).
    redacted_summary: Mapped[str] = mapped_column(Text, default="")
    # Metadata for the later IMAP full text access (account/uid/from/subject), no content.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # Redaction of this item: 'redacted' = only the summary to Claude (default, safe);
    # 'unredacted' = full text directly (only when an AssistantPolicy allows that for the source).
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")
    # Raw text stored ONLY when a rule allows 'unredacted' (otherwise NULL, so never stored in house).
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Learned action hint from the applying AssistantPolicy (for instance "file in Paperless").
    action_hint: Mapped[str] = mapped_column(Text, default="")

    # new = waiting for approval (nothing runs); approved = released to the worker;
    # running → done | error.
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Tool gate: what the run is currently waiting for approval on (status='awaiting').
    pending_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    pending_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # One-shot approval for the next resumption (consumed at the gate).
    grant_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    grant_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Did the assistant report explicitly during the run ("the human should see this", tool
    # `notify_human`)? Only then does the conclusion send a message; otherwise the result
    # stands silently in the inbox. A finished "nothing to do" should not disturb
    # anybody.
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Out of the view, not out of the world. Deleting would be wrong here: the assistant
    # learns from finished items (rules, spam statistics), and a chat message that was
    # archived is still the context of the conversation that followed it.
    archived_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)


class AssistantPermission(TimestampMixin, Base):
    """Learned tool permission of the assistant (owner-scoped, project-less): 'always allow'
    or 'never' per tool (plus resource). Read by the approval gate (perms.evaluate). The
    content is personal, so DB-only, not in git."""
    __tablename__ = "assistant_permissions"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "tool", "resource", name="uq_assistant_perm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(150), default="")     # glob
    resource: Mapped[str] = mapped_column(String(500), default="*")  # glob
    action: Mapped[str] = mapped_column(String(10), default="ask")  # allow | ask | deny


class AssistantPolicy(TimestampMixin, Base):
    """Learned rule of the personal assistant for incoming items (mail above all).

    Owner-scoped, project-less. The content (sender, actions and so on) is personal and stays
    in the database, NOT in git. It is filled through the approval "always …" (inbox,
    Telegram) or maintained by hand. If a rule matches an incoming item, that item can run
    automatically (redacted or unredacted) and carries the learned action hint along.
    """
    __tablename__ = "assistant_policies"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "match_kind", "match_value", name="uq_assistant_policy_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # What the rule matches on: 'sender' (news@verband.de) · 'domain' (verband.de) · 'category' (invoice).
    match_kind: Mapped[str] = mapped_column(String(20), default="sender")
    match_value: Mapped[str] = mapped_column(String(300), default="")

    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True)   # skips the review
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")  # redacted | unredacted
    action_hint: Mapped[str] = mapped_column(Text, default="")         # gelernte Aktion
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantContact(TimestampMixin, Base):
    """Known address from the Obsidian vault, the acquittal list of the spam detection.

    Contacts stand in the vault (`03 Bereiche/Personen|Kontakte|Firmen`), no longer in
    Nextcloud. The vault is not read per mail but mirrored here periodically: the check is
    therefore an index lookup and does not depend on the reachability of a Syncthing
    replica.

    Rows are a mirror, not a possession: the reconciliation deletes what has disappeared from
    the vault. Nothing here is maintained by hand.
    """
    __tablename__ = "assistant_contacts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "email", name="uq_assistant_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    # Where the address comes from: vault path of the note (traceability on a false alarm).
    source_path: Mapped[str] = mapped_column(String(500), default="")
    # 'frontmatter' = declared address field (reliable) · 'body' = found in the text
    # (weaker: the address of a third party stands there sometimes as well).
    source_kind: Mapped[str] = mapped_column(String(20), default="frontmatter")


class SpamVerdict(TimestampMixin, Base):
    """One spam verdict about an incoming mail, and what the human said about it.

    This table is work stock (open questions to Telegram) and memory at the same time: the
    detection learns from the decided rows (see `SpamFeatureStat`). That is why a decided row
    is never deleted: it is the learning material.

    Features deliberately lie here already broken down (`features`), not only as a raw mail:
    learning happens over features, and those have to be reconstructable later without the
    original mail (which wanders into the spam folder or is deleted by the human).
    """
    __tablename__ = "spam_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    assistant_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    # The flow that asked this question. Through it the answer from Telegram advances the
    # process instead of moving the mail past it (see spam_review).
    # NULL = legacy from the time before the mail process, so the direct way.
    workflow_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_instances.id", ondelete="SET NULL"), nullable=True, index=True)

    # For the later IMAP action (moving): account, folder and UID of the message.
    account: Mapped[str] = mapped_column(String(120), default="")
    folder: Mapped[str] = mapped_column(String(255), default="")
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sender_email: Mapped[str] = mapped_column(String(320), default="", index=True)
    sender_domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    # Which of my aliases did the mail go to? An alias only one provider knows and that
    # suddenly receives foreign advertising is sold or leaked, and that is a signal beyond
    # the single case.
    recipient: Mapped[str] = mapped_column(String(320), default="", index=True)
    subject: Mapped[str] = mapped_column(String(500), default="")

    # Partial verdicts, so that it is traceable afterwards WHO was wrong.
    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    model_score: Mapped[float] = mapped_column(Float, default=0.0)
    learned_score: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Broken down features for the learning (list of feature keys, see spam_learn).
    features: Mapped[list] = mapped_column(JSON, default=list)

    # What the mail was classified as (the model's kind: phishing, werbung, rechnung …).
    # Deliberately a free value and not an enum: the statistics group by whatever stands
    # here, so a new kind needs no migration and no code.
    art: Mapped[str] = mapped_column(String(40), default="", index=True)
    # Findings of both sources in one shape: [{quelle, kennung, text}]. The rules have
    # carried key plus plain text forever (`RuleResult.treffer`), the model now delivers the
    # same. Card, note and statistics read from here.
    befunde: Mapped[list] = mapped_column(JSON, default=list)

    # pending = waiting for the human · spam / ham = decided · skipped = expired
    # (mail no longer findable or similar).
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Identifier of the digest card in which this case was asked about. A button "confirm
    # all" needs a nameable set, and the Telegram callback carries only 64 characters, so a
    # short identifier fits and a list of numbers does not.
    digest_batch: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # How it was decided: telegram | web | auto, separating learned truth from assumption.
    decided_by: Mapped[str] = mapped_column(String(20), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Result of the IMAP action (moved, or error message), purely for looking up afterwards.
    action_result: Mapped[str] = mapped_column(Text, default="")


class SpamFeatureStat(TimestampMixin, Base):
    """Learned frequency of a feature in spam versus non-spam.

    This is the memory of the detection: every decision of the human raises counters here,
    and every *future* mail is held against these counters. Without this table the detection
    would stay equally clever on every pass, and the human would answer the same question
    forever.

    Deliberately counters instead of a trained model: traceable (you can look up why),
    effective immediately (no training run), correctable (a wrong decision can be counted
    back).
    """
    __tablename__ = "spam_feature_stats"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "feature", name="uq_spam_feature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Feature as a key, for instance 'from:werbung@example.com' · 'dom:example.com' ·
    # 'to:shop-alias@meine-domain.de' · 'sig:spf_fail' · 'wort:gewonnen'.
    feature: Mapped[str] = mapped_column(String(400), default="", index=True)
    spam_count: Mapped[int] = mapped_column(Integer, default=0)
    ham_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatSummary(TimestampMixin, Base):
    """Continuously written summary of a conversation thread (human and one agent).

    The history was a pure time window: the last exchanges verbatim, everything before that
    gone without replacement. After twelve hours the assistant knew nothing any more, not
    gradually weaker but abruptly nothing. Now older exchanges wander here, into a summary
    that grows along; it replaces nothing newer but carries the older part.

    Exactly ONE row per (human, agent): it is written on, not multiplied.
    `bis_task_id` remembers how far it reaches; everything after is still verbatim.
    """
    __tablename__ = "chat_summaries"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "agent", name="uq_chat_summary_faden"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(100), default="assistent")
    bis_task_id: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
