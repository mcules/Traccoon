import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowGraph } from "./types";
import { BUTTON_SMALL, BUTTON_TEXT} from "../ui";

/**
 * "Describe it, I draw it": the entry for everybody who has no graph in their head.
 *
 * The draft lands on the canvas, not in the database: saving and publishing happen by hand
 * as always. And because it replaces the previous state there is an undo; otherwise one
 * attempt would cost the work of the last half hour.
 */
export default function BuilderPanel({
  defId,
  graph,
  adopt,
  nodeNumber: nodeNumber,
}: {
  defId?: number;
  graph: () => WorkflowGraph;
  adopt: (g: WorkflowGraph) => void;
  nodeNumber: number;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  // More than a start and an end on the canvas? Then a rebuild is usually meant.
  const [rebuild, setRebuild] = useState(nodeNumber > 2);
  const [running, setRunning] = useState(false);
  const [explanation, setExplanation] = useState("");
  const [error, setError] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [before, setBefore] = useState<WorkflowGraph | null>(null);

  const build = async () => {
    if (!defId || !text.trim()) return;
    setRunning(true);
    setErr("");
    setExplanation("");
    setError([]);
    const old = graph();
    try {
      const r = await workflowApi.draft(defId, text.trim(), rebuild ? old : undefined);
      if (!r.graph?.nodes?.length) {
        setErr(tr("builder.no_flow_came_back"));
        return;
      }
      setBefore(old);
      adopt(r.graph as WorkflowGraph);
      setExplanation(r.explanation || "");
      setError(r.error || []);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("common.error"));
    } finally {
      setRunning(false);
    }
  };

  const back = () => {
    if (!before) return;
    adopt(before);
    setBefore(null);
    setExplanation("");
    setError([]);
  };

  if (!open) {
    return (
      <div className="border-t border-line p-3">
        <button onClick={() => setOpen(true)}
          className={BUTTON_SMALL.secondary}>
          {tr("builder_panel.describe_instead")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 border-t border-line p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{tr("builder_panel.describe_instead_build")}</span>
        <button onClick={() => setOpen(false)} title={tr("builder_panel.close")}
          className={BUTTON_TEXT.secondary}>✕</button>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={rebuild
          ? tr("builder.what_change_example_put")
          : tr("builder.what_flow_do_instance")}
        className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
      />

      <label className="flex items-center gap-2 text-[11px] text-muted">
        <input type="checkbox" checked={rebuild} onChange={(e) => setRebuild(e.target.checked)} />
        {tr("builder.build_what_canvas")}
      </label>

      <div className="flex items-center gap-2">
        <button onClick={build} disabled={running || !text.trim() || !defId}
          className={BUTTON_SMALL.primary}>
          {running ? "zeichnet…" : "Zeichnen lassen"}
        </button>
        {before && (
          <button onClick={back}
            className={BUTTON_SMALL.secondary}>
            {tr("builder.back_previous_state")}
          </button>
        )}
      </div>

      {err && <div className="text-[11px] text-red-300">{err}</div>}
      {explanation && (
        <div className="rounded border border-line bg-surface p-2 text-[11px] text-muted">
          {explanation}
          <div className="mt-1 opacity-70">
            {tr("builder.canvas_not_saved_yet")}
          </div>
        </div>
      )}
      {error.length > 0 && (
        <div className="text-[11px] text-amber-300">
          {tr("builder.count_spots_still_missing", { count: error.length })}
        </div>
      )}
    </div>
  );
}
