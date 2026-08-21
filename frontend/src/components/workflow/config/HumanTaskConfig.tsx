import type { NodeConfig } from "../types";
import { tr } from "../../../i18n";
import type { MemberLite } from "../../../api";
import { AssigneeEditor } from "../assignee";
import { FormFieldsEditor } from "../formFields";

export default function HumanTaskConfig({
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
        <div className="mb-1 text-xs font-medium text-muted">{tr("human_task_config.responsible")}</div>
        <AssigneeEditor
          value={config.assignee}
          members={members}
          onChange={(a) => onChange({ ...config, assignee: a })}
        />
      </div>

      <div>
        <div className="mb-1 text-xs font-medium text-muted">{tr("human_task_config.form_fields")}</div>
        <FormFieldsEditor
          fields={config.form || []}
          onChange={(f) => onChange({ ...config, form: f })}
        />
      </div>

      <label className="flex items-center gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={!!config.handover}
          onChange={(e) => onChange({ ...config, handover: e.target.checked })}
        />
        {tr("human_task_config.allow_handover_another_person")}
      </label>

      <label className="block text-xs font-medium text-muted">
        {tr("human_task_config.due_hours")}
        <input
          type="number"
          value={config.due_in_hours ?? ""}
          onChange={(e) =>
            onChange({ ...config, due_in_hours: e.target.value === "" ? undefined : Number(e.target.value) })
          }
          className={`mt-1 ${inp}`}
        />
      </label>

      <label className="block text-xs font-medium text-muted">
        Anweisungen
        <textarea
          value={config.instructions || ""}
          onChange={(e) => onChange({ ...config, instructions: e.target.value })}
          rows={3}
          className={`mt-1 ${inp}`}
        />
      </label>
    </div>
  );
}
