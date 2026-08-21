import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import {
  Actions, Dialog, DialogFoot, INPUT_VALUE, Field as Formfield, Errorrow, ICON, IconButton,
  DeleteDialog, Area, Listing, ListRow, BUTTON, BUTTON_SMALL } from "./ui";

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
const CATEGORY: [string, string][] = [
  ["todo", "artifact_types_panel.open"],
  ["in_progress", "artifact_types_panel.progress"],
  ["done", "artifact_types_panel.done"],
];

const FIELDTYPE: [string, string][] = [
  ["text", "artifact_types_panel.text"],
  ["number", "artifact_types_panel.number"],
  ["date", "artifact_types_panel.date"],
  ["boolean", "artifact_types_panel.yes_no"],
  ["select", "artifact_types_panel.choice"],
];

const ORIGIN: Record<string, string> = {
  issue_type: "artifact_types_panel.issue_types_project",
  board_status: "artifact_types_panel.board_columns_project",
  sprint: "artifact_types_panel.sprints_board",
  member: "artifact_types_panel.members_project",
  location: "artifact_types_panel.locations",
};

const BACKING_LABEL: Record<string, string> = {
  issue: "artifact_types_panel.tickets_board_sprints_ai",
  hardware_asset: "artifact_types_panel.hardware_inventory_procurement_chain",
  generic: "artifact_types_panel.own_storage",
};

const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

/**
 * Artifact register: what Traccoon manages, which states it knows and which fields it
 * carries.
 *
 * Three levels following the ALMEX example: an **artifact type** orders things (process,
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
  const [deleteKind, setDeleteKind] = useState<Kind | null>(null);

  const { data: types } = useQuery({
    queryKey: ["artifact-types"], queryFn: () => api.get<Kind[]>("/artifact-types"),
  });

  const inv = () => { qc.invalidateQueries({ queryKey: ["artifact-types"] }); };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const ok = () => { setErr(""); inv(); };

  const create = useMutation({
    mutationFn: (fresh: { key: string; name: string; icon: string }) => api.post("/artifact-types", fresh),
    onSuccess: () => { setNewDialog(false); ok(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/artifact-types/${id}`),
    onSuccess: () => { setDeleteKind(null); ok(); }, onError: fail,
  });

  // One sentence, one key: the highlights fall away, because markup in the middle of a sentence
  // cannot be translated along (the word order is a different one elsewhere).
  return (
    <Area hint={tr("artifact_types_panel.artifact_starts_undefined_meaning")}>
      <Errorrow text={err} />

      <Listing>
        {types?.map((kind) => (
          <ArtifactKarte key={kind.id} t={kind} onFail={fail} onOk={ok}
                         onDelete={() => setDeleteKind(kind)} />
        ))}
      </Listing>

      <button onClick={() => { setErr(""); setNewDialog(true); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("artifact_types_panel.create_own_artifact")}
      </button>

      {newDialog && (
        <ArtifactDialog error={err} runs={create.isPending}
          onClose={() => { setNewDialog(false); setErr(""); }}
          onSave={(values) => create.mutate(values)} />
      )}
      {deleteKind && (
        <DeleteDialog was={deleteKind.name} runs={remove.isPending}
          hint={tr("artifact_types_panel.units_artifact_disappear")}
          onClose={() => setDeleteKind(null)} onDelete={() => remove.mutate(deleteKind.id)} />
      )}
    </Area>
  );
}

function ArtifactDialog({ error: error, runs: running, onClose, onSave }: {
  error: string; runs: boolean; onClose: () => void;
  onSave: (values: { key: string; name: string; icon: string }) => void;
}) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("📦");

  return (
    <Dialog title={tr("artifact_types_panel.create_own_artifact")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!key.trim() || !name.trim()} runs={running}
        saveText={tr("common.create")}
        onSave={() => onSave({ key: key.trim(), name: name.trim(), icon })} />}>
      <Errorrow text={error} />
      <div className="space-y-3">
        <div className="flex gap-3">
          <div className="w-20">
            <Formfield label={tr("artifact_types_panel.icon")}>
              <input value={icon} onChange={(e) => setIcon(e.target.value)}
                className={`${INPUT_VALUE} text-center`} />
            </Formfield>
          </div>
          <div className="flex-1">
            <Formfield label={tr("artifact_types_panel.key_contract")}>
              <input value={key} autoFocus onChange={(e) => setKey(e.target.value)}
                className={`${INPUT_VALUE} font-mono`} />
            </Formfield>
          </div>
        </div>
        <Formfield label={tr("artifact_types_panel.name_contract")}>
          <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT_VALUE} />
        </Formfield>
        <p className="text-[11px] text-muted">{tr("artifact_types_panel.gets_store_own_any")}</p>
      </div>
    </Dialog>
  );
}

// ── One artifact with states and fields ──────────────────────────────────────

function ArtifactKarte({ t: kind, onFail, onOk, onDelete }: {
  t: Kind; onFail: (e: unknown) => void; onOk: () => void; onDelete: () => void;
}) {
  const [showFields, setShowFields] = useState(false);
  return (
    <ListRow>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <span className="text-lg">{kind.icon}</span>
          <span className="font-medium">{kind.name}</span>
          <span className="font-mono text-xs text-muted">{kind.key}</span>
          {kind.builtin && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{tr("artifact_types_panel.built")}</span>
          )}
          {kind.fields.length > 0 && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
              {tr("artifact_types_panel.count_fields", { count: kind.fields.length })}
            </span>
          )}
        </div>
        <Actions>
          <button onClick={() => setShowFields(!showFields)}
            className={BUTTON_SMALL.secondary}>
            {showFields ? tr("artifact_types_panel.hide_fields") : tr("artifact_types_panel.fields")}
          </button>
          {!kind.builtin && (
            <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={onDelete} />
          )}
        </Actions>
      </div>
      <div className="mb-3 text-xs text-muted">
        {kind.description} · {tr("artifact_types_panel.data")}: {BACKING_LABEL[kind.backing] ? tr(BACKING_LABEL[kind.backing]) : kind.backing}
      </div>

      <Fields t={kind} onFail={onFail} onOk={onOk} open={showFields} />
    </ListRow>
  );
}

// ── Felder eines Artefakts samt Werteliste ───────────────────────────────────

function Fields({ t: kind, onFail, onOk, open: open }: {
  t: Kind; onFail: (e: unknown) => void; onOk: () => void; open: boolean;
}) {
  const [dialog, setDialog] = useState<Field | {} | null>(null);    // {} = neues Feld
  const [deleteField, setDeleteField] = useState<Field | null>(null);

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
    onSuccess: () => { setDeleteField(null); onOk(); }, onError: onFail,
  });

  if (!open) {
    return (
      <div className="mt-2 text-xs text-muted">
        {kind.fields.map((f) => f.label).join(" · ") || tr("artifact_types_panel.no_fields_yet")}
      </div>
    );
  }
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="mb-2 text-xs font-medium text-muted">{tr("artifact_types_panel.fields")}</div>

      <div className="space-y-1">
        {kind.fields.map((f) => (
          <div key={f.id}
            className={`flex items-center gap-2 rounded border border-line px-2 py-1.5 ${f.enabled ? "bg-surface" : "bg-surface/40"}`}>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
              <span className="font-mono text-xs text-muted" title={f.key}>{f.key}</span>
              <span className={f.enabled ? "" : "line-through"}>{f.label}</span>
              <span className="text-xs text-muted">
                {tr(FIELDTYPE.find(([k]) => k === f.kind)?.[1] || "")}
              </span>
              {f.multi && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.multi")}</span>}
              {f.required && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.required")}</span>}
              {f.builtin && <span className="rounded bg-surface px-1 text-xs text-muted">{tr("artifact_types_panel.built")}</span>}
              {f.options_source && (
                <span className="text-xs text-muted">
                  {tr("artifact_types_panel.values_come_project")} ({ORIGIN[f.options_source] ? tr(ORIGIN[f.options_source]) : f.options_source})
                </span>
              )}
              {!f.options_source && f.kind === "select" && f.options.length > 0 && (
                <span className="truncate text-xs text-muted">
                  {f.options.filter((o) => o.enabled).map((o) => o.label || o.value).join(" · ")}
                </span>
              )}
            </div>
            <Actions>
              <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => setDialog(f)} />
              {!f.builtin && (
                <IconButton icon={ICON.remove} title={tr("common.delete")} danger onClick={() => setDeleteField(f)} />
              )}
            </Actions>
          </div>
        ))}
        {kind.fields.length === 0 && (
          <div className="text-xs text-muted">{tr("artifact_types_panel.no_fields_yet_2")}</div>
        )}
      </div>

      <button onClick={() => setDialog({})}
        className={BUTTON_SMALL.secondary}>
        {ICON.fresh} {tr("artifact_types_panel.new_field")}
      </button>

      {dialog && (
        <FieldDialog field={"id" in dialog ? (dialog as Field) : null}
          onClose={() => setDialog(null)}
          onCreate={(fresh) => create.mutate(fresh)}
          onChange={(id, patch) => update.mutate({ id, ...patch })}
          onFail={onFail} onOk={onOk}
          runs={create.isPending || update.isPending} />
      )}
      {deleteField && (
        <DeleteDialog was={deleteField.label} runs={remove.isPending}
          onClose={() => setDeleteField(null)} onDelete={() => remove.mutate(deleteField.id)} />
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
function FieldDialog({ field: field, runs: running, onClose, onCreate: onCreate, onChange: onUpdate, onFail, onOk }: {
  field: Field | null;
  runs: boolean;
  onClose: () => void;
  onCreate: (fresh: { key: string; label: string; kind: string; multi: boolean }) => void;
  onChange: (id: number, patch: Record<string, any>) => void;
  onFail: (e: unknown) => void;
  onOk: () => void;
}) {
  const [key, setKey] = useState(field?.key || "");
  const [label, setLabel] = useState(field?.label || "");
  const [kind, setKind] = useState(field?.kind || "text");
  const [multi, setMulti] = useState(!!field?.multi);
  const [required, setRequired] = useState(!!field?.required);
  const [enabled, setEnabled] = useState(field ? field.enabled : true);

  const save = () => {
    if (field) onUpdate(field.id, { label, kind, multi, required, enabled });
    else onCreate({ key: key.trim(), label: label.trim(), kind, multi });
  };

  return (
    <Dialog title={field ? tr("artifact_types_panel.edit_field") : tr("artifact_types_panel.new_field")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} runs={running}
        disabled={!label.trim() || (!field && !key.trim())}
        saveText={field ? undefined : tr("common.create")} onSave={save} />}>
      <div className="space-y-3">
        <Formfield label={tr("artifact_types_panel.key_component")}
          hint={field?.builtin ? tr("artifact_types_panel.built_writes_column_column", { column: field.source }) : undefined}>
          <input value={key} disabled={!!field} autoFocus={!field}
            onChange={(e) => setKey(e.target.value)} className={`${INPUT_VALUE} font-mono disabled:opacity-60`} />
        </Formfield>
        <Formfield label={tr("artifact_types_panel.label_component")}>
          <input value={label} autoFocus={!!field} onChange={(e) => setLabel(e.target.value)} className={INPUT_VALUE} />
        </Formfield>
        <Formfield label={tr("artifact_types_panel.type")}>
          <select value={kind} disabled={!!field?.builtin} onChange={(e) => setKind(e.target.value)}
            className={`${INPUT_VALUE} disabled:opacity-60`}>
            {FIELDTYPE.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
        </Formfield>
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-sm text-ink"
            title={tr("artifact_types_panel.may_one_item_carry_several_values_at_once")}>
            <input type="checkbox" checked={multi} disabled={!!field?.builtin}
              onChange={(e) => setMulti(e.target.checked)} />
            {tr("artifact_types_panel.multiple_values")}
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            {tr("artifact_types_panel.required")}
          </label>
          {field && (
            <label className="flex items-center gap-2 text-sm text-ink"
              title={tr("artifact_types_panel.disabled_fields_are_no_longer_offered")}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              {tr("artifact_types_panel.active")}
            </label>
          )}
        </div>

        {field && field.options_source && (
          <p className="text-xs text-muted">
            {tr("artifact_types_panel.values_come_project")} ({ORIGIN[field.options_source] ? tr(ORIGIN[field.options_source]) : field.options_source})
            — {tr("artifact_types_panel.nothing_maintain_here")}
          </p>
        )}
        {field && !field.options_source && field.kind === "select" && (
          <div className="border-t border-line pt-3">
            <div className="mb-1 text-xs font-medium text-muted">{tr("artifact_types_panel.values")}</div>
            <Valuelist field={field} onFail={onFail} onOk={onOk} />
          </div>
        )}
      </div>
    </Dialog>
  );
}

function Valuelist({ field: field, onFail, onOk }: {
  field: Field; onFail: (e: unknown) => void; onOk: () => void;
}) {
  const [value, setValue] = useState("");
  // With the state field the values additionally carry a board category and "waiting"; and
  // nothing is deleted there, because the keys correspond to real database values.
  const isStatus = field.key === "status";
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
          {isStatus && (
            <>
              <select value={o.category || "in_progress"}
                onChange={(e) => update.mutate({ id: o.id, category: e.target.value })}
                title={tr("artifact_types_panel.board_category")} className={`text-xs ${inp}`}>
                {CATEGORY.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
              </select>
              <IconButton icon={o.waiting ? "⏸" : "▷"} active={o.waiting}
                title={tr(o.waiting ? "artifact_types_panel.waits_person" : "artifact_types_panel.runs_own")}
                onClick={() => update.mutate({ id: o.id, waiting: !o.waiting })} />
            </>
          )}
          <IconButton icon={o.enabled ? "○" : "●"}
            title={tr(o.enabled ? "artifact_types_panel.stop_offering_stays_existing" : "artifact_types_panel.offer")}
            onClick={() => update.mutate({ id: o.id, enabled: !o.enabled })} />
          {!isStatus && (
            <IconButton icon={ICON.remove} title={tr("common.delete")} danger
              onClick={() => remove.mutate(o.id)} />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input value={value} onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && create.mutate()}
          placeholder={tr("artifact_types_panel.value_enter")} className={`flex-1 text-sm ${inp}`} />
        <IconButton icon={ICON.fresh} title={tr("common.create")} disabled={!value.trim()}
          onClick={() => value.trim() && create.mutate()} />
      </div>
    </div>
  );
}
