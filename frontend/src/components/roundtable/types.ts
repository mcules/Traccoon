// Schicht 0 — der Vertrag des „Büros". Nur Typen, kein einziger Laufzeitwert.
//
// Drei Dinge stehen hier: der Ereignis-Strom vom Backend (`Ev`), das Kommandovokabular des
// Raums (`Cmd`) und der Zustand, den die Engine daraus baut (`ActorState`, `Frame`).
// Dazwischen liegt genau eine reine Funktion (`mapEvent`, Welle F) — die ist die Naht.
//
// Regeln siehe PIXEL-CONTRACT.md. Für diese Datei zählt vor allem Regel 5:
// kein `enum`, kein `namespace`, nichts, was das Streichen der Typen überlebt.

// ── Kleine Vokabeln ──────────────────────────────────────────────────────────

/** Sechs Bilder statt vierzig Werkzeugnamen. Zuordnung: `toolAct.ts` (Tabelle, keine Heuristik). */
export type ToolAct = "read" | "write" | "run" | "browse" | "delegate" | "other";

/** Was auf dem Monitor der Figur steht. Abgeleitet aus `ToolAct` (`toolAct.ts::screenFor`),
 *  nicht aus dem Werkzeugnamen — sonst behauptete der Bildschirm mehr, als das Log hergibt.
 *  `blank` = dunkler Schirm (kein laufendes Werkzeug). */
export type ScreenKind = "code" | "log" | "page" | "search" | "link" | "wait" | "blank";

/** Grundstimmung einer Anzeige (Monitorrahmen, Dock-Kachel). Vier Zustände, vier Farben —
 *  dieselben wie in der Zeitleiste, damit zwei Ansichten desselben Laufs nie widersprechen. */
export type MoodKind = "work" | "wait" | "error" | "done";

/** Spiegelt `Run.status` im Backend. */
export type RunStatus =
  | "running" | "success" | "failed" | "blocked" | "planned" | "loop_exhausted";

/** Färbt ausschließlich den Rand einer Sprechblase, nichts sonst.
 *  `success|planned → ok` · `failed|loop_exhausted → err` · `blocked → blocked` · sonst `null`. */
export type Verdict = "ok" | "err" | "blocked" | null;

export type Pose = "sit" | "walk" | "stand";

/** Warum jemand auf einen Menschen wartet: Rückfrage, Berechtigungsanfrage, Planfreigabe. */
export type GateKind = "question" | "permission" | "plan";

export interface Pt { x: number; y: number }

/** Der Zeichenkontext, so viel davon wie der Pixel-Vertrag erlaubt — und **nur** so viel
 *  (Regel 2.1: `fillStyle`, `globalAlpha`, `fillRect`).
 *
 *  Absichtlich ein eigener, struktureller Typ statt `CanvasRenderingContext2D`: damit ist das
 *  Regelwerk **im Typsystem** verankert, ein `ctx.beginPath()` in Schicht 1 also schon ein
 *  Typfehler und nicht erst ein Prüferfehler. Ein echter `CanvasRenderingContext2D` erfüllt die
 *  Form (deshalb ist `fillStyle` als `string | object` weit genug für `CanvasGradient`), und der
 *  Stub des Prüfers erfüllt sie ebenfalls — er sammelt einfach die `fillRect`-Aufrufe ein. */
export interface Ctx {
  fillStyle: string | object;
  globalAlpha: number;
  fillRect(x: number, y: number, w: number, h: number): void;
}

// ── Ereignis-Strom (Backend → Frontend) ──────────────────────────────────────
//
// snake_case, weil das ganze Repo snake_case spricht und das Backend die Felder genau so
// baut (`services/roundtable.py::step_events`). Eine camelCase-Übersetzungsschicht wäre
// dreißig Zeilen ohne einen einzigen gewonnenen Gedanken — es gibt sie deshalb nicht.

/** Umschlag, den jedes Ereignis trägt. */
export interface EvBase {
  /** Schema-Version des Vertrags. Aktuell 1. */
  v: number;
  /** `run_steps.id * 4 + slot`, global monoton, **Ankunftsreihenfolge**.
   *  Slots: 0 = synthetisierter Vorgänger (Altzeilen), 1 = Hauptereignis,
   *  2 = abgeleiteter Begleiter (`usage`/`file_edit`/`agent_spawn`), 3 = frei.
   *  Das Log wird nach `seq` geordnet, **nie** nach `ts` — siehe `MAX_GAP_MS` in `const.ts`. */
  seq: number;
  /** ISO-8601 mit Millisekunden. Nur für Zeitabstände und die Zeitleiste, nie für Reihenfolge. */
  ts: string;
  /** Session = ein Laufbaum: `"issue:412"` (Ticketraum) oder `"run:8871"` (Job/Assistent). */
  sid: string;
  /** An jedem Ereignis, damit die WS-Brücke ohne DB-Zugriff autorisieren kann. */
  project_id: number | null;
  owner_id: number | null;
  run_id: number;
  /** Stabile Aktor-Id im Raum, Form `"run:8871"`. Seed der Figur = `hash32(agent_id)`. */
  agent_id: string;
}

/** Eröffnet den Raum: Titel und Herkunft der Session. */
export interface EvSessionSeen extends EvBase {
  kind: "session_seen";
  title: string;
  issue_key: string | null;
  project_key: string | null;
  started_at: string | null;
}

/** Ein Lauf betritt den Raum. Auch der **einzige** Spawn-Auslöser:
 *  `parent_run_id`/`parent_tool_use_id` gesetzt = Unteragent. (`delegate` taugt dafür nicht,
 *  weil `runtime.py` den Unterlauf inline abwartet und die Zeile erst bei dessen Ende schreibt.) */
export interface EvRunStart extends EvBase {
  kind: "run_start";
  /** Rolle, z. B. `plan_agent`, `exec_agent`, `review_agent`, `assistant`. */
  agent: string;
  phase: "plan" | "execute";
  provider: string | null;
  model: string | null;
  parent_run_id: number | null;
  parent_tool_use_id: string | null;
  /** 0 = Wurzellauf. */
  spawn_depth: number;
  /** Fortsetzung nach Kontext-Kompaktierung; 0 = erster Anlauf. */
  continuation_index: number;
  task_id: number | null;
  issue_key: string | null;
}

/** Etwas von außen: Mensch, PM, Job-Auslöser. */
export interface EvUserMessage extends EvBase {
  kind: "user_message";
  source: string;
  text: string;
}

/** Echter Text des Modells. Achtung: Assistenten-Züge, in denen das Modell nur Werkzeuge rief,
 *  tragen im Backend wörtlich `"(Tool-Call)"` — die filtert `mapEvent`, sonst sagt jeder Agent
 *  im Raum dauernd „(Tool-Call)". */
export interface EvAgentText extends EvBase {
  kind: "agent_text";
  text: string;
}

/** Tokens **eines** Modellzugs, nicht des Laufs. Summen für den Inspektor bildet Schicht 0. */
export interface EvUsage extends EvBase {
  kind: "usage";
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  /** Wer tatsächlich geantwortet hat — bei einem Fallback-Lauf nicht das konfigurierte Modell. */
  provider: string | null;
  model: string | null;
}

export interface EvToolStart extends EvBase {
  kind: "tool_start";
  tool: string;
  /** Kurzes Ziel für die Blase: Pfad, URL, Rolle. Aus einer Tabelle, nicht geraten. */
  target: string | null;
  /** Klammert `tool_start` und `tool_result`. Bei Altzeilen `"legacy:<run_step_id>"`. */
  tool_use_id: string;
  args_preview: string | null;
}

export interface EvToolResult extends EvBase {
  kind: "tool_result";
  tool: string;
  tool_use_id: string;
  /** **Dreiwertig.** `null` heißt *unbekannt* (Altzeile ohne erkennbares Fehlerpräfix),
   *  nicht Erfolg. Wer `ok ?? true` schreibt, malt grüne Häkchen auf geratene Daten. */
  ok: boolean | null;
  error: string | null;
  /** `null` beim Altdaten-Pfad: dort kennt ein Werkzeugschritt nur einen Zeitpunkt.
   *  Der Raum zeigt dann `TOOL_BUSY_MS` als Ersatzdauer. */
  duration_ms: number | null;
  result_preview: string | null;
}

/** Abgeleiteter Begleiter eines Schreibwerkzeugs (Slot 2). */
export interface EvFileEdit extends EvBase {
  kind: "file_edit";
  path: string;
}

/** Abgeleiteter Begleiter (Slot 2): ein Unteragent wurde angefordert.
 *  Zeichnet die Spawn-Linie; die Figur selbst entsteht erst mit dessen `run_start`. */
export interface EvAgentSpawn extends EvBase {
  kind: "agent_spawn";
  child_role: string;
  prompt: string | null;
  tool_use_id: string | null;
  background: boolean;
}

/** Der Lauf verlässt den Raum. Kommt genau einmal je `run_start` — fehlt er,
 *  bleibt die Figur für immer stehen. */
export interface EvRunEnd extends EvBase {
  kind: "run_end";
  ok: boolean;
  status: RunStatus;
  /** Worauf geblockt wurde, wenn `status === "blocked"`: `question`, `permission`, … */
  blocker_kind: string | null;
  summary: string | null;
  error: string | null;
  iterations: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  /** Dreiwertig: `true` = gegen den Katalog bepreist (auch 0,00 ist bepreist!),
   *  `false` = kein `ProviderModel`-Eintrag, `null` = Altzeile. Die UI zeigt bei
   *  unvollständiger Bepreisung ein „≥". */
  cost_priced: boolean | null;
}

/** Meldung des Systems (Abbruch, Kappung, Kompaktierung). */
export interface EvSystem extends EvBase {
  kind: "system";
  text: string;
}

/** RESERVIERT — wird vom Backend **nie** geschickt.
 *
 *  Kein Provider-Adapter in Traccoon liefert Denkblöcke; Anthropic-Thinking wird im Worker gar
 *  nicht angefordert und käme auch nicht als eigener Schritt an. Der Zweig bleibt im Union, damit
 *  ein späterer Adapter ihn ohne Vertragsbruch füllen kann.
 *
 *  Bitte nicht „reparieren", indem `agent_text` als `thinking` durchgereicht wird: die Zeitleiste
 *  hat eine eigene Farbe für Denken, und die wäre damit gelogen. */
export interface EvThinking extends EvBase {
  kind: "thinking";
  text: string;
}

export type Ev =
  | EvSessionSeen | EvRunStart | EvUserMessage | EvAgentText | EvUsage
  | EvToolStart | EvToolResult | EvFileEdit | EvAgentSpawn | EvRunEnd
  | EvSystem | EvThinking;

export type EvKind = Ev["kind"];

// ── Kommandos — das gesamte Vokabular des Raums ──────────────────────────────
//
// Zwölf Stück, mehr gibt es nicht. Alles, was der Raum tun kann, ist eine Folge davon.
// `mapEvent(ev, ctx) -> Cmd[]` ist rein; die Engine kennt `Ev` überhaupt nicht.
//
// Gegenüber Roundtable **weggefallen**:
//   · `confront` — Roundtable lässt Agenten Behauptungen gegeneinander prüfen (CONFIRMED/REFUTED).
//     Traccoon hat kein solches Streitmodell: Läufe widersprechen einander nicht, sie scheitern
//     oder gelingen. Ein Kommando ohne Datenquelle wäre Dekoration.
//   · `prompt` — Roundtable zeigt den Anweisungstext eines Spawns als eigene Geste. Bei uns steckt
//     er in `agent_spawn.prompt` und gehört in den Inspektor, nicht auf die Bühne.
//
// **Neu** gegenüber Roundtable:
//   · `gate` / `resume` — die Mensch-im-Kreis-Form. `ask_human`, Berechtigungsanfragen und
//     Planfreigaben halten einen Lauf an, bis ein Mensch antwortet. Roundtable kennt das nicht,
//     weil dort niemand wartet. Bei uns ist es der häufigste Grund, warum ein Raum still steht —
//     und genau deshalb muss man es sehen (`GATE_PULSE_MS`).

export type Cmd =
  /** Legt die Figur an oder aktualisiert ihre Stammdaten. Idempotent — mehrfach erlaubt. */
  | { k: "ensureActor"; id: string; role: string; issue: string | null;
      phase: string | null; model: string | null; parent?: string }
  /** Denkblase (gepunktet). Wird derzeit nur aus abgeleiteten Zuständen erzeugt, nicht aus `thinking`. */
  | { k: "think"; id: string; text: string }
  /** Sprechblase mit Schreibmaschineneffekt (`TYPE_CPS`, `BUBBLE_MS`). */
  | { k: "say"; id: string; text: string }
  /** Werkzeug beginnt: Pose, Monitorbild und Beschäftigt-Zustand. */
  | { k: "tool"; id: string; act: ToolAct; tool: string; target?: string }
  /** Werkzeug endet. `ok: null` = unbekannt → neutrales Zeichen, kein grünes Häkchen. */
  | { k: "toolEnd"; id: string; ok: boolean | null }
  /** Datei geschrieben — zählt `edits` hoch und legt einen Zettel auf den Schreibtisch. */
  | { k: "edit"; id: string; path: string }
  /** Spawn-Linie von `parent` zu `id`. Die Figur `id` entsteht erst mit ihrem `ensureActor`. */
  | { k: "spawn"; id: string; parent: string; role: string }
  /** `id` läuft zu `to` und übergibt ein Ergebnis. Der Kern der Roundtable-Choreografie. */
  | { k: "deliver"; id: string; to: string; text?: string }
  /** Wartet auf einen Menschen. Hält die Figur an, bis `resume` kommt. */
  | { k: "gate"; id: string; kind: GateKind; text: string }
  /** Der Mensch hat geantwortet — Gate auf. */
  | { k: "resume"; id: string }
  /** Statuswechsel ohne Ortswechsel (färbt Dock und Blasenrand). */
  | { k: "status"; id: string; status: RunStatus }
  /** Fertig: Abschlussblase, dann durch die Tür (`DONE_LINGER_MS`). */
  | { k: "done"; id: string; ok: boolean; text?: string };

export type CmdKind = Cmd["k"];

// ── Zustand ──────────────────────────────────────────────────────────────────

/** Ein Eintrag je Agent. Alle Zeiten sind `engine.t` in ms (Simulationszeit, nicht Wanduhr).
 *  Koordinaten sind **Szenen**-Koordinaten (`SCENE` 1600×900); das Rendern multipliziert mit
 *  `POS_SCALE` und rundet erst dort — siehe PIXEL-CONTRACT.md Regel 1 und 2.3. */
export interface ActorState {
  /** `"run:8871"` — identisch zu `Ev.agent_id`. */
  id: string;
  role: string;
  issue: string | null;
  phase: string | null;
  model: string | null;

  /** Ganzzahlige Szenen-Koordinate. `y` ist der **Fußpunkt** (Sortierschlüssel der Szene). */
  x: number;
  y: number;
  /** Subpixel-Akkumulator: sammelt die Bruchteile der Bewegung, damit auch winzige `dt`
   *  vorankommen. Wird **nie** gerendert und nie gerundet zurückgeschrieben. */
  sub: Pt;

  pose: Pose;
  /** Blickt nach links. */
  flip: boolean;
  /** Auswärts — nicht im Raum sichtbar (`deskIndex === -2`). */
  away: boolean;

  /** Laufender Blasentext bzw. Startzeit für Schreibmaschineneffekt und `BUBBLE_MS`. */
  say?: string;
  sayAt?: number;
  /** Färbt nur den Blasenrand. */
  verdict: Verdict;
  think?: string;
  /** Startzeit der Denkblase (`engine.t`). Ohne sie hätte eine Denkblase keinen Ablauf und
   *  bliebe für immer stehen — dieselbe Rolle, die `sayAt` für die Sprechblase spielt. */
  thinkAt?: number;

  status: RunStatus;
  /** Sitzplatz: `0..11` = Pod (`hash32(runId) % 12`, deterministisch), `-1` = Chefplatz,
   *  `-2` = auswärts. */
  deskIndex: number;

  /** Laufendes Werkzeug. */
  act?: ToolAct;
  tool?: string;
  target?: string;
  /** `engine.t`, bis zu dem das Werkzeug beschäftigt aussieht. `0` = frei. */
  busy: number;
  /** Steht am Gate und wartet auf einen Menschen. */
  waiting: boolean;
  gate?: GateKind;

  /** Ergebnis des zuletzt beendeten Werkzeugs (dreiwertig, siehe `EvToolResult.ok`). */
  lastOk?: boolean | null;
  /** Zähler für Dock und Inspektor. */
  fails: number;
  resolved: number;

  /** Zuletzt geschriebener Pfad und Gesamtzahl. */
  edit?: string;
  edits: number;

  /** `engine.t` des Abschlusses — ab da läuft `DONE_LINGER_MS` bis zur Tür. */
  done?: number;
  doneOk?: boolean;

  /** Elternagent (Spawn). */
  parent?: string;
  /** Sichtbare Verbindungslinie bis `until` (`LINK_MS`). */
  link?: { to: string; until: number };
  /** „hat zugehört" — Kopfdrehung bis `heard` (`HEARD_MS`). */
  heard?: number;
  /** Hat den Raum durch die Tür verlassen; bleibt im Dock, verschwindet von der Bühne. */
  retired?: boolean;

  /** `hash32(id)`. Quelle **jeder** Variation dieser Figur — Aussehen, Gangart, Sitzplatz.
   *  Siehe PIXEL-CONTRACT.md Regel 3.2. */
  seed: number;
}

/** Kurzlebiger Effekt über der Szene. `t0`/`until` sind `engine.t`;
 *  Fortschritt = `(t - t0) / (until - t0)`, nie ein eigener Zähler (Regel 3.4). */
export interface Fx {
  kind: FxKind;
  /** Szenen-Koordinaten; `y` ist auch hier der Fußpunkt. */
  x: number;
  y: number;
  /** Ziel — nur bei `"link"`. */
  to?: Pt;
  t0: number;
  until: number;
  /** Bei `"emote"` genau ein Zeichen aus der Art-Tabelle. */
  text?: string;
  seed: number;
}

/** `emote` = Pop über dem Kopf (ersetzt Roundtables cheer/slump) · `spark` = Werkzeugfunke ·
 *  `link` = Spawn-/Übergabelinie · `drop` = Zettel fällt auf den Tisch. */
export type FxKind = "emote" | "spark" | "link" | "drop";

/** Das Einzige, was Schicht 1 zu sehen bekommt. `actors` ist **nach `y` sortiert**
 *  (Maler-Algorithmus) und eine Kopie — die Engine gibt nie ihre eigene Sammlung heraus. */
export interface Frame {
  /** Simulationszeit in ms seit `t0` der Session. */
  t: number;
  actors: ActorState[];
  fx: Fx[];
}

/** Eine Zeile des Logs, in **Ankunftsreihenfolge** (`seq`), nie nach `ts` sortiert.
 *  `ts` ist hier bereits die geparste Wanduhr in ms. */
export interface LogEntry {
  ts: number;
  seq: number;
  cmds: Cmd[];
}

// ── Aussehen (Schicht 1 liest das, Schicht 0 erzeugt es aus dem Seed) ────────

/** Die 19 Teile einer Figur, aufgelöst aus `seed`: Kopf 3 · Haar 5 · Torso 3 · Arme 4 · Beine 4.
 *  Die Farbfelder sind **Palettenschlüssel**, keine CSS-Farben — die Palette wird einmal je
 *  Themenwechsel aufgelöst (`pixel/palette.ts`). */
export interface Look {
  head: number;   // 0..2
  hair: number;   // 0..4
  torso: number;  // 0..2
  arms: number;   // 0..3
  legs: number;   // 0..3
  skin: string;
  hairCol: string;
  shirtCol: string;
  pantsCol: string;
}

/** Gangart, ebenfalls aus dem Seed. Damit laufen zwölf Leute erkennbar verschieden,
 *  ohne dass irgendwo gewürfelt wird. */
export interface Gait {
  /** Faktor auf `SPEED_PX_PER_S`, `1 ± PACE_SPREAD`. */
  speed: number;
  /** Auf-/Ab-Amplitude in **Pufferpixeln** (0..1). */
  bob: number;
  /** Startphase des 4-Bild-Laufzyklus, 0..1. */
  phase: number;

  // Ergänzt in Welle G. Grund: mit `speed/bob/phase` allein laufen zwölf Leute im selben
  // Takt und unterscheiden sich nur in der Geschwindigkeit — aus zwei Metern Abstand sieht
  // das aus wie eine einzige Animation. Die vier Felder unten sind die Silhouetten-Merkmale,
  // die man wirklich sieht. Alle stammen aus eigenen Salzen (Regel 3.2), erzeugt von
  // `pixel/palette.ts::gaitOf`, dem einzigen Hersteller eines `Gait`.

  /** Beinausschlag des Laufzyklus in **Pufferpixeln** (2..4). */
  stride: number;
  /** Vorlage des Oberkörpers in Laufrichtung, 0..1 (0 = kerzengerade). */
  lean: number;
  /** Armausschlag in **Pufferpixeln** (1..2). */
  swing: number;
  /** Startphase des Armzyklus, 0..1 — gegen `phase` versetzt, weil Arme und Beine
   *  gegenläufig schwingen; ohne Versatz sieht der Gang aus wie Marschieren. */
  armPhase: number;
}

// ── Raum (statische Geometrie, Szenen-Koordinaten) ──────────────────────────

/** Ein Arbeitsplatz. `sit` ist der Fußpunkt der sitzenden Figur, nicht die Stuhlmitte. */
export interface Seat {
  /** Mitte der Tischplatte. */
  desk: Pt;
  /** Fußpunkt der Figur, wenn sie hier sitzt. */
  sit: Pt;
  /** Blickt an diesem Platz nach links. */
  flip: boolean;
}

/** Wird einmal deterministisch gebaut (`room.ts`, Welle F) und danach nur gelesen. */
export interface Room {
  /** `seats[0..11]` = die zwölf Pod-Plätze (`deskIndex` 0..11), `seats[12]` = Chefplatz
   *  (`deskIndex === -1`). Genau `MAX_SEATS` Einträge; `deskIndex === -2` hat keinen Sitz. */
  seats: Seat[];
  /** Türschwelle — hier betreten und verlassen Figuren den Raum. */
  door: Pt;
  /** Mitte des runden Tisches. */
  table: Pt;
  /** Standplätze um den Tisch (Huddle), in fester Reihenfolge. */
  huddle: Pt[];
  /** Kaffeeecke (`IDLE_COFFEE_MS`). */
  coffee: Pt;
  /** Sammelpunkt außerhalb des Bildes für `deskIndex === -2`. */
  away: Pt;
}

// ── Roster & Zeitleiste ──────────────────────────────────────────────────────

/** Ein Lauf, wie ihn die Lese-API neben `events[]` mitliefert — direkt aus `runs`.
 *  Der Roster verdient seinen Platz, weil das Ereignisfenster vom **ältesten** Ende gekappt wird:
 *  ohne ihn gingen bei einer langen Session die `run_start`-Ereignisse verloren und der Raum
 *  bliebe leer. */
export interface RosterEntry {
  agent_id: string;
  run_id: number;
  agent: string;
  phase: string | null;
  status: RunStatus;
  issue_key: string | null;
  project_id: number | null;
  project_key: string | null;
  provider: string | null;
  model: string | null;
  parent_run_id: number | null;
  spawn_depth: number;
  started_at: string | null;
  ended_at: string | null;
  iterations: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  cost_priced: boolean | null;
}

export type Roster = RosterEntry[];

/** Ein Balken der Zeitleiste, `TIMELINE_BUCKET_MS` breit. `t` ist der Beginn des Fensters
 *  in `engine.t`-Zeit. Die vier Zähler sind die vier Farben — mehr Farben gibt es nicht. */
export interface Bucket {
  t: number;
  says: number;
  tools: number;
  thinks: number;
  errors: number;
}

/** Tag- oder Abendbüro. Kommt aus `<html data-theme>`, **nicht** aus der echten Uhrzeit —
 *  die bräche den Determinismus. */
export type Grade = "day" | "night";
