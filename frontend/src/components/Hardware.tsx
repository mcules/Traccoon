import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError, MemberLite, Project } from "../api";
import AssetProcurement from "./AssetProcurement";
import AssetWorkflow from "./AssetWorkflow";
import ArtifactFields from "./ArtifactFields";
import { AssigneeEditor } from "./workflow/assignee";
import type { AssigneeSpec } from "./workflow/types";

interface Model { id: number; name: string; category: string | null; manufacturer: string | null; }
interface Location { id: number; name: string; type: string; parent_id: number | null; full_path: string; }
interface Asset {
  id: number; model_id: number; project_id: number | null; location_id: number | null;
  purchase_status: string; serial_number: string | null; vendor: string | null;
  artifact_id: number | null;
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
  const [ansicht, setAnsicht] = useState<"klassisch" | "workflow">("klassisch");
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
          <h3 className="mb-2 font-medium">{tr("hardware.exemplare_dieses_projekt")}</h3>
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
                      className="text-muted hover:text-red-400" title={tr("hardware.exemplar_loeschen")}>🗑</button>
                  )}
                </div>
                {offen === a.id && (
                  <div className="border-t border-line p-2.5">
                    <div className="mb-2.5 inline-flex rounded border border-line bg-surface p-0.5 text-xs">
                      <button
                        onClick={() => setAnsicht("klassisch")}
                        className={`rounded px-2 py-0.5 ${ansicht === "klassisch" ? "bg-brand text-white" : "text-muted hover:text-ink"}`}
                      >
                        Klassische Schritte
                      </button>
                      <button
                        onClick={() => setAnsicht("workflow")}
                        className={`rounded px-2 py-0.5 ${ansicht === "workflow" ? "bg-brand text-white" : "text-muted hover:text-ink"}`}
                      >
                        🧭 Workflow
                      </button>
                    </div>
                    {ansicht === "klassisch" ? (
                      <AssetProcurement assetId={a.id} project={project} onChange={invAssets} />
                    ) : (
                      <AssetWorkflow assetId={a.id} projectId={a.project_id} assetLabel={modelName(a.model_id)} />
                    )}
                    {a.artifact_id && (
                      <div className="mt-3 rounded-lg border border-line p-3">
                        <div className="mb-2 text-xs font-medium text-muted">{tr("hardware.felder")}</div>
                        <ArtifactFields artifactId={a.artifact_id} compact />
                      </div>
                    )}
                    <AssetIssues assetId={a.id} projectKey={project.key} />
                  </div>
                )}
              </div>
            ))}
            {assets.data?.length === 0 && <div className="text-xs text-muted">{tr("hardware.keine_exemplare")}</div>}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2 rounded-lg border border-line bg-card p-3">
            <select value={aModel} onChange={(e) => setAModel(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("hardware.modell")}</option>
              {models.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
            <select value={aLoc} onChange={(e) => setALoc(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("hardware.ort")}</option>
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
            <h3 className="mb-2 font-medium">{tr("hardware.katalog_modelle")}</h3>
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
              <input value={mName} onChange={(e) => setMName(e.target.value)} placeholder={tr("hardware.modellname")}
                className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
              <button onClick={() => mName && addModel.mutate()} className="rounded bg-brand px-3 py-1 text-sm text-white">+</button>
            </div>
          </div>

          <div>
            <h3 className="mb-2 font-medium">{tr("hardware.lagerorte")}</h3>
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
              <input value={lName} onChange={(e) => setLName(e.target.value)} placeholder={tr("hardware.ortname")}
                className="rounded border border-line bg-surface px-2 py-1 text-sm" />
              <select value={lType} onChange={(e) => setLType(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                {LOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select value={lParent} onChange={(e) => setLParent(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                <option value="">{tr("hardware.uebergeordnet")}</option>
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

/** Tickets that hang off this unit (ABC-25), the opposite direction to the hardware field in the ticket. */
function AssetIssues({ assetId, projectKey }: { assetId: number; projectKey: string }) {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["asset-issues", assetId],
    queryFn: () => api.get<{ key: string; summary: string; status: string; archived: boolean }[]>(
      `/hardware/assets/${assetId}/issues`),
  });
  return (
    <div className="mt-3 border-t border-line pt-2.5">
      <div className="mb-1 text-xs font-medium text-muted">{tr("hardware.tickets")}</div>
      {data?.length ? (
        <ul className="space-y-1">
          {data.map((i) => (
            <li key={i.key}>
              <button onClick={() => navigate(`/projects/${projectKey}/tickets/${i.key}`)}
                className="flex w-full items-center gap-2 text-left text-xs hover:underline">
                <span className="font-mono text-brand">{i.key}</span>
                <span className={`flex-1 truncate ${i.archived ? "text-muted line-through" : ""}`}>{i.summary}</span>
                <span className="rounded bg-surface px-1.5 py-0.5 text-muted">{i.status}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-muted">{tr("hardware.keine_tickets_an_diesem_exemplar")}</div>
      )}
    </div>
  );
}

interface WfStep { name: string; order: number; assignee: AssigneeSpec }

/** Default procurement steps per project (a template for new units) including the responsible people. */
function WorkflowConfig({ project }: { project: Project }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["hw-workflow", project.id],
    queryFn: () => api.get<WfStep[]>(`/projects/${project.id}/hardware-workflow`),
  });
  const { data: members } = useQuery({
    queryKey: ["members", project.id],
    queryFn: () => api.get<MemberLite[]>(`/projects/${project.id}/members`),
  });
  // Create the draft only on the first edit; until then the server state applies.
  const [entwurf, setEntwurf] = useState<WfStep[] | null>(null);
  const schritte: WfStep[] = entwurf ?? data ?? [];
  const aendern = (next: WfStep[]) => setEntwurf(next.map((s, i) => ({ ...s, order: i })));
  const speichern = useMutation({
    mutationFn: () => api.put(`/projects/${project.id}/hardware-workflow`, {
      steps: schritte
        .filter((s) => s.name.trim())
        .map((s, order) => ({ name: s.name.trim(), order, assignee: s.assignee || {} })),
    }),
    onSuccess: () => { setEntwurf(null); qc.invalidateQueries({ queryKey: ["hw-workflow", project.id] }); },
  });
  // "Edit as a process": creates (idempotently) the workflow definition and opens the editor.
  const alsProzess = useMutation({
    mutationFn: () =>
      api.post<{ definition_id: number; current_version_id: number }>(
        `/projects/${project.id}/hardware-workflow/definition`,
      ),
    onSuccess: (res) => navigate(`/projects/${project.key}/workflows/${res.definition_id}`),
  });
  return (
    <div className="rounded-lg border border-line bg-card p-3">
      <h3 className="mb-1 font-medium">{tr("hardware.beschaffungsprozess")}</h3>
      <p className="mb-2 text-xs text-muted">
        {tr("hardware.prozess_hinweis")}
      </p>
      <div className="space-y-2">
        {schritte.map((s, i) => (
          <div key={i} className="rounded border border-line bg-surface p-2">
            <div className="flex items-center gap-2">
              <span className="w-5 text-xs text-muted">{i + 1}.</span>
              <input value={s.name}
                onChange={(e) => aendern(schritte.map((x, j) => j === i ? { ...x, name: e.target.value } : x))}
                placeholder={tr("hardware.schrittname")}
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-sm" />
              <button title={tr("hardware.nach_oben")} disabled={i === 0}
                onClick={() => { const n = [...schritte]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; aendern(n); }}
                className="text-muted hover:text-ink disabled:opacity-30">↑</button>
              <button title={tr("hardware.nach_unten")} disabled={i === schritte.length - 1}
                onClick={() => { const n = [...schritte]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; aendern(n); }}
                className="text-muted hover:text-ink disabled:opacity-30">↓</button>
              <button title={tr("hardware.schritt_entfernen")}
                onClick={() => aendern(schritte.filter((_, j) => j !== i))}
                className="text-muted hover:text-red-400">✕</button>
            </div>
            <div className="mt-1.5 flex items-center gap-2 pl-7">
              <span className="text-xs text-muted">{tr("hardware.zustaendig")}</span>
              <AssigneeEditor value={s.assignee?.mode ? s.assignee : undefined}
                members={members || []}
                onChange={(v) => aendern(schritte.map((x, j) => j === i ? { ...x, assignee: v } : x))} />
              {s.assignee?.mode && (
                <button onClick={() => aendern(schritte.map((x, j) => j === i ? { ...x, assignee: {} as AssigneeSpec } : x))}
                  className="text-xs text-muted hover:text-ink">{tr("hardware.zuruecksetzen")}</button>
              )}
            </div>
          </div>
        ))}
        {schritte.length === 0 && <div className="text-xs text-muted">{tr("hardware.noch_keine_schritte")}</div>}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button onClick={() => aendern([...schritte, { name: "", order: schritte.length, assignee: {} as AssigneeSpec }])}
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:text-ink">+ {tr("hardware.schritt")}</button>
        <button onClick={() => speichern.mutate()} disabled={!entwurf}
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:text-ink disabled:opacity-40">{tr("hardware.speichern")}</button>
        <button onClick={() => alsProzess.mutate()} disabled={alsProzess.isPending}
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:text-ink disabled:opacity-50"
          title={tr("hardware.diese_schrittliste_als_grafischen_workfl")}>
          🧭 {tr("hardware.als_prozess_bearbeiten")}
        </button>
      </div>
      {alsProzess.error && (
        <p className="mt-1 text-xs text-red-400">
          {alsProzess.error instanceof ApiError ? alsProzess.error.message : tr("hardware.prozess_nicht_offen")}
        </p>
      )}
      <p className="mt-1 text-xs text-muted">
        {tr("hardware.prozess_wirkung")}
      </p>
    </div>
  );
}
