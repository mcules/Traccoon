// Schicht 2 — der Inspektor: die ausgewählte Figur im Detail.
//
// Die Bühne zeigt, **dass** jemand arbeitet; hier steht, **woran**. Alles darin kommt aus zwei
// Quellen, und beide sind schon da: der Roster-Eintrag (Stammdaten und Summen, direkt aus
// `runs`) und das Log (was diese Figur zuletzt getan hat).
//
// Zwei Stellen verdienen eine Begründung:
//
//   · **`blocker_kind` steht nicht im Roster.** `RosterEntry` trägt ihn nicht — er kommt mit
//     `run_end` und ist dort bereits zu einer `GateKind` verdichtet (`mapEvent::GATE_OF`). Der
//     Inspektor liest deshalb das letzte `gate`-Kommando dieser Figur. Ist der Lauf `blocked`
//     und trotzdem kein Gate im Fenster (Kappung), steht ehrlich „Grund nicht im Fenster" da
//     statt eines geratenen Grundes.
//   · **Kosten mit „≥".** `cost_priced !== true` heißt: entweder Altzeile (`null`) oder kein
//     Katalogeintrag (`false`). Beides macht die Zahl zu einer Untergrenze. Ein Katalogeintrag
//     mit 0,00 wäre dagegen *bepreist und gratis* — genau diese Unterscheidung ist der Grund,
//     warum das Zeichen überhaupt existiert.

import { tr } from "../../i18n";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { Scope } from "./api.ts";
import type { Cmd, GateKind, Roster, RosterEntry } from "./types.ts";
import type { LogQuelle } from "./Timeline.tsx";
import {
  GATE_TEXT, dauerText, statusFarbe, statusText, uhrText, usdText, zahl,
} from "./TopBar.tsx";

/** So viele Schritte zeigt der Inspektor rückwärts. Mehr ist die Aufgabe des Docks. */
const SCHRITTE = 10;

export interface InspectorProps {
  scope: Scope;
  /** Die ausgewählte Figur, `null` = nichts ausgewählt. */
  entry: RosterEntry | null;
  /** Für den Elternlauf: aus ihm wird sein Name. */
  roster: Roster;
  recorder: LogQuelle;
  revision: number;
  /** `null` = Gegenwart, sonst Epoch-ms — der Inspektor zeigt denselben Moment wie der Raum. */
  seekTs: number | null;
  onSelect?: (id: string) => void;
  onClose?: () => void;
  /** Öffnet den Akte-Reiter im Dock für die Rolle dieses Laufs. Optional — ohne Dock (im
   *  Projekt-Reiter) gibt es keinen Ort, an den der Sprung führen könnte, und dann fehlt der
   *  Einstieg lieber, als ins Leere zu zeigen. Der Inspektor **bleibt** dabei beim einzelnen
   *  Lauf: er reicht die Rolle weiter, er wird nicht selbst zur Akte. */
  onOpenAkte?: (agent: string) => void;
  className?: string;
}

// ── Ableitungen aus dem Log ─────────────────────────────────────────────────────────────────

interface Schritt {
  key: string;
  ts: number;
  text: string;
  css?: string;
}

function schrittText(c: Cmd): { text: string; css?: string } | null {
  switch (c.k) {
    // Steht laut Vertrag vor **jedem** Kommando und ist reine Buchführung — in einer Liste der
    // letzten Schritte wäre es nur Rauschen, das die echten Schritte verdrängt.
    case "ensureActor": return null;
    case "say": return { text: `💬 ${c.text}` };
    case "think": return { text: `💭 ${c.text}`, css: "italic text-muted" };
    case "tool": return { text: `🔧 ${c.tool}${c.target ? ` · ${c.target}` : ""}` };
    case "toolEnd":
      return c.ok === true ? { text: "↩ Werkzeug erfolgreich", css: "text-green-400" }
        : c.ok === false ? { text: "↩ Werkzeug fehlgeschlagen", css: "text-red-400" }
          : { text: "↩ Werkzeug beendet, Ergebnis unbekannt", css: "text-muted" };
    case "edit": return { text: `📝 ${c.path}` };
    case "spawn": return { text: `🌱 ${tr("inspector.als_unteragent")}` };
    case "deliver": return { text: `📨 ${tr("inspector.uebergabe")}${c.text ? `: ${c.text}` : ""}` };
    case "gate": return { text: `⏸ ${tr(GATE_TEXT[c.kind])}`, css: "text-orange-400" };
    case "resume": return { text: `▶ ${tr("inspector.antwort_da")}` };
    case "status": return { text: `● ${statusText(c.status)}`, css: statusFarbe(c.status) };
    case "done": return c.ok ? { text: "✅ fertig", css: "text-green-400" }
      : { text: "❌ abgebrochen", css: "text-red-400" };
    // Der Serverschrank. `back` bekommt seine eigene Zeile statt „fehlgeschlagen": gescheitert
    // **und** geheilt ist die einzige gute Nachricht im Fehlerfall, und die Liste ist der Ort,
    // an dem man sie im Klartext lesen kann.
    case "deploy":
      return c.state === "start" ? { text: `🖥 ${tr("inspector.deploy_laeuft")} · ${c.label}` }
        : c.state === "ok" ? { text: `🖥 Deployment live · ${c.label}`, css: "text-green-400" }
          : c.state === "fail" ? { text: `🖥 Deployment fehlgeschlagen · ${c.label}`, css: "text-red-400" }
            : { text: `🖥 ${tr("inspector.deploy_zurueckgerollt")} · ${c.label}`, css: "text-orange-400" };
  }
}

interface Auszug {
  schritte: Schritt[];
  /** Zuletzt begonnenes Werkzeug samt Ergebnis, falls es im Fenster endete. */
  werkzeug: { tool: string; target?: string; ts: number; dauer: number | null; ok: boolean | null | undefined } | null;
  gate: GateKind | null;
  edits: number;
}

function auszugAus(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], id: string, bis: number | null): Auszug {
  const schritte: Schritt[] = [];
  let werkzeug: Auszug["werkzeug"] = null;
  let gate: GateKind | null = null;
  let edits = 0;
  for (const e of log) {
    if (bis !== null && e.ts > bis) continue;
    e.cmds.forEach((c, i) => {
      // `deploy` ist das einzige Kommando ohne `id`: es gehört dem Raum, nicht der Figur.
      // Die auslösende Figur steht in `by` — für den Inspektor ist das dieselbe Zugehörigkeit.
      if ((c.k === "deploy" ? c.by : c.id) !== id) return;
      if (c.k === "tool") {
        werkzeug = { tool: c.tool, target: c.target, ts: e.ts, dauer: null, ok: undefined };
      } else if (c.k === "toolEnd" && werkzeug && werkzeug.ok === undefined) {
        werkzeug = { ...werkzeug, ok: c.ok, dauer: Math.max(0, e.ts - werkzeug.ts) };
      } else if (c.k === "edit") {
        edits++;
      } else if (c.k === "gate") {
        gate = c.kind;
      } else if (c.k === "resume") {
        gate = null;
      }
      const t = schrittText(c);
      if (t) schritte.push({ key: `${e.seq}:${i}`, ts: e.ts, text: t.text, css: t.css });
    });
  }
  return { schritte: schritte.slice(-SCHRITTE), werkzeug, gate, edits };
}

// ── Die Komponente ──────────────────────────────────────────────────────────────────────────

export default function Inspector({
  scope, entry, roster, recorder, revision, seekTs, onSelect, onClose, onOpenAkte, className,
}: InspectorProps) {
  const log = useMemo(() => recorder.entries(), [recorder, revision]);
  const auszug = useMemo(
    () => (entry ? auszugAus(log, entry.agent_id, seekTs) : null),
    [log, entry?.agent_id, seekTs],
  );

  if (!entry || !auszug) {
    return (
      <div className={`rounded border border-line bg-card px-3 py-4 text-center text-xs text-muted ${className ?? ""}`}>
        {tr("inspector.keine_figur")}
      </div>
    );
  }

  const start = entry.started_at ? Date.parse(entry.started_at) : NaN;
  const ende = entry.ended_at ? Date.parse(entry.ended_at)
    : (entry.status === "running" ? Date.now() : NaN);
  const dauer = Number.isFinite(start) && Number.isFinite(ende) ? ende - start : null;
  const unbepreist = entry.cost_priced !== true;

  const eltern = entry.parent_run_id === null
    ? null
    : (roster.find((r) => r.run_id === entry.parent_run_id) ?? null);
  const elternId = entry.parent_run_id === null ? null : `run:${entry.parent_run_id}`;

  // Der Projektschlüssel steht am Lauf; im Projektumfang ist der Umfang selbst der Rückfall
  // (ein Lauf ohne `project_key` kann trotzdem in diesem Projekt sitzen).
  const projektKey = entry.project_key ?? (scope.kind === "project" ? scope.projectKey : null);

  const blockText = entry.status === "blocked"
    ? (auszug.gate ? tr(GATE_TEXT[auszug.gate]) : tr("inspector.grund_nicht_im_fenster"))
    : (entry.status === "planned" ? GATE_TEXT.plan : null);

  return (
    <div className={`flex min-h-0 flex-col rounded border border-line bg-card ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
        <span className="font-medium">{entry.agent || `Lauf ${entry.run_id}`}</span>
        <span className={`text-xs ${statusFarbe(entry.status)}`}>{statusText(entry.status)}</span>
        <div className="flex-1" />
        <span className="font-mono text-[11px] text-muted">#{entry.run_id}</span>
        {onClose && (
          <button type="button" onClick={onClose} title={tr("inspector.schliessen")}
            className="rounded border border-line px-1.5 text-xs text-muted hover:border-brand">✕</button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-2 text-xs">
        {blockText && (
          <div className="rounded border border-orange-400/40 bg-orange-400/5 px-2 py-1 text-orange-400">
            ⏸ {blockText}
          </div>
        )}

        <dl className="grid grid-cols-[8.5rem_1fr] gap-x-2 gap-y-1">
          <Feld label={tr("inspector.lauf_id")}>
            <span className="font-mono">{entry.agent_id}</span>
          </Feld>
          <Feld label={tr("inspector.rolle")}>{entry.agent || "—"}</Feld>
          <Feld label={tr("inspector.phase")}>
            {entry.phase === "plan" ? tr("dock.planung") : entry.phase === "execute" ? tr("dock.ausfuehrung") : (entry.phase || "—")}
          </Feld>
          <Feld label={tr("inspector.provider_modell")}>
            {entry.provider || "—"} / <span className="font-mono">{entry.model || "—"}</span>
          </Feld>
          <Feld label={tr("buero.tokens")}>
            <span title={`Cache gelesen ${zahl(entry.cache_read_tokens)}`}>
              {zahl(entry.in_tokens)} {tr("akte.ein")} · {zahl(entry.out_tokens)} {tr("akte.aus")}
            </span>
          </Feld>
          <Feld label={tr("akte.kosten")}>
            <span title={tr(unbepreist ? "inspector.kosten_teilweise" : "inspector.kosten_voll")}>
              {usdText(entry.cost_usd, unbepreist)}
            </span>
          </Feld>
          <Feld label={tr("inspector.start")}>{Number.isFinite(start) ? uhrText(start) : "—"}</Feld>
          <Feld label={tr("akte.dauer")}>{dauerText(dauer)}{entry.status === "running" && ` (${tr("buero.st_running")})`}</Feld>
          <Feld label={tr("akte.runden")}>{entry.iterations || 0}</Feld>
          <Feld label={tr("inspector.bearbeitungen")}>{auszug.edits}</Feld>
          <Feld label={tr("inspector.elternlauf")}>
            {elternId === null ? (
              <span className="text-muted">— (Wurzellauf)</span>
            ) : onSelect ? (
              <button type="button" onClick={() => onSelect(elternId)}
                className="text-brand hover:underline">
                {eltern?.agent || elternId}
              </button>
            ) : (
              <span>{eltern?.agent || elternId}</span>
            )}
          </Feld>
          <Feld label="Verschachtelung">{entry.spawn_depth}</Feld>
          <Feld label="Letztes Werkzeug">
            {auszug.werkzeug ? (
              <span>
                <span className="font-mono">{auszug.werkzeug.tool}</span>
                {auszug.werkzeug.target && <span className="text-muted"> · {auszug.werkzeug.target}</span>}
                <span className="text-muted">
                  {" · "}
                  {auszug.werkzeug.ok === undefined ? tr("buero.st_running")
                    : auszug.werkzeug.ok === true ? "erfolgreich"
                      : auszug.werkzeug.ok === false ? "fehlgeschlagen"
                        : "Ergebnis unbekannt"}
                  {auszug.werkzeug.ok !== undefined && ` · ${dauerText(auszug.werkzeug.dauer)}`}
                </span>
              </span>
            ) : <span className="text-muted">—</span>}
          </Feld>
        </dl>

        <div>
          <div className="mb-1 font-medium">Letzte Schritte</div>
          {auszug.schritte.length === 0 ? (
            <div className="text-muted">{tr("inspector.nichts_im_fenster")}</div>
          ) : (
            <div className="space-y-0.5">
              {auszug.schritte.map((s) => (
                <div key={s.key} className="flex gap-2">
                  <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(s.ts)}</span>
                  <span className={`min-w-0 flex-1 break-words ${s.css ?? ""}`}>{s.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-2 border-t border-line pt-2">
          {onOpenAkte && entry.agent && (
            <button type="button" onClick={() => onOpenAkte(entry.agent)}
              title={tr("inspector.alle_laeufe", { rolle: entry.agent })}
              className="rounded border border-line px-2 py-0.5 hover:border-brand">
              📇 Personalakte: {entry.agent}
            </button>
          )}
          {projektKey && entry.issue_key && (
            <Link to={`/projects/${projektKey}/tickets/${entry.issue_key}`}
              className="rounded border border-line px-2 py-0.5 hover:border-brand">
              🎫 Ticket {entry.issue_key}
            </Link>
          )}
          {projektKey && (
            <Link to={`/projects/${projektKey}?tab=monitor`}
              className="rounded border border-line px-2 py-0.5 hover:border-brand">
              📈 Agenten-Monitor
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function Feld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}
