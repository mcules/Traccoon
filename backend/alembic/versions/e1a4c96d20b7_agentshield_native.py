"""The configuration audit gets tables of its own, and its history moves in

Revision ID: e1a4c96d20b7
Revises: d5f1c93a7e28
"""
import datetime as dt
import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "e1a4c96d20b7"
down_revision = "d5f1c93a7e28"
branch_labels = None
depends_on = None

SEVERITIES = ("critical", "high", "medium", "low", "info")


def _when(raw):
    """The plugin wrote its times as ISO strings; the driver wants a datetime.

    Not a `CAST` in the statement: the parameter is bound typed, so the cast comes too late.
    """
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _key(config: str, rule: str, file: str) -> str:
    """The key of a finding, as the house computes it from now on.

    The collector hashed the title along, and the tool rewords its titles between versions —
    so the stored keys belong to titles that no longer exist. Carrying them over would make
    the first native run report the whole stock as gone and an identical stock as new.
    Recomputing costs nothing: configuration, rule and file all stand in the row.
    """
    return hashlib.sha256(" ".join([config, rule, file]).encode()).hexdigest()[:24]


def _counts(prefix: str, default: str = "0"):
    return [sa.Column(name, sa.Integer(), nullable=False, server_default=default)
            for name in SEVERITIES]


def upgrade() -> None:
    op.create_table(
        "agentshield_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(40), nullable=False, server_default="job"),
        sa.Column("configs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fixed_count", sa.Integer(), nullable=False, server_default="0"),
        *_counts("run"),
    )
    op.create_index("ix_agentshield_runs_started_at", "agentshield_runs", ["started_at"])

    op.create_table(
        "agentshield_run_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("agentshield_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("config", sa.String(200), nullable=False),
        sa.Column("grade", sa.String(4), nullable=False, server_default="?"),
        sa.Column("error", sa.String(300), nullable=False, server_default=""),
        *_counts("cfg"),
        sa.UniqueConstraint("run_id", "config", name="uq_shield_run_config"),
    )
    op.create_index("ix_agentshield_run_configs_run_id", "agentshield_run_configs", ["run_id"])
    op.create_index("ix_agentshield_run_configs_config", "agentshield_run_configs", ["config"])

    op.create_table(
        "agentshield_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("config", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("file", sa.String(300), nullable=False, server_default=""),
        sa.Column("rule", sa.String(120), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_agentshield_findings_key", "agentshield_findings", ["key"], unique=True)
    op.create_index("ix_agentshield_findings_config", "agentshield_findings", ["config"])
    op.create_index("ix_agentshield_findings_severity", "agentshield_findings", ["severity"])
    op.create_index("ix_agentshield_findings_status", "agentshield_findings", ["status"])

    _move_in()


def _move_in() -> None:
    """Carry over what the plugin collected.

    The findings are what matters: `first_seen` is how long something has been standing
    around, and `ignored` is a decision somebody made. Both would be gone with the plugin,
    and the next scan would present a week-old finding as new and reopen everything that was
    put aside.

    The plugin rows are left where they are. Removing them belongs to removing the plugin,
    and a migration that deletes the only copy of the data it just read is one nobody can
    run twice.
    """
    bind = op.get_bind()
    plugin_id = bind.execute(sa.text(
        "SELECT id FROM plugins WHERE slug = 'agentshield'")).scalar()
    if plugin_id is None:
        return

    rows = bind.execute(sa.text(
        "SELECT row FROM plugin_data WHERE plugin_id = :p AND table_name = 'findings'"
    ), {"p": plugin_id}).scalars().all()

    # Several old rows can fall onto one new key — the title used to be part of it, so a
    # reworded finding stands twice. They are one matter: the oldest sighting wins as
    # `first_seen`, the newest as `last_seen`, a decision to ignore beats everything (a
    # person made it), and an open one beats a fixed one (the newer word about it).
    merged: dict[str, dict] = {}
    for row in rows:
        data = row if isinstance(row, dict) else json.loads(row)
        config = str(data.get("config") or "")[:200]
        rule = str(data.get("rule") or "")[:120]
        file = str(data.get("file") or "")[:300]
        key = _key(config, rule, file)
        first, last = _when(data.get("first_seen")), _when(data.get("last_seen"))
        status = str(data.get("status") or "open")[:20]
        seen_count = int(data.get("seen_count") or 1)
        have = merged.get(key)
        if have is None:
            merged[key] = {
                "key": key, "config": config, "severity": str(data.get("severity") or "info")[:20],
                "title": str(data.get("title") or "")[:300], "file": file, "rule": rule,
                "detail": str(data.get("detail") or ""), "status": status,
                "first_seen": first, "last_seen": last, "seen_count": seen_count,
            }
            continue
        if first and (not have["first_seen"] or first < have["first_seen"]):
            have["first_seen"] = first
        if last and (not have["last_seen"] or last > have["last_seen"]):
            have["last_seen"] = last
            # The newer sighting also carries the newer wording.
            have["title"] = str(data.get("title") or "")[:300]
            have["severity"] = str(data.get("severity") or "info")[:20]
            have["detail"] = str(data.get("detail") or "")
        have["seen_count"] = max(have["seen_count"], seen_count)
        if have["status"] != "ignored" and (status == "ignored" or have["status"] == "fixed"):
            have["status"] = status if status == "ignored" else (
                "open" if status == "open" else have["status"])

    for item in merged.values():
        bind.execute(sa.text("""
            INSERT INTO agentshield_findings
                (key, config, severity, title, file, rule, detail, status,
                 first_seen, last_seen, seen_count)
            VALUES (:key, :config, :severity, :title, :file, :rule, :detail, :status,
                    COALESCE(:first_seen, now()), COALESCE(:last_seen, now()), :seen_count)
        """), item)

    runs = bind.execute(sa.text(
        "SELECT row FROM plugin_data WHERE plugin_id = :p AND table_name = 'runs' ORDER BY id"
    ), {"p": plugin_id}).scalars().all()
    for row in runs:
        data = row if isinstance(row, dict) else json.loads(row)
        run_id = bind.execute(sa.text("""
            INSERT INTO agentshield_runs
                (started_at, finished_at, trigger, configs, findings, new_count, fixed_count,
                 critical, high, medium, low, info)
            VALUES (COALESCE(:started_at, now()), :finished_at, :trigger, :configs,
                    :findings, :new_count, :fixed_count,
                    :critical, :high, :medium, :low, :info)
            RETURNING id
        """), {
            "started_at": _when(data.get("started_at")),
            "finished_at": _when(data.get("finished_at")),
            "trigger": str(data.get("trigger") or "job")[:40],
            "configs": int(data.get("configs") or 0),
            "findings": int(data.get("findings") or 0),
            "new_count": int(data.get("new_count") or 0),
            "fixed_count": int(data.get("fixed_count") or 0),
            **{name: int(data.get(name) or 0) for name in SEVERITIES},
        }).scalar()

        try:
            per_config = json.loads(data.get("configs_json") or "[]")
        except (TypeError, ValueError):
            per_config = []
        for one in per_config:
            counts = one.get("counts") or {}
            bind.execute(sa.text("""
                INSERT INTO agentshield_run_configs
                    (run_id, config, grade, error, critical, high, medium, low, info)
                VALUES (:run_id, :config, :grade, :error, :critical, :high, :medium, :low, :info)
                ON CONFLICT (run_id, config) DO NOTHING
            """), {
                "run_id": run_id,
                "config": str(one.get("config") or "")[:200],
                "grade": str(one.get("grade") or "?")[:4],
                "error": str(one.get("error") or "")[:300],
                **{name: int(counts.get(name) or 0) for name in SEVERITIES},
            })


def downgrade() -> None:
    op.drop_table("agentshield_run_configs")
    op.drop_table("agentshield_findings")
    op.drop_table("agentshield_runs")
