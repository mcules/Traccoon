import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatDate } from "../lib/formatTime";
import {
  Actions, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, DeleteDialog, Area, Tag, Listing, ListingEmpty, ListRow, BUTTON, BUTTON_TEXT} from "./ui";

interface Policy {
  id: number; match_kind: string; match_value: string;
  auto_approve: boolean; blocked: boolean; redaction: string; action_hint: string;
  enabled: boolean; origin: string; origin_task_id: number | null;
  hit_count: number; last_used_at: string | null; created_at: string;
}

const KIND_KEY: Record<string, string> = { sender: "assistant_policies.sender", domain: "assistant_policies.domain", category: "assistant_policies.category" };

export default function AssistantPolicies() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState<Policy | {} | null>(null);   // {} = neue Regel
  const [deleteRule, setDeleteRule] = useState<Policy | null>(null);
  const { data = [], isLoading } = useQuery({
    queryKey: ["policies"], queryFn: () => api.get<Policy[]>("/assistant/policies"),
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["policies"] });
  const guard = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const save = useMutation({
    mutationFn: (p: Partial<Policy> & { id?: number }) =>
      p.id ? api.put(`/assistant/policies/${p.id}`, p) : api.post("/assistant/policies", p),
    onSuccess: () => { setDialog(null); inv(); }, onError: guard,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.del(`/assistant/policies/${id}`),
    onSuccess: () => { setDeleteRule(null); inv(); }, onError: guard,
  });

  return (
    <div className="space-y-4">
      <ToolPermissions />
      <Area title={tr("assistant_policies.intake_rules")} hint={tr("assistant_policies.rules_assistant_learned_when")}>
      <Errorrow text={err} />

      {isLoading && <div className="text-sm text-muted">{tr("assistant_policies.loading")}</div>}

      <Listing>
        {data.map((p) => (
          <ListRow key={p.id} dimmed={!p.enabled}>
            <div className="flex flex-wrap items-center gap-1.5">
              <Tag>{KIND_KEY[p.match_kind] ? tr(KIND_KEY[p.match_kind]) : p.match_kind}</Tag>
              <span className="font-medium text-ink">{p.match_value}</span>
              <span className={`rounded px-1.5 text-xs ${p.blocked ? "bg-red-600/15 text-red-400"
                : p.auto_approve ? "bg-green-600/15 text-green-400" : "bg-surface text-muted"}`}>
                {tr(p.blocked ? "assistant_policies.blocked"
                  : p.auto_approve ? "assistant_policies.auto_approve" : "assistant_policies.hint_only")}</span>
              <span className={`rounded px-1.5 text-xs ${p.redaction === "unredacted" ? "bg-amber-500/15 text-amber-400" : "bg-surface text-muted"}`}>
                {tr(p.redaction === "unredacted" ? "assistant.unredacted" : "assistant.redacted")}</span>
              <span className="ml-auto text-xs text-muted">{p.hit_count}×</span>
            </div>
            {p.action_hint && <p className="mt-1 text-xs text-muted">↳ {p.action_hint}</p>}
            {/* Where a rule comes from and since when. A list of bare addresses cannot be
                judged months later, and taking one back is exactly the moment one asks. */}
            <p className="mt-1 text-xs text-muted">
              {tr("assistant_policies.since", { date: formatDate(p.created_at) })}
              {p.origin && ` · ${tr("assistant_policies.granted_at", { what: p.origin })}`}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1" />
              <Actions>
                <IconButton icon={p.blocked ? "✅" : "🚫"}
                  onClick={() => save.mutate({ ...p, blocked: !p.blocked, auto_approve: false })}
                  title={tr(p.blocked ? "assistant_policies.unblock" : "assistant_policies.block")} />
                <IconButton icon={p.enabled ? "⏸" : "⏵"} onClick={() => save.mutate({ ...p, enabled: !p.enabled })}
                  title={tr(p.enabled ? "jobs_panel.switch_off" : "jobs_panel.switch")} />
                <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => setDialog(p)} />
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => setDeleteRule(p)} />
              </Actions>
            </div>
          </ListRow>
        ))}
        {!isLoading && data.length === 0 && (
          <ListingEmpty>{tr("assistant_policies.no_rules_yet_choose")}</ListingEmpty>
        )}
      </Listing>

      <button onClick={() => setDialog({})} className={BUTTON.primary}>
        {ICON.fresh} {tr("assistant_policies.create_rule")}
      </button>
      </Area>

      {dialog && (
        <RuleDialog rule={"id" in dialog ? (dialog as Policy) : null} runs={save.isPending}
          onClose={() => setDialog(null)} onSave={(values) => save.mutate(values)} />
      )}
      {deleteRule && (
        <DeleteDialog was={deleteRule.match_value} runs={del.isPending}
          onClose={() => setDeleteRule(null)} onDelete={() => del.mutate(deleteRule.id)} />
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
  const A: Record<string, "green" | "red" | "neutral"> = { allow: "green", deny: "red", ask: "neutral" };

  return (
    <Area title="🔐 Tool-Freigaben" hint={tr("assistant_policies.what_assistant_may_do")}>
      <Listing>
        {data.map((p) => (
          <ListRow key={p.id}>
            <div className="flex items-center gap-2">
            <code className="text-ink">{p.tool}</code>
            {p.resource !== "*" && <code className="text-xs text-muted">{p.resource}</code>}
            <Tag color={A[p.action] || A.ask}>{p.action}</Tag>
            <div className="flex-1" />
            {p.action !== "allow" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "allow" })} className={BUTTON_TEXT.secondary}>→ allow</button>}
            {p.action !== "deny" && <button onClick={() => save.mutate({ tool: p.tool, resource: p.resource, action: "deny" })} className={BUTTON_TEXT.danger}>→ deny</button>}
            <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => del.mutate(p.id)} />
            </div>
          </ListRow>
        ))}
        {data.length === 0 && <ListingEmpty>{tr("assistant_policies.none_the_assistant_asks_before_every_sensitiv")}</ListingEmpty>}
      </Listing>
      <div className="flex gap-2">
        <input value={tool} onChange={(e) => setTool(e.target.value)} placeholder={tr("assistant_policies.tool_glob_z_b_obsidian")}
          className="flex-1 rounded border border-line bg-surface px-2 py-1.5 text-ink outline-none" />
        <select value={action} onChange={(e) => setAction(e.target.value)} className="rounded border border-line bg-surface px-2 py-1.5 text-ink">
          <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
        </select>
        <button onClick={() => { if (tool.trim()) { save.mutate({ tool: tool.trim(), action }); setTool(""); } }}
          className={BUTTON.primary}>+ Regel</button>
      </div>
    </Area>
  );
}

/**
 * A rule for incoming mail: what it matches, how it is processed, what it learned.
 *
 * Editing used to be a row of toggle buttons ("auto off", "→ redacted", "deactivate") that
 * each wrote one field on click. Which of them belonged together only became clear by
 * trying, and undoing meant clicking back through them.
 */
function RuleDialog({ rule: rule, runs: running, onClose, onSave }: {
  rule: Policy | null; runs: boolean;
  onClose: () => void; onSave: (p: Partial<Policy> & { id?: number }) => void;
}) {
  const [kind, setKind] = useState(rule?.match_kind || "sender");
  const [value, setValue] = useState(rule?.match_value || "");
  const [redaction, setRedaction] = useState(rule?.redaction || "redacted");
  const [hint, setHint] = useState(rule?.action_hint || "");
  const [autoApprove, setAutoApprove] = useState(rule ? rule.auto_approve : true);
  const [blocked, setBlocked] = useState(rule ? rule.blocked : false);
  const [enabled, setEnabled] = useState(rule ? rule.enabled : true);

  return (
    <Dialog title={tr(rule ? "assistant_policies.edit_rule" : "assistant_policies.create_rule")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!value.trim()} runs={running}
        saveText={rule ? undefined : tr("common.create")}
        onSave={() => onSave({
          ...(rule ? { id: rule.id } : {}),
          match_kind: kind, match_value: value.trim(), redaction, action_hint: hint,
          auto_approve: autoApprove && !blocked, blocked, enabled,
        })} />}>
      <div className="space-y-3">
        <Field label={tr("assistant_policies.matches")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={INPUT_VALUE}>
            <option value="sender">{tr("assistant_policies.sender")}</option>
            <option value="domain">{tr("assistant_policies.domain")}</option>
            <option value="category">{tr("assistant_policies.category")}</option>
          </select>
        </Field>
        <Field label={tr("assistant_policies.value_e_g_news_verband_de")}>
          <input value={value} autoFocus onChange={(e) => setValue(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("assistant_policies.processing")}>
          <select value={redaction} onChange={(e) => setRedaction(e.target.value)} className={INPUT_VALUE}>
            <option value="redacted">{tr("assistant.redacted")}</option>
            <option value="unredacted">{tr("assistant.unredacted")}</option>
          </select>
        </Field>
        <Field label={tr("assistant_policies.learned_action_optional")}>
          <input value={hint} onChange={(e) => setHint(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={autoApprove} disabled={blocked}
            onChange={(e) => setAutoApprove(e.target.checked)} />
          {tr("assistant_policies.auto_approve")}
        </label>
        {/* A rule cannot approve and block at the same time, so one switch takes the other
            out of reach instead of letting the row say yes and no about the same sender. */}
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={blocked}
            onChange={(e) => { setBlocked(e.target.checked); if (e.target.checked) setAutoApprove(false); }} />
          {tr("assistant_policies.blocked_hint")}
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          {tr("artifact_types_panel.active")}
        </label>
      </div>
    </Dialog>
  );
}
