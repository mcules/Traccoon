import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { tr } from "../i18n";
import { api, MailBoxCount, MailCounts, MyDashboard, RunState } from "../api";
import Onboarding from "../components/Onboarding";
import PluginTiles from "../components/PluginTiles";
import { useAuth } from "../auth";
import { formatTime } from "../lib/formatTime";
import { AssignedToMe, MySteps, NeedsMe, useMyWork } from "../components/MyWork";
import { Running, Stuck, useStuckFlows } from "../components/Operations";
import { Area, Breakdown, Figure, FigureTone } from "../components/ui";
import { usePageChrome } from "../pageChrome";

/**
 * The start page: what waits for *this* person, and what the machines are doing.
 *
 * It used to be the project list with the own work stuck on top. Two things in one place, and
 * the one that answers "what do I do now" stood above a grid that only answers "where do I
 * go". The list has moved to `/projects` — its own area, reachable from the rail — and the
 * start page keeps the parts that are about the person and the moment.
 *
 * The order is the order of urgency: the figures first, because they say in one line whether
 * anything is up at all; then what waits for me; then what is broken; then what is running by
 * itself; and last what is merely mine. Every block below the figures shows itself only when
 * it has content — a page of empty cards teaches nothing but where the cards are.
 *
 * Deliberately **no** project list: whoever wants to switch projects has the switcher in the
 * header on every page, and a second grid of the same tiles would only be one more way to the
 * same place.
 */
export default function Dashboard() {
  // Title without a sub-menu; otherwise the one of the last visited page would stay.
  usePageChrome(tr("nav.dashboard"), []);
  const { user } = useAuth();
  const { data } = useMyWork();
  const { data: flows } = useStuckFlows();
  const { waiting, inbox } = useInbox();

  const stats = data?.stats;
  const stuck = (flows?.length ?? 0) + (data?.job_errors.length ?? 0);
  const running = data?.running.length ?? 0;

  /**
   * Where the work lies, per figure.
   *
   * A number in the head of the page says how much, never where — and "7 waiting" over eight
   * projects is a question, not an answer. What the tooltip lists is the project, because
   * that is what one has to go into. Project-less work (the assistant, a job) gets its own
   * line instead of falling out of the count.
   */
  const byProject = (rows: { project_key?: string | null }[]) => {
    const tally = new Map<string, number>();
    for (const r of rows) {
      const key = r.project_key || tr("dash.no_project");
      tally.set(key, (tally.get(key) || 0) + 1);
    }
    return [...tally].map(([label, value]) => ({ label, value }));
  };

  return (
    <div className="space-y-4">
      <Onboarding />
      <Brakes state={data?.state} />

      {/* The head of the page: my own numbers, and beside them what the plugins report (the
          shield with its findings). The figures used to be five boxes of their own across the
          full width — five frames for five numbers, and no room left beside them for
          anything else; the plugin tiles stood at the foot, under three lists, where a count
          that wants to be seen is not. */}
      {/* Five columns, of which the work takes two: it carries five figures against three
          and one, and squeezed into a fifth of the row its words broke in the middle. The
          other four cards hold one line together — work, mailbox, reports, and what the
          plugins contribute — because the head of the page is meant to be read across in one
          go. Below `xl` the row is two columns wide and the work takes the whole first line.
          A second plugin tile pushes itself into the next row; four in a line is what fits,
          not a promise. */}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Area title={tr("dash.your_work")} subtitle={<Week data={data} />} span="md:col-span-2">
          <div className="grid grid-cols-3 gap-y-3 sm:grid-cols-5">
            <Figure bare label={tr("my_work.waiting")} value={stats?.action ?? 0}
              tone={stats?.action ? "wait" : "quiet"}
              note={data && <Breakdown title={tr("my_work.waiting")}
                rows={byProject(data.action)} />} />
            <Figure bare label={tr("my_work.assigned_me")} value={stats?.assigned ?? 0}
              note={data && <Breakdown title={tr("my_work.assigned_me")}
                rows={byProject(data.assigned)} />} />
            <Figure bare label={tr("my_work.running_now")} value={running}
              tone={running ? "run" : "quiet"} to="/office"
              note={data && <Breakdown title={tr("my_work.running_now")}
                rows={data.running.map((r) => ({
                  label: `${r.agent || "?"}${r.issue_key ? ` · ${r.issue_key}` : ""}`,
                  value: r.project_key || tr("dash.no_project"),
                }))} />} />
            <Figure bare label={tr("ops.stuck")} value={stuck}
              tone={stuck ? "bad" : "quiet"} to="/processes/operations"
              note={data && <Breakdown title={tr("ops.stuck")} rows={[
                ...byProject(flows || []),
                ...(data.job_errors.length
                  ? [{ label: tr("dash.jobs"), value: data.job_errors.length }] : []),
              ]} />} />
            <Figure bare label={tr("layout.inbox")} value={waiting}
              tone={waiting ? "brand" : "quiet"} to="/inbox"
              note={<Breakdown title={tr("layout.inbox")} rows={inbox} />} />
          </div>
        </Area>
        <MailCard />
        <ReportsCard />
        {user?.global_role === "admin" && <AuditCard />}
        <PluginTiles />
      </div>

      <NeedsMe />
      <MySteps />
      {/* Both answer "what are the machines doing" — one what stands, one what moves. A fixed
          pair: they keep their places whether or not they have something in them, otherwise
          the page rearranges itself under the reader every time an agent finishes. */}
      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
        <Stuck />
        <Running />
      </div>
      <AssignedToMe />
    </div>
  );
}

/**
 * Why nothing is moving — when that is the case.
 *
 * A quiet start page has two readings: there is nothing to do, or nothing can run. They look
 * exactly the same, and the difference has cost whole nights: a runner gone since the morning
 * looks like a calm day until somebody misses a result.
 *
 * Only shown when a brake is actually on. Three of them, in the order in which they bite.
 */
function Brakes({ state }: { state?: RunState }) {
  if (!state) return null;
  const bar = (tone: string, text: string, to?: string, link?: string) => (
    <div className={`flex flex-wrap items-center gap-x-2 rounded-lg border px-3 py-2 text-sm ${tone}`}>
      <span>{text}</span>
      {to && <Link to={to} className="underline underline-offset-2">{link}</Link>}
    </div>
  );
  return (
    <div className="space-y-2">
      {!state.runner && bar("border-red-500/40 bg-red-500/10 text-red-300",
        `⛔ ${tr("dash.runner_gone")}`)}
      {state.paused === "pause" && bar("border-amber-500/40 bg-amber-500/10 text-amber-300",
        `⏸ ${tr("dash.emergency_stop")}`, "/admin", tr("dash.to_maintenance"))}
      {state.paused === "update" && bar("border-brand/40 bg-brand/10 text-brand",
        `🔄 ${tr("dash.update_waiting")}`)}
      {state.shift_end && bar("border-line bg-surface text-muted",
        `🌙 ${tr("dash.shift_over")}`, "/account/agents", tr("dash.to_settings"))}
    </div>
  );
}

/**
 * The mailbox, in three numbers.
 *
 * Behind them sits an IMAP connection per mailbox, which is why they are asked for rarely
 * (and answered from the cache of the server in between). Unread for the spam folder, the
 * plain count for the drafts: a draft is never unread, it simply lies there and is
 * forgotten — which is the only reason to count it at all.
 */
function MailCard() {
  const { data } = useQuery({
    queryKey: ["mail-counts"],
    queryFn: () => api.get<MailCounts>("/mailbox/counts"),
    refetchInterval: 120_000,
    refetchOnWindowFocus: true,
    retry: false,
  });
  // Where not every mailbox answered, the number is an incomplete answer and says so.
  const partial = !!data && data.accounts < data.accounts_total;
  // Behind every figure: which mailbox contributes how much. With three mailboxes "12 spam"
  // otherwise leaves open which of them one has to go into — and the mailboxes belong under
  // one another, which is why this is a note and not a `title`.
  const per = (label: string, kind: keyof Omit<MailBoxCount, "name">) => {
    const boxes = data?.boxes || [];
    if (!boxes.length) return undefined;
    return (
      <Breakdown title={label}
        rows={boxes.map((b) => ({ label: b.name, value: b[kind] }))}
        foot={partial && (
          <span className="text-amber-300">
            {tr("dash.n_of_m_mailboxes", { n: data!.accounts, m: data!.accounts_total })}
          </span>
        )} />
    );
  };

  return (
    <Area title={tr("dash.mail")} subtitle={partial ? (
      <span className="font-sans text-amber-300">
        {tr("dash.n_of_m_mailboxes", { n: data!.accounts, m: data!.accounts_total })}
      </span>
    ) : undefined}>
      <div className="grid grid-cols-3">
        <Figure bare label={tr("dash.unread")} value={data?.unread ?? 0}
          note={per(tr("dash.unread"), "unread")}
          tone={data?.unread ? "brand" : "quiet"} to="/mail" />
        <Figure bare label={tr("mail.role_junk")} value={data?.spam ?? 0}
          note={per(tr("mail.role_junk"), "spam")}
          tone={data?.spam ? "wait" : "quiet"} to="/mail" />
        <Figure bare label={tr("mail.role_drafts")} value={data?.drafts ?? 0}
          note={per(tr("mail.role_drafts"), "drafts")} to="/mail" />
      </div>
    </Area>
  );
}

/**
 * What people reported, before anybody made work of it.
 *
 * The reports are the step before the ticket: somebody ran into something, wishes for
 * something or wants to know something, and until it is judged it lies on `/bugs` and
 * nowhere else. That was the whole problem — a page one has to remember to visit is a page
 * one visits after the third mail asking whether anybody read it.
 *
 * Three figures rather than one with the kinds behind it: the kind decides the hurry (three
 * broken things are an evening, three wishes are a quarter), and a hurry one has to hover
 * for is a hurry one reads too late. They fit — the mailbox carries three of them in a cell
 * of the same width.
 *
 * The three keep their places whether they have anything in them or not; a figure that moves
 * as it fills up cannot be read at a glance. A kind nobody set gets a fourth place beside
 * them, and only then, so that this box and the list it leads to stay the same number.
 */
const REPORT_KIND: { key: string; label: string; tone: FigureTone }[] = [
  // Short words, unlike the sentences the report page tags with ("Something is broken"):
  // there they stand in a row of their own, here in a column some 70 pixels wide.
  // The colour is the role: something broken is somebody who cannot use the program right
  // now, a question is somebody waiting for an answer, a wish waits without hurting.
  { key: "bug", label: "dash.reports_bugs", tone: "bad" },
  { key: "feature", label: "dash.reports_wishes", tone: "quiet" },
  { key: "question", label: "dash.reports_questions", tone: "wait" },
];

interface AuditOverview {
  open: Record<"critical" | "high" | "medium" | "low" | "info", number>;
  ignored: number; fixed: number; stacks: number;
  last_run: { started_at: string; configs: number } | null;
}

interface ReportCount {
  kind: string;
  count: number;
  apps: { app: string; count: number }[];
}

/**
 * The configuration audit, in three numbers.
 *
 * Only what one might have to act on: critical, high, medium. Low and info exist and are on
 * the page itself — on the start page they would be two more numbers that never mean
 * anything and take the room of the ones that do.
 */
function AuditCard() {
  const { data } = useQuery({
    queryKey: ["audit-overview"],
    queryFn: () => api.get<AuditOverview>("/agentshield/overview"),
    // The scan runs once a day. Asking every eight seconds would be asking the same question
    // a thousand times for one answer.
    refetchInterval: 300_000,
    retry: false,
  });
  const last = data?.last_run;
  // When the last run was goes UNDER the numbers, not beside the heading. Beside it, the line
  // wraps in a card this narrow and pushes the figures a row down — and then this one card's
  // numbers stand lower than those of the three next to it, which is the first thing one sees
  // when reading across the row.
  return (
    <Area title={tr("agentshield.title")}>
      <div className="grid grid-cols-3">
        {(["critical", "high", "medium"] as const).map((severity) => (
          <Figure bare key={severity} label={tr(`agentshield.sev_${severity}`)}
            value={data?.open[severity] ?? 0} to="/audit"
            tone={data?.open[severity] ? (severity === "medium" ? "wait" : "bad") : "quiet"} />
        ))}
      </div>
      {last && (
        <div className="mt-2 text-xs text-muted">
          {tr("agentshield.stacks_affected", { when: formatTime(last.started_at),
                                               stacks: data?.stacks ?? 0 })}
        </div>
      )}
    </Area>
  );
}

function ReportsCard() {
  const { data = [] } = useQuery({
    queryKey: ["bug-summary"],
    queryFn: () => api.get<ReportCount[]>("/bugs/summary"),
    // Rarer than the rest of the page: a report comes in a few times a week, and the answer
    // sits behind a query over every open one of them.
    refetchInterval: 60_000,
  });
  const known = new Set(REPORT_KIND.map((k) => k.key));
  const rest = data.filter((r) => !known.has(r.kind));

  /** Behind the number: which program the reports came out of. Three broken things out of
   *  one program are one fault; out of three they are three. */
  const note = (label: string, rows: ReportCount[]) => {
    const tally = new Map<string, number>();
    for (const r of rows) {
      for (const a of r.apps) {
        const app = a.app || tr("dash.no_program");
        tally.set(app, (tally.get(app) || 0) + a.count);
      }
    }
    return <Breakdown title={label} rows={[...tally].map(([l, value]) => ({ label: l, value }))} />;
  };

  return (
    <Area title={tr("bugs.title")}>
      <div className="grid grid-cols-3">
        {REPORT_KIND.map(({ key, label, tone }) => {
          const found = data.find((r) => r.kind === key);
          return (
            <Figure bare key={key} label={tr(label)} value={found?.count ?? 0}
              tone={found?.count ? tone : "quiet"} to="/bugs"
              note={found && note(tr(label), [found])} />
          );
        })}
        {rest.length > 0 && (
          <Figure bare label={tr("dash.reports_other")}
            value={rest.reduce((sum, r) => sum + r.count, 0)} to="/bugs"
            note={note(tr("dash.reports_other"), rest)} />
        )}
      </div>
    </Area>
  );
}

/**
 * Waiting items of the assistant inbox — the same query the rail counts with.
 *
 * Plus what kind they are: a mail, a job, a report. The number says that something is
 * waiting, the kind says whether it is worth going in now.
 */
function useInbox(): { waiting: number; inbox: { label: string; value: number }[] } {
  const { data = [] } = useQuery({
    queryKey: ["inbox"],
    queryFn: () => api.get<{ status: string; kind: string }[]>("/assistant/inbox"),
    refetchInterval: 15000,
  });
  const open = data.filter((t) => t.status === "new");
  const tally = new Map<string, number>();
  for (const t of open) tally.set(t.kind || "?", (tally.get(t.kind || "?") || 0) + 1);
  return { waiting: open.length,
           inbox: [...tally].map(([label, value]) => ({ label, value })) };
}

/**
 * The week in one line: throughput and burn.
 *
 * Not a figure of its own — nothing about it needs doing, it is the balance one glances at,
 * so it stands as a line beside the heading of the work card. Tokens stand next to the money because a subscription run costs 0.00 dollars and still
 * happened; whoever reads only the money would think the machines had been idle.
 */
function Week({ data }: { data?: MyDashboard }) {
  // A week of nothing needs no line about it: three zeros say less than the space they take.
  if (!data || (!data.stats.done_7d && !data.costs.week.tokens)) return null;
  const { day, week } = data.costs;
  const num = (n: number) => new Intl.NumberFormat(undefined, {
    notation: "compact", maximumFractionDigits: 1,
  }).format(n);
  const money = (n: number) => `$${n < 10 ? n.toFixed(2) : Math.round(n)}`;

  return (
    <span className="font-sans">
      {tr("dash.last_7_days")}: {data.stats.done_7d} {tr("dash.done")} · {money(week.usd)} ·{" "}
      {num(week.tokens)} {tr("dash.tokens")} — {tr("dash.today")}: {money(day.usd)} ·{" "}
      {num(day.tokens)} {tr("dash.tokens")}
    </span>
  );
}
