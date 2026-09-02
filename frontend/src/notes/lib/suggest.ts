import { EditorView, ViewPlugin, ViewUpdate, keymap } from '@codemirror/view';
import { Prec } from '@codemirror/state';
import { prepareQuery, fuzzySearch, fuzzySearchPath, type FuzzyMatch } from './fuzzy';
import { listCommands, runCommand } from './commands';

/**
 * Editor suggesters (docs §9):
 *  - `[[` link suggester — trigger when lastIndexOf("[[") > lastIndexOf("]") on the
 *    line text up to the cursor; `![[` works the same. Stops in display-text mode (`|`).
 *  - `#` tag suggester — trigger on /(^|\s)#…$/ before the cursor.
 * Ranking uses the exact the predecessor fuzzy score (lib/fuzzy.ts). Max 20 items.
 */

let linkFiles: () => string[] = () => [];
export function setLinkSuggestFiles(fn: () => string[]) {
  linkFiles = fn;
}
let vaultTags: () => string[] = () => [];
export function setTagSuggestTags(fn: () => string[]) {
  vaultTags = fn;
}

// Tag body charset (§7) — editor flavour additionally requires a letter.
const TAG_TAIL_RE = /(^|\s)#([^ -⁯⸀-⹿'!"#$%&()*+,.:;<=>?@^`{|}~[\]\\\s]*)$/;

interface Item {
  label: string; // main text (basename / tag)
  note?: string; // secondary text (folder path)
  insert: string; // text replacing the query
  /** A registered command to run instead of inserting text. */
  command?: string;
  match: FuzzyMatch;
}

interface Ctx {
  mode: 'link' | 'tag' | 'task' | 'slash';
  embed: boolean;
  from: number; // start of the query text (after the trigger)
  query: string;
  /** task mode: which part of a task line is being completed */
  sub?: 'field' | 'date' | 'recurrence';
  symbol?: string;
  line?: string;
  inTask?: boolean;
}

/* ---- Tasks plugin field suggestions (symbols and wording from the plugin) -- */

const TASK_LINE_RE = /^(\s*[-*+]\s+\[.\]\s)/;
const TASK_DATE_CTX_RE = /(📅|🛫|⏳|✅|❌|➕)\uFE0F?\s*([0-9a-zA-Z /-]*)$/u;
const TASK_REC_CTX_RE = /(🔁)\uFE0F?\s*([0-9a-zA-Z ]*)$/u;

const DUE = '📅';
const START = '🛫';
const SCHEDULED = '⏳';
const CREATED = '➕';
const RECURRENCE = '🔁';
const ON_COMPLETION = '🏁';
const PRIORITIES: Array<[string, string]> = [
  ['⏫', 'high'],
  ['🔼', 'medium'],
  ['🔽', 'low'],
  ['🔺', 'highest'],
  ['⏬', 'lowest'],
];

const DATE_WORDS = [
  'today', 'tomorrow', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
  'next week', 'next month', 'next year',
];

const RECURRENCE_WORDS = [
  'every day', 'every week', 'every month', 'every year',
  'every week on Sunday', 'every week on Monday', 'every week on Tuesday', 'every week on Wednesday',
  'every week on Thursday', 'every week on Friday', 'every week on Saturday',
];

/** The plugin's abbreviations: td, tm, yd, tw, nw, we/weekend. */
const DATE_ABBREV: Record<string, string> = {
  td: 'today', tm: 'tomorrow', yd: 'yesterday', tw: 'this week', nw: 'next week', we: 'saturday', weekend: 'saturday',
};

const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function isoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Natural-language dates, in the shapes the plugin's suggestions produce. */
export function parseTaskDate(input: string, base = new Date()): Date | null {
  const raw = input.trim().toLowerCase();
  if (!raw) return null;
  const text = DATE_ABBREV[raw] ?? raw;
  const today = new Date(base.getFullYear(), base.getMonth(), base.getDate());
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (iso) return new Date(+iso[1], +iso[2] - 1, +iso[3]);
  if (text === 'today') return today;
  if (text === 'tomorrow') return new Date(today.getTime() + 864e5);
  if (text === 'yesterday') return new Date(today.getTime() - 864e5);
  if (text === 'this week' || text === 'next week') {
    const d = new Date(today);
    d.setDate(d.getDate() + (text === 'next week' ? 7 : 0) - ((d.getDay() + 6) % 7));
    return d;
  }
  if (text === 'next month') {
    const d = new Date(today);
    d.setMonth(d.getMonth() + 1);
    return d;
  }
  if (text === 'next year') {
    const d = new Date(today);
    d.setFullYear(d.getFullYear() + 1);
    return d;
  }
  const weekday = WEEKDAYS.indexOf(text.replace(/^next\s+/, ''));
  if (weekday >= 0) {
    const d = new Date(today);
    do {
      d.setDate(d.getDate() + 1);
    } while (d.getDay() !== weekday);
    return d;
  }
  const rel = /^in\s+(\d+)\s+(day|days|week|weeks|month|months|year|years)$/.exec(text);
  if (rel) {
    const n = Number(rel[1]);
    const d = new Date(today);
    if (rel[2].startsWith('day')) d.setDate(d.getDate() + n);
    else if (rel[2].startsWith('week')) d.setDate(d.getDate() + 7 * n);
    else if (rel[2].startsWith('month')) d.setMonth(d.getMonth() + n);
    else d.setFullYear(d.getFullYear() + n);
    return d;
  }
  return null;
}

function detect(view: EditorView): Ctx | null {
  const sel = view.state.selection.main;
  if (!sel.empty) return null;
  const line = view.state.doc.lineAt(sel.head);
  const before = line.text.slice(0, sel.head - line.from);

  const ob = before.lastIndexOf('[[');
  const cb = before.lastIndexOf(']');
  if (ob >= 0 && ob > cb) {
    const q = before.slice(ob + 2);
    // display-text / heading / block modes end basic file suggestions
    if (!q.includes('|') && !q.includes('#') && !q.includes('\n')) {
      return { mode: 'link', embed: ob > 0 && before[ob - 1] === '!', from: line.from + ob + 2, query: q };
    }
    return null;
  }

  const tm = before.match(TAG_TAIL_RE);
  if (tm) {
    const after = line.text[sel.head - line.from];
    if (after !== '#') {
      const start = sel.head - tm[2].length;
      return { mode: 'tag', embed: false, from: start, query: tm[2] };
    }
  }

  // A date or recurrence symbol was just typed: complete its value. This is the
  // one place the menu opens by itself, exactly as in the plugin — the symbol is
  // a deliberate keystroke, not idle typing.
  const taskM = line.text.match(TASK_LINE_RE);
  if (taskM && sel.head - line.from >= taskM[1].length) {
    const dm = before.match(TASK_DATE_CTX_RE);
    if (dm) {
      return { mode: 'task', sub: 'date', embed: false, symbol: dm[1], from: sel.head - dm[0].length, query: dm[2], line: line.text };
    }
    const rm = before.match(TASK_REC_CTX_RE);
    if (rm) {
      return { mode: 'task', sub: 'recurrence', embed: false, symbol: rm[1], from: sel.head - rm[0].length, query: rm[2], line: line.text };
    }
  }

  // `/` opens the command menu, like the predecessor's slash commands.
  const slash = /(?:^|\s)\/([^\s/]*)$/.exec(before);
  if (slash) {
    return {
      mode: 'slash',
      embed: false,
      from: sel.head - slash[1].length - 1,
      query: slash[1],
      line: line.text,
      inTask: !!taskM,
    };
  }
  return null;
}

/** Editor insertions offered by the slash menu, beyond the task fields. */
/** Plain text insertions the slash menu offers beyond the registered commands. */
const SLASH_INSERTS: Array<{ label: string; insert: string }> = [
  { label: 'Todo', insert: '- [ ] ' },
  { label: 'Überschrift 1', insert: '# ' },
  { label: 'Überschrift 2', insert: '## ' },
  { label: 'Überschrift 3', insert: '### ' },
];

/** Items of the `/` menu: the task fields when the line is a task, then the
 *  editor insertions. */
function slashItems(ctx: Ctx): Item[] {
  const none = { score: 0, matches: [] as [number, number][] };
  const query = ctx.query.trim().toLowerCase();
  const keep = (label: string) => !query || label.toLowerCase().includes(query);
  const out: Item[] = [];
  if (ctx.inTask) out.push(...taskFieldItems(ctx.line ?? ''));
  out.push(...SLASH_INSERTS.map((c) => ({ label: c.label, insert: c.insert, match: none })));
  // Everything registered for the editor shows up here on its own — a command
  // added anywhere in the app is offered at `/` without touching this file.
  out.push(
    ...listCommands({ editorOnly: true }).map((c) => ({
      label: c.name,
      insert: '',
      command: c.id,
      match: none,
    })),
  );
  const matched = out.filter((i) => keep(i.label));
  return matched.slice(0, 20);
}

/** The suggestions the Tasks plugin offers, in its order and wording. */
function taskItems(ctx: Ctx): Item[] {
  const none = { score: 0, matches: [] as [number, number][] };
  const line = ctx.line ?? '';
  const query = ctx.query.trim().toLowerCase();
  const keep = (label: string) => !query || label.toLowerCase().includes(query);

  if (ctx.sub === 'date') {
    const out: Item[] = [];
    const typed = parseTaskDate(ctx.query);
    if (typed) {
      const d = isoDate(typed);
      out.push({ label: `${ctx.symbol} ${d}`, insert: `${ctx.symbol} ${d} `, match: none });
    }
    for (const word of DATE_WORDS) {
      if (query && !word.toLowerCase().startsWith(query) && !word.toLowerCase().includes(query)) continue;
      const d = parseTaskDate(word);
      if (!d) continue;
      const iso = isoDate(d);
      out.push({ label: `${word} (${iso})`, insert: `${ctx.symbol} ${iso} `, match: none });
    }
    return out.slice(0, 20);
  }

  if (ctx.sub === 'recurrence') {
    return RECURRENCE_WORDS.filter((w) => !query || w.toLowerCase().includes(query))
      .map((w) => ({ label: `${RECURRENCE} ${w}`, insert: `${RECURRENCE} ${w} `, match: none }))
      .slice(0, 20);
  }

  return taskFieldItems(line, query);
}

/** The plugin's field list; a field already on the line is not offered again. */
function taskFieldItems(line: string, query = ''): Item[] {
  const none = { score: 0, matches: [] as [number, number][] };
  const keep = (label: string) => !query || label.toLowerCase().includes(query);
  const all: Item[] = [];
  const add = (symbol: string, label: string, insert = `${symbol} `) => {
    if (line.includes(symbol)) return;
    all.push({ label: `${symbol} ${label}`, insert, match: none });
  };
  add(DUE, 'due date');
  add(START, 'start date');
  add(SCHEDULED, 'scheduled date');
  if (!PRIORITIES.some(([sym]) => line.includes(sym))) {
    for (const [sym, name] of PRIORITIES) all.push({ label: `${sym} ${name} priority`, insert: `${sym} `, match: none });
  }
  add(RECURRENCE, 'recurring (repeat)');
  if (!line.includes(CREATED)) {
    const today = isoDate(new Date());
    all.push({ label: `${CREATED} created today (${today})`, insert: `${CREATED} ${today} `, match: none });
  }
  add(ON_COMPLETION, 'on completion');

  const matched = all.filter((i) => keep(i.label));
  // autoSuggestMinMatch = 0: with nothing matching, the whole list is shown.
  return (matched.length ? matched : all).slice(0, 20);
}

function computeItems(ctx: Ctx): Item[] {
  if (ctx.mode === 'slash') return slashItems(ctx);
  if (ctx.mode === 'task') return taskItems(ctx);
  const pq = prepareQuery(ctx.query);
  const out: Item[] = [];
  if (ctx.mode === 'link') {
    for (const path of linkFiles()) {
      const target = path.replace(/\.md$/i, '');
      const m = ctx.query ? fuzzySearchPath(pq, target) : { score: 0, matches: [] as [number, number][] };
      if (!m) continue;
      const slash = target.lastIndexOf('/');
      out.push({
        label: slash >= 0 ? target.slice(slash + 1) : target,
        note: slash >= 0 ? target.slice(0, slash) : undefined,
        insert: target,
        match: m,
      });
    }
  } else {
    for (const tag of vaultTags()) {
      const m = ctx.query ? fuzzySearch(pq, tag) : { score: 0, matches: [] as [number, number][] };
      if (!m) continue;
      out.push({ label: tag, insert: tag, match: m });
    }
  }
  out.sort((x, y) => y.match.score - x.match.score);
  return out.slice(0, 20);
}

/** Bold the matched ranges of `label` (suggestion-highlight). */
function renderLabel(el: HTMLElement, label: string, item: Item) {
  // matches are in target coordinates; map to the label when it's a suffix (basename)
  const offset = item.insert.length - label.length;
  let last = 0;
  for (const [a, b] of item.match.matches) {
    const from = Math.max(0, a - offset);
    const to = Math.max(0, b - offset);
    if (to <= 0 || from >= label.length || to <= from) continue;
    if (from > last) el.appendChild(document.createTextNode(label.slice(last, from)));
    const hl = document.createElement('span');
    hl.className = 'suggestion-highlight';
    hl.textContent = label.slice(from, to);
    el.appendChild(hl);
    last = to;
  }
  if (last < label.length) el.appendChild(document.createTextNode(label.slice(last)));
}

class SuggestState {
  dom: HTMLElement | null = null;
  ctx: Ctx | null = null;
  items: Item[] = [];
  selected = 0;
  /** Where Escape closed the menu — it stays closed for that trigger, instead
   *  of popping back up on the next keystroke. */
  dismissedFrom: number | null = null;

  constructor(readonly view: EditorView) {}

  update(u: ViewUpdate) {
    if (u.docChanged) {
      // defer: coordsAtPos must run after the view finishes updating
      requestAnimationFrame(() => this.refresh());
      return;
    }
    // Typing opens the menu, nothing else. Placing the caret behind a date used
    // to pop the date list open, and that popup then swallowed the next click —
    // which reads as "the editor puts my cursor a line too high".
    if (u.selectionSet || u.focusChanged) {
      this.dismissedFrom = null;
      this.hide();
    }
  }

  refresh() {
    const view = this.view;
    if (!view.hasFocus) return this.hide();
    const ctx = detect(view);
    if (!ctx) {
      this.dismissedFrom = null;
      return this.hide();
    }
    if (this.dismissedFrom === ctx.from) return this.hide();
    this.dismissedFrom = null;
    this.ctx = ctx;
    this.items = computeItems(ctx);
    this.selected = 0;
    if (!this.items.length) return this.hide();
    this.show();
  }

  show() {
    const view = this.view;
    if (!this.dom) {
      this.dom = document.createElement('div');
      this.dom.className = 'suggestion-container';
      const host = (document.querySelector('.theme-light, .theme-dark') as HTMLElement) ?? document.body;
      host.appendChild(this.dom);
    }
    const dom = this.dom;
    dom.textContent = '';
    this.items.forEach((item, i) => {
      const el = document.createElement('div');
      el.className = 'suggestion-item' + (i === this.selected ? ' is-selected' : '');
      const title = document.createElement('span');
      title.className = 'suggestion-title';
      renderLabel(title, item.label, item);
      el.appendChild(title);
      if (item.note) {
        const note = document.createElement('span');
        note.className = 'suggestion-note';
        note.textContent = item.note;
        el.appendChild(note);
      }
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        this.selected = i;
        this.accept();
      });
      el.addEventListener('mousemove', () => {
        if (this.selected !== i) {
          this.selected = i;
          this.renderSelection();
        }
      });
      dom.appendChild(el);
    });
    const ctx = this.ctx!;
    const coords = view.coordsAtPos(ctx.from - (ctx.mode === 'link' ? 2 : 1));
    if (!coords) return this.hide();
    dom.style.left = `${Math.round(Math.min(coords.left, window.innerWidth - 320))}px`;
    dom.style.display = 'block';
    // Flip above the cursor when there is no room below (like the predecessor).
    const height = Math.min(dom.scrollHeight, 300);
    if (coords.bottom + 4 + height > window.innerHeight && coords.top - 4 - height > 0) {
      dom.style.top = `${Math.round(coords.top - 4 - height)}px`;
    } else {
      dom.style.top = `${Math.round(coords.bottom + 4)}px`;
    }
  }

  renderSelection() {
    if (!this.dom) return;
    [...this.dom.children].forEach((c, i) => c.classList.toggle('is-selected', i === this.selected));
    (this.dom.children[this.selected] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' });
  }

  hide() {
    this.ctx = null;
    if (this.dom) {
      this.dom.remove();
      this.dom = null;
    }
  }

  get active() {
    return this.dom !== null && this.ctx !== null && this.items.length > 0;
  }

  /** Escape: close and keep it closed until the trigger changes. */
  dismiss() {
    this.dismissedFrom = this.ctx?.from ?? null;
    this.hide();
  }

  accept() {
    const ctx = this.ctx;
    const item = this.items[this.selected];
    if (!ctx || !item) return;
    const view = this.view;
    const head = view.state.selection.main.head;
    if (ctx.mode === 'link') {
      // consume a `]]` the user (or auto-pair) already typed after the cursor
      const after = view.state.sliceDoc(head, head + 2);
      const extra = after === ']]' ? 2 : 0;
      view.dispatch({
        changes: { from: ctx.from, to: head + extra, insert: `${item.insert}]]` },
        selection: { anchor: ctx.from + item.insert.length + 2 },
        userEvent: 'input.complete',
      });
    } else if (item.command) {
      // A command replaces the typed `/query` and then does its own thing —
      // remove the query first so the command acts on a clean line.
      view.dispatch({
        changes: { from: ctx.from, to: head, insert: '' },
        selection: { anchor: ctx.from },
        userEvent: 'input.complete',
      });
      this.hide();
      runCommand(item.command);
      return;
    } else {
      view.dispatch({
        changes: { from: ctx.from, to: head, insert: item.insert },
        selection: { anchor: ctx.from + item.insert.length },
        userEvent: 'input.complete',
      });
    }
    this.hide();
    view.focus();
  }

  move(dir: 1 | -1) {
    this.selected = (this.selected + dir + this.items.length) % this.items.length;
    this.renderSelection();
  }

  destroy() {
    this.hide();
  }
}

const plugin = ViewPlugin.fromClass(SuggestState, {
  eventHandlers: {
    blur() {
      const self = this as unknown as SuggestState;
      // let an item mousedown win first
      setTimeout(() => self.hide(), 120);
      return false;
    },
  },
});

/** Run `f` when the suggester popup is open; otherwise let the key fall through. */
function whenActive(view: EditorView, f: (s: SuggestState) => void): boolean {
  const s = view.plugin(plugin);
  if (!s || !s.active) return false;
  f(s);
  return true;
}

// Highest precedence so Enter/Tab/arrows beat the editor keymaps while open.
const suggesterKeys = Prec.highest(
  keymap.of([
    { key: 'ArrowDown', run: (v) => whenActive(v, (s) => s.move(1)) },
    { key: 'ArrowUp', run: (v) => whenActive(v, (s) => s.move(-1)) },
    { key: 'Enter', run: (v) => whenActive(v, (s) => s.accept()) },
    { key: 'Tab', run: (v) => whenActive(v, (s) => s.accept()) },
    { key: 'Escape', run: (v) => whenActive(v, (s) => s.dismiss()) },
  ]),
);

export const suggesterPlugin = [plugin, suggesterKeys];
