import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Project } from "../api";
import {
  Aktionen, Dialog, DialogFuss, EINGABE, Feld as Formularfeld, Fehlerzeile, ICON, IconKnopf,
  LoeschDialog, KNOPF_KLEIN} from "./ui";

interface Wert {
  id: number; value: string; label: string; color: string; order: number; enabled: boolean;
}
interface Feld {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; order: number; description: string; enabled: boolean;
  source: string; options_source: string; builtin: boolean; project_id: number | null;
  options: Wert[]; dynamic_options: [string, string][];
}
interface Typ {
  id: number; key: string; name: string; icon: string; backing: string;
  project_id: number | null; fields: Feld[];
}

const FELDTYP: [string, string][] = [
  ["text", "Text"], ["number", "Zahl"], ["date", "Datum"],
  ["boolean", "Ja/Nein"], ["select", "Auswahl"],
];

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Eigene Felder dieses Projekts.
 *
 * An artifact is initially something undefined; it gets its meaning only through its fields.
 * Ticket and hardware bring a shipped set along; here the project adds its own. The shipped
 * ones are for viewing only: board, sprints and the AI lifecycle run on them, which is why
 * they cannot be removed.
 *
 * What comes into being here applies exclusively to this project and appears immediately in
 * the ticket respectively hardware view.
 */
export default function ProjectFields({ project }: { project: Project }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data: typen } = useQuery({
    queryKey: ["artifact-types", project.id],
    queryFn: () => api.get<Typ[]>(`/artifact-types?project_id=${project.id}`),
  });

  const inv = () => qc.invalidateQueries({ queryKey: ["artifact-types", project.id] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const ok = () => { setErr(""); inv(); };

  // Only what concerns this project: hardware only in hardware projects.
  const sichtbar = (typen || []).filter(
    (t) => t.backing !== "hardware_asset" || project.has_hardware);

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        {tr("project_fields.einleitung")}
      </p>
      <Fehlerzeile text={err} />

      {sichtbar.map((t) => (
        <ArtefaktFelder key={t.id} t={t} projectId={project.id} onFail={fail} onOk={ok} />
      ))}
    </div>
  );
}

/**
 * The fields of one artifact within this project.
 *
 * A field used to be a row of controls: label as an input written on blur, type as a select,
 * three checkboxes and the value list behind them. Which of them belonged to which field was
 * a matter of counting columns as soon as the row wrapped. It is a dialog now, and the row
 * says what the field is.
 */
function ArtefaktFelder({ t, projectId, onFail, onOk }: {
  t: Typ; projectId: number; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [dialog, setDialog] = useState<Feld | {} | null>(null);   // {} = neues Feld
  const [loeschFeld, setLoeschFeld] = useState<Feld | null>(null);

  const anlegen = useMutation({
    mutationFn: (neu: { key: string; label: string; kind: string; multi: boolean }) =>
      api.post(`/artifact-types/${t.id}/fields?project_id=${projectId}`, neu),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-fields/${id}`, rest),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-fields/${id}`),
    onSuccess: () => { setLoeschFeld(null); onOk(); }, onError: onFail,
  });

  // Editable is only what belongs to THIS project. Shipped fields and those applying
  // everywhere stand here merely for viewing; changing them would concern all projects.
  const fest = t.fields.filter((f) => f.project_id !== projectId);
  const eigene = t.fields.filter((f) => f.project_id === projectId);

  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">{t.icon}</span>
        <span className="font-medium">{t.name}</span>
      </div>

      {fest.length > 0 && (
        <div className="mb-3 text-xs text-muted">
          {tr("project_fields.gilt_ueberall")}: {fest.map((f) => f.label).join(" · ")}
          <span className="ml-1 text-[11px]">{tr("project_fields.hier_nicht_aenderbar")}</span>
        </div>
      )}

      <div className="space-y-1">
        {eigene.map((f) => (
          <div key={f.id}
            className={`flex items-center gap-2 rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
              <span className="font-mono text-xs text-muted">{f.key}</span>
              <span className={f.enabled ? "" : "line-through"}>{f.label}</span>
              <span className="text-xs text-muted">{FELDTYP.find(([k]) => k === f.kind)?.[1]}</span>
              {f.multi && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.mehrfach")}</span>}
              {f.required && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.pflicht")}</span>}
              {f.kind === "select" && f.options.length > 0 && (
                <span className="truncate text-xs text-muted">
                  {f.options.filter((o) => o.enabled).map((o) => o.label || o.value).join(" · ")}
                </span>
              )}
            </div>
            <Aktionen>
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(f)} />
              <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschFeld(f)} />
            </Aktionen>
          </div>
        ))}
        {eigene.length === 0 && (
          <div className="text-xs text-muted">{tr("project_fields.noch_keine_eigenen_felder")}</div>
        )}
      </div>

      <button onClick={() => setDialog({})}
        className={KNOPF_KLEIN.neben}>
        {ICON.neu} {tr("artifact_types_panel.feld_anlegen")}
      </button>

      {dialog && (
        <FeldDialog feld={"id" in dialog ? (dialog as Feld) : null}
          laeuft={anlegen.isPending || aendern.isPending}
          onClose={() => setDialog(null)}
          onAnlegen={(neu) => anlegen.mutate(neu)}
          onAendern={(id, patch) => aendern.mutate({ id, ...patch })}
          onFail={onFail} onOk={onOk} />
      )}
      {loeschFeld && (
        <LoeschDialog was={loeschFeld.label} laeuft={loeschen.isPending}
          onClose={() => setLoeschFeld(null)} onLoeschen={() => loeschen.mutate(loeschFeld.id)} />
      )}
    </div>
  );
}

function FeldDialog({ feld, laeuft, onClose, onAnlegen, onAendern, onFail, onOk }: {
  feld: Feld | null; laeuft: boolean; onClose: () => void;
  onAnlegen: (neu: { key: string; label: string; kind: string; multi: boolean }) => void;
  onAendern: (id: number, patch: Record<string, any>) => void;
  onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [key, setKey] = useState(feld?.key || "");
  const [label, setLabel] = useState(feld?.label || "");
  const [kind, setKind] = useState(feld?.kind || "text");
  const [multi, setMulti] = useState(!!feld?.multi);
  const [required, setRequired] = useState(!!feld?.required);
  const [enabled, setEnabled] = useState(feld ? feld.enabled : true);

  return (
    <Dialog titel={tr(feld ? "artifact_types_panel.feld_bearbeiten" : "artifact_types_panel.feld_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={laeuft}
        deaktiviert={!label.trim() || (!feld && !key.trim())}
        speichernText={feld ? undefined : tr("common.anlegen")}
        onSpeichern={() => feld
          ? onAendern(feld.id, { label, kind, multi, required, enabled })
          : onAnlegen({ key: key.trim(), label: label.trim(), kind, multi })} />}>
      <div className="space-y-3">
        <Formularfeld label={tr("project_fields.schluessel_kunde")}>
          <input value={key} disabled={!!feld} autoFocus={!feld} onChange={(e) => setKey(e.target.value)}
            className={`${EINGABE} font-mono disabled:opacity-60`} />
        </Formularfeld>
        <Formularfeld label={tr("project_fields.bezeichnung_kunde")}>
          <input value={label} autoFocus={!!feld} onChange={(e) => setLabel(e.target.value)} className={EINGABE} />
        </Formularfeld>
        <Formularfeld label={tr("artifact_types_panel.typ")}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={EINGABE}>
            {FELDTYP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Formularfeld>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-sm text-ink"
            title={tr("project_fields.darf_ein_ticket_mehrere_werte_gleichzeit")}>
            <input type="checkbox" checked={multi} onChange={(e) => setMulti(e.target.checked)} />
            {tr("artifact_types_panel.mehrfachauswahl")}
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            {tr("artifact_types_panel.pflicht")}
          </label>
          {feld && (
            <label className="flex items-center gap-2 text-sm text-ink"
              title={tr("project_fields.abgeschaltete_felder_werden_nicht_mehr_a")}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              {tr("artifact_types_panel.aktiv")}
            </label>
          )}
        </div>
        {feld && feld.kind === "select" && (
          <div className="border-t border-line pt-3">
            <div className="mb-1 text-xs font-medium text-muted">{tr("project_fields.werte")}</div>
            <Werteliste feld={feld} onFail={onFail} onOk={onOk} />
          </div>
        )}
      </div>
    </Dialog>
  );
}

function Werteliste({ feld, onFail, onOk }: {
  feld: Feld; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [wert, setWert] = useState("");
  const anlegen = useMutation({
    mutationFn: () => api.post(`/artifact-fields/${feld.id}/options`, { value: wert.trim() }),
    onSuccess: () => { setWert(""); onOk(); }, onError: onFail,
  });
  const aendern = useMutation({
    mutationFn: ({ id, ...rest }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-field-options/${id}`, rest),
    onSuccess: onOk, onError: onFail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-field-options/${id}`),
    onSuccess: onOk, onError: onFail,
  });

  return (
    <div className="space-y-1">
      {feld.options.map((o) => (
        <div key={o.id} className="flex items-center gap-2 rounded border border-line px-2 py-1 text-sm">
          <span className={o.enabled ? "flex-1" : "flex-1 text-muted line-through"}>{o.label || o.value}</span>
          <IconKnopf icon={o.enabled ? "○" : "●"}
            titel={tr(o.enabled ? "project_fields.nicht_mehr_anbieten" : "artifact_types_panel.wieder_anbieten")}
            onClick={() => aendern.mutate({ id: o.id, enabled: !o.enabled })} />
          <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
            onClick={() => loeschen.mutate(o.id)} />
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input value={wert} onChange={(e) => setWert(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && wert.trim() && anlegen.mutate()}
          placeholder={tr("project_fields.wert_enter")} className={`flex-1 text-sm ${inp}`} />
        <IconKnopf icon={ICON.neu} titel={tr("common.anlegen")} disabled={!wert.trim()}
          onClick={() => wert.trim() && anlegen.mutate()} />
      </div>
    </div>
  );
}
