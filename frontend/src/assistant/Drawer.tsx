import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { ICON } from "../components/ui";
import AssistantPanel from "./Panel";

/**
 * The assistant, from anywhere.
 *
 * It used to be a panel on one page, so asking it about a ticket meant leaving
 * the ticket. Here it is a drawer over whatever you are standing on: the page
 * stays where it is, and what that page can send along it says itself
 * (`context.tsx`).
 *
 * Over everything, including the note view, which covers the header with its
 * own full-screen layer — hence a z-index above that rather than above the
 * ordinary page.
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

  if (!open) return null;
  return (
    <aside className="fixed right-0 top-0 z-40 flex h-screen w-full max-w-[400px] flex-col
                      border-l border-line bg-card shadow-xl sm:w-[380px]">
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
