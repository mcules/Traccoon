"""Assistant sessions: a conversation can be created, loaded, switched and closed

The thread of the personal assistant was endless. Every chat message of an owner belonged to
one conversation, cut only by the calendar (`CHAT_MEMORY_DAYS`). There was no way to begin a
new subject without dragging yesterday's along, and none to pick up a subject that had been
set aside. The session is that cut.

Two things in here are load-bearing:

* `chat_summaries.session_id` plus the widened unique key. Without it the compacted memory of
  one conversation is read into the next one, and that bug is invisible — the agent simply
  "remembers" something the human never said in that conversation.
* The backfill. Every `(owner, agent)` pair that has chat tasks gets ONE session
  "Bisherige Unterhaltung", and its tasks and its summary are pointed at it. Nobody loses
  their history, and it lands where they expect it: as the conversation they were in.

`DEV_CREATE_ALL` does not replace this, and it gets in the way of it. `create_all` creates
missing TABLES but never missing COLUMNS: on a running installation the two new tables are
therefore already there (the backend created them when it reloaded), while
`assistant_tasks.session_id` and `chat_summaries.session_id` are not. Every step below
therefore looks first and acts second — the migration has to work on a database that has
already seen half of it, because that is the normal state here, not the exception.

Revision ID: c9d4b17a3e58
Revises: b7e2c94a03f1
Create Date: 2026-08-24
"""
import datetime as dt
import json

from alembic import op
import sqlalchemy as sa


revision = 'c9d4b17a3e58'
down_revision = 'b7e2c94a03f1'
branch_labels = None
depends_on = None


# The one table the backfill writes into, described once so the insert can hand back the id
# it generated on both dialects (no RETURNING, no second SELECT).
def _sessions_table() -> sa.Table:
    return sa.Table(
        "assistant_sessions", sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_user_id", sa.Integer),
        sa.Column("agent", sa.String(length=100)),
        sa.Column("title", sa.String(length=200)),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("meta", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def _agent_of(raw) -> str:
    """The agent a chat task belongs to, out of its `meta`.

    Read in Python and not in SQL: `meta->>'agent'` and `json_extract` are two different
    dialects, and the number of chat rows is in the thousands, not the millions.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    if not isinstance(raw, dict):
        return "assistent"
    return str(raw.get("agent") or "assistent")


def _stamp(value):
    """A timestamp as a datetime, whatever the driver handed back.

    Postgres returns `datetime`, SQLite returns the text it stored. The value is read here and
    written into a typed column again, so it has to be one thing by then — and a comparison
    between a string and a datetime would raise in the middle of the loop.
    """
    if not isinstance(value, str):
        return value
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def backfill(conn) -> int:
    """Put every existing chat task into the conversation it was already in.

    One session per (owner, agent) — a session belongs to exactly one agent, so the GameProj
    operator does not end up in the same conversation as the assistant. Returns the number of
    sessions created; importable so the test can run exactly this code instead of a
    reimplementation of it.
    """
    # Nur was noch in keiner Unterhaltung steht. Ohne diese Bedingung legte ein zweiter Lauf
    # (Wiederholung, teilweise angewandte Migration) neben jede bestehende Unterhaltung eine
    # zweite und verteilte die Nachrichten darauf.
    rows = conn.execute(sa.text(
        "SELECT id, owner_user_id, meta, created_at FROM assistant_tasks "
        "WHERE kind = 'chat' AND session_id IS NULL ORDER BY id")).fetchall()
    threads: dict[tuple, dict] = {}
    for task_id, owner, meta, created in rows:
        created = _stamp(created)
        key = (owner, _agent_of(meta))
        thread = threads.setdefault(key, {"ids": [], "first": created, "last": created})
        thread["ids"].append(task_id)
        # `created_at` may be NULL on very old rows; those simply do not move the window.
        if created is not None:
            if thread["first"] is None or created < thread["first"]:
                thread["first"] = created
            if thread["last"] is None or created > thread["last"]:
                thread["last"] = created

    table = _sessions_table()
    now = sa.func.now()
    for (owner, agent), thread in threads.items():
        result = conn.execute(table.insert().values(
            owner_user_id=owner, agent=agent, title="Bisherige Unterhaltung",
            last_message_at=thread["last"], closed_at=None, meta={},
            # The conversation began when its oldest message did, not when this migration ran:
            # the list is ordered by these two.
            created_at=thread["first"] or now, updated_at=now))
        session_id = result.inserted_primary_key[0]
        conn.execute(
            sa.text("UPDATE assistant_tasks SET session_id = :s WHERE id IN :ids")
              .bindparams(sa.bindparam("ids", expanding=True)),
            {"s": session_id, "ids": thread["ids"]})
        # The memory of that thread belongs to that session as well. Without this line the
        # summary would stay unassigned and be read into whatever session came next.
        conn.execute(sa.text(
            "UPDATE chat_summaries SET session_id = :s "
            "WHERE session_id IS NULL AND agent = :a AND "
            "(owner_user_id = :o OR (owner_user_id IS NULL AND :o IS NULL))"),
            {"s": session_id, "a": agent, "o": owner})
    return len(threads)


def _looked_at(conn):
    return sa.inspect(conn)


def _tables(insp) -> set:
    return set(insp.get_table_names())


def _columns(insp, table: str) -> set:
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(insp, table: str) -> set:
    return {i["name"] for i in insp.get_indexes(table)}


def _unique(insp, table: str) -> dict:
    return {u["name"]: list(u["column_names"]) for u in insp.get_unique_constraints(table)}


def _foreign_keys(insp, table: str) -> set:
    return {f["name"] for f in insp.get_foreign_keys(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = _looked_at(conn)
    present = _tables(insp)

    if "assistant_sessions" not in present:
        op.create_table(
            "assistant_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("agent", sa.String(length=100), nullable=False,
                      server_default="assistent"),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
    # The default list is "open, newest first"; these are the columns it reads.
    have = _indexes(_looked_at(conn), "assistant_sessions")
    for name, column in (("ix_assistant_sessions_owner_user_id", "owner_user_id"),
                         ("ix_assistant_sessions_last_message_at", "last_message_at"),
                         ("ix_assistant_sessions_closed_at", "closed_at")):
        if name not in have:
            op.create_index(name, "assistant_sessions", [column])

    if "assistant_channel_sessions" not in present:
        op.create_table(
            "assistant_channel_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("channel", sa.String(length=20), nullable=False,
                      server_default="telegram"),
            sa.Column("session_id", sa.Integer(),
                      sa.ForeignKey("assistant_sessions.id", ondelete="CASCADE"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
    insp = _looked_at(conn)
    have = _indexes(insp, "assistant_channel_sessions")
    for name, column in (("ix_assistant_channel_sessions_owner_user_id", "owner_user_id"),
                         ("ix_assistant_channel_sessions_session_id", "session_id")):
        if name not in have:
            op.create_index(name, "assistant_channel_sessions", [column])
    if "uq_assistant_channel" not in _unique(insp, "assistant_channel_sessions"):
        op.create_unique_constraint("uq_assistant_channel", "assistant_channel_sessions",
                                    ["owner_user_id", "channel"])

    # ── The two columns nothing but this migration can bring ─────────────────
    insp = _looked_at(conn)
    if "session_id" not in _columns(insp, "assistant_tasks"):
        op.add_column("assistant_tasks", sa.Column("session_id", sa.Integer(), nullable=True))
    insp = _looked_at(conn)
    if "fk_assistant_tasks_session" not in _foreign_keys(insp, "assistant_tasks"):
        op.create_foreign_key("fk_assistant_tasks_session", "assistant_tasks",
                              "assistant_sessions", ["session_id"], ["id"], ondelete="CASCADE")
    if "ix_assistant_tasks_session_id" not in _indexes(insp, "assistant_tasks"):
        op.create_index("ix_assistant_tasks_session_id", "assistant_tasks", ["session_id"])

    insp = _looked_at(conn)
    if "session_id" not in _columns(insp, "chat_summaries"):
        op.add_column("chat_summaries", sa.Column("session_id", sa.Integer(), nullable=True))
    insp = _looked_at(conn)
    if "fk_chat_summaries_session" not in _foreign_keys(insp, "chat_summaries"):
        op.create_foreign_key("fk_chat_summaries_session", "chat_summaries",
                              "assistant_sessions", ["session_id"], ["id"], ondelete="CASCADE")
    if "ix_chat_summaries_session_id" not in _indexes(insp, "chat_summaries"):
        op.create_index("ix_chat_summaries_session_id", "chat_summaries", ["session_id"])

    # The key of the memory grows by the session. This is the line that keeps one
    # conversation's memory out of the next one.
    unique = _unique(_looked_at(conn), "chat_summaries")
    wanted = ["owner_user_id", "agent", "session_id"]
    if unique.get("uq_chat_summary_faden") != wanted:
        if "uq_chat_summary_faden" in unique:
            op.drop_constraint("uq_chat_summary_faden", "chat_summaries", type_="unique")
        op.create_unique_constraint("uq_chat_summary_faden", "chat_summaries", wanted)

    backfill(conn)


def downgrade() -> None:
    op.drop_constraint("uq_chat_summary_faden", "chat_summaries", type_="unique")
    op.create_unique_constraint("uq_chat_summary_faden", "chat_summaries",
                                ["owner_user_id", "agent"])
    op.drop_index("ix_chat_summaries_session_id", table_name="chat_summaries")
    op.drop_constraint("fk_chat_summaries_session", "chat_summaries", type_="foreignkey")
    op.drop_column("chat_summaries", "session_id")

    op.drop_index("ix_assistant_tasks_session_id", table_name="assistant_tasks")
    op.drop_constraint("fk_assistant_tasks_session", "assistant_tasks", type_="foreignkey")
    op.drop_column("assistant_tasks", "session_id")

    op.drop_index("ix_assistant_channel_sessions_session_id",
                  table_name="assistant_channel_sessions")
    op.drop_index("ix_assistant_channel_sessions_owner_user_id",
                  table_name="assistant_channel_sessions")
    op.drop_table("assistant_channel_sessions")

    op.drop_index("ix_assistant_sessions_closed_at", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_last_message_at", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_owner_user_id", table_name="assistant_sessions")
    op.drop_table("assistant_sessions")
