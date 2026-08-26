"""What happens when a run hits its budget instead of its goal.

An iteration limit, a time limit, a token budget: all three are the same statement, and it
is a technical one. The run has spent what it was given — it says nothing about whether the
work is finished, and it is never a reason to throw the work away.

Ticket runs learned that early: their flow draws an edge called `loop_exhausted` and the
next round picks the work up. Everything else did not. A chat run reported "🤖 Assistant —
Error / Iteration limit reached" to the person and lost part of their own conversation with
it; a job run ended hard on `error`. Same cause, three answers, and two of them wrong.

So the rule lives here, once:

* a run that hits the limit is continued, with what it had reached so far as its starting
  point,
* not endlessly — after `MAX_ROUNDS` continuations it stops and says so honestly, with what
  it achieved rather than with an error,
* and the person is not bothered with any of it as long as it works out.
"""
from __future__ import annotations

# How often a run may be picked up again. Three is not a measured value, it is a ceiling: a
# run that has not come to an end after four budgets is not short of budget, it is stuck, and
# a fourth round costs money without changing that.
MAX_ROUNDS = 3

# What the continuation is told about the round before it. Long enough to carry a state,
# short enough not to eat the budget of the new run.
HINT_CHARS = 2000


def may_continue(rounds: int) -> bool:
    """Is another round allowed after `rounds` continuations so far?"""
    return rounds < MAX_ROUNDS


def hint(summary: str) -> str:
    """The starting point for the next round.

    Deliberately not "you failed": the model is not to start over, it is to carry on. What it
    wrote down at the end of the last round is the only thing it still has of it.
    """
    text = (summary or "").strip()[:HINT_CHARS]
    if not text:
        return ("The round before this one ran out of budget without leaving a note. Take "
                "stock first, then carry on where it stopped.")
    return "This is a continuation. State of the round before:\n\n" + text


def paused_note(summary: str, rounds: int) -> str:
    """The honest closing message when the ceiling is reached.

    It is not an error and must not read like one: nothing broke, the work is unfinished.
    Whatever was achieved goes first — that is what the person asked for.
    """
    text = (summary or "").strip()
    head = (f"⏸ Paused after {rounds} continuations — the work is not finished, "
            f"nothing is lost. The state so far:")
    return f"{head}\n\n{text}" if text else head
