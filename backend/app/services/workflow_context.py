"""Which fields live in the context of a flow, and where they come from.

Conditions on a decision read from `instance.context`. What sits there was known only to
the code: the trigger drops its payload in, every action adds its results. The editor
offered an empty text field where you had to guess the right path, and a typo only showed
up when the branch never fired in production.

This catalog is the one place that says who writes what into the context. It feeds the
picker in the editor. It is descriptive, not binding: the truth stays in the code of the
actions, and anything missing here can still be typed by hand.
"""
from __future__ import annotations


def _f(path: str, typ: str, description: str) -> dict:
    """Ein Katalogeintrag. Die Schluessel sind englisch, weil der Editor sie liest und die
    Doku sie zeigt — der Katalog ist eine Schnittstelle, keine interne Notiz."""
    return {"path": path, "type": typ, "description": description}


# Always present, regardless of trigger and actions.
BASIS = [
    _f("event.name", "text", "Name des auslösenden Ereignisses"),
    _f("event.project_id", "number", "Projekt, aus dem das Ereignis kam"),
    _f("continuation", "number", "Wie oft der Lauf an derselben Sache schon fortgesetzt wurde"),
]

# What a trigger brings along. Key = event name (see events.BUILTIN_EVENTS).
AUSLOESER: dict[str, list[dict]] = {
    "issue.created": [
        _f("issue.key", "text", "Ticket-Kennung, z. B. ABC-31"),
        _f("issue.summary", "text", "Titel des Tickets"),
        _f("issue.type", "text", "Vorgangsart"),
        _f("issue.reporter_id", "number", "Wer es gemeldet hat"),
    ],
    "issue.assigned": [
        _f("issue.key", "text", "Ticket-Kennung"),
        _f("issue.assigned_agent", "text", "Zugewiesener Agent"),
    ],
    "issue.status_changed": [
        _f("issue.key", "text", "Ticket-Kennung"),
        _f("issue.status", "text", "Neue Board-Spalte"),
    ],
    "issue.agent_status_changed": [
        _f("issue.key", "text", "Ticket-Kennung"),
        _f("issue.agent_status", "text", "Neuer KI-Zustand (planning, plan_review, …)"),
        _f("issue.hold_reason", "text", "Grund der Blockade, falls vorhanden"),
    ],
    "issue.done": [
        _f("issue.key", "text", "Ticket-Kennung"),
        _f("issue.agent_status", "text", "Zustand (hier: done)"),
    ],
    "comment.added": [
        _f("issue.key", "text", "Ticket, an dem kommentiert wurde"),
        _f("comment.body", "text", "Text des Kommentars"),
        _f("comment.author_id", "number", "Wer kommentiert hat"),
    ],
    "hardware.status_changed": [
        _f("asset.id", "number", "Exemplar"),
        _f("asset.status", "text", "Neuer Beschaffungs-Zustand"),
    ],
    "deployment.finished": [
        _f("deployment.status", "text", "Ergebnis des Deployments"),
        _f("deployment.project_id", "number", "Betroffenes Projekt"),
    ],
    # Not an event but the payload of the trigger: a webhook (mode `workflow`) or a job
    # passes it through via `context_map`. Which fields those are is up to the trigger, the
    # ones here belong to the shipped ticket intake.
    "(Webhook/Job)": [
        _f("title", "text", "Titel der Meldung → Ticket-Titel"),
        _f("body", "text", "Inhalt der Meldung → Beschreibung"),
        _f("agent", "text", "Agent, der gleich zugewiesen wird"),
        _f("ignore", "boolean", "Meldung verwerfen statt anlegen"),
    ],
    "(Mail-Aktion)": [
        _f("mail.account", "text", "Kurzname des Postfachs"),
        _f("mail.account_id", "number", "Konto, aus dem die Mail stammt"),
        _f("mail.folder", "text", "Ordner"),
        _f("mail.uid", "number", "Nachricht im Ordner"),
        _f("mail.subject", "text", "Betreff"),
        _f("mail.from", "text", "Absenderadresse"),
        _f("mail.text", "text", "Text der Nachricht"),
        _f("mail.attachments", "list", "Alle Anhänge (index, filename, content_type, size)"),
        _f("attachment.index", "number", "Gewählter Anhang (nur beim Knopf am Anhang)"),
        _f("attachment.filename", "text", "Name des gewählten Anhangs"),
    ],
    "mail.received": [
        _f("mail.subject", "text", "Betreff"),
        _f("mail.from", "text", "Absender (Rohwert der Kopfzeile)"),
        _f("mail.account", "text", "Postfach, in dem sie liegt"),
        _f("mail.folder", "text", "Ordner"),
        _f("mail.uid", "number", "Nachrichtenkennung im Ordner"),
        _f("intake.agent", "text", "Zuständiger Assistent"),
        _f("intake.owner_id", "number", "Besitzer des Postfachs"),
        _f("intake.auto_run", "boolean", "Auslöser erzwingt einen Sofortlauf"),
    ],
}

# What an action writes into the context. Key = action name (workflow_actions).
AKTIONEN: dict[str, list[dict]] = {
    "refresh_facts": [
        _f("project.needs_acceptance", "boolean", "Projekt verlangt eine Abnahme"),
        _f("project.auto_deploy", "boolean", "Deployment läuft automatisch"),
        _f("project.auto_continue", "boolean", "Agent darf selbsttätig weitermachen"),
        _f("project.git_enabled", "boolean", "Projekt hat ein Repository"),
        _f("project.use_pull_request", "boolean", "Auslieferung über Pull Request"),
        _f("issue.has_plan", "boolean", "Es liegt ein Plan vor"),
        _f("issue.has_parent", "boolean", "Teilaufgabe eines Sammeltickets"),
        _f("issue.merge_status", "text", "Stand des Merges"),
        _f("issue.testenv_status", "text", "Stand der Testumgebung"),
        _f("issue.assigned_agent", "text", "Zugewiesener Agent"),
    ],
    "create_ticket": [
        _f("created_ticket.id", "number", "Angelegtes Ticket"),
        _f("created_ticket.key", "text", "Kennung des angelegten Tickets"),
    ],
    "set_field": [_f("fields.<schlüssel>", "text", "Gesetzte Feldwerte des Artefakts")],
    "assistant_task": [
        _f("task.task_id", "number", "Nummer des Auftrags im Assistenten-Eingang"),
        _f("task.status", "text", "approved = läuft, new = wartet auf Freigabe"),
        _f("assistant.output", "text", "Antwort des Assistenten (nur mit „warten“)"),
        _f("assistant.status", "text", "done oder error (nur mit „warten“)"),
    ],
    "mail_flag": [],
    "mail_move": [],
    "mail_attachment": [
        _f("attachment.filename", "text", "Dateiname des geholten Anhangs"),
        _f("attachment.content_type", "text", "Art der Datei (application/pdf …)"),
        _f("attachment.size", "number", "Größe in Bytes"),
        _f("attachment.base64", "text", "Der Inhalt, wie ihn Werkzeuge erwarten"),
    ],
    "answer": [
        _f("answer", "text|object", "Was dieser Lauf seinem Auslöser zurückgibt"),
    ],
    "metric_record": [
        _f("metric.value", "number", "Der eben festgehaltene Wert"),
        _f("metric.per_day", "number", "Änderung pro Tag (negativ = fällt)"),
        _f("metric.days_left", "number", "Tage bis zum Zielwert (leer, wenn unklar)"),
        _f("metric.empty_at", "text", "Datum, an dem der Zielwert erreicht wird"),
        _f("metric.fit", "number", "Wie gut die Gerade passt (0–1)"),
        _f("metric.points", "number", "Wieviele Messpunkte im Fenster liegen"),
        _f("metric.warn", "boolean", "Vorwarnzeit erreicht — jetzt Bescheid geben"),
    ],
    "series_record": [
        _f("series.kind", "text", "Art der Reihe: number, location oder text"),
        _f("series.stored", "boolean", "Wurde der Punkt gespeichert"),
        _f("series.value", "number", "Bei Zahlen: der eben festgehaltene Wert"),
        _f("series.lat", "number", "Bei Standorten: Breite des letzten Punktes"),
        _f("series.lon", "number", "Bei Standorten: Länge des letzten Punktes"),
        _f("series.battery", "number", "Akkustand, wenn das Gerät einen mitschickt"),
        _f("series.places", "list", "Orte, in denen das Gerät jetzt steht"),
        _f("series.entered", "list", "Orte, die mit diesem Punkt betreten wurden"),
        _f("series.left", "list", "Orte, die mit diesem Punkt verlassen wurden"),
        _f("series.points", "number", "Wieviele Punkte die Reihe insgesamt hat"),
    ],
    "metric_read": [
        _f("metric.value", "number", "Letzter Wert der Reihe"),
        _f("metric.alter_stunden", "number", "Wie alt der letzte Wert ist (Stunden)"),
        _f("metric.still", "boolean", "Reihe schweigt länger als erlaubt"),
        _f("metric.still_melden", "boolean", "Jetzt melden — einmal je Stille-Phase"),
        _f("metric.gefunden", "boolean", "Reihe existiert überhaupt"),
        _f("metric.days_left", "number", "Tage bis zum Zielwert (leer, wenn unklar)"),
        _f("metric.empty_at", "text", "Datum, an dem der Zielwert erreicht wird"),
        _f("metric.per_day", "number", "Änderung pro Tag (negativ = fällt)"),
        _f("metric.points", "number", "Wieviele Messpunkte im Fenster liegen"),
    ],
    "tool_call": [
        _f("tool.ok", "boolean", "Werkzeug-Aufruf war erfolgreich"),
        _f("tool.text", "text", "Antwort des Werkzeugs im Klartext"),
        _f("tool.json", "object", "Antwort zerlegt, falls sie JSON war (tiefer adressierbar)"),
        _f("tool.error", "text", "Fehlermeldung, falls der Aufruf misslang"),
    ],
    "http_request": [
        _f("http.status_code", "number", "Antwortcode der Gegenstelle"),
        _f("http.ok", "boolean", "Antwort war erfolgreich (2xx)"),
        _f("http.body", "text", "Antwortinhalt (bei JSON auch tiefer adressierbar)"),
    ],
    "accept_merge": [
        _f("merge.result", "text", "merged | conflict | pr_open | …"),
        _f("merge.escalate", "boolean", "Konflikt gehört zum Menschen"),
    ],
    "mail_classify": [
        _f("classification.category", "text", "Kategorie aus der lokalen Einordnung"),
        _f("classification.priority", "text", "Dringlichkeit"),
        _f("classification.sensitive", "boolean", "Enthält Schützenswertes"),
        _f("classification.redacted_summary", "text", "Geschwärzte Kurzfassung"),
        _f("policy.auto", "boolean", "Gelernte Regel gibt automatisch frei"),
        _f("policy.redaction", "text", "redacted | unredacted"),
    ],
    "note_append": [
        _f("note.ok", "boolean", "Notiz wurde geschrieben"),
        _f("note.error", "text", "Fehlertext, wenn nicht"),
    ],
    "spam_evaluate": [
        _f("spam.aktiv", "boolean", "Spam-Erkennung ist überhaupt eingeschaltet"),
        _f("spam.score", "number", "Gesamturteil 0..1"),
        _f("spam.rule_score", "number", "Teilurteil der Regeln"),
        _f("spam.model_score", "number", "Teilurteil des lokalen Modells"),
        _f("spam.learned_score", "number", "Teilurteil des Gedächtnisses"),
        _f("spam.serverurteil", "boolean", "Eigener Mailserver hat Spam markiert"),
        _f("spam.modellurteil", "boolean", "Lokales Modell nennt einen Betrugsversuch"),
        _f("spam.art", "text", "Als was eingestuft (phishing, werbung, rechnung …)"),
        _f("spam.befunde_text", "text", "Woran es erkannt wurde, als Satz"),
        _f("spam.bekannter_kontakt", "boolean", "Absender steht im Kontaktbestand"),
        _f("spam.geklaert", "boolean", "Gedächtnis ist sich über den Absender einig"),
        _f("spam.geklaert_urteil", "text", "spam | ham (nur wenn geklärt)"),
        _f("spam.faelschungsverdacht", "boolean", "Echtheitsprüfung fehlgeschlagen"),
        _f("spam.frage_ab", "number", "Schwelle, ab der gefragt wird"),
        _f("spam.sofort_ab", "number", "Schwelle für die sofortige Einzelkarte"),
        _f("spam.auto_ab", "number", "Schwelle für das Verschieben ohne Rückfrage"),
        _f("spam.auto_melden", "boolean",
           "Soll eine ohne Rückfrage weggeräumte Mail gemeldet werden?"),
    ],
    "spam_card": [_f("spam.verdict_id", "number", "Angelegte Urteils-Zeile"),
                  _f("spam.karte", "text", "sofort | sammel | rueckholbar"),
                  _f("spam.karte_titel", "text", "Überschrift für den Melde-Knoten"),
                  _f("spam.karte_text", "text", "Text für den Melde-Knoten"),
                  _f("spam.karte_art", "text", "spam_auto | spam_review (bestimmt die Knöpfe)"),
                  _f("spam.karte_faellig", "boolean", "Ist jetzt eine Karte dran?")],
    "spam_apply": [_f("spam.entschieden", "text", "spam | ham"),
                   _f("spam.ergebnis", "text", "Rückmeldung der IMAP-Aktion")],
    "assistant_task": [_f("task.id", "number", "Angelegtes Assistent-Item"),
                       _f("task.status", "text", "new | approved"),
                       _f("task.auto", "boolean", "Von einer gelernten Regel freigegeben")],
}

# Node types that leave something behind themselves.
KNOTEN: dict[str, list[dict]] = {
    "agent_task": [
        _f("agent.status", "text", "Ergebnis des Laufs (done, blocked, failed, …)"),
        _f("agent.stalled", "boolean", "Der Lauf kam nicht mehr voran"),
        _f("agent.has_subtickets", "boolean", "Der Plan schlägt Teilaufgaben vor"),
        _f("agent.summary", "text", "Abschlusstext des Laufs"),
        _f("agent.hold_status", "text", "Zustand, in den das Ticket dabei fällt"),
        _f("agent.hold_reason", "text", "Grund dafür"),
    ],
    "wait_event": [
        _f("event.name", "text", "Welches Ereignis den Lauf geweckt hat"),
    ],
    "subflow": [
        _f("subflow.outcome", "text", "Ausgang des Kind-Ablaufs"),
    ],
}


def katalog() -> dict:
    """The whole catalog, the way the editor needs it."""
    return {"base": BASIS, "triggers": AUSLOESER, "actions": AKTIONEN, "nodes": KNOTEN}
