import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowDefinition, WorkflowSubjectKind } from "./types";
import {
  Actions, Area, Dialog, DialogFoot, Tag, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListRow, DeleteDialog, State, BUTTON } from "../ui";

const EMPTY = { key: "", name: "", subject_kind: "standalone" as WorkflowSubjectKind,
                description: "", template: "" };
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
/** Three columns: the flow itself, its state, the handgrips. */
const COLUMNS = "sm:grid-cols-[minmax(0,1fr)_9rem_auto]";

/**
 * Own, project-less processes: everything that belongs to no project and no slot.
 *
 * Meant for flows that have no ticket as their subject (subject `standalone`) and are set
 * off by a job, a webhook or an agent, for instance a nightly price comparison with an
 * approval step.
 *
 * What is created is either a skeleton (start plus end) or a **template**: a finished flow
 * to rebuild. The skeleton does not answer how one makes something that really runs out of
 * two nodes; the templates show the four patterns almost every own flow consists of.
 * Publishing happens in the editor in both cases.
 */
export default function OwnWorkflowsPanel() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState("");
  const [newDialog, setNewDialog] = useState(false);
  const [deleteFlow, setDeleteFlow] = useState<WorkflowDefinition | null>(null);
  // Renaming: name and key often come into being in passing (out of a route, out of a job
  // name) and then describe the trigger instead of the matter.
  const [rename, setRename] = useState<WorkflowDefinition | null>(null);
  const [nameNew, setNameNew] = useState("");
  const [keyNew, setKeyNew] = useState("");

  const { data: all } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  const { data: templates } = useQuery({
    queryKey: ["workflow-templates"], queryFn: workflowApi.templates, staleTime: 30 * 60_000 });
  const chosen = (templates || []).find((v) => v.key === f.template);
  // Slot flows stand at the top in the process set, project flows in the respective project.
  const own = (all || []).filter((d) => d.project_id === null && !d.slot && !d.archived_at);

  const open_it = (d: WorkflowDefinition) =>
    nav(`/workflows/${d.id}`, { state: { from: "/processes/own" } });
  const inv = () => qc.invalidateQueries({ queryKey: ["workflows-all"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const create = useMutation({
    mutationFn: () => workflowApi.create({
      project_id: null, key: f.key.trim(), name: f.name.trim(),
      subject_kind: f.subject_kind, description: f.description.trim() || undefined,
      template: f.template || undefined,
    }),
    onSuccess: (d) => { setF(EMPTY); setErr(""); setNewDialog(false); inv(); nav(`/workflows/${d.id}`, { state: { from: "/processes/own" } }); },
    onError: fail,
  });
  const toggle = useMutation({
    mutationFn: (d: WorkflowDefinition) => workflowApi.update(d.id, { enabled: !d.enabled }),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const save = useMutation({
    mutationFn: () => workflowApi.update(rename!.id,
      { name: nameNew.trim(), key: keyNew.trim() }),
    onSuccess: () => { setErr(""); setRename(null); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => workflowApi.del(id),
    onSuccess: () => { setErr(""); setDeleteFlow(null); inv(); }, onError: fail,
  });

  return (
    <Area hint={tr("own_workflows_panel.own_flows_no_project")}>
      <Errorrow text={err} />

      {own.length > 0 ? (
        /* Without column headers: with a handful of entries name, key and state explain
           themselves, and a heading row would be a row of noise above five rows of
           content. */
        <Listing>
          {own.map((d) => (
            <ListRow key={d.id} columns={COLUMNS} dimmed={!d.enabled}
              onClick={() => open_it(d)}>
              {/* Two rows instead of five columns: the name carries the entry, everything
                  technical stands one floor lower and quieter. That keeps the list
                  aligned when one name is long and the next one short. */}
              <div className="min-w-0 basis-full sm:basis-auto">
                <div className="truncate font-medium text-ink">{d.name}</div>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                  <span className="truncate font-mono">{d.key}</span>
                  <span className="text-line">·</span>
                  <Tag>{d.subject_kind}</Tag>
                </div>
              </div>
              {!d.enabled
                ? <State color="grey" text={tr("own_workflows_panel.off")} />
                : d.current_version_id
                  ? <State color="green" text={tr("proc.published")} />
                  : <State color="yellow" text={tr("own_workflows.draft_only")} />}
              {/* Clicks on the buttons belong to the buttons — otherwise the editor would
                  open behind the
                  delete dialog the editor as well. */}
              <div className="ml-auto shrink-0 sm:ml-0 sm:justify-self-end"
                onClick={(e) => e.stopPropagation()}>
                <Actions>
                  <IconButton icon={ICON.edit} title={tr("own_workflows_panel.editor")}
                    onClick={() => open_it(d)} />
                  <IconButton icon="🏷" title={tr("own_workflows_panel.rename")}
                    onClick={() => { setErr(""); setNameNew(d.name); setKeyNew(d.key); setRename(d); }} />
                  <IconButton icon={d.enabled ? "⏸" : "⏵"} onClick={() => toggle.mutate(d)}
                    title={tr(d.enabled ? "own_workflows_panel.off" : "own_workflows_panel.an")} />
                  <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                    onClick={() => setDeleteFlow(d)} />
                </Actions>
              </div>
            </ListRow>
          ))}
        </Listing>
      ) : (
        <Listing><ListingEmpty>{tr("own_workflows_panel.no_own_flows_yet")}</ListingEmpty></Listing>
      )}

      {rename && (
        <Dialog title={tr("own_workflows_panel.rename")} onClose={() => setRename(null)}
          foot={<DialogFoot onCancel={() => setRename(null)}
            disabled={!nameNew.trim() || !keyNew.trim()} runs={save.isPending}
            onSave={() => save.mutate()} />}>
          <div className="space-y-3">
            <label className="block text-xs font-medium text-muted">
              {tr("own_workflows_panel.name")}
              <input value={nameNew} autoFocus onChange={(e) => setNameNew(e.target.value)}
                className={`mt-1 w-full ${inp}`} />
            </label>
            <label className="block text-xs font-medium text-muted">
              {tr("own_workflows_panel.key")}
              <input value={keyNew} onChange={(e) => setKeyNew(e.target.value)}
                className={`mt-1 w-full font-mono ${inp}`} />
              <span className="mt-1 block text-[11px] text-muted">
                {tr("own_workflows_panel.lower_case_letters_digits")}
              </span>
            </label>
          </div>
        </Dialog>
      )}

      <button onClick={() => { setErr(""); setNewDialog(true); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("own_workflows_panel.new_flow")}
      </button>

      {newDialog && (
        <Dialog wide title={tr("own_workflows_panel.new_flow")} onClose={() => setNewDialog(false)}
          foot={<DialogFoot onCancel={() => setNewDialog(false)} runs={create.isPending}
            disabled={!f.key.trim() || !f.name.trim()} saveText={tr("common.create")}
            onSave={() => create.mutate()} />}>
          <div className="space-y-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })}
            placeholder={tr("own_workflows_panel.key_e_g_price")} className={inp} />
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder={tr("own_workflows_panel.name")} className={inp} />
          <select value={f.template} className={inp}
            onChange={(e) => setF({ ...f, template: e.target.value })}>
            <option value="">{tr("own_workflows.empty_skeleton_start_end")}</option>
            {(templates || []).map((v) => (
              <option key={v.key} value={v.key}>Vorlage: {v.name}</option>
            ))}
          </select>
          {!f.template && (
            <select value={f.subject_kind} className={inp}
              onChange={(e) => setF({ ...f, subject_kind: e.target.value as WorkflowSubjectKind })}>
              <option value="standalone">standalone (kein Gegenstand)</option>
              <option value="issue">issue (Ticket)</option>
              <option value="hardware_asset">hardware_asset</option>
            </select>
          )}
          <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })}
            placeholder={tr("own_workflows_panel.description_optional")} className={`${inp} min-w-48 flex-1`} />
        </div>
        {chosen && (
          <p className="text-xs text-muted">
            <b>{chosen.name}</b> — {chosen.description}{" "}
            <span className="text-brand">{chosen.hint}</span>
          </p>
        )}
          </div>
        </Dialog>
      )}
      {deleteFlow && (
        <DeleteDialog was={deleteFlow.key} runs={remove.isPending}
          onClose={() => setDeleteFlow(null)} onDelete={() => remove.mutate(deleteFlow.id)} />
      )}
    </Area>
  );
}
