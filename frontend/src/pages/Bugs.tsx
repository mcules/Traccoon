import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
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
  /* Where an answer goes by mail, and whether it would go at all: an address without a
     mailbox behind it looks like a way and is none. */
  reply_email: string; mail_ready: boolean;
  /* Wie viele Einträge der Gegenseite ich noch nicht gesehen habe. */
  unread: number;
}

interface Post {
  id: number; body: string; author: string; internal: boolean;
  /* web · app · mail — which door this sentence came through. */
  via: string;
  images: { id: number; filename: string }[]; created_at: string | null;
}

interface Mailbox { id: number; name: string }

type Filter = "open" | "unread" | "unanswered" | "all";
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
  /* Die Startseite verlinkt auf einen bestimmten Ausschnitt ("neue Antworten",
     "unbeantwortet"). Ohne das käme man von dort immer auf derselben Liste heraus und
     müsste den Reiter selbst suchen. */
  const [params] = useSearchParams();
  const gewuenscht = params.get("state");
  const [filter, setFilter] = useState<Filter>(
    (["open", "unread", "unanswered", "all"] as const).includes(gewuenscht as Filter)
      ? (gewuenscht as Filter) : "open");
  const [art, setArt] = useState<"" | "bug" | "feature" | "question">("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [ticketFor, setTicketFor] = useState<Bug | null>(null);
  const [reporterFor, setReporterFor] = useState<Bug | null>(null);
  const [opening, setOpening] = useState(false);
  const [err, setErr] = useState("");

  const { data = [], isLoading } = useQuery({
    queryKey: ["bugs", filter, art],
    queryFn: () => api.get<Bug[]>("/bugs?"
      + [filter === "all" ? "" : `state=${filter}`, art ? `kind=${art}` : ""]
        .filter(Boolean).join("&")),
    refetchInterval: 30000,
  });
  /* Die Zahl für den Reiter. Eigene Abfrage, weil sie auch dann stimmen muss, wenn die
     Liste gerade nach Art gefiltert ist oder nur die offenen zeigt: eine Antwort auf eine
     längst abgehakte Meldung ist genau die, die sonst verloren geht. */
  const { data: warten } = useQuery({
    queryKey: ["bugs-waiting"],
    queryFn: () => api.get<{ unread_posts: number; unread_reports: number;
                             unanswered: number }>("/bugs/waiting"),
    refetchInterval: 30000,
  });
  const fresh = () => {
    qc.invalidateQueries({ queryKey: ["bugs"] });
    qc.invalidateQueries({ queryKey: ["bugs-waiting"] });
  };
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
            ["open", tr("bugs.filter_open")],
            ["unread", warten?.unread_reports
              ? tr("bugs.filter_unread_count", { count: warten.unread_reports })
              : tr("bugs.filter_unread")],
            ["unanswered", warten?.unanswered
              ? tr("bugs.filter_unanswered_count", { count: warten.unanswered })
              : tr("bugs.filter_unanswered")],
            ["all", tr("common.all")],
          ]} />
          <Tab active={art} onChoose={setArt} selection={[
            ["", tr("bugs.kind_any")], ["bug", tr("bugs.kind_bug")],
            ["feature", tr("bugs.kind_feature")], ["question", tr("bugs.kind_question")],
          ]} />
          <div className="flex-1" />
          <span className="text-xs text-muted">{tr("bugs.count", { count: data.length })}</span>
          <button className={BUTTON_SMALL.secondary} onClick={() => setOpening(true)}>
            {tr("bugs.open_one")}
          </button>
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
                  {/* Die Zahl steht vor dem Titel, nicht dahinter: gesucht wird die Zeile,
                      in der etwas Neues steht, nicht die mit dem längsten Titel. */}
                  {bug.unread > 0 && (
                    <Tag color="brand">{tr("bugs.unread", { count: bug.unread })}</Tag>
                  )}
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
                <div className={`text-ink ${bug.unread > 0 ? "font-semibold" : "font-medium"}`}>
                  {bug.title}
                </div>
                <div className="text-xs text-muted">
                  {bug.contact}
                  {/* The address is the difference between a report and a conversation, so
                      it stands in the line one reads without opening anything. */}
                  {bug.reply_email && ` · ✉ ${bug.reply_email}`}
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
                    <Thread bug={bug} onPosted={fresh} onError={failed} />
                    <div className="flex flex-wrap gap-2">
                      <button className={BUTTON_SMALL.secondary}
                        onClick={() => setReporterFor(bug)}>
                        {tr("bugs.reporter_edit")}
                      </button>
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
      {reporterFor && (
        <ReporterDialog bug={reporterFor} onClose={() => setReporterFor(null)}
          onDone={() => { setReporterFor(null); fresh(); }} onError={failed} />
      )}
      {opening && (
        <OpenDialog onClose={() => setOpening(false)}
          onDone={(id) => { setOpening(false); setOpenId(id); fresh(); }} onError={failed} />
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


/** The mailboxes of this person. Which one answers is normally the project's business; here
 * it is only needed for a conversation one opens oneself, which has no project. */
function useMailboxes() {
  return useQuery({
    queryKey: ["mail-accounts"],
    queryFn: () => api.get<Mailbox[]>("/mailbox/accounts"),
  });
}


/** Who the reporter is and how they are reached.
 *
 * Most reports arrive without an address — a callsign in the contact field, the mail address
 * somewhere in the third sentence. This is where it is typed in, and only then does an
 * answer travel. Under which address it goes out is decided by the project, not here. */
function ReporterDialog({ bug, onClose, onDone, onError }: {
  bug: Bug; onClose: () => void; onDone: () => void; onError: (e: unknown) => void;
}) {
  const [contact, setContact] = useState(bug.contact);
  const [email, setEmail] = useState(bug.reply_email);

  const save = useMutation({
    mutationFn: () => api.post(`/bugs/${bug.id}/reporter`, { contact, reply_email: email }),
    onSuccess: onDone, onError,
  });

  return (
    <Dialog title={tr("bugs.reporter_edit")} onClose={onClose} foot={
      <DialogFoot onCancel={onClose} onSave={() => save.mutate()}
        saveText={tr("common.save")} runs={save.isPending} />
    }>
      <Field label={tr("bugs.reporter")} hint={tr("bugs.reporter_hint")}>
        <input className={INPUT_VALUE} value={contact}
          onChange={(e) => setContact(e.target.value)} />
      </Field>
      <Field label={tr("bugs.reply_email")} hint={tr("bugs.reply_email_hint")}>
        <input className={INPUT_VALUE} value={email} placeholder="name@example.org"
          onChange={(e) => setEmail(e.target.value)} />
      </Field>
    </Dialog>
  );
}


/** A conversation whose first sentence is ours.
 *
 * The same thing as an incoming report, deliberately: everything around it (the thread, the
 * way to a ticket, the states) works on it without a second kind of thing. */
function OpenDialog({ onClose, onDone, onError }: {
  onClose: () => void; onDone: (id: number) => void; onError: (e: unknown) => void;
}) {
  const { data: mailboxes = [] } = useMailboxes();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("question");
  const [contact, setContact] = useState("");
  const [email, setEmail] = useState("");
  const [details, setDetails] = useState("");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [from, setFrom] = useState("");
  // A report opened here has no project to take the mailbox from, so the first one of one's
  // own stands in — with only one mailbox that is the answer to a question nobody asked.
  const box = accountId ?? mailboxes[0]?.id ?? null;

  const create = useMutation({
    mutationFn: () => api.post<Bug>("/bugs", {
      title, kind, details, contact, reply_email: email,
      account_id: box, mail_from: from,
    }),
    onSuccess: (bug) => onDone(bug.id), onError,
  });

  return (
    <Dialog title={tr("bugs.open_one")} onClose={onClose} hold foot={
      <DialogFoot onCancel={onClose} onSave={() => create.mutate()}
        saveText={tr("bugs.open_it")} runs={create.isPending} disabled={!title.trim()} />
    }>
      <Field label={tr("bugs.subject")}>
        <input className={INPUT_VALUE} value={title}
          onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label={tr("bugs.kind_any")}>
        <select className={INPUT_VALUE} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="question">{tr("bugs.kind_question")}</option>
          <option value="bug">{tr("bugs.kind_bug")}</option>
          <option value="feature">{tr("bugs.kind_feature")}</option>
        </select>
      </Field>
      <Field label={tr("bugs.reporter")} hint={tr("bugs.reporter_hint")}>
        <input className={INPUT_VALUE} value={contact}
          onChange={(e) => setContact(e.target.value)} />
      </Field>
      <Field label={tr("bugs.reply_email")} hint={tr("bugs.reply_email_hint")}>
        <input className={INPUT_VALUE} value={email} placeholder="name@example.org"
          onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label={tr("bugs.answering_mailbox")} hint={tr("bugs.answering_mailbox_hint")}>
        <select className={INPUT_VALUE} value={box ?? ""}
          onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">{tr("bugs.no_answering_mailbox")}</option>
          {mailboxes.map((one) => (
            <option key={one.id} value={one.id}>{one.name}</option>
          ))}
        </select>
      </Field>
      <Field label={tr("bugs.answering_address")} hint={tr("bugs.answering_address_hint")}>
        <input className={INPUT_VALUE} value={from} placeholder="reports@example.org"
          onChange={(e) => setFrom(e.target.value)} />
      </Field>
      <Field label={tr("bugs.what_is_it_about")} hint={tr("bugs.what_is_it_about_hint")}>
        <textarea className={INPUT_VALUE} rows={4} value={details}
          onChange={(e) => setDetails(e.target.value)} />
      </Field>
    </Dialog>
  );
}


/** What has been said about a report, and the field to add something to it.
 *
 * Internal notes stand in here as well, visibly set apart: they never go to the reporter,
 * and that is exactly what one has to see while writing, not hope for afterwards. */
function Thread({ bug, onPosted, onError }: {
  bug: Bug; onPosted: () => void; onError: (e: unknown) => void;
}) {
  const bugId = bug.id;
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [internal, setInternal] = useState(false);
  const [drafted, setDrafted] = useState("");
  /* Was dem Agenten zur Überarbeitung gesagt wurde, älteste zuerst. Sie bleiben stehen,
     bis die Antwort raus ist: man sieht, was man schon verlangt hat, und muss es nicht
     jede Runde neu tippen. */
  const [comments, setComments] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const { data: posts = [] } = useQuery({
    queryKey: ["bug-posts", bugId],
    // Das Lesen setzt drüben den Lesestand dieser Person. Deshalb muss danach die Liste
    // neu gezogen werden, sonst steht die Markierung noch da, obwohl man gerade hinsieht.
    queryFn: async () => {
      const rows = await api.get<Post[]>(`/bugs/${bugId}/posts`);
      if (bug.unread > 0) onPosted();
      return rows;
    },
  });

  /* Vorformulieren und überarbeiten sind dasselbe Tor: was im Feld steht, geht als Entwurf
     mit, die Anmerkungen sagen, was daran anders werden soll. Das Ergebnis landet im Feld
     und nirgendwo sonst — abgeschickt wird mit demselben Knopf wie ein selbst getippter
     Satz, und den drückt ein Mensch. */
  const propose = useMutation({
    mutationFn: (nachtrag: string) => {
      const alle = [...comments, ...(nachtrag.trim() ? [nachtrag.trim()] : [])];
      return api.post<{ text: string; agent: string }>(`/bugs/${bugId}/draft`,
        { draft: text, comments: alle }).then((draft) => ({ draft, alle }));
    },
    onSuccess: ({ draft, alle }) => {
      setText(draft.text);
      setDrafted(draft.agent);
      setComments(alle);
      setComment("");
    },
    onError,
  });

  const say = useMutation({
    mutationFn: async () => {
      const post = await api.post<Post>(`/bugs/${bugId}/posts`, { body: text, internal });
      return post;
    },
    onSuccess: () => {
      setText("");
      setDrafted("");
      setComments([]);
      setComment("");
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
            {post.via === "mail" && <Tag color="blue">{tr("bugs.via_mail")}</Tag>}
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

      {/* Where this answer travels to — before it is written, not afterwards. An answer
          that only lands in the list is the thing this whole way exists against. */}
      {!internal && (
        <div className="text-xs text-muted">
          {bug.mail_ready
            ? tr("bugs.answer_goes_by_mail", { address: bug.reply_email })
            : bug.reply_email
              ? tr("bugs.no_answering_address_yet")
              : tr("bugs.only_in_the_program")}
        </div>
      )}
      <textarea className={INPUT_VALUE} rows={drafted ? 6 : 2} value={text}
        placeholder={tr("bugs.answer_placeholder")}
        onChange={(e) => { setText(e.target.value); }} />
      {drafted && (
        /* Who wrote it, and that nothing has happened yet. A proposal one takes for an answer
           without noticing is the mistake this line exists against. */
        <div className="text-xs text-muted">{tr("bugs.draft_by", { agent: drafted })}</div>
      )}

      {!internal && (drafted || comments.length > 0) && (
        <div className="space-y-1.5 rounded border border-line bg-surface/50 p-2">
          {comments.length > 0 && (
            /* Was schon gesagt wurde, zum Nachlesen und zum Wegnehmen: eine Anmerkung, die
               man zurücknimmt, muss aus der Runde verschwinden, sonst zieht sie den
               nächsten Entwurf weiter in die alte Richtung. */
            <div className="flex flex-wrap gap-1.5">
              {comments.map((one, i) => (
                <button key={`${one}-${i}`} className={BUTTON_SMALL.secondary}
                  title={tr("bugs.comment_drop")}
                  onClick={() => setComments(comments.filter((_, k) => k !== i))}>
                  {one} ×
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <input className={INPUT_VALUE} value={comment}
              placeholder={tr("bugs.comment_placeholder")}
              onChange={(e) => setComment(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (comment.trim() || comments.length)) {
                  e.preventDefault();
                  propose.mutate(comment);
                }
              }} />
            <button className={BUTTON_SMALL.secondary}
              disabled={propose.isPending || (!comment.trim() && !comments.length)}
              onClick={() => propose.mutate(comment)}>
              {propose.isPending ? tr("bugs.drafting") : tr("bugs.revise")}
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={internal}
            onChange={(e) => setInternal(e.target.checked)} />
          {tr("bugs.internal_hint")}
        </label>
        <div className="flex-1" />
        {!internal && (
          <button className={BUTTON_SMALL.secondary} disabled={propose.isPending}
            title={tr("bugs.draft_hint")} onClick={() => propose.mutate("")}>
            {propose.isPending ? tr("bugs.drafting")
              : text.trim() ? tr("bugs.revise") : tr("bugs.draft_it")}
          </button>
        )}
        <button className={BUTTON_SMALL.secondary} disabled={!text.trim() || say.isPending}
          onClick={() => say.mutate()}>
          {internal ? tr("bugs.note_it") : tr("bugs.answer")}
        </button>
      </div>
    </div>
  );
}
