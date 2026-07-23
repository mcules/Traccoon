import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

type Variable = { key: string; label: string; secret: boolean; required: boolean };
const EMPTY = { id: 0, name: "", display_name: "", transport: "http", url: "", variables: [] as Variable[], enabled: true };

export default function McpPanel() {
  const qc = useQueryClient();
  const { data: servers } = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<any[]>("/mcp-servers") });
  const { data: myMcp } = useQuery({ queryKey: ["my-mcp"], queryFn: () => api.get<any>("/me/mcp") });
  const [reach, setReach] = useState<string[] | null>(null);
  const sel = reach ?? (myMcp?.servers as string[] | undefined) ?? [];
  const toggle = (s: string) => setReach(sel.includes(s) ? sel.filter((x) => x !== s) : [...sel, s]);
  const saveReach = useMutation({
    mutationFn: () => api.put("/me/mcp", { servers: sel }),
    onSuccess: () => { setReach(null); setErr(""); qc.invalidateQueries({ queryKey: ["my-mcp"] }); },
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
      {/* Eigene MCP-Reichweite (MCPJungle-Gruppe) — self-service konfigurierbar */}
      <div className="mb-4 rounded-lg border border-line bg-card p-3">
        <div className="mb-1 flex items-center gap-2 text-sm font-medium text-ink">
          🔌 Meine MCP-Reichweite
          {myMcp?.provisioned
            ? <span className="rounded bg-green-600/15 px-1.5 text-xs text-green-400">aktiv{myMcp.group ? ` · ${myMcp.group}` : ""}</span>
            : <span className="rounded bg-yellow-500/15 px-1.5 text-xs text-yellow-400">nicht provisioniert</span>}
        </div>
        <p className="mb-2 text-xs text-muted">
          Diese Server stehen deinen Agenten/dem Assistenten zur Verfügung. Wähle aus, was du freigibst
          — beim Speichern wird deine Gruppe + ein <b>gescopeter</b> Token neu provisioniert.
        </p>
        {(myMcp?.available?.length ?? 0) > 0 ? (
          <>
            <div className="flex flex-wrap gap-1.5">
              {myMcp.available.map((s: string) => {
                const on = sel.includes(s);
                return (
                  <button key={s} onClick={() => toggle(s)}
                    className={`rounded border px-2 py-0.5 text-xs ${on ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
                    {on ? "✓ " : ""}{s}</button>
                );
              })}
            </div>
            {reach !== null && (
              <div className="mt-2 flex items-center gap-2">
                <button onClick={() => saveReach.mutate()} disabled={saveReach.isPending}
                  className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50">
                  {saveReach.isPending ? "Provisioniere…" : "Speichern & provisionieren"}</button>
                <button onClick={() => setReach(null)} className="text-xs text-muted hover:text-ink">verwerfen</button>
              </div>
            )}
          </>
        ) : (
          <div className="text-xs text-muted">
            Keine Server-Liste verfügbar (MCPJungle nicht erreichbar oder kein Admin-Token).
            {(myMcp?.servers?.length ?? 0) > 0 && <> Aktuell: {myMcp.servers.join(", ")}</>}
          </div>
        )}
      </div>

      <p className="mb-3 text-sm text-muted">Eigene MCP-Server (Tool-Anbieter für Agenten). Tools erreichbar unter
        <span className="font-mono"> &lt;name&gt;__&lt;tool&gt;</span>. Definiere <b>Variablen</b> (z. B. Auth) —
        pro Agent legst du dann eine <b>Instanz</b> an und füllst sie aus.</p>
      <p className="mb-3 text-xs text-yellow-400">Nur <b>http</b>/<b>sse</b> werden bedient (kein stdio).</p>

      <div className="mb-4 space-y-2">
        {servers?.map((m) => (
          <div key={m.id} className="flex items-center gap-3 rounded border border-line bg-card p-2 text-sm">
            <span className="font-mono">{m.name}</span><span className="text-muted">{m.transport}</span>
            {(m.variables?.length ?? 0) > 0 && <span className="rounded bg-surface px-1 text-xs">{m.variables.length} Variable(n)</span>}
            {m.enabled && <span className="text-xs text-green-400">aktiv</span>}
            <div className="flex-1" />
            <button onClick={() => setForm({ id: m.id, name: m.name, display_name: m.display_name || "", transport: m.transport, url: m.url || "", variables: m.variables || [], enabled: m.enabled })}
              className="text-brand">bearbeiten</button>
            <button onClick={() => del.mutate(m.id)} className="text-muted hover:text-red-400">löschen</button>
          </div>
        ))}
        {servers?.length === 0 && <div className="text-xs text-muted">Keine MCP-Server.</div>}
      </div>

      {form ? (
        <div className="space-y-2 rounded-lg border border-line bg-card p-3">
          <div className="flex items-center justify-between text-sm font-medium">
            {form.id ? "Server bearbeiten" : "Neuer Server"}
            <button onClick={() => setForm(null)} className="text-muted">✕</button>
          </div>
          <div className="flex flex-wrap gap-2">
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Name (z. B. banking)"
              className="w-40 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
            <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}
              className="rounded border border-line bg-surface px-2 py-1.5 text-sm">
              <option value="http">http</option><option value="sse">sse</option>
            </select>
            <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://…/mcp"
              className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-sm" />
          </div>
          <div>
            <div className="mb-1 text-xs text-muted">Variablen (werden pro Instanz ausgefüllt, als Header angewandt)</div>
            {form.variables.map((v, i) => (
              <div key={i} className="mb-1 flex items-center gap-2">
                <input value={v.key} onChange={(e) => upd(form, setForm, i, { key: e.target.value })} placeholder="Key (z. B. Authorization)"
                  className="w-44 rounded border border-line bg-surface px-2 py-1 text-sm" />
                <input value={v.label} onChange={(e) => upd(form, setForm, i, { label: e.target.value })} placeholder="Label"
                  className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
                <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={v.secret} onChange={(e) => upd(form, setForm, i, { secret: e.target.checked })} />geheim</label>
                <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={v.required} onChange={(e) => upd(form, setForm, i, { required: e.target.checked })} />Pflicht</label>
                <button onClick={() => setForm({ ...form, variables: form.variables.filter((_, j) => j !== i) })} className="text-muted hover:text-red-400">✕</button>
              </div>
            ))}
            <button onClick={() => setForm({ ...form, variables: [...form.variables, { key: "", label: "", secret: true, required: false }] })}
              className="text-xs text-brand hover:underline">+ Variable</button>
          </div>
          {err && <div className="text-sm text-red-400">{err}</div>}
          <button onClick={() => form.name && save.mutate(form)} className="rounded bg-brand px-3 py-1.5 text-sm text-white">Speichern</button>
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
