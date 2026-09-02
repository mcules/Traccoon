import { useEffect, useState } from 'react';
import { useStore } from '../lib/store';
import { api } from '../lib/api';
import Icon from './Icon';

/**
 * Something shared from another app.
 *
 * Android hands a share over as a page load with the text in the address, so
 * this is what opens: what was shared, and the two things one actually wants to
 * do with it — put it under today, or start a note about it. Nothing is written
 * before that choice, because a link dropped into the wrong note is worse than
 * one that took a tap longer.
 *
 * A shared page usually comes as title plus link; a shared selection as text
 * alone. Both end up as one line one can write under.
 */
export default function ShareTarget({ onDone }: { onDone: () => void }) {
  const openFile = useStore((s) => s.openFile);
  const openDailyNote = useStore((s) => s.openDailyNote);
  const notify = useStore((s) => s.notify);
  const loadTree = useStore((s) => s.loadTree);
  const [busy, setBusy] = useState(false);
  const [shared, setShared] = useState<{ title: string; text: string; url: string } | null>(null);
  const [note, setNote] = useState('');

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setShared({
      title: (q.get('title') ?? '').trim(),
      text: (q.get('text') ?? '').trim(),
      url: (q.get('url') ?? '').trim(),
    });
  }, []);

  if (!shared) return null;

  // Some apps put the link into `text` rather than `url`; take whichever holds one.
  const link = shared.url || (/^https?:\/\/\S+$/.test(shared.text) ? shared.text : '');
  const body = shared.text && shared.text !== link ? shared.text : '';
  const label = shared.title || body.split('\n')[0].slice(0, 80) || link;

  const line = () => {
    const parts: string[] = [];
    if (link && label && label !== link) parts.push(`[${label}](${link})`);
    else if (link) parts.push(link);
    else parts.push(label);
    if (body && body !== label) parts.push(body);
    if (note.trim()) parts.push(note.trim());
    return parts.join(' — ');
  };

  const toDaily = async () => {
    setBusy(true);
    try {
      const daily = await api.dailyNote(0);
      const current = await api.read(daily.path);
      const text = current.content.replace(/\s*$/, '');
      await api.write(daily.path, `${text}\n- ${line()}\n`, current.hash);
      await loadTree();
      await openDailyNote(0);
      notify('In die Tagesnotiz eingetragen');
      onDone();
    } catch (e: any) {
      notify(e.message || 'Konnte nicht eingetragen werden');
    } finally {
      setBusy(false);
    }
  };

  const toNewNote = async () => {
    setBusy(true);
    try {
      const safe = (label || 'Geteilt').replace(/[\\/:*?"<>|#^[\]]/g, ' ').trim().slice(0, 80) || 'Geteilt';
      const path = `${safe}.md`;
      const parts = [`# ${label || 'Geteilt'}`, ''];
      if (link) parts.push(link, '');
      if (body && body !== label) parts.push(body, '');
      if (note.trim()) parts.push(note.trim(), '');
      await api.write(path, parts.join('\n'));
      await loadTree();
      await openFile(path);
      notify('Notiz angelegt');
      onDone();
    } catch (e: any) {
      notify(e.message || 'Notiz konnte nicht angelegt werden');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-bg">
      <div className="modal share-target">
        <div className="ts-head">
          <Icon name="arrow-up-right" size={16} />
          <div className="ts-title">Geteilt</div>
          <button className="tool-btn" title="Verwerfen" onClick={onDone}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="share-body">
          <div className="share-preview">{line()}</div>
          <textarea
            className="assistant-input"
            rows={3}
            placeholder="Notiz dazu (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div className="share-foot">
          <button className="btn secondary" disabled={busy} onClick={toNewNote}>
            Neue Notiz
          </button>
          <button className="btn" disabled={busy} onClick={toDaily}>
            An die Tagesnotiz
          </button>
        </div>
      </div>
    </div>
  );
}
