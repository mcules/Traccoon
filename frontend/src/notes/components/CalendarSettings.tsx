import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { useStore } from '../lib/store';
import Icon from './Icon';

interface Source {
  name: string;
  url: string;
  linkTarget: string;
  hasAuth: boolean;
  authUser?: string;
  authPassword?: string;
}

/**
 * Managing the calendar sources.
 *
 * The list lives in the vault, in the same file the desktop plugin reads, so a
 * calendar added here appears there as well. Credentials are never sent back to
 * the page — an entry only reports whether it has any, and leaving the fields
 * empty keeps what is stored.
 */
export default function CalendarSettings() {
  const notify = useStore((s) => s.notify);
  const [sources, setSources] = useState<Source[]>([]);
  const [caldav, setCaldav] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState<Record<string, string>>({});

  const load = () =>
    api
      .calendarSources()
      .then((r) => {
        setSources(r.calendars);
        setCaldav(r.caldav.configured);
      })
      .catch(() => {});

  useEffect(() => {
    void load();
  }, []);

  const patch = (i: number, p: Partial<Source>) =>
    setSources((list) => list.map((s, j) => (i === j ? { ...s, ...p } : s)));

  const save = async () => {
    setBusy(true);
    try {
      const r = await api.saveCalendarSources(sources);
      notify(
        r.errors.length
          ? `Gespeichert — ${r.errors.length} Quelle(n) meldeten einen Fehler`
          : `Gespeichert, ${r.events} Termine abgerufen`,
      );
      await load();
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  const test = async (s: Source, i: number) => {
    setChecked((c) => ({ ...c, [i]: 'wird geprüft…' }));
    const r = await api.testCalendarSource(s.url, s.authUser, s.authPassword).catch(() => null);
    setChecked((c) => ({
      ...c,
      [i]: !r ? 'nicht erreichbar' : r.ok ? `in Ordnung — ${r.events} Termine` : `Fehler: ${r.message}`,
    }));
  };

  return (
    <div className="setting-section">
      <h2>Kalender</h2>
      <p className="setting-hint">
        Diese Quellen werden gelesen. Termine <b>anlegen</b> geht nur in Kalendern des eigenen
        Zugangs — {caldav ? 'der ist eingerichtet.' : 'dafür fehlen noch die Zugangsdaten (CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD im Stack).'}
      </p>

      {sources.map((s, i) => (
        <div key={i} className="cal-source">
          <div className="cal-source-row">
            <input
              placeholder="Name"
              value={s.name}
              onChange={(e) => patch(i, { name: e.target.value })}
              style={{ maxWidth: 180 }}
            />
            <input
              placeholder="Adresse des Kalenders (ICS oder CalDAV)"
              value={s.url}
              onChange={(e) => patch(i, { url: e.target.value })}
            />
            <button className="tool-btn" onClick={() => void test(s, i)}>
              <Icon name="refresh-cw" size={14} /> Prüfen
            </button>
            <button
              className="tool-btn"
              title="Entfernen"
              onClick={() => setSources((l) => l.filter((_, j) => j !== i))}
            >
              <Icon name="trash" size={14} />
            </button>
          </div>
          <div className="cal-source-row">
            <input
              placeholder="Notiz, auf die der Kalendername zeigt (optional)"
              value={s.linkTarget}
              onChange={(e) => patch(i, { linkTarget: e.target.value })}
            />
            <input
              placeholder={s.hasAuth ? 'Benutzer (gespeichert)' : 'Benutzer (falls nötig)'}
              value={s.authUser ?? ''}
              onChange={(e) => patch(i, { authUser: e.target.value })}
              style={{ maxWidth: 200 }}
            />
            <input
              type="password"
              placeholder={s.hasAuth ? 'Passwort (gespeichert)' : 'Passwort'}
              value={s.authPassword ?? ''}
              onChange={(e) => patch(i, { authPassword: e.target.value })}
              style={{ maxWidth: 200 }}
            />
          </div>
          {checked[i] && <div className="setting-hint">{checked[i]}</div>}
        </div>
      ))}

      <div className="event-actions">
        <button
          className="tool-btn"
          onClick={() => setSources((l) => [...l, { name: '', url: '', linkTarget: '', hasAuth: false }])}
        >
          <Icon name="plus" size={15} /> Kalender hinzufügen
        </button>
        <span className="grow" />
        <button className="primary" disabled={busy} onClick={() => void save()}>
          <Icon name="check" size={15} /> Speichern
        </button>
      </div>
    </div>
  );
}
