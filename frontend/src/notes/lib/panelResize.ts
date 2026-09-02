/**
 * Dragging a side panel wider or narrower.
 *
 * Both panels do the same thing mirrored, so they share it: the handle sits on
 * the panel's inner edge, the drag writes the width into a CSS variable that
 * the layout grid follows, and the result is remembered per device — a wide
 * screen and a laptop want different widths, and neither should overwrite the
 * other's.
 *
 * The width is written straight to the document rather than through React: a
 * drag produces a pointer event per frame, and re-rendering the whole workspace
 * that often makes the panel lag behind the pointer.
 */

interface PanelResize {
  /** CSS variable the layout reads, e.g. `--sidebar-width`. */
  variable: string;
  /** Where the width is remembered between visits. */
  storageKey: string;
  /** Which way the panel grows: the left one grows to the right. */
  grows: 'right' | 'left';
  /** Selector of the panel on the other side, whose width limits this one. */
  other: string;
}

/** Put back the width from last time. Call once, on mount. */
export function restorePanelWidth({ variable, storageKey }: PanelResize): void {
  try {
    const w = localStorage.getItem(storageKey);
    if (w) document.documentElement.style.setProperty(variable, `${w}px`);
  } catch {
    /* private mode: the default width is fine */
  }
}

/**
 * How wide a panel may get.
 *
 * There used to be a fixed ceiling of 560 pixels, which on a wide screen is an
 * arbitrary wall one runs into for no reason. The only real limit is that the
 * note has to stay usable, so the ceiling is whatever is left of the window
 * after the ribbon, the other panel and a readable column for the note itself.
 * On a large display that is far more than 560; on a small one it is less, and
 * either way it is a consequence rather than a number someone picked.
 */
const MIN_WIDTH = 120;
const MIN_NOTE = 320;

function maxWidth(other: string): number {
  const ribbon = document.querySelector('.ribbon')?.getBoundingClientRect().width ?? 0;
  const otherW = document.querySelector(other)?.getBoundingClientRect().width ?? 0;
  return Math.max(MIN_WIDTH, Math.round(window.innerWidth - ribbon - otherW - MIN_NOTE));
}

/** The handle's pointerdown. Returns a handler ready to hang on the element. */
export function panelResizeHandler(opts: PanelResize) {
  const { variable, storageKey, grows, other } = opts;
  return (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget as HTMLElement;
    const startX = e.clientX;
    const min = MIN_WIDTH;
    const max = maxWidth(other);
    const startW = handle.parentElement?.getBoundingClientRect().width ?? min;
    handle.classList.add('active');
    document.body.classList.add('wo-col-resizing');

    const move = (ev: PointerEvent) => {
      const moved = ev.clientX - startX;
      const w = Math.min(max, Math.max(min, Math.round(startW + (grows === 'right' ? moved : -moved))));
      document.documentElement.style.setProperty(variable, `${w}px`);
    };
    const up = () => {
      handle.classList.remove('active');
      document.body.classList.remove('wo-col-resizing');
      const w = parseInt(document.documentElement.style.getPropertyValue(variable), 10);
      if (w) {
        try {
          localStorage.setItem(storageKey, String(w));
        } catch {
          /* nothing to remember it with; the width still holds for this visit */
        }
      }
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
}
