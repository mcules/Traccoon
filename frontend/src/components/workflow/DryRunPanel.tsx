import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { FlowNode } from "./nodes/shared";
import Steplog, { type Step } from "./StepLog";
import { BUTTON_SMALL, BUTTON_TEXT} from "../ui";

/**
 * Play the flow through before it runs for real.
 *
 * Until now one only noticed at the first live run whether an expression is right and
 * whether the branch leads into the intended path, with all the effects on the outside. The
 * trial run goes through the same graph (real conditions, real templates), but every action
 * only reports what it would do.
 *
 * The example payload from the start node serves as the input: it is there anyway, and
 * whoever maintained it has their test case with it.
 */
export default function DryrunPanel(
  { defId, nodes, graph }: { defId?: number; nodes: FlowNode[]; graph: () => unknown },
) {
  const [runs, setRuns] = useState(false);
  const [error, setError] = useState("");
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [result, setResult] = useState("");

  const start = nodes.find((n) => n.type === "start");
  const probe = (start?.data.config.trigger?.sample ?? {}) as Record<string, unknown>;
  const hatProbe = Object.keys(probe).length > 0;

  const los = async () => {
    if (!defId) return;
    setRuns(true); setError(""); setSteps(null);
    try {
      // The state from the editor, not the saved one; otherwise one checks yesterday's.
      const r = await workflowApi.dryrun(defId, probe, graph());
      setSteps(r.steps);
      const plaintext: Record<string, string> = {
        completed: "durchgelaufen", failed: "abgebrochen", waiting: "wartet",
        running: "dry_run.still_running", cancelled: "dry_run.cancelled",
      };
      setResult(r.error ? `${plaintext[r.status] || r.status} — ${r.error}`
                          : (plaintext[r.status] || r.status));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Probelauf fehlgeschlagen");
    } finally {
      setRuns(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-line p-3 text-xs text-muted">
      <div className="flex items-center gap-2">
        <button
          onClick={los}
          disabled={!defId || runs}
          className={BUTTON_SMALL.secondary}
        >
          {tr(runs ? "dry_run.running" : "dry_run.dry_run")}
        </button>
        {result && <span className="text-[11px]">Ergebnis: <b>{result}</b></span>}
      </div>
      <p className="text-[11px]">
        {hatProbe
          ? tr("dry_run.plays_draft_through_sample")
          : tr("dry_run.without_sample_payload_start")}
      </p>
      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-red-300">{error}</div>
      )}
      {steps && (
        <div className="fixed bottom-4 left-[220px] z-40 w-[440px] rounded-lg border border-line
                        bg-card p-2 shadow-xl">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-ink">
              Probelauf — {steps.length} Schritt{steps.length === 1 ? "" : "e"}
            </span>
            <button onClick={() => setSteps(null)}
              className={BUTTON_TEXT.secondary} title={tr("dry_run_panel.close")}>✕</button>
          </div>
          <Steplog steps={steps} maxHeight="18rem"
            emptyText={tr("dry_run.no_step_ran_flow")} />
        </div>
      )}
    </div>
  );
}
