"""The curator: keeps the learned memory readable instead of letting it run wild.

The memory (ABC-30) is a bullet list per note that every run appends to at the bottom. It
therefore only grows, and `read_memory` truncates at `MAX_MEMORY_CHARS`. From that limit on,
the bottom falls silently out of the prompt: first learned, then forgotten, without anybody
noticing. That is exactly what Predecessor has its `curator` for: tidying up as a small background
job of its own on the aux model.

The rules are deliberately strict, because other people's memories are touched here:

* **Never delete, only archive.** What flies out lands in `Archiv-<notiz>.md`. A wrong
  judgement of the model then costs one hand movement, not a memory.
* **Pinned lines are taboo.** A line with 📌 stays word for word.
* **When in doubt, keep.** The assignment says explicitly that unclear things are not sorted away.
* **Only summarise, do not invent.** No new statements, no reinterpretations.
* **Doing nothing is a valid result.** No aux model, a short note, an unclear answer: the
  note stays as it is.
"""
from __future__ import annotations

import datetime as dt
import logging

from ..services.appsettings import get_setting, set_setting
from .tools_memory import _note_target, _read_note, memory_root, note_path

log = logging.getLogger("traccoon.curator")

# Only from this length on is tidying up worth it; below it the list is manageable anyway.
MIN_CHARS = 1500
# How often per note tidying up happens at most.
DISTANCE_HOURS = 24
PIN = "📌"

TASK = (
    "You are tidying up the memory note of an assistant. It is a list of instructions it has "
    "learned from its person.\n\n"
    "RULES:\n"
    "1. Merge duplicates and obviously equivalent lines into ONE line.\n"
    "2. If two lines contradict each other, keep the YOUNGER one (further down) and throw the "
    "older one out.\n"
    "3. Throw out what is recognisably done or overtaken.\n"
    "4. Everything else stays — when in doubt, KEEP it. Invent nothing, reinterpret nothing, "
    "do not rewrite content in a way that changes its meaning.\n"
    f"5. Lines with {PIN} stay word for word unchanged and in their order.\n\n"
    "Answer in exactly two sections, without a preamble:\n"
    "### KEEP\n"
    "<the tidied list, one bullet line per insight>\n"
    "### ARCHIVE\n"
    "<the removed lines, unchanged, one bullet line each — or the word: none>\n\n"
    "--- Note ---\n"
)


def _parts(answer: str) -> tuple[str, str] | None:
    """(keep, archive) from the model answer; None when it does not follow the format.

    `### ARCHIVE` is REQUIRED, and not out of pedantry: the assignment demands exactly two
    sections, so the second one is the proof that the answer arrived whole. An answer that
    ran into `max_tokens` breaks off in the middle of the KEEP list — and that list is what
    overwrites the note. Without this check a truncated answer looks like a tidy-up and files
    away every entry behind the break, unarchived and unnoticed.
    """
    if "### KEEP" not in answer or "### ARCHIVE" not in answer:
        return None
    keep, archive = answer.split("### KEEP", 1)[1].split("### ARCHIVE", 1)
    keep = keep.strip()
    archive = archive.strip()
    if archive.lower() in ("none", "none.", "-", ""):
        archive = ""
    return (keep, archive) if keep else None


def _lines(text: str) -> list[str]:
    return [z.strip() for z in text.splitlines() if z.strip().startswith(("-", "*"))]


async def _latest_key(owner_id: int, path: str) -> str:
    return f"curator_last:{owner_id}:{path}"


async def due(db, owner_id: int, path: str, *, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    raw = await get_setting(db, await _latest_key(owner_id, path), "")
    if not raw:
        return True
    try:
        return (now - dt.datetime.fromisoformat(raw)).total_seconds() >= DISTANCE_HOURS * 3600
    except ValueError:
        return True


async def curate_note(db, mcp, *, owner_id: int, path: str, agent, tokens: dict,
                          base_urls: dict) -> str | None:
    """Tidy up one memory note. The return value is a short report, None = nothing done."""
    content = (await _read_note(mcp, path)).strip()
    if len(content) < MIN_CHARS:
        return None

    pinned = [z for z in _lines(content) if PIN in z]

    from .aux import aux_chat
    # The answer repeats the whole list plus what is thrown out, so it is roughly as long as
    # the note itself. A fixed 3000 was enough for a short note and broke off in the middle of
    # a 12k one — German markdown runs at ~3 characters per token, and both sections have to
    # fit. Scaled with a floor, capped so a runaway note cannot pull the aux model apart.
    budget = min(16000, max(3000, len(content) // 2))
    answer = await aux_chat(db, owner_id=owner_id, task="curator",
                             messages=[{"role": "user", "content": TASK + content}],
                             agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=budget)
    if not answer:
        return None
    shared = _parts(answer)
    if shared is None:
        log.warning("Curator: the answer does not follow the format (%d chars, KEEP=%s, "
                    "ARCHIVE=%s — a missing ARCHIVE usually means it hit max_tokens=%d), "
                    "%s stays unchanged",
                    len(answer), "### KEEP" in answer, "### ARCHIVE" in answer, budget, path)
        return None
    keep, archive = shared

    # Safety nets against an overeager model. They take hold BEFORE writing, because an
    # overwritten memory could only be rescued from the archive.
    if not _lines(keep):
        log.warning("Curator: the result has no entries, %s stays unchanged", path)
        return None
    missing = [z for z in pinned if z not in keep]
    if missing:
        log.warning("Curator: %d pinned line(s) were missing in the result, %s stays unchanged",
                    len(missing), path)
        return None
    if len(_lines(keep)) < len(_lines(content)) / 3:
        log.warning("Curator: the result throws more than two thirds away, %s stays unchanged", path)
        return None

    header = f"# {path.rsplit('/', 1)[-1].removesuffix('.md')}\n\n"
    if archive:
        # Archive FIRST, truncate AFTERWARDS: if the second step breaks off, nothing is lost.
        arch_path = path.rsplit("/", 1)[0] + "/Archiv-" + path.rsplit("/", 1)[-1]
        stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%d")
        try:
            await mcp.call("obsidian__obsidian_append_to_note",
                           {"target": _note_target(arch_path),
                            "content": f"\n## Aussortiert am {stamp}\n{archive}\n"})
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator: archive %s not writable (%s), %s stays unchanged",
                        arch_path, exc, path)
            return None

    try:
        await mcp.call("obsidian__obsidian_write_note",
                       {"target": _note_target(path), "content": header + keep + "\n",
                        "overwrite": True})
    except Exception as exc:  # noqa: BLE001
        log.warning("Curator: %s not writable (%s)", path, exc)
        return None

    await set_setting(db, await _latest_key(owner_id, path),
                      dt.datetime.now(tz=dt.timezone.utc).isoformat())
    before, after = len(_lines(content)), len(_lines(keep))
    return f"{path}: {before} → {after} entries, {len(_lines(archive))} archived"


async def curate(db, mcp, *, owner_id: int, agent_role: str = "", project_key: str = "",
                    agent=None, tokens: dict | None = None, base_urls: dict | None = None) -> list[str]:
    """Tidy up all relevant memory notes of a human, as far as they are due."""
    root = await memory_root(db, owner_id)
    if not root:
        return []
    reports = []
    # All four areas, including the narrowest one: `Projekt-<KEY>-Agent-<rolle>.md` is the
    # block that stands last in the prompt, so it is the first to fall out of the budget once
    # it has grown. Skipping it here would silently undo what it was built for.
    for area in ("person", "agent", "project", "project_agent"):
        path = note_path(root, area, agent_role, project_key)
        if not path or not await due(db, owner_id, path):
            continue
        report = await curate_note(db, mcp, owner_id=owner_id, path=path, agent=agent,
                                        tokens=tokens or {}, base_urls=base_urls or {})
        if report:
            reports.append(report)
    return reports
