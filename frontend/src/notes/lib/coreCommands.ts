import { registerCommands, type Command } from './commands';
import { useStore } from './store';
import { api } from './api';
import {
  fmtInline,
  fmtChecklist,
  fmtPrefixLines,
  fmtInsert,
  fmtLink,
  fmtIndent,
  fmtOutdent,
  fmtUndo,
  fmtRedo,
  editorFind,
  getActiveEditor,
} from './activeEditor';
import { tr } from "../../i18n";

/**
 * The app's own commands, registered once at startup.
 *
 * Ids are the predecessor's, so the vault's own files can be read rather than
 * re-invented: `app.json` names the mobile toolbar by these ids, `hotkeys.json`
 * binds keys to them. Where this app has something the predecessor does not, the id
 * carries a `notes:` prefix so the two can never be confused.
 */

const store = () => useStore.getState();
const hasEditor = () => !!getActiveEditor();
const hasNote = () => !!store().activePath;

export function registerCoreCommands(): void {
  const cmds: Command[] = [
    // --- files and workspace
    { id: 'file-explorer:new-file', name: tr("notes_sidebar.new_note"), hotkey: 'Mod+N', run: () => store().newNote() },
    { id: 'canvas:new-file', name: 'Neues Canvas', run: () => store().newCanvas() },
    { id: 'daily-notes', name: tr("notes_cmd.open_daily_note"), run: () => store().openDailyNote() },
    {
      id: 'daily-notes:goto-prev',
      name: 'Tagesnotiz: gestern',
      run: () => store().openDailyNote(-1),
    },
    {
      id: 'daily-notes:goto-next',
      name: 'Tagesnotiz: morgen',
      run: () => store().openDailyNote(1),
    },
    { id: 'editor:save-file', name: tr("common.save"), hotkey: 'Mod+S', enabled: hasNote, run: () => store().save() },
    {
      id: 'bookmarks:bookmark-current-view',
      name: 'Lesezeichen setzen/entfernen',
      enabled: hasNote,
      run: () => {
        const p = store().activePath;
        if (p) store().toggleBookmark(p);
      },
    },
    {
      id: 'workspace:split-vertical',
      name: tr("notes_workspace.open_beside"),
      enabled: hasNote,
      run: () => {
        const p = store().activePath;
        if (p) store().openToSide(p);
      },
    },
    { id: 'global-search:open', name: tr("notes_cmd.open_search"), run: () => store().setLeftPanel('search') },
    { id: 'bookmarks:open', name: tr("notes_cmd.open_bookmarks"), run: () => store().setLeftPanel('bookmarks') },
    { id: 'graph:open', name: tr("notes_cmd.open_graph"), run: () => store().setGraph(true) },
    { id: 'calendar:open', name: tr("notes_cmd.open_calendar"), run: () => store().openCalendar() },
    {
      id: 'calendar:sync-today',
      name: tr("notes_calendar.write_into_today"),
      run: async () => {
        const now = new Date();
        const p2 = (n: number) => String(n).padStart(2, '0');
        const date = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}`;
        const r = await api.calendarSyncDay(date);
        store().notify(
          r.written ? `Termine übernommen (${r.added} neu, ${r.updated} geändert)` : tr("notes_cmd.nothing_to_change"),
        );
      },
    },
    { id: 'app:open-settings', name: tr("notes_cmd.open_settings"), run: () => { window.location.href = '/account/notes'; } },
    { id: 'app:open-hotkeys', name: tr("notes_hotkeys.title"), run: () => store().setHotkeys(true) },
    {
      id: 'note-composer:extract-heading',
      name: tr("notes_cmd.section_to_new_note"),
      editor: true,
      enabled: hasEditor,
      run: async () => {
        const { extractToNote } = await import('./noteComposer');
        await extractToNote('heading');
      },
    },
    {
      id: 'note-composer:split-file',
      name: tr("notes_cmd.selection_to_new_note"),
      editor: true,
      enabled: hasEditor,
      run: async () => {
        const { extractToNote } = await import('./noteComposer');
        await extractToNote('selection');
      },
    },
    {
      id: 'editor:attach-file',
      name: tr("notes_cmd.attach_file"),
      editor: true,
      enabled: hasEditor,
      run: () => store().setAttachSheet(true),
    },
    {
      id: 'templates:insert-template',
      name: tr("notes_cmd.insert_template"),
      editor: true,
      enabled: hasEditor,
      run: () => store().setTemplatePicker(true),
    },
    { id: 'app:open-trash', name: tr("notes_cmd.open_trash"), run: () => store().setTrash(true) },
    {
      // The vault binds Mod+R to this. The browser does the same thing on that
      // key anyway, so honouring it costs nothing and keeps the two in step.
      id: 'app:reload',
      name: tr("notes_cmd.reload"),
      run: () => window.location.reload(),
    },
    { id: 'markdown:toggle-preview', name: 'Leseansicht', run: () => store().setViewMode('reading') },
    { id: 'markdown:edit-mode', name: 'Bearbeiten (Live)', run: () => store().setViewMode('live') },
    { id: 'markdown:source-mode', name: 'Quelltext', run: () => store().setViewMode('source') },
    {
      id: 'notes:reindex',
      name: tr("notes_cmd.rebuild_index"),
      run: async () => {
        store().notify(tr("notes_cmd.rebuilding_index"), 0);
        try {
          await api.reindex();
          store().notify(tr("notes_cmd.index_rebuilt"));
        } catch {
          store().notify(tr("notes_cmd.index_failed"));
        }
      },
    },

    // --- editor: formatting
    { id: 'editor:toggle-bold', name: 'Fett', hotkey: 'Mod+B', editor: true, enabled: hasEditor, run: () => fmtInline('**') },
    { id: 'editor:toggle-italics', name: 'Kursiv', hotkey: 'Mod+I', editor: true, enabled: hasEditor, run: () => fmtInline('*') },
    { id: 'editor:toggle-strikethrough', name: 'Durchgestrichen', editor: true, enabled: hasEditor, run: () => fmtInline('~~') },
    { id: 'editor:toggle-highlight', name: 'Hervorgehoben', editor: true, enabled: hasEditor, run: () => fmtInline('==') },
    { id: 'editor:toggle-code', name: 'Code', editor: true, enabled: hasEditor, run: () => fmtInline('`') },
    { id: 'editor:toggle-blockquote', name: 'Zitat', editor: true, enabled: hasEditor, run: () => fmtPrefixLines('> ') },
    { id: 'editor:toggle-bullet-list', name: tr("notes_cmd.bullet_list"), editor: true, enabled: hasEditor, run: () => fmtPrefixLines('- ') },
    { id: 'editor:toggle-numbered-list', name: 'Nummerierte Liste', editor: true, enabled: hasEditor, run: () => fmtPrefixLines('1. ') },
    { id: 'editor:toggle-checklist-status', name: 'Aufgabe an/aus', editor: true, enabled: hasEditor, run: () => fmtChecklist() },
    { id: 'editor:set-heading', name: tr("notes_cmd.heading"), editor: true, enabled: hasEditor, run: () => fmtPrefixLines('# ') },
    { id: 'editor:insert-link', name: tr("notes_cmd.insert_link"), editor: true, enabled: hasEditor, run: () => fmtLink() },
    { id: 'editor:insert-wikilink', name: tr("notes_cmd.insert_wikilink"), editor: true, enabled: hasEditor, run: () => void fmtInsert('[[]]', 2) },
    { id: 'editor:insert-embed', name: tr("notes_cmd.insert_embed"), editor: true, enabled: hasEditor, run: () => void fmtInsert('![[]]', 3) },
    { id: 'editor:insert-tag', name: tr("notes_cmd.insert_tag"), editor: true, enabled: hasEditor, run: () => void fmtInsert('#') },
    { id: 'editor:insert-callout', name: tr("notes_cmd.insert_callout"), editor: true, enabled: hasEditor, run: () => void fmtInsert('> [!info]\n> ') },
    { id: 'editor:insert-table', name: tr("notes_cmd.insert_table"), editor: true, enabled: hasEditor, run: () => void fmtInsert('| A | B |\n| --- | --- |\n|  |  |\n') },
    { id: 'editor:insert-horizontal-rule', name: tr("notes_cmd.insert_rule"), editor: true, enabled: hasEditor, run: () => void fmtInsert('\n---\n') },
    { id: 'editor:insert-codeblock', name: tr("notes_cmd.insert_code"), editor: true, enabled: hasEditor, run: () => void fmtInsert('```\n\n```', 4) },
    { id: 'editor:indent-list', name: tr("notes_cmd.indent"), editor: true, enabled: hasEditor, run: () => fmtIndent() },
    { id: 'editor:unindent-list', name: tr("notes_cmd.outdent"), editor: true, enabled: hasEditor, run: () => fmtOutdent() },
    { id: 'editor:undo', name: tr("notes_cmd.undo"), hotkey: 'Mod+Z', editor: true, enabled: hasEditor, run: () => fmtUndo() },
    { id: 'editor:redo', name: 'Wiederholen', hotkey: 'Mod+Shift+Z', editor: true, enabled: hasEditor, run: () => fmtRedo() },
    { id: 'editor:open-search', name: tr("notes_cmd.find_in_note"), hotkey: 'Mod+F', enabled: hasEditor, run: () => void editorFind() },
    // the predecessor's own id for the Slides plugin, so a key bound to it in the
    // vault's hotkeys.json works here without a translation table.
    {
      id: 'slides:start',
      name: tr("notes_cmd.start_presentation"),
      enabled: () => {
        const p = store().activePath;
        return !!p && /\.(md|markdown)$/i.test(p);
      },
      run: () => store().setPresenting(store().activePath),
    },
    {
      id: 'notes:tab-switcher',
      name: tr("notes_cmd.open_notes"),
      run: () => store().setTabSwitcher(true),
    },
  ];
  registerCommands(cmds);
}
