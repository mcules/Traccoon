import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { setzeZeitzone } from "./lib/formatTime";
import { api, getToken, setToken, User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.get<User>("/auth/me");
      setUser(me);
      // Alle Uhrzeiten der Oberfläche laufen ab hier in der Zone dieser Person, nicht in der
      // des Browsers — dieselbe Angabe, mit der der Server ihre Zeitpläne rechnet.
      setzeZeitzone(me.timezone);
      // The server is the source of the theme: apply it on loading (no toggle UI here).
      if (me.theme === "light" || me.theme === "dark") {
        document.documentElement.setAttribute("data-theme", me.theme);
        localStorage.setItem("traccoon_theme", me.theme);
      }
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function login(email: string, password: string) {
    const res = await api.post<{ access_token: string }>("/auth/login", { email, password });
    setToken(res.access_token);
    await refresh();
  }

  function logout() {
    setToken(null);
    setUser(null);
    location.href = "/login";
  }

  return (
    <Ctx.Provider value={{ user, loading, login, logout, refresh }}>{children}</Ctx.Provider>
  );
}
