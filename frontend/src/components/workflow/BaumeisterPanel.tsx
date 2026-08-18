import { useState } from "react";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowGraph } from "./types";

/**
 * „Beschreib es, ich zeichne es" — der Einstieg für alle, die keinen Graphen im Kopf haben.
 *
 * Der Entwurf landet auf der Fläche, nicht in der Datenbank: gespeichert und veröffentlicht
 * wird wie immer von Hand. Und weil er den bisherigen Stand ersetzt, gibt es ein
 * Zurück — sonst kostet ein Versuch die Arbeit der letzten halben Stunde.
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
  // Mehr als Start und Ende auf der Fläche? Dann ist ein Umbau meist gemeint.
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
        setErr("Es kam kein Ablauf zurück — beschreib es etwas genauer.");
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
          className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
          ✍ Beschreiben statt bauen
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2 border-t border-line p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">Beschreiben statt bauen</span>
        <button onClick={() => setOffen(false)} title="schließen"
          className="text-xs text-muted hover:text-ink">✕</button>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={umbauen
          ? "Was soll sich ändern? z. B. „häng vor das Deployment eine Freigabe“"
          : "Was soll der Ablauf tun? z. B. „jede Nacht die offenen Bestellungen holen und mir eine Zusammenfassung schicken“"}
        className="w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
      />

      <label className="flex items-center gap-2 text-[10px] text-muted">
        <input type="checkbox" checked={umbauen} onChange={(e) => setUmbauen(e.target.checked)} />
        Auf dem bauen, was auf der Fläche liegt
      </label>

      <div className="flex items-center gap-2">
        <button onClick={bauen} disabled={laeuft || !text.trim() || !defId}
          className="rounded bg-brand px-2 py-1 text-xs text-white disabled:opacity-50">
          {laeuft ? "zeichnet…" : "Zeichnen lassen"}
        </button>
        {vorher && (
          <button onClick={zurueck}
            className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
            Zurück zum vorherigen Stand
          </button>
        )}
      </div>

      {err && <div className="text-[10px] text-red-300">{err}</div>}
      {erklaerung && (
        <div className="rounded border border-line bg-surface p-2 text-[10px] text-muted">
          {erklaerung}
          <div className="mt-1 opacity-70">
            Auf der Fläche — noch nicht gespeichert. Sieh ihn dir an, bevor du speicherst.
          </div>
        </div>
      )}
      {fehler.length > 0 && (
        <div className="text-[10px] text-amber-300">
          {fehler.length} Stelle{fehler.length === 1 ? "" : "n"} fehlen noch — sie stehen
          unten bei den Validierungsfehlern.
        </div>
      )}
    </div>
  );
}
