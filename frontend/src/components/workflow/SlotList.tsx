import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, workflowApi, type IssueType, type Project } from "../../api";
import type { WorkflowSlotInfo } from "./types";
import {
  Bereich, Etikett, Fehlerzeile, Liste, ListenZeile, Zeilenknopf,
} from "../ui";
import { projektPfad } from "../../projectTabs";

/** Where the applicable flow comes from: the most important information on this page. */
// Keys instead of texts: the table comes into being while the module loads, and a tr() here
// would fix the language of the first call.
type EtikettFarbe = "neutral" | "gruen" | "gelb" | "rot" | "blau" | "violett" | "brand";
const ORIGIN: Record<WorkflowSlotInfo["origin"], { label: string; farbe: EtikettFarbe; hint: string }> = {
  builtin: { label: "slot_list.herkunft_builtin", farbe: "neutral",
             hint: "slot_list.herkunft_builtin_hinweis" },
  global: { label: "slot_list.herkunft_global", farbe: "blau",
            hint: "slot_list.herkunft_global_hinweis" },
  user: { label: "slot_list.herkunft_user", farbe: "violett",
          hint: "slot_list.herkunft_user_hinweis" },
  project: { label: "slot_list.herkunft_project", farbe: "gelb",
             hint: "slot_list.herkunft_project_hinweis" },
  none: { label: "slot_list.herkunft_none", farbe: "rot",
          hint: "slot_list.herkunft_none_hinweis" },
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
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

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
      nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: projektPfad(project.key, "settings", "processes") } });
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

  const aktuellerSatz = slots?.find((s) => s.origin !== "project")?.set_id ?? null;

  return (
    <Bereich hinweis={tr("slot_list.einleitung")}>
      {sets && sets.length > 1 && (
        <label className="block text-xs font-medium text-muted">
          {tr("slot_list.prozess_satz")}
          <select
            value={aktuellerSatz ?? ""}
            onChange={(e) => chooseSet.mutate(e.target.value ? Number(e.target.value) : null)}
            className="mt-1 w-full max-w-md rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          >
            <option value="">{tr("slot_list.automatisch_satz_eines_eigentuemers_sons")}</option>
            {sets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.is_builtin ? " (ausgeliefert)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      <Fehlerzeile text={err} />

      <Liste>
        {slots?.map((s) => {
          const o = ORIGIN[s.origin];
          return (
            <ListenZeile key={s.slot}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{s.name}</span>
                <Etikett farbe={o.farbe} titel={tr(o.hint)}>{tr(o.label)}</Etikett>
                {!s.published && (
                  <Etikett farbe="gelb">{tr("slot_list.nicht_veroeffentlicht")}</Etikett>
                )}
                <div className="flex-1" />
                {s.definition_id && (
                  <Zeilenknopf onClick={() => nav(`/projects/${project.key}/workflows/${s.definition_id}`,
                    { state: { from: projektPfad(project.key, "settings", "processes") } })}>
                    {tr(s.origin === "project" ? "slot_list.bearbeiten" : "slot_list.ansehen")}
                  </Zeilenknopf>
                )}
                {s.origin === "project" ? (
                  <Zeilenknopf gefahr titel={tr("slot_list.eigene_kopie_verwerfen_es_gilt_wieder_de")}
                    onClick={() => { setBusy(s.slot); reset.mutate({ slot: s.slot }); }}>
                    {tr("slot_list.zuruecksetzen")}
                  </Zeilenknopf>
                ) : (
                  <Zeilenknopf titel={tr("slot_list.kopie_fuer_dieses_projekt_anlegen_und_be")}
                    onClick={() => { setBusy(s.slot); customize.mutate({ slot: s.slot }); }}>
                    Anpassen
                  </Zeilenknopf>
                )}
              </div>
              <div className="mt-1 text-xs text-muted">{s.description}</div>

              {/* Ein Ticket-Ablauf darf je Vorgangsart ein eigener sein: ein Bug fährt dann
                  einen anderen Lebenszyklus als eine Aufgabe, alle übrigen folgen weiter
                  dem Satz. */}
              {s.subject_kind === "issue" && (meta?.types?.length ?? 0) > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
                  <span className="text-[11px] text-muted">{tr("slot_list.je_vorgangsart")}</span>
                  {(s.per_issue_type || []).map((v) => (
                    <span key={v.issue_type_id}
                          className="flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                      <button onClick={() => nav(`/projects/${project.key}/workflows/${v.definition_id}`, { state: { from: projektPfad(project.key, "settings", "processes") } })}
                              title={tr("slot_list.eigenen_ablauf_dieser_vorgangsart_bearbe")}>
                        {v.issue_type_name}
                      </button>
                      <button onClick={() => reset.mutate({ slot: s.slot, art: v.issue_type_id })}
                              title={tr("slot_list.eigenen_ablauf_verwerfen_die_vorgangsart")}
                              className="hover:text-red-300">✕</button>
                    </span>
                  ))}
                  <select
                    value=""
                    onChange={(e) => e.target.value
                      && customize.mutate({ slot: s.slot, art: Number(e.target.value) })}
                    className="rounded border border-line bg-surface px-1.5 py-0.5 text-[11px] text-muted"
                  >
                    <option value="">{tr("slot_list.eigener_ablauf_fuer")}</option>
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
            </ListenZeile>
          );
        })}
      </Liste>
    </Bereich>
  );
}
