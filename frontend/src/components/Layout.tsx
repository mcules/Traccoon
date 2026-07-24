import { ReactNode, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, Project } from "../api";
import { useAuth } from "../auth";
import { useChrome } from "../pageChrome";
import NotificationBell from "./NotificationBell";
import AgentsBadge from "./AgentsBadge";
import InboxBadge from "./InboxBadge";
import UpdateFooter from "./UpdateFooter";

function ProjectsMenu() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const loc = useLocation();
  // Aktuelles Projekt aus der URL (/projects/:key…) — der Dropdown-Knopf zeigt es direkt an,
  // damit der Projektname nicht zusätzlich als Titel im Header stehen muss.
  const curKey = loc.pathname.match(/^\/projects\/([^/]+)/)?.[1];
  // Key ["projects"] – ggf. bereits durch die Projekte-Seite gecached.
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const cur = projects?.find((p) => p.key === curKey);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex max-w-[16rem] items-center gap-1 truncate rounded-md border border-line px-2.5 py-1 text-ink hover:bg-surface"
        title={cur?.name}
      >
        {cur ? (
          <><span className="text-xs text-muted">{cur.key}</span><span className="truncate">{cur.name}</span></>
        ) : (
          <span className="text-muted">Projekte</span>
        )}
        <span className="text-muted">▾</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-30 mt-2 max-h-96 w-64 overflow-y-auto rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            {(projects?.length ?? 0) === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted">Keine Projekte.</div>
            )}
            {projects?.map((p) => (
              <button
                key={p.key}
                onClick={() => {
                  navigate("/projects/" + p.key);
                  setOpen(false);
                }}
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

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded px-2 py-1 text-muted hover:text-ink"
      >
        👤 {user?.display_name || user?.username} ▾
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-2 w-48 rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            <Link
              to="/profil"
              onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface"
            >
              Profil
            </Link>
            <Link
              to="/settings"
              onClick={() => setOpen(false)}
              className="block rounded px-2 py-1.5 text-ink hover:bg-surface"
            >
              Einstellungen
            </Link>
            {isAdmin && (
              <Link
                to="/admin"
                onClick={() => setOpen(false)}
                className="block rounded px-2 py-1.5 text-ink hover:bg-surface"
              >
                Admin
              </Link>
            )}
            <div className="my-1 border-t border-line" />
            <button
              onClick={logout}
              className="block w-full rounded px-2 py-1.5 text-left text-ink hover:bg-surface"
            >
              Abmelden
            </button>
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
  // aktiv = exakter Pfad-Treffer ODER voller Treffer inkl. Query (z.B. ?tab=board)
  const isActive = (to: string) => loc.pathname === to || current === to;
  // Auf Projektseiten zeigt bereits das Projekte-Dropdown den Projektnamen → Titel unterdrücken.
  const onProjectPage = /^\/projects\//.test(loc.pathname);

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-line bg-card px-5 py-3">
        {/* Linker Block */}
        <div className="flex shrink-0 items-center gap-3">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <span className="text-lg">🦝</span> Traccoon
          </Link>
          <ProjectsMenu />
          {!onProjectPage && chrome.title && (
            <span className="font-medium text-ink">{chrome.title}</span>
          )}
        </div>

        {/* Zentrierter Block: Untermenü als Buttons */}
        <div className="flex flex-1 justify-center">
          {chrome.tabs.length > 0 && (
            <nav className="flex min-w-0 items-center gap-1.5 overflow-x-auto">
              {chrome.tabs.map((tab) => (
                <Link
                  key={tab.key}
                  to={tab.to}
                  className={`shrink-0 whitespace-nowrap rounded-md border px-3 py-1 text-sm ${
                    isActive(tab.to)
                      ? "border-brand bg-brand text-white"
                      : "border-line text-muted hover:bg-surface hover:text-ink"
                  }`}
                >
                  {tab.label}
                </Link>
              ))}
            </nav>
          )}
        </div>

        {/* Rechter Block */}
        <div className="flex shrink-0 items-center gap-3">
          <InboxBadge />
          <AgentsBadge />
          <NotificationBell />
          <UserMenu />
        </div>
      </header>
      {/* [&>*]:mx-auto zentriert begrenzte Seiten-Spalten (z.B. Dashboard max-w-4xl); volle
          Breite (w-full Tabellen/Board) bleibt unberührt, da mx-auto bei Auto-Breite wirkungslos ist. */}
      <main className="mx-auto max-w-[1400px] p-5 [&>*]:mx-auto">{children}</main>
      <UpdateFooter />
    </div>
  );
}
