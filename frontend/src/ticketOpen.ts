import type { MouseEvent } from "react";

// onOpen gets the event along so that the caller can tell a left click from a middle click.
export type OnOpenTicket = (key: string, e?: MouseEvent) => void;

// Bundle of handlers for a ticket-opening element (board card, list row, backlog key):
//  - left click   -> onOpen(key, e)   (the caller decides popup versus page by preference)
//  - middle click -> onOpen(key, e)   (the caller opens the full page in a new tab)
//  - the mousedown of the middle button is suppressed in order to avoid the browser autoscroll.
export function ticketOpenHandlers(key: string, onOpen: OnOpenTicket) {
  return {
    onClick: (e: MouseEvent) => onOpen(key, e),
    onAuxClick: (e: MouseEvent) => { if (e.button === 1) onOpen(key, e); },
    onMouseDown: (e: MouseEvent) => { if (e.button === 1) e.preventDefault(); },
  };
}
