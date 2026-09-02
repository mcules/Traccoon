import { useEffect } from 'react';
import { panelResizeHandler, restorePanelWidth } from '../lib/panelResize';
import { useStore } from '../lib/store';
import FileTree, { collectFolderPaths } from './FileTree';
import SearchPanel from './SearchPanel';
import TagsPanel from './TagsPanel';
import BookmarksPanel from './BookmarksPanel';
import Icon from './Icon';

const TITLES: Record<string, string> = {
  files: 'Files',
  search: 'Search',
  tags: 'Tags',
  bookmarks: 'Bookmarks',
};

const LEFT_PANEL = {
  variable: '--sidebar-width',
  storageKey: 'wo-sidebar-width',
  grows: 'right' as const,
  other: '.right-sidebar',
};

export default function Sidebar() {
  const leftPanel = useStore((s) => s.leftPanel);
  const newNote = useStore((s) => s.newNote);
  const newCanvas = useStore((s) => s.newCanvas);
  const newFolder = useStore((s) => s.newFolder);
  const setTrash = useStore((s) => s.setTrash);
  const tree = useStore((s) => s.tree);
  const expanded = useStore((s) => s.expanded);
  const setExpanded = useStore((s) => s.setExpanded);
  const treeSort = useStore((s) => s.treeSort);
  const setTreeSort = useStore((s) => s.setTreeSort);
  const autoReveal = useStore((s) => s.autoReveal);
  const toggleAutoReveal = useStore((s) => s.toggleAutoReveal);
  const openContextMenu = useStore((s) => s.openContextMenu);
  const vaultName = tree?.name || 'Vault';

  const allCollapsed = expanded.length === 0;
  const toggleCollapseAll = () => setExpanded(allCollapsed ? collectFolderPaths(tree) : []);

  // Width and its handle are shared with the right panel; only the direction
  // of growth differs.
  useEffect(() => restorePanelWidth(LEFT_PANEL), []);
  const onResizeDown = panelResizeHandler(LEFT_PANEL);

  const openSortMenu = (e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    openContextMenu({
      x: r.left,
      y: r.bottom + 4,
      items: [
        { label: 'File name (A to Z)', icon: treeSort === 'name-asc' ? 'check' : undefined, onClick: () => setTreeSort('name-asc') },
        { label: 'File name (Z to A)', icon: treeSort === 'name-desc' ? 'check' : undefined, onClick: () => setTreeSort('name-desc') },
        { label: '', separator: true },
        { label: 'Modified time (new to old)', icon: treeSort === 'mtime-desc' ? 'check' : undefined, onClick: () => setTreeSort('mtime-desc') },
        { label: 'Modified time (old to new)', icon: treeSort === 'mtime-asc' ? 'check' : undefined, onClick: () => setTreeSort('mtime-asc') },
        { label: '', separator: true },
        { label: 'Created time (new to old)', icon: treeSort === 'ctime-desc' ? 'check' : undefined, onClick: () => setTreeSort('ctime-desc') },
        { label: 'Created time (old to new)', icon: treeSort === 'ctime-asc' ? 'check' : undefined, onClick: () => setTreeSort('ctime-asc') },
      ],
    });
  };

  return (
    <div className="sidebar">
      <div className="nav-header">
        <span className="nav-title">{TITLES[leftPanel]}</span>
        {leftPanel === 'files' && (
          <>
            <button className="nav-action" title="Neue Notiz" onClick={() => newNote()}>
              <Icon name="square-pen" size={16} />
            </button>
            <button className="nav-action" title="Neues Canvas" onClick={() => newCanvas()}>
              <Icon name="layout-dashboard" size={16} />
            </button>
            <button className="nav-action" title="Neuer Ordner" onClick={() => newFolder()}>
              <Icon name="folder-plus" size={16} />
            </button>
            <button className="nav-action" title="Change sort order" onClick={openSortMenu}>
              <Icon name="arrow-up-narrow-wide" size={16} />
            </button>
            <button
              className={`nav-action ${autoReveal ? 'active' : ''}`}
              title="Auto reveal current file"
              onClick={() => toggleAutoReveal()}
            >
              <Icon name="crosshair" size={16} />
            </button>
            <button
              className="nav-action"
              title={allCollapsed ? 'Expand all' : 'Collapse all'}
              onClick={toggleCollapseAll}
            >
              <Icon name={allCollapsed ? 'chevrons-up-down' : 'chevrons-down-up'} size={16} />
            </button>
            <button className="nav-action" title="Papierkorb" onClick={() => setTrash(true)}>
              <Icon name="trash" size={16} />
            </button>
          </>
        )}
      </div>
      <div className="sidebar-body">
        {leftPanel === 'files' && <FileTree />}
        {leftPanel === 'search' && <SearchPanel />}
        {leftPanel === 'tags' && <TagsPanel />}
        {leftPanel === 'bookmarks' && <BookmarksPanel />}
      </div>
      <div className="vault-footer">
        <span className="vault-name">
          <Icon name="gem" size={15} /> {vaultName}
        </span>
      </div>
      <div className="sidebar-resizer" title="Drag to resize" onPointerDown={onResizeDown} />
    </div>
  );
}
