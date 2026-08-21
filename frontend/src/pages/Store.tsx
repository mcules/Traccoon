import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatDateTime } from "../lib/formatTime";
import { tr } from "../i18n";
import { usePageChrome } from "../pageChrome";
import {
  Area, Errorrow, ICON, IconButton, Listing, ListingEmpty, ListenLine,
} from "../components/ui";
import Markdown from "../components/Markdown";

type Entry = { id: number; title: string; format: string; ts: string | null; body?: string };
type Store = { id: number; key: string; name: string; last_at: string | null };

/**
 * A store: what a flow has written into it bit by bit.
 *
 * The daily review used to lie in the output field of a job run — truncated, without a heading,
 * and the report about it pointed at a page that did not exist. Here it stands with its
 * history: the versions on the left, the selected one on the right.
 */
export default function StorePage() {
  const { key = "", id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState("");

  const { data: listing } = useQuery({
    queryKey: ["ablage", key],
    queryFn: () => api.get<{ storage: Store; entries: Entry[] }>(
      `/documents/${encodeURIComponent(key)}/entries`),
  });
  // Without a version in the address, the newest one: a link out of a report should point at
  // the state, not at a number that will be a different one tomorrow.
  const chosen = id ? Number(id) : listing?.entries?.[0]?.id;
  const { data: entry } = useQuery({
    queryKey: ["ablage-eintrag", key, chosen],
    queryFn: () => api.get<{ entry: Entry }>(
      `/documents/${encodeURIComponent(key)}/entries/${chosen}`),
    enabled: !!chosen,
  });

  const remove = useMutation({
    mutationFn: (eid: number) =>
      api.del(`/documents/${encodeURIComponent(key)}/entries/${eid}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["ablage", key] }); },
    onError: (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error")),
  });

  usePageChrome(listing?.storage?.name || key, [], "", "side");

  return (
    <Area hint={tr("store.versions_left_selected_one")}>
      <Errorrow text={err} />
      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Listing>
          {(listing?.entries || []).length === 0 && <ListingEmpty>{tr("store.storage_still_empty")}</ListingEmpty>}
          {(listing?.entries || []).map((e) => (
            <ListenLine key={e.id} columns="sm:grid-cols-[minmax(0,1fr)_auto]" dense
              onClick={() => nav(`/documents/${encodeURIComponent(key)}/${e.id}`)}>
              <div className="min-w-0">
                <div className={`truncate text-sm ${e.id === chosen ? "font-medium text-ink" : "text-muted"}`}>
                  {e.title || tr("store.no_heading")}
                </div>
                <div className="text-xs text-muted">{e.ts ? formatDateTime(e.ts) : ""}</div>
              </div>
              <div onClick={(ev) => ev.stopPropagation()}>
                <IconButton icon={ICON.remove} danger title={tr("common.delete")}
                  onClick={() => remove.mutate(e.id)} />
              </div>
            </ListenLine>
          ))}
        </Listing>

        <div className="rounded-lg border border-line bg-card p-4">
          {entry?.entry
            ? entry.entry.format === "markdown"
              ? <Markdown text={entry.entry.body || ""} />
              : <pre className="whitespace-pre-wrap text-sm text-ink">{entry.entry.body}</pre>
            : <div className="text-sm text-muted">{tr("store.no_version_selected")}</div>}
        </div>
      </div>
    </Area>
  );
}
