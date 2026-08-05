// Schicht 0 — alle Zahlen des „Büros" an einem Ort.
//
// Wer eine Zahl im Code findet, die hier fehlt, hat einen Fehler gefunden: gestreute Konstanten
// sind der schnellste Weg, Bühne und Zeitleiste auseinanderlaufen zu lassen.
//
// Alle `*_MS` sind Millisekunden in **Simulationszeit** (`engine.t`), nicht Wanduhr.
// Kein `enum` (PIXEL-CONTRACT.md Regel 5) — flache `const` und `as const`-Objekte.

// ── Auflösung ────────────────────────────────────────────────────────────────

/** Der Bildpuffer. Fest. Alle Sprites, Blasen und Schriften sind in **diesen** Pixeln gemessen. */
export const PIX = { w: 480, h: 270 } as const;

/** Der Raum, in dem die Simulation rechnet. Positionen leben hier. */
export const SCENE = { w: 1600, h: 900 } as const;

/** `SCENE → PIX`, **nur für Positionen**. Sprites werden nicht mitskaliert — eine Figur ist
 *  16×24 Pufferpixel, nicht 16×24 Szenenpixel. Regel 1 des Pixel-Vertrags, und die Regel,
 *  deren Verletzung das ganze Kunstbudget um Faktor 3 verrechnet. */
export const POS_SCALE = 0.3;

// ── Bewegung ─────────────────────────────────────────────────────────────────

/** Grundtempo in **Szenen**pixeln je Sekunde (≈45 Pufferpixel/s). */
export const SPEED_PX_PER_S = 150;
/** Streuung des Tempos je Figur: `1 ± 0.17`, aus dem Seed. */
export const PACE_SPREAD = 0.17;
/** Ankunft am Ziel: so lange wird noch getrippelt, bevor die Pose wechselt. */
export const SETTLE_MS = 600;
/** Mehr als zwei vorgemerkte Wege werden verworfen — sonst rennt eine Figur nach einem
 *  Ereignisschwall minutenlang Aufträge ab, die längst überholt sind. */
export const MAX_QUEUED_TRIPS = 2;
/** Betreten mehrere Läufe gleichzeitig den Raum, kommen sie gestaffelt durch die Tür. */
export const ARRIVE_STAGGER_MS = 2600;

// ── Blasen & Aufmerksamkeit ──────────────────────────────────────────────────

/** Standzeit einer Sprechblase. */
export const BUBBLE_MS = 5000;
/** Schreibmaschineneffekt: Zeichen je Sekunde. */
export const TYPE_CPS = 40;
/** Wie lange eine Figur nach dem Sprechen noch in Sprechpose bleibt. */
export const SPEAK_HOLD_MS = 2500;
/** Sichtbarkeit einer Spawn-/Übergabelinie. */
export const LINK_MS = 2200;
/** Wie lange ein Zuhörer den Kopf gedreht hält. */
export const HEARD_MS = 4000;

// ── Huddle ───────────────────────────────────────────────────────────────────

/** Ab drei gleichzeitig aktiven Läufen wird es ein Huddle am runden Tisch. */
export const HUDDLE_MIN = 3;
/** Fenster, in dem diese drei zusammenkommen müssen. */
export const HUDDLE_WINDOW_MS = 7500;
/** Mindestdauer, bevor sich das Huddle wieder auflöst. */
export const HUDDLE_HOLD_MS = 4000;

// ── Leerlauf & Abgang ────────────────────────────────────────────────────────

/** Nach anderthalb Minuten ohne Ereignis geht die Figur Kaffee holen — das einzige
 *  Lebenszeichen in einer langen Werkzeugkette. */
export const IDLE_COFFEE_MS = 90_000;
export const COFFEE_HOLD_MS = 4000;
/** Nach `done` bleibt die Figur noch stehen, bevor sie durch die Tür geht … */
export const DONE_LINGER_MS = 1800;
/** … um bis zu so viel je Figur gestreut (aus dem Seed), damit nicht alle gleichzeitig gehen. */
export const DONE_LINGER_SPREAD_MS = 4200;

// ── Werkzeuge & Gates ────────────────────────────────────────────────────────

/** Ersatzdauer eines Werkzeugschritts.
 *
 *  Beim Altdaten-Pfad (`kind=''`-Zeilen aus der Zeit vor der
 *  Worker-Instrumentierung) kennt ein Werkzeugaufruf nur *einen* Zeitpunkt: `tool_start` und
 *  `tool_result` werden aus derselben Zeile synthetisiert, `duration_ms` ist `null`. Ohne eine
 *  Ersatzdauer blitzte die Werkzeugpose für 0 ms auf und der Raum sähe aus, als täte niemand
 *  etwas. Liegt ein echtes `duration_ms` vor, gewinnt das. */
export const TOOL_BUSY_MS = 1400;

/** Pulsdauer des „wartet auf einen Menschen"-Signals über der Figur.
 *
 *  Ein Gate (`ask_human`, Berechtigungsanfrage, Planfreigabe) ist der häufigste Grund
 *  für einen stillen Raum, und ein
 *  stiller Raum ohne sichtbaren Grund liest sich wie ein Absturz. */
export const GATE_PULSE_MS = 1200;

// ── Zeit, Replay, Kappung ────────────────────────────────────────────────────

/** Obergrenze für die Pause zwischen zwei Ereignissen.
 *
 *  Bewusst knapp. Ein `check`-Build erzeugt regelmäßig minutenlange Stille an einer einzigen
 *  `run`-Werkzeugzeile; großzügiger gewählt säße man Minuten vor einem toten Bild. Ein gekürzter Zeitsprung ist das kleinere Übel.
 *
 *  Wird **beidseitig** angewandt: `dt = min(MAX_GAP_MS, max(0, ts - prev))`. Das `max(0, …)`
 *  ist nicht theoretisch — unter `WORKER_CONCURRENCY > 1` kann `ts` gegenüber `seq` rückwärts
 *  laufen, und ein negatives `dt` würde die Engine rückwärts drehen. */
export const MAX_GAP_MS = 20_000;

/** Mehr Log-Zeilen spielt `seek` nicht ab; darüber wird vom **ältesten** Ende gekappt. */
export const REPLAY_CAP = 20_000;
/** Schrittweite beim Zurückspulen: `min(REPLAY_STEP_MS, timeToNextCmd)`. */
export const REPLAY_STEP_MS = 250;
/** Schrittweite im Livebetrieb. */
export const LIVE_STEP_MS = 100;
/** Obergrenze für ein einzelnes `tick(dt)` — schützt gegen einen Tab, der im Hintergrund lag. */
export const MAX_FRAME_MS = 100;

// ── Kapazitäten ──────────────────────────────────────────────────────────────

/** Mehr Figuren zeigt der Raum nicht gleichzeitig; die ältesten fertigen gehen zuerst. */
export const MAX_ACTORS = 24;
/** 12 Pod-Plätze + Chefplatz. Sitzwahl ist `hash32(runId) % 12`, deterministisch, ohne Warteschlange. */
export const MAX_SEATS = 13;

// ── Zeitleiste ───────────────────────────────────────────────────────────────

/** Ein Balken je Sekunde. */
export const TIMELINE_BUCKET_MS = 1000;
/** Mehr Balken werden nicht vorgehalten. */
export const TIMELINE_CAP = 1200;
/** Auf so viele Spalten wird für die Anzeige zusammengefasst. */
export const TIMELINE_COLUMNS = 220;
