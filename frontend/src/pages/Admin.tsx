import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

type Tab = "users" | "cost";
const TABS: [Tab, string][] = [
  ["users", "Nutzer"], ["cost", "Kosten"],
];

export default function Admin() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Administration</h1>
      <div className="mb-4 flex gap-1 border-b border-line">
        {TABS.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {label}</button>
        ))}
      </div>
      {tab === "users" && <Users />}
      {tab === "cost" && <Cost />}
    </div>
  );
}

function Users() {
  const qc = useQueryClient();
  const { data: users } = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<any[]>("/users") });
  const act = useMutation({
    mutationFn: (v: { id: number; path: string }) => api.post(`/users/${v.id}/${v.path}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const [mcpFor, setMcpFor] = useState<number | null>(null);
  return (
    <table className="w-full text-sm">
      <thead><tr className="border-b border-line text-left text-xs uppercase text-muted">
        <th className="py-2">Nutzer</th><th>Rolle</th><th>Status</th><th></th></tr></thead>
      <tbody>
        {users?.map((u) => (
          <>
          <tr key={u.id} className="border-b border-line">
            <td className="py-2">{u.display_name} <span className="text-muted">({u.email})</span></td>
            <td>{u.global_role}</td><td>{u.status}</td>
            <td className="space-x-3 text-right">
              <button onClick={() => setMcpFor(mcpFor === u.id ? null : u.id)} className="text-muted hover:text-ink">MCP</button>
              {u.status === "pending" && <button onClick={() => act.mutate({ id: u.id, path: "approve" })} className="text-brand">freischalten</button>}
              {u.status === "active" && <button onClick={() => act.mutate({ id: u.id, path: "disable" })} className="text-muted hover:text-red-400">sperren</button>}
            </td>
          </tr>
          {mcpFor === u.id && (
            <tr><td colSpan={4} className="bg-card px-2 py-2"><McpAssign userId={u.id} /></td></tr>
          )}
          </>
        ))}
      </tbody>
    </table>
  );
}

/** MCP-Server-Zuteilung je User (harte Trennung — Token erzeugt das provision-Skript). */
function McpAssign({ userId }: { userId: number }) {
  const { data, refetch } = useQuery({
    queryKey: ["user-mcp", userId], queryFn: () => api.get<any>(`/users/${userId}/mcp`),
  });
  const [text, setText] = useState<string | null>(null);
  const val = text ?? (data ? (data.servers || []).join(", ") : "");
  const save = async () => {
    const servers = val.split(",").map((s: string) => s.trim()).filter(Boolean);
    await api.put(`/users/${userId}/mcp-servers`, { servers });
    setText(null); refetch();
  };
  return (
    <div className="text-xs">
      <div className="mb-1 text-muted">Erlaubte MCP-Server (Komma) — z. B. obsidian, imap, paperless, banking, homeassistant, gameproj.
        {data?.provisioned
          ? <span className="ml-1 text-green-400">· provisioniert (Gruppe {data.group})</span>
          : <span className="ml-1 text-yellow-400">· noch nicht provisioniert</span>}
      </div>
      <div className="flex gap-2">
        <input value={val} onChange={(e) => setText(e.target.value)}
          className="flex-1 rounded border border-line bg-surface px-2 py-1" />
        <button onClick={save} className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
      </div>
      <div className="mt-1 text-muted">Danach auf dem Host <code>python3 scripts/provision_mcp.py</code> ausführen —
        das legt Gruppe + Token in MCPJungle an und schreibt sie dem User zu.</div>
    </div>
  );
}

function Cost() {
  const { data } = useQuery({ queryKey: ["cost-global"], queryFn: () => api.get<any>("/costs/global") });
  return (
    <div>
      <div className="mb-3 text-2xl font-semibold">${data?.total_usd?.toFixed(4) ?? "0"}</div>
      <table className="w-full text-sm">
        <thead><tr className="border-b border-line text-left text-xs uppercase text-muted"><th className="py-2">Modell</th><th>USD</th><th>Calls</th></tr></thead>
        <tbody>{data?.by_model?.map((m: any) => (
          <tr key={m.model} className="border-b border-line"><td className="py-2">{m.model}</td><td>${m.usd}</td><td>{m.calls}</td></tr>
        ))}</tbody>
      </table>
      {(!data?.by_model || data.by_model.length === 0) && <div className="text-sm text-muted">Noch keine Kosten.</div>}
    </div>
  );
}
