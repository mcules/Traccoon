"""Welche Felder im Kontext eines Ablaufs stehen — und woher sie kommen.

Bedingungen an einer Verzweigung lesen aus `instance.context`. Was dort steht, wusste
bisher nur der Code: der Auslöser legt seine Nutzlast hinein, jede Aktion schreibt ihre
Ergebnisse dazu. Im Editor blieb ein leeres Textfeld, in das man den richtigen Pfad raten
musste — und ein Tippfehler fiel erst auf, wenn der Zweig im Betrieb nie griff.

Dieser Katalog ist die eine Stelle, an der steht, wer was in den Kontext schreibt. Er
speist die Auswahl im Editor. Er ist bewusst **beschreibend**, nicht bindend: die Wahrheit
bleibt der Code der Aktionen; wer hier etwas vergisst, kann den Pfad weiterhin von Hand
eintragen (freie Eingabe bleibt möglich).
"""
from __future__ import annotations


def _f(pfad: str, typ: str, beschreibung: str) -> dict:
    return {"pfad": pfad, "typ": typ, "beschreibung": beschreibung}


# Immer vorhanden, unabhängig von Auslöser und Aktionen.
BASIS = [
    _f("event.name", "text", "Name des auslösenden Ereignisses"),
    _f("event.project_id", "zahl", "Projekt, aus dem das Ereignis kam"),
    _f("continuation", "zahl", "Wie oft der Lauf an derselben Sache schon fortgesetzt wurde"),
]

# Was ein Auslöser mitbringt. Schlüssel = Ereignisname (siehe events.BUILTIN_EVENTS).
AUSLOESER: dict[str, list[dict]] = {
    "issue.created": [
        _f("issue.key", "text", "Ticket-Kennung, z. B. TRA-31"),
        _f("issue.summary", "text", "Titel des Tickets"),
        _f("issue.type", "text", "Vorgangsart"),
        _f("issue.reporter_id", "zahl", "Wer es gemeldet hat"),
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
        _f("comment.author_id", "zahl", "Wer kommentiert hat"),
    ],
    "hardware.status_changed": [
        _f("asset.id", "zahl", "Exemplar"),
        _f("asset.status", "text", "Neuer Beschaffungs-Zustand"),
    ],
    "deployment.finished": [
        _f("deployment.status", "text", "Ergebnis des Deployments"),
        _f("deployment.project_id", "zahl", "Betroffenes Projekt"),
    ],
    # Kein Ereignis, sondern die Nutzlast des Auslösers: ein Webhook (Modus `workflow`)
    # oder ein Job reicht sie über `context_map` durch. Welche Felder das sind, bestimmt
    # der Auslöser — die hier sind die des ausgelieferten Ticket-Eingangs.
    "(Webhook/Job)": [
        _f("title", "text", "Titel der Meldung → Ticket-Titel"),
        _f("body", "text", "Inhalt der Meldung → Beschreibung"),
        _f("agent", "text", "Agent, der gleich zugewiesen wird"),
        _f("ignore", "ja/nein", "Meldung verwerfen statt anlegen"),
    ],
    "mail.received": [
        _f("mail.subject", "text", "Betreff"),
        _f("mail.from", "text", "Absender (Rohwert der Kopfzeile)"),
        _f("mail.account", "text", "Postfach, in dem sie liegt"),
        _f("mail.folder", "text", "Ordner"),
        _f("mail.uid", "zahl", "Nachrichtenkennung im Ordner"),
        _f("eingang.agent", "text", "Zuständiger Assistent"),
        _f("eingang.owner_id", "zahl", "Besitzer des Postfachs"),
        _f("eingang.auto_run", "ja/nein", "Auslöser erzwingt einen Sofortlauf"),
    ],
}

# Was eine Aktion in den Kontext schreibt. Schlüssel = Aktionsname (workflow_actions).
AKTIONEN: dict[str, list[dict]] = {
    "refresh_facts": [
        _f("project.needs_acceptance", "ja/nein", "Projekt verlangt eine Abnahme"),
        _f("project.auto_deploy", "ja/nein", "Deployment läuft automatisch"),
        _f("project.auto_continue", "ja/nein", "Agent darf selbsttätig weitermachen"),
        _f("project.git_enabled", "ja/nein", "Projekt hat ein Repository"),
        _f("project.use_pull_request", "ja/nein", "Auslieferung über Pull Request"),
        _f("issue.has_plan", "ja/nein", "Es liegt ein Plan vor"),
        _f("issue.has_parent", "ja/nein", "Teilaufgabe eines Sammeltickets"),
        _f("issue.merge_status", "text", "Stand des Merges"),
        _f("issue.testenv_status", "text", "Stand der Testumgebung"),
        _f("issue.assigned_agent", "text", "Zugewiesener Agent"),
    ],
    "create_ticket": [
        _f("created_ticket.id", "zahl", "Angelegtes Ticket"),
        _f("created_ticket.key", "text", "Kennung des angelegten Tickets"),
    ],
    "set_field": [_f("fields.<schlüssel>", "text", "Gesetzte Feldwerte des Artefakts")],
    "messwert": [
        _f("messreihe.wert", "zahl", "Der eben festgehaltene Wert"),
        _f("messreihe.pro_tag", "zahl", "Änderung pro Tag (negativ = fällt)"),
        _f("messreihe.rest_tage", "zahl", "Tage bis zum Zielwert (leer, wenn unklar)"),
        _f("messreihe.leer_am", "text", "Datum, an dem der Zielwert erreicht wird"),
        _f("messreihe.guete", "zahl", "Wie gut die Gerade passt (0–1)"),
        _f("messreihe.punkte", "zahl", "Wieviele Messpunkte im Fenster liegen"),
        _f("messreihe.warnen", "ja/nein", "Vorwarnzeit erreicht — jetzt Bescheid geben"),
    ],
    "messreihe_lesen": [
        _f("messreihe.wert", "zahl", "Letzter Wert der Reihe"),
        _f("messreihe.alter_stunden", "zahl", "Wie alt der letzte Wert ist (Stunden)"),
        _f("messreihe.still", "ja/nein", "Reihe schweigt länger als erlaubt"),
        _f("messreihe.still_melden", "ja/nein", "Jetzt melden — einmal je Stille-Phase"),
        _f("messreihe.gefunden", "ja/nein", "Reihe existiert überhaupt"),
        _f("messreihe.rest_tage", "zahl", "Tage bis zum Zielwert (leer, wenn unklar)"),
        _f("messreihe.leer_am", "text", "Datum, an dem der Zielwert erreicht wird"),
        _f("messreihe.pro_tag", "zahl", "Änderung pro Tag (negativ = fällt)"),
        _f("messreihe.punkte", "zahl", "Wieviele Messpunkte im Fenster liegen"),
    ],
    "tool_call": [
        _f("tool.ok", "ja/nein", "Werkzeug-Aufruf war erfolgreich"),
        _f("tool.text", "text", "Antwort des Werkzeugs im Klartext"),
        _f("tool.json", "objekt", "Antwort zerlegt, falls sie JSON war (tiefer adressierbar)"),
        _f("tool.error", "text", "Fehlermeldung, falls der Aufruf misslang"),
    ],
    "http_request": [
        _f("http.status_code", "zahl", "Antwortcode der Gegenstelle"),
        _f("http.ok", "ja/nein", "Antwort war erfolgreich (2xx)"),
        _f("http.body", "text", "Antwortinhalt (bei JSON auch tiefer adressierbar)"),
    ],
    "accept_merge": [
        _f("merge.result", "text", "merged | conflict | pr_open | …"),
        _f("merge.escalate", "ja/nein", "Konflikt gehört zum Menschen"),
    ],
    "mail_classify": [
        _f("klasse.category", "text", "Kategorie aus der lokalen Einordnung"),
        _f("klasse.priority", "text", "Dringlichkeit"),
        _f("klasse.sensitive", "ja/nein", "Enthält Schützenswertes"),
        _f("klasse.redacted_summary", "text", "Geschwärzte Kurzfassung"),
        _f("policy.auto", "ja/nein", "Gelernte Regel gibt automatisch frei"),
        _f("policy.redaction", "text", "redacted | unredacted"),
    ],
    "spam_evaluate": [
        _f("spam.aktiv", "ja/nein", "Spam-Erkennung ist überhaupt eingeschaltet"),
        _f("spam.score", "zahl", "Gesamturteil 0..1"),
        _f("spam.rule_score", "zahl", "Teilurteil der Regeln"),
        _f("spam.model_score", "zahl", "Teilurteil des lokalen Modells"),
        _f("spam.learned_score", "zahl", "Teilurteil des Gedächtnisses"),
        _f("spam.serverurteil", "ja/nein", "Eigener Mailserver hat Spam markiert"),
        _f("spam.bekannter_kontakt", "ja/nein", "Absender steht im Kontaktbestand"),
        _f("spam.geklaert", "ja/nein", "Gedächtnis ist sich über den Absender einig"),
        _f("spam.geklaert_urteil", "text", "spam | ham (nur wenn geklärt)"),
        _f("spam.faelschungsverdacht", "ja/nein", "Echtheitsprüfung fehlgeschlagen"),
        _f("spam.frage_ab", "zahl", "Schwelle, ab der gefragt wird"),
        _f("spam.sofort_ab", "zahl", "Schwelle für die sofortige Einzelkarte"),
        _f("spam.auto_ab", "zahl", "Schwelle für das Verschieben ohne Rückfrage"),
    ],
    "spam_card": [_f("spam.verdict_id", "zahl", "Angelegte Urteils-Zeile"),
                  _f("spam.karte", "text", "sofort | sammel | rueckholbar")],
    "spam_apply": [_f("spam.entschieden", "text", "spam | ham"),
                   _f("spam.ergebnis", "text", "Rückmeldung der IMAP-Aktion")],
    "assistant_task": [_f("task.id", "zahl", "Angelegtes Assistent-Item"),
                       _f("task.status", "text", "new | approved"),
                       _f("task.auto", "ja/nein", "Von einer gelernten Regel freigegeben")],
}

# Knotentypen, die selbst etwas hinterlassen.
KNOTEN: dict[str, list[dict]] = {
    "agent_task": [
        _f("agent.status", "text", "Ergebnis des Laufs (done, blocked, failed, …)"),
        _f("agent.stalled", "ja/nein", "Der Lauf kam nicht mehr voran"),
        _f("agent.has_subtickets", "ja/nein", "Der Plan schlägt Teilaufgaben vor"),
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
    """Der ganze Katalog, wie ihn der Editor braucht."""
    return {"basis": BASIS, "ausloeser": AUSLOESER, "aktionen": AKTIONEN, "knoten": KNOTEN}
