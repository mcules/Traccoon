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
 * Ein Knopf.
 *
 * Ein Knopf ist blau. Vorher waren die meisten grau umrandet, und grau ist die Farbe, mit
 * der eine Oberfläche „hier ist nichts zu holen" sagt: In einer Kopfzeile mit vier davon
 * ging jeder einzelne unter, und die Farbe, die eigentlich „abgeschaltet" heißt, war der
 * Normalzustand.
 *
 * Deshalb gilt hier: **Grau heißt abgeschaltet, sonst nichts.**
 *
 * Drei Arten, mehr braucht es nicht:
 *
 * * `haupt` — die eine Handlung, um die es auf dieser Fläche geht (gefüllt blau).
 * * `neben` — alles andere, was man tun kann (blauer Rahmen, blaue Schrift). Bleibt lesbar
 *   und ordnet sich der Hauptsache unter, ohne ins Graue zu rutschen.
 * * `gefahr` — was man nicht versehentlich tut (rot).
 *
 * `zeichen` ist das Kurzzeichen für schmale Bildschirme: Dort steht nur es, sonst der Text.
 * `stand` hängt ein Ergebnis an den Knopf (✓/✗) — für Handlungen, deren Ausgang man später
 * noch sehen will, ohne sie zu wiederholen.
 */
export type ButtonArt = "haupt" | "neben" | "zusage" | "gefahr";
export type ButtonState = "gut" | "schlecht" | "offen";

/**
 * Die Klassen dazu, für die Stellen, die (noch) ein blankes `<button>` brauchen —
 * abgeschaltete Zustände inbegriffen, damit auch dort Grau nur „geht gerade nicht" heißt.
 *
 * Eine Quelle, zwei Zugänge: Die Komponente unten benutzt dieselben Zeilen. Neu geschrieben
 * wird mit `<Knopf>`; die Konstanten sind für Knöpfe mit eigener Mechanik (Umschalter,
 * Dateiauswahl, Reiter), die keine Komponente sein wollen.
 */
const RUMPF = "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 "
  + "text-sm leading-none transition-colors disabled:cursor-not-allowed "
  + "disabled:border-line disabled:bg-transparent disabled:text-muted";
// Blau heißt: die FLÄCHE ist blau, nicht die Schrift. Ein Knopf mit blauem Rahmen und
// blauer Schrift ist immer noch überwiegend Hintergrund — und damit fast so leise wie der
// graue, den er ablösen sollte.
const FARBE = {
  haupt: "border border-brand bg-brand text-white hover:bg-brand/90",
  // Grün ist keine Willkür, sondern eine Bedeutung wie Rot: „ich stimme zu" — freigeben,
  // abnehmen, bestätigen. Deshalb bleibt sie, statt in Blau aufzugehen.
  zusage: "border border-green-600 bg-green-600 text-white hover:bg-green-600/90",
  // Optisch gleich: „haupt" sagt im Code, worum es auf der Fläche geht, und ist kein
  // Versprechen auf ein anderes Aussehen. Wer später abstufen will, ändert diese eine Zeile.
  neben: "border border-brand bg-brand text-white hover:bg-brand/90",
  gefahr: "border border-red-600 bg-red-600 text-white hover:bg-red-600/90",
} as const;
export const BUTTON = {
  haupt: `${RUMPF} ${FARBE.haupt}`,
  neben: `${RUMPF} ${FARBE.neben}`,
  zusage: `${RUMPF} ${FARBE.zusage}`,
  gefahr: `${RUMPF} ${FARBE.gefahr}`,
} as const;

/**
 * Handlungen ohne Fläche: „mehr anzeigen", ein × zum Entfernen, ein Link in einer Zeile.
 *
 * Auch hier gilt Grau nur für abgeschaltet — eine Aktion, die es gibt, ist blau. Ein
 * Textknopf ist trotzdem kein Knopf mit Fläche: Er ordnet sich dem Text unter, in dem er
 * steht, statt ihn zu unterbrechen.
 */
export const BUTTON_TEXT = {
  neben: "text-brand transition-colors hover:underline disabled:text-muted disabled:no-underline",
  gefahr: "text-red-400 transition-colors hover:text-red-300 disabled:text-muted",
} as const;

/** Dieselben Knöpfe in klein — für Zeilen und Werkzeugleisten, wo die volle Höhe die
 *  Zeile auseinanderzöge. Farbe und Bedeutung bleiben gleich. */
const RUMPF_KLEIN = RUMPF.replace("px-3 py-1.5 text-sm", "px-2 py-1 text-xs");
export const BUTTON_KLEIN = {
  haupt: `${RUMPF_KLEIN} ${FARBE.haupt}`,
  neben: `${RUMPF_KLEIN} ${FARBE.neben}`,
  zusage: `${RUMPF_KLEIN} ${FARBE.zusage}`,
  gefahr: `${RUMPF_KLEIN} ${FARBE.gefahr}`,
} as const;

export function Button({ art = "neben", zeichen: chars, stand: state, titel: title, onClick, type = "button",
                        disabled = false, laeuft: running = false, breit = false, klein = false,
                        children }: {
  art?: ButtonArt; zeichen?: string; stand?: ButtonState; titel?: string;
  onClick?: () => void; type?: "button" | "submit"; disabled?: boolean; laeuft?: boolean;
  breit?: boolean; klein?: boolean; children: ReactNode;
}) {
  const aus = disabled || running;
  // Abgeschaltet ist abgeschaltet: keine Farbe, kein Zeiger, kein Hover. Sonst sieht ein
  // Knopf, der gerade nichts kann, aus wie einer, der etwas kann.
  const farbe = aus
    ? `${klein ? RUMPF_KLEIN : RUMPF} border border-line bg-transparent text-muted cursor-not-allowed`
    : (klein ? BUTTON_KLEIN : BUTTON)[art];
  const zeigen = { gut: "✓", schlecht: "✗", offen: "" }[state ?? "offen"];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={aus}
      title={title}
      className={`${breit ? "w-full" : ""} ${farbe}`}
    >
      {zeigen && (
        <span className={state === "gut" ? "text-green-400" : "text-red-400"}>{zeigen}</span>
      )}
      {chars && <span className="sm:hidden">{chars}</span>}
      <span className={chars ? "hidden sm:inline" : ""}>{children}</span>
    </button>
  );
}

/**
 * An action as an icon.
 *
 * `titel` is not decoration: it is the tooltip AND the accessible name, and without it an
 * icon button is a mystery to anybody not seeing the picture.
 */
export function IconButton({ icon, titel: title, onClick, gefahr = false, disabled = false, aktiv = false }: {
  icon: string; titel: string; onClick: () => void;
  gefahr?: boolean; disabled?: boolean; aktiv?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      // Auch hier: Grau heißt abgeschaltet. Ein Handgriff, den es gibt, ist blau — nur
      // ohne Füllung, sonst wäre eine Liste mit zwanzig Zeilen ein Feuerwerk.
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-sm
        leading-none transition-colors disabled:border-line disabled:text-muted
        disabled:opacity-60 ${
        aktiv ? "border-brand bg-brand text-white ring-2 ring-brand/40"
          : gefahr ? "border-red-600 bg-red-600 text-white hover:bg-red-600/90"
                   : "border-brand bg-brand text-white hover:bg-brand/90"
      }`}
    >
      {icon}
    </button>
  );
}

/** The row of actions at the right hand end of an entry. */
export function Actions({ children }: { children: ReactNode }) {
  return <div className="flex shrink-0 items-center gap-1">{children}</div>;
}

/**
 * A dialog for creating and editing.
 *
 * Escape closes, a click beside it closes, and the body does not scroll underneath while it
 * is open. The heading says what is being edited, because the row it came from is covered.
 */
export function Dialog({ titel: title, onClose, children, fuss, breit = false, festhalten = false }: {
  titel: string; onClose: () => void; children: ReactNode; fuss?: ReactNode; breit?: boolean;
  /** Kein Escape, kein Klick daneben — nur die eigenen Knöpfe schließen.
   *
   *  Für Dialoge, in denen etwas entsteht: Ein danebengegangener Klick hat einen halb
   *  geschriebenen Text gekostet, und „Escape" tippt man beim Formulieren schneller, als man
   *  denkt. Wo nur ausgewählt wird, bleibt beides — dort ist Wegklicken bequem, nicht teuer. */
  festhalten?: boolean;
}) {
  useEffect(() => {
    const zu = (e: KeyboardEvent) => { if (e.key === "Escape" && !festhalten) onClose(); };
    window.addEventListener("keydown", zu);
    const vorher = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", zu); document.body.style.overflow = vorher; };
  }, [onClose, festhalten]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onClick={festhalten ? undefined : onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[88vh] w-full flex-col rounded-xl border border-line bg-card shadow-2xl ${
          breit ? "max-w-3xl" : "max-w-lg"}`}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold text-ink">{title}</h2>
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
export function DialogFuss({ onAbbrechen: onCancel, onSpeichern, speichernText, laeuft: running = false, deaktiviert = false }: {
  onAbbrechen: () => void; onSpeichern: () => void; speichernText?: string;
  laeuft?: boolean; deaktiviert?: boolean;
}) {
  return (
    <>
      <Button onClick={onCancel}>{tr("common.abbrechen")}</Button>
      <Button art="haupt" onClick={onSpeichern} disabled={deaktiviert} laeuft={running}>
        {speichernText || tr("common.speichern")}
      </Button>
    </>
  );
}

/** A labelled field in a dialog. Label above, hint below, both optional. */
export function Field({ label, hinweis: hint, children }: {
  label: string; hinweis?: string; children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

/** Input styling of the dialogs, so that eleven panels do not each invent their own. */
export const INPUT_VALUE = "w-full rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink outline-none";

/**
 * A safety question before something that is hard to take back.
 *
 * As a dialog and not as a `confirm()`: the browser dialog cannot say WHAT is about to
 * happen to whom, and a list of nine similar entries is exactly the place where that
 * matters.
 */
export function ConfirmDialog({ titel: title, text, hinweis: hint, bestaetigenText: confirmText, gefahr = true,
                                    onClose, onBestaetigen: onConfirm, laeuft: running = false }: {
  titel: string; text: string; hinweis?: string; bestaetigenText: string; gefahr?: boolean;
  onClose: () => void; onBestaetigen: () => void; laeuft?: boolean;
}) {
  return (
    <Dialog titel={title} onClose={onClose} fuss={
      <>
        <Button onClick={onClose}>{tr("common.abbrechen")}</Button>
        <Button art={gefahr ? "gefahr" : "haupt"} onClick={onConfirm} laeuft={running}>
          {confirmText}
        </Button>
      </>
    }>
      <p className="text-sm text-ink">{text}</p>
      {hint && <p className="mt-2 text-xs text-muted">{hint}</p>}
    </Dialog>
  );
}

/** The delete case of the safety question, named after what disappears. */
export function LoeschDialog({ was, hinweis: hint, onClose, onLoeschen: onDelete, laeuft: running = false }: {
  was: string; hinweis?: string; onClose: () => void; onLoeschen: () => void; laeuft?: boolean;
}) {
  return (
    <ConfirmDialog titel={tr("common.loeschen")} text={tr("common.wirklich_loeschen", { was })}
      hinweis={hint} bestaetigenText={tr("common.loeschen")} laeuft={running}
      onClose={onClose} onBestaetigen={onDelete} />
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
export function Listing({ children, className = "" }: {
  children: ReactNode; className?: string;
}) {
  return (
    <div className={`overflow-hidden rounded-lg border border-line ${className}`}>
      <div className="divide-y divide-line">{children}</div>
    </div>
  );
}

/** The line that stands in place of entries when there are none. */
export function ListingLeer({ children }: { children: ReactNode }) {
  return <div className="bg-surface px-3 py-2.5 text-xs text-muted">{children}</div>;
}

/**
 * Column headings. Hidden on small screens, where the entries wrap anyway.
 *
 * Same surface as the entries below on purpose: a heading in the colour of the card put a
 * bright bar across the list and cut the very surface in two that was meant to hold it
 * together. What separates the heading is the type — small, quiet, spaced out — not a wall.
 */
export function ListenHeader({ spalten, children }: { spalten: string; children: ReactNode }) {
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
export const LINE = "group block bg-surface px-3 py-2.5 text-sm transition-colors hover:bg-card";

export function ListenLine({ spalten, gedimmt = false, warnung = false, dicht = false,
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
export function Area({ titel: title, nebentitel, hinweis: hint, werkzeuge: tools, children }: {
  titel?: ReactNode; nebentitel?: ReactNode; hinweis?: ReactNode; werkzeuge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      {title && (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-semibold text-ink">{title}</span>
          {nebentitel && <span className="font-mono text-xs text-muted">{nebentitel}</span>}
        </div>
      )}
      {hint && <p className="text-sm text-muted">{hint}</p>}
      {tools && (
        <div className="flex flex-wrap items-center gap-3 text-sm">{tools}</div>
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
export function Reiter<T extends string>({ aktiv, auswahl: selection, onWaehlen, senkrecht = false }: {
  aktiv: T; auswahl: [T, string][]; onWaehlen: (value: T) => void; senkrecht?: boolean;
}) {
  return (
    <div className={senkrecht
      ? "flex shrink-0 flex-row flex-wrap gap-1 sm:w-40 sm:flex-col sm:flex-nowrap sm:border-r sm:border-line sm:pr-3"
      : "flex flex-wrap gap-1 border-b border-line pb-2"}>
      {selection.map(([value, label]) => (
        <button key={value} onClick={() => onWaehlen(value)}
          className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
            senkrecht ? "text-left" : ""} ${
            aktiv === value
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
export function Etikett({ farbe = "neutral", titel: title, children }: {
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
    <span title={title}
      className={`shrink-0 truncate rounded border px-1.5 py-0.5 text-[11px] ${stil}`}>
      {children}
    </span>
  );
}

/** Secondary action inside an entry ("versions", "history", "cancel"). */
export function Zeilenknopf({ onClick, titel: title, gefahr = false, children }: {
  onClick: () => void; titel?: string; gefahr?: boolean; children: ReactNode;
}) {
  return (
    // Auch der Zeilenknopf ist ein Knopf: blau, nicht grau (siehe DESIGN.md).
    <button onClick={(e) => { e.stopPropagation(); onClick(); }} title={title}
      className={gefahr ? BUTTON_KLEIN.gefahr : BUTTON_KLEIN.neben}>
      {children}
    </button>
  );
}

/** State of an entry: a dot plus a word. Colour carries the urgency, the word the meaning. */
export function State({ farbe, text }: { farbe: "gruen" | "gelb" | "grau" | "rot"; text: string }) {
  const point = { gruen: "bg-emerald-400", gelb: "bg-amber-400", grau: "bg-muted",
                  rot: "bg-red-400" }[farbe];
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${point}`} />
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
