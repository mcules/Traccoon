import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import {
  Aktionen, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, LoeschDialog,
} from "./ui";

type Status = { id: number; name: string; category: string; order: number };
const KATEGORIEN: [string, string][] = [
  ["todo", "Offen"], ["in_progress", "In Arbeit"], ["done", "Erledigt"],
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
  const [loeschStatus, setLoeschStatus] = useState<Status | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => { if (meta) setRows([...meta.statuses].sort((a, b) => a.order - b.order)); }, [meta]);
  const inv = () => qc.invalidateQueries({ queryKey: ["meta", project.id] });
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const speichern = async (s: Status | null, name: string, category: string) => {
    setErr("");
    try {
      if (s) await api.put(`/projects/${project.id}/statuses/${s.id}`, { name, category });
      else await api.post(`/projects/${project.id}/statuses`, { name, category });
      setDialog(null); inv();
    } catch (e) { fehler(e); }
  };
  const move = async (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= rows.length) return;
    const ids = rows.map((r) => r.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try { await api.post(`/projects/${project.id}/statuses/reorder`, { ordered_ids: ids }); inv(); }
    catch (e) { fehler(e); }
  };
  const del = async (s: Status) => {
    setErr("");
    try { await api.del(`/projects/${project.id}/statuses/${s.id}`); setLoeschStatus(null); inv(); }
    catch (e) { setLoeschStatus(null); fehler(e); }
  };

  const katLabel = (k: string) => KATEGORIEN.find(([key]) => key === k)?.[1] || k;

  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-1 text-sm font-medium">{tr("status_manager.board_spalten_status")}</div>
      <p className="mb-3 text-xs text-muted">{tr("status_manager.einleitung")}</p>
      <Fehlerzeile text={err} />
      <div className="space-y-1.5">
        {rows.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2 rounded border border-line px-2 py-1.5 text-sm">
            <div className="flex flex-col">
              <button onClick={() => move(i, -1)} disabled={i === 0}
                title={tr("status_manager.nach_oben")} aria-label={tr("status_manager.nach_oben")}
                className="leading-none text-muted hover:text-ink disabled:opacity-30">▲</button>
              <button onClick={() => move(i, 1)} disabled={i === rows.length - 1}
                title={tr("status_manager.nach_unten")} aria-label={tr("status_manager.nach_unten")}
                className="leading-none text-muted hover:text-ink disabled:opacity-30">▼</button>
            </div>
            <span className="flex-1">{s.name}</span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{katLabel(s.category)}</span>
            <Aktionen>
              <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(s)} />
              <IconKnopf icon={ICON.loeschen} titel={tr("status_manager.status_loeschen")} gefahr
                onClick={() => setLoeschStatus(s)} />
            </Aktionen>
          </div>
        ))}
      </div>
      <button onClick={() => setDialog({})}
        className="mt-3 rounded bg-brand px-3 py-1.5 text-sm text-white">
        {ICON.neu} {tr("status_manager.neue_spalte")}
      </button>
      <p className="mt-2 text-xs text-muted">{tr("status_manager.kategorie_hinweis")}</p>

      {dialog && (
        <StatusDialog status={"id" in dialog ? (dialog as Status) : null}
          onClose={() => setDialog(null)}
          onSpeichern={(name, kat) => speichern("id" in dialog ? (dialog as Status) : null, name, kat)} />
      )}
      {loeschStatus && (
        <LoeschDialog was={loeschStatus.name} hinweis={tr("status_manager.loeschen_hinweis")}
          onClose={() => setLoeschStatus(null)} onLoeschen={() => del(loeschStatus)} />
      )}
    </div>
  );
}

function StatusDialog({ status, onClose, onSpeichern }: {
  status: Status | null; onClose: () => void; onSpeichern: (name: string, kategorie: string) => void;
}) {
  const [name, setName] = useState(status?.name || "");
  const [kategorie, setKategorie] = useState(status?.category || "todo");

  return (
    <Dialog titel={tr(status ? "status_manager.spalte_bearbeiten" : "status_manager.neue_spalte")} onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} deaktiviert={!name.trim()}
        speichernText={status ? undefined : tr("common.anlegen")}
        onSpeichern={() => onSpeichern(name.trim(), kategorie)} />}>
      <div className="space-y-3">
        <Feld label={tr("status_manager.name")}>
          <input value={name} autoFocus onChange={(e) => setName(e.target.value)} className={EINGABE} />
        </Feld>
        <Feld label={tr("status_manager.kategorie")} hinweis={tr("status_manager.kategorie_hinweis")}>
          <select value={kategorie} onChange={(e) => setKategorie(e.target.value)} className={EINGABE}>
            {KATEGORIEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Feld>
      </div>
    </Dialog>
  );
}
