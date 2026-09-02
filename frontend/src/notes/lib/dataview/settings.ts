/**
 * The original plugins' settings, fetched once per session.
 *
 * Rendering has to follow them: dataview formats a date as "MMMM dd, yyyy" by
 * default, prints a null as `\-`, and does NOT write a completion date when a
 * task is ticked in a query result. The defaults here mirror the plugins'.
 */

import { api } from '../api';

export interface DataviewSettings {
  renderNullAs: string;
  taskCompletionTracking: boolean;
  warnOnEmptyResult: boolean;
  defaultDateFormat: string;
  defaultDateTimeFormat: string;
  tableIdColumnName: string;
  tableGroupColumnName: string;
  showResultCount: boolean;
  inlineQueryPrefix: string;
  inlineJsQueryPrefix: string;
  enableInlineDataview: boolean;
  prettyRenderInlineFields: boolean;
  prettyRenderInlineFieldsInLivePreview: boolean;
}

export interface TasksSettings {
  setDoneDate: boolean;
  setCancelledDate: boolean;
  recurrenceOnNextLine: boolean;
  globalQuery: string;
  globalFilter: string;
}

const DATAVIEW_FALLBACK: DataviewSettings = {
  renderNullAs: '\\-',
  taskCompletionTracking: false,
  warnOnEmptyResult: true,
  defaultDateFormat: 'MMMM dd, yyyy',
  defaultDateTimeFormat: 'h:mm a - MMMM dd, yyyy',
  tableIdColumnName: 'File',
  tableGroupColumnName: 'Group',
  showResultCount: true,
  inlineQueryPrefix: '=',
  inlineJsQueryPrefix: '$=',
  enableInlineDataview: true,
  prettyRenderInlineFields: true,
  prettyRenderInlineFieldsInLivePreview: true,
};

const TASKS_FALLBACK: TasksSettings = {
  setDoneDate: true,
  setCancelledDate: true,
  recurrenceOnNextLine: false,
  globalQuery: '',
  globalFilter: '',
};

let dv = DATAVIEW_FALLBACK;
let tasks = TASKS_FALLBACK;
let loading: Promise<void> | null = null;

export function dataviewSettings(): DataviewSettings {
  return dv;
}
export function tasksSettings(): TasksSettings {
  return tasks;
}

/** Load once; callers that need current values await this first. */
export function ensurePluginSettings(): Promise<void> {
  loading ??= api
    .dvSettings()
    .then((s) => {
      dv = { ...DATAVIEW_FALLBACK, ...(s.dataview ?? {}) };
      tasks = { ...TASKS_FALLBACK, ...(s.tasks ?? {}) };
    })
    .catch(() => {
      /* keep the plugin defaults */
    });
  return loading;
}

const MONTHS_LONG = Array.from({ length: 12 }, (_, i) => new Date(2020, i, 1).toLocaleString('de-DE', { month: 'long' }));
const MONTHS_SHORT = Array.from({ length: 12 }, (_, i) => new Date(2020, i, 1).toLocaleString('de-DE', { month: 'short' }));
const DAYS_LONG = Array.from({ length: 7 }, (_, i) => new Date(2020, 10, 1 + i).toLocaleString('de-DE', { weekday: 'long' }));
const DAYS_SHORT = Array.from({ length: 7 }, (_, i) => new Date(2020, 10, 1 + i).toLocaleString('de-DE', { weekday: 'short' }));

/** Luxon-style format tokens, the subset the plugins' formats use. */
export function formatWithTokens(d: Date, fmt: string): string {
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  const h12 = d.getHours() % 12 === 0 ? 12 : d.getHours() % 12;
  const map: Record<string, string> = {
    yyyy: String(d.getFullYear()),
    yy: pad(d.getFullYear() % 100),
    MMMM: MONTHS_LONG[d.getMonth()],
    MMM: MONTHS_SHORT[d.getMonth()],
    MM: pad(d.getMonth() + 1),
    M: String(d.getMonth() + 1),
    dd: pad(d.getDate()),
    d: String(d.getDate()),
    EEEE: DAYS_LONG[d.getDay()],
    EEE: DAYS_SHORT[d.getDay()],
    HH: pad(d.getHours()),
    H: String(d.getHours()),
    hh: pad(h12),
    h: String(h12),
    mm: pad(d.getMinutes()),
    ss: pad(d.getSeconds()),
    a: d.getHours() < 12 ? 'AM' : 'PM',
  };
  return fmt.replace(/yyyy|yy|MMMM|MMM|MM|M|dd|d|EEEE|EEE|HH|H|hh|h|mm|ss|a/g, (t) => map[t] ?? t);
}

/** Dataview's "minimal" date rendering: date-only unless a time is set. */
export function renderMinimalDate(ts: number): string {
  const d = new Date(ts);
  const hasTime = d.getHours() !== 0 || d.getMinutes() !== 0 || d.getSeconds() !== 0;
  return formatWithTokens(d, hasTime ? dv.defaultDateTimeFormat : dv.defaultDateFormat);
}
