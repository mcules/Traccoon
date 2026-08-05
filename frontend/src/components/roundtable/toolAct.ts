// Schicht 0 — Werkzeugname → Bild.
//
// `ToolAct = read | write | run | browse | delegate | other`: **sechs Bilder statt vierzig Namen**.
// Roundtables Begründung, und sie gilt hier unverändert: einen Agenten ans Fenster laufen zu
// lassen, weil ein Werkzeugname zufällig „web" enthält, ist eine Lüge, die der Zuschauer nicht
// überprüfen kann. Er sieht eine Figur browsen und hat keine Möglichkeit festzustellen, dass in
// Wahrheit eine Datei gelesen wurde. Lieber kein Bild als ein falsches.
//
// ── Der Kompromiss, offen benannt ────────────────────────────────────────────
//
// Roundtables Grundsatz lautet „eine Tabelle, keine Heuristik". Der hält dort, weil dessen
// Werkzeugmenge geschlossen ist: Claude Code hat ein festes Dutzend Tools. Traccoons Menge ist
// es nicht — jeder MCP-Server, den ein Nutzer registriert, bringt beliebig benannte Werkzeuge
// mit (`obsidian__obsidian_get_note`, `homeassistant__call_service`, …). Eine erschöpfende
// Tabelle dafür kann es nicht geben.
//
// Der Kompromiss bewahrt die Absicht:
//   1. `TOOL_ACT` ist **erschöpfend für jedes Werkzeug, das Traccoon selbst besitzt** — die
//      nativen aus `worker/runtime.py`, die deutschen Gedächtniswerkzeuge aus
//      `worker/tools_memory.py` und alle zwanzig `traccoon_*` aus `worker/tools_traccoon.py`.
//      Für die driftet nichts, sie sind aufgezählt.
//   2. `NATIVE_TOOLS` ist eine **eigene, gepflegte Liste** derselben Namen. Bewusst nicht aus
//      `Object.keys(TOOL_ACT)` abgeleitet: ein vergessenes Werkzeug fehlte sonst in beiden und
//      der Prüfer (Welle M) merkte nichts. Er hält die Backend-Sollliste gegen `NATIVE_TOOLS`
//      **und** `NATIVE_TOOLS` gegen die Tabelle — erst dadurch bricht der Build bei einer Lücke.
//   3. Die Heuristik greift **nur** bei MCP-Namen und **nur** für `read`/`write`/`run`.
//      `browse` und `delegate` sind daraus nicht erreichbar: ob ein fremdes Werkzeug ins Netz
//      geht oder Arbeit weiterreicht, steht in seinem Namen nicht drin. Genau das war die Lüge
//      oben.

import type { MoodKind, ScreenKind, ToolAct } from "./types.ts";

// ── 1. Die Tabelle — autoritativ, driftet nie ────────────────────────────────

/** Jedes Traccoon-eigene Werkzeug mit seinem Bild. Reihenfolge = Herkunftsdatei im Worker,
 *  damit ein neues Werkzeug dort nachgetragen wird, wo man es sucht. */
export const TOOL_ACT: Record<string, ToolAct> = {
  // ── worker/runtime.py — native Werkzeuge ───────────────────────────────────
  // `submit_plan` schreibt kein File, liefert aber ein Schriftstück ab; `write` ist das
  // ehrlichste der sechs Bilder.
  submit_plan: "write",
  // `ask_human`/`continue_later` sind Gesten an den Ablauf, keine Tätigkeit. Was der Zuschauer
  // sehen soll, kommt aus dem `gate`-Kommando (Hand heben), nicht aus einer Werkzeugpose.
  ask_human: "other",
  continue_later: "other",
  fs_read: "read",
  fs_list: "read",
  fs_write: "write",
  fs_edit: "write",
  check: "run",
  deploy: "run",
  // Rendert die Projektseite und sieht sie an — das ist der eine native Fall von „browse".
  screenshot: "browse",
  read_attachment: "read",
  open_tasks: "read",
  codegraph: "read",
  delegate: "delegate",
  load_skill: "read",

  // ── worker/tools_memory.py — die deutschen Gedächtniswerkzeuge ─────────────
  erinnere_dich: "write",
  vergiss: "write",
  gedaechtnis_suchen: "read",

  // ── worker/tools_traccoon.py — die zwanzig Steuerwerkzeuge ─────────────────
  traccoon_list_projects: "read",
  traccoon_list_issues: "read",
  traccoon_get_issue: "read",
  traccoon_create_issue: "write",
  traccoon_comment: "write",
  traccoon_assign_agent: "write",
  // Stößt einen fremden Lauf an. **Nicht** `delegate`: das Bild dazu ist die Spawn-Linie zu
  // einer Figur im selben Raum, und die entsteht hier nie — der geplante Lauf gehört zu einem
  // anderen Ticket und damit zu einer anderen Session.
  traccoon_start_planning: "run",
  traccoon_approve_plan: "write",
  traccoon_issue_costs: "read",
  // Nachricht nach draußen an einen Menschen: weder Lesen noch Schreiben noch Laufenlassen.
  traccoon_notify_human: "other",
  traccoon_list_destinations: "read",
  traccoon_list_jobs: "read",
  traccoon_get_job: "read",
  traccoon_job_templates: "read",
  traccoon_create_job: "write",
  traccoon_update_job: "write",
  traccoon_run_job: "run",
  traccoon_list_workflows: "read",
  traccoon_start_workflow: "run",
  // Der einzige Weg eines Agenten ins Netz (Ziele, `allow_agents`) — hier ist „browse" belegt.
  traccoon_http_call: "browse",
};

/** Die Sollliste, gegen die der Prüfer testet: jeder Name, den Traccoon selbst als Werkzeug
 *  anbietet. Quellen sind `worker/runtime.py` (`*_TOOL`-Konstanten und `_delegate_tool`),
 *  `worker/tools_memory.py::MEMORY_TOOLS` und `worker/tools_traccoon.py::TRACCOON_TOOLS`.
 *
 *  Doppelt geführt und nicht aus `TOOL_ACT` abgeleitet — siehe Kopf, Punkt 2. */
export const NATIVE_TOOLS: readonly string[] = [
  // runtime.py (15)
  "submit_plan", "ask_human", "continue_later",
  "fs_read", "fs_list", "fs_write", "fs_edit",
  "check", "deploy", "screenshot", "read_attachment", "open_tasks", "codegraph",
  "delegate", "load_skill",
  // tools_memory.py (3)
  "erinnere_dich", "vergiss", "gedaechtnis_suchen",
  // tools_traccoon.py (20)
  "traccoon_list_projects", "traccoon_list_issues", "traccoon_get_issue",
  "traccoon_create_issue", "traccoon_comment", "traccoon_assign_agent",
  "traccoon_start_planning", "traccoon_approve_plan", "traccoon_issue_costs",
  "traccoon_notify_human", "traccoon_list_destinations",
  "traccoon_list_jobs", "traccoon_get_job", "traccoon_job_templates",
  "traccoon_create_job", "traccoon_update_job", "traccoon_run_job",
  "traccoon_list_workflows", "traccoon_start_workflow", "traccoon_http_call",
];

// ── 2./3. MCP ────────────────────────────────────────────────────────────────

/** Trenner zwischen Servername und Werkzeugname bei MCP-Werkzeugen.
 *  Gesetzt in `worker/mcp_client.py::MultiMcpSession.list_tools` (`f"{name}__{t.name}"`);
 *  MCPJungle liefert seine Gateway-Werkzeuge in derselben Form. */
const MCP_SEP = "__";

/** Die eine Heuristik. Sie sieht ausschließlich MCP-Namen und kennt nur drei der sechs Bilder.
 *
 *  Angewandt wird sie auf die **Wortmarken** des nackten Namens, nicht auf den Namensanfang:
 *  ein striktes `startsWith` feuerte fast nie, weil MCP-Server ihren eigenen Namen als erste
 *  Marke wiederholen (`obsidian_get_note`, `calendar_list_events`). Die erste Marke, die in
 *  dieser Tabelle steht, gewinnt; findet sich keine, bleibt es `other` — und `other` ist keine
 *  Notlösung, sondern die richtige Antwort auf „ich weiß es nicht". */
const MCP_VERB: Record<string, ToolAct> = {
  get: "read", list: "read", read: "read", search: "read", find: "read", query: "read",
  fetch: "read", describe: "read", show: "read", view: "read", download: "read",
  resolve: "read", overview: "read", history: "read", summary: "read",
  create: "write", add: "write", update: "write", set: "write", write: "write",
  save: "write", delete: "write", remove: "write", patch: "write", post: "write",
  put: "write", upload: "write", rename: "write", move: "write", mark: "write",
  bulk: "write", manage: "write", submit: "write", send: "write", edit: "write",
  append: "write", replace: "write", insert: "write",
  run: "run", start: "run", stop: "run", exec: "run", execute: "run", trigger: "run",
  call: "run", restart: "run", reload: "run", build: "run", deploy: "run", sync: "run",
  render: "run", fire: "run", install: "run",
};

/** `hasOwnProperty` statt `in`: ein Werkzeug namens `constructor` oder `toString` liefe sonst
 *  über die Prototypenkette und bekäme ein Bild, das nirgends in der Tabelle steht. */
function tableHas<T>(table: Record<string, T>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(table, key);
}

/** Werkzeugname → Bild. Auflösungsreihenfolge:
 *
 *  1. **exakte Tabelle** — autoritativ, driftet nie;
 *  2. MCP-Namen `server__tool` auf den nackten Namen kürzen und noch einmal die Tabelle fragen
 *     (ein Projekt-MCP darf durchaus `traccoon__fs_read` heißen);
 *  3. genau eine Präfix-Heuristik, und die gilt **nur** für MCP.
 *
 *  Ein unbekannter Name **ohne** `__` ist kein MCP-Werkzeug, also entweder ein Tippfehler oder
 *  ein neues natives Werkzeug, das in die Tabelle gehört — der bekommt `other` und der Prüfer
 *  meldet die Lücke. Ihn heuristisch zu erraten hieße, die Lücke zu verstecken. */
export function toolAct(name: string): ToolAct {
  const tool = (name || "").trim();
  if (!tool) return "other";
  if (tableHas(TOOL_ACT, tool)) return TOOL_ACT[tool];

  const cut = tool.lastIndexOf(MCP_SEP);
  if (cut < 0) return "other";           // kein MCP → keine Heuristik
  const bare = tool.slice(cut + MCP_SEP.length);
  if (tableHas(TOOL_ACT, bare)) return TOOL_ACT[bare];

  for (const mark of bare.toLowerCase().split("_")) {
    if (mark && tableHas(MCP_VERB, mark)) return MCP_VERB[mark];
  }
  return "other";
}

// ── Monitor und Stimmung ─────────────────────────────────────────────────────

/** Bild je Tätigkeit. Die Grundtabelle — sie hat für jedes der sechs `ToolAct` einen Eintrag,
 *  damit nie ein Fall durchfällt. */
const SCREEN_BY_ACT: Record<ToolAct, ScreenKind> = {
  read: "code",
  write: "code",
  run: "log",
  browse: "page",
  delegate: "link",
  other: "blank",
};

/** Die wenigen Werkzeuge, bei denen das Bild der Tätigkeit sichtbar falsch wäre.
 *  Kurz halten: jede Zeile hier ist eine Ausnahme von „sechs Bilder statt vierzig Namen"
 *  und muss sich lohnen. */
const SCREEN_BY_TOOL: Record<string, ScreenKind> = {
  // Suchen sieht anders aus als Lesen — Trefferliste statt Quelltext.
  codegraph: "search",
  gedaechtnis_suchen: "search",
  fs_list: "search",
  // Etwas Gerendertes ansehen, nicht Quelltext.
  screenshot: "page",
  read_attachment: "page",
};

/** Was auf dem Monitor steht.
 *
 *  `waiting` gewinnt vor allem anderen: eine Figur, die auf einen Menschen wartet, ist genau
 *  das, was der Zuschauer sehen soll — auch wenn im Hintergrund noch ein Werkzeug offen ist
 *  (der Berechtigungsdialog hält den Aufruf ja gerade an). */
export function screenFor(act: ToolAct | undefined, tool: string | undefined,
                          waiting: boolean): ScreenKind {
  if (waiting) return "wait";
  const t = (tool || "").trim();
  if (t && tableHas(SCREEN_BY_TOOL, t)) return SCREEN_BY_TOOL[t];
  if (!act) return "blank";
  return SCREEN_BY_ACT[act];
}

/** Stimmung der Anzeige.
 *
 *  Reihenfolge ist eine Aussage: **fertig** schlägt alles (ein beendeter Lauf ist beendet, auch
 *  wenn unterwegs etwas schieflief), dann **wartend** (der einzige Zustand, in dem der Mensch
 *  etwas tun muss — er darf nicht hinter einem alten Fehler verschwinden), dann **Fehler**,
 *  sonst **Arbeit**.
 *
 *  `fails`/`waiting` sind Zähler, keine Schalter: dieselbe Funktion beschriftet die Kachel
 *  eines einzelnen Agenten (0/1) und die Zusammenfassung eines ganzen Raums (n). */
export function moodFor(done: boolean, fails: number, waiting: number): MoodKind {
  if (done) return "done";
  if (waiting > 0) return "wait";
  if (fails > 0) return "error";
  return "work";
}
