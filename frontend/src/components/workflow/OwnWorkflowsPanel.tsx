import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowDefinition, WorkflowSubjectKind } from "./types";
import {
  Aktionen, Bereich, Dialog, DialogFuss, Etikett, Fehlerzeile, ICON, IconKnopf, Liste,
  ListeLeer, ListenZeile, LoeschDialog, Zustand,
} from "../ui";

const EMPTY = { key: "", name: "", subject_kind: "standalone" as WorkflowSubjectKind,
                description: "", template: "" };
const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
/** Drei Spalten: der Ablauf selbst, sein Zustand, die Handgriffe. */
const SPALTEN = "sm:grid-cols-[minmax(0,1fr)_9rem_auto]";

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
  const [neuDialog, setNeuDialog] = useState(false);
  const [loeschAblauf, setLoeschAblauf] = useState<WorkflowDefinition | null>(null);

  const { data: alle } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  const { data: vorlagen } = useQuery({
    queryKey: ["workflow-templates"], queryFn: workflowApi.templates, staleTime: 30 * 60_000 });
  const gewaehlt = (vorlagen || []).find((v) => v.key === f.template);
  // Slot flows stand at the top in the process set, project flows in the respective project.
  const eigene = (alle || []).filter((d) => d.project_id === null && !d.slot && !d.archived_at);

  const oeffnen = (d: WorkflowDefinition) =>
    nav(`/workflows/${d.id}`, { state: { from: "/processes/eigene" } });
  const inv = () => qc.invalidateQueries({ queryKey: ["workflows-all"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const anlegen = useMutation({
    mutationFn: () => workflowApi.create({
      project_id: null, key: f.key.trim(), name: f.name.trim(),
      subject_kind: f.subject_kind, description: f.description.trim() || undefined,
      template: f.template || undefined,
    }),
    onSuccess: (d) => { setF(EMPTY); setErr(""); setNeuDialog(false); inv(); nav(`/workflows/${d.id}`, { state: { from: "/processes/eigene" } }); },
    onError: fail,
  });
  const umschalten = useMutation({
    mutationFn: (d: WorkflowDefinition) => workflowApi.update(d.id, { enabled: !d.enabled }),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => workflowApi.del(id),
    onSuccess: () => { setErr(""); setLoeschAblauf(null); inv(); }, onError: fail,
  });

  return (
    <Bereich hinweis={tr("own_workflows_panel.einleitung")}>
      <Fehlerzeile text={err} />

      {eigene.length > 0 ? (
        /* Ohne Spaltenköpfe: bei einer Handvoll Einträgen erklären sich Name, Schlüssel und
           Zustand von selbst, und eine Überschriftenzeile wäre eine Zeile Rauschen über
           fünf Zeilen Inhalt. */
        <Liste>
          {eigene.map((d) => (
            <ListenZeile key={d.id} spalten={SPALTEN} gedimmt={!d.enabled}
              onClick={() => oeffnen(d)}>
              {/* Zwei Zeilen statt fünf Spalten: der Name trägt den Eintrag, alles
                  Technische steht eine Etage tiefer und leiser. Das hält die Liste auch
                  dann ausgerichtet, wenn ein Name lang und der nächste kurz ist. */}
              <div className="min-w-0 basis-full sm:basis-auto">
                <div className="truncate font-medium text-ink">{d.name}</div>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                  <span className="truncate font-mono">{d.key}</span>
                  <span className="text-line">·</span>
                  <Etikett>{d.subject_kind}</Etikett>
                </div>
              </div>
              {!d.enabled
                ? <Zustand farbe="grau" text={tr("own_workflows_panel.aus")} />
                : d.current_version_id
                  ? <Zustand farbe="gruen" text={tr("proc.veroeffentlicht")} />
                  : <Zustand farbe="gelb" text={tr("own_workflows.nur_entwurf")} />}
              {/* Klicks auf die Knöpfe gehören den Knöpfen — sonst öffnete sich hinter dem
                  Löschdialog auch noch der Editor. */}
              <div className="ml-auto shrink-0 sm:ml-0 sm:justify-self-end"
                onClick={(e) => e.stopPropagation()}>
                <Aktionen>
                  <IconKnopf icon={ICON.bearbeiten} titel={tr("own_workflows_panel.editor")}
                    onClick={() => oeffnen(d)} />
                  <IconKnopf icon={d.enabled ? "⏸" : "⏵"} onClick={() => umschalten.mutate(d)}
                    titel={tr(d.enabled ? "own_workflows_panel.aus" : "own_workflows_panel.an")} />
                  <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                    onClick={() => setLoeschAblauf(d)} />
                </Aktionen>
              </div>
            </ListenZeile>
          ))}
        </Liste>
      ) : (
        <Liste><ListeLeer>{tr("own_workflows_panel.noch_keine_eigenen_prozesse")}</ListeLeer></Liste>
      )}

      <button onClick={() => { setErr(""); setNeuDialog(true); }}
        className="rounded bg-brand px-3 py-1.5 text-sm text-white">
        {ICON.neu} {tr("own_workflows_panel.ablauf_anlegen")}
      </button>

      {neuDialog && (
        <Dialog breit titel={tr("own_workflows_panel.ablauf_anlegen")} onClose={() => setNeuDialog(false)}
          fuss={<DialogFuss onAbbrechen={() => setNeuDialog(false)} laeuft={anlegen.isPending}
            deaktiviert={!f.key.trim() || !f.name.trim()} speichernText={tr("common.anlegen")}
            onSpeichern={() => anlegen.mutate()} />}>
          <div className="space-y-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })}
            placeholder={tr("own_workflows_panel.key_platzhalter")} className={inp} />
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder={tr("own_workflows_panel.name")} className={inp} />
          <select value={f.template} className={inp}
            onChange={(e) => setF({ ...f, template: e.target.value })}>
            <option value="">{tr("own_workflows.leeres_geruest")}</option>
            {(vorlagen || []).map((v) => (
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
            placeholder={tr("own_workflows_panel.beschreibung_optional")} className={`${inp} min-w-48 flex-1`} />
        </div>
        {gewaehlt && (
          <p className="text-xs text-muted">
            <b>{gewaehlt.name}</b> — {gewaehlt.description}{" "}
            <span className="text-brand">{gewaehlt.hinweis}</span>
          </p>
        )}
          </div>
        </Dialog>
      )}
      {loeschAblauf && (
        <LoeschDialog was={loeschAblauf.key} laeuft={loeschen.isPending}
          onClose={() => setLoeschAblauf(null)} onLoeschen={() => loeschen.mutate(loeschAblauf.id)} />
      )}
    </Bereich>
  );
}
