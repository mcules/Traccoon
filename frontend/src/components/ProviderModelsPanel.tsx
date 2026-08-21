import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import {
  Actions, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, DeleteDialog, Area, ListingEmpty, BUTTON} from "./ui";

interface Model {
  id: number; provider: string; model: string; display_name: string;
  price_input: number; price_output: number; price_cache_read: number; enabled: boolean;
  context_tokens: number | null; speed_tps: number | null;
}

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
 * (prices in USD per 1M tokens). "Fetch models" queries every stored token at its own
 * endpoint, so with an OpenAI-compatible proxy its local model names come, not those of
 * OpenAI.
 *
 * Every row used to be a form: seven inputs per model, a save button per row, and a second
 * card presentation underneath for phones because nine columns do not fit on 390 px. Thirty
 * models were thirty open forms. The table shows values now, and editing happens in the
 * dialog, which is the same on every width.
 */
export default function ProviderModelsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [dialog, setDialog] = useState<Model | null>(null);
  const [deleteModel, setDeleteModel] = useState<Model | null>(null);

  const { data: models } = useQuery({
    queryKey: ["provider-models"], queryFn: () => api.get<Model[]>("/providers/models"),
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["provider-models"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const flash = (t: string) => { setNote(t); setTimeout(() => setNote(""), 4000); };

  const save = useMutation({
    mutationFn: (m: Model) => api.put("/providers/models", {
      provider: m.provider, model: m.model, display_name: m.display_name,
      price_input: m.price_input, price_output: m.price_output,
      price_cache_read: m.price_cache_read, enabled: m.enabled,
      context_tokens: m.context_tokens, speed_tps: m.speed_tps,
    }),
    onSuccess: () => { setErr(""); setDialog(null); inv(); },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/providers/models/${id}`),
    onSuccess: () => { setErr(""); setDeleteModel(null); inv(); }, onError: fail,
  });
  const prices = useMutation({
    mutationFn: () => api.post<any>("/providers/models/prices"),
    onSuccess: (r) => {
      const n = r.updated?.length ?? 0;
      const names = (r.updated || []).slice(0, 4).map((u: any) => u.model).join(", ");
      const context = r.context_set ? ` · ${tr("provider_models_panel.count_context_windows_set", { count: r.context_set })}` : "";
      flash((n
        ? tr("provider_models_panel.count_price_s_taken", { count: n, names: names + (n > 4 ? " …" : "") })
        : tr("provider_models_panel.all_prices_up_date"))
        + context
        + (r.unknown?.length ? ` · ${tr("provider_models_panel.count_without_entry_unchanged", { count: r.unknown.length })}` : ""));
      setErr(""); inv();
    },
    onError: fail,
  });
  const fetch = useMutation({
    mutationFn: () => api.post<Record<string, any>>("/providers/models/fetch"),
    onSuccess: (r) => {
      const parts = Object.entries(r).map(([label, v]: [string, any]) =>
        v.error ? `${label}: ${tr("common.error")} (${v.error})`
          : `${label}: ${v.total ?? 0}${v.added ? ` (+${v.added} ${tr("provider_models_panel.new")})` : ""}`
            + `${v.disabled ? ` (${v.disabled} ${tr("provider_models_panel.switched_off")})` : ""}`);
      flash(parts.length ? parts.join(" · ") : tr("provider_models_panel.no_provider_tokens_stored"));
      setErr(""); inv();
    },
    onError: fail,
  });

  const provider = [...new Set((models || []).map((m) => m.provider))].sort();
  const number = (v: number | null) => v === null || v === undefined ? "—" : String(v);

  return (
    <div className="space-y-4">
      <Area hint={tr("provider_models_panel.which_provider_serves_which")} tools={<>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button onClick={() => fetch.mutate()} disabled={fetch.isPending}
            className={BUTTON.secondary}>
            {fetch.isPending ? tr("common.loading") : `↻ ${tr("provider_models_panel.fetch_models")}`}
          </button>
          <button onClick={() => prices.mutate()} disabled={prices.isPending}
            title={tr("provider_models_panel.take_prices_from_the_open_catalog_models_dev")}
            className={BUTTON.secondary}>
            {prices.isPending ? tr("common.loading") : `💲 ${tr("provider_models_panel.prices")} (models.dev)`}
          </button>
        </div>
      </>}>
        <Errorrow text={err} />
        {note && <p className="text-sm text-muted">{note}</p>}
      </Area>

      {provider.map((p) => (
        <Area key={p} title={PROVIDER_LABEL[p] ? tr(PROVIDER_LABEL[p]) : p} subtitle={p}>
          {/* Werte statt Eingabefelder: die Tabelle darf scrollen, die Seite nicht. */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase text-muted">
                  <th className="py-2">{tr("provider_models_panel.model_id")}</th>
                  <th>{tr("provider_models_panel.display_name")}</th>
                  <th className="text-right">{tr("provider_models_panel.input")}</th>
                  <th className="text-right">{tr("provider_models_panel.output")}</th>
                  <th className="text-right">{tr("provider_models_panel.cache_read")}</th>
                  <th className="text-right">{tr("provider_models_panel.context")}</th>
                  <th className="text-right">≈ t/s</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(models || []).filter((m) => m.provider === p).map((m) => (
                  <tr key={m.id} className={`border-b border-line ${m.enabled ? "" : "opacity-50"}`}>
                    <td className="py-2 pr-2 font-mono text-xs">{m.model}</td>
                    <td className="pr-2">{m.display_name || "—"}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_input}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_output}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_cache_read}</td>
                    <td className="pr-2 text-right tabular-nums">{number(m.context_tokens)}</td>
                    <td className="pr-2 text-right tabular-nums">{number(m.speed_tps)}</td>
                    <td className="py-1 text-right">
                      <div className="flex justify-end">
                        <Actions>
                          <IconButton icon={ICON.edit} title={tr("common.edit")}
                            onClick={() => { setErr(""); setDialog(m); }} />
                          <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                            onClick={() => setDeleteModel(m)} />
                        </Actions>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Area>
      ))}

      {models && models.length === 0 && (
        <Area><ListingEmpty>{tr("provider_models_panel.catalog_empty_fetch_models")}</ListingEmpty></Area>
      )}

      {dialog && (
        <ModelDialog model={dialog} error={err} runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(m) => save.mutate(m)} />
      )}
      {deleteModel && (
        <DeleteDialog was={deleteModel.model} runs={remove.isPending}
          onClose={() => setDeleteModel(null)} onDelete={() => remove.mutate(deleteModel.id)} />
      )}
    </div>
  );
}

function ModelDialog({ model, error: error, runs: running, onClose, onSave }: {
  model: Model; error: string; runs: boolean;
  onClose: () => void; onSave: (m: Model) => void;
}) {
  const [m, setM] = useState<Model>(model);
  const numberField = (value: number | null, set: (v: number | null) => void, step: string) => (
    <input type="number" step={step} min="0" value={value ?? ""} placeholder="—"
      onChange={(e) => set(e.target.value ? Number(e.target.value) : null)} className={INPUT_VALUE} />
  );

  return (
    <Dialog wide title={model.model} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running} onSave={() => onSave(m)} />}>
      <Errorrow text={error} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label={tr("provider_models_panel.display_name")}>
            <input value={m.display_name} autoFocus className={INPUT_VALUE}
              onChange={(e) => setM({ ...m, display_name: e.target.value })} />
          </Field>
        </div>
        <Field label={tr("provider_models_panel.input")} hint={tr("provider_models_panel.usd_per_1m_tokens")}>
          {numberField(m.price_input, (v) => setM({ ...m, price_input: v ?? 0 }), "0.01")}
        </Field>
        <Field label={tr("provider_models_panel.output")} hint={tr("provider_models_panel.usd_per_1m_tokens")}>
          {numberField(m.price_output, (v) => setM({ ...m, price_output: v ?? 0 }), "0.01")}
        </Field>
        <Field label={tr("provider_models_panel.cache_read")} hint={tr("provider_models_panel.usd_per_1m_tokens")}>
          {numberField(m.price_cache_read, (v) => setM({ ...m, price_cache_read: v ?? 0 }), "0.01")}
        </Field>
        <Field label={tr("provider_models_panel.context")}
          hint={tr("provider_models_panel.maximum_context_window_in_tokens")}>
          {numberField(m.context_tokens, (v) => setM({ ...m, context_tokens: v }), "1024")}
        </Field>
        <Field label="≈ t/s" hint={tr("provider_models_panel.measured_output_speed_tokens_s")}>
          {numberField(m.speed_tps, (v) => setM({ ...m, speed_tps: v }), "1")}
        </Field>
        <label className="flex items-end gap-2 pb-1.5 text-sm text-ink">
          <input type="checkbox" checked={m.enabled} onChange={(e) => setM({ ...m, enabled: e.target.checked })} />
          {tr("provider_models_panel.active")}
        </label>
      </div>
    </Dialog>
  );
}
