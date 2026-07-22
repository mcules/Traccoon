import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatTime } from "../lib/formatTime";

// Projektloses Arbeits-Item des persönlichen Assistenten (z. B. aus einer eingehenden
// Mail, lokal vorklassifiziert + geschwärzt). Backend: api/mail.py (_out).
interface InboxItem {
  id: number; kind: string; source: string; title: string;
  category: string; priority: string; sensitive: boolean;
  redacted_summary: string; status: string;
  from: string | null; subject: string | null;
  result: string; error: string;
  created_at: string; finished_at: string | null;
}

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

export default function Inbox() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("offen");
  const [openId, setOpenId] = useState<number | null>(null);
  const [err, setErr] = useState("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["inbox"], queryFn: () => api.get<InboxItem[]>("/assistant/inbox"),
    refetchInterval: 5000,
  });
  const inv = () => qc.invalidateQueries({ queryKey: ["inbox"] });

  const approve = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/inbox/${id}/approve`),
    onSuccess: inv, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });
  const reject = useMutation({
    mutationFn: (id: number) => api.post(`/assistant/inbox/${id}/reject`),
    onSuccess: inv, onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const items = data.filter((t) =>
    filter === "alle" ? true : filter === "offen" ? OPEN.includes(t.status) : !OPEN.includes(t.status));
  const nNew = data.filter((t) => t.status === "new").length;

  return (
    <div className="max-w-3xl">
      <div className="mb-1 flex items-center gap-2">
        <h1 className="text-lg font-semibold">🗂️ Assistent-Inbox</h1>
        {nNew > 0 && <span className="rounded bg-brand/20 px-1.5 text-xs text-brand">{nNew} neu</span>}
      </div>
      <p className="mb-4 text-sm text-muted">
        Eingänge deines persönlichen Assistenten — lokal vorklassifiziert und geschwärzt. Erst deine
        <b> Freigabe</b> lässt den Assistenten handeln (und gibt den Volltext frei); bis dahin läuft nichts.
      </p>

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
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-1.5">
                    <span className={`rounded px-1.5 text-xs ${st.cls}`}>{st.label}</span>
                    <span className={`rounded px-1.5 text-xs ${prio.cls}`}>{prio.label}</span>
                    {t.category && <span className="rounded bg-surface px-1.5 text-xs text-muted">{t.category}</span>}
                    {t.sensitive && <span title="sensibel — vertraulich behandeln">🔒</span>}
                    <span className="ml-auto text-xs text-muted">{formatTime(t.created_at)}</span>
                  </div>
                  <div className="truncate font-medium text-ink">{t.subject || t.title}</div>
                  {t.from && <div className="truncate text-xs text-muted">von {t.from}</div>}
                  {t.redacted_summary && (
                    <p className="mt-1.5 text-sm text-muted">{t.redacted_summary}</p>
                  )}
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2">
                {(t.status === "new" || t.status === "error") && (
                  <>
                    <button onClick={() => { setErr(""); approve.mutate(t.id); }} disabled={approve.isPending}
                      className="rounded bg-brand px-3 py-1 text-sm text-white disabled:opacity-50">
                      {t.status === "error" ? "Erneut freigeben" : "Freigeben"}
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
    </div>
  );
}
