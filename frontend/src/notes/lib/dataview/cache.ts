/**
 * Client-side cache for everything the query engines fetch.
 *
 * The server answers in single-digit milliseconds; what costs time is the round
 * trip, and a note easily triggers dozens. Widgets are also rebuilt whenever the
 * editor re-decorates (tab switch, scroll, cursor), and without a cache every
 * rebuild asked again for the same answer.
 *
 * Everything is dropped when the vault changes on disk — the websocket event
 * that already invalidates the page cache.
 */

const stores = new Map<string, Map<string, Promise<unknown>>>();

function store(name: string): Map<string, Promise<unknown>> {
  let s = stores.get(name);
  if (!s) {
    s = new Map();
    stores.set(name, s);
  }
  return s;
}

/** Remember one in-flight or finished answer per key. */
export function memo<T>(name: string, key: string, load: () => Promise<T>): Promise<T> {
  const s = store(name);
  const hit = s.get(key);
  if (hit) return hit as Promise<T>;
  const p = load().catch((err: unknown) => {
    s.delete(key); // a failure must not be cached
    throw err;
  });
  s.set(key, p);
  return p;
}

export function clearDataviewCaches(): void {
  stores.clear();
}

/* --------------------------------------------------------------- batching */

interface Pending<Req, Res> {
  req: Req;
  resolve: (value: Res) => void;
  reject: (err: unknown) => void;
}

/**
 * Collect calls made in the same tick and send them as one request. A note with
 * twenty inline queries then costs one round trip instead of twenty.
 */
export function makeBatcher<Req, Res>(send: (reqs: Req[]) => Promise<Res[]>, waitMs = 15) {
  let queue: Pending<Req, Res>[] = [];
  let timer: number | null = null;

  const flush = () => {
    timer = null;
    const batch = queue;
    queue = [];
    if (!batch.length) return;
    send(batch.map((b) => b.req))
      .then((results) => {
        batch.forEach((b, i) => b.resolve(results[i]));
      })
      .catch((err) => batch.forEach((b) => b.reject(err)));
  };

  return (req: Req): Promise<Res> =>
    new Promise<Res>((resolve, reject) => {
      queue.push({ req, resolve, reject });
      if (timer === null) timer = window.setTimeout(flush, waitMs);
    });
}
