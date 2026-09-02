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
    { id: 'file-explorer:new-file', name: 'Neue Notiz', hotkey: 'Mod+N', run: () => store().newNote() },
    { id: 'canvas:new-file', name: 'Neues Canvas', run: () => store().newCanvas() },
    { id: 'daily-notes', name: 'Tagesnotiz öffnen', run: () => store().openDailyNote() },
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
    { id: 'editor:save-file', name: 'Speichern', hotkey: 'Mod+S', enabled: hasNote, run: () => store().save() },
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
      name: 'Rechts daneben öffnen',
      enabled: hasNote,
      run: () => {
        const p = store().activePath;
        if (p) store().openToSide(p);
      },
    },
    { id: 'global-search:open', name: 'Suche öffnen', run: () => store().setLeftPanel('search') },
    { id: 'bookmarks:open', name: 'Lesezeichen öffnen', run: () => store().setLeftPanel('bookmarks') },
    { id: 'graph:open', name: 'Graph öffnen', run: () => store().setGraph(true) },
    { id: 'calendar:open', name: 'Kalender öffnen', run: () => store().openCalendar() },
    {
      id: 'calendar:sync-today',
      name: 'Termine in die heutige Notiz schreiben',
      run: async () => {
        const now = new Date();
        const p2 = (n: number) => String(n).padStart(2, '0');
        const date = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())}`;
        const r = await api.calendarSyncDay(date);
        store().notify(
          r.written ? `Termine übernommen (${r.added} neu, ${r.updated} geändert)` : 'Nichts zu ändern',
        );
      },
    },
    { id: 'app:open-settings', name: 'Einstellungen öffnen', run: () => store().setSettings(true) },
    {
      id: 'note-composer:extract-heading',
      name: 'Abschnitt in neue Notiz auslagern',
      editor: true,
      enabled: hasEditor,
      run: async () => {
        const { extractToNote } = await import('./noteComposer');
        await extractToNote('heading');
      },
    },
    {
      id: 'note-composer:split-file',
      name: 'Auswahl in neue Notiz auslagern',
      editor: true,
      enabled: hasEditor,
      run: async () => {
        const { extractToNote } = await import('./noteComposer');
        await extractToNote('selection');
      },
    },
    {
      id: 'editor:attach-file',
      name: 'Datei anhängen',
      editor: true,
      enabled: hasEditor,
      run: () => store().setAttachSheet(true),
    },
    {
      id: 'templates:insert-template',
      name: 'Vorlage einfügen',
      editor: true,
      enabled: hasEditor,
      run: () => store().setTemplatePicker(true),
    },
    { id: 'app:open-trash', name: 'Papierkorb öffnen', run: () => store().setTrash(true) },
    {
      // The vault binds Mod+R to this. The browser does the same thing on that
      // key anyway, so honouring it costs nothing and keeps the two in step.
      id: 'app:reload',
      name: 'Oberfläche neu laden',
      run: () => window.location.reload(),
    },
    { id: 'markdown:toggle-preview', name: 'Leseansicht', run: () => store().setViewMode('reading') },
    { id: 'markdown:edit-mode', name: 'Bearbeiten (Live)', run: () => store().setViewMode('live') },
    { id: 'markdown:source-mode', name: 'Quelltext', run: () => store().setViewMode('source') },
    {
      id: 'notes:reindex',
      name: 'Suchindex neu aufbauen',
      run: async () => {
        store().notify('Suchindex wird neu aufgebaut…', 0);
        try {
          await api.reindex();
          store().notify('Suchindex neu aufgebaut');
        } catch {
          store().notify('Suchindex konnte nicht aufgebaut werden');
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
    { id: 'editor:toggle-bullet-list', name: 'Aufzählung', editor: true, enabled: hasEditor, run: () => fmtPrefixLines('- ') },
    { id: 'editor:toggle-numbered-list', name: 'Nummerierte Liste', editor: true, enabled: hasEditor, run: () => fmtPrefixLines('1. ') },
    { id: 'editor:toggle-checklist-status', name: 'Aufgabe an/aus', editor: true, enabled: hasEditor, run: () => fmtChecklist() },
    { id: 'editor:set-heading', name: 'Überschrift', editor: true, enabled: hasEditor, run: () => fmtPrefixLines('# ') },
    { id: 'editor:insert-link', name: 'Link einfügen', editor: true, enabled: hasEditor, run: () => fmtLink() },
    { id: 'editor:insert-wikilink', name: 'Wikilink einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('[[]]', 2) },
    { id: 'editor:insert-embed', name: 'Einbettung einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('![[]]', 3) },
    { id: 'editor:insert-tag', name: 'Tag einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('#') },
    { id: 'editor:insert-callout', name: 'Callout einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('> [!info]\n> ') },
    { id: 'editor:insert-table', name: 'Tabelle einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('| A | B |\n| --- | --- |\n|  |  |\n') },
    { id: 'editor:insert-horizontal-rule', name: 'Trennlinie einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('\n---\n') },
    { id: 'editor:insert-codeblock', name: 'Codeblock einfügen', editor: true, enabled: hasEditor, run: () => void fmtInsert('```\n\n```', 4) },
    { id: 'editor:indent-list', name: 'Einrücken', editor: true, enabled: hasEditor, run: () => fmtIndent() },
    { id: 'editor:unindent-list', name: 'Ausrücken', editor: true, enabled: hasEditor, run: () => fmtOutdent() },
    { id: 'editor:undo', name: 'Rückgängig', hotkey: 'Mod+Z', editor: true, enabled: hasEditor, run: () => fmtUndo() },
    { id: 'editor:redo', name: 'Wiederholen', hotkey: 'Mod+Shift+Z', editor: true, enabled: hasEditor, run: () => fmtRedo() },
    { id: 'editor:open-search', name: 'Im Dokument suchen', hotkey: 'Mod+F', enabled: hasEditor, run: () => void editorFind() },
    // the predecessor's own id for the Slides plugin, so a key bound to it in the
    // vault's hotkeys.json works here without a translation table.
    {
      id: 'slides:start',
      name: 'Präsentation starten',
      enabled: () => {
        const p = store().activePath;
        return !!p && /\.(md|markdown)$/i.test(p);
      },
      run: () => store().setPresenting(store().activePath),
    },
    {
      id: 'notes:tab-switcher',
      name: 'Geöffnete Notizen',
      run: () => store().setTabSwitcher(true),
    },
  ];
  registerCommands(cmds);
}
