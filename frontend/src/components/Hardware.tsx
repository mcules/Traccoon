import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import AssetProcurement from "./AssetProcurement";

interface Model { id: number; name: string; category: string | null; manufacturer: string | null; }
interface Location { id: number; name: string; type: string; parent_id: number | null; full_path: string; }
interface Asset {
  id: number; model_id: number; project_id: number | null; location_id: number | null;
  purchase_status: string; serial_number: string | null; vendor: string | null;
}
const STATUS = ["planned", "ordered", "delivered", "installed", "stored", "retired"];
const LOC_TYPES = ["room", "rack", "shelf", "slot", "server", "other"];

export default function Hardware({ project }: { project: Project }) {
  const qc = useQueryClient();
  const kannVerwalten = project.my_role === "maintainer" || project.my_role === "owner";
  const models = useQuery({ queryKey: ["hw-models"], queryFn: () => api.get<Model[]>("/hardware/models") });
  const locations = useQuery({ queryKey: ["hw-locations"], queryFn: () => api.get<Location[]>("/locations") });
  const assets = useQuery({
    queryKey: ["hw-assets", project.id],
    queryFn: () => api.get<Asset[]>(`/hardware/assets?project_id=${project.id}`),
  });
  const modelName = (id: number) => models.data?.find((m) => m.id === id)?.name || `#${id}`;
  const locPath = (id: number | null) => (id ? locations.data?.find((l) => l.id === id)?.full_path : "—");

  const [mName, setMName] = useState("");
  const [lName, setLName] = useState("");
  const [lType, setLType] = useState("room");
  const [lParent, setLParent] = useState("");
  const [aModel, setAModel] = useState("");
  const [aLoc, setALoc] = useState("");
  const [aStatus, setAStatus] = useState("planned");
  const [offen, setOffen] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const invAssets = () => qc.invalidateQueries({ queryKey: ["hw-assets", project.id] });
  const addModel = useMutation({
    mutationFn: () => api.post("/hardware/models", { name: mName }),
    onSuccess: () => { setMName(""); qc.invalidateQueries({ queryKey: ["hw-models"] }); }, onError: fehler,
  });
  const delModel = useMutation({
    mutationFn: (id: number) => api.del(`/hardware/models/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hw-models"] }), onError: fehler,
  });
  const addLoc = useMutation({
    mutationFn: () => api.post("/locations", { name: lName, type: lType, parent_id: lParent ? Number(lParent) : null }),
    onSuccess: () => { setLName(""); qc.invalidateQueries({ queryKey: ["hw-locations"] }); }, onError: fehler,
  });
  const delLoc = useMutation({
    mutationFn: (id: number) => api.del(`/locations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hw-locations"] }), onError: fehler,
  });
  const addAsset = useMutation({
    mutationFn: () => api.post("/hardware/assets", {
      model_id: Number(aModel), project_id: project.id,
      location_id: aLoc ? Number(aLoc) : null, purchase_status: aStatus,
    }),
    onSuccess: invAssets, onError: fehler,
  });
  const delAsset = useMutation({
    mutationFn: (id: number) => api.del(`/hardware/assets/${id}`),
    onSuccess: () => { setOffen(null); invAssets(); }, onError: fehler,
  });

  return (
    <div>
      {err && <div className="mb-3 text-sm text-red-400">{err}</div>}
      <div className="grid gap-6 lg:grid-cols-2">
        <section>
          <h3 className="mb-2 font-medium">Exemplare (dieses Projekt)</h3>
          <div className="space-y-2">
            {assets.data?.map((a) => (
              <div key={a.id} className="rounded border border-line bg-card text-sm">
                <div className="flex items-start gap-2 p-2.5">
                  <button onClick={() => setOffen(offen === a.id ? null : a.id)} className="flex-1 text-left">
                    <div className="font-medium">{modelName(a.model_id)}</div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted">
                      <span className="rounded bg-surface px-1.5 py-0.5">{a.purchase_status}</span>
                      <span>📍 {locPath(a.location_id)}</span>
                      {a.serial_number && <span>SN {a.serial_number}</span>}
                      <span className="text-brand">{offen === a.id ? "▾ Beschaffung" : "▸ Beschaffung"}</span>
                    </div>
                  </button>
                  {kannVerwalten && (
                    <button onClick={() => delAsset.mutate(a.id)}
                      className="text-muted hover:text-red-400" title="Exemplar löschen">🗑</button>
                  )}
                </div>
                {offen === a.id && (
                  <div className="border-t border-line p-2.5">
                    <AssetProcurement assetId={a.id} project={project} onChange={invAssets} />
                  </div>
                )}
              </div>
            ))}
            {assets.data?.length === 0 && <div className="text-xs text-muted">Keine Exemplare.</div>}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2 rounded-lg border border-line bg-card p-3">
            <select value={aModel} onChange={(e) => setAModel(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">Modell…</option>
              {models.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
            <select value={aLoc} onChange={(e) => setALoc(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">Ort…</option>
              {locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)}
            </select>
            <select value={aStatus} onChange={(e) => setAStatus(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              {STATUS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button onClick={() => aModel && addAsset.mutate()} className="rounded bg-brand px-3 py-1 text-sm text-white">
              + Exemplar
            </button>
          </div>
        </section>

        <section className="space-y-6">
          {kannVerwalten && <WorkflowConfig project={project} />}

          <div>
            <h3 className="mb-2 font-medium">Katalog (Modelle)</h3>
            <div className="flex flex-wrap gap-1">
              {models.data?.map((m) => (
                <span key={m.id} className="flex items-center gap-1 rounded bg-surface px-2 py-1 text-xs">
                  {m.name}
                  {kannVerwalten && (
                    <button onClick={() => delModel.mutate(m.id)}
                      className="text-muted hover:text-red-400">✕</button>
                  )}
                </span>
              ))}
            </div>
            <div className="mt-2 flex gap-2">
              <input value={mName} onChange={(e) => setMName(e.target.value)} placeholder="Modellname"
                className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
              <button onClick={() => mName && addModel.mutate()} className="rounded bg-brand px-3 py-1 text-sm text-white">+</button>
            </div>
          </div>

          <div>
            <h3 className="mb-2 font-medium">Lagerorte</h3>
            <div className="space-y-1">
              {locations.data?.map((l) => (
                <div key={l.id} className="flex items-center gap-1.5 text-xs text-muted">
                  📍 {l.full_path} <span className="opacity-60">({l.type})</span>
                  {kannVerwalten && (
                    <button onClick={() => delLoc.mutate(l.id)}
                      className="ml-auto text-muted hover:text-red-400">✕</button>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <input value={lName} onChange={(e) => setLName(e.target.value)} placeholder="Ortname"
                className="rounded border border-line bg-surface px-2 py-1 text-sm" />
              <select value={lType} onChange={(e) => setLType(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                {LOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select value={lParent} onChange={(e) => setLParent(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                <option value="">— übergeordnet —</option>
                {locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)}
              </select>
              <button onClick={() => lName && addLoc.mutate()} className="rounded bg-brand px-3 py-1 text-sm text-white">+</button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

/** Standard-Beschaffungsschritte je Projekt (Vorlage für neue Exemplare). */
function WorkflowConfig({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["hw-workflow", project.id],
    queryFn: () => api.get<{ name: string }[]>(`/projects/${project.id}/hardware-workflow`),
  });
  const [text, setText] = useState<string | null>(null);
  const wert = text ?? (data ? data.map((s) => s.name).join("\n") : "");
  const speichern = useMutation({
    mutationFn: () => api.put(`/projects/${project.id}/hardware-workflow`, {
      steps: wert.split("\n").map((n) => n.trim()).filter(Boolean).map((name, order) => ({ name, order })),
    }),
    onSuccess: () => { setText(null); qc.invalidateQueries({ queryKey: ["hw-workflow", project.id] }); },
  });
  return (
    <div className="rounded-lg border border-line bg-card p-3">
      <h3 className="mb-1 font-medium">Beschaffungsprozess</h3>
      <p className="mb-2 text-xs text-muted">Schritte (eine Zeile je Schritt), die jedes neue Exemplar durchläuft.</p>
      <textarea value={wert} onChange={(e) => setText(e.target.value)} rows={4}
        className="w-full rounded border border-line bg-surface px-2 py-1.5 font-mono text-xs" />
      <button onClick={() => speichern.mutate()}
        className="mt-2 rounded border border-line px-3 py-1 text-xs text-muted hover:text-ink">Speichern</button>
      <p className="mt-1 text-xs text-muted">Wirkt auf künftige Exemplare; bereits angelegte behalten ihre Schritte.</p>
    </div>
  );
}
