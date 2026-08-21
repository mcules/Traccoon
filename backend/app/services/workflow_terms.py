"""Die Begriffe der Abläufe — englisch, mit Gedächtnis für die deutschen.

Aktionsnamen, ihre Parameter und die Felder, die sie in den Kontext schreiben, waren
gemischt: `messwert` neben `set_status`, `auftrag` neben `prompt`, `{{ akku.rest_tage }}`
neben `{{ tool.status }}`. Wer einen Ablauf baut, musste bei jedem Feld raten, in welcher
Sprache dieses eine gemeint ist.

Englisch ist die Vorgabe. Die deutschen Namen bleiben lesbar — sie stehen in
veröffentlichten Fassungen, und die sind unveränderlich, solange Instanzen daran hängen.
Deshalb zwei Wege:

* `normalisiere_aktion` / `normalisiere_params` übersetzen beim Ausführen. Ein alter Graph
  läuft damit weiter, ohne dass er angefasst wurde.
* `migriere_alle` schreibt die gespeicherten Graphen um — Aktionsnamen, Parameter,
  `{{ … }}`-Pfade und die `var`-Pfade der Weichen. Danach steht überall dasselbe Wort.

Die Kontext-Wörter sind der heikle Teil: Sie stehen nicht als Schlüssel im Graphen, sondern
mitten in Texten (`{{ akku.rest_tage }} Tage`) und in Bedingungen. Ersetzt wird deshalb
segmentweise und nur hinter einem Punkt — `wert` allein bleibt, `akku.wert` wird
`akku.value`.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("traccoon.terms")

# Woran eine Fassung erkennt, dass sie schon umgeschrieben wurde. Ohne diese Marke liefe die
# Umstellung ein zweites Mal über ihr eigenes Ergebnis — und `assistant_task` (neu: der
# allgemeine Auftrag) würde zum Mail-Weg, weil derselbe Name früher genau das hieß.
MARKE = "terms"
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

# Nur für die einmalige Umstellung, NICHT beim Ausführen: In einem alten Graphen meinte
# `assistant_task` den Mail-Eingang (`mail_actions`), in einem neuen den allgemeinen
# Auftrag. Der Mail-Weg behält seinen Altnamen unter einem Namen, der nicht kollidiert.
EINMALIG: dict[str, str] = {
    "assistant_task": "mail_assistant_task",
    "assistant_card": "mail_assistant_card",
    "assistant_run": "mail_assistant_run",
}

# ── Parameter ───────────────────────────────────────────────────────────────
# Viele hatten schon ein englisches Gegenstück, das der Code als Zweitname las; hier steht
# jetzt, welches gilt.
PARAMS: dict[str, str] = {
    # Nachzuegler aus der Umstellung auf durchgehend englische Namen (Stand en8). Sie stehen
    # als Schluessel in gespeicherten Graphen; ohne diese drei Zeilen liest die Aktion nach
    # der Umbenennung ins Leere, und ein Zweig, der auf einen Parameter wartet, greift nie.
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
# Was Aktionen in den Kontext schreiben, und wie Abläufe es lesen (`{{ akku.rest_tage }}`).
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

# Was ein Ablauf ohne Punkt liest: die Zeitwerte, die jeder wiederkehrende Lauf mitbekommt.
# Sie stehen allein in der Klammer (`{{ zeitfenster }}`), deshalb reicht die Pfad-Ersetzung
# nicht — die greift erst ab dem ersten Punkt.
CONTEXT_EINZELN: dict[str, str] = {
    "heute": "today",
    "jetzt": "now",
    "seit": "since",
    "zeitfenster": "window",
}

# Die Filter der Vorlagensprache (`{{ quellen | verbinde:", " }}`).
FILTER: dict[str, str] = {
    "gross": "upper", "klein": "lower", "trimmen": "trim", "kurz": "truncate",
    "ersetze": "replace", "mal": "times", "rund": "round", "betrag": "abs",
    "anzahl": "count", "erstes": "first", "letztes": "last", "verbinde": "join",
    "feld": "field", "dateiname": "basename", "zeilen_mit": "lines_with",
    "datum": "date", "plus_zeit": "add_time",
}

# Die Schlüssel, unter denen Aktionen ihr Ergebnis ablegen, wenn niemand etwas anderes sagt.
# Sie stehen als erstes Segment in den Pfaden (`{{ messreihe.wert }}`).
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
    # Nachzuegler aus dem Mail-Weg und der Notiz-Aktion (Stand en5). Sie standen in
    # gespeicherten Graphen als `{{ eingang.owner_id }}`, `{{ klasse.category }}` und
    # `{{ notiz.ok }}` — ohne diese drei Zeilen zeigen die nach der Umbenennung ins Leere.
    "eingang": "intake",
    "klasse": "classification",
    "notiz": "note",
}


def normalise_action(name: str) -> str:
    return ACTIONS.get(name, name)


def normalise_params(params: dict) -> dict:
    """Deutsche Parameter auf ihre englischen Namen. Ein schon englischer bleibt stehen.

    Steht beides im selben Knoten (aus einer halb umgestellten Fassung), gewinnt der
    englische — er ist der, den der Editor geschrieben hat.
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

def _pfade_replace(text: str) -> str:
    """`{{ akku.rest_tage }}` → `{{ akku.days_left }}`, auch mit Filtern dahinter."""
    def eine(m: re.Match) -> str:
        # Dieselbe Zerlegung wie beim Auswerten: ein `|` in Anführungszeichen trennt nicht.
        from .workflow_expr import _parts

        parts = _parts(m.group(1))
        header = _segmente(parts[0].strip())
        # Hinter dem Strich stehen Filter, nicht Pfade: `verbinde:", "` wird `join:", "`,
        # sein Argument bleibt, wie es ist.
        remainder = []
        for t in parts[1:]:
            name, doppelpunkt, arg = t.strip().partition(":")
            remainder.append(FILTER.get(name, name) + (doppelpunkt + arg if doppelpunkt else ""))
        return "{{ " + " | ".join([header, *remainder]) + " }}"
    return re.sub(r"\{\{([^}]*)\}\}", eine, text)


def _segmente(inhalt: str) -> str:
    """Punktpfade im Ausdruck übersetzen, den Rest (Filter, Text) unangetastet lassen."""
    def path(m: re.Match) -> str:
        parts = m.group(0).split(".")
        if len(parts) == 1:
            # Ein Wort ohne Punkt ist nur dann ein Kontextname, wenn wir ihn kennen —
            # alles andere gehört dem Menschen (`{{ titel }}` aus seinen Job-Parametern).
            return CONTEXT_EINZELN.get(parts[0], parts[0])
        header = CONTEXT_KEY.get(parts[0], parts[0])
        remainder = [CONTEXT.get(t, t) for t in parts[1:]]
        return ".".join([header, *remainder])
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", path, inhalt)


def _ist_kontextpfad(value: str) -> bool:
    """Ist dieser nackte String ein Kontextpfad, dessen erstes Segment umbenannt wurde?

    Absichtlich eng: Ein `path` steht auch fuer eine Vault-Datei, eine URL oder ein
    Verzeichnis. Angefasst wird nur, was mit einem Namen beginnt, den die Tabelle kennt —
    alles andere bleibt Zeichen fuer Zeichen, wie es war.
    """
    header = value.split(".", 1)[0]
    return "." in value and "/" not in value and " " not in value and header in CONTEXT_KEY


def _var_replace(node):
    """Die `var`-Pfade der Weichen (JSONLogic) mitnehmen.

    Und die nackten Pfade, die nicht in `{{ }}` stehen: Ein Empfaenger wird als
    `{"mode": "context", "path": "intake.owner_id"}` genannt, ohne Klammern. Ohne diesen
    Zweig blieb genau dieser Pfad bei einer Umbenennung stehen und zeigte danach ins Leere —
    die Meldung ging an niemanden, und auffallen wuerde das erst, wenn jemand sie vermisst.
    """
    if isinstance(node, dict):
        aus = {}
        for k, v in node.items():
            if k == "var" and isinstance(v, str):
                aus[k] = _segmente(v)
            elif k == "path" and isinstance(v, str) and _ist_kontextpfad(v):
                aus[k] = _segmente(v)
            else:
                aus[k] = _var_replace(v)
        return aus
    if isinstance(node, list):
        return [_var_replace(v) for v in node]
    if isinstance(node, str):
        return _pfade_replace(node)
    return node


def migrate_graph(graph: dict) -> tuple[dict, bool]:
    """Einen Graphen auf die englischen Begriffe bringen. Gibt (Graph, geändert) zurück.

    Genau einmal: Die Marke am Graphen hält fest, dass er umgeschrieben ist. Ein zweiter
    Durchgang würde die Namen weiterdrehen, die beim ersten ihre Bedeutung gewechselt haben.
    """
    import json

    if (graph or {}).get(MARKE) == STATE:
        return graph, False
    # Die Namen, die ihre Bedeutung gewechselt haben, werden GENAU EINMAL umgebogen: beim
    # ersten Durchgang. Ein späterer Nachlauf (neue Wörter in der Tabelle) darf sie nicht
    # noch einmal anfassen, sonst würde aus dem allgemeinen Auftrag der Mail-Weg.
    erstmals = not (graph or {}).get(MARKE)
    vorher = json.dumps(graph, sort_keys=True, ensure_ascii=False)
    new = {**graph, MARKE: STATE, "nodes": []}
    for node in graph.get("nodes") or []:
        node = dict(node)
        daten = dict(node.get("data") or {})
        cfg = dict(daten.get("config") or {})
        action = cfg.get("action")
        if isinstance(action, dict):
            name = str(action.get("action") or "")
            params = _var_replace(normalise_params(action.get("params") or {}))
            # Der Wert von `context_key` IST ein Kontextname; ohne ihn zeigten die Pfade
            # daneben ins Leere („{{ result.output }}“ neben `context_key: ergebnis“).
            if isinstance(params.get("context_key"), str):
                params["context_key"] = CONTEXT_KEY.get(params["context_key"],
                                                               params["context_key"])
            action = {**action,
                      "action": normalise_action(EINMALIG.get(name, name) if erstmals else name),
                      "params": params}
            cfg["action"] = action
        elif isinstance(action, str):
            cfg["action"] = normalise_action(EINMALIG.get(action, action) if erstmals
                                                else action)
        # Die ganze Config durchlaufen statt einer Liste von Feldern. Die Liste hier war
        # eine Aufzählung dessen, woran jemand gedacht hatte — `assignee.path` stand nicht
        # darin und blieb bei einer Umbenennung stehen. `_var_ersetzen` fasst ohnehin nur
        # an, was wie ein Pfad aussieht.
        for field in list(cfg):
            if field != "action":
                cfg[field] = _var_replace(cfg[field])
        daten["config"] = cfg
        node["data"] = daten
        new["nodes"].append(node)
    new["edges"] = graph.get("edges") or []
    return new, json.dumps(new, sort_keys=True, ensure_ascii=False) != vorher


async def migrate_all(db) -> int:
    """Alle gespeicherten Fassungen umschreiben. Gibt die Anzahl der geänderten zurück.

    Auch veröffentlichte: Es ist eine Umbenennung, kein anderer Ablauf — und eine Instanz,
    die daran hängt, liest ihren Graphen über dieselbe Zeile. Ließe man sie stehen, gälte in
    der Hälfte der Abläufe weiter die alte Sprache.
    """
    from sqlalchemy import select

    from ..models.workflow import WorkflowVersion

    changed = 0
    for v in (await db.execute(select(WorkflowVersion))).scalars().all():
        new, anders = migrate_graph(v.graph or {})
        if anders:
            v.graph = new
            changed += 1
    if changed:
        await db.commit()
        log.info("%s Ablauf-Fassungen auf die englischen Begriffe umgeschrieben", changed)
    return changed
