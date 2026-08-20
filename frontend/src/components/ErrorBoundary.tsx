// The safety net component: a render exception no longer tears the whole page down.
//
// ── Why it exists at all ────────────────────────────────────────────────────────────────────
//
// Until now the frontend had **no** ErrorBoundary at all. In React 18 that means an exception
// thrown while rendering unmounts the complete tree, and what is left is a white page. In
// front of a human that is annoying (they reload); in front of a wall screen it is final,
// because nobody stands there.
//
// ── Why the restart is throttled here ───────────────────────────────────────────────────────
//
// Automatic reloading heals exactly one case: a temporary state (an event the stage cannot
// digest, a half rolled out build). With a real program bug the fresh tree throws again
// immediately, and without a brake the screen would run into a reload loop and hammer the
// backend while doing so. `sicheresNeuladen` therefore remembers the last attempt per reason
// in the `sessionStorage` and allows the second one only after the minimum distance.
// Afterwards the message stays: failing visibly is better than circling silently.

import { Component, type ErrorInfo, type ReactNode } from "react";
import { KNOPF_KLEIN } from "./ui";

/** Prefix of the keys in the `sessionStorage`. Deliberately `session`, not `local`: a brake
 *  should brake a running tab, not the machine for tomorrow. */
const SPEICHER_PRAEFIX = "traccoon_reload:";

/**
 * Reloads the page, at most once per `grund` within `mindestAbstandMs`.
 *
 * It lies here and not with the watchdog because both need the same discipline and two
 * reload rules are guaranteed to drift apart. `true` = a reload happened.
 */
export function sicheresNeuladen(grund: string, mindestAbstandMs: number): boolean {
  const key = SPEICHER_PRAEFIX + grund;
  try {
    const vorher = Number(sessionStorage.getItem(key) ?? "0");
    const jetzt = Date.now();
    if (Number.isFinite(vorher) && jetzt - vorher < mindestAbstandMs) {
      console.warn(`[traccoon] Neuladen (${grund}) unterdrückt — zuletzt vor `
        + `${Math.round((jetzt - vorher) / 1000)} s.`);
      return false;
    }
    sessionStorage.setItem(key, String(jetzt));
  } catch {
    // No storage (private mode, cookies switched off): then unthrottled. Without reloading
    // the screen would surely stay dead; with it there is at least a chance.
  }
  console.warn(`[traccoon] Seite wird neu geladen (${grund}).`);
  location.reload();
  return true;
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Reload by itself after this many milliseconds. Without the value nothing happens by
   *  itself, which is the right default everywhere a human is sitting in front of it. */
  reloadAfterMs?: number;
  /** Mindestabstand zweier automatischer Neuladeversuche. Siehe Dateikopf. */
  reloadMinGapMs?: number;
  /** How the reason is named in the console and in the brake. */
  label?: string;
}

interface State {
  fehler: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, State> {
  state: State = { fehler: null };
  private timer: number | null = null;

  static getDerivedStateFromError(fehler: Error): State {
    return { fehler };
  }

  componentDidCatch(fehler: Error, info: ErrorInfo): void {
    console.error(`[traccoon] Renderfehler in ${this.props.label ?? "der Ansicht"}:`,
      fehler, info.componentStack);
    const nach = this.props.reloadAfterMs;
    if (nach === undefined || this.timer !== null) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      sicheresNeuladen(`boundary:${this.props.label ?? "view"}`,
        this.props.reloadMinGapMs ?? 10 * 60_000);
    }, nach);
  }

  componentWillUnmount(): void {
    if (this.timer !== null) window.clearTimeout(this.timer);
  }

  private jetztNeuladen = () => location.reload();

  render(): ReactNode {
    const { fehler } = this.state;
    if (!fehler) return this.props.children;
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-2xl">🛠️</div>
        <div className="text-sm font-semibold text-ink">
          Diese Ansicht ist ausgestiegen.
        </div>
        <div className="max-w-lg break-words text-xs text-muted">
          {fehler.message || String(fehler)}
        </div>
        {this.props.reloadAfterMs !== undefined && (
          <div className="text-xs text-muted">
            Es wird gleich von selbst neu geladen.
          </div>
        )}
        <button
          type="button"
          onClick={this.jetztNeuladen}
          className={KNOPF_KLEIN.neben}
        >
          Jetzt neu laden
        </button>
      </div>
    );
  }
}
