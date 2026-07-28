import { useQuery } from "@tanstack/react-query";
import { api } from "../../../api";
import type { NodeConfig } from "../types";

interface ProjektLite { id: number; key: string; name: string }

/**
 * Auslöser eines Ablaufs.
 *
 * Statt einen Ablauf von außen fest zu verdrahten (Webhook oder Job zeigt auf eine
 * bestimmte Definition), meldet Traccoon ein **Ereignis** — und der Ablauf entscheidet
 * hier selbst, ob er darauf hört. So hängen an einem Ereignis beliebig viele Abläufe, und
 * ein Projekt kann einen eigenen danebenstellen, ohne den Auslöser anzufassen.
 */
export default function StartConfig({
  config,
  onChange,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
}) {
  const { data: events } = useQuery({
    queryKey: ["workflow-events"],
    queryFn: () => api.get<{ event: string; label: string }[]>("/workflow-events"),
    staleTime: 10 * 60_000,
  });
  const { data: projekte } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<ProjektLite[]>("/projects"),
    staleTime: 5 * 60_000,
  });

  const t: Record<string, any> = config.trigger || {};
  const setT = (next: Record<string, any>) => {
    const zusammen: Record<string, any> = { ...t, ...next };
    // Leere Angaben nicht mitschleppen — ein Trigger ohne Ereignis ist keiner.
    for (const k of Object.keys(zusammen)) {
      if (zusammen[k] === "" || zusammen[k] === undefined || zusammen[k] === null) {
        delete zusammen[k];
      }
    }
    onChange({
      ...config,
      trigger: zusammen.event ? (zusammen as NodeConfig["trigger"]) : undefined,
    });
  };
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        Auslöser
        <input
          list="ereignisse"
          value={t.event || ""}
          onChange={(e) => setT({ event: e.target.value.trim() })}
          placeholder="kein Auslöser — nur manueller Start"
          className={`mt-1 ${inp}`}
        />
        <datalist id="ereignisse">
          {events?.map((e) => (
            <option key={e.event} value={e.event}>{e.label}</option>
          ))}
        </datalist>
        <span className="mt-1 block text-[10px] text-muted">
          Eigene Namen sind erlaubt — ein Webhook im Modus <b>Ereignis</b> oder
          <code className="mx-1 rounded bg-surface px-1">POST /api/events</code> meldet sie.
        </span>
      </label>

      {t.event && (
        <>
          <label className="block text-xs font-medium text-muted">
            Nur für dieses Projekt
            <select
              value={t.project_id ?? ""}
              onChange={(e) => setT({ project_id: e.target.value ? Number(e.target.value) : "" })}
              className={`mt-1 ${inp}`}
            >
              <option value="">— jedes Projekt —</option>
              {projekte?.map((p) => (
                <option key={p.id} value={p.id}>{p.key} · {p.name}</option>
              ))}
            </select>
            <span className="mt-1 block text-[10px] text-muted">
              Gehört der Ablauf schon zu einem Projekt, gilt das ohnehin — das hier grenzt
              zusätzlich ein (z. B. ein systemweiter Ablauf nur für ein bestimmtes Projekt).
            </span>
          </label>

          <label className="block text-xs font-medium text-muted">
            Nur wenn (Bedingung, JSONLogic)
            <textarea
              rows={3}
              value={t.filter ? JSON.stringify(t.filter, null, 1) : ""}
              onChange={(e) => {
                const roh = e.target.value.trim();
                if (!roh) return setT({ filter: "" });
                try { setT({ filter: JSON.parse(roh) }); } catch { /* Tippen abwarten */ }
              }}
              placeholder={'{"==": [{"var": "issue.priority"}, "highest"]}'}
              className={`mt-1 ${inp} font-mono`}
            />
            <span className="mt-1 block text-[10px] text-muted">
              Leer = immer. Geprüft wird der Inhalt des Ereignisses (im Ablauf als
              <code className="mx-1 rounded bg-surface px-1">{"{{…}}"}</code> verfügbar).
            </span>
          </label>
        </>
      )}
    </div>
  );
}
