import { useEffect, useState } from 'react';
import { tr } from "../../i18n";
import { useStore } from '../lib/store';
import { api } from '../lib/api';
import { getActiveEditor } from '../lib/activeEditor';
import { pendingCount } from '../lib/pending';
import Icon from './Icon';

export default function StatusBar() {
  const content = useStore((s) => s.content);
  const activePath = useStore((s) => s.activePath);
  const dirty = useStore((s) => s.dirty);
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
  // How fresh the kept versions are. There is nothing to press here: the
  // versions come from the hourly backup beside the vault, which writes on its
  // own, and this side only ever reads them.
  const [history, setHistory] = useState<{ has: boolean; last: string | null } | null>(null);
  useEffect(() => {
    const refresh = () => api.historyInfo().then(setHistory).catch(() => setHistory(null));
    refresh();
    const id = setInterval(refresh, 60000);
    return () => clearInterval(id);
  }, []);

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

  /** How long ago the last backup ran, in words. */
  const ago = (iso: string | null): string => {
    if (!iso) return '';
    const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (!Number.isFinite(min) || min < 0) return '';
    if (min < 1) return tr("notes_versions.just_now");
    if (min < 60) return tr("notes_versions.minutes_ago", { n: min });
    const h = Math.round(min / 60);
    return h < 48 ? tr("notes_versions.hours_ago", { n: h })
                  : tr("notes_versions.days_ago", { n: Math.round(h / 24) });
  };

  // What matters is how fresh the kept versions are. The state of a working
  // tree nobody commits to used to stand here as well; there is no such tree.
  const historyLabel = !history?.has
    ? tr("notes_versions.no_vault_backup")
    : tr("notes_versions.backup_age", { age: ago(history.last) || tr("notes_versions.present") });

  return (
    <div className="status-bar">
      {!online && (
        <span className="status-offline" title={tr("notes_status.offline")}>
          <Icon name="wifi-off" size={13} />
          Offline{waiting ? ` · ${waiting} wartet` : ''}
        </span>
      )}
      {dirty && <span>{tr("notes_status.saving")}</span>}
      {isText && sel && (
        <span title={tr("notes_status.selection")}>
          {sel.words} von {words} Wörtern
        </span>
      )}
      {isText && !sel && <span>{words} Wörter</span>}
      {isText && !sel && <span>{content.length} Zeichen</span>}
      {isText && (
        <span
          className="clickable"
          title={tr("notes_status.toggle_edit_read")}
          onClick={() => setViewMode(viewMode === 'reading' ? 'live' : 'reading')}
        >
          {viewMode === 'reading' ? 'Leseansicht' : viewMode === 'source' ? 'Quelltext' : 'Bearbeiten'}
        </span>
      )}
      <span title={tr("notes_versions.from_backup")}>
        <Icon name="clock" size={13} />
        {historyLabel}
      </span>
    </div>
  );
}
