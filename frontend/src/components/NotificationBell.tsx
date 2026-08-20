import { useState } from "react";
import { Link } from "react-router-dom";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { KNOPF_TEXT } from "./ui";

type Meldung = {
  id: number; kind: string; title: string; body: string;
  issue_key: string; project_key: string; assistant_task_id: number | null;
  sent: boolean; read: boolean; created_at: string;
};

/**
 * The bell: what still wants something.
 *
 * It deliberately does not show everything. Whatever went out over the messenger has been
 * read there; repeating it here made the bell a second inbox with a red dot that could not
 * be worked off (400 of 420 rows). The server decides that (see `api/notifications.py`),
 * `?all=1` brings the history back for the "where was that message again" case.
 */
export default function NotificationBell() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [alle, setAlle] = useState(false);
  const { data: count } = useQuery({
    queryKey: ["notif-count"], queryFn: () => api.get<{ count: number }>("/notifications/unread-count"),
    refetchInterval: 15000,
  });
  const { data: list } = useQuery({
    queryKey: ["notif-list", alle],
    queryFn: () => api.get<Meldung[]>("/notifications" + (alle ? "?all=1" : "")),
    enabled: open,
  });

  const inv = () => {
    qc.invalidateQueries({ queryKey: ["notif-count"] });
    qc.invalidateQueries({ queryKey: ["notif-list"] });
  };

  async function readAll() {
    await api.post("/notifications/read-all");
    inv();
  }

  async function gelesen(n: Meldung) {
    if (n.read) return;
    await api.post(`/notifications/${n.id}/read`);
    inv();
  }

  // Where a card leads: to the ticket it belongs to, otherwise (assistant, spam) into the
  // inbox. Without a target it stays a plain row.
  const ziel = (n: Meldung): string | null =>
    n.issue_key && n.project_key ? `/projects/${n.project_key}/tickets/${n.issue_key}`
      : n.assistant_task_id ? "/inbox" : null;

  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} title={tr("notification_bell.benachrichtigungen")}
        className="relative flex h-10 w-10 items-center justify-center text-muted hover:text-ink md:h-8 md:w-8">
        🔔
        {(count?.count || 0) > 0 && (
          <span className="absolute -right-2 -top-1 rounded-full bg-red-500 px-1 text-[11px] text-white">
            {count!.count}
          </span>
        )}
      </button>
      {open && (
        <>
        <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
        <div className="absolute right-0 top-8 z-30 max-h-96 w-80 overflow-y-auto rounded-lg border border-line bg-card p-2 shadow-lg">
          <div className="mb-2 flex items-center justify-between gap-2 px-1">
            <span className="text-xs font-medium text-muted">
              {alle ? tr("notification_bell.verlauf") : tr("notification_bell.offen")}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={() => setAlle((v) => !v)} className={KNOPF_TEXT.neben}>
                {alle ? tr("notification_bell.nur_offene") : tr("notification_bell.alle_zeigen")}
              </button>
              <button onClick={readAll} className="text-xs text-brand">{tr("notification_bell.alle_gelesen")}</button>
            </div>
          </div>
          {list?.map((n) => {
            const to = ziel(n);
            const inhalt = (
              <>
                <div className="flex items-start gap-1.5">
                  <span className="flex-1 font-medium">{n.title}</span>
                  {/* Went out somewhere else and still standing here: this one is waiting. */}
                  {n.sent && !alle && <span className="shrink-0 text-[11px] text-amber-400">{tr("notification_bell.wartet")}</span>}
                </div>
                {n.body && <div className="line-clamp-3 text-xs text-muted">{n.body}</div>}
              </>
            );
            const cls = `block rounded p-2 text-sm hover:bg-surface ${n.read ? "opacity-60" : ""}`;
            return to ? (
              <Link key={n.id} to={to} className={cls}
                onClick={() => { setOpen(false); gelesen(n); }}>{inhalt}</Link>
            ) : (
              <div key={n.id} className={cls} onClick={() => gelesen(n)}>{inhalt}</div>
            );
          })}
          {list?.length === 0 && (
            <div className="px-1 text-xs text-muted">
              {alle ? tr("notification_bell.nichts_neues") : tr("notification_bell.nichts_offen")}
            </div>
          )}
        </div>
        </>
      )}
    </div>
  );
}
