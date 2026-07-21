import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import NotificationBell from "./NotificationBell";

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("traccoon_theme", next);
}

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-line bg-card px-5 py-3">
        <Link to="/" className="flex items-center gap-2 font-semibold">
          <span className="text-lg">🦝</span> Traccoon
        </Link>
        <div className="flex-1" />
        <Link to="/settings" className="text-muted hover:text-ink">Einstellungen</Link>
        {user?.global_role === "admin" && (
          <Link to="/admin" className="text-muted hover:text-ink">Admin</Link>
        )}
        <NotificationBell />
        <button onClick={toggleTheme} className="rounded px-2 py-1 text-muted hover:text-ink" title="Theme">
          ◑
        </button>
        <span className="text-muted">{user?.display_name || user?.username}</span>
        <button onClick={logout} className="text-muted hover:text-ink">
          Abmelden
        </button>
      </header>
      <main className="mx-auto max-w-[1400px] p-5">{children}</main>
    </div>
  );
}
