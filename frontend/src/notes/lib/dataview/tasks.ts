/**
 * Renders a ```tasks block (the Tasks plugin's query language).
 *
 * Kept close to what the plugin shows: the matching tasks with working
 * checkboxes, a backlink to the source note, group headings and the task count
 * — each of which the query can switch off with `hide …`.
 */

import { toggleTaskInOpenNote } from './toggleInEditor';
import { api } from '../api';
import { errorEl } from './render';
import { inlineMarkdown, taskFieldsHtml, wireInternalLinks } from './format';
import { renderInlineDataview } from './inline';
import { memo } from './cache';
import { ensurePluginSettings } from './settings';
import { tr, trKnown, language } from "../../../i18n";

interface TaskHit {
  text: string;
  completed: boolean;
  status: string;
  statusType: string;
  statusName: string;
  path: string;
  line: number;
  level: number;
  noteName: string;
  section?: string;
}

interface TasksGroup {
  key: string;
  label: string;
  link?: string;
  tasks: TaskHit[];
}

interface TasksResult {
  kind: 'tasks';
  groups: TasksGroup[];
  total: number;
  layout: { hideTaskCount: boolean; hideBacklink: boolean; hideToolbar: boolean; shortMode: boolean };
  warnings: string[];
}

function taskItem(t: TaskHit, hideBacklink: boolean): HTMLElement {
  const li = document.createElement('li');
  const closed = t.statusType === 'DONE' || t.statusType === 'CANCELLED';
  li.className = 'task-list-item plugin-tasks-list-item' + (closed ? ' is-checked' : '');
  li.dataset.task = t.status;
  li.dataset.taskStatusName = t.statusName;
  li.dataset.taskStatusType = t.statusType;

  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'task-list-item-checkbox';
  box.checked = closed;
  box.addEventListener('click', (ev) => {
    ev.stopPropagation();
    const checked = box.checked;
    // A task in the note that is open on screen has to go through the editor,
    // or its buffer saves the tick away again — see toggleTaskInOpenNote.
    void toggleTaskInOpenNote({ path: t.path, line: t.line, text: t.text, checked, mode: 'tasks' })
      .then((done) => (done ? null : api.dvToggleTask({ path: t.path, line: t.line, text: t.text, checked, mode: 'tasks' })))
      .then(() => li.classList.toggle('is-checked', checked))
      .catch((err: Error) => {
        box.checked = !checked;
        li.classList.add('dataview-error');
        li.title = err.message;
      });
  });

  // Checkbox in its own column, description and backlink in a second one, so a
  // wrapped line continues under the text rather than under the checkbox.
  const row = document.createElement('div');
  row.className = 'task-row';
  const body = document.createElement('div');
  body.className = 'task-body';
  const desc = document.createElement('span');
  desc.className = 'tasks-desc task-description';
  // Description and field markers are rendered apart, as the plugin does: the
  // dates get their own spans with the distance attribute CSS snippets hook on.
  const parts = taskFieldsHtml(t.text);
  // The due date goes in front of the description, where the eye lands first —
  // a task list is read by "when", and the desktop app puts it there too. The
  // remaining markers (priority, recurrence, done date) stay behind the text.
  if (parts.due) {
    const due = document.createElement('span');
    due.className = 'task-fields task-fields-lead';
    due.innerHTML = parts.due;
    body.appendChild(due);
  }
  desc.innerHTML = parts.description;
  body.appendChild(desc);
  if (parts.fields) {
    const fields = document.createElement('span');
    fields.className = 'task-fields';
    fields.innerHTML = parts.fields;
    body.appendChild(fields);
  }
  row.append(box, body);
  li.appendChild(row);

  if (!hideBacklink) {
    const extras = document.createElement('span');
    extras.className = 'task-extras';
    const back = document.createElement('span');
    back.className = 'tasks-backlink';
    const a = document.createElement('a');
    a.className = 'internal-link';
    a.href = '#';
    a.dataset.wikilink = t.path;
    a.textContent = t.section ? `${t.noteName} > ${t.section}` : t.noteName;
    back.append(document.createTextNode(' ('), a, document.createTextNode(')'));
    extras.appendChild(back);
    body.appendChild(extras);
  }
  return li;
}

/**
 * What the heading of a group says, in the language of the account.
 *
 * The server sends both: a key when the heading is a word it chose, and the
 * finished sentence beside it for whoever has no catalogue. A group by path or
 * by status carries no key at all — those words come out of the vault, and
 * translating them would be inventing.
 *
 * A group by day is the one that is formatted rather than looked up: the key is
 * the date, and the weekday beside it is something the browser already knows in
 * every language.
 */
function groupHeading(g: { key: string; label: string; label_key?: string }): string {
  if (!g.label_key) return g.label;
  if (g.label_key === 'notes_task.group_date') {
    const d = new Date(`${g.key}T12:00:00`);
    if (Number.isNaN(d.getTime())) return g.label;
    return `${g.key} ${new Intl.DateTimeFormat(language(), { weekday: 'long' }).format(d)}`;
  }
  return trKnown(g.label_key) ?? g.label;
}

function renderTasks(host: HTMLElement, res: TasksResult): void {
  host.textContent = '';
  if (!res.total) {
    const empty = document.createElement('div');
    empty.className = 'dataview-empty';
    empty.textContent = tr("notes_task.none_found");
    host.appendChild(empty);
  }
  for (const g of res.groups) {
    if (!g.tasks.length) continue;
    const heading = groupHeading(g);
    if (heading) {
      const h = document.createElement('h4');
      h.className = 'dataview-group-header tasks-group-heading';
      if (g.link) {
        const a = document.createElement('a');
        a.className = 'internal-link';
        a.href = '#';
        a.dataset.wikilink = g.link;
        a.textContent = heading;
        h.appendChild(a);
      } else {
        h.textContent = heading;
      }
      host.appendChild(h);
    }
    const ul = document.createElement('ul');
    ul.className = 'dataview contains-task-list plugin-tasks-query-result';
    for (const t of g.tasks) ul.appendChild(taskItem(t, res.layout.hideBacklink));
    host.appendChild(ul);
  }
  if (!res.layout.hideTaskCount && res.total) {
    const count = document.createElement('div');
    count.className = 'tasks-count task-count';
    count.textContent = `${res.total} ${res.total === 1 ? 'task' : 'tasks'}`;
    host.appendChild(count);
  }
  // An instruction we could not read is shown rather than silently ignored —
  // otherwise a typo quietly widens the result set.
  if (res.warnings.length) {
    const w = document.createElement('div');
    w.className = 'dataview-error tasks-warning';
    w.textContent = tr("notes_task.not_understood") + " " + res.warnings.join(" · ");
    host.appendChild(w);
  }
}

/** Run a ```tasks block and render it into a fresh element. */
export function renderTasksBlock(code: string): HTMLElement {
  const host = document.createElement('div');
  host.className = 'dataview-block tasks-block';
  const wait = document.createElement('div');
  wait.className = 'dataview-empty';
  wait.textContent = '…';
  host.appendChild(wait);
  wireInternalLinks(host);
  void ensurePluginSettings()
    .then(() => memo('tasks', code, () => api.dvTasks(code)))
    .then((res) => {
      if (res.kind === 'error') {
        host.textContent = '';
        host.appendChild(errorEl(res.message));
        return;
      }
      renderTasks(host, res as TasksResult);
      // Inline fields inside a task's text render pretty here too.
      renderInlineDataview(host, null);
    })
    .catch((err: Error) => {
      host.textContent = '';
      host.appendChild(errorEl(`Tasks: ${err.message}`));
    });
  return host;
}
