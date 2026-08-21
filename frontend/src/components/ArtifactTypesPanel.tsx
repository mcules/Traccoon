import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import {
  Actions, Dialog, DialogFuss, INPUT_VALUE, Field as Formularfeld, Fehlerzeile, ICON, IconButton,
  LoeschDialog, Area, Listing, ListenLine, BUTTON, BUTTON_KLEIN } from "./ui";

interface Value {
  id: number; value: string; label: string; color: string; order: number; enabled: boolean;
  category: string; waiting: boolean;
}
interface Field {
  id: number; key: string; label: string; kind: string; multi: boolean;
  required: boolean; order: number; description: string; enabled: boolean;
  source: string; options_source: string; builtin: boolean;
  options: Value[]; dynamic_options: [string, string][];
}
interface Kind {
  id: number; key: string; name: string; plural: string; icon: string; color: string;
  backing: string; project_id: number | null;
  builtin: boolean; enabled: boolean; description: string;
  fields: Field[];
}

// The lists hold keys, not texts: they come into being while the module loads, and a tr()
// at this place would freeze the language of the first call.
const KATEGORIE: [string, string][] = [
  ["todo", "artifact_types_panel.kat_todo"],
  ["in_progress", "artifact_types_panel.kat_in_progress"],
  ["done", "artifact_types_panel.kat_done"],
];

const FELDTYP: [string, string][] = [
  ["text", "artifact_types_panel.typ_text"],
  ["number", "artifact_types_panel.typ_number"],
  ["date", "artifact_types_panel.typ_date"],
  ["boolean", "artifact_types_panel.typ_boolean"],
  ["select", "artifact_types_panel.typ_select"],
];

const HERKUNFT: Record<string, string> = {
  issue_type: "artifact_types_panel.herkunft_issue_type",
  board_status: "artifact_types_panel.herkunft_board_status",
  sprint: "artifact_types_panel.herkunft_sprint",
  member: "artifact_types_panel.herkunft_member",
  location: "artifact_types_panel.herkunft_location",
};

const BACKING_LABEL: Record<string, string> = {
  issue: "artifact_types_panel.backing_issue",
  hardware_asset: "artifact_types_panel.backing_hardware",
  generic: "artifact_types_panel.backing_generic",
};

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Artifact register: what Traccoon manages, which states it knows and which fields it
 * carries.
 *
 * Three levels following the Artefakt example: an **artifact type** orders things (process,
 * object), an **artifact** is the thing itself (ticket, hardware), and below it hang the
 * **fields** with their **value list**. The field says whether a single unit may carry one
 * or several values from it.
 *
 * A field used to be edited in its own row: label as an input, type as a select, three
 * checkboxes and the value list behind them, all in a line that wrapped into a column of
 * unlabelled controls on anything narrower than a desktop. Editing happens in a dialog now,
 * and the row says what the field is.
 */
export default function ArtifactTypesPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [newDialog, setNewDialog] = useState(false);
  const [loeschKind, setLoeschKind] = useState<Kind | null>(null);

  const { data: typen } = useQuery({
    queryKey: ["artifact-types"], queryFn: () => api.get<Kind[]>("/artifact-types"),
  });

  const inv = () => { qc.invalidateQueries({ queryKey: ["artifact-types"] }); };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));
  const ok = () => { setErr(""); inv(); };

  const create = useMutation({
    mutationFn: (fresh: { key: string; name: string; icon: string }) => api.post("/artifact-types", fresh),
    onSuccess: () => { setNewDialog(false); ok(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-types/${id}`),
    onSuccess: () => { setLoeschKind(null); ok(); }, onError: fail,
  });

  // Ein Satz, ein Schlüssel: die Hervorhebungen fallen weg, weil sich Markup mitten im Satz
  // nicht mitübersetzen lässt (die Wortstellung ist anderswo eine andere).
  return (
    <Area hinweis={tr("artifact_types_panel.einleitung")}>
      <Fehlerzeile text={err} />

      <Listing>
        {typen?.map((kind) => (
          <ArtifactKarte key={kind.id} t={kind} onFail={fail} onOk={ok}
                         onDelete={() => setLoeschKind(kind)} />
        ))}
      </Listing>

      <button onClick={() => { setErr(""); setNewDialog(true); }}
        className={BUTTON.haupt}>
        {ICON.neu} {tr("artifact_types_panel.eigenes_artefakt_anlegen")}
      </button>

      {newDialog && (
        <ArtifactDialog fehler={err} laeuft={create.isPending}
          onClose={() => { setNewDialog(false); setErr(""); }}
          onSpeichern={(values) => create.mutate(values)} />
      )}
      {loeschKind && (
        <LoeschDialog was={loeschKind.name} laeuft={remove.isPending}
          hinweis={tr("artifact_types_panel.loeschen_hinweis")}
          onClose={() => setLoeschKind(null)} onLoeschen={() => remove.mutate(loeschKind.id)} />
      )}
    </Area>
  );
}

function ArtifactDialog({ fehler: error, laeuft: running, onClose, onSpeichern }: {
  fehler: string; laeuft: boolean; onClose: () => void;
  onSpeichern: (values: { key: string; name: string; icon: string }) => void;
}) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("📦");

  return (
    <Dialog titel={tr("artifact_types_panel.eigenes_artefakt_anlegen")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!key.trim() || !name.trim()} laeuft={running}
        speichernText={tr("common.anlegen")}
        onSpeichern={() => onSpeichern({ key: key.trim(), name: name.trim(), icon })} />}>
      <Fehlerzeile text={error} />
      <div className="space-y-3">
        <div className="flex gap-3">
          <div className="w-20">
            <Formularfeld label={tr("artifact_types_panel.icon")}>
              <input value={icon} onChange={(e) => setIcon(e.target.value)}
                className={`${INPUT_VALUE} text-center`} />
            </Formularfeld>
          </div>
          <div className="flex-1">
            <Formularfeld label={tr("artifact_types_panel.schluessel_vertrag")}>
              <input value={key} autoFocus onChange={(e) => setKey(e.target.value)}
                className={`${INPUT_VALUE} font-mono`} />
            </Formularfeld>
          </div>
        </div>
        <Formularfeld label={tr("artifact_types_panel.name_vertrag")}>
          <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT_VALUE} />
        </Formularfeld>
        <p className="text-[11px] text-muted">{tr("artifact_types_panel.eigenes_artefakt_hinweis")}</p>
      </div>
    </Dialog>
  );
}

// ── One artifact with states and fields ──────────────────────────────────────

function ArtifactKarte({ t: kind, onFail, onOk, onDelete }: {
  t: Kind; onFail: (e: unknown) => void; onOk: () => void; onDelete: () => void;
}) {
  const [zeigeFields, setZeigeFields] = useState(false);
  return (
    <ListenLine>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <span className="text-lg">{kind.icon}</span>
          <span className="font-medium">{kind.name}</span>
          <span className="font-mono text-xs text-muted">{kind.key}</span>
          {kind.builtin && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{tr("artifact_types_panel.eingebaut")}</span>
          )}
          {kind.fields.length > 0 && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
              {tr("artifact_types_panel.felder_anzahl", { anzahl: kind.fields.length })}
            </span>
          )}
        </div>
        <Actions>
          <button onClick={() => setZeigeFields(!zeigeFields)}
            className={BUTTON_KLEIN.neben}>
            {zeigeFields ? tr("artifact_types_panel.felder_ausblenden") : tr("artifact_types_panel.felder")}
          </button>
          {!kind.builtin && (
            <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={onDelete} />
          )}
        </Actions>
      </div>
      <div className="mb-3 text-xs text-muted">
        {kind.description} · {tr("artifact_types_panel.daten")}: {BACKING_LABEL[kind.backing] ? tr(BACKING_LABEL[kind.backing]) : kind.backing}
      </div>

      <Fields t={kind} onFail={onFail} onOk={onOk} offen={zeigeFields} />
    </ListenLine>
  );
}

// ── Felder eines Artefakts samt Werteliste ───────────────────────────────────

function Fields({ t: kind, onFail, onOk, offen: open }: {
  t: Kind; onFail: (e: unknown) => void; onOk: () => void; offen: boolean;
}) {
  const [dialog, setDialog] = useState<Field | {} | null>(null);    // {} = neues Feld
  const [loeschField, setLoeschField] = useState<Field | null>(null);

  const create = useMutation({
    mutationFn: (fresh: { key: string; label: string; kind: string; multi: boolean }) =>
      api.post(`/artifact-types/${kind.id}/fields`, fresh),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const update = useMutation({
    mutationFn: ({ id, ...remainder }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-fields/${id}`, remainder),
    onSuccess: () => { setDialog(null); onOk(); }, onError: onFail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-fields/${id}`),
    onSuccess: () => { setLoeschField(null); onOk(); }, onError: onFail,
  });

  if (!open) {
    return (
      <div className="mt-2 text-xs text-muted">
        {kind.fields.map((f) => f.label).join(" · ") || tr("artifact_types_panel.keine_felder")}
      </div>
    );
  }
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="mb-2 text-xs font-medium text-muted">{tr("artifact_types_panel.felder")}</div>

      <div className="space-y-1">
        {kind.fields.map((f) => (
          <div key={f.id}
            className={`flex items-center gap-2 rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
              <span className="font-mono text-xs text-muted" title={f.key}>{f.key}</span>
              <span className={f.enabled ? "" : "line-through"}>{f.label}</span>
              <span className="text-xs text-muted">
                {tr(FELDTYP.find(([k]) => k === f.kind)?.[1] || "")}
              </span>
              {f.multi && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.mehrfach")}</span>}
              {f.required && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.pflicht")}</span>}
              {f.builtin && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.eingebaut")}</span>}
              {f.options_source && (
                <span className="text-xs text-muted">
                  {tr("artifact_types_panel.werte_aus_projekt")} ({HERKUNFT[f.options_source] ? tr(HERKUNFT[f.options_source]) : f.options_source})
                </span>
              )}
              {!f.options_source && f.kind === "select" && f.options.length > 0 && (
                <span className="truncate text-xs text-muted">
                  {f.options.filter((o) => o.enabled).map((o) => o.label || o.value).join(" · ")}
                </span>
              )}
            </div>
            <Actions>
              <IconButton icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(f)} />
              {!f.builtin && (
                <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschField(f)} />
              )}
            </Actions>
          </div>
        ))}
        {kind.fields.length === 0 && (
          <div className="text-xs text-muted">{tr("artifact_types_panel.noch_keine_felder")}</div>
        )}
      </div>

      <button onClick={() => setDialog({})}
        className={BUTTON_KLEIN.neben}>
        {ICON.neu} {tr("artifact_types_panel.feld_anlegen")}
      </button>

      {dialog && (
        <FieldDialog feld={"id" in dialog ? (dialog as Field) : null}
          onClose={() => setDialog(null)}
          onAnlegen={(fresh) => create.mutate(fresh)}
          onAendern={(id, patch) => update.mutate({ id, ...patch })}
          onFail={onFail} onOk={onOk}
          laeuft={create.isPending || update.isPending} />
      )}
      {loeschField && (
        <LoeschDialog was={loeschField.label} laeuft={remove.isPending}
          onClose={() => setLoeschField(null)} onLoeschen={() => remove.mutate(loeschField.id)} />
      )}
    </div>
  );
}

/**
 * One field, with its value list where it has one.
 *
 * A built-in field writes into a real column, so its key and its type are locked; only
 * label, requirement and visibility belong to whoever set it up.
 */
function FieldDialog({ feld: field, laeuft: running, onClose, onAnlegen: onCreate, onAendern: onUpdate, onFail, onOk }: {
  feld: Field | null;
  laeuft: boolean;
  onClose: () => void;
  onAnlegen: (fresh: { key: string; label: string; kind: string; multi: boolean }) => void;
  onAendern: (id: number, patch: Record<string, any>) => void;
  onFail: (e: unknown) => void;
  onOk: () => void;
}) {
  const [key, setKey] = useState(field?.key || "");
  const [label, setLabel] = useState(field?.label || "");
  const [kind, setKind] = useState(field?.kind || "text");
  const [multi, setMulti] = useState(!!field?.multi);
  const [required, setRequired] = useState(!!field?.required);
  const [enabled, setEnabled] = useState(field ? field.enabled : true);

  const speichern = () => {
    if (field) onUpdate(field.id, { label, kind, multi, required, enabled });
    else onCreate({ key: key.trim(), label: label.trim(), kind, multi });
  };

  return (
    <Dialog titel={field ? tr("artifact_types_panel.feld_bearbeiten") : tr("artifact_types_panel.feld_anlegen")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} laeuft={running}
        deaktiviert={!label.trim() || (!field && !key.trim())}
        speichernText={field ? undefined : tr("common.anlegen")} onSpeichern={speichern} />}>
      <div className="space-y-3">
        <Formularfeld label={tr("artifact_types_panel.schluessel_komponente")}
          hinweis={field?.builtin ? tr("artifact_types_panel.eingebaut_hinweis", { spalte: field.source }) : undefined}>
          <input value={key} disabled={!!field} autoFocus={!field}
            onChange={(e) => setKey(e.target.value)} className={`${INPUT_VALUE} font-mono disabled:opacity-60`} />
        </Formularfeld>
        <Formularfeld label={tr("artifact_types_panel.bezeichnung_komponente")}>
          <input value={label} autoFocus={!!field} onChange={(e) => setLabel(e.target.value)} className={INPUT_VALUE} />
        </Formularfeld>
        <Formularfeld label={tr("artifact_types_panel.typ")}>
          <select value={kind} disabled={!!field?.builtin} onChange={(e) => setKind(e.target.value)}
            className={`${INPUT_VALUE} disabled:opacity-60`}>
            {FELDTYP.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
        </Formularfeld>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-sm text-ink"
            title={tr("artifact_types_panel.darf_ein_exemplar_mehrere_werte_gleichze")}>
            <input type="checkbox" checked={multi} disabled={!!field?.builtin}
              onChange={(e) => setMulti(e.target.checked)} />
            {tr("artifact_types_panel.mehrfachauswahl")}
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            {tr("artifact_types_panel.pflicht")}
          </label>
          {field && (
            <label className="flex items-center gap-2 text-sm text-ink"
              title={tr("artifact_types_panel.abgeschaltete_felder_werden_nicht_mehr_a")}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              {tr("artifact_types_panel.aktiv")}
            </label>
          )}
        </div>

        {field && field.options_source && (
          <p className="text-xs text-muted">
            {tr("artifact_types_panel.werte_aus_projekt")} ({HERKUNFT[field.options_source] ? tr(HERKUNFT[field.options_source]) : field.options_source})
            — {tr("artifact_types_panel.nichts_zu_pflegen")}
          </p>
        )}
        {field && !field.options_source && field.kind === "select" && (
          <div className="border-t border-line pt-3">
            <div className="mb-1 text-xs font-medium text-muted">{tr("artifact_types_panel.werte")}</div>
            <Werteliste feld={field} onFail={onFail} onOk={onOk} />
          </div>
        )}
      </div>
    </Dialog>
  );
}

function Werteliste({ feld: field, onFail, onOk }: {
  feld: Field; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [value, setValue] = useState("");
  // With the state field the values additionally carry a board category and "waiting"; and
  // nothing is deleted there, because the keys correspond to real database values.
  const istStatus = field.key === "status";
  const create = useMutation({
    mutationFn: () => api.post(`/artifact-fields/${field.id}/options`, { value: value.trim() }),
    onSuccess: () => { setValue(""); onOk(); }, onError: onFail,
  });
  const update = useMutation({
    mutationFn: ({ id, ...remainder }: { id: number } & Record<string, any>) =>
      api.put(`/artifact-field-options/${id}`, remainder),
    onSuccess: onOk, onError: onFail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-field-options/${id}`),
    onSuccess: onOk, onError: onFail,
  });

  return (
    <div className="space-y-1">
      {field.options.map((o) => (
        <div key={o.id} className="flex items-center gap-2 rounded border border-line px-2 py-1 text-sm">
          <span className={o.enabled ? "flex-1" : "flex-1 text-muted line-through"}>{o.label || o.value}</span>
          {istStatus && (
            <>
              <select value={o.category || "in_progress"}
                onChange={(e) => update.mutate({ id: o.id, category: e.target.value })}
                title={tr("artifact_types_panel.board_kategorie")} className={`text-xs ${inp}`}>
                {KATEGORIE.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
              </select>
              <IconButton icon={o.waiting ? "⏸" : "▷"} aktiv={o.waiting}
                titel={tr(o.waiting ? "artifact_types_panel.wartet_auf_mensch" : "artifact_types_panel.laeuft_allein")}
                onClick={() => update.mutate({ id: o.id, waiting: !o.waiting })} />
            </>
          )}
          <IconButton icon={o.enabled ? "○" : "●"}
            titel={tr(o.enabled ? "artifact_types_panel.nicht_mehr_anbieten" : "artifact_types_panel.wieder_anbieten")}
            onClick={() => update.mutate({ id: o.id, enabled: !o.enabled })} />
          {!istStatus && (
            <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
              onClick={() => remove.mutate(o.id)} />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input value={value} onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && create.mutate()}
          placeholder={tr("artifact_types_panel.wert_enter")} className={`flex-1 text-sm ${inp}`} />
        <IconButton icon={ICON.neu} titel={tr("common.anlegen")} disabled={!value.trim()}
          onClick={() => value.trim() && create.mutate()} />
      </div>
    </div>
  );
}
