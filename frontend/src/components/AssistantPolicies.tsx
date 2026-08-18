import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";

interface Policy {
  id: number; match_kind: string; match_value: string;
  auto_approve: boolean; redaction: string; action_hint: string;
  enabled: boolean; hit_count: number; last_used_at: string | null; created_at: string;
}

const KIND_LABEL: Record<string, string> = { sender: "Absender", domain: "Domain", category: "Kategorie" };

export default function AssistantPolicies() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data = [], isLoading } = useQuery({
    queryKey: ["policies"], queryFn: () => api.get<Policy[]>("/assistant/policies"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["policies"] });
  const guard = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const save = useMutation({
    mutationFn: (p: Partial<Policy> & { id?: number }) =>
      p.id ? api.put(`/assistant/policies/${p.id}`, p) : api.post("/assistant/policies", p),
    onSuccess: inv, onError: guard,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/assistant/policies/${id}`), onSuccess: inv, onError: guard,
  });

  return (
    <div className="space-y-6">
      <ToolPermissions />
      <div className="space-y-3">
      <div className="text-sm font-medium text-ink">📥 Eingangs-Regeln (Mail)</div>
      <p className="text-sm text-muted">
        {tr("assistant_policies.einleitung")}
      </p>
      {err && <div className="rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}

      {isLoading && <div className="text-sm text-muted">{tr("assistant_policies.laedt")}</div>}
      {!isLoading && data.length === 0 && (
        <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">
          {tr("assistant_policies.keine_regeln")}
        </div>
      )}

      <div className="space-y-2">
        {data.map((p) => (
          <div key={p.id} className={`rounded-lg border border-line bg-card p-3 text-sm ${p.enabled ? "" : "opacity-60"}`}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded bg-surface px-1.5 text-xs text-muted">{KIND_LABEL[p.match_kind] || p.match_kind}</span>
              <span className="font-medium text-ink">{p.match_value}</span>
              <span className={`rounded px-1.5 text-xs ${p.auto_approve ? "bg-green-600/15 text-green-400" : "bg-surface text-muted"}`}>
                {tr(p.auto_approve ? "assistant_policies.auto_freigabe" : "assistant_policies.nur_vorgabe")}</span>
              <span className={`rounded px-1.5 text-xs ${p.redaction === "unredacted" ? "bg-amber-500/15 text-amber-400" : "bg-surface text-muted"}`}>
                {tr(p.redaction === "unredacted" ? "assistant.ungeschwaerzt" : "assistant.geschwaerzt")}</span>
              <span className="ml-auto text-xs text-muted">{p.hit_count}×</span>
            </div>
            {p.action_hint && <p className="mt-1 text-xs text-muted">↳ {p.action_hint}</p>}
            <div className="mt-2 flex gap-2">
              <button onClick={() => save.mutate({ ...p, auto_approve: !p.auto_approve })}
                className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                {tr(p.auto_approve ? "assistant_policies.auto_aus" : "assistant_policies.auto_an")}</button>
              <button onClick={() => save.mutate({ ...p, redaction: p.redaction === "unredacted" ? "redacted" : "unredacted" })}
                className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                {p.redaction === "unredacted" ? `→ ${tr("assistant.geschwaerzt")}` : `→ ${tr("assistant.ungeschwaerzt")}`}</button>
              <button onClick={() => save.mutate({ ...p, enabled: !p.enabled })}
                className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-ink">
                {p.enabled ? "Deaktivieren" : "Aktivieren"}</button>
              <button onClick={() => del.mutate(p.id)}
                className="ml-auto rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-red-400">
                {tr("common.loeschen")}</button>
            </div>
          </div>
        ))}
      </div>

      <NewPolicy onSave={(p) => save.mutate(p)} />
      </div>
    </div>
  );
}

interface Perm { id: number; tool: string; resource: string; action: string; }

function ToolPermissions() {
  const qc = useQueryClient();
  const [tool, setTool] = useState("");
  const [action, setAction] = useState("allow");
  const { data = [] } = useQuery({ queryKey: ["tool-perms"], queryFn: () => api.get<Perm[]>("/assistant/tool-permissions") });
  const inv = () => qc.invalidateQueries({ queryKey: ["tool-perms"] });
  const save = useMutation({ mutationFn: (p: { tool: string; resource?: string; action: string }) => api.post("/assistant/tool-permissions", p), onSuccess: inv });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/assistant/tool-permissions/${id}`), onSuccess: inv });
  const A: Record<string, string> = { allow: "bg-green-600/15 text-green-400", deny: "bg-red-500/15 text-red-400", ask: "bg-surface text-muted" };

  return (
    <div className="space-y-2">
      <div className="text-sm font-medium text-ink">🔐 Tool-Freigaben</div>
      <p className="text-sm text-muted">
        {tr("assistant_policies.rechte_hinweis")}
      </p>
      <div className="space-y-1">
        {data.map((p) => (
          <div key={p.id} className="flex items-center gap-2 rounded border border-line bg-card p-2 text-sm">
            <code className="text-ink">{p.tool}</code>
            {p.resource !== "*" && <code className="text-xs text-muted">{p.resource}</code>}
            <span className={`rounded px-1.5 text-xs ${A[p.action] || A.ask}`}>{p.action}</span>
            <div className="flex-1" />
            {p.action !== "allow" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "allow" })} className="text-xs text-muted hover:text-green-400">→ allow</button>}
            {p.action !== "deny" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "deny" })} className="text-xs text-muted hover:text-red-400">→ deny</button>}
            <button onClick={() => del.mutate(p.id)} className="text-xs text-muted hover:text-red-400">{tr("common.loeschen_klein")}</button>
          </div>
        ))}
        {data.length === 0 && <div className="text-xs text-muted">{tr("assistant_policies.keine_der_assistent_fragt_bei_jeder_heik")}</div>}
      </div>
      <div className="flex gap-2">
        <input value={tool} onChange={(e) => setTool(e.target.value)} placeholder={tr("assistant_policies.tool_glob_z_b_obsidian")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-ink outline-none" />
        <select value={action} onChange={(e) => setAction(e.target.value)} className="rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
        </select>
        <button onClick={() => { if (tool.trim()) { save.mutate({ tool: tool.trim(), action }); setTool(""); } }}
          className="rounded bg-brand px-3 py-1 text-sm text-white">+ Regel</button>
      </div>
    </div>
  );
}

function NewPolicy({ onSave }: { onSave: (p: Partial<Policy>) => void }) {
  const [kind, setKind] = useState("sender");
  const [value, setValue] = useState("");
  const [redaction, setRedaction] = useState("redacted");
  const [hint, setHint] = useState("");
  return (
    <div className="space-y-2 rounded-lg border border-line bg-card p-3">
      <div className="text-xs uppercase text-muted">{tr("assistant_policies.regel_manuell_anlegen")}</div>
      <div className="flex flex-wrap gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="sender">{tr("assistant_policies.absender")}</option>
          <option value="domain">{tr("assistant_policies.domain")}</option>
          <option value="category">{tr("assistant_policies.kategorie")}</option>
        </select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={tr("assistant_policies.wert_z_b_news_darc_de")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-ink outline-none" />
        <select value={redaction} onChange={(e) => setRedaction(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="redacted">{tr("assistant.geschwaerzt")}</option>
          <option value="unredacted">{tr("assistant.ungeschwaerzt")}</option>
        </select>
      </div>
      <input value={hint} onChange={(e) => setHint(e.target.value)} placeholder={tr("assistant_policies.gelernte_aktion_optional")}
        className="w-full rounded border border-line bg-surface px-2 py-1.5 text-ink outline-none" />
      <button
        onClick={() => { if (value.trim()) { onSave({ match_kind: kind, match_value: value.trim(), redaction, action_hint: hint, auto_approve: true, enabled: true }); setValue(""); setHint(""); } }}
        className="rounded bg-brand px-3 py-1 text-sm text-white">{tr("assistant_policies.regel_anlegen")}</button>
    </div>
  );
}
