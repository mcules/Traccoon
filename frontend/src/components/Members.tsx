import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

interface Member {
  id: number; user_id: number; username: string; display_name: string;
  role: string; ai_assign: boolean;
}
const ROLES = ["viewer", "member", "maintainer", "owner"];

export default function Members({ project }: { project: Project }) {
  const qc = useQueryClient();
  const key = ["members", project.id];
  const { data: members } = useQuery({ queryKey: key, queryFn: () => api.get<Member[]>(`/projects/${project.id}/members`) });
  const [uid, setUid] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: key });

  const update = useMutation({
    mutationFn: (v: { user_id: number; body: any }) =>
      api.put(`/projects/${project.id}/members/${v.user_id}`, v.body),
    onSuccess: inv,
  });
  const add = useMutation({
    mutationFn: () => api.post(`/projects/${project.id}/members`, { user_id: Number(uid), role }),
    onSuccess: () => { setUid(""); setErr(""); inv(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const remove = useMutation({
    mutationFn: (user_id: number) => api.del(`/projects/${project.id}/members/${user_id}`),
    onSuccess: inv,
  });

  return (
    <div className="max-w-3xl">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase text-muted">
            <th className="py-2">Mitglied</th><th>Rolle</th>
            <th title="Darf KI nutzen + Tickets zuweisen">KI-Recht</th><th></th>
          </tr>
        </thead>
        <tbody>
          {members?.map((m) => (
            <tr key={m.id} className="border-b border-line">
              <td className="py-2">{m.display_name || m.username} <span className="text-muted">#{m.user_id}</span></td>
              <td>
                <select value={m.role}
                  onChange={(e) => update.mutate({ user_id: m.user_id, body: { role: e.target.value } })}
                  className="rounded border border-line bg-surface px-2 py-1">
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </td>
              <td>
                <input type="checkbox" checked={m.ai_assign}
                  onChange={(e) => update.mutate({ user_id: m.user_id, body: { ai_assign: e.target.checked } })} />
              </td>
              <td className="text-right">
                <button onClick={() => remove.mutate(m.user_id)} className="text-muted hover:text-red-400">Entfernen</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-4 flex items-end gap-2">
        <label className="text-xs text-muted">User-ID
          <input value={uid} onChange={(e) => setUid(e.target.value)}
            className="mt-1 block w-24 rounded border border-line bg-surface px-2 py-1" />
        </label>
        <label className="text-xs text-muted">Rolle
          <select value={role} onChange={(e) => setRole(e.target.value)}
            className="mt-1 block rounded border border-line bg-surface px-2 py-1">
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button onClick={() => uid && add.mutate()} className="rounded bg-brand px-3 py-1.5 text-white">Hinzufügen</button>
        {err && <span className="text-sm text-red-400">{err}</span>}
      </div>
      <p className="mt-3 text-xs text-muted">
        KI-Recht steuert PM-Chat + Agent-Zuweisung. owner/maintainer erhalten es standardmäßig, member/viewer nicht.
      </p>
    </div>
  );
}
