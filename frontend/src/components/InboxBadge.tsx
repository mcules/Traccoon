import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

// Assistent-Inbox-Link mit Zähler ungelesener (Status "new") Eingänge.
export default function InboxBadge() {
  const { data = [] } = useQuery({
    queryKey: ["inbox"], queryFn: () => api.get<{ status: string }[]>("/assistant/inbox"),
    refetchInterval: 15000,
  });
  const nNew = data.filter((t) => t.status === "new").length;
  return (
    <Link to="/inbox" title="Assistent-Inbox"
      className="relative flex items-center gap-1 text-muted hover:text-ink">
      <span>🗂️</span>
      {nNew > 0 && (
        <span className="rounded-full bg-brand px-1.5 text-xs font-medium text-white tabular-nums">{nNew}</span>
      )}
    </Link>
  );
}
