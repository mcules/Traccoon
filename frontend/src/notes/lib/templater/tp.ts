import { api } from '../api';
import { useStore } from '../store';
import { formatMoment, addDays, parseMoment } from './moment';
import { askText, askChoice } from './ask';

/**
 * The `tp` object the vault's templates actually use.
 *
 * Measured rather than guessed: across every template in the vault exactly
 * eight functions appear — prompt, date.now, suggester, file.title,
 * file.find_tfile, file.create_new, config.active_file and file.content. User
 * scripts and shell access are switched off in the vault's own settings, which
 * removes the part of Templater that would be genuinely dangerous to rebuild.
 *
 * Anything not implemented throws by name, so a template that grows a new call
 * says so instead of quietly producing half a note.
 */

export interface TpContext {
  /** Title of the note being created — `tp.file.title`. */
  title: string;
  /** Path of the note, once it exists. */
  path?: string;
  /** Its current text, for `tp.file.content`. */
  content?: string;
}

const missing = (name: string) => () => {
  throw new Error(`Diese Vorlage braucht ${name}, das hier (noch) nicht nachgebaut ist.`);
};

export function makeTp(ctx: TpContext) {
  const notify = useStore.getState().notify;

  const file = {
    title: ctx.title,
    content: ctx.content ?? '',
    /** The note this template is being applied to. */
    find_tfile: async (name: string) => {
      const r = await api.resolve(name).catch(() => ({ path: null as string | null }));
      return r.path ? { path: r.path, basename: r.path.split('/').pop()?.replace(/\.md$/i, '') ?? name } : null;
    },
    /**
     * Create a note from a template. The vault uses it to spin off a company
     * stub while filling in a person — `openNew` is false there, so the note is
     * made and left alone.
     */
    create_new: async (
      template: { path: string } | string,
      filename: string,
      openNew = false,
      folder?: { path: string } | string,
    ) => {
      const dir = typeof folder === 'string' ? folder : folder?.path ?? '';
      const path = `${dir ? `${dir}/` : ''}${filename}.md`;
      const tplPath = typeof template === 'string' ? template : template.path;
      const { text } = await api.template(tplPath, filename);
      await api.write(path, text);
      await useStore.getState().loadTree();
      if (openNew) await useStore.getState().openFile(path);
      return { path, basename: filename };
    },
    // Present but unused by this vault — named so a new call is obvious.
    cursor: missing('tp.file.cursor'),
    include: missing('tp.file.include'),
    move: missing('tp.file.move'),
    rename: missing('tp.file.rename'),
  };

  return {
    file,
    config: { active_file: ctx.path ? { path: ctx.path, basename: ctx.title } : null },
    date: {
      /**
       * `tp.date.now(format, offset, reference, referenceFormat)` — the
       * four-argument form is how the daily template says "seven days after the
       * day this note is named for".
       */
      now: (format = 'YYYY-MM-DD', offset = 0, reference?: string, referenceFormat = 'YYYY-MM-DD') => {
        const base = reference ? parseMoment(reference, referenceFormat) ?? new Date() : new Date();
        return formatMoment(addDays(base, Number(offset) || 0), format);
      },
      tomorrow: (format = 'YYYY-MM-DD') => formatMoment(addDays(new Date(), 1), format),
      yesterday: (format = 'YYYY-MM-DD') => formatMoment(addDays(new Date(), -1), format),
    },
    system: {
      prompt: (label: string, fallback = '', _throwOnCancel = false, multiline = false) =>
        askText(label, fallback, multiline),
      suggester: (
        labels: string[] | ((item: unknown) => string),
        values: unknown[],
        _throwOnCancel = false,
        placeholder = '',
      ) => {
        const texts = Array.isArray(labels) ? labels : values.map((v) => String(labels(v)));
        return askChoice(texts, values, placeholder);
      },
      clipboard: async () => navigator.clipboard.readText().catch(() => ''),
    },
    // Reached only if a template starts using them.
    web: new Proxy({}, { get: (_t, k) => missing(`tp.web.${String(k)}`) }),
    user: new Proxy({}, { get: (_t, k) => missing(`tp.user.${String(k)}`) }),
    frontmatter: new Proxy({}, { get: () => undefined }),
    _notify: notify,
  };
}
