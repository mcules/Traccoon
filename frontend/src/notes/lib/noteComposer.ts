import { api } from './api';
import { useStore } from './store';
import { getActiveEditor } from './activeEditor';
import { tr } from "../../i18n";

/**
 * Splitting notes apart and putting them together.
 *
 * A note grows a section that deserves its own place, or two notes turn out to
 * be one. Doing that by hand means copy, paste, delete, and then fixing every
 * link that pointed at the old place — which is exactly the kind of chore that
 * gets postponed until the vault is untidy.
 *
 * Extracting leaves a link behind, so nothing is lost from where it was.
 */

const slug = (text: string) =>
  text
    .replace(/^#+\s*/, '')
    .replace(/[\\/:*?"<>|]/g, '-')
    .trim()
    .slice(0, 80) || tr("notes_sidebar.new_note");

/** The section under the heading the caret sits in, as a line range. */
export function headingRangeAt(lines: string[], line: number): { from: number; to: number; title: string } | null {
  let start = -1;
  let level = 0;
  for (let i = line; i >= 0; i--) {
    const m = /^(#{1,6})\s+(.*)$/.exec(lines[i]);
    if (m) {
      start = i;
      level = m[1].length;
      break;
    }
  }
  if (start < 0) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    const m = /^(#{1,6})\s/.exec(lines[i]);
    if (m && m[1].length <= level) {
      end = i;
      break;
    }
  }
  return { from: start, to: end, title: lines[start].replace(/^#+\s*/, '').trim() };
}

/** Move the selection, or the current section, into a note of its own. */
export async function extractToNote(mode: 'selection' | 'heading'): Promise<void> {
  const view = getActiveEditor();
  const { activePath, notify } = useStore.getState();
  if (!view || !activePath) {
    notify(tr("notes_workspace.none_open_short"));
    return;
  }
  const doc = view.state.doc;
  const sel = view.state.selection.main;

  let from: number;
  let to: number;
  let title: string;
  if (mode === 'selection') {
    if (sel.empty) {
      notify(tr("notes_compose.nothing_selected"));
      return;
    }
    from = sel.from;
    to = sel.to;
    title = slug(doc.sliceString(from, Math.min(to, from + 100)).split('\n')[0]);
  } else {
    const lines = doc.toString().split('\n');
    const range = headingRangeAt(lines, doc.lineAt(sel.head).number - 1);
    if (!range) {
      notify(tr("notes_compose.no_heading"));
      return;
    }
    from = doc.line(range.from + 1).from;
    to = range.to >= lines.length ? doc.length : doc.line(range.to + 1).from;
    title = slug(range.title);
  }

  const text = doc.sliceString(from, to).trim();
  const folder = activePath.includes('/') ? activePath.slice(0, activePath.lastIndexOf('/')) : '';
  let target = `${folder ? `${folder}/` : ''}${title}.md`;
  // Never write over an existing note while tidying one up.
  for (let i = 2; await api.read(target).then(() => true).catch(() => false); i++) {
    target = `${folder ? `${folder}/` : ''}${title} ${i}.md`;
  }

  await api.write(target, `${text}\n`);
  view.dispatch({ changes: { from, to, insert: `![[${title}]]\n` }, userEvent: 'input' });
  await useStore.getState().loadTree();
  notify(`Ausgelagert nach ${target}`);
}

/**
 * Append this note to another and remove it, pointing its links at the target.
 * The link rewrite is the same one a rename uses.
 */
export async function mergeInto(targetPath: string): Promise<void> {
  const { activePath, notify, save } = useStore.getState();
  if (!activePath) return;
  await save();
  const source = await api.read(activePath);
  const target = await api.read(targetPath);
  const sourceText = typeof source === 'string' ? source : source.content;
  const targetText = typeof target === 'string' ? target : target.content;
  // Strip the source's frontmatter: a merged note keeps the target's properties.
  const body = sourceText.replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/, '').trim();
  await api.write(targetPath, `${targetText.replace(/\s+$/, '')}\n\n${body}\n`);
  // Links that pointed here now point at the target — same machinery as a move.
  await api.rename(activePath, targetPath).catch(() => {});
  await api.remove(activePath).catch(() => {});
  await useStore.getState().loadTree();
  await useStore.getState().openFile(targetPath);
  notify(`Zusammengeführt in ${targetPath}`);
}
