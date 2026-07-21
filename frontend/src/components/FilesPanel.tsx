import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Editor from "@monaco-editor/react";
import { api, ApiError, Project } from "../api";
import { langOf } from "../monaco";

type RepoStatus = { branch: string; dirty: string[]; ahead: number; behind: number; has_remote: boolean };

export default function FilesPanel({ project }: { project: Project }) {
  const pid = project.id;
  const qc = useQueryClient();
  const [sel, setSel] = useState<string>("");
  const [value, setValue] = useState<string>("");
  const [orig, setOrig] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [commitOpen, setCommitOpen] = useState(false);

  const flash = (t: string) => { setErr(""); setMsg(t); setTimeout(() => setMsg(""), 2500); };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const status = useQuery({
    queryKey: ["repo-status", pid],
    queryFn: () => api.get<RepoStatus>(`/projects/${pid}/repo/status`),
    refetchInterval: 15000, retry: false,
  });
  const tree = useQuery({
    queryKey: ["repo-tree", pid],
    queryFn: () => api.get<{ files: string[] }>(`/projects/${pid}/repo/tree`),
    retry: false,
  });
  const file = useQuery({
    queryKey: ["repo-file", pid, sel],
    queryFn: () => api.get<{ path: string; content: string }>(`/projects/${pid}/repo/file?path=${encodeURIComponent(sel)}`),
    enabled: !!sel,
  });
  useEffect(() => {
    if (file.data && file.data.path === sel) { setValue(file.data.content); setOrig(file.data.content); }
  }, [file.data, sel]);

  const dirty = value !== orig;
  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["repo-status", pid] });
    qc.invalidateQueries({ queryKey: ["repo-tree", pid] });
  };

  const save = useMutation({
    mutationFn: () => api.put(`/projects/${pid}/repo/file`, { path: sel, content: value }),
    onSuccess: () => { setOrig(value); flash("Gespeichert."); qc.invalidateQueries({ queryKey: ["repo-status", pid] }); },
    onError: fail,
  });
  const pull = useMutation({
    mutationFn: () => api.post<any>(`/projects/${pid}/repo/pull`),
    onSuccess: () => { flash("Gepullt."); invalidateAll(); if (sel) qc.invalidateQueries({ queryKey: ["repo-file", pid, sel] }); },
    onError: fail,
  });
  const push = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/repo/push`),
    onSuccess: () => { flash("Gepusht."); qc.invalidateQueries({ queryKey: ["repo-status", pid] }); },
    onError: fail,
  });

  if (status.isError) {
    const m = status.error instanceof ApiError ? status.error.message : "Repo nicht verfügbar";
    return <div className="rounded border border-line bg-card p-4 text-sm text-muted">{m}</div>;
  }
  const st = status.data;
  const files = (tree.data?.files || []).filter((f) => !filter || f.toLowerCase().includes(filter.toLowerCase()));

  return (
    <div>
      {/* Branch-Leiste */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-card px-3 py-2 text-sm">
        <span className="font-mono">🌿 {st?.branch || "…"}</span>
        {st && st.behind > 0 && <span className="rounded bg-yellow-500/20 px-1.5 text-xs text-yellow-300">↓ {st.behind}</span>}
        {st && st.ahead > 0 && <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">↑ {st.ahead}</span>}
        {st && st.dirty.length > 0 && (
          <span className="rounded bg-orange-500/20 px-1.5 text-xs text-orange-300">● {st.dirty.length} geändert</span>
        )}
        <div className="flex-1" />
        {st?.has_remote && (
          <>
            <button onClick={() => pull.mutate()} disabled={pull.isPending}
              className="rounded border border-line px-2 py-1 text-xs hover:text-brand disabled:opacity-50">
              {pull.isPending ? "…" : "↓ Pull"}</button>
            <button onClick={() => push.mutate()} disabled={push.isPending}
              className="rounded border border-line px-2 py-1 text-xs hover:text-brand disabled:opacity-50">
              {push.isPending ? "…" : "↑ Push"}</button>
          </>
        )}
        <button onClick={() => { setErr(""); setCommitOpen(true); }} disabled={!st || st.dirty.length === 0}
          className="rounded bg-brand px-3 py-1 text-xs text-white disabled:opacity-40">Commit</button>
      </div>
      {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
      {msg && <div className="mb-2 text-sm text-green-400">{msg}</div>}

      <div className="flex gap-3" style={{ height: "72vh" }}>
        {/* Datei-Liste */}
        <div className="flex w-64 shrink-0 flex-col rounded-lg border border-line bg-card">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Dateien filtern…"
            className="m-2 rounded border border-line bg-surface px-2 py-1 text-xs" />
          <div className="flex-1 overflow-y-auto px-1 pb-2">
            {files.map((f) => {
              const isDirty = st?.dirty.includes(f);
              const slash = f.lastIndexOf("/");
              return (
                <button key={f} onClick={() => setSel(f)}
                  className={`block w-full truncate rounded px-2 py-1 text-left text-xs hover:bg-surface ${
                    sel === f ? "bg-surface text-ink" : "text-muted"}`}
                  title={f}>
                  {slash >= 0 && <span className="opacity-50">{f.slice(0, slash + 1)}</span>}
                  <span className={sel === f ? "text-ink" : ""}>{f.slice(slash + 1)}</span>
                  {isDirty && <span className="text-orange-400"> ●</span>}
                </button>
              );
            })}
            {files.length === 0 && <div className="px-2 py-2 text-xs text-muted">Keine Dateien.</div>}
          </div>
        </div>

        {/* Editor */}
        <div className="flex flex-1 flex-col rounded-lg border border-line bg-card">
          {sel ? (
            <>
              <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs">
                <span className="font-mono text-muted">{sel}</span>
                {dirty && <span className="text-orange-400">● ungespeichert</span>}
                <div className="flex-1" />
                <button onClick={() => save.mutate()} disabled={!dirty || save.isPending}
                  className="rounded bg-brand px-3 py-1 text-white disabled:opacity-40">
                  {save.isPending ? "…" : "Speichern (⌘S)"}</button>
              </div>
              <div className="flex-1">
                {file.isLoading ? (
                  <div className="p-3 text-sm text-muted">Lädt…</div>
                ) : file.isError ? (
                  <div className="p-3 text-sm text-red-400">
                    {file.error instanceof ApiError ? file.error.message : "Datei nicht lesbar"}
                  </div>
                ) : (
                  <Editor height="100%" theme="vs-dark" path={sel} language={langOf(sel)}
                    value={value} onChange={(v) => setValue(v ?? "")}
                    onMount={(editor, monaco) => {
                      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
                        if (value !== orig) save.mutate();
                      });
                    }}
                    options={{ fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false,
                      automaticLayout: true, tabSize: 2 }} />
                )}
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              Datei links auswählen.
            </div>
          )}
        </div>
      </div>

      {commitOpen && <CommitModal pid={pid} onClose={() => setCommitOpen(false)}
        onDone={() => { setCommitOpen(false); invalidateAll(); if (sel) { setOrig(value); } flash("Committet."); }} />}
    </div>
  );
}

function CommitModal({ pid, onClose, onDone }: { pid: number; onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState("");
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const gen = useMutation({
    mutationFn: () => api.post<{ title: string; description: string }>(`/projects/${pid}/repo/commit-message`),
    onSuccess: (r) => { setErr(""); setTitle(r.title || ""); setDesc(r.description || ""); },
    onError: fail,
  });
  useEffect(() => { gen.mutate(); }, []); // beim Öffnen automatisch generieren

  const commit = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/repo/commit`, { title, description: desc }),
    onSuccess: onDone, onError: fail,
  });

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-xl rounded-xl border border-line bg-card p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Commit</h2>
          <button onClick={onClose} className="text-muted hover:text-ink">✕</button>
        </div>
        <div className="mb-2 flex items-center gap-2">
          <label className="text-xs text-muted">Titel</label>
          <button onClick={() => gen.mutate()} disabled={gen.isPending}
            className="text-xs text-muted hover:text-brand disabled:opacity-50">
            {gen.isPending ? "generiert…" : "↻ neu generieren"}</button>
        </div>
        <input value={title} onChange={(e) => setTitle(e.target.value)}
          className="mb-3 w-full rounded border border-line bg-surface px-3 py-2 text-sm" />
        <label className="text-xs text-muted">Beschreibung</label>
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={6}
          className="mb-3 mt-1 w-full rounded border border-line bg-surface px-3 py-2 text-sm" />
        {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink">Abbrechen</button>
          <button onClick={() => title.trim() && commit.mutate()} disabled={!title.trim() || commit.isPending}
            className="rounded bg-brand px-4 py-1.5 text-sm text-white disabled:opacity-40">
            {commit.isPending ? "Committet…" : "Committen"}</button>
        </div>
      </div>
    </div>
  );
}
