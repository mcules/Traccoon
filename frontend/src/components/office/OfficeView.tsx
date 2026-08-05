// Schicht 2 — der Zusammenbau. Eine Ansicht, zwei Einsatzorte: als Projekt-Reiter (begrenzt,
// ohne Dock und Inspektor) und als Vollbildseite.
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

// ── Oberfläche ──────────────────────────────────────────────────────────────────────────────

export interface OfficeViewProps {
  scope: Scope;
  /** `"tab"` = im Projekt-Reiter (kein Dock, kein Inspektor), `"full"` = Vollbildseite. */
  variant: "tab" | "full";
  /** Startpunkt der Wiedergabe in Epoch-ms, `null`/fehlt = live. Nur beim Einhängen gelesen. */
  initialAt?: number | null;
  /** Meldet jeden Wechsel des Sprungpunkts — die Vollbildseite schreibt ihn in die URL. */
  onAtChange?: (ts: number | null) => void;
  /** Nur `variant="tab"`: „⤢ Vollbild". */
  onFullscreen?: () => void;
  /** Nur `variant="full"`: „⤡ Vollbild verlassen" und das letzte Esc. */
  onClose?: () => void;
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
  initialSid, onSidChange, className,
}: OfficeViewProps): JSX.Element {
  const voll = variant === "full";

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
    queryKey: ["office", "sessions", scopeKey],
    queryFn: () => officeApi.sessions(scope, { limit: SESSION_LIMIT }),
    refetchInterval: SESSION_REFETCH_MS,
    refetchOnWindowFocus: false,
    staleTime: 10_000,
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

  // ── Tastaturkarte ─────────────────────────────────────────────────────────────────────────
  //
  // Der Listener wird **einmal** angemeldet. Alles, was er wissen muss, liest er aus einem
  // Spiegel-Ref — sonst müsste er bei jedem Überfahren einer Figur neu registriert werden.
  const stand = useRef({
    voll, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose,
  });
  stand.current = { voll, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose };

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
      className={voll
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
