// Schicht 2 — der Zusammenbau. Eine Ansicht, drei Einsatzorte: als Projekt-Reiter (begrenzt,
// ohne Dock und Inspektor), als Vollbildseite und als Wandschirm (`?kiosk=1`).
//
// ══ Der Kiosk ist eine Variante, keine Route ═════════════════════════════════════════════════
//
// `variant="kiosk"` blendet aus, statt neu zu bauen: Werkzeugleiste, Dock, Inspektor,
// Zeitleiste und alles Bedienbare der Kopfzeile fallen weg, der Rest ist derselbe Code. Eine
// eigene Route wäre eine Kopie von `pages/Office.tsx` gewesen — und `?project=`, `?sid=` und
// `?at=` hätten in beiden gepflegt werden müssen.
//
// Zwei Dinge unterscheiden ihn im Verhalten:
//
//   · **Raumrotation.** Der gewöhnliche Nachrück-Effekt greift nur, wenn gar keine Sitzung
//     gewählt ist oder die gewählte aus dem Fenster fällt. Ein Wandschirm braucht mehr: 516
//     von 632 Läufen sind unter fünf Minuten fertig, ein einmal gewählter Raum ist also die
//     meiste Zeit tot. Also: passiert `KIOSK_SWITCH_AFTER_MS` lang nichts und ein anderer Raum
//     ist live, wird gewechselt.
//   · **Tastatur auf `Escape`.** Vor der Wand steht keine Tastatur; was trotzdem eine anfasst,
//     soll den Kiosk verlassen können und sonst nichts auslösen.
//
// ══ Was diese Datei besitzt — und was ausdrücklich nicht ═════════════════════════════════════
//
// Sie hält den Zustand, der in Menschentempo wechselt: Auswahl, Überfahren, Sprungpunkt,
// Dock-Reiter, Tempo, Sitzungsfilter, Hilfe. **Nicht** hier leben Recorder, Replay, Kamera und
// Bild — die stecken in Refs innerhalb von `Stage` bzw. `useOfficeFeed`, und genau deshalb
// kostet ein laufender Raum keinen einzigen Renderdurchlauf dieser Komponente.
//
// ══ Die eine Naht, die der Plan offen ließ ═══════════════════════════════════════════════════
//
// `useOfficeFeed(scope, sid)` bedient **eine** Sitzung; ohne `sid` verwirft `accept()` jedes
// Ereignis und der Raum bliebe für immer leer. Der Umfang (`Scope`) sagt aber nur, *welche*
// Sitzungen in Frage kommen. Also wählt diese Ansicht eine aus: sie holt die Sitzungsliste
// (`officeApi.sessions`, im Backend ausdrücklich als „der Inhalt des Projekt-Reiters" geführt)
// und nimmt die oberste — die Liste kommt bereits nach letztem Ereignis absteigend sortiert.
// Gibt es mehrere, steht darüber ein Wähler.
//
// Das ist **nicht** dasselbe wie die Sitzungsreiter der Kopfzeile: die sind ein Filter auf den
// Roster **einer** Sitzung (dimmen, nicht entfernen), der Wähler hier wechselt den Raum.
//
// ══ Die Tastaturkarte ════════════════════════════════════════════════════════════════════════
//
// Der erste globale Tastatur-Listener der Anwendung. Drei Regeln halten ihn verträglich:
//
//   1. Nur solange die Ansicht hängt (`useEffect`-Aufräumer meldet ab).
//   2. Früher Ausstieg bei `input`/`textarea`/`contentEditable` — das Dock hat ein Suchfeld,
//      und niemand will beim Tippen von „bla" das Dock umschalten.
//   3. Früher Ausstieg bei `e.defaultPrevented`. Der Listener sitzt am Ende der Blasenphase,
//      also hat jede Komponente, die die Taste schon verbraucht hat, bereits `preventDefault`
//      gerufen: die Bühne für Alt+Pfeile und `+ - 0 Pos1`, die Zeitleiste für ihren wandernden
//      Fokus. Ohne diese Zeile spulte jeder Pfeiltastendruck in der Zeitleiste **zusätzlich**
//      um eine Sekunde.
//
// Kein ⌘K/Strg+K (gehört dem Browser), und alles mit Strg/Cmd bleibt unangetastet.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Stage from "./Stage.tsx";
import Timeline from "./Timeline.tsx";
import Dock, { DOCK_TABS, type DockTab } from "./Dock.tsx";
import Inspector from "./Inspector.tsx";
import TopBar, { passtZumFilter, type Tempo } from "./TopBar.tsx";
import { officeApi, parseSid, sidKey, type Scope, type SessionSummary } from "./api.ts";
import { useOfficeFeed } from "./useOfficeFeed.ts";
import { useTheme } from "./useTheme.ts";

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** Ein Pfeiltastendruck spult so weit, mit Umschalt zehnmal so weit. */
const STEP_MS = 1000;
const STEP_GROSS_MS = 10_000;

/** So viele Sitzungen holt der Wähler. Mehr als das sucht niemand mehr durch. */
const SESSION_LIMIT = 30;

/** Die Sitzungsliste ist eine gewöhnliche Abfrage — sie ändert sich, wenn ein Lauf beginnt
 *  oder endet, nicht im Sekundentakt. Der Live-Strom hängt an der **gewählten** Sitzung. */
const SESSION_REFETCH_MS = 30_000;

/** Rückblick der Sitzungsliste. Das Backend gibt sich mit einer Woche zufrieden — für einen
 *  Live-Monitor die richtige Vorgabe, für diese Ansicht die falsche: ein Projekt, dessen
 *  letzter Lauf zwölf Tage her ist, zeigte sonst einen leeren Raum samt der Behauptung, es
 *  habe dort nie einen Agenten gegeben. Das Büro ist ein Rückblick, kein Wecker; wie frisch
 *  eine Sitzung ist, sagt ohnehin ihr `live`-Kennzeichen. Die Aufbewahrung deckelt das Fenster
 *  von selbst — archivierte Läufe verschwinden nach `run_retention_days` (Standard 30 Tage). */
const SESSION_WINDOW_H = 24 * 180;

/** Kiosk: passiert im gezeigten Raum so lange nichts und ist ein anderer `live`, wird
 *  gewechselt. Anderthalb Minuten sind länger als jede Denkpause eines Agenten (das Backend
 *  nennt einen Raum nach 90 s ohne Ereignis selbst nicht mehr „live") und kurz genug, dass
 *  die Wand nicht minutenlang einen leeren Schreibtisch zeigt. */
const KIOSK_SWITCH_AFTER_MS = 90_000;

/** So oft sieht der Kiosk nach, ob er weiterrücken sollte. Rein rechnerisch, ohne Netz. */
const KIOSK_ROTATE_TICK_MS = 5000;

/** Kiosk: die Sitzungsliste **ist** hier die Steuerung — sie entscheidet, welcher Raum an der
 *  Wand steht. Deshalb dichter als die 30 s der bedienten Ansicht. */
const KIOSK_SESSION_REFETCH_MS = 15_000;

/** Nach so langer Zeigerruhe verschwindet der ⛶-Knopf. Er ist die einzige Bedienung, die der
 *  Kiosk braucht (Vollbild verlangt eine Nutzergeste) — und die einzige, die stört. */
const KIOSK_KNOPF_MS = 5000;

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

export interface OfficeViewProps {
  scope: Scope;
  /** `"tab"` = im Projekt-Reiter (kein Dock, kein Inspektor), `"full"` = Vollbildseite,
   *  `"kiosk"` = Wandschirm ohne Bedienung. */
  variant: "tab" | "full" | "kiosk";
  /** Startpunkt der Wiedergabe in Epoch-ms, `null`/fehlt = live. Nur beim Einhängen gelesen. */
  initialAt?: number | null;
  /** Meldet jeden Wechsel des Sprungpunkts — die Vollbildseite schreibt ihn in die URL. */
  onAtChange?: (ts: number | null) => void;
  /** Nur `variant="tab"`: „⤢ Vollbild". */
  onFullscreen?: () => void;
  /** `variant="full"`: „⤡ Vollbild verlassen" und das letzte Esc.
   *  `variant="kiosk"`: das **einzige** Esc — es verlässt den Wandschirm. */
  onClose?: () => void;
  /** Meldet die aktuelle Fehlermeldung (oder `undefined`) nach außen. Der Wachhund der
   *  Kioskseite hängt daran: er kann nur neu laden, was er auch sieht. */
  onErrorChange?: (fehler: string | undefined) => void;
  /** Vorgewählte Sitzung (`"issue:412"`), z. B. aus der URL. Nur beim Einhängen gelesen. */
  initialSid?: string | null;
  /** Meldet den Wechsel des Raums — die Vollbildseite schreibt ihn in die URL, damit ein
   *  geteilter Link auf denselben Raum zeigt wie der Sprungpunkt darin. */
  onSidChange?: (sid: string | null) => void;
  className?: string;
}

// ── Die Ansicht ─────────────────────────────────────────────────────────────────────────────

export default function OfficeView({
  scope, variant, initialAt, onAtChange, onFullscreen, onClose,
  initialSid, onSidChange, onErrorChange, className,
}: OfficeViewProps): JSX.Element {
  const voll = variant === "full";
  const kiosk = variant === "kiosk";
  /** Bühne füllt die Fläche statt im 16:9-Kasten zu sitzen — gilt für beide großen Formen. */
  const grossflaechig = voll || kiosk;

  // ── Zustand ────────────────────────────────────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | undefined>(undefined);
  const [seekTs, setSeekTs] = useState<number | null>(initialAt ?? null);
  const [dockTab, setDockTab] = useState<DockTab>("chat");
  const [dockOpen, setDockOpen] = useState(true);
  /** Das **gewählte** Tempo. Angehalten ist ein eigener Schalter, damit die Leertaste den
   *  vorigen Wert zurückbringt und nicht stumpf auf 1× springt. */
  const [speed, setSpeed] = useState<Tempo>(1);
  const [paused, setPaused] = useState(false);
  const [sessionFilter, setSessionFilter] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [sidStr, setSidStr] = useState<string | null>(initialSid ?? null);

  const grade = useTheme();

  // Rückrufe als Spiegel-Refs: sie werden aus Effekten und aus dem Tastatur-Listener gerufen,
  // und keiner von beiden soll neu laufen, bloß weil der Aufrufer eine frische Funktion
  // gereicht hat.
  const onAtChangeRef = useRef(onAtChange);
  onAtChangeRef.current = onAtChange;
  const onSidChangeRef = useRef(onSidChange);
  onSidChangeRef.current = onSidChange;

  // ── Sitzungsliste und die gewählte Sitzung ────────────────────────────────────────────────
  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const sessions = useQuery({
    queryKey: ["office", "sessions", scopeKey, kiosk ? "kiosk" : "bedient"],
    queryFn: async () => {
      const basis = { limit: SESSION_LIMIT, sinceHours: SESSION_WINDOW_H };
      if (!kiosk) return officeApi.sessions(scope, basis);
      // Kiosk: erst die laufenden Räume — die sind das, was ein Wandschirm beantworten soll.
      // Läuft gerade nichts (nachts der Normalfall), fällt er auf die volle Liste zurück und
      // zeigt den zuletzt aktiven Raum, statt schwarz zu werden.
      const live = await officeApi.sessions(scope, { ...basis, status: "live" });
      return live.sessions.length ? live : officeApi.sessions(scope, basis);
    },
    refetchInterval: kiosk ? KIOSK_SESSION_REFETCH_MS : SESSION_REFETCH_MS,
    refetchOnWindowFocus: false,
    staleTime: kiosk ? 5000 : 10_000,
    retry: 1,
  });
  const liste: SessionSummary[] = sessions.data?.sessions ?? [];

  // Ohne Wahl die oberste: die Liste kommt nach letztem Ereignis absteigend, ein laufender
  // Raum steht also von selbst oben. Verschwindet die gewählte Sitzung aus dem Fenster
  // (`since_hours`), wird ebenfalls nachgerückt statt auf einen toten Raum zu zeigen.
  useEffect(() => {
    if (!liste.length) return;
    if (sidStr && liste.some((s) => s.sid === sidStr)) return;
    const naechste = liste.find((s) => s.live) ?? liste[0];
    setSidStr(naechste.sid);
    onSidChangeRef.current?.(naechste.sid);
  }, [liste, sidStr]);

  const sid = useMemo(() => parseSid(sidStr) ?? undefined, [sidStr]);
  const gewaehlt = liste.find((s) => s.sid === sidStr) ?? null;

  const { recorder, revision, roster, totals, live, error } = useOfficeFeed(scope, sid);

  // ── Sprungpunkt ───────────────────────────────────────────────────────────────────────────
  //
  // Ein Setzer statt eines Effekts auf `seekTs`: der Effekt liefe auch beim Einhängen und
  // schriebe den gerade gelesenen Wert sofort wieder in die URL zurück. Der Vergleich läuft
  // über ein Spiegel-Ref und **nicht** in einem `setState`-Aktualisierer — der darf keine
  // Nebenwirkung haben (React ruft ihn im Entwicklungsmodus absichtlich zweimal auf).
  const seekRef = useRef<number | null>(seekTs);
  seekRef.current = seekTs;

  const setSeek = useCallback((ts: number | null) => {
    if (seekRef.current === ts) return;
    seekRef.current = ts;
    setSeekTs(ts);
    onAtChangeRef.current?.(ts);
  }, []);

  const waehleSitzung = useCallback((s: string) => {
    setSidStr(s);
    onSidChangeRef.current?.(s);
    // Ein anderer Raum hat andere Figuren und eine andere Zeitachse — beides zurücksetzen,
    // sonst zeigte der Inspektor auf jemanden, der hier nie war.
    setSelectedId(null);
    setHoverId(undefined);
    setSessionFilter(null);
    setSeek(null);
  }, [setSeek]);

  // ── Raumrotation (nur Kiosk) ──────────────────────────────────────────────────────────────
  //
  // Der Effekt weiter oben rückt nur nach, wenn **keine** Sitzung gewählt ist oder die
  // gewählte aus dem Fenster fällt — richtig für einen Menschen, der sich einen Raum
  // ausgesucht hat. Der Wandschirm hat niemanden, der auswählt: er soll zeigen, wo gerade
  // etwas passiert. Also die zweite Regel, und sie ist bewusst schmal gehalten:
  // gewechselt wird nur, wenn hier lange nichts geschah **und** anderswo etwas läuft.
  // Ohne die zweite Hälfte tanzte die Wand nachts zwischen lauter toten Räumen hin und her.
  useEffect(() => {
    if (!kiosk || liste.length < 2) return;
    const tick = () => {
      const jetzt = Date.now();
      const aktuell = liste.find((s) => s.sid === sidStr);
      const zuletzt = aktuell?.last_event_at ? Date.parse(aktuell.last_event_at) : NaN;
      // Kein Zeitstempel = kein Beweis für Leben. Dann zählt der Raum als still.
      const still = !Number.isFinite(zuletzt) || jetzt - zuletzt > KIOSK_SWITCH_AFTER_MS;
      if (!still) return;
      // Die Liste kommt nach letztem Ereignis absteigend — der erste Treffer ist der
      // frischeste laufende Raum.
      const naechster = liste.find((s) => s.live && s.sid !== sidStr);
      if (naechster) waehleSitzung(naechster.sid);
    };
    const timer = window.setInterval(tick, KIOSK_ROTATE_TICK_MS);
    return () => window.clearInterval(timer);
  }, [kiosk, liste, sidStr, waehleSitzung]);

  // ── Der ⛶-Knopf (nur Kiosk) ───────────────────────────────────────────────────────────────
  //
  // `requestFullscreen()` braucht eine Nutzergeste und scheitert sonst **still** — automatisch
  // anfordern ist also keine Option, sondern nur eine, die nie funktioniert. Der Knopf ist die
  // Geste; nach `KIOSK_KNOPF_MS` Zeigerruhe verschwindet er wieder. In der Praxis startet die
  // Wand ohnehin als `chromium --kiosk` und braucht ihn nie.
  const [knopfSichtbar, setKnopfSichtbar] = useState(true);
  const knopfRef = useRef(true);
  useEffect(() => {
    if (!kiosk) return;
    let timer: number | null = null;
    const zeigen = (v: boolean) => {
      if (knopfRef.current === v) return;   // je Mausbewegung ein Renderdurchlauf wäre absurd
      knopfRef.current = v;
      setKnopfSichtbar(v);
    };
    const wach = () => {
      zeigen(true);
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => zeigen(false), KIOSK_KNOPF_MS);
    };
    wach();
    window.addEventListener("pointermove", wach, { passive: true });
    window.addEventListener("pointerdown", wach, { passive: true });
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      window.removeEventListener("pointermove", wach);
      window.removeEventListener("pointerdown", wach);
    };
  }, [kiosk]);

  const vollbildUmschalten = useCallback(() => {
    try {
      if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
      else void document.documentElement.requestFullscreen?.().catch(() => {});
    } catch {
      // Manche Browser werfen synchron statt abzulehnen. Ein Wandschirm ohne Vollbild ist
      // immer noch ein Wandschirm.
    }
  }, []);

  // ── Tastaturkarte ─────────────────────────────────────────────────────────────────────────
  //
  // Der Listener wird **einmal** angemeldet. Alles, was er wissen muss, liest er aus einem
  // Spiegel-Ref — sonst müsste er bei jedem Überfahren einer Figur neu registriert werden.
  const stand = useRef({
    voll, kiosk, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose,
  });
  stand.current = { voll, kiosk, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Schon verbraucht: Bühne (Alt+Pfeile, +/-/0/Pos1) und Zeitleiste (wandernder Fokus).
      if (e.defaultPrevented) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const ziel = e.target as HTMLElement | null;
      if (ziel) {
        const tag = ziel.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || ziel.isContentEditable) return;
        // Die Leertaste betätigt fokussierte Knöpfe — die gehört dann dem Knopf, nicht uns.
        if (e.key === " " && ziel.closest("button, a, [role='button']")) return;
      }
      const s = stand.current;

      // Kiosk: genau eine Taste. Alles andere (Dock, Tempo, Spulen, Hilfe) steuert etwas,
      // das dort gar nicht sichtbar ist — es wäre eine unsichtbare Bedienung, und die
      // hinterlässt einen Wandschirm, den niemand mehr versteht.
      if (s.kiosk) {
        if (e.key === "Escape" && s.onClose) { s.onClose(); e.preventDefault(); }
        return;
      }

      switch (e.key) {
        case "?":
          setHelpOpen((v) => !v);
          e.preventDefault();
          return;

        case "1": case "2": case "3": {
          if (!s.voll) return;                       // im Reiter gibt es kein Dock
          const t = DOCK_TABS[Number(e.key) - 1];
          if (!t) return;
          setDockTab(t.key);
          setDockOpen(true);
          e.preventDefault();
          return;
        }

        case "b": case "B":
          if (!s.voll) return;
          setDockOpen((v) => !v);
          e.preventDefault();
          return;

        case "l": case "L":
          setSeek(null);
          e.preventDefault();
          return;

        case " ":
          setPaused((v) => !v);
          e.preventDefault();
          return;

        case "ArrowLeft": case "ArrowRight": {
          const b = s.recorder.bounds();
          if (!b || b.t1 === 0) return;
          const schritt = (e.shiftKey ? STEP_GROSS_MS : STEP_MS) * (e.key === "ArrowLeft" ? -1 : 1);
          const basis = s.seekTs ?? b.t1;
          const ziel2 = basis + schritt;
          // Über das jüngste Ereignis hinaus gibt es nur einen sinnvollen Ort: die Gegenwart.
          setSeek(ziel2 >= b.t1 ? null : Math.max(b.t0, ziel2));
          e.preventDefault();
          return;
        }

        case "Escape":
          // Von innen nach außen abwickeln — jede Ebene genau eine Esc.
          if (s.helpOpen) { setHelpOpen(false); e.preventDefault(); return; }
          if (s.voll && s.dockOpen) { setDockOpen(false); e.preventDefault(); return; }
          if (s.seekTs !== null) { setSeek(null); e.preventDefault(); return; }
          if (s.selectedId !== null) { setSelectedId(null); e.preventDefault(); return; }
          if (s.voll && s.onClose) { s.onClose(); e.preventDefault(); }
          return;

        default:
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setSeek]);

  // ── Abgeleitetes ──────────────────────────────────────────────────────────────────────────
  // Der Sitzungsreiter ist ein Filter, kein Kanalwechsel (`TopBar.tsx`, Punkt 2): wer nicht
  // dazugehört, wird auf der Bühne **blass**, nicht entfernt. Entfernen gäbe seinen Sitzplatz
  // frei, ließe die Übergabelinien ins Nichts zeigen und zeigte je Reiter einen anderen Raum.
  // Ohne aktiven Filter bleibt die Menge `undefined` — dann kostet das Dimmen keinen Handgriff.
  const gedimmt = useMemo(() => {
    if (sessionFilter === null) return undefined;
    const out = new Set<string>();
    for (const r of roster) if (!passtZumFilter(scope, r, sessionFilter)) out.add(r.agent_id);
    return out;
  }, [roster, scope, sessionFilter]);

  const eintrag = useMemo(
    () => (selectedId === null ? null : roster.find((r) => r.agent_id === selectedId) ?? null),
    [roster, selectedId],
  );
  const titel = gewaehlt
    ? [gewaehlt.issue_key, gewaehlt.title].filter(Boolean).join(" · ")
    : undefined;
  const fehler = error
    ?? (sessions.error ? `Sitzungen nicht ladbar: ${(sessions.error as Error).message}` : undefined);

  // Der Wachhund der Kioskseite lebt außerhalb dieser Komponente (er lädt die Seite neu, das
  // ist keine Zuständigkeit einer Ansicht). Über den Spiegel-Ref bleibt der Effekt an `fehler`
  // hängen und nicht an der Identität des Rückrufs.
  const onErrorChangeRef = useRef(onErrorChange);
  onErrorChangeRef.current = onErrorChange;
  useEffect(() => { onErrorChangeRef.current?.(fehler); }, [fehler]);

  const kopf = (
    <TopBar
      scope={scope}
      titel={titel || undefined}
      roster={roster}
      totals={totals}
      // Die Pille sagt etwas über den **Strom**, nicht über die Wiedergabe: angehalten heißt
      // nicht getrennt. Das Anhalten steht in der Werkzeugleiste darunter.
      live={live}
      seekTs={seekTs}
      onBackToLive={() => setSeek(null)}
      speed={speed}
      onSpeedChange={(t) => { setSpeed(t); setPaused(false); }}
      filter={sessionFilter}
      onFilterChange={setSessionFilter}
      fullscreen={voll}
      onToggleFullscreen={voll ? onClose : onFullscreen}
      error={fehler}
      kiosk={kiosk}
    />
  );

  const werkzeugleiste = (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {liste.length > 1 && (
        <label className="flex min-w-0 items-center gap-1.5 text-muted">
          <span className="shrink-0">Sitzung</span>
          <select
            value={sidStr ?? ""}
            onChange={(e) => waehleSitzung(e.target.value)}
            title="Welcher Raum gezeigt wird. Die Reiter darüber filtern innerhalb dieses Raums."
            className="max-w-[22rem] truncate rounded border border-line bg-surface px-2 py-1 text-ink"
          >
            {liste.map((s) => (
              <option key={s.sid} value={s.sid}>
                {(s.live ? "● " : "") + [s.issue_key, s.title].filter(Boolean).join(" · ")}
              </option>
            ))}
          </select>
        </label>
      )}

      <button
        type="button"
        onClick={() => setPaused((v) => !v)}
        aria-pressed={paused}
        title={paused ? "Wiedergabe fortsetzen (Leertaste)" : "Wiedergabe anhalten (Leertaste)"}
        className="rounded border border-line px-2 py-1 text-muted hover:border-brand hover:text-ink"
      >
        {paused ? "▶ Fortsetzen" : "⏸ Anhalten"}
      </button>

      {voll && (
        <button
          type="button"
          onClick={() => setDockOpen((v) => !v)}
          aria-pressed={dockOpen}
          title="Dock und Inspektor ein- oder ausblenden (B)"
          className="rounded border border-line px-2 py-1 text-muted hover:border-brand hover:text-ink"
        >
          {dockOpen ? "▸ Dock ausblenden" : "◂ Dock einblenden"}
        </button>
      )}

      <div className="flex-1" />

      {sessions.isLoading && <span className="text-muted">Sitzungen laden…</span>}

      <button
        type="button"
        onClick={() => setHelpOpen(true)}
        title="Tastaturkürzel (?)"
        className="rounded border border-line px-2 py-1 text-muted hover:border-brand hover:text-ink"
      >
        ? Tasten
      </button>
    </div>
  );

  const buehne = (
    <Stage
      recorder={recorder}
      revision={revision}
      seekTs={seekTs}
      speed={paused ? 0 : speed}
      grade={grade}
      selected={selectedId ?? undefined}
      hover={hoverId}
      dimmed={gedimmt}
      onSelect={(id) => setSelectedId(id ?? null)}
      onHover={setHoverId}
      // Kiosk: die Bühne führt selbst Kamera (Welle „Kiosk-Kamera", `office/kiosk.ts`) —
      // sie folgt dem Geschehen, statt starr den ganzen Raum zu zeigen.
      kiosk={kiosk}
      className={grossflaechig
        ? "min-h-0 flex-1 rounded border border-line"
        : "aspect-[16/9] w-full rounded border border-line"}
    />
  );

  const zeitleiste = (
    <Timeline
      recorder={recorder}
      revision={revision}
      seekTs={seekTs}
      onSeek={(ts) => setSeek(ts)}
      className="shrink-0"
    />
  );

  // Der Wandschirm: Kopfzeile (schreibgeschützt), Bühne, ein einziger Knopf. Keine
  // Werkzeugleiste, kein Dock, kein Inspektor, keine Zeitleiste — nichts davon kann dort
  // jemand bedienen, und was niemand bedienen kann, verschenkt nur Fläche.
  if (kiosk) {
    return (
      <div className={`relative flex min-h-0 flex-col gap-2 ${className ?? ""}`}>
        {kopf}
        {buehne}
        <button
          type="button"
          onClick={vollbildUmschalten}
          title="Vollbild ein- oder ausschalten"
          aria-label="Vollbild umschalten"
          className={"absolute right-3 top-3 z-10 rounded border border-line bg-card/80 px-2 py-1 "
            + "text-sm text-muted transition-opacity hover:border-brand hover:text-ink "
            + (knopfSichtbar ? "opacity-80" : "pointer-events-none opacity-0")}
        >
          ⛶
        </button>
      </div>
    );
  }

  return (
    <div className={`flex min-h-0 flex-col gap-2 ${className ?? ""}`}>
      {kopf}
      {werkzeugleiste}

      {voll ? (
        <div className="flex min-h-0 flex-1 gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            {buehne}
            {zeitleiste}
          </div>
          {dockOpen && (
            <aside className="flex w-[24rem] shrink-0 flex-col gap-2 xl:w-[28rem]">
              <Dock
                scope={scope}
                tab={dockTab}
                onTabChange={setDockTab}
                recorder={recorder}
                revision={revision}
                roster={roster}
                filter={sessionFilter}
                seekTs={seekTs}
                selectedId={selectedId}
                onSelect={setSelectedId}
                className="min-h-0 flex-1"
              />
              <Inspector
                scope={scope}
                entry={eintrag}
                roster={roster}
                recorder={recorder}
                revision={revision}
                seekTs={seekTs}
                onSelect={setSelectedId}
                onClose={() => setSelectedId(null)}
                className="max-h-[45%] shrink-0"
              />
            </aside>
          )}
        </div>
      ) : (
        <>
          {buehne}
          {zeitleiste}
        </>
      )}

      {helpOpen && <Hilfe voll={voll} onClose={() => setHelpOpen(false)} />}
    </div>
  );
}

// ── Hilfe ───────────────────────────────────────────────────────────────────────────────────

/** Dieselbe Tabelle, die oben im Kopf dieser Datei steht — nur eben dort, wo man sie sucht. */
function Hilfe({ voll, onClose }: { voll: boolean; onClose: () => void }): JSX.Element {
  const zeilen: [string, string][] = [
    ["?", "Diese Hilfe ein- und ausblenden"],
    ...(voll ? ([
      ["1 2 3", "Dock: Chat, Agenten, Werkzeuge"],
      ["B", "Dock ein- oder ausblenden"],
    ] as [string, string][]) : []),
    ["L", "Zurück zu Live"],
    ["Leertaste", "Wiedergabe anhalten oder fortsetzen"],
    ["← →", "Eine Sekunde zurück oder vor (mit Umschalt zehn)"],
    ["Esc", voll
      ? "Hilfe, Dock, Wiedergabe, Auswahl — und zuletzt die Seite verlassen"
      : "Hilfe, Wiedergabe, Auswahl schließen"],
    ["Alt + ← ↑ → ↓", "Im Raum schwenken (Bühne muss den Fokus haben)"],
    ["+ − 0", "Zoomen und zurücksetzen (Bühne muss den Fokus haben)"],
  ];
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Tastaturkürzel des Büros"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-md overflow-y-auto rounded-lg border border-line bg-card p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold">🏢 Büro — Tastenkürzel</h2>
          <div className="flex-1" />
          <button type="button" onClick={onClose} autoFocus
            className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:border-brand hover:text-ink">
            Schließen
          </button>
        </div>
        <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1.5 text-xs">
          {zeilen.map(([taste, text]) => (
            <div key={taste} className="contents">
              <dt className="font-mono text-ink">{taste}</dt>
              <dd className="text-muted">{text}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-[11px] text-muted">
          In Textfeldern sind alle Kürzel aus — dort tippt man.
        </p>
      </div>
    </div>
  );
}
