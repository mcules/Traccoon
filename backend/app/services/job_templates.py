"""Job-Vorlagen: ein Muster + Parameter statt eines kopierten Prompts.

Anlass war der KI-&-Tech-News-Job. Sein Prompt war gut, aber er beschrieb sein Thema, seine
Quellen und seinen Aufbau in einem Stück — ein zweiter Digest (Security, Funk, Recht …) wäre
eine Kopie mit drei geänderten Zeilen gewesen, die beim nächsten Verbessern auseinanderläuft.

Eine Vorlage liefert Prompt + Voreinstellungen; was den einen Job vom anderen unterscheidet,
steht in `params` (→ `jobs.args`) und wird beim Lauf über `job_params.rendere` eingesetzt.
Vorlagen sind Code, keine Daten: sie sollen mit dem Prompt-Handwerk mitwachsen, ohne dass
jemand bestehende Jobs nachpflegt.
"""
from __future__ import annotations

from copy import deepcopy

_DIGEST_PROMPT = """Erstelle den Rückblick „{{titel}}" für das Zeitfenster {{zeitfenster}}.
Autonom, keine Rückfragen.

Thema: {{thema}}

Recherchiere per Web-Suche aus: {{quellen}}. Nur echte, belegte Meldungen; Themen STRENG
quellenübergreifend deduplizieren; Einordnung auf {{sprache}}.

Gib das Ergebnis als **Markdown** aus (es wird zu einer HTML-Seite gerendert — KEINE
Längenbegrenzung, KEIN eigenes HTML, KEINE Telegram-Rücksicht). Struktur:

# {{symbol}} {{titel}} — Stand {{heute}}

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
        # Voreinstellung = der bewährte KI-&-Tech-Digest. Wer ein anderes Thema will, ändert
        # Parameter — nicht den Prompt.
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


def liste() -> list[dict]:
    """Vorlagen für die Auswahl (Schlüssel, Beschriftung, Parameter mit Vorgabewerten)."""
    return [{"key": k, "label": v["label"], "beschreibung": v["beschreibung"],
             "params": deepcopy(v["params"]), "felder": deepcopy(v["felder"])}
            for k, v in JOB_TEMPLATES.items()]


def anwenden(key: str, params: dict | None = None) -> dict:
    """Vorlage → Job-Felder (inkl. `args` = Vorgabe-Parameter, von `params` überschrieben).

    Unbekannter Schlüssel → KeyError; der Aufrufer macht daraus seine eigene Fehlermeldung.
    """
    vorlage = JOB_TEMPLATES[key]
    felder = deepcopy(vorlage["felder"])
    felder["args"] = {**deepcopy(vorlage["params"]), **(params or {})}
    return felder
