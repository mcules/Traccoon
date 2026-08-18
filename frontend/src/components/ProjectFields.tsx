import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Project } from "../api";

interface Wert {
  id: number; value: string; label: string; color: string; order: number; enabled: boolean;
}
interface Feld {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; order: number; description: string; enabled: boolean;
  source: string; options_source: string; builtin: boolean; project_id: number | null;
  options: Wert[]; dynamic_options: [string, string][];
}
interface Typ {
  id: number; key: string; name: string; icon: string; backing: string;
  project_id: number | null; fields: Feld[];
}

const FELDTYP: [string, string][] = [
  ["text", "Text"], ["number", "Zahl"], ["date", "Datum"],
  ["boolean", "Ja/Nein"], ["select", "Auswahl"],
];

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Eigene Felder dieses Projekts.
 *
 * Ein Artefakt ist zunächst etwas Undefiniertes — seine Bedeutung bekommt es erst durch
 * seine Felder. Ticket und Hardware bringen einen ausgelieferten Satz mit; hier ergänzt das
 * Projekt eigene. Die ausgelieferten stehen nur zur Ansicht: Board, Sprints und der
 * KI-Lebenszyklus laufen darauf, deshalb lassen sie sich nicht entfernen.
 *
 * Was hier entsteht, gilt ausschließlich für dieses Projekt und erscheint sofort in der
 * Ticket- bzw. Hardware-Ansicht.
 */
export default function ProjectFields({ project }: { project: Project }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data: typen } = useQuery({
    queryKey: ["artifact-types", project.id],
    queryFn: () => api.get<Typ[]>(`/artifact-types?project_id=${project.id}`),
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["artifact-types", project.id] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const ok = () => { setErr(""); inv(); };

  // Nur, was dieses Projekt betrifft: Hardware nur in Hardware-Projekten.
  const sichtbar = (typen || []).filter(
    (t) => t.backing !== "hardware_asset" || project.has_hardware);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Ein Artefakt bekommt seine Bedeutung durch seine Felder. Ticket und Hardware bringen
        feste Felder mit — hier ergänzt du eigene, die <b>nur in diesem Projekt</b> gelten.
        Sie erscheinen sofort in der Ticket- bzw. Hardware-Ansicht.
      </p>
      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      {sichtbar.map((t) => (
        <ArtefaktFelder key={t.id} t={t} projectId={project.id} onFail={fail} onOk={ok} />
      ))}
    </div>
  );
}

function ArtefaktFelder({ t, projectId, onFail, onOk }: {
  t: Typ; projectId: number; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [neu, setNeu] = useState({ key: "", label: "", kind: "text", multi: false });

  const anlegen = useMutation({
    mutationFn: () => api.post(`/artifact-types/${t.id}/fields?project_id=${projectId}`, neu),
    onSuccess: () => { setNeu({ key: "", label: "", kind: "text", multi: false }); onOk(); },
    onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-fields/${id}`, rest),
    onSuccess: onOk, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-fields/${id}`), onSuccess: onOk, onError: onFail,
  });

  // Bearbeitbar ist nur, was DIESEM Projekt gehört. Ausgelieferte Felder und solche, die
  // überall gelten, stehen hier bloß zur Ansicht — sie zu ändern beträfe alle Projekte.
  const fest = t.fields.filter((f) => f.project_id !== projectId);
  const eigene = t.fields.filter((f) => f.project_id === projectId);

  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">{t.icon}</span>
        <span className="font-medium">{t.name}</span>
      </div>

      {fest.length > 0 && (
        <div className="mb-3 text-xs text-muted">
          Gilt überall: {fest.map((f) => f.label).join(" · ")}
          <span className="ml-1 text-[11px]">{tr("project_fields.hier_nicht_aenderbar")}</span>
        </div>
      )}

      <div className="space-y-2">
        {eigene.map((f) => (
          <div key={f.id} className={`rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="w-28 shrink-0 font-mono text-xs text-muted">{f.key}</span>
              <input defaultValue={f.label}
                onBlur={(e) => e.target.value !== f.label && aendern.mutate({ id: f.id, label: e.target.value })}
                className={`flex-1 ${inp}`} />
              <select defaultValue={f.kind}
                onChange={(e) => aendern.mutate({ id: f.id, kind: e.target.value })} className={inp}>
                {FELDTYP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <label className="flex items-center gap-1 text-xs text-muted"
                     title={tr("project_fields.darf_ein_ticket_mehrere_werte_gleichzeit")}>
                <input type="checkbox" checked={f.multi}
                  onChange={(e) => aendern.mutate({ id: f.id, multi: e.target.checked })} />
                Mehrfachauswahl
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={f.required}
                  onChange={(e) => aendern.mutate({ id: f.id, required: e.target.checked })} />
                Pflicht
              </label>
              <label className="flex items-center gap-1 text-xs text-muted"
                     title={tr("project_fields.abgeschaltete_felder_werden_nicht_mehr_a")}>
                <input type="checkbox" checked={f.enabled}
                  onChange={(e) => aendern.mutate({ id: f.id, enabled: e.target.checked })} />
                aktiv
              </label>
              <button onClick={() => confirm(`Feld „${f.label}“ löschen?`) && loeschen.mutate(f.id)}
                className="rounded border border-line px-1.5 py-0.5 text-xs hover:border-red-400">✕</button>
            </div>
            {f.kind === "select" && <Werteliste feld={f} onFail={onFail} onOk={onOk} />}
          </div>
        ))}
        {eigene.length === 0 && (
          <div className="text-xs text-muted">{tr("project_fields.noch_keine_eigenen_felder")}</div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input value={neu.key} onChange={(e) => setNeu({ ...neu, key: e.target.value })}
          placeholder={tr("project_fields.schluessel_kunde")} className={`w-40 font-mono ${inp}`} />
        <input value={neu.label} onChange={(e) => setNeu({ ...neu, label: e.target.value })}
          placeholder={tr("project_fields.bezeichnung_kunde")} className={`flex-1 ${inp}`} />
        <select value={neu.kind} onChange={(e) => setNeu({ ...neu, kind: e.target.value })} className={inp}>
          {FELDTYP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={neu.multi}
            onChange={(e) => setNeu({ ...neu, multi: e.target.checked })} />
          Mehrfachauswahl
        </label>
        <button onClick={() => neu.key.trim() && neu.label.trim() && anlegen.mutate()}
          className="rounded border border-line px-2 py-1 text-xs hover:border-brand">
          Feld anlegen
        </button>
      </div>
    </div>
  );
}

function Werteliste({ feld, onFail, onOk }: {
  feld: Feld; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [wert, setWert] = useState("");
  const anlegen = useMutation({
    mutationFn: () => api.post(`/artifact-fields/${feld.id}/options`, { value: wert.trim() }),
    onSuccess: () => { setWert(""); onOk(); }, onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-field-options/${id}`, rest),
    onSuccess: onOk, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-field-options/${id}`),
    onSuccess: onOk, onError: onFail,
  });

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-28">
      <span className="text-[11px] text-muted">{tr("project_fields.werte")}</span>
      {feld.options.map((o) => (
        <span key={o.id}
          className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${
            o.enabled ? "bg-brand/15 text-ink" : "bg-surface text-muted line-through"}`}>
          {o.label || o.value}
          <button onClick={() => aendern.mutate({ id: o.id, enabled: !o.enabled })}
            title={o.enabled ? "Nicht mehr anbieten (bleibt an vorhandenen Tickets)" : "Wieder anbieten"}
            className="text-muted hover:text-ink">{o.enabled ? "○" : "●"}</button>
          <button onClick={() => loeschen.mutate(o.id)} title={tr("project_fields.loeschen")}
            className="text-muted hover:text-red-300">✕</button>
        </span>
      ))}
      <input value={wert} onChange={(e) => setWert(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && wert.trim() && anlegen.mutate()}
        placeholder={tr("project_fields.wert_enter")} className={`w-32 text-xs ${inp}`} />
    </div>
  );
}
