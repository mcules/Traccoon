import { useState } from "react";
import { tr } from "../../i18n";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, workflowApi } from "../../api";
import type { WorkflowDefinition, WorkflowSubjectKind } from "./types";
import {
  Actions, Area, Dialog, DialogFuss, Etikett, Fehlerzeile, ICON, IconButton, Listing,
  ListingLeer, ListenLine, LoeschDialog, State, BUTTON } from "../ui";

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
  const [newDialog, setNewDialog] = useState(false);
  const [loeschFlow, setLoeschFlow] = useState<WorkflowDefinition | null>(null);
  // Umbenennen: Name und Schlüssel entstehen oft nebenbei (aus einer Route, aus einem
  // Job-Namen) und beschreiben dann den Auslöser statt der Sache.
  const [umbenennen, setUmbenennen] = useState<WorkflowDefinition | null>(null);
  const [nameNew, setNameNew] = useState("");
  const [keyNew, setKeyNew] = useState("");

  const { data: all } = useQuery({ queryKey: ["workflows-all"], queryFn: workflowApi.listAll });
  const { data: templates } = useQuery({
    queryKey: ["workflow-templates"], queryFn: workflowApi.templates, staleTime: 30 * 60_000 });
  const gewaehlt = (templates || []).find((v) => v.key === f.template);
  // Slot flows stand at the top in the process set, project flows in the respective project.
  const eigene = (all || []).filter((d) => d.project_id === null && !d.slot && !d.archived_at);

  const open_it = (d: WorkflowDefinition) =>
    nav(`/workflows/${d.id}`, { state: { from: "/processes/own" } });
  const inv = () => qc.invalidateQueries({ queryKey: ["workflows-all"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const create = useMutation({
    mutationFn: () => workflowApi.create({
      project_id: null, key: f.key.trim(), name: f.name.trim(),
      subject_kind: f.subject_kind, description: f.description.trim() || undefined,
      template: f.template || undefined,
    }),
    onSuccess: (d) => { setF(EMPTY); setErr(""); setNewDialog(false); inv(); nav(`/workflows/${d.id}`, { state: { from: "/processes/own" } }); },
    onError: fail,
  });
  const umschalten = useMutation({
    mutationFn: (d: WorkflowDefinition) => workflowApi.update(d.id, { enabled: !d.enabled }),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const speichern = useMutation({
    mutationFn: () => workflowApi.update(umbenennen!.id,
      { name: nameNew.trim(), key: keyNew.trim() }),
    onSuccess: () => { setErr(""); setUmbenennen(null); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => workflowApi.del(id),
    onSuccess: () => { setErr(""); setLoeschFlow(null); inv(); }, onError: fail,
  });

  return (
    <Area hinweis={tr("own_workflows_panel.einleitung")}>
      <Fehlerzeile text={err} />

      {eigene.length > 0 ? (
        /* Ohne Spaltenköpfe: bei einer Handvoll Einträgen erklären sich Name, Schlüssel und
           Zustand von selbst, und eine Überschriftenzeile wäre eine Zeile Rauschen über
           fünf Zeilen Inhalt. */
        <Listing>
          {eigene.map((d) => (
            <ListenLine key={d.id} spalten={SPALTEN} gedimmt={!d.enabled}
              onClick={() => open_it(d)}>
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
                ? <State farbe="grau" text={tr("own_workflows_panel.aus")} />
                : d.current_version_id
                  ? <State farbe="gruen" text={tr("proc.veroeffentlicht")} />
                  : <State farbe="gelb" text={tr("own_workflows.nur_entwurf")} />}
              {/* Klicks auf die Knöpfe gehören den Knöpfen — sonst öffnete sich hinter dem
                  Löschdialog auch noch der Editor. */}
              <div className="ml-auto shrink-0 sm:ml-0 sm:justify-self-end"
                onClick={(e) => e.stopPropagation()}>
                <Actions>
                  <IconButton icon={ICON.bearbeiten} titel={tr("own_workflows_panel.editor")}
                    onClick={() => open_it(d)} />
                  <IconButton icon="🏷" titel={tr("own_workflows_panel.umbenennen")}
                    onClick={() => { setErr(""); setNameNew(d.name); setKeyNew(d.key); setUmbenennen(d); }} />
                  <IconButton icon={d.enabled ? "⏸" : "⏵"} onClick={() => umschalten.mutate(d)}
                    titel={tr(d.enabled ? "own_workflows_panel.aus" : "own_workflows_panel.an")} />
                  <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                    onClick={() => setLoeschFlow(d)} />
                </Actions>
              </div>
            </ListenLine>
          ))}
        </Listing>
      ) : (
        <Listing><ListingLeer>{tr("own_workflows_panel.noch_keine_eigenen_prozesse")}</ListingLeer></Listing>
      )}

      {umbenennen && (
        <Dialog titel={tr("own_workflows_panel.umbenennen")} onClose={() => setUmbenennen(null)}
          fuss={<DialogFuss onAbbrechen={() => setUmbenennen(null)}
            deaktiviert={!nameNew.trim() || !keyNew.trim()} laeuft={speichern.isPending}
            onSpeichern={() => speichern.mutate()} />}>
          <div className="space-y-3">
            <label className="block text-xs font-medium text-muted">
              {tr("own_workflows_panel.name")}
              <input value={nameNew} autoFocus onChange={(e) => setNameNew(e.target.value)}
                className={`mt-1 w-full ${inp}`} />
            </label>
            <label className="block text-xs font-medium text-muted">
              {tr("own_workflows_panel.schluessel")}
              <input value={keyNew} onChange={(e) => setKeyNew(e.target.value)}
                className={`mt-1 w-full font-mono ${inp}`} />
              <span className="mt-1 block text-[11px] text-muted">
                {tr("own_workflows_panel.schluessel_hinweis")}
              </span>
            </label>
          </div>
        </Dialog>
      )}

      <button onClick={() => { setErr(""); setNewDialog(true); }}
        className={BUTTON.haupt}>
        {ICON.neu} {tr("own_workflows_panel.ablauf_anlegen")}
      </button>

      {newDialog && (
        <Dialog breit titel={tr("own_workflows_panel.ablauf_anlegen")} onClose={() => setNewDialog(false)}
          fuss={<DialogFuss onAbbrechen={() => setNewDialog(false)} laeuft={create.isPending}
            deaktiviert={!f.key.trim() || !f.name.trim()} speichernText={tr("common.anlegen")}
            onSpeichern={() => create.mutate()} />}>
          <div className="space-y-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <input value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })}
            placeholder={tr("own_workflows_panel.key_platzhalter")} className={inp} />
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder={tr("own_workflows_panel.name")} className={inp} />
          <select value={f.template} className={inp}
            onChange={(e) => setF({ ...f, template: e.target.value })}>
            <option value="">{tr("own_workflows.leeres_geruest")}</option>
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
      {loeschFlow && (
        <LoeschDialog was={loeschFlow.key} laeuft={remove.isPending}
          onClose={() => setLoeschFlow(null)} onLoeschen={() => remove.mutate(loeschFlow.id)} />
      )}
    </Area>
  );
}
