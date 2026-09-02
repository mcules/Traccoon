/**
 * Inline dataview: `= expression` code spans and `[key:: value]` fields.
 *
 * Both are things the plugin does *outside* query blocks, all over ordinary
 * notes — this vault has 166 inline queries (contact tables are built from
 * them) and 86 inline fields. Markup and class names follow the plugin so the
 * existing look carries over.
 */

import { api } from '../api';
import { makeBatcher, memo } from './cache';
import { inlineMarkdown, valueHtml, wireInternalLinks } from './format';
import { dataviewSettings, ensurePluginSettings } from './settings';
import { revive } from './values';

/** `[key:: value]` and `(key:: value)`, the two wrappings dataview accepts.
 *  A fresh instance per call: a shared /g/ regex carries `lastIndex` between
 *  callers, which silently skipped every field but the first in a cell. */
export function inlineFieldRegex(): RegExp {
  return /(\[|\()\s*([^[\]()<>:]{1,60}?)\s*::\s*([^\]\)]*?)\s*(\]|\))/g;
}

/** A rendered inline field, as `replaceInlineFields` builds it. */
export function inlineFieldEl(key: string, value: string, wrapping: '[' | '('): HTMLElement {
  const span = document.createElement('span');
  span.className = 'dataview inline-field';
  if (wrapping === '[') {
    const k = document.createElement('span');
    k.className = 'dataview inline-field-key';
    k.dataset.dvKey = key;
    k.innerHTML = inlineMarkdown(key);
    const v = document.createElement('span');
    v.className = 'dataview inline-field-value';
    v.innerHTML = inlineMarkdown(value);
    span.append(k, v);
  } else {
    const v = document.createElement('span');
    v.className = 'dataview inline-field-standalone-value';
    v.dataset.dvKey = key;
    v.innerHTML = inlineMarkdown(value);
    span.appendChild(v);
  }
  return span;
}

/** One request for all inline queries that render in the same tick. */
const askInline = makeBatcher<{ expr: string; path?: string }, { ok: boolean; value?: unknown; error?: string }>(
  (items) => api.dvInlineBatch(items).then((r) => r.results),
);

function inlineValue(expr: string, path: string | null) {
  return memo('inline', `${path ?? ''}::${expr}`, () => askInline({ expr, path: path ?? undefined }));
}

/** A rendered inline query; fills in once the server answered. */
export function inlineQueryEl(expr: string, path: string | null): HTMLElement {
  const span = document.createElement('span');
  span.className = 'dataview dataview-inline-query';
  span.textContent = '…';
  wireInternalLinks(span);
  void ensurePluginSettings()
    .then(() => inlineValue(expr, path))
    .then((res) => {
      if (res.ok) {
        span.innerHTML = valueHtml(revive(res.value));
      } else {
        span.className = 'dataview dataview-error';
        span.textContent = `Dataview (for inline query '${expr}'): ${res.error ?? 'error'}`;
      }
    })
    .catch((err: Error) => {
      span.className = 'dataview dataview-error';
      span.textContent = `Dataview: ${err.message}`;
    });
  return span;
}

/** True when this code span holds an inline query (`= …`). */
export function inlineQueryOf(text: string): string | null {
  const s = dataviewSettings();
  if (!s.enableInlineDataview) return null;
  const t = text.trimStart();
  if (s.inlineJsQueryPrefix && t.startsWith(s.inlineJsQueryPrefix)) return null; // JS inline: not supported
  if (s.inlineQueryPrefix && t.startsWith(s.inlineQueryPrefix)) return t.slice(s.inlineQueryPrefix.length).trim();
  return null;
}

/* ------------------------------------------------------- reading-view pass */

const SKIP_TAGS = new Set(['CODE', 'PRE', 'SCRIPT', 'STYLE', 'TEXTAREA']);
const HOST_SELECTOR = 'p, li, td, th, dd, dt, h1, h2, h3, h4, h5, h6, blockquote, figcaption, .tasks-desc, .task-body';

/** Field pattern over rendered HTML: a value may already contain markup, e.g.
 *  `[lieferant:: [[IKEA]]]` becomes an <a> before this runs. */
function htmlFieldRegex(): RegExp {
  return /(\[|\()\s*([^[\]()<>:]{1,60}?)\s*::\s*((?:[^\]\)<]|<[^>]*>)*?)\s*(\]|\))/g;
}

/**
 * Replace inline fields with their pretty form.
 *
 * Works on innerHTML, exactly like the plugin's `replaceInlineFields`: a field
 * whose value holds a link is no longer a single text node by the time we see
 * it, so a text-node walk would miss precisely those.
 */
function replaceFieldsIn(root: HTMLElement): void {
  const hosts: HTMLElement[] = [];
  const candidates = [root, ...root.querySelectorAll<HTMLElement>(HOST_SELECTOR)];
  for (const el of candidates) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    if (!el.textContent?.includes('::')) continue;
    // Only the innermost container, so nothing is rewritten twice.
    if (el.querySelector(HOST_SELECTOR)) continue;
    if (el.querySelector('.dataview.inline-field')) continue;
    hosts.push(el);
  }
  for (const el of hosts) {
    const src = el.innerHTML;
    const re = htmlFieldRegex();
    let out = '';
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(src))) {
      if ((m[1] === '[') !== (m[4] === ']')) continue;
      const probe = document.createElement('span');
      probe.innerHTML = m[3];
      out += src.slice(last, m.index) + inlineFieldEl(m[2], '', m[1] as '[' | '(').outerHTML.replace(
        /(<span class="dataview inline-field-(?:value|standalone-value)"[^>]*>)(<\/span>)/,
        `$1${m[3]}$2`,
      );
      last = m.index + m[0].length;
    }
    if (!last) continue;
    el.innerHTML = out + src.slice(last);
  }
}

/** Same as `renderInlineDataview`, but waits for the plugin settings first. */
export function renderInlineDataviewWhenReady(root: HTMLElement, path: string | null): void {
  void ensurePluginSettings().then(() => renderInlineDataview(root, path));
}

/** Render inline queries and inline fields inside already-rendered markdown. */
export function renderInlineDataview(root: HTMLElement, path: string | null): void {
  const settings = dataviewSettings();
  if (settings.enableInlineDataview) {
    for (const code of [...root.querySelectorAll('code')]) {
      if (code.closest('pre')) continue;
      const expr = inlineQueryOf(code.textContent ?? '');
      if (expr === null) continue;
      code.replaceWith(inlineQueryEl(expr, path));
    }
  }
  if (settings.prettyRenderInlineFields) replaceFieldsIn(root);
}
