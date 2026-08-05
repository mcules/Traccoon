// Der Auffangnetz-Baustein: eine Render-Ausnahme reißt nicht mehr die ganze Seite mit.
//
// ── Warum es die überhaupt gibt ─────────────────────────────────────────────────────────────
//
// Bis hierher hatte das Frontend **keine** einzige ErrorBoundary. In React 18 heißt das: eine
// geworfene Ausnahme im Rendern hängt den kompletten Baum aus, und übrig bleibt eine weiße
// Seite. Vor einem Menschen ist das ärgerlich (er lädt neu); vor einem Wandschirm ist es
// endgültig, denn dort steht niemand.
//
// ── Warum der Neustart hier gedrosselt wird ─────────────────────────────────────────────────
//
// Automatisches Neuladen heilt genau einen Fall: einen vorübergehenden Zustand (ein Ereignis,
// das die Bühne nicht verdaut, ein halb ausgerollter Bau). Bei einem echten Programmfehler
// wirft der frische Baum sofort wieder — ohne Bremse liefe der Schirm in eine Neulade-Schleife
// und hämmerte dabei das Backend. `sicheresNeuladen` merkt sich deshalb je Grund den letzten
// Versuch in der `sessionStorage` und lässt den zweiten erst nach dem Mindestabstand zu.
// Danach bleibt die Meldung stehen — sichtbar zu scheitern ist besser als still zu kreisen.

import { Component, type ErrorInfo, type ReactNode } from "react";

/** Vorsatz der Schlüssel in der `sessionStorage`. Bewusst `session`, nicht `local`: eine
 *  Bremse soll einen laufenden Tab bremsen, nicht den Rechner für morgen. */
const SPEICHER_PRAEFIX = "traccoon_reload:";

/**
 * Lädt die Seite neu — höchstens einmal je `grund` innerhalb von `mindestAbstandMs`.
 *
 * Liegt hier und nicht beim Wachhund, weil beide dieselbe Disziplin brauchen und zwei
 * Neulade-Regeln garantiert auseinanderdriften. `true` = es wurde neu geladen.
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
    // Kein Speicher (privater Modus, abgeschaltete Cookies): dann eben ungebremst. Ohne
    // Neuladen bliebe der Schirm sicher tot, mit ihm besteht wenigstens eine Chance.
  }
  console.warn(`[traccoon] Seite wird neu geladen (${grund}).`);
  location.reload();
  return true;
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Nach so vielen Millisekunden von selbst neu laden. Fehlt der Wert, passiert nichts von
   *  allein — das ist die richtige Vorgabe überall dort, wo ein Mensch davorsitzt. */
  reloadAfterMs?: number;
  /** Mindestabstand zweier automatischer Neuladeversuche. Siehe Dateikopf. */
  reloadMinGapMs?: number;
  /** Womit der Grund in der Konsole und in der Bremse benannt wird. */
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
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:border-brand hover:text-ink"
        >
          Jetzt neu laden
        </button>
      </div>
    );
  }
}
