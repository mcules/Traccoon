"""Job scheduler (cron/interval/once): real execution (prompt to worker, script to subprocess)."""
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
from ..models.ops import Job, JobRun
from ..models.notification import Notification
from ..models.user import User

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
        from .i18n import tr
        besitzer = await db.get(User, job.user_id) if job.user_id else None
        db.add(Notification(
            kind="job",
            title=await tr(db, "server.notify.job", getattr(besitzer, "locale", None),
                           name=job.name),
            body=(jr.output or jr.error)[:4000], chat_id=job.notify_chat))


async def run_job_kind(db, job: Job, jr: JobRun) -> bool:
    """Runs the non-prompt kinds of a job.

    Returns `True` = done (the JobRun is set finished), `False` = prompt job, which belongs
    in the Redis queue of the worker.

    The only place where `kind` branches: before, the branching stood only in the scheduler,
    which is why "run now" (API and agent tool) silently gave workflow and http jobs to the
    assistant as a prompt job: instead of the workflow instance an agent ran on an empty
    prompt.
    """
    if job.kind == "script":
        await _run_script(db, job, jr)
        return True
    if job.kind == "workflow":
        await _start_workflow_job(db, job, jr)
        return True
    if job.kind == "http":
        await _run_http_job(db, job, jr)
        return True
    if job.kind == "film":
        # The after-work film builds for 15 to 20 s and holds up the tick (15 s) for exactly
        # that long. Accepted for the same reason as with `_run_script`: a second execution
        # path beside this one would be a second schedule, a second history and a second
        # pause switch. The httpx timeout in the job lies below `job.run_timeout` so that the
        # JobRun can still write its error itself.
        from .office_film import run_film_job
        await run_film_job(db, job, jr)
        return True
    return False


async def _tick() -> None:
    now = _now()
    async with SessionLocal() as db:
        jobs = (
            await db.execute(select(Job).where(Job.enabled.is_(True), Job.paused.is_(False)))
        ).scalars().all()
        nachreichen: list[dict] = []
        for job in jobs:
            if not _due(job, now):
                continue
            job.last_run_at = now
            jr = JobRun(job_id=job.id, status="running")
            db.add(jr)
            await db.flush()
            if not await run_job_kind(db, job, jr):  # prompt → Worker
                nachreichen.append({"kind": "job", "task_id": f"job-{jr.id}",
                                    "job_id": job.id, "job_run_id": jr.id})
            log.info("job %s triggered (%s)", job.name, job.kind)
        await db.commit()
        # Commit FIRST, queue AFTERWARDS. The other way round the assignment lies in Redis
        # before the JobRun exists in the database; a free worker grabs it within
        # milliseconds, finds `jr is None` and returns SILENTLY (worker/__main__.py:494). The
        # job run then stays on "running" forever, without an error and without a run.
        for auftrag in nachreichen:
            await enqueue_task(auftrag)


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
        from .job_params import parameter
        inst = await start_workflow(
            db, definition, subject_kind=definition.subject_kind, context=parameter(job.args),
            actor_id=job.user_id, source=f"job:{job.id}",
        )
        jr.status = "ok"; jr.output = f"Workflow-Instanz #{inst.id} gestartet"
    except Exception as e:  # noqa: BLE001
        jr.status = "error"; jr.error = str(e)[:2000]
        log.exception("workflow-job %s fehlgeschlagen", job.name)
    jr.finished_at = _now()


async def _run_http_job(db, job: Job, jr: JobRun) -> None:
    """kind=http: calls a stored destination when due.

    The call stands in `job.http_request` ({method, path, query, headers, body}), the same
    shape as the process action, so that a flow can move effortlessly between schedule and
    process. Errors land as job errors (and therefore in the usual notify path).
    """
    from ..models.destination import Destination
    from . import destinations
    if job.destination_id is None:
        jr.status = "error"; jr.error = "Job ohne Ziel"; jr.finished_at = _now()
        return
    dest = await db.get(Destination, job.destination_id)
    if dest is None or not dest.enabled:
        jr.status = "error"; jr.error = "Ziel fehlt oder ist deaktiviert"; jr.finished_at = _now()
        return
    req = job.http_request or {}
    try:
        res = await destinations.call(
            db, dest,
            method=req.get("method") or "POST", path=req.get("path") or "",
            query=req.get("query") or {}, headers=req.get("headers") or {},
            body=req.get("body"), timeout=job.run_timeout or None,
        )
        jr.status = "ok" if res["ok"] else "error"
        jr.exit_code = res["status_code"]
        jr.output = (res.get("text") or json.dumps(res.get("json", ""), ensure_ascii=False))[:20000]
        if not res["ok"]:
            jr.error = f"HTTP {res['status_code']}: {res.get('error', '')[:500]}"
    except Exception as e:  # noqa: BLE001
        jr.status = "error"; jr.error = str(e)[:2000]
        log.exception("http-job %s fehlgeschlagen", job.name)
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
            besitzer = (await db.get(User, sub.owner_user_id)
                        if sub and sub.owner_user_id else None)
            db.add(Notification(
                kind="webhook",
                title=await tr(db, "server.notify.webhook_weitere",
                               getattr(besitzer, "locale", None),
                               route=row.route, anzahl=n, schluessel=row.event_key),
                body=json.dumps(row.payloads[:10], ensure_ascii=False)[:4000],
                chat_id=sub.notify_chat if sub else None))
            log.info("coalesce geflusht: %s/%s (%d Ereignisse)", row.route, row.event_key, n)
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
    from .spam_review import digest_faellig

    async with SessionLocal() as db:
        await digest_faellig(db)


async def _postfach_lernen() -> None:
    """Collect what the human decided themselves, without asking.

    Two paths, both without a question and without a model: mails they moved into the spam
    folder themselves (on the phone, in the webmail), and addresses they wrote to themselves.
    The former is spam learning material, the latter an acquittal.
    """
    from ..models.ops import WebhookSub
    from .spam_bootstrap import antwort_kontakte, spam_rueckkopplung

    async with SessionLocal() as db:
        owner_ids = (await db.execute(select(WebhookSub.owner_user_id).where(
            WebhookSub.mode == "assistant",
            WebhookSub.owner_user_id.isnot(None)).distinct())).scalars().all()
        for owner_id in owner_ids:
            try:
                await spam_rueckkopplung(db, owner_id)
                await antwort_kontakte(db, owner_id)
            except Exception:  # noqa: BLE001
                log.exception("Mailbox reconciliation for user %s failed", owner_id)


async def _vault_kontakte() -> None:
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
                await _vault_kontakte()
                await _postfach_lernen()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(INTERVAL)
