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


def _f(path: str, kind: str, description: str) -> dict:
    """Ein Katalogeintrag.

    The keys are English because the editor reads them and the docs show them — the catalog is
    an interface, not an internal note. `description` on the other hand is an **i18n key**, not
    a text: the editor shows it to the person in their language, and
    beide Kataloge (de/en) tragen ihn vollstaendig.
    """
    return {"path": path, "type": kind, "description": description}


# Always present, regardless of trigger and actions.
BASIS = [
    _f("event.name", "text", "ctx.name_of_the_triggering_event"),
    _f("event.project_id", "number", "ctx.project_the_event_came_from"),
    _f("continuation", "number", "ctx.how_often_the_run_has_already_continued_on_th"),
]

# What a trigger brings along. Key = event name (see events.BUILTIN_EVENTS).
TRIGGER: dict[str, list[dict]] = {
    "issue.created": [
        _f("issue.key", "text", "ctx.ticket_key_example"),
        _f("issue.summary", "text", "ctx.title_ticket"),
        _f("issue.type", "text", "ctx.kind_process"),
        _f("issue.reporter_id", "number", "ctx.who_reported_it"),
    ],
    "issue.assigned": [
        _f("issue.key", "text", "ctx.ticket_key"),
        _f("issue.assigned_agent", "text", "ctx.assigned_agent"),
    ],
    "issue.status_changed": [
        _f("issue.key", "text", "ctx.ticket_key"),
        _f("issue.status", "text", "ctx.new_board_column"),
    ],
    "issue.agent_status_changed": [
        _f("issue.key", "text", "ctx.ticket_key"),
        _f("issue.agent_status", "text", "ctx.new_ai_state_planning_plan_review"),
        _f("issue.hold_reason", "text", "ctx.reason_for_the_block_if_there_is_one"),
    ],
    "issue.done": [
        _f("issue.key", "text", "ctx.ticket_key"),
        _f("issue.agent_status", "text", "ctx.state_here_done"),
    ],
    "comment.added": [
        _f("issue.key", "text", "ctx.the_ticket_that_was_commented_on"),
        _f("comment.body", "text", "ctx.text_comment"),
        _f("comment.author_id", "number", "ctx.who_commented"),
    ],
    "hardware.status_changed": [
        _f("asset.id", "number", "ctx.item"),
        _f("asset.status", "text", "ctx.new_procurement_state"),
    ],
    "deployment.finished": [
        _f("deployment.status", "text", "ctx.result_deployment"),
        _f("deployment.project_id", "number", "ctx.project_concerned"),
    ],
    # Not an event but the payload of the trigger: a webhook (mode `workflow`) or a job
    # passes it through via `context_map`. Which fields those are is up to the trigger, the
    # ones here belong to the shipped ticket intake.
    "(Webhook/Job)": [
        _f("title", "text", "ctx.title_of_the_report_ticket_title"),
        _f("body", "text", "ctx.body_of_the_report_description"),
        _f("agent", "text", "ctx.agent_about_to_be_assigned"),
        _f("ignore", "boolean", "ctx.discard_the_report_instead_of_creating_it"),
    ],
    "(Mail-Aktion)": [
        _f("mail.account", "text", "ctx.short_name_mailbox"),
        _f("mail.account_id", "number", "ctx.account_the_mail_came_from"),
        _f("mail.folder", "text", "ctx.folder"),
        _f("mail.uid", "number", "ctx.message_folder"),
        _f("mail.subject", "text", "ctx.subject"),
        _f("mail.from", "text", "ctx.sender_address"),
        _f("mail.text", "text", "ctx.text_message"),
        _f("mail.attachments", "list", "ctx.all_attachments_index_filename_content_type_s"),
        _f("attachment.index", "number", "ctx.the_chosen_attachment_only_from_the_button_on"),
        _f("attachment.filename", "text", "ctx.name_of_the_chosen_attachment"),
    ],
    "mail.received": [
        _f("mail.subject", "text", "ctx.subject"),
        _f("mail.from", "text", "ctx.sender_raw_header_value"),
        _f("mail.account", "text", "ctx.the_mailbox_it_sits_in"),
        _f("mail.folder", "text", "ctx.folder"),
        _f("mail.uid", "number", "ctx.message_id_within_folder"),
        _f("intake.agent", "text", "ctx.assistant_charge"),
        _f("intake.owner_id", "number", "ctx.owner_mailbox"),
        _f("intake.auto_run", "boolean", "ctx.the_trigger_forces_an_immediate_run"),
    ],
}

# What an action writes into the context. Key = action name (workflow_actions).
ACTIONS: dict[str, list[dict]] = {
    "refresh_facts": [
        _f("project.needs_acceptance", "boolean", "ctx.the_project_requires_a_review"),
        _f("project.auto_deploy", "boolean", "ctx.deployment_runs_automatically"),
        _f("project.auto_continue", "boolean", "ctx.agent_may_carry_on_by_itself"),
        _f("project.git_enabled", "boolean", "ctx.the_project_has_a_repository"),
        _f("project.use_pull_request", "boolean", "ctx.delivery_through_a_pull_request"),
        _f("issue.has_plan", "boolean", "ctx.a_plan_exists"),
        _f("issue.has_parent", "boolean", "ctx.subtask_collecting_ticket"),
        _f("issue.merge_status", "text", "ctx.state_merge"),
        _f("issue.testenv_status", "text", "ctx.state_test_environment"),
        _f("issue.assigned_agent", "text", "ctx.assigned_agent"),
    ],
    "create_ticket": [
        _f("created_ticket.id", "number", "ctx.ticket_created"),
        _f("created_ticket.key", "text", "ctx.key_of_the_created_ticket"),
    ],
    "set_field": [_f("fields.<key>", "text", "ctx.field_values_set_on_the_artifact")],
    "assistant_task": [
        _f("task.task_id", "number", "ctx.number_of_the_task_in_the_assistant_inbox"),
        _f("task.status", "text", "ctx.approved_running_new_waiting_for_release"),
        _f("assistant.output", "text", "ctx.the_assistant_s_answer_only_with_wait"),
        _f("assistant.status", "text", "ctx.done_or_error_only_with_wait"),
    ],
    "mail_flag": [],
    "mail_move": [],
    "mail_attachment": [
        _f("attachment.filename", "text", "ctx.file_name_of_the_fetched_attachment"),
        _f("attachment.content_type", "text", "ctx.file_type_application_pdf"),
        _f("attachment.size", "number", "ctx.size_bytes"),
        _f("attachment.base64", "text", "ctx.the_content_as_tools_expect_it"),
    ],
    "mail_document": [
        _f("document.noted", "boolean", "ctx.was_the_document_connected_to_an_attachment"),
        _f("document.doc_id", "text", "ctx.the_number_over_there"),
    ],
    "answer": [
        _f("answer", "text|object", "ctx.what_this_run_returns_to_whoever_started_it"),
    ],
    "metric_record": [
        _f("metric.value", "number", "ctx.the_value_just_recorded"),
        _f("metric.per_day", "number", "ctx.change_per_day_negative_falling"),
        _f("metric.days_left", "number", "ctx.days_until_the_target_value_empty_when_unclea"),
        _f("metric.empty_at", "text", "ctx.date_on_which_the_target_value_is_reached"),
        _f("metric.fit", "number", "ctx.how_well_the_line_fits_0_1"),
        _f("metric.points", "number", "ctx.how_many_measuring_points_lie_within_the_wind"),
        _f("metric.warn", "boolean", "ctx.early_warning_reached_say_something_now"),
    ],
    "series_record": [
        _f("series.kind", "text", "ctx.kind_of_the_series_number_location_or_text"),
        _f("series.stored", "boolean", "ctx.was_the_point_stored"),
        _f("series.value", "number", "ctx.with_numbers_the_value_just_recorded"),
        _f("series.lat", "number", "ctx.with_locations_latitude_of_the_last_point"),
        _f("series.lon", "number", "ctx.with_locations_longitude_of_the_last_point"),
        _f("series.battery", "number", "ctx.battery_level_when_the_device_sends_one"),
        _f("series.places", "list", "ctx.places_the_device_is_in_right_now"),
        _f("series.entered", "list", "ctx.places_entered_with_this_point"),
        _f("series.left", "list", "ctx.places_left_with_this_point"),
        _f("series.points", "number", "ctx.how_many_points_the_series_has_in_total"),
    ],
    "metric_read": [
        _f("metric.value", "number", "ctx.latest_value_of_the_series"),
        _f("metric.alter_stunden", "number", "ctx.how_old_the_latest_value_is_hours"),
        _f("metric.still", "boolean", "ctx.the_series_has_been_silent_longer_than_allowe"),
        _f("metric.still_melden", "boolean", "ctx.report_now_once_per_silent_phase"),
        _f("metric.gefunden", "boolean", "ctx.series_exists_all"),
        _f("metric.days_left", "number", "ctx.days_until_the_target_value_empty_when_unclea"),
        _f("metric.empty_at", "text", "ctx.date_on_which_the_target_value_is_reached"),
        _f("metric.per_day", "number", "ctx.change_per_day_negative_falling"),
        _f("metric.points", "number", "ctx.how_many_measuring_points_lie_within_the_wind"),
    ],
    "tool_call": [
        _f("tool.ok", "boolean", "ctx.the_tool_call_succeeded"),
        _f("tool.text", "text", "ctx.the_tool_s_answer_as_plain_text"),
        _f("tool.json", "object", "ctx.response_parsed_if_it_was_json_addressable_de"),
        _f("tool.error", "text", "ctx.error_message_if_the_call_failed"),
    ],
    "http_request": [
        _f("http.status_code", "number", "ctx.status_code_counterpart"),
        _f("http.ok", "boolean", "ctx.the_response_was_successful_2xx"),
        _f("http.body", "text", "ctx.response_body_with_json_also_addressable_deep"),
    ],
    # The audit writes its summary under `audit` (or wherever `context_key` says), so a flow
    # can decide afterwards whether the numbers are worth a message.
    "agentshield_scan": [
        _f("audit.findings", "number", "ctx.how_many_findings_are_open"),
        _f("audit.new", "number", "ctx.how_many_appeared_in_this_run"),
        _f("audit.fixed", "number", "ctx.how_many_are_gone_since_the_run_before"),
        _f("audit.critical", "number", "ctx.of_them_critical"),
        _f("audit.high", "number", "ctx.of_them_high"),
        _f("audit.configs", "number", "ctx.how_many_configurations_were_scanned"),
    ],
    "accept_merge": [
        _f("merge.result", "text", "ctx.merged_conflict_pr_open"),
        _f("merge.escalate", "boolean", "ctx.the_conflict_belongs_to_a_human"),
    ],
    "mail_classify": [
        _f("classification.category", "text", "ctx.category_from_the_local_classification"),
        _f("classification.priority", "text", "ctx.urgency"),
        _f("classification.sensitive", "boolean", "ctx.contains_something_worth_protecting"),
        _f("classification.redacted_summary", "text", "ctx.redacted_short_version"),
        _f("policy.auto", "boolean", "ctx.a_learned_rule_releases_it_automatically"),
        _f("policy.redaction", "text", "ctx.redacted_unredacted"),
    ],
    "note_append": [
        _f("note.ok", "boolean", "ctx.note_written"),
        _f("note.error", "text", "ctx.error_text_not"),
    ],
    "spam_evaluate": [
        _f("spam.aktiv", "boolean", "ctx.spam_detection_is_switched_on_at_all"),
        _f("spam.score", "number", "ctx.overall_verdict_0_1"),
        _f("spam.rule_score", "number", "ctx.partial_verdict_rules"),
        _f("spam.model_score", "number", "ctx.partial_verdict_of_the_local_model"),
        _f("spam.learned_score", "number", "ctx.partial_verdict_memory"),
        _f("spam.serverurteil", "boolean", "ctx.our_own_mail_server_flagged_it_as_spam"),
        _f("spam.modellurteil", "boolean", "ctx.the_local_model_calls_it_an_attempted_fraud"),
        _f("spam.art", "text", "ctx.what_it_was_classified_as_phishing_ads_invoic"),
        _f("spam.findings_text", "text", "ctx.what_gave_it_away_as_a_sentence"),
        _f("spam.bekannter_kontakt", "boolean", "ctx.sender_is_among_the_known_contacts"),
        _f("spam.settled", "boolean", "ctx.the_memory_agrees_about_this_sender"),
        _f("spam.settled_verdict", "text", "ctx.spam_ham_only_once_settled"),
        _f("spam.faelschungsverdacht", "boolean", "ctx.authenticity_check_failed"),
        _f("spam.frage_ab", "number", "ctx.threshold_above_which_it_asks"),
        _f("spam.sofort_ab", "number", "ctx.threshold_for_an_immediate_single_card"),
        _f("spam.auto_ab", "number", "ctx.threshold_for_moving_without_asking"),
        _f("spam.auto_melden", "boolean", "ctx.should_a_mail_filed_without_asking_be_reporte"),
    ],
    "spam_card": [_f("spam.verdict_id", "number", "ctx.verdict_row_created"),
                  _f("spam.karte", "text", "ctx.immediate_batched_recoverable"),
                  _f("spam.card_title", "text", "ctx.heading_for_the_notify_node"),
                  _f("spam.card_text", "text", "ctx.text_for_the_notify_node"),
                  _f("spam.card_kind", "text", "ctx.spam_auto_spam_review_decides_the_buttons"),
                  _f("spam.card_due", "boolean", "ctx.is_a_card_due_now")],
    "spam_apply": [_f("spam.entschieden", "text", "ctx.spam_ham"),
                   _f("spam.ergebnis", "text", "ctx.response_of_the_imap_action")],
    "assistant_task": [_f("task.id", "number", "ctx.assistant_item_created"),
                       _f("task.status", "text", "ctx.new_approved"),
                       _f("task.auto", "boolean", "ctx.released_by_a_learned_rule")],
}

# Node types that leave something behind themselves.
NODE: dict[str, list[dict]] = {
    "agent_task": [
        _f("agent.status", "text", "ctx.result_of_the_run_done_blocked_failed"),
        _f("agent.stalled", "boolean", "ctx.the_run_stopped_making_progress"),
        _f("agent.has_subtickets", "boolean", "ctx.the_plan_proposes_subtasks"),
        _f("agent.summary", "text", "ctx.closing_text_run"),
        _f("agent.hold_status", "text", "ctx.state_the_ticket_falls_into_as_a_result"),
        _f("agent.hold_reason", "text", "ctx.reason"),
    ],
    "wait_event": [
        _f("event.name", "text", "ctx.which_event_woke_the_run"),
    ],
    "subflow": [
        _f("subflow.outcome", "text", "ctx.outcome_of_the_child_flow"),
    ],
}


def catalog() -> dict:
    """The whole catalog, the way the editor needs it."""
    return {"base": BASIS, "triggers": TRIGGER, "actions": ACTIONS, "nodes": NODE}
