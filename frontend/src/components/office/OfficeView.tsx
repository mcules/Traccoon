// Layer 2, the assembly. One view, three places of use: as a project tab (bounded, without
// dock and inspector), as a full page and as a wall screen (`?kiosk=1`).
//
// ══ The kiosk is a variant, not a route ════════════════════════════════════════════════════
//
// `variant="kiosk"` hides instead of rebuilding: toolbar, dock, inspector, timeline and
// everything operable in the header fall away, the rest is the same code. A route of its own
// would have been a copy of `pages/Office.tsx`, and `?project=`, `?sid=` and `?at=` would have
// had to be maintained in both.
//
// Zwei Dinge unterscheiden ihn im Verhalten:
//
//   · **Room rotation.** The ordinary follow up effect only applies when no session is chosen
//     at all or the chosen one falls out of the window. A wall screen needs more: 516 of 632
//     runs finish in under five minutes, so a room once chosen is dead most of the time. Hence:
//     if nothing happens for `KIOSK_SWITCH_AFTER_MS` and another room is live, it switches.
//     if nothing happens for `KIOSK_SWITCH_AFTER_MS` and another room is live, it switches.
//   · **Keyboard on `Escape`.** There is no keyboard in front of the wall; whoever touches one
//     anyway should be able to leave the kiosk and trigger nothing else.
//
// ══ What this file owns, and what it explicitly does not ═══════════════════════════════════
//
// It holds the state that changes at human pace: selection, hover, seek point, dock tab, speed,
// session filter, help. **Not** here live recorder, replay, camera and image: those sit in refs
// inside `Stage` and `useOfficeFeed`, and exactly for that reason a running room costs not a
// single render pass of this component.
//
// ══ One room or all, and why all is the default globally ═══════════════════════════════════
//
// `useOfficeFeed(scope, sid)` serves **one** session. The scope only says *which* sessions come
// into question, so this view picks one: it fetches the session list (`officeApi.sessions`) and
// takes the top one, since the list already arrives sorted by the last event, descending. Above
// it stands a picker.
//
// **Globally "all sessions" is the default** (`ALLE`). The full page `/buero` answers "what is
// the house doing right now", and a single room is the wrong excerpt for that: 516 of 632 runs
// finish in under five minutes, the chosen room is dead most of the time while work happens
// next door. The feed then takes `GET /office/events` as the snapshot and live every event the
// socket delivers (which already filters server side to what is allowed). The window is
// `ALLE_FENSTER_H` hours and stands **visibly** in the header: a silent excerpt would be a claim
// about the day.
//
// **In the project tab it stays one session.** There a ticket is the room, and the tab is the
// view of that one ticket, not of the project. So the mode hangs on `scope.kind`, and
// `useOfficeFeed` demands an explicit `opts` for it: "no `sid`" alone only means "the list is
// still on its way" in the tab.
//
// The picker is **not** the same as the session tabs in the header: those are a filter on the
// roster (dim, do not remove) and only become properly useful with "all", because they then
// group by project instead of by the one project that stood everywhere anyway.
//
// ══ The keyboard map ═══════════════════════════════════════════════════════════════════════
//
// The first global keyboard listener of the application. Three rules keep it compatible:
//
//   1. Only while the view is mounted (the `useEffect` cleanup unregisters it).
//   2. An early exit on `input`/`textarea`/`contentEditable`: the dock has a search field, and
//      nobody wants to toggle the dock while typing "bla".
//   3. An early exit on `e.defaultPrevented`. The listener sits at the end of the bubble phase,
//      so every component that already consumed the key has called `preventDefault`: the stage
//      for Alt plus arrows and `+ - 0 Home`, the timeline for its travelling focus. Without this
//      line every arrow key press in the timeline would **additionally** seek by one second.
//      line every arrow key press in the timeline would additionally seek by one second.
//
// No ⌘K/Ctrl+K (that belongs to the browser), and everything with Ctrl or Cmd stays untouched.

import { tr } from "../../i18n";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Stage from "./Stage.tsx";
import Timeline from "./Timeline.tsx";
import Dock, { DOCK_TABS, type DockTab } from "./Dock.tsx";
import Inspector from "./Inspector.tsx";
import TopBar, { passtZumFilter, type Tempo } from "./TopBar.tsx";
import { officeApi, parseSid, sidKey, type Scope, type SessionSummary } from "./api.ts";
import { ALL_WINDOW_H, useOfficeFeed } from "./useOfficeFeed.ts";
import { useTheme } from "./useTheme.ts";
import { BUTTON, BUTTON_KLEIN} from "../ui";

// ── Stellschrauben ──────────────────────────────────────────────────────────────────────────

/** The value of the picker for "all sessions". Deliberately not a valid `Sid`: `parseSid`
 *  returns `null` for it, and exactly by that the feed recognises the mode. It also stands in
 *  the URL (`?sid=alle`), so a shared link shows the same room. */
const ALL = "alle";

/** One arrow key press seeks this far, ten times as far with shift. */
const STEP_MS = 1000;
const STEP_GROSS_MS = 10_000;

/** The picker fetches this many sessions. Nobody searches through more than that. */
const SESSION_LIMIT = 30;

/** The session list is an ordinary query: it changes when a run begins or ends, not by the
 *  second. The live stream hangs on the **chosen** session. */
const SESSION_REFETCH_MS = 30_000;

/** How far the session list looks back. The backend is content with a week, which is the right
 *  default for a live monitor and the wrong one for this view: a project whose last run was
 *  twelve days ago would otherwise show an empty room plus the claim that there had never been
 *  an agent there. The office is a look back, not an alarm clock; how fresh
 *  a session is, is said by its `live` flag anyway. The retention caps the window by itself:
 *  archived runs disappear after `run_retention_days` (30 days by default). */
const SESSION_WINDOW_H = 24 * 180;

/** Kiosk: if nothing happens in the room shown for this long and another one is `live`, it
 *  switches. A minute and a half is longer than any thinking pause of an agent (the backend
 *  itself stops calling a room "live" after 90 s without an event) and short enough that the
 *  wall does not show an empty desk for minutes. */
const KIOSK_SWITCH_AFTER_MS = 90_000;

/** How often the kiosk checks whether it should move on. Purely computational, no network. */
const KIOSK_ROTATE_TICK_MS = 5000;

/** Kiosk: the session list **is** the control here, it decides which room stands on the wall.
 *  Hence denser than the 30 s of the operated view. */
const KIOSK_SESSION_REFETCH_MS = 15_000;

/** After this long without pointer movement the ⛶ button disappears. It is the only control the
 *  kiosk needs (full screen demands a user gesture) and the only one that disturbs. */
const KIOSK_BUTTON_MS = 5000;

// ── Interface ───────────────────────────────────────────────────────────────────────────────

export interface OfficeViewProps {
  scope: Scope;
  /** `"tab"` is in the project tab (no dock, no inspector), `"full"` the full page, `"kiosk"`
   *  the wall screen without controls. */
  variant: "tab" | "full" | "kiosk";
  /** Start of playback in epoch ms, `null` or missing means live. Read on mount only. */
  initialAt?: number | null;
  /** Reports every change of the seek point: the full page writes it into the URL. */
  onAtChange?: (ts: number | null) => void;
  /** Only `variant="tab"`: "⤢ full screen". */
  onFullscreen?: () => void;
  /** `variant="full"`: "⤡ leave full screen" and the last Esc.
   *  `variant="kiosk"`: the **only** Esc, and it leaves the wall screen. */
  onClose?: () => void;
  /** Reports the current error message (or `undefined`) outwards. The watchdog of the kiosk
   *  page hangs on it: it can only reload what it also sees. */
  onErrorChange?: (error: string | undefined) => void;
  /** Preselected session (`"issue:412"`), from the URL for instance. Read on mount only. */
  initialSid?: string | null;
  /** Reports the change of room: the full page writes it into the URL so that a shared link
   *  points at the same room as the seek point inside it. */
  onSidChange?: (sid: string | null) => void;
  className?: string;
}

// ── The view ────────────────────────────────────────────────────────────────────────────────

export default function OfficeView({
  scope, variant, initialAt, onAtChange, onFullscreen, onClose,
  initialSid, onSidChange, onErrorChange, className,
}: OfficeViewProps): JSX.Element {
  const voll = variant === "full";
  const kiosk = variant === "kiosk";
  /** The stage fills the area instead of sitting in a 16:9 box; applies to both large forms. */
  const grossflaechig = voll || kiosk;

  // ── Zustand ────────────────────────────────────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | undefined>(undefined);
  const [seekTs, setSeekTs] = useState<number | null>(initialAt ?? null);
  const [dockTab, setDockTab] = useState<DockTab>("chat");
  const [dockOpen, setDockOpen] = useState(true);
  /** The **chosen** speed. Paused is a switch of its own, so that the space bar brings the
   *  previous value back instead of jumping bluntly to 1×. */
  const [speed, setSpeed] = useState<Tempo>(1);
  const [paused, setPaused] = useState(false);
  const [sessionFilter, setSessionFilter] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  /** Is there an "all sessions" here at all? Globally yes, in the project tab no (a ticket is
   *  the room there), and on the wall screen no either: that one rotates through the running
   *  rooms itself and needs a concrete one for that. */
  const allMoeglich = scope.kind === "global" && !kiosk;
  const [sidStr, setSidStr] = useState<string | null>(
    initialSid ?? (allMoeglich ? ALL : null));
  const allModus = allMoeglich && sidStr === ALL;

  const grade = useTheme();

  // Callbacks as mirror refs: they are called from effects and from the keyboard listener, and
  // neither should run again just because the caller passed a fresh function.
  // gereicht hat.
  const onAtChangeRef = useRef(onAtChange);
  onAtChangeRef.current = onAtChange;
  const onSidChangeRef = useRef(onSidChange);
  onSidChangeRef.current = onSidChange;

  // ── Session list and the chosen session ───────────────────────────────────────────────────
  const scopeKey = scope.kind === "project" ? `project:${scope.projectId}` : "global";
  const sessions = useQuery({
    queryKey: ["office", "sessions", scopeKey, kiosk ? "kiosk" : "bedient"],
    queryFn: async () => {
      const basis = { limit: SESSION_LIMIT, sinceHours: SESSION_WINDOW_H };
      if (!kiosk) return officeApi.sessions(scope, basis);
      // Kiosk: the running rooms first, because that is what a wall screen should answer. When
      // nothing runs (the normal case at night) it falls back to the full list and shows the
      // room that was active last instead of going black.
      const live = await officeApi.sessions(scope, { ...basis, status: "live" });
      return live.sessions.length ? live : officeApi.sessions(scope, basis);
    },
    refetchInterval: kiosk ? KIOSK_SESSION_REFETCH_MS : SESSION_REFETCH_MS,
    refetchOnWindowFocus: false,
    staleTime: kiosk ? 5000 : 10_000,
    retry: 1,
  });
  const listing: SessionSummary[] = sessions.data?.sessions ?? [];

  // Without a choice the top one: the list arrives sorted by the last event, descending, so a
  // running room stands at the top by itself. When the chosen session drops out of the window
  // (`since_hours`) it follows up as well instead of pointing at a dead room.
  useEffect(() => {
    // "All" is a choice, not a missing value: nothing follows up here.
    if (allModus) return;
    // …unless the scope cannot do it at all: `?sid=alle` in the project tab (or on the wall
    // screen) has to fall onto a real room, otherwise the stage would stay empty.
    if (!listing.length) return;
    if (sidStr && sidStr !== ALL && listing.some((s) => s.sid === sidStr)) return;
    const naechste = listing.find((s) => s.live) ?? listing[0];
    setSidStr(naechste.sid);
    onSidChangeRef.current?.(naechste.sid);
  }, [listing, sidStr, allModus]);

  const sid = useMemo(() => parseSid(sidStr) ?? undefined, [sidStr]);
  const gewaehlt = listing.find((s) => s.sid === sidStr) ?? null;

  const { recorder, revision, roster, totals, live, error } = useOfficeFeed(
    scope, sid, { alleSitzungen: allModus, sinceHours: ALL_WINDOW_H });

  // ── Sprungpunkt ───────────────────────────────────────────────────────────────────────────
  //
  // A setter instead of an effect on `seekTs`: the effect would also run on mount and write the
  // value just read straight back into the URL. The comparison runs through a mirror ref and
  // **not** in a `setState` updater, which must have no side effect (React deliberately calls
  // it twice in development mode).
  const seekRef = useRef<number | null>(seekTs);
  seekRef.current = seekTs;

  const setSeek = useCallback((ts: number | null) => {
    if (seekRef.current === ts) return;
    seekRef.current = ts;
    setSeekTs(ts);
    onAtChangeRef.current?.(ts);
  }, []);

  const waehleSession = useCallback((s: string) => {
    setSidStr(s);
    onSidChangeRef.current?.(s);
    // Another room has other characters and another time axis: reset both, otherwise the
    // inspector would point at somebody who was never here.
    setSelectedId(null);
    setHoverId(undefined);
    setSessionFilter(null);
    setSeek(null);
  }, [setSeek]);

  // ── Room rotation (kiosk only) ────────────────────────────────────────────────────────────
  //
  // The effect further up only follows up when **no** session is chosen or the chosen one falls
  // out of the window, which is right for a person who picked a room. The wall screen has
  // nobody to pick: it should show where something is happening. Hence the second rule, and it
  // is deliberately narrow: it switches only when nothing happened here for a long time **and**
  // something runs elsewhere. Without the second half the wall would dance between dead rooms
  // at night.
  useEffect(() => {
    if (!kiosk || listing.length < 2) return;
    const tick = () => {
      const now = Date.now();
      const current = listing.find((s) => s.sid === sidStr);
      const latest = current?.last_event_at ? Date.parse(current.last_event_at) : NaN;
      // No timestamp means no proof of life. Then the room counts as quiet.
      const still = !Number.isFinite(latest) || now - latest > KIOSK_SWITCH_AFTER_MS;
      if (!still) return;
      // The list arrives sorted by the last event, descending, so the first hit is the
      // frischeste laufende Raum.
      const naechster = listing.find((s) => s.live && s.sid !== sidStr);
      if (naechster) waehleSession(naechster.sid);
    };
    const timer = window.setInterval(tick, KIOSK_ROTATE_TICK_MS);
    return () => window.clearInterval(timer);
  }, [kiosk, listing, sidStr, waehleSession]);

  // ── The ⛶ button (kiosk only) ─────────────────────────────────────────────────────────────
  //
  // `requestFullscreen()` needs a user gesture and fails **silently** otherwise, so requesting
  // it automatically is not an option but only one that never works. The button is the gesture;
  // after `KIOSK_KNOPF_MS` without pointer movement it disappears again. In practice the wall
  // starts as `chromium --kiosk` anyway and never needs it.
  const [buttonVisible, setButtonVisible] = useState(true);
  const buttonRef = useRef(true);
  useEffect(() => {
    if (!kiosk) return;
    let timer: number | null = null;
    const zeigen = (v: boolean) => {
      if (buttonRef.current === v) return;   // je Mausbewegung ein Renderdurchlauf wäre absurd
      buttonRef.current = v;
      setButtonVisible(v);
    };
    const wach = () => {
      zeigen(true);
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => zeigen(false), KIOSK_BUTTON_MS);
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
      // Some browsers throw synchronously instead of rejecting. A wall screen without full
      // screen is still a wall screen.
    }
  }, []);

  // ── Tastaturkarte ─────────────────────────────────────────────────────────────────────────
  //
  // The listener is registered **once**. Everything it needs to know it reads from a mirror
  // ref, otherwise it would have to be re-registered on every hover over a character.
  const state = useRef({
    voll, kiosk, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose,
  });
  state.current = { voll, kiosk, dockOpen, helpOpen, seekTs, selectedId, recorder, onClose };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Already consumed: the stage (Alt plus arrows, +/-/0/Home) and the timeline (travelling focus).
      if (e.defaultPrevented) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) return;
        // The space bar activates focused buttons, and then it belongs to the button, not to us.
        if (e.key === " " && target.closest("button, a, [role='button']")) return;
      }
      const s = state.current;

      // Kiosk: exactly one key. Everything else (dock, speed, seeking, help) controls something
      // that is not visible there at all, so it would be an invisible control, and that leaves
      // behind a wall screen nobody understands any more.
      if (s.kiosk) {
        if (e.key === "Escape" && s.onClose) { s.onClose(); e.preventDefault(); }
        return;
      }

      switch (e.key) {
        case "?":
          setHelpOpen((v) => !v);
          e.preventDefault();
          return;

        // The mapping is generic (`DOCK_TABS[digit - 1]`): a fifth tab would only need its
        // digit here. `4` is the personnel file.
        case "1": case "2": case "3": case "4": {
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
          const step = (e.shiftKey ? STEP_GROSS_MS : STEP_MS) * (e.key === "ArrowLeft" ? -1 : 1);
          const basis = s.seekTs ?? b.t1;
          const ziel2 = basis + step;
          // Beyond the newest event there is only one sensible place: the present.
          setSeek(ziel2 >= b.t1 ? null : Math.max(b.t0, ziel2));
          e.preventDefault();
          return;
        }

        case "Escape":
          // Unwind from the inside out, one Esc per level.
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
  // The session tab is a filter, not a change of channel (`TopBar.tsx`, point 2): whoever does
  // not belong goes **pale** on the stage, it is not removed. Removing would free its seat, let
  // the handover lines point into nothing and show a different room per tab. Without an active
  // filter the set stays `undefined`, and then the dimming costs no work at all.
  const gedimmt = useMemo(() => {
    if (sessionFilter === null) return undefined;
    const out = new Set<string>();
    for (const r of roster) if (!passtZumFilter(scope, r, sessionFilter)) out.add(r.agent_id);
    return out;
  }, [roster, scope, sessionFilter]);

  const entry = useMemo(
    () => (selectedId === null ? null : roster.find((r) => r.agent_id === selectedId) ?? null),
    [roster, selectedId],
  );
  // The window belongs in the heading, not in a footnote: the room shows an excerpt, and an
  // unnamed excerpt would look like "there was nothing more going on".
  const title = allModus
    ? tr("office_view.alle_sitzungen_fenster", { stunden: ALL_WINDOW_H })
    : (gewaehlt ? [gewaehlt.issue_key, gewaehlt.title].filter(Boolean).join(" · ") : undefined);
  // `error` is already taken by the destructuring above — this is the text shown, not the
  // error object itself.
  const errorText = error
    ?? (sessions.error ? tr("office_view.sitzungen_nicht_ladbar", { fehler: (sessions.error as Error).message }) : undefined);

  // The watchdog of the kiosk page lives outside this component (it reloads the page, which is
  // not the business of a view). Through the mirror ref the effect stays tied to `fehler` and
  // not to the identity of the callback.
  const onErrorChangeRef = useRef(onErrorChange);
  onErrorChangeRef.current = onErrorChange;
  useEffect(() => { onErrorChangeRef.current?.(error); }, [error]);

  const header = (
    <TopBar
      scope={scope}
      titel={title || undefined}
      roster={roster}
      totals={totals}
      // The pill says something about the **stream**, not about playback: paused does not mean
      // disconnected. Pausing stands in the toolbar below it.
      live={live}
      seekTs={seekTs}
      onBackToLive={() => setSeek(null)}
      speed={speed}
      onSpeedChange={(t) => { setSpeed(t); setPaused(false); }}
      filter={sessionFilter}
      onFilterChange={setSessionFilter}
      onFullscreen={voll ? undefined : onFullscreen}
      error={error}
      kiosk={kiosk}
    />
  );

  const werkzeugleiste = (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {(allMoeglich || listing.length > 1) && (
        <label className="flex min-w-0 items-center gap-1.5 text-muted">
          <span className="shrink-0">Sitzung</span>
          <select
            value={sidStr ?? ""}
            onChange={(e) => waehleSession(e.target.value)}
            title={tr("office_view.welcher_raum")}
            className="max-w-[22rem] truncate rounded border border-line bg-surface px-2 py-1 text-ink"
          >
            {/* Erste Option und Vorgabe: der ganze Betrieb in einem Raum. */}
            {allMoeglich && (
              <option value={ALL}>{tr("office_view.alle_sitzungen_option", { stunden: ALL_WINDOW_H })}</option>
            )}
            {listing.map((s) => (
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
        className={BUTTON_KLEIN.neben}
      >
        {paused ? "▶ Fortsetzen" : "⏸ Anhalten"}
      </button>

      {voll && (
        <button
          type="button"
          onClick={() => setDockOpen((v) => !v)}
          aria-pressed={dockOpen}
          title={tr("office_view.dock_umschalten")}
          className={BUTTON_KLEIN.neben}
        >
          {dockOpen ? "▸ Dock ausblenden" : "◂ Dock einblenden"}
        </button>
      )}

      <div className="flex-1" />

      {sessions.isLoading && <span className="text-muted">Sitzungen laden…</span>}

      <button
        type="button"
        onClick={() => setHelpOpen(true)}
        title={tr("office_view.tastenkuerzel")}
        className={BUTTON_KLEIN.neben}
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
      // Kiosk: the stage steers the camera itself (`office/kiosk.ts`), following the action
      // instead of rigidly showing the whole room.
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

  // The wall screen: header (read only), stage, one single button. No toolbar, no dock, no
  // inspector, no timeline: nobody can operate any of that there, and what nobody can operate
  // only wastes space.
  if (kiosk) {
    return (
      <div className={`relative flex min-h-0 flex-col gap-2 ${className ?? ""}`}>
        {header}
        {buehne}
        <button
          type="button"
          onClick={vollbildUmschalten}
          title={tr("office_view.vollbild")}
          aria-label="Vollbild umschalten"
          className={"absolute right-3 top-3 z-10 rounded border border-line bg-card/80 px-2 py-1 "
            + "text-sm text-muted transition-opacity hover:border-brand hover:text-ink "
            + (buttonVisible ? "opacity-80" : "pointer-events-none opacity-0")}
        >
          ⛶
        </button>
      </div>
    );
  }

  return (
    <div className={`flex min-h-0 flex-col gap-2 ${className ?? ""}`}>
      {header}
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
                entry={entry}
                roster={roster}
                recorder={recorder}
                revision={revision}
                seekTs={seekTs}
                onSelect={setSelectedId}
                onClose={() => setSelectedId(null)}
                // The entry into the file: the tab above jumps to the **role** of this run (the
                // dock reads the role from `selectedId`), the inspector stays on the **single
                // run**. Both truths at once, neither replaces the other.
                // andere.
                onOpenAkte={() => { setDockTab("akte"); setDockOpen(true); }}
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

/** The same table that stands in the header of this file, only where one looks for it. */
function Hilfe({ voll, onClose }: { voll: boolean; onClose: () => void }): JSX.Element {
  const lines: [string, string][] = [
    ["?", tr("office_view.hilfe_umschalten")],
    ...(voll ? ([
      ["1 2 3 4", "Dock: Chat, Agenten, Werkzeuge, Personalakte"],
      ["B", tr("office_view.dock_taste")],
    ] as [string, string][]) : []),
    ["L", tr("buero.zurueck_zu_live")],
    [tr("office_view.leertaste"), tr("office_view.wiedergabe_taste")],
    ["← →", tr("office_view.pfeile")],
    ["Esc", voll
      ? tr("office_view.esc_voll")
      : tr("office_view.esc_reiter")],
    ["Alt + ← ↑ → ↓", tr("office_view.schwenken")],
    ["+ − 0", tr("office_view.zoomen")],
  ];
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={tr("office_view.tastenkuerzel_titel")}
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-md overflow-y-auto rounded-lg border border-line bg-card p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold">🏢 {tr("office_view.tastenkuerzel_titel")}</h2>
          <div className="flex-1" />
          <button type="button" onClick={onClose} autoFocus
            className={BUTTON_KLEIN.neben}>
            {tr("office_view.schliessen")}
          </button>
        </div>
        <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1.5 text-xs">
          {lines.map(([taste, text]) => (
            <div key={taste} className="contents">
              <dt className="font-mono text-ink">{taste}</dt>
              <dd className="text-muted">{text}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-[11px] text-muted">
          {tr("office_view.textfelder")}
        </p>
      </div>
    </div>
  );
}
