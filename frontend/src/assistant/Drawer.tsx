import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { ICON } from "../components/ui";
import { HEADER_ROW } from "../nav";
import AssistantPanel from "./Panel";

/**
 * The assistant, from anywhere.
 *
 * It used to be a panel on one page, so asking it about a ticket meant leaving
 * the ticket. Here it stands beside whatever you are standing on and slides in
 * from the right; what that page can send along it says itself (`context.tsx`).
 *
 * Beside, not over: a panel that covers the page hides the very thing one is
 * asking about, and the answer usually has to be compared with it. So it is a
 * column of the layout and the content narrows by its width. Only on a phone
 * does it lie on top — 380 of 390 pixels leave nothing to narrow.
 */

const OPEN = "traccoon.assistant.open";
const WIDTH = "traccoon.assistant.width";

/** Wie schmal und wie breit es sinnvoll ist. Darunter passt keine Sprechblase
 *  mehr neben ihren Rand, darueber bleibt vom Inhalt daneben nichts uebrig. */
const MIN = 300;
const MAX = 820;
const STANDARD = 380;

function gemerkteBreite(): number {
  const raw = Number(localStorage.getItem(WIDTH));
  return raw >= MIN && raw <= MAX ? raw : STANDARD;
}

export function useAssistantDrawer() {
  const [open, setOpen] = useState(() => localStorage.getItem(OPEN) === "1");
  useEffect(() => {
    if (open) localStorage.setItem(OPEN, "1");
    else localStorage.removeItem(OPEN);
  }, [open]);
  return { open, setOpen };
}

export function AssistantButton({ onClick, open }: { onClick: () => void; open: boolean }) {
  return (
    <button type="button" onClick={onClick}
      title={tr("assistant.open")} aria-label={tr("assistant.open")}
      className={`rounded px-2 py-1 text-lg leading-none transition-colors ${
        open ? "bg-surface text-ink" : "text-muted hover:text-ink"}`}>
      {ICON.assistant}
    </button>
  );
}

export function AssistantDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Escape closes it, like every other layer in the house. Only while it is
  // open: otherwise it would swallow the key from whatever else is listening.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Slides in rather than appearing: the width goes from nothing to its own in
  // one step after mounting, and the content beside it narrows along with it.
  // Without the second render it would simply stand there, and a panel that
  // pops into existence beside the text one is reading is a jump, not a move.
  const [wide, setWide] = useState(false);
  useEffect(() => {
    if (!open) { setWide(false); return; }
    const id = requestAnimationFrame(() => setWide(true));
    return () => cancelAnimationFrame(id);
  }, [open]);

  /**
   * How wide, and remembered per device.
   *
   * How much room a conversation deserves next to the work depends on what one
   * is doing at that machine, not on the conversation — the same reason the
   * note panels are draggable. While the edge is being dragged the transition
   * is off: an animation that chases the pointer lags a frame behind it and
   * feels like the handle is loose.
   */
  const [breite, setBreite] = useState(gemerkteBreite);
  const [zieht, setZieht] = useState(false);
  // Die gemerkte Breite gilt nur, wo daneben noch Platz ist. Auf dem Handy
  // liegt das Panel ueber allem und nimmt die ganze Breite — eine Zahl in
  // Pixeln waere dort eine Einschraenkung ohne Zweck. Ein Inline-Wert schlaegt
  // jede Klasse, also wird er dort gar nicht erst gesetzt.
  const [breit_genug, setBreitGenug] = useState(
    () => window.matchMedia("(min-width: 640px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 640px)");
    const merken = () => setBreitGenug(mq.matches);
    mq.addEventListener("change", merken);
    return () => mq.removeEventListener("change", merken);
  }, []);
  const ziehen = (start: React.PointerEvent) => {
    start.preventDefault();
    setZieht(true);
    const von = start.clientX;
    const ab = breite;
    const bewegen = (e: PointerEvent) => {
      // Nach links ziehen macht breiter: der Rand liegt auf der linken Seite.
      const neu = Math.min(MAX, Math.max(MIN, ab + (von - e.clientX)));
      setBreite(neu);
    };
    const los = () => {
      setZieht(false);
      window.removeEventListener("pointermove", bewegen);
      window.removeEventListener("pointerup", los);
      setBreite((b) => { localStorage.setItem(WIDTH, String(b)); return b; });
    };
    window.addEventListener("pointermove", bewegen);
    window.addEventListener("pointerup", los);
  };

  if (!open) return null;
  return (
    <aside
      style={{ width: !wide ? 0 : breit_genug ? breite : undefined }}
      className={`relative z-40 flex shrink-0 flex-col overflow-hidden border-l border-line
                  bg-card ${zieht ? "" : "transition-[width] duration-200 ease-out"}
                  max-sm:fixed max-sm:right-0 max-sm:top-0 max-sm:h-screen max-sm:shadow-xl
                  sm:sticky sm:top-0 sm:h-screen ${wide ? "w-full" : "w-0"}`}>
      {/* Der Griff liegt auf der Kante und ist breiter als die Linie, die man
          sieht: eine Kante von einem Pixel trifft niemand. */}
      <div onPointerDown={ziehen} title={tr("assistant.drag_width")}
        className="absolute inset-y-0 left-0 z-10 hidden w-1.5 cursor-col-resize
                   hover:bg-brand/40 sm:block" />
      {/* Dieselbe Zeilenhoehe wie die Kopfzeile des Hauses daneben. */}
      <div data-assistant="head" className={`justify-between ${HEADER_ROW}`}>
        <span className="font-semibold text-ink">{tr("notes_assistant.name")}</span>
        <button type="button" onClick={onClose} title={tr("common.close")}
          aria-label={tr("common.close")} className="px-2 text-muted hover:text-ink">✕</button>
      </div>
      <div className="min-h-0 flex-1">
        <AssistantPanel compact />
      </div>
    </aside>
  );
}
