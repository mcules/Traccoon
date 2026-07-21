#!/usr/bin/env python3
"""Traccoon Deployer-Watchdog (Port des predecessor-Musters auf Postgres, pure stdlib + psycopg2).

Pollt die `deployments`-Tabelle. Bewusst getrennt + build-unabhängiges Image, damit ein
Self-Deploy den Deployer nicht mitkillt (sonst kein Rollback). Spricht Postgres DIREKT
(überlebt den Recreate von backend/worker).

Zweigleisig:
  - Self-Deploy (stack_dir == SELF_STACK_DIR): Image :rollback taggen → build+up → HTTP-Health
    → ok | Rollback + failed. finalize: auslösendes Ticket to_test/hold (Race-Closer).
  - Generisches Projekt-Deploy: compose build+up im Projekt-Stack, Container-Health, KEIN Rollback.
  - check-only: nur build, kein up (billiger Verify).
"""
import re
import json
import os
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2

INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
PREVIEW_PORT = int(os.getenv("PREVIEW_SERVER_PORT", "8661"))
# Ressourcendeckel je Preview-Container. Der Host teilt sich viele Stacks —
# eine Testumgebung darf ihn nicht leerlaufen lassen.
PREVIEW_MEMORY = os.getenv("PREVIEW_MEMORY", "2g")
PREVIEW_CPUS = os.getenv("PREVIEW_CPUS", "2")
PREVIEW_PREFIX = "traccoon-preview-"

PG_DSN = os.getenv("PG_DSN", "")               # Zugangsdaten aus der Umgebung (.env/compose)
SELF_STACK_DIR = os.getenv("SELF_STACK_DIR", "")  # Stack-Pfad aus der Umgebung
SELF_SERVICES = os.getenv("SELF_SERVICES", "backend worker frontend").split()
HEALTH_URL = os.getenv("HEALTH_URL", "http://backend:8800/api/health")
POLL = 3


def db():
    c = psycopg2.connect(PG_DSN)
    c.autocommit = True
    return c


def sh(args, cwd=None, timeout=900):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def http_ok(url, tries=30, delay=3):
    import urllib.request
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(delay)
    return False


def _run(sql, params):
    """Kurzlebige, eigene Verbindung je Write — der lange Build/Recreate killt sonst die
    dauerhaft gehaltene Verbindung, und Status/Finalize gehen verloren (building bleibt hängen)."""
    c = db()
    try:
        with c.cursor() as cur:
            cur.execute(sql, params)
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def set_status(conn, dep_id, status, log=""):
    _run("UPDATE deployments SET status=%s, log=left(coalesce(log,'')||%s, 20000), "
         "finished_at=now() WHERE id=%s", (status, "\n" + log[-8000:], dep_id))


def mark_building(conn, dep_id):
    _run("UPDATE deployments SET status='building', started_at=now() WHERE id=%s", (dep_id,))


def finalize_issue(conn, issue_id, ok):
    """Self-Deploy-Race-Closer: der auslösende Run wurde vom Recreate gekillt → hier abschließen."""
    if not issue_id:
        return
    if ok:
        _run("UPDATE issues SET agent_status='to_test', agent_working=false WHERE id=%s "
             "AND agent_status IN ('in_progress','approved')", (issue_id,))
    else:
        _run("UPDATE issues SET agent_status='hold', hold_reason='merge', agent_working=false "
             "WHERE id=%s", (issue_id,))


def add_comment(conn, issue_id, body):
    if not issue_id:
        return
    _run("INSERT INTO comments(issue_id,author_id,author_label,body,kind,created_at,updated_at) "
         "VALUES(%s,NULL,'deployer',%s,'agent',now(),now())", (issue_id, body[:4000]))


def compose(stack_dir, *args, timeout=900):
    return sh(["docker", "compose", *args], cwd=stack_dir, timeout=timeout)


def do_check(conn, dep):
    dep_id, stack_dir, worktree = dep["id"], dep["stack_dir"], dep["worktree"]
    target = worktree or stack_dir
    preview = os.path.join(target, "compose.preview.yml")
    if os.path.isfile(preview):
        rc, out = sh(["docker", "compose", "-f", preview, "-p", f"traccoon-check-{dep_id}", "build"],
                     cwd=target, timeout=900)
    else:
        rc, out = compose(target, "build")
    set_status(conn, dep_id, "ok" if rc == 0 else "failed", out)


def do_self_deploy(conn, dep):
    dep_id, issue_id = dep["id"], dep["issue_id"]
    # Update = neuen Code holen (best effort). Schlägt der Pull fehl (Auth/Netz), wird der
    # aktuelle Stand gebaut — der Deploy bricht deswegen nicht ab.
    pr, pout = sh(["git", "-C", SELF_STACK_DIR, "-c", "safe.directory=*", "pull", "--ff-only"], timeout=120)
    pout = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", pout)  # Token nie loggen
    print(f"[deployer] self-deploy git pull rc={pr}: {pout[-300:]}", flush=True)
    # Der Pull lief als root → .git-Dateien gehoeren sonst root und blockieren den Host-Git.
    # Ownership auf den Repo-Owner zuruecksetzen.
    try:
        stt = os.stat(SELF_STACK_DIR)
        sh(["chown", "-R", f"{stt.st_uid}:{stt.st_gid}", os.path.join(SELF_STACK_DIR, ".git")])
    except Exception:  # noqa: BLE001
        pass
    # laufendes Image sichern
    sh(["docker", "tag", "traccoon-backend:latest", "traccoon-backend:rollback"])
    rc, out = compose(SELF_STACK_DIR, "build", *SELF_SERVICES, timeout=1200)
    if rc != 0:
        set_status(conn, dep_id, "failed", "Build fehlgeschlagen:\n" + out)
        finalize_issue(conn, issue_id, False)
        add_comment(conn, issue_id, "Deploy-Build fehlgeschlagen (kein Recreate).")
        return
    rc, out2 = compose(SELF_STACK_DIR, "up", "-d", *SELF_SERVICES, timeout=300)
    time.sleep(8)
    if http_ok(HEALTH_URL):
        set_status(conn, dep_id, "ok", "Self-Deploy ok\n" + out2[-1500:])
        finalize_issue(conn, issue_id, True)
    else:
        # Rollback
        sh(["docker", "tag", "traccoon-backend:rollback", "traccoon-backend:latest"])
        compose(SELF_STACK_DIR, "up", "-d", *SELF_SERVICES, timeout=300)
        set_status(conn, dep_id, "rolledback", "Health rot → Rollback\n" + out2[-1500:])
        finalize_issue(conn, issue_id, False)
        add_comment(conn, issue_id, "Deploy: Health rot → automatischer Rollback auf Vorversion.")


def do_generic_deploy(conn, dep):
    dep_id, issue_id, stack_dir = dep["id"], dep["issue_id"], dep["stack_dir"]
    rc, out = sh(["docker", "compose", "--project-directory", stack_dir, "build"], timeout=1200)
    if rc != 0:
        set_status(conn, dep_id, "failed", out)
        finalize_issue(conn, issue_id, False)
        return
    rc2, out2 = sh(["docker", "compose", "--project-directory", stack_dir, "up", "-d"], timeout=300)
    time.sleep(6)
    rc3, ps = sh(["docker", "compose", "--project-directory", stack_dir, "ps"], timeout=60)
    ok = rc2 == 0 and "Exit" not in ps and "Restarting" not in ps
    set_status(conn, dep_id, "ok" if ok else "failed", out2 + "\n" + ps)
    finalize_issue(conn, issue_id, ok)


def process(conn, dep):
    try:
        mark_building(conn, dep["id"])
        targets_self = SELF_STACK_DIR and dep["stack_dir"] \
            and os.path.abspath(dep["stack_dir"]) == os.path.abspath(SELF_STACK_DIR)
        if dep["status_prev"] == "pending-check" or dep["check_only"]:
            do_check(conn, dep)
        elif dep.get("self_deploy"):
            # Nur der explizit angeforderte Wartungs-Update recreated den Host-Stack.
            do_self_deploy(conn, dep)
        elif dep["stack_dir"] and not targets_self:
            do_generic_deploy(conn, dep)
        else:
            # Leerer stack_dir ODER self-zielend ohne self_deploy-Flag → NIE implizit
            # den Host recreaten (verhinderte den Self-Deploy-Loop).
            set_status(conn, dep["id"], "failed",
                       "Abgelehnt: Self-Deploy nur über das explizite Wartungs-Update. "
                       "Impliziter Host-Deploy (leerer/self-stack_dir) ist gesperrt.")
            finalize_issue(conn, dep["issue_id"], False)
    except Exception:  # noqa: BLE001
        set_status(conn, dep["id"], "failed", "Deployer-Ausnahme:\n" + traceback.format_exc())


class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: A003
        pass

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if INTERNAL_TOKEN and self.headers.get("X-Traccoon-Internal") != INTERNAL_TOKEN:
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        if self.path == "/preview/up":
            name = body.get("project_name", "")
            cfile = body.get("compose_file", "")
            port = str(body.get("port", ""))
            mode = body.get("mode", "compose")
            workdir = body.get("workdir") or os.path.dirname(cfile)
            env = {**os.environ, "PREVIEW_PORT": port,
                   "PREVIEW_MEMORY": PREVIEW_MEMORY, "PREVIEW_CPUS": PREVIEW_CPUS,
                   **{str(k): str(v) for k, v in (body.get("env") or {}).items()}}
            log_parts = []

            prestart = (body.get("prestart") or "").strip()
            if prestart and os.path.isdir(workdir):
                p = subprocess.run(prestart, shell=True, cwd=workdir, capture_output=True,
                                   text=True, env=env, timeout=600)
                log_parts.append(f"prestart rc={p.returncode}\n{p.stdout}{p.stderr}")
                if p.returncode != 0:
                    self._json(500, {"ok": False, "log": "\n".join(log_parts)[-2000:]})
                    return

            if mode == "dockerfile":
                rc, out = build_and_run_dockerfile(name, workdir, port,
                                                   int(body.get("container_port") or 8080), env)
            elif cfile and os.path.isfile(cfile):
                p = subprocess.run(["docker", "compose", "-p", name, "-f", cfile, "up", "-d", "--build"],
                                   capture_output=True, text=True, env=env, timeout=900)
                rc, out = p.returncode, (p.stdout + p.stderr)
            else:
                rc, out = 1, f"kein compose_file: {cfile}"
            log_parts.append(out)
            if rc == 0:
                log_parts.append(cap_resources(name))
            self._json(200 if rc == 0 else 500,
                       {"ok": rc == 0, "log": "\n".join(log_parts)[-2000:]})
        elif self.path == "/preview/cleanup":
            keep = set(body.get("keep") or [])
            removed = cleanup_orphans(keep)
            self._json(200, {"ok": True, "removed": removed})
        elif self.path == "/preview/down":
            self._json(200, {"ok": teardown(body.get("project_name", ""), body.get("compose_file", ""))})
        else:
            self._json(404, {"error": "not found"})


def build_and_run_dockerfile(name, workdir, host_port, container_port, env):
    """Preview ohne compose.preview.yml: Dockerfile bauen und einzeln starten.

    Der Container bekommt dasselbe compose-Projekt-Label wie im compose-Modus,
    damit Deckel und Aufraeumen ihn genauso finden.
    """
    if not os.path.isfile(os.path.join(workdir, "Dockerfile")):
        return 1, f"kein Dockerfile in {workdir}"
    b = subprocess.run(["docker", "build", "-t", f"{name}:preview", workdir],
                       capture_output=True, text=True, timeout=1800)
    if b.returncode != 0:
        return b.returncode, (b.stdout + b.stderr)
    subprocess.run(["docker", "rm", "-f", f"{name}-app"], capture_output=True, text=True, timeout=60)
    run_env = []
    for k, v in env.items():
        if k.startswith(("PREVIEW_", "PATH", "HOME", "HOSTNAME", "LANG")):
            continue  # Deployer-Interna gehoeren nicht in die Preview
        run_env += ["-e", f"{k}={v}"]
    r = subprocess.run(
        ["docker", "run", "-d", "--name", f"{name}-app",
         "--label", f"com.docker.compose.project={name}",
         "--memory", PREVIEW_MEMORY, "--memory-swap", PREVIEW_MEMORY, "--cpus", PREVIEW_CPUS,
         "-p", f"{host_port}:{container_port}", *run_env, f"{name}:preview"],
        capture_output=True, text=True, timeout=300)
    return r.returncode, (b.stdout[-500:] + r.stdout + r.stderr)


def cap_resources(project_name):
    """Speicher-/CPU-Deckel nachziehen — unabhaengig davon, was die compose.preview.yml sagt."""
    p = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project_name}"],
                       capture_output=True, text=True, timeout=30)
    ids = [x for x in p.stdout.split() if x]
    if not ids:
        return "cap: keine Container gefunden"
    u = subprocess.run(["docker", "update", "--memory", PREVIEW_MEMORY,
                        "--memory-swap", PREVIEW_MEMORY, "--cpus", PREVIEW_CPUS, *ids],
                       capture_output=True, text=True, timeout=60)
    if u.returncode != 0:
        # z. B. Kernel ohne Swap-Limit — kein Grund, die Preview scheitern zu lassen
        return f"cap: nicht gesetzt ({u.stderr.strip()[:200]})"
    return f"cap: {PREVIEW_MEMORY} / {PREVIEW_CPUS} CPU auf {len(ids)} Container"


def teardown(project_name, compose_file):
    """Preview abbauen — compose-Stack UND einzeln gestartete Dockerfile-Container."""
    subprocess.run(["docker", "compose", "-p", project_name,
                    *(["-f", compose_file] if compose_file else []), "down", "-v"],
                   capture_output=True, text=True, timeout=180)
    # Reste per Label einsammeln (Dockerfile-Modus laeuft ohne compose)
    p = subprocess.run(["docker", "ps", "-aq", "--filter",
                        f"label=com.docker.compose.project={project_name}"],
                       capture_output=True, text=True, timeout=30)
    ids = [x for x in p.stdout.split() if x]
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True, timeout=120)
    return True


def cleanup_orphans(keep):
    """Preview-Stacks abraeumen, die das Backend nicht mehr kennt (Neustart, Absturz, Abnahme)."""
    p = subprocess.run(["docker", "ps", "-a", "--format", "{{.Label \"com.docker.compose.project\"}}"],
                       capture_output=True, text=True, timeout=30)
    projects = {x.strip() for x in p.stdout.splitlines()
                if x.strip().startswith(PREVIEW_PREFIX)}
    removed = []
    for proj in sorted(projects - set(keep)):
        teardown(proj, "")
        removed.append(proj)
        print(f"[deployer] verwaiste Preview entfernt: {proj}", flush=True)
    return removed


def _serve_preview():
    srv = ThreadingHTTPServer(("0.0.0.0", PREVIEW_PORT), PreviewHandler)
    print(f"[deployer] preview-server :{PREVIEW_PORT}", flush=True)
    srv.serve_forever()


def main():
    print(f"[deployer] gestartet, poll deployments @ {PG_DSN.split('@')[-1]}", flush=True)
    threading.Thread(target=_serve_preview, daemon=True).start()
    while True:
        try:
            conn = db()
            with conn.cursor() as cur:
                cur.execute("SELECT id, project_id, issue_id, stack_dir, worktree, check_only, status, self_deploy "
                            "FROM deployments WHERE status IN ('pending','pending-check') ORDER BY id LIMIT 1")
                row = cur.fetchone()
            if row:
                dep = {"id": row[0], "project_id": row[1], "issue_id": row[2], "stack_dir": row[3],
                       "worktree": row[4], "check_only": row[5], "status_prev": row[6], "self_deploy": row[7]}
                print(f"[deployer] processing #{dep['id']} ({dep['status_prev']})", flush=True)
                process(conn, dep)
            conn.close()
        except Exception:  # noqa: BLE001
            print("[deployer] loop-Fehler:\n" + traceback.format_exc(), flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
