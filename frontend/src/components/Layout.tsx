import { ReactNode, useEffect, useState } from "react";
import { tr } from "../i18n";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, Project } from "../api";
import { useAuth } from "../auth";
import { useChrome, type ChromeTab } from "../pageChrome";
import { primaryNavigation, isArea, RAIL_WIDTH, type NavEntry } from "../nav";
import { pluginNav, usePlugins } from "../plugins";
import NotificationBell from "./NotificationBell";
import AgentsBadge from "./AgentsBadge";
import UpdateFooter from "./UpdateFooter";

// Project title (name plus subtitle), at the same time a quick switcher. On project pages it
// shows the current project; otherwise a compact "projects ▾". A click opens the project list.
function ProjectSwitcher() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const loc = useLocation();
  const curKey = loc.pathname.match(/^\/projects\/([^/]+)/)?.[1];
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const cur = projects?.find((p) => p.key === curKey);

  return (
    <div className="relative min-w-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex min-h-[40px] min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-left hover:bg-surface md:min-h-0"
        title={cur?.name}
      >
        <div className="min-w-0">
          {cur ? (
            <>
              <div className="truncate text-sm font-semibold leading-tight text-ink">{cur.name}</div>
              {cur.description && (
                <div className="max-w-[46vw] truncate text-[11px] leading-tight text-muted sm:max-w-[260px]">
                  {cur.description}
                </div>
              )}
            </>
          ) : (
            <span className="text-sm text-muted">{tr("layout.projects")}</span>
          )}
        </div>
        <span className="shrink-0 text-xs text-muted">▾</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-30 mt-2 max-h-96 w-64 overflow-y-auto rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            {(projects?.length ?? 0) === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted">{tr("layout.no_projects")}</div>
            )}
            {projects?.map((p) => (
              <button
                key={p.key}
                onClick={() => { navigate("/projects/" + p.key); setOpen(false); }}
                className={`block w-full truncate rounded px-2 py-1.5 text-left hover:bg-surface ${
                  p.key === curKey ? "bg-surface text-ink" : "text-ink"
                }`}
                title={p.name}
              >
                <span className="text-muted">{p.key}</span> {p.name}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** Waiting items of the assistant inbox, as a number on the navigation entry. */
function useInboxCounter(): number {
  const { data = [] } = useQuery({
    queryKey: ["inbox"], queryFn: () => api.get<{ status: string }[]>("/assistant/inbox"),
    refetchInterval: 15000,
  });
  return data.filter((t) => t.status === "new").length;
}

/**
 * The personal channel: the mailbox reports by itself.
 *
 * The counterpart to `mail_watch` in the backend — there a watcher keeps the IMAP connection
 * open (IDLE), here the UI listens. Only THAT something has happened is reported; the state the
 * usual queries fetch. Two sources for the same number would be one too many.
 *
 * The channel hangs on the layout and not on the mail page: the counter in the bar is
 * everywhere, so the report has to arrive everywhere too.
 */
function useMailPush(): void {
  const qc = useQueryClient();
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const address = `${location.origin.replace(/^http/, "ws")}/api/ws/me`
      + `?token=${encodeURIComponent(token)}`;
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let to = false;

    const join = () => {
      if (to) return;
      ws = new WebSocket(address);
      ws.onmessage = (e) => {
        try {
          if (JSON.parse(e.data)?.type !== "mail") return;
        } catch { return; }
        qc.invalidateQueries({ queryKey: ["mail-unread"] });
        qc.invalidateQueries({ queryKey: ["mail-folders"] });
        qc.invalidateQueries({ queryKey: ["mail-list"] });
      };
      // A torn channel is the normal case (sleep, a network change, a restart of the
      // backend). Without a rebuild the UI would be silent afterwards and nobody would know
      // warum nichts mehr kommt.
      ws.onclose = () => { if (!to) retry = window.setTimeout(join, 5000); };
    };
    join();

    return () => {
      to = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, [qc]);
}

/**
 * Unread mail across all mailboxes.
 *
 * Asked for less often than the assistant inbox: behind it sits an IMAP connection per
 * mailbox, and whoever wants to see new mail to the second has the tab open anyway
 * offen.
 */
function useMailCounter(): number {
  const { data } = useQuery({
    queryKey: ["mail-unread"],
    queryFn: () => api.get<{ total: number }>("/mailbox/unread"),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  });
  return data?.total ?? 0;
}

/**
 * The areas as a narrow rail on the left, from the medium width on.
 *
 * A rail and not a bar in the header: the header already carries the project (title,
 * switcher, badges), and an area navigation that has to share the row with it is the first
 * thing to be squeezed out. On the left it keeps its place at every width, and above all it
 * is *visible*, which the old list behind the avatar was not.
 */
function AreaRail() {
  const { user } = useAuth();
  const loc = useLocation();
  const waiting = useInboxCounter();
  const newMails = useMailCounter();
  useMailPush();
  const entries = primaryNavigation(user?.global_role === "admin", pluginNav(usePlugins()));

  return (
    <nav className={`sticky top-0 hidden h-screen ${RAIL_WIDTH} shrink-0 flex-col items-center gap-1 border-r border-line bg-card py-3 md:flex`}>
      <Link to="/" title={tr("layout.traccoon_start")} className="mb-2 text-2xl">🦝</Link>
      {entries.map((e) => (
        <RailsButton key={e.key} entry={e} active={isArea(loc.pathname + loc.hash, e.to)}
          counter={e.counter === "inbox" ? waiting : e.counter === "mail" ? newMails : 0} />
      ))}
    </nav>
  );
}

function RailsButton({ entry: entry, active, counter: counter }: {
  entry: NavEntry; active: boolean; counter: number;
}) {
  return (
    <Link
      to={entry.to}
      title={entry.label}
      className={`relative flex w-[60px] flex-col items-center gap-0.5 rounded-lg px-1 py-2 ${
        active ? "bg-surface text-ink" : "text-muted hover:bg-surface hover:text-ink"
      }`}
    >
      <span className="text-lg leading-none">{entry.icon}</span>
      <span className="w-full truncate text-center text-[10px] leading-tight">{entry.label}</span>
      {counter > 0 && (
        <span className="absolute right-1 top-1 rounded-full bg-brand px-1 text-[10px] font-medium text-white tabular-nums">
          {counter}
        </span>
      )}
    </Link>
  );
}

/**
 * The same areas on a phone, out of the same list.
 *
 * Formerly a second, hand written list stood here that had drifted against the one behind
 * the avatar (different order, the office missing there, the inbox missing in both).
 */
function MobileMenu() {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const loc = useLocation();
  const waiting = useInboxCounter();
  const newMails = useMailCounter();
  const entries = primaryNavigation(user?.global_role === "admin", pluginNav(usePlugins()));
  const close = () => setOpen(false);

  return (
    <div className="md:hidden">
      <button onClick={() => setOpen((v) => !v)} aria-label={tr("layout.menu")} title={tr("layout.menu")}
        className="relative flex h-10 w-10 items-center justify-center rounded-md border border-line bg-surface text-lg leading-none text-ink hover:bg-card">
        ☰
        {waiting > 0 && <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-brand" />}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={close} />
          <div className="absolute inset-x-2 top-full z-40 mt-1 max-h-[80vh] overflow-y-auto rounded-lg border border-line bg-card p-2 shadow-2xl">
            {entries.map((e) => (
              <Link key={e.key} to={e.to} onClick={close}
                className={`flex items-center gap-2 rounded px-2 py-2 text-sm ${
                  isArea(loc.pathname + loc.hash, e.to) ? "bg-surface text-ink" : "text-ink hover:bg-surface"
                }`}>
                <span>{e.icon}</span>
                <span className="flex-1">{e.label}</span>
                {((e.counter === "inbox" && waiting > 0) || (e.counter === "mail" && newMails > 0)) && (
                  <span className="rounded-full bg-brand px-1.5 text-xs text-white tabular-nums">
                    {e.counter === "inbox" ? waiting : newMails}
                  </span>
                )}
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** Only what really belongs to the person: the account and the way out. */
function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const name = user?.display_name || user?.username || "";
  const initials = name.trim().slice(0, 2).toUpperCase() || "?";

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={name}
        className="flex h-10 w-10 items-center justify-center rounded-full border border-line bg-surface text-xs font-medium text-ink hover:bg-card md:h-8 md:w-8"
      >
        {initials}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-2 w-48 rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            <div className="truncate px-2 py-1.5 text-xs text-muted">{name}</div>
            <div className="my-1 border-t border-line" />
            <Link to="/account" onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.account")}</Link>
            <button onClick={logout}
              className="block w-full rounded px-2 py-1.5 text-left text-ink hover:bg-surface">{tr("layout.log")}</button>
          </div>
        </>
      )}
    </div>
  );
}

export default function Layout({ children }: { children: ReactNode }) {
  const { chrome } = useChrome();
  const loc = useLocation();
  const current = loc.pathname + loc.search;
  const isActive = (t: ChromeTab) => chrome.active ? t.key === chrome.active
    : (loc.pathname === t.to || current === t.to);
  const onProjectPage = /^\/projects\//.test(loc.pathname);
  const sideways = chrome.layout === "seite" && chrome.tabs.length > 0;

  return (
    <div className="flex min-h-full">
      <AreaRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-card px-3 py-2 sm:gap-3 sm:px-5 relative">
          {/* Links: Menü (mobil) + Projekt-Titel/Switcher bzw. Seitentitel */}
          <div className="flex min-w-0 shrink items-center gap-2 sm:gap-3">
            <MobileMenu />
            {!onProjectPage && chrome.title && (
              <span className="truncate font-semibold text-ink">{chrome.title}</span>
            )}
            {/* Der Wechsler ist auf einer projektlosen Seite am Handy nur Platzverbrauch —
                that is where the page title stands, and the project list hangs in the menu. */}
            <div className={!onProjectPage && chrome.title ? "hidden sm:block" : ""}>
              <ProjectSwitcher />
            </div>
          </div>

          <div className="flex-1" />

          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <div className="hidden sm:block"><AgentsBadge /></div>
            <NotificationBell />
            <UserMenu />
          </div>
        </header>
        {/* [&>*]:mx-auto zentriert begrenzte Seiten-Spalten; volle Breite bleibt unberührt. */}
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-3 [&>*]:mx-auto sm:p-5">
          {sideways ? (
            <div className="flex flex-col gap-4 md:flex-row md:gap-6">
              <PagesNavigation tabs={chrome.tabs} active={isActive} sideways />
              <div className="min-w-0 flex-1">{children}</div>
            </div>
          ) : (
            <>
              <PagesNavigation tabs={chrome.tabs} active={isActive} />
              {children}
            </>
          )}
        </main>
        <UpdateFooter />
      </div>
    </div>
  );
}

/**
 * The tabs of the current page, on the page.
 *
 * Formerly they stood in the header: a pill bar that scrolled sideways from eight tabs on
 * (settings, administration) and cut off the first and the active tab while doing so, and a
 * navigation one cannot see is none. On a phone they were hidden in the burger menu instead,
 * so in a second place in a second form.
 *
 * Two shapes since: a wrapping bar for a handful of entries, and beside the content for the
 * pages with eight or nine (settings, administration, project settings). Wrapping is fine
 * for five, but at nine the bar takes two lines and reads like a word cloud. Below `md`
 * both look the same, because there is no room for a column beside the content.
 */
function PagesNavigation({ tabs, active, sideways = false }: {
  tabs: ChromeTab[]; active: (t: ChromeTab) => boolean; sideways?: boolean;
}) {
  if (tabs.length === 0) return null;
  // No card of its own: below stand cards, and a navigation in the same frame as the content
  // reads as one more box instead of as the way between them. What carries it is a line — to
  // the side one on the right, above one below.
  const container = sideways
    ? "flex shrink-0 flex-wrap gap-1 md:w-52 md:flex-col md:flex-nowrap md:border-r md:border-line md:pr-3"
    : "mb-4 flex flex-wrap gap-1 border-b border-line pb-2";
  return (
    <nav className={container}>
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          to={tab.to}
          className={`flex min-h-[36px] items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors md:min-h-0 ${
            active(tab)
              ? "bg-brand/15 font-medium text-brand ring-1 ring-inset ring-brand/30"
              : "text-muted hover:bg-card hover:text-ink"
          }`}
        >
          {tab.icon && <span className="text-base leading-none">{tab.icon}</span>}
          <span>{tab.label}</span>
        </Link>
      ))}
    </nav>
  );
}
