import { useState } from "react";
import { tr } from "../../i18n";
import { ApiError, workflowApi } from "../../api";
import type { FlowNode } from "./nodes/shared";
import Schrittprotokoll, { type Schritt } from "./Schrittprotokoll";

/**
 * Den Ablauf durchspielen, bevor er echt läuft.
 *
 * Bis hierhin merkte man erst am ersten scharfen Lauf, ob ein Ausdruck stimmt und ob die
 * Weiche in den gedachten Zweig führt — mit allen Wirkungen nach außen. Der Probelauf geht
 * denselben Graphen durch (echte Bedingungen, echte Vorlagen), aber jede Aktion meldet nur,
 * was sie täte.
 *
 * Als Eingabe dient die Beispiel-Nutzlast vom Start-Knoten: sie ist ohnehin schon da, und
 * wer sie gepflegt hat, hat damit auch seinen Testfall.
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
      // Der Stand aus dem Editor, nicht der gespeicherte — sonst prüft man von gestern.
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
          className="rounded border border-line px-2 py-1 text-ink hover:bg-surface disabled:opacity-50"
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
              className="text-muted hover:text-ink" title={tr("probelauf_panel.schliessen")}>✕</button>
          </div>
          <Schrittprotokoll schritte={schritte} maxHoehe="18rem"
            leerText={tr("probelauf.kein_schritt")} />
        </div>
      )}
    </div>
  );
}
