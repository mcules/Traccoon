"""Job templates: one pattern plus parameters instead of a copied prompt.

The occasion was the AI and tech news job. Its prompt was good, but it described its topic,
its sources and its structure in one piece; a second digest (security, radio, law …) would
have been a copy with three changed lines that drifts apart on the next improvement.

A template delivers the prompt plus defaults; what distinguishes one job from another stands
in `params` (becoming `jobs.args`) and is inserted at the run over `job_params.rendere`.
Templates are code, not data: they should grow with the prompt craft without anybody having
to maintain existing jobs afterwards.
"""
from __future__ import annotations

from copy import deepcopy

_DIGEST_PROMPT = """Write the review "{{title}}" for the window {{window}}.
Autonomous, no questions back.

Topic: {{topic}}

Research it over web search from: {{sources}}. Only real, sourced reports; deduplicate topics
STRICTLY across sources; write the assessment in {{language}}.

Give the result as **Markdown** (it is rendered into an HTML page — NO length limit, NO HTML
of your own, NO consideration for Telegram). Structure:

# {{symbol}} {{title}} — as of {{today}}

## At a glance
- {{scope}} terse bullet points with the most important topics.

## Top reports
Per report:
### <category> — <headline>
2-4 sentences of assessment. The source(s) as a Markdown link.

## Discussions and signals
Relevant debates with a link plus a short context (why it is being discussed).

## Further sources
Important articles with a link plus a short context.

Use real URLs as Markdown links `[source](https://…)`."""


JOB_TEMPLATES: dict[str, dict] = {
    "research-digest": {
        "label": "Research digest",
        "description": "A recurring review of a topic over web search, as an HTML page. "
                        "Topic, sources and scope come out of the parameters.",
        "fields": {
            "type": "cron",
            "schedule": "0 6 * * *",
            "kind": "prompt",
            "result_html": True,
            "notify_mode": "always",
            "run_timeout": 900,
            "prompt": _DIGEST_PROMPT,
        },
        # The default is the proven AI and tech digest. Whoever wants another topic changes
        # parameters, not the prompt.
        "params": {
            "title": "AI and tech news",
            "symbol": "🗞️",
            "topic": "Artificial intelligence, software and technology in general",
            "language": "English",
            "scope": "5-8",
            "sources": ["Hacker News", "TechCrunch", "The Verge", "Ars Technica",
                        "MIT Tech Review", "VentureBeat",
                        "OpenAI/Anthropic/Google/Meta/NVIDIA-Blogs", "arXiv"],
        },
    },
}


def listing() -> list[dict]:
    """Templates for the selection (key, label, parameters with default values)."""
    return [{"key": k, "label": v["label"], "description": v["description"],
             "params": deepcopy(v["params"]), "fields": deepcopy(v["fields"])}
            for k, v in JOB_TEMPLATES.items()]


def apply(key: str, params: dict | None = None) -> dict:
    """Template to job fields (including `args` = default parameters, overridden by `params`).

    An unknown key raises a KeyError; the caller turns that into its own error message.
    """
    template = JOB_TEMPLATES[key]
    fields = deepcopy(template["fields"])
    fields["args"] = {**deepcopy(template["params"]), **(params or {})}
    return fields
