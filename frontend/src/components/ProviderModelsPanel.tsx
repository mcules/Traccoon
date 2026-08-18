import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";

interface Modell {
  id: number; provider: string; model: string; display_name: string;
  price_input: number; price_output: number; price_cache_read: number; enabled: boolean;
  context_tokens: number | null; speed_tps: number | null;
}

const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
const num = "w-24 rounded border border-line bg-surface px-2 py-1 text-right text-sm text-ink";

// Anzeige-Namen der Provider. `openai` ist bei uns meist gar nicht OpenAI, sondern ein
// OpenAI-kompatibler Endpoint (LiteLLM, vLLM, Ollama …) — deshalb die Klammer.
const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "Claude (Subscription/OAuth)",
  codex: "Codex (ChatGPT-Subscription)",
  openai: "OpenAI-kompatibel (API-Key/eigener Endpoint)",
};

/**
 * Modellkatalog: welcher Provider welche Modelle bereitstellt und was sie kosten.
 *
 * Der Katalog speist die Modell-Auswahl im Agent-Editor und die Kostenrechnung
 * (Preise in USD je 1 Mio. Token). „Modelle abrufen" fragt jeden hinterlegten Token
 * an seinem eigenen Endpoint ab — bei einem OpenAI-kompatiblen Proxy kommen also
 * dessen lokale Modellnamen, nicht die von OpenAI.
 */
export default function ProviderModelsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [edit, setEdit] = useState<Record<number, Partial<Modell>>>({});

  const { data: modelle } = useQuery({
    queryKey: ["provider-models"], queryFn: () => api.get<Modell[]>("/providers/models"),
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["provider-models"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const flash = (t: string) => { setNote(t); setTimeout(() => setNote(""), 4000); };

  const speichern = useMutation({
    mutationFn: (m: Modell) => api.put("/providers/models", {
      provider: m.provider, model: m.model, display_name: m.display_name,
      price_input: m.price_input, price_output: m.price_output,
      price_cache_read: m.price_cache_read, enabled: m.enabled,
      context_tokens: m.context_tokens, speed_tps: m.speed_tps,
    }),
    onSuccess: (_r, m) => { setErr(""); setEdit((e) => { const n = { ...e }; delete n[m.id]; return n; }); inv(); },
    onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/providers/models/${id}`),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const preise = useMutation({
    mutationFn: () => api.post<any>("/providers/models/prices"),
    onSuccess: (r) => {
      const n = r.updated?.length ?? 0;
      const namen = (r.updated || []).slice(0, 4).map((u: any) => u.model).join(", ");
      const kontext = r.context_set ? ` · ${r.context_set} Kontextfenster gesetzt` : "";
      flash((n
        ? `${n} Preis(e) aus models.dev übernommen: ${namen}${n > 4 ? " …" : ""}`
        : "Alle Preise aktuell")
        + kontext
        + (r.unknown?.length ? ` · ${r.unknown.length} ohne Eintrag (unverändert)` : ""));
      setErr(""); inv();
    },
    onError: fail,
  });
  const abrufen = useMutation({
    mutationFn: () => api.post<Record<string, any>>("/providers/models/fetch"),
    onSuccess: (r) => {
      const teile = Object.entries(r).map(([label, v]: [string, any]) =>
        v.error ? `${label}: Fehler (${v.error})`
          : `${label}: ${v.total ?? 0}${v.added ? ` (+${v.added} neu)` : ""}${v.disabled ? ` (${v.disabled} deaktiviert)` : ""}`);
      flash(teile.length ? teile.join(" · ") : "Keine Provider-Tokens hinterlegt.");
      setErr(""); inv();
    },
    onError: fail,
  });

  // Zeile = gespeicherter Stand, überlagert von noch nicht gespeicherten Eingaben.
  const zeile = (m: Modell): Modell => ({ ...m, ...edit[m.id] });
  const setzen = (m: Modell, feld: Partial<Modell>) =>
    setEdit((e) => ({ ...e, [m.id]: { ...e[m.id], ...feld } }));
  const geaendert = (m: Modell) => edit[m.id] !== undefined;

  const provider = [...new Set((modelle || []).map((m) => m.provider))].sort();

  return (
    <div className="space-y-4">
      {err && <div className="rounded border border-red-400/40 bg-red-400/10 px-2 py-1 text-sm text-red-400">{err}</div>}
      {note && <div className="rounded border border-line bg-card px-2 py-1 text-sm text-muted">{note}</div>}

      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-muted">
          Welcher Provider welche Modelle bereitstellt. Preise in <b>USD je 1 Mio. Token</b> —
          sie bestimmen die Kostenrechnung der Läufe; <b>0</b> heißt „zählt nichts" (z. B. lokale
          Modelle). <b>{tr("provider_models_panel.kontext")}</b> ist das größte Fenster in Tokens, <b>≈ t/s</b> die
          gemessene Ausgabegeschwindigkeit — bei lokalen Modellen ist genau das der
          Auswahlgrund, denn der Preis ist dort 0. Deaktivierte Modelle verschwinden aus der
          Auswahl im Agent-Editor, bleiben aber für die Abrechnung alter Läufe erhalten.
          <b> {tr("provider_models_panel.modelle_abrufen")}</b> fragt deine Endpoints, <b>{tr("provider_models_panel.preise")}</b> holt Preise und
          Kontextfenster aus models.dev — lokale Modelle stehen dort nicht und bleiben
          unangetastet, deren t/s misst du selbst.
        </p>
        <div className="flex shrink-0 gap-2">
          <button onClick={() => abrufen.mutate()} disabled={abrufen.isPending}
            className="rounded border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface disabled:opacity-50">
            {abrufen.isPending ? "Lädt…" : "↻ Modelle abrufen"}
          </button>
          <button onClick={() => preise.mutate()} disabled={preise.isPending} title={tr("provider_models_panel.preise_aus_dem_offenen_katalog_models_de")}
            className="rounded border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface disabled:opacity-50">
            {preise.isPending ? "Lädt…" : "💲 Preise (models.dev)"}
          </button>
        </div>
      </div>

      {provider.map((p) => (
        <div key={p} className="rounded-lg border border-line bg-card p-4">
          <div className="mb-2 text-sm font-semibold text-ink">{PROVIDER_LABEL[p] || p}
            <span className="ml-2 font-mono text-xs font-normal text-muted">{p}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase text-muted">
                  <th className="py-2">{tr("provider_models_panel.modell_id")}</th><th>{tr("provider_models_panel.anzeigename")}</th>
                  <th className="text-right">{tr("provider_models_panel.input")}</th><th className="text-right">{tr("provider_models_panel.output")}</th>
                  <th className="text-right">{tr("provider_models_panel.cache_read")}</th>
                  <th className="text-right">{tr("provider_models_panel.kontext")}</th><th className="text-right">≈ t/s</th>
                  <th className="text-center">{tr("provider_models_panel.aktiv")}</th><th />
                </tr>
              </thead>
              <tbody>
                {(modelle || []).filter((m) => m.provider === p).map((roh) => {
                  const m = zeile(roh);
                  return (
                    <tr key={m.id} className={`border-b border-line ${m.enabled ? "" : "opacity-50"}`}>
                      <td className="py-2 pr-2 font-mono text-xs">{m.model}</td>
                      <td className="pr-2">
                        <input value={m.display_name} className={inp}
                          onChange={(e) => setzen(roh, { display_name: e.target.value })} />
                      </td>
                      <td className="pr-2">
                        <input type="number" step="0.01" min="0" value={m.price_input} className={num}
                          onChange={(e) => setzen(roh, { price_input: Number(e.target.value) })} />
                      </td>
                      <td className="pr-2">
                        <input type="number" step="0.01" min="0" value={m.price_output} className={num}
                          onChange={(e) => setzen(roh, { price_output: Number(e.target.value) })} />
                      </td>
                      <td className="pr-2">
                        <input type="number" step="0.01" min="0" value={m.price_cache_read} className={num}
                          onChange={(e) => setzen(roh, { price_cache_read: Number(e.target.value) })} />
                      </td>
                      <td className="pr-2">
                        <input type="number" step="1024" min="0" value={m.context_tokens ?? ""}
                          placeholder="—" className={num} title={tr("provider_models_panel.maximales_kontextfenster_in_tokens")}
                          onChange={(e) => setzen(roh, { context_tokens: e.target.value ? Number(e.target.value) : null })} />
                      </td>
                      <td className="pr-2">
                        <input type="number" step="1" min="0" value={m.speed_tps ?? ""}
                          placeholder="—" className={num} title={tr("provider_models_panel.gemessene_ausgabegeschwindigkeit_tokens_")}
                          onChange={(e) => setzen(roh, { speed_tps: e.target.value ? Number(e.target.value) : null })} />
                      </td>
                      <td className="text-center">
                        <input type="checkbox" checked={m.enabled}
                          onChange={(e) => setzen(roh, { enabled: e.target.checked })} />
                      </td>
                      <td className="whitespace-nowrap py-2 text-right">
                        <button onClick={() => speichern.mutate(m)} disabled={!geaendert(roh)}
                          className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface disabled:opacity-30">
                          Speichern
                        </button>
                        <button onClick={() => { if (confirm(`${m.model} aus dem Katalog löschen?`)) loeschen.mutate(m.id); }}
                          className="ml-1 rounded border border-line px-2 py-1 text-xs text-red-400 hover:bg-surface">
                          ✕
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {modelle && modelle.length === 0 && (
        <div className="text-sm text-muted">
          Katalog leer — „Modelle abrufen" holt sie von den Endpoints deiner Provider-Tokens.
        </div>
      )}
    </div>
  );
}
