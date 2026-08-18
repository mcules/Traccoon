import { ReactNode, useState } from "react";
import { tr } from "../i18n";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Project } from "../api";
import { useAuth } from "../auth";
import { useChrome, type ChromeTab } from "../pageChrome";
import NotificationBell from "./NotificationBell";
import AgentsBadge from "./AgentsBadge";
import InboxBadge from "./InboxBadge";
import UpdateFooter from "./UpdateFooter";

// Projekt-Titel (Name + Untertitel) — zugleich Schnellwechsler. Auf Projektseiten zeigt er das
// aktuelle Projekt; sonst kompakt „Projekte ▾". Klick öffnet die Projektliste.
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
            <span className="text-sm text-muted">{tr("layout.projekte")}</span>
          )}
        </div>
        <span className="shrink-0 text-xs text-muted">▾</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-30 mt-2 max-h-96 w-64 overflow-y-auto rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            {(projects?.length ?? 0) === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted">{tr("layout.keine_projekte")}</div>
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

function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const isAdmin = user?.global_role === "admin";
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
            <Link to="/profil" onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.profil")}</Link>
            <Link to="/settings" onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.einstellungen")}</Link>
            <Link to="/processes" onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.prozesse")}</Link>
            {/* Die Pillenleiste gehört der jeweiligen Seite — eine globale Ansicht hat dort
                keinen Platz. Das Büro steht deshalb hier, neben „Prozesse": beides sind
                projektübergreifende Seiten, keine Einstellungen. */}
            <Link to="/buero" onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.buero")}</Link>
            {isAdmin && (
              <Link to="/admin" onClick={() => setOpen(false)}
                className="block rounded px-2 py-1.5 text-ink hover:bg-surface">{tr("layout.admin")}</Link>
            )}
            <div className="my-1 border-t border-line" />
            <button onClick={logout}
              className="block w-full rounded px-2 py-1.5 text-left text-ink hover:bg-surface">{tr("layout.abmelden")}</button>
          </div>
        </>
      )}
    </div>
  );
}

// Mobiles Burger-Menü: die Sprünge zwischen den großen Bereichen. Die Reiter der jeweiligen
// Seite stehen nicht mehr hier, sondern auf der Seite selbst — sie waren sonst zweimal
// vorhanden, an zwei verschiedenen Stellen, mit zwei verschiedenen Darstellungen.
function MobileMenu() {
  const [open, setOpen] = useState(false);
  const { user, logout } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const close = () => setOpen(false);
  const item = "flex items-center gap-2 rounded px-2 py-2 text-sm";
  return (
    <div className="md:hidden">
      <button onClick={() => setOpen((v) => !v)} aria-label={tr("layout.menue")} title={tr("layout.menue")}
        className="flex h-10 w-10 items-center justify-center rounded-md border border-line bg-surface text-lg leading-none text-ink hover:bg-card md:h-8 md:w-8">
        ☰
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={close} />
          <div className="absolute inset-x-2 top-full z-40 mt-1 max-h-[80vh] overflow-y-auto rounded-lg border border-line bg-card p-2 shadow-2xl">
            <Link to="/" onClick={close} className={`${item} text-ink hover:bg-surface`}>🦝 <span>{tr("layout.projekte")}</span></Link>
            <Link to="/inbox" onClick={close} className={`${item} text-ink hover:bg-surface`}>📥 <span>{tr("layout.inbox")}</span></Link>
            <Link to="/processes" onClick={close} className={`${item} text-ink hover:bg-surface`}>🔀 <span>{tr("layout.prozesse")}</span></Link>
            <Link to="/buero" onClick={close} className={`${item} text-ink hover:bg-surface`}>🏢 <span>{tr("layout.buero_2")}</span></Link>
            <Link to="/profil" onClick={close} className={`${item} text-ink hover:bg-surface`}>👤 <span>{tr("layout.profil")}</span></Link>
            <Link to="/settings" onClick={close} className={`${item} text-ink hover:bg-surface`}>⚙️ <span>{tr("layout.einstellungen")}</span></Link>
            {isAdmin && (
              <Link to="/admin" onClick={close} className={`${item} text-ink hover:bg-surface`}>🛠️ <span>{tr("layout.admin")}</span></Link>
            )}
            <div className="my-1 border-t border-line" />
            <button onClick={() => { close(); logout(); }}
              className={`${item} w-full text-left text-ink hover:bg-surface`}>🚪 <span>{tr("layout.abmelden")}</span></button>
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
  const isActive = (to: string) => loc.pathname === to || current === to;
  const onProjectPage = /^\/projects\//.test(loc.pathname);

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-card px-3 py-2 sm:gap-3 sm:px-5 relative">
        {/* Links: Marke + Projekt-Titel/Switcher bzw. Seitentitel */}
        <div className="flex min-w-0 shrink items-center gap-2 sm:gap-3">
          <Link to="/" title={tr("layout.traccoon_start")}
            className="flex h-10 w-10 shrink-0 items-center justify-center text-xl md:h-8 md:w-8">🦝</Link>
          {!onProjectPage && chrome.title && (
            <span className="truncate font-semibold text-ink">{chrome.title}</span>
          )}
          {/* Der Wechsler ist auf einer projektlosen Seite am Handy nur Platzverbrauch —
              dort steht der Seitentitel, und die Projektliste hängt im Menü. */}
          <div className={!onProjectPage && chrome.title ? "hidden sm:block" : ""}>
            <ProjectSwitcher />
          </div>
        </div>

        <div className="flex-1" />

        {/* Rechts: Badges (ab sm) + Nutzer + Burger (mobil) */}
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <div className="hidden items-center gap-2 sm:flex sm:gap-3">
            <InboxBadge />
            <AgentsBadge />
          </div>
          <NotificationBell />
          <UserMenu />
          <MobileMenu />
        </div>
      </header>
      {/* [&>*]:mx-auto zentriert begrenzte Seiten-Spalten; volle Breite bleibt unberührt. */}
      <main className="mx-auto max-w-[1400px] p-3 [&>*]:mx-auto sm:p-5">
        <SeitenNavigation tabs={chrome.tabs} aktiv={(t) => chrome.active ? t.key === chrome.active : isActive(t.to)} />
        {children}
      </main>
      <UpdateFooter />
    </div>
  );
}

/**
 * Die Reiter der aktuellen Seite, auf der Seite.
 *
 * Vorher standen sie in der Kopfzeile: eine Pillenleiste, die ab acht Reitern (Einstellungen,
 * Administration) seitwärts scrollte und dabei den ersten und den aktiven Reiter abschnitt —
 * eine Navigation, die man nicht sieht, ist keine. Am Handy waren sie stattdessen im
 * Burger-Menü versteckt, also an einer zweiten Stelle in einer zweiten Form.
 *
 * Hier bricht die Leiste einfach um. Sie kostet eine Zeile Höhe und zeigt dafür alles, auf
 * jeder Breite, mit Beschriftung statt bloßem Zeichen.
 */
function SeitenNavigation({ tabs, aktiv }: { tabs: ChromeTab[]; aktiv: (t: ChromeTab) => boolean }) {
  if (tabs.length === 0) return null;
  return (
    <nav className="mb-4 flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1">
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          to={tab.to}
          className={`flex min-h-[36px] items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm md:min-h-0 ${
            aktiv(tab)
              ? "bg-surface font-medium text-ink"
              : "text-muted hover:bg-surface hover:text-ink"
          }`}
        >
          {tab.icon && <span className="text-base leading-none">{tab.icon}</span>}
          <span>{tab.label}</span>
        </Link>
      ))}
    </nav>
  );
}
