import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

interface Member {
  id: number; user_id: number; username: string; display_name: string;
  role: string; ai_assign: boolean;
}
interface Invitation {
  id: number; project_id: number; email: string; role: string;
  status: string; created_at: string; expires_at: string | null;
}
const ROLES = ["viewer", "member", "maintainer", "owner"];

export default function Members({ project }: { project: Project }) {
  const qc = useQueryClient();
  const key = ["members", project.id];
  const invKey = ["invitations", project.id];
  const { data: members } = useQuery({ queryKey: key, queryFn: () => api.get<Member[]>(`/projects/${project.id}/members`) });
  const { data: invitations } = useQuery({
    queryKey: invKey,
    queryFn: () => api.get<Invitation[]>(`/projects/${project.id}/invitations`),
  });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");
  const [q, setQ] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: key });
  const invInv = () => qc.invalidateQueries({ queryKey: invKey });

  const update = useMutation({
    mutationFn: (v: { user_id: number; body: any }) =>
      api.put(`/projects/${project.id}/members/${v.user_id}`, v.body),
    onSuccess: inv,
  });
  const invite = useMutation({
    mutationFn: () => api.post<{ status: string }>(`/projects/${project.id}/invitations`, { email, role }),
    onSuccess: (res) => {
      setEmail(""); setErr("");
      setInfo(res.status === "added"
        ? "Bereits registrierter User wurde direkt zugeordnet."
        : "Einladung per E-Mail versendet.");
      setTimeout(() => setInfo(""), 4000);
      inv(); invInv();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/projects/${project.id}/invitations/${id}`),
    onSuccess: invInv,
  });
  const remove = useMutation({
    mutationFn: (user_id: number) => api.del(`/projects/${project.id}/members/${user_id}`),
    onSuccess: inv,
  });

  // Nutzersuche (Benutzername/Anzeigename) zum Direkt-Hinzufügen bestehender Konten.
  const search = useQuery({
    queryKey: ["user-search", q],
    queryFn: () => api.get<{ id: number; username: string; display_name: string; status: string }[]>(
      `/users/search?q=${encodeURIComponent(q.trim())}`),
    enabled: q.trim().length >= 1,
  });
  const addExisting = useMutation({
    mutationFn: (user_id: number) => api.post(`/projects/${project.id}/members`, { user_id, role }),
    onSuccess: () => {
      setErr(""); setInfo("Nutzer hinzugefügt."); setTimeout(() => setInfo(""), 3000); setQ(""); inv();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const pending = invitations?.filter((i) => i.status === "pending") || [];
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const memberIds = new Set(members?.map((m) => m.user_id) || []);
  const matches = (search.data || []).filter((u) => !memberIds.has(u.id));

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

      {/* Bestehenden Nutzer per Benutzername/Anzeigename suchen und direkt hinzufügen */}
      <div className="mt-5">
        <label className="text-xs text-muted">Bestehenden Nutzer hinzufügen (Benutzername oder Anzeigename)
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name eingeben…"
            className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1" />
        </label>
        {q.trim().length >= 1 && (
          <div className="mt-1 divide-y divide-line rounded border border-line bg-card">
            {matches.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted">
                {search.isFetching ? "Suche…" : "Keine passenden Nutzer."}
              </div>
            )}
            {matches.map((u) => (
              <div key={u.id} className="flex items-center gap-2 px-2 py-1.5 text-sm">
                <span className="flex-1 truncate">
                  {u.display_name || u.username} <span className="text-muted">@{u.username}</span>
                  {u.status !== "active" && <span className="ml-1 text-xs text-muted">({u.status})</span>}
                </span>
                <button onClick={() => addExisting.mutate(u.id)}
                  className="rounded bg-brand px-2 py-1 text-xs text-white">+ als {role}</button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 flex items-end gap-2">
        <label className="flex-1 text-xs text-muted">…oder per E-Mail einladen
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com"
            className="mt-1 block w-full rounded border border-line bg-surface px-2 py-1" />
        </label>
        <label className="text-xs text-muted">Rolle
          <select value={role} onChange={(e) => setRole(e.target.value)}
            className="mt-1 block rounded border border-line bg-surface px-2 py-1">
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <button disabled={!emailValid} onClick={() => emailValid && invite.mutate()}
          className="rounded bg-brand px-3 py-1.5 text-white disabled:opacity-50">
          Einladen
        </button>
        {err && <span className="text-sm text-red-400">{err}</span>}
      </div>
      {info && <div className="mt-2 text-sm text-green-400">{info}</div>}
      <p className="mt-3 text-xs text-muted">
        Ist die E-Mail-Adresse bereits bei Traccoon registriert, wird der User direkt zugeordnet.
        Sonst erhält er eine Einladungsmail mit Link zur Anmeldung/Registrierung.
        KI-Recht steuert PM-Chat + Agent-Zuweisung. owner/maintainer erhalten es standardmäßig, member/viewer nicht.
      </p>

      {pending.length > 0 && (
        <div className="mt-6">
          <div className="mb-2 text-xs font-medium uppercase text-muted">Offene Einladungen</div>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase text-muted">
                <th className="py-2">E-Mail</th><th>Rolle</th><th>Läuft ab</th><th></th>
              </tr>
            </thead>
            <tbody>
              {pending.map((i) => (
                <tr key={i.id} className="border-b border-line">
                  <td className="py-2">{i.email}</td>
                  <td>{i.role}</td>
                  <td className="text-muted">{i.expires_at ? new Date(i.expires_at).toLocaleDateString() : "—"}</td>
                  <td className="text-right">
                    <button onClick={() => revoke.mutate(i.id)} className="text-muted hover:text-red-400">
                      Widerrufen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
