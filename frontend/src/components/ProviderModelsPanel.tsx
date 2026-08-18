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

// Display names of the providers. `openai` is mostly not OpenAI here but an
// OpenAI-compatible endpoint (LiteLLM, vLLM, Ollama …), hence the bracket.
const PROVIDER_LABEL: Record<string, string> = {
  claude_code: "provider_models_panel.provider_claude_code",
  codex: "provider_models_panel.provider_codex",
  openai: "provider_models_panel.provider_openai",
};

/**
 * Model catalog: which provider provides which models and what they cost.
 *
 * The catalog feeds the model selection in the agent editor and the cost computation
 * (prices in USD per 1M tokens). "Fetch models" queries every stored token
 * queries every stored token at its own endpoint, so with an OpenAI-compatible proxy its
 * local model names come, not those of OpenAI.
 */
export default function ProviderModelsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [edit, setEdit] = useState<Record<number, Partial<Modell>>>({});
  // On a phone only the name stands per model at first: 30 models with seven fields each
  // would otherwise be ten screens of scrolling before one finds the one being looked for.
  const [offen, setOffen] = useState<number | null>(null);

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
        v.error ? `${label}: ${tr("common.fehler")} (${v.error})`
          : `${label}: ${v.total ?? 0}${v.added ? ` (+${v.added} ${tr("provider_models_panel.neu")})` : ""}`
            + `${v.disabled ? ` (${v.disabled} ${tr("provider_models_panel.deaktiviert")})` : ""}`);
      flash(teile.length ? teile.join(" · ") : tr("provider_models_panel.keine_tokens"));
      setErr(""); inv();
    },
    onError: fail,
  });

  // Row = the saved state, overlaid by inputs not saved yet.
  const zeile = (m: Modell): Modell => ({ ...m, ...edit[m.id] });
  const setzen = (m: Modell, feld: Partial<Modell>) =>
    setEdit((e) => ({ ...e, [m.id]: { ...e[m.id], ...feld } }));
  const geaendert = (m: Modell) => edit[m.id] !== undefined;

  /**
   * The number fields of a model row, described once.
   *
   * On the desktop they stand side by side as table columns, on a phone as labelled fields
   * below each other: nine columns on 390 px were previously either cut off or so narrow
   * that every cell held one word per line. Two presentations, one source, so that they do
   * not drift apart.
   */
  const zahlenFelder = (roh: Modell) => {
    const m = zeile(roh);
    return [
      { key: "price_input", label: tr("provider_models_panel.input"),
        node: <input type="number" step="0.01" min="0" value={m.price_input} className={num}
                onChange={(e) => setzen(roh, { price_input: Number(e.target.value) })} /> },
      { key: "price_output", label: tr("provider_models_panel.output"),
        node: <input type="number" step="0.01" min="0" value={m.price_output} className={num}
                onChange={(e) => setzen(roh, { price_output: Number(e.target.value) })} /> },
      { key: "price_cache_read", label: tr("provider_models_panel.cache_read"),
        node: <input type="number" step="0.01" min="0" value={m.price_cache_read} className={num}
                onChange={(e) => setzen(roh, { price_cache_read: Number(e.target.value) })} /> },
      { key: "context_tokens", label: tr("provider_models_panel.kontext"),
        node: <input type="number" step="1024" min="0" value={m.context_tokens ?? ""}
                placeholder="—" className={num} title={tr("provider_models_panel.maximales_kontextfenster_in_tokens")}
                onChange={(e) => setzen(roh, { context_tokens: e.target.value ? Number(e.target.value) : null })} /> },
      { key: "speed_tps", label: "≈ t/s",
        node: <input type="number" step="1" min="0" value={m.speed_tps ?? ""}
                placeholder="—" className={num} title={tr("provider_models_panel.gemessene_ausgabegeschwindigkeit_tokens_")}
                onChange={(e) => setzen(roh, { speed_tps: e.target.value ? Number(e.target.value) : null })} /> },
    ];
  };

  const provider = [...new Set((modelle || []).map((m) => m.provider))].sort();

  return (
    <div className="space-y-4">
      {err && <div className="rounded border border-red-400/40 bg-red-400/10 px-2 py-1 text-sm text-red-400">{err}</div>}
      {note && <div className="rounded border border-line bg-card px-2 py-1 text-sm text-muted">{note}</div>}

      <div className="flex flex-col items-start justify-between gap-2 sm:flex-row sm:gap-4">
        <p className="text-sm text-muted">{tr("provider_models_panel.einleitung")}</p>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button onClick={() => abrufen.mutate()} disabled={abrufen.isPending}
            className="rounded border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface disabled:opacity-50">
            {abrufen.isPending ? tr("common.laedt") : `↻ ${tr("provider_models_panel.modelle_abrufen")}`}
          </button>
          <button onClick={() => preise.mutate()} disabled={preise.isPending} title={tr("provider_models_panel.preise_aus_dem_offenen_katalog_models_de")}
            className="rounded border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface disabled:opacity-50">
            {preise.isPending ? tr("common.laedt") : `💲 ${tr("provider_models_panel.preise")} (models.dev)`}
          </button>
        </div>
      </div>

      {provider.map((p) => (
        <div key={p} className="rounded-lg border border-line bg-card p-4">
          <div className="mb-2 text-sm font-semibold text-ink">{PROVIDER_LABEL[p] ? tr(PROVIDER_LABEL[p]) : p}
            <span className="ml-2 font-mono text-xs font-normal text-muted">{p}</span>
          </div>
          {/* Am Schreibtisch die Tabelle, am Handy Karten: dieselben Felder aus zahlenFelder(). */}
          <div className="hidden overflow-x-auto md:block">
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
                      {zahlenFelder(roh).map((f) => (
                        <td key={f.key} className="pr-2">{f.node}</td>
                      ))}
                      <td className="text-center">
                        <input type="checkbox" checked={m.enabled}
                          onChange={(e) => setzen(roh, { enabled: e.target.checked })} />
                      </td>
                      <td className="whitespace-nowrap py-2 text-right">
                        <button onClick={() => speichern.mutate(m)} disabled={!geaendert(roh)}
                          className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface disabled:opacity-30">
                          {tr("common.speichern")}
                        </button>
                        <button onClick={() => { if (confirm(tr("provider_models_panel.loeschen_frage", { modell: m.model }))) loeschen.mutate(m.id); }}
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

          <div className="space-y-2 md:hidden">
            {(modelle || []).filter((m) => m.provider === p).map((roh) => {
              const m = zeile(roh);
              return (
                <div key={m.id} className={`rounded border border-line p-2 ${m.enabled ? "" : "opacity-50"}`}>
                  <button onClick={() => setOffen(offen === m.id ? null : m.id)}
                    className="flex w-full items-baseline gap-2 text-left">
                    <span className="text-muted">{offen === m.id ? "▾" : "▸"}</span>
                    <span className="min-w-0 flex-1 break-all font-mono text-xs text-muted">{m.model}</span>
                    {geaendert(roh) && <span className="text-xs text-brand">•</span>}
                  </button>
                  {offen !== m.id ? null : (
                  <>
                  <input value={m.display_name} className={`${inp} mt-1`}
                    onChange={(e) => setzen(roh, { display_name: e.target.value })} />
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {zahlenFelder(roh).map((f) => (
                      <label key={f.key} className="text-[11px] text-muted">
                        {f.label}
                        <div className="mt-0.5 [&>input]:w-full">{f.node}</div>
                      </label>
                    ))}
                  </div>
                  <div className="mt-2 flex items-center gap-3 text-xs">
                    <label className="flex items-center gap-1 text-muted">
                      <input type="checkbox" checked={m.enabled}
                        onChange={(e) => setzen(roh, { enabled: e.target.checked })} />
                      {tr("provider_models_panel.aktiv")}
                    </label>
                    <div className="flex-1" />
                    <button onClick={() => speichern.mutate(m)} disabled={!geaendert(roh)}
                      className="rounded border border-line px-2 py-1 text-ink hover:bg-surface disabled:opacity-30">
                      {tr("common.speichern")}
                    </button>
                    <button onClick={() => { if (confirm(tr("provider_models_panel.loeschen_frage", { modell: m.model }))) loeschen.mutate(m.id); }}
                      className="rounded border border-line px-2 py-1 text-red-400 hover:bg-surface">✕</button>
                  </div>
                  </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {modelle && modelle.length === 0 && (
        <div className="text-sm text-muted">
          {tr("provider_models_panel.katalog_leer")}
        </div>
      )}
    </div>
  );
}
