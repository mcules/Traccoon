/**
 * Writing without a connection.
 *
 * A save that cannot reach the server is not a save that failed — it is one
 * that has not happened yet. The text goes into a queue in the browser's own
 * storage, together with the version it was written against, and is sent as
 * soon as there is a network again. That survives a closed tab, a reload and a
 * flat battery, which "keep it in memory and hope" does not.
 *
 * The queue holds one entry per note, always the newest text: a note typed in
 * three times while offline should arrive once, not three times.
 *
 * Sending is the same conditional write as everywhere else, so a note that
 * changed elsewhere in the meantime is merged rather than overwritten — which
 * is exactly the case offline writing makes likely.
 */

const KEY = 'pending-writes';

export interface PendingWrite {
  path: string;
  content: string;
  /** The version this text was written against, for the conditional write. */
  baseHash: string;
  /**
   * That version's text. Needed because a note may well have changed elsewhere
   * while this one waited: without the common ancestor the two versions cannot
   * be merged, only chosen between.
   */
  baseText: string;
  at: number;
}

function read(): PendingWrite[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? (list as PendingWrite[]) : [];
  } catch {
    return []; // private mode, or something else wrote nonsense there
  }
}

function write(list: PendingWrite[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* storage full or blocked: the note stays in the editor, which is still
       better than pretending it was queued */
  }
}

export const pendingWrites = (): PendingWrite[] => read();
export const pendingCount = (): number => read().length;

/** Remember a note that could not be sent. Replaces an older entry for it. */
export function queueWrite(path: string, content: string, baseHash: string, baseText: string): void {
  const list = read().filter((p) => p.path !== path);
  list.push({ path, content, baseHash, baseText, at: Date.now() });
  write(list);
}

export function forgetWrite(path: string): void {
  write(read().filter((p) => p.path !== path));
}

/** True when this failure means "no network", not "the server said no". */
export function isOffline(e: unknown): boolean {
  if (!navigator.onLine) return true;
  const err = e as { status?: number; name?: string };
  // A rejected request has no status; a refused one has.
  return err?.status === undefined || err?.name === 'TypeError';
}
