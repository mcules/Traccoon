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
_DIGEST_AUFTRAG = """Erstelle den Rückblick „<Titel>" für das Zeitfenster aus den Angaben unten.
Autonom, keine Rückfragen.

Thema: <worum es geht>

Recherchiere per Web-Suche aus: <Quellen, mit Komma getrennt>. Nur echte, belegte Meldungen;
Themen STRENG quellenübergreifend deduplizieren; Einordnung auf Deutsch.

Gib das Ergebnis als **Markdown** aus (es wird zu einer HTML-Seite gerendert — KEINE
Längenbegrenzung, KEIN eigenes HTML, KEINE Telegram-Rücksicht). Struktur:

# 🗞️ <Titel> — Stand <hier das Datum aus „Heute" in den Angaben unten einsetzen>

## Auf einen Blick
- 5–8 knappe Bulletpoints mit den wichtigsten Themen.

## Top-Meldungen
Pro Meldung:
### <Kategorie> — <Überschrift>
2–4 Sätze Einordnung. Quelle(n) als Markdown-Link.

## Diskussionen & Signale
Relevante Debatten mit Link + kurzem Kontext (warum diskutiert).

## Weitere Quellen
Wichtige Artikel mit Link + Kurzkontext.

Nutze echte URLs als Markdown-Links `[Quelle](https://…)`."""

# A watcher is the same flow with the other two knobs: no store, and a word that keeps it
# quiet. Without that word it would report every morning that there is nothing to report.
_WATCH_SENTINEL = "KEIN_NEUZUGANG"
_WATCH_AUFTRAG = f"""REGEL VOR ALLEM ANDEREN — Schweigen ist der Normalfall.
Wenn es nichts Neues gibt, antworte mit genau einem Wort und sonst nichts: {_WATCH_SENTINEL}
Keine Einleitung, keine Zusammenfassung, keine Begründung, keine Aufzählung des bekannten
Stands. Nur dieses eine Wort. Der Job meldet nur dann, wenn deine Antwort dieses Wort NICHT
enthält — an den allermeisten Tagen ist {_WATCH_SENTINEL} die richtige Antwort. Umgekehrt:
Bei einem echten Neuzugang darf {_WATCH_SENTINEL} nirgends in deiner Antwort stehen.

Aufgabe: Prüfe, ob es seit dem letzten erfolgreichen Lauf (siehe „Angaben zu diesem Lauf"
unten) etwas Neues zu <Thema> gibt.

Quellen:
1. <Adresse>
2. <Adresse>

Bekannter Stand, das ist die Grundlinie, niemals melden: <was es schon gibt>.

Bei echtem Neuzugang pro Treffer: <was gemeldet werden soll>.

Zur Erinnerung: nichts Neues = die Antwort ist genau {_WATCH_SENTINEL}."""


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
        "label": "Alte Unterhaltungen aufräumen",
        "description": "Löscht geschlossene Unterhaltungen des Assistenten, die älter als 90 "
                       "Tage sind — die fünf jüngsten bleiben in jedem Fall. Was gerade "
                       "läuft und was offen ist, wird nie angefasst.",
        "fields": {
            "type": "cron",
            # Nachts und wöchentlich: es ist Hausputz, kein Ereignis.
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
