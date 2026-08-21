import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, workflowApi, type IssueType, type Project } from "../../api";
import type { WorkflowSlotInfo } from "./types";
import {
  Area, Tag, Errorrow, Listing, ListenLine, Rowbutton, BUTTON_TEXT} from "../ui";
import { projectPath } from "../../projectTabs";

/** Where the applicable flow comes from: the most important information on this page. */
// Keys instead of texts: the table comes into being while the module loads, and a tr() here
// would fix the language of the first call.
type TagColor = "neutral" | "green" | "yellow" | "red" | "blue" | "violet" | "brand";
const ORIGIN: Record<WorkflowSlotInfo["origin"], { label: string; color: TagColor; hint: string }> = {
  builtin: { label: "slot_list.default", color: "neutral",
             hint: "slot_list.shipped_flow_changes_default" },
  global: { label: "slot_list.global_set", color: "blue",
            hint: "slot_list.system_wide_set" },
  user: { label: "slot_list.my_set", color: "violet",
          hint: "slot_list.personal_set_project_owner" },
  project: { label: "slot_list.adapted", color: "yellow",
             hint: "slot_list.copy_project_no_longer" },
  none: { label: "slot_list.missing", color: "red",
          hint: "slot_list.no_flow_assigned" },
};

/**
 * The flows of a project: what applies, where it comes from and how to change it.
 *
 * Adjusting creates a copy (the set then no longer acts here), and resetting throws the copy
 * away. Running processes stay unaffected: they hang off their version.
 */
export default function SlotList({ project }: { project: Project }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");

  const { data: slots } = useQuery({
    queryKey: ["workflow-slots", project.id],
    queryFn: () => workflowApi.projectSlots(project.id),
  });
  const { data: sets } = useQuery({ queryKey: ["workflow-sets"], queryFn: workflowApi.sets });

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["workflow-slots", project.id] });
    qc.invalidateQueries({ queryKey: ["workflows", project.id] });
  };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  // Issue types of the project: a ticket flow may be a separate one per type.
  const { data: meta } = useQuery({
    queryKey: ["meta", project.id],
    queryFn: () => api.get<{ types: IssueType[] }>(`/projects/${project.id}/meta`),
  });

  const customize = useMutation({
    mutationFn: ({ slot, art }: { slot: string; art?: number }) =>
      workflowApi.customizeSlot(project.id, slot, art),
    onSuccess: (d) => {
      setErr("");
      inv();
      nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: projectPath(project.key, "settings", "processes") } });
    },
    onError: fail,
    onSettled: () => setBusy(""),
  });
  const reset = useMutation({
    mutationFn: ({ slot, art }: { slot: string; art?: number }) =>
      workflowApi.resetSlot(project.id, slot, art),
    onSuccess: () => {
      setErr("");
      inv();
    },
    onError: fail,
    onSettled: () => setBusy(""),
  });
  const chooseSet = useMutation({
    mutationFn: (setId: number | null) => workflowApi.setProjectSet(project.id, setId),
    onSuccess: () => {
      setErr("");
      inv();
    },
    onError: fail,
  });

  const currentPreset = slots?.find((s) => s.origin !== "project")?.set_id ?? null;

  return (
    <Area hint={tr("slot_list.these_flows_drive_work")}>
      {sets && sets.length > 1 && (
        <label className="block text-xs font-medium text-muted">
          {tr("slot_list.process_set_project")}
          <select
            value={currentPreset ?? ""}
            onChange={(e) => chooseSet.mutate(e.target.value ? Number(e.target.value) : null)}
            className="mt-1 w-full max-w-md rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          >
            <option value="">{tr("slot_list.automatic_an_owner_s_set_otherwise_the_defaul")}</option>
            {sets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.is_builtin ? " (ausgeliefert)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      <Errorrow text={err} />

      <Listing>
        {slots?.map((s) => {
          const o = ORIGIN[s.origin];
          return (
            <ListenLine key={s.slot}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{s.name}</span>
                <Tag color={o.color} title={tr(o.hint)}>{tr(o.label)}</Tag>
                {!s.published && (
                  <Tag color="yellow">{tr("slot_list.not_published")}</Tag>
                )}
                <div className="flex-1" />
                {s.definition_id && (
                  <Rowbutton onClick={() => nav(`/projects/${project.key}/workflows/${s.definition_id}`,
                    { state: { from: projectPath(project.key, "settings", "processes") } })}>
                    {tr(s.origin === "project" ? "slot_list.edit" : "slot_list.view")}
                  </Rowbutton>
                )}
                {s.origin === "project" ? (
                  <Rowbutton danger title={tr("slot_list.drop_the_copy_the_set_applies_again")}
                    onClick={() => { setBusy(s.slot); reset.mutate({ slot: s.slot }); }}>
                    {tr("slot_list.reset")}
                  </Rowbutton>
                ) : (
                  <Rowbutton title={tr("slot_list.create_a_copy_for_this_project_and_edit_it")}
                    onClick={() => { setBusy(s.slot); customize.mutate({ slot: s.slot }); }}>
                    Anpassen
                  </Rowbutton>
                )}
              </div>
              <div className="mt-1 text-xs text-muted">{s.description}</div>

              {/* A ticket flow may be its own per kind of matter: a bug then runs a different
                  lifecycle than a task, all the others keep following
                  dem Satz. */}
              {s.subject_kind === "issue" && (meta?.types?.length ?? 0) > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
                  <span className="text-[11px] text-muted">{tr("slot_list.per_issue_type")}</span>
                  {(s.per_issue_type || []).map((v) => (
                    <span key={v.issue_type_id}
                          className="flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                      <button onClick={() => nav(`/projects/${project.key}/workflows/${v.definition_id}`, { state: { from: projectPath(project.key, "settings", "processes") } })}
                              title={tr("slot_list.edit_the_own_flow_of_this_issue_type")}>
                        {v.issue_type_name}
                      </button>
                      <button onClick={() => reset.mutate({ slot: s.slot, art: v.issue_type_id })}
                              title={tr("slot_list.drop_the_own_flow_the_issue_type_follows_the")}
                              className={BUTTON_TEXT.danger}>✕</button>
                    </span>
                  ))}
                  <select
                    value=""
                    onChange={(e) => e.target.value
                      && customize.mutate({ slot: s.slot, art: Number(e.target.value) })}
                    className="rounded border border-line bg-surface px-1.5 py-0.5 text-[11px] text-muted"
                  >
                    <option value="">{tr("slot_list.own_flow")}</option>
                    {(meta?.types || [])
                      .filter((t) => !(s.per_issue_type || []).some((v) => v.issue_type_id === t.id))
                      .map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </div>
              )}
              <div className="mt-0.5 text-[11px] text-muted">
                {tr(o.hint)}
                {s.set_name && s.origin !== "project" ? ` (${s.set_name})` : ""}
              </div>
            </ListenLine>
          );
        })}
      </Listing>
    </Area>
  );
}
