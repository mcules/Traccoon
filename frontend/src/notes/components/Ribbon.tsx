import { tr } from "../../i18n";
import { useStore } from '../lib/store';
import Icon from './Icon';

/**
 * The strip down the left edge.
 *
 * The theme switch used to sit here as well as in the settings; one place for a
 * setting is enough, and this strip is for what one reaches for often.
 */
export default function Ribbon() {
  const setLeftPanel = useStore((s) => s.setLeftPanel);
  const leftPanel = useStore((s) => s.leftPanel);
  const setGraph = useStore((s) => s.setGraph);
  const openCalendar = useStore((s) => s.openCalendar);
  const setPalette = useStore((s) => s.setPalette);
  const openDailyNote = useStore((s) => s.openDailyNote);


  return (
    <div className="ribbon">
      <button className={leftPanel === 'files' ? 'active' : ''} title={tr("notes_ribbon.files")} onClick={() => setLeftPanel('files')}>
        <Icon name="file-text" size={18} />
      </button>
      <button className={leftPanel === 'search' ? 'active' : ''} title={tr("notes_ribbon.search_hotkey")} onClick={() => setLeftPanel('search')}>
        <Icon name="search" size={18} />
      </button>
      <button title={tr("notes_ribbon.graph")} onClick={() => setGraph(true)}>
        <Icon name="graph" size={18} />
      </button>
      <button className={leftPanel === 'bookmarks' ? 'active' : ''} title={tr("notes_ribbon.bookmarks_and_recent")} onClick={() => setLeftPanel('bookmarks')}>
        <Icon name="bookmark" size={18} />
      </button>
      <button title={tr("notes_ribbon.calendar")} onClick={() => void openCalendar()}>
        <Icon name="calendar-days" size={18} />
      </button>
      <button title={tr("notes_ribbon.daily_note")} onClick={() => openDailyNote()}>
        <Icon name="calendar" size={18} />
      </button>
      <button className={leftPanel === 'tags' ? 'active' : ''} title={tr("notes_ribbon.tags")} onClick={() => setLeftPanel('tags')}>
        <Icon name="hash" size={18} />
      </button>
      <button title={tr("notes_ribbon.palette")} onClick={() => setPalette(true, 'commands')}>
        <Icon name="command" size={18} />
      </button>
      <div className="spacer" />
      <button title={tr("notes_ribbon.settings")} onClick={() => { window.location.href = '/account/notes'; }}>
        <Icon name="settings" size={18} />
      </button>
    </div>
  );
}
