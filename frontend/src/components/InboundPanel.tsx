import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { tr } from "../i18n";
import { formatDateTime } from "../lib/formatTime";
import {
  Actions, Area, Dialog, Errorrow, ICON, IconButton, Listing, ListingEmpty, ListRow, Tab, Tag,
} from "./ui";

/**
 * What arrived from outside, and what became of it.
 *
 * This list is the other half of the promise the inbox makes. Storing every delivery is
 * worth nothing if a stuck one is invisible: it would be kept forever and looked at never,
 * which from where the sender stands is the same as losing it. So a parked delivery says
 * loudly what it is, keeps its payload readable, and can be sent on its way again with one
 * click once the reason is gone.
 */
interface Delivery {
  id: number; channel: string; target: string; route: string;
  status: string; attempts: number; last_error: string; outcome: string;
  received_at: string; next_try_at: string | null; finished_at: string | null; size: number;
}

type TagColor = "neutral" | "green" | "yellow" | "red" | "blue";
/* Four states, and only one of them wants attention. "dropped" is deliberately quiet: a
   filtered event or a repeat is a correct outcome, not a mishap. */
const STATE: Record<string, { label: string; color: TagColor }> = {
  new: { label: "inbound.state_waiting", color: "blue" },
  done: { label: "inbound.state_done", color: "green" },
  dropped: { label: "inbound.state_dropped", color: "neutral" },
  parked: { label: "inbound.state_parked", color: "red" },
};

export default function InboundPanel() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [err, setErr] = useState("");
  const [look, setLook] = useState<Delivery | null>(null);

  const { data = [], isLoading } = useQuery({
    queryKey: ["inbound", filter],
    queryFn: () => api.get<Delivery[]>(
      "/inbound" + (filter === "open" ? "?status=new,parked" : "")),
    refetchInterval: 15000,
  });
  const failed = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const again = useMutation({
    mutationFn: (id: number) => api.post(`/inbound/${id}/retry`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbound"] }),
    onError: failed,
  });

  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-1 text-lg font-semibold">{tr("inbound.title")}</h1>
      <p className="mb-4 text-sm text-muted">{tr("inbound.intro")}</p>

      <Area tools={<>
        <Tab active={filter} onChoose={setFilter} selection={[
          ["open", tr("inbound.filter_open")], ["all", tr("common.all")],
        ]} />
        <div className="flex-1" />
        <span className="text-xs text-muted">{data.length}</span>
      </>}>
        <Errorrow text={err} />
        {isLoading && <div className="text-sm text-muted">{tr("common.loading")}</div>}
        <Listing>
          {data.map((d) => {
            const state = STATE[d.status] || { label: d.status, color: "neutral" as const };
            return (
              <ListRow key={d.id} onClick={() => setLook(d)}>
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <Tag color={state.color}>{tr(state.label)}</Tag>
                  <span className="font-medium text-ink">{d.route || d.target}</span>
                  {d.attempts > 1 && (
                    <Tag>{tr("inbound.n_attempts", { n: d.attempts })}</Tag>
                  )}
                  <span className="ml-auto text-xs text-muted">
                    {formatDateTime(d.received_at)}
                  </span>
                </div>
                {d.last_error && <p className="text-xs text-red-400">{d.last_error}</p>}
                {!d.last_error && d.outcome && (
                  <p className="truncate text-xs text-muted">{d.outcome}</p>
                )}
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-xs text-muted">
                    {tr("inbound.size", { n: d.size })}
                    {d.next_try_at && ` · ${tr("inbound.next_try",
                      { when: formatDateTime(d.next_try_at) })}`}
                  </span>
                  <div className="flex-1" />
                  <Actions>
                    <IconButton icon="↻" title={tr("inbound.retry")}
                      disabled={again.isPending || d.status === "done"}
                      onClick={() => again.mutate(d.id)} />
                    <IconButton icon={ICON.open} title={tr("inbound.show_payload")}
                      onClick={() => setLook(d)} />
                  </Actions>
                </div>
              </ListRow>
            );
          })}
          {!isLoading && data.length === 0 && (
            <ListingEmpty>{tr("inbound.nothing_here")}</ListingEmpty>
          )}
        </Listing>
      </Area>

      {look && <Payload delivery={look} onClose={() => setLook(null)} />}
    </div>
  );
}

/** What actually stood in it, the question one has about a stuck delivery. */
function Payload({ delivery, onClose }: { delivery: Delivery; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["inbound-body", delivery.id],
    queryFn: () => api.get<{ headers: Record<string, string>; body: string }>(
      `/inbound/${delivery.id}/body`),
  });
  return (
    <Dialog huge title={`${delivery.route || delivery.target} · ${formatDateTime(delivery.received_at)}`}
      onClose={onClose}>
      <div className="space-y-3">
        {delivery.last_error && (
          <p className="text-sm text-red-400">{delivery.last_error}</p>
        )}
        {delivery.outcome && <p className="text-sm text-muted">{delivery.outcome}</p>}
        <details>
          <summary className="cursor-pointer text-xs text-muted">{tr("inbound.headers")}</summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-surface p-2 font-mono text-[11px] text-muted">
            {Object.entries(data?.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}
          </pre>
        </details>
        <pre className="max-h-96 overflow-auto rounded bg-surface p-2 font-mono text-[11px] leading-tight text-ink">
          {data?.body ?? tr("common.loading")}
        </pre>
      </div>
    </Dialog>
  );
}
