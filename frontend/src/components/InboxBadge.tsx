import { Link } from "react-router-dom";
import { tr } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

// Assistant inbox link with a counter of unread (status "new") items.
export default function InboxBadge() {
  const { data = [] } = useQuery({
    queryKey: ["inbox"], queryFn: () => api.get<{ status: string }[]>("/assistant/inbox"),
    refetchInterval: 15000,
  });
  const nNew = data.filter((t) => t.status === "new").length;
  return (
    <Link to="/inbox" title={tr("inbox_badge.assistent_inbox")}
      className="relative flex h-10 min-w-[40px] items-center justify-center gap-1 text-muted hover:text-ink md:h-8 md:min-w-[32px]">
      <span>🗂️</span>
      {nNew > 0 && (
        <span className="rounded-full bg-brand px-1.5 text-xs font-medium text-white tabular-nums">{nNew}</span>
      )}
    </Link>
  );
}
