import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { formatDateTime } from "../lib/formatTime";
import { tr } from "../i18n";
import { usePageChrome } from "../pageChrome";
import {
  Area, Fehlerzeile, ICON, IconButton, Listing, ListingLeer, ListenLine,
} from "../components/ui";
import Markdown from "../components/Markdown";

type Entry = { id: number; title: string; format: string; ts: string | null; body?: string };
type Ablage = { id: number; key: string; name: string; last_at: string | null };

/**
 * Eine Ablage: was ein Ablauf hier nach und nach hineingeschrieben hat.
 *
 * Der tägliche Rückblick lag vorher im Ausgabefeld eines Job-Laufs — abgeschnitten, ohne
 * Überschrift, und die Meldung dazu verwies auf eine Seite, die es nicht gab. Hier steht er
 * mit seinem Verlauf: links die Fassungen, rechts die gewählte.
 */
export default function AblagePage() {
  const { key = "", id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState("");

  const { data: listing } = useQuery({
    queryKey: ["ablage", key],
    queryFn: () => api.get<{ storage: Ablage; entries: Entry[] }>(
      `/documents/${encodeURIComponent(key)}/entries`),
  });
  // Ohne Fassung in der Adresse die neueste: Ein Link aus einer Meldung soll auf den Stand
  // zeigen, nicht auf eine Nummer, die morgen eine andere ist.
  const gewaehlt = id ? Number(id) : listing?.entries?.[0]?.id;
  const { data: entry } = useQuery({
    queryKey: ["ablage-eintrag", key, gewaehlt],
    queryFn: () => api.get<{ entry: Entry }>(
      `/documents/${encodeURIComponent(key)}/entries/${gewaehlt}`),
    enabled: !!gewaehlt,
  });

  const remove = useMutation({
    mutationFn: (eid: number) =>
      api.del(`/documents/${encodeURIComponent(key)}/entries/${eid}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["ablage", key] }); },
    onError: (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  usePageChrome(listing?.storage?.name || key, [], "", "seite");

  return (
    <Area hinweis={tr("ablage.einleitung")}>
      <Fehlerzeile text={err} />
      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Listing>
          {(listing?.entries || []).length === 0 && <ListingLeer>{tr("ablage.leer")}</ListingLeer>}
          {(listing?.entries || []).map((e) => (
            <ListenLine key={e.id} spalten="sm:grid-cols-[minmax(0,1fr)_auto]" dicht
              onClick={() => nav(`/documents/${encodeURIComponent(key)}/${e.id}`)}>
              <div className="min-w-0">
                <div className={`truncate text-sm ${e.id === gewaehlt ? "font-medium text-ink" : "text-muted"}`}>
                  {e.title || tr("ablage.ohne_titel")}
                </div>
                <div className="text-xs text-muted">{e.ts ? formatDateTime(e.ts) : ""}</div>
              </div>
              <div onClick={(ev) => ev.stopPropagation()}>
                <IconButton icon={ICON.loeschen} gefahr titel={tr("common.loeschen")}
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
            : <div className="text-sm text-muted">{tr("ablage.nichts_gewaehlt")}</div>}
        </div>
      </div>
    </Area>
  );
}
