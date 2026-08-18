import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, workflowApi, type IssueType, type Project } from "../../api";
import type { WorkflowSlotInfo } from "./types";

/** Woher der geltende Ablauf kommt — die wichtigste Information auf dieser Seite. */
const ORIGIN: Record<WorkflowSlotInfo["origin"], { label: string; cls: string; hint: string }> = {
  builtin: {
    label: "Standard",
    cls: "bg-surface text-muted",
    hint: "Ausgelieferter Ablauf. Änderungen am Standard wirken hier sofort.",
  },
  global: {
    label: "Globaler Satz",
    cls: "bg-blue-500/15 text-blue-300",
    hint: "Aus einem systemweiten Satz.",
  },
  user: {
    label: "Mein Satz",
    cls: "bg-violet-500/15 text-violet-300",
    hint: "Aus dem persönlichen Satz eines Projekt-Eigentümers.",
  },
  project: {
    label: "Angepasst",
    cls: "bg-amber-500/15 text-amber-300",
    hint: "Eigene Kopie dieses Projekts — vom Satz entkoppelt.",
  },
  none: { label: "fehlt", cls: "bg-red-500/15 text-red-300", hint: "Kein Ablauf hinterlegt." },
};

/**
 * Die Abläufe eines Projekts: was gilt, woher es kommt, und wie man es ändert.
 *
 * Anpassen legt eine Kopie an (der Satz wirkt dann hier nicht mehr), Zurücksetzen wirft die
 * Kopie weg. Laufende Vorgänge bleiben davon unberührt — sie hängen an ihrer Version.
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

  // Vorgangsarten des Projekts — ein Ticket-Ablauf darf je Art ein eigener sein.
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
      nav(`/projects/${project.key}/workflows/${d.id}`, { state: { from: `/projects/${project.key}?tab=workflows` } });
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
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Diese Abläufe steuern, was Traccoon tut — vom Ticket-Lebenszyklus bis zur Abnahme. Ohne
        Anpassung gilt der ausgelieferte Standard bzw. der gewählte Satz; „Anpassen“ legt eine
        Kopie für dieses Projekt an, „Zurücksetzen“ verwirft sie wieder.
      </p>

      {sets && sets.length > 1 && (
        <label className="block text-xs font-medium text-muted">
          Prozess-Satz dieses Projekts
          <select
            value={aktuellerSatz ?? ""}
            onChange={(e) => chooseSet.mutate(e.target.value ? Number(e.target.value) : null)}
            className="mt-1 w-full max-w-md rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink"
          >
            <option value="">Automatisch (Satz eines Eigentümers, sonst Standard)</option>
            {sets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.is_builtin ? " (ausgeliefert)" : ""}
              </option>
            ))}
          </select>
        </label>
      )}

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      <div className="space-y-2">
        {slots?.map((s) => {
          const o = ORIGIN[s.origin];
          return (
            <div key={s.slot} className="rounded border border-line bg-card p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{s.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-xs ${o.cls}`}>{o.label}</span>
                {!s.published && (
                  <span className="rounded bg-yellow-500/15 px-1.5 py-0.5 text-xs text-yellow-300">
                    nicht veröffentlicht
                  </span>
                )}
                <div className="flex-1" />
                {s.definition_id && (
                  <button
                    onClick={() => nav(`/projects/${project.key}/workflows/${s.definition_id}`, { state: { from: `/projects/${project.key}?tab=workflows` } })}
                    className="rounded border border-line px-2 py-1 text-xs hover:border-brand"
                  >
                    {s.origin === "project" ? "Bearbeiten" : "Ansehen"}
                  </button>
                )}
                {s.origin === "project" ? (
                  <button
                    onClick={() => {
                      setBusy(s.slot);
                      reset.mutate({ slot: s.slot });
                    }}
                    disabled={busy === s.slot}
                    className="rounded border border-line px-2 py-1 text-xs hover:border-amber-400 disabled:opacity-50"
                    title="Eigene Kopie verwerfen — es gilt wieder der Satz"
                  >
                    Zurücksetzen
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      setBusy(s.slot);
                      customize.mutate({ slot: s.slot });
                    }}
                    disabled={busy === s.slot || !s.definition_id}
                    className="rounded border border-line px-2 py-1 text-xs hover:border-brand disabled:opacity-50"
                    title="Kopie für dieses Projekt anlegen und bearbeiten"
                  >
                    Anpassen
                  </button>
                )}
              </div>
              <div className="mt-1 text-xs text-muted">{s.description}</div>

              {/* Ein Ticket-Ablauf darf je Vorgangsart ein eigener sein: ein Bug fährt dann
                  einen anderen Lebenszyklus als eine Aufgabe, alle übrigen folgen weiter
                  dem Satz. */}
              {s.subject_kind === "issue" && (meta?.types?.length ?? 0) > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
                  <span className="text-[11px] text-muted">Je Vorgangsart:</span>
                  {(s.per_issue_type || []).map((v) => (
                    <span key={v.issue_type_id}
                          className="flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300">
                      <button onClick={() => nav(`/projects/${project.key}/workflows/${v.definition_id}`, { state: { from: `/projects/${project.key}?tab=workflows` } })}
                              title="Eigenen Ablauf dieser Vorgangsart bearbeiten">
                        {v.issue_type_name}
                      </button>
                      <button onClick={() => reset.mutate({ slot: s.slot, art: v.issue_type_id })}
                              title="Eigenen Ablauf verwerfen — die Vorgangsart folgt wieder dem Satz"
                              className="hover:text-red-300">✕</button>
                    </span>
                  ))}
                  <select
                    value=""
                    onChange={(e) => e.target.value
                      && customize.mutate({ slot: s.slot, art: Number(e.target.value) })}
                    className="rounded border border-line bg-surface px-1.5 py-0.5 text-[11px] text-muted"
                  >
                    <option value="">+ eigener Ablauf für …</option>
                    {(meta?.types || [])
                      .filter((t) => !(s.per_issue_type || []).some((v) => v.issue_type_id === t.id))
                      .map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </div>
              )}
              <div className="mt-0.5 text-[10px] text-muted">
                {o.hint}
                {s.set_name && s.origin !== "project" ? ` (${s.set_name})` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
