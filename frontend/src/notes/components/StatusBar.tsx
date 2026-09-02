import { useEffect, useState } from 'react';
import { useStore } from '../lib/store';
import { api } from '../lib/api';
import { getActiveEditor } from '../lib/activeEditor';
import { pendingCount } from '../lib/pending';
import Icon from './Icon';

export default function StatusBar() {
  const content = useStore((s) => s.content);
  const activePath = useStore((s) => s.activePath);
  const dirty = useStore((s) => s.dirty);
  const loadTree = useStore((s) => s.loadTree);
  const notify = useStore((s) => s.notify);
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const online = useStore((s) => s.online);
  // How many notes are waiting to be sent. Counted from storage rather than
  // held in the store, because the queue outlives this tab.
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    const tick = () => setWaiting(pendingCount());
    tick();
    const id = setInterval(tick, 3000);
    return () => clearInterval(id);
  }, []);
  const [git, setGit] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);

  const refresh = () => api.gitStatus().then(setGit).catch(() => setGit(null));
  useEffect(() => {
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
      await refresh();
    } catch (e: any) {
      notify(`Sync failed: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const isText = activePath && /\.(md|markdown|txt)$/i.test(activePath);
  const words = isText ? content.trim().split(/\s+/).filter(Boolean).length : 0;

  // Word count of the selection, like the predecessor: while text is selected the bar
  // counts that, not the whole note. Polled rather than subscribed because the
  // selection lives in CodeMirror, which does not push into the store.
  const [sel, setSel] = useState<{ words: number; chars: number } | null>(null);
  useEffect(() => {
    const tick = () => {
      const v = getActiveEditor();
      const r = v?.state.selection.main;
      if (!v || !r || r.empty) return setSel(null);
      const text = v.state.sliceDoc(r.from, r.to);
      setSel({ words: text.trim().split(/\s+/).filter(Boolean).length, chars: text.length });
    };
    const id = setInterval(tick, 400);
    return () => clearInterval(id);
  }, []);

  /** "vor 12 Minuten" — how long ago the last backup ran. */
  const ago = (iso: string | null): string => {
    if (!iso) return '';
    const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (!Number.isFinite(min) || min < 0) return '';
    if (min < 1) return 'gerade eben';
    if (min < 60) return `vor ${min} min`;
    const h = Math.round(min / 60);
    return h < 48 ? `vor ${h} h` : `vor ${Math.round(h / 24)} Tagen`;
  };

  // Sync off is the normal state here: the versions come from the hourly backup
  // beside the vault, and what matters then is how fresh that is — not the state
  // of a working tree nobody commits to.
  const gitLabel = !git?.enabled
    ? git?.hasHistory
      ? `Sicherung ${ago(git.historyLast) || 'vorhanden'}`
      : 'Keine Vault-Sicherung'
    : git.clean
      ? `git ${git.branch}${git.ahead ? ` ↑${git.ahead}` : ''}${git.behind ? ` ↓${git.behind}` : ''}`
      : `${git.modified + git.notAdded} offene Änderungen`;

  return (
    <div className="status-bar">
      {!online && (
        <span className="status-offline" title="Ohne Netz: Lesen geht, Geschriebenes wartet">
          <Icon name="wifi-off" size={13} />
          Offline{waiting ? ` · ${waiting} wartet` : ''}
        </span>
      )}
      {dirty && <span>Wird gespeichert…</span>}
      {isText && sel && (
        <span title="Auswahl">
          {sel.words} von {words} Wörtern
        </span>
      )}
      {isText && !sel && <span>{words} Wörter</span>}
      {isText && !sel && <span>{content.length} Zeichen</span>}
      {isText && (
        <span
          className="clickable"
          title="Zwischen Bearbeiten und Lesen wechseln"
          onClick={() => setViewMode(viewMode === 'reading' ? 'live' : 'reading')}
        >
          {viewMode === 'reading' ? 'Leseansicht' : viewMode === 'source' ? 'Quelltext' : 'Bearbeiten'}
        </span>
      )}
      <span
        className="clickable"
        title={git?.enabled ? 'Jetzt abgleichen' : 'Versionen kommen aus der stündlichen Sicherung'}
        onClick={git?.enabled ? sync : undefined}
      >
        <Icon name="refresh-cw" size={13} style={syncing ? { animation: 'spin 1s linear infinite' } : undefined} />
        {gitLabel}
      </span>
    </div>
  );
}
