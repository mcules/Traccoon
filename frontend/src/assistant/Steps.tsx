import { useLayoutEffect, useRef } from "react";
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
  /**
   * Where we last put it ourselves, or −1 for "not yet".
   *
   * The rule is not "is the reader at the bottom" but "has the reader moved".
   * Those differ exactly when it matters: a batch of ten lines arrives at once,
   * the box is suddenly two hundred pixels taller than the last position, and
   * "at the bottom" answers no although nobody touched anything — from then on
   * it stops following and the newest line is off screen for the rest of the
   * run. Comparing against our own last position cannot be fooled by growth.
   */
  const put = useRef(-1);
  const lines = linesOf(steps);

  // Follow the newest line, but only while the reader is at the bottom:
  // scrolling up to read something and being yanked back down two seconds later
  // is worse than not following at all.
  //
  // Before paint, and the "placed" mark only latches once the box can actually
  // scroll. The panel opens as a drawer, so the first fill can arrive while the
  // box has no height yet: scrolling it then does nothing, and a mark set on
  // that measurement would keep it at the top for the rest of the run.
  /** To the bottom, and remember that we were the ones who put it there. */
  const follow = (el: HTMLDivElement) => {
    el.scrollTop = el.scrollHeight;
    put.current = el.scrollTop;
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    // Whether the reader has taken over. Not asked before the observer is set
    // up: the first version returned here, so once the answer was "yes" nothing
    // ever watched the box again and it stayed at the top for the rest of the
    // run.
    const mine = () => put.current < 0 || Math.abs(el.scrollTop - put.current) <= 1;
    if (lines.length && mine()) follow(el);

    // A width of its own is not something a reader can do. The panel slides in
    // from nothing, and a scroll container that was zero pixels wide has its
    // position reset to the top when it gets its width — which reads exactly
    // like somebody scrolling up, and stopped the following for good. So a
    // change of width hands the following back.
    let width = el.clientWidth;
    const watch = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        put.current = -1;
      }
      if (lines.length && mine()) follow(el);
    });
    watch.observe(el);
    const id = requestAnimationFrame(() => { if (lines.length && mine()) follow(el); });
    return () => { cancelAnimationFrame(id); watch.disconnect(); };
  });

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
        <div ref={box} data-assistant="steps"
          className="mt-1.5 max-h-[9.6em] overflow-y-auto border-t border-line pt-1.5
                     text-[0.92em] leading-relaxed">
          {lines.map((l) => (
            <div key={l.key} data-assistant="step" className="flex min-w-0 items-baseline gap-1.5">
              {l.tool ? (
                <>
                  <span data-failed={l.ok === false ? "1" : undefined}
                    className={`w-3.5 shrink-0 ${l.ok === false ? "text-red-400" : "text-brand"}`}>
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
