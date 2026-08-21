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

_DIGEST_PROMPT = """Erstelle den Rückblick „{{titel}}" für das Zeitfenster {{window}}.
Autonom, keine Rückfragen.

Thema: {{thema}}

Recherchiere per Web-Suche aus: {{quellen}}. Nur echte, belegte Meldungen; Themen STRENG
quellenübergreifend deduplizieren; Einordnung auf {{sprache}}.

Gib das Ergebnis als **Markdown** aus (es wird zu einer HTML-Seite gerendert — KEINE
Längenbegrenzung, KEIN eigenes HTML, KEINE Telegram-Rücksicht). Struktur:

# {{symbol}} {{titel}} — Stand {{today}}

## Auf einen Blick
- {{umfang}} knappe Bulletpoints mit den wichtigsten Themen.

## Top-Meldungen
Pro Meldung:
### <Kategorie> — <Überschrift>
2–4 Sätze Einordnung. Quelle(n) als Markdown-Link.

## Diskussionen & Signale
Relevante Debatten mit Link + kurzem Kontext (warum diskutiert).

## Weitere Quellen
Wichtige Artikel mit Link + Kurzkontext.

Nutze echte URLs als Markdown-Links `[Quelle](https://…)`."""


JOB_TEMPLATES: dict[str, dict] = {
    "recherche-digest": {
        "label": "Recherche-Digest",
        "beschreibung": "Wiederkehrender Themen-Rückblick per Web-Suche, als HTML-Seite. "
                        "Thema, Quellen und Umfang kommen aus den Parametern.",
        "felder": {
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
            "titel": "KI- & Tech-News",
            "symbol": "🗞️",
            "thema": "Künstliche Intelligenz, Software und Technik allgemein",
            "sprache": "Deutsch",
            "umfang": "5–8",
            "quellen": ["Hacker News", "TechCrunch", "The Verge", "Ars Technica",
                        "MIT Tech Review", "VentureBeat",
                        "OpenAI/Anthropic/Google/Meta/NVIDIA-Blogs", "arXiv"],
        },
    },
}


def listing() -> list[dict]:
    """Templates for the selection (key, label, parameters with default values)."""
    return [{"key": k, "label": v["label"], "beschreibung": v["beschreibung"],
             "params": deepcopy(v["params"]), "felder": deepcopy(v["felder"])}
            for k, v in JOB_TEMPLATES.items()]


def apply(key: str, params: dict | None = None) -> dict:
    """Template to job fields (including `args` = default parameters, overridden by `params`).

    An unknown key raises a KeyError; the caller turns that into its own error message.
    """
    template = JOB_TEMPLATES[key]
    fields = deepcopy(template["felder"])
    fields["args"] = {**deepcopy(template["params"]), **(params or {})}
    return fields
