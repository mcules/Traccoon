import { useEffect, useRef, useState } from 'react';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { EditorView, keymap, highlightActiveLine, drawSelection } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { search, searchKeymap } from '@codemirror/search';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { languages } from '@codemirror/language-data';
import { syntaxHighlighting, indentUnit } from '@codemirror/language';
import { notesHighlightStyle } from '../lib/highlight';
import { useStore } from '../lib/store';
import type { TreeNode } from '../lib/api';
import { notesKeymap, caretToFirstSafeLine } from '../lib/editorCommands';
import { suggesterPlugin, setLinkSuggestFiles, setTagSuggestTags } from '../lib/suggest';
import {
  livePreviewPlugin,
  livePreviewState,
  livePreviewTheme,
  frontmatterField,
  tableField,
  htmlBlockField,
  mermaidField,
  dataviewField,
  noteEmbedField,
  emptyBlockState,
  listFoldState,
  setListFolds,
  toggleListFold,
  listFoldDeco,
  dataviewInlineField,
  editorFocusedField,
  editorFocusReporter,
  userEngagedField,
  notePathField,
  rememberNotePath,
  setNotePath,
  htmlRenderedState,
  htmlPreviewField,
  calloutFoldState,
  calloutFoldDeco,
  noteTitleField,
  inlineTitleField,
  editorClickFix,
  livePreviewReadonly,
  setLivePreviewReadonly,
  setLivePreviewEnabled,
  setLivePreviewLinkHandler,
  setLivePreviewMenuHandler,
  setLivePreviewNoteEmbedProvider,
  setLivePreviewPropertyProvider,
  setLivePreviewPropertyTypes,
  setLivePreviewPropertyTypeSetter,
  setLivePreviewTagProvider,
  setNoteTitle,
} from '../lib/livePreview';
import { tr } from "../../i18n";
import { renderMarkdown } from '../lib/markdown';
import { extractEmbedSection } from '../lib/embedSection';
import { setActiveEditor } from '../lib/activeEditor';
import { api } from '../lib/api';
import { memo } from '../lib/dataview/cache';
import { diffLines, lineEditsToChanges, toLines } from '../lib/merge';

const titleOf = (path: string | null) =>
  path ? (path.split('/').pop() ?? path).replace(/\.(md|markdown)$/i, '') : '';

// Reading mode = the same Live Preview editor, made read-only via this compartment.
const readonlyExt = (reading: boolean) =>
  reading ? [EditorView.editable.of(false), EditorState.readOnly.of(true)] : [];

export default function Editor() {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const readonlyComp = useRef(new Compartment()).current;
  const applyingExternal = useRef(false);
  // Which note the editor doc currently holds — tells a note switch apart from
  // a change to the note that is already open.
  const appliedPath = useRef<string | null>(null);
  const viewRef = view;
  // Last resort of the watchdog below: rebuild the editor from scratch.
  const [remountKey, setRemountKey] = useState(0);
  const activePath = useStore((s) => s.activePath);
  const content = useStore((s) => s.content);
  const setContent = useStore((s) => s.setContent);
  const save = useStore((s) => s.save);
  const viewMode = useStore((s) => s.viewMode);
  const openWikilink = useStore((s) => s.openWikilink);
  const openContextMenu = useStore((s) => s.openContextMenu);
  const setLeftPanel = useStore((s) => s.setLeftPanel);
  const tree = useStore((s) => s.tree);
  // The vault decides whether text is capped to a reading column; this user has
  // it off, and hard-coding either way made the setting a lie.
  const [readableLineWidth, setReadableLineWidth] = useState(false);
  /**
   * What Tab puts in, from the vault's own setting.
   *
   * CodeMirror indents by two spaces, the predecessor by a tab shown four wide. In a
   * document written with the latter, two spaces are not a nesting level at
   * all — the list stays where it is, and Tab looks like it does nothing.
   */
  const [indent, setIndent] = useState('\t');
  /** Den Namen der Notiz ueber ihrem Text wiederholen - aus `appearance.json`. */
  const [inlineTitle, setInlineTitle] = useState(false);
  useEffect(() => {
    api
      .vaultConfig()
      .then((c) => {
        setReadableLineWidth(c.app.readableLineLength);
        setIndent(c.app.useTab ? '\t' : ' '.repeat(c.app.tabSize || 4));
        setInlineTitle(c.app.showInlineTitle);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLivePreviewLinkHandler(openWikilink);
  }, [openWikilink]);

  useEffect(() => {
    setLivePreviewMenuHandler(openContextMenu);
    setLivePreviewPropertyProvider(() => api.properties().then((r) => r.properties).catch(() => []));
    setLivePreviewTagProvider(() => api.tags().then((r) => r.tags.map((t) => t.tag)).catch(() => []));
    // ![[note]] transclusion: resolve + render with the same pipeline as Reading.
    // Cached: a dashboard embeds the same notes on every re-render, and each
    // embed used to cost a resolve plus a read. The cache is dropped whenever
    // the vault changes on disk.
    const resolveEmbed = (target: string) =>
      memo('embed', target, async () => {
        try {
          const { path } = await api.resolve(target);
          if (!path) return null;
          const r = await api.read(path);
          return { path, content: typeof r === 'string' ? r : r.content };
        } catch {
          return null;
        }
      });
    setLivePreviewNoteEmbedProvider(async (target) => {
      const note = await resolveEmbed(target.split('#')[0].trim());
      if (!note) return null;
      // `![[Note#Section]]` shows that section only — and the embedded note is
      // what a query inside it runs against, so its path travels along.
      const section = extractEmbedSection(note.content, target);
      const html = await renderMarkdown(section, { rawUrl: (p) => api.rawUrl(p), resolveEmbed });
      return { html, path: note.path };
    });
  }, [openContextMenu]);

  // Feed the `[[` link suggester (vault file paths) and the `#` tag suggester.
  useEffect(() => {
    const files: string[] = [];
    const walk = (n: TreeNode) => {
      if (n.type === 'file') files.push(n.path);
      n.children?.forEach(walk);
    };
    if (tree) walk(tree);
    setLinkSuggestFiles(() => files);
  }, [tree]);
  useEffect(() => {
    let tags: string[] = [];
    api
      .tags()
      .then((r) => {
        tags = r.tags.map((t) => t.tag.replace(/^#/, ''));
      })
      .catch(() => {});
    setTagSuggestTags(() => tags);
  }, [activePath]);

  // Load the vault's property type registry once. Where it lives on the disk is
  // the server's business, not the browser's.
  useEffect(() => {
    setLivePreviewPropertyTypeSetter((key, type) => api.setPropertyType(key, type).then((r) => r.types));
    api
      .propertyTypes()
      .then((r) => {
        setLivePreviewPropertyTypes(r.types);
        const v = view.current;
        if (v) v.dispatch({ effects: setLivePreviewEnabled.of(v.state.field(livePreviewState)) });
      })
      .catch(() => {});
  }, []);

  // --- editor formatting actions (used by the right-click menu) ---
  const wrap = (before: string, after = before) => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    const sel = v.state.sliceDoc(from, to);
    v.dispatch({
      changes: { from, to, insert: before + sel + after },
      selection: { anchor: from + before.length, head: from + before.length + sel.length },
    });
    v.focus();
  };
  const prefixLines = (prefix: string) => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    const a = v.state.doc.lineAt(from).number;
    const b = v.state.doc.lineAt(to).number;
    const changes = [];
    for (let n = a; n <= b; n++) changes.push({ from: v.state.doc.line(n).from, insert: prefix });
    v.dispatch({ changes });
    v.focus();
  };
  const insert = (text: string, caretOffset = text.length) => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    v.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + caretOffset } });
    v.focus();
  };
  const copy = async () => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    await navigator.clipboard.writeText(v.state.sliceDoc(from, to)).catch(() => {});
  };
  const cut = async () => {
    const v = view.current;
    if (!v) return;
    const { from, to } = v.state.selection.main;
    await navigator.clipboard.writeText(v.state.sliceDoc(from, to)).catch(() => {});
    v.dispatch({ changes: { from, to, insert: '' } });
    v.focus();
  };
  const paste = async () => {
    const t = await navigator.clipboard.readText().catch(() => '');
    if (t) insert(t);
  };
  const selectAll = () => {
    const v = view.current;
    if (v) v.dispatch({ selection: { anchor: 0, head: v.state.doc.length } });
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const v = view.current;
    const sel = v ? v.state.sliceDoc(v.state.selection.main.from, v.state.selection.main.to) : '';
    openContextMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          label: 'Format', icon: 'pencil', submenu: [
            { label: 'Bold', onClick: () => wrap('**') },
            { label: 'Italic', onClick: () => wrap('*') },
            { label: 'Strikethrough', onClick: () => wrap('~~') },
            { label: 'Highlight', onClick: () => wrap('==') },
            { label: 'Inline code', onClick: () => wrap('`') },
          ],
        },
        {
          label: 'Paragraph', icon: 'file-text', submenu: [
            { label: 'Heading 1', onClick: () => prefixLines('# ') },
            { label: 'Heading 2', onClick: () => prefixLines('## ') },
            { label: 'Heading 3', onClick: () => prefixLines('### ') },
            { label: 'Bullet list', onClick: () => prefixLines('- ') },
            { label: 'Numbered list', onClick: () => prefixLines('1. ') },
            { label: 'Task list', onClick: () => prefixLines('- [ ] ') },
            { label: 'Quote', onClick: () => prefixLines('> ') },
            { label: 'Code block', onClick: () => wrap('```\n', '\n```') },
          ],
        },
        {
          label: 'Insert', icon: 'plus', submenu: [
            { label: 'Internal link', onClick: () => insert('[[]]', 2) },
            { label: 'External link', onClick: () => wrap('[', '](url)') },
            { label: 'Embed file', onClick: () => insert('![[]]', 3) },
            { label: 'Callout', onClick: () => insert('> [!note] Title\n> ', 18) },
            { label: 'Table', onClick: () => insert('\n| Column 1 | Column 2 |\n| --- | --- |\n|  |  |\n') },
            { label: 'Horizontal rule', onClick: () => insert('\n---\n') },
            { label: tr("notes_calendar.day"), onClick: () => insert('#') },
          ],
        },
        { label: '', separator: true },
        { label: 'Ausschneiden', onClick: cut },
        { label: tr("notes_menu.copy"), onClick: copy },
        { label: tr("notes_menu.paste"), onClick: paste },
        { label: 'Select all', onClick: selectAll },
        ...(sel
          ? [
              { label: '', separator: true },
              { label: `Search for “${sel.slice(0, 24)}”`, icon: 'search', onClick: () => setLeftPanel('search') },
            ]
          : []),
      ],
    });
  };

  // (Re)create the view when the active file changes.
  useEffect(() => {
    if (!host.current) return;
    view.current?.destroy();

    const isMd = activePath ? /\.(md|markdown)$/i.test(activePath) : false;
    // Where the caret starts before the note's text has even arrived. This effect
    // runs on the path, not on the content (see its dependency list), so `content`
    // here is still the note before this one — the real placing happens in the
    // sync effect below, through `caretToFirstSafeLine`. Position 0 until then,
    // and never a position computed from the wrong note's length.
    const initPos = 0;
    const state = EditorState.create({
      doc: content,
      selection: { anchor: initPos },
      extensions: [
        history(),
        // On touch, drawSelection() replaces the browser's own selection —
        // and with it the drag handles that are the only way to adjust a
        // selection with a finger. Multiple cursors are worth less there than
        // being able to select at all.
        ...(matchMedia('(pointer: coarse)').matches ? [] : [drawSelection()]),
        highlightActiveLine(),
        // the default of the predecessor hotkeys (§4) win over CodeMirror's defaults.
        Prec.high(
          keymap.of(
            notesKeymap({
              openLink: (t) => void openWikilink(t),
              togglePreview: () =>
                useStore.getState().setViewMode(useStore.getState().viewMode === 'reading' ? 'live' : 'reading'),
              save: () => void useStore.getState().save(),
            }),
          ),
        ),
        // In-document Find/Replace (⌘F / ⌘⇧F open the panel; ⌘G next).
        search({ top: true }),
        keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
        /**
         * Triple-click selects the line, and only the line.
         *
         * By default it takes the line break with it, and the break belongs to
         * the line but is drawn at the start of the next one — so a selected
         * line always came with a stub of the line below it hanging off the
         * left. The text is the same either way; this is about what one sees.
         */
        EditorView.domEventHandlers({
          mousedown(event, view) {
            if (event.detail !== 3) return false;
            const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
            if (pos === null) return false;
            const line = view.state.doc.lineAt(pos);
            view.dispatch({ selection: { anchor: line.from, head: line.to } });
            event.preventDefault();
            return true;
          },
        }),
        // Tab and Shift-Tab move every selected line, by the vault's own unit.
        indentUnit.of(indent),
        EditorState.tabSize.of(indent === '\t' ? 4 : indent.length),
        // GFM base so Strikethrough/Table/TaskList nodes exist (the predecessor dialect);
        // codeLanguages lazy-loads grammars for fenced blocks (the predecessor: Prism).
        markdown({ base: markdownLanguage, codeLanguages: languages }),
        suggesterPlugin,
        // The predecessor token palette via CSS vars (works in both themes); markdown
        // structure styling is owned by the Live Preview decorations.
        syntaxHighlighting(notesHighlightStyle),
        EditorView.lineWrapping,
        // Live Preview drives BOTH live and reading; reading is just read-only.
        livePreviewState.init(() => isMd && viewMode !== 'source'),
        livePreviewReadonly.init(() => viewMode === 'reading'),
        readonlyComp.of(readonlyExt(viewMode === 'reading')),
        noteTitleField.init(() => (inlineTitle ? titleOf(activePath) : '')),
        inlineTitleField,
        frontmatterField,
        tableField,
        htmlBlockField,
        mermaidField,
        notePathField.init(() => {
          rememberNotePath(activePath ?? '');
          return activePath ?? '';
        }),
        editorFocusedField,
        editorFocusReporter,
        userEngagedField,
        emptyBlockState,
        dataviewField,
        noteEmbedField,
        listFoldState,
        listFoldDeco,
        dataviewInlineField,
        htmlRenderedState,
        htmlPreviewField,
        calloutFoldState,
        calloutFoldDeco,
        livePreviewPlugin,
        livePreviewTheme,
        editorClickFix,
        EditorView.updateListener.of((u) => {
          // Ignore doc changes we applied programmatically (external content sync)
          if (u.docChanged && !applyingExternal.current) setContent(u.state.doc.toString());
          // Faltzustand merken: was der Nutzer zuklappt oder aufklappt, soll
          // beim naechsten Oeffnen der Notiz noch so sein.
          if (u.transactions.some((t) => t.effects.some((e) => e.is(toggleListFold) || e.is(setListFolds)))) {
            const p = useStore.getState().activePath;
            if (p) {
              const doc = u.state.doc;
              useStore.getState().setNoteFolds(
                p,
                (u.state.field(listFoldState, false) ?? [])
                  .filter((x) => x <= doc.length)
                  .map((x) => doc.lineAt(x).number),
              );
            }
          }
        }),
      ],
    });
    const v = new EditorView({ state, parent: host.current });
    view.current = v;
    setActiveEditor(v);

    // A click inside a rendered block has to put the caret somewhere too.
    // Those blocks answer `ignoreEvent()` with true — their links, checkboxes and
    // buttons are their own business and must not become editor input — and
    // CodeMirror then skips ALL of its own handlers for that event, including the
    // one that normally moves the caret to where somebody clicked. The caret
    // stayed where it was, the editor took the focus anyway, and the browser
    // scrolled that old caret into view: click below the task list, land at the
    // end of the note. So the caret is placed here, before CodeMirror sees the
    // event, and everything the block does with the click still happens.
    const caretIntoBlocks = (event: MouseEvent) => {
      if (event.button !== 0 || event.shiftKey || event.detail > 1) return;
      const target = event.target as HTMLElement | null;
      // A transcluded note is another note. Clicking inside it must not drag the
      // caret onto the ONE line that holds it — from there, every later click
      // takes the caret away again, that line is redrawn, and the embed rebuilds
      // and drops to its loading height before filling back in. The note jumps,
      // and only ever after a click in the header first: that was the report.
      if (target?.closest('.cm-note-embed')) return;
      if (!target?.closest('.cm-dataview, .cm-drawing-embed')) return;
      const pos = v.posAtCoords({ x: event.clientX, y: event.clientY }, false);
      if (pos !== v.state.selection.main.head) v.dispatch({ selection: { anchor: pos } });
    };
    host.current.addEventListener('mousedown', caretIntoBlocks, true);
    // Opening a note does NOT take the keyboard. A note is opened to be read far
    // more often than to be written in, and a caret placed by nobody still counts
    // as a caret: it revealed the raw syntax of whatever line it landed on, which
    // for a note starting with a transclusion meant the embed never rendered until
    // you clicked elsewhere. Click into the text and it is yours.
    // The exception is a note that was just created: that one was opened in order
    // to be written in, and asking for a click first would only be in the way.
    if (useStore.getState().takeEditorFocus()) v.focus();
    const leaving = host.current;
    return () => {
      leaving.removeEventListener('mousedown', caretIntoBlocks, true);
      setActiveEditor(null);
      v.destroy();
    };
    // remountKey is the watchdog's last resort: rebuild the editor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath, remountKey]);

  // Sync the editor doc when `content` changes from OUTSIDE the editor — e.g. the
  // active note's content arrives asynchronously after reload/hydrate, or is
  // pushed by cross-tab sync. (User typing changes content too, but then the doc
  // already equals content, so this is a no-op.)
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const current = v.state.doc.toString();
    if (current === content) return;
    applyingExternal.current = true;
    // Two different situations share this effect. A different note arrived:
    // replace everything and place the caret below the frontmatter. The SAME
    // note changed underneath us (a merge, cross-tab sync): patch only the lines
    // that differ, so the caret, the selection and the undo history survive.
    const sameNote = appliedPath.current === activePath;
    appliedPath.current = activePath;
    if (sameNote) {
      const changes = lineEditsToChanges(current, diffLines(toLines(current), toLines(content)));
      if (changes.length) v.dispatch({ changes, scrollIntoView: false });
    } else {
      v.dispatch({ changes: { from: 0, to: current.length, insert: content } });
      // Where the caret goes when a note opens: past the frontmatter AND out of
      // any opening code block, or Live Preview shows that block as source.
      caretToFirstSafeLine(v);
    }
    applyingExternal.current = false;
    // Content arriving after layout (a slow fetch, a note opened while the tab
    // was hidden) can leave CodeMirror with a stale viewport measurement — the
    // note then looks empty until something forces a re-measure, which is what
    // switching tabs did. Ask for one right away instead.
    v.requestMeasure();
    requestAnimationFrame(() => v.requestMeasure());

    // Watchdog: a note that has text but renders nothing is the "I only see the
    // heading" case. Re-measure, and if that does not help, nudge the editor
    // with an empty transaction. It logs what it saw, so a report can say more
    // than "it was empty".
    const docLength = content.length;
    if (docLength > 200) {
      const checks = [180, 600, 1500];
      const timers = checks.map((delay, step) =>
        window.setTimeout(() => {
          const view = viewRef.current;
          if (!view || view.state.doc.length < 200) return;
          const rendered = view.contentDOM.textContent?.trim().length ?? 0;
          const height = view.contentDOM.getBoundingClientRect().height;
          if (rendered > 40 && height > 60) return;
          console.warn(
            `[editor] note looks empty (attempt ${step + 1}): doc=${view.state.doc.length} rendered=${rendered} height=${Math.round(height)} lines=${view.contentDOM.childElementCount}`,
          );
          view.requestMeasure();
          if (step === 1) view.dispatch({}); // force a fresh viewport pass
          if (step === checks.length - 1) setRemountKey((k: number) => k + 1); // rebuild
        }, delay),
      );
      return () => timers.forEach((t) => window.clearTimeout(t));
    }
    // `indent` is in here because it arrives one tick after mount, with the
    // vault's settings: the editor has to be built again to pick it up.
  }, [content, activePath, indent]);

  // Gemerkten Faltzustand anwenden - genau einmal je Notiz, danach entscheidet
  // der Nutzer. Steht mit Absicht HINTER dem Abgleich des Textes: solange der
  // Editor den Text der Notiz noch nicht hat, gibt es keine Zeilen zum Falten,
  // und ein leeres Ergebnis wuerde als "alles aufgeklappt" gemerkt.
  const restoredFor = useRef<string | null>(null);
  useEffect(() => {
    const v = view.current;
    if (!v || !activePath || !content || restoredFor.current === activePath) return;
    if (v.state.doc.length !== content.length) return;
    restoredFor.current = activePath;
    const lines = useStore.getState().noteFolds[activePath];
    if (!lines?.length) return;
    const doc = v.state.doc;
    v.dispatch({ effects: setListFolds.of(lines.filter((n) => n >= 1 && n <= doc.lines).map((n) => doc.line(n).from)) });
  }, [activePath, content]);

  // Toggle live preview / readonly when the view mode changes (no recreate).
  useEffect(() => {
    const isMd = activePath ? /\.(md|markdown)$/i.test(activePath) : false;
    view.current?.dispatch({
      effects: [
        setLivePreviewEnabled.of(isMd && viewMode !== 'source'),
        setLivePreviewReadonly.of(viewMode === 'reading'),
        readonlyComp.reconfigure(readonlyExt(viewMode === 'reading')),
        setNoteTitle.of(inlineTitle ? titleOf(activePath) : ''),
        setNotePath.of(activePath ?? ''),
      ],
    });
  }, [viewMode, activePath, inlineTitle]);

  // A tab that was hidden while its note loaded can come back with a stale
  // viewport measurement — the note then looks empty until something forces a
  // re-measure. Do it ourselves whenever the document becomes visible again.
  useEffect(() => {
    const remeasure = () => {
      if (document.visibilityState === 'visible') view.current?.requestMeasure();
    };
    document.addEventListener('visibilitychange', remeasure);
    window.addEventListener('focus', remeasure);
    return () => {
      document.removeEventListener('visibilitychange', remeasure);
      window.removeEventListener('focus', remeasure);
    };
  }, []);

  // Keep the caret clear of the on-screen keyboard and the toolbar above it.
  // Without this the line being typed sits behind both for the last third of
  // the screen, which is where one writes most of the time.
  useEffect(() => {
    const v = view.current;
    if (!v || !matchMedia('(pointer: coarse)').matches) return;
    const keepVisible = () => {
      const view2 = view.current;
      if (!view2 || !view2.hasFocus) return;
      view2.dispatch({ effects: EditorView.scrollIntoView(view2.state.selection.main.head, { y: 'nearest', yMargin: 90 }) });
    };
    const vv = window.visualViewport;
    vv?.addEventListener('resize', keepVisible);
    document.addEventListener('selectionchange', keepVisible);
    return () => {
      vv?.removeEventListener('resize', keepVisible);
      document.removeEventListener('selectionchange', keepVisible);
    };
  }, [remountKey]);

  // Debounced autosave. Deliberately slow: every save rewrites the whole file,
  // and Syncthing carries each rewrite to every peer — at 900ms a typing pause
  // was a transfer, which widened the window for a concurrent edit. The pause is
  // paid for by saving immediately whenever the tab is left or hidden, so no
  // keystroke ever waits longer than the moment the editor loses attention.
  useEffect(() => {
    const id = window.setTimeout(() => save(), 2500);
    return () => window.clearTimeout(id);
  }, [content, save]);

  useEffect(() => {
    const flush = () => void save();
    const onHide = () => {
      if (document.visibilityState === 'hidden') flush();
    };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', flush);
    window.addEventListener('blur', flush);
    return () => {
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', flush);
      window.removeEventListener('blur', flush);
      flush();
    };
  }, [save]);

  // The predecessor DOM contract (§20): markdown-source-view.cm-s-notes.mod-cm6
  // + .is-live-preview when Live Preview is on; readable line length caps width.
  const cls = [
    'cm-host',
    'markdown-source-view',
    readableLineWidth ? 'is-readable-line-width' : '',
    'cm-s-notes',
    'mod-cm6',
    viewMode !== 'source' ? 'is-live-preview live-preview' : '',
    viewMode === 'reading' ? 'is-reading-mode' : '',
  ].join(' ');
  return <div className={cls} ref={host} onContextMenu={onContextMenu} />;
}
