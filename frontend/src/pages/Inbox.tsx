import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatTime } from "../lib/formatTime";
import AssistantPolicies from "../components/AssistantPolicies";
import AssistantChat from "../components/AssistantChat";
import {
  Area, Tag, Errorrow, Listing, ListingEmpty, ListRow, Tab, BUTTON } from "../components/ui";
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

type Tab = "chat" | "inbox" | "rules" | "statistik";
type Filter = "offen" | "erledigt" | "alle";
const OPEN = ["new", "approved", "running"];

// The tables hold keys: they come into being while the module loads, and a tr() at this
// place would keep the old label on a language change.
type TagColor = "neutral" | "green" | "yellow" | "red" | "blue" | "violet" | "brand";
const PRIO: Record<string, { label: string; color: TagColor }> = {
  urgent: { label: "inbox.prio_urgent", color: "red" },
  high: { label: "inbox.prio_high", color: "yellow" },
  normal: { label: "inbox.prio_normal", color: "neutral" },
  low: { label: "inbox.prio_low", color: "neutral" },
};
const STATUS: Record<string, { label: string; color: TagColor }> = {
  new: { label: "inbox.status_new", color: "brand" },
  approved: { label: "inbox.status_approved", color: "yellow" },
  running: { label: "inbox.status_running", color: "blue" },
  done: { label: "inbox.status_done", color: "green" },
  error: { label: "inbox.status_error", color: "red" },
};

// Pull the local part out of "Name <mail>", for the "always from …" label.
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
      <h1 className="mb-1 text-lg font-semibold">{tr("inbox.personal_assistant")}</h1>
      <p className="mb-4 text-sm text-muted">{tr("inbox.incoming_mail_classified_redacted")}</p>
      <div className="mb-4">
        <Tab active={tab} onChoose={setTab} selection={[
          ["chat", tr("inbox.chat")],
          ["inbox", tr("inbox.incoming")],
          ["rules", tr("inbox.learned_rules")],
          ["statistik", tr("inbox.statistics")],
        ]} />
      </div>
      {tab === "chat" ? <AssistantChat />
        : tab === "inbox" ? <InboxList />
        : tab === "statistik" ? <Stats />
        : <AssistantPolicies />}
    </div>
  );
}

type Classification = { total: number; sortedout: number; passed: number; open: number };
type StatsData = {
  days: number;
  kinds: Record<string, Classification>;
  model: { decided: number; treffer: number; quote: number | null };
};

/**
 * As what mail was classified.
 *
 * Counted by the server out of the rows that exist anyway, so the view shows the whole
 * stock from the first opening instead of starting at zero. The bars are plain div widths:
 * a chart library for six numbers would be a dependency nobody can read afterwards.
 */
function Stats() {
  const [days, setDays] = useState(30);
  const { data } = useQuery({
    queryKey: ["assistant-statistik", days],
    queryFn: () => api.get<StatsData>(`/assistant/stats?days=${days}`),
  });
  const kinds = Object.entries(data?.kinds || {});
  const largest = Math.max(1, ...kinds.map(([, w]) => w.total));

  return (
    <div className="space-y-4">
      <Area
        hint={tr("inbox.what_mail_classified_counted")}
        tools={<Tab active={String(days)} onChoose={(w) => setDays(Number(w))} selection={[
          ["7", tr("common.days_7")], ["30", tr("common.days_30")], ["90", tr("common.days_90")], ["365", tr("common.year_1")],
        ]} />}
      >
        {kinds.length === 0 && <p className="text-sm text-muted">{tr("inbox.nothing_classified_period")}</p>}
        <div className="space-y-2">
          {kinds.map(([art, w]) => (
            <div key={art}>
              <div className="mb-0.5 flex items-baseline gap-2 text-sm">
                <span className="font-medium text-ink">{art}</span>
                <span className="text-xs text-muted">
                  {w.total}× · {tr("inbox.count_sorted", { count: w.sortedout })}
                  {w.open > 0 && ` · ${tr("inbox.count_open", { count: w.open })}`}
                </span>
              </div>
              {/* Two sections on one bar: what was cleared away, what stayed. */}
              <div className="flex h-2.5 overflow-hidden rounded bg-surface"
                style={{ width: `${Math.round((w.total / largest) * 100)}%`, minWidth: "6%" }}>
                <div className="bg-red-500/60" style={{ flexGrow: w.sortedout || 0 }} />
                <div className="bg-brand/50" style={{ flexGrow: (w.total - w.sortedout) || 0 }} />
              </div>
            </div>
          ))}
        </div>
      </Area>

      <Area title={tr("inbox.hit_rate_local_model")} hint={tr("inbox.how_often_model_judged")}>
        {data?.model.quote === null || data?.model.decided === 0 ? (
          <p className="text-sm text-muted">{tr("inbox.nothing_decided_yet")}</p>
        ) : (
          <p className="text-sm text-ink">
            <span className="text-2xl font-semibold">
              {Math.round((data?.model.quote ?? 0) * 100)}%
            </span>{" "}
            <span className="text-muted">
              {tr("inbox.hits_total_agreement", {
                hits: data?.model.treffer ?? 0, total: data?.model.decided ?? 0,
              })}
            </span>
          </p>
        )}
      </Area>
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
    onSuccess: inv, onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")),
  });

  const items = data.filter((entry) =>
    filter === "alle" ? true : filter === "offen" ? OPEN.includes(entry.status) : !OPEN.includes(entry.status));

  return (
    <>
      <div className="mb-4 flex gap-1 border-b border-line">
      </div>
      <Area
        tools={<Tab active={filter} onChoose={setFilter} selection={[
          ["offen", tr("common.open_state")], ["erledigt", tr("common.done_state")], ["alle", tr("common.all")],
        ]} />}
      >
      <Errorrow text={err} />
      {isLoading && <div className="text-sm text-muted">{tr("inbox.loading")}</div>}
      <Listing>
        {items.map((entry) => {
          const prio = PRIO[entry.priority] || PRIO.normal;
          const st = STATUS[entry.status] || { label: entry.status, color: "neutral" as const };
          const expanded = openId === entry.id;
          return (
            <ListRow key={entry.id}>
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <Tag color={st.color}>{tr(st.label)}</Tag>
                <Tag color={prio.color}>{tr(prio.label)}</Tag>
                {entry.category && <Tag>{entry.category}</Tag>}
                {entry.sensitive && <span title={tr("inbox.sensitive_title")}>🔒</span>}
                {entry.redaction === "unredacted" && (
                  <Tag color="yellow" title={tr("inbox.full_text_released")}>{tr("inbox.unredacted")}</Tag>
                )}
                <span className="ml-auto text-xs text-muted">{formatTime(entry.created_at)}</span>
              </div>
              <div className="truncate font-medium text-ink">{entry.subject || entry.title}</div>
              {entry.from && <div className="truncate text-xs text-muted">{tr("inbox.from")} {entry.from}</div>}
              {entry.redacted_summary && <p className="mt-1.5 break-words text-sm text-muted">{entry.redacted_summary}</p>}
              {entry.action_hint && (
                <p className="mt-1.5 break-words text-xs text-brand">↳ {tr("inbox.learned_rule")}: {entry.action_hint}</p>
              )}

              <div className="mt-3 flex items-center gap-2">
                {(entry.status === "new" || entry.status === "error") && (
                  <>
                    <button onClick={() => { setErr(""); setApproveId(approveId === entry.id ? null : entry.id); }}
                      className={BUTTON.primary}>
                      {entry.status === "error" ? tr("inbox.approve_again") : tr("inbox.approve_dots")}
                    </button>
                    <button onClick={() => { setErr(""); reject.mutate(entry.id); }} disabled={reject.isPending}
                      className={BUTTON.secondary}>
                      {tr("inbox.discard")}
                    </button>
                  </>
                )}
                {entry.status === "approved" && <span className="text-sm text-muted">{tr("inbox.waiting_processed")}</span>}
                {entry.status === "running" && <span className="text-sm text-brand">{tr("inbox.assistant_working")}</span>}
                {(entry.result || entry.error) && (
                  <button onClick={() => setOpenId(expanded ? null : entry.id)}
                    className="ml-auto rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink">
                    {expanded ? tr("inbox.hide_details") : tr("inbox.details")}
                  </button>
                )}
              </div>

              {approveId === entry.id && (entry.status === "new" || entry.status === "error") && (
                <ApprovePanel item={entry} onDone={() => { setApproveId(null); inv(); }}
                  onError={(m) => setErr(m)} />
              )}

              {expanded && (entry.result || entry.error) && (
                <div className="mt-3 border-t border-line pt-3">
                  {entry.error && <div className="mb-2 rounded bg-red-500/10 px-2 py-1.5 text-sm text-red-400 whitespace-pre-wrap">{entry.error}</div>}
                  {entry.result && <div className="text-sm text-ink whitespace-pre-wrap">{entry.result}</div>}
                </div>
              )}
            </ListRow>
          );
        })}
        {!isLoading && items.length === 0 && (
          <ListingEmpty>{tr("inbox.nothing_here")}</ListingEmpty>
        )}
      </Listing>
      </Area>
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
    onSuccess: onDone, onError: (e) => onError(e instanceof ApiError ? e.message : tr("common.error")),
  });
  const mail = senderEmail(item.from);

  return (
    <div className="mt-3 space-y-3 rounded border border-line bg-surface p-3 text-sm">
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.scope")}</div>
        <div className="flex flex-wrap gap-1">
          {([
            ["once", tr("inbox.time_only")],
            ["sender", tr("inbox.always_sender", { sender: mail || tr("inbox.sender") })],
            ["category", tr("inbox.always_category_category", { category: item.category || "?" })],
          ] as ["once" | "sender" | "category", string][]).map(([s, l]) => (
            <button key={s} onClick={() => setScope(s)}
              className={`rounded border px-2 py-1 text-xs ${scope === s ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.processing")}</div>
        <div className="flex gap-1">
          {([["redacted", tr("inbox.redacted")], ["unredacted", tr("inbox.unredacted")]] as ["redacted" | "unredacted", string][]).map(([r, l]) => (
            <button key={r} onClick={() => setRedaction(r)}
              className={`rounded border px-2 py-1 text-xs ${redaction === r ? "border-brand bg-brand/15 text-brand" : "border-line text-muted hover:text-ink"}`}>
              {l}</button>
          ))}
        </div>
        {redaction === "unredacted" && (
          <p className="mt-1 text-xs text-amber-400">{tr("inbox.the_full_text_goes_to_the_assistant_unredacte")}</p>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-muted">{tr("inbox.learned_action_optional")}</div>
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder={tr("inbox.e_g_file_it_in_the_archive_and_document_it_in")}
          className="w-full rounded border border-line bg-card px-2 py-1.5 text-ink outline-none" />
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => approve.mutate()} disabled={approve.isPending}
          className={BUTTON.primary}>
          {tr(scope === "once" ? "inbox.approve" : "inbox.approve_remember")}
        </button>
        {scope !== "once" && <span className="text-xs text-muted">{tr("inbox.creates_rule")}</span>}
      </div>
    </div>
  );
}
