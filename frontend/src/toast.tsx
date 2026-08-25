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
export type ToastKind = "info" | "error";

type Entry = { id: number; text: string; kind: ToastKind };

let entries: Entry[] = [];
let counter = 0;
const listener = new Set<() => void>();

function announce() {
  listener.forEach((fn) => fn());
}

/** Show a message. Errors stay longer, because one usually wants to read them twice. */
export function toast(text: string, kind: ToastKind = "info"): void {
  if (!text) return;
  const id = ++counter;
  entries = [...entries, { id, text, kind }];
  announce();
  window.setTimeout(() => close(id), kind === "error" ? 7000 : 4000);
}

function close(id: number): void {
  entries = entries.filter((e) => e.id !== id);
  announce();
}

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
          className={`toast-in pointer-events-auto w-full rounded-lg border px-3 py-2 text-left
            text-sm shadow-2xl transition-opacity hover:opacity-80 ${
            e.kind === "error" ? "border-red-500/40 bg-red-500/15 text-red-200"
                                : "border-line bg-card text-ink"}`}>
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
