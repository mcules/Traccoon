"""The front door, and nothing else.

This service exists for one reason: while Traccoon is being rebuilt, somebody has to be
standing at the door. Nobody who sends to us tries twice, the archive hook, the house
automation, the tracker and the mail watcher all fire exactly once, so a closed door is lost
information, and no queue inside the house can help with that.

So it does as little as possible, on purpose:

* It knows no secrets. The signature is checked later, by the house, over exactly the bytes
  stored here. A receiver that needs no secret is one that can stand outside without being a
  risk.
* It knows no business logic. It writes a row and answers a receipt. That is what keeps it
  from ever needing a deployment: there is nothing in here that changes when Traccoon does.
* It survives the database. If Postgres is unreachable, the delivery goes into a file beside
  it and is carried over on the next chance. A door that only works while everything else
  does would be no door.

Whoever changes something here should ask first whether it belongs in the house instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request, Response

log = logging.getLogger("ingest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DSN = os.environ["DATABASE_URL"]
# Where a delivery goes when the database cannot take it. Append only, one JSON object per
# line: a format that survives a process being killed in the middle of writing, because a
# half-written last line costs one delivery and not the file.
SPOOL = Path(os.getenv("SPOOL_FILE", "/spool/pending.jsonl"))
# A body bigger than this is not a webhook any more. Without a cap the door is an invitation
# to fill the disk of the house.
MAX_BODY = int(os.getenv("MAX_BODY_BYTES", str(1024 * 1024)))
SKIP_HEADERS = {"authorization", "cookie", "proxy-authorization"}

INSERT = """
    INSERT INTO inbound_deliveries (channel, target, route, body, headers, received_at, status)
    VALUES ($1, $2, $3, $4, $5::jsonb, COALESCE($6, now()), 'new')
    RETURNING id
"""
ROUTE_OF = "SELECT route FROM webhook_subs WHERE public_id = $1 AND enabled"

app = FastAPI(title="Traccoon Ingest", docs_url=None, redoc_url=None, openapi_url=None)
_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool | None:
    """The connection pool, or None while the database is away.

    Deliberately built lazily and never held on to when it breaks: this service has to start
    even when nothing else is up, otherwise it would be down exactly when it is needed.
    """
    global _pool
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4, timeout=5,
                                              command_timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("no database (%s), deliveries go to the spool file", exc)
            return None
    return _pool


def keep_headers(raw) -> dict:
    return {k.lower(): v for k, v in raw.items() if k.lower() not in SKIP_HEADERS}


def spool_write(entry: dict) -> None:
    SPOOL.parent.mkdir(parents=True, exist_ok=True)
    with SPOOL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


async def spool_carry_over() -> int:
    """Take what the file holds into the database, oldest first.

    The file is only removed once every line is in. A line that cannot be read is written
    back: a delivery nobody understands is still not one to throw away.
    """
    if not SPOOL.exists():
        return 0
    p = await pool()
    if p is None:
        return 0
    lines = SPOOL.read_text(encoding="utf-8").splitlines()
    left, moved = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            async with p.acquire() as con:
                await con.fetchval(INSERT, entry["channel"], entry["target"], entry["route"],
                                   bytes.fromhex(entry["body_hex"]), json.dumps(entry["headers"]),
                                   None)
            moved += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("spool line stays put (%s)", exc)
            left.append(line)
    if left:
        SPOOL.write_text("\n".join(left) + "\n", encoding="utf-8")
    else:
        SPOOL.unlink(missing_ok=True)
    if moved:
        log.info("%d delivery/deliveries carried over from the spool", moved)
    return moved


@app.post("/api/hooks/{public_id}")
async def take_in(public_id: str, request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY:
        return Response(json.dumps({"error": "body too large"}), status_code=413,
                        media_type="application/json")
    headers = keep_headers(request.headers)

    p = await pool()
    if p is not None:
        try:
            async with p.acquire() as con:
                route = await con.fetchval(ROUTE_OF, public_id)
                if route is None:
                    # An unknown route is refused here, not stored: otherwise anybody could
                    # fill the table by guessing at addresses.
                    return Response(json.dumps({"error": "unknown route"}), status_code=404,
                                    media_type="application/json")
                did = await con.fetchval(INSERT, "webhook", public_id, route, body,
                                         json.dumps(headers), None)
            return Response(json.dumps({"accepted": True, "delivery_id": did}),
                            status_code=202, media_type="application/json")
        except Exception as exc:  # noqa: BLE001
            log.warning("database write failed (%s), into the spool", exc)
            global _pool
            _pool = None

    # No database. The route cannot be checked, so it is taken in unchecked: a delivery for an
    # address that does not exist is thrown away by the house later, a delivery that was
    # refused here is gone for good.
    spool_write({"channel": "webhook", "target": public_id, "route": "",
                 "body_hex": body.hex(), "headers": headers, "at": time.time()})
    return Response(json.dumps({"accepted": True, "spooled": True}), status_code=202,
                    media_type="application/json")


@app.get("/health")
async def health() -> dict:
    waiting = 0
    if SPOOL.exists():
        waiting = sum(1 for line in SPOOL.read_text(encoding="utf-8").splitlines() if line.strip())
    return {"ok": True, "spooled": waiting}


async def _carry_loop() -> None:
    while True:
        try:
            await spool_carry_over()
        except Exception:  # noqa: BLE001
            log.exception("carrying the spool over failed")
        await asyncio.sleep(20)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(_carry_loop())
