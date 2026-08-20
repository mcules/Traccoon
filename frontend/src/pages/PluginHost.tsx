import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { useAuth } from "../auth";
import { tr } from "../i18n";
import { usePlugins } from "../plugins";
import { usePageChrome } from "../pageChrome";
import { Bereich } from "../components/ui";

/**
 * The host a plugin runs in — and the bridge over which it gets its data.
 *
 * The iframe deliberately has **no** `allow-same-origin`. The plugin therefore has an opaque
 * origin: no access to the token in `localStorage`, none to the API, and the delivered CSP
 * (`connect-src 'none'`) closes the way out as well. Everything it needs it asks for here,
 * and the host hands out only what the manifest declared and an admin granted.
 *
 * The check protects against the plugin, not against the person in front of it: they have
 * their token anyway. That is precisely the threat model — foreign code running in the
 * browser of a logged-in person.
 *
 * The names of the calls are English because they are a public interface: plugins are
 * written against them, and the wiki documents them.
 */

type CallContext = { slug: string; user: User | null };

type Call = {
  /** Which right this call requires. Absent means none (something about oneself). */
  right?: (args: any) => string;
  fetch: (args: any, ctx: CallContext) => Promise<any>;
};

/** Query part from what a plugin asked for — only known fields, nothing passed through. */
function range(a: any): string {
  const parts: string[] = [];
  if (a?.from) parts.push(`von=${encodeURIComponent(String(a.from))}`);
  if (a?.to) parts.push(`bis=${encodeURIComponent(String(a.to))}`);
  const limit = Number(a?.limit);
  if (Number.isFinite(limit) && limit > 0) {
    parts.push(`limit=${Math.min(50000, Math.floor(limit))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

/**
 * What a plugin may ask for.
 *
 * Every entry names its own right. The split of the parameters is what matters: whatever
 * decides *whose* data is fetched (the plugin's slug, the logged-in person) is set by the
 * host — the plugin only names *what* it wants. Otherwise a plugin could reach into another
 * one's storage by passing a foreign slug.
 */
const CALLS: Record<string, Call> = {
  me: {
    fetch: async (_a, { user }) => ({
      name: user?.display_name || user?.username || "",
      locale: user?.locale || "de",
      timezone: user?.timezone || "",
      theme: document.documentElement.getAttribute("data-theme") || "dark",
    }),
  },
  "series.list": {
    right: (a) => `series:${a?.kind || "number"}`,
    fetch: (a) => api.get(`/series?kind=${encodeURIComponent(String(a?.kind || "number"))}`),
  },
  "series.points": {
    right: (a) => `series:${a?.kind || "number"}`,
    fetch: (a) => api.get(
      `/series/${encodeURIComponent(String(a?.key || ""))}/points${range(a)}`),
  },
  "series.live": {
    right: (a) => `series:${a?.kind || "location"}`,
    fetch: (a) => api.get(`/series-live?kind=${encodeURIComponent(String(a?.kind || "location"))}`),
  },
  // Places belong to locations and share their right: whoever may see the track may also
  // know the names of the fences that show up in it.
  "places.list": {
    right: () => "series:location",
    fetch: () => api.get("/places"),
  },
  "store.list": {
    fetch: (a, { slug }) =>
      api.get(`/plugins/${slug}/data/${encodeURIComponent(String(a?.table || ""))}`),
  },
  "store.create": {
    fetch: (a, { slug }) =>
      api.post(`/plugins/${slug}/data/${encodeURIComponent(String(a?.table || ""))}`,
               a?.row || {}),
  },
  "store.delete": {
    fetch: (a, { slug }) =>
      api.del(`/plugins/${slug}/data/${encodeURIComponent(String(a?.table || ""))}/${
        encodeURIComponent(String(a?.id || ""))}`),
  },
};

export default function PluginHost() {
  const { slug = "" } = useParams();
  const loc = useLocation();
  const { user } = useAuth();
  const plugins = usePlugins();
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState("");

  const plugin = useMemo(() => plugins.find((p) => p.slug === slug), [plugins, slug]);
  usePageChrome(plugin?.name || tr("plugins.titel"), []);

  // The grants in a ref: the listener is installed once but must always see the current
  // state — otherwise it would hang on whatever was true at the first render.
  const granted = useRef<string[]>([]);
  granted.current = plugin?.reads_granted || [];

  useEffect(() => {
    if (!slug) return;
    const ctx: CallContext = { slug, user };

    function answer(id: number, ok: boolean, value: any) {
      const target = frame.current?.contentWindow;
      // `*` as the target origin is not sloppiness here but necessary: the iframe has the
      // origin `null`, and any other value would make the browser drop the message. The
      // recipient is unambiguous all the same — it is exactly this window.
      target?.postMessage(
        ok ? { source: "traccoon", id, ok: true, data: value }
           : { source: "traccoon", id, ok: false, error: String(value) }, "*");
    }

    async function onMessage(e: MessageEvent) {
      const d = e.data;
      if (!d || d.source !== "plugin") return;
      // Only from exactly this iframe — not from another window joining in.
      if (e.source !== frame.current?.contentWindow) return;
      if (d.ready) return;
      if (typeof d.id !== "number") return;

      const call = CALLS[String(d.call)];
      if (!call) return answer(d.id, false, tr("plugins.ruf_unbekannt"));
      if (call.right) {
        const needed = call.right(d.args);
        if (!granted.current.includes(needed)) {
          return answer(d.id, false, `${tr("plugins.recht_fehlt")}: ${needed}`);
        }
      }
      try {
        answer(d.id, true, await call.fetch(d.args || {}, ctx));
      } catch (err) {
        answer(d.id, false, err instanceof Error ? err.message : tr("common.fehler"));
      }
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [slug, user]);

  useEffect(() => setError(""), [slug]);

  if (plugins.length && !plugin) {
    return <Bereich hinweis={tr("plugins.nicht_gefunden")}><div /></Bereich>;
  }

  // The anchor decides which page of a plugin with several contributions is meant.
  const src = `/api/plugins/${slug}/app/${loc.hash || ""}`;

  return (
    <div className="h-[calc(100vh-9rem)] min-h-[420px] w-full overflow-hidden rounded-lg border border-line bg-surface">
      {error && <div className="p-3 text-sm text-red-500">{error}</div>}
      <iframe
        ref={frame}
        src={src}
        title={plugin?.name || slug}
        onError={() => setError(tr("plugins.laedt_nicht"))}
        className="h-full w-full border-0"
        sandbox="allow-scripts allow-popups allow-forms"
      />
    </div>
  );
}
