import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, workflowApi } from "../../../api";
import type { NodeConfig } from "../types";

interface ProjektLite { id: number; key: string; name: string }

/**
 * Auslöser eines Ablaufs.
 *
 * Zwei Quellen, beide hier einstellbar:
 *
 * **Ereignis** — Traccoon meldet etwas („Ticket angelegt", „Mail eingegangen"), und der
 * Ablauf entscheidet selbst, ob er darauf hört. So hängen an einem Ereignis beliebig viele
 * Abläufe, ohne dass der Auslöser sie kennen muss.
 *
 * **Webhook** — für alles, was von außen kommt und weder MCP noch Traccoons Ereignisse
 * kennt: der Ablauf bekommt eine eigene Adresse. Vorher gab es die zwar (Einstellungen →
 * Webhooks, Modus `workflow`), aber am anderen Ende: im Ablauf selbst war seine Quelle
 * unsichtbar. Die Beispiel-Nutzlast daneben ist mehr als Doku — aus ihr entstehen die
 * Kontextfelder, die die Verzweigungen zur Auswahl anbieten.
 */
export default function StartConfig({
  config,
  onChange,
  defId,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** Definition, zu der dieser Start-Knoten gehört — für die eingehende Adresse. */
  defId?: number;
}) {
  const qc = useQueryClient();
  const { data: hook } = useQuery({
    queryKey: ["workflow-webhook", defId],
    queryFn: () => workflowApi.webhookGet(defId as number),
    enabled: !!defId,
  });
  const adresseAnlegen = useMutation({
    mutationFn: () => workflowApi.webhookCreate(defId as number),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow-webhook", defId] }),
  });
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
    // Ein Trigger ohne Ereignis ist kein Ereignis-Trigger — aber die Beispiel-Nutzlast
    // eines Webhooks lebt hier ebenfalls, die darf nicht mit verschwinden.
    const leer = !zusammen.event && !zusammen.sample;
    onChange({
      ...config,
      trigger: leer ? undefined : (zusammen as NodeConfig["trigger"]),
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

      {/* ── Webhook als Quelle ─────────────────────────────────────────── */}
      <div className="rounded border border-line bg-surface p-2">
        <div className="mb-1 text-xs font-medium text-muted">Eingehende Adresse (Webhook)</div>
        {hook ? (
          <div className="space-y-1">
            <div className="break-all font-mono text-[11px] text-ink">{hook.url}</div>
            <div className="text-[10px] text-muted">
              Signatur: <code className="rounded bg-card px-1">X-Webhook-Signature</code> =
              HMAC-SHA256 des Rumpfes, hex, ohne Präfix. Geheimnis:{" "}
              <code className="break-all rounded bg-card px-1">{hook.secret}</code>
            </div>
            <div className="text-[10px] text-muted">
              Die Nutzlast landet vollständig im Kontext. Feiner abbilden (nur bestimmte
              Felder) lässt sich das unter Einstellungen → Webhooks.
            </div>
          </div>
        ) : (
          <button
            onClick={() => adresseAnlegen.mutate()}
            disabled={!defId || adresseAnlegen.isPending}
            className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink disabled:opacity-50"
          >
            {adresseAnlegen.isPending ? "…" : "+ Adresse erzeugen"}
          </button>
        )}
      </div>

      <label className="block text-xs font-medium text-muted">
        Beispiel-Nutzlast (JSON)
        <textarea
          rows={4}
          value={t.sample ? JSON.stringify(t.sample, null, 1) : ""}
          onChange={(e) => {
            const roh = e.target.value.trim();
            if (!roh) return setT({ sample: "" });
            try { setT({ sample: JSON.parse(roh) }); } catch { /* Tippen abwarten */ }
          }}
          placeholder={'{"vorgang": {"id": 42, "titel": "Störung"}, "quelle": "Zabbix"}'}
          className={`mt-1 ${inp} font-mono`}
        />
        <span className="mt-1 block text-[10px] text-muted">
          Einmal einfügen, was das fremde System schickt — die Felder daraus stehen danach
          in jeder Verzweigung zur Auswahl.
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
