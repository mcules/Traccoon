import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatTime } from "../lib/formatTime";
import AssistantPolicies from "../components/AssistantPolicies";
import AssistantChat from "../components/AssistantChat";
import { usePageChrome } from "../pageChrome";

interface InboxItem {
  id: number; kind: string; source: string; title: string;
  category: string; priority: string; sensitive: boolean;
  redacted_summary: string; status: string;
  from: string | null; subject: string | null;
  redaction: string; action_hint: string;
  result: string; error: string;
  created_at: string; finished_at: string | null;
}

type Tab = "chat" | "inbox" | "rules";
type Filter = "offen" | "erledigt" | "alle";
const OPEN = ["new", "approved", "running"];

// The tables hold keys: they come into being while the module loads, and a tr() at this
// place would keep the old label on a language change.
const PRIO: Record<string, { label: string; cls: string }> = {
  urgent: { label: "inbox.prio_urgent", cls: "bg-red-500/15 text-red-400" },
  high: { label: "inbox.prio_high", cls: "bg-amber-500/15 text-amber-400" },
  normal: { label: "inbox.prio_normal", cls: "bg-surface text-muted" },
  low: { label: "inbox.prio_low", cls: "bg-surface text-muted" },
};
const STATUS: Record<string, { label: string; cls: string }> = {
  new: { label: "inbox.status_new", cls: "bg-brand/20 text-brand" },
  approved: { label: "inbox.status_approved", cls: "bg-amber-500/15 text-amber-400" },
  running: { label: "inbox.status_running", cls: "bg-brand/20 text-brand" },
  done: { label: "inbox.status_done", cls: "bg-green-600/15 text-green-400" },
  error: { label: "inbox.status_error", cls: "bg-red-500/15 text-red-400" },
};

// Pull mcules@… out of "Name <mail>", for the "always from …" label.
function senderEmail(from: string | null): string {
  const m = (from || "").match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
  return m ? m[0] : (from || "");
}

export default function Inbox() {
  // Own tabs in the page content; in the header only the title, no sub-menu.
  usePageChrome(tr("nav.assistant"), []);
  const [tab, setTab] = useState<Tab>("chat");
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-1 text-lg font-semibold">{tr("inbox.persoenlicher_assistent")}</h1>
      <p className="mb-4 text-sm text-muted">{tr("inbox.einleitung")}</p>
      <div className="mb-4 flex gap-1 border-b border-line">
        {([["chat", "inbox.reiter_chat"], ["inbox", "inbox.reiter_eingaenge"],
           ["rules", "inbox.reiter_regeln"]] as [Tab, string][]).map(([reiter, l]) => (
          <button key={reiter} onClick={() => setTab(reiter)}
            className={`px-3 py-2 text-sm ${tab === reiter ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {tr(l)}</button>
        ))}
      </div>
      {tab === "chat" ? <AssistantChat /> : tab === "inbox" ? <InboxList /> : <AssistantPolicies />}
    </div>
  );
}

function InboxList() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("offen");
  const [openId, setOpenId] = useState<number | null>(null);
  const [approveId, setApproveId] = useState<number | null>(null);
  const [err, setErr] = useState("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["inbox"], queryFn: () => api.get<InboxItem[]>("/assistant/inbox"),
    refetchInterval: 5000,
  });
  const inv = () => { qc.invalidateQueries({ queryKey: ["inbox"] }); qc.invalidateQueries({ queryKey: ["policies"] }); };
  const reject = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/inbox/${id}/reject`),
    onSuccess: inv, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const items = data.filter((eintrag) =>
    filter === "alle" ? true : filter === "offen" ? OPEN.includes(eintrag.status) : !OPEN.includes(eintrag.status));

  return (
    <>
      <div className="mb-4 flex gap-1 border-b border-line">
        {(["offen", "erledigt", "alle"] as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-2 text-sm capitalize ${filter === f ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {f}</button>
        ))}
      </div>
      {err && <div className="mb-3 rounded bg-red-500/10 px-3 py-2 text-sm text-red-400">{err}</div>}
      {isLoading && <div className="text-sm text-muted">{tr("inbox.laedt")}</div>}
      {!isLoading && items.length === 0 && (
        <div className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">
          Nichts hier. Eingehende Mails erscheinen automatisch.
        </div>
      )}
      <div className="space-y-2">
        {items.map((eintrag) => {
          const prio = PRIO[eintrag.priority] || PRIO.normal;
          const st = STATUS[eintrag.status] || { label: eintrag.status, cls: "bg-surface text-muted" };
          const expanded = openId === eintrag.id;
          return (
            <div key={eintrag.id} className="rounded-lg border border-line bg-card p-4">
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className={`rounded px-1.5 text-xs ${st.cls}`}>{tr(st.label)}</span>
                <span className={`rounded px-1.5 text-xs ${prio.cls}`}>{tr(prio.label)}</span>
                {eintrag.category && <span className="rounded bg-surface px-1.5 text-xs text-muted">{eintrag.category}</span>}
                {eintrag.sensitive && <span title="sensibel — vertraulich behandeln">🔒</span>}
                {eintrag.redaction === "unredacted" && <span className="rounded bg-amber-500/15 px-1.5 text-xs text-amber-400" title={tr("inbox.volltext_freigegeben")}>ungeschwärzt</span>}
                <span className="ml-auto text-xs text-muted">{formatTime(eintrag.created_at)}</span>
              </div>
              <div className="truncate font-medium text-ink">{eintrag.subject || eintrag.title}</div>
              {eintrag.from && <div className="truncate text-xs text-muted">von {eintrag.from}</div>}
              {eintrag.redacted_summary && <p className="mt-1.5 break-words text-sm text-muted">{eintrag.redacted_summary}</p>}
              {eintrag.action_hint && (
                <p className="mt-1.5 break-words text-xs text-brand">↳ {tr("inbox.gelernte_vorgabe")}: {eintrag.action_hint}</p>
              )}

              <div className="mt-3 flex items-center gap-2">
                {(eintrag.status === "new" || eintrag.status === "error") && (
                  <>
                    <button onClick={() => { setErr(""); setApproveId(approveId === eintrag.id ? null : eintrag.id); }}
                      className="rounded bg-brand px-3 py-1 text-sm text-white">
                      {eintrag.status === "error" ? "Erneut freigeben" : "Freigeben…"}
                    </button>
                    <button onClick={() => { setErr(""); reject.mutate(eintrag.id); }} disabled={reject.isPending}
                      className="rounded border border-line px-3 py-1 text-sm text-muted hover:text-ink">
                      Verwerfen
                    </button>
                  </>
                )}
                {eintrag.status === "approved" && <span className="text-sm text-muted">{tr("inbox.wartet_auf_bearbeitung")}</span>}
                {eintrag.status === "running" && <span className="text-sm text-brand">🔄 Assistent arbeitet…</span>}
                {(eintrag.result || eintrag.error) && (
                  <button onClick={() => setOpenId(expanded ? null : eintrag.id)}
                    className="ml-auto rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
                    {expanded ? "Details ausblenden" : "Details"}
                  </button>
                )}
              </div>

              {approveId === eintrag.id && (eintrag.status === "new" || eintrag.status === "error") && (
                <ApprovePanel item={eintrag} onDone={() => { setApproveId(null); inv(); }}
                  onError={(m) => setErr(m)} />
              )}

              {expanded && (eintrag.result || eintrag.error) && (
                <div className="mt-3 border-t border-line pt-3">
                  {eintrag.error && <div className="mb-2 rounded bg-red-500/10 px-2 py-1.5 text-sm text-red-400 whitespace-pre-wrap">{eintrag.error}</div>}
                  {eintrag.result && <div className="text-sm text-ink whitespace-pre-wrap">{eintrag.result}</div>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

function ApprovePanel({ item, onDone, onError }:
  { item: InboxItem; onDone: () => void; onError: (m: string) => void }) {
  const [scope, setScope] = useState<"once" | "sender" | "category">("once");
  const [redaction, setRedaction] = useState<"redacted" | "unredacted">(
    item.redaction === "unredacted" ? "unredacted" : "redacted");
  const [note, setNote] = useState(item.action_hint || "");
  const approve = useMutation({
    mutationFn: () => api.post(`/assistant/inbox/${item.id}/approve`,
      { scope, redaction, action_note: note }),
    onSuccess: onDone, onError: (e) => onError(e instanceof ApiError ? e.message : "Fehler"),
  });
  const mail = senderEmail(item.from);

  return (
    <div className="mt-3 space-y-3 rounded border border-line bg-surface p-3 text-sm">
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.umfang")}</div>
        <div className="flex flex-wrap gap-1">
          {([
            ["once", tr("inbox.umfang_einmal")],
            ["sender", tr("inbox.umfang_absender", { absender: mail || tr("inbox.absender") })],
            ["category", tr("inbox.umfang_kategorie", { kategorie: item.category || "?" })],
          ] as ["once" | "sender" | "category", string][]).map(([s, l]) => (
            <button key={s} onClick={() => setScope(s)}
              className={`rounded border px-2 py-1 text-xs ${scope === s ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.verarbeitung")}</div>
        <div className="flex gap-1">
          {([["redacted", tr("inbox.geschwaerzt")], ["unredacted", tr("inbox.ungeschwaerzt")]] as ["redacted" | "unredacted", string][]).map(([r, l]) => (
            <button key={r} onClick={() => setRedaction(r)}
              className={`rounded border px-2 py-1 text-xs ${redaction === r ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
        {redaction === "unredacted" && (
          <p className="mt-1 text-xs text-amber-400">{tr("inbox.volltext_geht_ungeschwaerzt_an_den_assis")}</p>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.gelernte_aktion_optional")}</div>
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={tr("inbox.z_b_in_paperless_ablegen_und_im_vault_do")}
          className="w-full rounded border border-line bg-card px-2 py-1.5 text-ink outline-none" />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => approve.mutate()} disabled={approve.isPending}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50">
          {tr(scope === "once" ? "inbox.freigeben" : "inbox.freigeben_merken")}
        </button>
        {scope !== "once" && <span className="text-xs text-muted">{tr("inbox.legt_regel_an")}</span>}
      </div>
    </div>
  );
}
