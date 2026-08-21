import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListenLine, DeleteDialog, BUTTON, BUTTON_TEXT} from "./ui";

type Variable = { key: string; label: string; secret: boolean; required: boolean };
const EMPTY = { id: 0, name: "", display_name: "", transport: "http", url: "", variables: [] as Variable[], enabled: true };

export default function McpPanel() {
  const qc = useQueryClient();
  const { data: servers } = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<any[]>("/mcp-servers") });
  const { data: myMcp } = useQuery({ queryKey: ["my-mcp"], queryFn: () => api.get<any>("/me/mcp") });
  const [dialog, setDialog] = useState<typeof EMPTY | null>(null);
  const [deleteServer, setDeleteServer] = useState<any | null>(null);
  const [err, setErr] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["mcp"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const importMcp = useMutation({
    mutationFn: () => api.post("/me/mcp/import"),
    onSuccess: () => { setErr(""); inv(); qc.invalidateQueries({ queryKey: ["my-mcp"] }); },
    onError: fail,
  });
  const save = useMutation({
    mutationFn: (f: typeof EMPTY) => {
      const body = { name: f.name, display_name: f.display_name, transport: f.transport,
                     url: f.url, variables: f.variables, enabled: f.enabled };
      return f.id ? api.put(`/mcp-servers/${f.id}`, body) : api.post("/mcp-servers", body);
    },
    onSuccess: () => { setDialog(null); setErr(""); inv(); },
    onError: fail,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/mcp-servers/${id}`),
    onSuccess: () => { setDeleteServer(null); inv(); }, onError: fail });

  return (
    <Area>
      {/* Verfügbare MCP-Server aus MCPJungle als echte Registry-Einträge übernehmen */}
      {(myMcp?.available?.length ?? 0) > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface p-3">
          <div className="text-sm">
            <span className="font-medium text-ink">🔌 {tr("mcp_panel.verfuegbar_anzahl", { count: myMcp.available.length })}</span>
            {myMcp?.provisioned && <span className="ml-2 rounded bg-yellow-500/15 px-1.5 text-xs text-yellow-400">{tr("mcp_panel.gateway_gruppe_aktiv")}</span>}
            <p className="text-xs text-muted">{tr("mcp_panel.als_echte_editierbare_server_eintraege_u")}</p>
          </div>
          <div className="flex-1" />
          <button onClick={() => importMcp.mutate()} disabled={importMcp.isPending}
            className={BUTTON.primary}>
            {tr(importMcp.isPending ? "mcp_panel.uebernehme" : "mcp_panel.server_uebernehmen")}</button>
        </div>
      )}

      <p className="mb-3 text-sm text-muted">{tr("mcp_panel.einleitung")}</p>
      <p className="mb-3 text-xs text-yellow-400">{tr("mcp_panel.nur_http_sse")}</p>
      <Errorrow text={err} />

      <Listing className="mb-4">
        {servers?.map((m) => (
          <ListenLine key={m.id}>
            <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
              <span className="font-mono">{m.name}</span><span className="text-muted">{m.transport}</span>
              {(m.variables?.length ?? 0) > 0 && (
                <span className="rounded bg-surface px-1 text-xs">{tr("mcp_panel.variablen_anzahl", { count: m.variables.length })}</span>
              )}
              {m.enabled && <span className="text-xs text-green-400">{tr("mcp_panel.aktiv")}</span>}
            </div>
            <Actions>
              <IconButton icon={ICON.edit} title={tr("common.bearbeiten")}
                onClick={() => { setErr(""); setDialog({
                  id: m.id, name: m.name, display_name: m.display_name || "", transport: m.transport,
                  url: m.url || "", variables: m.variables || [], enabled: m.enabled }); }} />
              <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger onClick={() => setDeleteServer(m)} />
            </Actions>
            </div>
          </ListenLine>
        ))}
        {servers?.length === 0 && <ListingEmpty>{tr("mcp_panel.keine_mcp_server")}</ListingEmpty>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog({ ...EMPTY, variables: [] }); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("mcp_panel.server_anlegen")}
      </button>

      {dialog && (
        <ServerDialog start={dialog} error={err} runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(f) => save.mutate(f)} />
      )}
      {deleteServer && (
        <DeleteDialog was={deleteServer.name} runs={del.isPending}
          onClose={() => setDeleteServer(null)} onDelete={() => del.mutate(deleteServer.id)} />
      )}
    </Area>
  );
}

function ServerDialog({ start, error: error, runs: running, onClose, onSave }: {
  start: typeof EMPTY; error: string; runs: boolean;
  onClose: () => void; onSave: (f: typeof EMPTY) => void;
}) {
  const [form, setForm] = useState(start);
  const setVariable = (i: number, patch: Partial<Variable>) =>
    setForm({ ...form, variables: form.variables.map((v, j) => j === i ? { ...v, ...patch } : v) });

  return (
    <Dialog wide title={form.id ? tr("mcp_panel.server_bearbeiten") : tr("mcp_panel.server_anlegen")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!form.name.trim()} runs={running}
        onSave={() => onSave(form)}
        saveText={form.id ? undefined : tr("common.anlegen")} />}>
      <Errorrow text={error} />
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label={tr("mcp_panel.name_z_b_banking")}>
            <input value={form.name} autoFocus onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={`${INPUT_VALUE} font-mono`} />
          </Field>
          <Field label={tr("mcp_panel.transport")}>
            <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}
              className={INPUT_VALUE}>
              <option value="http">http</option><option value="sse">sse</option>
            </select>
          </Field>
          <div className="sm:col-span-2">
            <Field label="URL">
              <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })}
                placeholder="https://…/mcp" className={INPUT_VALUE} />
            </Field>
          </div>
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-muted">{tr("mcp_panel.variablen_werden_pro_instanz_ausgefuellt")}</div>
          {form.variables.map((v, i) => (
            <div key={i} className="mb-1 flex flex-wrap items-center gap-2">
              <input value={v.key} onChange={(e) => setVariable(i, { key: e.target.value })}
                placeholder={tr("mcp_panel.key_z_b_authorization")} className={`w-44 ${INPUT_VALUE}`} />
              <input value={v.label} onChange={(e) => setVariable(i, { label: e.target.value })}
                placeholder={tr("mcp_panel.label")} className={`min-w-[8rem] flex-1 ${INPUT_VALUE}`} />
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={v.secret} onChange={(e) => setVariable(i, { secret: e.target.checked })} />
                {tr("mcp_panel.geheim")}
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={v.required} onChange={(e) => setVariable(i, { required: e.target.checked })} />
                {tr("mcp_panel.pflicht")}
              </label>
              <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger
                onClick={() => setForm({ ...form, variables: form.variables.filter((_, j) => j !== i) })} />
            </div>
          ))}
          <button type="button"
            onClick={() => setForm({ ...form, variables: [...form.variables, { key: "", label: "", secret: true, required: false }] })}
            className={BUTTON_TEXT.secondary}>+ {tr("mcp_panel.variable")}</button>
        </div>
      </div>
    </Dialog>
  );
}
