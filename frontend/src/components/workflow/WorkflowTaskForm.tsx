import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi, type NodeConfig } from "../../api";
import type { MemberLite } from "../../api";
import { DynamicForm, defaultValues, missingRequired } from "./formFields";
import { tr } from "../../i18n";
import { BUTTON } from "../ui";

/** Editing form for an open step (human_task or approval). */
export default function WorkflowTaskForm({
  iid,
  sid,
  nodeType,
  config,
  members = [],
  onDone,
}: {
  iid: number;
  sid: number;
  nodeType: "human_task" | "approval";
  config: NodeConfig;
  members?: MemberLite[];
  onDone?: () => void;
}) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, any>>(() => defaultValues(config.form));
  const [handover, setHandover] = useState("");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["workflow-instance", iid] });
    qc.invalidateQueries({ queryKey: ["workflow-tasks"] });
    qc.invalidateQueries({ queryKey: ["my-dashboard"] });
    onDone?.();
  };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const complete = useMutation({
    mutationFn: () =>
      workflowApi.completeStep(iid, sid, {
        form_data: values,
        next_assignee: handover ? Number(handover) : undefined,
      }),
    onSuccess: inv,
    onError: fail,
  });
  const approve = useMutation({
    mutationFn: () => workflowApi.approveStep(iid, sid, { reason: reason || undefined }),
    onSuccess: inv,
    onError: fail,
  });
  const reject = useMutation({
    mutationFn: () => workflowApi.rejectStep(iid, sid, { reason }),
    onSuccess: inv,
    onError: fail,
  });

  const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  if (nodeType === "approval") {
    const needReason = !!config.reason_required_on_reject;
    return (
      <div className="space-y-2">
        {config.instructions && <div className="text-xs text-muted">{config.instructions}</div>}
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={tr(needReason ? "task_form.reason_required_rejection" : "task_form.reason_optional")}
          className={`w-full ${inp}`}
        />
        <div className="flex gap-2">
          <button
            disabled={approve.isPending}
            onClick={() => approve.mutate()}
            className={BUTTON.confirm}
          >
            ✅ Genehmigen
          </button>
          <button
            disabled={reject.isPending || (needReason && !reason.trim())}
            onClick={() => reject.mutate()}
            className="rounded border border-red-400/50 px-3 py-1 text-sm text-red-400 hover:bg-red-400/10 disabled:opacity-50"
          >
            ✕ Ablehnen
          </button>
        </div>
        {err && <div className="text-xs text-red-400">{err}</div>}
      </div>
    );
  }

  // human_task
  const missing = missingRequired(config.form || [], values);
  return (
    <div className="space-y-2">
      {config.instructions && <div className="text-xs text-muted">{config.instructions}</div>}
      {(config.form?.length ?? 0) > 0 && (
        <DynamicForm fields={config.form!} values={values} onChange={setValues} />
      )}
      {config.handover && (
        <label className="block text-xs text-muted">
          {tr("task_form.hand")}
          <select value={handover} onChange={(e) => setHandover(e.target.value)} className={`mt-1 w-full ${inp}`}>
            <option value="">{tr("task_form.do_not_hand")}</option>
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.display_name || m.username}
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        disabled={complete.isPending || missing.length > 0}
        onClick={() => complete.mutate()}
        title={missing.length ? `Pflichtfelder: ${missing.join(", ")}` : undefined}
        className={BUTTON.primary}
      >
        ✓ Erledigt
      </button>
      {missing.length > 0 && (
        <span className="ml-2 text-xs text-yellow-400">Pflichtfelder offen: {missing.join(", ")}</span>
      )}
      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}
