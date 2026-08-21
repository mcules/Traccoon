// The global full screen page of the office, `/buero`.
//
// ── Why it stays inside `<Layout>` and still covers everything ──────────────────────────────
//
// Exactly following the model of `WorkflowEditor.tsx`: the page stays an ordinary route
// inside `<PageChromeProvider><Layout>` and renders `fixed inset-0 z-30`. Two things hang off
// that, and both would be broken without it:
//
//   · **`z-30` covers the header.** That one is `sticky top-0 z-10`; everything below would
//     otherwise lie under it instead of above it.
//   · **No `usePageChrome`.** The hook cleans up on leaving (`setChrome({title:"",tabs:[]})`),
//     so not calling it is what wipes away the sub-menu of the previous page. If this page set
//     tabs of its own they would stand under the full screen and nobody would see them.
//
// ── What is in the URL ──────────────────────────────────────────────────────────────────────
//
//   `?project=KEY`  Scope. Without it the scope is global, all projects the user may see
//                   (authorised server side, never here).
//   `?sid=issue:412` The room. `officeApi.sessions` delivers the list; without a value the
//                   view takes the most recent one. Without this parameter `?at=` would be
//                   pointless: a moment without a room points at nothing.
//   `?at=<epoch-ms>` Starting point of the replay.
//   `?kiosk=1`      Wall screen: no operation, no header, self-healing on.
//
// Writing uses `replace: true`: a shared link to a moment is the whole intention, but every
// click on the timeline as an entry in the history would be an imposition on the back button.
//
// ══ The kiosk ═══════════════════════════════════════════════════════════════════════════════
//
// **One parameter, not a second route.** A route of its own would have been a copy of this
// file, and `?project=`/`?sid=`/`?at=` would have had to be maintained twice from then on.
// The default is the **global** scope: a wall screen answers "what is the house doing right
// now"; 516 of 632 runs finish in under five minutes, so a single project room would be an
// empty desk most of the time. `?kiosk=1&project=TRA` remains possible nevertheless.
//
// **Full screen is not requested automatically.** `requestFullscreen()` demands a user
// gesture and fails silently otherwise, which is why the view has a ⛶ button and there is no
// line about it here. In practice the wall starts as `chromium --kiosk`; whoever "repairs"
// that later builds a function that never worked.
//
// ── Self-healing: what watches here, and what already healed before ─────────────────────────
//
//   · **WS abort**: healed by `useOfficeFeed` itself (backoff plus full snapshot).
//     Deliberately nothing stands here.
//   · **Token expired**: `useTokenKeepalive` renews every six hours; without it the screen
//     would be a login form after `jwt_expire_minutes` (720).
//   · **Socket close code 4401/4403**: reconnecting does not help against that, but a fresh
//     page load with a fresh token does, so a reload after `WACHHUND_AUTH_MS`.
//   · **Contract breach (`hello.v` !== `EVENT_VERSION`)**: reload immediately. It means a new
//     frontend has been rolled out; reloading is not the emergency brake here but exactly the
//     right answer.
//   · **Every other permanent error**: after `WACHHUND_FEHLER_MS`.
//   · **Render exception**: caught by the `ErrorBoundary`, which reloads after ten seconds.
//   · **Once every night**: the recorder holds up to `REPLAY_CAP` entries and the DOM has run
//     a whole day; a cut at a fixed hour costs nothing and clears everything.
//
// All reload paths run through `sicheresNeuladen` and are thereby secured against the one
// danger that is worse than a dead screen: the reload loop.
//
// ── Why the watchdog reads the error message instead of an error code ───────────────────────
//
// `useOfficeFeed` reports its state today as **one** German sentence. Extending it there with
// a machine readable reason would mean touching a file that belongs to another wave. That is
// why `frist()` recognises the two special cases by a piece of text: close to the source,
// with a reference, and fail-safe, because whoever recognises nothing gets the long deadline.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tr } from "../i18n";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Project } from "../api";
import OfficeView from "../components/office/OfficeView.tsx";
import ErrorBoundary, { safeReload } from "../components/ErrorBoundary.tsx";
import type { Scope } from "../components/office/api.ts";
import { useWakeLock } from "../hooks/useWakeLock.ts";
import { useTokenKeepalive } from "../hooks/useTokenKeepalive.ts";
import { projectPath } from "../projectTabs";
import { RAIL_LEAVEBLANK } from "../nav";
import { BUTTON } from "../components/ui";

// ── Adjustable settings of the watchdog ─────────────────────────────────────────────────────

/** This long an error may stand before a reload happens. Two minutes are longer than any
 *  backend restart and than the full reconnect staircase of the feed; what stays that long
 *  does not go away by itself any more. */
const WATCHDOG_ERROR_MS = 120_000;

/** Authentication error of the socket (4401/4403). Shorter, because here a fresh page load
 *  with a renewed token can actually change something. */
const WATCHDOG_AUTH_MS = 60_000;

/** Mindestabstand zweier automatischer Neuladeversuche desselben Grundes. */
const RELOAD_DISTANCE_MS = 10 * 60_000;

/** Render exception: wait briefly (perhaps it was an event that is over in a moment), then
 *  rebuild. */
const BOUNDARY_RELOAD_MS = 10_000;

/** The nightly cut, local time. 4 o'clock: after the 19:00 peak and before everything that
 *  starts up in the morning. */
const NIGHT_HOUR = 4;

/**
 * How long this error may stand. `0` means immediately.
 *
 * The pieces of text come verbatim from `components/office/useOfficeFeed.ts` (socket
 * `onclose` respectively `hello` handling). If the wording changes there, the long deadline
 * silently takes hold here: worse, but never wrong.
 */
function deadline(notice: string): number {
  if (notice.includes("Vertragsversion")) return 0;
  if (notice.includes("Keine Berechtigung")) return WATCHDOG_AUTH_MS;
  return WATCHDOG_ERROR_MS;
}

function reasonFrom(notice: string): string {
  if (notice.includes("Vertragsversion")) return "vertragsbruch";
  if (notice.includes("Keine Berechtigung")) return "socket-auth";
  return "dauerfehler";
}

/** Milliseconds until the next full `stunde` in local time. */
function toHour(hour: number): number {
  const now = new Date();
  const target = new Date(now);
  target.setHours(hour, 0, 0, 0);
  if (target.getTime() <= now.getTime()) target.setDate(target.getDate() + 1);
  return target.getTime() - now.getTime();
}

export default function Office(): JSX.Element {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const projectKey = params.get("project");
  const kioskRaw = params.get("kiosk");
  const kiosk = kioskRaw === "1" || kioskRaw === "true";

  // The same query as in the header (`ProjectSwitcher`), from the cache, not fresh.
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projectKey ? projects?.find((p) => p.key === projectKey) : undefined;

  // Read only **once**: the view owns the jump point and the room itself afterwards, and
  // reading back from the URL would turn every write into a circle.
  const start = useRef<{ at: number | null; sid: string | null }>({
    at: (() => {
      const raw = params.get("at");
      const n = raw === null ? NaN : Number(raw);
      return Number.isFinite(n) && n > 0 ? n : null;
    })(),
    sid: params.get("sid"),
  });

  const scope = useMemo<Scope>(
    () => (project
      ? { kind: "project", projectId: project.id, projectKey: project.key }
      : { kind: "global" }),
    [project?.id, project?.key],
  );

  /** One writer for both parameters: they never change independently of each other. */
  const write = useCallback((field: "at" | "sid", value: string | null) => {
    setParams((before) => {
      const next = new URLSearchParams(before);
      if (value === null) next.delete(field);
      else next.set(field, value);
      return next;
    }, { replace: true });
  }, [setParams]);

  const onAtChange = useCallback(
    (ts: number | null) => write("at", ts === null ? null : String(ts)),
    [write],
  );
  const onSidChange = useCallback(
    (sid: string | null) => write("sid", sid),
    [write],
  );

  // Back to where the office came from: into the project tab when a project is involved,
  // otherwise to the project list.
  const back = () => navigate(projectKey ? projectPath(projectKey, "operations", "office") : "/");

  /** Esc in the kiosk: one level back into the operable full screen page, not out of the
   *  office; room and jump point stay, because usually you want to intervene right here. */
  const kioskLeave = useCallback(() => {
    setParams((before) => {
      const next = new URLSearchParams(before);
      next.delete("kiosk");
      return next;
    }, { replace: true });
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
  }, [setParams]);

  // ── Selbstheilung ─────────────────────────────────────────────────────────────────────────

  useWakeLock(kiosk);
  useTokenKeepalive(kiosk);

  const [error, setError] = useState<string | undefined>(undefined);

  // Watchdog for permanent errors. The timer is reset on **every** change of the message: an
  // error that comes and goes is not a permanent error, and another error is another case.
  useEffect(() => {
    if (!kiosk || !error) return;
    const waittime = deadline(error);
    const reason = reasonFrom(error);
    if (waittime === 0) {
      // Contract breach: a new frontend lies on the server, this one here is from yesterday.
      safeReload(reason, RELOAD_DISTANCE_MS);
      return;
    }
    const timer = window.setTimeout(
      () => safeReload(reason, RELOAD_DISTANCE_MS), waittime);
    return () => window.clearTimeout(timer);
  }, [kiosk, error]);

  // The nightly cut. A single timer instead of a clock query on a beat: the deadline is
  // always under 24 h and therefore fits comfortably into `setTimeout`.
  useEffect(() => {
    if (!kiosk) return;
    const timer = window.setTimeout(
      () => safeReload("nacht", RELOAD_DISTANCE_MS), toHour(NIGHT_HOUR));
    return () => window.clearTimeout(timer);
  }, [kiosk]);

  // If `?project=` is in the URL but the project list is still on its way, the scope would be
  // "global" for the blink of an eye, and the feed would build its socket twice.
  const waits = !!projectKey && isLoading;

  return (
    // Die Bereichsschiene bleibt frei: das Büro deckt die Seite zu, nicht den Weg hinaus.
    // Im Kiosk gibt es keinen Weg hinaus (Wandschirm), dort deckt es wirklich alles.
    <div className={`fixed inset-0 z-30 flex flex-col bg-surface ${kiosk ? "" : RAIL_LEAVEBLANK}`}>
      {/* Die Kopfzeile dieser Seite (Zurück-Knopf) ist Bedienung — im Kiosk fällt sie weg.
          Was der Wandschirm an Beschriftung braucht, steht in der Kopfzeile der Ansicht. */}
      {!kiosk && (
        <div className="flex shrink-0 items-center gap-3 border-b border-line bg-card px-4 py-2">
          {/* Zurück nur mit Projekt-Bezug: dorthin führt die Bereichsschiene nicht. Aus dem
              globalen Büro geht es über die Schiene hinaus, ein zweiter Ausgang wäre nur ein
              zweiter Ort, an dem man ihn sucht. */}
          {projectKey && (
            <button
              onClick={back}
              className={BUTTON.secondary}
            >
              ← {tr("office.zurueck_projekt")}
            </button>
          )}
          <h1 className="text-sm font-semibold">{tr("office.buero")}</h1>
          <span className="font-mono text-xs text-muted">
            {projectKey ?? tr("office.alle_projekte")}
          </span>
          {!!projectKey && !isLoading && !project && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
              title={tr("office.kein_projekt_mit_diesem_schluessel_die_a")}>
              unbekanntes Projekt
            </span>
          )}
        </div>
      )}

      {waits ? (
        <div className="p-4 text-sm text-muted">{tr("office.buero_laedt")}</div>
      ) : (
        <ErrorBoundary
          label="buero"
          reloadAfterMs={kiosk ? BOUNDARY_RELOAD_MS : undefined}
          reloadMinGapMs={RELOAD_DISTANCE_MS}
        >
          <OfficeView
            scope={scope}
            variant={kiosk ? "kiosk" : "full"}
            initialAt={start.current.at}
            onAtChange={onAtChange}
            initialSid={start.current.sid}
            onSidChange={onSidChange}
            onErrorChange={setError}
            onClose={kiosk ? kioskLeave : back}
            className="min-h-0 flex-1 p-3"
          />
        </ErrorBoundary>
      )}
    </div>
  );
}
