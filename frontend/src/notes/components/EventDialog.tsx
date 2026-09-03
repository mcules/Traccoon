import { useEffect, useState } from 'react';
import { tr } from "../../i18n";
import { api } from '../lib/api';
import { useStore } from '../lib/store';
import Icon from './Icon';

/**
 * Creating and changing an appointment.
 *
 * Only calendars that sit on a login can be written — a subscription is a
 * public address and read-only by nature, so it is simply not offered rather
 * than failing on save. Several logins are normal: appointments live on more
 * than one server, and one server holds more than one account.
 */
export interface EventDraft {
  uid?: string;
  /** The calendar as it is set up here — a row, not a collection name. */
  calendar?: number;
  /** What the fetched appointment calls its calendar. A feed knows its display
   *  name but not which login it came from, so the writable list is matched
   *  against this — otherwise editing would act on whichever calendar happened
   *  to be first in the list. */
  calendarName?: string;
  title: string;
  date: string;
  time: string;
  endTime: string;
  allDay: boolean;
  location?: string;
  description?: string;
}

export default function EventDialog({
  draft,
  onClose,
  onSaved,
}: {
  draft: EventDraft | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const notify = useStore((s) => s.notify);
  const [calendars, setCalendars] =
    useState<Array<{ id: number; name: string; server: string; server_id: number }>>([]);
  const [configured, setConfigured] = useState(true);
  const [form, setForm] = useState<EventDraft | null>(draft);
  const [busy, setBusy] = useState(false);

  useEffect(() => setForm(draft), [draft]);
  useEffect(() => {
    if (!draft) return;
    api
      .calendarWritable()
      .then((r) => {
        setConfigured(r.configured);
        setCalendars(r.calendars);
        setForm((f) => {
          if (!f || f.calendar) return f;
          const byName = f.calendarName ? r.calendars.find((c) => c.name === f.calendarName) : undefined;
          return { ...f, calendar: (byName ?? r.calendars[0])?.id };
        });
      })
      .catch(() => setConfigured(false));
  }, [draft]);

  if (!form) return null;
  const many = new Set(calendars.map((c) => c.server_id)).size > 1;
  const set = (patch: Partial<EventDraft>) => setForm((f) => (f ? { ...f, ...patch } : f));

  const save = async () => {
    if (!form.calendar || !form.title.trim()) return;
    setBusy(true);
    try {
      await api.calendarSaveEvent({
        calendar: form.calendar,
        uid: form.uid,
        title: form.title.trim(),
        start: form.allDay ? form.date : `${form.date}T${form.time || '09:00'}`,
        end: form.allDay ? form.date : `${form.date}T${form.endTime || form.time || '10:00'}`,
        allDay: form.allDay,
        location: form.location,
        description: form.description,
      });
      notify(form.uid ? tr("notes_calendar.event_changed") : tr("notes_calendar.event_created"));
      onSaved();
      onClose();
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  /** Open the daily note of this appointment's day, at its line. */
  const toNote = async () => {
    const today = new Date();
    const p2 = (n: number) => String(n).padStart(2, '0');
    const heute = `${today.getFullYear()}-${p2(today.getMonth() + 1)}-${p2(today.getDate())}`;
    const diff = Math.round(
      (new Date(`${form.date}T12:00:00`).getTime() - new Date(`${heute}T12:00:00`).getTime()) / 86400000,
    );
    onClose();
    await useStore.getState().openDailyNote(diff);
  };

  const remove = async () => {
    if (!form.uid || !form.calendar) return;
    setBusy(true);
    try {
      await api.calendarDeleteEvent(form.calendar, form.uid);
      notify(tr("notes_calendar.event_deleted"));
      onSaved();
      onClose();
    } catch (e: any) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal event-dialog" onClick={(e) => e.stopPropagation()}>
        <h3>{form.uid ? tr("notes_calendar.edit_event") : tr("notes_calendar.new_event")}</h3>
        {!configured && <p className="calendar-error">{tr("notes_calendar.no_write_access")}</p>}
        <label>
          {tr("notes_calendar.event_title")}
          <input autoFocus value={form.title} onChange={(e) => set({ title: e.target.value })} />
        </label>
        <label>
          {tr("notes_ribbon.calendar")}
          <select value={form.calendar ?? ''}
            onChange={(e) => set({ calendar: Number(e.target.value) })}>
            {calendars.map((c) => (
              <option key={c.id} value={c.id}>
                {/* The login is named only where it distinguishes anything:
                    with one login it is noise on every line. */}
                {many ? `${c.server} – ${c.name}` : c.name}
              </option>
            ))}
          </select>
        </label>
        <div className="event-row">
          <label>
            Datum
            <input type="date" value={form.date} onChange={(e) => set({ date: e.target.value })} />
          </label>
          {!form.allDay && (
            <>
              <label>
                {tr("notes_calendar.from")}
                <input type="time" value={form.time} onChange={(e) => set({ time: e.target.value })} />
              </label>
              <label>
                Bis
                <input type="time" value={form.endTime} onChange={(e) => set({ endTime: e.target.value })} />
              </label>
            </>
          )}
        </div>
        <label className="event-check">
          <input type="checkbox" checked={form.allDay} onChange={(e) => set({ allDay: e.target.checked })} />
          {tr("notes_calendar.all_day_upper")}
        </label>
        <label>
          Ort
          <input value={form.location ?? ''} onChange={(e) => set({ location: e.target.value })} />
        </label>
        <label>
          {tr("notes_shared.note")}
          <textarea rows={3} value={form.description ?? ''} onChange={(e) => set({ description: e.target.value })} />
        </label>
        <div className="event-actions">
          {form.uid && (
            <button className="danger" disabled={busy} onClick={() => void remove()}>
              <Icon name="trash" size={15} /> {tr("common.delete")}
            </button>
          )}
          {/* Only for an appointment that exists: while creating one there is
              nothing yet to write notes about. */}
          {form.uid && (
            <button className="tool-btn" title={tr("notes_calendar.notes_on_this")} onClick={() => void toNote()}>
              <Icon name="file-text" size={15} /> {tr("notes_calendar.daily_note")}
            </button>
          )}
          <span className="grow" />
          <button className="tool-btn" onClick={onClose}>
            <Icon name="x" size={15} /> {tr("notes_dialog.cancel")}
          </button>
          <button className="primary" disabled={busy || !form.title.trim() || !form.calendar} onClick={() => void save()}>
            <Icon name="check" size={15} /> {tr("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
