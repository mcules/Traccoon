import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Actions, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, DeleteDialog, BUTTON, BUTTON_TEXT} from "./ui";

type Status = { id: number; name: string; category: string; order: number };
const CATEGORIES: [string, string][] = [
  ["todo", tr("common.open_state")], ["in_progress", tr("common.in_progress")], ["done", tr("common.done_state")],
];

/**
 * Management of the board columns (statuses) of a project.
 *
 * The order stays in the row (two arrows, one click), because moving a column is a matter of
 * position and not of a form. Name and category live in the dialog: renaming on blur meant
 * one never knew whether it had been saved.
 */
export default function StatusManager({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { data: meta } = useQuery({
    queryKey: ["meta", project.id], queryFn: () => api.get<any>(`/projects/${project.id}/meta`),
  });
  const [rows, setRows] = useState<Status[]>([]);
  const [dialog, setDialog] = useState<Status | {} | null>(null);   // {} = neue Spalte
  const [deleteStatus, setDeleteStatus] = useState<Status | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => { if (meta) setRows([...meta.statuses].sort((a, b) => a.order - b.order)); }, [meta]);
  const inv = () => qc.invalidateQueries({ queryKey: ["meta", project.id] });
  const error = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const save = async (s: Status | null, name: string, category: string) => {
    setErr("");
    try {
      if (s) await api.put(`/projects/${project.id}/statuses/${s.id}`, { name, category });
      else await api.post(`/projects/${project.id}/statuses`, { name, category });
      setDialog(null); inv();
    } catch (e) { error(e); }
  };
  const move = async (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= rows.length) return;
    const ids = rows.map((r) => r.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try { await api.post(`/projects/${project.id}/statuses/reorder`, { ordered_ids: ids }); inv(); }
    catch (e) { error(e); }
  };
  const del = async (s: Status) => {
    setErr("");
    try { await api.del(`/projects/${project.id}/statuses/${s.id}`); setDeleteStatus(null); inv(); }
    catch (e) { setDeleteStatus(null); error(e); }
  };

  const catLabel = (k: string) => CATEGORIES.find(([key]) => key === k)?.[1] || k;

  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-1 text-sm font-medium">{tr("status_manager.board_columns_states")}</div>
      <p className="mb-3 text-xs text-muted">{tr("status_manager.these_columns_make_up")}</p>
      <Errorrow text={err} />
      <div className="space-y-1.5">
        {rows.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2 rounded border border-line px-2 py-1.5 text-sm">
            <div className="flex flex-col">
              <button onClick={() => move(i, -1)} disabled={i === 0}
                title={tr("status_manager.move_up")} aria-label={tr("status_manager.move_up")}
                className={BUTTON_TEXT.secondary}>▲</button>
              <button onClick={() => move(i, 1)} disabled={i === rows.length - 1}
                title={tr("status_manager.move_down")} aria-label={tr("status_manager.move_down")}
                className={BUTTON_TEXT.secondary}>▼</button>
            </div>
            <span className="flex-1">{s.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{catLabel(s.category)}</span>
            <Actions>
              <IconButton icon={ICON.edit} title={tr("common.edit")} onClick={() => setDialog(s)} />
              <IconButton icon={ICON.remove} title={tr("status_manager.delete_state")} danger
                onClick={() => setDeleteStatus(s)} />
            </Actions>
          </div>
        ))}
      </div>
      <button onClick={() => setDialog({})}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("status_manager.new_column")}
      </button>
      <p className="mt-2 text-xs text-muted">{tr("status_manager.category_drives_statistics_throughput")}</p>

      {dialog && (
        <StatusDialog status={"id" in dialog ? (dialog as Status) : null}
          onClose={() => setDialog(null)}
          onSave={(name, cat) => save("id" in dialog ? (dialog as Status) : null, name, cat)} />
      )}
      {deleteStatus && (
        <DeleteDialog was={deleteStatus.name} hint={tr("status_manager.status_holding_tickets_can")}
          onClose={() => setDeleteStatus(null)} onDelete={() => del(deleteStatus)} />
      )}
    </div>
  );
}

function StatusDialog({ status, onClose, onSave }: {
  status: Status | null; onClose: () => void; onSave: (name: string, category: string) => void;
}) {
  const [name, setName] = useState(status?.name || "");
  const [category, setCategory] = useState(status?.category || "todo");

  return (
    <Dialog title={tr(status ? "status_manager.edit_column" : "status_manager.new_column")} onClose={onClose}
      foot={<DialogFoot onCancel={onClose} disabled={!name.trim()}
        saveText={status ? undefined : tr("common.create")}
        onSave={() => onSave(name.trim(), category)} />}>
      <div className="space-y-3">
        <Field label={tr("status_manager.name")}>
          <input value={name} autoFocus onChange={(e) => setName(e.target.value)} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("status_manager.category")} hint={tr("status_manager.category_drives_statistics_throughput")}>
          <select value={category} onChange={(e) => setCategory(e.target.value)} className={INPUT_VALUE}>
            {CATEGORIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
      </div>
    </Dialog>
  );
}
