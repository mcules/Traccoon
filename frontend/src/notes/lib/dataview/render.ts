/**
 * Turns a query result into the DOM the predecessor's Dataview produces: a table, a
 * bullet list or a task list, with links that behave like every other internal
 * link in the app (`data-wikilink`, handled by Preview / Live Preview).
 */

import { toggleTaskInOpenNote } from './toggleInEditor';
import { api, type DvGroupResult, type DvQueryResult } from '../api';
import { memo } from './cache';
import { dataviewSettings, ensurePluginSettings } from './settings';
import { escapeHtml, inlineMarkdown, valueHtml, wireInternalLinks } from './format';
import { renderInlineDataview } from './inline';
import { revive, type RawTask } from './values';

/* ----------------------------------------------------------- result views */

function tableEl(headers: string[], rows: unknown[][]): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'dataview-table-wrap';
  const table = document.createElement('table');
  table.className = 'dataview table-view-table';
  if (headers.length) {
    const thead = document.createElement('thead');
    thead.className = 'table-view-thead';
    const tr = document.createElement('tr');
    tr.className = 'table-view-tr-header';
    headers.forEach((h, i) => {
      const th = document.createElement('th');
      th.className = 'table-view-th';
      th.innerHTML = inlineMarkdown(h);
      // Dataview puts the result count in the first header cell.
      if (i === 0 && dataviewSettings().showResultCount) {
        const count = document.createElement('span');
        count.className = 'dataview small-text';
        count.textContent = String(rows.length);
        th.appendChild(count);
      }
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    table.appendChild(thead);
  }
  const tbody = document.createElement('tbody');
  tbody.className = 'table-view-tbody';
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const cell of row) {
      const td = document.createElement('td');
      td.innerHTML = valueHtml(revive(cell));
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function listEl(items: unknown[]): HTMLElement {
  const ul = document.createElement('ul');
  ul.className = 'dataview list-view-ul';
  for (const item of items) {
    const li = document.createElement('li');
    li.innerHTML = valueHtml(revive(item));
    ul.appendChild(li);
  }
  return ul;
}

function taskListEl(tasks: RawTask[]): HTMLElement {
  const ul = document.createElement('ul');
  ul.className = 'dataview contains-task-list';
  for (const t of tasks) {
    const li = document.createElement('li');
    li.className = 'task-list-item' + (t.completed ? ' is-checked' : '');
    li.dataset.task = t.completed ? 'x' : t.status;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'task-list-item-checkbox';
    box.checked = t.completed;
    box.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const checked = box.checked;
      // Through the editor when the task sits in the open note (see
      // toggleTaskInOpenNote), otherwise straight at the file.
      void toggleTaskInOpenNote({ path: t.path, line: t.line, text: t.text, checked, mode: 'dataview' })
        .then((done) => (done ? null : api.dvToggleTask({ path: t.path, line: t.line, text: t.text, checked, mode: 'dataview' })))
        .then(() => {
          li.classList.toggle('is-checked', checked);
        })
        .catch((err: Error) => {
          box.checked = !checked;
          li.classList.add('dataview-error');
          li.title = err.message;
        });
    });
    const span = document.createElement('span');
    span.className = 'task-body';
    span.innerHTML = inlineMarkdown(t.text);
    // Checkbox and text sit in their own row so a wrapped line continues under
    // the text instead of falling back under the checkbox.
    const row = document.createElement('div');
    row.className = 'task-row';
    row.append(box, span);
    li.appendChild(row);
    if (t.children?.length) li.appendChild(taskListEl(t.children));
    ul.appendChild(li);
  }
  return ul;
}

function groupHeader(key: unknown): HTMLElement {
  const h = document.createElement('h4');
  h.className = 'dataview-group-header';
  h.innerHTML = valueHtml(revive(key));
  return h;
}

/** Dataview's own empty-result notice, down to the wording. */
function emptyEl(kind: 'list' | 'table' | 'task'): HTMLElement {
  const box = document.createElement('div');
  box.className = 'dataview dataview-error-box';
  const p = document.createElement('p');
  p.className = 'dataview dataview-error-message';
  p.textContent = `Dataview: No results to show for ${kind} query.`;
  box.appendChild(p);
  return box;
}

function loadingEl(): HTMLElement {
  const pre = document.createElement('div');
  pre.className = 'dataview-empty';
  pre.textContent = '…';
  return pre;
}

export function errorEl(message: string): HTMLElement {
  const div = document.createElement('div');
  div.className = 'dataview-error';
  div.textContent = message;
  return div;
}

/** Render a finished query result into `host` (cleared first). */
export function renderResult(host: HTMLElement, res: DvQueryResult, path?: string | null): void {
  host.textContent = '';
  host.classList.add('dataview-result');
  if (res.kind === 'error') {
    host.appendChild(errorEl(res.message));
    return;
  }
  const groups: DvGroupResult[] | undefined = 'groups' in res ? res.groups : undefined;
  // Inline fields and queries inside result cells render like anywhere else.
  const withInline = () => renderInlineDataview(host, path ?? null);
  queueMicrotask(withInline);

  if (res.kind === 'task') {
    const total = res.groups.reduce((n, g) => n + g.tasks.length, 0);
    if (!total) {
      if (dataviewSettings().warnOnEmptyResult) host.appendChild(emptyEl('task'));
      return;
    }
    for (const g of res.groups) {
      if (!g.tasks.length) continue;
      if (g.key !== null) host.appendChild(groupHeader(g.key));
      host.appendChild(taskListEl(g.tasks as unknown as RawTask[]));
    }
    return;
  }

  if (groups && groups.length) {
    for (const g of groups) {
      host.appendChild(groupHeader(g.key));
      host.appendChild(res.kind === 'table' ? tableEl(res.headers, g.rows) : listEl(g.items));
    }
    return;
  }

  if (res.kind === 'table') {
    host.appendChild(tableEl(res.headers, res.rows));
    if (!res.rows.length && dataviewSettings().warnOnEmptyResult) host.appendChild(emptyEl('table'));
    return;
  }
  if (!res.items.length) {
    if (dataviewSettings().warnOnEmptyResult) host.appendChild(emptyEl('list'));
    return;
  }
  host.appendChild(listEl(res.items));
}

/** Run a ```dataview block and render it into a fresh element. */
export function renderDqlBlock(code: string, path: string | null): HTMLElement {
  const host = document.createElement('div');
  host.className = 'dataview-block';
  host.appendChild(loadingEl());
  wireInternalLinks(host);
  void ensurePluginSettings()
    .then(() => memo('query', `${path ?? ''}::${code}`, () => api.dvQuery(code, path ?? undefined)))
    .then((res) => renderResult(host, res, path))
    .catch((err: Error) => {
      host.textContent = '';
      host.appendChild(errorEl(`Dataview: ${err.message}`));
    });
  return host;
}
