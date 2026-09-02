/**
 * A tab that was open across a deployment.
 *
 * The built files carry a hash in their name, so a new deployment writes new
 * names and removes the old ones. A page loaded before that keeps running the
 * files it started with — fine, until it reaches for a part it has not needed
 * yet. Diagrams, formulas and drawings are all loaded on demand, so the first
 * note with a diagram in it fails with "error loading dynamically imported
 * module", and the note looks broken when the only thing that is out of date is
 * the page itself.
 *
 * There is nothing to repair at that point: the file it wants is gone. The
 * page reloads, once, and comes back on the current build. Once, because a
 * reload loop over a genuine network failure would be worse than the error.
 */
const KEY = 'reloaded-for-stale-build';
const QUIET_MS = 30_000;

const looksStale = (message: string): boolean =>
  /dynamically imported module|Importing a module script failed|error loading chunk/i.test(message);

function reloadOnce(): void {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(KEY) ?? 0);
  } catch {
    /* private mode: then it may reload again, which is still better than a
       note that stays broken */
  }
  if (Date.now() - last < QUIET_MS) return; // already tried; do not loop
  try {
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    /* see above */
  }
  location.reload();
}

export function watchForStaleBuild(): void {
  window.addEventListener('unhandledrejection', (e) => {
    const reason = e.reason as { message?: string } | string | undefined;
    const msg = typeof reason === 'string' ? reason : (reason?.message ?? '');
    if (looksStale(msg)) reloadOnce();
  });
  window.addEventListener('error', (e) => {
    if (looksStale(e.message ?? '')) reloadOnce();
  });
}
