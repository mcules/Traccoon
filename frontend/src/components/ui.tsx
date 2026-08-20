import { ReactNode, useEffect } from "react";
import { tr } from "../i18n";

/**
 * The two shapes every administrative list in here needs: an action on a row, and a form
 * that does not live in the row.
 *
 * Before this, every panel carried its own creation form permanently open under the list,
 * and editing an entry filled that very form, somewhere below, while the row one had
 * clicked stayed at the top. With nine kinds of entry that was nine slightly different
 * forms, and on a phone the fields wrapped into a column of inputs whose belonging to
 * anything was pure guesswork. Actions were words in a row ("edit", "delete", "test"),
 * which reads like a sentence and takes the width of one.
 */

/** Icons of the recurring actions. One place, so "delete" looks the same everywhere. */
export const ICON = {
  neu: "＋", bearbeiten: "✏️", loeschen: "🗑️", testen: "🧪", starten: "▶️",
  standard: "⭐", kopieren: "⧉", zurueck: "↩", oeffnen: "↗",
} as const;

/**
 * An action as an icon.
 *
 * `titel` is not decoration: it is the tooltip AND the accessible name, and without it an
 * icon button is a mystery to anybody not seeing the picture.
 */
export function IconKnopf({ icon, titel, onClick, gefahr = false, disabled = false, aktiv = false }: {
  icon: string; titel: string; onClick: () => void;
  gefahr?: boolean; disabled?: boolean; aktiv?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={titel}
      aria-label={titel}
      disabled={disabled}
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line text-sm leading-none transition-colors disabled:opacity-40 ${
        aktiv ? "bg-brand/20 text-brand"
          : gefahr ? "text-muted hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-400"
                   : "text-muted hover:bg-surface hover:text-ink"
      }`}
    >
      {icon}
    </button>
  );
}

/** The row of actions at the right hand end of an entry. */
export function Aktionen({ children }: { children: ReactNode }) {
  return <div className="flex shrink-0 items-center gap-1">{children}</div>;
}

/**
 * A dialog for creating and editing.
 *
 * Escape closes, a click beside it closes, and the body does not scroll underneath while it
 * is open. The heading says what is being edited, because the row it came from is covered.
 */
export function Dialog({ titel, onClose, children, fuss, breit = false }: {
  titel: string; onClose: () => void; children: ReactNode; fuss?: ReactNode; breit?: boolean;
}) {
  useEffect(() => {
    const zu = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", zu);
    const vorher = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", zu); document.body.style.overflow = vorher; };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose} role="dialog" aria-modal="true" aria-label={titel}>
      <div onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[88vh] w-full flex-col rounded-xl border border-line bg-card shadow-2xl ${
          breit ? "max-w-3xl" : "max-w-lg"}`}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold text-ink">{titel}</h2>
          <button onClick={onClose} title={tr("common.schliessen")} aria-label={tr("common.schliessen")}
            className="text-muted hover:text-ink">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {fuss && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">
            {fuss}
          </div>
        )}
      </div>
    </div>
  );
}

/** The two buttons at the foot of nearly every dialog. */
export function DialogFuss({ onAbbrechen, onSpeichern, speichernText, laeuft = false, deaktiviert = false }: {
  onAbbrechen: () => void; onSpeichern: () => void; speichernText?: string;
  laeuft?: boolean; deaktiviert?: boolean;
}) {
  return (
    <>
      <button onClick={onAbbrechen} className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink">
        {tr("common.abbrechen")}
      </button>
      <button onClick={onSpeichern} disabled={laeuft || deaktiviert}
        className="rounded bg-brand px-4 py-1.5 text-sm text-white disabled:opacity-50">
        {speichernText || tr("common.speichern")}
      </button>
    </>
  );
}

/** A labelled field in a dialog. Label above, hint below, both optional. */
export function Feld({ label, hinweis, children }: {
  label: string; hinweis?: string; children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className="mt-1">{children}</div>
      {hinweis && <span className="mt-1 block text-[11px] text-muted">{hinweis}</span>}
    </label>
  );
}

/** Input styling of the dialogs, so that eleven panels do not each invent their own. */
export const EINGABE = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none";

/**
 * A safety question before something that is hard to take back.
 *
 * As a dialog and not as a `confirm()`: the browser dialog cannot say WHAT is about to
 * happen to whom, and a list of nine similar entries is exactly the place where that
 * matters.
 */
export function BestaetigenDialog({ titel, text, hinweis, bestaetigenText, gefahr = true,
                                    onClose, onBestaetigen, laeuft = false }: {
  titel: string; text: string; hinweis?: string; bestaetigenText: string; gefahr?: boolean;
  onClose: () => void; onBestaetigen: () => void; laeuft?: boolean;
}) {
  return (
    <Dialog titel={titel} onClose={onClose} fuss={
      <>
        <button onClick={onClose} className="rounded border border-line px-3 py-1.5 text-sm text-muted hover:text-ink">
          {tr("common.abbrechen")}
        </button>
        <button onClick={onBestaetigen} disabled={laeuft}
          className={`rounded px-4 py-1.5 text-sm text-white disabled:opacity-50 ${
            gefahr ? "bg-red-600" : "bg-brand"}`}>
          {bestaetigenText}
        </button>
      </>
    }>
      <p className="text-sm text-ink">{text}</p>
      {hinweis && <p className="mt-2 text-xs text-muted">{hinweis}</p>}
    </Dialog>
  );
}

/** The delete case of the safety question, named after what disappears. */
export function LoeschDialog({ was, hinweis, onClose, onLoeschen, laeuft = false }: {
  was: string; hinweis?: string; onClose: () => void; onLoeschen: () => void; laeuft?: boolean;
}) {
  return (
    <BestaetigenDialog titel={tr("common.loeschen")} text={tr("common.wirklich_loeschen", { was })}
      hinweis={hinweis} bestaetigenText={tr("common.loeschen")} laeuft={laeuft}
      onClose={onClose} onBestaetigen={onLoeschen} />
  );
}

/**
 * A list of entries — the shape almost every panel here needs.
 *
 * Why not a table: five columns stand out over the edge of a phone, and what one does not
 * see one does not look for. Why not the loose rows we had: they carried `bg-card` INSIDE a
 * card of the same colour, so a 1px border was all that separated an entry from its
 * background, and a list of five looked like a wall of text.
 *
 * So: one surface, entries on the darker (light mode: greyer) layer, separated by lines. The
 * alignment of a table without its rigidity — the columns are a grid the caller passes in
 * (`spalten`), and below `sm` everything falls back into a wrapping row.
 */
export function Liste({ children, className = "" }: {
  children: ReactNode; className?: string;
}) {
  return (
    <div className={`overflow-hidden rounded-lg border border-line ${className}`}>
      <div className="divide-y divide-line">{children}</div>
    </div>
  );
}

/** The line that stands in place of entries when there are none. */
export function ListeLeer({ children }: { children: ReactNode }) {
  return <div className="bg-surface px-3 py-2.5 text-xs text-muted">{children}</div>;
}

/**
 * Column headings. Hidden on small screens, where the entries wrap anyway.
 *
 * Same surface as the entries below on purpose: a heading in the colour of the card put a
 * bright bar across the list and cut the very surface in two that was meant to hold it
 * together. What separates the heading is the type — small, quiet, spaced out — not a wall.
 */
export function ListenKopf({ spalten, children }: { spalten: string; children: ReactNode }) {
  return (
    <div className={`hidden gap-x-3 bg-surface px-3 pb-1 pt-2 text-[10px] uppercase tracking-wider text-muted/70 sm:grid ${spalten}`}>
      {children}
    </div>
  );
}

/**
 * One entry. `gedimmt` marks what is switched off — visible, but visibly not in service.
 *
 * With `onClick` the whole row becomes the way in (the usual case: open the thing). The
 * buttons inside stop the click themselves, so the row does not open behind a dialog.
 */
/** The classes of an entry — for the cases where the entry has to be a `<Link>` (middle
 *  click, context menu) and therefore cannot be a `ListenZeile`. */
export const ZEILE = "group block bg-surface px-3 py-2.5 text-sm transition-colors hover:bg-card";

export function ListenZeile({ spalten, gedimmt = false, warnung = false, dicht = false,
                             onClick, children }: {
  spalten?: string; gedimmt?: boolean; warnung?: boolean; dicht?: boolean;
  onClick?: () => void; children: ReactNode;
}) {
  // Without columns the entry only gets the surface and its padding; the layout inside is
  // the caller's business. That way an entry with a second line (a URL, a last run) does not
  // have to be pressed into a grid it does not want.
  const layout = spalten
    ? `flex flex-wrap items-center gap-x-3 gap-y-1 sm:grid ${spalten}`
    : "";
  return (
    <div
      onClick={onClick}
      className={`group bg-surface px-3 text-sm transition-colors ${dicht ? "py-1.5" : "py-2.5"} ${layout} ${
        onClick ? "cursor-pointer hover:bg-card" : ""} ${gedimmt ? "opacity-55" : ""} ${
        // Ein Streifen links statt einer eingefärbten Fläche: die Zeile bleibt lesbar, und
        // in einer langen Liste sieht man die auffälligen Einträge trotzdem von weitem.
        warnung ? "border-l-2 border-amber-400 pl-[calc(0.75rem-2px)]" : ""}`}
    >
      {children}
    </div>
  );
}

/**
 * The frame every tab of a page shares: one card, an explaining sentence, room for tools.
 *
 * Written down once because five tabs had grown five answers to the same question — one put
 * its list into a card, the next left it loose on the page, a third invented its own heading.
 * From a step away that reads as five pages instead of one.
 */
export function Bereich({ titel, nebentitel, hinweis, werkzeuge, children }: {
  titel?: ReactNode; nebentitel?: ReactNode; hinweis?: ReactNode; werkzeuge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      {titel && (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-semibold text-ink">{titel}</span>
          {nebentitel && <span className="font-mono text-xs text-muted">{nebentitel}</span>}
        </div>
      )}
      {hinweis && <p className="text-sm text-muted">{hinweis}</p>}
      {werkzeuge && (
        <div className="flex flex-wrap items-center gap-3 text-sm">{werkzeuge}</div>
      )}
      {children}
    </div>
  );
}

/**
 * Tabs INSIDE a page (assistant: chat · inbox · rules; a filter above a list).
 *
 * Looks like the page navigation on purpose, because it does the same thing: the eye should
 * not have to learn a second language for the same movement. What it does not do is change
 * the address — that stays the job of `usePageChrome`.
 */
export function Reiter<T extends string>({ aktiv, auswahl, onWaehlen, senkrecht = false }: {
  aktiv: T; auswahl: [T, string][]; onWaehlen: (wert: T) => void; senkrecht?: boolean;
}) {
  return (
    <div className={senkrecht
      ? "flex shrink-0 flex-row flex-wrap gap-1 sm:w-40 sm:flex-col sm:flex-nowrap sm:border-r sm:border-line sm:pr-3"
      : "flex flex-wrap gap-1 border-b border-line pb-2"}>
      {auswahl.map(([wert, label]) => (
        <button key={wert} onClick={() => onWaehlen(wert)}
          className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
            senkrecht ? "text-left" : ""} ${
            aktiv === wert
              ? "bg-brand/15 font-medium text-brand ring-1 ring-inset ring-brand/30"
              : "text-muted hover:bg-card hover:text-ink"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * A short, recurring value (kind, state, count): readable as a value, not as prose.
 *
 * The colour is a role, not a shade — that way an amber warning looks the same on every page
 * instead of being mixed anew each time (there were `bg-amber-500/15`, `/10` and `/20` in
 * three files, all meaning "watch out").
 */
export function Etikett({ farbe = "neutral", titel, children }: {
  farbe?: "neutral" | "gruen" | "gelb" | "rot" | "blau" | "violett" | "brand";
  titel?: string; children: ReactNode;
}) {
  const stil = {
    neutral: "border-line bg-card text-muted",
    gruen: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    gelb: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    rot: "border-red-500/30 bg-red-500/10 text-red-300",
    blau: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    violett: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    brand: "border-brand/40 bg-brand/15 text-brand",
  }[farbe];
  return (
    <span title={titel}
      className={`shrink-0 truncate rounded border px-1.5 py-0.5 text-[11px] ${stil}`}>
      {children}
    </span>
  );
}

/** Secondary action inside an entry ("versions", "history", "cancel"). */
export function Zeilenknopf({ onClick, titel, gefahr = false, children }: {
  onClick: () => void; titel?: string; gefahr?: boolean; children: ReactNode;
}) {
  return (
    <button onClick={(e) => { e.stopPropagation(); onClick(); }} title={titel}
      className={`shrink-0 rounded border border-line px-2 py-1 text-xs text-muted transition-colors ${
        gefahr ? "hover:border-red-400 hover:text-red-300" : "hover:border-brand hover:text-ink"}`}>
      {children}
    </button>
  );
}

/** State of an entry: a dot plus a word. Colour carries the urgency, the word the meaning. */
export function Zustand({ farbe, text }: { farbe: "gruen" | "gelb" | "grau" | "rot"; text: string }) {
  const punkt = { gruen: "bg-emerald-400", gelb: "bg-amber-400", grau: "bg-muted",
                  rot: "bg-red-400" }[farbe];
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${punkt}`} />
      {text}
    </span>
  );
}

/** Error line inside a dialog or above a list. */
export function Fehlerzeile({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {text}
    </div>
  );
}
