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

    Die Schluessel sind englisch, weil der Editor sie liest und die Doku sie zeigt — der
    Katalog ist eine Schnittstelle, keine interne Notiz. `description` ist dagegen ein
    **i18n-Schluessel**, kein Text: Der Editor zeigt ihn dem Menschen in dessen Sprache, und
    beide Kataloge (de/en) tragen ihn vollstaendig.
    """
    return {"path": path, "type": kind, "description": description}


# Always present, regardless of trigger and actions.
BASIS = [
    _f("event.name", "text", "ctx.name_des_ausloesenden_ereignisses"),
    _f("event.project_id", "number", "ctx.projekt_aus_dem_das_ereignis_kam"),
    _f("continuation", "number", "ctx.wie_oft_der_lauf_an_derselben_sache_schon_fo"),
]

# What a trigger brings along. Key = event name (see events.BUILTIN_EVENTS).
TRIGGER: dict[str, list[dict]] = {
    "issue.created": [
        _f("issue.key", "text", "ctx.ticket_kennung_z_b_tra_31"),
        _f("issue.summary", "text", "ctx.titel_des_tickets"),
        _f("issue.type", "text", "ctx.vorgangsart"),
        _f("issue.reporter_id", "number", "ctx.wer_es_gemeldet_hat"),
    ],
    "issue.assigned": [
        _f("issue.key", "text", "ctx.ticket_kennung"),
        _f("issue.assigned_agent", "text", "ctx.zugewiesener_agent"),
    ],
    "issue.status_changed": [
        _f("issue.key", "text", "ctx.ticket_kennung"),
        _f("issue.status", "text", "ctx.neue_board_spalte"),
    ],
    "issue.agent_status_changed": [
        _f("issue.key", "text", "ctx.ticket_kennung"),
        _f("issue.agent_status", "text", "ctx.neuer_ki_zustand_planning_plan_review"),
        _f("issue.hold_reason", "text", "ctx.grund_der_blockade_falls_vorhanden"),
    ],
    "issue.done": [
        _f("issue.key", "text", "ctx.ticket_kennung"),
        _f("issue.agent_status", "text", "ctx.zustand_hier_done"),
    ],
    "comment.added": [
        _f("issue.key", "text", "ctx.ticket_an_dem_kommentiert_wurde"),
        _f("comment.body", "text", "ctx.text_des_kommentars"),
        _f("comment.author_id", "number", "ctx.wer_kommentiert_hat"),
    ],
    "hardware.status_changed": [
        _f("asset.id", "number", "ctx.exemplar"),
        _f("asset.status", "text", "ctx.neuer_beschaffungs_zustand"),
    ],
    "deployment.finished": [
        _f("deployment.status", "text", "ctx.ergebnis_des_deployments"),
        _f("deployment.project_id", "number", "ctx.betroffenes_projekt"),
    ],
    # Not an event but the payload of the trigger: a webhook (mode `workflow`) or a job
    # passes it through via `context_map`. Which fields those are is up to the trigger, the
    # ones here belong to the shipped ticket intake.
    "(Webhook/Job)": [
        _f("title", "text", "ctx.titel_der_meldung_ticket_titel"),
        _f("body", "text", "ctx.inhalt_der_meldung_beschreibung"),
        _f("agent", "text", "ctx.agent_der_gleich_zugewiesen_wird"),
        _f("ignore", "boolean", "ctx.meldung_verwerfen_statt_anlegen"),
    ],
    "(Mail-Aktion)": [
        _f("mail.account", "text", "ctx.kurzname_des_postfachs"),
        _f("mail.account_id", "number", "ctx.konto_aus_dem_die_mail_stammt"),
        _f("mail.folder", "text", "ctx.ordner"),
        _f("mail.uid", "number", "ctx.nachricht_im_ordner"),
        _f("mail.subject", "text", "ctx.betreff"),
        _f("mail.from", "text", "ctx.absenderadresse"),
        _f("mail.text", "text", "ctx.text_der_nachricht"),
        _f("mail.attachments", "list", "ctx.alle_anhaenge_index_filename_content_type_si"),
        _f("attachment.index", "number", "ctx.gewaehlter_anhang_nur_beim_knopf_am_anhang"),
        _f("attachment.filename", "text", "ctx.name_des_gewaehlten_anhangs"),
    ],
    "mail.received": [
        _f("mail.subject", "text", "ctx.betreff"),
        _f("mail.from", "text", "ctx.absender_rohwert_der_kopfzeile"),
        _f("mail.account", "text", "ctx.postfach_in_dem_sie_liegt"),
        _f("mail.folder", "text", "ctx.ordner"),
        _f("mail.uid", "number", "ctx.nachrichtenkennung_im_ordner"),
        _f("intake.agent", "text", "ctx.zustaendiger_assistent"),
        _f("intake.owner_id", "number", "ctx.besitzer_des_postfachs"),
        _f("intake.auto_run", "boolean", "ctx.ausloeser_erzwingt_einen_sofortlauf"),
    ],
}

# What an action writes into the context. Key = action name (workflow_actions).
ACTIONS: dict[str, list[dict]] = {
    "refresh_facts": [
        _f("project.needs_acceptance", "boolean", "ctx.projekt_verlangt_eine_abnahme"),
        _f("project.auto_deploy", "boolean", "ctx.deployment_laeuft_automatisch"),
        _f("project.auto_continue", "boolean", "ctx.agent_darf_selbsttaetig_weitermachen"),
        _f("project.git_enabled", "boolean", "ctx.projekt_hat_ein_repository"),
        _f("project.use_pull_request", "boolean", "ctx.auslieferung_ueber_pull_request"),
        _f("issue.has_plan", "boolean", "ctx.es_liegt_ein_plan_vor"),
        _f("issue.has_parent", "boolean", "ctx.teilaufgabe_eines_sammeltickets"),
        _f("issue.merge_status", "text", "ctx.stand_des_merges"),
        _f("issue.testenv_status", "text", "ctx.stand_der_testumgebung"),
        _f("issue.assigned_agent", "text", "ctx.zugewiesener_agent"),
    ],
    "create_ticket": [
        _f("created_ticket.id", "number", "ctx.angelegtes_ticket"),
        _f("created_ticket.key", "text", "ctx.kennung_des_angelegten_tickets"),
    ],
    "set_field": [_f("fields.<schlüssel>", "text", "ctx.gesetzte_feldwerte_des_artefakts")],
    "assistant_task": [
        _f("task.task_id", "number", "ctx.nummer_des_auftrags_im_assistenten_eingang"),
        _f("task.status", "text", "ctx.approved_laeuft_new_wartet_auf_freigabe"),
        _f("assistant.output", "text", "ctx.antwort_des_assistenten_nur_mit_warten"),
        _f("assistant.status", "text", "ctx.done_oder_error_nur_mit_warten"),
    ],
    "mail_flag": [],
    "mail_move": [],
    "mail_attachment": [
        _f("attachment.filename", "text", "ctx.dateiname_des_geholten_anhangs"),
        _f("attachment.content_type", "text", "ctx.art_der_datei_application_pdf"),
        _f("attachment.size", "number", "ctx.groesse_in_bytes"),
        _f("attachment.base64", "text", "ctx.der_inhalt_wie_ihn_werkzeuge_erwarten"),
    ],
    "answer": [
        _f("answer", "text|object", "ctx.was_dieser_lauf_seinem_ausloeser_zurueckgibt"),
    ],
    "metric_record": [
        _f("metric.value", "number", "ctx.der_eben_festgehaltene_wert"),
        _f("metric.per_day", "number", "ctx.aenderung_pro_tag_negativ_faellt"),
        _f("metric.days_left", "number", "ctx.tage_bis_zum_zielwert_leer_wenn_unklar"),
        _f("metric.empty_at", "text", "ctx.datum_an_dem_der_zielwert_erreicht_wird"),
        _f("metric.fit", "number", "ctx.wie_gut_die_gerade_passt_01"),
        _f("metric.points", "number", "ctx.wieviele_messpunkte_im_fenster_liegen"),
        _f("metric.warn", "boolean", "ctx.vorwarnzeit_erreicht_jetzt_bescheid_geben"),
    ],
    "series_record": [
        _f("series.kind", "text", "ctx.art_der_reihe_number_location_oder_text"),
        _f("series.stored", "boolean", "ctx.wurde_der_punkt_gespeichert"),
        _f("series.value", "number", "ctx.bei_zahlen_der_eben_festgehaltene_wert"),
        _f("series.lat", "number", "ctx.bei_standorten_breite_des_letzten_punktes"),
        _f("series.lon", "number", "ctx.bei_standorten_laenge_des_letzten_punktes"),
        _f("series.battery", "number", "ctx.akkustand_wenn_das_geraet_einen_mitschickt"),
        _f("series.places", "list", "ctx.orte_in_denen_das_geraet_jetzt_steht"),
        _f("series.entered", "list", "ctx.orte_die_mit_diesem_punkt_betreten_wurden"),
        _f("series.left", "list", "ctx.orte_die_mit_diesem_punkt_verlassen_wurden"),
        _f("series.points", "number", "ctx.wieviele_punkte_die_reihe_insgesamt_hat"),
    ],
    "metric_read": [
        _f("metric.value", "number", "ctx.letzter_wert_der_reihe"),
        _f("metric.alter_stunden", "number", "ctx.wie_alt_der_letzte_wert_ist_stunden"),
        _f("metric.still", "boolean", "ctx.reihe_schweigt_laenger_als_erlaubt"),
        _f("metric.still_melden", "boolean", "ctx.jetzt_melden_einmal_je_stille_phase"),
        _f("metric.gefunden", "boolean", "ctx.reihe_existiert_ueberhaupt"),
        _f("metric.days_left", "number", "ctx.tage_bis_zum_zielwert_leer_wenn_unklar"),
        _f("metric.empty_at", "text", "ctx.datum_an_dem_der_zielwert_erreicht_wird"),
        _f("metric.per_day", "number", "ctx.aenderung_pro_tag_negativ_faellt"),
        _f("metric.points", "number", "ctx.wieviele_messpunkte_im_fenster_liegen"),
    ],
    "tool_call": [
        _f("tool.ok", "boolean", "ctx.werkzeug_aufruf_war_erfolgreich"),
        _f("tool.text", "text", "ctx.antwort_des_werkzeugs_im_klartext"),
        _f("tool.json", "object", "ctx.antwort_zerlegt_falls_sie_json_war_tiefer_ad"),
        _f("tool.error", "text", "ctx.fehlermeldung_falls_der_aufruf_misslang"),
    ],
    "http_request": [
        _f("http.status_code", "number", "ctx.antwortcode_der_gegenstelle"),
        _f("http.ok", "boolean", "ctx.antwort_war_erfolgreich_2xx"),
        _f("http.body", "text", "ctx.antwortinhalt_bei_json_auch_tiefer_adressier"),
    ],
    "accept_merge": [
        _f("merge.result", "text", "ctx.merged_conflict_pr_open"),
        _f("merge.escalate", "boolean", "ctx.konflikt_gehoert_zum_menschen"),
    ],
    "mail_classify": [
        _f("classification.category", "text", "ctx.kategorie_aus_der_lokalen_einordnung"),
        _f("classification.priority", "text", "ctx.dringlichkeit"),
        _f("classification.sensitive", "boolean", "ctx.enthaelt_schuetzenswertes"),
        _f("classification.redacted_summary", "text", "ctx.geschwaerzte_kurzfassung"),
        _f("policy.auto", "boolean", "ctx.gelernte_regel_gibt_automatisch_frei"),
        _f("policy.redaction", "text", "ctx.redacted_unredacted"),
    ],
    "note_append": [
        _f("note.ok", "boolean", "ctx.notiz_wurde_geschrieben"),
        _f("note.error", "text", "ctx.fehlertext_wenn_nicht"),
    ],
    "spam_evaluate": [
        _f("spam.aktiv", "boolean", "ctx.spam_erkennung_ist_ueberhaupt_eingeschaltet"),
        _f("spam.score", "number", "ctx.gesamturteil_0_1"),
        _f("spam.rule_score", "number", "ctx.teilurteil_der_regeln"),
        _f("spam.model_score", "number", "ctx.teilurteil_des_lokalen_modells"),
        _f("spam.learned_score", "number", "ctx.teilurteil_des_gedaechtnisses"),
        _f("spam.serverurteil", "boolean", "ctx.eigener_mailserver_hat_spam_markiert"),
        _f("spam.modellurteil", "boolean", "ctx.lokales_modell_nennt_einen_betrugsversuch"),
        _f("spam.art", "text", "ctx.als_was_eingestuft_phishing_werbung_rechnung"),
        _f("spam.befunde_text", "text", "ctx.woran_es_erkannt_wurde_als_satz"),
        _f("spam.bekannter_kontakt", "boolean", "ctx.absender_steht_im_kontaktbestand"),
        _f("spam.geklaert", "boolean", "ctx.gedaechtnis_ist_sich_ueber_den_absender_eini"),
        _f("spam.geklaert_urteil", "text", "ctx.spam_ham_nur_wenn_geklaert"),
        _f("spam.faelschungsverdacht", "boolean", "ctx.echtheitspruefung_fehlgeschlagen"),
        _f("spam.frage_ab", "number", "ctx.schwelle_ab_der_gefragt_wird"),
        _f("spam.sofort_ab", "number", "ctx.schwelle_fuer_die_sofortige_einzelkarte"),
        _f("spam.auto_ab", "number", "ctx.schwelle_fuer_das_verschieben_ohne_rueckfrag"),
        _f("spam.auto_melden", "boolean", "ctx.soll_eine_ohne_rueckfrage_weggeraeumte_mail"),
    ],
    "spam_card": [_f("spam.verdict_id", "number", "ctx.angelegte_urteils_zeile"),
                  _f("spam.karte", "text", "ctx.sofort_sammel_rueckholbar"),
                  _f("spam.karte_titel", "text", "ctx.ueberschrift_fuer_den_melde_knoten"),
                  _f("spam.karte_text", "text", "ctx.text_fuer_den_melde_knoten"),
                  _f("spam.karte_art", "text", "ctx.spam_auto_spam_review_bestimmt_die_knoepfe"),
                  _f("spam.karte_faellig", "boolean", "ctx.ist_jetzt_eine_karte_dran")],
    "spam_apply": [_f("spam.entschieden", "text", "ctx.spam_ham"),
                   _f("spam.ergebnis", "text", "ctx.rueckmeldung_der_imap_aktion")],
    "assistant_task": [_f("task.id", "number", "ctx.angelegtes_assistent_item"),
                       _f("task.status", "text", "ctx.new_approved"),
                       _f("task.auto", "boolean", "ctx.von_einer_gelernten_regel_freigegeben")],
}

# Node types that leave something behind themselves.
NODE: dict[str, list[dict]] = {
    "agent_task": [
        _f("agent.status", "text", "ctx.ergebnis_des_laufs_done_blocked_failed"),
        _f("agent.stalled", "boolean", "ctx.der_lauf_kam_nicht_mehr_voran"),
        _f("agent.has_subtickets", "boolean", "ctx.der_plan_schlaegt_teilaufgaben_vor"),
        _f("agent.summary", "text", "ctx.abschlusstext_des_laufs"),
        _f("agent.hold_status", "text", "ctx.zustand_in_den_das_ticket_dabei_faellt"),
        _f("agent.hold_reason", "text", "ctx.grund_dafuer"),
    ],
    "wait_event": [
        _f("event.name", "text", "ctx.welches_ereignis_den_lauf_geweckt_hat"),
    ],
    "subflow": [
        _f("subflow.outcome", "text", "ctx.ausgang_des_kind_ablaufs"),
    ],
}


def catalog() -> dict:
    """The whole catalog, the way the editor needs it."""
    return {"base": BASIS, "triggers": TRIGGER, "actions": ACTIONS, "nodes": NODE}
