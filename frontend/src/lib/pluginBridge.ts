import { RefObject, useEffect, useRef } from "react";
import { api, type User } from "../api";
import { useAuth } from "../auth";
import { language, textsWithPrefix, tr } from "../i18n";

/**
 * The bridge over which a plugin gets its data — and the list of what it may ask for.
 *
 * This used to live inside the page that shows a plugin full screen. It moved out when the
 * dashboard grew tiles: a tile is the same plugin behind the same fence, only smaller, and
 * two copies of a security boundary are one copy too many.
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
  if (a?.from) parts.push(`from=${encodeURIComponent(String(a.from))}`);
  if (a?.to) parts.push(`to=${encodeURIComponent(String(a.to))}`);
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
  // The phrases of a plugin live in the catalogs of the house like every other text — a
  // plugin that carried its own would be the one corner an admin cannot correct and no
  // translation reaches. It only gets its own: the prefix is its slug, set here, not by the
  // caller.
  "i18n.texts": {
    fetch: async (_a, { slug }) => ({
      language: language(),
      texts: textsWithPrefix(`${slug}.`),
    }),
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

/**
 * What a plugin may hand the host **unasked**: the breakdown behind its tile.
 *
 * The one message that goes the other way. A tile is a figure like the host's own ones, and
 * behind those stands a list of where the number comes from — but the tile is a frame the
 * host must not point into (no `allow-same-origin`), and its content takes no mouse at all
 * (`pointer-events-none`, so that a click belongs to the host and not to foreign code). A
 * tooltip inside the plugin would therefore never open, and the plugin has to say what
 * stands in it. The host draws it, over its own card.
 *
 * Everything that arrives is text and nothing else: strings, cut to length, at most a dozen
 * lines. Whatever a plugin sends beyond that is dropped, not rendered.
 */
export type TileNote = { title: string; rows: { label: string; value: string }[] };

/**
 * One note per figure instead of one per tile.
 *
 * "12 open" over four stacks is the same unanswered question as "7 waiting" over eight
 * projects, and the host's own figures each carry their own list for exactly that reason. A
 * tile that sends a single note for its whole card gives the same answer wherever one points
 * — which is no answer at all once the card holds three numbers.
 *
 * The plugin says where each figure sits (the bridge measures it), the host lays an invisible
 * zone over that place. The rectangle is in the frame's pixels, and the frame covers the card
 * edge to edge, so they are the card's pixels too.
 */
export type TileZone = TileNote & { key: string; rect: Rect };
type Rect = { x: number; y: number; w: number; h: number };

/** At most this many zones per tile — a tile with more figures than this is a page. */
const MAX_ZONES = 8;

function rowsOf(raw: any): TileNote["rows"] {
  const cut = (v: any) => String(v ?? "").slice(0, 80);
  const rows = Array.isArray(raw?.rows) ? raw.rows.slice(0, 12) : [];
  return rows.map((r: any) => ({ label: cut(r?.label), value: cut(r?.value) }))
             .filter((r: TileNote["rows"][number]) => r.label);
}

/**
 * A rectangle we are willing to lay over our own card.
 *
 * Everything here comes from foreign code, so nothing is taken on trust: no negative or
 * unreal numbers, and a zone may not grow past the tile it belongs to. A plugin that asked
 * for a 10000px zone would otherwise spread a hover target of its own choosing across the
 * page around it.
 */
function rectOf(raw: any): Rect | null {
  const n = (v: any, max: number) => {
    const x = Number(v);
    return Number.isFinite(x) ? Math.min(Math.max(x, 0), max) : null;
  };
  const x = n(raw?.x, 4096), y = n(raw?.y, 4096);
  const w = n(raw?.w, 4096), h = n(raw?.h, 4096);
  if (x === null || y === null || !w || !h) return null;
  return { x, y, w, h };
}

export type TileNotes = { whole: TileNote | null; zones: TileZone[] };

function noteOf(raw: any): TileNotes | null {
  if (!raw || typeof raw !== "object") return null;
  const cut = (v: any) => String(v ?? "").slice(0, 80);

  if (Array.isArray(raw.zones)) {
    const zones: TileZone[] = [];
    for (const z of raw.zones.slice(0, MAX_ZONES)) {
      const rect = rectOf(z?.rect);
      const rows = rowsOf(z);
      if (rect && rows.length) {
        zones.push({ key: cut(z?.key) || String(zones.length), title: cut(z?.title), rows, rect });
      }
    }
    return zones.length ? { whole: null, zones } : null;
  }

  const rows = rowsOf(raw);
  return rows.length ? { whole: { title: cut(raw.title), rows }, zones: [] } : null;
}

/** Answer the questions of exactly this iframe, for as long as it is on the page. */
export function usePluginBridge(
  slug: string,
  frame: RefObject<HTMLIFrameElement | null>,
  readsGranted: string[],
  onNote?: (note: TileNotes | null) => void,
) {
  const { user } = useAuth();
  // The grants in a ref: the listener is installed once but must always see the current
  // state — otherwise it would hang on whatever was true at the first render.
  const granted = useRef<string[]>([]);
  granted.current = readsGranted;
  // Same reason: the listener is installed once and must call the current receiver.
  const note = useRef<typeof onNote>(undefined);
  note.current = onNote;

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
      if (d.note !== undefined) return note.current?.(noteOf(d.note));
      if (d.ready) return;
      if (typeof d.id !== "number") return;

      const call = CALLS[String(d.call)];
      if (!call) return answer(d.id, false, tr("plugins.unknown_call"));
      if (call.right) {
        const needed = call.right(d.args);
        if (!granted.current.includes(needed)) {
          return answer(d.id, false, `${tr("plugins.right_not_released")}: ${needed}`);
        }
      }
      try {
        answer(d.id, true, await call.fetch(d.args || {}, ctx));
      } catch (err) {
        answer(d.id, false, err instanceof Error ? err.message : tr("common.error"));
      }
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [slug, user, frame]);
}
