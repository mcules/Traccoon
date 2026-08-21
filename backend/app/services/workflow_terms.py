"""The vocabulary of the flows — English, with a memory for the German words.

Action names, their parameters and the fields they write into the context were mixed:
`messwert` next to `set_status`, `auftrag` next to `prompt`, `{{ akku.rest_tage }}` next to
`{{ tool.status }}`. Whoever built a flow had to guess for every field which language this
particular one was meant in.

English is the default. The German names stay readable — they appear in published versions,
and those are immutable as long as instances hang on them.
Deshalb zwei Wege:

* `normalisiere_aktion` / `normalisiere_params` translate at run time. An old graph keeps
  running that way without having been touched.
* `migriere_alle` rewrites the stored graphs — action names, parameters, `{{ … }}` paths and
  the `var` paths of the decisions. Afterwards the same word stands everywhere.

The context words are the delicate part: they do not appear as keys in the graph but in the
middle of texts (`{{ akku.rest_tage }} Tage`) and in conditions. Replacement therefore
happens segment by segment and only after a dot — `wert` alone stays, `akku.wert` becomes
`akku.value`.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("traccoon.terms")

# How a version recognises that it has already been rewritten. Without this mark the
# conversion would run a second time over its own result — and `assistant_task` (now: the
# general assignment) would become the mail path, because that name used to mean exactly that.
MARK = "terms"
STATE = "en7"

# ── Aktionen ────────────────────────────────────────────────────────────────
ACTIONS: dict[str, str] = {
    "agent_lauf": "agent_run",
    "antwort": "answer",
    "assistent_auftrag": "assistant_task",
    "dokument": "document",
    "dokument_lesen": "document_read",
    "job_pausieren": "job_pause",
    "mail_anhang": "mail_attachment",
    "messreihe_lesen": "metric_read",
    "messwert": "metric_record",
    "notiz_anhaengen": "note_append",
    "skript": "script",
}

# Only for the one-off conversion, NOT at run time: in an old graph `assistant_task` meant the
# mail intake (`mail_actions`), in a new one the general assignment. The mail path keeps its
# legacy name under a name that does not collide.
EINMALIG: dict[str, str] = {
    "assistant_task": "mail_assistant_task",
    "assistant_card": "mail_assistant_card",
    "assistant_run": "mail_assistant_run",
}

# ── Parameter ───────────────────────────────────────────────────────────────
# Many already had an English counterpart that the code read as a second name; here stands
# jetzt, welches gilt.
PARAMS: dict[str, str] = {
    # Latecomers from the switch to consistently English names (state en8). They appear as
    # keys in stored graphs; without these three lines the action reads into the void after
    # the rename, and a branch waiting for a parameter never fires.
    "vorentschieden": "predecided",
    "rueckholbar": "recoverable",
    "melden": "report",
    "ablage": "storage",
    "argumente": "args",
    "art": "kind",
    "auftrag": "task",
    "befehl": "command",
    "behalten": "keep",
    "bezug": "ref",
    "drossel_key": "throttle_key",
    "drossel_minuten": "throttle_minutes",
    "einheit": "unit",
    "felder": "fields",
    "fenster_tage": "window_days",
    "freigabe": "approval",
    "hinweis": "hint",
    "kanal": "channel",
    "kategorie": "category",
    "pfad": "path",
    "pflicht": "required",
    "prioritaet": "priority",
    "quelle": "source",
    "referenz": "reference",
    "reihe": "series",
    "schwaerzen": "redaction",
    "still_ab": "silent_from",
    "still_stunden": "silence_hours",
    "timeout_sek": "timeout_sec",
    "titel": "title",
    "ueberschrift": "heading",
    "volltext": "full_text",
    "vorwarn_tage": "warn_days",
    "warten": "wait",
    "weiter": "resume",
    "werkzeug": "tool",
    "wert": "value",
    "wiederholungen": "retries",
    "warte_sek": "retry_wait_sec",
    "ziel": "target",
    "zusammenfassung": "summary",
}

# ── Kontext-Wörter ──────────────────────────────────────────────────────────
# What actions write into the context, and how flows read it (`{{ akku.rest_tage }}`).
CONTEXT: dict[str, str] = {
    "alter_stunden": "age_hours",
    "ablage": "storage",
    "auftrag": "task",
    "einheit": "unit",
    "erster_am": "first_at",
    "erster_wert": "first_value",
    "gefunden": "found",
    "grund": "reason",
    "guete": "fit",
    "ignoriert": "ignored",
    "leer_am": "empty_at",
    "letzter_am": "last_at",
    "letzter_guter": "last_good",
    "pro_tag": "per_day",
    "punkte": "points",
    "reihe": "series",
    "rest_tage": "days_left",
    "roh": "raw",
    "still": "silent",
    "still_melden": "report_silence",
    "still_stunden": "silence_hours",
    "titel": "title",
    "uebersprungen": "skipped",
    "vorwarn_tage": "warn_days",
    "warnen": "warn",
    "wert": "value",
    "ziel": "target",
    "ziel_datum": "target_date",
}

# What a flow reads without a dot: the time values every recurring run is given. They stand
# alone inside the braces (`{{ zeitfenster }}`), which is why path replacement is not enough
# — that one only takes effect from the first dot onwards.
CONTEXT_EINZELN: dict[str, str] = {
    "heute": "today",
    "jetzt": "now",
    "seit": "since",
    "zeitfenster": "window",
}

# The filters of the template language (`{{ quellen | verbinde:", " }}`).
FILTER: dict[str, str] = {
    "gross": "upper", "klein": "lower", "trimmen": "trim", "kurz": "truncate",
    "ersetze": "replace", "mal": "times", "rund": "round", "betrag": "abs",
    "anzahl": "count", "erstes": "first", "letztes": "last", "verbinde": "join",
    "feld": "field", "dateiname": "basename", "zeilen_mit": "lines_with",
    "datum": "date", "plus_zeit": "add_time",
}

# The keys under which actions store their result when nobody says otherwise. They stand as
# the first segment in the paths (`{{ messreihe.wert }}`).
CONTEXT_KEY: dict[str, str] = {
    "messreihe": "metric",
    "dokument": "document",
    "lauf": "run",
    "assistent": "assistant",
    "anhang_daten": "attachment",
    "skript": "script",
    "auftrag": "task",
    "antwort": "answer",
    "ergebnis": "result",
    # Latecomers from the mail path and the note action (state en5). They stood in stored
    # graphs as `{{ eingang.owner_id }}`, `{{ klasse.category }}` and `{{ notiz.ok }}` —
    # without these three lines those point into the void after the rename.
    "eingang": "intake",
    "klasse": "classification",
    "notiz": "note",
}


def normalise_action(name: str) -> str:
    return ACTIONS.get(name, name)


def normalise_params(params: dict) -> dict:
    """German parameters to their English names. An already English one stays put.

    If both appear in the same node (from a half-converted version), the English one wins —
    it is the one the editor wrote.
    """
    if not isinstance(params, dict):
        return params
    aus: dict = {}
    for key, value in params.items():
        new = PARAMS.get(key, key)
        if new in aus and key != new:
            continue
        aus[new] = value
    return aus


# ── Migration gespeicherter Graphen ─────────────────────────────────────────

def _paths_replace(text: str) -> str:
    """`{{ akku.rest_tage }}` → `{{ akku.days_left }}`, filters behind it included."""
    def one(m: re.Match) -> str:
        # The same split as when evaluating: a `|` inside quotes does not separate.
        from .workflow_expr import _parts

        parts = _parts(m.group(1))
        header = _segments(parts[0].strip())
        # Behind the bar stand filters, not paths: `verbinde:", "` becomes `join:", "`, its
        # argument stays as it is.
        remainder = []
        for t in parts[1:]:
            name, colon, arg = t.strip().partition(":")
            remainder.append(FILTER.get(name, name) + (colon + arg if colon else ""))
        return "{{ " + " | ".join([header, *remainder]) + " }}"
    return re.sub(r"\{\{([^}]*)\}\}", one, text)


def _segments(content: str) -> str:
    """Translate dotted paths in the expression, leave the rest (filters, text) untouched."""
    def path(m: re.Match) -> str:
        parts = m.group(0).split(".")
        if len(parts) == 1:
            # A word without a dot is a context name only if we know it — everything else
            # belongs to the person (`{{ titel }}` from their job parameters).
            return CONTEXT_EINZELN.get(parts[0], parts[0])
        header = CONTEXT_KEY.get(parts[0], parts[0])
        remainder = [CONTEXT.get(t, t) for t in parts[1:]]
        return ".".join([header, *remainder])
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", path, content)


def _is_contextpath(value: str) -> bool:
    """Ist dieser nackte String ein Kontextpfad, dessen erstes Segment umbenannt wurde?

    Deliberately narrow: a `path` also stands for a vault file, a URL or a directory. Only
    what begins with a name the table knows is touched — everything else stays character for
    character as it was.
    """
    header = value.split(".", 1)[0]
    return "." in value and "/" not in value and " " not in value and header in CONTEXT_KEY


def _var_replace(node):
    """Take the `var` paths of the decisions (JSONLogic) along.

    And the bare paths that do not sit in `{{ }}`: a recipient is named as
    `{"mode": "context", "path": "intake.owner_id"}`, without braces. Without this branch
    exactly that path stayed put across a rename and pointed into the void afterwards — the
    report went to nobody, and that would be noticed only once somebody missed it.
    """
    if isinstance(node, dict):
        aus = {}
        for k, v in node.items():
            if k == "var" and isinstance(v, str):
                aus[k] = _segments(v)
            elif k == "path" and isinstance(v, str) and _is_contextpath(v):
                aus[k] = _segments(v)
            else:
                aus[k] = _var_replace(v)
        return aus
    if isinstance(node, list):
        return [_var_replace(v) for v in node]
    if isinstance(node, str):
        return _paths_replace(node)
    return node


def migrate_graph(graph: dict) -> tuple[dict, bool]:
    """Bring a graph onto the English vocabulary. Returns (graph, changed).

    Exactly once: the mark on the graph records that it has been rewritten. A second pass
    would turn the names further that changed their meaning during the first.
    """
    import json

    if (graph or {}).get(MARK) == STATE:
        return graph, False
    # The names that changed their meaning are bent over EXACTLY ONCE: during the first pass.
    # A later follow-up (new words in the table) must not touch them again, otherwise the
    # general assignment would turn into the mail path.
    firsttime = not (graph or {}).get(MARK)
    before = json.dumps(graph, sort_keys=True, ensure_ascii=False)
    new = {**graph, MARK: STATE, "nodes": []}
    for node in graph.get("nodes") or []:
        node = dict(node)
        data = dict(node.get("data") or {})
        cfg = dict(data.get("config") or {})
        action = cfg.get("action")
        if isinstance(action, dict):
            name = str(action.get("action") or "")
            params = _var_replace(normalise_params(action.get("params") or {}))
            # The value of `context_key` IS a context name; without it the paths pointed
            # daneben ins Leere („{{ result.output }}“ neben `context_key: ergebnis“).
            if isinstance(params.get("context_key"), str):
                params["context_key"] = CONTEXT_KEY.get(params["context_key"],
                                                               params["context_key"])
            action = {**action,
                      "action": normalise_action(EINMALIG.get(name, name) if firsttime else name),
                      "params": params}
            cfg["action"] = action
        elif isinstance(action, str):
            cfg["action"] = normalise_action(EINMALIG.get(action, action) if firsttime
                                                else action)
        # Walk the whole config instead of a list of fields. The list here was an enumeration
        # of what somebody had thought of — `assignee.path` was not in it and stayed put
        # across a rename. `_var_ersetzen` only touches what looks like a path anyway.
        for field in list(cfg):
            if field != "action":
                cfg[field] = _var_replace(cfg[field])
        data["config"] = cfg
        node["data"] = data
        new["nodes"].append(node)
    new["edges"] = graph.get("edges") or []
    return new, json.dumps(new, sort_keys=True, ensure_ascii=False) != before


async def migrate_all(db) -> int:
    """Rewrite all stored versions. Returns the number of changed ones.

    Published ones too: it is a rename, not a different flow — and an instance hanging on one
    reads its graph through the same line. Leaving them put would mean the old language kept
    applying in half of the flows.
    """
    from sqlalchemy import select

    from ..models.workflow import WorkflowVersion

    changed = 0
    for v in (await db.execute(select(WorkflowVersion))).scalars().all():
        new, different = migrate_graph(v.graph or {})
        if different:
            v.graph = new
            changed += 1
    if changed:
        await db.commit()
        log.info("%s Ablauf-Fassungen auf die englischen Begriffe umgeschrieben", changed)
    return changed
