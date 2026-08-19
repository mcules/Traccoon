"""Codegraph integration: one code knowledge graph PER WORKTREE, so that agents get the
relevant symbols, call paths and blast radius with ONE query instead of reading many files
one by one (which saves tokens).

Isolation: every query runs strictly with ``cwd = worktree``; codegraph indexes and reads
exclusively the files of this ticket, never another worktree or the repository outside it.
The index lies in ``<worktree>/.codegraph/`` and is excluded from git locally (no accidental
committing of the SQLite database over ``git add -A``).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("traccoon.codegraph")

# Kill switch (on by default). Only reading query subcommands are allowed over the agent tool.
ENABLED = os.getenv("CODEGRAPH_ENABLED", "1").lower() not in ("0", "false", "no", "")
_BIN = os.getenv("CODEGRAPH_BIN", "codegraph")
_TIMEOUT = float(os.getenv("CODEGRAPH_TIMEOUT", "60"))
_INIT_TIMEOUT = float(os.getenv("CODEGRAPH_INIT_TIMEOUT", "300"))
_MAX_OUT = int(os.getenv("CODEGRAPH_MAX_OUTPUT", "12000"))

QUERY_COMMANDS = {"explore", "query", "node", "callers", "callees", "impact", "files", "affected"}

_ENV = {**os.environ, "CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_TELEMETRY": "0", "DO_NOT_TRACK": "1"}
_locks: dict[str, asyncio.Lock] = {}
_bin_ok: bool | None = None


async def _exec(program: str, *args: str, cwd: str, timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            program, *args, cwd=cwd, env=_ENV,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return 127, f"{program}: nicht gefunden"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"{program}: Zeitüberschreitung ({timeout:.0f}s)"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def available() -> bool:
    """codegraph enabled AND the binary callable? The result is cached."""
    global _bin_ok
    if not ENABLED:
        return False
    if _bin_ok is None:
        rc, _ = await _exec(_BIN, "version", cwd="/tmp", timeout=15)
        _bin_ok = rc == 0
        if not _bin_ok:
            log.warning("codegraph binary not callable (%s), the tool is disabled", _BIN)
    return _bin_ok


def _lock(root: str) -> asyncio.Lock:
    lk = _locks.get(root)
    if lk is None:
        lk = _locks[root] = asyncio.Lock()
    return lk


async def _git_exclude_codegraph(root: str) -> None:
    """Exclude `.codegraph/` from git locally (changing no tracked .gitignore).
    `git rev-parse --git-path info/exclude` delivers the correct path in git worktrees too."""
    rc, p = await _exec("git", "rev-parse", "--git-path", "info/exclude", cwd=root, timeout=15)
    if rc != 0:
        return
    excl = Path(p.strip())
    if not excl.is_absolute():
        excl = Path(root) / excl
    try:
        excl.parent.mkdir(parents=True, exist_ok=True)
        existing = excl.read_text(encoding="utf-8") if excl.exists() else ""
        if ".codegraph/" not in existing:
            with open(excl, "a", encoding="utf-8") as f:
                f.write(("" if existing.endswith("\n") or not existing else "\n") + ".codegraph/\n")
    except OSError:
        pass


async def ensure_indexed(root: str) -> None:
    """Keep the index fresh: with a missing index a one-off `init` (which builds the graph),
    otherwise an incremental `sync`. Serialised per worktree (lock)."""
    async with _lock(root):
        if (Path(root) / ".codegraph").exists():
            await _exec(_BIN, "sync", cwd=root, timeout=_TIMEOUT)
        else:
            await _git_exclude_codegraph(root)
            rc, out = await _exec(_BIN, "init", cwd=root, timeout=_INIT_TIMEOUT)
            if rc != 0:
                log.warning("codegraph init fehlgeschlagen in %s: %s", root, out[:300])


async def query(root: str | None, command: str, arg: str) -> str:
    """Run one codegraph query in the worktree and deliver the (truncated) output."""
    if not root:
        return "FEHLER: kein Workspace für dieses Projekt."
    if not await available():
        return "FEHLER: codegraph ist nicht verfügbar."
    if command not in QUERY_COMMANDS:
        return (f"FEHLER: unbekanntes command '{command}'. "
                f"Erlaubt: {', '.join(sorted(QUERY_COMMANDS))}.")
    await ensure_indexed(root)
    args = [command] + ([arg] if arg else [])
    rc, out = await _exec(_BIN, *args, cwd=root, timeout=_TIMEOUT)
    out = out.strip()
    if len(out) > _MAX_OUT:
        out = out[:_MAX_OUT] + "\n…(gekürzt — gezielter fragen oder impact/node für Details)"
    return out or "(keine Treffer)"
