/**
 * Rendering pieces shared by the query results and the inline renderer:
 * escaping, a small inline-markdown pass and the value formatter that follows
 * dataview's settings. Kept apart from `render.ts` so the inline module can use
 * them without a circular import.
 */

import { useStore } from '../store';
import { dataviewSettings, renderMinimalDate } from './settings';
import { DvDateTime, DvDurationValue, DvLinkValue, isTaskValue } from './values';

/* -------------------------------------------------------- inline markdown */

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

export function linkHtml(target: string, label: string, embed = false): string {
  const cls = 'internal-link' + (embed ? ' embed' : '');
  return `<a class="${cls}" href="#" data-wikilink="${escapeHtml(target)}">${escapeHtml(label)}</a>`;
}

/**
 * Small inline-markdown renderer for table cells and list items. Deliberately
 * not the full pipeline: cells hold link/emphasis/code, never block structure,
 * and running unified per cell on a 300-row table is a waste.
 */
export function inlineMarkdown(src: string): string {
  const out: string[] = [];
  let rest = src;
  const CODE = /`([^`]+)`/;
  // Split code spans out first so their contents stay literal.
  for (;;) {
    const m = CODE.exec(rest);
    if (!m) break;
    out.push(inlineNoCode(rest.slice(0, m.index)));
    out.push(`<code>${escapeHtml(m[1])}</code>`);
    rest = rest.slice(m.index + m[0].length);
  }
  out.push(inlineNoCode(rest));
  return out.join('');
}

function inlineNoCode(src: string): string {
  let s = escapeHtml(src);
  // ![[embed]] / [[target|alias]]
  s = s.replace(/(!?)\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g, (_all, bang: string, target: string, _sub: string, alias: string) => {
    const base = target.slice(target.lastIndexOf('/') + 1).replace(/\.(md|markdown)$/i, '');
    return linkHtml(target, alias || base, bang === '!');
  });
  // [label](url)
  s = s.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (_all, label: string, url: string) => {
    if (/^https?:\/\//i.test(url)) return `<a class="external-link" href="${url}" target="_blank" rel="noreferrer">${label || url}</a>`;
    return linkHtml(decodeURIComponent(url), label || url);
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  s = s.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;:!?])/g, '$1<em>$2</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  s = s.replace(/==([^=]+)==/g, '<mark>$1</mark>');
  s = s.replace(/(^|\s)#([\p{L}\p{N}_\-/]*[\p{L}_\-/][\p{L}\p{N}_\-/]*)/gu, '$1<a class="tag" href="#" data-tag="$2">#$2</a>');
  // Markdown backslash escapes: `\-` is a literal dash. Dataview's default for
  // an empty value is exactly that, so without this a null reads as "\-".
  s = s.replace(/\\([\\`*_{}\[\]()#+\-.!|~>])/g, '$1');
  return s;
}

/* ------------------------------------------------------------ value cells */

export function valueHtml(v: unknown): string {
  const x = v;
  // A null renders as dataview's `renderNullAs` (a markdown-escaped dash by
  // default), not as some placeholder of our own.
  if (x === null || x === undefined || x === '') return inlineMarkdown(dataviewSettings().renderNullAs);
  if (x instanceof DvLinkValue) return linkHtml(x.target, x.display ?? x.name, x.embed);
  if (x instanceof DvDateTime) return escapeHtml(renderMinimalDate(x.ts));
  if (x instanceof DvDurationValue) return escapeHtml(x.toString());
  if (isTaskValue(x)) return inlineMarkdown(x.text);
  if (Array.isArray(x)) {
    if (!x.length) return inlineMarkdown(dataviewSettings().renderNullAs);
    return x.map(valueHtml).join(', ');
  }
  if (typeof x === 'boolean') return x ? '✓' : '✗';
  if (typeof x === 'number') return String(x);
  if (typeof x === 'object') return escapeHtml(JSON.stringify(x));
  return inlineMarkdown(String(x));
}


/**
 * Make internal links inside a rendered block work.
 *
 * A block lives in a CodeMirror widget, and a widget's events never reach the
 * editor's own link handling (that is what `ignoreEvent` is for) — so without
 * this, every link in a query result, an inline query or a group heading is
 * dead while the same link in prose works. The note embed does the same thing.
 */
export function wireInternalLinks(el: HTMLElement): void {
  el.addEventListener('mousedown', (e) => {
    const a = (e.target as HTMLElement).closest('[data-wikilink]') as HTMLElement | null;
    if (!a || (e as MouseEvent).button !== 0) return;
    e.preventDefault();
    const target = a.getAttribute('data-wikilink');
    if (target) void useStore.getState().openWikilink(target);
  });
  // Swallow the click that follows, so the reading view's own handler does not
  // open the same note a second time.
  el.addEventListener('click', (e) => {
    if ((e.target as HTMLElement).closest('[data-wikilink]')) {
      e.preventDefault();
      e.stopPropagation();
    }
  });
}

/* ------------------------------------------------- task fields, as the plugin

 * The Tasks plugin does not print a task's dates as plain text: it lifts them
 * out of the description and wraps each in its own span, carrying how far away
 * the date is in `data-task-due`. That attribute is what CSS snippets hook
 * into — this vault has one that appends "⚠️ überfällig" / "📍 fällig" — so a
 * renderer that just dumps the raw line silently disables them.
 *
 * Dates are shown as DD.MM.YYYY. The vault used to achieve that with a startup
 * script that rewrote the DOM behind the plugin's back; doing it while
 * rendering costs nothing and cannot race.
 */

const FIELD_CLASS: Record<string, string> = {
  '📅': 'task-due',
  '⏳': 'task-scheduled',
  '🛫': 'task-start',
  '➕': 'task-created',
  '✅': 'task-done',
  '❌': 'task-cancelled',
};

const PRIORITY_CLASS: Record<string, string> = {
  '🔺': 'task-priority',
  '⏫': 'task-priority',
  '🔼': 'task-priority',
  '🔽': 'task-priority',
  '⏬': 'task-priority',
};

/** How far off a date is, in the plugin's own vocabulary (past-3d, today, …). */
export function dueDistance(iso: string, today = new Date()): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return '';
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((date.getTime() - base.getTime()) / 86400000);
  if (days === 0) return 'today';
  const dir = days < 0 ? 'past' : 'future';
  const n = Math.abs(days);
  return n <= 7 ? `${dir}-${n}d` : `${dir}-far`;
}

const germanDate = (iso: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : iso;
};

/**
 * Split a task line into its description and the trailing field markers, and
 * render each the way the plugin does.
 */
export function taskFieldsHtml(text: string): { description: string; fields: string; due: string } {
  const symbols = Object.keys(FIELD_CLASS).concat(Object.keys(PRIORITY_CLASS), ['🔁', '🏁']);
  const first = symbols
    .map((s) => text.indexOf(s))
    .filter((i) => i >= 0)
    .sort((a, b) => a - b)[0];
  if (first === undefined) return { description: inlineMarkdown(text), fields: '', due: '' };

  const description = inlineMarkdown(text.slice(0, first).trimEnd());
  const rest = text.slice(first);
  const parts: string[] = [];
  let due = '';
  // Each marker owns the text up to the next marker.
  const re = new RegExp(`(${symbols.map((s) => s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')).join('|')})\\s*([^${symbols.join('')}]*)`, 'gu');
  for (let m = re.exec(rest); m; m = re.exec(rest)) {
    const symbol = m[1];
    const value = m[2].trim();
    const cls = FIELD_CLASS[symbol] ?? PRIORITY_CLASS[symbol] ?? (symbol === '🔁' ? 'task-recurring' : 'task-on-completion');
    const isDate = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const shown = isDate ? germanDate(value) : value;
    const attr =
      cls === 'task-due' && isDate ? ` data-task-due="${dueDistance(value)}"` : '';
    const span = `<span class="${cls}"${attr}>${escapeHtml(symbol)}${shown ? ` ${escapeHtml(shown)}` : ''}</span>`;
    if (cls === 'task-due' && !due) due = span;
    else parts.push(span);
  }
  return { description, fields: parts.join(' '), due };
}
