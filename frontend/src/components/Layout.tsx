import { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import NotificationBell from "./NotificationBell";
import AgentsBadge from "./AgentsBadge";
import InboxBadge from "./InboxBadge";
import UpdateFooter from "./UpdateFooter";

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("traccoon_theme", next);
}

function initials(name?: string) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  const s = parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2);
  return s.toUpperCase();
}

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const location = useLocation();

  const navItems: [string, string, string][] = [
    ["/", "🏠", "Projekte"],
    ["/settings", "⚙️", "Einstellungen"],
    ...(user?.global_role === "admin" ? ([["/admin", "🛠️", "Admin"]] as [string, string, string][]) : []),
  ];
  const isActive = (path: string) => (path === "/" ? location.pathname === "/" : location.pathname.startsWith(path));

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 border-b border-line bg-card/90 px-5 py-2.5 backdrop-blur supports-[backdrop-filter]:bg-card/70">
        <div className="mx-auto flex max-w-[1400px] items-center gap-4">
          <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold">
            <span className="text-lg">🦝</span> Traccoon
          </Link>

          <div className="flex-1" />

          {/* Zentriertes Dock: Pill-Navigation mit Icon+Label und aktivem Highlight */}
          <nav className="flex items-center gap-0.5 rounded-full border border-line bg-surface/70 p-1 shadow-sm">
            {navItems.map(([path, icon, label]) => (
              <Link
                key={path}
                to={path}
                aria-label={label}
                className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm transition-colors ${
                  isActive(path)
                    ? "bg-brand text-white shadow"
                    : "text-muted hover:bg-card hover:text-ink"
                }`}
              >
                <span aria-hidden="true">{icon}</span>
                <span className="hidden sm:inline">{label}</span>
              </Link>
            ))}
          </nav>

          <div className="flex-1" />

          {/* Rechte Aktionen als runde Icon-Buttons */}
          <div className="flex shrink-0 items-center gap-1">
            <div className="rounded-full p-1 hover:bg-surface">
              <InboxBadge />
            </div>
            <div className="rounded-full p-1 hover:bg-surface">
              <AgentsBadge />
            </div>
            <div className="rounded-full p-1 hover:bg-surface">
              <NotificationBell />
            </div>
            <button
              onClick={toggleTheme}
              title="Theme"
              aria-label="Theme wechseln"
              className="flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface hover:text-ink"
            >
              ◑
            </button>
            <span
              title={user?.display_name || user?.username}
              aria-label={user?.display_name || user?.username || "Benutzer"}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-brand/20 text-xs font-semibold text-brand"
            >
              {initials(user?.display_name || user?.username)}
            </span>
            <button
              onClick={logout}
              title="Abmelden"
              aria-label="Abmelden"
              className="flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface hover:text-ink"
            >
              ⏻
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] p-5">{children}</main>
      <UpdateFooter />
    </div>
  );
}
