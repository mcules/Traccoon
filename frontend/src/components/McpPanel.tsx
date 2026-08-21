import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Actions, Area, Dialog, DialogFuss, INPUT_VALUE, Field, Fehlerzeile, ICON, IconButton, Listing,
  ListingLeer, ListenLine, LoeschDialog, BUTTON, BUTTON_TEXT} from "./ui";

type Variable = { key: string; label: string; secret: boolean; required: boolean };
const EMPTY = { id: 0, name: "", display_name: "", transport: "http", url: "", variables: [] as Variable[], enabled: true };

export default function McpPanel() {
  const qc = useQueryClient();
  const { data: servers } = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<any[]>("/mcp-servers") });
  const { data: myMcp } = useQuery({ queryKey: ["my-mcp"], queryFn: () => api.get<any>("/me/mcp") });
  const [dialog, setDialog] = useState<typeof EMPTY | null>(null);
  const [loeschServer, setLoeschServer] = useState<any | null>(null);
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
    onSuccess: () => { setLoeschServer(null); inv(); }, onError: fail });

  return (
    <Area>
      {/* Verfügbare MCP-Server aus MCPJungle als echte Registry-Einträge übernehmen */}
      {(myMcp?.available?.length ?? 0) > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface p-3">
          <div className="text-sm">
            <span className="font-medium text-ink">🔌 {tr("mcp_panel.verfuegbar_anzahl", { anzahl: myMcp.available.length })}</span>
            {myMcp?.provisioned && <span className="ml-2 rounded bg-yellow-500/15 px-1.5 text-xs text-yellow-400">{tr("mcp_panel.gateway_gruppe_aktiv")}</span>}
            <p className="text-xs text-muted">{tr("mcp_panel.als_echte_editierbare_server_eintraege_u")}</p>
          </div>
          <div className="flex-1" />
          <button onClick={() => importMcp.mutate()} disabled={importMcp.isPending}
            className={BUTTON.haupt}>
            {tr(importMcp.isPending ? "mcp_panel.uebernehme" : "mcp_panel.server_uebernehmen")}</button>
        </div>
      )}

      <p className="mb-3 text-sm text-muted">{tr("mcp_panel.einleitung")}</p>
      <p className="mb-3 text-xs text-yellow-400">{tr("mcp_panel.nur_http_sse")}</p>
      <Fehlerzeile text={err} />

      <Listing className="mb-4">
        {servers?.map((m) => (
          <ListenLine key={m.id}>
            <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2">
              <span className="font-mono">{m.name}</span><span className="text-muted">{m.transport}</span>
              {(m.variables?.length ?? 0) > 0 && (
                <span className="rounded bg-surface px-1 text-xs">{tr("mcp_panel.variablen_anzahl", { anzahl: m.variables.length })}</span>
              )}
              {m.enabled && <span className="text-xs text-green-400">{tr("mcp_panel.aktiv")}</span>}
            </div>
            <Actions>
              <IconButton icon={ICON.bearbeiten} titel={tr("common.bearbeiten")}
                onClick={() => { setErr(""); setDialog({
                  id: m.id, name: m.name, display_name: m.display_name || "", transport: m.transport,
                  url: m.url || "", variables: m.variables || [], enabled: m.enabled }); }} />
              <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschServer(m)} />
            </Actions>
            </div>
          </ListenLine>
        ))}
        {servers?.length === 0 && <ListingLeer>{tr("mcp_panel.keine_mcp_server")}</ListingLeer>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog({ ...EMPTY, variables: [] }); }}
        className={BUTTON.haupt}>
        {ICON.neu} {tr("mcp_panel.server_anlegen")}
      </button>

      {dialog && (
        <ServerDialog start={dialog} fehler={err} laeuft={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(f) => save.mutate(f)} />
      )}
      {loeschServer && (
        <LoeschDialog was={loeschServer.name} laeuft={del.isPending}
          onClose={() => setLoeschServer(null)} onLoeschen={() => del.mutate(loeschServer.id)} />
      )}
    </Area>
  );
}

function ServerDialog({ start, fehler: error, laeuft: running, onClose, onSpeichern }: {
  start: typeof EMPTY; fehler: string; laeuft: boolean;
  onClose: () => void; onSpeichern: (f: typeof EMPTY) => void;
}) {
  const [form, setForm] = useState(start);
  const setzeVariable = (i: number, patch: Partial<Variable>) =>
    setForm({ ...form, variables: form.variables.map((v, j) => j === i ? { ...v, ...patch } : v) });

  return (
    <Dialog breit titel={form.id ? tr("mcp_panel.server_bearbeiten") : tr("mcp_panel.server_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!form.name.trim()} laeuft={running}
        onSpeichern={() => onSpeichern(form)}
        speichernText={form.id ? undefined : tr("common.anlegen")} />}>
      <Fehlerzeile text={error} />
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
              <input value={v.key} onChange={(e) => setzeVariable(i, { key: e.target.value })}
                placeholder={tr("mcp_panel.key_z_b_authorization")} className={`w-44 ${INPUT_VALUE}`} />
              <input value={v.label} onChange={(e) => setzeVariable(i, { label: e.target.value })}
                placeholder={tr("mcp_panel.label")} className={`min-w-[8rem] flex-1 ${INPUT_VALUE}`} />
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={v.secret} onChange={(e) => setzeVariable(i, { secret: e.target.checked })} />
                {tr("mcp_panel.geheim")}
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                <input type="checkbox" checked={v.required} onChange={(e) => setzeVariable(i, { required: e.target.checked })} />
                {tr("mcp_panel.pflicht")}
              </label>
              <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                onClick={() => setForm({ ...form, variables: form.variables.filter((_, j) => j !== i) })} />
            </div>
          ))}
          <button type="button"
            onClick={() => setForm({ ...form, variables: [...form.variables, { key: "", label: "", secret: true, required: false }] })}
            className={BUTTON_TEXT.neben}>+ {tr("mcp_panel.variable")}</button>
        </div>
      </div>
    </Dialog>
  );
}
