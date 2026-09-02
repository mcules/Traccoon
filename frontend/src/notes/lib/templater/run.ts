import { api } from '../api';
import { useStore } from '../store';
import { makeTp, type TpContext } from './tp';
import { makeApp } from '../dataview/dvjs';

/**
 * Run a template and return the text it produces.
 *
 * The generated code arrives as a module from this origin (the content policy
 * forbids building functions from strings here), and is handed the same `tp`
 * the desktop plugin offers plus the `app` shim the dataviewjs blocks already
 * use — the vault's templates reach for both.
 */
export async function runTemplate(templatePath: string, ctx: TpContext): Promise<string> {
  const { id } = await api.templaterCompile(templatePath);
  // Same as the script import in dvjs: a real module import carries the bridge
  // prefix itself, because it never passes the fetch wrapper.
  const mod = (await import(/* @vite-ignore */ `/api/notes/templater/module/${id}.mjs`)) as {
    default: (tp: unknown, app: unknown, host: unknown) => Promise<string>;
  };
  // The shim wants a run state; a template has no container to draw into and
  // nothing to report misses about, so it gets an empty one.
  /** What the predecessor's `new Notice(text)` does here: say it in the status toast. */
  class Notice {
    constructor(message: string, timeout = 4000) {
      useStore.getState().notify(String(message), timeout);
    }
  }
  return mod.default(
    makeTp(ctx),
    makeApp({ misses: new Set(), container: document.createElement('div'), path: ctx.path ?? null }),
    { Notice },
  );
}

/** Apply a template to the note that is open, inserting at the caret. */
export async function insertTemplate(templatePath: string): Promise<void> {
  const { activePath, content, notify } = useStore.getState();
  const title = (activePath ?? '').split('/').pop()?.replace(/\.(md|markdown)$/i, '') ?? '';
  try {
    const text = await runTemplate(templatePath, { title, path: activePath ?? undefined, content });
    const { fmtInsert } = await import('../activeEditor');
    if (!fmtInsert(text)) notify('Keine Notiz offen, in die die Vorlage passt');
  } catch (e) {
    notify(`Vorlage: ${(e as Error).message}`);
  }
}

let folderTemplates: Array<{ folder: string; template: string }> | null = null;

/**
 * The template a new note in this folder should start from, if the vault names
 * one. Deepest match wins, so a rule for a subfolder beats one for its parent.
 */
export async function templateForFolder(notePath: string): Promise<string | null> {
  if (!folderTemplates) {
    folderTemplates = await api
      .templaterConfig()
      .then((r) => r.folderTemplates)
      .catch(() => []);
  }
  const dir = notePath.includes('/') ? notePath.slice(0, notePath.lastIndexOf('/')) : '';
  const hits = folderTemplates.filter((f) => dir === f.folder || dir.startsWith(`${f.folder}/`));
  if (!hits.length) return null;
  return hits.sort((a, b) => b.folder.length - a.folder.length)[0].template;
}

/** Fill a newly created note from its folder's template, if there is one. */
export async function applyFolderTemplate(notePath: string): Promise<boolean> {
  const tpl = await templateForFolder(notePath);
  if (!tpl) return false;
  const title = notePath.split('/').pop()?.replace(/\.(md|markdown)$/i, '') ?? '';
  try {
    const text = await runTemplate(tpl, { title, path: notePath, content: '' });
    if (!text.trim()) return false;
    await api.write(notePath, text);
    return true;
  } catch (e) {
    useStore.getState().notify(`Vorlage: ${(e as Error).message}`);
    return false;
  }
}
