import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatTime } from "../lib/formatTime";
import AssistantPolicies from "../components/AssistantPolicies";
import AssistantChat from "../components/AssistantChat";

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

const PRIO: Record<string, { label: string; cls: string }> = {
  urgent: { label: "dringend", cls: "bg-red-500/15 text-red-400" },
  high: { label: "hoch", cls: "bg-amber-500/15 text-amber-400" },
  normal: { label: "normal", cls: "bg-surface text-muted" },
  low: { label: "niedrig", cls: "bg-surface text-muted" },
};
const STATUS: Record<string, { label: string; cls: string }> = {
  new: { label: "neu", cls: "bg-brand/20 text-brand" },
  approved: { label: "freigegeben", cls: "bg-amber-500/15 text-amber-400" },
  running: { label: "läuft…", cls: "bg-brand/20 text-brand" },
  done: { label: "erledigt", cls: "bg-green-600/15 text-green-400" },
  error: { label: "Fehler", cls: "bg-red-500/15 text-red-400" },
};

// mcules@… aus "Name <mail>" ziehen, für das "immer von …"-Label.
function senderEmail(from: string | null): string {
  const m = (from || "").match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
  return m ? m[0] : (from || "");
}

export default function Inbox() {
  const [tab, setTab] = useState<Tab>("chat");
  return (
    <div className="max-w-3xl">
      <h1 className="mb-1 text-lg font-semibold">🗂️ Persönlicher Assistent</h1>
      <p className="mb-4 text-sm text-muted">
        Eingänge lokal vorklassifiziert &amp; geschwärzt. Erst deine <b>Freigabe</b> lässt den Assistenten
        handeln — und du kannst dabei <b>Regeln lernen</b> lassen („ab jetzt immer …").
      </p>
      <div className="mb-4 flex gap-1 border-b border-line">
        {([["chat", "Chat"], ["inbox", "Eingänge"], ["rules", "Gelernte Regeln"]] as [Tab, string][]).map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-ink" : "text-muted"}`}>
            {l}</button>
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

  const items = data.filter((t) =>
    filter === "alle" ? true : filter === "offen" ? OPEN.includes(t.status) : !OPEN.includes(t.status));

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
      {isLoading && <div className="text-sm text-muted">Lädt…</div>}
      {!isLoading && items.length === 0 && (
        <div className="rounded-lg border border-dashed border-line p-8 text-center text-sm text-muted">
          Nichts hier. Eingehende Mails erscheinen automatisch.
        </div>
      )}
      <div className="space-y-2">
        {items.map((t) => {
          const prio = PRIO[t.priority] || PRIO.normal;
          const st = STATUS[t.status] || { label: t.status, cls: "bg-surface text-muted" };
          const expanded = openId === t.id;
          return (
            <div key={t.id} className="rounded-lg border border-line bg-card p-4">
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className={`rounded px-1.5 text-xs ${st.cls}`}>{st.label}</span>
                <span className={`rounded px-1.5 text-xs ${prio.cls}`}>{prio.label}</span>
                {t.category && <span className="rounded bg-surface px-1.5 text-xs text-muted">{t.category}</span>}
                {t.sensitive && <span title="sensibel — vertraulich behandeln">🔒</span>}
                {t.redaction === "unredacted" && <span className="rounded bg-amber-500/15 px-1.5 text-xs text-amber-400" title="Volltext freigegeben">ungeschwärzt</span>}
                <span className="ml-auto text-xs text-muted">{formatTime(t.created_at)}</span>
              </div>
              <div className="truncate font-medium text-ink">{t.subject || t.title}</div>
              {t.from && <div className="truncate text-xs text-muted">von {t.from}</div>}
              {t.redacted_summary && <p className="mt-1.5 text-sm text-muted">{t.redacted_summary}</p>}
              {t.action_hint && (
                <p className="mt-1.5 text-xs text-brand">↳ gelernte Vorgabe: {t.action_hint}</p>
              )}

              <div className="mt-3 flex items-center gap-2">
                {(t.status === "new" || t.status === "error") && (
                  <>
                    <button onClick={() => { setErr(""); setApproveId(approveId === t.id ? null : t.id); }}
                      className="rounded bg-brand px-3 py-1 text-sm text-white">
                      {t.status === "error" ? "Erneut freigeben" : "Freigeben…"}
                    </button>
                    <button onClick={() => { setErr(""); reject.mutate(t.id); }} disabled={reject.isPending}
                      className="rounded border border-line px-3 py-1 text-sm text-muted hover:text-ink">
                      Verwerfen
                    </button>
                  </>
                )}
                {t.status === "approved" && <span className="text-sm text-muted">wartet auf Bearbeitung…</span>}
                {t.status === "running" && <span className="text-sm text-brand">🔄 Assistent arbeitet…</span>}
                {(t.result || t.error) && (
                  <button onClick={() => setOpenId(expanded ? null : t.id)}
                    className="ml-auto rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
                    {expanded ? "Details ausblenden" : "Details"}
                  </button>
                )}
              </div>

              {approveId === t.id && (t.status === "new" || t.status === "error") && (
                <ApprovePanel item={t} onDone={() => { setApproveId(null); inv(); }}
                  onError={(m) => setErr(m)} />
              )}

              {expanded && (t.result || t.error) && (
                <div className="mt-3 border-t border-line pt-3">
                  {t.error && <div className="mb-2 rounded bg-red-500/10 px-2 py-1.5 text-sm text-red-400 whitespace-pre-wrap">{t.error}</div>}
                  {t.result && <div className="text-sm text-ink whitespace-pre-wrap">{t.result}</div>}
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
        <div className="mb-1 text-xs uppercase text-muted">Umfang</div>
        <div className="flex flex-wrap gap-1">
          {([
            ["once", "Nur diesmal"],
            ["sender", `Immer von ${mail || "Absender"}`],
            ["category", `Immer Kategorie „${item.category || "?"}"`],
          ] as ["once" | "sender" | "category", string][]).map(([s, l]) => (
            <button key={s} onClick={() => setScope(s)}
              className={`rounded border px-2 py-1 text-xs ${scope === s ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">Verarbeitung</div>
        <div className="flex gap-1">
          {([["redacted", "geschwärzt"], ["unredacted", "ungeschwärzt"]] as ["redacted" | "unredacted", string][]).map(([r, l]) => (
            <button key={r} onClick={() => setRedaction(r)}
              className={`rounded border px-2 py-1 text-xs ${redaction === r ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
        {redaction === "unredacted" && (
          <p className="mt-1 text-xs text-amber-400">⚠ Volltext geht ungeschwärzt an den Assistenten (Claude).</p>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">Gelernte Aktion (optional)</div>
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="z. B. In Paperless ablegen und im Vault dokumentieren"
          className="w-full rounded border border-line bg-card px-2 py-1.5 text-ink outline-none" />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => approve.mutate()} disabled={approve.isPending}
          className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50">
          {scope === "once" ? "Freigeben" : "Freigeben & merken"}
        </button>
        {scope !== "once" && <span className="text-xs text-muted">legt eine Regel an</span>}
      </div>
    </div>
  );
}
