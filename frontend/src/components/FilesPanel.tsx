import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Editor from "@monaco-editor/react";
import { api, ApiError, Project } from "../api";
import { longOf } from "../monaco";
import Markdown from "./Markdown";
import { BUTTON, BUTTON_SMALL, BUTTON_TEXT} from "./ui";

type RepoStatus = { branch: string; dirty: string[]; ahead: number; behind: number; has_remote: boolean };
type Node = { name: string; path: string; dir: boolean; children: Node[] };

const IMAGE_EXT = ["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "avif"];
const ext = (p: string) => p.split(".").pop()?.toLowerCase() || "";
const isImage = (p: string) => IMAGE_EXT.includes(ext(p));
const isMarkdown = (p: string) => ext(p) === "md" || ext(p) === "markdown";

function buildTree(files: string[]): Node[] {
  const root: Node = { name: "", path: "", dir: true, children: [] };
  for (const f of files) {
    const parts = f.split("/");
    let cur = root;
    parts.forEach((part, i) => {
      const leaf = i === parts.length - 1;
      const path = parts.slice(0, i + 1).join("/");
      let child = cur.children.find((c) => c.name === part && c.dir === !leaf);
      if (!child) { child = { name: part, path, dir: !leaf, children: [] }; cur.children.push(child); }
      cur = child;
    });
  }
  const sortRec = (n: Node) => {
    n.children.sort((a, b) => (a.dir !== b.dir ? (a.dir ? -1 : 1) : a.name.localeCompare(b.name)));
    n.children.forEach(sortRec);
  };
  sortRec(root);
  return root.children;
}

function Tree({ nodes, sel, onSelect, expanded, toggle, dirty }: {
  nodes: Node[]; sel: string; onSelect: (p: string) => void;
  expanded: Set<string>; toggle: (p: string) => void; dirty: string[];
}) {
  return (
    <>
      {nodes.map((n) => n.dir ? (
        <div key={n.path}>
          <button onClick={() => toggle(n.path)}
            className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs text-muted hover:bg-surface">
            <span className="opacity-70">{expanded.has(n.path) ? "▾" : "▸"}</span>
            <span>📁 {n.name}</span>
          </button>
          {expanded.has(n.path) && (
            <div className="ml-3 border-l border-line pl-1">
              <Tree nodes={n.children} sel={sel} onSelect={onSelect} expanded={expanded} toggle={toggle} dirty={dirty} />
            </div>
          )}
        </div>
      ) : (
        <button key={n.path} onClick={() => onSelect(n.path)} title={n.path}
          className={`block w-full truncate rounded px-1.5 py-0.5 text-left text-xs hover:bg-surface ${
            sel === n.path ? "bg-surface text-ink" : "text-muted"}`}>
          {n.name}{dirty.includes(n.path) && <span className="text-orange-400"> ●</span>}
        </button>
      ))}
    </>
  );
}

export default function FilesPanel({ project }: { project: Project }) {
  const pid = project.id;
  const qc = useQueryClient();
  const [sel, setSel] = useState<string>("");
  const [value, setValue] = useState<string>("");
  const [orig, setOrig] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState(false);   // Markdown-Vorschau
  const [imgUrl, setImgUrl] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [commitOpen, setCommitOpen] = useState(false);

  const flash = (t: string) => { setErr(""); setMsg(t); setTimeout(() => setMsg(""), 2500); };
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");

  const status = useQuery({
    queryKey: ["repo-status", pid], queryFn: () => api.get<RepoStatus>(`/projects/${pid}/repo/status`),
    refetchInterval: 15000, retry: false,
  });
  const tree = useQuery({
    queryKey: ["repo-tree", pid], queryFn: () => api.get<{ files: string[] }>(`/projects/${pid}/repo/tree`), retry: false,
  });
  const img = isImage(sel);
  const file = useQuery({
    queryKey: ["repo-file", pid, sel],
    queryFn: () => api.get<{ path: string; content: string }>(`/projects/${pid}/repo/file?path=${encodeURIComponent(sel)}`),
    enabled: !!sel && !img,
  });
  useEffect(() => {
    if (file.data && file.data.path === sel) { setValue(file.data.content); setOrig(file.data.content); }
  }, [file.data, sel]);
  // Reset the preview when switching to a markdown file.
  useEffect(() => { setPreview(false); }, [sel]);
  // Bild laden (authentifiziert → Object-URL), sauber freigeben.
  useEffect(() => {
    if (!img) { setImgUrl(""); return; }
    let url = ""; let alive = true;
    api.blobUrl(`/projects/${pid}/repo/raw?path=${encodeURIComponent(sel)}`)
      .then((u) => { url = u; if (alive) setImgUrl(u); else URL.revokeObjectURL(u); })
      .catch(fail);
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [img, sel, pid]);
  // With an active filter, expand the folders of the hits.
  useEffect(() => {
    if (!filter) return;
    const matched = (tree.data?.files || []).filter((f) => f.toLowerCase().includes(filter.toLowerCase()));
    const dirs = new Set<string>();
    for (const f of matched) { const parts = f.split("/"); for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join("/")); }
    setExpanded((prev) => new Set([...prev, ...dirs]));
  }, [filter, tree.data]);

  const dirty = !img && value !== orig;
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
    onSuccess: () => { flash("Gepusht."); qc.invalidateQueries({ queryKey: ["repo-status", pid] }); }, onError: fail,
  });

  if (status.isError) {
    const m = status.error instanceof ApiError ? status.error.message : tr("files_panel.repo_nicht_verfuegbar");
    return <div className="rounded border border-line bg-card p-4 text-sm text-muted">{m}</div>;
  }
  const st = status.data;
  const allFiles = tree.data?.files || [];
  const filtered = filter ? allFiles.filter((f) => f.toLowerCase().includes(filter.toLowerCase())) : allFiles;
  const nodes = buildTree(filter ? filtered : allFiles);
  const toggle = (p: string) => setExpanded((prev) => { const n = new Set(prev); n.has(p) ? n.delete(p) : n.add(p); return n; });

  return (
    <div>
      {/* Branch-Leiste */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-card px-3 py-2 text-sm">
        <span className="font-mono">🌿 {st?.branch || "…"}</span>
        {st && st.behind > 0 && <span className="rounded bg-yellow-500/20 px-1.5 text-xs text-yellow-300">↓ {st.behind}</span>}
        {st && st.ahead > 0 && <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">↑ {st.ahead}</span>}
        {st && st.dirty.length > 0 && <span className="rounded bg-orange-500/20 px-1.5 text-xs text-orange-300">● {st.dirty.length} geändert</span>}
        <div className="flex-1" />
        {st?.has_remote && (
          <>
            <button onClick={() => pull.mutate()} disabled={pull.isPending}
              className={BUTTON_SMALL.secondary}>{pull.isPending ? "…" : "↓ Pull"}</button>
            <button onClick={() => push.mutate()} disabled={push.isPending}
              className={BUTTON_SMALL.secondary}>{push.isPending ? "…" : "↑ Push"}</button>
          </>
        )}
        <button onClick={() => { setErr(""); setCommitOpen(true); }} disabled={!st || st.dirty.length === 0}
          className={BUTTON_SMALL.primary}>{tr("files_panel.commit")}</button>
      </div>
      {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
      {msg && <div className="mb-2 text-sm text-green-400">{msg}</div>}

      <div className="flex gap-3" style={{ height: "72vh" }}>
        {/* Verzeichnisbaum */}
        <div className="flex w-64 shrink-0 flex-col rounded-lg border border-line bg-card">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={tr("files_panel.dateien_filtern")}
            className="m-2 rounded border border-line bg-surface px-2 py-1 text-xs" />
          <div className="flex-1 overflow-y-auto px-1 pb-2">
            {nodes.length > 0
              ? <Tree nodes={nodes} sel={sel} onSelect={setSel} expanded={expanded} toggle={toggle} dirty={st?.dirty || []} />
              : <div className="px-2 py-2 text-xs text-muted">{tr("files_panel.keine_dateien")}</div>}
          </div>
        </div>

        {/* Editor / Vorschau */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-line bg-card">
          {!sel ? (
            <div className="flex h-full items-center justify-center text-sm text-muted">{tr("files_panel.datei_links_auswaehlen")}</div>
          ) : (
            <>
              <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs">
                <span className="font-mono text-muted">{sel}</span>
                {dirty && <span className="text-orange-400">● ungespeichert</span>}
                <div className="flex-1" />
                {isMarkdown(sel) && (
                  <button onClick={() => setPreview((v) => !v)}
                    className={BUTTON_SMALL.secondary}>
                    {preview ? "✎ Editor" : "👁 Vorschau"}</button>
                )}
                {!img && (
                  <button onClick={() => save.mutate()} disabled={!dirty || save.isPending}
                    className={BUTTON_SMALL.primary}>{save.isPending ? "…" : "Speichern (⌘S)"}</button>
                )}
              </div>
              <div className="min-h-0 flex-1">
                {img ? (
                  imgUrl
                    ? <div className="flex h-full items-center justify-center overflow-auto p-4">
                        <img src={imgUrl} alt={sel} className="max-h-full max-w-full object-contain" />
                      </div>
                    : <div className="p-3 text-sm text-muted">{tr("files_panel.bild_laedt")}</div>
                ) : file.isLoading ? (
                  <div className="p-3 text-sm text-muted">{tr("files_panel.laedt")}</div>
                ) : file.isError ? (
                  <div className="p-3 text-sm text-red-400">{file.error instanceof ApiError ? file.error.message : tr("files_panel.datei_nicht_lesbar")}</div>
                ) : isMarkdown(sel) && preview ? (
                  <div className="h-full overflow-auto p-4"><Markdown text={value} /></div>
                ) : (
                  <Editor height="100%" theme="vs-dark" path={sel} language={longOf(sel)}
                    value={value} onChange={(v) => setValue(v ?? "")}
                    onMount={(editor, monaco) => {
                      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => { if (value !== orig) save.mutate(); });
                    }}
                    options={{ fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false, automaticLayout: true, tabSize: 2 }} />
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {commitOpen && <CommitModal pid={pid} onClose={() => setCommitOpen(false)}
        onDone={() => { setCommitOpen(false); invalidateAll(); setOrig(value); flash("Committet."); }} />}
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
    onSuccess: (r) => { setErr(""); setTitle(r.title || ""); setDesc(r.description || ""); }, onError: fail,
  });
  useEffect(() => { gen.mutate(); }, []);

  const commit = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/repo/commit`, { title, description: desc }),
    onSuccess: onDone, onError: fail,
  });

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-xl rounded-xl border border-line bg-card p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{tr("files_panel.commit")}</h2>
          <button onClick={onClose} className={BUTTON_TEXT.secondary}>✕</button>
        </div>
        <div className="mb-2 flex items-center gap-2">
          <label className="text-xs text-muted">{tr("files_panel.titel")}</label>
          <button onClick={() => gen.mutate()} disabled={gen.isPending}
            className={BUTTON_TEXT.secondary}>{gen.isPending ? "generiert…" : "↻ neu generieren"}</button>
        </div>
        <input value={title} onChange={(e) => setTitle(e.target.value)}
          className="mb-3 w-full rounded border border-line bg-surface px-3 py-2 text-sm" />
        <label className="text-xs text-muted">{tr("files_panel.beschreibung")}</label>
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={6}
          className="mb-3 mt-1 w-full rounded border border-line bg-surface px-3 py-2 text-sm" />
        {err && <div className="mb-2 text-sm text-red-400">{err}</div>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className={BUTTON.secondary}>{tr("files_panel.abbrechen")}</button>
          <button onClick={() => title.trim() && commit.mutate()} disabled={!title.trim() || commit.isPending}
            className={BUTTON.primary}>{commit.isPending ? "Committet…" : "Committen"}</button>
        </div>
      </div>
    </div>
  );
}
