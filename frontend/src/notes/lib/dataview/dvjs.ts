import { tr } from "../../../i18n";
/**
 * ```dataviewjs support — the `dv` API and the slice of the predecessor's `app` object
 * that vault scripts actually touch.
 *
 * Two things make this work without the predecessor:
 *   1. the predecessor's DOM sugar (`el.createEl`, `createDiv`, `setText`, …) is added
 *      to HTMLElement.prototype, because virtually every dataviewjs snippet in
 *      the wild uses it.
 *   2. `dv.pages()` is synchronous in Dataview but our data comes over HTTP, so
 *      a block whose source was not cached yet records a miss, and the block is
 *      re-run once the data arrived (at most a few rounds).
 */

import { api, type DvRawPage } from '../api';
import { useStore } from '../store';
import { getActiveEditor } from '../activeEditor';
import { listFoldState as listFoldStateRef, setListFolds } from '../livePreview';
import { DvDateTime, DvDurationValue, DvLinkValue, revive } from './values';
import { errorEl } from './render';
import { inlineMarkdown, valueHtml, wireInternalLinks } from './format';
import { installDomSugar } from '../domSugar';
import { clearDataviewCaches } from './cache';

/* ---------------------------------------------------------- page proxies */

export type DvPageProxy = Record<string, unknown> & { file: Record<string, unknown> };

const pagesBySource = new Map<string, DvPageProxy[]>();
const pageByPath = new Map<string, DvPageProxy>();
const metaByPath = new Map<string, { headings: Array<{ heading: string; level: number; line: number }> }>();

function toProxy(raw: DvRawPage): DvPageProxy {
  const file = revive(raw.file) as Record<string, unknown>;
  const fields = revive(raw.fields) as Record<string, unknown>;
  return { ...fields, file };
}

async function loadSource(source: string): Promise<void> {
  const { pages } = await api.dvPages(source);
  const proxies = pages.map(toProxy);
  pagesBySource.set(source, proxies);
  for (const p of proxies) pageByPath.set(String(p.file.path), p);
}

async function loadPage(path: string): Promise<void> {
  const { page } = await api.dvPage(path);
  if (page) pageByPath.set(String((page.file as Record<string, unknown>).path ?? path), toProxy(page));
  pageByPath.set(path, pageByPath.get(path) ?? ({ file: { path } } as DvPageProxy));
}

/* --------------------------------------------------------------- DataArray */

class DataArray<T> {
  constructor(readonly values: T[]) {}
  get length(): number {
    return this.values.length;
  }
  get array(): () => T[] {
    return () => this.values;
  }
  [Symbol.iterator](): Iterator<T> {
    return this.values[Symbol.iterator]();
  }
  where(f: (x: T, i: number) => unknown): DataArray<T> {
    return new DataArray(this.values.filter((x, i) => !!f(x, i)));
  }
  filter(f: (x: T, i: number) => unknown): DataArray<T> {
    return this.where(f);
  }
  map<U>(f: (x: T, i: number) => U): DataArray<U> {
    return new DataArray(this.values.map(f));
  }
  flatMap<U>(f: (x: T, i: number) => U[]): DataArray<U> {
    return new DataArray(this.values.flatMap(f));
  }
  forEach(f: (x: T, i: number) => void): void {
    this.values.forEach(f);
  }
  sort<U>(key: (x: T) => U, direction: 'asc' | 'desc' = 'asc'): DataArray<T> {
    const dir = direction === 'desc' ? -1 : 1;
    const cmp = (a: T, b: T) => {
      const ka = key(a) as unknown;
      const kb = key(b) as unknown;
      if (ka === kb) return 0;
      if (ka === null || ka === undefined) return 1;
      if (kb === null || kb === undefined) return -1;
      if (typeof ka === 'number' && typeof kb === 'number') return (ka - kb) * dir;
      return String(ka).localeCompare(String(kb), 'de', { numeric: true }) * dir;
    };
    return new DataArray([...this.values].sort(cmp));
  }
  limit(n: number): DataArray<T> {
    return new DataArray(this.values.slice(0, n));
  }
  first(): T | undefined {
    return this.values[0];
  }
  last(): T | undefined {
    return this.values[this.values.length - 1];
  }
  groupBy<K>(key: (x: T) => K): DataArray<{ key: K; rows: DataArray<T> }> {
    const map = new Map<string, { key: K; rows: T[] }>();
    for (const v of this.values) {
      const k = key(v);
      const id = typeof k === 'object' ? String(k) : String(k);
      let g = map.get(id);
      if (!g) {
        g = { key: k, rows: [] };
        map.set(id, g);
      }
      g.rows.push(v);
    }
    return new DataArray([...map.values()].map((g) => ({ key: g.key, rows: new DataArray(g.rows) })));
  }
  distinct(): DataArray<T> {
    return new DataArray([...new Set(this.values)]);
  }
  includes(x: T): boolean {
    return this.values.includes(x);
  }
  join(sep = ', '): string {
    return this.values.map((x) => String(x)).join(sep);
  }
}

/* ---------------------------------------------------------------- dv & app */

export interface RunState {
  misses: Set<string>;
  container: HTMLElement;
  path: string | null;
}

function makeDv(state: RunState) {
  const el = (tag: string, text: unknown, opts?: { cls?: string; attr?: Record<string, string> }): HTMLElement => {
    const node = document.createElement(tag);
    if (opts?.cls) node.className = opts.cls;
    if (opts?.attr) for (const [k, v] of Object.entries(opts.attr)) node.setAttribute(k, v);
    if (text !== null && text !== undefined && text !== '') node.innerHTML = valueHtml(text);
    state.container.appendChild(node);
    return node;
  };

  const pages = (source?: string): DataArray<DvPageProxy> => {
    const key = (source ?? '').trim();
    const cached = pagesBySource.get(key);
    if (cached) return new DataArray(cached);
    state.misses.add('pages:' + key);
    return new DataArray<DvPageProxy>([]);
  };

  const page = (path: string): DvPageProxy | undefined => {
    const hit = pageByPath.get(path);
    if (hit) return hit;
    state.misses.add('page:' + path);
    return undefined;
  };

  return {
    container: state.container,
    current: () => (state.path ? page(state.path) : undefined),
    pages,
    page,
    pagePaths: (source?: string) => pages(source).map((p) => p.file.link),
    array: <T>(x: T[]) => new DataArray(x),
    isArray: (x: unknown) => Array.isArray(x) || x instanceof DataArray,
    date: (x: unknown) => {
      if (x instanceof DvDateTime) return x;
      const s = String(x);
      const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(s);
      if (!m) return null;
      const d = new Date(+m[1], +m[2] - 1, +m[3], m[4] ? +m[4] : 0, m[5] ? +m[5] : 0);
      return new DvDateTime(d.getTime(), !!m[4]);
    },
    duration: (ms: number) => new DvDurationValue(ms),
    fileLink: (path: string, embed = false, display?: string) => new DvLinkValue(path, display, undefined, embed),
    el,
    header: (level: number, text: unknown) => el(`h${Math.min(6, Math.max(1, level))}`, text),
    paragraph: (text: unknown) => el('p', text),
    span: (text: unknown) => el('span', text),
    list: (items: unknown[] | DataArray<unknown>) => {
      const arr = items instanceof DataArray ? items.values : (items ?? []);
      const ul = document.createElement('ul');
      ul.className = 'dataview list-view-ul';
      for (const item of arr) {
        const li = document.createElement('li');
        li.innerHTML = valueHtml(item);
        ul.appendChild(li);
      }
      state.container.appendChild(ul);
      return ul;
    },
    table: (headers: string[], rows: unknown[][] | DataArray<unknown[]>) => {
      const data = rows instanceof DataArray ? rows.values : (rows ?? []);
      const table = document.createElement('table');
      table.className = 'dataview table-view-table';
      const thead = document.createElement('thead');
      const htr = document.createElement('tr');
      for (const h of headers ?? []) {
        const th = document.createElement('th');
        th.innerHTML = inlineMarkdown(String(h));
        htr.appendChild(th);
      }
      thead.appendChild(htr);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const row of data) {
        const tr = document.createElement('tr');
        for (const cell of row ?? []) {
          const td = document.createElement('td');
          td.innerHTML = valueHtml(cell);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      const wrap = document.createElement('div');
      wrap.className = 'dataview-table-wrap';
      wrap.appendChild(table);
      state.container.appendChild(wrap);
      return table;
    },
    taskList: (tasks: unknown[] | DataArray<unknown>) => {
      const arr = tasks instanceof DataArray ? tasks.values : (tasks ?? []);
      const ul = document.createElement('ul');
      ul.className = 'dataview contains-task-list';
      for (const t of arr as Array<Record<string, unknown>>) {
        const li = document.createElement('li');
        li.className = 'task-list-item' + (t.completed ? ' is-checked' : '');
        li.innerHTML = `<input type="checkbox" class="task-list-item-checkbox"${t.completed ? ' checked' : ''}> ` + inlineMarkdown(String(t.text ?? ''));
        ul.appendChild(li);
      }
      state.container.appendChild(ul);
      return ul;
    },
    view: () => {
      throw new Error(tr("notes_query.no_dv_view"));
    },
  };
}

export function makeApp(state: RunState) {
  const openWikilink = () => useStore.getState().openWikilink;
  return {
    vault: {
      getAbstractFileByPath: (p: string) => {
        const page = pageByPath.get(p);
        if (!page) state.misses.add('page:' + p);
        const base = p.slice(p.lastIndexOf('/') + 1);
        return page ? { path: p, name: base, basename: base.replace(/\.(md|markdown)$/i, ''), extension: 'md' } : null;
      },
      getMarkdownFiles: () => [...pageByPath.keys()].map((p) => ({ path: p })),
    },
    metadataCache: {
      getFileCache: (f: { path?: string } | null) => {
        if (!f?.path) return null;
        const meta = metaByPath.get(f.path);
        if (!meta) {
          state.misses.add('meta:' + f.path);
          return null;
        }
        return {
          headings: meta.headings.map((h) => ({ heading: h.heading, level: h.level, position: { start: { line: h.line, col: 0 }, end: { line: h.line, col: 0 } } })),
          frontmatter: pageByPath.get(f.path)?.file ?? {},
        };
      },
      getFirstLinkpathDest: (link: string) => ({ path: link }),
    },
    workspace: {
      // Cursor placement (eState) has no equivalent here — open the note.
      openLinkText: (target: string) => {
        void openWikilink()(target);
      },
      getActiveFile: () => (state.path ? { path: state.path } : null),
      // Enough of the editor for scripts that fold on render — the daily note's
      // navigation block collapses the details of each appointment this way.
      get activeLeaf() {
        const view = getActiveEditor();
        if (!view) return undefined;
        return {
          view: {
            editor: {
              getValue: () => view.state.doc.toString(),
              lineCount: () => view.state.doc.lines,
            },
            currentMode: {
              getFoldInfo: () => {
                const folded = view.state.field(listFoldStateRef, false) ?? [];
                return {
                  folds: [...folded].map((pos) => ({ from: view.state.doc.lineAt(pos).number - 1, to: view.state.doc.lineAt(pos).number })),
                  lines: view.state.doc.lines,
                };
              },
              applyFoldInfo: (info: { folds?: Array<{ from: number }> }) => {
                // Hat der Nutzer fuer diese Notiz schon selbst gefaltet, gilt
                // seine Entscheidung. Sonst klappte ein Skript in der Notiz bei
                // jedem Oeffnen wieder alles zu, was er zuletzt aufgeklappt hat.
                const path = useStore.getState().activePath;
                if (path && useStore.getState().noteFolds[path]) return;
                const doc = view.state.doc;
                const positions = (info?.folds ?? [])
                  .map((f) => f.from + 1)
                  .filter((n) => n >= 1 && n <= doc.lines)
                  .map((n) => doc.line(n).from);
                view.dispatch({ effects: setListFolds.of(positions) });
              },
            },
          },
        };
      },
    },
  };
}

/* ------------------------------------------------------------------- runner */

async function resolveMisses(misses: Set<string>): Promise<boolean> {
  let loaded = false;
  for (const miss of misses) {
    const [kind, ...rest] = miss.split(':');
    const arg = rest.join(':');
    try {
      if (kind === 'pages' && !pagesBySource.has(arg)) {
        await loadSource(arg);
        loaded = true;
      } else if (kind === 'page' && !pageByPath.has(arg)) {
        await loadPage(arg);
        loaded = true;
      } else if (kind === 'meta' && !metaByPath.has(arg)) {
        const m = await api.dvMeta(arg);
        metaByPath.set(arg, { headings: m.headings ?? [] });
        loaded = true;
      }
    } catch {
      /* leave the miss unresolved — the block renders without that data */
    }
  }
  return loaded;
}

type BlockFn = (dv: unknown, app: unknown, input: unknown, container: HTMLElement) => Promise<unknown>;

const scriptCache = new Map<string, Promise<BlockFn>>();

/**
 * Compile a block's code into a callable.
 *
 * `new Function` is off the table: the app ships a strict CSP (`script-src
 * 'self'`, no 'unsafe-eval'). So the code is registered with the server and
 * imported back as a normal ES module from our own origin, which the CSP does
 * allow — the block runs with the same reach it has in the predecessor, without
 * opening `eval` for the whole app.
 */
function compile(code: string): Promise<BlockFn> {
  const cached = scriptCache.get(code);
  if (cached) return cached;
  const p = api
    .dvRegisterScript(code)
    // Not through the fetch wrapper: this is a real module import, so the address
    // has to carry the bridge prefix itself. The reading cookie is what lets it
    // through, the same as for images.
    .then((r) => import(/* @vite-ignore */ `/api/notes/dataview/script/${r.id}.mjs`))
    .then((mod: { default: BlockFn }) => mod.default);
  scriptCache.set(code, p);
  return p;
}

/** Run one ```dataviewjs block into a fresh element. */
export function renderJsBlock(code: string, path: string | null): HTMLElement {
  installDomSugar();
  const host = document.createElement('div');
  host.className = 'dataview-block dataview-js';
  wireInternalLinks(host);

  const run = async (round: number): Promise<void> => {
    const state: RunState = { misses: new Set(), container: host, path };
    host.textContent = '';
    const dv = makeDv(state);
    const app = makeApp(state);
    try {
      const fn = await compile(code);
      await fn.call(host, dv, app, undefined, host);
    } catch (err) {
      scriptCache.delete(code);
      host.appendChild(errorEl(`dataviewjs: ${(err as Error).message}`));
      return;
    }
    // Data the script asked for that we did not have yet → fetch and replay.
    if (state.misses.size && round < 3) {
      const loaded = await resolveMisses(state.misses);
      if (loaded) await run(round + 1);
    }
  };

  // `dv.current()` is needed by almost every block, so prefetch it before the
  // first run to avoid a visible re-render.
  const warm = path && !pageByPath.has(path) ? loadPage(path).catch(() => {}) : Promise.resolve();
  void warm.then(() => run(0));
  return host;
}

/** Drop cached page data (called when the vault changes underneath us). */
export function invalidateDataviewCache(): void {
  pagesBySource.clear();
  pageByPath.clear();
  metaByPath.clear();
  clearDataviewCaches();
}
