import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import {
  Aktionen, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, LoeschDialog, Bereich, ListeLeer, KNOPF} from "./ui";

interface Modell {
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
  const [dialog, setDialog] = useState<Modell | null>(null);
  const [loeschModell, setLoeschModell] = useState<Modell | null>(null);

  const { data: modelle } = useQuery({
    queryKey: ["provider-models"], queryFn: () => api.get<Modell[]>("/providers/models"),
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["provider-models"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
  const flash = (t: string) => { setNote(t); setTimeout(() => setNote(""), 4000); };

  const speichern = useMutation({
    mutationFn: (m: Modell) => api.put("/providers/models", {
      provider: m.provider, model: m.model, display_name: m.display_name,
      price_input: m.price_input, price_output: m.price_output,
      price_cache_read: m.price_cache_read, enabled: m.enabled,
      context_tokens: m.context_tokens, speed_tps: m.speed_tps,
    }),
    onSuccess: () => { setErr(""); setDialog(null); inv(); },
    onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/providers/models/${id}`),
    onSuccess: () => { setErr(""); setLoeschModell(null); inv(); }, onError: fail,
  });
  const preise = useMutation({
    mutationFn: () => api.post<any>("/providers/models/prices"),
    onSuccess: (r) => {
      const n = r.updated?.length ?? 0;
      const namen = (r.updated || []).slice(0, 4).map((u: any) => u.model).join(", ");
      const kontext = r.context_set ? ` · ${tr("provider_models_panel.kontext_gesetzt", { anzahl: r.context_set })}` : "";
      flash((n
        ? tr("provider_models_panel.preise_uebernommen", { anzahl: n, namen: namen + (n > 4 ? " …" : "") })
        : tr("provider_models_panel.preise_aktuell"))
        + kontext
        + (r.unknown?.length ? ` · ${tr("provider_models_panel.ohne_eintrag", { anzahl: r.unknown.length })}` : ""));
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

  const provider = [...new Set((modelle || []).map((m) => m.provider))].sort();
  const zahl = (v: number | null) => v === null || v === undefined ? "—" : String(v);

  return (
    <div className="space-y-4">
      <Bereich hinweis={tr("provider_models_panel.einleitung")} werkzeuge={<>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button onClick={() => abrufen.mutate()} disabled={abrufen.isPending}
            className={KNOPF.neben}>
            {abrufen.isPending ? tr("common.laedt") : `↻ ${tr("provider_models_panel.modelle_abrufen")}`}
          </button>
          <button onClick={() => preise.mutate()} disabled={preise.isPending}
            title={tr("provider_models_panel.preise_aus_dem_offenen_katalog_models_de")}
            className={KNOPF.neben}>
            {preise.isPending ? tr("common.laedt") : `💲 ${tr("provider_models_panel.preise")} (models.dev)`}
          </button>
        </div>
      </>}>
        <Fehlerzeile text={err} />
        {note && <p className="text-sm text-muted">{note}</p>}
      </Bereich>

      {provider.map((p) => (
        <Bereich key={p} titel={PROVIDER_LABEL[p] ? tr(PROVIDER_LABEL[p]) : p} nebentitel={p}>
          {/* Werte statt Eingabefelder: die Tabelle darf scrollen, die Seite nicht. */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase text-muted">
                  <th className="py-2">{tr("provider_models_panel.modell_id")}</th>
                  <th>{tr("provider_models_panel.anzeigename")}</th>
                  <th className="text-right">{tr("provider_models_panel.input")}</th>
                  <th className="text-right">{tr("provider_models_panel.output")}</th>
                  <th className="text-right">{tr("provider_models_panel.cache_read")}</th>
                  <th className="text-right">{tr("provider_models_panel.kontext")}</th>
                  <th className="text-right">≈ t/s</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(modelle || []).filter((m) => m.provider === p).map((m) => (
                  <tr key={m.id} className={`border-b border-line ${m.enabled ? "" : "opacity-50"}`}>
                    <td className="py-2 pr-2 font-mono text-xs">{m.model}</td>
                    <td className="pr-2">{m.display_name || "—"}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_input}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_output}</td>
                    <td className="pr-2 text-right tabular-nums">{m.price_cache_read}</td>
                    <td className="pr-2 text-right tabular-nums">{zahl(m.context_tokens)}</td>
                    <td className="pr-2 text-right tabular-nums">{zahl(m.speed_tps)}</td>
                    <td className="py-1 text-right">
                      <div className="flex justify-end">
                        <Aktionen>
                          <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                            onClick={() => { setErr(""); setDialog(m); }} />
                          <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                            onClick={() => setLoeschModell(m)} />
                        </Aktionen>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Bereich>
      ))}

      {modelle && modelle.length === 0 && (
        <Bereich><ListeLeer>{tr("provider_models_panel.katalog_leer")}</ListeLeer></Bereich>
      )}

      {dialog && (
        <ModellDialog modell={dialog} fehler={err} laeuft={speichern.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(m) => speichern.mutate(m)} />
      )}
      {loeschModell && (
        <LoeschDialog was={loeschModell.model} laeuft={loeschen.isPending}
          onClose={() => setLoeschModell(null)} onLoeschen={() => loeschen.mutate(loeschModell.id)} />
      )}
    </div>
  );
}

function ModellDialog({ modell, fehler, laeuft, onClose, onSpeichern }: {
  modell: Modell; fehler: string; laeuft: boolean;
  onClose: () => void; onSpeichern: (m: Modell) => void;
}) {
  const [m, setM] = useState<Modell>(modell);
  const zahlFeld = (wert: number | null, setzen: (v: number | null) => void, step: string) => (
    <input type="number" step={step} min="0" value={wert ?? ""} placeholder="—"
      onChange={(e) => setzen(e.target.value ? Number(e.target.value) : null)} className={EINGABE} />
  );

  return (
    <Dialog breit titel={modell.model} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft} onSpeichern={() => onSpeichern(m)} />}>
      <Fehlerzeile text={fehler} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Feld label={tr("provider_models_panel.anzeigename")}>
            <input value={m.display_name} autoFocus className={EINGABE}
              onChange={(e) => setM({ ...m, display_name: e.target.value })} />
          </Feld>
        </div>
        <Feld label={tr("provider_models_panel.input")} hinweis={tr("provider_models_panel.usd_je_1m")}>
          {zahlFeld(m.price_input, (v) => setM({ ...m, price_input: v ?? 0 }), "0.01")}
        </Feld>
        <Feld label={tr("provider_models_panel.output")} hinweis={tr("provider_models_panel.usd_je_1m")}>
          {zahlFeld(m.price_output, (v) => setM({ ...m, price_output: v ?? 0 }), "0.01")}
        </Feld>
        <Feld label={tr("provider_models_panel.cache_read")} hinweis={tr("provider_models_panel.usd_je_1m")}>
          {zahlFeld(m.price_cache_read, (v) => setM({ ...m, price_cache_read: v ?? 0 }), "0.01")}
        </Feld>
        <Feld label={tr("provider_models_panel.kontext")}
          hinweis={tr("provider_models_panel.maximales_kontextfenster_in_tokens")}>
          {zahlFeld(m.context_tokens, (v) => setM({ ...m, context_tokens: v }), "1024")}
        </Feld>
        <Feld label="≈ t/s" hinweis={tr("provider_models_panel.gemessene_ausgabegeschwindigkeit_tokens_")}>
          {zahlFeld(m.speed_tps, (v) => setM({ ...m, speed_tps: v }), "1")}
        </Feld>
        <label className="flex items-end gap-2 pb-1.5 text-sm text-ink">
          <input type="checkbox" checked={m.enabled} onChange={(e) => setM({ ...m, enabled: e.target.checked })} />
          {tr("provider_models_panel.aktiv")}
        </label>
      </div>
    </Dialog>
  );
}
