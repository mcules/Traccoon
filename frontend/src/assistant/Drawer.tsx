import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { ICON } from "../components/ui";
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

  if (!open) return null;
  return (
    <aside className={`z-40 flex shrink-0 flex-col overflow-hidden border-l border-line
                       bg-card transition-[width] duration-200 ease-out
                       max-sm:fixed max-sm:right-0 max-sm:top-0 max-sm:h-screen max-sm:shadow-xl
                       sm:sticky sm:top-0 sm:h-screen
                       ${wide ? "w-full sm:w-[380px]" : "w-0"}`}>
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
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
