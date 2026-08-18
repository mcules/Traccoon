import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../../../i18n";
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
  subjectKind,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** Definition, zu der dieser Start-Knoten gehört — für die eingehende Adresse. */
  defId?: number;
  /** Subjekt des Ablaufs — bestimmt, ob ein Artefakt benannt werden muss. */
  subjectKind?: string;
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
  // Drei Arten, einen Ablauf zu starten. Sie schließen sich aus: eine eingehende Adresse
  // an einem Ereignis-Auslöser wäre eine zweite Tür, die niemand benutzt — und sie stünde
  // im Weg, wenn man nur sehen will, worauf der Ablauf hört.
  // Die Art steht ausdrücklich in der Konfiguration, statt aus dem Inhalt geraten zu
  // werden: sonst fällt „Ereignis" beim Umschalten sofort auf „von Hand" zurück, solange
  // noch kein Ereignisname eingetragen ist — man wählt etwas, und es passiert scheinbar
  // nichts. (`t.event` gilt weiterhin als Ereignis, das ist der Bestand.)
  const art: "manuell" | "ereignis" | "webhook" =
    t.kind === "webhook" ? "webhook" : (t.kind === "ereignis" || t.event) ? "ereignis" : "manuell";
  const setArt = (neu: typeof art) => {
    const rest = { ...t };
    delete rest.event; delete rest.project_id; delete rest.filter; delete rest.kind;
    if (neu !== "manuell") rest.kind = neu;
    onChange({
      ...config,
      trigger: Object.keys(rest).length ? (rest as NodeConfig["trigger"]) : undefined,
    });
  };
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
        {tr("start_config.wodurch_startet")}
        <select value={art} onChange={(e) => setArt(e.target.value as typeof art)}
          className={`mt-1 ${inp}`}>
          <option value="manuell">{tr("start_config.von_hand")}</option>
          <option value="ereignis">{tr("start_config.ereignis_in_traccoon")}</option>
          <option value="webhook">{tr("start_config.aufruf_von_aussen_webhook")}</option>
        </select>
      </label>

      {art === "ereignis" && (
      <label className="block text-xs font-medium text-muted">
        {tr("start_config.ausloeser")}
        <input
          list="ereignisse"
          value={t.event || ""}
          onChange={(e) => setT({ event: e.target.value.trim() })}
          placeholder={tr("start_config.kein_ausloeser_nur_manueller_start")}
          className={`mt-1 ${inp}`}
        />
        <datalist id="ereignisse">
          {events?.map((e) => (
            <option key={e.event} value={e.event}>{e.label}</option>
          ))}
        </datalist>
        <span className="mt-1 block text-[11px] text-muted">
          Eigene Namen sind erlaubt — ein Webhook im Modus <b>{tr("start_config.ereignis")}</b> oder
          <code className="mx-1 rounded bg-surface px-1">POST /api/events</code> meldet sie.
        </span>
      </label>
      )}

      {/* ── Webhook als Quelle ─────────────────────────────────────────── */}
      {art !== "ereignis" && (
      <div className="rounded border border-line bg-surface p-2">
        <div className="mb-1 text-xs font-medium text-muted">{tr("start_config.eingehende_adresse_webhook")}</div>
        {hook ? (
          <div className="space-y-1">
            <div className="break-all font-mono text-[11px] text-ink">{hook.url}</div>
            <div className="text-[11px] text-muted">
              Signatur: <code className="rounded bg-card px-1">X-Webhook-Signature</code> =
              HMAC-SHA256 des Rumpfes, hex, ohne Präfix. Geheimnis:{" "}
              <code className="break-all rounded bg-card px-1">{hook.secret}</code>
            </div>
            <div className="text-[11px] text-muted">
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

        {subjectKind && subjectKind !== "standalone" && (
          <label className="mt-2 block text-[11px] text-muted">
            {tr("start_config.artefakt_feld")}
            <input
              value={t.subjekt_feld || ""}
              onChange={(e) => setT({ subjekt_feld: e.target.value.trim() })}
              placeholder={subjectKind === "issue" ? "vorgang.ticket" : "geraet.id"}
              className={`mt-1 font-mono ${inp}`}
            />
            <span className="mt-1 block">
              {subjectKind === "issue"
                ? "Ticket-Kennung (ABC-31) oder Ticket-Nummer."
                : "Nummer des Exemplars."}{" "}
              Ohne diese Angabe startet der Ablauf nicht — er hängt an einem Artefakt, und
              das fremde System muss sagen, an welchem.
            </span>
          </label>
        )}
      </div>
      )}

      {art !== "ereignis" && (
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
        <span className="mt-1 block text-[11px] text-muted">
          Einmal einfügen, was das fremde System schickt — die Felder daraus stehen danach
          in jeder Verzweigung zur Auswahl.
        </span>
      </label>
      )}

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
            <span className="mt-1 block text-[11px] text-muted">
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
            <span className="mt-1 block text-[11px] text-muted">
              Leer = immer. Geprüft wird der Inhalt des Ereignisses (im Ablauf als
              <code className="mx-1 rounded bg-surface px-1">{"{{…}}"}</code> verfügbar).
            </span>
          </label>
        </>
      )}
    </div>
  );
}
