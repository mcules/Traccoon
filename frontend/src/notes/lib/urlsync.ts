// Deep links: the address mirrors the open note, so a link to one can be sent
// and a reload comes back to where one was.
//
// Everything lives under `/notes`, and that is not decoration. This is one area
// of a larger application now; an address outside that prefix leaves the router
// of the house, and a reload then lands on the start page with the note gone.
// The views that are not notes get plain names rather than a pseudo path pushed
// through the note route, which used to produce an unreadable `calendar%3A//view`.
import { useStore, GRAPH_PATH, CALENDAR_PATH } from './store';
import { popConsumedByOverlay } from './overlayHistory';

const AREA = '/notes';

export function pathToUrl(path: string | null): string {
  if (!path) return AREA;
  if (path === GRAPH_PATH) return `${AREA}/graph`;
  if (path === CALENDAR_PATH) return `${AREA}/calendar`;
  return `${AREA}/n/${path.split('/').map(encodeURIComponent).join('/')}`;
}

/** Vault path encoded in a location pathname, or null if it isn't a deep link. */
export function urlToPath(pathname: string): string | null {
  if (pathname === `${AREA}/graph`) return GRAPH_PATH;
  if (pathname === `${AREA}/calendar`) return CALENDAR_PATH;
  if (pathname.startsWith(`${AREA}/n/`)) {
    try {
      const rel = pathname.slice(`${AREA}/n/`.length).split('/').map(decodeURIComponent).join('/');
      return rel || null;
    } catch {
      return null;
    }
  }
  return null;
}

/** True while we're applying a popstate — suppresses the pushState echo. */
let applyingPop = false;
let started = false;

/**
 * Start two-way sync. Call once after auth. Returns the deep-linked path that
 * was present in the URL at load time (to open after the workspace restores).
 */
export function initUrlSync(): string | null {
  const initial = urlToPath(window.location.pathname);
  if (started) return initial;
  started = true;

  // store → URL. The very first sync (workspace restore on load) replaces the
  // entry instead of pushing, so Back doesn't land on a stale '/'.
  let firstSync = true;
  useStore.subscribe((state, prev) => {
    if (state.activePath === prev.activePath) return;
    const url = pathToUrl(state.activePath);
    if (window.location.pathname === url) return;
    if (applyingPop || firstSync) window.history.replaceState(null, '', url);
    else window.history.pushState(null, '', url);
    firstSync = false;
  });

  // URL → store (browser back/forward)
  window.addEventListener('popstate', () => {
    // Back closed a drawer or a dialog — that was the whole of it.
    if (popConsumedByOverlay()) return;
    const path = urlToPath(window.location.pathname);
    if (!path || path === useStore.getState().activePath) return;
    applyingPop = true;
    Promise.resolve(useStore.getState().openFile(path))
      .catch(() => {})
      .finally(() => {
        applyingPop = false;
      });
  });

  return initial;
}
