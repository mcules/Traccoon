import type { AutoActionName } from "../types";

/**
 * Was eine Aktion einstellen kann — je Aktion ausgeschrieben, damit im Editor direkt
 * sichtbar ist, welche Felder es gibt und welche Werte erlaubt sind. Ohne diese Beschreibung
 * blieb nur ein leeres Schlüssel/Wert-Feld, in das man die richtigen Namen erraten musste.
 *
 * `options` kann statisch sein oder zur Laufzeit gefüllt werden (Board-Spalten, Agenten,
 * Mitglieder) — siehe `ActionParams.tsx`.
 */
export type FieldType = "text" | "textarea" | "number" | "boolean" | "select" | "kv" | "json";

export interface FieldSpec {
  key: string;
  label: string;
  type: FieldType;
  /** Auswahlwerte: [Wert, Beschriftung]. */
  options?: [string, string][];
  /** Laufzeit-Quelle für die Auswahl. */
  source?: "board_status" | "agent_role" | "member" | "artifact_status" | "artifact_field"
    | "mcp_tool" | "person";
  placeholder?: string;
  hint?: string;
  /** Vorbelegung, solange nichts gesetzt ist (wichtig bei Ja/Nein-Feldern). */
  default?: boolean;
  /** Feld nur zeigen, wenn ein anderes Feld diesen Wert hat. */
  showIf?: [string, string[]];
  required?: boolean;
}

export interface ActionSpec {
  /** Ein Satz: was die Aktion tut. */
  summary: string;
  fields: FieldSpec[];
  /** Ausgänge, die die Aktion erzeugt (asynchrone Aktionen). */
  outcomes?: string;
  /** Für welche Subjekte die Aktion sinnvoll ist. Leer = für alle. */
  subjects?: ("issue" | "hardware_asset" | "standalone")[];
}

const AGENT_STATUS: [string, string][] = [
  ["planning", "Planung läuft"],
  ["plan_review", "Plan wartet auf Freigabe"],
  ["approved", "Freigegeben (Umsetzung darf starten)"],
  ["in_progress", "In Umsetzung"],
  ["to_test", "Zur Abnahme bereit"],
  ["testing", "In Abnahme"],
  ["done", "Fertig"],
  ["hold", "Angehalten (wartet auf Menschen)"],
  ["failed", "Fehlgeschlagen"],
  ["open", "Offen"],
];

const HOLD_REASON: [string, string][] = [
  ["", "— automatisch —"],
  ["plan_review", "Plan-Freigabe"],
  ["plan_split", "Aufteilungs-Freigabe"],
  ["question", "Rückfrage"],
  ["permission", "Berechtigung"],
  ["review", "Review-Befunde"],
  ["merge", "Merge-Konflikt"],
  ["stuck", "Feststecker"],
  ["cap", "Kostengrenze"],
  ["interrupted", "Unterbrochen"],
  ["incomplete", "Unvollständig"],
  ["verify", "Prüfung offen"],
];

const TO_MODE: [string, string][] = [
  ["user", "Bestimmte Person"],
  ["role", "Projekt-Rolle"],
  ["reporter", "Melder des Tickets"],
  ["context", "Aus dem Kontext (User-ID)"],
];

const KEINE: ActionSpec = { summary: "", fields: [] };

export const ACTION_SPECS: Record<AutoActionName, ActionSpec> = {
  http_request: { summary: "Ruft ein hinterlegtes Ziel auf.", fields: [] },  // eigene Maske

  set_context: {
    summary: "Schreibt Werte in den Prozess-Kontext (später als {{schlüssel}} nutzbar).",
    fields: [{ key: "", label: "Zuweisungen", type: "kv",
               hint: "Werte dürfen {{pfad}} aus dem Kontext enthalten." }],
  },

  comment: {
    summary: "Schreibt einen Kommentar an das gebundene Ticket.",
    fields: [{ key: "text", label: "Text", type: "textarea", required: true,
               placeholder: "Der Agent hat {{agent.summary}} gemeldet." }],
  },

  notify: {
    summary: "Schickt eine Benachrichtigung — Glocke immer, hinaus auf dem Weg der Person.",
    fields: [
      { key: "to.mode", label: "Empfänger", type: "select", options: TO_MODE },
      { key: "to.user_id", label: "Person", type: "select", source: "person",
        showIf: ["to.mode", ["user"]] },
      { key: "to.role", label: "Rolle", type: "select", showIf: ["to.mode", ["role"]],
        options: [["owner", "Owner"], ["maintainer", "Maintainer"], ["member", "Mitglied"],
                  ["viewer", "Leser"]] },
      { key: "to.path", label: "Kontext-Pfad", type: "text", showIf: ["to.mode", ["context"]],
        placeholder: "freigeber_id" },
      { key: "title", label: "Betreff", type: "text", placeholder: "{{issue_key}}: Hinweis" },
      { key: "text", label: "Text", type: "textarea" },
      { key: "drossel_minuten", label: "Höchstens alle … Minuten", type: "number",
        hint: "0 = aus. Der Ablauf läuft weiterhin bei jedem Aufruf durch — nur die "
            + "Nachricht bleibt aus. Nötig, wo die Gegenstelle nicht selbst zusammenfasst: "
            + "ein Tracker wiederholt seinen Alarm im Sekundentakt, solange er anliegt." },
      { key: "drossel_key", label: "Drossel-Schlüssel", type: "text",
        placeholder: "shelter.diebstahl",
        hint: "Was als „dieselbe Nachricht“ gilt. Leer = dieser Knoten für sich; mit "
            + "{{pfad}} trennt man nach Gerät oder Art." },
      { key: "channel", label: "Weg", type: "select",
        options: [["", "Standard der Person"], ["telegram", "Telegram"], ["email", "E-Mail"]],
        hint: "Leer lassen ist der Normalfall — jeder verwaltet im Profil, wie er erreicht "
            + "wird. Ist der gewählte Weg dort nicht hinterlegt, wird der andere genommen." },
    ],
  },

  messwert: {
    summary: "Schreibt eine Zahl in eine Messreihe und liest ab, wohin sie läuft.",
    fields: [
      { key: "reihe", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "Schlüssel der Reihe — gleicher Schlüssel = gleiche Reihe." },
      { key: "wert", label: "Wert", type: "text", required: true,
        placeholder: "{{ position.attributes.batteryLevel }}" },
      { key: "einheit", label: "Einheit", type: "text", placeholder: "%" },
      { key: "name", label: "Anzeigename", type: "text", placeholder: "Akku Shelter" },
      { key: "min", label: "Kleinster gültiger Wert", type: "number",
        hint: "Geräte melden Unsinn, wenn sie etwas nicht wissen — solche Werte gehören "
            + "nicht in die Reihe (der Tracker schickt z. B. 127 % für „unbekannt“)." },
      { key: "max", label: "Größter gültiger Wert", type: "number" },
      { key: "ziel", label: "Zielwert", type: "number",
        hint: "Wert, auf den die Reihe zuläuft — 0 heißt „leer“." },
      { key: "vorwarn_tage", label: "Vorwarnung (Tage)", type: "number",
        hint: "Wie früh gewarnt werden soll. 0 schaltet die Warnung ab; gewarnt wird "
            + "einmal je Auffüllung, nicht bei jedem Wert." },
      { key: "fenster_tage", label: "Trendfenster (Tage)", type: "number",
        hint: "Wie weit zurück für die Gerade gelesen wird (Standard 30)." },
    ],
    outcomes: "Kontext danach: messreihe.wert, .pro_tag, .rest_tage, .leer_am, .guete, .warnen",
  },

  messreihe_lesen: {
    summary: "Sieht eine Messreihe an, ohne sie zu füttern — und merkt, wenn sie verstummt.",
    fields: [
      { key: "reihe", label: "Reihe", type: "text", required: true,
        placeholder: "akku.shelter",
        hint: "{{pfad}} erlaubt — so prüft derselbe Ablauf mehrere Reihen, je nach "
            + "Startkontext des Jobs." },
      { key: "still_stunden", label: "Verstummt nach … Stunden", type: "number",
        hint: "0 = nicht prüfen. Gemeldet wird einmal je Stille-Phase; sobald wieder ein "
            + "Wert kommt, zählt sie von vorn." },
      { key: "ziel", label: "Zielwert", type: "number" },
      { key: "fenster_tage", label: "Trendfenster (Tage)", type: "number" },
    ],
    outcomes: "Kontext danach: messreihe.wert, .alter_stunden, .still, .still_melden, "
            + ".gefunden, .rest_tage, .leer_am",
  },

  webhook: {
    summary: "Ruft eine freie URL auf. Für wiederkehrende Gegenstellen besser ein Ziel anlegen.",
    fields: [
      { key: "url", label: "URL", type: "text", required: true,
        placeholder: "https://example.com/hook" },
      { key: "method", label: "Methode", type: "select",
        options: [["POST", "POST"], ["GET", "GET"], ["PUT", "PUT"], ["PATCH", "PATCH"],
                  ["DELETE", "DELETE"]] },
      { key: "headers", label: "Kopfzeilen", type: "kv" },
      { key: "payload", label: "Body", type: "json" },
      { key: "secret", label: "Secret aus dem Tresor", type: "text",
        hint: "Name im Secret-Tresor; im Aufruf als {{secret}} verfügbar." },
      { key: "timeout_sec", label: "Zeitlimit (s)", type: "number" },
    ],
  },

  create_ticket: {
    summary: "Legt ein neues Ticket an (im Projekt des Prozesses, sofern nichts anderes steht).",
    fields: [
      { key: "summary", label: "Titel", type: "text", required: true,
        placeholder: "Störung: {{event.name}}" },
      { key: "description", label: "Beschreibung", type: "textarea" },
      { key: "assigned_agent", label: "Agent zuweisen", type: "select", source: "agent_role",
        hint: "Leer = niemand. Zuweisung startet den Lebenszyklus." },
      { key: "start_agent_status", label: "Startzustand", type: "select", options: AGENT_STATUS },
      { key: "project_id", label: "Anderes Projekt (ID)", type: "number" },
      { key: "context_key", label: "Ergebnis im Kontext unter", type: "text",
        placeholder: "created_ticket" },
    ],
  },

  refresh_facts: {
    subjects: ["issue"],
    summary: "Liest Projekt- und Ticket-Einstellungen in den Kontext (project.*, issue.*), "
      + "damit Verzweigungen darauf prüfen können.",
    fields: [],
  },

  set_field: {
    summary: "Setzt ein freies Feld des Artefakts, an dem der Ablauf hängt. Welche Felder es "
      + "gibt, sagt das Artefakt-Register (Administration → Artefakte).",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "field", label: "Feld", type: "select", source: "artifact_field", required: true },
      { key: "values", label: "Wert(e)", type: "text", required: true,
        hint: "Mehrere durch Komma trennen. {{vorlagen}} aus dem Kontext sind erlaubt." },
      { key: "mode", label: "Vorgehen", type: "select", options: [
          ["set", "Ersetzen"], ["add", "Ergänzen"], ["remove", "Entfernen"]],
        hint: "Ergänzen/Entfernen lohnt nur bei Feldern mit Mehrfachauswahl." },
    ],
  },

  set_status: {
    summary: "Setzt den Zustand des Artefakts, an dem der Ablauf hängt — Ticket oder "
      + "Hardware. Die möglichen Werte kommen aus dem Artefakt-Register.",
    subjects: ["issue", "hardware_asset"],
    fields: [
      { key: "status", label: "Zustand", type: "select", source: "artifact_status",
        required: true },
      { key: "reason", label: "Grund", type: "select", options: HOLD_REASON,
        showIf: ["__subject", ["issue"]],
        hint: "Nur bei Tickets — unterscheidet u. a. Plan- von Aufteilungs-Freigabe." },
      { key: "notify", label: "Benachrichtigen", type: "boolean", default: true,
        hint: "Meldet Plan-Freigabe, Abnahme, Fehler und Blockade (Standard: an)." },
    ],
  },


  set_board_status: {
    summary: "Verschiebt das Ticket in eine Board-Spalte.",
    subjects: ["issue"],
    fields: [
      { key: "status", label: "Spalte", type: "select", source: "board_status" },
      { key: "category", label: "…oder Kategorie", type: "select",
        options: [["", "—"], ["todo", "To Do"], ["in_progress", "In Arbeit"], ["done", "Fertig"]],
        hint: "Greift, wenn keine Spalte mit passendem Namen existiert." },
    ],
  },

  assign_agent: {
    summary: "Weist dem Ticket einen Agenten zu.",
    subjects: ["issue"],
    fields: [{ key: "agent", label: "Agent", type: "select", source: "agent_role", required: true }],
  },

  set_cap_baseline: {
    subjects: ["issue"],
    summary: "Setzt das Kostenfenster neu: ab hier zählt die Runaway-Bremse nur frische Läufe. "
      + "Gehört an jede menschliche Freigabe.",
    fields: [],
  },

  split_tickets: {
    subjects: ["issue"],
    summary: "Legt die im Plan vorgeschlagenen Teilaufgaben als Kind-Tickets an "
      + "(Teil 1 startet, der Rest wartet auf seinen Vorgänger).",
    fields: [],
  },

  tool_call: {
    summary: "Ruft ein MCP-Werkzeug auf — Mail, Vault, Paperless, Nextcloud, Hausautomation "
      + "und alles andere aus deiner MCP-Registry. Das Ergebnis steht danach unter tool.* "
      + "im Kontext (tool.ok, tool.text, tool.json).",
    fields: [
      { key: "tool", label: "Werkzeug", type: "select", source: "mcp_tool", required: true,
        hint: "Die Liste kommt aus deinen MCP-Servern (Einstellungen → MCP-Server)." },
      { key: "arguments", label: "Argumente", type: "kv",
        hint: "Werte dürfen {{pfad}} aus dem Kontext enthalten." },
      { key: "context_key", label: "Ergebnis im Kontext unter", type: "text",
        placeholder: "tool" },
      { key: "fail_on_error", label: "Fehler bricht ab", type: "boolean", default: false,
        hint: "Aus: der Ablauf entscheidet selbst über tool.ok an einer Weiche." },
    ],
  },

  mail_classify: {
    subjects: ["standalone"],
    summary: "Ordnet die eingegangene Mail im Haus ein (Kategorie, Dringlichkeit, "
      + "Kurzfassung) und sucht die gelernte Regel zum Absender. Schreibt klasse.* "
      + "und policy.* in den Kontext.",
    fields: [
      { key: "classify_agent", label: "Klassifizier-Agent", type: "text",
        hint: "Leer = der Agent aus dem Auslöser. Ganz ohne Agenten wird nur durchgereicht." },
    ],
  },

  spam_evaluate: {
    subjects: ["standalone"],
    summary: "Zieht Regeln, lokales Modell und Gedächtnis zu einem Urteil zusammen "
      + "(spam.score, spam.geklaert, spam.frage_ab …). Entscheidet selbst nichts.",
    fields: [],
  },

  spam_card: {
    subjects: ["standalone"],
    summary: "Legt die Urteils-Zeile an und stellt die Telegram-Rückfrage. Unterhalb der "
      + "Sofort-Schwelle wartet der Fall auf die Sammel-Karte.",
    fields: [
      { key: "vorentschieden", label: "Schon entschieden", type: "boolean", default: false,
        hint: "Meldet einen vom Gedächtnis geklärten Fall — als Hinweis, nicht als Frage." },
    ],
  },

  spam_apply: {
    subjects: ["standalone"],
    summary: "Schreibt das Urteil fest, lernt daraus und bewegt die Mail (Spam-Ordner "
      + "bzw. zurück in den Posteingang).",
    fields: [
      { key: "entscheidung", label: "Entscheidung", type: "select",
        options: [["spam", "Ist Spam"], ["ham", "Kein Spam"]],
        hint: "Leer = die Antwort des Menschen aus dem Kontext (spam.entschieden)." },
      { key: "decided_by", label: "Entschieden von", type: "text", placeholder: "auto" },
    ],
  },

  assistant_task: {
    subjects: ["standalone"],
    summary: "Macht aus der Mail ein Assistent-Item (das, was der Mensch freigibt). "
      + "Doppelte Zustellung erzeugt kein zweites Item.",
    fields: [],
  },

  assistant_card: {
    subjects: ["standalone"],
    summary: "Schickt die Freigabekarte zum Assistent-Item.",
    fields: [],
  },

  assistant_run: {
    subjects: ["standalone"],
    summary: "Reiht den Assistenten-Lauf ein (für Items, die eine gelernte Regel bereits "
      + "freigegeben hat).",
    fields: [],
  },

  stop_agent: { summary: "Bricht einen laufenden Agentenlauf ab.", fields: [], subjects: ["issue"] },
  start_testenv: { summary: "Startet die Testumgebung des Tickets.", fields: [], subjects: ["issue"] },
  stop_testenv: {
    subjects: ["issue"],
    summary: "Räumt die Testumgebung ab (Container, Volumes, Worktree, Port). "
      + "Muss VOR dem Merge laufen.",
    fields: [],
  },

  accept_merge: {
    subjects: ["issue"],
    summary: "Mergt den Ticket-Branch bzw. öffnet einen Pull Request.",
    fields: [{ key: "timeout_sec", label: "Zeitlimit (s)", type: "number", placeholder: "900" }],
    outcomes: "Läuft asynchron; der Ausgang heißt wie das Ergebnis: merged, conflict, "
      + "pr_open, no_git, push_failed — sonst „weiter\".",
  },

  deploy: {
    subjects: ["issue"],
    summary: "Reiht ein Deployment ein.",
    fields: [{ key: "force", label: "Auch ohne Auto-Deploy", type: "boolean", default: false,
               hint: "Ohne Haken passiert nichts, wenn Auto-Deploy am Projekt aus ist." }],
  },

};

export const FALLBACK_SPEC: ActionSpec = KEINE;
