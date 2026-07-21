import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, AttachmentInfo, Comment, FileChange, Issue, Project, ProjectMeta } from "../api";
import Markdown from "./Markdown";

const AGENTS = ["project_manager", "architect", "developer", "code_reviewer", "tester", "devops"];
const PRIOS = ["lowest", "low", "medium", "high", "highest"];

type Draft = { summary: string; description: string; priority: string; type_id: number; status_id: number };

// <subtickets>[...]</subtickets>-Block aus dem Plan lesen (strukturierter Aufteilungs-Vorschlag).
function parseSplit(plan: string | null): { items?: any[]; raw?: string; error?: boolean } {
  const m = (plan || "").match(/<subtickets>\s*(\[[\s\S]*?\])\s*<\/subtickets>/);
  if (!m) return {};
  try { return { items: JSON.parse(m[1]) }; }
  catch { return { raw: m[1], error: true }; }
}

export default function TicketDrawer({
  issueKey, project, meta, issues, onOpen, onClose,
}: {
  issueKey: string; project: Project; meta: ProjectMeta; issues: Issue[];
  onOpen: (k: string) => void; onClose: () => void;
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
  const [comment, setComment] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [agent, setAgent] = useState("project_manager");
  const [err, setErr] = useState("");

  // Gepufferte Bearbeitung: Änderungen erst beim Klick auf „Speichern" übernehmen (ABC-2).
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
  const unassign = useMutation({
    mutationFn: () => api.del(`/issues/${issueKey}/assign-agent`),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const life = useMutation({
    mutationFn: (path: string) => api.post(`/issues/${issueKey}/${path}`),
    onSuccess: invalidate, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
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
  const kannVerwalten = project.my_role === "maintainer" || project.my_role === "owner";

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

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-line bg-card p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <span className="font-mono text-sm text-muted">{issueKey}</span>
          <button onClick={onClose} className="text-muted hover:text-ink">✕</button>
        </div>
        {!issue || !draft ? (
          <div className="text-muted">Lädt…</div>
        ) : (
          <>
            {/* Rückverweis Sub-Ticket → Sammelticket */}
            {parent && (
              <button onClick={() => onOpen(parent.key)}
                className="mb-3 block w-full truncate rounded border border-purple-500/40 bg-purple-500/5 px-3 py-1.5 text-left text-xs text-purple-300 hover:bg-purple-500/10">
                🧩 Teil {(issue.split_order ?? 0) + 1}/{siblings.length} von {parent.key} — {parent.summary}
              </button>
            )}

            <input value={draft.summary}
              onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
              className="mb-3 w-full rounded border border-line bg-surface px-3 py-2 text-base font-medium" />

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

            <details open className="mb-3 rounded border border-line">
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">Beschreibung</summary>
              <div className="px-3 pb-3">
                <textarea value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  rows={4} className="w-full rounded border border-line bg-surface px-3 py-2" />
              </div>
            </details>

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

            {/* Strukturierter Aufteilungs-Vorschlag (Hold „Aufteilung") */}
            {showSplit && (
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
            )}

            {/* Plan / Begründung-Überblick als Markdown, einklappbar */}
            {planText && (
              <details open={preApproval} className="mb-4 rounded border border-line">
                <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted">{planLabel}</summary>
                <div className="px-3 pb-3"><Markdown text={planText} /></div>
              </details>
            )}

            {/* Sub-Ticket-Liste am Sammelticket */}
            {isUmbrella && (
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
            )}

            {project.my_ai_assign && (
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
                  <div className="mt-3 flex gap-2">
                    <button onClick={() => life.mutate("complete")}
                      className="rounded bg-green-600 px-3 py-1 text-sm text-white">✅ Abnehmen</button>
                    <button onClick={() => life.mutate("testenv/start")}
                      className="rounded border border-line px-3 py-1 text-sm text-muted">Testenv starten</button>
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
                      <div className="mt-1 text-muted">Review-Befunde offen — kommentiere Korrekturen, um fortzusetzen.</div>
                    ) : (
                      <button onClick={() => life.mutate("plan")}
                        className="mt-2 rounded bg-brand px-3 py-1 text-white">↻ Neu planen</button>
                    )}
                  </div>
                )}
                {err && <div className="mt-2 text-sm text-red-400">{err}</div>}
              </div>
            )}

            {/* Vom Agenten geänderte Dateien + manuelle Anhänge */}
            <div className="mb-4 rounded-lg border border-line p-3">
              <div className="mb-2 text-sm font-medium">Dateien</div>
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

            <div className="mb-2 text-sm font-medium">Kommentare</div>
            <div className="space-y-2">
              {comments?.map((c) => (
                <div key={c.id} className="rounded border border-line bg-surface p-2 text-sm">
                  <div className="mb-1 flex items-center gap-2 text-xs text-muted">
                    <span>{c.author_id ? c.author_label : "🤖 " + c.author_label}</span>
                    {c.kind === "internal" && <span className="rounded bg-line px-1">intern</span>}
                  </div>
                  <div className="whitespace-pre-wrap">{c.body}</div>
                </div>
              ))}
              {comments?.length === 0 && <div className="text-xs text-muted">Noch keine Kommentare.</div>}
            </div>
            <div className="mt-2 flex gap-2">
              <input value={comment} onChange={(e) => setComment(e.target.value)}
                placeholder="Kommentar…" className="flex-1 rounded border border-line bg-surface px-2 py-1.5" />
              <button onClick={() => comment.trim() && addComment.mutate()}
                className="rounded bg-brand px-3 py-1.5 text-white">Senden</button>
            </div>

            {kannVerwalten && (
              <div className="mt-6 border-t border-line pt-3">
                {confirmDel ? (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-red-400">
                      Ticket {issueKey} wirklich löschen?
                      {isUmbrella && ` (${children.length} Sub-Tickets bleiben bestehen)`}
                    </span>
                    <button onClick={() => del.mutate()}
                      className="rounded bg-red-500 px-3 py-1 text-white">Ja, löschen</button>
                    <button onClick={() => setConfirmDel(false)}
                      className="rounded border border-line px-3 py-1 text-muted">Abbrechen</button>
                  </div>
                ) : (
                  <button onClick={() => setConfirmDel(true)}
                    className="text-sm text-muted hover:text-red-400">🗑 Ticket löschen</button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
