import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Issue, Project, ProjectMeta } from "../api";
import { BUTTON, BUTTON_TEXT} from "./ui";

const PRIOS = ["lowest", "low", "medium", "high", "highest"];

// Modal for creating a ticket with all the relevant fields plus attachments (TRA-5).
export default function NewTicketModal({
  project, meta, onClose, onCreated,
}: {
  project: Project; meta: ProjectMeta; onClose: () => void; onCreated?: (key: string) => void;
}) {
  const qc = useQueryClient();
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [typeId, setTypeId] = useState<number | undefined>(meta.types[0]?.id);
  const [statusId, setStatusId] = useState<number | undefined>(meta.statuses[0]?.id);
  const [assigneeId, setAssigneeId] = useState<string>("");
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [err, setErr] = useState("");

  // Manage image previews as object URLs (and release them again cleanly).
  useEffect(() => {
    const urls = files.map((f) => (f.type.startsWith("image/") ? URL.createObjectURL(f) : ""));
    setPreviews(urls);
    return () => urls.forEach((u) => u && URL.revokeObjectURL(u));
  }, [files]);

  const addFiles = (list: FileList | File[] | null) => {
    if (!list) return;
    const arr = Array.from(list);
    if (arr.length) setFiles((prev) => [...prev, ...arr]);
  };
  const removeFile = (i: number) => setFiles((prev) => prev.filter((_, idx) => idx !== i));

  // Paste screenshots straight from the clipboard (Ctrl+V).
  const onPaste = (e: React.ClipboardEvent) => {
    const imgs: File[] = [];
    for (const it of Array.from(e.clipboardData.items)) {
      if (it.kind === "file" && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (imgs.length) { e.preventDefault(); addFiles(imgs); }
  };

  const create = useMutation({
    mutationFn: async () => {
      const issue = await api.post<Issue>(`/projects/${project.id}/issues`, {
        summary: summary.trim(),
        description: description.trim() || null,
        priority,
        type_id: typeId,
        status_id: statusId,
      });
      // Set the assignment separately over its own (membership checked) endpoint.
      if (assigneeId) await api.post(`/issues/${issue.key}/assignee`, { user_id: Number(assigneeId) });
      // Upload attachments only after creating: they need the ticket key.
      for (const f of files) await api.upload(`/issues/${issue.key}/attachments`, f);
      return issue;
    },
    onSuccess: (issue) => {
      qc.invalidateQueries({ queryKey: ["issues", project.id] });
      onCreated?.(issue.key);
      onClose();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("new_ticket_modal.create_failed")),
  });

  const canSave = !!summary.trim() && !create.isPending;

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} onPaste={onPaste}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-line bg-card p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{tr("new_ticket_modal.new_ticket")}</h2>
          <button onClick={onClose} className={BUTTON_TEXT.secondary}>✕</button>
        </div>

        <label className="text-xs text-muted">{tr("new_ticket_modal.title")}</label>
        <input autoFocus value={summary} onChange={(e) => setSummary(e.target.value)}
          placeholder={tr("new_ticket_modal.short_summary")}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && canSave) create.mutate(); }}
          className="mb-3 mt-1 w-full rounded border border-line bg-surface px-3 py-2 text-base" />

        <label className="text-xs text-muted">{tr("new_ticket_modal.description")}</label>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)}
          rows={4} placeholder={tr("new_ticket_modal.optional")}
          className="mb-3 mt-1 w-full rounded border border-line bg-surface px-3 py-2" />

        <div className="mb-4 flex flex-wrap gap-3">
          <label className="text-xs text-muted">Typ
            <select value={typeId} onChange={(e) => setTypeId(Number(e.target.value))}
              className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
              {meta.types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-muted">Status
            <select value={statusId} onChange={(e) => setStatusId(Number(e.target.value))}
              className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
              {meta.statuses.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="text-xs text-muted">{tr("new_ticket_modal.priority")}
            <select value={priority} onChange={(e) => setPriority(e.target.value)}
              className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
              {PRIOS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="text-xs text-muted">{tr("new_ticket_modal.assigned")}
            <select value={assigneeId} onChange={(e) => setAssigneeId(e.target.value)}
              className="mt-1 block rounded border border-line bg-surface px-2 py-1 text-ink">
              <option value="">— niemand —</option>
              {meta.members.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.display_name || m.username}{m.status === "placeholder" ? " (Platzhalter)" : ""}
                </option>
              ))}
            </select>
          </label>
        </div>

        {/* Attachments / screenshots */}
        <label className="text-xs text-muted">{tr("new_ticket_modal.attachments")}</label>
        <div className="mb-4 mt-1 rounded-lg border border-dashed border-line p-3">
          {files.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {files.map((f, i) => (
                <div key={i} className="group relative">
                  {previews[i] ? (
                    <img src={previews[i]} alt={f.name}
                      className="h-16 w-16 rounded border border-line object-cover" />
                  ) : (
                    <div className="flex h-16 w-16 items-center justify-center rounded border border-line bg-surface px-1 text-center text-[11px] text-muted">
                      {f.name}
                    </div>
                  )}
                  <button onClick={() => removeFile(i)}
                    className="absolute -right-1.5 -top-1.5 rounded-full bg-red-500 px-1 text-xs leading-none text-white">✕</button>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-3 text-xs text-muted">
            <label className="cursor-pointer rounded border border-line px-2 py-1 hover:text-ink">
              + {tr("new_ticket_modal.choose_file")}
              <input type="file" multiple accept="image/*" className="hidden"
                onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
            <span>{tr("new_ticket_modal.paste_screenshot_ctrl_v")}</span>
          </div>
        </div>

        {err && <div className="mb-3 text-sm text-red-400">{err}</div>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose}
            className={BUTTON.secondary}>{tr("new_ticket_modal.cancel")}</button>
          <button disabled={!canSave} onClick={() => create.mutate()}
            className="rounded bg-brand px-4 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-40">
            {tr(create.isPending ? "new_ticket_modal.creating" : "common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
