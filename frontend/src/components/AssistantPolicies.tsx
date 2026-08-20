import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import {
  Aktionen, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, LoeschDialog, Bereich, Etikett, Liste, ListeLeer, ListenZeile, KNOPF, KNOPF_TEXT} from "./ui";

interface Policy {
  id: number; match_kind: string; match_value: string;
  auto_approve: boolean; redaction: string; action_hint: string;
  enabled: boolean; hit_count: number; last_used_at: string | null; created_at: string;
}

const KIND_LABEL: Record<string, string> = { sender: "Absender", domain: "Domain", category: "Kategorie" };

export default function AssistantPolicies() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState<Policy | {} | null>(null);   // {} = neue Regel
  const [loeschRegel, setLoeschRegel] = useState<Policy | null>(null);
  const { data = [], isLoading } = useQuery({
    queryKey: ["policies"], queryFn: () => api.get<Policy[]>("/assistant/policies"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["policies"] });
  const guard = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const save = useMutation({
    mutationFn: (p: Partial<Policy> & { id?: number }) =>
      p.id ? api.put(`/assistant/policies/${p.id}`, p) : api.post("/assistant/policies", p),
    onSuccess: () => { setDialog(null); inv(); }, onError: guard,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/assistant/policies/${id}`),
    onSuccess: () => { setLoeschRegel(null); inv(); }, onError: guard,
  });

  return (
    <div className="space-y-4">
      <ToolPermissions />
      <Bereich titel="📥 Eingangs-Regeln (Mail)" hinweis={tr("assistant_policies.einleitung")}>
      <Fehlerzeile text={err} />

      {isLoading && <div className="text-sm text-muted">{tr("assistant_policies.laedt")}</div>}

      <Liste>
        {data.map((p) => (
          <ListenZeile key={p.id} gedimmt={!p.enabled}>
            <div className="flex flex-wrap items-center gap-1.5">
              <Etikett>{KIND_LABEL[p.match_kind] || p.match_kind}</Etikett>
              <span className="font-medium text-ink">{p.match_value}</span>
              <span className={`rounded px-1.5 text-xs ${p.auto_approve ? "bg-green-600/15 text-green-400" : "bg-surface text-muted"}`}>
                {tr(p.auto_approve ? "assistant_policies.auto_freigabe" : "assistant_policies.nur_vorgabe")}</span>
              <span className={`rounded px-1.5 text-xs ${p.redaction === "unredacted" ? "bg-amber-500/15 text-amber-400" : "bg-surface text-muted"}`}>
                {tr(p.redaction === "unredacted" ? "assistant.ungeschwaerzt" : "assistant.geschwaerzt")}</span>
              <span className="ml-auto text-xs text-muted">{p.hit_count}×</span>
            </div>
            {p.action_hint && <p className="mt-1 text-xs text-muted">↳ {p.action_hint}</p>}
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1" />
              <Aktionen>
                <IconKnopf icon={p.enabled ? "⏸" : "⏵"} onClick={() => save.mutate({ ...p, enabled: !p.enabled })}
                  titel={tr(p.enabled ? "jobs_panel.deaktivieren" : "jobs_panel.aktivieren")} />
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(p)} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschRegel(p)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {!isLoading && data.length === 0 && (
          <ListeLeer>{tr("assistant_policies.keine_regeln")}</ListeLeer>
        )}
      </Liste>

      <button onClick={() => setDialog({})} className={KNOPF.haupt}>
        {ICON.neu} {tr("assistant_policies.regel_anlegen")}
      </button>
      </Bereich>

      {dialog && (
        <RegelDialog regel={"id" in dialog ? (dialog as Policy) : null} laeuft={save.isPending}
          onClose={() => setDialog(null)} onSpeichern={(werte) => save.mutate(werte)} />
      )}
      {loeschRegel && (
        <LoeschDialog was={loeschRegel.match_value} laeuft={del.isPending}
          onClose={() => setLoeschRegel(null)} onLoeschen={() => del.mutate(loeschRegel.id)} />
      )}
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
  const A: Record<string, "gruen" | "rot" | "neutral"> = { allow: "gruen", deny: "rot", ask: "neutral" };

  return (
    <Bereich titel="🔐 Tool-Freigaben" hinweis={tr("assistant_policies.rechte_hinweis")}>
      <Liste>
        {data.map((p) => (
          <ListenZeile key={p.id}>
            <div className="flex items-center gap-2">
            <code className="text-ink">{p.tool}</code>
            {p.resource !== "*" && <code className="text-xs text-muted">{p.resource}</code>}
            <Etikett farbe={A[p.action] || A.ask}>{p.action}</Etikett>
            <div className="flex-1" />
            {p.action !== "allow" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "allow" })} className={KNOPF_TEXT.neben}>→ allow</button>}
            {p.action !== "deny" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "deny" })} className={KNOPF_TEXT.gefahr}>→ deny</button>}
            <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => del.mutate(p.id)} />
            </div>
          </ListenZeile>
        ))}
        {data.length === 0 && <ListeLeer>{tr("assistant_policies.keine_der_assistent_fragt_bei_jeder_heik")}</ListeLeer>}
      </Liste>
      <div className="flex gap-2">
        <input value={tool} onChange={(e) => setTool(e.target.value)} placeholder={tr("assistant_policies.tool_glob_z_b_obsidian")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-ink outline-none" />
        <select value={action} onChange={(e) => setAction(e.target.value)} className="rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
        </select>
        <button onClick={() => { if (tool.trim()) { save.mutate({ tool: tool.trim(), action }); setTool(""); } }}
          className={KNOPF.haupt}>+ Regel</button>
      </div>
    </Bereich>
  );
}

/**
 * A rule for incoming mail: what it matches, how it is processed, what it learned.
 *
 * Editing used to be a row of toggle buttons ("auto off", "→ redacted", "deactivate") that
 * each wrote one field on click. Which of them belonged together only became clear by
 * trying, and undoing meant clicking back through them.
 */
function RegelDialog({ regel, laeuft, onClose, onSpeichern }: {
  regel: Policy | null; laeuft: boolean;
  onClose: () => void; onSpeichern: (p: Partial<Policy> & { id?: number }) => void;
}) {
  const [kind, setKind] = useState(regel?.match_kind || "sender");
  const [value, setValue] = useState(regel?.match_value || "");
  const [redaction, setRedaction] = useState(regel?.redaction || "redacted");
  const [hint, setHint] = useState(regel?.action_hint || "");
  const [autoApprove, setAutoApprove] = useState(regel ? regel.auto_approve : true);
  const [enabled, setEnabled] = useState(regel ? regel.enabled : true);

  return (
    <Dialog titel={tr(regel ? "assistant_policies.regel_bearbeiten" : "assistant_policies.regel_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!value.trim()} laeuft={laeuft}
        speichernText={regel ? undefined : tr("common.anlegen")}
        onSpeichern={() => onSpeichern({
          ...(regel ? { id: regel.id } : {}),
          match_kind: kind, match_value: value.trim(), redaction, action_hint: hint,
          auto_approve: autoApprove, enabled,
        })} />}>
      <div className="space-y-3">
        <Feld label={tr("assistant_policies.trifft_auf")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={EINGABE}>
            <option value="sender">{tr("assistant_policies.absender")}</option>
            <option value="domain">{tr("assistant_policies.domain")}</option>
            <option value="category">{tr("assistant_policies.kategorie")}</option>
          </select>
        </Feld>
        <Feld label={tr("assistant_policies.wert_z_b_news_darc_de")}>
          <input value={value} autoFocus onChange={(e) => setValue(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("assistant_policies.verarbeitung")}>
          <select value={redaction} onChange={(e) => setRedaction(e.target.value)} className={EINGABE}>
            <option value="redacted">{tr("assistant.geschwaerzt")}</option>
            <option value="unredacted">{tr("assistant.ungeschwaerzt")}</option>
          </select>
        </Feld>
        <Feld label={tr("assistant_policies.gelernte_aktion_optional")}>
          <input value={hint} onChange={(e) => setHint(e.target.value)} className={EINGABE} />
        </Feld>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />
          {tr("assistant_policies.auto_freigabe")}
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          {tr("artifact_types_panel.aktiv")}
        </label>
      </div>
    </Dialog>
  );
}
