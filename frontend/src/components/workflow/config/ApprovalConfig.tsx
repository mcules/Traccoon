import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";
import type { MemberLite } from "../../../api";
import { AssigneeEditor } from "../assignee";

export default function ApprovalConfig({
  config,
  onChange,
  members,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  members: MemberLite[];
}) {
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-medium text-muted">{tr("approval_config.freigeber")}</div>
        <AssigneeEditor
          value={config.approvers}
          members={members}
          onChange={(a) => onChange({ ...config, approvers: a })}
        />
      </div>

      <label className="block text-xs font-medium text-muted">
        Gate
        <select
          value={config.gate || "none"}
          onChange={(e) => onChange({ ...config, gate: e.target.value as NodeConfig["gate"] })}
          className={`mt-1 ${inp}`}
        >
          <option value="ai_assign">KI-Recht (ai_assign)</option>
          <option value="role">{tr("approval_config.projektrolle")}</option>
          <option value="none">{tr("approval.ohne_gate")}</option>
        </select>
      </label>

      <label className="flex items-center gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={!!config.reason_required_on_reject}
          onChange={(e) => onChange({ ...config, reason_required_on_reject: e.target.checked })}
        />
        {tr("approval_config.begruendung_pflicht")}
      </label>
    </div>
  );
}
