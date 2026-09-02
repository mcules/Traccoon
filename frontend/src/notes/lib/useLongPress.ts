import { useRef } from 'react';

/**
 * Long-press as a stand-in for right-click.
 *
 * On a phone there is no context menu event, which left rename, move, delete
 * and share unreachable in the file tree — every one of them lives behind the
 * right-click menu. Holding a row for half a second now opens the same menu.
 *
 * `preventDefault` on the trigger matters: without it the browser starts its
 * own text-selection gesture on top of the menu.
 */
export function useLongPress(handler: (x: number, y: number) => void, ms = 500) {
  const timer = useRef<number | null>(null);
  const start = useRef({ x: 0, y: 0 });
  const fired = useRef(false);

  const clear = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.pointerType === 'mouse') return; // the mouse has its own menu
      start.current = { x: e.clientX, y: e.clientY };
      fired.current = false;
      clear();
      timer.current = window.setTimeout(() => {
        fired.current = true;
        navigator.vibrate?.(10);
        handler(start.current.x, start.current.y);
      }, ms);
    },
    onPointerMove: (e: React.PointerEvent) => {
      // A finger that travels is scrolling, not holding.
      if (Math.abs(e.clientX - start.current.x) > 10 || Math.abs(e.clientY - start.current.y) > 10) clear();
    },
    onPointerUp: (e: React.PointerEvent) => {
      clear();
      // Swallow the tap that would otherwise follow the menu opening.
      if (fired.current) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    onPointerCancel: clear,
  };
}
