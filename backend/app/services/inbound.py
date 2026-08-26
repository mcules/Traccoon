"""What arrives from outside: take it in first, work on it afterwards.

A webhook used to be carried out inside its own request. Signature, flow start, answer. And
whatever threw in the middle took the payload with it. That is the wrong shape for this house:
nobody who sends to us tries twice. The archive hook, the house automation, the tracker and
the mail watcher all fire exactly once, and a restart during an update was enough to lose
what they had to say.

So the order is turned around. The raw body lands in `inbound_deliveries`, unread and
unchecked, and the answer is a receipt. Everything after that (signature, filter, flow) runs
from the table, may fail, and is repeated with growing distance. After a handful of attempts a
delivery is parked instead of hammering on, and the person is told: something is standing
still and nobody would have noticed otherwise.

Two things follow from this that are easy to overlook:

* **The signature is checked when the work is done, not when the delivery is taken in.** That
  is what lets a small separate receiver stand at the front door: it needs no secrets, only
  the right to insert a row, and it can therefore keep standing while the rest of the house
  is being rebuilt.
* **A caller that waits for an answer cannot be served from a queue.** A webhook with
  `response_timeout` therefore keeps running through the old, synchronous way. Nothing is lost
  there either as long as the house is up, and when it is not, a queue would not help the
  caller anyway: it wants the answer, not a receipt.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models.ops import InboundDelivery, WebhookCoalesce, WebhookSub

log = logging.getLogger("traccoon.inbound")

# How long the attempts stay apart. The first repeat comes quickly, because the usual cause is
# a restart that is over in seconds; after that the distance grows, because a cause that
# survives ten minutes is not a hiccup.
BACKOFF = (30, 120, 600, 3600)
# After this many attempts a delivery is parked. It stays complete and can be repeated by
# hand, what stops is the pointless hammering.
MAX_ATTEMPTS = len(BACKOFF) + 1
# How many are worked through per pass. Small on purpose: this is a background lane, and a
# backlog of a thousand must not push the rest of the house aside.
BATCH = 25
BEAT = 5.0

# Headers that are of no use later and have no business lying in a table.
_SKIP_HEADERS = {"authorization", "cookie", "proxy-authorization"}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def keep_headers(raw: dict) -> dict:
    """The headers worth keeping, lowercase.

    Lowercase because HTTP header names are case insensitive and whoever reads them later
    should not have to know how the sender happened to spell them.
    """
    return {str(k).lower(): str(v) for k, v in (raw or {}).items()
            if str(k).lower() not in _SKIP_HEADERS}


# ── the small helpers that shape a payload into a context ───────────────────

def dig_payload(data, path: str):
    """Resolve a dot path in the payload (`event.attributes.alarm`, `posten.0.name`)."""
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def fill(tpl: str, payload) -> str:
    """Fills `{field}` from the payload, `{a.b.c}` deeply as well.

    Nested payloads are the normal case as soon as the sender is not Traccoon:
    `{position.address}` or `{event.attributes.alarm}` were not addressable before.
    """
    out = tpl
    for hits in set(re.findall(r"\{([A-Za-z0-9_.]+)\}", tpl)):
        value = dig_payload(payload, hits)
        if value is not None:
            out = out.replace("{" + hits + "}", str(value))
    return out


def set_deep(target: dict, path: str, value) -> None:
    """`intake.agent` creates {"intake": {"agent": …}}, a dot in the TARGET nests."""
    parts = [t for t in str(path).split(".") if t]
    if not parts:
        return
    node = target
    for t in parts[:-1]:
        next_one = node.get(t)
        if not isinstance(next_one, dict):
            next_one = {}
            node[t] = next_one
        node = next_one
    node[parts[-1]] = value


def context_of(sub: WebhookSub, payload) -> dict:
    """The context of the run, in one place, for every kind of delivery.

    `context_map` fetches values from the payload (a dotted path; an **empty** path = the
    whole payload, so it lands under a key of its own instead of flat in the context),
    `context_fixed` sets fixed values in whose text `{field}` is filled from the payload.
    Without either, the payload is the context, as before.
    """
    nutz = payload if isinstance(payload, dict) else {"payload": payload}
    cmap = sub.context_map or {}
    fixed = sub.context_fixed or {}
    if not cmap and not fixed:
        return nutz
    ctx: dict = {}
    for target, path in cmap.items():
        set_deep(ctx, str(target), nutz if not path else dig_payload(nutz, str(path)))
    for target, value in fixed.items():
        set_deep(ctx, str(target), fill(value, nutz) if isinstance(value, str) else value)
    return ctx


def reference_of(sub: WebhookSub, payload) -> str | None:
    """The key against double delivery: a field of the payload or a template.

    A key out of several fields (`{account}:{uid}`) is the normal case as soon as the foreign
    system sends no id of its own, this exact composition used to sit hard-wired in the mail
    intake and was available to no other trigger.
    """
    field = (sub.ref_field or "").strip()
    nutz = payload if isinstance(payload, dict) else {}
    if not field:
        return None
    value = fill(field, nutz) if "{" in field else dig_payload(nutz, field)
    return str(value) if value not in (None, "") else None


# ── taking it in ────────────────────────────────────────────────────────────

async def store(db: AsyncSession, *, channel: str, target: str, route: str,
                body: bytes, headers: dict) -> InboundDelivery:
    """Put a delivery down. Nothing is looked at, nothing is checked."""
    row = InboundDelivery(channel=channel, target=target, route=route,
                          body=body or b"", headers=keep_headers(headers))
    db.add(row)
    await db.flush()
    return row


# ── working on it ───────────────────────────────────────────────────────────

class Dropped(Exception):
    """Correctly not carried out: a filter, a duplicate, a collection window.

    Deliberately not an error. It is a result, and repeating it would produce the same one.
    """


class Retry(Exception):
    """Not now, later. A flow that is missing may come back after a deployment."""


async def deliver(db: AsyncSession, sub: WebhookSub, raw: bytes, headers: dict) -> dict:
    """Carry out one delivery: check the signature, filter, collect, start.

    Raises `Dropped` when it was rightly not carried out and `Retry` when it should be tried
    again later. Everything else that goes wrong flies as it is and is treated as a retry.
    """
    from ..models.workflow import WorkflowDefinition, WorkflowInstance
    from ..services.workflow_engine import start_workflow
    from ..services.workflow_subject import subject_from_payload

    head = keep_headers(headers)
    if sub.secret:
        sig = head.get("x-webhook-signature", "")
        expected = hmac.new(sub.secret.encode(), raw or b"", hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            # Not a retry: the same bytes give the same signature for ever.
            raise Dropped("invalid signature")
    try:
        payload = json.loads((raw or b"").decode("utf-8"))
    except Exception:  # noqa: BLE001
        payload = {}

    route = sub.route
    # Where the event type comes from: from a header (X-GitHub-Event) or, when the sender
    # sets none, from the payload itself (`payload:event.type`).
    event = ""
    if sub.event_header:
        if sub.event_header.startswith("payload:"):
            event = str(dig_payload(payload, sub.event_header[len("payload:"):]) or "")
        else:
            event = head.get(sub.event_header.lower(), "")
    if sub.event_filter:
        allowed = [e.strip() for e in sub.event_filter.split(",") if e.strip()]
        if event not in allowed:
            raise Dropped(f"event '{event}' filtered out")

    # Alarm events skip the collection window: they are meant to run through, not to wait for
    # the summary.
    immediate = bool(event) and event in (sub.alert_events or [])
    cooldown = 0 if immediate else (int((sub.event_cooldowns or {}).get(event, 0)) if event else 0)
    if cooldown > 0:
        now = _now()
        ekey = (head.get((sub.event_key_header or "").lower(), "")
                if sub.event_key_header else "") or event
        open_win = (await db.execute(select(WebhookCoalesce).where(
            WebhookCoalesce.route == route, WebhookCoalesce.event_key == ekey,
            WebhookCoalesce.flushed.is_(False), WebhookCoalesce.window_until > now,
        ).with_for_update())).scalars().first()
        if open_win is not None:
            # JSON column: assign a new list, otherwise SQLAlchemy does not detect the change.
            open_win.payloads = [*open_win.payloads, payload]
            raise Dropped(f"collected into the window of '{ekey}'")
        # The first delivery runs through normally but opens the window for follow-up events.
        db.add(WebhookCoalesce(route=route, event_key=ekey,
                               window_until=now + dt.timedelta(seconds=cooldown), payloads=[]))

    ctx = context_of(sub, payload)
    src_ref = reference_of(sub, payload)

    if sub.mode == "event":
        from ..services.events import emit
        name = (sub.event_name or (payload.get("event") if isinstance(payload, dict) else "")
                or f"webhook.{route}")
        ids = await emit(db, str(name), project_id=sub.project_id, payload=ctx,
                         actor_id=sub.owner_user_id, source_ref=src_ref)
        return {"accepted": True, "mode": "event", "event": name, "instances": ids}

    if sub.workflow_definition_id is None:
        raise Retry("webhook without workflow_definition_id")
    definition = await db.get(WorkflowDefinition, sub.workflow_definition_id)
    if definition is None or definition.current_version_id is None:
        # Worth repeating: a flow can be republished, and until then the delivery waits
        # instead of disappearing.
        raise Retry("the flow is missing or not published")
    if src_ref:
        dup = (await db.execute(select(WorkflowInstance).where(
            WorkflowInstance.source == f"webhook:{route}",
            WorkflowInstance.source_ref == src_ref))).scalar_one_or_none()
        if dup is not None:
            raise Dropped(f"already delivered as instance {dup.id}")

    issue_id, asset_id, error = await subject_from_payload(
        db, definition, payload if isinstance(payload, dict) else {}, ctx,
        owner_id=sub.owner_user_id)
    if error:
        # The subject of the run cannot be found, the ticket may only be created a moment
        # later, so this waits too.
        raise Retry(error)
    inst = await start_workflow(
        db, definition, subject_kind=definition.subject_kind, context=ctx,
        issue_id=issue_id, hardware_asset_id=asset_id,
        actor_id=sub.owner_user_id, source=f"webhook:{route}", source_ref=src_ref,
    )
    return {"accepted": True, "mode": "workflow", "instance_id": inst.id,
            "status": inst.status.value,
            **({"issue_id": issue_id} if issue_id else {}),
            **({"hardware_asset_id": asset_id} if asset_id else {})}


async def work_one(db: AsyncSession, row: InboundDelivery) -> str:
    """One delivery, with everything that may go wrong. Returns the new status."""
    row.attempts = (row.attempts or 0) + 1
    sub = (await db.execute(select(WebhookSub).where(
        WebhookSub.public_id == row.target))).scalar_one_or_none()
    if sub is None or not sub.enabled:
        # The route is gone or switched off. Parked and not dropped: the payload is evidence,
        # and whoever switched the route off may want to see what still came in.
        row.status, row.outcome = "parked", "the route no longer exists or is switched off"
        row.finished_at = _now()
        _tell_about(db, row)
        return row.status
    row.route = sub.route

    try:
        result = await deliver(db, sub, bytes(row.body or b""), row.headers or {})
    except Dropped as why:
        row.status, row.outcome, row.finished_at = "dropped", str(why)[:500], _now()
        return row.status
    except Retry as why:
        return _later(db, row, str(why))
    except Exception as exc:  # noqa: BLE001, an unexpected one is a reason to try again
        log.exception("delivery %s failed", row.id)
        return _later(db, row, f"{type(exc).__name__}: {exc}")

    row.status, row.finished_at = "done", _now()
    row.outcome = json.dumps(result, default=str)[:500]
    row.last_error = ""
    return row.status


def _later(db: AsyncSession, row: InboundDelivery, why: str) -> str:
    """Try again, or park it, and say so."""
    row.last_error = why[:2000]
    if row.attempts >= MAX_ATTEMPTS:
        row.status, row.finished_at = "parked", _now()
        row.outcome = f"parked after {row.attempts} attempts"
        _tell_about(db, row)
        return row.status
    row.next_try_at = _now() + dt.timedelta(seconds=BACKOFF[min(row.attempts - 1,
                                                                len(BACKOFF) - 1)])
    row.status = "new"
    return row.status


def _tell_about(db: AsyncSession, row: InboundDelivery) -> None:
    """A parked delivery that nobody sees is the same as a lost one.

    Written in the SAME transaction as the parking itself: two facts that belong together , 
    "this is standing still" and "somebody has been told", must not be able to drift apart.
    """
    from ..models.notification import Notification
    db.add(Notification(
        kind="system",
        title=f"📥 Delivery from '{row.route or row.target}' is stuck",
        body=(f"After {row.attempts} attempts nothing worked: {row.last_error or row.outcome}"
              f"\n\nIt is kept complete and can be repeated by hand (delivery {row.id}).")))


async def drain(db: AsyncSession, *, limit: int = BATCH) -> int:
    """Work off what is due. Returns how many were touched."""
    now = _now()
    rows = (await db.execute(
        select(InboundDelivery)
        .where(InboundDelivery.status == "new",
               (InboundDelivery.next_try_at.is_(None)) | (InboundDelivery.next_try_at <= now))
        .order_by(InboundDelivery.id).limit(limit)
        .with_for_update(skip_locked=True))).scalars().all()
    for row in rows:
        await work_one(db, row)
        # One at a time: a delivery that succeeds must not be rolled back by the next one
        # failing, and the whole point of this table is that a mishap costs at most its own
        # payload.
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("delivery %s could not be written away", row.id)
            await db.rollback()
    return len(rows)


async def run_inbox() -> None:
    """The lane that empties the inbox, for as long as the house is up.

    Deliberately its own loop and not part of the scheduler tick: the scheduler runs on a
    beat measured in minutes, and a webhook that has been taken in should be carried out in
    seconds. On start it picks up whatever was left standing, which is exactly the case this
    whole table exists for.
    """
    await asyncio.sleep(3)
    while True:
        try:
            async with SessionLocal() as db:
                done = await drain(db)
            if done:
                log.info("inbox: %d deliveries worked off", done)
        except Exception:  # noqa: BLE001
            log.exception("inbox pass failed")
        await asyncio.sleep(BEAT)
