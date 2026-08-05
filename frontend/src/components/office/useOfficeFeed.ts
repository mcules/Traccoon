// Schicht 2 — das Herzstück: aus Socket + Schnappschuss wird ein lückenloses, doppelfreies Log.
//
// ══ Der Ablauf, und er ist determinismuskritisch ═════════════════════════════════════════════
//
//   1. WebSocket öffnen und `subscribe` senden.
//   2. Eingehende Ereignisse **puffern**, solange der Backfill läuft.
//   3. Schnappschuss über `officeApi.events(…)` holen — seitenweise, bis eine Seite kürzer
//      ist als `limit`.
//   4. Gepufferte Ereignisse nachziehen. Doppler verwirft `Recorder.push` anhand der `seq`
//      von selbst; hier wird nichts verglichen.
//   5. Live gehen.
//
// Die Reihenfolge ist kein Stil, sondern die einzige, die kein Ereignis verliert: Erst der
// Socket, dann der Schnappschuss. Andersherum fiele jede Zeile, die zwischen dem Lesen der
// Datenbank und dem Abonnieren geschrieben wird, in ein Loch — und ein fehlendes `run_end`
// heißt, dass eine Figur den Raum nie verlässt.
//
// ══ Warum **nie** inkrementell mit `after_seq` gepollt wird ═════════════════════════════════
//
// `seq` stammt aus `run_steps.id`, also aus einer `SERIAL`-Spalte — und die wird **vor** dem
// Commit vergeben. Zwei parallele Worker (`WORKER_CONCURRENCY > 1`) können ihre Zeilen deshalb
// in umgekehrter Reihenfolge sichtbar machen: erst 1005, dann 1003. Ein Poller, der sich seinen
// Höchststand merkt, überspränge 1003 für immer, ohne es je zu bemerken.
//
// `after_seq` ist ausschließlich (a) Seitenblättern innerhalb **eines** Schnappschusses und
// (b) Lückenfüllung unmittelbar nach einer Wiederverbindung — in beiden Fällen deckt der
// Puffer bzw. der nachfolgende volle Schnappschuss, was das Blättern verpasst haben könnte.
// Erkennt der Client ein Loch (jede Wiederverbindung gilt als Loch, weil er nicht wissen
// kann, was während der Trennung geschah), holt er einen **vollen neuen** Schnappschuss.
// Das ist billig: `Recorder.push` wirft alles Bekannte weg.
//
// ══ Zustandsdisziplin, die die Bildrate rettet ══════════════════════════════════════════════
//
// · Der `Recorder` lebt in einem **Ref**. Die Bühne rendert nie pro Bild neu.
// · Das einzige Rendersignal ist `revision` — und der Bump ist auf `REVISION_THROTTLE_MS`
//   gedrosselt. Ein Schub von 60 Schritten löst einen Renderdurchlauf aus, nicht sechzig.
// · Dedupliziert wird **ausschließlich** in `Recorder.push`. Genau deshalb sind
//   Backfill-Wettlauf, Wiederverbindungs-Doppler und manuelles Neuladen mit derselben Zeile
//   erledigt — es gibt keinen zweiten Ort, an dem eine Regel driften könnte.
// · Der Schnappschuss ist `staleTime: Infinity`: er ist ein Zeitpunkt, kein Wert, der veralten
//   könnte. Fortgeschrieben wird er über den Socket, nicht über ein Neuladen.
// · Bei `ApiError` mit Status 401 leitet `src/api.ts` bereits hart weiter — hier steht bewusst
//   keine zweite Behandlung.
//
// ══ Zwei Betriebsarten, ein Weg ═════════════════════════════════════════════════════════════
//
// Mit `sid` ist der Raum **eine** Sitzung; Ereignisse fremder Sitzungen werden verworfen, weil
// der Server nach Projekt filtert und nicht nach Raum.
//
// Ohne `sid` und mit `opts.alleSitzungen` ist der Raum das **Fenster**: der Schnappschuss kommt
// aus `GET /office/events`, und live wird jedes Ereignis angenommen, das der Socket liefert —
// der filtert bereits serverseitig auf das, was dieser Nutzer sehen darf (`api/office_ws.py`).
// Das geht nur, weil `seq` aus `run_steps.id` stammt, einer SERIAL-Spalte: über Läufe **und**
// Projekte hinweg monoton, also ergeben zwanzig Sitzungen EINE Folge und `Recorder.push`
// entdoppelt sie mit derselben Regel wie eine einzelne. Der Sitzplatz (`hash32(run_id) % 12`)
// ist ohnehin sitzungsunabhängig.
//
// Beide Arten nehmen denselben Weg durch diese Datei — es gibt einen Recorder, eine
// Blätterschleife und einen Übergang von Puffer zu Live, nicht zwei.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getToken } from "../../api";
import {
  EVENT_PAGE_LIMIT, EVENT_VERSION, officeApi, officeWsUrl, sidKey, subscribeMessage,
  type CostRollup, type EventPage, type Scope, type Sid, type WsIn,
} from "./api.ts";
import { REPLAY_CAP } from "./const.ts";
import type { Ev, EvRunEnd, EvRunStart, LogEntry, Roster, RosterEntry } from "./types.ts";

// Welle E liefert `./recorder.ts` (Schicht 0). Solange die Datei fehlt, hält das `@ts-ignore`
// den Bau grün; die Zeile darunter ist bereits die endgültige. Beim Landen von Welle E: den
// Kommentar löschen und prüfen, dass `RecorderApi` unten zur Klasse passt — dann ist auch
// `RecorderApi` überflüssig und wird durch `import type { Recorder }` ersetzt.
// @ts-ignore -- Modul folgt mit Welle E (office/recorder.ts)
import { Recorder } from "./recorder.ts";

// ── Die Oberfläche, gegen die hier gebaut wird ──────────────────────────────────────────────

/** Was der Feed vom `Recorder` braucht (Plan-Oberfläche der Welle E).
 *  **Nicht** hier implementieren — der Recorder ist Schicht 0 und gehört zur Engine. */
export interface RecorderApi {
  /** Nimmt ein Ereignis auf. `false` = schon bekannt (`seq` bereits gesehen) und verworfen.
   *  Der EINE Ort, an dem dedupliziert wird. */
  push(ev: Ev): boolean;
  /** Zählt bei jeder tatsächlichen Aufnahme hoch. Der Feed drosselt daraus sein `revision`. */
  readonly revision: number;
  /** Das Log in Ankunftsreihenfolge — als **Kopie**. `readonly`, weil die Kopie zwar dem
   *  Aufrufer gehört, aber weitergereicht wird (`new Replay(log)`): eine Liste, die unterwegs
   *  jemand verändern darf, wäre genau der Aliasing-Fehler, den die Kopie verhindern soll. */
  entries(): readonly LogEntry[];
  /** Zeit- und `seq`-Grenzen des Logs, `null` solange es leer ist. */
  bounds(): { t0: number; t1: number; seq0: number; seq1: number } | null;
  /** Alles vergessen (Sitzungswechsel). */
  reset(): void;
}

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** Höchstens fünf Renderdurchläufe je Sekunde aus dem Ereignisstrom. Bewusst nur hinten
 *  ausgelöst (trailing): das erste Ereignis eines Schubs kostet 200 ms Verzögerung, dafür
 *  kostet der ganze Schub genau einen Durchlauf. */
const REVISION_THROTTLE_MS = 200;

/** Wartezeiten der Wiederverbindung. Der letzte Wert gilt danach dauerhaft — ein Backend, das
 *  gerade neu startet, soll wiederkommen, ohne dass hundert Tabs es dabei überrennen. */
const RECONNECT_MS = [1000, 2000, 5000, 10_000, 20_000];

/** Gegen Zwischenboxen, die stille Verbindungen kappen (der Server antwortet mit `pong`). */
const PING_MS = 45_000;

/** Kommt der Socket nicht zustande, wird der Schnappschuss trotzdem geholt. Eine Ansicht ohne
 *  Live-Strom ist brauchbar; eine leere Ansicht ist es nicht. */
const SOCKET_GRACE_MS = 4000;

/** Deckel des Puffers. Er greift nur in einem Fehlerfall — wenn der Schnappschuss gar nicht
 *  zurückkommt (Endpunkt tot) und der Socket trotzdem liefert, würde der Puffer sonst
 *  unbegrenzt wachsen. Verworfen wird vom **ältesten** Ende, genau wie beim Ereignisfenster
 *  des Backends; und sobald der Schnappschuss wieder trägt, ist die Lücke ohnehin gefüllt. */
const BUFFER_CAP = REPLAY_CAP;

/** Mehr Seiten holt der Schnappschuss nicht. `REPLAY_CAP` ist ohnehin die Grenze dessen, was
 *  die Engine abspielt — ohne Deckel könnte ein Backend, das `limit` ignoriert, den Browser
 *  in eine Endlosschleife schicken. */
const MAX_PAGES = Math.ceil(REPLAY_CAP / EVENT_PAGE_LIMIT) + 1;

// ── Rückgabe ────────────────────────────────────────────────────────────────────────────────

/** Summen über die ganze Sitzung. Tokens kommen aus dem Roster (autoritativ je Lauf), Kosten
 *  aus dem Kosten-Aufruf — der kennt als Einziger den Unterschied zwischen „bepreist und
 *  gratis" und „nicht im Katalog". */
export interface FeedTotals {
  runs: number;
  running: number;
  in_tokens: number;
  out_tokens: number;
  cache_read_tokens: number;
  /** Abgerechnet, aus `CostEntry`. */
  cost_usd_billed: number;
  /** Geschätzt gegen den aktuellen Katalog — richtig auch bei einem Fallback-Lauf. */
  cost_usd_estimated: number;
  /** Mindestens ein Modellzug hat keinen Katalogeintrag → die Anzeige stellt ein „≥" davor. */
  cost_partial: boolean;
}

/** Vorgabefenster des Modus „alle Sitzungen", in Stunden.
 *
 *  Muss zu `api/office.py::EVENTS_SINCE_HOURS_DEFAULT` passen — die Oberfläche schreibt die
 *  Zahl in die Kopfzeile („der letzten 12 Stunden"), und eine Überschrift, die ein anderes
 *  Fenster nennt als das gemessene, wäre schlimmer als gar keine. Warum zwölf: am Bestand
 *  gemessen liegen darin 14 Läufe, bei 24 h sind es 23 und `office/const.ts` lässt
 *  `MAX_ACTORS = 24` Figuren gleichzeitig zu — der Raum stünde dauerhaft an der Kante und
 *  verdrängte bei jedem neuen Lauf eine Figur. */
export const ALLE_FENSTER_H = 12;

/** Zusatzschalter des Feeds. */
export interface OfficeFeedOpts {
  /** Ohne `sid` **alle** Sitzungen in einen Raum mischen, statt leer zu bleiben. */
  alleSitzungen?: boolean;
  /** Fenster dieses Modus in Stunden. */
  sinceHours?: number;
}

export interface OfficeFeed {
  /** Lebt in einem Ref und wechselt nur bei einem Sitzungswechsel die Identität. */
  recorder: RecorderApi;
  /** Das einzige Rendersignal. Steigt gedrosselt. */
  revision: number;
  roster: Roster;
  totals: FeedTotals;
  /** Socket offen **und** Backfill durch — Ereignisse kommen in Echtzeit an. */
  live: boolean;
  error?: string;
}

const EMPTY_TOTALS: FeedTotals = {
  runs: 0, running: 0, in_tokens: 0, out_tokens: 0, cache_read_tokens: 0,
  cost_usd_billed: 0, cost_usd_estimated: 0, cost_partial: false,
};

// ── Schnappschuss ───────────────────────────────────────────────────────────────────────────

interface Snapshot {
  events: Ev[];
  agents: Roster;
  page: EventPage | null;
}

/** Seitenweise, bis eine Seite kürzer ist als `limit`.
 *
 *  Das Blättern benutzt `after_seq` — und das ist der eine erlaubte Gebrauch: es läuft
 *  innerhalb **eines** Vorgangs vorwärts, und was dabei nachträglich mit kleinerer `seq`
 *  sichtbar wird, liegt bereits im Puffer des Sockets (der lief vorher an). Der Recorder
 *  fügt es an seinem Platz ein und wirft den Doppler weg.
 *
 *  `lade` ist der einzige Unterschied zwischen „eine Sitzung" und „alle Sitzungen": beide
 *  Endpunkte liefern dieselbe Form, also blättert **eine** Schleife durch beide. Zwei
 *  Schleifen wären zwei Gelegenheiten, die Reihenfolge unterschiedlich zu verlieren. */
async function fetchSnapshot(lade: (afterSeq?: number) => Promise<EventPage>): Promise<Snapshot> {
  const events: Ev[] = [];
  let agents: Roster = [];
  let page: EventPage | null = null;
  let afterSeq: number | undefined;

  for (let i = 0; i < MAX_PAGES; i++) {
    const p = await lade(afterSeq);
    const batch = p.events ?? [];
    // Der Roster steht nur auf der ersten Seite; spätere Seiten überschreiben ihn nicht mit
    // einem leeren Feld — sonst wäre der Raum nach dem Blättern besetzungslos.
    if (p.agents && p.agents.length) agents = p.agents;
    for (const ev of batch) events.push(ev);
    page = p;
    if (batch.length < EVENT_PAGE_LIMIT) break;
    const last = batch[batch.length - 1];
    const next = p.seq_to ?? last?.seq;
    if (next === undefined || next === null) break;
    afterSeq = next;
  }
  return { events, agents, page };
}

// ── Roster ──────────────────────────────────────────────────────────────────────────────────

/** Ein Lauf, der live den Raum betritt, steht noch in keinem Roster — der kam mit dem
 *  Schnappschuss. Bis zum nächsten Schnappschuss wird der Eintrag aus dem `run_start`-Ereignis
 *  gebaut; die Zahlen füllt das `run_end` nach. */
function rosterFromRunStart(ev: EvRunStart): RosterEntry {
  return {
    agent_id: ev.agent_id, run_id: ev.run_id, agent: ev.agent, phase: ev.phase,
    status: "running", issue_key: ev.issue_key, project_id: ev.project_id,
    project_key: null, provider: ev.provider, model: ev.model,
    parent_run_id: ev.parent_run_id, spawn_depth: ev.spawn_depth,
    started_at: ev.ts, ended_at: null, iterations: 0,
    in_tokens: 0, out_tokens: 0, cache_read_tokens: 0, cost_usd: 0, cost_priced: null,
  };
}

function rosterFromRunEnd(prev: RosterEntry | undefined, ev: EvRunEnd): RosterEntry {
  const base: RosterEntry = prev ?? {
    agent_id: ev.agent_id, run_id: ev.run_id, agent: "", phase: null, status: ev.status,
    issue_key: null, project_id: ev.project_id, project_key: null, provider: null, model: null,
    parent_run_id: null, spawn_depth: 0, started_at: null, ended_at: null, iterations: 0,
    in_tokens: 0, out_tokens: 0, cache_read_tokens: 0, cost_usd: 0, cost_priced: null,
  };
  return {
    ...base, status: ev.status, ended_at: ev.ts, iterations: ev.iterations,
    in_tokens: ev.in_tokens, out_tokens: ev.out_tokens, cache_read_tokens: ev.cache_read_tokens,
    cost_usd: ev.cost_usd, cost_priced: ev.cost_priced,
  };
}

// ── Der Feed ────────────────────────────────────────────────────────────────────────────────

export function useOfficeFeed(scope: Scope, sid?: Sid, opts?: OfficeFeedOpts): OfficeFeed {
  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const sinceHours = opts?.sinceHours ?? ALLE_FENSTER_H;
  /** „Alle Sitzungen": kein Raum gewählt **und** der Aufrufer will diesen Modus. Das
   *  zweite Stück ist kein Zierrat — ohne `sid` ist auch der Projekt-Reiter, solange seine
   *  Sitzungsliste noch unterwegs ist, und der soll dann nicht für einen Wimpernschlag das
   *  ganze Projekt laden, um es sofort wieder wegzuwerfen. */
  const alle = !sid && !!opts?.alleSitzungen;
  // Die Identität des Logs. Sie wechselt beim Raumwechsel **und** beim Wechsel des
  // Fensters — beides ist ein anderes Log, und der Recorder muss dazwischen leer sein.
  const key = sid ? sidKey(sid) : (alle ? `alle:${scopeKey}:${sinceHours}` : null);

  // ── Refs: alles, was pro Ereignis angefasst wird, aber kein Rendern auslösen darf ─────────
  const recorderRef = useRef<RecorderApi | null>(null);
  if (recorderRef.current === null) recorderRef.current = new Recorder() as RecorderApi;
  const recorder = recorderRef.current;

  /** Ereignisse, die während des Backfills eintreffen. Wird beim Nachziehen geleert. */
  const bufferRef = useRef<Ev[]>([]);
  /** `false`, solange der Schnappschuss läuft — dann wird gepuffert statt aufgenommen. */
  const liveRef = useRef(false);
  /** Aktuelle Sitzung, damit der Nachrichtenempfänger ohne Neuanmeldung mitzieht.
   *  Im Modus „alle Sitzungen" bleibt sie leer — dort trennt niemand. */
  const sidRef = useRef<string | null>(sid ? sidKey(sid) : null);
  sidRef.current = sid ? sidKey(sid) : null;
  /** Spiegel für `accept`: der Empfänger hängt am Socket-Effekt und darf nicht bei jedem
   *  Moduswechsel neu angemeldet werden. */
  const alleRef = useRef(alle);
  alleRef.current = alle;

  const wsRef = useRef<WebSocket | null>(null);
  const bumpTimerRef = useRef<number | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const closingRef = useRef(false);
  const rosterRef = useRef<Map<string, RosterEntry>>(new Map());

  // ── Zustand: sechs Werte, mehr sieht React vom Strom nicht ────────────────────────────────
  const [revision, setRevision] = useState(0);
  const [roster, setRoster] = useState<Roster>([]);
  const [live, setLive] = useState(false);
  const [wsError, setWsError] = useState<string | undefined>(undefined);
  /** > 0 heißt „Socket ist versorgt" und gibt den Schnappschuss frei; jede Erhöhung ist ein
   *  neuer, **voller** Schnappschuss (Erstverbindung, Wiederverbindung, Notfall ohne Socket). */
  const [generation, setGeneration] = useState(0);

  /** Gedrosseltes Rendersignal. Mehrfach je Tick aufrufen ist ausdrücklich erlaubt. */
  const scheduleBump = useCallback(() => {
    if (bumpTimerRef.current !== null) return;
    bumpTimerRef.current = window.setTimeout(() => {
      bumpTimerRef.current = null;
      setRevision((r) => r + 1);
    }, REVISION_THROTTLE_MS);
  }, []);

  /** Roster aus einem Grenz-Ereignis fortschreiben. Passiert ein paar Mal je Sitzung, nicht
   *  je Schritt — deshalb darf es hier echten Zustand anfassen. */
  const patchRoster = useCallback((ev: Ev) => {
    if (ev.kind !== "run_start" && ev.kind !== "run_end") return;
    const map = rosterRef.current;
    const prev = map.get(ev.agent_id);
    if (ev.kind === "run_start") {
      if (prev) return;                       // der Schnappschuss kennt ihn schon
      const neu = rosterFromRunStart(ev);
      // Das Ereignis trägt die Projekt-**Id**, nicht den Schlüssel — den kennt nur der
      // Schnappschuss. Steht schon jemand aus demselben Projekt im Raum, ist der Schlüssel
      // damit belegt und nicht geraten; sonst bleibt er leer, und die Figur sitzt bis zum
      // nächsten Schnappschuss unter „(ohne Projekt)". Das ist im Modus „alle Sitzungen"
      // sichtbar, weil die Reiter dort nach Projekt gruppieren.
      if (neu.project_id !== null) {
        for (const r of map.values()) {
          if (r.project_id === neu.project_id && r.project_key) {
            neu.project_key = r.project_key;
            break;
          }
        }
      }
      map.set(ev.agent_id, neu);
    } else {
      map.set(ev.agent_id, rosterFromRunEnd(prev, ev));
    }
    setRoster([...map.values()]);
  }, []);

  /** Ein Ereignis von der Leitung: prüfen, ob es hierher gehört, dann puffern oder aufnehmen. */
  const accept = useCallback((ev: Ev) => {
    // Vertragsversion: lieber nichts zeigen als etwas Falsches (siehe `EVENT_VERSION`).
    if (ev.v !== EVENT_VERSION) return;
    // Der Server filtert nach Projekt, nicht nach Raum — die Sitzung trennt der Client.
    // Im Modus „alle Sitzungen" gibt es nichts zu trennen: was der Socket liefert, ist
    // bereits genau das, was dieser Nutzer sehen darf (`api/office_ws.py::visible`), und
    // der Raum zeigt es zusammen. Es hier trotzdem wegzuwerfen war die eine Zeile, die
    // die Übersicht bisher unmöglich machte.
    if (!alleRef.current && (!sidRef.current || ev.sid !== sidRef.current)) return;
    if (!liveRef.current) {
      const buf = bufferRef.current;
      buf.push(ev);
      if (buf.length > BUFFER_CAP) buf.splice(0, buf.length - BUFFER_CAP);
      return;
    }
    if (recorderRef.current?.push(ev)) {
      patchRoster(ev);
      scheduleBump();
    }
  }, [patchRoster, scheduleBump]);

  // ── Sitzungswechsel: Recorder leeren, wieder in den Backfill ──────────────────────────────
  //
  // Steht vor dem `useQuery`, damit dieser Effekt im selben Durchlauf zuerst läuft: der
  // Schnappschuss darf nie in einen Recorder fallen, der noch die alte Sitzung hält.
  useEffect(() => {
    liveRef.current = false;
    bufferRef.current = [];
    recorderRef.current?.reset();
    rosterRef.current = new Map();
    setRoster([]);
    setLive(false);
    setRevision((r) => r + 1);
  }, [key]);

  // ── Der Socket ────────────────────────────────────────────────────────────────────────────
  //
  // Hängt nur am Geltungsbereich, nicht an der Sitzung: ein Wechsel des Raums innerhalb
  // desselben Projekts baut die Verbindung nicht neu auf (`sidRef` zieht mit).
  useEffect(() => {
    closingRef.current = false;
    attemptRef.current = 0;
    let graceTimer: number | null = null;

    /** Gibt den Schnappschuss frei. Mehrfach aufrufen ist der Normalfall (jede
     *  Wiederverbindung) — jedes Mal wird ein voller neuer geholt. */
    const armSnapshot = () => setGeneration((g) => g + 1);

    const clearPing = () => {
      if (pingTimerRef.current !== null) { window.clearInterval(pingTimerRef.current); pingTimerRef.current = null; }
    };

    const connect = () => {
      if (closingRef.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(officeWsUrl(getToken()));
      } catch {
        // Kein Socket möglich (z. B. blockierter Upgrade): der Schnappschuss allein muss reichen.
        armSnapshot();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setWsError(undefined);
        ws.send(JSON.stringify(subscribeMessage(scope)));
        // Erst jetzt den Schnappschuss holen — vorher fiele jede Zeile, die zwischen Lesen
        // und Abonnieren geschrieben wird, in ein Loch.
        armSnapshot();
        clearPing();
        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
        }, PING_MS);
      };

      ws.onmessage = (e) => {
        let msg: WsIn;
        try { msg = JSON.parse(e.data as string) as WsIn; } catch { return; }
        if (msg.type === "office_ev") { accept(msg.ev); return; }
        if (msg.type === "hello" && msg.v !== EVENT_VERSION) {
          // Das Backend spricht einen anderen Vertrag. Nicht raten, nicht wiederverbinden.
          closingRef.current = true;
          setWsError(`Das Büro spricht Vertragsversion ${msg.v}, diese Ansicht kennt `
            + `${EVENT_VERSION}. Bitte die Seite neu laden.`);
          ws.close();
        }
      };

      ws.onclose = (e) => {
        clearPing();
        liveRef.current = false;
        setLive(false);
        if (closingRef.current) return;
        // 4401/4403 kommen aus der Authentifizierung des Sockets — dagegen hilft kein
        // Wiederholen. 401 auf dem HTTP-Weg leitet `src/api.ts` ohnehin schon hart weiter.
        if (e.code === 4401 || e.code === 4403) {
          setWsError("Keine Berechtigung für den Live-Strom des Büros.");
          return;
        }
        const wait = RECONNECT_MS[Math.min(attemptRef.current, RECONNECT_MS.length - 1)];
        attemptRef.current++;
        retryTimerRef.current = window.setTimeout(connect, wait);
      };
    };

    connect();
    // Notfallanker: kommt der Socket nicht zustande, wird trotzdem einmal geholt. Ohne das
    // bliebe die Ansicht schwarz, wo eine statische Ansicht möglich gewesen wäre.
    graceTimer = window.setTimeout(() => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) armSnapshot();
    }, SOCKET_GRACE_MS);

    return () => {
      closingRef.current = true;
      if (graceTimer !== null) window.clearTimeout(graceTimer);
      if (retryTimerRef.current !== null) { window.clearTimeout(retryTimerRef.current); retryTimerRef.current = null; }
      if (pingTimerRef.current !== null) { window.clearInterval(pingTimerRef.current); pingTimerRef.current = null; }
      if (bumpTimerRef.current !== null) { window.clearTimeout(bumpTimerRef.current); bumpTimerRef.current = null; }
      wsRef.current?.close();
      wsRef.current = null;
      bufferRef.current = [];
      liveRef.current = false;
    };
  }, [scopeKey, accept]);   // `accept` ist über `useCallback` stabil

  // ── Der Schnappschuss ─────────────────────────────────────────────────────────────────────
  //
  // `staleTime: Infinity`: ein Schnappschuss ist ein Zeitpunkt, kein Wert, der veraltet.
  // Fortgeschrieben wird über den Socket. Neu geholt wird er nur, wenn `generation` steigt —
  // also bei einem Loch, und ein Loch ist jede Wiederverbindung.
  const snapshot = useQuery({
    queryKey: ["office", "events", key, generation],
    queryFn: () => fetchSnapshot(sid
      ? (afterSeq) => officeApi.events(sid, { limit: EVENT_PAGE_LIMIT, afterSeq })
      // Der Umfang reist als `project_id` mit — er **verengt** serverseitig und
      // autorisiert nie; die erlaubte Menge rechnet das Backend ohnehin selbst.
      : (afterSeq) => officeApi.allEvents({
        limit: EVENT_PAGE_LIMIT, afterSeq, sinceHours,
        ...(scope.kind === "project" ? { projectId: scope.projectId } : {}),
      })),
    enabled: (!!sid || alle) && generation > 0,
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  // Schritt 4 und 5: Schnappschuss aufnehmen, Puffer nachziehen, live gehen.
  // Das läuft in EINEM synchronen Block — dazwischen kann keine Nachricht dazwischenkommen,
  // und genau deshalb braucht der Übergang keine Sperre.
  useEffect(() => {
    const data = snapshot.data;
    if (!data) return;
    const rec = recorderRef.current;
    if (!rec) return;

    for (const ev of data.events) rec.push(ev);

    const map = new Map<string, RosterEntry>();
    for (const entry of data.agents) map.set(entry.agent_id, entry);
    rosterRef.current = map;

    const buffered = bufferRef.current;
    bufferRef.current = [];
    for (const ev of buffered) {
      // Doppelte wirft `push` weg — hier wird nichts verglichen. Das ist der ganze Trick.
      if (rec.push(ev)) patchRoster(ev);
    }

    liveRef.current = true;
    setRoster([...rosterRef.current.values()]);
    setLive(wsRef.current?.readyState === WebSocket.OPEN);
    setRevision((r) => r + 1);
  }, [snapshot.data, patchRoster]);

  // ── Kosten ────────────────────────────────────────────────────────────────────────────────
  //
  // Eine ganz normale Abfrage: Kosten ändern sich am Laufende, nicht je Schritt. Solange etwas
  // läuft, wird nachgesehen; danach nicht mehr.
  //
  // Im Modus „alle Sitzungen" gibt es sie **nicht**: der Roll-up ist je Raum gebaut, und ein
  // Aufruf je gezeigter Sitzung wären zwanzig Runden für eine Summe. `computeTotals` fällt
  // dann auf den Roster zurück — Tokens stehen dort ohnehin autoritativ, die Kosten sind die
  // abgerechneten je Lauf, und `cost_priced` trägt das „≥" wie überall sonst. Die Kopfzeile
  // meint damit die Summe über genau die Sitzungen, die im Raum stehen.
  const sessionRunning = roster.some((r) => r.status === "running");
  const cost = useQuery({
    queryKey: ["office", "cost", key],
    queryFn: () => officeApi.cost(sid!),
    enabled: !!sid && generation > 0,
    staleTime: 30_000,
    refetchInterval: sessionRunning ? 60_000 : false,
    refetchOnWindowFocus: false,
    retry: 1,
  });

  const totals = useMemo<FeedTotals>(() => computeTotals(roster, cost.data), [roster, cost.data]);

  const error = wsError
    ?? (snapshot.error ? `Verlauf nicht ladbar: ${(snapshot.error as Error).message}` : undefined);

  return { recorder, revision, roster, totals, live, error };
}

/** Tokens aus dem Roster, Kosten aus dem Roll-up — und wo der Roll-up (noch) fehlt, ehrlich
 *  aus dem Roster, samt „unvollständig", wenn auch nur ein Lauf unbepreist ist. */
function computeTotals(roster: Roster, cost: CostRollup | undefined): FeedTotals {
  if (!roster.length && !cost) return EMPTY_TOTALS;
  let inTok = 0, outTok = 0, cacheTok = 0, running = 0, fallbackCost = 0, unpriced = false;
  for (const r of roster) {
    inTok += r.in_tokens || 0;
    outTok += r.out_tokens || 0;
    cacheTok += r.cache_read_tokens || 0;
    fallbackCost += r.cost_usd || 0;
    if (r.status === "running") running++;
    // `null` = Altzeile, `false` = kein Katalogeintrag. Beides heißt: die Summe ist eine
    // Untergrenze, und die Anzeige stellt ein „≥" davor.
    if (r.cost_priced !== true) unpriced = true;
  }
  const billed = cost?.cost_usd_billed ?? fallbackCost;
  return {
    runs: roster.length,
    running,
    in_tokens: cost?.in_tokens ?? inTok,
    out_tokens: cost?.out_tokens ?? outTok,
    cache_read_tokens: cost?.cache_read_tokens ?? cacheTok,
    cost_usd_billed: billed,
    cost_usd_estimated: cost?.cost_usd_estimated ?? billed,
    cost_partial: cost?.cost_partial ?? unpriced,
  };
}
