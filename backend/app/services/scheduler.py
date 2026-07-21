"""Job-Scheduler (cron/interval/once) — echte Ausführung (prompt→Worker, script→Subprocess)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os

from croniter import croniter
from sqlalchemy import select

from ..core.redis import enqueue_task
from ..db import SessionLocal
from ..models.predecessor import Job, JobRun
from ..models.notification import Notification

log = logging.getLogger("scheduler")
INTERVAL = 15
SCRIPT_DIR = os.getenv("JOB_SCRIPT_DIR", "/scripts")
EX_SUCCESS = 42


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _due(job: Job, now: dt.datetime) -> bool:
    if job.type == "interval":
        secs = int(job.schedule) if job.schedule.isdigit() else 60
        return job.last_run_at is None or (now - job.last_run_at).total_seconds() >= secs
    if job.type == "cron":
        base = job.last_run_at or (now - dt.timedelta(minutes=1))
        try:
            return croniter(job.schedule, base).get_next(dt.datetime) <= now
        except (ValueError, KeyError):
            return False
    if job.type == "once":
        if job.last_run_at is not None:
            return False
        try:
            return dt.datetime.fromisoformat(job.schedule) <= now
        except ValueError:
            return False
    return False


def _resolve_script(command: str) -> str | None:
    full = os.path.realpath(os.path.join(SCRIPT_DIR, command))
    if full == os.path.realpath(SCRIPT_DIR) or full.startswith(os.path.realpath(SCRIPT_DIR) + os.sep):
        return full
    return None


async def _run_script(db, job: Job, jr: JobRun) -> None:
    script = _resolve_script(job.command)
    if not script or not os.path.isfile(script):
        jr.status, jr.error = "error", f"Script nicht im erlaubten Verzeichnis: {job.command}"
        jr.finished_at = _now()
        return
    try:
        p = await asyncio.create_subprocess_exec(
            script, *[str(a) for a in (job.args or [])],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=job.run_timeout)
        rc = p.returncode or 0
        jr.output = out.decode("utf-8", "replace")[:20000]
        jr.exit_code = rc
        jr.status = "ok" if rc in (0, EX_SUCCESS) else "error"
        if rc == EX_SUCCESS and job.pause_on_success:
            job.paused = True
    except asyncio.TimeoutError:
        jr.status, jr.error = "error", "Script-Timeout"
    jr.finished_at = _now()
    if job.notify_mode == "always" or (job.notify_mode == "on_output" and jr.output) or \
            (job.notify_mode == "on_error" and jr.status == "error"):
        db.add(Notification(kind="job", title=f"Job: {job.name}", body=(jr.output or jr.error)[:4000],
                            chat_id=job.notify_chat))


async def _tick() -> None:
    now = _now()
    async with SessionLocal() as db:
        jobs = (
            await db.execute(select(Job).where(Job.enabled.is_(True), Job.paused.is_(False)))
        ).scalars().all()
        for job in jobs:
            if not _due(job, now):
                continue
            job.last_run_at = now
            jr = JobRun(job_id=job.id, status="running")
            db.add(jr)
            await db.flush()
            if job.kind == "script":
                await _run_script(db, job, jr)
            else:  # prompt → Worker
                await enqueue_task({"kind": "job", "task_id": f"job-{jr.id}",
                                    "job_id": job.id, "job_run_id": jr.id})
            log.info("job %s ausgelöst (%s)", job.name, job.kind)
        await db.commit()


async def _flush_coalesced() -> None:
    """Abgelaufene Coalescing-Fenster zu je einer Sammel-Notification zusammenfassen."""
    from ..models.predecessor import WebhookCoalesce, WebhookSub

    now = _now()
    async with SessionLocal() as db:
        rows = (await db.execute(select(WebhookCoalesce).where(
            WebhookCoalesce.flushed.is_(False), WebhookCoalesce.window_until <= now,
        ))).scalars().all()
        for row in rows:
            row.flushed = True
            if not row.payloads:
                continue  # Fenster lief leer aus — die Erstzustellung war die einzige
            sub = (await db.execute(select(WebhookSub).where(
                WebhookSub.route == row.route))).scalar_one_or_none()
            n = len(row.payloads)
            db.add(Notification(
                kind="webhook", title=f"{row.route}: {n} weitere Ereignisse ({row.event_key})",
                body=json.dumps(row.payloads[:10], ensure_ascii=False)[:4000],
                chat_id=sub.notify_chat if sub else None))
            log.info("coalesce geflusht: %s/%s (%d Ereignisse)", row.route, row.event_key, n)
        await db.commit()


async def run_scheduler() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await _tick()
            await _flush_coalesced()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(INTERVAL)
