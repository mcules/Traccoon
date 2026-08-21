// Layer 2, the dock below the stage. Four tabs: three on the same data, **chat** (what was
// said), **agents** (who is there) and **tools** (what was done), and one beside them, the
// **personnel file** (what a role does across all runs).
//
// The file is deliberately the foreign body here. The first three tabs read the log of this
// session and freeze with the room; the file asks the server itself and has a time window of
// its own across runs **and** sessions. It sits in the dock nevertheless and not in the
// inspector: the inspector explicitly promises in its head to get by without a query of its
// own, and would have no space for it in its 45 % tile either. The tab is a list of **roles**,
// exactly the axis the `agents` tab lacks.
//
// ── Where the text comes from, and what that costs ──────────────────────────────────────────
//
// The `Recorder` keeps **commands**, not events (`LogEntry = {ts, seq, cmds}`), which is the
// seam that makes the replay without snapshots possible. The chat therefore reads from the
// commands `say`/`think`/`deliver`/`gate`/`done` and not from `agent_text` and relatives. In
// practice that is the same thing: `user_message` and `agent_text` become exactly these
// commands in `mapEvent`.
//
// There is one exception, and it is deliberate: `system` messages (abort, truncation,
// compaction) produce **no** command according to `mapEvent` and therefore do not appear here.
// Adding them would mean keeping the event stream a second time: the same data in two places,
// with two truncation rules. If they are needed, they belong in `mapEvent`.
//
// ── Freezing ────────────────────────────────────────────────────────────────────────────────
//
// When rewinding, the dock shows the same moment as the room: everything with `ts > seekTs` is
// left out. A dock that ran on while the stage stood in the past would be two views of the
// same run contradicting each other.

import { tr } from "../../i18n";
import { useEffect, useMemo, useRef } from "react";
import Personnelfile from "./Personalakte.tsx";
import type { Scope } from "./api.ts";
import type { Cmd, Roster, RosterEntry } from "./types.ts";
import type { LogSource } from "./Timeline.tsx";
import {
  GATE_TEXT, durationText, fitsToFilter, statusColor, statusText, tokenText, uhrText, usdText, number,
} from "./TopBar.tsx";

// ── Kappung ─────────────────────────────────────────────────────────────────────────────────
//
// All three lists can grow to four digits. Truncation happens from the **oldest** end, the
// same direction as log and event window, and it is stated that truncation happened.

const CHAT_CAP = 200;
const TOOL_CAP = 200;
const AGENT_CAP = 80;

// ── Interface ───────────────────────────────────────────────────────────────────────────────

export type DockTab = "chat" | "agents" | "tools" | "akte";

export const DOCK_TABS: readonly { key: DockTab; label: string; icon: string }[] = [
  { key: "chat", label: "dock.chat", icon: "💬" },
  { key: "agents", label: "dock.agents", icon: "🤖" },
  { key: "tools", label: "dock.tools", icon: "🔧" },
  { key: "akte", label: "dock.personnel_file", icon: "📇" },
];

export interface DockProps {
  scope: Scope;
  tab: DockTab;
  onTabChange: (t: DockTab) => void;
  recorder: LogSource;
  /** Recomputation signal of the feed. */
  revision: number;
  roster: Roster;
  /** Session filter, `null` = all. Dims, does not remove. */
  filter: string | null;
  /** `null` = present, otherwise epoch ms: the dock freezes on this moment. */
  seekTs: number | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  className?: string;
}

// ── Abgeleitete Zeilen ──────────────────────────────────────────────────────────────────────

interface ChatLine {
  key: string;
  ts: number;
  id: string;
  icon: string;
  text: string;
  /** Class for the text: errors and gates stand out. */
  css?: string;
}

function chatFrom(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], to: number | null): ChatLine[] {
  const out: ChatLine[] = [];
  for (const e of log) {
    if (to !== null && e.ts > to) continue;
    e.cmds.forEach((c, i) => {
      const key = `${e.seq}:${i}`;
      if (c.k === "say") out.push({ key, ts: e.ts, id: c.id, icon: "💬", text: c.text });
      else if (c.k === "think") out.push({ key, ts: e.ts, id: c.id, icon: "💭", text: c.text, css: "italic text-muted" });
      else if (c.k === "deliver") out.push({ key, ts: e.ts, id: c.id, icon: "📨", text: c.text || tr("dock.hands_result") });
      else if (c.k === "gate") out.push({ key, ts: e.ts, id: c.id, icon: "⏸", text: c.text || tr(GATE_TEXT[c.kind]), css: "text-orange-400" });
      else if (c.k === "done") {
        out.push({
          key, ts: e.ts, id: c.id, icon: c.ok ? "✅" : "❌",
          text: c.text || (c.ok ? "fertig" : "abgebrochen"),
          css: c.ok ? undefined : "text-red-400",
        });
      }
    });
  }
  return out;
}

interface ToolLine {
  key: string;
  ts: number;
  id: string;
  tool: string;
  target?: string;
  /** `null` = no end in the window (still running or the end was truncated away). */
  duration: number | null;
  /** Three valued **plus** `undefined` for "still running". `null` means *unknown*, not *good*. */
  ok: boolean | null | undefined;
}

/** Pairs `tool` with the next `toolEnd` of the same figure.
 *
 *  The commands carry no `tool_use_id`, which stays in the event. That is enough anyway: a
 *  run calls its tools one after another (the worker awaits every call), so "the next end of
 *  the same figure" is also the right one. The duration is therefore the distance of the wall
 *  clock times, which on the legacy path (both synthesised from **one** row) correctly gives
 *  0 and not the substitute duration of the stage: that one is a display decision and not a
 *  measurement, and an invented duration in a list showing durations would be a lie. */
function toolsFrom(log: readonly { ts: number; seq: number; cmds: Cmd[] }[], to: number | null): ToolLine[] {
  const open = new Map<string, ToolLine>();
  const out: ToolLine[] = [];
  for (const e of log) {
    if (to !== null && e.ts > to) continue;
    e.cmds.forEach((c, i) => {
      if (c.k === "tool") {
        const z: ToolLine = {
          key: `${e.seq}:${i}`, ts: e.ts, id: c.id, tool: c.tool,
          target: c.target, duration: null, ok: undefined,
        };
        // A second start without an end only displaces the first from the pairing; in the
        // list it stays (it did run) and keeps its "still running".
        open.set(c.id, z);
        out.push(z);
      } else if (c.k === "toolEnd") {
        const z = open.get(c.id);
        if (!z) return;
        open.delete(c.id);
        z.ok = c.ok;
        z.duration = Math.max(0, e.ts - z.ts);
      }
    });
  }
  // `out` stands in start order; the open entries are already contained in it.
  return out;
}

// ── The component ───────────────────────────────────────────────────────────────────────────

export default function Dock({
  scope, tab, onTabChange, recorder, revision, roster, filter, seekTs,
  selectedId, onSelect, className,
}: DockProps) {
  const log = useMemo(() => recorder.entries(), [recorder, revision]);
  const chat = useMemo(() => chatFrom(log, seekTs), [log, seekTs]);
  const tools = useMemo(() => toolsFrom(log, seekTs), [log, seekTs]);

  const nachId = useMemo(() => {
    const m = new Map<string, RosterEntry>();
    for (const r of roster) m.set(r.agent_id, r);
    return m;
  }, [roster]);

  const agents = useMemo(() => {
    const copy = [...roster];
    // Running first, then the most recent: the order in which one looks.
    copy.sort((a, b) => {
      const la = a.status === "running", lb = b.status === "running";
      if (la !== lb) return la ? -1 : 1;
      return (b.started_at ? Date.parse(b.started_at) : 0) - (a.started_at ? Date.parse(a.started_at) : 0);
    });
    return copy;
  }, [roster]);

  /** The **role** of the selected figure, the jump point of the file. A run without a role
   *  (job, assistant) gives `null`: then the choice in the file stays with the viewer instead
   *  of pointing at an empty role. */
  const chosenRole = selectedId ? (nachId.get(selectedId)?.agent || null) : null;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Scroll to the end in live operation; when rewinding explicitly **not**, because there the
  // viewer has chosen a place and wants to keep it.
  // The file is not a list that grows downwards: scrolling it to the end would show the last
  // tool bar of the last role instead of the heading with the time window.
  useEffect(() => {
    if (seekTs !== null || tab === "akte") return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [revision, tab, seekTs]);

  const name = (id: string): string => {
    const r = nachId.get(id);
    if (!r) return id;
    return r.agent || `Lauf ${r.run_id}`;
  };
  const dimmed = (id: string): boolean => {
    const r = nachId.get(id);
    return r ? !fitsToFilter(scope, r, filter) : false;
  };

  return (
    <div className={`flex min-h-0 flex-col rounded border border-line bg-card ${className ?? ""}`}>
      <div className="flex shrink-0 gap-1 border-b border-line px-2 pt-1.5" role="tablist"
        aria-label="Dock">
        {DOCK_TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            onClick={() => onTabChange(t.key)}
            className={"rounded-t border-b-2 px-2.5 py-1 text-xs "
              + (tab === t.key ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink")}>
            {t.icon} {tr(t.label)}
            {/* Die Akte bekommt keine Zahl: wie viele Rollen es gibt, weiß erst ihre eigene
                Abfrage — eine Zahl aus dem Roster wäre eine andere Menge mit demselben
                Aussehen. */}
            {t.key !== "akte" && (
              <span className="ml-1 text-[11px] text-muted">
                {t.key === "chat" ? chat.length : t.key === "agents" ? roster.length : tools.length}
              </span>
            )}
          </button>
        ))}
        <div className="flex-1" />
        {/* Der Einfrierhinweis gilt für die drei Log-Reiter. Die Akte friert nicht ein — sie
            names its own window in its own heading. */}
        {seekTs !== null && tab !== "akte" && (
          <span className="self-center pb-1 text-[11px] text-orange-400"
            title={tr("dock.dock_shows_same_moment")}>
            {tr("dock.frozen")} {uhrText(seekTs)}
          </span>
        )}
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {tab === "chat" && (
          <ChatListing rows={chat} name={name} dimmed={dimmed} onSelect={onSelect} />
        )}
        {tab === "agents" && (
          <AgentListing entries={agents} scope={scope} filter={filter}
            selectedId={selectedId} onSelect={onSelect} />
        )}
        {tab === "tools" && (
          <ToolListing rows={tools} name={name} dimmed={dimmed} onSelect={onSelect} />
        )}
        {tab === "akte" && (
          // The role of the selected figure, not the figure itself: the inspector below keeps
          // showing the single run, the file the role. Two truths side by side, and neither
          // pretends to be the other.
          <Personnelfile scope={scope} focusAgent={chosenRole} />
        )}
      </div>
    </div>
  );
}

// ── Kappungshinweis ─────────────────────────────────────────────────────────────────────────

function Capped({ n }: { n: number }) {
  if (n <= 0) return null;
  return (
    <div className="mb-1 border-b border-dashed border-line pb-1 text-[11px] text-muted">
      {tr("dock.count_older_entries_hidden", { count: number(n) })}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="py-4 text-center text-xs text-muted">{text}</div>;
}

// ── Chat ────────────────────────────────────────────────────────────────────────────────────

function ChatListing({ rows: lines, name, dimmed, onSelect }: {
  rows: ChatLine[];
  name: (id: string) => string;
  dimmed: (id: string) => boolean;
  onSelect: (id: string) => void;
}) {
  if (lines.length === 0) return <Empty text={tr("dock.nothing_said_yet")} />;
  const show = lines.slice(-CHAT_CAP);
  return (
    <div className="space-y-1">
      <Capped n={lines.length - show.length} />
      {show.map((z) => (
        <div key={z.key} className={`flex gap-2 text-xs ${dimmed(z.id) ? "opacity-40" : ""}`}>
          <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(z.ts)}</span>
          <span className="shrink-0">{z.icon}</span>
          <button type="button" onClick={() => onSelect(z.id)}
            className="shrink-0 max-w-[9rem] truncate text-left text-muted hover:text-brand"
            title={tr("dock.select_character_name", { name: name(z.id) })}>
            {name(z.id)}
          </button>
          <span className={`min-w-0 flex-1 break-words ${z.css ?? ""}`}>{z.text}</span>
        </div>
      ))}
    </div>
  );
}

// ── Agenten ─────────────────────────────────────────────────────────────────────────────────

function AgentListing({ entries: entries, scope, filter, selectedId, onSelect }: {
  entries: RosterEntry[];
  scope: Scope;
  filter: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (entries.length === 0) return <Empty text={tr("dock.nobody_room")} />;
  const show = entries.slice(0, AGENT_CAP);
  const now = Date.now();
  return (
    <div className="space-y-1">
      {show.map((r) => {
        const start = r.started_at ? Date.parse(r.started_at) : NaN;
        const ende = r.ended_at ? Date.parse(r.ended_at) : (r.status === "running" ? now : NaN);
        const duration = Number.isFinite(start) && Number.isFinite(ende) ? ende - start : null;
        const from = !fitsToFilter(scope, r, filter);
        return (
          <button key={r.agent_id} type="button" onClick={() => onSelect(r.agent_id)}
            aria-pressed={selectedId === r.agent_id}
            className={"flex w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded border px-2 py-1 text-left text-xs "
              + (selectedId === r.agent_id ? "border-brand bg-brand/5" : "border-transparent hover:border-line")
              + (from ? " opacity-40" : "")}>
            <span className="font-medium">{r.agent || `Lauf ${r.run_id}`}</span>
            {r.phase && <span className="text-muted">{tr(r.phase === "plan" ? "dock.planning" : "dock.execution")}</span>}
            <span className={statusColor(r.status)}>{statusText(r.status)}</span>
            {r.issue_key && <span className="font-mono text-[11px] text-brand">{r.issue_key}</span>}
            <div className="flex-1" />
            <span className="text-muted" title={r.provider ? `${r.provider} · ${r.model ?? "—"}` : undefined}>
              {r.model || "—"}
            </span>
            <span className="text-muted" title={`Eingabe ${number(r.in_tokens)} · Ausgabe ${number(r.out_tokens)}`}>
              {tokenText(r.in_tokens + r.out_tokens)}tok
            </span>
            <span className="text-muted">{usdText(r.cost_usd, r.cost_priced !== true)}</span>
            <span className="text-muted">{durationText(duration)}</span>
          </button>
        );
      })}
      <Capped n={entries.length - show.length} />
    </div>
  );
}

// ── Werkzeuge ───────────────────────────────────────────────────────────────────────────────

/** `ok === null` is **unknown**, not green: with old data nobody measured whether the call went
 *  through. A tick on that would be a claim about data that does not exist. */
function result(ok: boolean | null | undefined): { symbol: string; css: string; title: string } {
  if (ok === undefined) return { symbol: "…", css: "text-muted", title: tr("dock.still_running") };
  if (ok === true) return { symbol: "✓", css: "text-green-400", title: tr("office_room.st_success") };
  if (ok === false) return { symbol: "✕", css: "text-red-400", title: tr("office_room.st_failed") };
  return { symbol: "?", css: "text-muted", title: tr("dock.unknown_old_data_without") };
}

function ToolListing({ rows: lines, name, dimmed, onSelect }: {
  rows: ToolLine[];
  name: (id: string) => string;
  dimmed: (id: string) => boolean;
  onSelect: (id: string) => void;
}) {
  if (lines.length === 0) return <Empty text={tr("dock.no_tool_used_yet")} />;
  const show = lines.slice(-TOOL_CAP);
  return (
    <div className="space-y-1">
      <Capped n={lines.length - show.length} />
      {show.map((z) => {
        const e = result(z.ok);
        return (
          <div key={z.key} className={`flex items-center gap-2 text-xs ${dimmed(z.id) ? "opacity-40" : ""}`}>
            <span className="shrink-0 font-mono text-[11px] text-muted">{uhrText(z.ts)}</span>
            <span className={`shrink-0 ${e.css}`} title={e.title}>{e.symbol}</span>
            <button type="button" onClick={() => onSelect(z.id)}
              className="shrink-0 max-w-[8rem] truncate text-left text-muted hover:text-brand"
              title={tr("dock.select_character_name", { name: name(z.id) })}>
              {name(z.id)}
            </button>
            <span className="shrink-0 font-mono">{z.tool}</span>
            <span className="min-w-0 flex-1 truncate text-muted" title={z.target}>{z.target ?? ""}</span>
            <span className="shrink-0 text-muted">{z.ok === undefined ? "—" : durationText(z.duration)}</span>
          </div>
        );
      })}
    </div>
  );
}
