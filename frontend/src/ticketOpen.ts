import type { MouseEvent } from "react";

// onOpen bekommt das Event mit, damit der Aufrufer Links-/Mittelklick unterscheiden kann.
export type OnOpenTicket = (key: string, e?: MouseEvent) => void;

// Handler-Bündel für ein ticket-öffnendes Element (Board-Karte, Listenzeile, Backlog-Key):
//  - Linksklick  → onOpen(key, e)           (Aufrufer entscheidet Popup vs. Seite je Präferenz)
//  - Mittelklick → onOpen(key, e)           (Aufrufer öffnet die volle Seite in neuem Tab)
//  - Mousedown der mittleren Taste wird unterdrückt, um das Browser-Autoscroll zu vermeiden.
export function ticketOpenHandlers(key: string, onOpen: OnOpenTicket) {
  return {
    onClick: (e: MouseEvent) => onOpen(key, e),
    onAuxClick: (e: MouseEvent) => { if (e.button === 1) onOpen(key, e); },
    onMouseDown: (e: MouseEvent) => { if (e.button === 1) e.preventDefault(); },
  };
}
