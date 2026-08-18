import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

type Variable = { key: string; label: string; secret: boolean; required: boolean };
const EMPTY = { id: 0, name: "", display_name: "", transport: "http", url: "", variables: [] as Variable[], enabled: true };

export default function McpPanel() {
  const qc = useQueryClient();
  const { data: servers } = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<any[]>("/mcp-servers") });
  const { data: myMcp } = useQuery({ queryKey: ["my-mcp"], queryFn: () => api.get<any>("/me/mcp") });
  const importMcp = useMutation({
    mutationFn: () => api.post("/me/mcp/import"),
    onSuccess: () => { setErr(""); inv(); qc.invalidateQueries({ queryKey: ["my-mcp"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["mcp"] });
  const save = useMutation({
    mutationFn: (f: typeof EMPTY) => {
      const body = { name: f.name, display_name: f.display_name, transport: f.transport, url: f.url, variables: f.variables, enabled: f.enabled };
      return f.id ? api.put(`/mcp-servers/${f.id}`, body) : api.post("/mcp-servers", body);
    },
    onSuccess: () => { setForm(null); setErr(""); inv(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/mcp-servers/${id}`), onSuccess: inv,
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler") });

  return (
    <div>
      {/* Verfügbare MCP-Server aus MCPJungle als echte Registry-Einträge übernehmen */}
      {(myMcp?.available?.length ?? 0) > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-card p-3">
          <div className="text-sm">
            <span className="font-medium text-ink">🔌 {myMcp.available.length} MCP-Server verfügbar</span>
            {myMcp?.provisioned && <span className="ml-2 rounded bg-yellow-500/15 px-1.5 text-xs text-yellow-400">{tr("mcp_panel.gateway_gruppe_aktiv")}</span>}
            <p className="text-xs text-muted">{tr("mcp_panel.als_echte_editierbare_server_eintraege_u")}</p>
          </div>
          <div className="flex-1" />
          <button onClick={() => importMcp.mutate()} disabled={importMcp.isPending}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white disabled:opacity-50">
            {tr(importMcp.isPending ? "mcp_panel.uebernehme" : "mcp_panel.server_uebernehmen")}</button>
        </div>
      )}

      <p className="mb-3 text-sm text-muted">{tr("mcp_panel.einleitung")}</p>
      <p className="mb-3 text-xs text-yellow-400">{tr("mcp_panel.nur_http_sse")}</p>

      <div className="mb-4 space-y-2">
        {servers?.map((m) => (
          <div key={m.id} className="flex items-center gap-3 rounded border border-line bg-card p-2 text-sm">
            <span className="font-mono">{m.name}</span><span className="text-muted">{m.transport}</span>
            {(m.variables?.length ?? 0) > 0 && <span className="rounded bg-surface px-1 text-xs">{tr("mcp_panel.variablen_anzahl", { anzahl: m.variables.length })}</span>}
            {m.enabled && <span className="text-xs text-green-400">{tr("mcp_panel.aktiv")}</span>}
            <div className="flex-1" />
            <button onClick={() => setForm({ id: m.id, name: m.name, display_name: m.display_name || "", transport: m.transport, url: m.url || "", variables: m.variables || [], enabled: m.enabled })}
              className="text-brand">{tr("common.bearbeiten")}</button>
            <button onClick={() => del.mutate(m.id)} className="text-muted hover:text-red-400">{tr("common.loeschen_klein")}</button>
          </div>
        ))}
        {servers?.length === 0 && <div className="text-xs text-muted">{tr("mcp_panel.keine_mcp_server")}</div>}
      </div>

      {form ? (
        <div className="space-y-2 rounded-lg border border-line bg-card p-3">
          <div className="flex items-center justify-between text-sm font-medium">
            {form.id ? "Server bearbeiten" : "Neuer Server"}
            <button onClick={() => setForm(null)} className="text-muted">✕</button>
          </div>
          <div className="flex flex-wrap gap-2">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={tr("mcp_panel.name_z_b_banking")}
              className="w-40 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
            <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}
              className="rounded border border-line bg-surface px-2 py-1.5 text-sm">
              <option value="http">http</option><option value="sse">sse</option>
            </select>
            <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://…/mcp"
              className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
          </div>
          <div>
            <div className="mb-1 text-xs text-muted">{tr("mcp_panel.variablen_werden_pro_instanz_ausgefuellt")}</div>
            {form.variables.map((v, i) => (
              <div key={i} className="mb-1 flex items-center gap-2">
                <input value={v.key} onChange={(e) => upd(form, setForm, i, { key: e.target.value })} placeholder={tr("mcp_panel.key_z_b_authorization")}
                  className="w-44 rounded border border-line bg-surface px-2 py-1 text-sm" />
                <input value={v.label} onChange={(e) => upd(form, setForm, i, { label: e.target.value })} placeholder={tr("mcp_panel.label")}
                  className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
                <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={v.secret} onChange={(e) => upd(form, setForm, i, { secret: e.target.checked })} />{tr("mcp_panel.geheim")}</label>
                <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={v.required} onChange={(e) => upd(form, setForm, i, { required: e.target.checked })} />{tr("mcp_panel.pflicht")}</label>
                <button onClick={() => setForm({ ...form, variables: form.variables.filter((_, j) => j !== i) })} className="text-muted hover:text-red-400">✕</button>
              </div>
            ))}
            <button onClick={() => setForm({ ...form, variables: [...form.variables, { key: "", label: "", secret: true, required: false }] })}
              className="text-xs text-brand hover:underline">+ Variable</button>
          </div>
          {err && <div className="text-sm text-red-400">{err}</div>}
          <button onClick={() => form.name && save.mutate(form)} className="rounded bg-brand px-3 py-1.5 text-sm text-white">{tr("mcp_panel.speichern")}</button>
        </div>
      ) : (
        <button onClick={() => setForm({ ...EMPTY, variables: [] })} className="rounded bg-brand px-3 py-1.5 text-sm text-white">+ Server</button>
      )}
    </div>
  );
}

function upd(form: any, setForm: (f: any) => void, i: number, patch: Partial<Variable>) {
  setForm({ ...form, variables: form.variables.map((v: Variable, j: number) => j === i ? { ...v, ...patch } : v) });
}
