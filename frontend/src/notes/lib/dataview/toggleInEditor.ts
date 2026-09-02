import { api } from '../api';
import { getActiveEditor } from '../activeEditor';
import { useStore } from '../store';

/**
 * Tick a task off through the open editor instead of through the file.
 *
 * A query result may point at any note; when it points at the one on screen,
 * writing the file directly is wrong — the editor still holds the old text and
 * saves it back a moment later, so the tick silently disappears. In that case
 * the change is applied as an ordinary document edit and the normal save path
 * carries it to disk.
 *
 * Returns false when the task lives elsewhere (caller falls back to the API) or
 * when the line no longer matches, so nothing is written on a stale result.
 */
export async function toggleTaskInOpenNote(opts: {
  path: string;
  /** 0-based line index, as the query results report it. */
  line: number;
  /** The task's text after the marker, for verifying the line. */
  text: string;
  checked: boolean;
  mode: 'tasks' | 'dataview';
}): Promise<boolean> {
  const view = getActiveEditor();
  if (!view) return false;
  const open = useStore.getState().activePath;
  if (!open || open !== opts.path) return false;
  const n = opts.line + 1;
  if (n < 1 || n > view.state.doc.lines) return false;
  const line = view.state.doc.line(n);
  const m = /^(\s*[-*+]\s+\[)(.)(\]\s?)(.*)$/.exec(line.text);
  if (!m || (opts.text && m[4].trim() !== opts.text.trim())) return false;

  const { lines } = await api.dvTaskLines({ line: line.text, checked: opts.checked, mode: opts.mode });
  view.dispatch({ changes: { from: line.from, to: line.to, insert: lines.join('\n') }, userEvent: 'input' });
  return true;
}
