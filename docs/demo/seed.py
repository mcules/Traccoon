#!/usr/bin/env python3
"""Fill a demo Traccoon with fictional data — for the pictures in the README.

Everybody in here is invented (Ada, Grace, Alan), every address ends in example.org, and the
coordinates are the Brandenburg Gate. Run it against the demo stack from `docs/demo/compose.yml`,
never against a real one: it creates projects, tickets and series and assumes an empty house.

    python3 docs/demo/seed.py            # against http://localhost:8089
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8089/api"
EMAIL, PASSWORD = "ada@example.org", "demo-demo-demo"
TOKEN = ""


def call(method: str, path: str, body: dict | list | None = None, quiet: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if quiet:
            return None
        print(f"  ! {method} {path} -> {e.code} {e.read()[:200].decode(errors='replace')}")
        return None


def main() -> None:
    global TOKEN
    TOKEN = (call("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD}) or {}).get("access_token", "")
    if not TOKEN:
        raise SystemExit("Login failed — is the demo stack up and seeded with BOOTSTRAP_ADMIN_*?")
    print("logged in as ada")

    call("POST", "/agents/seed-defaults", {}, quiet=True)

    # ── Two projects: one with software, one with sensors ────────────────────
    web = call("POST", "/projects", {"name": "Acme Website",
                                     "description": "Marketing site and shop of a made-up company.",
                                     "managed": True})
    sensors = call("POST", "/projects", {"name": "Sensor Network",
                                         "description": "Temperature and battery of the warehouse sensors."})
    if not web:
        raise SystemExit("no project — is the house already full?")
    print(f"projects: {web['key']}, {sensors['key'] if sensors else '-'}")

    meta = call("GET", f"/projects/{web['id']}/meta") or {}
    by_name = {s["name"].lower(): s["id"] for s in meta.get("statuses", [])}
    todo = by_name.get("to do")
    doing = by_name.get("in progress", todo)
    testing = by_name.get("testing", doing)

    tickets = [
        ("Checkout drops the voucher on the last step", doing,
         "Reproducible with two items in the basket: the voucher is applied, the order confirmation "
         "shows the full price. Suspicion is the session, not the price calculation."),
        ("Product images load twice on mobile", todo,
         "The srcset is right, the lazy loader fetches the fallback anyway."),
        ("Nightly export to the warehouse system", testing,
         "Runs, but writes an empty file when the shop had no order that day."),
        ("Cookie banner blocks the search on iOS", todo,
         "Only in Safari, only in portrait mode."),
        ("Search: umlauts find nothing", doing,
         "\"Grüntee\" finds nothing, \"Gruentee\" does. The index normalises, the query does not."),
    ]
    for summary, status, text in tickets:
        call("POST", f"/projects/{web['id']}/issues",
             {"summary": summary, "description": text, "status_id": status})
    print(f"{len(tickets)} tickets")

    # ── Data series: numbers and a location ──────────────────────────────────
    call("POST", "/series", {"key": "warehouse.temperature", "kind": "number", "name": "Warehouse temperature",
                             "description": "Every ten minutes from the sensor above the ramp.",
                             "settings": {"unit": "°C"}})
    call("POST", "/series", {"key": "van.delivery", "kind": "location", "name": "Delivery van",
                             "color": "#38bdf8", "description": "Reports through the ingest path."})
    call("POST", "/series", {"key": "weekly.review", "kind": "text", "name": "Weekly review",
                             "description": "What the agents worked out, week by week."})
    print("series: warehouse.temperature, van.delivery, weekly.review")

    # ── Points, so the charts and the map have something to show ─────────────
    import datetime as dt
    import math

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    temp_token = (call("POST", "/series/warehouse.temperature/token", {}) or {}).get("token")
    van_token = (call("POST", "/series/van.delivery/token", {}) or {}).get("token")
    if temp_token:
        batch = [{"ts": (now - dt.timedelta(hours=h)).isoformat(),
                  "value": round(7.5 + 2.4 * math.sin(h / 3.4) - h * 0.012, 2)}
                 for h in range(72, -1, -1)]
        for point in batch:
            call("POST", f"/ingest/{temp_token}", point, quiet=True)
        print(f"{len(batch)} measurements")
    if van_token:
        # A short round through Berlin-Mitte, one point every four minutes.
        lat, lon = 52.5163, 13.3777
        route = []
        for i in range(40):
            lat += 0.0016 * math.cos(i / 6.0)
            lon += 0.0024 * math.sin(i / 5.0)
            route.append({"ts": (now - dt.timedelta(minutes=4 * (40 - i))).isoformat(),
                          "lat": round(lat, 6), "lon": round(lon, 6),
                          "accuracy": 8 + (i % 5), "battery": max(0.35, 0.92 - i * 0.012)})
        for point in route:
            call("POST", f"/ingest/{van_token}", point, quiet=True)
        print(f"{len(route)} positions")

    # ── A scheduled flow, so the job list is not empty ───────────────────────
    flows = call("GET", "/workflows") or []
    daily = next((w for w in flows if w.get("key") == "daily-review"), None)
    if not daily:
        daily = call("POST", "/workflows", {"name": "Daily review", "key": "daily-review",
                                            "subject_kind": "standalone",
                                            "template": "assistant"}, quiet=True)
    call("POST", "/jobs", {"name": "Daily review", "type": "cron", "schedule": "0 18 * * 1-5",
                           "kind": "workflow", "workflow_key": "daily-review", "enabled": True}, quiet=True)
    print("job: daily review")


if __name__ == "__main__":
    main()
