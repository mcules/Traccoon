import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

interface Grant {
  id: number; project_id: number; user_id: number; username: string; display_name: string;
  resource_type: "location" | "asset"; resource_id: number; resource_label: string;
  level: "view" | "manage"; recursive: boolean;
}
interface Location { id: number; full_path: string; }
interface Asset { id: number; model_id: number; }

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

  const [uid, setUid] = useState("");
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
    onSuccess: () => { setUid(""); setRid(""); setErr(""); inv(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/projects/${project.id}/resource-grants/${id}`),
    onSuccess: inv,
  });

  return (
    <div className="max-w-3xl">
      <p className="mb-3 text-xs text-muted">
        Feingranularer Zugriff auf einen einzelnen Ort oder ein Exemplar — ohne dass der Nutzer
        volles Projekt-Mitglied wird (z. B. „Wart“ nur fürs Wasserhäuschen samt Masten).
      </p>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase text-muted">
            <th className="py-2">Nutzer</th><th>Objekt</th><th>Stufe</th><th>Rekursiv</th><th></th>
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
                <button onClick={() => remove.mutate(g.id)} className="text-muted hover:text-red-400">Entziehen</button>
              </td>
            </tr>
          ))}
          {grants?.length === 0 && (
            <tr><td colSpan={5} className="py-2 text-xs text-muted">Keine Freigaben.</td></tr>
          )}
        </tbody>
      </table>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="text-xs text-muted">User-ID
          <input value={uid} onChange={(e) => setUid(e.target.value)}
            className="mt-1 block w-24 rounded border border-line bg-surface px-2 py-1" />
        </label>
        <label className="text-xs text-muted">Objektart
          <select value={rtype} onChange={(e) => { setRtype(e.target.value as "location" | "asset"); setRid(""); }}
            className="mt-1 block rounded border border-line bg-surface px-2 py-1">
            <option value="location">Ort</option>
            <option value="asset">Exemplar</option>
          </select>
        </label>
        <label className="text-xs text-muted">Objekt
          <select value={rid} onChange={(e) => setRid(e.target.value)}
            className="mt-1 block w-48 rounded border border-line bg-surface px-2 py-1">
            <option value="">— auswählen —</option>
            {rtype === "location"
              ? locations.data?.map((l) => <option key={l.id} value={l.id}>{l.full_path}</option>)
              : assets.data?.map((a) => <option key={a.id} value={a.id}>Exemplar #{a.id}</option>)}
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
            gilt auch für Kind-Orte
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
