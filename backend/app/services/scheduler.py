"""Job scheduler (cron/interval/once): real execution (prompt to worker, script to subprocess)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os

from croniter import croniter
from zoneinfo import ZoneInfo
from sqlalchemy import select

from ..db import SessionLocal
from ..models.ops import Job, JobRun
from .job_modes import OLD_KINDS
from ..models.notification import Notification
from ..models.user import User

log = logging.getLogger("scheduler")
INTERVAL = 15
SCRIPT_DIR = os.getenv("JOB_SCRIPT_DIR", "/scripts")


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


STD_TZ = ZoneInfo("Europe/Berlin")


def zone_of(user) -> ZoneInfo:
    """The timezone the schedule of this person is meant in.

    A schedule is written by people: "0 8 * * *" means eight in the morning where the one who
    entered it stands — not eight UTC. Until here everything computed in UTC,
    und ein Morgenjob lief im Sommer um zehn.
    """
    try:
        return ZoneInfo(getattr(user, "timezone", "") or STD_TZ.key)
    except Exception:  # noqa: BLE001 — eine unbekannte Zone darf keinen Job anhalten
        return STD_TZ


def _seconds(schedule: str) -> int:
    """Der Abstand eines Intervall-Jobs.

    `900` and `interval:900` mean the same thing — the second spelling stands in older jobs.
    Something unreadable yields one minute: rather too often than not at all, because a job
    nie laeuft, faellt niemandem auf.
    """
    raw = (schedule or "").strip().removeprefix("interval:").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else 60


# What may stand in `type`. `kind` is something else — the kind of work (workflow, film).
# Confusing the two is the obvious mistake, and it does not stand out: a job with an unknown
# `type` is simply never due, while the UI shows "enabled, all
# 15 Minuten" anzeigt. Genau so lag der Job "Predecessor-Posteingang" 13 Tage still.
SCHEDULE_KINDS = ("cron", "interval", "once")


def _due(job: Job, now: dt.datetime, zone: ZoneInfo = STD_TZ) -> bool:
    if job.type not in SCHEDULE_KINDS:
        log.warning("Job %s (%s) hat den Zeitplan-Typ '%s' — erlaubt sind %s. Er laeuft "
                    "deshalb nie; gemeint war vermutlich `kind`.",
                    job.id, job.name, job.type, "/".join(SCHEDULE_KINDS))
        return False
    if job.type == "interval":
        secs = _seconds(job.schedule)
        return job.last_run_at is None or (now - job.last_run_at).total_seconds() >= secs
    if job.type == "cron":
        # Compute in the zone of the owner and only compare afterwards: croniter works with
        # the wall clock it is given.
        now_local = now.astimezone(zone)
        base = (job.last_run_at or (now - dt.timedelta(minutes=1))).astimezone(zone)
        try:
            return croniter(job.schedule, base).get_next(dt.datetime) <= now_local
        except (ValueError, KeyError):
            return False
    if job.type == "once":
        if job.last_run_at is not None:
            return False
        try:
            moment = dt.datetime.fromisoformat(job.schedule)
            # Without a timezone in the text the owner's is meant, not UTC.
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=zone)
            return moment <= now
        except ValueError:
            return False
    return False


def _resolve_script(command: str) -> str | None:
    full = os.path.realpath(os.path.join(SCRIPT_DIR, command))
    if full == os.path.realpath(SCRIPT_DIR) or full.startswith(os.path.realpath(SCRIPT_DIR) + os.sep):
        return full
    return None



async def run_job_kind(db, job: Job, jr: JobRun) -> None:
    """Runs a job. Afterwards its run stands on finished or on running.

    This used to hold the only branch over five job kinds. Four of them were the same matter
    in four executions — each with its own error handling, its own notification and the limit
    of being able to do exactly one thing. They have become nodes (`agent_run`, `script`,
    `http_request`), and a job has been schedule plus flow ever since.

    The film stays a kind of its own: it does nothing but itself, and prising it out of its
    500 lines would bring no gain for a single job. It holds the tick (15 s) up for the
    duration of its build — deliberately, for the same reason the script did before: a second
    execution path next to it would be a second schedule, a second
    Historie und ein zweiter Pausenschalter.
    """
    # An old kind is converted here, not refused: otherwise there would be a window (an entry
    # made by hand in the database, a restored backup) in which a job
    # anders liefe, als am Bildschirm steht.
    if job.kind in OLD_KINDS:
        from .job_modes import as_flow
        await as_flow(db, job)
        await db.flush()

    if job.kind == "workflow":
        await _start_workflow_job(db, job, jr)
        return
    if job.kind == "film":
        from .office_film import run_film_job
        await run_film_job(db, job, jr)
        return
    # A kind that does not exist: such a job used to fall mutely into the queue and ran into
    # the empty prompt field at the assistant. A visible error is
    # besser als ein Lauf, der etwas anderes tut, als draufsteht.
    jr.status = "error"
    jr.error = f"Unbekannte Job-Art „{job.kind}“"
    jr.finished_at = _now()


async def _tick() -> None:
    now = _now()
    async with SessionLocal() as db:
        jobs = (
            await db.execute(select(Job).where(Job.enabled.is_(True), Job.paused.is_(False)))
        ).scalars().all()
        # Fetch the zones once per owner instead of per job: there are few people and many
        # Jobs.
        zones: dict[int | None, ZoneInfo] = {}
        for job in jobs:
            if job.user_id not in zones:
                owner = await db.get(User, job.user_id) if job.user_id else None
                zones[job.user_id] = zone_of(owner)
            if not _due(job, now, zones[job.user_id]):
                continue
            job.last_run_at = now
            jr = JobRun(job_id=job.id, status="running")
            db.add(jr)
            await db.flush()
            await run_job_kind(db, job, jr)
            log.info("job %s triggered (%s)", job.name, job.kind)
        await db.commit()


async def _start_workflow_job(db, job: Job, jr: JobRun) -> None:
    """kind=workflow: starts a workflow instance when due (subject standalone)."""
    from ..models.workflow import WorkflowDefinition
    from ..services.workflow_engine import start_workflow
    if job.workflow_definition_id is None:
        jr.status = "error"; jr.error = "Job ohne workflow_definition_id"; jr.finished_at = _now()
        return
    definition = await db.get(WorkflowDefinition, job.workflow_definition_id)
    if definition is None or definition.current_version_id is None:
        jr.status = "error"; jr.error = "Definition fehlt oder nicht veröffentlicht"
        jr.finished_at = _now()
        return
    try:
        # The parameter set of the job is the start context of the run, by the same rule as
        # with prompt jobs (only an object counts, a list stays a script argument). Before,
        # `{}` stood here: the same flow for a second metric series would have needed a
        # second flow although only one word changes.
        from .job_params import builtin_values, parameter
        # The time values belong to it ever since the prompt jobs became flows: `{{ since }}`
        # and `{{ window }}` stood in their assignments and came out of the job world. Only
        # SUCCESSFUL runs count — if the job was broken yesterday, the window has to take the
        # gap along, otherwise a day falls silently under the table.
        leadtime = (await db.execute(
            select(JobRun.started_at).where(JobRun.job_id == job.id, JobRun.id != jr.id,
                                            JobRun.status == "ok")
            .order_by(JobRun.id.desc()).limit(1))).scalar()
        owner = await db.get(User, job.user_id) if job.user_id else None
        # Who ordered the run belongs in the context: a flow that reports something wants to
        # be able to name itself, and the digest link hangs on the run number.
        inst = await start_workflow(
            db, definition, subject_kind=definition.subject_kind,
            context={**builtin_values(last_run=leadtime, zone=zone_of(owner)),
                     **parameter(job.args),
                     "job": {"id": job.id, "name": job.name, "run_id": jr.id}},
            actor_id=job.user_id, source=f"job:{job.id}",
        )
        jr.workflow_instance_id = inst.id
        jr.status = "ok"
        # Short flows are already finished here; then their result stands in the history right
        # away instead of "started". All others the engine fills in when they end.
        from .workflow_engine import job_answer_text
        text = job_answer_text(inst) if inst.finished_at is not None else ""
        jr.output = text[:20000] if text else f"Workflow-Instanz #{inst.id} gestartet"
    except Exception as e:  # noqa: BLE001
        jr.status = "error"; jr.error = str(e)[:2000]
        log.exception("workflow job %s failed", job.name)
    jr.finished_at = _now()



async def _flush_coalesced() -> None:
    """Abgelaufene Coalescing-Fenster zu je einer Sammel-Notification zusammenfassen."""
    from ..models.ops import WebhookCoalesce, WebhookSub

    now = _now()
    async with SessionLocal() as db:
        rows = (await db.execute(select(WebhookCoalesce).where(
            WebhookCoalesce.flushed.is_(False), WebhookCoalesce.window_until <= now,
        ))).scalars().all()
        for row in rows:
            row.flushed = True
            if not row.payloads:
                continue  # the window ran out empty; the first delivery was the only one
            # Route names are no longer unique since the multi-user change (models/ops.py).
            # `scalar_one_or_none` raised `MultipleResultsFound` with two routes of the same
            # name and tore down the whole scheduler tick, on which jobs, timers and clean-up
            # work hang. The first hit is enough here: only the chat address is read from the
            # webhook.
            sub = (await db.execute(select(WebhookSub).where(
                WebhookSub.route == row.route).order_by(WebhookSub.id))).scalars().first()
            n = len(row.payloads)
            from .i18n import tr
            owner = (await db.get(User, sub.owner_user_id)
                        if sub and sub.owner_user_id else None)
            db.add(Notification(
                kind="webhook",
                title=await tr(db, "server.notify.webhook_more",
                               getattr(owner, "locale", None),
                               route=row.route, count=n, event_key=row.event_key),
                body=json.dumps(row.payloads[:10], ensure_ascii=False)[:4000],
                chat_id=sub.notify_chat if sub else None))
            log.info("coalesce flushed: %s/%s (%d events)", row.route, row.event_key, n)
        await db.commit()


RUN_RETENTION_KEY = "run_retention_days"
RUN_RETENTION_DEFAULT = 30
_purge_after = 0.0  # monotonic mark: clean-up runs at most hourly
# The vault changes rarely; hourly is enough. The first pass happens right at the start so
# that the acquittal list is not empty for an hour.
_vault_after = 0.0


async def _purge_archived_runs() -> None:
    """Delete archived agent runs after the retention period (ABC-29).

    The period in days comes from the AppSetting `run_retention_days` (default 30, 0 = never
    delete). RunSteps hang off it over ON DELETE CASCADE.
    """
    from sqlalchemy import delete

    from ..models.agents import Run
    from .appsettings import get_setting

    async with SessionLocal() as db:
        raw = await get_setting(db, RUN_RETENTION_KEY, str(RUN_RETENTION_DEFAULT))
        try:
            days = int(raw)
        except ValueError:
            days = RUN_RETENTION_DEFAULT
        if days <= 0:
            return
        cutoff = _now() - dt.timedelta(days=days)
        res = await db.execute(
            delete(Run).where(Run.archived.is_(True), Run.archived_at.isnot(None),
                              Run.archived_at < cutoff)
        )
        await db.commit()
        if res.rowcount:
            log.info("%d archived agent runs older than %d days deleted", res.rowcount, days)


async def _spam_digest() -> None:
    """Digest card for spam suspicions below the immediate threshold."""
    from .spam_review import digest_due

    async with SessionLocal() as db:
        await digest_due(db)


async def _mailbox_learn() -> None:
    """Collect what the human decided themselves, without asking.

    Two paths, both without a question and without a model: mails they moved into the spam
    folder themselves (on the phone, in the webmail), and addresses they wrote to themselves.
    The former is spam learning material, the latter an acquittal.
    """
    from ..models.ops import WebhookSub
    from .spam_bootstrap import answer_contacts, spam_feedback

    async with SessionLocal() as db:
        owner_ids = (await db.execute(select(WebhookSub.owner_user_id).where(
            WebhookSub.mode == "assistant",
            WebhookSub.owner_user_id.isnot(None)).distinct())).scalars().all()
        for owner_id in owner_ids:
            try:
                await spam_feedback(db, owner_id)
                await answer_contacts(db, owner_id)
            except Exception:  # noqa: BLE001
                log.exception("Mailbox reconciliation for user %s failed", owner_id)


async def _vault_contacts() -> None:
    """Update known addresses from the vault (the acquittal list of the spam detection).

    Only for people who actually run a mail webhook; for everybody else there would be
    nothing to check, and the vault pass costs file accesses.
    """
    from ..models.ops import WebhookSub
    from .vault_contacts import sync_contacts

    async with SessionLocal() as db:
        owner_ids = (await db.execute(select(WebhookSub.owner_user_id).where(
            WebhookSub.mode == "assistant",
            WebhookSub.owner_user_id.isnot(None)).distinct())).scalars().all()
        for owner_id in owner_ids:
            try:
                await sync_contacts(db, owner_id)
            except Exception:  # noqa: BLE001
                log.exception("Vault contact reconciliation for user %s failed", owner_id)


async def run_scheduler() -> None:
    global _purge_after, _vault_after
    await asyncio.sleep(8)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await _tick()
            await _flush_coalesced()
            await _spam_digest()
            if loop.time() >= _purge_after:
                _purge_after = loop.time() + 3600
                await _purge_archived_runs()
            if loop.time() >= _vault_after:
                _vault_after = loop.time() + 3600
                await _vault_contacts()
                await _mailbox_learn()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(INTERVAL)
