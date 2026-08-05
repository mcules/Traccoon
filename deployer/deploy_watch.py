#!/usr/bin/env python3
"""Traccoon Deployer-Watchdog (Port des Vorläufer-Musters auf Postgres, pure stdlib + psycopg2).

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
# Namensschema der Testumgebungen: test-<ticket-key> / test-b<id>. Der Altname bleibt in
# den Aufraeum-Filtern, damit noch laufende Alt-Stacks abraeumbar bleiben.
PREVIEW_PREFIXES = ("test-", "traccoon-preview-")
# Gleichzeitige Docker-Builds deckeln — viele parallele Builds bringen den Host in die Knie.
MAX_BUILDS = int(os.getenv("TESTENV_MAX_BUILDS", "2"))
_build_sem = threading.BoundedSemaphore(MAX_BUILDS)
# Wie lange nach dem Start geprueft wird, ob der Einstiegs-Container noch lebt.
LIVENESS_WAIT = int(os.getenv("TESTENV_LIVENESS_WAIT", "6"))

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


def pull_stack(stack_dir):
    """Neuen Stand in den Deploy-Checkout holen — best effort, mit Log-Zeile.

    Der Agent arbeitet in einem eigenen Klon (`<WORKSPACE_ROOT>/<key>`) und pusht dorthin;
    der Stack-Ordner ist ein ZWEITER Checkout desselben Repos. Ohne diesen Pull baut der
    Deploy stur den Stand, der zufaellig im Ordner liegt — der Fehler faellt nicht auf,
    weil der Build gelingt und der Container startet. Er liefert nur alten Code aus.

    Fehlschlaege (kein Repo, kein Netz, keine Auth, nicht vorspulbar) sind KEIN Deploy-Abbruch:
    dann wird der vorhandene Stand gebaut, so wie es der Self-Deploy auch haelt. Was passiert
    ist, steht im Log der Deployment-Zeile.
    """
    if not os.path.isdir(os.path.join(stack_dir, ".git")):
        return "kein Git-Checkout — Pull uebersprungen"
    rc, out = sh(["git", "-C", stack_dir, "-c", "safe.directory=*", "pull", "--ff-only"], timeout=180)
    out = re.sub(r"(x-access-token|[A-Za-z0-9_.-]+):[^@\s]+@", r"\1:***@", out)  # Token nie loggen
    # Der Pull lief als root → .git wuerde sonst root gehoeren und den Host-Git blockieren.
    try:
        stt = os.stat(stack_dir)
        sh(["chown", "-R", f"{stt.st_uid}:{stt.st_gid}", os.path.join(stack_dir, ".git")])
    except Exception:  # noqa: BLE001
        pass
    print(f"[deployer] git pull {stack_dir} rc={rc}: {out[-300:]}", flush=True)
    return f"git pull rc={rc}: {out.strip()[-500:]}"


def do_generic_deploy(conn, dep):
    dep_id, issue_id, stack_dir = dep["id"], dep["issue_id"], dep["stack_dir"]
    pull_log = pull_stack(stack_dir)
    rc, out = sh(["docker", "compose", "--project-directory", stack_dir, "build"], timeout=1200)
    out = pull_log + "\n" + out
    if rc != 0:
        set_status(conn, dep_id, "failed", out)
        finalize_issue(conn, issue_id, False)
        return
    rc2, out2 = sh(["docker", "compose", "--project-directory", stack_dir, "up", "-d"], timeout=300)
    time.sleep(6)
    rc3, ps = sh(["docker", "compose", "--project-directory", stack_dir, "ps"], timeout=60)
    ok = rc2 == 0 and "Exit" not in ps and "Restarting" not in ps
    # Der Pull gehoert auch in den Erfolgsfall: „Already up to date" statt „Fast-forward"
    # ist der Unterschied zwischen „nichts Neues" und „falscher Ordner" — und genau den
    # will man sehen koennen, ohne im Deployer-Log zu graben.
    set_status(conn, dep_id, "ok" if ok else "failed", pull_log + "\n" + out2 + "\n" + ps)
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
            ok, log = preview_up(body)
            self._json(200 if ok else 500, {"ok": ok, "log": log})
        elif self.path == "/preview/cleanup":
            keep = set(body.get("keep") or [])
            removed = cleanup_orphans(keep)
            self._json(200, {"ok": True, "removed": removed})
        elif self.path == "/preview/down":
            self._json(200, {"ok": teardown(body.get("project_name", ""), body.get("compose_file", ""))})
        elif self.path == "/preview/logs":
            self._json(200, preview_logs(body.get("project_name", ""), body.get("service"),
                                         int(body.get("tail") or 200)))
        elif self.path == "/preview/list":
            self._json(200, {"stacks": preview_list()})
        else:
            self._json(404, {"error": "not found"})


def _run_prestart(prestart, workdir, env, log_parts):
    """Prestart-Befehle: eine Zeile = ein Befehl, `#`-Kommentare werden übersprungen.
    Der erste Fehlschlag bricht ab — dann wird gar nicht erst gebaut."""
    for line in (prestart or "").splitlines():
        cmd = line.strip()
        if not cmd or cmd.startswith("#"):
            continue
        p = subprocess.run(cmd, shell=True, cwd=workdir, capture_output=True,
                           text=True, env=env, timeout=600)
        log_parts.append(f"$ {cmd}\nrc={p.returncode}\n{p.stdout}{p.stderr}")
        if p.returncode != 0:
            return False
    return True


def _ensure_branch_worktree(repo_dir, workdir, branch, log_parts):
    """Worktree für einen beliebigen Branch anlegen bzw. auf dessen aktuellen Stand bringen.
    Die Umgebung baut IMMER den Branch-Stand, nie den geteilten Integrations-Checkout."""
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        log_parts.append(f"kein Repo unter {repo_dir}")
        return False
    subprocess.run(["git", "-C", repo_dir, "fetch", "--all", "--prune"],
                   capture_output=True, text=True, timeout=300)
    if os.path.isdir(workdir):
        r = subprocess.run(["git", "-C", workdir, "reset", "--hard", f"origin/{branch}"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            r = subprocess.run(["git", "-C", workdir, "reset", "--hard", branch],
                               capture_output=True, text=True, timeout=120)
        log_parts.append(f"worktree refresh rc={r.returncode}\n{r.stdout}{r.stderr}")
        return r.returncode == 0
    os.makedirs(os.path.dirname(workdir), exist_ok=True)
    r = subprocess.run(["git", "-C", repo_dir, "worktree", "add", "--detach", workdir, branch],
                       capture_output=True, text=True, timeout=300)
    log_parts.append(f"worktree add rc={r.returncode}\n{r.stdout}{r.stderr}")
    return r.returncode == 0


def _write_env_file(workdir, env_vars, log_parts):
    """Injizierte Werte als .env in den Worktree schreiben — nur so sehen `docker compose`
    und ein `COPY`-Build sie; reine Prozess-Env reicht dafür nicht."""
    try:
        with open(os.path.join(workdir, ".env"), "w", encoding="utf-8") as fh:
            for k, v in env_vars.items():
                fh.write(f"{k}={v}\n")
        return True
    except OSError as exc:
        log_parts.append(f".env konnte nicht geschrieben werden: {exc}")
        return False


def _connect_sidecars(name, sidecars, log_parts):
    """Projekt-Services (z. B. ein zentraler Proxy) ins Testenv-Netz hängen.
    Fehler sind tolerant: ein fehlender Sidecar darf die Umgebung nicht killen."""
    net = f"{name}_default"
    for sc in sidecars or []:
        container, alias = sc.get("container"), sc.get("alias") or sc.get("container")
        if not container:
            continue
        r = subprocess.run(["docker", "network", "connect", "--alias", alias, net, container],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            log_parts.append(f"sidecar {container} nicht verbunden: {r.stderr.strip()[:200]}")


def _alive(name):
    """Läuft nach dem Start noch mindestens ein Container des Stacks?"""
    p = subprocess.run(["docker", "ps", "-a", "--filter",
                        f"label=com.docker.compose.project={name}", "--format", "{{.Status}}"],
                       capture_output=True, text=True, timeout=30)
    states = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    if not states:
        return False, "kein Container gestartet"
    dead = [s for s in states if s.startswith(("Exited", "Restarting", "Dead"))]
    if len(dead) == len(states):
        return False, "Container sofort beendet: " + "; ".join(dead[:5])
    return True, ""


def preview_up(body):
    """Testumgebung starten: Worktree → .env → Prestart → Build/Start → Sidecars → Liveness."""
    name = body.get("project_name", "")
    cfile = body.get("compose_file", "")
    port = str(body.get("port", ""))
    mode = body.get("mode", "compose")
    workdir = body.get("workdir") or os.path.dirname(cfile)
    mem = str(body.get("mem_limit") or PREVIEW_MEMORY)
    cpus = str(body.get("cpus") or PREVIEW_CPUS)
    injected = {str(k): str(v) for k, v in (body.get("env") or {}).items()}
    env = {**os.environ, "PREVIEW_PORT": port, "PREVIEW_MEMORY": mem, "PREVIEW_CPUS": cpus,
           **injected}
    log_parts = []

    # Branch-Testumgebung: Worktree für den gewünschten Branch bereitstellen.
    if body.get("branch"):
        if not _ensure_branch_worktree(body.get("repo_dir", ""), workdir, body["branch"], log_parts):
            return False, "\n".join(log_parts)[-4000:]

    if not os.path.isdir(workdir):
        return False, f"Arbeitsverzeichnis fehlt: {workdir}"

    _write_env_file(workdir, injected, log_parts)

    if not _run_prestart(body.get("prestart"), workdir, env, log_parts):
        return False, "\n".join(log_parts)[-4000:]

    with _build_sem:  # Build-Concurrency deckeln
        if mode == "dockerfile":
            rc, out = build_and_run_dockerfile(
                name, workdir, port, int(body.get("container_port") or 8080), env,
                dockerfile=body.get("dockerfile") or "Dockerfile", mem=mem, cpus=cpus)
        elif cfile and os.path.isfile(cfile):
            p = subprocess.run(["docker", "compose", "-p", name, "-f", cfile, "up", "-d", "--build"],
                               capture_output=True, text=True, env=env, cwd=workdir, timeout=1800)
            rc, out = p.returncode, (p.stdout + p.stderr)
        else:
            rc, out = 1, f"kein compose_file: {cfile}"
    log_parts.append(out)
    if rc != 0:
        teardown(name, cfile)
        return False, "\n".join(log_parts)[-4000:]

    _connect_sidecars(name, body.get("sidecars"), log_parts)
    log_parts.append(cap_resources(name, mem, cpus))

    # Nachprüfung: kein „grün", das sofort tot ist.
    time.sleep(LIVENESS_WAIT)
    alive, why = _alive(name)
    if not alive:
        log_parts.append("Liveness-Prüfung fehlgeschlagen: " + why)
        log_parts.append(preview_logs(name, None, 100).get("log", ""))
        teardown(name, cfile)
        return False, "\n".join(log_parts)[-4000:]
    return True, "\n".join(log_parts)[-4000:]


def preview_logs(project_name, service, tail):
    """Logs eines Stacks (compose) bzw. des Einzel-Containers (Dockerfile-Modus)."""
    tail = max(1, min(int(tail or 200), 2000))
    cmd = ["docker", "compose", "-p", project_name, "logs", "--no-color", "--tail", str(tail)]
    if service:
        cmd.append(service)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    if p.returncode != 0 or not out.strip():
        q = subprocess.run(["docker", "logs", "--tail", str(tail), f"{project_name}-app"],
                           capture_output=True, text=True, timeout=120)
        out = q.stdout + q.stderr
    return {"project_name": project_name, "service": service, "log": out[-200000:]}


def preview_list():
    """Laufende Testumgebungen, gruppiert nach Compose-Projekt."""
    fmt = '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.Names}}\t{{.Status}}'
    p = subprocess.run(["docker", "ps", "-a", "--format", fmt],
                       capture_output=True, text=True, timeout=30)
    stacks = {}
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        proj, svc, cname, status = (x.strip() for x in parts[:4])
        if not proj.startswith(PREVIEW_PREFIXES):
            continue
        stacks.setdefault(proj, {"project_name": proj, "services": []})["services"].append(
            {"service": svc or cname, "container": cname, "status": status})
    return list(stacks.values())


def build_and_run_dockerfile(name, workdir, host_port, container_port, env,
                             dockerfile="Dockerfile", mem=None, cpus=None):
    """Preview ohne compose.preview.yml: Dockerfile bauen und einzeln starten.

    Der Container bekommt dasselbe compose-Projekt-Label wie im compose-Modus,
    damit Deckel und Aufraeumen ihn genauso finden.
    """
    mem = mem or PREVIEW_MEMORY
    cpus = cpus or PREVIEW_CPUS
    if not os.path.isfile(os.path.join(workdir, dockerfile)):
        return 1, f"kein {dockerfile} in {workdir}"
    b = subprocess.run(["docker", "build", "-f", os.path.join(workdir, dockerfile),
                        "-t", f"{name}:preview", workdir],
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
         "--memory", mem, "--memory-swap", mem, "--cpus", cpus,
         "-p", f"{host_port}:{container_port}", *run_env, f"{name}:preview"],
        capture_output=True, text=True, timeout=300)
    return r.returncode, (b.stdout[-500:] + r.stdout + r.stderr)


def cap_resources(project_name, mem=None, cpus=None):
    """Speicher-/CPU-Deckel nachziehen — unabhaengig davon, was die compose.preview.yml sagt."""
    p = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project_name}"],
                       capture_output=True, text=True, timeout=30)
    ids = [x for x in p.stdout.split() if x]
    if not ids:
        return "cap: keine Container gefunden"
    mem = mem or PREVIEW_MEMORY
    cpus = cpus or PREVIEW_CPUS
    u = subprocess.run(["docker", "update", "--memory", mem,
                        "--memory-swap", mem, "--cpus", cpus, *ids],
                       capture_output=True, text=True, timeout=60)
    if u.returncode != 0:
        # z. B. Kernel ohne Swap-Limit — kein Grund, die Preview scheitern zu lassen
        return f"cap: nicht gesetzt ({u.stderr.strip()[:200]})"
    return f"cap: {mem} / {cpus} CPU auf {len(ids)} Container"


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
    # Das gebaute Preview-Image mitnehmen. `down -v` raeumt Container und Volumes, aber
    # nie Images — und ein Preview-Image ist kein Kleinkram: eine Node-App mit Vite-Build
    # liegt bei ~600 MB. Wer taeglich Tickets testet, fuellt damit stillschweigend die
    # Platte. Die Images heissen <projectname>-<service>; per Label eingesammelt, damit
    # kein fremdes Image erwischt wird.
    q = subprocess.run(["docker", "images", "-q", "--filter",
                        f"label=com.docker.compose.project={project_name}"],
                       capture_output=True, text=True, timeout=30)
    imgs = sorted({x for x in q.stdout.split() if x})
    if imgs:
        subprocess.run(["docker", "rmi", "-f", *imgs], capture_output=True, text=True, timeout=180)
    return True


def cleanup_orphans(keep):
    """Preview-Stacks abraeumen, die das Backend nicht mehr kennt (Neustart, Absturz, Abnahme)."""
    p = subprocess.run(["docker", "ps", "-a", "--format", "{{.Label \"com.docker.compose.project\"}}"],
                       capture_output=True, text=True, timeout=30)
    projects = {x.strip() for x in p.stdout.splitlines()
                if x.strip().startswith(PREVIEW_PREFIXES)}
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
