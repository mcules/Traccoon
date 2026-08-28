"""The configuration audit: ask the scanner, compare, keep the history.

The scanner runs in a container of its own (`agentshield/`, service `agentshield`), and it
has to: it drives an npm tool over the `.claude` directories of the host, which needs the
binary and read-only mounts that have no business inside the backend image. Everything else
is here — and that is the point of this module.

It used to be the other way round. The collector kept its own state, compared runs itself,
wrote a hundred rows through the plugin store one HTTP call at a time and reported its own
events. That made the part outside the house the part that owned the knowledge: what counts
as the same finding, when something is new, what "gone" means. Now the scanner answers one
question — what do you see right now — and the comparing, the history and the events happen
where the rest of Traccoon's does.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agentshield import SEVERITIES, ShieldFinding, ShieldRun, ShieldRunConfig

log = logging.getLogger(__name__)

SHIELD_URL = os.getenv("AGENTSHIELD_URL", "http://agentshield:8790")
# How long a scan may take. Thirteen configurations take a second or two; the timeout is for
# the case where the tool hangs, not for the normal way.
SCAN_TIMEOUT = float(os.getenv("AGENTSHIELD_TIMEOUT", "600"))
# How many runs stay. The history is read whole when somebody opens the page, so it needs an
# end — and a year of daily runs is more curve than any screen can draw.
KEEP_RUNS = int(os.getenv("AGENTSHIELD_KEEP_RUNS", "365"))


def finding_key(config: str, raw: dict) -> str:
    """What makes a finding the same one across runs: configuration, rule and file.

    Not the line and not an excerpt of the text — both move as soon as somebody inserts a
    line above, and the finding would be new every day although nothing changed.

    And **not the title**, which the collector used to hash along. The tool rewords its
    titles between versions ("No PreToolUse hooks" → "No PreToolUse security hooks
    configured"), and every one of those rewordings retired a finding and opened an
    identical one beside it: a run that reported thirty gone and thirty new, with nothing
    changed on the machine. Measured against a live scan not one of the stored keys matched
    with the title in it, and sixteen of seventeen did without.

    The title was in there for the allow rules, where the rule is what distinguishes two
    findings — but it stands in the rule id itself
    (`permissions-permissive-Bash(docker info *)`), so nothing is lost. Checked against a
    full scan: no two findings of a configuration share a key.
    """
    parts = [config, str(raw.get("rule") or raw.get("id") or ""), str(raw.get("file") or "")]
    return hashlib.sha256(" ".join(parts).encode()).hexdigest()[:24]


def normalise(report: dict) -> tuple[list[dict], list[dict]]:
    """The scanner's answer, turned into rows.

    The counts per configuration are counted here and not taken from the tool's own summary:
    a finding whose severity we do not know is dropped two lines below, and a history that
    shows a number the list beside it cannot account for is a history nobody trusts.
    """
    findings: list[dict] = []
    configs: list[dict] = []
    for one in report.get("configs") or []:
        name = str(one.get("config") or "")
        if not name:
            continue
        if one.get("error"):
            configs.append({"config": name, "grade": "?", "error": str(one["error"])[:300],
                            **{s: 0 for s in SEVERITIES}})
            continue
        counts = {s: 0 for s in SEVERITIES}
        for raw in one.get("findings") or []:
            severity = str(raw.get("severity") or "")
            if severity not in counts:
                continue
            counts[severity] += 1
            findings.append({
                "key": finding_key(name, raw),
                "config": name,
                "severity": severity,
                "title": str(raw.get("title") or "")[:300],
                "file": str(raw.get("file") or "")[:300],
                "rule": str(raw.get("rule") or raw.get("id") or "")[:120],
                "detail": str(raw.get("description") or raw.get("recommendation") or "")[:800],
            })
        configs.append({"config": name, "grade": str(one.get("grade") or "?")[:4], "error": "",
                        **counts})
    return findings, configs


async def ask_scanner(timeout: float | None = None) -> dict:
    """One POST to the container, the raw picture back.

    `httpx` is imported inside the function like everywhere else in the house: that way a
    test double takes effect and importing this module costs nothing.
    """
    import httpx

    async with httpx.AsyncClient(timeout=timeout or SCAN_TIMEOUT) as client:
        res = await client.post(f"{SHIELD_URL.rstrip('/')}/scan", json={})
        res.raise_for_status()
        return res.json()


async def reconcile(db: AsyncSession, fresh: list[dict], now: dt.datetime) -> dict:
    """Bring the stored findings onto the new scan.

    `ignored` always wins: what a person clicked away must not be reopened by a scan, or
    clicking it away was worth nothing.

    The very first run is a baseline. Everything is new there, and whoever already hangs a
    flow on the event would get a hundred messages for a state that is simply the starting
    point — so the first run reports itself as a run, not as a hundred finds.
    """
    known = {row.key: row for row in (await db.execute(select(ShieldFinding))).scalars().all()}
    baseline = not known
    seen: set[str] = set()
    new_rows: list[ShieldFinding] = []
    gone_rows: list[ShieldFinding] = []

    for item in fresh:
        seen.add(item["key"])
        row = known.get(item["key"])
        if row is None:
            row = ShieldFinding(**item, status="open", first_seen=now, last_seen=now,
                                seen_count=1)
            db.add(row)
            if not baseline:
                new_rows.append(row)
            continue
        # A finding can change its face without becoming another one: the tool rewords a
        # title, a rule gets a longer explanation. The key decides identity, the rest simply
        # follows.
        row.severity = item["severity"]
        row.title = item["title"]
        row.file = item["file"]
        row.rule = item["rule"]
        row.detail = item["detail"]
        row.last_seen = now
        row.seen_count = (row.seen_count or 0) + 1
        if row.status == "fixed":
            # It is back. Not a new one — it carries its own history, and `first_seen` says
            # since when this has been a matter at all.
            row.status = "open"
            new_rows.append(row)

    for key, row in known.items():
        if key in seen or row.status == "fixed":
            continue
        row.status = "fixed"
        row.last_seen = row.last_seen or now
        gone_rows.append(row)

    await db.flush()
    return {"new": new_rows, "gone": gone_rows, "baseline": baseline}


async def prune(db: AsyncSession) -> None:
    """Keep the history to a length. Oldest runs go, their configurations go with them."""
    total = (await db.execute(select(func.count(ShieldRun.id)))).scalar_one()
    if total <= KEEP_RUNS:
        return
    old = (await db.execute(
        select(ShieldRun.id).order_by(ShieldRun.started_at.asc()).limit(total - KEEP_RUNS)
    )).scalars().all()
    await db.execute(sa_delete(ShieldRun).where(ShieldRun.id.in_(old)))


async def scan(db: AsyncSession, trigger: str = "job") -> dict:
    """One audit, end to end: ask, compare, write, report.

    Returns the summary of the run — the same shape the events carry, so a flow that runs
    the scan and a flow that listens for it see the matter in the same words.
    """
    started = dt.datetime.now(tz=dt.timezone.utc)
    report = await ask_scanner()
    findings, configs = normalise(report)
    finished = dt.datetime.now(tz=dt.timezone.utc)

    counts = {s: 0 for s in SEVERITIES}
    for item in findings:
        counts[item["severity"]] += 1

    result = await reconcile(db, findings, finished)

    run = ShieldRun(
        started_at=started, finished_at=finished, trigger=trigger[:40] or "job",
        configs=len(configs), findings=len(findings),
        new_count=0 if result["baseline"] else len(result["new"]),
        fixed_count=len(result["gone"]), **counts,
    )
    db.add(run)
    await db.flush()
    for one in configs:
        db.add(ShieldRunConfig(run_id=run.id, **one))
    await prune(db)
    await db.flush()

    summary = {
        "run_id": run.id, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "trigger": run.trigger, "configs": run.configs, "findings": run.findings,
        "new": run.new_count, "fixed": run.fixed_count, "baseline": result["baseline"],
        **counts,
    }

    # The single findings first, the closing event afterwards: a flow hanging on the end may
    # assume that everything the single events refer to already stands.
    from .events import emit

    for row in result["new"]:
        await emit(db, "agentshield.finding.new", payload={"finding": _short(row), "run": summary},
                   source_ref=f"{run.id}:{row.key}")
    for row in result["gone"]:
        await emit(db, "agentshield.finding.fixed",
                   payload={"finding": _short(row), "run": summary},
                   source_ref=f"{run.id}:{row.key}")
    await emit(db, "agentshield.audit.finished",
               payload={"run": summary, "configs": configs}, source_ref=str(run.id))

    log.info("Configuration audit: %s findings over %s configurations (%s new, %s gone)",
             run.findings, run.configs, run.new_count, run.fixed_count)
    return summary


def _short(row: ShieldFinding) -> dict:
    """A finding as a flow sees it — no database object, no lazy loading in a template."""
    return {"key": row.key, "config": row.config, "severity": row.severity, "title": row.title,
            "file": row.file, "rule": row.rule, "detail": row.detail, "status": row.status,
            "first_seen": row.first_seen.isoformat() if row.first_seen else None,
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            "seen_count": row.seen_count}
