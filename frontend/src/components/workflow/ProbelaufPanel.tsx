import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { FlowNode } from "./nodes/shared";
import Schrittprotokoll, { type Schritt } from "./Schrittprotokoll";
import { KNOPF_KLEIN, KNOPF_TEXT} from "../ui";

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
export default function ProbelaufPanel(
  { defId, nodes, graph }: { defId?: number; nodes: FlowNode[]; graph: () => unknown },
) {
  const [läuft, setLäuft] = useState(false);
  const [fehler, setFehler] = useState("");
  const [schritte, setSchritte] = useState<Schritt[] | null>(null);
  const [ergebnis, setErgebnis] = useState("");

  const start = nodes.find((n) => n.type === "start");
  const probe = (start?.data.config.trigger?.sample ?? {}) as Record<string, unknown>;
  const hatProbe = Object.keys(probe).length > 0;

  const los = async () => {
    if (!defId) return;
    setLäuft(true); setFehler(""); setSchritte(null);
    try {
      // The state from the editor, not the saved one; otherwise one checks yesterday's.
      const r = await workflowApi.probelauf(defId, probe, graph());
      setSchritte(r.steps);
      const klartext: Record<string, string> = {
        completed: "durchgelaufen", failed: "abgebrochen", waiting: "wartet",
        running: "probelauf.laeuft_noch", cancelled: "probelauf.abgebrochen",
      };
      setErgebnis(r.error ? `${klartext[r.status] || r.status} — ${r.error}`
                          : (klartext[r.status] || r.status));
    } catch (e) {
      setFehler(e instanceof ApiError ? e.message : "Probelauf fehlgeschlagen");
    } finally {
      setLäuft(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-line p-3 text-xs text-muted">
      <div className="flex items-center gap-2">
        <button
          onClick={los}
          disabled={!defId || läuft}
          className={KNOPF_KLEIN.neben}
        >
          {tr(läuft ? "probelauf.laeuft" : "probelauf.starten")}
        </button>
        {ergebnis && <span className="text-[11px]">Ergebnis: <b>{ergebnis}</b></span>}
      </div>
      <p className="text-[11px]">
        {hatProbe
          ? tr("probelauf.hinweis_mit_nutzlast")
          : tr("probelauf.ohne_beispiel")}
      </p>
      {fehler && (
        <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-red-300">{fehler}</div>
      )}
      {schritte && (
        <div className="fixed bottom-4 left-[220px] z-40 w-[440px] rounded-lg border border-line
                        bg-card p-2 shadow-xl">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium text-ink">
              Probelauf — {schritte.length} Schritt{schritte.length === 1 ? "" : "e"}
            </span>
            <button onClick={() => setSchritte(null)}
              className={KNOPF_TEXT.neben} title={tr("probelauf_panel.schliessen")}>✕</button>
          </div>
          <Schrittprotokoll schritte={schritte} maxHoehe="18rem"
            leerText={tr("probelauf.kein_schritt")} />
        </div>
      )}
    </div>
  );
}
