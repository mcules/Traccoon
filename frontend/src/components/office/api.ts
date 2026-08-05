// Schicht 2 — die Lese-API des „Büros".
//
// Benannte Unter-API im Stil von `processApi`/`workflowApi` in `src/api.ts`: ein flaches Objekt
// mit drei Aufrufen, alle über den gemeinsamen `api`-Helfer. Der bringt Basis-Pfad `/api`,
// den `Authorization`-Kopf aus `getToken()` und — wichtig — die harte Weiterleitung bei 401.
// Deshalb steht hier **keine** eigene 401-Behandlung; sie wäre eine zweite Wahrheit.
//
// Die Antwort-Typen leben hier und **nicht** in `types.ts`: das ist Schicht 0 und kennt keine
// API. Was von der Leitung kommt, ist eine Transportform; was der Raum daraus baut, ist der
// Vertrag. Genau ein Feld überquert die Grenze unverändert — `Ev` —, und das ist Absicht:
// Historie und Live-Strom entstehen im Backend in derselben Funktion (`services/office.py`
// `step_events`), also gibt es auch im Frontend nur eine Form.
//
// ── Stand der Abstimmung mit dem Backend ────────────────────────────────────────────────────
// Der Live-Socket (Welle D, `api/office_ws.py`) ist **gegen echten Code** gebaut:
// `/api/ws?token=`, `{"type":"subscribe","scopes":[…]}`, `{"type":"office_ev","ev":{…}}`.
// Die Lese-Endpunkte (Welle C, `api/office.py`) existierten beim Schreiben dieser Datei
// noch nicht — Pfade und Feldnamen stehen deshalb nach Plan. Alles, was der Feed nicht selbst
// braucht, ist `?`-optional; so bricht eine Abweichung im Nebenfeld nicht den Bau, sondern
// fehlt bloß. Die Stellen, die zwingend passen müssen, sind mit ⚠ markiert.

import { api } from "../../api";
import type { Ev, Roster, RunStatus } from "./types.ts";

/** Version des Ereignis-Umschlags (`services/office.py::EVENT_VERSION`). Ändert sich die
 *  Bedeutung eines Feldes, zählt das Backend hoch — und die Ansicht verweigert die Darstellung,
 *  statt eine falsche Deutung zu zeichnen. Lieber ein Hinweis als ein gelogener Raum. */
export const EVENT_VERSION = 1;

// ── Geltungsbereich und Adresse ─────────────────────────────────────────────────────────────

/** Woher die Sitzungsliste kommt: aus einem Projekt-Reiter oder von der globalen Seite.
 *  `projectKey` steckt mit drin, weil die globale Seite `?project=KEY` in die URL schreibt
 *  und Kopfzeile wie Filter den Schlüssel zeigen, nicht die Id. */
export type Scope =
  | { kind: "project"; projectId: number; projectKey: string }
  | { kind: "global" };

/** Adresse eines Raums. Genau zwei Formen, siehe `services/office.py::session_id`:
 *  `issue:{id}` = der Raum eines Tickets (Planung, Ausführung, Fortsetzungen, Unteragenten),
 *  `run:{root}` = ein Lauf ohne Ticket (Job, Assistent). */
export interface Sid {
  kind: "issue" | "run";
  ref: number;
}

/** `Sid` → `"issue:412"`. Dieselbe Zeichenkette steht in jedem Ereignis als `ev.sid`. */
export function sidKey(sid: Sid): string {
  return `${sid.kind}:${sid.ref}`;
}

/** `"issue:412"` → `Sid`, sonst `null`. Fail-closed: unlesbare Adressen ergeben keinen Raum,
 *  statt still auf einen falschen zu zeigen. */
export function parseSid(raw: string | null | undefined): Sid | null {
  if (!raw) return null;
  const [kind, ref] = raw.split(":");
  if (kind !== "issue" && kind !== "run") return null;
  const n = Number(ref);
  return Number.isSafeInteger(n) && n > 0 ? { kind, ref: n } : null;
}

// ── Antwortformen ───────────────────────────────────────────────────────────────────────────

/** Welche Räume die Liste überhaupt zurückgibt (`services`/`api/office.py::SESSION_STATUS`).
 *  `live` heißt dort **nicht** „Status running in der Datenbank", sondern „running **und**
 *  jünger als das Live-Fenster" — nach einem Worker-Absturz bliebe `running` sonst für immer
 *  stehen und der Kiosk zeigte einen Raum, in dem nie wieder etwas passiert. */
export type SessionStatus = "all" | "live" | "recent";

/** Eine Sitzung in der Liste — ein Raum, noch nicht betreten. */
export interface SessionSummary {
  /** ⚠ `"issue:412"` / `"run:8871"`. */
  sid: string;
  kind?: "issue" | "run";
  ref?: number;
  title?: string;
  issue_key?: string | null;
  project_id?: number | null;
  project_key?: string | null;
  /** Status der Sitzung: läuft noch etwas, oder wie ist sie ausgegangen. */
  status?: RunStatus;
  /** Wie viele Läufe zu diesem Raum gehören (Wurzel + Fortsetzungen + Unteragenten). */
  runs?: number;
  started_at?: string | null;
  ended_at?: string | null;
  /** Jüngster Zeitstempel dieses Raums (ISO). Das Backend liefert ihn seit jeher und
   *  sortiert die Liste danach; deklariert war er hier bloß nie. Der Kiosk braucht ihn:
   *  ein Raum, in dem 90 s nichts geschah, ist der falsche für einen Wandschirm. */
  last_event_at?: string | null;
  /** Mindestens ein Lauf ist `running`. */
  live?: boolean;
  /** Die Schritte sind der Aufbewahrung zum Opfer gefallen — der Raum bleibt leer,
   *  die Kosten stimmen trotzdem (`CostEntry.run_id` ist `SET NULL`). */
  purged?: boolean;
  cost_usd?: number;
  cost_priced?: boolean | null;
}

/** Umschlag der Sitzungsliste. Liefert das Backend ein nacktes Feld, baut `sessions()` den
 *  Umschlag selbst — siehe dort. */
export interface SessionList {
  sessions: SessionSummary[];
  /** Wie viele es insgesamt gäbe, falls die Antwort gekappt wurde. */
  total?: number;
}

/** Eine Seite des Ereignisfensters. */
export interface EventPage {
  /** ⚠ Die Ereignisse selbst, aufsteigend nach `seq`. */
  events: Ev[];
  /** ⚠ Roster direkt aus `runs` — nur auf der ersten Seite verlässlich befüllt.
   *  Er verdient seinen Platz, weil vom **ältesten** Ende gekappt wird: ohne ihn gingen bei
   *  einer langen Sitzung die `run_start`-Ereignisse verloren und der Raum bliebe leer. */
  agents?: Roster;
  sid?: string;
  title?: string;
  issue_key?: string | null;
  project_id?: number | null;
  project_key?: string | null;
  /** Kleinste bzw. größte gelieferte `seq` dieser Seite. */
  seq_from?: number | null;
  seq_to?: number | null;
  /** Vom ältesten Ende gekappt — es gibt ältere Ereignisse, die nicht mitkommen. */
  truncated?: boolean;
  purged?: boolean;
  live?: boolean;
}

/** Kosten je Modell. `priced` ist die Unterscheidung, die Traccoon heute fehlt:
 *  ein Katalogeintrag mit 0,00 ist *bepreist und gratis*, gar kein Eintrag ist *unbekannt*. */
export interface CostByModel {
  provider?: string | null;
  model?: string | null;
  in_tokens?: number;
  out_tokens?: number;
  cache_read_tokens?: number;
  cost_usd?: number;
  priced?: boolean;
  calls?: number;
}

/** Kosten eines Raums, zweigleisig: abgerechnet (aus `CostEntry`, autoritativ) und geschätzt
 *  (Schritt-Tokens gegen den aktuellen Katalog — richtig auch bei einem Fallback-Lauf). */
export interface CostRollup {
  cost_usd_billed?: number;
  cost_usd_estimated?: number;
  /** Mindestens ein Modellzug hat keinen Katalogeintrag → die Anzeige stellt ein „≥" davor. */
  cost_partial?: boolean;
  in_tokens?: number;
  out_tokens?: number;
  cache_read_tokens?: number;
  by_model?: CostByModel[];
  by_agent?: { agent?: string; agent_id?: string; cost_usd?: number; calls?: number }[];
  purged?: boolean;
}

// ── Die Aufrufe ─────────────────────────────────────────────────────────────────────────────

/** Obergrenze einer Ereignisseite. Das Backend deckelt selbst (`EVENT_CAP_MAX = 20 000`);
 *  4 000 ist der Wert aus dem Plan und hält eine Seite klein genug, dass der Schub beim
 *  Nachziehen nicht zur Ruckelquelle wird. */
export const EVENT_PAGE_LIMIT = 4000;

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const officeApi = {
  /** Räume, die dieser Nutzer sehen darf.
   *
   *  Zwei Pfade, ein Ergebnis: der Projekt-Reiter fragt unter dem Projekt (Zugriff prüft
   *  `get_project_access`, Fremde bekommen 404), die globale Seite fragt übergreifend.
   *  `projectId` **verengt** dort nur — autorisiert wird es nie. */
  sessions: (scope: Scope,
             opts?: { limit?: number; projectId?: number; sinceHours?: number;
                      status?: SessionStatus }): Promise<SessionList> => {
    const path = scope.kind === "project"
      ? `/projects/${scope.projectId}/office/sessions${qs({ limit: opts?.limit, since_hours: opts?.sinceHours, status: opts?.status })}`
      : `/office/sessions${qs({ limit: opts?.limit, since_hours: opts?.sinceHours, project_id: opts?.projectId, status: opts?.status })}`;
    // Ob der Umschlag `{sessions: […]}` heißt oder ein nacktes Feld ist, entscheidet Welle C.
    // Beides wird hier zum selben Ergebnis — das ist billiger als eine Rückfrage und
    // überlebt die Abstimmung in beide Richtungen.
    return api.get<SessionList | SessionSummary[]>(path)
      .then((r) => (Array.isArray(r) ? { sessions: r } : r));
  },

  /** Eine Seite Ereignisse.
   *
   *  `afterSeq` ist **ausschließlich** Seitenblättern und Lückenfüllung direkt nach einer
   *  Wiederverbindung — **nie** ein Poller. Warum, steht ausführlich in `useOfficeFeed.ts`
   *  und im Modul-Docstring von `api/office_ws.py`: `seq` kommt aus einer `SERIAL`-Spalte,
   *  die **vor** dem Commit vergeben wird. */
  events: (sid: Sid, opts?: { limit?: number; afterSeq?: number }): Promise<EventPage> =>
    api.get<EventPage>(
      `/office/sessions/${sid.kind}/${sid.ref}/events`
      + qs({ limit: opts?.limit ?? EVENT_PAGE_LIMIT, after_seq: opts?.afterSeq })),

  /** Kosten des Raums — eigener Aufruf, weil sie eine ganz andere Änderungsrate haben als
   *  die Ereignisse und den Schnappschuss sonst dauernd entwerten würden. */
  cost: (sid: Sid): Promise<CostRollup> =>
    api.get<CostRollup>(`/office/sessions/${sid.kind}/${sid.ref}/cost`),
};

// ── Der Live-Socket ─────────────────────────────────────────────────────────────────────────
//
// Gegen echten Code gebaut (`backend/app/api/office_ws.py`, Welle D): EIN Nutzer-Socket
// bedient Projekt-Reiter **und** globale Seite; gefiltert wird serverseitig. Der Client kann
// per `subscribe` nur **verengen**, nie erweitern — ein Abo auf ein fremdes Projekt ergibt
// Stille, keinen Fehler.

/** Was der Server über den Socket schickt. */
export type WsIn =
  | { type: "hello"; v: number; user_id: number; is_admin: boolean; projects: number[]; acl_ttl_s: number }
  | { type: "subscribed"; scope: number[] | null }
  | { type: "office_ev"; ev: Ev }
  | { type: "pong" };

/** Was der Client schickt. */
export type WsOut =
  | { type: "subscribe"; scopes: ({ kind: "project"; id: number } | { kind: "global" })[] }
  | { type: "ping" };

/** Adresse des Nutzer-Sockets. Ein Socket je Sitzung im Browser reicht — die Trennung nach
 *  Projekt macht der Server, nicht die Anzahl der Verbindungen. */
export function officeWsUrl(token: string | null): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/api/ws?token=${encodeURIComponent(token ?? "")}`;
}

/** `Scope` → die `subscribe`-Nachricht. Ein Objekt, damit die Verengung an genau einer Stelle
 *  entsteht und der Aufrufer sie nicht von Hand zusammenbaut. */
export function subscribeMessage(scope: Scope): WsOut {
  return scope.kind === "project"
    ? { type: "subscribe", scopes: [{ kind: "project", id: scope.projectId }] }
    : { type: "subscribe", scopes: [{ kind: "global" }] };
}
