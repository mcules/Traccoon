/**
 * The app as an installed app, and readable without a connection.
 *
 * The worker cannot know on its own which files make up the app: their names
 * carry a build hash, and some are only fetched when a note needs them. So the
 * page tells it — after loading, it hands over the list of what it actually
 * loaded, and the notes most recently read. That way the offline copy is of
 * the app as it is running, not of a guess made when it was written.
 *
 * Everything here degrades to nothing where service workers are unavailable —
 * an old browser, or plain HTTP — and the app simply needs a network again.
 */
import { useStore } from './store';

let registered = false;

/** The active worker, once there is one. */
function worker(): Promise<ServiceWorker | null> {
  if (!('serviceWorker' in navigator)) return Promise.resolve(null);
  return navigator.serviceWorker.ready.then((r) => r.active).catch(() => null);
}

/** What this page loaded from our own origin — the app's own files. */
function shellUrls(): string[] {
  const here = location.origin;
  const urls = performance
    .getEntriesByType('resource')
    .map((e) => e.name)
    .filter((u) => u.startsWith(here))
    .filter((u) => {
      const p = new URL(u).pathname;
      return p.startsWith('/assets/') || p.startsWith('/icons/') || p === '/manifest.webmanifest';
    });
  return [...new Set(urls)];
}

export function installOffline(): void {
  if (registered || !('serviceWorker' in navigator)) return;
  registered = true;

  const register = async () => {
    try {
      await navigator.serviceWorker.register('/sw.js');
    } catch {
      return; // no workers here; nothing else to do
    }
    // Wait for the app to settle before handing over its file list, so the
    // lazily loaded parts (maths, diagrams) are in it too.
    window.setTimeout(async () => {
      const w = await worker();
      w?.postMessage({ type: 'shell', urls: shellUrls() });
    }, 3000);
  };

  // Registering competes with the first paint, so it waits for load — unless
  // the page is loaded already, in which case that event will never come.
  if (document.readyState === 'complete') void register();
  else window.addEventListener('load', () => void register());

  const setOnline = () => {
    const on = navigator.onLine;
    useStore.getState().setOnline(on);
    // Back on the network: send what was written while it was gone, before
    // anything else has a chance to read a stale version.
    if (on) void useStore.getState().flushPending();
  };
  window.addEventListener('online', setOnline);
  window.addEventListener('offline', setOnline);
  setOnline();
}

/** Ask the worker to keep these notes readable offline. */
export function keepOffline(paths: string[]): void {
  if (!paths.length) return;
  void worker().then((w) =>
    w?.postMessage({
      type: 'keep',
      urls: paths.map((p) => `/api/notes/files/content?path=${encodeURIComponent(p)}`),
    }),
  );
}
