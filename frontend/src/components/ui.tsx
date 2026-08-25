import { ReactNode, RefObject, useEffect, useRef, useState } from "react";
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
  fresh: "＋", edit: "✏️", remove: "🗑️", testing: "🧪", start: "▶️",
  standard: "⭐", copy: "⧉", back: "↩", open: "↗",
} as const;

/**
 * Ein Knopf.
 *
 * A button is blue. Before, most of them had a grey border, and grey is the colour a UI says
 * "nothing to get here" with: in a header with four of them every single one went under, and
 * the colour that actually means "disabled" was the
 * Normalzustand.
 *
 * So the rule here is: **grey means disabled, nothing else.**
 *
 * Drei Arten, mehr braucht es nicht:
 *
 * * `primary` — the one action this surface is about (filled blue).
 * * `secondary` — everything else one can do (blue border, blue text). Stays readable and
 *   subordinates itself to the main matter without sliding into grey.
 * * `danger` — what one does not do by accident (red).
 *
 * `symbol` is the short sign for narrow screens: there only it stands, otherwise the text.
 * `state` hangs a result on the button (✓/✗) — for actions whose outcome one wants to see
 * later without repeating them.
 */
export type ButtonVariant = "primary" | "secondary" | "confirm" | "danger";
export type ButtonState = "good" | "bad" | "open";

/**
 * The classes for it, for the places that (still) need a bare `<button>` — disabled states
 * included, so that grey only means "not possible right now" there as well.
 *
 * One source, two entrances: the component below uses the same lines. New code is written
 * with `<Button>`; the constants are for buttons with a mechanism of their own (toggles, file
 * pickers, tabs) that do not want to be a component.
 */
const BASE = "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 "
  + "text-sm leading-none transition-colors disabled:cursor-not-allowed "
  + "disabled:border-line disabled:bg-transparent disabled:text-muted";
// Blue means: the SURFACE is blue, not the text. A button with a blue border and blue text is
// still mostly background — and thereby almost as quiet as the grey one it was meant to
// replace.
const COLOR = {
  primary: "border border-brand bg-brand text-white hover:bg-brand/90",
  // Green is no arbitrariness but a meaning like red: "I agree" — release, accept, confirm.
  // That is why it stays instead of being absorbed into blue.
  confirm: "border border-green-600 bg-green-600 text-white hover:bg-green-600/90",
  // Visually the same: "primary" says in the code what the surface is about and is no promise
  // of a different look. Whoever wants to grade them later changes this one line.
  secondary: "border border-brand bg-brand text-white hover:bg-brand/90",
  danger: "border border-red-600 bg-red-600 text-white hover:bg-red-600/90",
} as const;
export const BUTTON = {
  primary: `${BASE} ${COLOR.primary}`,
  secondary: `${BASE} ${COLOR.secondary}`,
  confirm: `${BASE} ${COLOR.confirm}`,
  danger: `${BASE} ${COLOR.danger}`,
} as const;

/**
 * Actions without a surface: "show more", an × to remove, a link inside a line.
 *
 * Here too grey only stands for disabled — an action that exists is blue. A text button is
 * still no button with a surface: it subordinates itself to the text it stands in
 * steht, statt ihn zu unterbrechen.
 */
export const BUTTON_TEXT = {
  secondary: "text-brand transition-colors hover:underline disabled:text-muted disabled:no-underline",
  danger: "text-red-400 transition-colors hover:text-red-300 disabled:text-muted",
} as const;

/** The same buttons in small — for rows and toolbars where the full height would pull the
 *  line apart. Colour and meaning stay the same. */
const BASE_SMALL = BASE.replace("px-3 py-1.5 text-sm", "px-2 py-1 text-xs");
export const BUTTON_SMALL = {
  primary: `${BASE_SMALL} ${COLOR.primary}`,
  secondary: `${BASE_SMALL} ${COLOR.secondary}`,
  confirm: `${BASE_SMALL} ${COLOR.confirm}`,
  danger: `${BASE_SMALL} ${COLOR.danger}`,
} as const;

export function Button({ variant = "secondary", symbol: chars, state, title: title, onClick, type = "button",
                        disabled = false, runs: running = false, wide = false, small = false,
                        children }: {
  variant?: ButtonVariant; symbol?: string; state?: ButtonState; title?: string;
  onClick?: () => void; type?: "button" | "submit"; disabled?: boolean; runs?: boolean;
  wide?: boolean; small?: boolean; children: ReactNode;
}) {
  const from = disabled || running;
  // Disabled is disabled: no colour, no pointer, no hover. Otherwise a button that can do
  // nothing right now looks like one that can do something.
  const color = from
    ? `${small ? BASE_SMALL : BASE} border border-line bg-transparent text-muted cursor-not-allowed`
    : (small ? BUTTON_SMALL : BUTTON)[variant];
  const show = { good: "✓", bad: "✗", open: "" }[state ?? "open"];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={from}
      title={title}
      className={`${wide ? "w-full" : ""} ${color}`}
    >
      {show && (
        <span className={state === "good" ? "text-green-400" : "text-red-400"}>{show}</span>
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
export function IconButton({ icon, title: title, onClick, danger = false, disabled = false, active = false }: {
  icon: string; title: string; onClick: () => void;
  danger?: boolean; disabled?: boolean; active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      // Here too: grey means disabled. A handgrip that exists is blue — only without a fill,
      // otherwise a list of twenty rows would be a fireworks display.
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-sm
        leading-none transition-colors disabled:border-line disabled:text-muted
        disabled:opacity-60 ${
        active ? "border-brand bg-brand text-white ring-2 ring-brand/40"
          : danger ? "border-red-600 bg-red-600 text-white hover:bg-red-600/90"
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
export function Dialog({ title: title, onClose, children, foot, wide = false, huge = false,
                        hold = false }: {
  title: string; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean;
  /** Nearly the whole window. For things one LOOKS at rather than fills in: a PDF in a column
   *  of 768 pixels is a PDF one reads with the zoom, which is no reading. */
  huge?: boolean;
  /** No escape, no click beside it — only its own buttons close it.
   *
   *  For dialogs in which something comes into being: a misplaced click has cost a half
   *  written text, and "escape" is typed faster while composing than one thinks. Where only a
   *  choice is made, both stay — there clicking away is convenient, not expensive. */
  hold?: boolean;
}) {
  useEffect(() => {
    const to = (e: KeyboardEvent) => { if (e.key === "Escape" && !hold) onClose(); };
    window.addEventListener("keydown", to);
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", to); document.body.style.overflow = before; };
  }, [onClose, hold]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onClick={hold ? undefined : onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div onClick={(e) => e.stopPropagation()}
        className={`flex w-full flex-col rounded-xl border border-line bg-card shadow-2xl ${
          huge ? "max-h-[94vh] max-w-6xl" : "max-h-[88vh] " + (wide ? "max-w-3xl" : "max-w-lg")}`}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold text-ink">{title}</h2>
          <button type="button" onClick={onClose} title={tr("common.close")} aria-label={tr("common.close")}
            className="text-muted hover:text-ink">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {foot && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">
            {foot}
          </div>
        )}
      </div>
    </div>
  );
}

/** The two buttons at the foot of nearly every dialog. */
export function DialogFoot({ onCancel: onCancel, onSave, saveText, runs: running = false, disabled = false }: {
  onCancel: () => void; onSave: () => void; saveText?: string;
  runs?: boolean; disabled?: boolean;
}) {
  return (
    <>
      <Button onClick={onCancel}>{tr("common.cancel")}</Button>
      <Button variant="primary" onClick={onSave} disabled={disabled} runs={running}>
        {saveText || tr("common.save")}
      </Button>
    </>
  );
}

/** A labelled field in a dialog. Label above, hint below, both optional. */
export function Field({ label, hint: hint, children }: {
  label: string; hint?: string; children: ReactNode;
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
export function ConfirmDialog({ title: title, text, hint: hint, confirmText: confirmText, danger = true,
                                    onClose, onConfirm: onConfirm, runs: running = false }: {
  title: string; text: string; hint?: string; confirmText: string; danger?: boolean;
  onClose: () => void; onConfirm: () => void; runs?: boolean;
}) {
  return (
    <Dialog title={title} onClose={onClose} foot={
      <>
        <Button onClick={onClose}>{tr("common.cancel")}</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} runs={running}>
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
export function DeleteDialog({ was, hint: hint, onClose, onDelete: onDelete, runs: running = false }: {
  was: string; hint?: string; onClose: () => void; onDelete: () => void; runs?: boolean;
}) {
  return (
    <ConfirmDialog title={tr("common.delete")} text={tr("common.really_delete_what", { was })}
      hint={hint} confirmText={tr("common.delete")} runs={running}
      onClose={onClose} onConfirm={onDelete} />
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
export function ListingEmpty({ children }: { children: ReactNode }) {
  return <div className="bg-surface px-3 py-2.5 text-xs text-muted">{children}</div>;
}

/**
 * Column headings. Hidden on small screens, where the entries wrap anyway.
 *
 * Same surface as the entries below on purpose: a heading in the colour of the card put a
 * bright bar across the list and cut the very surface in two that was meant to hold it
 * together. What separates the heading is the type — small, quiet, spaced out — not a wall.
 */
export function ListHeader({ columns, children }: { columns: string; children: ReactNode }) {
  return (
    <div className={`hidden gap-x-3 bg-surface px-3 pb-1 pt-2 text-[10px] uppercase tracking-wider text-muted/70 sm:grid ${columns}`}>
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

export function ListRow({ columns, dimmed = false, warning = false, dense = false,
                             active = false, onClick, children }: {
  columns?: string; dimmed?: boolean; warning?: boolean; dense?: boolean;
  /** Where one IS. The whole row carries it, not the writing in it: a coloured word among
   *  black ones is something one has to look for, a coloured row one simply sees, and in a
   *  list of thirty that is the difference between finding and searching. */
  active?: boolean;
  onClick?: () => void; children: ReactNode;
}) {
  // Without columns the entry only gets the surface and its padding; the layout inside is
  // the caller's business. That way an entry with a second line (a URL, a last run) does not
  // have to be pressed into a grid it does not want.
  const layout = columns
    ? `flex flex-wrap items-center gap-x-3 gap-y-1 sm:grid ${columns}`
    : "";
  return (
    <div
      onClick={onClick}
      className={`group px-3 text-sm transition-colors ${dense ? "py-1.5" : "py-2.5"} ${layout} ${
        active ? "bg-brand/20 text-ink ring-1 ring-inset ring-brand/40" : "bg-surface"} ${
        onClick && !active ? "cursor-pointer hover:bg-card" : onClick ? "cursor-pointer" : ""} ${
        dimmed ? "opacity-55" : ""} ${
        // A stripe on the left instead of a coloured surface: the row stays readable, and in a
        // long list one still sees the conspicuous entries from afar.
        warning ? "border-l-2 border-amber-400 pl-[calc(0.75rem-2px)]" : ""}`}
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
export function Area({ title: title, subtitle, hint: hint, tools: tools, fills = false,
                      column = false, children }: {
  title?: ReactNode; subtitle?: ReactNode; hint?: ReactNode; tools?: ReactNode;
  /** The card fills the height it is given, and what does not fit scrolls INSIDE it.
   *  For a page built of columns beside each other (the mailbox): without this the frame
   *  scrolls away with the content and the heading of a list leaves through the top edge. */
  fills?: boolean;
  /** With `fills`: the content is a column in which a child may take what is left
   *  (`flex-1`), and nothing scrolls by itself. For a mail that is meant to fill its space.
   *  Without it the content scrolls, which is what a list wants — in a flex column a list
   *  would be squeezed instead of scrolled, and `Listing` cuts off what sticks out. */
  column?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`rounded-lg border border-line bg-card p-4 ${
      fills ? "flex min-h-0 flex-1 flex-col gap-3" : "space-y-3"}`}>
      {title && (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-semibold text-ink">{title}</span>
          {subtitle && <span className="font-mono text-xs text-muted">{subtitle}</span>}
        </div>
      )}
      {hint && <p className="text-sm text-muted">{hint}</p>}
      {tools && (
        <div className="flex flex-wrap items-center gap-3 text-sm">{tools}</div>
      )}
      {fills
        ? <div className={column ? "flex min-h-0 flex-1 flex-col gap-3 overflow-hidden"
                                  : "min-h-0 flex-1 overflow-y-auto"}>{children}</div>
        : children}
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
export function Tab<T extends string>({ active, selection: selection, onChoose, vertical = false }: {
  active: T; selection: [T, string][]; onChoose: (value: T) => void; vertical?: boolean;
}) {
  return (
    <div className={vertical
      ? "flex shrink-0 flex-row flex-wrap gap-1 sm:w-40 sm:flex-col sm:flex-nowrap sm:border-r sm:border-line sm:pr-3"
      : "flex flex-wrap gap-1 border-b border-line pb-2"}>
      {selection.map(([value, label]) => (
        <button type="button" key={value} onClick={() => onChoose(value)}
          className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
            vertical ? "text-left" : ""} ${
            active === value
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
export function Tag({ color = "neutral", title: title, children }: {
  color?: "neutral" | "green" | "yellow" | "red" | "blue" | "violet" | "brand";
  title?: string; children: ReactNode;
}) {
  const style = {
    neutral: "border-line bg-card text-muted",
    green: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    yellow: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    red: "border-red-500/30 bg-red-500/10 text-red-300",
    blue: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    violet: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    brand: "border-brand/40 bg-brand/15 text-brand",
  }[color];
  return (
    <span title={title}
      className={`shrink-0 truncate rounded border px-1.5 py-0.5 text-[11px] ${style}`}>
      {children}
    </span>
  );
}

/** Secondary action inside an entry ("versions", "history", "cancel"). */
export function Rowbutton({ onClick, title: title, danger = false, children }: {
  onClick: () => void; title?: string; danger?: boolean; children: ReactNode;
}) {
  return (
    // The row button is a button too: blue, not grey (see DESIGN.md).
    //
    // `type="button"` is not decoration: without it HTML makes every button in a form an
    // submit button. The ✕ beside the mail search was one, so pressing Enter emptied the
    // field instead of searching with it.
    <button type="button" onClick={(e) => { e.stopPropagation(); onClick(); }} title={title}
      className={danger ? BUTTON_SMALL.danger : BUTTON_SMALL.secondary}>
      {children}
    </button>
  );
}

/**
 * Sort control of a list — belongs in the `tools` row of its `Area`.
 *
 * Not a heading row: two of the three flow lists have no columns at all (a run is a tag, a
 * name, a state and a time in one line), and a heading above a row that does not line up
 * would point at nothing. The bar says the same thing for every list, whatever a row looks
 * like inside.
 *
 * The active field carries the arrow; clicking it again turns the direction round. Clicking
 * a different one sorts by that, ascending.
 */
export function SortBar({ fields, by, dir, onSort }: {
  fields: readonly { key: string; label: string }[];
  by: string; dir: "asc" | "desc"; onSort: (key: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-xs text-muted">{tr("sort.by")}</span>
      {fields.map((f) => {
        const active = f.key === by;
        return (
          <button type="button" key={f.key} onClick={() => onSort(f.key)}
            title={tr(active && dir === "asc" ? "sort.descending" : "sort.ascending")}
            className={active
              ? "rounded bg-surface px-1.5 py-0.5 text-xs font-medium text-brand"
              : "rounded px-1.5 py-0.5 text-xs text-muted transition-colors hover:text-ink"}>
            {f.label}{active && (dir === "asc" ? " ▲" : " ▼")}
          </button>
        );
      })}
    </div>
  );
}

/** State of an entry: a dot plus a word. Colour carries the urgency, the word the meaning. */
export function State({ color, text }: { color: "green" | "yellow" | "grey" | "red"; text: string }) {
  const point = { green: "bg-emerald-400", yellow: "bg-amber-400", grey: "bg-muted",
                  red: "bg-red-400" }[color];
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${point}`} />
      {text}
    </span>
  );
}

/** Error line inside a dialog or above a list. */
export function Errorrow({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="mb-3 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {text}
    </div>
  );
}


/**
 * A small menu that hangs off a button.
 *
 * For the handles of an entry that are needed rarely and would otherwise stand in every row:
 * a folder is clicked twenty times a day and emptied twice a year, and a delete button beside
 * every one of thirty folders is thirty chances to hit the wrong thing.
 *
 * Deliberately not on the right mouse button: a web page that swallows the browser menu
 * surprises people, and on a phone there is no right button at all. The sign is visible
 * instead, and where a row has a hover state it may keep quiet until then (`quiet`).
 */
export function Menu({ title: title, sign = "⋯", quiet = false, children }: {
  title: string; sign?: string; quiet?: boolean; children: (close: () => void) => ReactNode;
}) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const WIDTH = 224;      // w-56, needed as a number to keep the menu inside the window

  /**
   * The menu hangs off the window, not off the row.
   *
   * `absolute` would be the obvious way and it was the wrong one: the column it stands in
   * scrolls, and a scrolling box cuts off everything that reaches out of it. The menu was
   * sliced down the middle, half of every label gone. `fixed` with the measured position of
   * the button knows no such edge.
   */
  const open = () => {
    const box = button.current?.getBoundingClientRect();
    if (!box) return;
    const top = window.innerHeight - box.bottom < 280 ? box.top - 272 : box.bottom + 4;
    setAt({ top: Math.max(8, top), left: Math.min(Math.max(8, box.right - WIDTH),
                                                  window.innerWidth - WIDTH - 8) });
  };
  // Scrolling moves the row and would leave the menu standing somewhere in the picture.
  useEffect(() => {
    if (!at) return;
    const zu = () => setAt(null);
    window.addEventListener("scroll", zu, true);
    window.addEventListener("resize", zu);
    return () => {
      window.removeEventListener("scroll", zu, true);
      window.removeEventListener("resize", zu);
    };
  }, [at]);

  return (
    <>
      <button type="button" ref={button}
        onClick={(e) => { e.stopPropagation(); at ? setAt(null) : open(); }}
        title={title} aria-label={title}
        // Quiet means quiet where there is a pointer. On a touch screen there is no hover, and
        // a sign that only appears on hovering is a sign that does not exist there.
        className={`shrink-0 rounded px-1.5 text-muted transition-colors hover:bg-surface
          hover:text-ink ${quiet && !at
            ? "sm:opacity-0 sm:focus:opacity-100 sm:group-hover:opacity-100" : ""}`}
      >
        {sign}
      </button>
      {at && (
        <>
          {/* The surface that catches the next click, wherever it lands. Without it a menu
              stays open behind the page one has moved on to. */}
          <div className="fixed inset-0 z-40"
               onClick={(e) => { e.stopPropagation(); setAt(null); }} />
          <div onClick={(e) => e.stopPropagation()}
            style={{ top: at.top, left: at.left, width: WIDTH }}
            className="fixed z-50 rounded-lg border border-line bg-card p-1 text-sm shadow-2xl">
            {children(() => setAt(null))}
          </div>
        </>
      )}
    </>
  );
}

/** One line of a `Menu`. Switched off it says why, it does not disappear: a handle that is
 *  missing looks like one that does not exist. */
export function MenuItem({ onClick, disabled = false, danger = false, title: title, children }: {
  onClick: () => void; disabled?: boolean; danger?: boolean; title?: string;
  children: ReactNode;
}) {
  return (
    <button type="button" disabled={disabled} title={title}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      className={`block w-full rounded px-2 py-1.5 text-left transition-colors ${
        disabled ? "cursor-not-allowed text-muted/60"
          : danger ? "text-red-400 hover:bg-red-500/10" : "text-ink hover:bg-surface"}`}>
      {children}
    </button>
  );
}

/** The line between two groups of a menu. */
export function MenuLine() {
  return <div className="my-1 border-t border-line" />;
}


/**
 * The seam between two columns, to be dragged.
 *
 * Which column deserves how much depends on the mail and on the person: a list of subjects
 * needs width once the sender writes in sentences, and a newsletter laid out for a sheet of
 * paper needs it too. Whoever decides that once should not have to decide it again on the
 * next visit, which is why the caller keeps the number and not this handle.
 *
 * The grip is wider than the line one sees. A seam of one pixel is a seam one hits by luck;
 * eight pixels are caught reliably, and the line inside stays quiet until the pointer arrives.
 */
export function Splitter({ leftOf, value, onChange, min = 240, keepRight = 420, standard,
                           title: title }: {
  /** The element to the left of the seam. Its left edge is where the width is measured from. */
  leftOf: RefObject<HTMLElement | null>;
  value: number; onChange: (px: number) => void;
  min?: number;
  /** How much the column on the right keeps at the very least. */
  keepRight?: number;
  /** Where a double click puts it back to. */
  standard?: number;
  title: string;
}) {
  const [pulls, setPulls] = useState(false);

  const set = (clientX: number) => {
    const box = leftOf.current?.getBoundingClientRect();
    if (!box) return;
    const room = window.innerWidth - box.left - keepRight;
    onChange(Math.round(Math.max(min, Math.min(clientX - box.left, Math.max(min, room)))));
  };

  useEffect(() => {
    if (!pulls) return;
    const move = (e: PointerEvent) => { e.preventDefault(); set(e.clientX); };
    const up = () => setPulls(false);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    // While dragging, everything the pointer sweeps over would otherwise be selected, and one
    // ends up with half the mailbox marked blue.
    const before = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.userSelect = before;
      document.body.style.cursor = "";
    };
  }, [pulls]);

  return (
    <div
      role="separator" aria-orientation="vertical" aria-valuenow={value} tabIndex={0}
      title={title} aria-label={title}
      onPointerDown={(e) => { e.preventDefault(); setPulls(true); }}
      onDoubleClick={() => standard !== undefined && onChange(standard)}
      // The keyboard can do it too, and it costs three lines: a seam that only answers to the
      // mouse is one that some people cannot move at all.
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") onChange(Math.max(min, value - (e.shiftKey ? 50 : 10)));
        if (e.key === "ArrowRight") onChange(value + (e.shiftKey ? 50 : 10));
      }}
      className="group hidden w-2 shrink-0 cursor-col-resize items-stretch justify-center
        outline-none xl:flex"
    >
      <div className={`w-px rounded transition-colors ${
        pulls ? "w-0.5 bg-brand" : "bg-line group-hover:bg-brand group-focus:bg-brand"}`} />
    </div>
  );
}


/**
 * Something is happening and it takes a moment.
 *
 * A ring and not three dancing dots: it says "it is running" without pretending to know how
 * far along it is. Where a wait has a length, a number is better than any animation.
 */
export function Spinner({ text }: { text?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-brand"
            role="status" aria-live="polite" aria-label={text || tr("common.loading")} />
      {text && <span>{text}</span>}
    </span>
  );
}

/**
 * The wait laid OVER the content, not in its place.
 *
 * What is underneath keeps its size, and nothing on the page moves: a list that empties
 * itself while it is being refilled makes everything below it jump, and the click one had
 * already aimed at lands somewhere else. What one sees is the old state, greyed, with the
 * word for what is happening on top.
 */
export function Busy({ text, show, children }: {
  text?: string; show: boolean; children: ReactNode;
}) {
  if (!show) return <>{children}</>;
  return (
    <div className="relative min-h-[6rem]">
      <div className="pointer-events-none opacity-40 transition-opacity">{children}</div>
      <div className="absolute inset-0 flex items-start justify-center pt-8">
        <div className="rounded-lg border border-line bg-card px-4 py-2 shadow-2xl">
          <Spinner text={text} />
        </div>
      </div>
    </div>
  );
}
