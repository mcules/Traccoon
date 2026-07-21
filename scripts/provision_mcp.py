#!/usr/bin/env python3
"""MCPJungle-Provisionierung pro Traccoon-User (Operator-Skript, auf dem Docker-Host).

Für jeden User mit gesetzten `mcp_servers` wird eine MCPJungle-Gruppe
`traccoon-<uid>` + ein mcp-client-Token angelegt und in die DB geschrieben
(`users.mcp_group`, `users.mcp_token_enc`). Der Worker nutzt dann den
owner-eigenen Gruppen-Endpoint — harte serverseitige Tool-Trennung.

Voraussetzung: läuft dort, wo `docker exec mcpjungle` geht (Host), und erreicht
die Traccoon-DB. Konfiguration ausschließlich über die Umgebung (.env):
  PG_DSN                 postgresql://<user>:<pw>@<host>:<port>/<db>
  SECRET_ENCRYPTION_KEY  Traccoon-Fernet-Key
  MCPJUNGLE_GROUPS_DIR   Host-Pfad, der im mcpjungle-Container als /groups gemountet ist
Aufruf:  python3 scripts/provision_mcp.py
"""
import json
import os
import re
import subprocess
import sys

import psycopg2
from cryptography.fernet import Fernet

# Alle Werte aus der Umgebung (.env) — keine Hostnamen/Pfade/Zugangsdaten im Code.
GROUPS_HOST = os.getenv("MCPJUNGLE_GROUPS_DIR", "")  # host → /groups im mcpjungle-Container
PG_DSN = os.getenv("PG_DSN", "")
FERNET_KEY = os.getenv("SECRET_ENCRYPTION_KEY", "")


def mj(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "mcpjungle", "/mcpjungle", *args],
                          capture_output=True, text=True)


def main() -> None:
    if not FERNET_KEY:
        sys.exit("SECRET_ENCRYPTION_KEY fehlt (muss der Traccoon-Key sein).")
    if not PG_DSN:
        sys.exit("PG_DSN fehlt (postgresql://<user>:<pw>@<host>:<port>/<db> in der Umgebung setzen).")
    if not GROUPS_HOST:
        sys.exit("MCPJUNGLE_GROUPS_DIR fehlt (Host-Pfad des /groups-Mounts in der Umgebung setzen).")
    f = Fernet(FERNET_KEY.encode())
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("SELECT id, username, mcp_servers FROM users WHERE mcp_servers IS NOT NULL")
    rows = cur.fetchall()
    for uid, username, servers in rows:
        servers = servers or []
        if not servers:
            continue
        group = f"traccoon-{uid}"
        conf = {"name": group, "description": f"Traccoon-User {username}",
                "included_servers": list(servers)}
        with open(os.path.join(GROUPS_HOST, f"{group}.json"), "w", encoding="utf-8") as fh:
            json.dump(conf, fh)
        r = mj("create", "group", "--conf", f"/groups/{group}.json")
        if "already exists" in (r.stdout + r.stderr):
            mj("update", "group", "--conf", f"/groups/{group}.json")
        mj("delete", "mcp-client", group)   # alten Token wegwerfen (Rotation)
        r = mj("create", "mcp-client", group, "--allow", ",".join(servers))
        out = (r.stdout or "") + (r.stderr or "")   # Token steht auf stderr
        m = re.search(r"Access token:\s*(\S+)", out)
        if not m:
            print(f"! {username}: kein Token erhalten:\n{out[:300]}")
            continue
        token_enc = "enc:v1:" + f.encrypt(m.group(1).encode()).decode()
        cur.execute("UPDATE users SET mcp_group=%s, mcp_token_enc=%s WHERE id=%s",
                    (group, token_enc, uid))
        conn.commit()
        print(f"✓ {username}: group={group} servers={servers}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
