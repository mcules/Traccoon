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

If the aux model fails for a piece, an honest marker stands in its place; the rest of the
summary stays. A run without parts of its history is unpleasant, but an aborted run is
worse, and a run without any history at all starts from the beginning.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("traccoon.compaction")

# From which share of the permitted context compaction happens. Not only at 100 %: the
# summary itself and the next answer need room as well.
THRESHOLD = 0.8
# This many messages at the end stay untouched, the immediate working context.
BEHALTEN = 6
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


def _header_ende(messages: list[dict]) -> int:
    """Index behind the untouchable beginning (leading system messages plus first assignment)."""
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    if i < len(messages) and messages[i].get("role") == "user":
        i += 1                                  # the actual assignment
    return i


def _schnittfaehig(m: dict) -> bool:
    """May a cut be made BEFORE this message?

    Exactly one sort is impermissible: the `tool` answer. It hangs off the preceding
    `assistant` with `tool_calls` and must never be separated from it. Everything else,
    `user`, `system` AND `assistant`, begins a new turn and is a clean seam.

    Formerly only `user`/`system` counted as a seam. With an agent that calls nothing but
    tools for 60 rounds those practically do not exist: the history consists of
    assistant/tool pairs. The truncation therefore knew only two exits, almost nothing (the
    oldest four messages) or everything (history boiled down to three messages, agent
    without memory, starts from the beginning). On exactly that, ABC-4 hung for two full
    runs on 2026-08-06 without writing a single file.
    """
    return m.get("role") != "tool" and not m.get("tool_call_id")


def _sichere_limit(messages: list[dict], ab: int) -> int:
    """Next index from `ab` at which a cut may be made.

    Safe is the beginning of every message that is not a `tool` answer: that one hangs off a
    preceding `assistant` with `tool_calls` and must never be separated from it.
    """
    for i in range(ab, len(messages)):
        if _schnittfaehig(messages[i]):
            return i
    return len(messages)


def _sichere_limit_rueckwaerts(messages: list[dict], bis_hoechstens: int) -> int | None:
    """Largest permitted cut that does NOT lie behind `bis_hoechstens`, or None.

    Needed because the forward search can never make a block smaller: if there is no seam
    between the desired place and the current limit, it jumps beyond the limit. Whoever
    wants to shrink a block with that goes round in circles.
    """
    for i in range(min(bis_hoechstens, len(messages)) - 1, -1, -1):
        if _schnittfaehig(messages[i]):
            return i
    return None


def plan(messages: list[dict], limit_tokens: int, gemessen: int) -> tuple[int, int] | None:
    """(from, to) of the block to be summarised, or None when there is nothing to do."""
    if not limit_tokens or gemessen < limit_tokens * THRESHOLD:
        return None
    von = _header_ende(messages)
    bis = _sichere_limit(messages, max(von, len(messages) - BEHALTEN))
    if bis - von < MIN_BLOCK:
        return None
    return von, bis


def _as_text(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        rolle = m.get("role", "?")
        inhalt = m.get("content")
        if isinstance(inhalt, list):        # Anthropic blocks: only the text parts
            inhalt = " ".join(b.get("text", "") for b in inhalt if isinstance(b, dict))
        inhalt = (inhalt or "").strip()
        if not inhalt and m.get("tool_calls"):
            inhalt = "(ruft Werkzeuge auf: " + ", ".join(
                (c.get("function") or {}).get("name", "?") for c in m["tool_calls"]) + ")"
        if inhalt:
            parts.append(f"[{rolle}] {inhalt[:4000]}")
    return "\n\n".join(parts)


TASK = (
    "Fasse den folgenden Ausschnitt eines Agenten-Laufs zusammen. Die Zusammenfassung ERSETZT "
    "den Ausschnitt — was hier fehlt, ist für den weiteren Lauf verloren.\n\n"
    "Nimm auf: erledigte Schritte und ihr Ergebnis, getroffene Entscheidungen samt Begründung, "
    "gefundene Fakten (Namen, Pfade, IDs, Zahlen), offene Fäden und alles, was der Mensch "
    "vorgegeben hat. Lass weg: Wiederholungen, Werkzeug-Rohausgaben, Höflichkeiten.\n\n"
    "Schreib in Stichpunkten, deutsch, ohne Vorrede.\n\n--- Ausschnitt ---\n"
)


HANDOVER_TASK = (
    "Der folgende Agenten-Lauf wurde an einer Grenze beendet (Zeit, Iterationen oder Tokens) "
    "und wird gleich in einem FRISCHEN Lauf fortgesetzt — der weiß nichts außer dem, was du "
    "jetzt aufschreibst. Schreib die Übergabe an ihn, in genau diesen drei Abschnitten:\n\n"
    "**Erkenntnisse** — was ich über den Code herausgefunden habe, mit Datei-Pfaden, "
    "Funktions- und Feldnamen. Das erspart dem nächsten Lauf das erneute Suchen.\n"
    "**Erledigt** — welche Dateien ich bereits geändert habe und was darin steht. Wenn "
    "nichts geändert wurde: schreib genau das hin.\n"
    "**Nächster Schritt** — was der nächste Lauf ALS ERSTES tun soll, konkret.\n\n"
    "Keine Vorrede, deutsch, dicht. Erfinde nichts: was nicht im Ausschnitt steht, gehört "
    "nicht in die Übergabe.\n\n--- Lauf ---\n"
)


async def handover(db, *, messages: list[dict], reason: str, last_text: str,
                    owner_id, agent, tokens: dict, base_urls: dict) -> str:
    """Handover to the continuation run: what was learned, what was done, what comes next.

    Until now the continuation only held `grund` plus the last sentence of the agent. That
    was not even enough to know which files had already been read: ABC-12 started three runs
    in a row on 2026-08-07 with `open_tasks` and the same search query and wrote not a line
    of code in an hour and a half. The run ends at a limit, so the thread has to be rescued
    from the history, not from its last sentence.

    If the aux model drops out, the old, honest stopgap remains.
    """
    from .aux import aux_chat

    notloesung = f"{reason}\n\nLetzter Stand:\n{last_text or '(kein Text)'}"
    von = _header_ende(messages)
    if len(messages) - von < MIN_BLOCK:
        return notloesung
    chunks = _haeppchen(messages, von, len(messages))[:MAX_CHUNKS]
    roh = await _zusammenfassen(db, messages, chunks, owner_id=owner_id, agent=agent,
                                tokens=tokens, base_urls=base_urls)
    if not roh.strip():
        return notloesung
    # Second pass: the actual handover is made from the piece summaries. With a single piece
    # that would be a summary of the summary, and then it is better to work on the history
    # directly instead.
    source = roh if len(chunks) > 1 else _as_text(messages[von:])[:MAX_AUX_CHARS]
    text = await aux_chat(
        db, owner_id=owner_id, task="compression",
        messages=[{"role": "user", "content": HANDOVER_TASK + source}],
        agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1500)
    if not text:
        log.warning("Handover without an aux model, the last state stays")
        return f"{reason}\n\nStand aus dem Verlauf:\n{roh}"
    return f"{reason}\n\n{text.strip()}"


def _haeppchen(messages: list[dict], von: int, bis: int) -> list[tuple[int, int]]:
    """Cut the block into pieces the aux model can hold.

    Always at permitted seams and always with progress: if need be a piece is a single
    message (whose text `_als_text` truncates at 4000 characters anyway).
    """
    chunks: list[tuple[int, int]] = []
    start = von
    while start < bis:
        ende = bis
        while ende > start + 1 and len(_as_text(messages[start:ende])) > MAX_AUX_CHARS:
            kleiner = _sichere_limit_rueckwaerts(messages, start + (ende - start) // 2)
            if kleiner is None or kleiner <= start or kleiner >= ende:
                ende = start + 1        # no seam left: one message, but progress
                break
            ende = kleiner
        chunks.append((start, ende))
        start = ende
    return chunks


async def _zusammenfassen(db, messages: list[dict], chunks: list[tuple[int, int]], *,
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

    async def _stueck(nr: int, a: int, b: int) -> str:
        von_wo = f"(Teil {nr} von {len(chunks)})\n\n" if len(chunks) > 1 else ""
        async with counter:
            try:
                text = await aux_chat(
                    db, owner_id=owner_id, task="compression",
                    messages=[{"role": "user",
                               "content": TASK + von_wo + _as_text(messages[a:b])}],
                    agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1024)
            except Exception:  # noqa: BLE001 - one outage must not cost the run
                log.exception("Compaction: piece %d/%d failed", nr, len(chunks))
                text = None
        if text:
            return text.strip()
        log.warning("Compaction: piece %d/%d without a summary (aux not available)",
                    nr, len(chunks))
        return (f"- (Teil {nr}: {b - a} Nachrichten, Zusammenfassung nicht möglich — "
                "dieser Abschnitt ist verloren, im Zweifel nachprüfen.)")

    parts = await asyncio.gather(*[_stueck(nr, a, b)
                                   for nr, (a, b) in enumerate(chunks, 1)])
    return "\n".join(parts)


async def kompaktiere(db, *, messages: list[dict], limit_tokens: int, gemessen: int,
                      owner_id: int | None, agent, tokens: dict, base_urls: dict) -> list[dict] | None:
    """Truncate the history. Returns the new message list, or None when there was nothing to do."""
    area = plan(messages, limit_tokens, gemessen)
    if area is None:
        return None
    von, bis = area
    # The whole block is summarised, but in pieces the (small, local) aux model accepts as
    # well. On 2026-07-31 the worker stood at 100 % CPU for 8 hours at this place because the
    # shrinking ran over the FORWARD search and always returned the same limit on a pure tool
    # history; that is why `_haeppchen` searches backwards and forces progress in every
    # round.
    chunks = _haeppchen(messages, von, bis)
    if len(chunks) > MAX_CHUNKS:
        # Catch only the oldest part; the rest stays verbatim and comes next time.
        chunks = chunks[:MAX_CHUNKS]
        bis = chunks[-1][1]
    zusammenfassung = await _zusammenfassen(db, messages, chunks, owner_id=owner_id,
                                            agent=agent, tokens=tokens, base_urls=base_urls)
    log.info("Compaction: %d messages summarised in %d piece(s)",
             bis - von, len(chunks))

    replacement = ("# Zusammenfassung des bisherigen Verlaufs\n"
              "(Der ausführliche Verlauf wurde gekürzt, um im Kontextfenster zu bleiben. "
              "Was hier steht, ist alles, was davon bleibt — arbeite damit weiter, statt "
              "noch einmal von vorn zu beginnen.)\n\n" + zusammenfassung)

    return messages[:von] + [{"role": "system", "content": replacement}] + messages[bis:]
