// Layer 2, the inspector: the selected figure in detail.
//
// The stage shows **that** somebody is working; here stands **what on**. Everything in it
// comes from two sources, and both are already there: the roster entry (master data and
// totals, straight from `runs`) and the log (what this figure did last).
//
// Two places deserve a justification:
//
//   · **`blocker_kind` does not stand in the roster.** `RosterEntry` does not carry it; it
//     comes with `run_end` and is already condensed to a `GateKind` there
//     (`mapEvent::GATE_OF`). The inspector therefore reads the last `gate` command of this
//     figure. If the run is `blocked` and there is still no gate in the window (truncation),
//     it honestly says "reason not in the window" instead of a guessed reason.
//   · **Costs with "≥".** `cost_priced !== true` means: either an old row (`null`) or no
//     catalog entry (`false`). Both make the number a lower bound. A catalog entry with 0.00
//     on the other hand would be *priced and free*, and exactly that distinction is the
//     reason the sign exists at all.

import { tr } from "../../i18n";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { Scope } from "./api.ts";
import type { Cmd, GateKind, Roster, RosterEntry } from "./types.ts";
import type { LogSource } from "./Timeline.tsx";
import { projectPath } from "../../projectTabs";
import {
  GATE_TEXT, durationText, statusColor, statusText, uhrText, usdText, number,
} from "./TopBar.tsx";
import { BUTTON_SMALL, BUTTON_TEXT} from "../ui";

/** This many steps back the inspector shows. More is the job of the dock. */
const STEPS = 10;

export interface InspectorProps {
  scope: Scope;
  /** The selected figure, `null` = nothing selected. */
  entry: RosterEntry | null;
  /** For the parent run: its name is taken from it. */
  roster: Roster;
  recorder: LogSource;
  revision: number;
  /** `null` = present, otherwise epoch ms: the inspector shows the same moment as the room. */
  seekTs: number | null;
  onSelect?: (id: string) => void;
  onClose?: () => void;
  /** Opens the file tab in the dock for the role of this run. Optional: without a dock (in
   *  the project tab) there is no place the jump could lead to, and then the entry is better
   *  missing than pointing into nothing. The inspector **stays** with the single run in the
   *  process: it passes the role on, it does not become the file itself. */
  onOpenFile?: (agent: string) => void;
  className?: string;
}

// ── Derivations from the log ────────────────────────────────────────────────────────────────

interface Step {
  key: string;
  ts: number;
  text: string;
  css?: string;
}

function stepText(c: Cmd): { text: string; css?: string } | null {
  switch (c.k) {
    // By contract it stands before **every** command and is pure bookkeeping; in a list of
    // the last steps it would only be noise displacing the real steps.
    case "ensureActor": return null;
    case "say": return { text: `💬 ${c.text}` };
    case "think": return { text: `💭 ${c.text}`, css: "italic text-muted" };
    case "tool": return { text: `🔧 ${c.tool}${c.target ? ` · ${c.target}` : ""}` };
    case "toolEnd":
      return c.ok === true ? { text: tr("inspector.tool_ok"), css: "text-green-400" }
        : c.ok === false ? { text: tr("inspector.tool_failed"), css: "text-red-400" }
          : { text: tr("inspector.tool_unknown"), css: "text-muted" };
    case "edit": return { text: `📝 ${c.path}` };
    case "spawn": return { text: `🌱 ${tr("inspector.started_subagent")}` };
    case "deliver": return { text: `📨 ${tr("inspector.handover")}${c.text ? `: ${c.text}` : ""}` };
    case "gate": return { text: `⏸ ${tr(GATE_TEXT[c.kind])}`, css: "text-orange-400" };
    case "resume": return { text: `▶ ${tr("inspector.answer_received_carrying")}` };
    case "status": return { text: `● ${statusText(c.status)}`, css: statusColor(c.status) };
    case "done": return c.ok ? { text: tr("inspector.done"), css: "text-green-400" }
      : { text: tr("inspector.aborted"), css: "text-red-400" };
    // The server rack. `back` gets a line of its own instead of "failed": failed **and**
    // healed is the only good news in the error case, and the list is the place where one can
    // read it in plain text.
    case "deploy":
      return c.state === "start" ? { text: `🖥 ${tr("inspector.deployment_running")} · ${c.label}` }
        : c.state === "ok" ? { text: `🖥 Deployment live · ${c.label}`, css: "text-green-400" }
          : c.state === "fail" ? { text: `🖥 Deployment fehlgeschlagen · ${c.label}`, css: "text-red-400" }
            : { text: `🖥 ${tr("inspector.deployment_rolled_back")} · ${c.label}`, css: "text-orange-400" };
  }
}

interface Excerpt {
  steps: Step[];
  /** Zuletzt begonnenes Werkzeug samt Ergebnis, falls es im Fenster endete. */
  tool: { tool: string; target?: string; ts: number; duration: number | null; ok: boolean | null | undefined } | null;
  gate: GateKind | null;
  edits: number;
}

function excerptFrom(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], id: string, to: number | null): Excerpt {
  const steps: Step[] = [];
  let tool: Excerpt["tool"] = null;
  let gate: GateKind | null = null;
  let edits = 0;
  for (const e of log) {
    if (to !== null && e.ts > to) continue;
    e.cmds.forEach((c, i) => {
      // `deploy` is the only command without an `id`: it belongs to the room, not to the
      // figure. The triggering figure stands in `by`, the same affiliation for the inspector.
      if ((c.k === "deploy" ? c.by : c.id) !== id) return;
      if (c.k === "tool") {
        tool = { tool: c.tool, target: c.target, ts: e.ts, duration: null, ok: undefined };
      } else if (c.k === "toolEnd" && tool && tool.ok === undefined) {
        tool = { ...tool, ok: c.ok, duration: Math.max(0, e.ts - tool.ts) };
      } else if (c.k === "edit") {
        edits++;
      } else if (c.k === "gate") {
        gate = c.kind;
      } else if (c.k === "resume") {
        gate = null;
      }
      const t = stepText(c);
      if (t) steps.push({ key: `${e.seq}:${i}`, ts: e.ts, text: t.text, css: t.css });
    });
  }
  return { steps: steps.slice(-STEPS), tool: tool, gate, edits };
}

// ── The component ───────────────────────────────────────────────────────────────────────────

export default function Inspector({
  scope, entry, roster, recorder, revision, seekTs, onSelect, onClose, onOpenFile, className,
}: InspectorProps) {
  const log = useMemo(() => recorder.entries(), [recorder, revision]);
  const excerpt = useMemo(
    () => (entry ? excerptFrom(log, entry.agent_id, seekTs) : null),
    [log, entry?.agent_id, seekTs],
  );

  if (!entry || !excerpt) {
    return (
      <div className={`rounded border border-line bg-card px-3 py-4 text-center text-xs text-muted ${className ?? ""}`}>
        {tr("inspector.no_character_selected_click")}
      </div>
    );
  }

  const start = entry.started_at ? Date.parse(entry.started_at) : NaN;
  const ende = entry.ended_at ? Date.parse(entry.ended_at)
    : (entry.status === "running" ? Date.now() : NaN);
  const duration = Number.isFinite(start) && Number.isFinite(ende) ? ende - start : null;
  const unpriced = entry.cost_priced !== true;

  const parent = entry.parent_run_id === null
    ? null
    : (roster.find((r) => r.run_id === entry.parent_run_id) ?? null);
  const parentId = entry.parent_run_id === null ? null : `run:${entry.parent_run_id}`;

  // The project key stands on the run; in the project scope the scope itself is the fallback
  // (a run without a `project_key` can still sit in this project).
  const projectKey = entry.project_key ?? (scope.kind === "project" ? scope.projectKey : null);

  const blockText = entry.status === "blocked"
    ? (excerpt.gate ? tr(GATE_TEXT[excerpt.gate]) : tr("inspector.reason_outside_the_window"))
    : (entry.status === "planned" ? GATE_TEXT.plan : null);

  return (
    <div className={`flex min-h-0 flex-col rounded border border-line bg-card ${className ?? ""}`}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
        <span className="font-medium">{entry.agent || `Lauf ${entry.run_id}`}</span>
        <span className={`text-xs ${statusColor(entry.status)}`}>{statusText(entry.status)}</span>
        <div className="flex-1" />
        <span className="font-mono text-[11px] text-muted">#{entry.run_id}</span>
        {onClose && (
          <button type="button" onClick={onClose} title={tr("inspector.close_inspector")}
            className={BUTTON_SMALL.secondary}>✕</button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-2 text-xs">
        {blockText && (
          <div className="rounded border border-orange-400/40 bg-orange-400/5 px-2 py-1 text-orange-400">
            ⏸ {blockText}
          </div>
        )}

        <dl className="grid grid-cols-[8.5rem_1fr] gap-x-2 gap-y-1">
          <Field label={tr("inspector.run_id")}>
            <span className="font-mono">{entry.agent_id}</span>
          </Field>
          <Field label={tr("inspector.role")}>{entry.agent || "—"}</Field>
          <Field label={tr("inspector.phase")}>
            {entry.phase === "plan" ? tr("dock.planning") : entry.phase === "execute" ? tr("dock.execution") : (entry.phase || "—")}
          </Field>
          <Field label={tr("inspector.provider_model")}>
            {entry.provider || "—"} / <span className="font-mono">{entry.model || "—"}</span>
          </Field>
          <Field label={tr("office_room.tokens")}>
            <span title={`Cache gelesen ${number(entry.cache_read_tokens)}`}>
              {number(entry.in_tokens)} {tr("personnel_file.text_2")} · {number(entry.out_tokens)} {tr("personnel_file.text")}
            </span>
          </Field>
          <Field label={tr("personnel_file.cost")}>
            <span title={tr(unpriced ? "inspector.model_no_price_catalog" : "inspector.priced_against_catalog")}>
              {usdText(entry.cost_usd, unpriced)}
            </span>
          </Field>
          <Field label={tr("inspector.start")}>{Number.isFinite(start) ? uhrText(start) : "—"}</Field>
          <Field label={tr("personnel_file.duration")}>{durationText(duration)}{entry.status === "running" && ` (${tr("office_room.st_running")})`}</Field>
          <Field label={tr("personnel_file.rounds")}>{entry.iterations || 0}</Field>
          <Field label={tr("inspector.edits")}>{excerpt.edits}</Field>
          <Field label={tr("inspector.parent_run")}>
            {parentId === null ? (
              <span className="text-muted">— (Wurzellauf)</span>
            ) : onSelect ? (
              <button type="button" onClick={() => onSelect(parentId)}
                className={BUTTON_TEXT.secondary}>
                {parent?.agent || parentId}
              </button>
            ) : (
              <span>{parent?.agent || parentId}</span>
            )}
          </Field>
          <Field label="Verschachtelung">{entry.spawn_depth}</Field>
          <Field label={tr("inspector.last_tool")}>
            {excerpt.tool ? (
              <span>
                <span className="font-mono">{excerpt.tool.tool}</span>
                {excerpt.tool.target && <span className="text-muted"> · {excerpt.tool.target}</span>}
                <span className="text-muted">
                  {" · "}
                  {excerpt.tool.ok === undefined ? tr("office_room.st_running")
                    : excerpt.tool.ok === true ? "erfolgreich"
                      : excerpt.tool.ok === false ? "fehlgeschlagen"
                        : tr("inspector.result_unknown")}
                  {excerpt.tool.ok !== undefined && ` · ${durationText(excerpt.tool.duration)}`}
                </span>
              </span>
            ) : <span className="text-muted">—</span>}
          </Field>
        </dl>

        <div>
          <div className="mb-1 font-medium">{tr("inspector.last_steps")}</div>
          {excerpt.steps.length === 0 ? (
            <div className="text-muted">{tr("inspector.nothing_window")}</div>
          ) : (
            <div className="space-y-0.5">
              {excerpt.steps.map((s) => (
                <div key={s.key} className="flex gap-2">
                  <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(s.ts)}</span>
                  <span className={`min-w-0 flex-1 break-words ${s.css ?? ""}`}>{s.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-2 border-t border-line pt-2">
          {onOpenFile && entry.agent && (
            <button type="button" onClick={() => onOpenFile(entry.agent)}
              title={tr("inspector.all_runs_role_role", { role: entry.agent })}
              className={BUTTON_SMALL.secondary}>
              📇 Personalakte: {entry.agent}
            </button>
          )}
          {projectKey && entry.issue_key && (
            <Link to={`/projects/${projectKey}/tickets/${entry.issue_key}`}
              className="rounded border border-line px-2 py-0.5 hover:border-brand">
              🎫 Ticket {entry.issue_key}
            </Link>
          )}
          {projectKey && (
            <Link to={projectPath(projectKey, "operations", "monitor")}
              className="rounded border border-line px-2 py-0.5 hover:border-brand">
              📈 Agenten-Monitor
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}
