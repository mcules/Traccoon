import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { api, type User } from "../api";
import { useAuth } from "../auth";
import { tr } from "../i18n";
import { usePlugins } from "../plugins";
import { usePageChrome } from "../pageChrome";
import { Bereich } from "../components/ui";

/**
 * Der Wirt, in dem ein Plugin laeuft — und die Brücke, über die es an Daten kommt.
 *
 * Das iframe bekommt bewusst **kein** `allow-same-origin`. Damit hat das Plugin eine
 * undurchsichtige Herkunft: kein Zugriff auf den Token im `localStorage`, keiner auf die
 * API, und die ausgelieferte CSP (`connect-src 'none'`) sperrt ihm auch den Weg nach
 * draussen. Alles, was es an Daten braucht, fragt es hier — und der Wirt gibt nur heraus,
 * was das Manifest angemeldet und ein Admin freigegeben hat.
 *
 * Die Prüfung an dieser Stelle schützt vor dem Plugin, nicht vor dem Menschen davor: Er hat
 * seinen Token ohnehin. Genau das ist das Bedrohungsmodell — ein fremdes Stück Code, das im
 * Browser einer angemeldeten Person läuft.
 */

type RufCtx = { slug: string; user: User | null };

type Ruf = {
  /** Welches Recht dieser Ruf verlangt. Ohne Angabe: keins (etwas über sich selbst). */
  recht?: (args: any) => string;
  hole: (args: any, ctx: RufCtx) => Promise<any>;
};

/** Query-Teil aus den Angaben eines Plugins — nur bekannte Felder, nichts durchgereicht. */
function spanne(a: any): string {
  const teile: string[] = [];
  if (a?.von) teile.push(`von=${encodeURIComponent(String(a.von))}`);
  if (a?.bis) teile.push(`bis=${encodeURIComponent(String(a.bis))}`);
  const grenze = Number(a?.grenze);
  if (Number.isFinite(grenze) && grenze > 0) {
    teile.push(`limit=${Math.min(50000, Math.floor(grenze))}`);
  }
  return teile.length ? `?${teile.join("&")}` : "";
}

/**
 * Was ein Plugin fragen darf.
 *
 * Jeder Eintrag nennt sein Recht selbst. Wichtig ist die Aufteilung der Parameter: Was
 * bestimmt, *wessen* Daten geholt werden (der Slug des Plugins, der angemeldete Mensch),
 * setzt der Wirt — das Plugin nennt nur, *was* es will. Sonst könnte ein Plugin über einen
 * fremden Slug in die Ablage eines anderen sehen.
 */
const RUFE: Record<string, Ruf> = {
  ich: {
    hole: async (_a, { user }) => ({
      name: user?.display_name || user?.username || "",
      locale: user?.locale || "de",
      timezone: user?.timezone || "",
      theme: document.documentElement.getAttribute("data-theme") || "dark",
    }),
  },
  "reihen.liste": {
    recht: (a) => `series:${a?.art || "number"}`,
    hole: (a) => api.get(`/series?kind=${encodeURIComponent(String(a?.art || "number"))}`),
  },
  "reihen.punkte": {
    recht: (a) => `series:${a?.art || "number"}`,
    hole: (a) => api.get(
      `/series/${encodeURIComponent(String(a?.key || ""))}/points${spanne(a)}`),
  },
  "reihen.live": {
    recht: (a) => `series:${a?.art || "location"}`,
    hole: (a) => api.get(`/series-live?kind=${encodeURIComponent(String(a?.art || "location"))}`),
  },
  // Orte haengen an den Standorten und teilen deren Recht: Wer die Spur sehen darf, darf
  // auch wissen, wie die Zaeune heissen, die darin auftauchen.
  "orte.liste": {
    recht: () => "series:location",
    hole: () => api.get("/places"),
  },
  "ablage.lesen": {
    hole: (a, { slug }) =>
      api.get(`/plugins/${slug}/data/${encodeURIComponent(String(a?.tabelle || ""))}`),
  },
  "ablage.schreiben": {
    hole: (a, { slug }) =>
      api.post(`/plugins/${slug}/data/${encodeURIComponent(String(a?.tabelle || ""))}`,
               a?.zeile || {}),
  },
  "ablage.loeschen": {
    hole: (a, { slug }) =>
      api.del(`/plugins/${slug}/data/${encodeURIComponent(String(a?.tabelle || ""))}/${
        encodeURIComponent(String(a?.id || ""))}`),
  },
};

export default function PluginRahmen() {
  const { slug = "" } = useParams();
  const loc = useLocation();
  const { user } = useAuth();
  const plugins = usePlugins();
  const rahmen = useRef<HTMLIFrameElement>(null);
  const [fehler, setFehler] = useState("");

  const plugin = useMemo(() => plugins.find((p) => p.slug === slug), [plugins, slug]);
  usePageChrome(plugin?.name || tr("plugins.titel"), []);

  // Die Freigaben in einem Ref: Der Zuhörer wird einmal gesetzt, soll aber immer den
  // aktuellen Stand sehen — sonst hinge er an dem, der beim ersten Rendern galt.
  const erlaubt = useRef<string[]>([]);
  erlaubt.current = plugin?.liest_erlaubt || [];

  useEffect(() => {
    if (!slug) return;
    const ctx: RufCtx = { slug, user };

    function antworte(id: number, gut: boolean, wert: any) {
      const ziel = rahmen.current?.contentWindow;
      // `*` als Zieladresse ist hier keine Nachlässigkeit, sondern notwendig: Das iframe hat
      // die Herkunft `null`, jede andere Angabe würde die Nachricht verwerfen. Der Empfänger
      // ist trotzdem eindeutig — es ist genau dieses Fenster.
      ziel?.postMessage(
        gut ? { quelle: "traccoon", id, ok: true, daten: wert }
            : { quelle: "traccoon", id, ok: false, fehler: String(wert) }, "*");
    }

    async function beiNachricht(e: MessageEvent) {
      const d = e.data;
      if (!d || d.quelle !== "plugin") return;
      // Nur aus genau diesem iframe — nicht aus einem anderen Fenster, das sich einklinkt.
      if (e.source !== rahmen.current?.contentWindow) return;
      if (d.bereit) return;
      if (typeof d.id !== "number") return;

      const ruf = RUFE[String(d.ruf)];
      if (!ruf) return antworte(d.id, false, tr("plugins.ruf_unbekannt"));
      if (ruf.recht) {
        const noetig = ruf.recht(d.args);
        if (!erlaubt.current.includes(noetig)) {
          return antworte(d.id, false, `${tr("plugins.recht_fehlt")}: ${noetig}`);
        }
      }
      try {
        antworte(d.id, true, await ruf.hole(d.args || {}, ctx));
      } catch (err) {
        antworte(d.id, false, err instanceof Error ? err.message : tr("common.fehler"));
      }
    }

    window.addEventListener("message", beiNachricht);
    return () => window.removeEventListener("message", beiNachricht);
  }, [slug, user]);

  useEffect(() => setFehler(""), [slug]);

  if (plugins.length && !plugin) {
    return <Bereich hinweis={tr("plugins.nicht_gefunden")}><div /></Bereich>;
  }

  // Der Anker entscheidet, welche Seite eines Plugins mit mehreren Beiträgen gemeint ist.
  const quelle = `/api/plugins/${slug}/app/${loc.hash || ""}`;

  return (
    <div className="h-[calc(100vh-9rem)] min-h-[420px] w-full overflow-hidden rounded-lg border border-line bg-surface">
      {fehler && <div className="p-3 text-sm text-red-500">{fehler}</div>}
      <iframe
        ref={rahmen}
        src={quelle}
        title={plugin?.name || slug}
        onError={() => setFehler(tr("plugins.laedt_nicht"))}
        className="h-full w-full border-0"
        sandbox="allow-scripts allow-popups allow-forms"
      />
    </div>
  );
}
