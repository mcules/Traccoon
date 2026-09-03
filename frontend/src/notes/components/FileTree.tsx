import { useEffect, useRef, useState } from 'react';
import { tr } from "../../i18n";
import { useStore, type TreeSort } from '../lib/store';
import { useLongPress } from '../lib/useLongPress';
import { api, type TreeNode } from '../lib/api';
import { findNode, pruneDescendants } from '../lib/tree';
import { pathToUrl } from '../lib/urlsync';
import Icon from './Icon';

/** Inline rename box shown in place of a tree row's name (like the predecessor). */
function RenameInput({ node, onDone }: { node: TreeNode; onDone: () => void }) {
  const loadTree = useStore((s) => s.loadTree);
  const closeTab = useStore((s) => s.closeTab);
  const notify = useStore((s) => s.notify);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    // Select the name but not the extension, like the predecessor.
    const dot = node.type === 'file' ? node.name.lastIndexOf('.') : -1;
    el.setSelectionRange(0, dot > 0 ? dot : node.name.length);
  }, [node.name, node.type]);

  const finish = (commit: boolean) => async () => {
    if (done.current) return;
    done.current = true;
    const name = (ref.current?.value ?? '').trim();
    onDone();
    if (!commit || !name || name === node.name) return;
    const dir = parentDir(node.path);
    const to = dir ? `${dir}/${name}` : name;
    if (to === node.path) return;
    try {
      await api.rename(node.path, to);
      closeTab(node.path);
    } catch (e: any) {
      notify(e?.message ?? 'Rename failed');
    }
    await loadTree();
  };

  return (
    <input
      ref={ref}
      className="tree-rename"
      defaultValue={node.name}
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); void finish(true)(); }
        else if (e.key === 'Escape') { e.preventDefault(); void finish(false)(); }
      }}
      onBlur={finish(true)}
    />
  );
}

function fileIcon(node: TreeNode): string | null {
  const ext = node.ext ?? '';
  if (/\.(md|markdown)$/.test(ext)) return null; // markdown: text only, like the predecessor
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(ext)) return 'image';
  if (ext === '.pdf') return 'file-pdf';
  return 'paperclip';
}

function parentDir(path: string): string {
  const i = path.lastIndexOf('/');
  return i < 0 ? '' : path.slice(0, i);
}

/** Parse the dragged path list from a drag event (multi-select aware). */
function readDragPaths(e: React.DragEvent): string[] {
  const multi = e.dataTransfer.getData('text/wo-paths');
  if (multi) {
    try {
      const arr = JSON.parse(multi);
      if (Array.isArray(arr)) return arr.filter((p): p is string => typeof p === 'string');
    } catch { /* fall through to legacy single */ }
  }
  const single = e.dataTransfer.getData('text/wo-path');
  return single ? [single] : [];
}

/** Move every path into targetDir ('' = vault root). Skips no-ops and self/descendant moves. */
async function moveItemsTo(paths: string[], targetDir: string): Promise<void> {
  const { closeTab, loadTree, setSelected, notify } = useStore.getState();
  let moved = 0;
  for (const from of pruneDescendants(paths)) {
    if (!from) continue;
    const base = from.split('/').pop()!;
    const to = targetDir ? `${targetDir}/${base}` : base;
    if (to === from) continue; // already there
    if (targetDir === from || targetDir.startsWith(`${from}/`)) continue; // into self/descendant
    try {
      await api.rename(from, to);
      closeTab(from);
      moved++;
    } catch (err: any) {
      notify(err?.message ?? 'Move failed');
    }
  }
  setSelected([]);
  await loadTree();
  if (moved > 1) notify(`Moved ${moved} items`);
}

/** Visible tree rows in display order — used to resolve a Shift-click range. */
function visibleOrder(): string[] {
  return [...document.querySelectorAll<HTMLElement>('.tree-row[data-path]')]
    .map((el) => el.dataset.path!)
    .filter(Boolean);
}

/**
 * Pick a name for `base` inside `targetDir` that doesn't collide with an
 * existing child — appends " copy" / " copy N" before the extension, like the predecessor.
 */
function uniqueChildName(tree: TreeNode | null, targetDir: string, base: string): string {
  const folder = targetDir ? findNode(tree, targetDir) : tree;
  const taken = new Set((folder?.children ?? []).map((c) => c.name.toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  const dot = base.lastIndexOf('.');
  const stem = dot > 0 ? base.slice(0, dot) : base;
  const ext = dot > 0 ? base.slice(dot) : '';
  let name = `${stem} copy${ext}`;
  for (let i = 1; taken.has(name.toLowerCase()); i++) name = `${stem} copy ${i}${ext}`;
  return name;
}

/** Die Farbfolge des Themes: elf Toene, dann von vorn. */
const FOLDER_COLORS = [
  'red', 'maroon', 'peach', 'yellow', 'green', 'teal',
  'sky', 'sapphire', 'blue', 'lavender', 'mauve',
];

function Node({ node, depth, colorIndex }: { node: TreeNode; depth: number; colorIndex?: number }) {
  const expanded = useStore((s) => s.expanded);
  const toggleFolder = useStore((s) => s.toggleFolder);
  const open = expanded.includes(node.path); // persisted across reloads
  const [dropping, setDropping] = useState(false);
  const activePath = useStore((s) => s.activePath);
  const openFile = useStore((s) => s.openFile);
  const openToSide = useStore((s) => s.openToSide);
  const loadTree = useStore((s) => s.loadTree);
  const closeTab = useStore((s) => s.closeTab);
  const openContextMenu = useStore((s) => s.openContextMenu);
  const setMovePath = useStore((s) => s.setMovePath);
  const clipboard = useStore((s) => s.clipboard);
  const setClipboard = useStore((s) => s.setClipboard);
  const newNote = useStore((s) => s.newNote);
  const newCanvas = useStore((s) => s.newCanvas);
  const newFolder = useStore((s) => s.newFolder);
  const renamingPath = useStore((s) => s.renamingPath);
  const setRenamingPath = useStore((s) => s.setRenamingPath);
  const toggleBookmark = useStore((s) => s.toggleBookmark);
  const bookmarks = useStore((s) => s.bookmarks);
  const notify = useStore((s) => s.notify);
  const isSelected = useStore((s) => s.selected.includes(node.path));
  const setSelected = useStore((s) => s.setSelected);
  const setSelectAnchor = useStore((s) => s.setSelectAnchor);

  const isFolder = node.type === 'folder';
  const editing = renamingPath === node.path;
  const isCut = clipboard?.mode === 'cut' && clipboard.path === node.path;

  // Click selection: plain = single (+open/toggle), Cmd/Ctrl = toggle one,
  // Shift = range from the anchor across the visible rows (like the predecessor/Finder).
  const onRowClick = (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey) {
      const cur = useStore.getState().selected;
      setSelected(cur.includes(node.path) ? cur.filter((p) => p !== node.path) : [...cur, node.path]);
      setSelectAnchor(node.path);
      return;
    }
    if (e.shiftKey) {
      e.preventDefault(); // don't text-select across rows
      const order = visibleOrder();
      const anchor = useStore.getState().selectAnchor ?? node.path;
      const a = order.indexOf(anchor);
      const b = order.indexOf(node.path);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        setSelected(order.slice(lo, hi + 1));
      } else {
        setSelected([node.path]);
      }
      return;
    }
    setSelected([node.path]);
    setSelectAnchor(node.path);
    if (isFolder) toggleFolder(node.path);
    else openFile(node.path);
  };

  const doDeleteMany = async () => {
    const paths = pruneDescendants(useStore.getState().selected);
    if (!paths.length || !confirm(`Delete ${paths.length} item${paths.length > 1 ? 's' : ''}?`)) return;
    let n = 0;
    for (const p of paths) {
      const r = await api.remove(p).catch(() => null);
      if (r) { closeTab(p); n++; }
    }
    setSelected([]);
    await loadTree();
    notify(`Deleted ${n} item${n > 1 ? 's' : ''}`);
  };
  const doMoveMany = () => setMovePath(useStore.getState().selected);

  const doRename = () => setRenamingPath(node.path);
  const doDelete = async () => {
    if (confirm(`Delete "${node.name}"?`)) {
      const r = await api.remove(node.path);
      closeTab(node.path);
      await loadTree();
      notify(r.deleted ? 'Deleted permanently' : 'Moved to trash');
    }
  };

  const doCopy = async () => {
    const r = await api.read(node.path).catch(() => null);
    if (!r) return;
    const content = typeof r === 'string' ? r : r.content;
    const dot = node.path.lastIndexOf('.');
    const copyPath = dot > 0 ? `${node.path.slice(0, dot)} copy${node.path.slice(dot)}` : `${node.path} copy`;
    await api.write(copyPath, content);
    await loadTree();
    notify('Made a copy');
  };
  const doMove = () => setMovePath(node.path);

  const doClipboard = (mode: 'copy' | 'cut') => () => {
    setClipboard({ path: node.path, mode });
    notify(mode === 'cut' ? 'Cut' : 'Copied');
  };
  const doPaste = async () => {
    const clip = useStore.getState().clipboard;
    if (!clip) return;
    const targetDir = isFolder ? node.path : parentDir(node.path);
    // Never paste a folder into itself or one of its own descendants.
    if (clip.path === targetDir || targetDir === clip.path || targetDir.startsWith(`${clip.path}/`)) {
      notify('Cannot paste into itself');
      return;
    }
    const base = clip.path.split('/').pop()!;
    const srcDir = parentDir(clip.path);
    if (clip.mode === 'cut') {
      if (targetDir === srcDir) { setClipboard(null); return; } // already here — no-op
      const to = targetDir ? `${targetDir}/${base}` : base;
      try {
        await api.rename(clip.path, to);
        closeTab(clip.path);
        setClipboard(null);
        await loadTree();
        notify('Moved');
      } catch (e: any) {
        notify(e?.message ?? 'Paste failed');
      }
      return;
    }
    // copy — recursive server-side copy (works for both files and folders).
    const name = uniqueChildName(useStore.getState().tree, targetDir, base);
    const to = targetDir ? `${targetDir}/${name}` : name;
    try {
      await api.copy(clip.path, to);
      await loadTree();
      notify('Pasted');
    } catch (e: any) {
      notify(e?.message ?? 'Paste failed');
    }
  };

  const copyPath = () => {
    navigator.clipboard?.writeText(node.path).catch(() => {});
    notify('Path copied');
  };
  const copyUrl = () => {
    navigator.clipboard?.writeText(`${location.origin}${pathToUrl(node.path)}`).catch(() => {});
    notify('URL copied');
  };

  // Same menu from two gestures: right-click on a desktop, a long press on a
  // phone — where there is no context-menu event at all, which used to leave
  // rename/move/delete unreachable.
  const openMenuAt = (x: number, y: number) => {
    const sel = useStore.getState().selected;
    // Right-clicking a row that's part of a multi-selection → bulk actions.
    if (sel.length > 1 && sel.includes(node.path)) {
      openContextMenu({
        x,
        y,
        items: [
          { label: `Move ${sel.length} items to…`, onClick: doMoveMany },
          { label: '', separator: true },
          { label: `Delete ${sel.length} items`, danger: true, onClick: doDeleteMany },
        ],
      });
      return;
    }
    // Right-clicking outside the selection collapses it onto this single row.
    if (!sel.includes(node.path)) { setSelected([node.path]); setSelectAnchor(node.path); }
    const items = isFolder
      ? [
          { label: tr("notes_sidebar.new_note"), onClick: () => newNote(node.path) },
          { label: 'Neues Canvas', onClick: () => newCanvas(node.path) },
          { label: tr("notes_sidebar.new_folder"), onClick: () => newFolder(node.path) },
          { label: '', separator: true },
          { label: tr("notes_menu.copy"), onClick: doClipboard('copy') },
          { label: 'Ausschneiden', onClick: doClipboard('cut') },
          ...(clipboard ? [{ label: tr("notes_menu.paste"), onClick: doPaste }] : []),
          { label: '', separator: true },
          { label: 'Umbenennen…', onClick: doRename },
          { label: tr("notes_menu.move_folder_to"), onClick: doMove },
          { label: tr("notes_menu.copy_path"), onClick: copyPath },
          { label: tr("notes_menu.copy_link_path"), onClick: copyUrl },
          { label: '', separator: true },
          { label: tr("common.delete"), danger: true, onClick: doDelete },
        ]
      : [
          { label: tr("notes_menu.open"), onClick: () => openFile(node.path) },
          { label: tr("notes_workspace.open_beside"), onClick: () => openToSide(node.path) },
          { label: '', separator: true },
          { label: bookmarks.includes(node.path) ? 'Remove bookmark' : 'Bookmark', onClick: () => toggleBookmark(node.path) },
          { label: tr("notes_menu.duplicate"), onClick: doCopy },
          { label: '', separator: true },
          { label: tr("notes_menu.copy"), onClick: doClipboard('copy') },
          { label: 'Ausschneiden', onClick: doClipboard('cut') },
          ...(clipboard ? [{ label: tr("notes_menu.paste"), onClick: doPaste }] : []),
          { label: '', separator: true },
          { label: 'Umbenennen…', onClick: doRename },
          { label: 'Move file to…', onClick: doMove },
          { label: tr("notes_menu.copy_link_path"), onClick: copyUrl },
          { label: '', separator: true },
          { label: tr("common.delete"), danger: true, onClick: doDelete },
        ];
    openContextMenu({ x, y, items });
  };

  const onContext = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    openMenuAt(e.clientX, e.clientY);
  };
  const longPress = useLongPress(openMenuAt);

  const onDragStart = (e: React.DragEvent) => {
    // Drag the whole selection if this row is part of it; otherwise drag just it
    // (and make it the selection, so the highlight matches what's being dragged).
    const sel = useStore.getState().selected;
    const paths = sel.includes(node.path) && sel.length > 1 ? sel : [node.path];
    if (!sel.includes(node.path)) { setSelected([node.path]); setSelectAnchor(node.path); }
    e.dataTransfer.setData('text/wo-paths', JSON.stringify(paths));
    e.dataTransfer.setData('text/wo-path', node.path); // legacy single-path readers
    e.dataTransfer.effectAllowed = 'move';
  };
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropping(false);
    const targetDir = isFolder ? node.path : parentDir(node.path);
    await moveItemsTo(readDragPaths(e), targetDir);
  };

  if (isFolder) {
    return (
      <div
        className="tree-item is-folder"
        // Welche Farbe der Ordner traegt, entscheidet seine Stelle unter den
        // ORDNERN - ein CSS-Selektor koennte nur alle Kinder zaehlen und
        // wuerde die Dateien mitzaehlen.
        style={
          colorIndex === undefined
            ? undefined
            : ({ ['--folder-color' as string]: `var(--ctp-${FOLDER_COLORS[colorIndex % FOLDER_COLORS.length]})` } as React.CSSProperties)
        }
      >
        <div
          className={`tree-row folder ${isSelected ? 'selected' : ''} ${dropping ? 'drop-target' : ''}`}
          style={isCut ? { opacity: 0.5 } : undefined}
          data-path={node.path}
          draggable
          onDragStart={onDragStart}
          onClick={onRowClick}
          onContextMenu={onContext}
          {...longPress}
          onDragOver={(e) => { e.preventDefault(); setDropping(true); }}
          onDragLeave={() => setDropping(false)}
          onDrop={onDrop}
        >
          <span className="twisty">
            <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
          </span>
          {editing ? (
            <RenameInput node={node} onDone={() => setRenamingPath(null)} />
          ) : (
            <span className="name">{node.name}</span>
          )}
        </div>
        {open && (
          <div className="tree-children">
            {(node.children ?? []).map((c) => (
              <Node key={c.path} node={c} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  const fi = fileIcon(node);
  return (
    <div className="tree-item">
      <div
        className={`tree-row ${activePath === node.path ? 'active' : ''} ${isSelected ? 'selected' : ''} ${dropping ? 'drop-target' : ''}`}
        style={isCut ? { opacity: 0.5 } : undefined}
        data-path={node.path}
        draggable
        onDragStart={onDragStart}
        onClick={onRowClick}
        onContextMenu={onContext}
        {...longPress}
        // A file is a valid drop target too: dropping onto it moves the dragged
        // item into the file's parent folder (the behaviour of the predecessor). Without this,
        // drops on a file — or anywhere inside an expanded folder's contents —
        // bubble up to the root handler and either no-op or move to the vault root.
        onDragOver={(e) => { e.preventDefault(); setDropping(true); }}
        onDragLeave={() => setDropping(false)}
        onDrop={onDrop}
        title={node.path}
      >
        <span className="twisty leaf" />
        {fi && <span className="twisty"><Icon name={fi} size={14} /></span>}
        {editing ? (
          <RenameInput node={node} onDone={() => setRenamingPath(null)} />
        ) : (
          <span className="name">{node.name.replace(/\.(md|markdown)$/, '')}</span>
        )}
        {bookmarks.includes(node.path) && <Icon name="bookmark" size={12} className="bm-star" />}
      </div>
    </div>
  );
}

/** All folder paths in the tree (used by the header's Expand-all button). */
export function collectFolderPaths(root: TreeNode | null): string[] {
  if (!root) return [];
  const out: string[] = [];
  const walk = (n: TreeNode) => {
    for (const c of n.children ?? []) {
      if (c.type === 'folder') { out.push(c.path); walk(c); }
    }
  };
  walk(root);
  return out;
}

/**
 * Recursively sort a tree's children by the chosen order. Folders are always
 * grouped first and ordered by name (like the predecessor); the time/name criterion
 * applies to files. Only rendered (expanded) folders' children are shown, so
 * this naturally sorts just what's visible in the panel.
 */
function sortTree(node: TreeNode, order: TreeSort): TreeNode {
  if (!node.children) return node;
  const cmp = (a: TreeNode, b: TreeNode): number => {
    if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
    if (a.type === 'folder') return a.name.localeCompare(b.name);
    switch (order) {
      case 'name-desc': return -a.name.localeCompare(b.name);
      case 'mtime-desc': return (b.mtime ?? 0) - (a.mtime ?? 0);
      case 'mtime-asc': return (a.mtime ?? 0) - (b.mtime ?? 0);
      case 'ctime-desc': return (b.ctime ?? 0) - (a.ctime ?? 0);
      case 'ctime-asc': return (a.ctime ?? 0) - (b.ctime ?? 0);
      default: return a.name.localeCompare(b.name); // name-asc
    }
  };
  const children = node.children
    .map((c) => (c.type === 'folder' ? sortTree(c, order) : c))
    .sort(cmp);
  return { ...node, children };
}

/** The extensions the app can actually open — the same set the desktop shows. */
const OPENABLE = new Set([
  'md', 'markdown', 'canvas', 'base', 'excalidraw', 'txt', 'pdf',
  'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp', 'avif',
  'mp3', 'wav', 'm4a', '3gp', 'flac', 'ogg', 'oga', 'opus',
  'mp4', 'webm', 'ogv', 'mov', 'mkv',
]);

function isOpenableFile(name: string): boolean {
  const dot = name.lastIndexOf('.');
  // No extension at all: leave it, it may well be a note without one.
  if (dot <= 0) return true;
  return OPENABLE.has(name.slice(dot + 1).toLowerCase());
}

export default function FileTree() {
  // Whether to show the rest anyway — the vault's own setting.
  const [showUnsupported, setShowUnsupported] = useState(false);
  useEffect(() => {
    api
      .vaultConfig()
      .then((c) => setShowUnsupported(c.app.showUnsupportedFiles))
      .catch(() => {});
  }, []);
  const rawTree = useStore((s) => s.tree);
  const treeSort = useStore((s) => s.treeSort);
  const tree = rawTree ? sortTree(rawTree, treeSort) : rawTree;
  const loadTree = useStore((s) => s.loadTree);
  const notify = useStore((s) => s.notify);
  const closeTab = useStore((s) => s.closeTab);
  const clipboard = useStore((s) => s.clipboard);
  const setClipboard = useStore((s) => s.setClipboard);
  const openContextMenu = useStore((s) => s.openContextMenu);
  const newNote = useStore((s) => s.newNote);
  const newCanvas = useStore((s) => s.newCanvas);
  const newFolder = useStore((s) => s.newFolder);
  const activePath = useStore((s) => s.activePath);
  const autoReveal = useStore((s) => s.autoReveal);
  const setExpanded = useStore((s) => s.setExpanded);

  // Auto-reveal: when enabled, expand ancestors of the active file + scroll to it.
  useEffect(() => {
    if (!autoReveal || !activePath || !activePath.includes('.')) return;
    const segs = activePath.split('/');
    segs.pop();
    const ancestors: string[] = [];
    let acc = '';
    for (const s of segs) { acc = acc ? `${acc}/${s}` : s; ancestors.push(acc); }
    const cur = useStore.getState().expanded;
    const missing = ancestors.filter((a) => !cur.includes(a));
    if (missing.length) setExpanded([...cur, ...missing]);
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent('wo-reveal-file', { detail: { path: activePath } }));
    }, 60);
  }, [activePath, autoReveal, setExpanded]);

  // "Reveal file in navigation": scroll + flash the row once its folders expand.
  useEffect(() => {
    const onReveal = (e: Event) => {
      const path = (e as CustomEvent<{ path: string }>).detail?.path;
      if (!path) return;
      requestAnimationFrame(() => {
        const el = document.querySelector<HTMLElement>(`.tree-row[data-path="${CSS.escape(path)}"]`);
        if (!el) return;
        el.scrollIntoView({ block: 'center' });
        el.classList.add('reveal-flash');
        window.setTimeout(() => el.classList.remove('reveal-flash'), 1200);
      });
    };
    window.addEventListener('wo-reveal-file', onReveal);
    return () => window.removeEventListener('wo-reveal-file', onReveal);
  }, []);

  const setSelected = useStore((s) => s.setSelected);

  const onRootDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    // Drop on the empty area = move to the vault root. Only nested items move.
    const paths = readDragPaths(e).filter((p) => p.includes('/'));
    if (paths.length) await moveItemsTo(paths, '');
  };

  // Paste into the vault root (right-click on the empty area of the file tree).
  const pasteToRoot = async () => {
    const clip = useStore.getState().clipboard;
    if (!clip) return;
    const base = clip.path.split('/').pop()!;
    if (clip.mode === 'cut') {
      if (!clip.path.includes('/')) { setClipboard(null); return; } // already at root — no-op
      try {
        await api.rename(clip.path, base);
        closeTab(clip.path);
        setClipboard(null);
        await loadTree();
        notify('Moved');
      } catch (e: any) {
        notify(e?.message ?? 'Paste failed');
      }
      return;
    }
    const name = uniqueChildName(useStore.getState().tree, '', base);
    try {
      await api.copy(clip.path, name);
      await loadTree();
      notify('Pasted');
    } catch (e: any) {
      notify(e?.message ?? 'Paste failed');
    }
  };

  const onRootContext = (e: React.MouseEvent) => {
    e.preventDefault();
    openContextMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        { label: tr("notes_sidebar.new_note"), onClick: () => newNote('') },
        { label: 'Neues Canvas', onClick: () => newCanvas('') },
        { label: tr("notes_sidebar.new_folder"), onClick: () => newFolder('') },
        ...(clipboard
          ? [{ label: '', separator: true }, { label: tr("notes_menu.paste"), onClick: pasteToRoot }]
          : []),
      ],
    });
  };

  /**
   * What the list shows, and in which colour.
   *
   * The desktop hides files it cannot open — a `.json` beside the notes is on
   * the disk but not in the list, because the list is of things one can open.
   * The vault says whether to do that; its default is to hide them.
   *
   * The colour index counts folders only. A CSS selector cannot: it would count
   * the files between them and shift every colour after the first loose file.
   */
  const visibleChildren = (tree?.children ?? []).filter(
    (c) => c.type === 'folder' || showUnsupported || isOpenableFile(c.name),
  );
  const folderIndex = new Map<string, number>();
  let n = 0;
  for (const c of visibleChildren) if (c.type === 'folder') folderIndex.set(c.path, n++);

  if (!tree) return <div style={{ padding: 12, color: 'var(--text-faint)' }}>{tr("common.loading")}</div>;
  if (!tree.children?.length)
    return (
      <div
        onContextMenu={onRootContext}
        style={{ padding: 12, color: 'var(--text-faint)', minHeight: '100%' }}
      >
        Vault is empty.
      </div>
    );
  return (
    <div
      // Named so the colouring can reach the top-level folders: the colour
      // cycles per folder of the first level, everything below inherits.
      className="file-tree"
      onDragOver={(e) => e.preventDefault()}
      onDrop={onRootDrop}
      onContextMenu={onRootContext}
      // Click on the empty area below the rows clears the selection.
      onClick={(e) => { if (e.target === e.currentTarget) setSelected([]); }}
      style={{ minHeight: '100%' }}
    >
      {visibleChildren.map((c, i) => (
        <Node key={c.path} node={c} depth={0} colorIndex={folderIndex.get(c.path)} />
      ))}
    </div>
  );
}
