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
  ["planning", "option.planung_laeuft"],
  ["plan_review", "option.plan_wartet_auf_freigabe"],
  ["approved", "option.freigegeben_umsetzung_darf_starten"],
  ["in_progress", "option.in_umsetzung"],
  ["to_test", "option.zur_abnahme_bereit"],
  ["testing", "option.in_abnahme"],
  ["done", "option.fertig"],
  ["hold", "option.angehalten_wartet_auf_menschen"],
  ["failed", "option.fehlgeschlagen"],
  ["open", "option.offen"],
];

const HOLD_REASON: [string, string][] = [
  ["", "option.automatisch"],
  ["plan_review", "option.plan_freigabe"],
  ["plan_split", "option.aufteilungs_freigabe"],
  ["question", "option.rueckfrage"],
  ["permission", "option.berechtigung"],
  ["review", "option.review_befunde"],
  ["merge", "option.merge_konflikt"],
  ["stuck", "option.feststecker"],
  ["cap", "option.kostengrenze"],
  ["interrupted", "option.unterbrochen"],
  ["incomplete", "option.unvollstaendig"],
  ["verify", "option.pruefung_offen"],
];

const TO_MODE: [string, string][] = [
  ["user", "option.bestimmte_person"],
  ["role", "option.projekt_rolle"],
  ["reporter", "option.melder_des_tickets"],
  ["context", "option.aus_dem_kontext_user_id"],
];

const KEINE: ActionSpec = { summary: "", fields: [] };

export const ACTION_SPECS: Record<AutoActionName, ActionSpec> = {
  http_request: { summary: "action_fields.ruft_ein_hinterlegtes_ziel_auf", fields: [] },  // eigene Maske

  set_context: {
    summary: "action_fields.schreibt_werte_in_den_prozess_kontext_spaete",
    fields: [{ key: "", label: "Zuweisungen", type: "kv",
               hint: "action_fields.werte_duerfen_pfad_aus_dem_kontext_enthalten" }],
  },

  comment: {
    summary: "action_fields.schreibt_einen_kommentar_an_das_gebundene_ti",
    fields: [{ key: "text", label: "Text", type: "textarea", required: true,
               placeholder: "action_fields.der_agent_hat_agent_summary_gemeldet" }],
  },

  notify: {
    summary: "action_fields.schickt_eine_benachrichtigung_glocke_immer_h",
    fields: [
      { key: "to.mode", label: "action_fields.empfaenger", type: "select", options: TO_MODE },
      { key: "to.user_id", label: "Person", type: "select", source: "person",
        showIf: ["to.mode", ["user"]] },
      { key: "to.role", label: "Rolle", type: "select", showIf: ["to.mode", ["role"]],
        options: [["owner", "Owner"], ["maintainer", "Maintainer"], ["member", "Mitglied"],
                  ["viewer", "Leser"]] },
      { key: "to.path", label: "Kontext-Pfad", type: "text", showIf: ["to.mode", ["context"]],
        placeholder: "freigeber_id" },
      { key: "title", label: "Betreff", type: "text", placeholder: "action_fields.issue_key_hinweis" },
      { key: "text", label: "Text", type: "textarea" },
      { key: "drossel_minuten", label: "action_fields.hoechstens_alle_minuten", type: "number",
        hint: "action_fields.0_aus_der_ablauf_laeuft_weiterhin_bei_jedem_"},
      { key: "drossel_key", label: "action_fields.drossel_schluessel", type: "text",
        placeholder: "shelter.diebstahl",
        hint: "action_fields.was_als_dieselbe_nachricht_gilt_leer_dieser_"},
      { key: "channel", label: "Weg", type: "select",
        options: [["", "option.standard_der_person"], ["telegram", "option.telegram"], ["email", "option.e_mail"]],
        hint: "action_fields.leer_lassen_ist_der_normalfall_jeder_verwalt"},
      { key: "kind", label: "Art", type: "text", placeholder: "workflow_notify",
        hint: "Entscheidet, welche Knöpfe der Bot anhängt (z. B. spam_auto = „zurückholen“). Leer = gewöhnliche Meldung ohne Knöpfe." },
      { key: "bezug", label: "Bezug", type: "kv",
        placeholder: "spam_verdict_id: {{ spam.verdict_id }}",
        hint: "Worauf sich die Knöpfe beziehen: spam_verdict_id, assistant_task_id, issue_id oder project_id." },
    ],
  },

  assistent_auftrag: {
    summary: "Gibt dem persönlichen Assistenten einen freien Auftrag — ohne Mail, ohne Ticket, ohne Projekt.",
    fields: [
      { key: "auftrag", label: "Auftrag", type: "textarea", required: true,
        placeholder: "Lies Paperless-Dokument {{ doc_id }} und halte Wissenswertes im Vault fest.",
        hint: "Der Auftrag selbst. {{ pfad }} holt Werte aus dem Kontext." },
      { key: "titel", label: "Titel", type: "text",
        placeholder: "Paperless #{{ doc_id }}",
        hint: "Überschrift im Assistenten-Eingang. Leer = erste Zeile des Auftrags." },
      { key: "agent", label: "Agent", type: "select", source: "agent_role",
        hint: "Wer den Auftrag ausführt. Leer = der persönliche Assistent." },
      { key: "freigabe", label: "Erst freigeben lassen", type: "boolean",
        hint: "An: der Auftrag wartet im Eingang auf deinen Menschen. Aus: er läuft sofort." },
      { key: "warten", label: "Auf das Ergebnis warten", type: "boolean",
        hint: "An: der Schritt bleibt stehen, bis der Lauf fertig ist — nur dann kann der Ablauf mit der Antwort weiterarbeiten." },
      { key: "context_key", label: "Ergebnis unter", type: "text", placeholder: "assistent",
        showIf: ["warten", ["true"]],
        hint: "Kontext-Schlüssel für das Ergebnis: {{ assistent.output }} ist die Antwort." },
      { key: "timeout_sek", label: "Zeitgrenze (Sekunden)", type: "number",
        showIf: ["warten", ["true"]],
        hint: "0 = Vorgabe der Engine. Eine Rückfrage des Assistenten kann die Grenze reißen." },
      { key: "prioritaet", label: "Priorität", type: "select",
        options: [["normal", "normal"], ["low", "niedrig"], ["high", "hoch"], ["urgent", "dringend"]] },
    ],
    outcomes: "Kontext danach: auftrag.task_id, auftrag.status. Mit „warten\" zusätzlich <Ergebnis unter>.output und .status.",
  },

  antwort: {
    summary: "Legt fest, was dieser Lauf an seinen Auslöser zurückgibt (ein wartender Webhook liest genau das).",
    fields: [
      { key: "text", label: "Antwort (Text)", type: "textarea",
        placeholder: "{{ assistent.output }}",
        hint: "Freier Text. Für eine strukturierte Antwort dieses Feld leer lassen und unten Felder setzen." },
      { key: "felder", label: "Antwort (Felder)", type: "kv",
        hint: "Schlüssel → Wert, Werte mit {{ pfad }}. Ein Objekt hier IST der Rumpf der Antwort." },
      { key: "context_key", label: "Ablegen unter", type: "text", placeholder: "antwort",
        hint: "Nur ändern, wenn der Webhook eine eigene Zuordnung benutzt." },
    ],
    outcomes: "Kontext danach: antwort (Text oder Objekt).",
  },

  notiz_anhaengen: {
    summary: "action_fields.haengt_eine_zeile_an_eine_notiz_im_vault",
    fields: [
      { key: "pfad", label: "action_fields.notiz_pfad", type: "text", required: true,
        placeholder: "04 Wissen/Erkennung/{{ spam.art }}.md",
        hint: "action_fields.pfad_darf_aus_dem_kontext_kommen_so_waechst" },
      { key: "text", label: "Text", type: "textarea", required: true,
        placeholder: "- {{ spam.sender_domain }}: {{ spam.befunde_text }}" },
      { key: "ueberschrift", label: "action_fields.abschnitt_optional", type: "text",
        hint: "action_fields.wird_angelegt_wenn_es_ihn_noch_nicht_gibt" },
    ],
    outcomes: "action_fields.kontext_danach_notiz_ok_notiz_error",
  },

  messwert: {
    summary: "action_fields.schreibt_eine_zahl_in_eine_messreihe_und_lie",
    fields: [
      { key: "reihe", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.schluessel_der_reihe_gleicher_schluessel_gle" },
      { key: "wert", label: "Wert", type: "text", required: true,
        placeholder: "action_fields.position_attributes_batterylevel" },
      { key: "einheit", label: "Einheit", type: "text", placeholder: "%" },
      { key: "name", label: "Anzeigename", type: "text", placeholder: "action_fields.akku_shelter" },
      { key: "min", label: "action_fields.kleinster_gueltiger_wert", type: "number",
        hint: "action_fields.geraete_melden_unsinn_wenn_sie_etwas_nicht_w"},
      { key: "max", label: "action_fields.groesster_gueltiger_wert", type: "number" },
      { key: "ziel", label: "Zielwert", type: "number",
        hint: "action_fields.wert_auf_den_die_reihe_zulaeuft_0_heisst_lee" },
      { key: "vorwarn_tage", label: "action_fields.vorwarnung_tage", type: "number",
        hint: "action_fields.wie_frueh_gewarnt_werden_soll_0_schaltet_die"},
      { key: "fenster_tage", label: "action_fields.trendfenster_tage", type: "number",
        hint: "action_fields.wie_weit_zurueck_fuer_die_gerade_gelesen_wir" },
    ],
    outcomes: "action_fields.kontext_danach_messreihe_wert_pro_tag_rest_t",
  },

  messreihe_lesen: {
    summary: "action_fields.sieht_eine_messreihe_an_ohne_sie_zu_fuettern",
    fields: [
      { key: "reihe", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "action_fields.pfad_erlaubt_so_prueft_derselbe_ablauf_mehre"},
      { key: "still_stunden", label: "action_fields.verstummt_nach_stunden", type: "number",
        hint: "action_fields.0_nicht_pruefen_gemeldet_wird_einmal_je_stil"},
      { key: "ziel", label: "Zielwert", type: "number" },
      { key: "fenster_tage", label: "action_fields.trendfenster_tage", type: "number" },
    ],
    outcomes: "action_fields.kontext_danach_messreihe_wert_alter_stunden_",
  },

  webhook: {
    summary: "action_fields.ruft_eine_freie_url_auf_fuer_wiederkehrende_",
    fields: [
      { key: "url", label: "URL", type: "text", required: true,
        placeholder: "https://example.com/hook" },
      { key: "method", label: "Methode", type: "select",
        options: [["POST", "POST"], ["GET", "GET"], ["PUT", "PUT"], ["PATCH", "PATCH"],
                  ["DELETE", "DELETE"]] },
      { key: "headers", label: "Kopfzeilen", type: "kv" },
      { key: "payload", label: "Body", type: "json" },
      { key: "secret", label: "action_fields.secret_aus_dem_tresor", type: "text",
        hint: "action_fields.name_im_secret_tresor_im_aufruf_als_secret_v" },
      { key: "timeout_sec", label: "action_fields.zeitlimit_s", type: "number" },
    ],
  },

  create_ticket: {
    summary: "action_fields.legt_ein_neues_ticket_an_im_projekt_des_proz",
    fields: [
      { key: "summary", label: "Titel", type: "text", required: true,
        placeholder: "action_fields.stoerung_event_name" },
      { key: "description", label: "Beschreibung", type: "textarea" },
      { key: "assigned_agent", label: "action_fields.agent_zuweisen", type: "select", source: "agent_role",
        hint: "action_fields.leer_niemand_zuweisung_startet_den_lebenszyk" },
      { key: "start_agent_status", label: "Startzustand", type: "select", options: AGENT_STATUS },
      { key: "project_id", label: "action_fields.anderes_projekt_id", type: "number" },
      { key: "context_key", label: "action_fields.ergebnis_im_kontext_unter", type: "text",
        placeholder: "created_ticket" },
    ],
  },

  refresh_facts: {
    subjects: ["issue"],
    summary: "action_fields.liest_projekt_und_ticket_einstellungen_in_de",
    fields: [],
  },

  set_field: {
    summary: "action_fields.setzt_ein_freies_feld_des_artefakts_an_dem_d",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "field", label: "Feld", type: "select", source: "artifact_field", required: true },
      { key: "values", label: "Wert(e)", type: "text", required: true,
        hint: "action_fields.mehrere_durch_komma_trennen_vorlagen_aus_dem" },
      { key: "mode", label: "Vorgehen", type: "select", options: [
          ["set", "option.ersetzen"], ["add", "option.ergaenzen"], ["remove", "option.entfernen"]],
        hint: "action_fields.ergaenzen_entfernen_lohnt_nur_bei_feldern_mi" },
    ],
  },

  set_status: {
    summary: "action_fields.setzt_den_zustand_des_artefakts_an_dem_der_a",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "status", label: "Zustand", type: "select", source: "artifact_status",
        required: true },
      { key: "reason", label: "Grund", type: "select", options: HOLD_REASON,
        showIf: ["__subject", ["issue"]],
        hint: "action_fields.nur_bei_tickets_unterscheidet_u_a_plan_von_a" },
      { key: "notify", label: "Benachrichtigen", type: "boolean", default: true,
        hint: "action_fields.meldet_plan_freigabe_abnahme_fehler_und_bloc" },
    ],
  },


  set_board_status: {
    summary: "action_fields.verschiebt_das_ticket_in_eine_board_spalte",
    subjects: ["issue"],
    fields: [
      { key: "status", label: "Spalte", type: "select", source: "board_status" },
      { key: "category", label: "action_fields.oder_kategorie", type: "select",
        options: [["", "—"], ["todo", "To Do"], ["in_progress", "In Arbeit"], ["done", "option.fertig"]],
        hint: "action_fields.greift_wenn_keine_spalte_mit_passendem_namen" },
    ],
  },

  assign_agent: {
    summary: "action_fields.weist_dem_ticket_einen_agenten_zu",
    subjects: ["issue"],
    fields: [{ key: "agent", label: "Agent", type: "select", source: "agent_role", required: true }],
  },

  set_cap_baseline: {
    subjects: ["issue"],
    summary: "action_fields.setzt_das_kostenfenster_neu_ab_hier_zaehlt_d",
    fields: [],
  },

  split_tickets: {
    subjects: ["issue"],
    summary: "action_fields.legt_die_im_plan_vorgeschlagenen_teilaufgabe",
    fields: [],
  },

  tool_call: {
    summary: "action_fields.ruft_ein_mcp_werkzeug_auf_mail_vault_paperle",
    fields: [
      { key: "tool", label: "Werkzeug", type: "select", source: "mcp_tool", required: true,
        hint: "action_fields.die_liste_kommt_aus_deinen_mcp_servern_einst" },
      { key: "arguments", label: "Argumente", type: "kv",
        hint: "action_fields.werte_duerfen_pfad_aus_dem_kontext_enthalten" },
      { key: "context_key", label: "action_fields.ergebnis_im_kontext_unter", type: "text",
        placeholder: "tool" },
      { key: "fail_on_error", label: "action_fields.fehler_bricht_ab", type: "boolean", default: false,
        hint: "action_fields.aus_der_ablauf_entscheidet_selbst_ueber_tool" },
    ],
  },

  mail_classify: {
    subjects: ["standalone"],
    summary: "action_fields.ordnet_die_eingegangene_mail_im_haus_ein_kat",
    fields: [
      { key: "classify_agent", label: "Klassifizier-Agent", type: "text",
        hint: "action_fields.leer_der_agent_aus_dem_ausloeser_ganz_ohne_a" },
    ],
  },

  spam_evaluate: {
    subjects: ["standalone"],
    summary: "action_fields.zieht_regeln_lokales_modell_und_gedaechtnis_",
    fields: [],
  },

  spam_card: {
    subjects: ["standalone"],
    summary: "action_fields.legt_die_urteils_zeile_an_und_stellt_die_tel",
    fields: [
      { key: "vorentschieden", label: "action_fields.schon_entschieden", type: "boolean", default: false,
        hint: "action_fields.meldet_einen_vom_gedaechtnis_geklaerten_fall" },
      { key: "rueckholbar", label: "Ohne Rückfrage weggeräumt", type: "boolean", default: false,
        hint: "Die Mail geht ohne Frage weg; die Karte trägt den Weg zurück." },
      { key: "melden", label: "Selbst melden", type: "boolean", default: true,
        hint: "Aus: dieser Schritt legt nur das Urteil an und stellt den Text bereit ({{ spam.karte_titel }}, {{ spam.karte_text }}) — verschickt wird er von einem Melde-Knoten dahinter, den man abschalten kann, ohne die Aussortierung zu verlieren." },
    ],
    outcomes: "Kontext danach: spam.verdict_id, spam.karte_titel, spam.karte_text, spam.karte_art, spam.karte_faellig.",
  },

  spam_apply: {
    subjects: ["standalone"],
    summary: "action_fields.schreibt_das_urteil_fest_lernt_daraus_und_be",
    fields: [
      { key: "entscheidung", label: "Entscheidung", type: "select",
        options: [["spam", "action_fields.ist_spam"], ["ham", "action_fields.kein_spam"]],
        hint: "action_fields.leer_die_antwort_des_menschen_aus_dem_kontex" },
      { key: "decided_by", label: "action_fields.entschieden_von", type: "text", placeholder: "auto" },
    ],
  },

  assistant_task: {
    subjects: ["standalone"],
    summary: "action_fields.macht_aus_der_mail_ein_assistent_item_das_wa",
    fields: [],
  },

  assistant_card: {
    subjects: ["standalone"],
    summary: "action_fields.schickt_die_freigabekarte_zum_assistent_item",
    fields: [],
  },

  assistant_run: {
    subjects: ["standalone"],
    summary: "action_fields.reiht_den_assistenten_lauf_ein_fuer_items_di",
    fields: [],
  },

  stop_agent: { summary: "action_fields.bricht_einen_laufenden_agentenlauf_ab", fields: [], subjects: ["issue"] },
  start_testenv: { summary: "action_fields.startet_die_testumgebung_des_tickets", fields: [], subjects: ["issue"] },
  stop_testenv: {
    subjects: ["issue"],
    summary: "action_fields.raeumt_die_testumgebung_ab_container_volumes",
    fields: [],
  },

  accept_merge: {
    subjects: ["issue"],
    summary: "action_fields.mergt_den_ticket_branch_bzw_oeffnet_einen_pu",
    fields: [{ key: "timeout_sec", label: "action_fields.zeitlimit_s", type: "number", placeholder: "900" }],
    outcomes: "action_fields.laeuft_asynchron_der_ausgang_heisst_wie_das_"
      + "pr_open, no_git, push_failed — sonst „weiter\".",
  },

  deploy: {
    subjects: ["issue"],
    summary: "action_fields.reiht_ein_deployment_ein",
    fields: [{ key: "force", label: "action_fields.auch_ohne_auto_deploy", type: "boolean", default: false,
               hint: "action_fields.ohne_haken_passiert_nichts_wenn_auto_deploy_" }],
  },

};

export const FALLBACK_SPEC: ActionSpec = KEINE;
