import { useState } from "react";
import { tr } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

type Status = {
  running_agents: number; update_pending: boolean; update_in_progress: boolean;
  last_update_completed_at: string | null;
};

const RECENT_MS = 10 * 60 * 1000; // „abgeschlossen" 10 Min lang anzeigen

export default function UpdateFooter() {
  const [dismissed, setDismissed] = useState<string>("");
  const { data } = useQuery({
    queryKey: ["admin-status"], queryFn: () => api.get<Status>("/admin/status"),
    refetchInterval: 5000,
  });
  if (!data) return null;

  const completedRecent = data.last_update_completed_at
    && Date.now() - new Date(data.last_update_completed_at).getTime() < RECENT_MS;

  let content: { cls: string; text: string; closable?: boolean } | null = null;
  if (data.update_in_progress) {
    content = { cls: "bg-brand/15 text-brand", text: `🔄 ${tr("update_footer.update_running_stack_being")}` };
  } else if (data.update_pending) {
    content = {
      cls: "bg-yellow-500/15 text-yellow-300",
      text: `⏳ ${tr("update_footer.update_queued_starts_once")}${
        data.running_agents > 0 ? ` (${tr("agents_badge.count_left", { count: data.running_agents })})` : ""}.`,
    };
  } else if (completedRecent && dismissed !== data.last_update_completed_at) {
    content = { cls: "bg-green-500/15 text-green-300", text: "✅ Update abgeschlossen.", closable: true };
  }
  if (!content) return null;

  return (
    <div className={`fixed bottom-0 left-0 right-0 z-20 flex items-center justify-center gap-3 border-t border-line px-4 py-2 text-sm ${content.cls}`}>
      <span>{content.text}</span>
      {content.closable && (
        <button onClick={() => setDismissed(data.last_update_completed_at || "")}
          className="text-xs opacity-70 hover:opacity-100">✕</button>
      )}
    </div>
  );
}
