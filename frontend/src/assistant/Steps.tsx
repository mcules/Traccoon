import { useEffect, useRef } from "react";
import { tr } from "../i18n";
import type { Step } from "./api";

/**
 * What the assistant is doing while it does it.
 *
 * A running message used to be a spinner and a stopwatch: you could see that
 * something was happening and nothing about what, for minutes at a time. The
 * run writes every step down anyway — the same ones the console prints — so
 * they belong where the person is waiting.
 *
 * Six lines stand open and the rest scrolls, because the panel is narrow and a
 * run of two hundred steps would otherwise push the conversation off the
 * screen. The newest is at the bottom and stays in view, the way a log does.
 */

type Line = {
  key: string;
  tool?: string;
  label?: string;
  ok?: boolean | null;
  ms?: number | null;
  text?: string;
};

/** Steps into lines. A tool's result belongs to the call it answers, so it
 *  marks that line rather than making one of its own — otherwise every call
 *  would take two lines and the six that fit would show three calls. */
export function linesOf(steps: Step[]): Line[] {
  const out: Line[] = [];
  for (const s of steps) {
    if (s.kind === "agent_text") {
      if (s.text.trim()) out.push({ key: `t${s.seq}`, text: s.text.trim() });
      continue;
    }
    if (s.kind === "tool_start") {
      out.push({ key: `s${s.seq}`, tool: s.tool, label: s.label, ok: null, ms: null });
      continue;
    }
    if (s.kind === "tool_result") {
      // The nearest call of the same tool that has not been answered yet.
      for (let i = out.length - 1; i >= 0; i--) {
        if (out[i].tool === s.tool && out[i].ok === null) {
          out[i] = { ...out[i], ok: s.ok, ms: s.ms };
          break;
        }
      }
    }
  }
  return out;
}

export default function Steps({ steps, name, since }: {
  steps: Step[];
  name: string;
  since: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  // Whether the box has ever been put where it belongs. The first fill arrives
  // as a full list, so the box is already scrollable and standing at the top —
  // "is the reader at the bottom" would answer no and the newest line would
  // never come into view at all.
  const placed = useRef(false);
  const lines = linesOf(steps);

  // Follow the newest line, but only while the reader is at the bottom:
  // scrolling up to read something and being yanked back down two seconds later
  // is worse than not following at all.
  useEffect(() => {
    const el = box.current;
    if (!el || !lines.length) return;
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (!placed.current || atEnd) {
      el.scrollTop = el.scrollHeight;
      placed.current = true;
    }
  }, [steps.length, lines.length]);

  return (
    <div className="w-[95%] self-start rounded-lg rounded-bl-sm border border-line bg-surface
                    px-2.5 py-2 text-sm text-muted">
      <div className="flex items-center gap-1.5">
        <span className="inline-block animate-spin">↻</span>
        {tr("notes_assistant.thinking", { name })}
        {since}
      </div>
      {lines.length > 0 && (
        // Sechs Zeilen mal Zeilenhoehe, in em: die Zahl der sichtbaren Zeilen
        // bleibt, wenn jemand die Schrift groesser stellt.
        <div ref={box}
          className="mt-1.5 max-h-[9.6em] overflow-y-auto border-t border-line pt-1.5
                     text-[0.92em] leading-relaxed">
          {lines.map((l) => (
            <div key={l.key} className="flex min-w-0 items-baseline gap-1.5">
              {l.tool ? (
                <>
                  <span className={`w-3.5 shrink-0 ${l.ok === false ? "text-red-400" : "text-brand"}`}>
                    {l.ok === null ? "·" : l.ok ? "✓" : "✗"}
                  </span>
                  <span className="shrink-0 font-mono text-[0.95em]">{l.tool}</span>
                  {l.label && (
                    <span className="min-w-0 truncate text-muted/70">{l.label}</span>
                  )}
                </>
              ) : (
                <span className="break-words text-ink">{l.text}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
