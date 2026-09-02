/**
 * Client-side mirror of the server's Dataview value model.
 *
 * The server sends tagged JSON (`{kind:'link'|'date'|'duration'|'task'}`); this
 * module turns it into objects that behave the way note authors expect inside
 * ```dataviewjs: a Link stringifies to `[[target|display]]` and a date to ISO,
 * because vault scripts routinely do `String(p.someDate).match(/\d{4}-\d{2}/)`.
 */

export interface RawLink {
  kind: 'link';
  target: string;
  path: string;
  display?: string;
  subpath?: string;
  embed?: boolean;
}
export interface RawDate {
  kind: 'date';
  ts: number;
  hasTime: boolean;
}
export interface RawDuration {
  kind: 'duration';
  ms: number;
}
export interface RawTask {
  kind: 'task';
  text: string;
  status: string;
  completed: boolean;
  fullyCompleted: boolean;
  checked: boolean;
  path: string;
  line: number;
  level: number;
  tags: string[];
  children: RawTask[];
  section?: string;
  due?: RawDate;
  done?: RawDate;
  priority?: string;
  fields: Record<string, unknown>;
}

export type DvValue = unknown;

function pad(n: number, w = 2): string {
  return String(Math.abs(n)).padStart(w, '0');
}

/** Minimal Luxon-shaped date. `toString()` is ISO so regexes in vault scripts hit. */
export class DvDateTime {
  readonly ts: number;
  readonly hasTime: boolean;
  constructor(ts: number, hasTime = true) {
    this.ts = ts;
    this.hasTime = hasTime;
  }
  get date(): Date {
    return new Date(this.ts);
  }
  get year(): number {
    return this.date.getFullYear();
  }
  get month(): number {
    return this.date.getMonth() + 1;
  }
  get day(): number {
    return this.date.getDate();
  }
  get hour(): number {
    return this.date.getHours();
  }
  get minute(): number {
    return this.date.getMinutes();
  }
  toISO(): string {
    return this.toString();
  }
  toISODate(): string {
    const d = this.date;
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  toFormat(fmt: string): string {
    const d = this.date;
    const map: Record<string, string> = {
      yyyy: String(d.getFullYear()),
      MM: pad(d.getMonth() + 1),
      dd: pad(d.getDate()),
      HH: pad(d.getHours()),
      mm: pad(d.getMinutes()),
      ss: pad(d.getSeconds()),
    };
    return fmt.replace(/yyyy|MM|dd|HH|mm|ss/g, (t) => map[t] ?? t);
  }
  valueOf(): number {
    return this.ts;
  }
  toString(): string {
    const d = this.date;
    const day = this.toISODate();
    return this.hasTime ? `${day}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` : day;
  }
}

export class DvDurationValue {
  constructor(readonly ms: number) {}
  valueOf(): number {
    return this.ms;
  }
  toString(): string {
    let ms = Math.abs(this.ms);
    const units: Array<[string, number]> = [
      ['years', 365 * 24 * 3600_000],
      ['months', 30 * 24 * 3600_000],
      ['days', 24 * 3600_000],
      ['hours', 3600_000],
      ['minutes', 60_000],
      ['seconds', 1000],
    ];
    const parts: string[] = [];
    for (const [name, size] of units) {
      const n = Math.floor(ms / size);
      if (n > 0) {
        parts.push(`${n} ${n === 1 ? name.slice(0, -1) : name}`);
        ms -= n * size;
      }
    }
    return parts.slice(0, 2).join(', ') || '0 seconds';
  }
}

export class DvLinkValue {
  constructor(
    readonly target: string,
    readonly display?: string,
    readonly subpath?: string,
    readonly embed = false,
  ) {}
  get path(): string {
    return this.target;
  }
  /** Basename without extension — what a link shows when it has no alias. */
  get name(): string {
    const base = this.target.slice(this.target.lastIndexOf('/') + 1);
    return base.replace(/\.(md|markdown)$/i, '');
  }
  withDisplay(display: string): DvLinkValue {
    return new DvLinkValue(this.target, display, this.subpath, this.embed);
  }
  toString(): string {
    const sub = this.subpath ? `#${this.subpath}` : '';
    const disp = this.display ? `|${this.display}` : '';
    return `${this.embed ? '!' : ''}[[${this.target}${sub}${disp}]]`;
  }
}

/** Revive tagged JSON from the server into the classes above. */
export function revive(v: unknown): unknown {
  if (v === null || v === undefined) return null;
  if (Array.isArray(v)) return v.map(revive);
  if (typeof v !== 'object') return v;
  const o = v as Record<string, unknown>;
  switch (o.kind) {
    case 'link':
      return new DvLinkValue(String(o.target), o.display as string | undefined, o.subpath as string | undefined, !!o.embed);
    case 'date':
      return new DvDateTime(Number(o.ts), !!o.hasTime);
    case 'duration':
      return new DvDurationValue(Number(o.ms));
    case 'task':
    case 'list-item':
      return reviveShallow(o);
    default: {
      const out: Record<string, unknown> = {};
      for (const [k, val] of Object.entries(o)) out[k] = revive(val);
      return out;
    }
  }
}

function reviveShallow(o: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(o)) out[k] = revive(val);
  return out;
}

export function isTaskValue(v: unknown): v is RawTask {
  return !!v && typeof v === 'object' && (v as { kind?: string }).kind === 'task';
}

export function asText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (v instanceof DvLinkValue) return v.display ?? v.name;
  if (Array.isArray(v)) return v.map(asText).join(', ');
  return String(v);
}
