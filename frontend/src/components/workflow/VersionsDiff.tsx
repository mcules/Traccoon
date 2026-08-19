import { useQuery } from "@tanstack/react-query";
import { tr } from "../../i18n";
import { workflowApi, type WfDiff, type WfDiffKnoten } from "../../api";
import { Dialog, Fehlerzeile } from "../ui";

/**
 * Was sich zwischen zwei Fassungen geändert hat.
 *
 * Bewusst kein Textvergleich über das JSON: eine verrutschte Klammer beantwortet nicht die
 * Frage „was macht der Ablauf jetzt anders". Verglichen wird nach Knoten und Kanten, und ein
 * geänderter Knoten sagt, WELCHE seiner Einstellungen anders ist — das ist die Zeile, nach
 * der man sucht. Die Anordnung bleibt außen vor; sie ist keine Fassung wert und deshalb auch
 * kein Unterschied.
 */
export default function VersionsDiff({ defId, versionId, gegen, titel, onClose }: {
  defId: number; versionId: number; gegen?: number; titel: string; onClose: () => void;
}) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["wf-diff", defId, versionId, gegen ?? null],
    queryFn: () => workflowApi.diff(defId, versionId, gegen),
  });

  return (
    <Dialog breit titel={titel} onClose={onClose}>
      {isLoading && <p className="text-sm text-muted">{tr("common.laedt")}</p>}
      <Fehlerzeile text={error ? String((error as Error).message || error) : ""} />
      {data && <DiffInhalt d={data} />}
    </Dialog>
  );
}

function DiffInhalt({ d }: { d: WfDiff }) {
  if (d.gleich) {
    return (
      <p className="text-sm text-muted">
        {tr("diff.gleich")}
      </p>
    );
  }
  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-muted">
        {d.von === null
          ? tr("diff.gegen_nichts", { bis: d.bis })
          : tr("diff.von_bis", { von: d.von, bis: d.bis })}
      </p>

      <Gruppe titel={tr("diff.knoten_neu")} eintraege={d.knoten_neu} farbe="text-green-400" />
      <Gruppe titel={tr("diff.knoten_weg")} eintraege={d.knoten_weg} farbe="text-red-400" />

      {d.knoten_geaendert.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
            {tr("diff.knoten_geaendert")}
          </div>
          <div className="space-y-2">
            {d.knoten_geaendert.map((k) => (
              <div key={k.id} className="rounded border border-line bg-surface p-2">
                <div className="mb-1 flex flex-wrap items-baseline gap-2">
                  <span className="font-medium text-ink">{k.label || k.id}</span>
                  <span className="font-mono text-xs text-muted">{k.id}</span>
                </div>
                <div className="space-y-1.5">
                  {k.felder.map((f) => (
                    <div key={f.feld}>
                      <div className="font-mono text-[11px] text-muted">{f.feld}</div>
                      {/* Zwei Zeilen statt einer: bei langen Werten sucht man sonst die
                          Stelle, an der sie auseinandergehen, mitten im Fließtext. */}
                      <div className="mt-0.5 break-all rounded bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-300">
                        − {f.vorher || "—"}
                      </div>
                      <div className="mt-0.5 break-all rounded bg-green-500/10 px-1.5 py-0.5 text-[11px] text-green-300">
                        + {f.nachher || "—"}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <Kanten titel={tr("diff.kanten_neu")} texte={d.kanten_neu} farbe="text-green-400" />
      <Kanten titel={tr("diff.kanten_weg")} texte={d.kanten_weg} farbe="text-red-400" />
    </div>
  );
}

function Gruppe({ titel, eintraege, farbe }: {
  titel: string; eintraege: WfDiffKnoten[]; farbe: string;
}) {
  if (eintraege.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">{titel}</div>
      <ul className="space-y-0.5">
        {eintraege.map((k) => (
          <li key={k.id} className={`text-sm ${farbe}`}>
            {k.label || k.id} <span className="font-mono text-xs opacity-70">{k.id}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Kanten({ titel, texte, farbe }: { titel: string; texte: string[]; farbe: string }) {
  if (texte.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">{titel}</div>
      <ul className="space-y-0.5">
        {texte.map((t) => <li key={t} className={`font-mono text-xs ${farbe}`}>{t}</li>)}
      </ul>
    </div>
  );
}
