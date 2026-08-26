"""Summarise a long history instead of running into the provider error.

`agent_definitions.max_context_tokens` already existed; nobody read it. Whoever set the
value believed in a protective limit that did not exist: the run kept growing until the
model rejected the request, and then the whole run was lost.

Now the actual context size (`usage.input_tokens`) is measured after every model call. If
it tears the threshold, the middle part of the history is replaced by a summary, produced
by the aux model, not by the working model.

The two traps that determine what happens here:

1. **You must not cut just anywhere.** An `assistant` with `tool_calls` and the
   corresponding `tool` answers are one unit; separating them makes the provider reject
   the request (HTTP 400), which would turn a threatening error into a certain one. Exactly
   one cutting point is therefore forbidden: before a `tool` answer.
2. **The assignment stays.** The system prompt and the first instruction survive every
   compaction. Whoever cuts the assignment away saves tokens and loses the task.
3. **Summarising happens piece by piece.** The aux model is small; a history of 500k
   characters does not fit into one assignment. It is therefore cut into pieces at
   permitted seams and summarised piece by piece, instead of trying it with everything at
   once and standing there empty handed.

If the aux model fails for a piece, that piece is taken over raw and clipped instead of
being dropped: it has to get smaller, that is the point of the whole operation, but nothing
disappears without trace. A run whose history is shortened is unpleasant; a run that tells
its person that part of their own conversation is gone is unusable.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("traccoon.compaction")

# From which share of the permitted context compaction happens. Not only at 100 %: the
# summary itself and the next answer need room as well.
THRESHOLD = 0.8
# This many messages at the end stay untouched, the immediate working context.
KEEP = 6
# Less than that is not worth it: then the history is so short that the summary would be
# about as long as the original.
MIN_BLOCK = 4
# How much text goes into ONE summarisation assignment at most. The aux model is
# deliberately small (local, 32k context); sending it the whole history of a large model
# makes it refuse, and the compaction would always run into the hard cut. If the block does
# not fit, only its oldest part is caught; the rest comes next time.
MAX_AUX_CHARS = 50_000
# This many pieces per compaction at most. Each is an aux call of its own; without a cap a
# very long history could spend a large part of the runtime summarising. What does not get
# its turn stays verbatim and is caught next time: nothing is lost, it only takes one round
# longer.
MAX_CHUNKS = 12
# How many pieces go to the aux model at once. One after another, twelve calls of up to two
# minutes each would be half the runtime of an agent run; all at once would overwhelm the
# small local endpoint.
AUX_PARALLEL = 3
# The emergency exit when the aux model delivers nothing: the block is taken over raw and
# clipped instead of being dropped. Both bounds are deliberately tight — the compaction runs
# BECAUSE the context is too big, so the replacement has to be smaller than what it replaces.
RAW_PER_MESSAGE = 400
RAW_PER_CHUNK = 4000


def _header_end(messages: list[dict]) -> int:
    """Index behind the untouchable beginning (leading system messages plus first assignment)."""
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    if i < len(messages) and messages[i].get("role") == "user":
        i += 1                                  # the actual assignment
    return i


def _cuttable(m: dict) -> bool:
    """May a cut be made BEFORE this message?

    Exactly one sort is impermissible: the `tool` answer. It hangs off the preceding
    `assistant` with `tool_calls` and must never be separated from it. Everything else,
    `user`, `system` AND `assistant`, begins a new turn and is a clean seam.

    Formerly only `user`/`system` counted as a seam. With an agent that calls nothing but
    tools for 60 rounds those practically do not exist: the history consists of
    assistant/tool pairs. The truncation therefore knew only two exits, almost nothing (the
    oldest four messages) or everything (history boiled down to three messages, agent
    without memory, starts from the beginning). On exactly that, one ticket hung for two full
    runs on 2026-08-06 without writing a single file.
    """
    return m.get("role") != "tool" and not m.get("tool_call_id")


def _safe_limit(messages: list[dict], ab: int) -> int:
    """Next index from `ab` at which a cut may be made.

    Safe is the beginning of every message that is not a `tool` answer: that one hangs off a
    preceding `assistant` with `tool_calls` and must never be separated from it.
    """
    for i in range(ab, len(messages)):
        if _cuttable(messages[i]):
            return i
    return len(messages)


def _safe_limit_backwards(messages: list[dict], to_atmost: int) -> int | None:
    """Largest permitted cut that does NOT lie behind `bis_hoechstens`, or None.

    Needed because the forward search can never make a block smaller: if there is no seam
    between the desired place and the current limit, it jumps beyond the limit. Whoever
    wants to shrink a block with that goes round in circles.
    """
    for i in range(min(to_atmost, len(messages)) - 1, -1, -1):
        if _cuttable(messages[i]):
            return i
    return None


def plan(messages: list[dict], limit_tokens: int, measured: int) -> tuple[int, int] | None:
    """(from, to) of the block to be summarised, or None when there is nothing to do."""
    if not limit_tokens or measured < limit_tokens * THRESHOLD:
        return None
    von = _header_end(messages)
    to = _safe_limit(messages, max(von, len(messages) - KEEP))
    if to - von < MIN_BLOCK:
        return None
    return von, to


def _as_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content")
        if isinstance(content, list):        # Anthropic blocks: only the text parts
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        content = (content or "").strip()
        if not content and m.get("tool_calls"):
            content = "(ruft Werkzeuge auf: " + ", ".join(
                (c.get("function") or {}).get("name", "?") for c in m["tool_calls"]) + ")"
        if content:
            parts.append(f"[{role}] {content[:4000]}")
    return "\n\n".join(parts)


TASK = (
    "Summarise the following excerpt of an agent run. The summary REPLACES the excerpt — what "
    "is missing here is lost for the rest of the run.\n\n"
    "Take in: finished steps and their result, decisions taken including the reasoning, facts "
    "found (names, paths, ids, numbers), open threads and everything the person laid down. "
    "Leave out: repetitions, raw tool output, politeness.\n\n"
    "Schreib in Stichpunkten, deutsch, ohne Vorrede.\n\n--- Ausschnitt ---\n"
)


HANDOVER_TASK = (
    "The following agent run was ended at a limit (time, iterations or tokens) and will be "
    "continued in a FRESH run in a moment — one that knows nothing except what you write down "
    "now. Write the handover to it, in exactly these three sections:\n\n"
    "**Findings** — what I found out about the code, with file paths, function and field names. "
    "That saves the next run the searching.\n"
    "**Done** — which files I already changed and what stands in them. If nothing was changed: "
    "write exactly that.\n"
    "**Next step** — what the next run is to do FIRST, concretely.\n\n"
    "No preamble, dense. Invent nothing: what does not stand in the excerpt does not belong in "
    "the handover.\n\n--- The run ---\n"
)


async def handover(db, *, messages: list[dict], reason: str, last_text: str,
                    owner_id, agent, tokens: dict, base_urls: dict) -> str:
    """Handover to the continuation run: what was learned, what was done, what comes next.

    Until now the continuation only held `grund` plus the last sentence of the agent. That
    was not even enough to know which files had already been read: one ticket started three runs
    in a row on 2026-08-07 with `open_tasks` and the same search query and wrote not a line
    of code in an hour and a half. The run ends at a limit, so the thread has to be rescued
    from the history, not from its last sentence.

    If the aux model drops out, the old, honest stopgap remains.
    """
    from .aux import aux_chat

    stopgap = f"{reason}\n\nLetzter Stand:\n{last_text or '(kein Text)'}"
    von = _header_end(messages)
    if len(messages) - von < MIN_BLOCK:
        return stopgap
    chunks = _chunk(messages, von, len(messages))[:MAX_CHUNKS]
    raw = await _summarise(db, messages, chunks, owner_id=owner_id, agent=agent,
                                tokens=tokens, base_urls=base_urls)
    if not raw.strip():
        return stopgap
    # Second pass: the actual handover is made from the piece summaries. With a single piece
    # that would be a summary of the summary, and then it is better to work on the history
    # directly instead.
    source = raw if len(chunks) > 1 else _as_text(messages[von:])[:MAX_AUX_CHARS]
    text = await aux_chat(
        db, owner_id=owner_id, task="compression",
        messages=[{"role": "user", "content": HANDOVER_TASK + source}],
        agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1500)
    if not text:
        log.warning("Handover without an aux model, the last state stays")
        return f"{reason}\n\nState from the history:\n{raw}"
    return f"{reason}\n\n{text.strip()}"


def _chunk(messages: list[dict], von: int, to: int) -> list[tuple[int, int]]:
    """Cut the block into pieces the aux model can hold.

    Always at permitted seams and always with progress: if need be a piece is a single
    message (whose text `_als_text` truncates at 4000 characters anyway).
    """
    chunks: list[tuple[int, int]] = []
    start = von
    while start < to:
        end = to
        while end > start + 1 and len(_as_text(messages[start:end])) > MAX_AUX_CHARS:
            smaller = _safe_limit_backwards(messages, start + (end - start) // 2)
            if smaller is None or smaller <= start or smaller >= end:
                end = start + 1        # no seam left: one message, but progress
                break
            end = smaller
        chunks.append((start, end))
        start = end
    return chunks


def _clipped(block: list[dict], budget: int) -> str:
    """The raw history, shortened: who said what, cut off at the sides.

    The emergency exit of the compaction. It HAS to shrink — that is the whole point of the
    operation — which is why the budget is derived from what is being replaced and not from
    a fixed number alone: a block of many short messages would otherwise grow through the
    role in front of every line. What survives is enough to know that something was said and
    roughly what: a name, a number, a decision at the beginning of a line.
    """
    out, spent = [], 0
    for m in block:
        role = str(m.get("role") or "?")
        text = str(m.get("content") or "")
        if not text and m.get("tool_calls"):
            text = "(tool calls: " + ", ".join(
                str((c.get("function") or {}).get("name") or "?") for c in m["tool_calls"]) + ")"
        if len(text) > RAW_PER_MESSAGE:
            half = RAW_PER_MESSAGE // 2
            text = text[:half] + " […] " + text[-half:]
        line = f"  {role}: {text}"
        if spent + len(line) > budget:
            out.append(f"  […] {len(block) - len(out)} further messages, no room left")
            break
        out.append(line)
        spent += len(line)
    return "\n".join(out)


async def _summarise(db, messages: list[dict], chunks: list[tuple[int, int]], *,
                          owner_id, agent, tokens, base_urls) -> str:
    """Summarise every piece on its own and append the parts to each other.

    Formerly the BLOCK was shrunk instead until it fitted into one aux assignment, and if no
    seam was found the whole history went to a model with 32k context in one go. That model
    refused, `aux_chat` delivered nothing, and instead of a summary the agent got the note
    that it does not know anything any more. Piece by piece the summary comes about even for
    a history of 500k characters.
    """
    from .aux import aux_chat
    counter = asyncio.Semaphore(AUX_PARALLEL)

    async def _piece(nr: int, a: int, b: int) -> str:
        from_where = f"(Teil {nr} von {len(chunks)})\n\n" if len(chunks) > 1 else ""
        async with counter:
            try:
                text = await aux_chat(
                    db, owner_id=owner_id, task="compression",
                    messages=[{"role": "user",
                               "content": TASK + from_where + _as_text(messages[a:b])}],
                    agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1024)
            except Exception:  # noqa: BLE001 - one outage must not cost the run
                log.exception("Compaction: piece %d/%d failed", nr, len(chunks))
                text = None
        if text:
            return text.strip()
        # No summary is a reason to shorten, never to throw away. What stood here before was
        # a note to the person that this part of THEIR conversation is gone and they should
        # check it themselves — for a chat with an assistant that is the one outcome that
        # must not exist. Raw and clipped is worse than a summary and far better than a hole.
        log.warning("Compaction: piece %d/%d without a summary (aux not available), "
                    "%d messages are taken over raw and clipped", nr, len(chunks), b - a)
        room = min(RAW_PER_CHUNK, max(200, _as_text(messages[a:b]).__len__() // 2))
        return (f"- (part {nr}: {b - a} messages, no summary possible — clipped verbatim "
                f"below.)\n{_clipped(messages[a:b], room)}")

    parts = await asyncio.gather(*[_piece(nr, a, b)
                                   for nr, (a, b) in enumerate(chunks, 1)])
    return "\n".join(parts)


async def compact(db, *, messages: list[dict], limit_tokens: int, measured: int,
                      owner_id: int | None, agent, tokens: dict, base_urls: dict) -> list[dict] | None:
    """Truncate the history. Returns the new message list, or None when there was nothing to do."""
    area = plan(messages, limit_tokens, measured)
    if area is None:
        return None
    von, to = area
    # The whole block is summarised, but in pieces the (small, local) aux model accepts as
    # well. On 2026-07-31 the worker stood at 100 % CPU for 8 hours at this place because the
    # shrinking ran over the FORWARD search and always returned the same limit on a pure tool
    # history; that is why `_haeppchen` searches backwards and forces progress in every
    # round.
    chunks = _chunk(messages, von, to)
    if len(chunks) > MAX_CHUNKS:
        # Catch only the oldest part; the rest stays verbatim and comes next time.
        chunks = chunks[:MAX_CHUNKS]
        to = chunks[-1][1]
    summary = await _summarise(db, messages, chunks, owner_id=owner_id,
                                            agent=agent, tokens=tokens, base_urls=base_urls)
    log.info("Compaction: %d messages summarised in %d piece(s)",
             to - von, len(chunks))

    replacement = ("# Zusammenfassung des bisherigen Verlaufs\n"
              "(The detailed history was shortened to stay inside the context window. "
              "What stands here is all that is left of it — work on with that instead of "
              "starting from the beginning again.)\n\n" + summary)

    return messages[:von] + [{"role": "system", "content": replacement}] + messages[to:]
