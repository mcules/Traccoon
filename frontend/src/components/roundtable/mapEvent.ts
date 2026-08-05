// Schicht 0 — die Naht: ein Backend-Ereignis wird zu Kommandos des Raums.
//
// `mapEvent` ist **rein**: kein DOM, keine Uhr, kein Zufall, kein Zustand außer dem übergebenen
// Roster. Genau das macht das Zurückspulen ohne Momentaufnahmen möglich — „neue Engine, Log von
// vorn abspielen" liefert dieselben Kommandos in derselben Reihenfolge und damit dasselbe Bild.
// Jede Ausnahme (ein Zähler, ein gemerktes „habe ich schon gesagt") bräche das sofort.
//
// Die Engine kennt `Ev` überhaupt nicht; sie sieht nur `Cmd[]`. Diese Datei ist die einzige
// Stelle, an der beide Vokabulare aufeinandertreffen.

import { toolAct } from "./toolAct.ts";
import type { Cmd, Ev, GateKind, Roster, RosterEntry, RunStatus } from "./types.ts";

// ── Kürzungen ────────────────────────────────────────────────────────────────
//
// Übernommen von Roundtable: eine Blase, die man nicht in einem Blick liest, ist keine Blase
// mehr, sondern ein Textfeld über einer 16×24-Figur.

/** Gedankenblase. */
const THINK_CHARS = 90;
/** Sprechblase: der erste Satz. */
const SAY_CHARS = 120;
/** Werkzeugziel (Pfad, URL, Rolle) unter dem Monitor. */
const TARGET_CHARS = 60;

/** Ab dieser Länge ist ein Modelltext kein Satz mehr, den jemand sagt, sondern ein Absatz, den
 *  jemand denkt. Die Grenze liegt bewusst über `SAY_CHARS`: ein Statussatz mit Nebensatz soll
 *  noch gesprochen werden, ein Bericht nicht. */
const THINK_FROM_CHARS = 180;

/** Alle Leerräume zu einem einzelnen Leerzeichen. Muss **vor** der Satzsuche laufen: sonst
 *  endete „…fertig.\nAls Nächstes…" nicht am Zeilenumbruch, obwohl dort offensichtlich ein
 *  Satz zu Ende ist. */
function squash(text: string): string {
  return (text || "").replace(/\s+/g, " ").trim();
}

/** Kürzt auf `max` Zeichen und schneidet dabei kein Wort mittendurch, wenn sich das vermeiden
 *  lässt. Das Auslassungszeichen zählt mit — `clip(s, 90)` ergibt nie mehr als 90 Zeichen. */
function clip(text: string, max: number): string {
  const t = squash(text);
  if (t.length <= max) return t;
  const hard = t.slice(0, max - 1);
  const space = hard.lastIndexOf(" ");
  return (space > max * 0.6 ? hard.slice(0, space) : hard) + "…";
}

/** Der erste Satz, höchstens `max` Zeichen lang.
 *
 *  Zwei Bedingungen, jede gegen einen konkreten Unfall:
 *
 *  1. Ein Satzende ist `[.!?]` **gefolgt von Leerraum oder Textende**. Ohne das würde aus
 *     „Latenz stieg um 1.4x auf 320 ms" das Fragment „Latenz stieg um 1.".
 *  2. Ein Punkt **hinter einer Ziffer** endet keinen Satz. Punkt 1 allein rettet die
 *     nummerierte Liste nämlich nicht: in „1. Datei lesen" steht hinter dem Punkt sehr wohl
 *     ein Leerzeichen, und die Blase sagte dann wörtlich „1.". Aufzählungsmarken und
 *     Versionsnummern sind derselbe Fall. `!`/`?` betrifft das nicht.
 *
 *  Der Preis ist ein Satz wie „Es waren 12." — der gilt nicht als beendet und wird stattdessen
 *  hart auf `max` gekürzt. Das ist die harmlosere Hälfte des Tauschs. */
function firstSentence(text: string, max: number): string {
  const t = squash(text);
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (c !== "." && c !== "!" && c !== "?") continue;
    if (c === "." && i > 0 && t[i - 1] >= "0" && t[i - 1] <= "9") continue;
    const next = t[i + 1];
    if (next === undefined || next === " ") return clip(t.slice(0, i + 1), max);
  }
  return clip(t, max);
}

// ── Kleine Nachschlagehilfen ─────────────────────────────────────────────────

/** Aktor-Id eines Laufs. Muss zu `services/roundtable.py::_event` passen (`f"run:{run_id}"`) —
 *  sonst zeigt der Raum zwei Figuren für denselben Lauf. */
function actorId(runId: number): string {
  return `run:${runId}`;
}

/** Linearer Scan statt einer Map: `mapEvent` ist rein und darf sich nichts merken, und ein
 *  Roster hat Dutzende Einträge, keine Tausende. Eine Map je Aufruf zu bauen wäre teurer als
 *  der Scan selbst. */
function rosterOf(roster: Roster, id: string): RosterEntry | undefined {
  for (const entry of roster) if (entry.agent_id === id) return entry;
  return undefined;
}

/** Der Umschlag jedes Ereignisses trägt `run_id`; daraus kommt die Figur.
 *
 *  Dieses `ensureActor` steht **vor** jedem Kommando, das eine Figur braucht. Grund ist die
 *  Kappung: das Ereignisfenster wird vom ältesten Ende beschnitten, ein `run_start` kann also
 *  fehlen. Ohne diesen Vorlauf handelten die ersten Kommandos einer langen Sitzung von einer
 *  Figur, die es nie gab. `ensureActor` ist laut Vertrag idempotent — mehrfach zu senden ist
 *  ausdrücklich erlaubt und hier die einzige zustandsfreie Lösung.
 *
 *  Ohne Roster-Eintrag bleiben die Stammdaten leer: eine namenlose Figur ist ehrlicher als eine
 *  erfundene Rolle. */
function ensure(ev: Ev, roster: Roster): Cmd {
  const id = ev.agent_id;
  const row = rosterOf(roster, id);
  const cmd: Cmd = {
    k: "ensureActor", id,
    role: row ? row.agent : "",
    issue: row ? row.issue_key : null,
    phase: row ? row.phase : null,
    model: row ? row.model : null,
  };
  if (row && row.parent_run_id !== null) cmd.parent = actorId(row.parent_run_id);
  return cmd;
}

// ── Werkzeug-Ergebnis ────────────────────────────────────────────────────────

/** Was die Laufzeit wirklich als Fehler zurückgibt — dieselbe Liste wie
 *  `services/roundtable.py::ERROR_PREFIXES`. */
const ERROR_PREFIXES = ["FEHLER:", "FEHLER ", "TOOL-FEHLER:", "FS-FEHLER:", "CHECK-FEHLER:",
                        "❌", "⛔"];

/** Das Ergebnis eines Werkzeugs, **dreiwertig**.
 *
 *  `null` heißt *unbekannt*, nicht *erfolgreich*. Bei Altdaten hat niemand gemessen, ob der
 *  Aufruf durchlief; ein grünes Häkchen darauf wäre eine Behauptung über Daten, die es nicht
 *  gibt. Wer hier `ok ?? true` schreibt, malt den halben Raum grün.
 *
 *  Der Rückfall auf Textschnüffelei steht bewusst **ganz am Ende** und nur für den Fall
 *  `ok === null`: heute erledigt das schon das Backend (`tool_ok`) für beide Pfade, aber ein
 *  Ereignisstrom aus einer älteren Version kennt diese Erkennung nicht — und ein belegter
 *  Fehler ist mehr wert als ein „unbekannt". Erfunden wird dabei nichts: es wird nur `null`
 *  zu `false` verschärft, nie zu `true`. */
function resultOk(ok: boolean | null, error: string | null, preview: string | null): boolean | null {
  if (ok !== null) return ok;
  const text = ((error || "") + (preview || "")).replace(/^\s+/, "");
  for (const p of ERROR_PREFIXES) if (text.startsWith(p)) return false;
  return null;
}

// ── Gates ────────────────────────────────────────────────────────────────────

/** `Run.blocker_kind` → die Art des Gates.
 *
 *  Die Werte stammen aus `worker/runtime.py` (`ask_human`, `permission`, `assistant_perm`) und
 *  `worker/__main__.py` (`review`, `question` bei einem geblockten Unterlauf). Unbekanntes wird
 *  zur Rückfrage: dass jemand wartet, ist belegt — nur worauf, ist es dann nicht. */
const GATE_OF: Record<string, GateKind> = {
  ask_human: "question",
  question: "question",
  review: "question",
  permission: "permission",
  assistant_perm: "permission",
};

const RUN_STATUS: readonly string[] = [
  "running", "success", "failed", "blocked", "planned", "loop_exhausted",
];

/** Das Backend baut `status` aus einem freien Mapping (`_run_end_fields`) und kann dabei einen
 *  leeren String liefern. Der Typ behauptet `RunStatus`; geprüft wird trotzdem, denn ein
 *  `status`-Kommando mit `""` färbte Dock und Blasenrand nach „unbekannt statt gar nicht". */
function isRunStatus(s: string): s is RunStatus {
  return RUN_STATUS.indexOf(s) >= 0;
}

// ── Ansagen von außen ────────────────────────────────────────────────────────

/** Welche `user_message`-Quellen im Raum wirklich gesagt werden.
 *
 *  Bewusst eine Erlaubnisliste, keine Sperrliste: `source` ist ein freies Feld (`step.target`,
 *  Rückfall `"system"`), und aus jeder System-, Hook- oder Wiedervorlagen-Nachricht ein
 *  Sprechen zu machen, füllte den Raum mit Blasen, die niemand geschrieben hat. Was hier fehlt,
 *  ist still — und eine stille Zeile im Textstrom ist der kleinere Schaden. */
const SAY_SOURCES: readonly string[] = ["ticket", "user", "human", "chat", "mail", "pm"];

// ── Die Übersetzung ──────────────────────────────────────────────────────────

/** Ein Ereignis → seine Kommandos. Leeres Feld heißt: im Raum passiert dazu nichts. */
export function mapEvent(ev: Ev, roster: Roster): Cmd[] {
  switch (ev.kind) {
    // Nur die Kopfzeile der Sitzung (Titel, Herkunft). Sie gehört in die Leiste über der
    // Bühne, nicht auf den Boden — Schicht 2 liest sie direkt aus dem Ereignis.
    case "session_seen":
      return [];

    case "run_start": {
      const cmds: Cmd[] = [{
        k: "ensureActor", id: ev.agent_id, role: ev.agent, issue: ev.issue_key,
        phase: ev.phase, model: ev.model,
        ...(ev.parent_run_id !== null ? { parent: actorId(ev.parent_run_id) } : {}),
      }];
      // **Der** Spawn-Moment. `delegate` taugt dafür nicht: `worker/runtime.py` wartet den
      // Unterlauf inline ab, die `delegate`-Zeile entsteht also erst bei dessen ENDE — der
      // Unteragent bekäme seine Linie rückwirkend, nachdem er längst fertig ist.
      if (ev.parent_run_id !== null) {
        cmds.push({ k: "spawn", id: ev.agent_id, parent: actorId(ev.parent_run_id),
                    role: ev.agent });
      }
      return cmds;
    }

    case "user_message": {
      const text = squash(ev.text);
      if (!text) return [];
      if (SAY_SOURCES.indexOf((ev.source || "").toLowerCase()) < 0) return [];
      // Der Agent sagt seinen Auftrag laut — der Raum hat keinen Erzähler, und dass jemand
      // weiß, woran er sitzt, ist die Information, die der Zuschauer sucht.
      return [ensure(ev, roster), { k: "say", id: ev.agent_id, text: firstSentence(text, SAY_CHARS) }];
    }

    case "agent_text": {
      const text = squash(ev.text);
      if (!text) return [];
      // `"(Tool-Call)"` ist der wörtliche Platzhalter der Laufzeit für einen Zug, in dem das
      // Modell nur Werkzeuge gerufen hat (`resp.text or "(Tool-Call)"`). Ungefiltert sagte
      // jeder Agent im Büro pausenlos „(Tool-Call)".
      //
      // Heute unterscheidet schon das Backend die beiden Fälle: der Worker schreibt für einen
      // reinen Werkzeugzug `kind="usage"` statt `kind="agent_text"`, und `step_events` filtert
      // den Platzhalter zusätzlich in beiden Pfaden. Die Prüfung hier ist der Rückfall für
      // Altdaten und für Ströme aus einer älteren Backend-Version — sie kostet einen Vergleich.
      if (text === "(Tool-Call)") return [];
      const head = ensure(ev, roster);
      return text.length >= THINK_FROM_CHARS
        ? [head, { k: "think", id: ev.agent_id, text: clip(text, THINK_CHARS) }]
        : [head, { k: "say", id: ev.agent_id, text: firstSentence(text, SAY_CHARS) }];
    }

    // Reserviert: kein Provider-Adapter in Traccoon liefert Denkblöcke, das Backend emittiert
    // die Art nie. Der Zweig steht hier trotzdem ausgefüllt, damit ein späterer Adapter nicht
    // erst diese Datei anfassen muss — und weil ein stiller `default`-Fall der Ort ist, an dem
    // ein neues Ereignis unbemerkt verschwindet.
    case "thinking": {
      const text = squash(ev.text);
      if (!text) return [];
      return [ensure(ev, roster), { k: "think", id: ev.agent_id, text: clip(text, THINK_CHARS) }];
    }

    // Tokens eines Modellzugs. `ActorState` führt keinen Tokenstand und `status` trägt nur den
    // `RunStatus` — es gibt schlicht kein Kommando, das die Zahl aufnehmen könnte. Sie ist
    // trotzdem nicht verloren: Zeitleiste, Dock und Inspektor lesen sie in Schicht 2 direkt aus
    // dem Ereignisstrom. Hier ein `status`-Kommando zu erfinden, das nichts überträgt, wäre
    // eine Zeile Rauschen je Modellzug.
    case "usage":
      return [];

    case "tool_start": {
      const target = clip(ev.target || "", TARGET_CHARS);
      return [ensure(ev, roster), {
        k: "tool", id: ev.agent_id, act: toolAct(ev.tool), tool: ev.tool,
        ...(target ? { target } : {}),
      }];
    }

    case "tool_result":
      return [ensure(ev, roster), {
        k: "toolEnd", id: ev.agent_id,
        ok: resultOk(ev.ok, ev.error, ev.result_preview),
      }];

    case "file_edit": {
      const path = squash(ev.path);
      if (!path) return [];
      return [ensure(ev, roster), { k: "edit", id: ev.agent_id, path }];
    }

    // Absichtlich ohne Kommando. Die Spawn-Linie kommt aus dem `run_start` des KINDES (siehe
    // oben); hier zusätzlich eine zu ziehen, ergäbe zwei Linien für einen Vorgang — eine davon
    // zu einer Figur, die es in diesem Moment noch gar nicht gibt. Der Anweisungstext des
    // Spawns (`prompt`) gehört in den Inspektor, nicht auf die Bühne.
    case "agent_spawn":
      return [];

    case "run_end": {
      const id = ev.agent_id;
      const cmds: Cmd[] = [ensure(ev, roster)];
      if (isRunStatus(ev.status)) cmds.push({ k: "status", id, status: ev.status });

      // Übergabe an den Elternlauf — der Kern der Roundtable-Choreografie: die Figur läuft
      // hinüber und reicht ihr Ergebnis weiter. Nur mit Roster, denn das `run_end` selbst kennt
      // seinen Elternteil nicht.
      //
      // Bei `blocked` **nicht**: ein geblockter Unterlauf hat nichts abzuliefern. Die Figur
      // liefe hinüber, überreichte nichts und höbe dann die Hand — die Übergabe wäre eine
      // Geste über einem leeren Ergebnis.
      const row = rosterOf(roster, id);
      if (row && row.parent_run_id !== null && ev.status !== "blocked") {
        const text = firstSentence(ev.summary || ev.error || "", SAY_CHARS);
        cmds.push({ k: "deliver", id, to: actorId(row.parent_run_id),
                    ...(text ? { text } : {}) });
      }

      // Hand heben statt verschwinden. Ein Lauf, der auf einen Menschen wartet, ist der
      // häufigste Grund für einen stillen Raum — und ein stiller Raum ohne sichtbaren Grund
      // liest sich wie ein Absturz. `planned` gehört dazu, obwohl es kein `blocker_kind` trägt:
      // ein eingereichter Plan wartet genauso auf eine Freigabe.
      const gate: GateKind | undefined =
        ev.status === "planned" ? "plan"
          : (ev.blocker_kind ? (GATE_OF[ev.blocker_kind] || "question") : undefined);
      if (gate) {
        cmds.push({ k: "gate", id, kind: gate,
                    text: firstSentence(ev.summary || ev.error || "", SAY_CHARS) });
      }

      // `done` schickt die Figur nach `DONE_LINGER_MS` durch die Tür. Bei `blocked` darf das
      // nicht passieren: dort steht sie am Gate und wartet auf eine Antwort — genau das soll
      // man sehen. `planned` dagegen ist wirklich fertig (der Lauf hat abgeliefert), die Figur
      // hebt beim Gehen nur noch den Plan hoch.
      if (ev.status !== "blocked") {
        const ok = ev.status === "success" || ev.status === "planned";
        const text = firstSentence(ev.summary || ev.error || "", SAY_CHARS);
        cmds.push({ k: "done", id, ok, ...(text ? { text } : {}) });
      }
      return cmds;
    }

    // Abbruch, Kappung, Kompaktierung: in v1 ohne Kommando. Diese Meldungen betreffen den
    // Ablauf, nicht den Raum — sie gehören in den Textstrom und in die Zeitleiste. Eine Figur
    // dafür sprechen zu lassen hieße, dem Agenten Worte in den Mund zu legen, die das System
    // gesagt hat.
    case "system":
      return [];
  }
}
