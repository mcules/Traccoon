import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowGraph } from "./types";
import { KNOPF_KLEIN, KNOPF_TEXT} from "../ui";

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
  knotenZahl,
}: {
  defId?: number;
  graph: () => WorkflowGraph;
  uebernehmen: (g: WorkflowGraph) => void;
  knotenZahl: number;
}) {
  const [offen, setOffen] = useState(false);
  const [text, setText] = useState("");
  // More than a start and an end on the canvas? Then a rebuild is usually meant.
  const [umbauen, setUmbauen] = useState(knotenZahl > 2);
  const [laeuft, setLaeuft] = useState(false);
  const [erklaerung, setErklaerung] = useState("");
  const [fehler, setFehler] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [vorher, setVorher] = useState<WorkflowGraph | null>(null);

  const bauen = async () => {
    if (!defId || !text.trim()) return;
    setLaeuft(true);
    setErr("");
    setErklaerung("");
    setFehler([]);
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
      setFehler(r.fehler || []);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Fehler");
    } finally {
      setLaeuft(false);
    }
  };

  const zurueck = () => {
    if (!vorher) return;
    uebernehmen(vorher);
    setVorher(null);
    setErklaerung("");
    setFehler([]);
  };

  if (!offen) {
    return (
      <div className="border-t border-line p-3">
        <button onClick={() => setOffen(true)}
          className={KNOPF_KLEIN.neben}>
          ✍ Beschreiben statt bauen
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 border-t border-line p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{tr("baumeister_panel.beschreiben_statt_bauen")}</span>
        <button onClick={() => setOffen(false)} title={tr("baumeister_panel.schliessen")}
          className={KNOPF_TEXT.neben}>✕</button>
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
        <button onClick={bauen} disabled={laeuft || !text.trim() || !defId}
          className={KNOPF_KLEIN.haupt}>
          {laeuft ? "zeichnet…" : "Zeichnen lassen"}
        </button>
        {vorher && (
          <button onClick={zurueck}
            className={KNOPF_KLEIN.neben}>
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
      {fehler.length > 0 && (
        <div className="text-[11px] text-amber-300">
          {tr("baumeister.fehlende_stellen", { anzahl: fehler.length })}
        </div>
      )}
    </div>
  );
}
