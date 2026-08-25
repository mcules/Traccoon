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
import { ICON, IconButton, DeleteDialog, Area, Tag, Errorrow, Listing, ListingEmpty, ListRow, BUTTON, BUTTON_SMALL, BUTTON_TEXT} from "./ui";

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
  const canManage = project.my_role === "maintainer" || project.my_role === "owner";
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
  const [open, setOpen] = useState<number | null>(null);
  const [deleteAsset, setDeleteAsset] = useState<any | null>(null);
  const [deleteModel, setDeleteModel] = useState<any | null>(null);
  const [deletePlace, setDeletePlace] = useState<any | null>(null);
  const [view, setView] = useState<"klassisch" | "workflow">("klassisch");
  const [err, setErr] = useState("");
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const invAssets = () => qc.invalidateQueries({ queryKey: ["hw-assets", project.id] });
  const addModel = useMutation({
    mutationFn: () => api.post("/hardware/models", { name: mName }),
    onSuccess: () => { setMName(""); qc.invalidateQueries({ queryKey: ["hw-models"] }); }, onError: error,
  });
  const delModel = useMutation({
    mutationFn: (id: number) => api.del(`/hardware/models/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hw-models"] }), onError: error,
  });
  const addLoc = useMutation({
    mutationFn: () => api.post("/locations", { name: lName, type: lType, parent_id: lParent ? Number(lParent) : null }),
    onSuccess: () => { setLName(""); qc.invalidateQueries({ queryKey: ["hw-locations"] }); }, onError: error,
  });
  const delLoc = useMutation({
    mutationFn: (id: number) => api.del(`/locations/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hw-locations"] }), onError: error,
  });
  const addAsset = useMutation({
    mutationFn: () => api.post("/hardware/assets", {
      model_id: Number(aModel), project_id: project.id,
      location_id: aLoc ? Number(aLoc) : null, purchase_status: aStatus,
    }),
    onSuccess: invAssets, onError: error,
  });
  const delAsset = useMutation({
    mutationFn: (id: number) => api.del(`/hardware/assets/${id}`),
    onSuccess: () => { setOpen(null); invAssets(); }, onError: error,
  });

  return (
    <div>
      <Errorrow text={err} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Area title={tr("hardware.items_project")}>
          <Listing>
            {assets.data?.map((a) => (
              <ListRow key={a.id}>
                <div className="flex items-start gap-2">
                  <button onClick={() => setOpen(open === a.id ? null : a.id)} className="min-w-0 flex-1 text-left">
                    <div className="truncate font-medium text-ink">{modelName(a.model_id)}</div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                      <Tag>{a.purchase_status}</Tag>
                      <span>📍 {locPath(a.location_id)}</span>
                      {a.serial_number && <span>SN {a.serial_number}</span>}
                      <span className="text-brand">{open === a.id ? "▾ Beschaffung" : "▸ Beschaffung"}</span>
                    </div>
                  </button>
                  {canManage && (
                    <IconButton icon={ICON.remove} title={tr("hardware.delete_item")} danger
                      onClick={() => setDeleteAsset(a)} />
                  )}
                </div>
                {open === a.id && (
                  <div className="mt-2 border-t border-line pt-2.5">
                    <div className="mb-2.5 inline-flex rounded border border-line bg-surface p-0.5 text-xs">
                      <button
                        onClick={() => setView("klassisch")}
                        className={`rounded px-2 py-0.5 ${view === "klassisch" ? "bg-brand text-white" : "text-muted hover:text-ink"}`}
                      >
                        Klassische Schritte
                      </button>
                      <button
                        onClick={() => setView("workflow")}
                        className={`rounded px-2 py-0.5 ${view === "workflow" ? "bg-brand text-white" : "text-muted hover:text-ink"}`}
                      >
                        🧭 Workflow
                      </button>
                    </div>
                    {view === "klassisch" ? (
                      <AssetProcurement assetId={a.id} project={project} onChange={invAssets} />
                    ) : (
                      <AssetWorkflow assetId={a.id} projectId={a.project_id} assetLabel={modelName(a.model_id)} />
                    )}
                    {a.artifact_id && (
                      <div className="mt-3 rounded-lg border border-line p-3">
                        <div className="mb-2 text-xs font-medium text-muted">{tr("hardware.fields")}</div>
                        <ArtifactFields artifactId={a.artifact_id} compact />
                      </div>
                    )}
                    <AssetIssues assetId={a.id} projectKey={project.key} />
                  </div>
                )}
              </ListRow>
            ))}
            {assets.data?.length === 0 && <ListingEmpty>{tr("hardware.no_items")}</ListingEmpty>}
          </Listing>
          <div className="flex flex-wrap items-end gap-2 rounded-lg border border-line bg-surface p-3">
            <select value={aModel} onChange={(e) => setAModel(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("hardware.model")}</option>
              {models.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
            <select value={aLoc} onChange={(e) => setALoc(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              <option value="">{tr("hardware.location")}</option>
              {locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)}
            </select>
            <select value={aStatus} onChange={(e) => setAStatus(e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-sm">
              {STATUS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button onClick={() => aModel && addAsset.mutate()} className={BUTTON.primary}>
              + Exemplar
            </button>
          </div>
        </Area>

        <section className="space-y-4">
          {canManage && <WorkflowConfig project={project} />}

          <Area title={tr("hardware.catalog_models")}>
            <Listing>
              {models.data?.map((m) => (
                <ListRow key={m.id}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-ink">{m.name}</span>
                    {canManage && (
                      <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                        onClick={() => setDeleteModel(m)} />
                    )}
                  </div>
                </ListRow>
              ))}
              {models.data?.length === 0 && <ListingEmpty>{tr("hardware.no_model_in_catalog")}</ListingEmpty>}
            </Listing>
            <div className="flex gap-2">
              <input value={mName} onChange={(e) => setMName(e.target.value)} placeholder={tr("hardware.model_name")}
                className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
              <button onClick={() => mName && addModel.mutate()} className={BUTTON.primary}>+</button>
            </div>
          </Area>

          <Area title={tr("hardware.locations")}>
            <Listing>
              {locations.data?.map((l) => (
                <ListRow key={l.id}>
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">📍 {l.full_path}</span>
                    <Tag>{l.type}</Tag>
                    {canManage && (
                      <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                        onClick={() => setDeletePlace(l)} />
                    )}
                  </div>
                </ListRow>
              ))}
              {locations.data?.length === 0 && <ListingEmpty>{tr("hardware.no_location_yet")}</ListingEmpty>}
            </Listing>
            <div className="flex flex-wrap gap-2">
              <input value={lName} onChange={(e) => setLName(e.target.value)} placeholder={tr("hardware.location_name")}
                className="rounded border border-line bg-surface px-2 py-1 text-sm" />
              <select value={lType} onChange={(e) => setLType(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                {LOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select value={lParent} onChange={(e) => setLParent(e.target.value)}
                className="rounded border border-line bg-surface px-2 py-1 text-sm">
                <option value="">{tr("hardware.parent")}</option>
                {locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)}
              </select>
              <button onClick={() => lName && addLoc.mutate()} className={BUTTON.primary}>+</button>
            </div>
          </Area>
        </section>
      </div>
      {deleteAsset && (
        <DeleteDialog was={modelName(deleteAsset.model_id)} hint={tr("hardware.delete_item")}
          onClose={() => setDeleteAsset(null)}
          onDelete={() => { delAsset.mutate(deleteAsset.id); setDeleteAsset(null); }} />
      )}
      {deleteModel && (
        <DeleteDialog was={deleteModel.name}
          onClose={() => setDeleteModel(null)}
          onDelete={() => { delModel.mutate(deleteModel.id); setDeleteModel(null); }} />
      )}
      {deletePlace && (
        <DeleteDialog was={deletePlace.full_path}
          onClose={() => setDeletePlace(null)}
          onDelete={() => { delLoc.mutate(deletePlace.id); setDeletePlace(null); }} />
      )}
    </div>
  );
}

/** Tickets that hang off this unit, the opposite direction to the hardware field in the ticket. */
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
                <Tag>{i.status}</Tag>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-muted">{tr("hardware.no_tickets_on_this_item")}</div>
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
  const [draft, setDraft] = useState<WfStep[] | null>(null);
  const steps: WfStep[] = draft ?? data ?? [];
  const update = (next: WfStep[]) => setDraft(next.map((s, i) => ({ ...s, order: i })));
  const save = useMutation({
    mutationFn: () => api.put(`/projects/${project.id}/hardware-workflow`, {
      steps: steps
        .filter((s) => s.name.trim())
        .map((s, order) => ({ name: s.name.trim(), order, assignee: s.assignee || {} })),
    }),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ["hw-workflow", project.id] }); },
  });
  // "Edit as a process": creates (idempotently) the workflow definition and opens the editor.
  const asFlow = useMutation({
    mutationFn: () =>
      api.post<{ definition_id: number; current_version_id: number }>(
        `/projects/${project.id}/hardware-workflow/definition`,
      ),
    onSuccess: (res) => navigate(`/projects/${project.key}/workflows/${res.definition_id}`),
  });
  return (
    <Area title={tr("hardware.procurement_flow")} hint={tr("hardware.steps_every_new_item")}>
      <Listing>
        {steps.map((s, i) => (
          <ListRow key={i}>
            <div className="flex items-center gap-2">
              <span className="w-5 text-xs text-muted">{i + 1}.</span>
              <input value={s.name}
                onChange={(e) => update(steps.map((x, j) => j === i ? { ...x, name: e.target.value } : x))}
                placeholder={tr("hardware.step_name")}
                className="flex-1 rounded border border-line bg-card px-2 py-1 text-sm" />
              <button title={tr("hardware.up")} disabled={i === 0}
                onClick={() => { const n = [...steps]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; update(n); }}
                className={BUTTON_TEXT.secondary}>↑</button>
              <button title={tr("hardware.down")} disabled={i === steps.length - 1}
                onClick={() => { const n = [...steps]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; update(n); }}
                className={BUTTON_TEXT.secondary}>↓</button>
              <IconButton icon={ICON.remove} title={tr("hardware.remove_step")} danger
                onClick={() => update(steps.filter((_, j) => j !== i))} />
            </div>
            <div className="mt-1.5 flex items-center gap-2 pl-7">
              <span className="text-xs text-muted">{tr("hardware.responsible")}</span>
              <AssigneeEditor value={s.assignee?.mode ? s.assignee : undefined}
                members={members || []}
                onChange={(v) => update(steps.map((x, j) => j === i ? { ...x, assignee: v } : x))} />
              {s.assignee?.mode && (
                <button onClick={() => update(steps.map((x, j) => j === i ? { ...x, assignee: {} as AssigneeSpec } : x))}
                  className={BUTTON_TEXT.secondary}>{tr("hardware.reset")}</button>
              )}
            </div>
          </ListRow>
        ))}
        {steps.length === 0 && <ListingEmpty>{tr("hardware.no_steps_yet")}</ListingEmpty>}
      </Listing>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => update([...steps, { name: "", order: steps.length, assignee: {} as AssigneeSpec }])}
          className={BUTTON_SMALL.secondary}>+ {tr("hardware.step")}</button>
        <button onClick={() => save.mutate()} disabled={!draft}
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:text-ink disabled:opacity-40">{tr("hardware.save")}</button>
        <button onClick={() => asFlow.mutate()} disabled={asFlow.isPending}
          className={BUTTON_SMALL.secondary}
          title={tr("hardware.edit_this_step_list_as_a_graphical_flow")}>
          🧭 {tr("hardware.edit_process")}
        </button>
      </div>
      {asFlow.error && (
        <p className="mt-1 text-xs text-red-400">
          {asFlow.error instanceof ApiError ? asFlow.error.message : tr("hardware.not_open_process")}
        </p>
      )}
      <p className="mt-1 text-xs text-muted">
        {tr("hardware.applies_future_items_existing")}
      </p>
    </Area>
  );
}
