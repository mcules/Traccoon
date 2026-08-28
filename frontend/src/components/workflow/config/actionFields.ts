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
    fields: [{ key: "", label: "action_fields.assignments", type: "kv",
               hint: "action_fields.values_may_contain_path_from_the_context" }],
  },

  comment: {
    summary: "action_fields.writes_a_comment_on_the_bound_ticket",
    fields: [{ key: "text", label: "action_fields.text", type: "textarea", required: true,
               placeholder: "action_fields.the_agent_reported_agent_summary" }],
  },

  notify: {
    summary: "action_fields.sends_a_notification_the_bell_always_outside",
    fields: [
      { key: "to.mode", label: "action_fields.recipient", type: "select", options: TO_MODE },
      { key: "to.user_id", label: "action_fields.person", type: "select", source: "person",
        showIf: ["to.mode", ["user"]] },
      { key: "to.role", label: "action_fields.role", type: "select", showIf: ["to.mode", ["role"]],
        options: [["owner", "Owner"], ["maintainer", "action_fields.maintainer"], ["member", "action_fields.member"],
                  ["viewer", "action_fields.reader"]] },
      { key: "to.path", label: "action_fields.context_path", type: "text", showIf: ["to.mode", ["context"]],
        placeholder: "approver_id" },
      { key: "title", label: "action_fields.subject", type: "text", placeholder: "action_fields.issue_key_note" },
      { key: "text", label: "action_fields.text", type: "textarea" },
      { key: "throttle_minutes", label: "action_fields.most_every_minutes", type: "number",
        hint: "action_fields.0_off_the_flow_still_runs_on_every_call_only"},
      { key: "throttle_key", label: "action_fields.throttle_key", type: "text",
        placeholder: "shelter.diebstahl",
        hint: "action_fields.what_counts_as_the_same_message_empty_this_no"},
      { key: "channel", label: "action_fields.path", type: "select",
        options: [["", "option.default_person"], ["telegram", "option.telegram"], ["email", "option.e_mail"]],
        hint: "action_fields.leaving_it_empty_is_the_normal_case_everyone"},
      { key: "kind", label: "action_fields.kind", type: "text", placeholder: "workflow_notify",
        hint: "action_fields.card_kind_hint" },
      { key: "ref", label: "action_fields.ref", type: "kv",
        placeholder: "spam_verdict_id: {{ spam.verdict_id }}",
        hint: "action_fields.ref_hint" },
    ],
  },

  mail_flag: {
    summary: "action_fields.mail_flag_summary",
    fields: [
      { key: "flag", label: "action_fields.flag", type: "select",
        options: [["seen", "action_fields.read"], ["flagged", "action_fields.important"], ["answered", "action_fields.answered"]] },
      { key: "on", label: "action_fields.set", type: "boolean",
        hint: "action_fields.flag_on_off" },
      { key: "folder", label: "action_fields.folder", type: "text",
        hint: "action_fields.empty_folder_from_trigger" },
      { key: "uid", label: "action_fields.mail_number", type: "number",
        hint: "action_fields.empty_mail_uid" },
    ],
    outcomes: "action_fields.outcome_flag",
  },

  mail_move: {
    summary: "action_fields.mail_move_summary",
    fields: [
      { key: "target", label: "action_fields.target_folder", type: "text",
        placeholder: "Archive/2026",
        hint: "action_fields.empty_account_archive" },
      { key: "folder", label: "action_fields.folder", type: "text",
        hint: "action_fields.empty_folder_from_trigger" },
      { key: "uid", label: "action_fields.mail_number", type: "number",
        hint: "action_fields.empty_mail_from_trigger" },
    ],
    outcomes: "action_fields.outcome_move",
  },

  mail_attachment: {
    subjects: ["standalone"],
    summary: "action_fields.attachment_summary",
    fields: [
      { key: "index", label: "action_fields.attachment", type: "number",
        hint: "action_fields.attachment_index_hint" },
      { key: "context_key", label: "action_fields.store_under", type: "text", placeholder: "attachment_data",
        hint: "action_fields.attachment_context_hint" },
      { key: "max_mb", label: "action_fields.size_mb", type: "number",
        hint: "action_fields.attachment_size_hint" },
    ],
    outcomes: "action_fields.outcome_attachment",
  },

  agent_run: {
    summary: "action_fields.agent_run_summary",
    fields: [
      { key: "task", label: "action_fields.assignment", type: "textarea", required: true,
        placeholder: "action_fields.summarise_example",
        hint: "action_fields.what_agent_should_do" },
      { key: "agent", label: "action_fields.agent", type: "select", source: "agent_role",
        hint: "action_fields.who_works" },
      { key: "title", label: "action_fields.title", type: "text",
        hint: "action_fields.run_heading_hint" },
      { key: "context_key", label: "action_fields.result_under", type: "text", placeholder: "run",
        hint: "action_fields.run_result_key" },
      { key: "timeout_sec", label: "action_fields.timeout_seconds", type: "number",
        hint: "action_fields.engine_default_zero" },
      { key: "wait", label: "action_fields.wait_for_result", type: "boolean",
        hint: "action_fields.off_only_kick_off" },
    ],
    outcomes: "action_fields.outcome_run",
  },

  script: {
    summary: "action_fields.script_summary",
    fields: [
      { key: "command", label: "action_fields.script", type: "text", required: true,
        placeholder: "pruefe.sh",
        hint: "action_fields.script_path_hint" },
      { key: "args", label: "action_fields.arguments", type: "json", placeholder: '["-x", "42"]' },
      { key: "timeout_sec", label: "action_fields.timeout_seconds", type: "number", placeholder: "600" },
      { key: "context_key", label: "action_fields.result_under", type: "text", placeholder: "script" },
    ],
    outcomes: "action_fields.outcome_script",
  },

  document: {
    summary: "action_fields.document_summary",
    fields: [
      { key: "storage", label: "action_fields.store", type: "text", required: true,
        placeholder: "ki-tech-news",
        hint: "action_fields.store_key_hint" },
      { key: "text", label: "action_fields.text", type: "textarea", required: true,
        placeholder: "{{ result.output }}",
        hint: "action_fields.document_text_hint" },
      { key: "title", label: "action_fields.heading", type: "text",
        hint: "action_fields.empty_first_heading" },
      { key: "name", label: "action_fields.store_name", type: "text",
        hint: "action_fields.only_first_time" },
      { key: "format", label: "action_fields.format", type: "select",
        options: [["markdown", "Markdown"], ["text", "action_fields.plain_text"]] },
      { key: "keep", label: "action_fields.keep_versions", type: "number", placeholder: "60" },
      { key: "context_key", label: "action_fields.reference_under", type: "text", placeholder: "document",
        hint: "action_fields.document_url_hint" },
    ],
    outcomes: "action_fields.outcome_document",
  },

  document_read: {
    summary: "action_fields.document_read_summary",
    fields: [
      { key: "storage", label: "action_fields.store", type: "text", required: true, placeholder: "ki-tech-news" },
      { key: "context_key", label: "action_fields.result_under", type: "text", placeholder: "document" },
    ],
    outcomes: "action_fields.outcome_document_read",
  },

  job_pause: {
    summary: "action_fields.job_pause_summary",
    fields: [
      { key: "job_id", label: "Job", type: "number",
        hint: "action_fields.empty_job_of_run" },
      { key: "resume", label: "action_fields.continue_instead", type: "boolean",
        hint: "action_fields.resume_job" },
    ],
    outcomes: "action_fields.outcome_job_pause",
  },

  assistant_task: {
    summary: "action_fields.assistant_task_summary",
    fields: [
      { key: "task", label: "action_fields.assignment", type: "textarea", required: true,
        placeholder: "action_fields.paperless_example",
        hint: "action_fields.assignment_hint" },
      { key: "title", label: "action_fields.title", type: "text",
        placeholder: "Paperless #{{ doc_id }}",
        hint: "action_fields.intake_heading" },
      { key: "agent", label: "action_fields.agent", type: "select", source: "agent_role",
        hint: "action_fields.who_executes" },
      { key: "approval", label: "action_fields.require_approval", type: "boolean",
        hint: "action_fields.approval_on_off" },
      { key: "wait", label: "action_fields.wait_for_result", type: "boolean",
        hint: "action_fields.wait_hint" },
      { key: "context_key", label: "action_fields.result_under", type: "text", placeholder: "assistant",
        showIf: ["warten", ["true"]],
        hint: "action_fields.assistant_result_key" },
      { key: "timeout_sec", label: "action_fields.timeout_seconds", type: "number",
        showIf: ["warten", ["true"]],
        hint: "action_fields.timeout_hint" },
      { key: "priority", label: "action_fields.priority", type: "select",
        options: [["action_fields.normal", "action_fields.normal"], ["low", "action_fields.low"], ["high", "action_fields.high"], ["urgent", "action_fields.urgent"]] },
      { key: "category", label: "action_fields.category_2", type: "text",
        hint: "action_fields.intake_sort" },
      { key: "reference", label: "action_fields.dedupe_key", type: "text",
        placeholder: "{{ intake.source_ref }}",
        hint: "action_fields.dedupe_key_hint" },
      { key: "summary", label: "action_fields.summary_label", type: "textarea",
        hint: "action_fields.short_form_intake" },
      { key: "full_text", label: "action_fields.fulltext", type: "textarea",
        hint: "action_fields.fulltext_keep_hint" },
      { key: "redaction", label: "action_fields.fulltext_handling", type: "select",
        options: [["redacted", "action_fields.redact_default"], ["unredacted", "action_fields.keep_fulltext"]] },
      { key: "hint", label: "action_fields.hint_for_assistant", type: "text",
        hint: "action_fields.what_should_happen" },
    ],
    outcomes: "action_fields.outcome_assistant_task",
  },

  assistant_session: {
    summary: "action_fields.assistant_session_summary",
    fields: [
      { key: "op", label: "action_fields.session_op", type: "select", required: true,
        options: [["create", "action_fields.session_op_create"],
                  ["close", "action_fields.session_op_close"],
                  ["delete", "action_fields.session_op_delete"]],
        hint: "action_fields.session_op_hint" },
      { key: "title", label: "action_fields.title", type: "text",
        showIf: ["op", ["create"]],
        hint: "action_fields.session_title_hint" },
      { key: "agent", label: "action_fields.agent", type: "select", source: "agent_role",
        hint: "action_fields.session_agent_hint" },
      { key: "session_id", label: "action_fields.session_number", type: "text",
        placeholder: "{{ session.id }}",
        hint: "action_fields.session_number_hint" },
      { key: "closed_only", label: "action_fields.session_closed_only", type: "boolean",
        default: true, showIf: ["op", ["delete"]],
        hint: "action_fields.session_closed_only_hint" },
      { key: "older_than_days", label: "action_fields.session_older_than_days", type: "number",
        showIf: ["op", ["close", "delete"]],
        hint: "action_fields.session_older_than_days_hint" },
      { key: "keep_last", label: "action_fields.session_keep_last", type: "number",
        showIf: ["op", ["delete"]],
        hint: "action_fields.session_keep_last_hint" },
      { key: "context_key", label: "action_fields.result_under", type: "text",
        placeholder: "session_cleanup", showIf: ["op", ["close", "delete"]],
        hint: "action_fields.session_context_key_hint" },
    ],
    outcomes: "action_fields.outcome_assistant_session",
  },

  answer: {
    summary: "action_fields.answer_summary",
    fields: [
      { key: "text", label: "action_fields.answer_text", type: "textarea",
        placeholder: "{{ assistant.output }}",
        hint: "action_fields.answer_text_hint" },
      { key: "fields", label: "action_fields.answer_fields", type: "kv",
        hint: "action_fields.answer_fields_hint" },
      { key: "context_key", label: "action_fields.store_under", type: "text", placeholder: "answer",
        hint: "action_fields.change_only_custom_map" },
    ],
    outcomes: "action_fields.outcome_answer",
  },

  note_append: {
    summary: "action_fields.appends_a_line_to_a_note_in_the_vault_creatin",
    fields: [
      { key: "path", label: "action_fields.path_note", type: "text", required: true,
        placeholder: "04 Wissen/Erkennung/{{ spam.kind }}.md",
        hint: "action_fields.the_path_may_come_from_the_context_that_way_e" },
      { key: "text", label: "action_fields.text", type: "textarea", required: true,
        placeholder: "- {{ spam.sender_domain }}: {{ spam.findings_text }}" },
      { key: "heading", label: "action_fields.section_optional", type: "text",
        hint: "action_fields.created_when_it_does_not_exist_yet" },
    ],
    outcomes: "action_fields.context_afterwards_notiz_ok_notiz_error",
  },

  series_record: {
    summary: "action_fields.writes_a_point_into_a_data_series_number_loca",
    subjects: ["standalone"],
    fields: [
      { key: "series", label: "action_fields.series", type: "text", required: true,
        placeholder: "tracker.shelter",
        hint: "action_fields.key_of_the_series_it_comes_into_being_with_it" },
      { key: "kind", label: "action_fields.kind", type: "select",
        options: [["number", "action_fields.number"], ["location", "action_fields.location"], ["text", "action_fields.text"]],
        hint: "action_fields.only_applies_when_the_series_is_created_an_ex" },
      { key: "value", label: "action_fields.value", type: "text", showIf: ["kind", ["number", ""]],
        placeholder: "{{ position.attributes.batteryLevel }}" },
      { key: "lat", label: "action_fields.width", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.latitude }}" },
      { key: "lon", label: "action_fields.length", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.longitude }}" },
      { key: "accuracy", label: "action_fields.accuracy_m", type: "text", showIf: ["kind", ["location"]] },
      { key: "altitude", label: "action_fields.altitude_m", type: "text", showIf: ["kind", ["location"]] },
      { key: "speed", label: "action_fields.speed", type: "text", showIf: ["kind", ["location"]] },
      { key: "course", label: "action_fields.direction", type: "text", showIf: ["kind", ["location"]] },
      { key: "battery", label: "action_fields.battery_pct", type: "text", showIf: ["kind", ["location"]],
        placeholder: "{{ position.attributes.batteryLevel }}" },
      { key: "title", label: "action_fields.heading", type: "text", showIf: ["kind", ["text"]] },
      { key: "body", label: "action_fields.text", type: "textarea", showIf: ["kind", ["text"]] },
      { key: "ts", label: "action_fields.moment", type: "text",
        placeholder: "{{ position.serverTime }}",
        hint: "action_fields.empty_means_now_unix_seconds_or_iso_time" },
      { key: "name", label: "action_fields.display_name", type: "text", placeholder: "action_fields.shelter" },
      { key: "color", label: "action_fields.color", type: "text", placeholder: "#3b82f6" },
      { key: "required", label: "action_fields.fail_without_value", type: "boolean",
        hint: "action_fields.normally_a_report_without_a_value_is_not_an_e" },
    ],
    outcomes: "action_fields.context_afterwards_series_kind_stored_value_o",
  },
  metric_record: {
    summary: "action_fields.writes_a_number_into_a_series_and_reads_off_w",
    fields: [
      { key: "series", label: "action_fields.series", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.key_of_the_series_same_key_means_same_series" },
      { key: "value", label: "action_fields.value", type: "text", required: true,
        placeholder: "action_fields.position_attributes_batterylevel" },
      { key: "unit", label: "action_fields.unit", type: "text", placeholder: "%" },
      { key: "name", label: "action_fields.display_name", type: "text", placeholder: "action_fields.battery_shelter" },
      { key: "min", label: "action_fields.smallest_valid_value", type: "number",
        hint: "action_fields.devices_report_nonsense_when_they_do_not_know"},
      { key: "max", label: "action_fields.largest_valid_value", type: "number" },
      { key: "target", label: "action_fields.target_value", type: "number",
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
      { key: "series", label: "action_fields.series", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.path_allowed_so_the_same_flow_checks_several"},
      { key: "silence_hours", label: "action_fields.quiet_after_hours", type: "number",
        hint: "action_fields.0_do_not_check_reported_once_per_phase_of_sil"},
      { key: "target", label: "action_fields.target_value", type: "number" },
      { key: "window_days", label: "action_fields.trend_window_days", type: "number" },
    ],
    outcomes: "action_fields.context_afterwards_messreihe_wert_alter_stund",
  },

  webhook: {
    summary: "action_fields.calls_a_free_url_for_a_far_side_you_use_again",
    fields: [
      { key: "url", label: "URL", type: "text", required: true,
        placeholder: "https://example.com/hook" },
      { key: "method", label: "action_fields.method", type: "select",
        options: [["POST", "POST"], ["GET", "GET"], ["PUT", "PUT"], ["PATCH", "PATCH"],
                  ["DELETE", "DELETE"]] },
      { key: "headers", label: "action_fields.headers", type: "kv" },
      { key: "payload", label: "Body", type: "json" },
      { key: "secret", label: "action_fields.secret_from_the_vault", type: "text",
        hint: "action_fields.name_in_the_secret_vault_available_in_the_cal" },
      { key: "timeout_sec", label: "action_fields.timeout_s", type: "number" },
    ],
  },

  create_ticket: {
    summary: "action_fields.creates_a_new_ticket_in_the_project_of_the_fl",
    fields: [
      { key: "summary", label: "action_fields.title", type: "text", required: true,
        placeholder: "action_fields.fault_event_name" },
      { key: "description", label: "action_fields.description", type: "textarea" },
      { key: "assigned_agent", label: "action_fields.assign_agent", type: "select", source: "agent_role",
        hint: "action_fields.empty_nobody_assigning_starts_the_lifecycle" },
      { key: "start_agent_status", label: "action_fields.start_state", type: "select", options: AGENT_STATUS },
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
      { key: "field", label: "action_fields.field", type: "select", source: "artifact_field", required: true },
      { key: "values", label: "action_fields.values", type: "text", required: true,
        hint: "action_fields.separate_several_by_comma_templates_from_the" },
      { key: "mode", label: "action_fields.approach", type: "select", options: [
          ["set", "option.replace"], ["add", "option.add"], ["remove", "option.remove"]],
        hint: "action_fields.adding_and_removing_only_pays_off_on_multi_se" },
    ],
  },

  set_status: {
    summary: "action_fields.sets_the_state_of_the_artifact_the_flow_hangs",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "status", label: "action_fields.state", type: "select", source: "artifact_status",
        required: true },
      { key: "reason", label: "action_fields.reason", type: "select", options: HOLD_REASON,
        showIf: ["__subject", ["issue"]],
        hint: "action_fields.tickets_only_it_separates_plan_approval_from" },
      { key: "notify", label: "action_fields.notify", type: "boolean", default: true,
        hint: "action_fields.reports_plan_approval_review_errors_and_block" },
    ],
  },


  set_board_status: {
    summary: "action_fields.moves_the_ticket_into_a_board_column",
    subjects: ["issue"],
    fields: [
      { key: "status", label: "action_fields.column", type: "select", source: "board_status" },
      { key: "category", label: "action_fields.category", type: "select",
        options: [["", "—"], ["todo", "action_fields.todo"], ["in_progress", "action_fields.in_progress"], ["done", "option.done"]],
        hint: "action_fields.applies_when_no_column_with_a_matching_name_e" },
    ],
  },

  assign_agent: {
    summary: "action_fields.assigns_an_agent_to_the_ticket",
    subjects: ["issue"],
    fields: [{ key: "agent", label: "action_fields.agent", type: "select", source: "agent_role", required: true }],
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
      { key: "tool", label: "action_fields.tool", type: "select", source: "mcp_tool", required: true,
        hint: "action_fields.the_list_comes_from_your_mcp_servers_settings" },
      { key: "arguments", label: "action_fields.arguments", type: "kv",
        hint: "action_fields.values_may_contain_path_from_the_context" },
      { key: "context_key", label: "action_fields.result_in_the_context_under", type: "text",
        placeholder: "tool" },
      { key: "fail_on_error", label: "action_fields.error_stops_run", type: "boolean", default: false,
        hint: "action_fields.off_the_flow_decides_for_itself_using_tool_ok" },
    ],
  },

  report_mail: {
    subjects: ["standalone"],
    summary: "action_fields.report_mail_summary",
    fields: [
      { key: "open_new", label: "action_fields.report_mail_open_new", type: "boolean", default: true,
        hint: "action_fields.report_mail_open_new_hint" },
    ],
    outcomes: "action_fields.report_mail_outcome",
  },

  mail_classify: {
    subjects: ["standalone"],
    summary: "action_fields.classifies_the_incoming_mail_in_house_categor",
    fields: [
      { key: "classify_agent", label: "action_fields.classify_agent", type: "text",
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
      { key: "rueckholbar", label: "action_fields.cleared_without_asking", type: "boolean", default: false,
        hint: "action_fields.mail_goes_card_returns" },
      { key: "melden", label: "action_fields.report_itself", type: "boolean", default: true,
        hint: "action_fields.report_itself_hint" },
    ],
    outcomes: "action_fields.outcome_spam_card",
  },

  spam_apply: {
    subjects: ["standalone"],
    summary: "action_fields.commits_the_verdict_learns_from_it_and_moves",
    fields: [
      { key: "entscheidung", label: "action_fields.decision", type: "select",
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
    outcomes: "action_fields.runs_asynchronously_the_outlet_is_named_after",
  },

  deploy: {
    subjects: ["issue"],
    summary: "action_fields.queues_a_deployment",
    fields: [{ key: "force", label: "action_fields.even_without_auto_deploy", type: "boolean", default: false,
               hint: "action_fields.without_the_checkbox_nothing_happens_when_aut" }],
  },

};

export const FALLBACK_SPEC: ActionSpec = NO;
