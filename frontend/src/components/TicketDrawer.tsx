import { useEffect, useMemo, useState, type ReactNode } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, AttachmentInfo, Comment, FileChange, Issue, IssueCosts, Project, ProjectMeta, workflowApi } from "../api";
import { useAuth } from "../auth";
import Markdown from "./Markdown";
import { waitInfo } from "../lib/waitReason";
import { formatTime } from "../lib/formatTime";
import ArtifactFields from "./ArtifactFields";
import WorkflowInstanceView from "./workflow/WorkflowInstanceView";
import LifecycleView from "./workflow/LifecycleView";
import WorkflowTaskForm from "./workflow/WorkflowTaskForm";
import { NODE_TYPE_LABELS } from "./workflow/types";
import { BUTTON, BUTTON_SMALL, BUTTON_TEXT} from "./ui";

const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PRIOS = ["lowest", "low", "medium", "high", "highest"];

// Default columns of the full ticket page (asPage). The order is the current state.
// Add new blocks here so that they are automatically appended to their default column with
// existing user layouts (see the useMemo below).
const DEFAULT_LEFT = ["wait", "parent", "summary", "description", "save", "split", "plan", "umbrella", "lifecycle", "comments"];
const DEFAULT_RIGHT = ["meta", "person", "felder", "hardware", "ai", "files", "workflows"];

type LayoutCol = "left" | "right";
type Layout = { left: string[]; right: string[] };

type Draft = { summary: string; description: string; priority: string; type_id: number; status_id: number };

// Read the <subtickets>[...]</subtickets> block from the plan (structured splitting proposal).
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
  // asPage: render as a full page (no overlay or popup), for the ticket route.
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
  // Hardware reference (ABC-25): load units and model names only in hardware projects.
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

  // ---- User specific block layout of the full ticket page (asPage only) ----
  const { user, refresh } = useAuth();

  // Effective layout: the saved order as the base, discard unknown keys, allow every known
  // key only ONCE (deduplicating over both columns) and append known keys that stand in NO
  // column (yet) to their default column, so that newly added blocks never disappear.
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

  // Local state for immediate optimistic feedback while dropping; reset when the saved
  // layout changes (for instance after a refresh() from another device).
  const [layout, setLayout] = useState<Layout>(effectiveLayout);
  useEffect(() => { setLayout(effectiveLayout); }, [user?.ticket_layout]); // eslint-disable-line react-hooks/exhaustive-deps

  // Persist the layout quietly (ignoring errors) and refresh the auth.
  const persistLayout = (next: Layout) => {
    api.put("/me/ticket-layout", next).then(() => refresh()).catch(() => { /* leise */ });
  };

  // Native HTML5 DnD (the pattern of Board.tsx). overKey = target block to insert before;
  // null = to the end of the target column (drop on the free column area).
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<LayoutCol | null>(null);
  const [overKey, setOverKey] = useState<string | null>(null);
  // Show handles and DnD only in the "edit layout" mode; otherwise everything looks normal.
  const [editLayout, setEditLayout] = useState(false);

  const dropOn = (col: LayoutCol, beforeKey: string | null) => {
    const dk = dragKey;
    setDragKey(null); setOverCol(null); setOverKey(null);
    if (!dk || dk === beforeKey) return; // Drop auf sich selbst = No-Op
    setLayout((prev) => {
      // Remove the key from its old position in BOTH columns (dedupe), then insert it anew.
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

  // Buffered editing: changes are taken over only on a click on "save" (ABC-2).
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
    // Reload in the error case as well: on merge problems /complete resets the ticket server
    // side (testing/hold), and the view has to show that.
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
  const canManage = project.my_role === "maintainer" || project.my_role === "owner";
  const canWrite = canManage || project.my_role === "member";
  const isDone = issue && meta.statuses.find((s) => s.id === issue.status_id)?.category === "done";

  // Split relations (from the loaded ticket list).
  const children = issue
    ? issues.filter((i) => i.parent_ticket_id === issue.id).sort((a, b) => (a.split_order ?? 0) - (b.split_order ?? 0))
    : [];
  const isUmbrella = children.length > 0;
  const parent = issue?.parent_ticket_id != null ? issues.find((i) => i.id === issue.parent_ticket_id) : undefined;
  const siblings = parent ? issues.filter((i) => i.parent_ticket_id === parent.id) : [];

  const split = parseSplit(issue?.plan || null);
  const showSplit = issue?.hold_reason === "plan_split";
  // Show the plan and overview without the raw <subtickets> block.
  const planText = (issue?.plan || "").replace(/<subtickets>[\s\S]*?<\/subtickets>/g, "").trim();
  const planLabel = tr(isUmbrella ? "ticket_drawer.reason_overview" : "ticket_drawer.plan");
  const preApproval = issue?.agent_status === "planning" || issue?.agent_status === "plan_review";
  const wait = issue ? waitInfo(issue) : null;
  const WAIT_KIND_COLOR: Record<string, string> = {
    error: "border-red-500/40 bg-red-500/10 text-red-300",
    question: "border-yellow-500/40 bg-yellow-500/10 text-yellow-300",
    external: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  };

  // Two column layout only in page mode (asPage); in the popup everything stays single column.
  const two = asPage;
  const shellOuter = asPage ? "" : "fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4";
  const shellInner = asPage
    // a little wider than before (max-w-6xl) so that the two columns have room.
    ? "mx-auto max-w-6xl rounded-xl border border-line bg-card p-6"
    : "max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-line bg-card p-5 shadow-2xl";
  // Archiving and deleting right at the top beside the ticket id (clear icon buttons).
  // Icon-Knopf in der Seitenleiste: blau wie jeder Knopf, nur ohne Beschriftung.
  const iconBtn = "rounded-md border border-brand bg-brand p-1.5 text-lg leading-none text-white";
  const headerActions = issue && (canWrite || canManage) && (
    <div className="flex items-center gap-1.5">
      {canWrite && issue.archived && (
        <>
          <span className="rounded bg-line px-1.5 text-[11px] uppercase text-muted">archiviert</span>
          <button onClick={() => archive.mutate()} title={tr("ticket_drawer.restore")}
            className={`${iconBtn} hover:bg-brand/90`}>♻️</button>
        </>
      )}
      {canWrite && !issue.archived && isDone && (
        <button onClick={() => archive.mutate()} title={tr("ticket_drawer.archive_ticket")}
          className={`${iconBtn} hover:bg-brand/90`}>📦</button>
      )}
      {canManage && (confirmDel ? (
        <span className="flex items-center gap-1 text-sm">
          <span className="text-red-400">{tr("ticket_drawer.delete")}</span>
          <button onClick={() => del.mutate()} className={BUTTON_SMALL.danger}>{tr("ticket_drawer.yes")}</button>
          <button onClick={() => setConfirmDel(false)}
            className={BUTTON_SMALL.secondary}>{tr("ticket_drawer.no")}</button>
        </span>
      ) : (
        <button onClick={() => setConfirmDel(true)} title={tr("ticket_drawer.delete_ticket")}
          className={`rounded-md border border-red-600 bg-red-600 p-1.5 text-lg leading-none text-white hover:bg-red-600/90`}>🗑️</button>
      ))}
    </div>
  );
  const header = (
    <div className="mb-3 flex items-center gap-2">
      <span className="font-mono text-sm text-muted">{issueKey}</span>
      {headerActions}
      <div className="flex-1" />
      {two && issue && (
        <button onClick={() => setEditLayout((v) => !v)} title={tr("ticket_drawer.rearrange_blocks_drag_and_drop")}
          className={`rounded-md border px-2.5 py-1 text-xs ${
            editLayout ? "border-brand bg-brand text-white" : "border-line text-muted hover:bg-surface hover:text-ink"
          }`}>
          {editLayout ? "✓ Layout fertig" : "⤢ Layout bearbeiten"}
        </button>
      )}
      <button onClick={onClose} className={BUTTON_TEXT.secondary}>
        {asPage ? `← ${tr("common.back")}` : "✕"}
      </button>
    </div>
  );

  // Loading state: the same shell plus header, so that page or popup does not jump.
  if (!issue || !draft) {
    return (
      <div className={shellOuter} onClick={asPage ? undefined : onClose}>
        <div className={shellInner} onClick={asPage ? undefined : (e) => e.stopPropagation()}>
          {header}
          <div className="text-muted">{tr("ticket_drawer.loading")}</div>
        </div>
      </div>
    );
  }

  // From here on issue and draft are guaranteed to exist (narrowed by the early return).
  // Every logical section is defined ONCE as a variable and afterwards sorted into one column
  // (popup) or two grid columns (asPage) depending on the mode.

  // ---- Blocks of the MAIN column (left, wide) ----
  const bWait = wait && (
    <div className={`mb-3 flex items-center gap-2 rounded border px-3 py-1.5 text-sm ${WAIT_KIND_COLOR[wait.kind]}`}>
      <span>{wait.icon}</span>
      <span className="font-medium">{wait.title}:</span>
      <span>{wait.label}</span>
    </div>
  );

  // Back reference from a sub-ticket to the collective ticket
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
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">{tr("ticket_drawer.description")}</summary>
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
          className={BUTTON.secondary}>{tr("ticket_drawer.discard")}</button>
      )}
      {dirty && <span className="text-xs text-yellow-400">{tr("ticket_drawer.unsaved_changes")}</span>}
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
                    <summary className="cursor-pointer text-xs text-muted">{tr("ticket_drawer.plan")}</summary>
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
        <div className="text-xs text-muted">{tr("ticket_drawer.no_structured_proposal_found_in_the_plan")}</div>
      )}
    </div>
  );

  // Plan and reasoning overview as markdown, collapsible
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

  // Files changed by the agent plus diff, test environment and attachments
  const bFiles = (
    <div className="mb-4 rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{tr("ticket_drawer.files")}</span>
        <div className="flex-1" />
        {fileChanges && fileChanges.length > 0 && (
          <button onClick={() => setShowDiff((v) => !v)}
            className={BUTTON_SMALL.secondary}>
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
        <div className="mb-3 text-xs text-muted">{tr("ticket_drawer.no_code_changes_yet")}</div>
      )}

      {showDiff && (
        <div className="mb-3 max-h-96 overflow-auto rounded bg-surface p-2">
          {diff.isLoading ? <div className="text-xs text-muted">{tr("ticket_drawer.loading")}</div>
            : !diff.data?.diff ? <div className="text-xs text-muted">{tr("ticket_drawer.no_diff")}</div>
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
          <span className="font-medium">{tr("ticket_drawer.test_environment")}</span>
          {issue.testenv_status === "running" && issue.testenv_url ? (
            <>
              <a href={issue.testenv_url} target="_blank" rel="noreferrer"
                className={BUTTON_TEXT.secondary}>🖥 {issue.testenv_url}</a>
              <button onClick={() => life.mutate("testenv/stop")}
                className={BUTTON_SMALL.danger}>{tr("ticket_drawer.stop")}</button>
            </>
          ) : issue.testenv_status === "starting" ? (
            <span className="text-muted">{tr("ticket_drawer.starting_build_takes_few")}</span>
          ) : (
            <button onClick={() => life.mutate("testenv/start")}
              className={BUTTON_SMALL.secondary}>🖥 Starten</button>
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
              className={BUTTON_TEXT.danger}>✕</button>
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
      <div className="mb-2 text-sm font-medium">{tr("ticket_drawer.comments")}</div>
      <div className="mb-2 flex gap-2">
        <input value={comment} onChange={(e) => setComment(e.target.value)}
          placeholder={tr("ticket_drawer.comment")} className="flex-1 rounded border border-line bg-surface px-2 py-1.5" />
        <button onClick={() => comment.trim() && addComment.mutate()}
          className={BUTTON.primary}>{tr("ticket_drawer.send")}</button>
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
        {comments?.length === 0 && <div className="text-xs text-muted">{tr("ticket_drawer.no_comments_yet")}</div>}
      </div>
    </>
  );

  // ---- Blocks of the SIDEBAR (right, narrow) ----
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

  // Person assignment, independent of the AI processing (ABC-20)
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
        placeholder={tr("ticket_drawer.new_person_name")}
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

  // Hardware reference (ABC-25): hang the ticket off a unit of the project.
  const bHardware = project.has_hardware && (
    <div className="mb-3 rounded-lg border border-line p-3">
      <div className="mb-2 text-sm font-medium">{tr("ticket_drawer.hardware")}</div>
      <select
        value={issue.asset_id ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          setAsset.mutate(v === "" ? null : Number(v));
        }}
        className="block w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink">
        <option value="">{tr("ticket_drawer.no_item")}</option>
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
              {issue.agent_status ? ` · ${issue.agent_working ? tr("ticket_drawer.running") : issue.agent_status}` : ""}
              {issue.hold_reason ? ` (${issue.hold_reason})` : ""}
            </span>
            <div className="flex-1" />
            {issue.agent_working ? (
              <button onClick={() => life.mutate("stop")}
                className={BUTTON_SMALL.danger}>
                ⏹ Stoppen
              </button>
            ) : (
              <button onClick={() => unassign.mutate()}
                className={BUTTON_SMALL.secondary}>
                aufheben
              </button>
            )}
          </div>
          {/* Noch nicht geplant oder fehlgeschlagen → Planung (neu) anstoßen. */}
          {!issue.agent_working && (issue.agent_status === null || issue.agent_status === "open"
            || issue.agent_status === "failed") && (
            <button onClick={() => life.mutate("plan")}
              className={BUTTON_SMALL.primary}>
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
            className={BUTTON_SMALL.primary}>{tr("ticket_drawer.hand_planning")}</button>
          <span className="text-xs text-muted">{tr("ticket_drawer.starts_the_planning_the_default_pm_decides_st")}</span>
        </div>
      )}
      {/* Lifecycle-Aktionen (Menschenhoheit) */}
      {issue.agent_status === "plan_review" && (
        <div className="mt-3 flex gap-2">
          {issue.hold_reason === "plan_split" ? (
            <button onClick={() => life.mutate("approve-split")}
              className={BUTTON.confirm}>✅ Aufteilung freigeben (Sub-Tickets anlegen)</button>
          ) : (
            <button onClick={() => life.mutate("approve-plan")}
              className={BUTTON.confirm}>✅ Plan freigeben</button>
          )}
          <button onClick={() => life.mutate("reject-plan")}
            className={BUTTON.secondary}>
            {issue.hold_reason === "plan_split" ? "Verwerfen" : "Ablehnen"}
          </button>
        </div>
      )}
      {(issue.agent_status === "to_test" || issue.agent_status === "testing") && (
        <div className="mt-3 space-y-2">
          {project.testenv_enabled !== false && (
            <div className="rounded border border-line bg-surface p-2.5 text-sm">
              <div className="mb-1.5 font-medium">{tr("ticket_drawer.test_environment_2")}</div>
              {issue.testenv_status === "starting" ? (
                <div className="text-muted">{tr("ticket_drawer.test_environment_starting_the_build_takes_a_f")}</div>
              ) : issue.testenv_status === "running" && issue.testenv_url ? (
                <div className="flex flex-wrap items-center gap-2">
                  <a href={issue.testenv_url} target="_blank" rel="noreferrer"
                    className={BUTTON.primary}>{tr("ticket_drawer.open_test_environment")}</a>
                  <button onClick={() => life.mutate("testenv/stop")}
                    className={BUTTON.danger}>
                    Stoppen</button>
                  <span className="text-xs text-muted">{issue.testenv_url}</span>
                </div>
              ) : (
                <button onClick={() => life.mutate("testenv/start")}
                  className={BUTTON.secondary}>
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
              className={BUTTON.confirm}>
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
              <input id="ans" placeholder={tr("ticket_drawer.reply_to_the_agent")}
                className="flex-1 rounded border border-line bg-surface px-2 py-1.5"
                onKeyDown={(e) => { const v = (e.target as HTMLInputElement).value;
                  if (e.key === "Enter" && v.trim()) { answerBlocker.mutate(v); (e.target as HTMLInputElement).value = ""; } }} />
              <button onClick={() => { const el = document.getElementById("ans") as HTMLInputElement;
                if (el?.value.trim()) { answerBlocker.mutate(el.value); el.value = ""; } }}
                className={BUTTON.primary}>{tr("ticket_drawer.reply")}</button>
            </div>
          ) : issue.hold_reason === "permission" ? (
            <div className="mt-1 text-muted">{tr("ticket_drawer.decide_permission_monitor_tab")}</div>
          ) : issue.hold_reason === "review" ? (
            <div className="mt-2 space-y-2">
              <div className="text-muted">{tr("ticket_drawer.review_findings_are_open_check_the_diff_below")}</div>
              <button onClick={() => life.mutate("complete")}
                className={BUTTON.confirm}>✅ Abnehmen</button>
            </div>
          ) : (
            <button onClick={() => life.mutate("plan")}
              className={BUTTON_SMALL.primary}>↻ Neu planen</button>
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

  // AI lifecycle. If a process is running, EXACTLY THAT graph is shown (including the
  // adjustments of the project); otherwise the shipped scheme as an orientation.
  const bLifecycle = (issue.assigned_agent || issue.agent_status != null) && (
    <details open className="mb-4 rounded-lg border border-line">
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-muted">
        KI-Lebenszyklus
      </summary>
      <div className="px-3 pb-3">
        {issue.workflow_instance_id ? (
          <WorkflowInstanceView
            iid={issue.workflow_instance_id}
            projectId={issue.project_id}
            height={asPage ? "360px" : "240px"}
          />
        ) : (
          <LifecycleView
            agentStatus={issue.agent_status}
            holdReason={issue.hold_reason}
            agentWorking={issue.agent_working}
            height={asPage ? "360px" : "240px"}
          />
        )}
      </div>
    </details>
  );

  // Prozess-Instanzen an diesem Ticket (Workflow-Engine)
  const bWorkflows = <IssueWorkflows issueId={issue.id} project={project} meta={meta} />;

  // (Archiving and deleting now sit at the top in the header, see headerActions.)

  // Free fields of the artifact (Administration → Artifacts). Without maintained fields the
  // component renders nothing and the block stays empty.
  const bFields = issue.artifact_id ? (
    <div className="rounded-lg border border-line p-3">
      <div className="mb-2 text-xs font-medium text-muted">{tr("ticket_drawer.fields")}</div>
      <ArtifactFields artifactId={issue.artifact_id} />
    </div>
  ) : null;

  // Key to block node. Used only in asPage mode. Meta and person keep their discreet card
  // frame here (as before in the sidebar). Falsy nodes (conditionally rendered blocks) are
  // skipped while rendering, but their key stays in the layout.
  const blockMap: Record<string, ReactNode> = {
    wait: bWait,
    parent: bParent,
    summary: bSummary,
    meta: <div className="rounded-lg border border-line p-3">{bMeta}</div>,
    person: <div className="rounded-lg border border-line p-3">{bPerson}</div>,
    fields: bFields,
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

  // Render one DnD column. The column area itself is a drop target (insert at the end); every
  // block is a drop target as well (insert before it). Only the small handle (⠿) is
  // draggable, so that inputs, selects and buttons in the block stay operable.
  const renderColumn = (col: LayoutCol, keys: string[]) => (
    <div
      className="space-y-4"
      onDragOver={editLayout ? (e) => { if (!dragKey) return; e.preventDefault(); setOverCol(col); setOverKey(null); } : undefined}
      onDrop={editLayout ? (e) => { e.preventDefault(); dropOn(col, null); } : undefined}
    >
      {keys.map((k) => {
        const node = blockMap[k];
        if (!node) return null; // Block existiert (aktuell) nicht → nicht rendern, Key bleibt im Layout
        // Normal mode: the block completely normally, without handle, DnD or frame.
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
                title={tr("ticket_drawer.drag_reorder")}
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
          // Full page: two column grid (main about 2/3, sidebar about 1/3). Blocks are only
          // reorderable by DnD in the "edit layout" mode (saved per user).
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
          // Popup: one column, the same order and look as before
          // (meta plus person right below the title, then the description and so on).
          <>
            {bWait}
            {bParent}
            {bSummary}
            {bMeta}
            {bPerson}
            {bFields}
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

/** Compact view of the workflow instances of a ticket plus open steps. */
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
                          {tr(NODE_TYPE_LABELS[s.node_type])}
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
