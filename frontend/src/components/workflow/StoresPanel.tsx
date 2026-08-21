import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { formatDateTime } from "../../lib/formatTime";
import { tr } from "../../i18n";
import { Area, Tag, Listing, ListingEmpty, ListenLine } from "../ui";

type Store = { id: number; key: string; name: string; description: string;
                keep: number; last_title: string; last_at: string | null; count: number | null };

/**
 * Stores: the texts flows have written.
 *
 * The counterpart to the metric series — numbers with a history there, texts with a history
 * here. Both answer the same question: what a flow worked out should outlast it.
 */
export default function StoresPanel() {
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["ablagen"],
                              queryFn: () => api.get<Store[]>("/documents") });
  const stores = data || [];

  return (
    <Area hint={tr("stores.texts_flows_written_history")}>
      <Listing>
        {stores.length === 0 && <ListingEmpty>{tr("stores.no_storage_yet_comes")}</ListingEmpty>}
        {stores.map((a) => (
          <ListenLine key={a.id} columns="sm:grid-cols-[minmax(0,1fr)_10rem_auto]"
            onClick={() => nav(`/documents/${encodeURIComponent(a.key)}`)}>
            <div className="min-w-0">
              <div className="truncate font-medium text-ink">{a.name || a.key}</div>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                <span className="truncate font-mono">{a.key}</span>
                {a.last_title && <><span className="text-line">·</span>
                  <span className="truncate">{a.last_title}</span></>}
              </div>
            </div>
            <span className="text-xs text-muted">
              {a.last_at ? formatDateTime(a.last_at) : "—"}
            </span>
            <Tag>{tr("stores.count_versions", { count: String(a.count ?? 0) })}</Tag>
          </ListenLine>
        ))}
      </Listing>
    </Area>
  );
}
