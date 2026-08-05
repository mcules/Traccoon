// Das Ereignis-Log, gegen das `office-check.mjs` prüft. Eingefroren.
//
// **Nicht ausgedacht, sondern nachgebaut.** Jede Zeile hier hat exakt die Form, die
// `backend/app/services/office.py::step_events` erzeugt — dieselbe Schlüsselmenge, die
// `backend/tests/test_office_normalize.py::FIELD_KEYS` als Vertrag festhält, derselbe
// Umschlag aus `_event`, dieselbe `seq`-Formel `run_steps.id * 4 + slot`. Eine Fixture,
// die etwas anderes schickt, prüft eine Ansicht, die es nicht gibt.
//
// Bewusst enthalten, weil jede dieser Formen im Raum etwas anderes auslöst:
//
//   · **drei Läufe** — ein Wurzellauf (8871, bekommt den Chefplatz), sein **delegierter
//     Unterlauf** (8872, `parent_run_id` gesetzt → Spawn-Linie und Übergabe am Ende) und ein
//     zweiter Wurzellauf (8873, Assistent).
//   · **Werkzeuge mit Erfolg UND Fehlschlag** — `ok: true`, `ok: false` mit Fehlerpräfix und
//     einmal `ok: null` (Altzeile: unbekannt, **nicht** Erfolg).
//   · **ein `gate`** — 8873 endet `blocked`/`ask_human`, die Figur hebt die Hand und geht
//     gerade NICHT durch die Tür.
//   · **Lücken in der Zeit** — eine über `MAX_GAP_MS` (20 s) hinaus, damit die Klemmung im
//     Replay wirklich zieht, und mehrere kleine.
//   · **ein `ts`, das gegenüber `seq` rückwärts läuft** (Zeile 454) — unter
//     `WORKER_CONCURRENCY > 1` der Normalfall und die Ursache der unteren Klemmung.
//   · **gleiche Zeitstempel** (`agent_text` + `usage`, `tool_start` + `agent_spawn`) — sie
//     müssen zusammen wirken, bevor die Uhr weiterläuft.
//   · **genau ein `run_end` je Lauf.** Fehlt er, bleibt die Figur für immer stehen.
//   · **ein Deployment mit beiden Enden** (`deploy` `start` → `fail`) — der Serverschrank ist
//     der einzige Zustand im `Frame`, der an keiner Figur hängt, und ohne diese zwei Zeilen
//     enthielte kein goldener Rahmen ein leuchtendes Rack. Die Ops-Hashes prüften die neue
//     Zeichnung dann nie: eine Prüfung, die den neuen Code nicht ausführt, ist Theater.
//
// Wer hier etwas ändert, ändert `tools/golden.json` mit — und zwar bewusst (`--bless`).

const SID = "issue:412";
const PROJECT_ID = 27;
const OWNER_ID = 3;

/** Nullpunkt des Logs. UTC mit Millisekunden, genau wie `services/office.py::_ts`. */
const T0 = Date.parse("2026-08-05T11:22:33.000Z");

/** ms nach `T0` → ISO-8601 mit Millisekunden. */
function ts(ms) {
  const d = new Date(T0 + ms);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`
    + `T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
    + `.${p(d.getUTCMilliseconds(), 3)}Z`;
}

/** `seq = run_steps.id * 4 + slot`. Slots: 0 Vorgänger · 1 Haupt · 2 abgeleitet · 3 frei. */
function seq(stepId, slot) {
  return stepId * 4 + slot;
}

/** Der gemeinsame Umschlag — Feld für Feld `services/office.py::_event`. */
function ev(runId, stepId, slot, atMs, kind, fields) {
  return {
    v: 1, seq: seq(stepId, slot), ts: ts(atMs), sid: SID,
    project_id: PROJECT_ID, owner_id: OWNER_ID,
    run_id: runId, agent_id: `run:${runId}`,
    kind, ...fields,
  };
}

// ── Das Log ──────────────────────────────────────────────────────────────────

export const EVENTS = [
  // Kopfzeile des Raums. `seq: 0` — sie steht vor allem anderen (`api/office.py` schiebt sie
  // mit `events.insert(0, …)` davor).
  ev(8871, 0, 0, 0, "session_seen", {
    title: "Anmeldung schlägt bei Umlauten fehl", issue_key: "ABC-412",
    project_key: "TRA", started_at: ts(0),
  }),

  // ── Wurzellauf 8871 ────────────────────────────────────────────────────────
  ev(8871, 100, 1, 0, "run_start", {
    agent: "exec_agent", phase: "execute", provider: "claude_code", model: "sonnet",
    parent_run_id: null, parent_tool_use_id: null, spawn_depth: 0,
    continuation_index: 0, task_id: null, issue_key: "ABC-412",
  }),
  ev(8871, 101, 1, 1200, "user_message", {
    source: "ticket",
    text: "Beim Login mit Umlauten im Passwort kommt ein 500er. Bitte fixen und testen.",
  }),
  ev(8871, 102, 1, 3400, "agent_text", {
    text: "Ich schaue mir zuerst den Login-Handler an.",
  }),
  ev(8871, 102, 2, 3400, "usage", {
    in_tokens: 4210, out_tokens: 96, cache_read_tokens: 3800, cache_write_tokens: 0,
    provider: "claude_code", model: "sonnet",
  }),

  ev(8871, 103, 1, 4100, "tool_start", {
    tool: "fs_read", target: "backend/app/api/auth.py", tool_use_id: "tu-1",
    args_preview: '{"path": "backend/app/api/auth.py"}',
  }),
  ev(8871, 104, 1, 4350, "tool_result", {
    tool: "fs_read", tool_use_id: "tu-1", ok: true, error: "", duration_ms: 243,
    result_preview: "from fastapi import APIRouter\n…",
  }),

  // Fehlschlag mit belegtem Fehlerpräfix (`ERROR_PREFIXES`).
  ev(8871, 105, 1, 5000, "tool_start", {
    tool: "codegraph", target: "verify_password", tool_use_id: "tu-2",
    args_preview: '{"query": "verify_password"}',
  }),
  ev(8871, 106, 1, 5900, "tool_result", {
    tool: "codegraph", tool_use_id: "tu-2", ok: false,
    error: "FEHLER: kein Index für diesen Worktree",
    duration_ms: 902, result_preview: "FEHLER: kein Index für diesen Worktree",
  }),

  // Delegation. Der Spawn hängt am **Start** (`step_events`), nicht am Ergebnis — der
  // Unterlauf wird inline abgewartet.
  ev(8871, 107, 1, 7000, "tool_start", {
    tool: "delegate", target: "review_agent", tool_use_id: "tu-3",
    args_preview: '{"role": "review_agent", "task": "Patch gegenlesen"}',
  }),
  ev(8871, 107, 2, 7000, "agent_spawn", {
    child_role: "review_agent", prompt: "Patch gegenlesen", tool_use_id: "tu-3",
    background: false,
  }),

  // ── Unterlauf 8872 ─────────────────────────────────────────────────────────
  ev(8872, 108, 1, 7200, "run_start", {
    agent: "review_agent", phase: "execute", provider: "codex", model: "gpt-5-codex",
    parent_run_id: 8871, parent_tool_use_id: "tu-3", spawn_depth: 1,
    continuation_index: 0, task_id: null, issue_key: "ABC-412",
  }),
  // Über `THINK_FROM_CHARS` (180) → wird eine Denkblase, keine Sprechblase.
  ev(8872, 109, 1, 9000, "agent_text", {
    text: "Der Handler dekodiert das Passwort mit latin-1, bevor er es an bcrypt reicht; bei "
      + "einem Umlaut im Klartext wirft das eine UnicodeDecodeError. Ich prüfe, ob dieselbe "
      + "Stelle auch beim Registrieren steht, sonst schlagen Anlegen und Anmelden verschieden fehl.",
  }),
  ev(8872, 110, 1, 10500, "tool_start", {
    tool: "fs_write", target: "backend/app/api/auth.py", tool_use_id: "tu-4",
    args_preview: '{"path": "backend/app/api/auth.py", "content": "…"}',
  }),
  ev(8872, 111, 1, 10900, "tool_result", {
    tool: "fs_write", tool_use_id: "tu-4", ok: true, error: "", duration_ms: 61,
    result_preview: "geschrieben: backend/app/api/auth.py",
  }),
  // Abgeleiteter Begleiter (Slot 2) — nur bei belegtem Erfolg eines `EDIT_TOOLS`.
  ev(8872, 111, 2, 10900, "file_edit", { path: "backend/app/api/auth.py" }),
  ev(8872, 112, 1, 12000, "run_end", {
    ok: true, status: "success", blocker_kind: null,
    summary: "Kodierung auf utf-8 umgestellt, Testfall ergänzt.", error: "",
    iterations: 4, in_tokens: 18400, out_tokens: 1120, cache_read_tokens: 16000,
    cost_usd: 0.0412, cost_priced: true,
  }),

  // `ts` läuft gegenüber `seq` RÜCKWÄRTS: der Usage-Begleiter des Unterlaufs kommt später an,
  // trägt aber die frühere Uhrzeit. Ohne die untere Klemmung drehte das die Engine zurück.
  ev(8872, 113, 2, 11800, "usage", {
    in_tokens: 900, out_tokens: 210, cache_read_tokens: 800, cache_write_tokens: 0,
    provider: "codex", model: "gpt-5-codex",
  }),
  ev(8871, 113, 1, 12200, "tool_result", {
    tool: "delegate", tool_use_id: "tu-3", ok: true, error: "", duration_ms: 5180,
    result_preview: "review_agent: Kodierung auf utf-8 umgestellt, Testfall ergänzt.",
  }),

  // ── Der Serverschrank geht an ──────────────────────────────────────────────
  // Echte Zeile des Watchers (`services/deploy_watch.py`), Slot 1, Felder exakt
  // `services/office.py::deploy_fields`. `log_head` ist beim Start leer — es gibt noch kein Log.
  // Die auslösende Figur ist der Wurzellauf: er hat gerade das Review zurückbekommen.
  ev(8871, 114, 1, 13500, "deploy", {
    deployment_id: 341, state: "start",
    target: "/opt/docker/stacks/traccoon", log_head: "",
  }),

  // ── Lücke: 31,5 s ohne ein Ereignis (über MAX_GAP_MS = 20 s) ───────────────

  // ── Zweiter Wurzellauf 8873 (Assistent) ────────────────────────────────────
  ev(8873, 115, 1, 45000, "run_start", {
    agent: "assistant", phase: "execute", provider: "claude_code", model: "sonnet",
    parent_run_id: null, parent_tool_use_id: null, spawn_depth: 0,
    continuation_index: 0, task_id: null, issue_key: "ABC-412",
  }),
  ev(8873, 116, 1, 45500, "user_message", {
    source: "chat", text: "Wie viele Tickets hängen noch an diesem Fehler?",
  }),
  ev(8873, 117, 1, 46200, "tool_start", {
    tool: "traccoon_list_issues", target: null, tool_use_id: "tu-5",
    args_preview: '{"project": "TRA"}',
  }),
  // `ok: null` — Altzeile ohne erkennbares Präfix. Unbekannt, NICHT Erfolg.
  ev(8873, 118, 1, 46900, "tool_result", {
    tool: "traccoon_list_issues", tool_use_id: "tu-5", ok: null, error: "",
    duration_ms: null, result_preview: "ABC-412, ABC-418",
  }),
  ev(8873, 119, 1, 48000, "run_end", {
    ok: null, status: "blocked", blocker_kind: "ask_human",
    summary: "Soll ABC-418 mit erledigt werden?", error: "",
    iterations: 2, in_tokens: 3100, out_tokens: 240, cache_read_tokens: 2900,
    cost_usd: 0.0071, cost_priced: false,
  }),

  // Systemmeldung: löst bewusst kein Kommando aus (`mapEvent` gibt `[]` zurück).
  ev(8871, 120, 1, 48500, "system", {
    text: "Kontext kompaktiert (42 Nachrichten → 12).",
  }),

  // ── Abschluss des Wurzellaufs ──────────────────────────────────────────────
  ev(8871, 121, 1, 52000, "agent_text", { text: "Fix ist drin, Prüfung läuft grün." }),

  // Das Gegenstück zum `start` von oben — **dasselbe** `deployment_id`. Genau deshalb braucht
  // der Rack-Zustand keinen Verfall: beide Enden sind echte Ereignisse. `log_head` trägt den
  // Wächter-Text, den in der Wirklichkeit alle 56 fehlgeschlagenen Deployments tragen
  // (`api/deployments.py`) — gekürzt schickt ihn schon das Backend (240 Zeichen).
  ev(8871, 122, 1, 52500, "deploy", {
    deployment_id: 341, state: "fail",
    target: "/opt/docker/stacks/traccoon",
    log_head: "Abgelehnt: Self-Deploy nur über das explizite Wartungs-Kommando.",
  }),

  ev(8871, 123, 1, 54000, "run_end", {
    ok: true, status: "success", blocker_kind: null,
    summary: "Login akzeptiert jetzt Umlaute; Regressionstest ergänzt.", error: "",
    iterations: 9, in_tokens: 52000, out_tokens: 3400, cache_read_tokens: 47000,
    cost_usd: 0.1938, cost_priced: true,
  }),
];

/** Der Roster, wie ihn die Lese-API neben `events[]` liefert (`api/office.py::_agent_row`).
 *  `mapEvent` braucht ihn für Rolle, Elternlauf und die Übergabe am Laufende. */
export const ROSTER = [
  {
    agent_id: "run:8871", run_id: 8871, agent: "exec_agent", phase: "execute",
    status: "success", issue_key: "ABC-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "claude_code", model: "sonnet", parent_run_id: null, spawn_depth: 0,
    started_at: ts(0), ended_at: ts(54000), iterations: 9,
    in_tokens: 52000, out_tokens: 3400, cache_read_tokens: 47000,
    cost_usd: 0.1938, cost_priced: true,
  },
  {
    agent_id: "run:8872", run_id: 8872, agent: "review_agent", phase: "execute",
    status: "success", issue_key: "ABC-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "codex", model: "gpt-5-codex", parent_run_id: 8871, spawn_depth: 1,
    started_at: ts(7200), ended_at: ts(12000), iterations: 4,
    in_tokens: 18400, out_tokens: 1120, cache_read_tokens: 16000,
    cost_usd: 0.0412, cost_priced: true,
  },
  {
    agent_id: "run:8873", run_id: 8873, agent: "assistant", phase: "execute",
    status: "blocked", issue_key: "ABC-412", project_id: PROJECT_ID, project_key: "TRA",
    provider: "claude_code", model: "sonnet", parent_run_id: null, spawn_depth: 0,
    started_at: ts(45000), ended_at: ts(48000), iterations: 2,
    in_tokens: 3100, out_tokens: 240, cache_read_tokens: 2900,
    cost_usd: 0.0071, cost_priced: false,
  },
];

/** Anfang und Ende des Logs in Epoch-ms — die Grenzen, zwischen denen geprüft wird. */
export const T_FROM = T0;
export const T_TO = T0 + 54000;

/** Die acht Zeitpunkte des goldenen Bildes, als Versatz zu `T_FROM`.
 *
 *  Gewählt, nicht gestreut — ein gleichmäßiges Raster trifft die interessanten Momente nicht.
 *  Jeder Punkt hier steht für einen Zustand, den nur er zeigt:
 *
 *    0      Ankunft: die Figur steht noch an der Tür, nichts ist gelaufen
 *    1200   mitten im Hereinlaufen — der einzige Punkt, an dem eine Interpolation geprüft wird
 *    7200   der Unterlauf betritt den Raum: Spawn-Linie, Funke am Werkzeug
 *    12000  `run_end` des Unterlaufs: Urteil, Emote, der Zettel fällt, die Übergabe wird geplant
 *    15500  Ankunft an der Übergabe — im `SETTLE_MS`-Fenster, in dem noch getrippelt wird;
 *           zugleich steht der Wurzellauf am Rack, das seit 2 s `start` zeigt (steigender Balken)
 *    25200  durch die Tür: `retired`, Sitzplatz wieder frei; das Rack leuchtet weiter, weil das
 *           Deployment noch läuft — genau der Zustand ohne Verfall
 *    48000  nach der 31,5-s-Lücke: Simulationszeit steht bei 36,5 s, weil `MAX_GAP_MS` klemmt;
 *           dazu die erhobene Hand des Gates
 *    54000  Ende des Logs: beide Wurzelläufe abgeschlossen bzw. wartend, das Rack steht auf
 *           `fail` (drei rote Reihen) und das „✗" über dem Schrank ist noch nicht verklungen
 *
 *  Ein Punkt, der bei einer geänderten Konstante NICHT umkippt, prüft nichts — wer hier etwas
 *  streicht, sollte vorher wissen, welche Zeile im Code dann ungeprüft bleibt. */
export const GOLDEN_OFFSETS = [0, 1200, 7200, 12000, 15500, 25200, 48000, 54000];
