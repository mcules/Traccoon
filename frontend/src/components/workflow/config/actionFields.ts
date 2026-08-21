import type { AutoActionName } from "../types";

/**
 * What an action can configure, written out per action so that it is directly visible in the
 * editor which fields exist and which values are allowed. Without this description only an
 * empty key/value field remained, in which one had to guess the right names.
 *
 * `options` can be static or filled at runtime (board columns, agents, members); see
 * `ActionParams.tsx`.
 */
export type FieldType = "text" | "textarea" | "number" | "boolean" | "select" | "kv" | "json";

export interface FieldSpec {
  key: string;
  label: string;
  type: FieldType;
  /** Auswahlwerte: [Wert, Beschriftung]. */
  options?: [string, string][];
  /** Runtime source for the selection. */
  source?: "board_status" | "agent_role" | "member" | "artifact_status" | "artifact_field"
    | "mcp_tool" | "person";
  placeholder?: string;
  hint?: string;
  /** Prefill as long as nothing is set (important with yes/no fields). */
  default?: boolean;
  /** Show the field only when another field has this value. */
  showIf?: [string, string[]];
  required?: boolean;
}

export interface ActionSpec {
  /** One sentence: what the action does. */
  summary: string;
  fields: FieldSpec[];
  /** Exits the action produces (asynchronous actions). */
  outcomes?: string;
  /** Which subjects the action makes sense for. Empty = for all. */
  subjects?: ("issue" | "hardware_asset" | "standalone")[];
}

const AGENT_STATUS: [string, string][] = [
  ["planning", "option.planning"],
  ["plan_review", "option.plan_waiting_for_approval"],
  ["approved", "option.approved_execution_may_start"],
  ["in_progress", "option.progress"],
  ["to_test", "option.ready_review"],
  ["testing", "option.review"],
  ["done", "option.done"],
  ["hold", "option.on_hold_waiting_for_a_person"],
  ["failed", "option.failed"],
  ["open", "option.open"],
];

const HOLD_REASON: [string, string][] = [
  ["", "option.automatic"],
  ["plan_review", "option.plan_approval"],
  ["plan_split", "option.split_approval"],
  ["question", "option.question"],
  ["permission", "option.permission"],
  ["review", "option.review_findings"],
  ["merge", "option.merge_conflict"],
  ["stuck", "option.stuck"],
  ["cap", "option.cost_limit"],
  ["interrupted", "option.interrupted"],
  ["incomplete", "option.incomplete"],
  ["verify", "option.check_pending"],
];

const TO_MODE: [string, string][] = [
  ["user", "option.specific_person"],
  ["role", "option.project_role"],
  ["reporter", "option.reporter_ticket"],
  ["context", "option.from_the_context_user_id"],
];

const NO: ActionSpec = { summary: "", fields: [] };

export const ACTION_SPECS: Record<AutoActionName, ActionSpec> = {
  http_request: { summary: "action_fields.calls_a_configured_destination", fields: [] },  // eigene Maske

  set_context: {
    summary: "action_fields.writes_values_into_the_flow_context_usable_la",
    fields: [{ key: "", label: "Zuweisungen", type: "kv",
               hint: "action_fields.values_may_contain_path_from_the_context" }],
  },

  comment: {
    summary: "action_fields.writes_a_comment_on_the_bound_ticket",
    fields: [{ key: "text", label: "Text", type: "textarea", required: true,
               placeholder: "action_fields.the_agent_reported_agent_summary" }],
  },

  notify: {
    summary: "action_fields.sends_a_notification_the_bell_always_outside",
    fields: [
      { key: "to.mode", label: "action_fields.recipient", type: "select", options: TO_MODE },
      { key: "to.user_id", label: "Person", type: "select", source: "person",
        showIf: ["to.mode", ["user"]] },
      { key: "to.role", label: "Rolle", type: "select", showIf: ["to.mode", ["role"]],
        options: [["owner", "Owner"], ["maintainer", "Maintainer"], ["member", "Mitglied"],
                  ["viewer", "Leser"]] },
      { key: "to.path", label: "Kontext-Pfad", type: "text", showIf: ["to.mode", ["context"]],
        placeholder: "freigeber_id" },
      { key: "title", label: "Betreff", type: "text", placeholder: "action_fields.issue_key_note" },
      { key: "text", label: "Text", type: "textarea" },
      { key: "throttle_minutes", label: "action_fields.most_every_minutes", type: "number",
        hint: "action_fields.0_off_the_flow_still_runs_on_every_call_only"},
      { key: "throttle_key", label: "action_fields.throttle_key", type: "text",
        placeholder: "shelter.diebstahl",
        hint: "action_fields.what_counts_as_the_same_message_empty_this_no"},
      { key: "channel", label: "Weg", type: "select",
        options: [["", "option.default_person"], ["telegram", "option.telegram"], ["email", "option.e_mail"]],
        hint: "action_fields.leaving_it_empty_is_the_normal_case_everyone"},
      { key: "kind", label: "Art", type: "text", placeholder: "workflow_notify",
        hint: "Entscheidet, welche Knöpfe der Bot anhängt (z. B. spam_auto = „zurückholen“). Leer = gewöhnliche Meldung ohne Knöpfe." },
      { key: "ref", label: "Bezug", type: "kv",
        placeholder: "spam_verdict_id: {{ spam.verdict_id }}",
        hint: "Worauf sich die Knöpfe beziehen: spam_verdict_id, assistant_task_id, issue_id oder project_id." },
    ],
  },

  mail_flag: {
    summary: "Markiert eine Mail als gelesen, wichtig oder beantwortet.",
    fields: [
      { key: "flag", label: "Markierung", type: "select",
        options: [["seen", "gelesen"], ["flagged", "wichtig"], ["answered", "beantwortet"]] },
      { key: "on", label: "Setzen", type: "boolean",
        hint: "An: Markierung setzen. Aus: wieder wegnehmen (z. B. als ungelesen)." },
      { key: "folder", label: "Ordner", type: "text",
        hint: "Leer = der Ordner der Mail aus dem Auslöser." },
      { key: "uid", label: "Nummer der Mail", type: "number",
        hint: "Leer = die Mail aus dem Auslöser. {{ mail.uid }} tut dasselbe." },
    ],
    outcomes: "Kontext danach: unverändert. Ergebnis: flag, on, uid.",
  },

  mail_move: {
    summary: "Verschiebt eine Mail in einen Ordner — ohne Ziel ins Archiv des Kontos.",
    fields: [
      { key: "target", label: "Zielordner", type: "text",
        placeholder: "Archive/2026",
        hint: "Leer = das Archiv des Kontos, samt Muster ({jahr}, {monat})." },
      { key: "folder", label: "Ordner", type: "text",
        hint: "Leer = der Ordner der Mail aus dem Auslöser." },
      { key: "uid", label: "Nummer der Mail", type: "number",
        hint: "Leer = die Mail aus dem Auslöser." },
    ],
    outcomes: "Kontext danach: unverändert. Ergebnis: moved, target.",
  },

  mail_attachment: {
    subjects: ["standalone"],
    summary: "Holt den Anhang einer Mail in den Kontext (Base64) — fuer Werkzeuge, die eine Datei erwarten.",
    fields: [
      { key: "index", label: "Anhang", type: "number",
        hint: "Leer lassen: der Anhang, an dem der Knopf haengt. Eine Zahl waehlt einen bestimmten." },
      { key: "context_key", label: "Ablegen unter", type: "text", placeholder: "anhang_daten",
        hint: "{{ attachment.base64 }} ist danach der Inhalt, {{ attachment.filename }} der Name." },
      { key: "max_mb", label: "Groesse (MB)", type: "number",
        hint: "Groesser wird nicht geholt: der Inhalt steht im Kontext des Laufs. Vorgabe 25." },
    ],
    outcomes: "Kontext danach: <Ablegen unter>.filename, .content_type, .size, .base64",
  },

  agent_run: {
    summary: "Lässt einen Agenten arbeiten und wartet auf sein Ergebnis — ohne Ticket, ohne Eingang.",
    fields: [
      { key: "task", label: "Auftrag", type: "textarea", required: true,
        placeholder: "Fasse die Meldungen aus {{ zeitfenster }} zusammen.",
        hint: "Was der Agent tun soll. {{ pfad }} holt Werte aus dem Kontext." },
      { key: "agent", label: "Agent", type: "select", source: "agent_role",
        hint: "Wer arbeitet. Leer = der persönliche Assistent." },
      { key: "title", label: "Titel", type: "text",
        hint: "Überschrift des Laufs. Leer = erste Zeile des Auftrags." },
      { key: "context_key", label: "Ergebnis unter", type: "text", placeholder: "lauf",
        hint: "Kontext-Schlüssel: {{ run.output }} ist der Text, {{ run.status }} sagt, ob es geklappt hat." },
      { key: "timeout_sec", label: "Zeitgrenze (Sekunden)", type: "number",
        hint: "0 = Vorgabe der Engine." },
      { key: "wait", label: "Auf das Ergebnis warten", type: "boolean",
        hint: "Aus: nur anstoßen. Dann steht danach kein Ergebnis im Kontext." },
    ],
    outcomes: "Kontext danach: <Ergebnis unter>.output, .status, .summary.",
  },

  script: {
    summary: "Führt ein hinterlegtes Skript aus (nur was im erlaubten Verzeichnis liegt).",
    fields: [
      { key: "command", label: "Skript", type: "text", required: true,
        placeholder: "pruefe.sh",
        hint: "Datei im Skript-Verzeichnis des Servers. Pfade darüber hinaus laufen nicht." },
      { key: "args", label: "Argumente", type: "json", placeholder: '["-x", "42"]' },
      { key: "timeout_sec", label: "Zeitgrenze (Sekunden)", type: "number", placeholder: "600" },
      { key: "context_key", label: "Ergebnis unter", type: "text", placeholder: "script" },
    ],
    outcomes: "Kontext danach: <Ergebnis unter>.output, .exit_code, .ok.",
  },

  document: {
    summary: "Legt einen Text in einer Ablage ab — mit Verlauf, wie ein Messwert in seiner Reihe.",
    fields: [
      { key: "storage", label: "Ablage", type: "text", required: true,
        placeholder: "ki-tech-news",
        hint: "Schlüssel der Ablage. Sie entsteht beim ersten Ablegen." },
      { key: "text", label: "Text", type: "textarea", required: true,
        placeholder: "{{ result.output }}",
        hint: "Was abgelegt wird. Leerer Text legt nichts ab — eine leere Fassung verdrängte nur eine echte." },
      { key: "title", label: "Überschrift", type: "text",
        hint: "Leer = die erste Überschrift oder Zeile des Textes." },
      { key: "name", label: "Name der Ablage", type: "text",
        hint: "Nur beim ersten Mal gebraucht, für die Übersicht." },
      { key: "format", label: "Format", type: "select",
        options: [["markdown", "Markdown"], ["text", "Nur Text"]] },
      { key: "keep", label: "Fassungen behalten", type: "number", placeholder: "60" },
      { key: "context_key", label: "Verweis unter", type: "text", placeholder: "document",
        hint: "{{ document.url }} ist der Link, {{ document.titel }} die Überschrift." },
    ],
    outcomes: "Kontext danach: <Verweis unter>.id, .ablage, .titel, .url",
  },

  document_read: {
    summary: "Holt die letzte Fassung einer Ablage in den Kontext — für Abläufe, die auf dem letzten Mal aufbauen.",
    fields: [
      { key: "storage", label: "Ablage", type: "text", required: true, placeholder: "ki-tech-news" },
      { key: "context_key", label: "Ergebnis unter", type: "text", placeholder: "document" },
    ],
    outcomes: "Kontext danach: <Ergebnis unter>.gefunden, .titel, .text, .ts",
  },

  job_pause: {
    summary: "Hält den Zeitplan an, aus dem dieser Lauf kommt — die Erinnerung, die aufhört.",
    fields: [
      { key: "job_id", label: "Job", type: "number",
        hint: "Leer = der Job, der diesen Lauf gestartet hat ({{ job.id }})." },
      { key: "resume", label: "Stattdessen fortsetzen", type: "boolean",
        hint: "An: den Job wieder laufen lassen, statt ihn anzuhalten." },
    ],
    outcomes: "Kontext danach: unverändert. Der Zeitplan steht (oder läuft wieder).",
  },

  assistant_task: {
    summary: "Gibt dem persönlichen Assistenten einen freien Auftrag — ohne Mail, ohne Ticket, ohne Projekt.",
    fields: [
      { key: "task", label: "Auftrag", type: "textarea", required: true,
        placeholder: "Lies Paperless-Dokument {{ doc_id }} und halte Wissenswertes im Vault fest.",
        hint: "Der Auftrag selbst. {{ pfad }} holt Werte aus dem Kontext." },
      { key: "title", label: "Titel", type: "text",
        placeholder: "Paperless #{{ doc_id }}",
        hint: "Überschrift im Assistenten-Eingang. Leer = erste Zeile des Auftrags." },
      { key: "agent", label: "Agent", type: "select", source: "agent_role",
        hint: "Wer den Auftrag ausführt. Leer = der persönliche Assistent." },
      { key: "approval", label: "Erst freigeben lassen", type: "boolean",
        hint: "An: der Auftrag wartet im Eingang auf deinen Menschen. Aus: er läuft sofort." },
      { key: "wait", label: "Auf das Ergebnis warten", type: "boolean",
        hint: "An: der Schritt bleibt stehen, bis der Lauf fertig ist — nur dann kann der Ablauf mit der Antwort weiterarbeiten." },
      { key: "context_key", label: "Ergebnis unter", type: "text", placeholder: "assistent",
        showIf: ["warten", ["true"]],
        hint: "Kontext-Schlüssel für das Ergebnis: {{ assistant.output }} ist die Antwort." },
      { key: "timeout_sec", label: "Zeitgrenze (Sekunden)", type: "number",
        showIf: ["warten", ["true"]],
        hint: "0 = Vorgabe der Engine. Eine Rückfrage des Assistenten kann die Grenze reißen." },
      { key: "priority", label: "Priorität", type: "select",
        options: [["normal", "normal"], ["low", "niedrig"], ["high", "hoch"], ["urgent", "dringend"]] },
      { key: "category", label: "Kategorie", type: "text",
        hint: "Wonach der Eingang sortiert wird. Frei wählbar." },
      { key: "reference", label: "Schlüssel gegen Doppelanlage", type: "text",
        placeholder: "{{ eingang.source_ref }}",
        hint: "Die Sache, um die es geht (z. B. Konto:UID einer Mail). Leer = der Knoten selbst, dann beauftragt ein zweiter Anlauf desselben Ablaufs nicht doppelt." },
      { key: "summary", label: "Zusammenfassung", type: "textarea",
        hint: "Kurzfassung für den Eingang, wenn der Volltext nicht dorthin soll." },
      { key: "full_text", label: "Volltext", type: "textarea",
        hint: "Wird nur gespeichert, wenn nicht geschwärzt wird — sonst läge das, wovor die Schwärzung schützt, gleich daneben." },
      { key: "redaction", label: "Umgang mit dem Volltext", type: "select",
        options: [["redacted", "schwärzen (Vorgabe)"], ["unredacted", "Volltext behalten"]] },
      { key: "hint", label: "Hinweis für den Assistenten", type: "text",
        hint: "Was mit der Sache geschehen soll, wenn es nicht im Auftrag steht." },
    ],
    outcomes: "Kontext danach: task.task_id, task.status sowie task.id/status. Mit „warten\" zusätzlich <Ergebnis unter>.output und .status.",
  },

  answer: {
    summary: "Legt fest, was dieser Lauf an seinen Auslöser zurückgibt (ein wartender Webhook liest genau das).",
    fields: [
      { key: "text", label: "Antwort (Text)", type: "textarea",
        placeholder: "{{ assistant.output }}",
        hint: "Freier Text. Für eine strukturierte Antwort dieses Feld leer lassen und unten Felder setzen." },
      { key: "fields", label: "Antwort (Felder)", type: "kv",
        hint: "Schlüssel → Wert, Werte mit {{ pfad }}. Ein Objekt hier IST der Rumpf der Antwort." },
      { key: "context_key", label: "Ablegen unter", type: "text", placeholder: "answer",
        hint: "Nur ändern, wenn der Webhook eine eigene Zuordnung benutzt." },
    ],
    outcomes: "Kontext danach: antwort (Text oder Objekt).",
  },

  note_append: {
    summary: "action_fields.appends_a_line_to_a_note_in_the_vault_creatin",
    fields: [
      { key: "path", label: "action_fields.path_note", type: "text", required: true,
        placeholder: "04 Wissen/Erkennung/{{ spam.art }}.md",
        hint: "action_fields.the_path_may_come_from_the_context_that_way_e" },
      { key: "text", label: "Text", type: "textarea", required: true,
        placeholder: "- {{ spam.sender_domain }}: {{ spam.befunde_text }}" },
      { key: "heading", label: "action_fields.section_optional", type: "text",
        hint: "action_fields.created_when_it_does_not_exist_yet" },
    ],
    outcomes: "action_fields.context_afterwards_notiz_ok_notiz_error",
  },

  series_record: {
    summary: "action_fields.writes_a_point_into_a_data_series_number_loca",
    subjects: ["standalone"],
    fields: [
      { key: "series", label: "Reihe", type: "text", required: true,
        placeholder: "tracker.shelter",
        hint: "action_fields.key_of_the_series_it_comes_into_being_with_it" },
      { key: "kind", label: "Art", type: "select",
        options: [["number", "Zahl"], ["location", "Standort"], ["text", "Text"]],
        hint: "action_fields.only_applies_when_the_series_is_created_an_ex" },
      { key: "value", label: "Wert", type: "text", showIf: ["kind", ["number", ""]],
        placeholder: "{{ position.attributes.batteryLevel }}" },
      { key: "lat", label: "Breite", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.latitude }}" },
      { key: "lon", label: "Länge", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.longitude }}" },
      { key: "accuracy", label: "Genauigkeit (m)", type: "text", showIf: ["kind", ["location"]] },
      { key: "altitude", label: "Höhe (m)", type: "text", showIf: ["kind", ["location"]] },
      { key: "speed", label: "Tempo", type: "text", showIf: ["kind", ["location"]] },
      { key: "course", label: "Richtung", type: "text", showIf: ["kind", ["location"]] },
      { key: "battery", label: "Akku (%)", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.attributes.batteryLevel }}" },
      { key: "title", label: "Überschrift", type: "text", showIf: ["kind", ["text"]] },
      { key: "body", label: "Text", type: "textarea", showIf: ["kind", ["text"]] },
      { key: "ts", label: "Zeitpunkt", type: "text",
        placeholder: "{{ position.serverTime }}",
        hint: "action_fields.empty_means_now_unix_seconds_or_iso_time" },
      { key: "name", label: "Anzeigename", type: "text", placeholder: "Shelter" },
      { key: "color", label: "Farbe", type: "text", placeholder: "#3b82f6" },
      { key: "required", label: "action_fields.fail_without_value", type: "boolean",
        hint: "action_fields.normally_a_report_without_a_value_is_not_an_e" },
    ],
    outcomes: "action_fields.context_afterwards_series_kind_stored_value_o",
  },
  metric_record: {
    summary: "action_fields.writes_a_number_into_a_series_and_reads_off_w",
    fields: [
      { key: "series", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.key_of_the_series_same_key_means_same_series" },
      { key: "value", label: "Wert", type: "text", required: true,
        placeholder: "action_fields.position_attributes_batterylevel" },
      { key: "unit", label: "Einheit", type: "text", placeholder: "%" },
      { key: "name", label: "Anzeigename", type: "text", placeholder: "action_fields.battery_shelter" },
      { key: "min", label: "action_fields.smallest_valid_value", type: "number",
        hint: "action_fields.devices_report_nonsense_when_they_do_not_know"},
      { key: "max", label: "action_fields.largest_valid_value", type: "number" },
      { key: "target", label: "Zielwert", type: "number",
        hint: "action_fields.value_the_series_runs_towards_0_means_empty" },
      { key: "warn_days", label: "action_fields.early_warning_days", type: "number",
        hint: "action_fields.how_early_to_warn_0_turns_the_warning_off_it"},
      { key: "window_days", label: "action_fields.trend_window_days", type: "number",
        hint: "action_fields.how_far_back_the_line_reads_default_30" },
    ],
    outcomes: "action_fields.context_afterwards_messreihe_wert_pro_tag_res",
  },

  metric_read: {
    summary: "action_fields.looks_at_a_series_without_feeding_it_and_noti",
    fields: [
      { key: "series", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.path_allowed_so_the_same_flow_checks_several"},
      { key: "silence_hours", label: "action_fields.quiet_after_hours", type: "number",
        hint: "action_fields.0_do_not_check_reported_once_per_phase_of_sil"},
      { key: "target", label: "Zielwert", type: "number" },
      { key: "window_days", label: "action_fields.trend_window_days", type: "number" },
    ],
    outcomes: "action_fields.context_afterwards_messreihe_wert_alter_stund",
  },

  webhook: {
    summary: "action_fields.calls_a_free_url_for_a_far_side_you_use_again",
    fields: [
      { key: "url", label: "URL", type: "text", required: true,
        placeholder: "https://example.com/hook" },
      { key: "method", label: "Methode", type: "select",
        options: [["POST", "POST"], ["GET", "GET"], ["PUT", "PUT"], ["PATCH", "PATCH"],
                  ["DELETE", "DELETE"]] },
      { key: "headers", label: "Kopfzeilen", type: "kv" },
      { key: "payload", label: "Body", type: "json" },
      { key: "secret", label: "action_fields.secret_from_the_vault", type: "text",
        hint: "action_fields.name_in_the_secret_vault_available_in_the_cal" },
      { key: "timeout_sec", label: "action_fields.timeout_s", type: "number" },
    ],
  },

  create_ticket: {
    summary: "action_fields.creates_a_new_ticket_in_the_project_of_the_fl",
    fields: [
      { key: "summary", label: "Titel", type: "text", required: true,
        placeholder: "action_fields.fault_event_name" },
      { key: "description", label: "Beschreibung", type: "textarea" },
      { key: "assigned_agent", label: "action_fields.assign_agent", type: "select", source: "agent_role",
        hint: "action_fields.empty_nobody_assigning_starts_the_lifecycle" },
      { key: "start_agent_status", label: "Startzustand", type: "select", options: AGENT_STATUS },
      { key: "project_id", label: "action_fields.other_project_id", type: "number" },
      { key: "context_key", label: "action_fields.result_in_the_context_under", type: "text",
        placeholder: "created_ticket" },
    ],
  },

  refresh_facts: {
    subjects: ["issue"],
    summary: "action_fields.reads_project_and_ticket_settings_into_the_co",
    fields: [],
  },

  set_field: {
    summary: "action_fields.sets_a_custom_field_of_the_artifact_the_flow",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "field", label: "Feld", type: "select", source: "artifact_field", required: true },
      { key: "values", label: "Wert(e)", type: "text", required: true,
        hint: "action_fields.separate_several_by_comma_templates_from_the" },
      { key: "mode", label: "Vorgehen", type: "select", options: [
          ["set", "option.replace"], ["add", "option.add"], ["remove", "option.remove"]],
        hint: "action_fields.adding_and_removing_only_pays_off_on_multi_se" },
    ],
  },

  set_status: {
    summary: "action_fields.sets_the_state_of_the_artifact_the_flow_hangs",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "status", label: "Zustand", type: "select", source: "artifact_status",
        required: true },
      { key: "reason", label: "Grund", type: "select", options: HOLD_REASON,
        showIf: ["__subject", ["issue"]],
        hint: "action_fields.tickets_only_it_separates_plan_approval_from" },
      { key: "notify", label: "Benachrichtigen", type: "boolean", default: true,
        hint: "action_fields.reports_plan_approval_review_errors_and_block" },
    ],
  },


  set_board_status: {
    summary: "action_fields.moves_the_ticket_into_a_board_column",
    subjects: ["issue"],
    fields: [
      { key: "status", label: "Spalte", type: "select", source: "board_status" },
      { key: "category", label: "action_fields.category", type: "select",
        options: [["", "—"], ["todo", "To Do"], ["in_progress", "In Arbeit"], ["done", "option.done"]],
        hint: "action_fields.applies_when_no_column_with_a_matching_name_e" },
    ],
  },

  assign_agent: {
    summary: "action_fields.assigns_an_agent_to_the_ticket",
    subjects: ["issue"],
    fields: [{ key: "agent", label: "Agent", type: "select", source: "agent_role", required: true }],
  },

  set_cap_baseline: {
    subjects: ["issue"],
    summary: "action_fields.resets_the_cost_window_from_here_the_runaway",
    fields: [],
  },

  split_tickets: {
    subjects: ["issue"],
    summary: "action_fields.creates_the_subtasks_proposed_in_the_plan_as",
    fields: [],
  },

  tool_call: {
    summary: "action_fields.calls_an_mcp_tool_mail_vault_documents_cloud",
    fields: [
      { key: "tool", label: "Werkzeug", type: "select", source: "mcp_tool", required: true,
        hint: "action_fields.the_list_comes_from_your_mcp_servers_settings" },
      { key: "arguments", label: "Argumente", type: "kv",
        hint: "action_fields.values_may_contain_path_from_the_context" },
      { key: "context_key", label: "action_fields.result_in_the_context_under", type: "text",
        placeholder: "tool" },
      { key: "fail_on_error", label: "action_fields.error_stops_run", type: "boolean", default: false,
        hint: "action_fields.off_the_flow_decides_for_itself_using_tool_ok" },
    ],
  },

  mail_classify: {
    subjects: ["standalone"],
    summary: "action_fields.classifies_the_incoming_mail_in_house_categor",
    fields: [
      { key: "classify_agent", label: "Klassifizier-Agent", type: "text",
        hint: "action_fields.empty_the_agent_from_the_trigger_without_an_a" },
    ],
  },

  spam_evaluate: {
    subjects: ["standalone"],
    summary: "action_fields.pulls_rules_the_local_model_and_the_memory_to",
    fields: [],
  },

  spam_card: {
    subjects: ["standalone"],
    summary: "action_fields.creates_the_verdict_row_and_asks_the_question",
    fields: [
      { key: "vorentschieden", label: "action_fields.already_decided", type: "boolean", default: false,
        hint: "action_fields.reports_a_case_the_memory_already_settled_as" },
      { key: "rueckholbar", label: "Ohne Rückfrage weggeräumt", type: "boolean", default: false,
        hint: "Die Mail geht ohne Frage weg; die Karte trägt den Weg zurück." },
      { key: "melden", label: "Selbst melden", type: "boolean", default: true,
        hint: "Aus: dieser Schritt legt nur das Urteil an und stellt den Text bereit ({{ spam.karte_titel }}, {{ spam.karte_text }}) — verschickt wird er von einem Melde-Knoten dahinter, den man abschalten kann, ohne die Aussortierung zu verlieren." },
    ],
    outcomes: "Kontext danach: spam.verdict_id, spam.karte_titel, spam.karte_text, spam.karte_art, spam.karte_faellig.",
  },

  spam_apply: {
    subjects: ["standalone"],
    summary: "action_fields.commits_the_verdict_learns_from_it_and_moves",
    fields: [
      { key: "entscheidung", label: "Entscheidung", type: "select",
        options: [["spam", "action_fields.spam"], ["ham", "action_fields.not_spam"]],
        hint: "action_fields.empty_the_person_s_answer_from_the_context_sp" },
      { key: "decided_by", label: "action_fields.decided", type: "text", placeholder: "auto" },
    ],
  },

  mail_assistant_task: {
    subjects: ["standalone"],
    summary: "action_fields.turns_the_mail_into_an_assistant_item_the_thi",
    fields: [],
  },

  mail_assistant_card: {
    subjects: ["standalone"],
    summary: "action_fields.sends_the_approval_card_for_the_assistant_ite",
    fields: [],
  },

  mail_assistant_run: {
    subjects: ["standalone"],
    summary: "action_fields.queues_the_assistant_run_for_items_a_learned",
    fields: [],
  },

  stop_agent: { summary: "action_fields.aborts_a_running_agent_run", fields: [], subjects: ["issue"] },
  start_testenv: { summary: "action_fields.starts_the_test_environment_of_the_ticket", fields: [], subjects: ["issue"] },
  stop_testenv: {
    subjects: ["issue"],
    summary: "action_fields.tears_the_test_environment_down_container_vol",
    fields: [],
  },

  accept_merge: {
    subjects: ["issue"],
    summary: "action_fields.merges_the_ticket_branch_or_opens_a_pull_requ",
    fields: [{ key: "timeout_sec", label: "action_fields.timeout_s", type: "number", placeholder: "900" }],
    outcomes: "action_fields.runs_asynchronously_the_outlet_is_named_after"
      + "pr_open, no_git, push_failed — sonst „weiter\".",
  },

  deploy: {
    subjects: ["issue"],
    summary: "action_fields.queues_a_deployment",
    fields: [{ key: "force", label: "action_fields.even_without_auto_deploy", type: "boolean", default: false,
               hint: "action_fields.without_the_checkbox_nothing_happens_when_aut" }],
  },

};

export const FALLBACK_SPEC: ActionSpec = NO;
