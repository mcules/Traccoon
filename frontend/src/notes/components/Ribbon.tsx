import { useEffect, useState } from 'react';
import { tr } from "../../i18n";
import { useStore } from '../lib/store';
import { api } from '../lib/api';
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
  const notify = useStore((s) => s.notify);
  const loadTree = useStore((s) => s.loadTree);

  // Show the Sync-now button only when git sync is enabled in settings.
  const [gitEnabled, setGitEnabled] = useState(false);
  const [syncing, setSyncing] = useState(false);
  useEffect(() => {
    const refresh = () => api.gitStatus().then((g) => setGitEnabled(!!g?.enabled)).catch(() => setGitEnabled(false));
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, []);

  const sync = async () => {
    if (syncing) return;
    setSyncing(true);
    notify('Syncing…');
    try {
      const r = await api.gitSync();
      notify(r.ok ? 'Synced ✓' : `Sync: ${r.log.at(-1)}`);
      await loadTree();
    } catch (e: any) {
      notify(`Sync failed: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

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
      {gitEnabled && (
        <button title={syncing ? tr("notes_status.syncing") : 'Jetzt abgleichen'} onClick={sync} disabled={syncing}>
          <Icon name="refresh-cw" size={18} style={syncing ? { animation: 'spin 1s linear infinite' } : undefined} />
        </button>
      )}
      <button title={tr("notes_ribbon.settings")} onClick={() => { window.location.href = '/account/notes'; }}>
        <Icon name="settings" size={18} />
      </button>
    </div>
  );
}
