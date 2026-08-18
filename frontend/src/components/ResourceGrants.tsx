import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

interface Grant {
  id: number; project_id: number; user_id: number; username: string; display_name: string;
  resource_type: "location" | "asset"; resource_id: number; resource_label: string;
  level: "view" | "manage"; recursive: boolean;
}
interface Location { id: number; full_path: string; }
interface Asset { id: number; model_id: number; serial_number?: string | null; }
interface HwModel { id: number; name: string; }
interface UserLite { id: number; username: string; display_name: string; }

const LEVELS = ["view", "manage"];

/** Granulare Freigabe eines einzelnen Orts/Exemplars an einen User — ohne volle
 * Projekt-Mitgliedschaft (Wart-Fall: sieht/verwaltet nur sein Wasserhäuschen + Masten). */
export default function ResourceGrants({ project }: { project: Project }) {
  const qc = useQueryClient();
  const key = ["resource-grants", project.id];
  const { data: grants } = useQuery({ queryKey: key, queryFn: () => api.get<Grant[]>(`/projects/${project.id}/resource-grants`) });
  const locations = useQuery({ queryKey: ["hw-locations"], queryFn: () => api.get<Location[]>("/locations") });
  const assets = useQuery({
    queryKey: ["hw-assets", project.id],
    queryFn: () => api.get<Asset[]>(`/hardware/assets?project_id=${project.id}`),
  });

  const models = useQuery({ queryKey: ["hw-models"], queryFn: () => api.get<HwModel[]>("/hardware/models") });
  const modelName = (id: number) => models.data?.find((m) => m.id === id)?.name;
  const assetLabel = (a: Asset) =>
    [modelName(a.model_id) || `Modell #${a.model_id}`, a.serial_number, `#${a.id}`].filter(Boolean).join(" · ");

  // Nutzer über Suche wählen statt roher ID — konsistent zu „Mitglieder".
  const [uid, setUid] = useState("");
  const [uq, setUq] = useState("");
  const userSearch = useQuery({
    queryKey: ["user-search", uq],
    queryFn: () => api.get<UserLite[]>(`/users/search?q=${encodeURIComponent(uq.trim())}`),
    enabled: uq.trim().length >= 1,
  });
  const [rtype, setRtype] = useState<"location" | "asset">("location");
  const [rid, setRid] = useState("");
  const [level, setLevel] = useState("view");
  const [recursive, setRecursive] = useState(true);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: key });

  const add = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/resource-grants`, {
      user_id: Number(uid), resource_type: rtype, resource_id: Number(rid),
      level, recursive,
    }),
    onSuccess: () => { setUid(""); setUq(""); setRid(""); setErr(""); inv(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/projects/${project.id}/resource-grants/${id}`),
    onSuccess: inv,
  });

  return (
    <div className="max-w-3xl">
      <p className="mb-3 text-xs text-muted">
        {tr("resource_grants.einleitung")}
      </p>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase text-muted">
            <th className="py-2">{tr("resource_grants.nutzer")}</th><th>{tr("resource_grants.objekt")}</th><th>{tr("resource_grants.stufe")}</th><th>{tr("resource_grants.rekursiv")}</th><th></th>
          </tr>
        </thead>
        <tbody>
          {grants?.map((g) => (
            <tr key={g.id} className="border-b border-line">
              <td className="py-2">{g.display_name || g.username} <span className="text-muted">#{g.user_id}</span></td>
              <td>
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs">{g.resource_type}</span> {g.resource_label}
              </td>
              <td>{g.level}</td>
              <td>{g.resource_type === "location" ? (g.recursive ? "ja" : "nein") : "—"}</td>
              <td className="text-right">
                <button onClick={() => remove.mutate(g.id)} className="text-muted hover:text-red-400">{tr("resource_grants.entziehen")}</button>
              </td>
            </tr>
          ))}
          {grants?.length === 0 && (
            <tr><td colSpan={5} className="py-2 text-xs text-muted">{tr("resource_grants.keine_freigaben")}</td></tr>
          )}
        </tbody>
      </table>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="relative block text-xs text-muted">Nutzer
          <input value={uq} onChange={(e) => { setUq(e.target.value); setUid(""); }}
            placeholder={tr("resource_grants.name_suchen")}
            className="mt-1 block w-44 rounded border border-line bg-surface px-2 py-1" />
          {uq.trim() && !uid && (
            <div className="absolute z-10 mt-1 max-h-48 w-44 overflow-auto rounded border border-line bg-surface">
              {userSearch.data?.map((u) => (
                <button key={u.id} type="button"
                  onClick={() => { setUid(String(u.id)); setUq(u.display_name || u.username); }}
                  className="block w-full px-2 py-1 text-left text-xs hover:bg-card">
                  {u.display_name || u.username} <span className="text-muted">@{u.username}</span>
                </button>
              ))}
              {userSearch.data?.length === 0 && (
                <div className="px-2 py-1 text-xs text-muted">{tr("resource_grants.kein_treffer")}</div>
              )}
            </div>
          )}
        </label>
        <label className="text-xs text-muted">Objektart
          <select value={rtype} onChange={(e) => { setRtype(e.target.value as "location" | "asset"); setRid(""); }}
            className="mt-1 block rounded border border-line bg-surface px-2 py-1">
            <option value="location">{tr("resource_grants.ort")}</option>
            <option value="asset">{tr("resource_grants.exemplar")}</option>
          </select>
        </label>
        <label className="text-xs text-muted">Objekt
          <select value={rid} onChange={(e) => setRid(e.target.value)}
            className="mt-1 block w-48 rounded border border-line bg-surface px-2 py-1">
            <option value="">{tr("resource_grants.auswaehlen")}</option>
            {rtype === "location"
              ? locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)
              : assets.data?.map((a) => <option key={a.id} value={a.id}>{assetLabel(a)}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">Stufe
          <select value={level} onChange={(e) => setLevel(e.target.value)}
            className="mt-1 block rounded border border-line bg-surface px-2 py-1">
            {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        {rtype === "location" && (
          <label className="flex items-center gap-1 text-xs text-muted">
            <input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} />
            {tr("resource_grants.auch_kind_orte")}
          </label>
        )}
        <button onClick={() => uid && rid && add.mutate()} className="rounded bg-brand px-3 py-1.5 text-white">
          Freigeben
        </button>
        {err && <span className="text-sm text-red-400">{err}</span>}
      </div>
    </div>
  );
}
