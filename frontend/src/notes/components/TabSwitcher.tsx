import { useStore, GRAPH_PATH, CALENDAR_PATH } from '../lib/store';
import { tr } from "../../i18n";
import Icon from './Icon';

/**
 * The open notes as a list, for when there is no room for a row of tabs.
 *
 * On a phone a tab strip shows one and a half tabs: enough to know something is
 * behind it, not enough to get there. This is the same set of tabs, one per
 * line, with the folder underneath the name — because on a narrow screen two
 * notes called "Notizen" are otherwise indistinguishable.
 */
export default function TabSwitcher() {
  const open = useStore((s) => s.tabSwitcherOpen);
  const setOpen = useStore((s) => s.setTabSwitcher);
  const tabs = useStore((s) => s.tabs);
  const activePath = useStore((s) => s.activePath);
  const openFile = useStore((s) => s.openFile);
  const closeTab = useStore((s) => s.closeTab);
  const newNote = useStore((s) => s.newNote);

  if (!open) return null;

  const icon = (p: string) =>
    p === GRAPH_PATH ? 'graph' : p === CALENDAR_PATH ? 'calendar' : 'file-text';

  return (
    <div className="modal-bg" onClick={() => setOpen(false)}>
      <div className="modal tab-switcher" onClick={(e) => e.stopPropagation()}>
        <div className="ts-head">
          <div className="ts-title">Geöffnet ({tabs.length})</div>
          <button
            className="tool-btn"
            title={tr("notes_sidebar.new_note")}
            onClick={() => {
              setOpen(false);
              void newNote();
            }}
          >
            <Icon name="plus" size={16} />
          </button>
          <button className="tool-btn" title={tr("common.close")} onClick={() => setOpen(false)}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="ts-list">
          {tabs.length === 0 && <div className="ts-empty">{tr("notes_tabs.none_open")}</div>}
          {tabs.map((t) => {
            const name = t.title.replace(/\.(md|markdown)$/, '');
            const folder = t.path.includes('/') ? t.path.slice(0, t.path.lastIndexOf('/')) : '';
            return (
              <div
                key={t.path}
                className={`ts-item ${activePath === t.path ? 'active' : ''}`}
                onClick={() => {
                  void openFile(t.path);
                  setOpen(false);
                }}
              >
                <Icon name={icon(t.path)} size={16} className="ts-icon" />
                <div className="ts-text">
                  <div className="ts-name">{name}</div>
                  {folder && <div className="ts-folder">{folder}</div>}
                </div>
                <button
                  className="tool-btn ts-close"
                  title={tr("notes_tabs.close_tab")}
                  onClick={(e) => {
                    e.stopPropagation();
                    closeTab(t.path);
                  }}
                >
                  <Icon name="x" size={16} />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
