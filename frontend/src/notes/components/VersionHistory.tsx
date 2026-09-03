import { useEffect, useState } from 'react';
import { tr, language } from "../../i18n";
import { useStore } from '../lib/store';
import { api, type NoteVersion } from '../lib/api';
import Icon from './Icon';

/** The kept versions of one note: what the hourly backup beside the vault has,
 *  and what a save here replaced. Look at one, put it back. */
export default function VersionHistory() {
  const path = useStore((s) => s.versionHistoryPath);
  const close = useStore((s) => s.setVersionHistory);
  const notify = useStore((s) => s.notify);
  const openFile = useStore((s) => s.openFile);
  const activePath = useStore((s) => s.activePath);

  const [commits, setCommits] = useState<NoteVersion[]>([]);
  /** Two sources answer different questions: the backup says what an hour ago
   *  looked like, the snapshots say what twenty minutes ago looked like. */
  const [source, setSource] = useState<'backup' | 'snapshots'>('backup');
  const [snaps, setSnaps] = useState<Array<{ ts: number; size: number }>>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!path) return;
    setLoading(true);
    setError('');
    setSelected(null);
    setPreview('');
    api
      .historyLog(path)
      .then((r) => {
        setCommits(r.commits);
        if (r.commits[0]) setSelected(r.commits[0].hash);
      })
      .catch((e) => setError(e.message || 'Failed to load history'))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    if (!path) return;
    api
      .snapshots(path)
      .then((r) => setSnaps(r.snapshots))
      .catch(() => setSnaps([]));
  }, [path]);

  useEffect(() => {
    if (!path || !selected || source !== 'snapshots') return;
    api
      .snapshotContent(path, Number(selected))
      .then((r) => setPreview(r.content))
      .catch(() => setPreview(''));
  }, [path, selected, source]);

  useEffect(() => {
    if (!path || !selected || source !== 'backup') return;
    api
      .historyShow(selected, path)
      .then((r) => setPreview(r.content))
      .catch(() => setPreview('(could not load this version)'));
  }, [path, selected]);

  if (!path) return null;

  const restore = async () => {
    if (!selected || !path) return;
    if (!confirm(tr("notes_versions.really_restore"))) return;
    try {
      // Restoring a snapshot goes through its own route, which keeps what is
      // being replaced — undoing a restore is then just another restore.
      if (source === 'snapshots') await api.restoreSnapshot(path, Number(selected));
      else await api.write(path, preview);
      if (path === activePath) await openFile(path);
      notify(tr("notes_versions.restored"));
      close(null);
    } catch (e: any) {
      notify(e.message || 'Wiederherstellen fehlgeschlagen');
    }
  };

  // On the language of the account, not on whatever the browser was set to:
  // the rest of this panel speaks the one, and a date in the other reads like a
  // different program wrote it.
  const fmtDate = (iso: string) => {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString(language());
  };

  return (
    <div className="modal-bg" onClick={() => close(null)}>
      <div className="modal version-history" onClick={(e) => e.stopPropagation()}>
        <div className="vh-head">
          <Icon name="clock" size={16} />
          <div className="vh-title">{tr("notes_versions.title")}</div>
          <div className="seg vh-source">
            <button className={source === 'backup' ? 'active' : ''} onClick={() => { setSource('backup'); setSelected(commits[0]?.hash ?? null); }}>
              {tr("notes_versions.backup_count", { n: commits.length })}
            </button>
            <button
              className={source === 'snapshots' ? 'active' : ''}
              onClick={() => { setSource('snapshots'); setSelected(snaps[0] ? String(snaps[0].ts) : null); }}
            >
              Zwischenstände ({snaps.length})
            </button>
          </div>
          <div className="vh-path">{path}</div>
          <button className="tool-btn" title={tr("common.close")} onClick={() => close(null)}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="vh-body">
          <div className="vh-list">
            {loading && <div className="vh-empty">{tr("common.loading")}</div>}
            {error && <div className="vh-empty">{error}</div>}
            {!loading && !error && source === 'backup' && commits.length === 0 && (
              <div className="vh-empty">{tr("notes_versions.no_backup")}</div>
            )}
            {source === 'snapshots' && snaps.length === 0 && (
              <div className="vh-empty">{tr("notes_versions.none_yet")}</div>
            )}
            {source === 'snapshots' &&
              snaps.map((sn, i) => (
                <div
                  key={sn.ts}
                  className={`vh-item ${selected === String(sn.ts) ? 'active' : ''}`}
                  onClick={() => setSelected(String(sn.ts))}
                >
                  <div className="vh-item-msg">
                    {i === 0 ? tr("notes_versions.replaced_last") : tr("notes_versions.interim")}
                  </div>
                  <div className="vh-item-meta">
                    {new Date(sn.ts).toLocaleString(language())} · {tr("notes_versions.characters", { n: sn.size })}
                  </div>
                </div>
              ))}
            {source === 'backup' && commits.map((c, i) => (
              <div
                key={c.hash}
                className={`vh-item ${selected === c.hash ? 'active' : ''}`}
                onClick={() => setSelected(c.hash)}
              >
                {/* The backup writes the time into its own message, so showing
                    both says the same thing twice. The message only earns a
                    line when it is not that. */}
                <div className="vh-item-msg">
                  {i === 0 ? tr("notes_versions.latest") : fmtDate(c.date)}
                </div>
                <div className="vh-item-meta">
                  {i === 0 ? fmtDate(c.date) : c.author}
                  {i === 0 ? ` · ${c.author}` : ''}
                </div>
              </div>
            ))}
          </div>
          <div className="vh-preview">
            <pre>{preview}</pre>
          </div>
        </div>
        <div className="vh-foot">
          <button className="btn secondary" onClick={() => close(null)}>
            {tr("notes_versions.close")}
          </button>
          <button className="btn" onClick={restore} disabled={!selected || commits.length === 0}>
            {tr("notes_versions.restore")}
          </button>
        </div>
      </div>
    </div>
  );
}
