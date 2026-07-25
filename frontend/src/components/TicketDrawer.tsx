import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, AttachmentInfo, Comment, FileChange, Issue, IssueCosts, Project, ProjectMeta, workflowApi } from "../api";
import { useAuth } from "../auth";
import Markdown from "./Markdown";
import { waitInfo } from "../lib/waitReason";
import { formatTime } from "../lib/formatTime";
import WorkflowInstanceView from "./workflow/WorkflowInstanceView";
import LifecycleView from "./workflow/LifecycleView";
import WorkflowTaskForm from "./workflow/WorkflowTaskForm";
import { NODE_TYPE_LABELS } from "./workflow/types";

const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PRIOS = ["lowest", "low", "medium", "high", "highest"];

// Default-Spalten der vollen Ticket-Seite (asPage). Reihenfolge = aktueller Stand.
// Neue Blöcke hier ergänzen, damit sie bei bestehenden Nutzer-Layouts automatisch
// an ihre Default-Spalte angehängt werden (siehe useMemo unten).
const DEFAULT_LEFT = ["wait", "parent", "summary", "description", "save", "split", "plan", "umbrella", "lifecycle", "comments"];
const DEFAULT_RIGHT = ["meta", "person", "hardware", "ai", "files", "workflows"];

type LayoutCol = "left" | "right";
type Layout = { left: string[]; right: string[] };

type Draft = { summary: string; description: string; priority: string; type_id: number; status_id: number };

// <subtickets>[...]</subtickets>-Block aus dem Plan lesen (strukturierter Aufteilungs-Vorschlag).
function parseSplit(plan: string | null): { items?: any[]; raw?: string; error?: boolean } {
  const m = (plan || "").match(/<subtickets>\s*(\[[\s\S]*?\])\s*<\/subtickets>/);
  if (!m) return {};
  try { return { items: JSON.parse(m[1]) }; }
  catch { return { raw: m[1], error: true }; }
}

export default function TicketDrawer({
  issueKey, project, meta, issues, onOpen, onClose, asPage = false,
}: {
  issueKey: string; project: Project; meta: ProjectMeta; issues: Issue[];
  onOpen: (k: string) => void; onClose: () => void;
  // asPage: als volle Seite rendern (kein Overlay/Popup) — für die Ticket-Route.
  asPage?: boolean;
}) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["issue", issueKey] });
    qc.invalidateQueries({ queryKey: ["issues", project.id] });
  };
  const { data: issue } = useQuery({
    queryKey: ["issue", issueKey], queryFn: () => api.get<Issue>(`/issues/${issueKey}`),
    refetchInterval: 4000,
  });
  const { data: comments } = useQuery({
    queryKey: ["comments", issueKey], queryFn: () => api.get<Comment[]>(`/issues/${issueKey}/comments`),
  });
  const { data: fileChanges } = useQuery({
    queryKey: ["files", issueKey],
    queryFn: () => api.get<FileChange[]>(`/issues/${issueKey}/files`),
    refetchInterval: 10000,
  });
  const { data: attachments } = useQuery({
    queryKey: ["attachments", issueKey],
    queryFn: () => api.get<AttachmentInfo[]>(`/issues/${issueKey}/attachments`),
  });
  const { data: costs } = useQuery({
    queryKey: ["issue-costs", issueKey],
    queryFn: () => api.get<IssueCosts>(`/issues/${issueKey}/costs`),
    refetchInterval: 10000,
  });
  // Hardware-Bezug (TRA-25): Exemplare + Modellnamen nur in Hardware-Projekten laden.
  const hwAssets = useQuery({
    queryKey: ["hw-assets", project.id],
    queryFn: () => api.get<{ id: number; model_id: number; serial_number: string | null }[]>(
      `/hardware/assets?project_id=${project.id}`),
    enabled: !!project.has_hardware,
  });
  const hwModels = useQuery({
    queryKey: ["hw-models"],
    queryFn: () => api.get<{ id: number; name: string }[]>("/hardware/models"),
    enabled: !!project.has_hardware,
  });
  const assetLabel = (a: { id: number; model_id: number; serial_number: string | null }) =>
    [hwModels.data?.find((m) => m.id === a.model_id)?.name || `Modell #${a.model_id}`,
     a.serial_number, `#${a.id}`].filter(Boolean).join(" · ");

  const [comment, setComment] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [agent, setAgent] = useState("project_manager");
  const [err, setErr] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const diff = useQuery({
    queryKey: ["diff", issueKey],
    queryFn: () => api.get<{ diff: string }>(`/issues/${issueKey}/diff`),
    enabled: showDiff,
  });

  // ---- Nutzerspezifisches Block-Layout der vollen Ticket-Seite (nur asPage) ----
  const { user, refresh } = useAuth();

  // Effektives Layout: gespeicherte Reihenfolge als Basis, unbekannte Keys verwerfen,
  // jeden bekannten Key nur EINMAL zulassen (dedupe über beide Spalten) und bekannte
  // Keys, die (noch) in KEINER Spalte stehen, an ihre Default-Spalte anhängen — so
  // verschwinden neu hinzugekommene Blöcke nie.
  const effectiveLayout = useMemo<Layout>(() => {
    const saved = user?.ticket_layout;
    const known = new Set([...DEFAULT_LEFT, ...DEFAULT_RIGHT]);
    const seen = new Set<string>();
    const clean = (arr?: string[]) => {
      const out: string[] = [];
      for (const k of arr || []) {
        if (known.has(k) && !seen.has(k)) { seen.add(k); out.push(k); }
      }
      return out;
    };
    const left = clean(saved?.left);
    const right = clean(saved?.right);
    for (const k of DEFAULT_LEFT) if (!seen.has(k)) { seen.add(k); left.push(k); }
    for (const k of DEFAULT_RIGHT) if (!seen.has(k)) { seen.add(k); right.push(k); }
    return { left, right };
  }, [user?.ticket_layout]);

  // Lokaler State für sofortiges optimistisches Feedback beim Droppen; bei Änderung
  // des gespeicherten Layouts (z.B. nach refresh() eines anderen Geräts) neu setzen.
  const [layout, setLayout] = useState<Layout>(effectiveLayout);
  useEffect(() => { setLayout(effectiveLayout); }, [user?.ticket_layout]); // eslint-disable-line react-hooks/exhaustive-deps

  // Layout leise persistieren (Fehler ignorieren) und Auth auffrischen.
  const persistLayout = (next: Layout) => {
    api.put("/me/ticket-layout", next).then(() => refresh()).catch(() => { /* leise */ });
  };

  // Natives HTML5-DnD (Muster wie Board.tsx). overKey = Zielblock, vor dem eingefügt wird;
  // null = ans Ende der Zielspalte (Drop auf freie Spaltenfläche).
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<LayoutCol | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);
  // Griffe/DnD nur im „Layout bearbeiten"-Modus zeigen; sonst sieht alles normal aus.
  const [editLayout, setEditLayout] = useState(false);

  const dropOn = (col: LayoutCol, beforeKey: string | null) => {
    const dk = dragKey;
    setDragKey(null); setOverCol(null); setOverKey(null);
    if (!dk || dk === beforeKey) return; // Drop auf sich selbst = No-Op
    setLayout((prev) => {
      // Key aus alter Position in BEIDEN Spalten entfernen (dedupe), dann neu einfügen.
      const left = prev.left.filter((k) => k !== dk);
      const right = prev.right.filter((k) => k !== dk);
      const cols: Layout = { left, right };
      const target = cols[col];
      const idx = beforeKey ? target.indexOf(beforeKey) : -1;
      if (idx === -1) target.push(dk); else target.splice(idx, 0, dk);
      persistLayout(cols);
      return cols;
    });
  };

  // Gepufferte Bearbeitung: Änderungen erst beim Klick auf „Speichern" übernehmen (TRA-2).
  const [draft, setDraft] = useState<Draft | null>(null);
  const seed = (i: Issue): Draft => ({
    summary: i.summary, description: i.description || "", priority: i.priority,
    type_id: i.type_id, status_id: i.status_id,
  });
  useEffect(() => { if (issue) setDraft(seed(issue)); }, [issueKey, issue?.id]);

  const dirty = !!(issue && draft && (
    draft.summary !== issue.summary ||
    draft.description !== (issue.description || "") ||
    draft.priority !== issue.priority ||
    draft.type_id !== issue.type_id ||
    draft.status_id !== issue.status_id
  ));

  const save = useMutation({
    mutationFn: async () => {
      if (!issue || !draft) return;
      const body: any = {};
      if (draft.summary !== issue.summary) body.summary = draft.summary;
      if (draft.description !== (issue.description || "")) body.description = draft.description;
      if (draft.priority !== issue.priority) body.priority = draft.priority;
      if (draft.type_id !== issue.type_id) body.type_id = draft.type_id;
      if (Object.keys(body).length) await api.put(`/issues/${issueKey}`, body);
      if (draft.status_id !== issue.status_id)
        await api.put(`/issues/${issueKey}/move`, { status_id: draft.status_id, position: 0 });
    },
    onSuccess: () => { setErr(""); invalidate(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen"),
  });

  const addComment = useMutation({
    mutationFn: () => api.post(`/issues/${issueKey}/comments`, { body: comment, kind: "agent" }),
    onSuccess: () => { setComment(""); qc.invalidateQueries({ queryKey: ["comments", issueKey] }); },
  });
  const assign = useMutation({
    mutationFn: () => api.post(`/issues/${issueKey}/assign-agent`, { agent }),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const [newPersonName, setNewPersonName] = useState("");
  const setAssignee = useMutation({
    mutationFn: (body: { user_id?: number; display_name?: string }) =>
      api.post(`/issues/${issueKey}/assignee`, body),
    onSuccess: () => {
      setNewPersonName("");
      invalidate();
      qc.invalidateQueries({ queryKey: ["meta", project.id] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const clearAssignee = useMutation({
    mutationFn: () => api.del(`/issues/${issueKey}/assignee`),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const setAsset = useMutation({
    mutationFn: (asset_id: number | null) => api.put(`/issues/${issueKey}`, { asset_id }),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["asset-issues"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const unassign = useMutation({
    mutationFn: () => api.del(`/issues/${issueKey}/assign-agent`),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const life = useMutation({
    mutationFn: (path: string) => api.post(`/issues/${issueKey}/${path}`),
    onSuccess: invalidate,
    // Auch im Fehlerfall neu laden: /complete setzt das Ticket bei Merge-Problemen
    // serverseitig zurück (testing/hold) — die Ansicht muss das zeigen.
    onError: (e) => { setErr(e instanceof ApiError ? e.message : "Fehler"); invalidate(); },
  });
  const refreshAttachments = () => qc.invalidateQueries({ queryKey: ["attachments", issueKey] });
  const uploadAttachment = useMutation({
    mutationFn: (file: File) => api.upload(`/issues/${issueKey}/attachments`, file),
    onSuccess: refreshAttachments, onError: (e) => setErr(e instanceof ApiError ? e.message : "Upload fehlgeschlagen"),
  });
  const delAttachment = useMutation({
    mutationFn: (aid: number) => api.del(`/attachments/${aid}`),
    onSuccess: refreshAttachments, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const answerBlocker = useMutation({
    mutationFn: (text: string) => api.post(`/issues/${issueKey}/blocker/answer`, { answer: text }),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const del = useMutation({
    mutationFn: () => api.del(`/issues/${issueKey}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["issues", project.id] }); onClose(); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const archive = useMutation({
    mutationFn: () => api.post(`/issues/${issueKey}/${issue?.archived ? "unarchive" : "archive"}`),
    onSuccess: () => {
      const wasArchiving = !issue?.archived;
      qc.invalidateQueries({ queryKey: ["issues", project.id] });
      qc.invalidateQueries({ queryKey: ["issues-archived", project.id] });
      if (wasArchiving) onClose();   // beim Archivieren das Modal direkt schließen
      else invalidate();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const kannVerwalten = project.my_role === "maintainer" || project.my_role === "owner";
  const canWrite = kannVerwalten || project.my_role === "member";
  const isDone = issue && meta.statuses.find((s) => s.id === issue.status_id)?.category === "done";

  // Split-Beziehungen (aus der geladenen Ticket-Liste).
  const children = issue
    ? issues.filter((i) => i.parent_ticket_id === issue.id).sort((a, b) => (a.split_order ?? 0) - (b.split_order ?? 0))
    : [];
  const isUmbrella = children.length > 0;
  const parent = issue?.parent_ticket_id != null ? issues.find((i) => i.id === issue.parent_ticket_id) : undefined;
  const siblings = parent ? issues.filter((i) => i.parent_ticket_id === parent.id) : [];

  const split = parseSplit(issue?.plan || null);
  const showSplit = issue?.hold_reason === "plan_split";
  // Plan/Überblick ohne den Roh-<subtickets>-Block anzeigen.
  const planText = (issue?.plan || "").replace(/<subtickets>[\s\S]*?<\/subtickets>/g, "").trim();
  const planLabel = isUmbrella ? "Begründung / Überblick" : "Plan";
  const preApproval = issue?.agent_status === "planning" || issue?.agent_status === "plan_review";
  const wait = issue ? waitInfo(issue) : null;
  const WAIT_KIND_COLOR: Record<string, string> = {
    error: "border-red-500/40 bg-red-500/10 text-red-300",
    question: "border-yellow-500/40 bg-yellow-500/10 text-yellow-300",
    external: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  };

  // Zweispalten-Layout nur im Seitenmodus (asPage); im Popup bleibt alles einspaltig.
  const two = asPage;
  const shellOuter = asPage ? "" : "fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4";
  const shellInner = asPage
    // etwas breiter als vorher (max-w-6xl), damit die zwei Spalten Platz haben.
    ? "mx-auto max-w-6xl rounded-xl border border-line bg-card p-6"
    : "max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-line bg-card p-5 shadow-2xl";
  // Archivieren/Löschen direkt oben neben der Ticket-ID (deutliche Icon-Buttons).
  const iconBtn = "rounded-md border border-line p-1.5 text-lg leading-none";
  const headerActions = issue && (canWrite || kannVerwalten) && (
    <div className="flex items-center gap-1.5">
      {canWrite && issue.archived && (
        <>
          <span className="rounded bg-line px-1.5 text-[10px] uppercase text-muted">archiviert</span>
          <button onClick={() => archive.mutate()} title="Wiederherstellen"
            className={`${iconBtn} text-muted hover:bg-surface hover:text-brand`}>♻️</button>
        </>
      )}
      {canWrite && !issue.archived && isDone && (
        <button onClick={() => archive.mutate()} title="Ticket archivieren"
          className={`${iconBtn} text-muted hover:bg-surface hover:text-brand`}>📦</button>
      )}
      {kannVerwalten && (confirmDel ? (
        <span className="flex items-center gap-1 text-sm">
          <span className="text-red-400">Löschen?</span>
          <button onClick={() => del.mutate()} className="rounded bg-red-500 px-2 py-1 text-xs text-white">Ja</button>
          <button onClick={() => setConfirmDel(false)}
            className="rounded border border-line px-2 py-1 text-xs text-muted">Nein</button>
        </span>
      ) : (
        <button onClick={() => setConfirmDel(true)} title="Ticket löschen"
          className={`${iconBtn} text-muted hover:bg-surface hover:text-red-400`}>🗑️</button>
      ))}
    </div>
  );
  const header = (
    <div className="mb-3 flex items-center gap-2">
      <span className="font-mono text-sm text-muted">{issueKey}</span>
      {headerActions}
      <div className="flex-1" />
      {two && issue && (
        <button onClick={() => setEditLayout((v) => !v)} title="Blöcke neu anordnen (Drag & Drop)"
          className={`rounded-md border px-2.5 py-1 text-xs ${
            editLayout ? "border-brand bg-brand text-white" : "border-line text-muted hover:bg-surface hover:text-ink"
          }`}>
          {editLayout ? "✓ Layout fertig" : "⤢ Layout bearbeiten"}
        </button>
      )}
      <button onClick={onClose} className="text-muted hover:text-ink">
        {asPage ? "← Zurück" : "✕"}
      </button>
    </div>
  );

  // Ladezustand: gleiche Hülle + Header, damit die Seite/das Popup nicht springt.
  if (!issue || !draft) {
    return (
      <div className={shellOuter} onClick={asPage ? undefined : onClose}>
        <div className={shellInner} onClick={asPage ? undefined : (e) => e.stopPropagation()}>
          {header}
          <div className="text-muted">Lädt…</div>
        </div>
      </div>
    );
  }

  // Ab hier sind issue + draft garantiert vorhanden (Narrowing durch die frühe Rückgabe).
  // Jede logische Sektion wird EINMAL als Variable definiert und danach je nach Modus
  // in eine Spalte (Popup) oder zwei Grid-Spalten (asPage) einsortiert.

  // ---- Blöcke der HAUPT-Spalte (links / breit) ----
  const bWait = wait && (
    <div className={`mb-3 flex items-center gap-2 rounded border px-3 py-1.5 text-sm ${WAIT_KIND_COLOR[wait.kind]}`}>
      <span>{wait.icon}</span>
      <span className="font-medium">{wait.title}:</span>
      <span>{wait.label}</span>
    </div>
  );

  // Rückverweis Sub-Ticket → Sammelticket
  const bParent = parent && (
    <button onClick={() => onOpen(parent.key)}
      className="mb-3 block w-full truncate rounded border border-purple-500/40 bg-purple-500/5 px-3 py-1.5 text-left text-xs text-purple-300 hover:bg-purple-500/10">
      🧩 Teil {(issue.split_order ?? 0) + 1}/{siblings.length} von {parent.key} — {parent.summary}
    </button>
  );

  const bSummary = (
    <input value={draft.summary}
      onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
      className="mb-3 w-full rounded border border-line bg-surface px-3 py-2 text-base font-medium" />
  );

  const bDescription = (
    <details open className="mb-3 rounded border border-line">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">Beschreibung</summary>
      <div className="px-3 pb-3">
        <textarea value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          rows={4} className="w-full rounded border border-line bg-surface px-3 py-2" />
      </div>
    </details>
  );

  const bSave = (
    <div className="mb-4 flex items-center gap-2">
      <button disabled={!dirty || !draft.summary.trim() || save.isPending}
        onClick={() => save.mutate()}
        className="rounded bg-brand px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-40">
        {save.isPending ? "Speichert…" : "Speichern"}
      </button>
      {dirty && (
        <button onClick={() => setDraft(seed(issue))}
          className="rounded border border-line px-3 py-1.5 text-muted hover:text-ink">Verwerfen</button>
      )}
      {dirty && <span className="text-xs text-yellow-400">● Ungespeicherte Änderungen</span>}
    </div>
  );

  // Strukturierter Aufteilungs-Vorschlag (Hold „Aufteilung")
  const bSplit = showSplit && (
    <div className="mb-4 rounded-lg border border-purple-500/40 bg-purple-500/5 p-3">
      <div className="mb-2 text-sm font-medium text-purple-300">
        🧩 Vorgeschlagene Aufteilung{split.items ? ` — ${split.items.length} Sub-Tickets (sequentiell)` : ""}
      </div>
      {split.items ? (
        <div className="space-y-2">
          {split.items.map((sub: any, i: number) => (
            <details key={i} className="rounded border border-line bg-surface">
              <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-sm">
                <span className="text-muted">{i + 1}.</span>
                <span className="flex-1">{sub.summary || sub.title || `Teil ${i + 1}`}</span>
                {sub.priority && (
                  <span className={`text-xs ${PRIOS.includes(sub.priority) ? "text-muted" : ""}`}>{sub.priority}</span>
                )}
              </summary>
              <div className="border-t border-line px-3 py-2">
                {sub.description && <Markdown text={sub.description} />}
                {sub.plan && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted">Plan</summary>
                    <Markdown text={sub.plan} />
                  </details>
                )}
              </div>
            </details>
          ))}
        </div>
      ) : split.error ? (
        <div>
          <div className="mb-1 text-xs text-red-400">
            ⚠ Vorschlag nicht lesbar — bitte Planung neu anstoßen oder korrigieren.
          </div>
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs">{split.raw}</pre>
        </div>
      ) : (
        <div className="text-xs text-muted">Kein strukturierter Vorschlag im Plan gefunden.</div>
      )}
    </div>
  );

  // Plan / Begründung-Überblick als Markdown, einklappbar
  const bPlan = planText && (
    <details open={preApproval} className="mb-4 rounded border border-line">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">{planLabel}</summary>
      <div className="px-3 pb-3"><Markdown text={planText} /></div>
    </details>
  );

  // Sub-Ticket-Liste am Sammelticket
  const bUmbrella = isUmbrella && (
    <div className="mb-4 rounded-lg border border-line p-3">
      <div className="mb-2 text-sm font-medium">
        Sub-Tickets — {children.filter((c) => c.agent_status === "done").length}/{children.length} fertig
      </div>
      <div className="space-y-1">
        {children.map((c, i) => (
          <button key={c.id} onClick={() => onOpen(c.key)}
            className="flex w-full items-center gap-2 rounded border border-line bg-surface px-2 py-1.5 text-left text-sm hover:border-brand">
            <span className="text-muted">{i + 1}.</span>
            <span className="flex-1 truncate">{c.summary}</span>
            <span className="rounded bg-line px-1 text-xs text-muted">{c.agent_status || "offen"}</span>
            <span className="font-mono text-xs text-muted">{c.key}</span>
          </button>
        ))}
      </div>
    </div>
  );

  // Vom Agenten geänderte Dateien + Diff + Testumgebung + Anhänge
  const bFiles = (
    <div className="mb-4 rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">Dateien</span>
        <div className="flex-1" />
        {fileChanges && fileChanges.length > 0 && (
          <button onClick={() => setShowDiff((v) => !v)}
            className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:text-brand">
            {showDiff ? "Diff ausblenden" : "Diff ansehen"}</button>
        )}
      </div>
      {fileChanges && fileChanges.length > 0 ? (
        <div className="mb-3 space-y-1">
          {fileChanges.map((f) => (
            <div key={f.id} className="flex items-center gap-2 font-mono text-xs">
              <span className={
                f.status === "added" ? "text-green-400"
                  : f.status === "deleted" ? "text-red-400" : "text-muted"}>
                {f.status === "added" ? "A" : f.status === "deleted" ? "D" : "M"}
              </span>
              <span className="flex-1 truncate">{f.path}</span>
              <span className="text-green-400">+{f.additions}</span>
              <span className="text-red-400">−{f.deletions}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mb-3 text-xs text-muted">Noch keine Code-Änderungen.</div>
      )}

      {showDiff && (
        <div className="mb-3 max-h-96 overflow-auto rounded bg-surface p-2">
          {diff.isLoading ? <div className="text-xs text-muted">Lädt…</div>
            : !diff.data?.diff ? <div className="text-xs text-muted">Kein Diff.</div>
            : <pre className="whitespace-pre font-mono text-[11px] leading-tight">
                {diff.data.diff.split("\n").map((ln, i) => (
                  <div key={i} className={
                    ln.startsWith("+") && !ln.startsWith("+++") ? "text-green-400"
                      : ln.startsWith("-") && !ln.startsWith("---") ? "text-red-400"
                      : ln.startsWith("@@") ? "text-brand"
                      : ln.startsWith("diff ") || ln.startsWith("index ") ? "text-muted" : ""}>
                    {ln || " "}</div>
                ))}
              </pre>}
        </div>
      )}

      {/* Testumgebung: sichtbar sobald es Code-Änderungen gibt */}
      {project.my_ai_assign && fileChanges && fileChanges.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 border-t border-line pt-2 text-xs">
          <span className="font-medium">Testumgebung:</span>
          {issue.testenv_status === "running" && issue.testenv_url ? (
            <>
              <a href={issue.testenv_url} target="_blank" rel="noreferrer"
                className="text-brand hover:underline">🖥 {issue.testenv_url}</a>
              <button onClick={() => life.mutate("testenv/stop")}
                className="rounded border border-line px-2 py-0.5 text-muted hover:text-red-400">Stoppen</button>
            </>
          ) : issue.testenv_status === "starting" ? (
            <span className="text-muted">startet… (Build dauert ein paar Minuten)</span>
          ) : (
            <button onClick={() => life.mutate("testenv/start")}
              className="rounded border border-line px-2 py-0.5 text-muted hover:text-brand">🖥 Starten</button>
          )}
          {issue.testenv_status === "error" && (
            <span className="text-red-400">Fehler{issue.testenv_error ? `: ${issue.testenv_error.slice(0, 120)}` : ""}</span>
          )}
        </div>
      )}

      <div className="space-y-1">
        {attachments?.map((a) => (
          <div key={a.id} className="flex items-center gap-2 text-xs">
            <button onClick={() => api.download(`/attachments/${a.id}`, a.filename)}
              className="flex-1 truncate text-left text-brand hover:underline">📎 {a.filename}</button>
            <span className="text-muted">{Math.round(a.size / 1024)} KB</span>
            <button onClick={() => delAttachment.mutate(a.id)}
              className="text-muted hover:text-red-400">✕</button>
          </div>
        ))}
      </div>
      <label className="mt-2 inline-block cursor-pointer rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
        + Anhang hochladen
        <input type="file" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0];
            if (f) uploadAttachment.mutate(f); e.target.value = ""; }} />
      </label>
    </div>
  );

  const bComments = (
    <>
      <div className="mb-2 text-sm font-medium">Kommentare</div>
      <div className="mb-2 flex gap-2">
        <input value={comment} onChange={(e) => setComment(e.target.value)}
          placeholder="Kommentar…" className="flex-1 rounded border border-line bg-surface px-2 py-1.5" />
        <button onClick={() => comment.trim() && addComment.mutate()}
          className="rounded bg-brand px-3 py-1.5 text-white">Senden</button>
      </div>
      <div className="space-y-2">
        {[...(comments || [])].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((c) => (
          <div key={c.id} className={`rounded border p-2 text-sm ${
            c.kind === "system" ? "border-dashed border-line bg-surface/50" : "border-line bg-surface"}`}>
            <div className="mb-1 flex items-center gap-2 text-xs text-muted">
              <span>{c.kind === "system" ? "ⓘ System" : c.author_id ? c.author_label : "🤖 " + c.author_label}</span>
              {c.kind === "internal" && <span className="rounded bg-line px-1">intern</span>}
              <span className="ml-auto">{formatTime(c.created_at)}</span>
            </div>
            <div className="whitespace-pre-wrap">{c.body}</div>
          </div>
        ))}
        {comments?.length === 0 && <div className="text-xs text-muted">Noch keine Kommentare.</div>}
      </div>
    </>
  );

  // ---- Blöcke der SEITENLEISTE (rechts / schmal) ----
  const bMeta = (
    <div className="mb-3 flex flex-wrap gap-3">
      <label className="text-xs text-muted">Status
        <select value={draft.status_id} onChange={(e) => setDraft({ ...draft, status_id: Number(e.target.value) })}
          className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
          {meta.statuses.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">Priorität
        <select value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
          className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
          {PRIOS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">Typ
        <select value={draft.type_id} onChange={(e) => setDraft({ ...draft, type_id: Number(e.target.value) })}
          className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
          {meta.types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
    </div>
  );

  // Personen-Zuweisung — unabhängig von der KI-Bearbeitung (TRA-20)
  const bPerson = (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <label className="text-xs text-muted">Zugewiesen an
        <select
          value={issue.assignee_user_id ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") clearAssignee.mutate();
            else if (v === "__new__") { /* Eingabefeld unten nutzen */ }
            else setAssignee.mutate({ user_id: Number(v) });
          }}
          className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
          <option value="">— niemand —</option>
          {meta.members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name || m.username}{m.status === "placeholder" ? " (Platzhalter)" : ""}
            </option>
          ))}
          <option value="__new__" disabled>— neue Person unten eintragen —</option>
        </select>
      </label>
      <input value={newPersonName} onChange={(e) => setNewPersonName(e.target.value)}
        placeholder="Neue Person (Name)…"
        onKeyDown={(e) => { if (e.key === "Enter" && newPersonName.trim())
          setAssignee.mutate({ display_name: newPersonName.trim() }); }}
        className="mt-1 w-40 rounded border border-line bg-surface px-2 py-1 text-sm" />
      <button disabled={!newPersonName.trim() || setAssignee.isPending}
        onClick={() => setAssignee.mutate({ display_name: newPersonName.trim() })}
        className="mt-1 rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink disabled:opacity-40">
        + Zuweisen
      </button>
    </div>
  );

  // Hardware-Bezug (TRA-25): Ticket an ein Exemplar des Projekts hängen.
  const bHardware = project.has_hardware && (
    <div className="mb-3 rounded-lg border border-line p-3">
      <div className="mb-2 text-sm font-medium">Hardware</div>
      <select
        value={issue.asset_id ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          setAsset.mutate(v === "" ? null : Number(v));
        }}
        className="block w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink">
        <option value="">— kein Exemplar —</option>
        {hwAssets.data?.map((a) => (
          <option key={a.id} value={a.id}>{assetLabel(a)}</option>
        ))}
      </select>
      {issue.asset_id != null && (
        <p className="mt-1 text-xs text-muted">
          Dieses Ticket ist am Exemplar vermerkt und dort unter „Tickets" sichtbar.
        </p>
      )}
    </div>
  );

  const bAI = project.my_ai_assign && (
    <div className="mb-4 rounded-lg border border-brand/40 bg-brand/5 p-3">
      <div className="mb-2 text-sm font-medium">KI-Bearbeitung</div>
      {issue.assigned_agent ? (
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2">
            <span className="rounded bg-brand/20 px-2 py-0.5 text-brand">
              🤖 {issue.assigned_agent}
              {issue.agent_status ? ` · ${issue.agent_working ? "läuft…" : issue.agent_status}` : ""}
              {issue.hold_reason ? ` (${issue.hold_reason})` : ""}
            </span>
            <div className="flex-1" />
            {issue.agent_working ? (
              <button onClick={() => life.mutate("stop")}
                className="rounded border border-red-400/50 px-2 py-1 text-red-400 hover:bg-red-400/10">
                ⏹ Stoppen
              </button>
            ) : (
              <button onClick={() => unassign.mutate()}
                className="rounded border border-line px-2 py-1 text-muted hover:text-ink">
                aufheben
              </button>
            )}
          </div>
          {/* Noch nicht geplant oder fehlgeschlagen → Planung (neu) anstoßen. */}
          {!issue.agent_working && (issue.agent_status === null || issue.agent_status === "open"
            || issue.agent_status === "failed") && (
            <button onClick={() => life.mutate("plan")}
              className="rounded bg-brand px-3 py-1 text-white">
              🧭 {issue.agent_status === "failed" ? "Erneut planen" : "Planung starten"}
            </button>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select value={agent} onChange={(e) => setAgent(e.target.value)}
            className="rounded border border-line bg-surface px-2 py-1 text-ink">
            {AGENTS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <button onClick={() => assign.mutate()}
            className="rounded bg-brand px-3 py-1 text-white">🧭 Zur Planung übergeben</button>
          <span className="text-xs text-muted">Startet die Planung; Standard-PM entscheidet Besetzung + Split.</span>
        </div>
      )}
      {/* Lifecycle-Aktionen (Menschenhoheit) */}
      {issue.agent_status === "plan_review" && (
        <div className="mt-3 flex gap-2">
          {issue.hold_reason === "plan_split" ? (
            <button onClick={() => life.mutate("approve-split")}
              className="rounded bg-green-600 px-3 py-1 text-sm text-white">✅ Aufteilung freigeben (Sub-Tickets anlegen)</button>
          ) : (
            <button onClick={() => life.mutate("approve-plan")}
              className="rounded bg-green-600 px-3 py-1 text-sm text-white">✅ Plan freigeben</button>
          )}
          <button onClick={() => life.mutate("reject-plan")}
            className="rounded border border-line px-3 py-1 text-sm text-muted">
            {issue.hold_reason === "plan_split" ? "Verwerfen" : "Ablehnen"}
          </button>
        </div>
      )}
      {(issue.agent_status === "to_test" || issue.agent_status === "testing") && (
        <div className="mt-3 space-y-2">
          {project.testenv_enabled !== false && (
            <div className="rounded border border-line bg-surface p-2.5 text-sm">
              <div className="mb-1.5 font-medium">Testumgebung</div>
              {issue.testenv_status === "starting" ? (
                <div className="text-muted">⏳ Testumgebung startet… (Build dauert ein paar Minuten)</div>
              ) : issue.testenv_status === "running" && issue.testenv_url ? (
                <div className="flex flex-wrap items-center gap-2">
                  <a href={issue.testenv_url} target="_blank" rel="noreferrer"
                    className="rounded bg-brand px-3 py-1 text-sm text-white">🖥 Testumgebung öffnen</a>
                  <button onClick={() => life.mutate("testenv/stop")}
                    className="rounded border border-line px-3 py-1 text-sm text-muted hover:text-red-400">
                    Stoppen</button>
                  <span className="text-xs text-muted">{issue.testenv_url}</span>
                </div>
              ) : (
                <button onClick={() => life.mutate("testenv/start")}
                  className="rounded border border-line px-3 py-1 text-sm text-muted hover:text-brand">
                  🖥 Testumgebung starten</button>
              )}
              {issue.testenv_status === "error" && issue.testenv_error && (
                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-card p-2 text-xs text-red-400">
                  {issue.testenv_error}</pre>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => life.mutate("complete")} disabled={life.isPending}
              className="rounded bg-green-600 px-3 py-1 text-sm text-white disabled:opacity-50">
              ✅ Auf Fertig setzen</button>
            <span className="text-xs text-muted">
              stoppt die Testumgebung, mergt den Branch und setzt erst dann „Fertig".
            </span>
          </div>
        </div>
      )}
      {issue.agent_status === "hold" && (
        <div className="mt-3 text-sm">
          <div className="text-yellow-400">⏸ Blockiert ({issue.hold_reason})</div>
          {issue.hold_reason === "question" ? (
            <div className="mt-2 flex gap-2">
              <input id="ans" placeholder="Antwort an den Agenten…"
                className="flex-1 rounded border border-line bg-surface px-2 py-1.5"
                onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value;
                  if (e.key === "Enter" && v.trim()) { answerBlocker.mutate(v); (e.target as HTMLInputElement).value = ""; } }} />
              <button onClick={() => { const el = document.getElementById("ans") as HTMLInputElement;
                if (el?.value.trim()) { answerBlocker.mutate(el.value); el.value = ""; } }}
                className="rounded bg-brand px-3 py-1.5 text-white">Antworten</button>
            </div>
          ) : issue.hold_reason === "permission" ? (
            <div className="mt-1 text-muted">Berechtigung im <b>Monitor</b>-Tab oder per Telegram entscheiden.</div>
          ) : issue.hold_reason === "review" ? (
            <div className="mt-2 space-y-2">
              <div className="text-muted">Review-Befunde offen — prüfe den Diff (unten „Diff ansehen"),
                dann abnehmen oder eine Korrektur kommentieren.</div>
              <button onClick={() => life.mutate("complete")}
                className="rounded bg-green-600 px-3 py-1 text-sm text-white">✅ Abnehmen</button>
            </div>
          ) : (
            <button onClick={() => life.mutate("plan")}
              className="mt-2 rounded bg-brand px-3 py-1 text-white">↻ Neu planen</button>
          )}
        </div>
      )}
      {costs && costs.total_usd > 0 && (
        <div className="mt-3 border-t border-brand/20 pt-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">KI-Kosten</span>
            <span className="text-ink">${costs.total_usd.toFixed(2)} · {costs.input_tokens}/{costs.output_tokens} tok</span>
          </div>
          <div className="mt-1 space-y-0.5 text-xs text-muted">
            {costs.by_model.map((m) => (
              <div key={`${m.provider}/${m.model}`}>
                {m.model || m.provider}: <span className="text-ink">${m.usd.toFixed(2)}</span> · {m.input_tokens}/{m.output_tokens} tok
              </div>
            ))}
          </div>
        </div>
      )}
      {err && <div className="mt-2 text-sm text-red-400">{err}</div>}
    </div>
  );

  // KI-Lebenszyklus als festes Flussdiagramm (read-only, Etappe 5).
  // Nur zeigen, wenn ein Agent zugewiesen ist ODER ein KI-Zustand existiert.
  const bLifecycle = (issue.assigned_agent || issue.agent_status != null) && (
    <details open className="mb-4 rounded-lg border border-line">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-muted">
        KI-Lebenszyklus
      </summary>
      <div className="px-3 pb-3">
        <LifecycleView
          agentStatus={issue.agent_status}
          holdReason={issue.hold_reason}
          agentWorking={issue.agent_working}
          height={asPage ? "360px" : "240px"}
        />
      </div>
    </details>
  );

  // Prozess-Instanzen an diesem Ticket (Workflow-Engine)
  const bWorkflows = <IssueWorkflows issueId={issue.id} project={project} meta={meta} />;

  // (Archivieren/Löschen liegen jetzt oben im Header — siehe headerActions.)

  // Key → Block-Node. Nur im asPage-Modus verwendet. Meta/Person behalten hier ihren
  // dezenten Karten-Rahmen (wie zuvor in der Sidebar). Falsy Nodes (bedingt gerenderte
  // Blöcke) werden beim Rendern übersprungen, ihr Key bleibt aber im Layout erhalten.
  const blockMap: Record<string, ReactNode> = {
    wait: bWait,
    parent: bParent,
    summary: bSummary,
    meta: <div className="rounded-lg border border-line p-3">{bMeta}</div>,
    person: <div className="rounded-lg border border-line p-3">{bPerson}</div>,
    hardware: bHardware,
    description: bDescription,
    save: bSave,
    split: bSplit,
    plan: bPlan,
    umbrella: bUmbrella,
    ai: bAI,
    lifecycle: bLifecycle,
    workflows: bWorkflows,
    files: bFiles,
    comments: bComments,
  };

  // Eine DnD-Spalte rendern. Die Spaltenfläche selbst ist Drop-Ziel (Einfügen ans Ende);
  // jeder Block ist zusätzlich Drop-Ziel (Einfügen davor). Nur der kleine Griff (⠿) ist
  // draggable, damit Inputs/Selects/Buttons im Block bedienbar bleiben.
  const renderColumn = (col: LayoutCol, keys: string[]) => (
    <div
      className="space-y-4"
      onDragOver={editLayout ? (e) => { if (!dragKey) return; e.preventDefault(); setOverCol(col); setOverKey(null); } : undefined}
      onDrop={editLayout ? (e) => { e.preventDefault(); dropOn(col, null); } : undefined}
    >
      {keys.map((k) => {
        const node = blockMap[k];
        if (!node) return null; // Block existiert (aktuell) nicht → nicht rendern, Key bleibt im Layout
        // Normalmodus: Block ganz normal, ohne Griff/DnD/Rahmen.
        if (!editLayout) return <div key={k}>{node}</div>;
        const showLine = overCol === col && overKey === k && dragKey !== k;
        return (
          <div key={k}>
            {showLine && <div className="mb-2 h-1 rounded bg-brand" />}
            <div
              className="relative rounded-md outline-dashed outline-1 outline-offset-2 outline-line"
              onDragOver={(e) => {
                if (!dragKey || dragKey === k) return;
                e.preventDefault(); e.stopPropagation();
                setOverCol(col); setOverKey(k);
              }}
              onDrop={(e) => { e.preventDefault(); e.stopPropagation(); dropOn(col, k); }}
            >
              {/* Griff nur im Edit-Modus, deutlich sichtbar. */}
              <div
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", k);
                  setDragKey(k);
                }}
                onDragEnd={() => { setDragKey(null); setOverCol(null); setOverKey(null); }}
                title="Zum Umsortieren ziehen"
                className="absolute -left-5 top-0 cursor-grab select-none text-brand active:cursor-grabbing"
              >
                ⠿
              </div>
              {node}
            </div>
          </div>
        );
      })}
    </div>
  );

  return (
    <div className={shellOuter} onClick={asPage ? undefined : onClose}>
      <div className={shellInner} onClick={asPage ? undefined : (e) => e.stopPropagation()}>
        {header}
        {two ? (
          // Volle Seite: zweispaltiges Grid (Haupt ~2/3, Sidebar ~1/3). Blöcke sind nur im
          // „Layout bearbeiten"-Modus per DnD umsortierbar (pro Nutzer gespeichert).
          <>
            {editLayout && (
              <div className="mb-3 rounded-md border border-brand/40 bg-brand/10 px-3 py-1.5 text-xs text-muted">
                Ziehe die Blöcke am Griff <span className="text-brand">⠿</span> an eine andere Position oder in die
                andere Spalte. Deine Anordnung wird gespeichert.
              </div>
            )}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <div className="lg:col-span-2">{renderColumn("left", layout.left)}</div>
              <div>{renderColumn("right", layout.right)}</div>
            </div>
          </>
        ) : (
          // Popup: eine Spalte, gleiche Reihenfolge/Optik wie zuvor
          // (Meta + Person direkt unter dem Titel, dann Beschreibung usw.).
          <>
            {bWait}
            {bParent}
            {bSummary}
            {bMeta}
            {bPerson}
            {bDescription}
            {bSave}
            {bSplit}
            {bPlan}
            {bUmbrella}
            {bAI}
            {bLifecycle}
            {bWorkflows}
            {bFiles}
            {bComments}
          </>
        )}
      </div>
    </div>
  );
}

/** Kompakte Ansicht der Workflow-Instanzen eines Tickets + offene Schritte. */
function IssueWorkflows({ issueId, project, meta }: { issueId: number; project: Project; meta: ProjectMeta }) {
  const { data: instances } = useQuery({
    queryKey: ["issue-workflows", issueId],
    queryFn: () => workflowApi.instancesForSubject(`issue:${issueId}`),
    refetchInterval: 8000,
  });
  if (!instances || instances.length === 0) return null;

  return (
    <div className="mb-4 space-y-3">
      {instances.map((inst) => {
        const open = inst.steps.filter(
          (s) => (s.status === "waiting" || s.status === "running") &&
            (s.node_type === "human_task" || s.node_type === "approval")
        );
        return (
          <div key={inst.id} className="rounded-lg border border-line p-3">
            <div className="mb-2 text-sm font-medium">Prozess-Instanz #{inst.id}</div>
            <WorkflowInstanceView iid={inst.id} projectId={project.id} height="240px" compact />
            {open.length > 0 && (
              <div className="mt-3 space-y-3">
                {open.map((s) => {
                  const node = inst.graph.nodes.find((n) => n.id === s.node_id);
                  if (!node) return null;
                  return (
                    <div key={s.id} className="rounded border border-brand/40 bg-brand/5 p-2">
                      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
                        <span className="rounded bg-surface px-1.5 py-0.5">
                          {NODE_TYPE_LABELS[s.node_type]}
                        </span>
                        <span className="text-ink">{node.data.config.label || "Offener Schritt"}</span>
                      </div>
                      <WorkflowTaskForm
                        iid={inst.id}
                        sid={s.id}
                        nodeType={s.node_type as "human_task" | "approval"}
                        config={node.data.config}
                        members={meta.members}
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
