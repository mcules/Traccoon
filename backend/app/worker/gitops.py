"""Git-Worktree-Engine (Port aus dem Vorläufer (gitops.py), self-contained).

Worktree pro Ticket unter <WORKSPACE_ROOT>/.traccoon-worktrees/<key>/<issue-key>.
Token kommt vom Aufrufer (Secret-Tresor) und wird nur in die ephemere Push-URL
injiziert, nie in origin. `git -c safe.directory=*` (Container = root, Repo = Host-User).
Alle Aufrufe weich — der Ticket-Lifecycle hängt nie an git.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

log = logging.getLogger("traccoon.gitops")

GIT_AUTHOR_NAME = os.getenv("GIT_AUTHOR_NAME", "Traccoon")
GIT_AUTHOR_EMAIL = os.getenv("GIT_AUTHOR_EMAIL", "traccoon@local")
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace")
MAIN = "main"


@dataclass
class GitCtx:
    workdir: str                       # Haupt-Repo-Checkout
    branch: str                        # Ticket-Branch
    remote: str = ""                   # HTTPS-URL ohne Token
    token: str = ""                    # aufgelöster Push-Token (userinfo)
    worktree: str | None = None        # Worktree-Pfad (worktree_per_task)
    base_commit: str | None = None
    main: str = MAIN
    enabled: bool = True


def issue_branch(issue_key: str) -> str:
    return f"traccoon/issue-{issue_key}"


def project_workdir(project_key: str) -> str:
    return f"{WORKSPACE_ROOT}/{project_key.lower()}"


def worktree_path(project_key: str, issue_key: str) -> str:
    return f"{WORKSPACE_ROOT}/.traccoon-worktrees/{project_key.lower()}/{issue_key}"


def _authed_url(remote: str, token: str) -> str:
    parts = urlsplit(remote)
    if parts.scheme not in ("http", "https") or not parts.hostname or not token:
        return remote
    userinfo = token if ":" in token else f"x-access-token:{token}"
    netloc = f"{userinfo}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _redact(text: str, token: str) -> str:
    if token:
        secret = token.split(":")[-1]
        if secret:
            text = text.replace(secret, "***")
    return text


async def _git(workdir: str, *args: str) -> tuple[int, str]:
    env = {**os.environ,
           "GIT_AUTHOR_NAME": GIT_AUTHOR_NAME, "GIT_AUTHOR_EMAIL": GIT_AUTHOR_EMAIL,
           "GIT_COMMITTER_NAME": GIT_AUTHOR_NAME, "GIT_COMMITTER_EMAIL": GIT_AUTHOR_EMAIL,
           "GIT_TERMINAL_PROMPT": "0"}
    try:
        p = await asyncio.create_subprocess_exec(
            "git", "-c", "safe.directory=*", "-C", workdir, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env)
        out, _ = await p.communicate()
        return p.returncode or 0, out.decode("utf-8", "replace").strip()
    except FileNotFoundError:
        return 127, "git nicht installiert"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


async def _is_repo(workdir: str) -> bool:
    rc, _ = await _git(workdir, "rev-parse", "--git-dir")
    return rc == 0


async def _head(workdir: str) -> str | None:
    rc, out = await _git(workdir, "rev-parse", "HEAD")
    return out.strip() if rc == 0 else None


async def _ensure_repo(ctx: GitCtx) -> bool:
    os.makedirs(ctx.workdir, exist_ok=True)
    if not await _is_repo(ctx.workdir):
        if ctx.remote:
            rc, out = await _git(os.path.dirname(ctx.workdir), "clone",
                                 _authed_url(ctx.remote, ctx.token), os.path.basename(ctx.workdir))
            if rc != 0:
                log.warning("git clone fehlgeschlagen: %s", _redact(out, ctx.token))
                # Fallback: leeres Repo
                await _git(ctx.workdir, "init", "-b", ctx.main)
        else:
            rc, out = await _git(ctx.workdir, "init", "-b", ctx.main)
            if rc != 0:
                return False
    await _git(ctx.workdir, "config", "user.name", GIT_AUTHOR_NAME)
    await _git(ctx.workdir, "config", "user.email", GIT_AUTHOR_EMAIL)
    if ctx.remote:
        rc, _ = await _git(ctx.workdir, "remote", "get-url", "origin")
        await _git(ctx.workdir, "remote", "set-url" if rc == 0 else "add", "origin", ctx.remote)
    return True


async def _push(ctx: GitCtx, workdir: str, branch: str) -> bool:
    """True bei Erfolg (oder ohne Remote — nichts zu tun); False bei Push-Fehler."""
    if not ctx.remote:
        return True
    rc, out = await _git(workdir, "push", _authed_url(ctx.remote, ctx.token), f"{branch}:{branch}")
    if rc != 0:
        log.warning("git push (%s): %s", branch, _redact(out, ctx.token))
        return False
    return True


async def diff_text(ctx: GitCtx, max_chars: int = 20000) -> str:
    """Kumulativer Diff des Ticket-Stands gegen die Abzweig-Basis (für Review-Gate).

    Wird gekappt, dann an einer Zeilengrenze und mit Ansage. Der stumme Schnitt mitten im
    Wort hat sich am 2026-08-07 gerächt: der Prüfer sah bei ABC-32 einen compose-Block, der
    „mittendrin abbricht (`v` als letzte Zeile)", und meldete das als unvollständige
    Service-Definition — ein Befund über den Boten, nicht über den Code. Ein Prüfer, der
    nicht weiß, dass ihm etwas vorenthalten wird, erfindet Erklärungen dafür.
    """
    wd = ctx.worktree or ctx.workdir
    if not await _is_repo(wd):
        return ""
    base = ctx.base_commit
    if not base:
        # Nicht `ctx.main` als Basis nehmen: der Branch hängt an einem ÄLTEREN Stand, und ein
        # Zwei-Punkt-Diff gegen den heutigen main zeigt dessen Fortschritt als „gelöscht" —
        # der Prüfer bekäme fremde Änderungen als Befund vorgesetzt.
        rc, mb = await _git(wd, "merge-base", ctx.main, "HEAD")
        base = mb.strip() if rc == 0 and mb.strip() else ctx.main
    # Zwei Punkte, nicht drei: `base...HEAD` zeigt nur COMMITTETE Arbeit. Im Review-Gate ist
    # die Korrektur aber noch uncommittet (committet wird erst nach dem Gate) — der Prüfer
    # sah damit den Stand VOR seinen eigenen Befunden, und die Stillstands-Erkennung fand
    # zwangsläufig „nichts verändert". Beide Tickets vom 2026-08-07 endeten so nach genau
    # einer Korrekturrunde, obwohl die Korrektur längst geschrieben war.
    rc, out = await _git(wd, "diff", base)
    if rc != 0:
        rc, out = await _git(wd, "diff", "HEAD")  # letzter Rückfall: nur der Arbeitsstand
    if len(out) <= max_chars:
        return out
    kopf = out[:max_chars]
    kopf = kopf[:kopf.rfind("\n") + 1] or kopf      # nie mitten in einer Zeile enden
    fehlt = sorted({z.split(" b/", 1)[1].strip() for z in out[len(kopf):].splitlines()
                    if z.startswith("diff --git ") and " b/" in z})
    hinweis = (f"\n[... Diff gekappt: {len(kopf)} von {len(out)} Zeichen gezeigt. Der Rest "
               "fehlt hier — das ist eine Grenze der Anzeige, KEIN unvollständiger Code.")
    if fehlt:
        hinweis += (" Nicht enthaltene Dateien (bei Bedarf mit `fs_read` selbst ansehen): "
                    + ", ".join(fehlt[:25]) + (", …" if len(fehlt) > 25 else ""))
    return kopf + hinweis + "]\n"


async def file_changes(ctx: GitCtx) -> list[dict]:
    """Geänderte Dateien seit der Abzweig-Basis: [{path, status, additions, deletions}]."""
    wd = ctx.worktree or ctx.workdir
    if not await _is_repo(wd):
        return []
    base = ctx.base_commit or ctx.main
    rc, num = await _git(wd, "diff", "--numstat", f"{base}...HEAD")
    if rc != 0 or not num.strip():
        rc, num = await _git(wd, "diff", "--numstat", "HEAD")
    rc2, names = await _git(wd, "diff", "--name-status", f"{base}...HEAD")
    status_map = {}
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            st = {"A": "added", "M": "modified", "D": "deleted"}.get(parts[0][0], "modified")
            status_map[parts[-1]] = st
    out = []
    for line in num.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add = int(parts[0]) if parts[0].isdigit() else 0
            dele = int(parts[1]) if parts[1].isdigit() else 0
            path = parts[2]
            out.append({"path": path, "status": status_map.get(path, "modified"),
                        "additions": add, "deletions": dele})
    return out


async def worktree_fingerprint(wt: str | None) -> str:
    if not wt or not await _is_repo(wt):
        return "no-repo"
    _, head = await _git(wt, "rev-parse", "HEAD")
    _, diff = await _git(wt, "diff", "HEAD")
    return hashlib.sha256(f"{head.strip()}|{diff}".encode("utf-8", "replace")).hexdigest()[:16]


async def refresh_main(ctx: GitCtx) -> str:
    """origin holen und main fast-forwarden — sonst zweigen Worktrees von veraltetem Stand ab.

    Nur --ff-only: lokale Abweichungen auf main werden nie überschrieben.
    """
    if not ctx.enabled or not ctx.remote:
        return "kein Remote"
    rc, out = await _git(ctx.workdir, "fetch", _authed_url(ctx.remote, ctx.token), ctx.main)
    if rc != 0:
        return f"fetch fehlgeschlagen: {_redact(out, ctx.token)}"
    # Nur wenn main auch ausgecheckt ist (Worktrees haben ihre eigenen Branches).
    rc, cur = await _git(ctx.workdir, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or cur.strip() != ctx.main:
        return "main nicht ausgecheckt — nur FETCH_HEAD aktualisiert"
    rc, out = await _git(ctx.workdir, "merge", "--ff-only", "FETCH_HEAD")
    if rc != 0:
        log.info("main nicht fast-forwardbar (%s): %s", ctx.main, out.strip()[:200])
        return "main abgewichen — kein Fast-Forward"
    return "main aktualisiert"


async def prepare(ctx: GitCtx) -> str:
    """Repo sicherstellen + Worktree anlegen. Setzt ctx.worktree/base_commit."""
    if not ctx.enabled:
        return "git aus"
    if not await _ensure_repo(ctx):
        return "git: Repo-Init fehlgeschlagen"
    await refresh_main(ctx)
    if ctx.worktree:
        os.makedirs(os.path.dirname(ctx.worktree), exist_ok=True)
        # Verwaiste Registrierungen (Verzeichnis gelöscht) entfernen, damit unten kein
        # leerer/fehlender Worktree fälschlich „wiederverwendet" wird.
        await _git(ctx.workdir, "worktree", "prune")
        rc, out = await _git(ctx.workdir, "worktree", "list", "--porcelain")
        registered = rc == 0 and ctx.worktree in out
        # Nur wiederverwenden, wenn das Verzeichnis existiert, ein echtes Working-Tree ist
        # UND ausgecheckte Dateien enthält (sonst ist es eine Leiche → frisch anlegen).
        if registered and os.path.isdir(ctx.worktree) and await _is_repo(ctx.worktree):
            rc_f, files = await _git(ctx.worktree, "ls-files")
            if rc_f == 0 and files.strip():
                ctx.base_commit = await _head(ctx.workdir)
                return f"git: Worktree wiederverwendet ({ctx.branch})"
        # Kaputte/leere Registrierung sauber entfernen, bevor wir neu anlegen.
        if registered:
            await _git(ctx.workdir, "worktree", "remove", "--force", ctx.worktree)
            await _git(ctx.workdir, "worktree", "prune")
        rc_b, _ = await _git(ctx.workdir, "rev-parse", "--verify", ctx.branch)
        if rc_b == 0:
            rc, out = await _git(ctx.workdir, "worktree", "add", ctx.worktree, ctx.branch)
        else:
            rc, out = await _git(ctx.workdir, "worktree", "add", "-b", ctx.branch, ctx.worktree, ctx.main)
        if rc != 0:
            return f"git: worktree add fehlgeschlagen: {out}"
        ctx.base_commit = await _head(ctx.workdir)
        return f"git: Worktree {ctx.worktree} ({ctx.branch})"
    return "git: bereit (kein Worktree)"


async def ensure_branch(ctx: GitCtx, branch: str, base: str) -> str:
    """Stellt sicher, dass `branch` existiert (lokal, ggf. vom Remote), abgezweigt von `base`.
    Für Sammelticket-Branches, von denen Sub-Tickets abzweigen und in die sie mergen."""
    if not ctx.enabled or not await _ensure_repo(ctx):
        return "git: kein Repo"
    await refresh_main(ctx)  # base (= merge_target) aktualisieren
    rc, _ = await _git(ctx.workdir, "rev-parse", "--verify", f"refs/heads/{branch}")
    if rc == 0:
        return f"git: Branch {branch} vorhanden"
    # Evtl. hat ein früherer Lauf ihn schon auf dem Remote angelegt.
    if ctx.remote:
        rc_f, _ = await _git(ctx.workdir, "fetch", _authed_url(ctx.remote, ctx.token), branch)
        if rc_f == 0 and (await _git(ctx.workdir, "rev-parse", "--verify", "FETCH_HEAD"))[0] == 0:
            await _git(ctx.workdir, "branch", branch, "FETCH_HEAD")
            return f"git: Branch {branch} vom Remote geholt"
    # Neu von base abzweigen (base existiert nach refresh_main; sonst HEAD).
    rc_b, _ = await _git(ctx.workdir, "rev-parse", "--verify", base)
    base_ref = base if rc_b == 0 else "HEAD"
    rc, out = await _git(ctx.workdir, "branch", branch, base_ref)
    if rc != 0:
        return f"git: Branch {branch} anlegen fehlgeschlagen: {out[:200]}"
    await _push(ctx, ctx.workdir, branch)
    return f"git: Branch {branch} angelegt (von {base_ref})"


async def commit(ctx: GitCtx, message: str) -> str:
    if not ctx.enabled:
        return "git aus"
    wd = ctx.worktree or ctx.workdir
    if not await _is_repo(wd):
        return "git: kein Repo"
    await _git(wd, "add", "-A")
    rc, _ = await _git(wd, "diff", "--cached", "--quiet")
    if rc == 0:
        return "git: nichts zu committen"
    rc, out = await _git(wd, "commit", "-m", message)
    if rc != 0:
        return f"git: commit fehlgeschlagen: {out[:200]}"
    await _push(ctx, wd, ctx.branch)
    return f"git: committed ({ctx.branch})"


@dataclass
class PremergeResult:
    clean: bool = True
    conflict: bool = False
    conflict_files: list[str] = field(default_factory=list)
    new_commits: int = 0
    base_changed: bool = False


async def precheck_merge(ctx: GitCtx) -> PremergeResult | None:
    """main-HEAD vs base_commit; Merge main→Worktree (Worktree bleibt, main sauber)."""
    if not ctx.enabled or not ctx.worktree or not await _is_repo(ctx.worktree):
        return None
    if ctx.remote:
        await _git(ctx.worktree, "fetch", _authed_url(ctx.remote, ctx.token), ctx.main)
        rc, main_head = await _git(ctx.worktree, "rev-parse", "FETCH_HEAD")
        if rc != 0:
            rc, main_head = await _git(ctx.worktree, "rev-parse", ctx.main)
    else:
        rc, main_head = await _git(ctx.worktree, "rev-parse", ctx.main)
    if rc != 0:
        return None
    main_head = main_head.strip()
    if ctx.base_commit and main_head == ctx.base_commit.strip():
        return PremergeResult(clean=True, base_changed=False)
    ref = "FETCH_HEAD" if ctx.remote else ctx.main
    rc_m, out = await _git(ctx.worktree, "merge", "--no-ff", "-m",
                           f"Merge {ctx.main} into {ctx.branch} (pre-merge gate)", ref)
    if rc_m != 0:
        await _git(ctx.worktree, "merge", "--abort")
        cfiles = [l.split()[-1] for l in out.splitlines() if "CONFLICT" in l]
        return PremergeResult(clean=False, conflict=True, conflict_files=cfiles, base_changed=True)
    return PremergeResult(clean=True, base_changed=True)


async def setup_conflict_resolution(ctx: GitCtx) -> list[str] | None:
    """Konflikt an den Agenten: main in Worktree mergen, Marker STEHEN LASSEN."""
    if not ctx.enabled or not ctx.worktree or not await _is_repo(ctx.worktree):
        return None
    if ctx.remote:
        await _git(ctx.worktree, "fetch", _authed_url(ctx.remote, ctx.token), ctx.main)
        rc, _ = await _git(ctx.worktree, "rev-parse", "FETCH_HEAD")
        ref = "FETCH_HEAD" if rc == 0 else ctx.main
    else:
        ref = ctx.main
    await _git(ctx.worktree, "merge", "--abort")
    rc, _ = await _git(ctx.worktree, "merge", "--no-ff", "-m",
                       f"Merge {ctx.main} into {ctx.branch} (Konflikt-Auflösung)", ref)
    if rc == 0:
        return []
    rc_u, files = await _git(ctx.worktree, "diff", "--name-only", "--diff-filter=U")
    return [l for l in files.splitlines() if l.strip()] if rc_u == 0 else []


async def accept(ctx: GitCtx) -> str:
    """Bei Abnahme: Ticket-Branch → main mergen + push."""
    if not ctx.enabled or not await _is_repo(ctx.workdir):
        return "git: kein Repo"
    rc, _ = await _git(ctx.workdir, "checkout", ctx.main)
    if rc != 0:
        await _git(ctx.workdir, "checkout", "-b", ctx.main, ctx.branch)
    else:
        rc, out = await _git(ctx.workdir, "merge", "--no-ff", "-m",
                             f"Merge {ctx.branch}", ctx.branch)
        if rc != 0:
            await _git(ctx.workdir, "merge", "--abort")
            return f"conflict:{ctx.branch}"
    # Lokal gemergt; schlägt der Push fehl, NICHT als „merged" melden (Remote wäre veraltet).
    if not await _push(ctx, ctx.workdir, ctx.main):
        return "push_failed"
    head = await _head(ctx.workdir)
    return f"merged:{head}"


def _github_slug(remote: str) -> str | None:
    """'https://github.com/user/repo.git' / 'git@github.com:user/repo' → 'user/repo'."""
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", remote or "")
    return m.group(1) if m else None


async def open_pull_request(ctx: GitCtx, title: str, body: str = "") -> str:
    """Ticket-Branch pushen und einen Pull Request öffnen, statt direkt zu mergen.

    Rückgabe: 'pr:<url>' bei Erfolg, sonst 'pr-fehler:<grund>'.
    """
    if not ctx.enabled or not ctx.remote:
        return "pr-fehler:kein Remote konfiguriert"
    slug = _github_slug(ctx.remote)
    if not slug:
        return "pr-fehler:nur GitHub-Remotes werden unterstützt"
    if not ctx.token:
        return "pr-fehler:kein Git-Token hinterlegt"

    wd = ctx.worktree or ctx.workdir
    rc, out = await _git(wd, "push", _authed_url(ctx.remote, ctx.token), f"{ctx.branch}:{ctx.branch}")
    if rc != 0:
        return f"pr-fehler:push fehlgeschlagen: {_redact(out, ctx.token)[:200]}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{slug}/pulls",
                headers={"Authorization": f"Bearer {ctx.token}",
                         "Accept": "application/vnd.github+json"},
                json={"title": title[:250], "body": body[:60000], "head": ctx.branch, "base": ctx.main})
            if resp.status_code == 201:
                return f"pr:{resp.json().get('html_url', '')}"
            # Ein bereits offener PR ist kein Fehler — den vorhandenen zurückgeben.
            if resp.status_code == 422:
                existing = await client.get(
                    f"https://api.github.com/repos/{slug}/pulls",
                    headers={"Authorization": f"Bearer {ctx.token}"},
                    params={"head": f"{slug.split('/')[0]}:{ctx.branch}", "state": "open"})
                if existing.status_code == 200 and existing.json():
                    return f"pr:{existing.json()[0].get('html_url', '')}"
            return f"pr-fehler:GitHub {resp.status_code}: {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return f"pr-fehler:Verbindungsfehler: {exc}"


async def remove_worktree(ctx: GitCtx) -> None:
    if not ctx.worktree or not await _is_repo(ctx.workdir):
        return
    try:
        await _git(ctx.workdir, "worktree", "remove", "--force", ctx.worktree)
        rc, out = await _git(ctx.workdir, "branch", "-d", ctx.branch)
        if rc != 0:
            log.warning("Branch %s NICHT gelöscht (ungemergt): %s", ctx.branch, out.strip())
        await _git(ctx.workdir, "worktree", "prune")
    except Exception:  # noqa: BLE001
        log.exception("worktree-remove fehlgeschlagen")
