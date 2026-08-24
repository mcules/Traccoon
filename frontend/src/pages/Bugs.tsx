import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, Project } from "../api";
import { tr } from "../i18n";
import { formatTime } from "../lib/formatTime";
import {
  Area, Button, BUTTON_SMALL, Dialog, DialogFoot, Errorrow, Field, INPUT_VALUE,
  Listing, ListingEmpty, ListRow, Tab, Tag,
} from "../components/ui";
import { usePageChrome } from "../pageChrome";

/**
 * Reports from outside, before anybody decided they are work.
 *
 * The page is deliberately an inbox and not a board: most reports never become a ticket
 * (three times the same thing, a misunderstanding, a fault of the device and not of the
 * program), and putting all of them on a board buries the board. Judging one is two clicks,
 * and only the third click, "make a ticket of it", creates work.
 */
interface Bug {
  id: number; title: string; status: string; kind: string; app: string; version: string;
  contact: string; environment: string; details: string; technical: string;
  ticket: string; project_id: number | null; created_at: string | null;
}

interface Post {
  id: number; body: string; author: string; internal: boolean;
  images: { id: number; filename: string }[]; created_at: string | null;
}

type TagColor = "neutral" | "green" | "yellow" | "red" | "blue" | "violet" | "brand";
/* The kind is no judgement, so it stays quiet - except "something is broken", which is
   exactly what the red role is for. */
const KIND: Record<string, { label: string; color: TagColor }> = {
  bug: { label: "bugs.kind_bug", color: "red" },
  feature: { label: "bugs.kind_feature", color: "neutral" },
  question: { label: "bugs.kind_question", color: "neutral" },
};
const STATE: Record<string, { label: string; color: TagColor }> = {
  new: { label: "bugs.state_new", color: "brand" },
  seen: { label: "bugs.state_seen", color: "blue" },
  ticket: { label: "bugs.state_ticket", color: "green" },
  rejected: { label: "bugs.state_rejected", color: "neutral" },
  duplicate: { label: "bugs.state_duplicate", color: "neutral" },
};

function art_of(bug: Bug) {
  return KIND[bug.kind] || KIND.bug;
}

export default function Bugs() {
  usePageChrome(tr("bugs.title"), []);
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"open" | "all">("open");
  const [art, setArt] = useState<"" | "bug" | "feature" | "question">("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [ticketFor, setTicketFor] = useState<Bug | null>(null);
  const [err, setErr] = useState("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["bugs", filter, art],
    queryFn: () => api.get<Bug[]>("/bugs?"
      + [filter === "open" ? "state=open" : "", art ? `kind=${art}` : ""].filter(Boolean).join("&")),
    refetchInterval: 30000,
  });
  const fresh = () => qc.invalidateQueries({ queryKey: ["bugs"] });
  const failed = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

  const judge = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      api.post(`/bugs/${id}/status`, { status }),
    onSuccess: fresh, onError: failed,
  });

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-1 text-lg font-semibold">{tr("bugs.title")}</h1>
      <p className="mb-4 text-sm text-muted">{tr("bugs.intro")}</p>

      <Area
        tools={<>
          <Tab active={filter} onChoose={setFilter} selection={[
            ["open", tr("bugs.filter_open")], ["all", tr("common.all")],
          ]} />
          <Tab active={art} onChoose={setArt} selection={[
            ["", tr("bugs.kind_any")], ["bug", tr("bugs.kind_bug")],
            ["feature", tr("bugs.kind_feature")], ["question", tr("bugs.kind_question")],
          ]} />
          <div className="flex-1" />
          <span className="text-xs text-muted">{tr("bugs.count", { count: data.length })}</span>
        </>}
      >
        <Errorrow text={err} />
        {isLoading && <div className="text-sm text-muted">{tr("common.loading")}</div>}
        <Listing>
          {data.map((bug) => {
            const state = STATE[bug.status] || { label: bug.status, color: "neutral" as const };
            const open = openId === bug.id;
            return (
              <ListRow key={bug.id} onClick={() => setOpenId(open ? null : bug.id)}>
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <Tag color={art_of(bug).color}>{tr(art_of(bug).label)}</Tag>
                  <Tag color={state.color}>{tr(state.label)}</Tag>
                  {bug.app && <Tag>{bug.app}</Tag>}
                  {bug.ticket && (
                    // The project key stands in front of the ticket key. Taking it from
                    // there and not from `project_id` is on purpose: a report may end up as
                    // a ticket in a different project than the one it came in for.
                    <Link to={`/projects/${bug.ticket.slice(0, bug.ticket.lastIndexOf("-"))}`
                      + `/tickets/${bug.ticket}`}
                      onClick={(e) => e.stopPropagation()}
                      title={tr("bugs.to_the_ticket")}>
                      <Tag color="green">{bug.ticket} ↗</Tag>
                    </Link>
                  )}
                </div>
                <div className="font-medium text-ink">{bug.title}</div>
                <div className="text-xs text-muted">
                  {bug.contact}
                  {bug.version && ` · ${bug.version}`}
                  {bug.created_at && ` · ${formatTime(bug.created_at)}`}
                </div>

                {open && (
                  <div className="mt-3 space-y-3" onClick={(e) => e.stopPropagation()}>
                    {bug.details && (
                      <p className="whitespace-pre-wrap text-sm text-ink">{bug.details}</p>
                    )}
                    {bug.environment && (
                      <p className="text-xs text-muted">{tr("bugs.environment")}: {bug.environment}</p>
                    )}
                    {bug.technical && (
                      <details>
                        <summary className="cursor-pointer text-xs text-muted">
                          {tr("bugs.attachment")}
                        </summary>
                        {/* The log is the reason these reports are worth anything, and it is
                            long: it gets its own scroll box instead of pushing the list apart. */}
                        <pre className="mt-1 max-h-72 overflow-auto rounded bg-surface p-2 font-mono text-[11px] leading-tight text-muted">
                          {bug.technical}
                        </pre>
                      </details>
                    )}
                    <Thread bugId={bug.id} onPosted={fresh} onError={failed} />
                    <div className="flex flex-wrap gap-2">
                      <Button variant="primary" onClick={() => setTicketFor(bug)}
                        disabled={!!bug.ticket}
                        title={bug.ticket ? tr("bugs.already_a_ticket", { key: bug.ticket }) : ""}>
                        {tr("bugs.make_ticket")}
                      </Button>
                      <button className={BUTTON_SMALL.secondary}
                        onClick={() => judge.mutate({ id: bug.id, status: "seen" })}>
                        {tr("bugs.mark_seen")}
                      </button>
                      <button className={BUTTON_SMALL.secondary}
                        onClick={() => judge.mutate({ id: bug.id, status: "duplicate" })}>
                        {tr("bugs.mark_duplicate")}
                      </button>
                      <button className={BUTTON_SMALL.danger}
                        onClick={() => judge.mutate({ id: bug.id, status: "rejected" })}>
                        {tr("bugs.mark_rejected")}
                      </button>
                    </div>
                  </div>
                )}
              </ListRow>
            );
          })}
          {data.length === 0 && !isLoading && (
            <ListingEmpty>{tr("bugs.nothing_here")}</ListingEmpty>
          )}
        </Listing>
      </Area>

      {ticketFor && (
        <TicketDialog bug={ticketFor} onClose={() => setTicketFor(null)}
          onDone={() => { setTicketFor(null); fresh(); }} onError={failed} />
      )}
    </div>
  );
}

/** Which project takes care of it, and under which heading. */
function TicketDialog({ bug, onClose, onDone, onError }: {
  bug: Bug; onClose: () => void; onDone: () => void; onError: (e: unknown) => void;
}) {
  const { data: projects = [] } = useQuery({
    queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects"),
  });
  const [projectId, setProjectId] = useState<number | null>(bug.project_id);
  const [summary, setSummary] = useState(bug.title);

  const create = useMutation({
    mutationFn: () => api.post(`/bugs/${bug.id}/ticket`, { project_id: projectId, summary }),
    onSuccess: onDone, onError,
  });

  return (
    <Dialog title={tr("bugs.make_ticket")} onClose={onClose} hold foot={
      <DialogFoot onCancel={onClose} onSave={() => create.mutate()}
        saveText={tr("bugs.create")} runs={create.isPending}
        disabled={!projectId || !summary.trim()} />
    }>
      <Field label={tr("bugs.which_project")} hint={tr("bugs.which_project_hint")}>
        <select className={INPUT_VALUE} value={projectId ?? ""}
          onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{tr("bugs.choose_project")}</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.key} · {p.name}</option>
          ))}
        </select>
      </Field>
      <Field label={tr("bugs.ticket_heading")} hint={tr("bugs.ticket_heading_hint")}>
        <input className={INPUT_VALUE} value={summary}
          onChange={(e) => setSummary(e.target.value)} />
      </Field>
    </Dialog>
  );
}


/** Was über eine Meldung gesagt wurde, und das Feld, um etwas dazuzusagen.
 *
 * Interne Notizen stehen hier mit, sichtbar abgesetzt: sie gehen nie an den Melder, und
 * genau das muss man beim Schreiben sehen können, nicht erst hinterher hoffen. */
function Thread({ bugId, onPosted, onError }: {
  bugId: number; onPosted: () => void; onError: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [internal, setInternal] = useState(false);
  const { data: posts = [] } = useQuery({
    queryKey: ["bug-posts", bugId], queryFn: () => api.get<Post[]>(`/bugs/${bugId}/posts`),
  });

  const say = useMutation({
    mutationFn: async () => {
      const post = await api.post<Post>(`/bugs/${bugId}/posts`, { body: text, internal });
      return post;
    },
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: ["bug-posts", bugId] });
      onPosted();
    },
    onError,
  });

  return (
    <div className="space-y-2">
      {posts.map((post) => (
        <div key={post.id}
          className={`rounded border-l-2 pl-2 ${post.internal
            ? "border-yellow-500/60 bg-yellow-500/5" : "border-line"}`}>
          <div className="flex items-center gap-1.5 text-xs text-muted">
            <span className="font-medium text-ink">{post.author}</span>
            {post.created_at && <span>{formatTime(post.created_at)}</span>}
            {post.internal && <Tag color="yellow">{tr("bugs.internal")}</Tag>}
          </div>
          <p className="whitespace-pre-wrap text-sm text-ink">{post.body}</p>
          {post.images.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-2">
              {post.images.map((img) => (
                <a key={img.id} href={`/api/bugs/images/${img.id}`} target="_blank"
                  rel="noreferrer" className="text-xs text-brand underline">
                  {img.filename}
                </a>
              ))}
            </div>
          )}
        </div>
      ))}

      <textarea className={INPUT_VALUE} rows={2} value={text}
        placeholder={tr("bugs.answer_placeholder")}
        onChange={(e) => setText(e.target.value)} />
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={internal}
            onChange={(e) => setInternal(e.target.checked)} />
          {tr("bugs.internal_hint")}
        </label>
        <div className="flex-1" />
        <button className={BUTTON_SMALL.secondary} disabled={!text.trim() || say.isPending}
          onClick={() => say.mutate()}>
          {internal ? tr("bugs.note_it") : tr("bugs.answer")}
        </button>
      </div>
    </div>
  );
}
