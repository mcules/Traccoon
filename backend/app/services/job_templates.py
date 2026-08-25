"""Job templates: one pattern plus parameters instead of a copied prompt.

The occasion was the AI and tech news job. Its prompt was good, but it described its topic,
its sources and its structure in one piece; a second digest (security, radio, law …) would
have been a copy with three changed lines that drifts apart on the next improvement.

Since the research jobs all run through ONE flow (`services/research_flow`), a template no
longer delivers a prompt but the start context of that flow: assignment, agent, store, and
the word at which the job stays silent. Templates are code, not data: they should grow with
the prompt craft without anybody having to maintain existing jobs afterwards.
"""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy.ext.asyncio import AsyncSession

from . import assistant_cleanup_flow, research_flow

# Placeholders of the flow language have NO place in here: `{{…}}` is replaced exactly one
# round, and an assignment is itself a context value — its braces would stay put literally.
# What the run knows (date, window, last run) the flow appends by itself.
_DIGEST_AUFTRAG = """Write the review "<title>" for the window given in the facts below.
Work on your own, ask nothing back.

Topic: <what it is about>

Research it over web search from: <sources, comma separated>. Only real, sourced reports.
Deduplicate topics STRICTLY across sources. Write the assessment in German: a person reads
this, and they read German.

Return the result as **markdown** (it is rendered into an HTML page, so there is NO length
limit, NO HTML of your own and NO need to spare a messenger). Structure:

# <title>, as of <put the date from "Today" in the facts below here>

## At a glance
- 5 to 8 short bullet points with the most important topics.

## Top reports
Per report:
### <category>: <headline>
2 to 4 sentences of assessment. Source or sources as a markdown link.

## Discussions and signals
Relevant debates with a link and short context (why it is being discussed).

## Further sources
Important articles with a link and short context.

Use real URLs as markdown links `[source](https://…)`."""

# A watcher is the same flow with the other two knobs: no store, and a word that keeps it
# quiet. Without that word it would report every morning that there is nothing to report.
_WATCH_SENTINEL = "KEIN_NEUZUGANG"
_WATCH_AUFTRAG = f"""THE RULE ABOVE ALL OTHERS: silence is the normal case.
If there is nothing new, answer with exactly one word and nothing else: {_WATCH_SENTINEL}
No preamble, no summary, no reasoning, no listing of what is already known. Only that one
word. The job reports only when your answer does NOT contain it, and on almost every day
{_WATCH_SENTINEL} is the right answer. The other way round: on a real new arrival
{_WATCH_SENTINEL} must not appear anywhere in your answer.

Task: check whether anything new has turned up about <topic> since the last successful run
(see "Facts about this run" below).

Sources:
1. <address>
2. <address>

What is already known, the baseline, never report it: <what already exists>.

On a real new arrival, per hit: <what should be reported>.

A reminder: nothing new means the answer is exactly {_WATCH_SENTINEL}. The answer itself is
written in German, a person reads it."""


JOB_TEMPLATES: dict[str, dict] = {
    "research-digest": {
        "label": "Research digest",
        "description": "A recurring review of a topic over web search, filed as a page. "
                        "Assignment, agent and store come out of the start context.",
        "fields": {
            "type": "cron",
            "schedule": "0 6 * * *",
            "kind": "workflow",
            # The flow by NAME, not by number: an id is a fact of this one database.
            # `with_flow` turns it into the number before it reaches a job.
            "workflow_key": research_flow.KEY,
        },
        "params": {
            "auftrag": _DIGEST_AUFTRAG,
            "agent": "news",
            # Its own key per job — two digests in one store would overwrite each other's
            # history.
            "ablage": "digest",
            "still_wenn": "",
        },
    },
    "unterhaltungen-aufraeumen": {
        "label": "Clear out old conversations",
        "description": "Deletes closed conversations of the assistant older than 90 days. "
                       "The five most recent ones stay in any case, and whatever is running "
                       "or still open is never touched.",
        "fields": {
            "type": "cron",
            # At night and weekly: this is housekeeping, not an event.
            "schedule": "20 4 * * 0",
            "kind": "workflow",
            "workflow_key": assistant_cleanup_flow.KEY,
        },
        "params": {
            "closed_only": True,
            "older_than_days": 90,
            "keep_last": 5,
            "agent": "",
        },
    },
    "research-watch": {
        "label": "Research watcher",
        "description": "Looks daily for something new and stays SILENT while there is none. "
                        "Reports only a real addition.",
        "fields": {
            "type": "cron",
            "schedule": "12 7 * * *",
            "kind": "workflow",
            "workflow_key": research_flow.KEY,
        },
        "params": {
            "auftrag": _WATCH_AUFTRAG,
            "agent": "news",
            "ablage": "",
            "still_wenn": _WATCH_SENTINEL,
        },
    },
}


async def with_flow(db: AsyncSession, fields: dict) -> dict:
    """Turn `workflow_key` into the `workflow_definition_id` of THIS database.

    Without the flow the field stays away: the form then shows an empty flow picker, which is
    honest, instead of a number that points at nothing.
    """
    key = fields.pop("workflow_key", "")
    if not key:
        return fields
    module = {research_flow.KEY: research_flow,
              assistant_cleanup_flow.KEY: assistant_cleanup_flow}.get(key)
    d = await module.find(db) if module is not None else None
    if d is not None and d.current_version_id:
        fields["workflow_definition_id"] = d.id
    return fields


def listing() -> list[dict]:
    """Templates for the selection (key, label, parameters with default values)."""
    return [{"key": k, "label": v["label"], "description": v["description"],
             "params": deepcopy(v["params"]), "fields": deepcopy(v["fields"])}
            for k, v in JOB_TEMPLATES.items()]


async def listing_for(db: AsyncSession) -> list[dict]:
    """The same listing, with the flow resolved — this is what the form needs."""
    entries = listing()
    for entry in entries:
        entry["fields"] = await with_flow(db, entry["fields"])
    return entries


def apply(key: str, params: dict | None = None) -> dict:
    """Template to job fields (including `args` = default parameters, overridden by `params`).

    An unknown key raises a KeyError; the caller turns that into its own error message. The
    flow is still in there as `workflow_key` — whoever creates a job has to pass the fields
    through `with_flow` first.
    """
    template = JOB_TEMPLATES[key]
    fields = deepcopy(template["fields"])
    fields["args"] = {**deepcopy(template["params"]), **(params or {})}
    return fields
