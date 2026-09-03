import { useEffect, useMemo, useState } from 'react';
import { tr, language } from "../../i18n";
import { api, type CalEvent } from '../lib/api';
import { useStore } from '../lib/store';
import Icon from './Icon';
import EventDialog, { type EventDraft } from './EventDialog';
import TimeGrid from './TimeGrid';

/**
 * The calendar, as a place of its own.
 *
 * Appointments used to exist only as text the sync wrote into daily notes,
 * which made the note carry two jobs at once: what is happening today, and what
 * one has to say about it. Here they are simply shown — month, week and day —
 * and the note keeps a one-line anchor per appointment to write under.
 */

type Mode = 'month' | 'week' | 'day';

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d: Date) => addDays(d, -((d.getDay() + 6) % 7)); // Monday
/**
 * The names of the days and months, from the language the interface is in.
 *
 * Written out here as two German lists before, which was one language too few
 * and twelve entries somebody would have had to translate by hand. The browser
 * already knows them in every language, so it is asked instead — with a Monday
 * as the reference date, because the week starts there in this view.
 */
const named = (fmt: Intl.DateTimeFormatOptions, from: Date, step: (i: number) => Date, n: number) =>
  Array.from({ length: n }, (_, i) =>
    new Intl.DateTimeFormat(language(), fmt).format(step(i)));
const WEEKDAYS = named({ weekday: 'short' }, new Date(2024, 0, 1),
  (i) => new Date(2024, 0, 1 + i), 7);          // 1 Jan 2024 was a Monday
const MONTHS = named({ month: 'long' }, new Date(2024, 0, 1),
  (i) => new Date(2024, i, 1), 12);

/** A stable colour per calendar, so the same source keeps its hue. */
function hueFor(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

export default function CalendarView() {
  const [mode, setMode] = useState<Mode>('month');
  const [anchor, setAnchor] = useState(new Date());
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [errors, setErrors] = useState<Array<{ calendar: string; message: string }>>([]);
  const openDailyNote = useStore((s) => s.openDailyNote);
  const notify = useStore((s) => s.notify);
  const [draft, setDraft] = useState<EventDraft | null>(null);
  /**
   * Calendars switched off in the legend.
   *
   * Remembered per device and by name: which calendars one wants to see is a
   * matter of what one is doing at that machine, not a property of the vault —
   * and the phone rarely wants the same subset as the desk.
   */
  const [hidden, setHidden] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem('cal-hidden');
      const list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string') : [];
    } catch {
      return [];
    }
  });
  const toggleCalendar = (name: string) =>
    setHidden((prev) => {
      const next = prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name];
      try { localStorage.setItem('cal-hidden', JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });

  const range = useMemo(() => {
    if (mode === 'day') return { from: iso(anchor), to: iso(anchor), days: [anchor] };
    if (mode === 'week') {
      const start = startOfWeek(anchor);
      const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
      return { from: iso(days[0]), to: iso(days[6]), days };
    }
    // As many whole weeks as the month actually reaches into. A fixed six-week
    // grid always shows a trailing week made entirely of the next month, which
    // is a row of the wrong month taking up a seventh of the view.
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const start = startOfWeek(first);
    const end = addDays(startOfWeek(last), 6);
    const count = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
    const days = Array.from({ length: count }, (_, i) => addDays(start, i));
    return { from: iso(days[0]), to: iso(days[days.length - 1]), days };
  }, [mode, anchor]);

  useEffect(() => {
    api
      .calendar(range.from, range.to)
      .then((r) => {
        setEvents(r.events);
        setErrors(r.errors);
      })
      .catch(() => setEvents([]));
  }, [range.from, range.to]);

  /** The calendars this range actually contains, in a stable order. */
  const calendars = useMemo(
    () => [...new Set(events.map((e) => e.calendar))].sort((a, b) => a.localeCompare(b, language())),
    [events],
  );

  const shown = useMemo(() => events.filter((e) => !hidden.includes(e.calendar)), [events, hidden]);

  const byDay = useMemo(() => {
    const m = new Map<string, CalEvent[]>();
    for (const e of shown) {
      const list = m.get(e.date) ?? [];
      list.push(e);
      m.set(e.date, list);
    }
    return m;
  }, [shown]);

  const step = (dir: number) => {
    if (mode === 'day') setAnchor((d) => addDays(d, dir));
    else if (mode === 'week') setAnchor((d) => addDays(d, dir * 7));
    else setAnchor((d) => new Date(d.getFullYear(), d.getMonth() + dir, 1));
  };

  const title =
    mode === 'day'
      ? `${WEEKDAYS[(anchor.getDay() + 6) % 7]}, ${anchor.getDate()}. ${MONTHS[anchor.getMonth()]} ${anchor.getFullYear()}`
      : mode === 'week'
        ? `KW ${weekNumber(anchor)} · ${MONTHS[anchor.getMonth()]} ${anchor.getFullYear()}`
        : `${MONTHS[anchor.getMonth()]} ${anchor.getFullYear()}`;

  const today = iso(new Date());

  const openNoteFor = async (date: string) => {
    const diff = Math.round(
      (new Date(`${date}T12:00:00`).getTime() - new Date(`${today}T12:00:00`).getTime()) / 86400000,
    );
    await openDailyNote(diff);
  };

  const refresh = async () => {
    notify(tr("notes_calendar.fetching"));
    try {
      const r = await api.calendarRefresh();
      notify(r.errors.length ? `Abgerufen, ${r.errors.length} Quelle(n) mit Fehler` : `${r.count} Termine abgerufen`);
      const again = await api.calendar(range.from, range.to);
      setEvents(again.events);
      setErrors(again.errors);
    } catch (e: any) {
      notify(e.message);
    }
  };

  return (
    <div className="calendar-view">
      <div className="calendar-header">
        <div className="calendar-nav">
          <button className="tool-btn" title={tr("notes_calendar.back")} onClick={() => step(-1)}>
            <Icon name="arrow-left" size={16} />
          </button>
          <button className="tool-btn cal-today" title={tr("notes_calendar.today")} onClick={() => setAnchor(new Date())}>
            {tr("notes_calendar.today")}
          </button>
          <button className="tool-btn" title={tr("notes_calendar.forward")} onClick={() => step(1)}>
            <Icon name="arrow-right" size={16} />
          </button>
        </div>
        <span className="calendar-title">{title}</span>
        <div className="seg">
          {(['month', 'week', 'day'] as Mode[]).map((m) => (
            <button key={m} className={mode === m ? 'active' : ''} onClick={() => setMode(m)}>
              {m === 'month' ? tr("notes_calendar.month") : m === 'week' ? tr("notes_calendar.week") : tr("notes_calendar.day")}
            </button>
          ))}
        </div>
        <button className="tool-btn" title={tr("notes_calendar.fetch_again")} onClick={() => void refresh()}>
          <Icon name="refresh-cw" size={15} />
        </button>
        <button
          className="primary"
          title={tr("notes_calendar.new_event")}
          onClick={() =>
            setDraft({ title: '', date: iso(anchor), time: '09:00', endTime: '10:00', allDay: false })
          }
        >
          <Icon name="plus" size={15} /> {tr("notes_calendar.new_event")}
        </button>
      </div>

      {calendars.length > 1 && (
        <div className="calendar-legend">
          {calendars.map((name) => {
            const off = hidden.includes(name);
            return (
              <button
                key={name}
                className={`cal-legend-item${off ? ' off' : ''}`}
                title={off ? `${name} einblenden` : `${name} ausblenden`}
                onClick={() => toggleCalendar(name)}
              >
                <span className="cal-dot" style={{ background: `hsl(${hueFor(name)}, 60%, 50%)` }} />
                {name}
              </button>
            );
          })}
        </div>
      )}

      {errors.map((e) => (
        <div key={e.calendar} className="calendar-error">
          {e.calendar}: {e.message}
        </div>
      ))}

      {mode === 'day' || mode === 'week' ? (
        <TimeGrid
          days={range.days}
          byDay={byDay}
          today={today}
          onOpen={setDraft}
          onNote={openNoteFor}
          onNew={(date, hour) =>
            setDraft({
              title: '',
              date,
              time: `${pad(hour)}:00`,
              endTime: `${pad(hour + 1)}:00`,
              allDay: false,
            })
          }
        />
      ) : (
        <div className={`calendar-grid ${mode}`}>
          {WEEKDAYS.map((w) => (
            <div key={w} className="calendar-weekday">
              {w}
            </div>
          ))}
          {range.days.map((d) => {
            const key = iso(d);
            const outside = mode === 'month' && d.getMonth() !== anchor.getMonth();
            return (
              <div
                key={key}
                className={`calendar-cell${outside ? ' outside' : ''}${key === today ? ' today' : ''}`}
                onDoubleClick={() =>
                  setDraft({ title: '', date: key, time: '09:00', endTime: '10:00', allDay: false })
                }
                onAuxClick={() => void openNoteFor(key)}
              >
                <div className="calendar-daynum" onClick={() => { setAnchor(d); setMode('day'); }}>
                  {d.getDate()}
                </div>
                <DayEvents events={byDay.get(key) ?? []} onOpen={setDraft} />
              </div>
            );
          })}
        </div>
      )}
      <EventDialog
        draft={draft}
        onClose={() => setDraft(null)}
        onSaved={() =>
          void api.calendar(range.from, range.to).then((r) => {
            setEvents(r.events);
            setErrors(r.errors);
          })
        }
      />
    </div>
  );
}

function DayEvents({
  events,
  full = false,
  onOpen,
}: {
  events: CalEvent[];
  full?: boolean;
  onOpen?: (d: EventDraft) => void;
}) {
  return (
    <>
      {events.map((e) => (
        <div
          key={e.id}
          className={`calendar-event${e.cancelled ? ' cancelled' : ''}`}
          style={{ borderLeftColor: `hsl(${hueFor(e.calendar)}, 60%, 50%)` }}
          title={`${e.time || tr("notes_calendar.all_day")} ${e.title} · ${e.calendar}${e.location ? `\n${e.location}` : ''}`}
          onClick={() =>
            onOpen?.({
              uid: e.uid,
              calendarName: e.calendar,
              title: e.title,
              date: e.date,
              time: e.time,
              endTime: e.endTime,
              allDay: e.allDay,
              location: e.location,
              description: e.description,
            })
          }
        >
          {!e.allDay && <span className="calendar-time">{e.time}</span>}{' '}
          <span className="calendar-event-title">{e.title}</span>
          {full && (
            <>
              <span className="calendar-cal"> · {e.calendar}</span>
              {e.location && <div className="calendar-detail">{e.location}</div>}
              {e.description && <div className="calendar-detail">{e.description}</div>}
            </>
          )}
        </div>
      ))}
    </>
  );
}

function weekNumber(d: Date): number {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  const start = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t.getTime() - start.getTime()) / 86400000 + 1) / 7);
}
