import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { setTimezone } from "./lib/formatTime";
import { api, getToken, setToken, User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** The other door: username, then the key on the device. */
  loginWithPasskey: (email: string) => Promise<void>;
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
      // All times of the UI run in the zone of this person from here on, not in that of the
      // browser — the same entry the server computes their schedules with.
      setTimezone(me.timezone);
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

  /**
   * Two calls, and the split is the point: the server first says WHAT is to be signed
   * (challenge plus which keys may answer), the device signs, and the signature goes back.
   * The challenge is spent on reading, so a signature cannot be handed in twice.
   */
  async function loginWithPasskey(email: string) {
    const { usePasskey } = await import("./passkeys");
    const options = await api.post<Record<string, any>>("/auth/passkey/options", { email });
    const credential = await usePasskey(options);
    const res = await api.post<{ access_token: string }>("/auth/passkey/login",
                                                          { email, credential });
    setToken(res.access_token);
    await refresh();
  }

  function logout() {
    setToken(null);
    setUser(null);
    location.href = "/login";
  }

  return (
    <Ctx.Provider value={{ user, loading, login, loginWithPasskey, logout, refresh }}>{children}</Ctx.Provider>
  );
}
