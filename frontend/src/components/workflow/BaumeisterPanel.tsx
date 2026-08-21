import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowGraph } from "./types";
import { BUTTON_KLEIN, BUTTON_TEXT} from "../ui";

/**
 * "Describe it, I draw it": the entry for everybody who has no graph in their head.
 *
 * The draft lands on the canvas, not in the database: saving and publishing happen by hand
 * as always. And because it replaces the previous state there is an undo; otherwise one
 * attempt would cost the work of the last half hour.
 */
export default function BaumeisterPanel({
  defId,
  graph,
  uebernehmen,
  knotenZahl: nodeZahl,
}: {
  defId?: number;
  graph: () => WorkflowGraph;
  uebernehmen: (g: WorkflowGraph) => void;
  knotenZahl: number;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  // More than a start and an end on the canvas? Then a rebuild is usually meant.
  const [umbauen, setUmbauen] = useState(nodeZahl > 2);
  const [running, setRunning] = useState(false);
  const [erklaerung, setErklaerung] = useState("");
  const [error, setError] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [vorher, setVorher] = useState<WorkflowGraph | null>(null);

  const bauen = async () => {
    if (!defId || !text.trim()) return;
    setRunning(true);
    setErr("");
    setErklaerung("");
    setError([]);
    const alt = graph();
    try {
      const r = await workflowApi.entwurf(defId, text.trim(), umbauen ? alt : undefined);
      if (!r.graph?.nodes?.length) {
        setErr(tr("baumeister.kein_ablauf"));
        return;
      }
      setVorher(alt);
      uebernehmen(r.graph as WorkflowGraph);
      setErklaerung(r.erklaerung || "");
      setError(r.fehler || []);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setRunning(false);
    }
  };

  const back = () => {
    if (!vorher) return;
    uebernehmen(vorher);
    setVorher(null);
    setErklaerung("");
    setError([]);
  };

  if (!open) {
    return (
      <div className="border-t border-line p-3">
        <button onClick={() => setOpen(true)}
          className={BUTTON_KLEIN.neben}>
          ✍ Beschreiben statt bauen
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 border-t border-line p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{tr("baumeister_panel.beschreiben_statt_bauen")}</span>
        <button onClick={() => setOpen(false)} title={tr("baumeister_panel.schliessen")}
          className={BUTTON_TEXT.neben}>✕</button>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={umbauen
          ? tr("baumeister.platzhalter_umbau")
          : tr("baumeister.platzhalter")}
        className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
      />

      <label className="flex items-center gap-2 text-[11px] text-muted">
        <input type="checkbox" checked={umbauen} onChange={(e) => setUmbauen(e.target.checked)} />
        {tr("baumeister.auf_bestand_bauen")}
      </label>

      <div className="flex items-center gap-2">
        <button onClick={bauen} disabled={running || !text.trim() || !defId}
          className={BUTTON_KLEIN.haupt}>
          {running ? "zeichnet…" : "Zeichnen lassen"}
        </button>
        {vorher && (
          <button onClick={back}
            className={BUTTON_KLEIN.neben}>
            {tr("baumeister.zurueck_zum_stand")}
          </button>
        )}
      </div>

      {err && <div className="text-[11px] text-red-300">{err}</div>}
      {erklaerung && (
        <div className="rounded border border-line bg-surface p-2 text-[11px] text-muted">
          {erklaerung}
          <div className="mt-1 opacity-70">
            {tr("baumeister.auf_der_flaeche")}
          </div>
        </div>
      )}
      {error.length > 0 && (
        <div className="text-[11px] text-amber-300">
          {tr("baumeister.fehlende_stellen", { anzahl: error.length })}
        </div>
      )}
    </div>
  );
}
