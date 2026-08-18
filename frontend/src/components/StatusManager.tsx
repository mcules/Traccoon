import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";

type Status = { id: number; name: string; category: string; order: number };
const KATEGORIEN: [string, string][] = [
  ["todo", "Offen"], ["in_progress", "In Arbeit"], ["done", "Erledigt"],
];

/** Verwaltung der Board-Spalten (Status) eines Projekts. */
export default function StatusManager({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { data: meta } = useQuery({
    queryKey: ["meta", project.id], queryFn: () => api.get<any>(`/projects/${project.id}/meta`),
  });
  const [rows, setRows] = useState<Status[]>([]);
  const [neu, setNeu] = useState("");
  const [neuKat, setNeuKat] = useState("todo");
  const [err, setErr] = useState("");

  useEffect(() => { if (meta) setRows([...meta.statuses].sort((a, b) => a.order - b.order)); }, [meta]);
  const inv = () => qc.invalidateQueries({ queryKey: ["meta", project.id] });
  const fehler = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const rename = async (s: Status, name: string) => {
    if (name === s.name || !name.trim()) return;
    try { await api.put(`/projects/${project.id}/statuses/${s.id}`, { name, category: s.category }); inv(); }
    catch (e) { fehler(e); }
  };
  const setKat = async (s: Status, category: string) => {
    try { await api.put(`/projects/${project.id}/statuses/${s.id}`, { name: s.name, category }); inv(); }
    catch (e) { fehler(e); }
  };
  const move = async (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= rows.length) return;
    const ids = rows.map((r) => r.id);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try { await api.post(`/projects/${project.id}/statuses/reorder`, { ordered_ids: ids }); inv(); }
    catch (e) { fehler(e); }
  };
  const add = async () => {
    if (!neu.trim()) return;
    try { await api.post(`/projects/${project.id}/statuses`, { name: neu, category: neuKat }); setNeu(""); inv(); }
    catch (e) { fehler(e); }
  };
  const del = async (s: Status) => {
    setErr("");
    try { await api.del(`/projects/${project.id}/statuses/${s.id}`); inv(); }
    catch (e) { fehler(e); }
  };

  return (
    <div className="rounded-lg border border-line bg-card p-4">
      <div className="mb-1 text-sm font-medium">{tr("status_manager.board_spalten_status")}</div>
      <p className="mb-3 text-xs text-muted">
        Diese Spalten bilden das Kanban-Board — von links (offen) nach rechts (erledigt).
        Neue Projekte starten mit To&nbsp;Do / In&nbsp;Arbeit / Fertig.
      </p>
      <div className="space-y-1.5">
        {rows.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2 text-sm">
            <div className="flex flex-col">
              <button onClick={() => move(i, -1)} disabled={i === 0}
                className="leading-none text-muted hover:text-ink disabled:opacity-30">▲</button>
              <button onClick={() => move(i, 1)} disabled={i === rows.length - 1}
                className="leading-none text-muted hover:text-ink disabled:opacity-30">▼</button>
            </div>
            <input defaultValue={s.name} key={s.name}
              onBlur={(e) => rename(s, e.target.value)}
              className="flex-1 rounded border border-line bg-surface px-2 py-1" />
            <select value={s.category} onChange={(e) => setKat(s, e.target.value)}
              className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
              {KATEGORIEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button onClick={() => del(s)} className="text-muted hover:text-red-400" title={tr("status_manager.status_loeschen")}>✕</button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <input value={neu} onChange={(e) => setNeu(e.target.value)} placeholder={tr("status_manager.neue_spalte")}
          onKeyDown={(e) => e.key === "Enter" && add()}
          className="flex-1 rounded border border-line bg-surface px-2 py-1 text-sm" />
        <select value={neuKat} onChange={(e) => setNeuKat(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
          {KATEGORIEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button onClick={add} className="rounded bg-brand px-3 py-1 text-sm text-white">+ Spalte</button>
      </div>
      <p className="mt-2 text-xs text-muted">
        Kategorie steuert die Auswertung (Durchsatz zählt „Erledigt“). Ein Status mit Tickets lässt sich
        erst löschen, wenn er leer ist.
      </p>
      {err && <div className="mt-2 text-sm text-red-400">{err}</div>}
    </div>
  );
}
