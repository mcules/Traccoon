import { useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

export default function NotificationBell() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data: count } = useQuery({
    queryKey: ["notif-count"], queryFn: () => api.get<{ count: number }>("/notifications/unread-count"),
    refetchInterval: 15000,
  });
  const { data: list } = useQuery({
    queryKey: ["notif-list"], queryFn: () => api.get<any[]>("/notifications"), enabled: open,
  });

  async function readAll() {
    await api.post("/notifications/read-all");
    qc.invalidateQueries({ queryKey: ["notif-count"] });
    qc.invalidateQueries({ queryKey: ["notif-list"] });
  }

  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} className="relative text-muted hover:text-ink" title={tr("notification_bell.benachrichtigungen")}>
        🔔
        {(count?.count || 0) > 0 && (
          <span className="absolute -right-2 -top-1 rounded-full bg-red-500 px-1 text-[10px] text-white">
            {count!.count}
          </span>
        )}
      </button>
      {open && (
        <>
        <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
        <div className="absolute right-0 top-8 z-30 max-h-96 w-80 overflow-y-auto rounded-lg border border-line bg-card p-2 shadow-lg">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-xs font-medium text-muted">{tr("notification_bell.benachrichtigungen")}</span>
            <button onClick={readAll} className="text-xs text-brand">alle gelesen</button>
          </div>
          {list?.map((n) => (
            <div key={n.id} className={`rounded p-2 text-sm ${n.read ? "opacity-60" : ""}`}>
              <div className="font-medium">{n.title}</div>
              <div className="text-xs text-muted">{n.body}</div>
            </div>
          ))}
          {list?.length === 0 && <div className="px-1 text-xs text-muted">{tr("notification_bell.nichts_neues")}</div>}
        </div>
        </>
      )}
    </div>
  );
}
