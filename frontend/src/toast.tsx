import { useEffect, useState } from "react";

/**
 * Short messages that come and go: bottom right, a few seconds, then away.
 *
 * They used to stand over the content and stay there. That is right for something one has to
 * act on, and wrong for "1 message marked as read": the sentence is already over when it is
 * read, and what remains is a line that pushes the page down until somebody happens to click
 * somewhere else.
 *
 * Deliberately without a context provider. A message is not a piece of state a page owns, it
 * is an event, and whoever triggers one should not have to reach through three components to
 * do it. `toast("…")` works from anywhere, `<Toasts />` stands once in the layout.
 */
export type ToastKind = "success" | "error" | "warning" | "info";

type Entry = { id: number; text: string; kind: ToastKind };

let entries: Entry[] = [];
let counter = 0;
const listener = new Set<() => void>();

function announce() {
  listener.forEach((fn) => fn());
}

/**
 * Show a message.
 *
 * The colour says what kind it is before one has read a word: green went well, red went
 * wrong, yellow is worth a look, blue is a piece of news. Errors and warnings stay longer,
 * because those are the two one usually reads twice.
 */
export function toast(text: string, kind: ToastKind = "info"): void {
  if (!text) return;
  const id = ++counter;
  entries = [...entries, { id, text, kind }];
  announce();
  window.setTimeout(() => close(id), kind === "error" || kind === "warning" ? 7000 : 4000);
}

// Ein Griff für die Browser-Sonde: sie soll alle vier Töne zeigen können, ohne im Postfach
// vier verschiedene Dinge auszulösen.
if (typeof window !== "undefined") {
  (window as any).__toast = toast;
}

function close(id: number): void {
  entries = entries.filter((e) => e.id !== id);
  announce();
}

/**
 * The four tones, opaque.
 *
 * Everywhere else in this house a colour is a wash over the surface it lies on, because there
 * it belongs to a row or a card. Here it lies over the page: a mail list shining through the
 * message makes it hard to read exactly where one reads it in passing. So: full colour, light
 * writing, and the same four meanings as everywhere else.
 *
 * Dark tones in both themes on purpose. A message is not part of the page, it is a note stuck
 * on top of it, and it may look the same in a light and a dark one.
 */
const TONE: Record<ToastKind, string> = {
  success: "border-emerald-500 bg-emerald-700 text-white",
  error: "border-red-500 bg-red-700 text-white",
  warning: "border-amber-400 bg-amber-600 text-white",
  info: "border-brand bg-brand text-white",
};

export function Toasts() {
  const [now, setNow] = useState(entries);
  useEffect(() => {
    const fn = () => setNow(entries);
    listener.add(fn);
    return () => { listener.delete(fn); };
  }, []);
  if (!now.length) return null;

  return (
    // `pointer-events-none` on the stack, `auto` on the message: what is not being clicked
    // must not swallow a click on the page underneath it.
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)]
      flex-col gap-2" role="status" aria-live="polite">
      {now.map((e) => (
        <button key={e.id} type="button" onClick={() => close(e.id)}
          title={tr_close()}
          // Stronger than the tags in a list: this thing stands over the page and has to be
          // read in passing, so it may carry its colour and not just hint at it.
          className={`toast-in pointer-events-auto w-full rounded-lg border px-3 py-2 text-left
            text-sm shadow-2xl transition-[filter] hover:brightness-110 ${TONE[e.kind]}`}>
          {e.text}
        </button>
      ))}
    </div>
  );
}

/** The tooltip, without pulling the whole i18n into this small module. */
function tr_close(): string {
  return document.documentElement.lang === "en" ? "Close" : "Schließen";
}
