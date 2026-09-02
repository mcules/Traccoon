// Deep-link URL sync (FR-10): the browser URL mirrors the open note as
// /note/<vault-relative-path>; the views that are not notes get plain names of
// their own (/graph, /calendar) rather than a pseudo path pushed through the
// note route, which produced the unreadable /note/calendar%3A//view.
// Opening such a URL after
// login opens the note; browser back/forward navigate via popstate.
import { useStore, GRAPH_PATH, CALENDAR_PATH } from './store';
import { popConsumedByOverlay } from './overlayHistory';

export function pathToUrl(path: string | null): string {
  if (!path) return '/';
  if (path === GRAPH_PATH) return '/graph';
  if (path === CALENDAR_PATH) return '/calendar';
  return `/note/${path.split('/').map(encodeURIComponent).join('/')}`;
}

/** Vault path encoded in a location pathname, or null if it isn't a deep link. */
export function urlToPath(pathname: string): string | null {
  if (pathname === '/graph') return GRAPH_PATH;
  if (pathname === '/calendar') return CALENDAR_PATH;
  if (pathname.startsWith('/note/')) {
    try {
      const rel = pathname.slice('/note/'.length).split('/').map(decodeURIComponent).join('/');
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
